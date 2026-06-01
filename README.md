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

When `server_id` is omitted, `opencode_coder` keeps the original direct
`opencode run` behavior. When `server_id` is provided, it runs through an attached
headless server:

```text
opencode run --attach <server_url> --dir <working_dir> --format json --dangerously-skip-permissions ...
```

The wrapper parses `sessionID` from JSON events written to stdout. If no valid JSON
event has appeared yet, `session_id` can be `null`.

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
- `tail_max_chars`: optional tail/output character limit when either include flag is
  enabled. The wrapper clamps this to a safe upper bound.

When `wait_seconds` is greater than zero, status waits until the job completes, new
stdout/stderr arrives, a new file change is detected, or the wait expires. Status
queries never start a new OpenCode process.

By default, status responses are compact: `stdout_tail`, `stderr_tail`, and `output`
are present for compatibility but empty. When a cursor is provided, the response
includes `stdout_delta` and/or `stderr_delta` with only the text after that cursor.
Cursors are character offsets inside a bounded in-memory buffer; they are not
persistent log file offsets. Use `include_tail=true` only when debugging a job and
cap it with `tail_max_chars` if the output may be large.

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
- `stdout_tail`
- `stderr_tail`
- `stdout_delta`
- `stderr_delta`
- `stdout_cursor`
- `stderr_cursor`
- `stdout_delta_truncated`
- `stderr_delta_truncated`
- `started_at`
- `finished_at`
- `first_output_at`
- `first_change_at`
- `last_activity_at`
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
)
print(next_status["stdout_delta"])
print(next_status["stderr_delta"])
```

Delta buffers are bounded by `MAX_DELTA_BUFFER_CHARS` inside the wrapper. If a cursor
is older than the retained buffer, the wrapper returns the currently available suffix
and sets `stdout_delta_truncated` or `stderr_delta_truncated` to `true`. Polling loops
should use compact status plus cursor/delta fields; request tail fields only for
targeted diagnostics:

```python
debug_status = opencode_coder_status(
    job_id,
    include_tail=True,
    include_output=True,
    tail_max_chars=4000,
)
```

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
`stderr_cursor`, and delta fields.
For polling loops, prefer compact `opencode_coder_status` responses with
`stdout_cursor` / `stderr_cursor` and delta fields. Tail fields are bounded by line
and character limits, but status does not include them unless `include_tail=true`.
Legacy `output` is also omitted from status unless `include_output=true`.

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
