# LiteOpenCodeMcp 中文说明

[English README](README.md)

[已有对话接入提示词](EXISTING_THREAD_OPENCODE_PROMPT.zh-CN.md)

LiteOpenCodeMcp 是一个轻量 MCP 包装层，用于在 Codex 中把 `opencode` 当作可调度的代码工作对话来使用。它的重点不是替代 OpenCode，而是补齐调用侧需要的任务状态、进度轮询、路径约束、server/session 复用、取消、diff 审查和跨 MCP 重启后的 server 发现能力。

## 安装与环境准备

### 前置要求

- Python 3.10 或更新版本。
- Git CLI 已加入 `PATH`。
- OpenCode CLI 已加入 `PATH`。
- OpenCode 已完成可用的模型 provider / 认证配置，能够执行真实模型请求。

OpenCode 官方安装文档见 <https://opencode.ai/docs/>。常见安装方式：

```powershell
# 有 Node.js/npm 时可跨平台使用
npm install -g opencode-ai

# 验证
opencode --version
opencode run "hello"
```

Windows 上 OpenCode 官方文档推荐 WSL 以获得更好的兼容性；如果使用原生 Windows，npm、Chocolatey、Scoop 等方式也可以工作。关键是启动 MCP server 的同一个环境必须能找到 `opencode` 可执行文件。

使用本 wrapper 执行真实任务前，需要先配置 OpenCode 认证：

```powershell
opencode auth login
opencode auth list
```

OpenCode 也可以按 provider 配置从环境变量或项目 `.env` 文件读取 API key。

### Python 环境

本项目使用官方 MCP Python SDK 的 FastMCP import path：

```python
from mcp.server.fastmcp import FastMCP
```

建议使用独立虚拟环境：

```powershell
cd D:\Develop\LiteOpenCodeMcp

python -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
python -m pip install "mcp[cli]"
```

Linux/macOS：

```bash
cd /path/to/LiteOpenCodeMcp

python3 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install "mcp[cli]"
```

本地快速验证：

```powershell
python -m py_compile opencode-coder.py test_opencode_coder.py
python -B -m unittest -v test_opencode_coder.py
```

真实 OpenCode integration smoke test 默认不运行；需要时见“真实 OpenCode Smoke Test”。

### MCP 客户端配置

在你的 MCP 客户端中把 `opencode-coder.py` 注册为 stdio MCP server。典型 JSON 配置如下：

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

请按本机实际路径调整 `command` 和 `args`。修改 MCP server 文件或工具 schema 后，需要重启 MCP 客户端或 MCP server 进程，新的工具 schema 才会加载。

可选环境变量：

- `OPENCODE_CODER_MAX_WAIT_SECONDS`：限制 MCP 同步等待上限，默认 `110`。
- `OPENCODE_CODER_FINISHED_JOB_TTL_SECONDS`：完成 job 的内存保留时间，默认 `3600`。
- `OPENCODE_CODER_REGISTRY_PATH`：覆盖 managed server registry JSON 路径。
- `OPENCODE_CODER_RUN_INTEGRATION`：仅在运行真实 integration smoke test 时设为 `1`。

## 推荐使用方式

推荐把 LiteOpenCodeMcp 当作“任务调度器 + 紧凑 review 面板”，不要当作会持续吐完整终端输出的同步命令。

- 默认复用 `server_id`。session 复用的目标是减少重复上下文读取和 token 消耗，但不是绝对安全机制。同一 `working_dir`、同一 feature/topic、上一 job `completed/success`、无 `no_event_noop_risk`、无异常 terminal status、无明显误解或不完整改动时，优先复用上一轮健康的 `session_id`。
- 同一 phase 内的连续修正、小步 review fix 通常适合复用 session。跨 phase 时不要机械禁止或机械复用，应重新评估：如果仍属于同一工具代码主题、上下文连续、风险边界没有显著变化，可以复用 session，但 prompt 必须重新声明目标、允许路径和禁止范围；如果任务类型、允许路径或风险边界明显变化，应新开 session。
- 不应跨 `working_dir`、跨仓库根、跨 Unity 项目复用 session。从纯代码/文本任务切换到 Unity 资产操作时，不应继续使用 OpenCode，应改用 Unity Skills / Unity Editor / 用户手动流程。上一 job 出现 `no_event_noop_risk`、`session_reuse_risk`、`policy_violation`、`failed`、`cancelled`、`timed_out`、明显误解或不完整改动时，应新开 session。
- 大工作提示词必须小步快跑：每个 OpenCode job 只交付一个明确目标，尽量避免把“多文件迁移 + 验证 + 文档 + 报告”塞进同一轮；完成后 review，再派发下一步。
- 每次实际派发 OpenCode 前，应先在主对话输出将要交给 `opencode_coder` 的提示词，让用户能理解本轮目标、范围、允许/禁止路径、验收和测试要求。提示词过长时也应至少输出结构化摘要和关键约束。
- 长任务优先使用 `wait_policy="start_only"` 或 `"first_output"`，让主对话尽快拿回控制权，
  再优先用 `opencode_coder_wait` 等待关键变化；只有 wait 不可用或需要 cursor/delta 诊断时，才回退 compact status 轮询。
- 轮询时优先使用 `caller_update_recommended`、`caller_update_reason`、`next_poll_after_seconds`
  控制汇报频率：派发后先用轻量等待获取第一个信号：
  `opencode_coder_wait(job_id, wait_seconds=120, return_on="interesting", include_status=false)`
  只返回 `job_id`、`status` 和 wait 结果；当 wait 发现 interesting 更新（如 terminal、首次变更、
  stalled、policy violation）后，再按需调用 `opencode_coder_status` 获取完整诊断快照。
  后续优先继续用 wait；普通状态查询或向用户汇报的节奏默认不低于 120 秒。`next_poll_after_seconds` 对 terminal/not_found/异常阶段默认为 `0`，对普通运行阶段建议 `120`，只作为 status fallback 的诊断参考，不要按短建议快速追问。普通 running 且只有近期普通活动时可静默继续；
  terminal、首次变更、stalled、policy violation、验证观察、no-event no-op 风险等才值得汇报。
- 普通轮询不要传 `include_tail`、`include_output`、`include_delta`。这些是调试开关，只有需要看原始 stdout/stderr 或 event 流时才打开，并配合字符上限。
- 不要只因 `status=completed` 或 `success=true` 就接受结果。完成后必须看 `suggested_action`、`work_summary_text`、变更文件、path policy、stall/risk、`no_event_noop_risk` 和 validation 字段。
- 如果 `no_event_noop_risk=true`，应不传 `session_id` 重试，或新开 session/server；不要把它当作正常完成。
- wrapper 不主动运行项目验证，也不会自动回滚半成品。需要结合 `opencode_coder_diff(job_id)`，必要时回退到本地 `git status` / `git diff` 复核。
- 初次执行不要轻易 cancel。首轮 job 即使 first change 前等待较久，也应优先用 `opencode_coder_wait` 观察到终止状态、明确 stall / policy 风险、外部等待或用户要求后，再考虑取消；不要只因为“看起来在思考”就中止。

长任务推荐流程：

1. 用 `opencode_server_start` 启动一个 managed OpenCode server。
2. 同一工作话题连续 job 默认带上上一轮健康完成的 `session_id`；换话题、换项目或上一轮有 no-op/失败/取消/超时风险时新开 session。
3. 派发前在主对话输出本轮 OpenCode 提示词或结构化摘要，明确目标、范围、路径策略、验收和验证。
4. 用 `opencode_coder(..., server_id=..., session_id=..., wait_policy="start_only")` 派发任务，尽快拿到 `job_id`。
5. 用 `opencode_coder_wait(job_id, wait_seconds=120, return_on="interesting", include_status=false)` 轻量等待关键变化；普通轮询/汇报间隔不低于 120 秒。
6. wait 返回 `interesting_update=true` 后，再用 `opencode_coder_status(job_id)` 获取完整诊断；如果 wait 不可用或需要 cursor/delta 调试，再回退到 compact status 轮询。
7. 用 `opencode_coder_diff(job_id)` 审查本 job 涉及的变更。
8. 每次 OpenCode job 完成后，如果主对话/FO review 通过、验证通过且无用户禁止，应做一次 job 级 commit；只 stage 和 commit 本次调用相关改动。
9. 需要中止时调用 `opencode_coder_cancel(job_id)`，但初次执行不要轻易取消。
10. 新对话或 MCP 重启后，用 `opencode_server_list` 找回可复用的 managed server。

如果不传 `server_id`，`opencode_coder` 仍保持直接执行 `opencode run` 的兼容行为。

## 工具列表

### `opencode_coder`

在目标工作目录中运行 OpenCode。

直接模式：

```text
opencode run --format json --dangerously-skip-permissions <prompt>
```

附加 server 模式：

```text
opencode run --attach <server_url> --dir <working_dir> --format json --dangerously-skip-permissions ...
```

参数：

- `prompt`：传给 OpenCode 的任务提示词。
- `working_dir`：OpenCode 运行目录，默认 `"."`。
- `timeout_seconds`：本次 MCP 调用最多等待多久。超时后返回 `timed_out`，但 OpenCode 进程继续在后台运行。
- `wait_policy`：等待策略，默认 `"completion"`，用于兼容旧调用方。
- `allow_concurrent`：默认 `false`。同一规范化 `working_dir` 已有 active job 时，默认拒绝第二个任务并返回已有 `job_id`。
- `allowed_paths`：可选，本 job 允许新增改动的文件或目录。
- `forbidden_paths`：可选，本 job 禁止新增改动的文件或目录。
- `server_id`：可选，来自 `opencode_server_start` 的 managed server id。
- `session_id`：可选，传给 OpenCode 的 `--session`。
- `continue_last`：可选，传递 `--continue`。
- `fork_session`：可选，传递 `--fork`。
- `title`：可选，传递 `--title`。
- `include_tail`：可选调试开关，默认 `false`，因此结果中的 `stdout_tail` / `stderr_tail` 为空。
- `include_output`：可选兼容/调试开关，默认 `false`，因此旧字段 `output` 为空。
- `include_delta`：可选调试开关，默认 `false`，因此即使传入 cursor 也不会返回原始 `stdout_delta` / `stderr_delta` 文本。
- `recent_events_limit`：返回的 event 摘要数量上限，默认 `5`；调试时可传 `20` 获取完整保留窗口。
- `delta_max_chars`：`include_delta=true` 时的 delta 响应字符上限，仅影响本次响应。
- `tail_max_chars`：启用 tail/output 时的字符上限。

`timeout_seconds` 会受到 `OPENCODE_CODER_MAX_WAIT_SECONDS` 限制，默认上限是 `110` 秒，避免 MCP 客户端先超时导致丢失 job 上下文。返回结果中会同时包含 `requested_timeout_seconds` 和 `effective_timeout_seconds`。

等待策略：

- `"completion"`：等待进程完成或到达 `timeout_seconds`。这是默认行为。
- `"start_only"`：进程启动并注册 job 后尽快返回，适合快速拿 `job_id`。
- `"first_output"`：等到 stdout/stderr 有输出、进程完成或超时。
- `"first_change"`：等到 `new_changed_files` 非空、进程完成或超时。

长任务建议使用 `"start_only"` 或 `"first_output"`，让调用方尽快恢复控制权，再用 `opencode_coder_wait(..., include_status=false)` 进行轻量长轮询。只有 wait 返回 interesting 更新，或需要完整诊断时，再调用 `opencode_coder_status`。

默认 `opencode_coder` 返回 compact 工作反馈：`status`、`success`、`suggested_action`、`summary`、`work_summary_text` / `last_text_output`、变更文件列表、风险字段、轻量诊断和 cursor 元数据。默认不返回 OpenCode 原始 stdout/stderr tail、stdout JSON event 流、delta 文本或旧字段 `output` 的大内容；需要调试时必须显式打开上面的参数。

不建议跨不同 `working_dir` 或不同仓库根目录复用 `session_id`。如果 attached job 已经
`completed` 但返回 `no_event_noop_risk=true`，不要把 `completed` / `success=true`
当作正常完成；应检查 session 范围，或不传 `session_id` 重试，也可以新开 session/server。

主要返回字段：

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
- `server_recovered_from_registry`
- `changed_files`
- `preexisting_changed_files`
- `all_changed_files`
- `new_changed_files`
- `policy_violation`
- `extra_changed_files`
- `forbidden_changed_files`
- `git_status_available`
- `git_status_error`
- `stdout_tail`
- `stderr_tail`
- `started_at`
- `finished_at`
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
- `progress_phase`
- `progress_message`
- `caller_update_recommended`
- `caller_update_reason`
- `next_poll_after_seconds`
- `time_to_first_output_seconds`
- `time_to_first_event_seconds`
- `time_to_first_tool_seconds`
- `time_to_first_change_seconds`
- `seconds_since_last_event`
- `seconds_since_last_change`
- `tool_activity_summary`
- `long_gap_segments`
- `root_cause_guess`
- `session_reuse_detected`
- `session_reuse_mode`
- `session_reuse_risk`
- `session_reuse_note`
- `same_session_recent_job_count`
- `same_session_last_job_status`
- `likely_preexisting_from_same_session`
- `likely_preexisting_same_session_files`
- `observed_validation_summary`
- `observed_validation_tools`
- `observed_validation_result`
- `observed_validation_errors_count`
- `is_stalled`
- `stall_reason`
- `suggested_action`
- `review_required`
- `incomplete_changes_risk`
- `potential_incomplete_changes_risk`
- `preexisting_dirty_warning`
- `validation_status`
- `validation_note`
- `wait_policy`
- `success`

### `opencode_coder_status`

按 `job_id` 查询内存中的 job 状态。

参数：

- `job_id`：`opencode_coder` 返回的 job id。
- `wait_seconds`：可选短等待，限制在 `0..30` 秒，默认 `0`。
- `stdout_cursor`：可选，前一次结果返回的 stdout cursor。
- `stderr_cursor`：可选，前一次结果返回的 stderr cursor。
- `include_tail`：可选调试开关，默认 `false`，因此轮询不会反复返回 `stdout_tail` / `stderr_tail`。
- `include_output`：可选兼容/调试开关，默认 `false`，因此轮询不会反复返回旧字段 `output`。
- `include_delta`：可选调试开关，默认 `false`，因此 compact 轮询只推进 cursor，不返回原始 `stdout_delta` / `stderr_delta` 文本。
- `recent_events_limit`：返回的 event 摘要数量上限，默认 `5`；调试时可传 `20` 获取完整保留窗口。
- `delta_max_chars`：`include_delta=true` 时的 delta 响应字符上限，仅影响本次响应。
- `tail_max_chars`：启用 tail/output 时的字符上限，会被包装层限制到安全范围内。

当 `wait_seconds > 0` 时，status 会等待到以下任一情况发生：job 完成、新 stdout/stderr 到达、新文件变更被检测到、等待超时。status 查询不会启动新的 OpenCode 进程。

默认 status 是 compact 响应：`stdout_tail`、`stderr_tail`、`output`、`stdout_delta` 和 `stderr_delta` 字段仍保留，但内容为空。cursor 仍会推进到当前最新偏移，方便调用方之后从“现在”开始调试轮询。需要返回 cursor 之后的文本时，显式传 `include_delta=true`。cursor 是包装层内存缓冲区中的字符偏移，不是持久化日志文件偏移；raw 字段只建议用于定向诊断。

相关字段：

- `stdout_delta`
- `stderr_delta`
- `stdout_cursor`
- `stderr_cursor`
- `stdout_delta_truncated`
- `stderr_delta_truncated`
- `stdout_delta_response_truncated`
- `stderr_delta_response_truncated`
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
- `progress_phase`
- `progress_message`
- `caller_update_recommended`
- `caller_update_reason`
- `next_poll_after_seconds`
- `time_to_first_output_seconds`
- `time_to_first_event_seconds`
- `time_to_first_tool_seconds`
- `time_to_first_change_seconds`
- `seconds_since_last_event`
- `seconds_since_last_change`
- `tool_activity_summary`
- `long_gap_segments`
- `root_cause_guess`
- `session_reuse_detected`
- `session_reuse_mode`
- `session_reuse_risk`
- `session_reuse_note`
- `same_session_recent_job_count`
- `same_session_last_job_status`
- `likely_preexisting_from_same_session`
- `likely_preexisting_same_session_files`
- `observed_validation_summary`
- `observed_validation_tools`
- `observed_validation_result`
- `observed_validation_errors_count`
- `is_stalled`
- `stall_reason`
- `suggested_action`
- `review_required`
- `incomplete_changes_risk`
- `potential_incomplete_changes_risk`
- `preexisting_dirty_warning`
- `validation_status`
- `validation_note`

完成的 job 默认至少在内存中保留 `OPENCODE_CODER_FINISHED_JOB_TTL_SECONDS` 秒，默认是 `3600`。

轮询示例：

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

如果 cursor 早于内存 buffer 保留范围，wrapper 会返回当前仍可用的后缀，并设置 `stdout_delta_truncated` / `stderr_delta_truncated`。如果 `delta_max_chars` 截断了本次响应，会设置 `stdout_delta_response_truncated` / `stderr_delta_response_truncated`，同时 cursor 仍推进到最新位置，避免下一次重复吐出同一段大输出。普通轮询建议只消费 compact status 和 cursor；需要查看 raw 输出时显式开启：

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

### Job 诊断与 Review 风险

每个 job result 都会返回轻量诊断字段：

- `runtime_seconds`：从 `started_at` 到 `finished_at` 的秒数；job 仍 active 时计算到当前时间。
- `idle_seconds`：距离最近一次可信活动的秒数，活动时间按 `last_activity_at`、`first_output_at`、`started_at` 取最合理值。延迟首次观察到文件变更本身不会重置这个计时。
- `is_stalled`：只对 active job 判断。为 `true` 表示超过 wrapper 的卡住阈值，但不等同于 failed。
- `stall_reason`：可能是 `changed_files_no_recent_activity`、`no_output_no_change_after_start`、`no_recent_activity` 或 `timed_out_waiting_for_completion`。
- `suggested_action`：给调用方的下一步建议，例如 `continue_polling`、`continue_polling_or_consider_cancel`、`consider_cancel`、`review_diff_then_consider_cancel`、`review_diff_or_git_status`、`check_session_or_retry_without_session`。
- `progress_phase`：紧凑的外部可观察阶段，例如 `starting`、`waiting_first_output`、`reading_context`、`planning_or_reasoning`、`long_context_or_planning`、`editing`、`validating`、`finalizing`、`stalled`、`no_event_noop_risk`、`completed`、`failed`、`cancelled`、`timed_out` 或 `not_found`。
- `progress_message`：面向人的短说明，不包含长 stdout、文件内容或完整 event JSON。
- `caller_update_recommended`、`caller_update_reason`、`next_poll_after_seconds`：给调用方的轮询/汇报建议。普通 running 且只有近期普通活动时通常应静默继续；terminal 状态、policy violation、首次观察到文件变更、stalled、验证观察、长时间无 first change、no-event no-op 风险才更适合汇报。
- `time_to_first_output_seconds`、`time_to_first_event_seconds`、`time_to_first_tool_seconds`、`time_to_first_change_seconds`、`seconds_since_last_event`、`seconds_since_last_change`：外部可观测耗时诊断。没有对应观察时返回 `null`，不会用 `0` 混淆。
- `tool_activity_summary`：基于有界 tool event 摘要统计的 read/edit/bash/list/unity/other 计数。
- `long_gap_segments`：最多 3 条紧凑空窗摘要，只包含 `duration_seconds`、`after`、`before`、`phase_guess`，文本有长度上限，不塞 raw 输出。
- `root_cause_guess`：保守启发式，例如 `slow_startup_or_attach`、`slow_before_first_event`、`slow_context_reading`、`slow_before_first_change`、`slow_after_edit`、`slow_validation`、`no_event_noop`、`stalled_running`、`completed_normally` 或 `unknown`。

这些 progress / root cause 字段只解释 wrapper 能观察到的进程输出、git snapshot 和有界 OpenCode event 摘要；它们是 heuristic，不代表能读取模型内部思考，也不能当作绝对根因。

wrapper 还会把 OpenCode 写到 stdout 的 JSON lines 解析成有界 event 诊断信息。这些字段只用于观察，不会改变执行策略：

- `recent_events`：最近的 OpenCode stdout JSON event 摘要。结果默认最多返回 5 条；调试时可传 `recent_events_limit=20` 获取完整保留窗口。wrapper 不保存完整原始 event。
- `recent_event_count`：本 job 观察到的 JSON event 总数；当超过上限后会大于 `len(recent_events)`。
- `last_event_type`、`last_event_at`、`last_event_summary`：最后一个 event 的类型、时间戳和有界结构化摘要。
- `last_text_output`：最近一次模型文本 event 的短 preview。
- `work_summary_text` / `assistant_last_text`：最近一次 assistant 文本 preview 的别名；调用方理解 completed job 的工作反馈时应优先看这里，而不是 raw stdout。
- `last_tool_name`、`last_tool_event`：最近一次观察到的工具活动。
- `last_step_reason`、`last_step_status`：event 中出现的最近 step reason / status。
- `last_session_id`：event JSON 中最近观察到的 `sessionID`。
- `diagnostic_phase`：粗略阶段，例如 `no_event_seen`、`model_text`、`tool_activity`、`step_started`、`step_finished`、`process_running_no_recent_event`、`process_finished` 或 `unknown`。
- `diagnostic_note`：面向调用方的短说明。stalled job 会结合 `is_stalled` / `stall_reason` 说明最后观察到的 event 阶段，但不会替代 `suggested_action`。
- `no_event_noop_risk`：attached completed job 显式使用 session 复用参数（`session_id`、`continue_last` 或 `fork_session`）且没有 stdout JSON event、没有本 job 范围内的文件变更、也没有有效 stdout/stderr 输出时为 `true`。
- `no_event_noop_reason`：`no_event_noop_risk=true` 时的机器可读原因。

event 摘要只包含 `type`、`timestamp`、`sessionID`、`messageID`、`part_type`、`tool_name`、`text_preview`、`reason`、`status` 等小字段。`text_preview` 约 300 字符后截断并加标记。非 JSON stdout 行或 JSON 解析失败不会影响 job 执行，也不会影响原有 `stdout_tail` / `stdout_delta`。

wrapper 不会因为这些字段自动 cancel、kill、回滚或清理文件。刚启动不久的 job 不应被标记为 stalled。`timed_out` 只表示本次 MCP 等待窗口结束但进程仍可能继续运行，应结合 `suggested_action`、`process_running` 和后续 status 决定继续轮询还是取消。

半成品风险字段：

- `review_required`：active、failed、cancelled、timed_out 且存在 `new_changed_files` 时为 `true`；completed 但有 path policy violation 或 `no_event_noop_risk=true` 时也为 `true`。
- `incomplete_changes_risk`：failed、cancelled、timed_out 且存在 `new_changed_files` 时为 `true`。
- `potential_incomplete_changes_risk`：active 且 stalled 的 job 已经存在 `new_changed_files` 时为 `true`。这是运行中疑似半成品的提前提醒，不替代 failed、cancelled、timed_out 场景下的 `incomplete_changes_risk`。
- `preexisting_dirty_warning`：任务开始前工作区已有 dirty 文件时非空，提醒 `all_changed_files` / diff 可能混入本 job 前已有改动，不能简单归因给本 job。连续使用 OpenCode 时该 warning 很常见，尤其是同一任务分多轮执行、后一轮基于前一轮未提交改动继续修正时；它本身不一定代表风险，但会增加 job 归因和 diff review 成本。

为了降低 `preexisting_dirty_warning` 并提升每个 job 的归因清晰度，采用本规范的调用方建议在每轮 OpenCode job 完成后，由主对话 / Feature Owner 完成 review 和验证；通过后默认做一次 job 级 commit，除非用户明确要求暂不提交。commit 不应由 OpenCode 默认执行，除非用户明确授权它处理提交。commit 前必须只 stage 本次调用相关文件，不得把用户已有脏改动或无关文件带入 commit；如果同一文件内混有用户手改和 OpenCode 改动，应使用 hunk 级别 review/stage，或先让用户确认。

推荐两种节奏：

- 每个通过 review 的 OpenCode job commit 一次：这是默认推荐节奏，job 归因最清晰，warning 最少，但 commit 数量较多。
- 每个 phase 完成后 commit 一次：commit 更少，但 phase 内连续修正仍可能出现 `preexisting_dirty_warning`。

无论是否 commit，只要出现 `preexisting_dirty_warning`，都必须用 `opencode_coder_diff` 或本地 `git status` / `git diff` 复核，不得只看 `completed` / `success`。

`no_event_noop_risk` 和 stalled 是两类问题：此时进程已经 completed，wrapper 不会 cancel、kill，也不会自动重跑。direct run 且没有 session 复用参数、completed 但有真实 stdout JSON event / 文本输出、或有 `new_changed_files` 的 job 不应被标记为 no-op 风险。一旦出现该字段，应优先不传 `session_id` 重试，或新开 session/server，而不是只因 `status=completed` 就接受结果。

session 复用诊断只基于当前 MCP 进程内存中仍可见的 job，重启后历史可能不可用：

- `session_reuse_detected`、`session_reuse_mode`：当前 job 显式使用 `session_id`、`continue_last` 或 `fork_session` 时标记。
- `same_session_recent_job_count`、`same_session_last_job_status`：只统计当前内存中可见的同 session job；不要求跨 MCP 重启准确。
- `session_reuse_risk`、`session_reuse_note`：仅在有明显风险时置风险，例如 `no_event_noop_risk`、同 session 可见历史存在 working_dir 不一致、上一同 session job 异常结束等。`session_reuse_risk=false` 不等于绝对安全。
- `likely_preexisting_from_same_session`、`likely_preexisting_same_session_files`：当前 job 的 preexisting dirty 路径与内存中可见的上一同 session job 改动路径有交集时给出提示；这是提示，不是证明。

受控 session 复用建议：同一 `working_dir`、同一 feature/topic、上一 job `completed/success`、无 `no_event_noop_risk`、无 `session_reuse_risk`、无异常 terminal status、无明显误解或不完整改动时，可以优先复用 session。同一 phase 内的连续修正、小步 review fix 通常适合复用；跨 phase 时应重新评估任务边界，如果仍属于同一工具代码主题、上下文连续、风险边界没有显著变化，可以复用，但 prompt 必须重新声明目标、允许路径和禁止范围；如果任务类型、允许路径或风险边界明显变化，应新开 session。不要跨 Unity 项目、跨仓库根、跨 `working_dir` 复用 session。即使 `session_reuse_risk=false`，也不能把 session 复用视为完全安全，仍必须 review diff 和风险字段。

active job 如果长时间没有 stdout/stderr 且没有文件变更，也可以被标记为 stalled；但由于没有观察到本 job 的文件改动，不会设置 `potential_incomplete_changes_risk`。

failed、cancelled、timed_out 都不具备原子性；如果它们留下文件变更，必须 review `opencode_coder_diff`、本地 `git status` 和本地 `git diff`，再决定是否接受或继续处理当前工作区。

验证字段保持保守：

- `validation_status` 为 `not_run_by_wrapper`。
- `validation_skipped_reason` 为 `not_run_by_wrapper`。
- `validation_note` 会提醒调用方 wrapper 没有主动运行测试或验证。
- `observed_validation_summary`、`observed_validation_tools`、`observed_validation_result`、`observed_validation_errors_count` 只描述从 OpenCode 工具执行信号中观察到的验证迹象，例如 bash/shell/command/test 工具事件或 Unity Skills 工具事件，不表示 wrapper 自己执行了验证。

prompt 里要求 OpenCode 运行验证，不代表验证真的执行了。job 未 `completed` 时，prompt 内要求的验证很可能没有执行。调用方必须查看实际 stdout/stderr、OpenCode report 或本地验证结果。
普通模型文本、README 内容、报告内容或普通 stdout 只是提到 `python -m py_compile`、`debug_check_compilation`、`git diff --check` 等命令时，不算验证执行。`read`、`open`、`grep`、`rg`、`search`、`glob`、`ls`、`get-childitem` 等读取/搜索/列表工具读到这些命令，也不算验证执行。`observed_validation_result` 没看到验证执行型工具活动时为 `none`；看到命令/工具但无法保守判断结果时为 `inconclusive`；能看到 Unity console `0 errors`、测试通过等明确标记时才为 `passed`；能看到失败或非零错误数时为 `failed`。同一观察窗口里同时存在 passed-looking 和 failed-looking 信号时，wrapper 优先返回 `failed` 或 `inconclusive`，避免误报 `passed`。

### `opencode_coder_wait`

长轮询工具，在单次 MCP 工具调用内部等待 job 出现"值得关注的变化"或等待超时后再返回。
它用于减少频繁 `opencode_coder_status` 轮询带来的 token 浪费，把多次 status 查询合并为一次等待。

参数：

- `job_id`：`opencode_coder` 返回的 job id。
- `wait_seconds`：最大等待秒数，限制在 `0..600` 秒，默认 `120`。上限保守，避免极端 MCP 工具调用超时。
- `return_on`：触发返回的事件类型：
  - `"interesting"`（默认）：等待 `caller_update_recommended=true`、terminal 状态、首次文件变更、policy violation、stalled、no_event_noop_risk、observed validation passed/failed 等任何值得关注的变化。超时时返回 `wait_timeout`。
  - `"terminal"`：只等待 `completed` / `failed` / `cancelled`。不把 running 或首次文件变更当作触发条件。
- `include_status`：默认 `true`，返回与 `opencode_coder_status` 兼容的完整状态快照加上 wait 专属字段。设 `false` 时只返回 `job_id`、`status` 和 wait 专属字段。轻量轮询应使用 `include_status=false` 节省 token；只有 wait 返回 interesting 更新时再调用 `opencode_coder_status` 获取完整诊断。
- `include_tail`、`include_output`、`include_delta`：调试开关，与 `opencode_coder_status` 一致，默认 `false` 保持输出紧凑。

wait 专属返回字段：

- `wait_return_reason`：等待返回原因。可能的值：
  - `terminal_status` — job 达到 completed/failed/cancelled。
  - `first_change_seen` — 本次等待窗口内出现了新的文件变更（不是前一次 opencode_coder 或 opencode_coder_wait 调用已观察到的旧变更）。
  - `caller_update_recommended`（或其底层原因如 `stalled`、`policy_violation`、`no_event_noop_risk`、`validation_observed`）— 进度诊断触发了 caller update。
  - `wait_timeout` — 等待窗口到期，无关键变化。
  - `not_found` — job_id 未找到。
- `interesting_update`：`true` 表示检测到有意义的变化；`false` 表示等待超时无新信号。
- `waited_seconds`：实际等待的秒数。

超时时（`wait_return_reason="wait_timeout"`、`interesting_update=false`）返回 compact heartbeat：
job 可能仍 running，`caller_update_recommended` 通常为 `false`，raw tail/delta/output 默认空。

`job_id` 未找到时立即返回，不等待。

与 `opencode_coder_status` 的区别：

- `opencode_coder_status` 是即时快照查询，可选短 `wait_seconds`（max 30s），用于简单输出/变更检测。
- `opencode_coder_wait` 是长轮询工具（max 600s），可在一次调用内过滤到更高级别的"interesting"事件，
  替代多次 status 轮询。
- `opencode_coder_wait` 不支持 stdout/stderr cursor 参数；需要 cursor 驱动的 delta 轮询时用
  `opencode_coder_status`。
- `opencode_coder_wait` 不是 MCP 主动推送通知——它是一次会阻塞主对话的工具调用。

### `opencode_coder_diff`

基于某个 `opencode_coder` job 的 `new_changed_files` 返回有界 git diff，方便 review。

```text
opencode_coder_diff(job_id, max_chars=20000)
```

参数：

- `job_id`：`opencode_coder` 返回的 job id。
- `max_chars`：返回的 `diff` 最大字符数，会被包装层限制到安全上限。

行为：

- tracked 文件使用 `git diff` 和 `git diff --cached`。
- untracked 普通文件会用内存中的 `/dev/null` unified diff 表示新增文件。
- 不在用户项目目录写临时文件。
- 非 git 目录或 git status 失败时返回结构化错误，不抛出 MCP 异常。

返回字段：

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

`success=true` 表示包装层生成了可用 diff，或本 job 没有 `new_changed_files`。如果 `new_changed_files` 非空但当前工作区对这些文件已经没有可展示 diff，例如 job 期间改过又还原，工具会返回 `success=false`，并通过 `diff_empty_reason` 说明原因，如 `current_worktree_has_no_diff_for_job_files`。无法渲染为文本 diff 的文件，例如 untracked 二进制文件、过大文件、非普通文件，会进入 `undiffed_files`，原因记录在有界的 `diff_command_errors` 中。

`opencode_coder_diff` 是 review aid，不是唯一审查来源。当 `success=false`、`diff_empty_reason` 非空、`diff_command_errors` 非空或 `undiffed_files` 非空时，应回退到本地 `git status` / `git diff` 复核。

diff 结果会携带文件变更相关的 review 风险字段，例如 `review_required`、`incomplete_changes_risk` 和 `preexisting_dirty_warning`。如果这些字段提示风险，不应只看 diff 文本就接受结果；需要同时检查 job status 和本地 git 状态。session no-op 风险以 job result/status 中的 `no_event_noop_risk` 为准；`opencode_coder_diff` 只审查本 job 范围内的文件变更。

注意：这是 review 辅助，不保证是“只包含本 job 的纯 patch”。如果某个文件在 job 开始前已经 dirty，且本 job 又修改了它，git diff 可能混入任务前已有改动；此时返回 `includes_preexisting_dirty_changes=true`。

### `opencode_coder_cancel`

取消仍在运行的 `opencode_coder` job，并返回与 status 类似的结构化结果。

行为：

- 未知或过期 `job_id` 返回 `status="not_found"`、`success=false`、`error="job_not_found"`。
- 已完成、已失败或已取消的 job 原样返回，不重复发送 terminate。
- running job 会先标记 `cancel_requested=true`，再向 OpenCode 进程发送 `process.terminate()`。
- 如果短时间内未退出，会尝试进程树清理。
- 取消不会回滚文件变更。

取消相关字段：

- `cancel_requested`
- `cancel_signal_sent`
- `cancel_kill_sent`
- `process_tree_kill_attempted`
- `process_tree_kill_succeeded`
- `process_tree_kill_error`

进程树清理是 best-effort。Windows 下使用 `taskkill /PID <pid> /T /F`；非 Windows 下新进程会放入新 session，fallback 时对进程组发送 `SIGKILL`。如果子进程脱离进程组或平台命令失败，仍可能无法完全清理。

### `opencode_server_start`

启动 managed OpenCode headless server。

```text
opencode serve --hostname <hostname> --port <port>
```

参数：

- `working_dir`：server 进程运行目录，默认 `"."`。
- `hostname`：绑定 host，默认 `"127.0.0.1"`。
- `port`：绑定端口。传 `0` 时包装层自动选择一个可用本地端口。

工具会等待端口可连接后返回：

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
- `recovered_from_registry`
- `registry_path`
- `registry_error`
- `process_running`
- `command`
- `success`
- `error`

managed server 元数据会持久化到用户项目外的 wrapper registry。详见“Registry 持久化”。

### `opencode_server_status`

按 `server_id` 查询 managed server 状态。

如果 server 不在当前 MCP 进程内存中，包装层会尝试从 registry 恢复 managed server 记录；恢复失败则返回 `not_found` 或 `lost`。

### `opencode_server_list`

列出当前包装层可见的 managed OpenCode servers。

```text
opencode_server_list(working_dir=None, include_lost=false)
```

参数：

- `working_dir`：可选路径过滤。相对路径和绝对路径会按 `opencode_coder` 相同方式规范化。
- `include_lost`：默认 `false`。为 `false` 时，stale registry 记录会被清理或通过顶层 `registry_error` 报告，但不进入 `servers`；为 `true` 时，stale 记录会以 `status="lost"` 返回。

返回字段：

- `servers`：server status 对象列表，结构与 `opencode_server_status` 一致。
- `count`
- `registry_path`
- `registry_error`
- `success`

这个工具会合并内存中的 server 和 registry 中的 server 记录。registry-only 记录会通过 pid + TCP 校验；校验通过后恢复进内存，并可用于 `opencode_coder(server_id=...)`。

### `opencode_server_stop`

停止 managed server，并返回最终 server 状态。

这是 best-effort 停止流程：先请求主 server 进程退出；如果超时，则尝试与 `opencode_coder_cancel` 相同的进程树清理策略。

如果某个 server 是 MCP 重启后从 registry 恢复出来的，包装层没有原始 `Popen` 句柄和 stdout/stderr pipe。这种情况下 status 和 attach 仍可工作，但 `opencode_server_stop` 会返回结构化限制说明，而不是盲目 kill pid。

## Registry 持久化

包装层只持久化 managed OpenCode server 元数据。job 记录、stdout/stderr buffer 和进程句柄仍只存在于内存中。MCP server 重启后，历史 `opencode_coder_status(old_job_id)` 仍可能返回 `not_found`。

默认 registry 路径：

- Windows：`%LOCALAPPDATA%\LiteOpenCodeMcp\opencode_coder_registry.json`
- Windows fallback：`%TEMP%\LiteOpenCodeMcp\opencode_coder_registry.json`
- 非 Windows：`$XDG_CACHE_HOME/LiteOpenCodeMcp/opencode_coder_registry.json`
- 非 Windows fallback：`~/.cache/LiteOpenCodeMcp/opencode_coder_registry.json`

可通过环境变量覆盖：

```powershell
$env:OPENCODE_CODER_REGISTRY_PATH = "D:\path\to\opencode_coder_registry.json"
```

registry 是 JSON 文件，通过临时文件 + atomic replace 写入。它只保存重新 attach 所需的 server 元数据：

- `server_id`
- `url`
- `hostname`
- `port`
- `working_dir`
- `pid`
- `started_at`
- `command`
- `command_summary`

恢复是惰性的：当 `opencode_server_status(server_id)`、`opencode_server_list()`、`opencode_server_stop(server_id)` 或 `opencode_coder(..., server_id=...)` 在内存中找不到 server 时，才读取 registry。恢复时会验证 pid 仍存在，并且 host/port 可以建立 TCP 连接。

限制：

- 不恢复 stdout/stderr tail 和 delta buffer。
- 不恢复原始 `Popen` 进程句柄。
- pid/url 验证失败会返回 `status="lost"` 并清理 stale registry 记录。
- registry JSON 损坏会通过 `registry_error` 报告，不会让 MCP tool 崩溃。
- 不按 `working_dir` 扫描系统进程。

## Git Snapshot

job 开始、结束和运行中 status 查询时，包装层会采集：

```text
git -C <working_dir> -c core.quotepath=false status --porcelain=v1 --untracked-files=all
```

文件字段含义：

- `preexisting_changed_files`：OpenCode 启动前已经 dirty 的文件。
- `all_changed_files`：当前 git status 中的所有 changed files。
- `new_changed_files`：本 job 期间 git status 或文件系统 fingerprint 发生变化的路径。包括新增路径，也包括任务前已 dirty 但本 job 又修改过的路径。
- `changed_files`：兼容字段，等同于 `all_changed_files`。

`new_changed_files` 可能包含最终已经不在 `all_changed_files` 中的路径，例如 job 清理了某个 preexisting dirty 文件。这是刻意设计：路径策略关心 job 触碰过什么，而不只关心最终还 dirty 什么。

非 git 目录不会让 OpenCode job 失败；相关列表为空，`git_status_available=false`，`git_status_error` 记录 git status 信息。

## 路径策略

`allowed_paths` 和 `forbidden_paths` 只检查 `new_changed_files`，不会把任务前已有的 dirty 文件默认算成本 job 越界。

如果任务前已有 dirty 文件被本 job 再次修改，它会进入 `new_changed_files`，并参与路径策略检查。

规则：

- 包装层会通过 `git rev-parse --show-toplevel` 获取 git root；如果可用，`git status` 返回的 changed file 会按 git-root-relative 路径解释。
- 相对 policy path 会同时按 `working_dir` 和 git root 解析。
- 多段相对 policy path 还可以按 git-relative suffix 匹配。这个规则用于支持 Unity 子目录布局，例如 git 报告 `CFantacy-TurnBasedStrategy/Assets/...`，而调用方传入 `Assets/...`。
- 支持绝对路径。
- `\` 和 `/` 会规范化。
- Windows 下大小写不敏感。
- policy path 匹配自身或子路径，因此目录路径匹配其子文件。
- `forbidden_paths` 优先于 `allowed_paths`。

越界结果：

- 新改动文件不在 `allowed_paths` 内时，返回 `policy_violation=true` 并列入 `extra_changed_files`。
- 新改动文件命中 `forbidden_paths` 时，返回 `policy_violation=true` 并列入 `forbidden_changed_files`。

路径策略只报告，不自动回滚、删除或修改文件。

`path_policy` 会返回有界诊断字段，方便排查误报：

- `working_dir`
- `git_root` / `git_root_error`
- `allowed_paths_normalized`
- `forbidden_paths_normalized`
- `checked_files_basis`
- `file_matches`
- `match_rule`

如果 git root 不可用，包装层会保留原有的 working-dir-relative fallback，并在 `path_policy.git_root_error` 说明原因。

## 状态值

- `running`：进程仍在运行。
- `timed_out`：MCP 等待窗口已到，但 OpenCode 进程继续在后台运行。
- `completed`：进程以 exit code `0` 完成。
- `failed`：进程启动失败或以非零 exit code 结束。
- `cancelled`：用户请求取消，OpenCode 进程已退出。
- `not_found`：查询了未知或已过期的 `job_id`。
- `stopped`：managed server 已按请求停止。
- `lost`：registry 中存在记录，但 pid/url 校验失败。

## 兼容性

包装层仍保留旧字段：

- `success`
- `output`
- `return_code`

新调用方建议优先使用 `status`、`exit_code`、`suggested_action`、`work_summary_text`、`new_changed_files`、风险字段和 `opencode_coder_diff`。普通观察循环优先使用 `opencode_coder_wait(..., include_status=false)`；只有 wait 返回 interesting 更新，或需要完整诊断时，再拉 compact status 和 cursor 元数据，避免重复传输完整 tail。`stdout_delta` / `stderr_delta` 与 `recent_events` 主要是调试诊断字段，不应作为普通调用链默认消费。只有需要调试时才传 `include_tail=true` 或 `include_delta=true`；旧字段 `output` 也只有传 `include_output=true` 时才会返回内容。

## 真实 OpenCode Smoke Test

真实集成 smoke test 默认跳过，需要显式开启。它会启动 managed `opencode serve`，通过 `--attach` 调用 `opencode_coder`，轮询 `opencode_coder_status`，并在临时 git 仓库中验证 snapshot 字段。`opencode_coder_wait` 当前由单元/fake 子进程测试覆盖；发布前如需声明真实集成覆盖，应补一次真实 wait smoke。

这个测试可能触发真实模型调用，依赖网络、认证、OpenCode provider 配置和相关费用。测试只写入 `TemporaryDirectory`，不会触碰用户项目。

PowerShell：

```powershell
$env:OPENCODE_CODER_RUN_INTEGRATION = "1"
python -B -m unittest -v test_opencode_coder.OpenCodeCoderIntegrationTests
Remove-Item Env:\OPENCODE_CODER_RUN_INTEGRATION
```

如果 `opencode` 不在 `PATH`，或没有设置 `OPENCODE_CODER_RUN_INTEGRATION=1`，测试会跳过。

## Backlog

- 决定真实 OpenCode integration smoke test 是否进入定期或 CI gated 检查。
- MCP server 重启后的完整 job recovery。目前 managed server registry MVP 已实现，但 job history 和 output pipes 仍只在内存中。
- 用真实子进程树增强跨平台 process-tree cleanup 验证。
- 从 OpenCode 输出中提取更详细的测试和验证信息。
- policy violation 的自动回滚或清理。
- `opencode_coder_wait` 目前通过周期 `refresh_job_snapshot` + 诊断重建来检测 interesting 信号；
  未来可考虑使用专用 threading Events 加速 stall/validation/noop 检测。
