# LiteOpenCodeMcp

Lightweight MCP wrapper for running `opencode` as a coding agent from Codex.

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

When `wait_seconds` is greater than zero, status waits until the job completes, new
stdout/stderr arrives, a new file change is detected, or the wait expires. Status
queries never start a new OpenCode process.

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
- `started_at`
- `finished_at`
- `first_output_at`
- `first_change_at`
- `last_activity_at`
- `command`
- `wait_policy`

Finished jobs are retained in memory for at least
`OPENCODE_CODER_FINISHED_JOB_TTL_SECONDS` seconds. The default is `3600`.

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
- `process_running`
- `command`
- `success`
- `error`

Server state is in memory only and is not written to the user project.

### `opencode_server_status`

Returns the same server fields for a known `server_id`.

### `opencode_server_stop`

Terminates the managed server process and returns the final server status. This is a
best-effort main-process termination; full process-tree cleanup is a backlog item.
For requested stops, `status` is the primary result field; the underlying process may
return a non-zero `exit_code` even when the stop operation succeeds.

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

- Relative paths are resolved against `working_dir`.
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

## Status Values

- `running`: process is still active inside the current MCP wait window.
- `timed_out`: MCP wait window elapsed, but the OpenCode process continues.
- `completed`: process finished with exit code `0`.
- `failed`: process could not start or finished with a non-zero exit code.
- `not_found`: status query used an unknown or expired `job_id`.

## Compatibility

The wrapper still returns legacy fields:

- `success`
- `output`
- `return_code`

New callers should prefer `status`, `exit_code`, `stdout_tail`, and `stderr_tail`.
Tail fields are bounded by line and character limits to keep MCP responses compact.

## Backlog

- Explicit `opencode_coder_cancel`.
- stdout/stderr cursor support for incremental tail reads.
- Real OpenCode integration smoke tests.
- Persistent server/job registry across MCP server restarts.
- Process-tree cleanup for server stop.
- More detailed test/validation extraction from OpenCode output.
- Automatic rollback or cleanup for policy violations.
