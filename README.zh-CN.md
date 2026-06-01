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

长任务推荐使用托管 server 和非阻塞等待：

1. 用 `opencode_server_start` 启动一个 managed OpenCode server。
2. 用 `opencode_coder(..., server_id=..., wait_policy="start_only")` 派发任务，尽快拿到 `job_id`。
3. 用 `opencode_coder_status(job_id, wait_seconds=...)` 观察进度。
4. 用 `opencode_coder_diff(job_id)` 审查本 job 涉及的变更。
5. 需要中止时调用 `opencode_coder_cancel(job_id)`。
6. 新对话或 MCP 重启后，用 `opencode_server_list` 找回可复用的 managed server。

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

`timeout_seconds` 会受到 `OPENCODE_CODER_MAX_WAIT_SECONDS` 限制，默认上限是 `110` 秒，避免 MCP 客户端先超时导致丢失 job 上下文。返回结果中会同时包含 `requested_timeout_seconds` 和 `effective_timeout_seconds`。

等待策略：

- `"completion"`：等待进程完成或到达 `timeout_seconds`。这是默认行为。
- `"start_only"`：进程启动并注册 job 后尽快返回，适合快速拿 `job_id`。
- `"first_output"`：等到 stdout/stderr 有输出、进程完成或超时。
- `"first_change"`：等到 `new_changed_files` 非空、进程完成或超时。

长任务建议使用 `"start_only"` 或 `"first_output"`，让调用方尽快恢复控制权，再通过 `opencode_coder_status` 轮询。

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
- `wait_policy`
- `success`

### `opencode_coder_status`

按 `job_id` 查询内存中的 job 状态。

参数：

- `job_id`：`opencode_coder` 返回的 job id。
- `wait_seconds`：可选短等待，限制在 `0..30` 秒，默认 `0`。
- `stdout_cursor`：可选，前一次结果返回的 stdout cursor。
- `stderr_cursor`：可选，前一次结果返回的 stderr cursor。

当 `wait_seconds > 0` 时，status 会等待到以下任一情况发生：job 完成、新 stdout/stderr 到达、新文件变更被检测到、等待超时。status 查询不会启动新的 OpenCode 进程。

如果传入 cursor，返回中会包含 `stdout_delta` / `stderr_delta`，只返回 cursor 之后的新增文本。cursor 是包装层内存缓冲区中的字符偏移，不是持久化日志文件偏移。

相关字段：

- `stdout_delta`
- `stderr_delta`
- `stdout_cursor`
- `stderr_cursor`
- `stdout_delta_truncated`
- `stderr_delta_truncated`
- `first_output_at`
- `first_change_at`
- `last_activity_at`

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
)
print(next_status["stdout_delta"])
print(next_status["stderr_delta"])
```

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
- `diff_truncated`
- `max_chars`
- `undiffed_files`
- `includes_preexisting_dirty_changes`
- `git_status_available`
- `error`
- `success`

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

- 相对路径基于 `working_dir` 解析。
- 支持绝对路径。
- `\` 和 `/` 会规范化。
- Windows 下大小写不敏感。
- policy path 匹配自身或子路径，因此目录路径匹配其子文件。
- `forbidden_paths` 优先于 `allowed_paths`。

越界结果：

- 新改动文件不在 `allowed_paths` 内时，返回 `policy_violation=true` 并列入 `extra_changed_files`。
- 新改动文件命中 `forbidden_paths` 时，返回 `policy_violation=true` 并列入 `forbidden_changed_files`。

路径策略只报告，不自动回滚、删除或修改文件。

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

新调用方建议优先使用 `status`、`exit_code`、`stdout_tail`、`stderr_tail`。轮询场景建议使用 `stdout_cursor` / `stderr_cursor` 和 delta 字段，避免重复传输完整 tail。

## 真实 OpenCode Smoke Test

真实集成 smoke test 默认跳过，需要显式开启。它会启动 managed `opencode serve`，通过 `--attach` 调用 `opencode_coder`，轮询 `opencode_coder_status`，并在临时 git 仓库中验证 snapshot 字段。

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
