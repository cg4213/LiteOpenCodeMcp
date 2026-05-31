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
- `allow_concurrent`: defaults to `false`. When false, a second job for the same
  normalized `working_dir` is rejected and returns the existing running `job_id`.

`timeout_seconds` is capped by `OPENCODE_CODER_MAX_WAIT_SECONDS` to avoid the MCP
client timing out before the wrapper can return job context. The default cap is
`110` seconds. Returned results include both `requested_timeout_seconds` and
`effective_timeout_seconds`.

### `opencode_coder_status`

Queries an in-memory job by `job_id`.

Returns a structured result including:

- `job_id`
- `status`
- `working_dir`
- `pid`
- `exit_code`
- `summary`
- `changed_files`
- `tests_run`
- `validation_skipped_reason`
- `stdout_tail`
- `stderr_tail`
- `started_at`
- `finished_at`
- `command`

Finished jobs are retained in memory for at least
`OPENCODE_CODER_FINISHED_JOB_TTL_SECONDS` seconds. The default is `3600`.

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

## Backlog

- `allowed_paths` sandboxing.
- Explicit `opencode_coder_cancel`.
- Automatic diff snapshots before and after a job.
- More detailed test/validation extraction from OpenCode output.
- Optional persistent job state across MCP server restarts.
