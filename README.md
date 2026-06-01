# LiteOpenCodeMcp

[中文说明](README.zh-CN.md)

Lightweight MCP wrapper for running `opencode` as a coding agent from Codex.

## Installation

### Prerequisites

- Python 3.10 or newer.
- Git CLI on `PATH`.
- OpenCode CLI on `PATH`.
- An OpenCode provider/auth setup that can run real model requests.

OpenCode's official install docs are at <https://opencode.ai/docs/>. Common install
options:

```powershell
# Cross-platform when Node.js/npm is available
npm install -g opencode-ai

# Verify
opencode --version
opencode run "hello"
```

On Windows, OpenCode's docs recommend WSL for the best experience, but native Windows
install methods such as npm, Chocolatey, or Scoop can also work. Make sure the
`opencode` executable is visible to the same environment that starts this MCP server.

Configure OpenCode authentication before using this wrapper for real tasks:

```powershell
opencode auth login
opencode auth list
```

OpenCode can also read provider keys from environment variables or a project `.env`
file, depending on your provider setup.

### Python Environment

This wrapper uses the official MCP Python SDK import path:

```python
from mcp.server.fastmcp import FastMCP
```

Create an isolated environment and install the MCP SDK:

```powershell
cd D:\Develop\LiteOpenCodeMcp

python -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
python -m pip install "mcp[cli]"
```

Linux/macOS equivalent:

```bash
cd /path/to/LiteOpenCodeMcp

python3 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install "mcp[cli]"
```

Quick local verification:

```powershell
python -m py_compile opencode-coder.py test_opencode_coder.py
python -B -m unittest -v test_opencode_coder.py
```

The real OpenCode integration smoke test is opt-in; see
[Integration Smoke Test](#integration-smoke-test).

### MCP Client Configuration

Register `opencode-coder.py` as a stdio MCP server in your MCP client. A typical JSON
configuration looks like:

```json
{
  "mcpServers": {
    "opencode_coder": {
      "command": "D:\\Develop\\LiteOpenCodeMcp\\.venv\\Scripts\\python.exe",
      "args": ["D:\\Develop\\LiteOpenCodeMcp\\opencode-coder.py"],
      "env": {
        "OPENCODE_CODER_MAX_WAIT_SECONDS": "110",
        "OPENCODE_CODER_FINISHED_JOB_TTL_SECONDS": "3600"
      }
    }
  }
}
```

Adjust paths for your machine. After changing the MCP server file or its tool schema,
restart the MCP client or the MCP server process so the new schema is loaded.

Optional environment variables:

- `OPENCODE_CODER_MAX_WAIT_SECONDS`: caps synchronous MCP wait time. Default `110`.
- `OPENCODE_CODER_FINISHED_JOB_TTL_SECONDS`: finished job retention window. Default
  `3600`.
- `OPENCODE_CODER_REGISTRY_PATH`: override the managed server registry JSON path.
- `OPENCODE_CODER_RUN_INTEGRATION`: set to `1` only when running the real integration
  smoke test.

## Recommended Calling Contract

For normal coding tasks, treat LiteOpenCodeMcp as a job scheduler plus compact review
surface, not as a raw terminal stream.

- Prefer a managed OpenCode server: discover it with `opencode_server_list`, start one
  with `opencode_server_start` when needed, then pass `server_id` to `opencode_coder`.
- Reuse `server_id` by default, not `session_id`. Only pass `session_id`,
  `continue_last`, or `fork_session` when intentional conversation continuity is
  required. Do not reuse a session across different `working_dir` or repository roots.
- Prefer `wait_policy="start_only"` or `"first_output"` for long tasks, then poll with
  compact `opencode_coder_status(job_id, wait_seconds=...)`.
- Do not request raw output in the normal polling loop. `include_tail`,
  `include_output`, and `include_delta` are debug switches and should be paired with
  explicit character caps when enabled.
- Treat `completed` / `success=true` as process status only. Before accepting a job,
  inspect `suggested_action`, `work_summary_text`, changed-file fields, path-policy
  fields, stall/risk fields, `no_event_noop_risk`, and validation fields.
- If `no_event_noop_risk=true`, retry without `session_id`, with a fresh session, or
  with a fresh server; do not accept the result just because the process completed.
- The wrapper does not run project validation and does not auto-rollback partial edits.
  Review `opencode_coder_diff(job_id)` and fall back to local `git status` / `git diff`
  when the diff result is incomplete or surprising.

## Tools

### `opencode_coder`

Runs `opencode run --format json --dangerously-skip-permissions <prompt>` in a target
working directory.

Parameters:

- `prompt`: task prompt passed to OpenCode.
- `working_dir`: directory where OpenCode should run. Defaults to `"."`.
- `timeout_seconds`: maximum MCP wait window. If the process is still running after
  this window, the tool returns a structured `timed_out` result with `job_id` and the
  process keeps running in the background.
- `wait_policy`: controls what the MCP call waits for. Defaults to `"completion"` for
  compatibility with older callers.
- `allow_concurrent`: defaults to `false`. When false, a second job for the same
  normalized `working_dir` is rejected and returns the existing running `job_id`.
- `allowed_paths`: optional list of files or directories that this job may newly
  change.
- `forbidden_paths`: optional list of files or directories that this job must not
  newly change.
- `server_id`: optional managed server id returned by `opencode_server_start`.
- `session_id`: optional OpenCode session id to pass with `--session`.
- `continue_last`: optional flag to pass `--continue`.
- `fork_session`: optional flag to pass `--fork`.
- `title`: optional task title to pass with `--title`.
- `include_tail`: optional debug flag. Defaults to `false`, so the result keeps
  `stdout_tail` / `stderr_tail` empty.
- `include_output`: optional compatibility/debug flag. Defaults to `false`, so the
  legacy `output` field is empty.
- `include_delta`: optional debug flag. Defaults to `false`, so `stdout_delta` /
  `stderr_delta` stay empty even when cursors are provided.
- `recent_events_limit`: maximum event summaries to return. Defaults to `5`; pass
  `20` for the full retained diagnostic window.
- `delta_max_chars`: optional response-only cap for returned deltas when
  `include_delta=true`.
- `tail_max_chars`: optional tail/output character cap when tail/output are enabled.

`timeout_seconds` is capped by `OPENCODE_CODER_MAX_WAIT_SECONDS` to avoid the MCP
client timing out before the wrapper can return job context. The default cap is
`110` seconds. Returned results include both `requested_timeout_seconds` and
`effective_timeout_seconds`.

Wait policies:

- `"completion"`: wait for process completion, or until `timeout_seconds` elapses.
  This is the default and keeps the old behavior.
- `"start_only"`: return after the process is started and the job is registered.
  Use this when the caller wants a `job_id` as quickly as possible.
- `"first_output"`: return after stdout/stderr receives any output, the process
  completes, or `timeout_seconds` elapses.
- `"first_change"`: return after `new_changed_files` becomes non-empty, the process
  completes, or `timeout_seconds` elapses.

For long-running coding tasks, prefer `"start_only"` or `"first_output"` so the
caller can regain control quickly and poll with `opencode_coder_status`.

By default, `opencode_coder` returns compact work feedback: `status`, `success`,
`suggested_action`, `summary`, `work_summary_text` / `last_text_output`,
changed-file lists, risk fields, lightweight diagnostics, and cursor metadata. It
does not return raw OpenCode stdout/stderr tails, stdout JSON event streams, delta
text, or legacy `output` unless the caller explicitly enables the debug flags above.

When `server_id` is omitted, `opencode_coder` keeps the original direct
`opencode run` behavior. When `server_id` is provided, it runs through an attached
headless server:

```text
opencode run --attach <server_url> --dir <working_dir> --format json --dangerously-skip-permissions ...
```

The wrapper parses `sessionID` from JSON events written to stdout. If no valid JSON
event has appeared yet, `session_id` can be `null`.

Avoid reusing a `session_id` across different `working_dir` or repository roots. If
a completed attached job reports `no_event_noop_risk=true`, do not treat
`completed` / `success=true` as normal completion; check the session scope or retry
without `session_id`, with a fresh session, or with a fresh server.

Job results also include:

- `session_id`
- `server_id`
- `server_url`
- `attached_to_server`
- `server_recovered_from_registry`
- `wait_policy`
- `first_output_at`
- `first_change_at`
- `last_activity_at`
- `last_event_type`
- `last_event_at`
- `last_event_summary`
- `recent_events`
- `recent_event_count`
- `last_text_output`
- `work_summary_text`
- `assistant_last_text`
- `last_tool_name`
- `last_tool_event`
- `last_step_reason`
- `last_step_status`
- `last_session_id`
- `diagnostic_phase`
- `diagnostic_note`
- `no_event_noop_risk`
- `no_event_noop_reason`
- `runtime_seconds`
- `idle_seconds`
- `is_stalled`
- `stall_reason`
- `suggested_action`
- `review_required`
- `incomplete_changes_risk`
- `potential_incomplete_changes_risk`
- `preexisting_dirty_warning`
- `validation_status`
- `validation_note`

### `opencode_coder_status`

Queries an in-memory job by `job_id`.

Parameters:

- `job_id`: job returned by `opencode_coder`.
- `wait_seconds`: optional short wait, clamped to `0..30`. Defaults to `0` for an
  immediate status response.
- `stdout_cursor`: optional stdout cursor returned by a previous job/status result.
- `stderr_cursor`: optional stderr cursor returned by a previous job/status result.
- `include_tail`: optional debug flag. Defaults to `false`, so compact polling does
  not resend `stdout_tail` / `stderr_tail`.
- `include_output`: optional compatibility/debug flag. Defaults to `false`, so
  compact polling does not resend legacy `output`.
- `include_delta`: optional debug flag. Defaults to `false`, so compact polling
  advances cursors without returning raw `stdout_delta` / `stderr_delta` text.
- `recent_events_limit`: maximum event summaries to return. Defaults to `5`; pass
  `20` for the full retained diagnostic window.
- `delta_max_chars`: optional response-only cap for returned deltas when
  `include_delta=true`.
- `tail_max_chars`: optional tail/output character limit when either include flag is
  enabled. The wrapper clamps this to a safe upper bound.

When `wait_seconds` is greater than zero, status waits until the job completes, new
stdout/stderr arrives, a new file change is detected, or the wait expires. Status
queries never start a new OpenCode process.

By default, status responses are compact: `stdout_tail`, `stderr_tail`, `output`,
`stdout_delta`, and `stderr_delta` are present for compatibility but empty. Cursors
still advance to the latest observed offsets so callers can resume future debug
polling from "now". Pass `include_delta=true` to return text after a supplied cursor.
Cursors are character offsets inside a bounded in-memory buffer; they are not
persistent log file offsets. Use raw fields only for targeted diagnostics.

Returns a structured result including:

- `job_id`
- `status`
- `working_dir`
- `pid`
- `exit_code`
- `summary`
- `session_id`
- `server_id`
- `server_url`
- `attached_to_server`
- `changed_files`
- `preexisting_changed_files`
- `all_changed_files`
- `new_changed_files`
- `policy_violation`
- `extra_changed_files`
- `forbidden_changed_files`
- `path_policy`
- `git_status_available`
- `git_status_error`
- `tests_run`
- `validation_skipped_reason`
- `validation_status`
- `validation_note`
- `stdout_tail`
- `stderr_tail`
- `stdout_delta`
- `stderr_delta`
- `stdout_cursor`
- `stderr_cursor`
- `stdout_delta_truncated`
- `stderr_delta_truncated`
- `stdout_delta_response_truncated`
- `stderr_delta_response_truncated`
- `started_at`
- `finished_at`
- `first_output_at`
- `first_change_at`
- `last_activity_at`
- `last_event_type`
- `last_event_at`
- `last_event_summary`
- `recent_events`
- `recent_event_count`
- `last_text_output`
- `work_summary_text`
- `assistant_last_text`
- `last_tool_name`
- `last_tool_event`
- `last_step_reason`
- `last_step_status`
- `last_session_id`
- `diagnostic_phase`
- `diagnostic_note`
- `no_event_noop_risk`
- `no_event_noop_reason`
- `runtime_seconds`
- `idle_seconds`
- `is_stalled`
- `stall_reason`
- `suggested_action`
- `review_required`
- `incomplete_changes_risk`
- `potential_incomplete_changes_risk`
- `preexisting_dirty_warning`
- `command`
- `wait_policy`
- `cancel_requested`
- `cancel_signal_sent`
- `cancel_kill_sent`
- `process_tree_kill_attempted`
- `process_tree_kill_succeeded`
- `process_tree_kill_error`

Finished jobs are retained in memory for at least
`OPENCODE_CODER_FINISHED_JOB_TTL_SECONDS` seconds. The default is `3600`.

Cursor polling example:

```python
status = opencode_coder_status(job_id)
stdout_cursor = status["stdout_cursor"]
stderr_cursor = status["stderr_cursor"]

next_status = opencode_coder_status(
    job_id,
    wait_seconds=5,
    stdout_cursor=stdout_cursor,
    stderr_cursor=stderr_cursor,
    include_delta=True,
    delta_max_chars=8000,
)
print(next_status["stdout_delta"])
print(next_status["stderr_delta"])
```

Delta buffers are bounded by `MAX_DELTA_BUFFER_CHARS` inside the wrapper. If a cursor
is older than the retained buffer, the wrapper returns the currently available suffix
and sets `stdout_delta_truncated` or `stderr_delta_truncated` to `true`. If
`delta_max_chars` trims the response, the wrapper sets
`stdout_delta_response_truncated` or `stderr_delta_response_truncated` to `true` and
still advances the cursor to the latest offset to avoid replaying the same large
output. Normal polling loops should consume compact status and cursor fields; request
delta/tail fields only for targeted diagnostics:

```python
debug_status = opencode_coder_status(
    job_id,
    include_tail=True,
    include_output=True,
    include_delta=True,
    recent_events_limit=20,
    delta_max_chars=8000,
    tail_max_chars=4000,
)
```

### Job Diagnostics And Review Risk

Every job result includes lightweight diagnostics:

- `runtime_seconds`: seconds from `started_at` to `finished_at`, or to now while the
  job is active.
- `idle_seconds`: seconds since the most recent trusted activity, using
  `last_activity_at`, `first_output_at`, then `started_at`. A delayed first
  observation of file changes does not by itself reset this timer.
- `is_stalled`: `true` only for active jobs that have exceeded the wrapper's stall
  thresholds. A stalled job is not automatically failed.
- `stall_reason`: `changed_files_no_recent_activity`,
  `no_output_no_change_after_start`, `no_recent_activity`, or
  `timed_out_waiting_for_completion` when applicable.
- `suggested_action`: compact caller guidance such as `continue_polling`,
  `continue_polling_or_consider_cancel`, `consider_cancel`,
  `review_diff_then_consider_cancel`, `review_diff_or_git_status`, or
  `check_session_or_retry_without_session`.

The wrapper also parses stdout JSON lines from OpenCode into bounded event
diagnostics. This is read-only observation; it does not change execution strategy:

- `recent_events`: structured summaries of the most recent OpenCode stdout JSON
  events. Results return at most 5 by default; pass `recent_events_limit=20` for the
  full retained diagnostic window. The wrapper never stores the full raw event.
- `recent_event_count`: total JSON events observed for this job, which may be larger
  than `len(recent_events)` after truncation.
- `last_event_type`, `last_event_at`, `last_event_summary`: the last observed event
  type, event/observation timestamp, and bounded structured summary.
- `last_text_output`: preview of the most recent model text event.
- `work_summary_text` / `assistant_last_text`: aliases for the latest assistant text
  preview; callers should prefer this over raw stdout for completed-job feedback.
- `last_tool_name`, `last_tool_event`: the most recent observed tool activity.
- `last_step_reason`, `last_step_status`: recent step-level reason/status when
  present in OpenCode events.
- `last_session_id`: the most recent `sessionID` observed in event JSON.
- `diagnostic_phase`: a coarse derived phase such as `no_event_seen`, `model_text`,
  `tool_activity`, `step_started`, `step_finished`,
  `process_running_no_recent_event`, `process_finished`, or `unknown`.
- `diagnostic_note`: short caller-facing explanation. For stalled jobs it includes
  the last observed event phase alongside `is_stalled` / `stall_reason`, but it does
  not replace `suggested_action`.
- `no_event_noop_risk`: `true` when an attached completed job with explicit session
  reuse (`session_id`, `continue_last`, or `fork_session`) produced no stdout JSON
  events, no job-scoped changes, and no meaningful stdout/stderr output.
- `no_event_noop_reason`: machine-readable reason when `no_event_noop_risk=true`.

Event summaries include only small fields such as `type`, `timestamp`, `sessionID`,
`messageID`, `part_type`, `tool_name`, `text_preview`, `reason`, and `status`.
`text_preview` is capped at about 300 characters plus a truncation marker. Non-JSON
stdout lines and JSON parse failures are ignored for event diagnostics and still flow
through the normal `stdout_tail` / `stdout_delta` paths.

The wrapper does not cancel, kill, revert, or clean files based on these fields.
Freshly started jobs should remain `is_stalled=false`. `timed_out` means the MCP wait
window elapsed while the process continued; use `suggested_action`,
`process_running`, and later status calls to decide whether to keep polling or cancel.

Review-risk fields make non-atomic outcomes explicit:

- `review_required`: `true` when an active, failed, cancelled, or timed-out job has
  `new_changed_files`, when a completed job has a path-policy violation, or when
  `no_event_noop_risk=true`.
- `incomplete_changes_risk`: `true` when a failed, cancelled, or timed-out job has
  `new_changed_files`.
- `potential_incomplete_changes_risk`: `true` when an active stalled job already has
  `new_changed_files`. This is an early running-state warning and does not replace
  `incomplete_changes_risk` for failed, cancelled, or timed-out jobs.
- `preexisting_dirty_warning`: non-empty when the worktree already had dirty files
  before the job. In that case, `all_changed_files` cannot be attributed solely to
  this job.

The no-event no-op risk is separate from stalled detection: the process has already
completed, so the wrapper will not cancel, kill, or automatically rerun it. It is
also not reported for direct runs without session reuse, completed jobs with real
stdout JSON events or text output, or jobs with `new_changed_files`. When it is
reported, callers should retry without `session_id` or start a fresh session/server
instead of accepting the result solely because `status=completed`.

An active job with no stdout/stderr and no file changes can still be marked stalled,
but it does not set `potential_incomplete_changes_risk` because no job-scoped file
change has been observed.

Failed, cancelled, and timed-out jobs are not atomic. If they changed files, review
`opencode_coder_diff`, local `git status`, and local `git diff` before accepting or
continuing from the worktree state.

Validation fields are intentionally conservative:

- `validation_status` is `not_run_by_wrapper`.
- `validation_skipped_reason` is `not_run_by_wrapper`.
- `validation_note` reminds callers that the wrapper did not run tests or validation.

A prompt asking OpenCode to run validation is not proof that validation ran. If the
job is not `completed`, prompt-requested validation may not have run at all. Callers
must inspect actual stdout/stderr, an OpenCode report, or local validation output.

### `opencode_coder_diff`

Returns a bounded git diff for a known `opencode_coder` job:

```text
opencode_coder_diff(job_id, max_chars=20000)
```

Parameters:

- `job_id`: job returned by `opencode_coder`.
- `max_chars`: maximum characters returned in `diff`. The wrapper clamps this to a
  safe upper bound.

The diff is based on the job's `new_changed_files`. Tracked files use `git diff` and
`git diff --cached`; untracked regular files are rendered as a `/dev/null` unified
diff using a bounded in-memory read. The tool never writes temp files into the user
project.

Returned fields:

- `job_id`
- `status`
- `working_dir`
- `new_changed_files`
- `preexisting_changed_files`
- `diff`
- `diff_empty_reason`
- `diff_source_files`
- `diff_command_errors`
- `diff_truncated`
- `max_chars`
- `undiffed_files`
- `includes_preexisting_dirty_changes`
- `review_required`
- `incomplete_changes_risk`
- `preexisting_dirty_warning`
- `git_status_available`
- `error`
- `success`

This is a review aid, not a guaranteed pure patch for only the current job. If a file
was already dirty before the job and the job touched it again, the returned git diff
may include earlier dirty changes too; in that case
`includes_preexisting_dirty_changes=true`.

`success=true` means the wrapper produced a usable diff result or there were no
`new_changed_files`. If `new_changed_files` is non-empty but the current worktree no
longer has a displayable diff for those files, the tool returns `success=false` with
`diff_empty_reason`, such as `current_worktree_has_no_diff_for_job_files`. Files that
cannot be rendered as text diffs, including untracked binary files, oversized
untracked files, and non-regular files, are listed in `undiffed_files`, with bounded
details in `diff_command_errors`.

Treat `opencode_coder_diff` as a review aid. When `success=false`,
`diff_empty_reason` is set, `diff_command_errors` is non-empty, or
`undiffed_files` is non-empty, fall back to local `git status` / `git diff` review
before accepting the job result.

The diff result carries file-change review-risk fields such as
`review_required`, `incomplete_changes_risk`, and `preexisting_dirty_warning`. If
any of those fields indicate risk, do not rely on the diff text alone; inspect the
job status and local git state before accepting the worktree. Session no-op risk is
reported on the job result/status via `no_event_noop_risk`; `opencode_coder_diff`
only reviews job-scoped file changes.

If `working_dir` is not a git repository, or git status fails, the tool returns a
structured error with `git_status_available=false`. Missing jobs return
`status="not_found"` and `error="job_not_found"`.

### `opencode_coder_cancel`

Cancels a running `opencode_coder` job by `job_id` and returns the same structured
job result shape as `opencode_coder_status`.

Behavior:

- Unknown or expired `job_id` returns `status="not_found"`, `success=false`, and
  `error="job_not_found"`.
- Completed, failed, or already cancelled jobs are returned as-is; no new terminate
  signal is sent.
- Running jobs are marked with `cancel_requested=true`, then the wrapper sends
  `process.terminate()` to the OpenCode process and waits briefly. If the process is
  still alive, it attempts best-effort process-tree cleanup.
- Cancelled jobs finish with `status="cancelled"` and `success=false`. The
  `exit_code` field keeps the actual process exit code.

Additional cancel fields:

- `cancel_requested`
- `cancel_signal_sent`
- `cancel_kill_sent`
- `process_tree_kill_attempted`
- `process_tree_kill_succeeded`
- `process_tree_kill_error`

Cancel is best-effort. The wrapper only targets the process it started and, if
needed, that process tree; it never scans by `working_dir` or kills unrelated
processes. On Windows, the fallback tree cleanup uses `taskkill /PID <pid> /T /F`
through `subprocess.run([...])`. On non-Windows platforms, newly started jobs are
placed in a new session and the fallback tree cleanup sends `SIGKILL` to that process
group. This is not an absolute guarantee, especially if grandchildren detach into
another session or the platform command fails. Cancel does not roll back file
changes. Callers should inspect `new_changed_files`, `all_changed_files`, and
`policy_violation` to review any changes that happened before cancellation.

### `opencode_server_start`

Starts a managed OpenCode headless server:

```text
opencode serve --hostname <hostname> --port <port>
```

Parameters:

- `working_dir`: directory where the server process should run. Defaults to `"."`.
- `hostname`: bind host. Defaults to `"127.0.0.1"`.
- `port`: bind port. If `0`, the wrapper chooses an available local port.

The tool waits until the selected port accepts TCP connections, then returns:

- `server_id`
- `url`
- `hostname`
- `port`
- `working_dir`
- `pid`
- `status`
- `exit_code`
- `started_at`
- `finished_at`
- `stdout_tail`
- `stderr_tail`
- `process_tree_kill_attempted`
- `process_tree_kill_succeeded`
- `process_tree_kill_error`
- `recovered_from_registry`
- `registry_path`
- `registry_error`
- `process_running`
- `command`
- `success`
- `error`

Managed server metadata is persisted in a small wrapper registry outside the user
project. See [Registry Persistence](#registry-persistence).

### `opencode_server_status`

Returns the same server fields for a known `server_id`. If the server is not present
in memory, the wrapper tries to recover a managed server record from the registry
before returning `not_found` or `lost`.

### `opencode_server_list`

Lists managed OpenCode servers visible to the wrapper:

```text
opencode_server_list(working_dir=None, include_lost=false)
```

Parameters:

- `working_dir`: optional path filter. Relative and absolute paths are normalized the
  same way as `opencode_coder`.
- `include_lost`: when false, stale registry records are cleaned or reported through
  the top-level `registry_error` but omitted from `servers`. When true, stale records
  are included as `status="lost"` entries.

The tool returns:

- `servers`: list of server status objects using the same shape as
  `opencode_server_status`.
- `count`
- `registry_path`
- `registry_error`
- `success`

The list includes in-memory servers and registry records. Registry-only records are
validated with the same pid + TCP check used by `opencode_server_status`; valid
records are recovered into memory and can be used by `opencode_coder(server_id=...)`.
The tool does not start servers automatically and does not scan system processes by
`working_dir`.

### `opencode_server_stop`

Terminates the managed server process and returns the final server status. This is a
best-effort stop. The wrapper first asks the main server process to terminate. If it
does not exit in time, it attempts the same process-tree cleanup strategy used by
`opencode_coder_cancel`: Windows uses `taskkill /PID <pid> /T /F`; non-Windows
launches the server in a new session and kills that process group as a fallback. For
requested stops, `status` is the primary result field; the underlying process may
return a non-zero `exit_code` even when the stop operation succeeds.

If a server was recovered from the registry after an MCP server restart, the wrapper
does not have its original `Popen` handle or stdout/stderr pipes. In that case status
and attach can still work when validation passes, but `opencode_server_stop` returns a
structured limitation instead of blindly killing a pid.

## Registry Persistence

The wrapper persists only managed OpenCode server metadata. Job records, stdout/stderr
buffers, and process handles remain in memory. After an MCP server restart, historical
`opencode_coder_status(old_job_id)` calls can still return `not_found`.

Default registry path:

- Windows: `%LOCALAPPDATA%\LiteOpenCodeMcp\opencode_coder_registry.json`
- Windows fallback: `%TEMP%\LiteOpenCodeMcp\opencode_coder_registry.json`
- Non-Windows: `$XDG_CACHE_HOME/LiteOpenCodeMcp/opencode_coder_registry.json`
- Non-Windows fallback: `~/.cache/LiteOpenCodeMcp/opencode_coder_registry.json`

Override with:

```powershell
$env:OPENCODE_CODER_REGISTRY_PATH = "D:\path\to\opencode_coder_registry.json"
```

The registry is JSON and is written with an atomic temporary-file replace. It stores
only the managed server metadata needed to reattach:

- `server_id`
- `url`
- `hostname`
- `port`
- `working_dir`
- `pid`
- `started_at`
- `command`
- `command_summary`

Recovery happens lazily when `opencode_server_status(server_id)`,
`opencode_server_stop(server_id)`, or `opencode_coder(..., server_id=...)` cannot find
the server in memory. The wrapper verifies that the recorded pid still exists and that
the server host/port accepts a TCP connection. If validation succeeds, the result has
`recovered_from_registry=true`; attached jobs can then use the recovered `server_url`.

Recovery limitations:

- stdout/stderr tail and delta buffers are not recovered.
- the original `Popen` process handle is not recovered.
- failed pid/url validation returns `status="lost"` and removes the stale registry
  record.
- corrupt registry JSON is reported through `registry_error` and does not crash the
  MCP tool.
- the wrapper never scans system processes by `working_dir`.

## Git Snapshots

When a job starts, the wrapper records:

```text
git -C <working_dir> -c core.quotepath=false status --porcelain=v1 --untracked-files=all
```

When the job finishes, and when a running job is queried, it records the same status
again.

Returned file fields:

- `preexisting_changed_files`: files already dirty before OpenCode started.
- `all_changed_files`: current changed files from git status.
- `new_changed_files`: files whose git status or filesystem fingerprint changed
  during this job. This includes new paths and preexisting dirty paths that were
  modified again by the job.
- `changed_files`: compatibility alias for `all_changed_files`.

`new_changed_files` may include a preexisting dirty path that is no longer present in
`all_changed_files` if the job cleaned or otherwise changed that path. This is
intentional: policy checks are about what the job touched, not only what remains
dirty at the end.

If `working_dir` is not a git repository, these lists are empty,
`git_status_available=false`, and `git_status_error` contains the git status message.
Git status failure does not fail the OpenCode job.

## Path Policy

`allowed_paths` and `forbidden_paths` are checked against `new_changed_files`, not
against files that were already dirty before the job. This avoids reporting
preexisting worktree changes as this job's policy violation. If a preexisting dirty
file is modified again by the job, it is included in `new_changed_files` and checked
against the path policy.

Rules:

- The wrapper asks git for `rev-parse --show-toplevel` and treats changed files from
  `git status` as git-root-relative when that root is available.
- Relative policy paths are tried both relative to `working_dir` and relative to the
  git root.
- Multi-component relative policy paths may also match as a git-relative suffix. This
  supports Unity subdirectory layouts where git reports
  `CFantacy-TurnBasedStrategy/Assets/...` but the caller allowed `Assets/...`.
- Absolute paths are allowed.
- `\` and `/` are normalized.
- Windows matching is case-insensitive.
- A policy path matches the exact file path or any descendant path, so directory paths
  match their children.
- `forbidden_paths` take priority over `allowed_paths` in the structured result.

If a new changed file is outside `allowed_paths`, the result includes
`policy_violation=true` and lists it in `extra_changed_files`. If a new changed file
matches `forbidden_paths`, the result includes `policy_violation=true` and lists it in
`forbidden_changed_files`.

Policy violations are report-only. The wrapper does not revert, delete, or otherwise
modify files.

The `path_policy` result includes bounded diagnostics to make false positives easier
to inspect:

- `working_dir`
- `git_root` / `git_root_error`
- `allowed_paths_normalized`
- `forbidden_paths_normalized`
- `checked_files_basis`
- `file_matches`
- `match_rule`

If git root detection fails, the wrapper keeps the previous working-dir-relative
fallback and records the reason in `path_policy.git_root_error`.

## Status Values

- `running`: process is still active inside the current MCP wait window.
- `timed_out`: MCP wait window elapsed, but the OpenCode process continues.
- `completed`: process finished with exit code `0`.
- `failed`: process could not start or finished with a non-zero exit code.
- `cancelled`: cancellation was requested and the OpenCode process exited.
- `not_found`: status query used an unknown or expired `job_id`.
- `stopped`: managed server was stopped by request.
- `lost`: a persisted managed server record existed, but pid/url validation failed.

## Compatibility

The wrapper still returns legacy fields:

- `success`
- `output`
- `return_code`

New callers should prefer `status`, `exit_code`, `stdout_cursor` /
`stderr_cursor`, `suggested_action`, `work_summary_text`, `new_changed_files`, risk
fields, and `opencode_coder_diff`.
For polling loops, prefer compact `opencode_coder_status` responses with cursor
metadata. `stdout_delta` / `stderr_delta` and `recent_events` are debug diagnostics,
not the normal calling contract. Tail fields are bounded by line and character
limits, but neither tool includes them unless `include_tail=true`. Legacy `output`
is also empty unless `include_output=true`.

## Integration Smoke Test

The real OpenCode integration smoke test is opt-in and skipped by default. It starts
a managed `opencode serve` process, runs `opencode_coder` through `--attach`, polls
`opencode_coder_status`, and verifies git snapshot fields in a temporary git
repository.

The smoke test may perform a real model call and can depend on network access,
authentication, configured OpenCode provider state, and any associated usage costs.
It writes only to a `TemporaryDirectory` created by the test and does not touch user
projects.

PowerShell:

```powershell
$env:OPENCODE_CODER_RUN_INTEGRATION = "1"
python -B -m unittest -v test_opencode_coder.OpenCodeCoderIntegrationTests
Remove-Item Env:\OPENCODE_CODER_RUN_INTEGRATION
```

If `opencode` is not on `PATH`, or if `OPENCODE_CODER_RUN_INTEGRATION` is not set to
`1`, the integration test is skipped. Failures from model, network, or authentication
problems include the structured job/server status and stdout/stderr tails for
diagnosis.

## Backlog

- Decide whether the real OpenCode integration smoke test should become a scheduled
  or CI-gated check.
- Full job recovery across MCP server restarts. Managed server registry MVP is
  implemented, but job history and output pipes are still memory-only.
- Stronger cross-platform process-tree cleanup verification with real child process
  trees.
- More detailed test/validation extraction from OpenCode output.
- Automatic rollback or cleanup for policy violations.
