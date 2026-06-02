# OpenCode wait/polling 默认值开发交互日志（2026-06-02）

## 范围

本文记录本轮 `LiteOpenCodeMcp` 中 OpenCode wait/polling 默认值优化的开发过程，重点覆盖主对话与 `opencode_coder` / OpenCode managed server 的交互事件，以及可确认的发生顺序和偏移时间。

本日志不展开完整代码 diff、测试输出和每次 shell 命令细节；本地验证、review 和 commit 只保留关键节点。

## 时间说明

- 本文使用偏移时间，不使用绝对时间。
- Server 段以 `opencode_server_start` 成功为 `S+0`。
- 每个 Job 段以该 job 的 `opencode_coder` 派发时间为 `J+0`。
- Job 内 `first_output_at`、`first_change_at`、`finished_at` 的偏移由 `opencode_coder_status` 返回的时间戳计算。
- 部分 `opencode_coder_wait` / `opencode_coder_status` 调用没有独立返回绝对调用时间，本文按相邻 job 生命周期时间和主对话记录标注为“约”。

## 目标

本轮目标是把 OpenCode 调用体验从高频短轮询调整为更低噪音的等待/轮询节奏：

- `opencode_coder_wait` 默认等待时间拉长到 120 秒。
- 普通 running 状态下 `next_poll_after_seconds` 调整为 120 秒。
- 终态、风险态、stall、policy violation、no-op、validation observed 等需要主对话立即处理的状态仍返回更短或 0 秒建议。
- 修正 `first_change_seen` 的等待竞态，避免等待接口重复返回陈旧 first-change 事件。

## OpenCode Server

| 偏移 | 交互 | 结果 |
| --- | --- | --- |
| S-约 3s | `opencode_server_list` | 未发现可复用 server，准备启动 managed server。 |
| S+0 | `opencode_server_start` | 启动成功，`server_id=6c5a5924fd984653848535e4519dbcc3`，URL `http://127.0.0.1:52706`，`pid=51896`，工作目录 `D:\Develop\LiteOpenCodeMcp`。 |
| S+约 30m54s | `opencode_server_status` | 确认 server 仍在 registry 中，随后准备停止。 |
| S+30m54.357s | `opencode_server_stop` | 停止成功，server 状态变为 `stopped`；返回 `exit_code=1`，但 stop 调用 `success=true`。 |

## MCP 调用逐次明细

本节混排主对话中记录到的 MCP 调用和 OpenCode job 生命周期事件，不合并 `opencode_coder_wait` / `opencode_coder_status`。其中 offset 仍按对应 server / job 的起点计算；没有工具侧绝对时间的调用使用“约”。

### 调用次数汇总

| 工具 | 次数 |
| --- | ---: |
| `opencode_server_list` | 1 |
| `opencode_server_start` | 1 |
| `opencode_server_status` | 1 |
| `opencode_server_stop` | 1 |
| `opencode_coder` | 4 |
| `opencode_coder_wait` | 15 |
| `opencode_coder_status` | 14 |
| `opencode_coder_diff` | 4 |
| 合计 | 41 |

生命周期事件行用于补齐 `first_output_at`、`first_change_at`、`finished_at` 等关键时间点，不计入上面的 MCP 调用次数。

### 逐次调用与生命周期事件表

| # | 偏移 | 工具 | 目标 | 结果 / 观察 |
| ---: | --- | --- | --- | --- |
| 1 | S-约 3s | `opencode_server_list` | server registry | 未发现可复用 server。 |
| 2 | S+0 | `opencode_server_start` | server | 启动 managed server，`server_id=6c5a5924fd984653848535e4519dbcc3`。 |
| 3 | J1+0 | `opencode_coder` | Job 1 | 派发首次实现任务，新建 session。 |
| E1 | J1+4.751s | OpenCode event | Job 1 | `first_output_at`：OpenCode 首次输出。 |
| E2 | J1+1m44.711s | OpenCode event | Job 1 | `first_change_at`：OpenCode 首次改动。 |
| 4 | J1+约 1m45s | `opencode_coder_wait` | Job 1 | 等到 `first_change_seen`，本次 wait 实际等待约 92.922 秒。 |
| 5 | J1+约 1m47s | `opencode_coder_status` | Job 1 | job 仍在 running，确认已进入编辑阶段。 |
| 6 | J1+约 2m07s | `opencode_coder_wait` | Job 1 | 再次快速返回 `first_change_seen`，暴露 first-change 事件可能被重复消费。 |
| 7 | J1+约 2m10s 到 J1+约 4m10s | `opencode_coder_wait` | Job 1 | `return_on=terminal` 且 `wait_seconds=120`，MCP 客户端约 120 秒处超时。 |
| 8 | J1+约 4m10s | `opencode_coder_status` | Job 1 | job 仍在 running；观察到临时 `__pycache__` dirty / policy 风险。 |
| 9 | J1+约 5m10s | `opencode_coder_wait` | Job 1 | `return_on=terminal` 且 `wait_seconds=100`，继续等待终态。 |
| 10 | J1+约 6m20s | `opencode_coder_status` | Job 1 | job 接近完成；后续状态显示 pycache 已清理。 |
| E3 | J1+6m28.104s | OpenCode event | Job 1 | `finished_at`：job 完成，`status=completed`。 |
| 11 | J1+约 6m28s | `opencode_coder_wait` | Job 1 | 等到 `completed`。 |
| 12 | J1+约 6m37s | `opencode_coder_status` | Job 1 | 拉取最终状态，进入 review。 |
| 13 | J1+约 6m37s | `opencode_coder_diff` | Job 1 | 拉取最终 diff。 |
| 14 | J2+0 | `opencode_coder` | Job 2 | 派发 first-change 竞态修复任务，复用 Job 1 session。 |
| E4 | J2+2.470s | OpenCode event | Job 2 | `first_output_at`：OpenCode 首次输出。 |
| 15 | J2+约 1m40s | `opencode_coder_wait` | Job 2 | `return_on=terminal` 且 `wait_seconds=100`，未等到终态，返回 wait timeout。 |
| 16 | J2+约 2m 到 J2+约 4m | `opencode_coder_status` | Job 2 | OpenCode 仍在阅读和规划，尚无 first change。 |
| 17 | J2+约 4m 到 J2+约 5m | `opencode_coder_wait` | Job 2 | 返回 `no_first_change_after_budget`。 |
| E5 | J2+5m44.799s | OpenCode event | Job 2 | `first_change_at`：OpenCode 首次改动。 |
| 18 | J2+约 5m45s | `opencode_coder_status` | Job 2 | 使用有限 tail 诊断，确认 OpenCode 已读取代码和测试。 |
| 19 | J2+约 6m06s | `opencode_coder_wait` | Job 2 | 返回 policy violation 相关状态，主要来源是临时 `__pycache__`。 |
| 20 | J2+约 6m08s | `opencode_coder_status` | Job 2 | job 仍在 running，最终状态仍记录过 policy violation。 |
| E6 | J2+6m13.050s | OpenCode event | Job 2 | `finished_at`：job 完成，`status=completed`。 |
| 21 | J2+约 6m13s | `opencode_coder_wait` | Job 2 | 等到 `completed`。 |
| 22 | J2+约 6m16s | `opencode_coder_status` | Job 2 | 拉取最终状态，进入 review。 |
| 23 | J2+约 6m16s | `opencode_coder_diff` | Job 2 | 拉取最终 diff。 |
| 24 | J3+0 | `opencode_coder` | Job 3 | 派发风险态 next_poll 修复任务；因上一 job 有 policy violation 记录，新开 session。 |
| E7 | J3+1.734s | OpenCode event | Job 3 | `first_output_at`：OpenCode 首次输出。 |
| 25 | J3+约 2m | `opencode_coder_wait` | Job 3 | 未到终态，返回 wait timeout / 继续等待类状态。 |
| 26 | J3+约 2m 到 J3+约 3m | `opencode_coder_status` | Job 3 | OpenCode 仍在读取上下文和规划。 |
| E8 | J3+3m42.680s | OpenCode event | Job 3 | `first_change_at`：OpenCode 首次改动。 |
| 27 | J3+约 3m45s | `opencode_coder_wait` | Job 3 | 返回 policy violation 相关状态。 |
| 28 | J3+约 4m | `opencode_coder_status` | Job 3 | job 处于 policy violation / validating 附近状态。 |
| 29 | J3+约 5m | `opencode_coder_wait` | Job 3 | 返回 `validation_observed`。 |
| 30 | J3+约 5m10s | `opencode_coder_status` | Job 3 | job 仍在 validating，policy violation 已恢复为 false。 |
| E9 | J3+6m24.970s | OpenCode event | Job 3 | `finished_at`：job 完成，`status=completed`。 |
| 31 | J3+约 6m25s | `opencode_coder_wait` | Job 3 | 等到 `completed`。 |
| 32 | J3+约 6m28s | `opencode_coder_status` | Job 3 | 拉取最终状态，进入 review。 |
| 33 | J3+约 6m28s | `opencode_coder_diff` | Job 3 | 拉取最终 diff。 |
| 34 | J4+0 | `opencode_coder` | Job 4 | 派发收窄 next_poll=0 reason 白名单任务，复用 Job 3 健康 session。 |
| E10 | J4+1.875s | OpenCode event | Job 4 | `first_output_at`：OpenCode 首次输出。 |
| 35 | J4+约 1m30s | `opencode_coder_wait` | Job 4 | 返回临时 policy violation / pycache dirty 相关状态。 |
| 36 | J4+约 1m35s | `opencode_coder_status` | Job 4 | job 仍在 running，观察到临时 pycache dirty。 |
| E11 | J4+1m47.872s | OpenCode event | Job 4 | `first_change_at`：OpenCode 首次改动。 |
| E12 | J4+2m30.305s | OpenCode event | Job 4 | `finished_at`：job 完成，`status=completed`。 |
| 37 | J4+约 2m30s | `opencode_coder_wait` | Job 4 | 等到 `completed`。 |
| 38 | J4+约 2m33s | `opencode_coder_status` | Job 4 | 拉取最终状态，进入 review。 |
| 39 | J4+约 2m33s | `opencode_coder_diff` | Job 4 | 拉取最终 diff。 |
| 40 | S+约 30m54s | `opencode_server_status` | server | commit 后确认 server 状态。 |
| 41 | S+30m54.357s | `opencode_server_stop` | server | 停止 managed server。 |

## Job 1：首次实现 P0 wait/polling 默认值

以下各 Job 小节是阶段摘要，可能合并描述相邻状态查询；逐次 MCP 调用以“调用逐次明细”表为准。

| 字段 | 内容 |
| --- | --- |
| `job_id` | `19ed611156ee45b59bcebcb0e1b3f259` |
| `server_id` | `6c5a5924fd984653848535e4519dbcc3` |
| `session_id` | `ses_17999f2ccffexGkrmassOMYSa7` |
| 结果 | `completed` / `success=true` / `exit_code=0` |
| 运行时间 | 388.104 秒 |
| 本轮变更文件 | `opencode-coder.py`、`test_opencode_coder.py`、`README.md`、`README.zh-CN.md` |

| 偏移 | 交互 | 结果 / 观察 |
| --- | --- | --- |
| J1+0 | `opencode_coder` | 派发首次实现任务，使用 managed server，新建 session。 |
| J1+4.751s | OpenCode 首次输出 | `first_output_at` 出现，说明 job 已开始执行。 |
| J1+约 1m45s | `opencode_coder_wait(wait_seconds=120)` | 等到 `first_change_seen`，该 wait 调用实际等待约 92.922 秒。 |
| J1+1m44.711s | OpenCode 首次改动 | `first_change_at` 出现。 |
| J1+约 1m47s | `opencode_coder_status` | 仍在运行，确认有编辑进展。 |
| J1+约 2m07s | `opencode_coder_wait(wait_seconds=120)` | 快速再次返回 `first_change_seen`，暴露 first-change 事件可能被重复消费的问题。 |
| J1+约 2m10s 到 J1+约 4m10s | `opencode_coder_wait(wait_seconds=120, return_on=terminal)` | MCP 客户端在约 120 秒处超时，说明单次 tool call 等待时间不应贴近客户端硬超时。 |
| J1+约 4m10s 到 J1+约 5m10s | `opencode_coder_status` | 看到临时 `__pycache__` 进入 dirty / policy 风险，但 job 仍在运行。 |
| J1+约 5m10s 到 J1+约 6m20s | `opencode_coder_wait(wait_seconds=100, return_on=terminal)` | 等待到 job 接近完成；后续状态显示 pycache 已清理。 |
| J1+6m28.104s | OpenCode job 完成 | `status=completed`，`success=true`。 |
| J1+约 6m37s | `opencode_coder_status` / `opencode_coder_diff` | 拉取最终状态和 diff，进入 FO review。 |

### Job 1 review 结论

OpenCode 认为任务完成，但本地主对话验证发现测试失败：

- `python -m py_compile opencode-coder.py test_opencode_coder.py`：通过。
- `git diff --check`：通过，仅有 Git LF/CRLF 提示。
- `python -B -m unittest -v test_opencode_coder.py`：失败 1 个测试。
- 失败用例：`test_wait_first_change_returns_immediately`，期望 `first_change_seen`，实际得到 `terminal_status`。

因此 Job 1 未 commit，进入下一轮修复。

## Job 2：修复 first-change 等待竞态

| 字段 | 内容 |
| --- | --- |
| `job_id` | `1326492676eb4333a7bb88d9845ee525` |
| `server_id` | `6c5a5924fd984653848535e4519dbcc3` |
| `session_id` | `ses_17999f2ccffexGkrmassOMYSa7` |
| 结果 | `completed` / `success=true` / `exit_code=0` |
| 运行时间 | 373.050 秒 |
| 本轮变更文件 | `opencode-coder.py`，另有临时 `__pycache__` dirty 记录 |

| 偏移 | 交互 | 结果 / 观察 |
| --- | --- | --- |
| J2+0 | `opencode_coder` | 派发修复任务，复用 Job 1 的 session。 |
| J2+2.470s | OpenCode 首次输出 | `first_output_at` 出现。 |
| J2+约 0 到 J2+约 1m40s | `opencode_coder_wait(wait_seconds=100, return_on=terminal)` | 未等到终态，返回 wait timeout。 |
| J2+约 2m 到 J2+约 4m | `opencode_coder_status` | 显示 OpenCode 仍在阅读和规划，还没有 first change。 |
| J2+约 4m 到 J2+约 5m | `opencode_coder_wait` | 返回 `no_first_change_after_budget`，提示首个改动等待预算已耗尽。 |
| J2+5m44.799s | OpenCode 首次改动 | `first_change_at` 出现，开始修改 `opencode-coder.py`。 |
| J2+约 5m45s 到 J2+约 6m | `opencode_coder_status(include_tail)` | 查看短 tail，确认 OpenCode 已读取代码和测试。 |
| J2+约 6m06s | `opencode_coder_wait` | 返回 policy violation 相关状态，原因主要是 job 期间产生过临时 `__pycache__`。 |
| J2+6m13.050s | OpenCode job 完成 | `status=completed`，`success=true`，但最终状态仍记录 `policy_violation=true`，来源是本轮临时 pycache dirty。 |
| J2+约 6m16s | `opencode_coder_status` / `opencode_coder_diff` | 拉取最终状态和 diff，进入 FO review。 |

### Job 2 review 结论

本地验证显示测试已恢复：

- `python -B -m unittest -v test_opencode_coder.py`：129 tests OK，1 integration skipped。

但 FO review 发现新的设计问题：

- `next_poll_by_phase` 只按 phase 决定等待时间，可能导致 `policy_violation` 等需要立即处理的状态在 editing / validating phase 下仍返回 120 秒。

因此 Job 2 未 commit，进入下一轮修复。

## Job 3：修复风险态 next_poll 过长问题

| 字段 | 内容 |
| --- | --- |
| `job_id` | `6d3349dac4a349eeac9e6ec0cf793b86` |
| `server_id` | `6c5a5924fd984653848535e4519dbcc3` |
| `session_id` | `ses_179897f12ffeSWzWpyZzLBdRZi` |
| 结果 | `completed` / `success=true` / `exit_code=0` |
| 运行时间 | 384.970 秒 |
| 本轮变更文件 | `opencode-coder.py`、`test_opencode_coder.py` |

| 偏移 | 交互 | 结果 / 观察 |
| --- | --- | --- |
| J3+0 | `opencode_coder` | 因上一 job 有 policy violation 记录，本轮新开 session 派发修复任务。 |
| J3+1.734s | OpenCode 首次输出 | `first_output_at` 出现。 |
| J3+约 0 到 J3+约 2m | `opencode_coder_wait` | 等待期间未到终态，返回 wait timeout / 继续等待类状态。 |
| J3+约 2m 到 J3+约 3m | `opencode_coder_status` | 看到 OpenCode 仍在读取上下文和规划。 |
| J3+3m42.680s | OpenCode 首次改动 | `first_change_at` 出现，开始修改 wrapper 和测试。 |
| J3+约 3m45s 到 J3+约 5m | `opencode_coder_wait` / `opencode_coder_status` | 期间观察到 policy violation / validation observed 等状态，随后状态恢复为无 policy violation。 |
| J3+6m24.970s | OpenCode job 完成 | `status=completed`，`success=true`，最终无 policy violation。 |
| J3+约 6m28s | `opencode_coder_status` / `opencode_coder_diff` | 拉取最终状态和 diff，进入 FO review。 |

### Job 3 review 结论

OpenCode 把 `caller_update_recommended=true` 泛化为 `next_poll_after_seconds=0`。FO review 发现这会让 `first_change_seen` 也变成 0 秒轮询建议，仍然会鼓励主对话快速轮询。

因此 Job 3 未 commit，要求下一轮做更窄修复：只对白名单风险 / 终态 reason 返回 0，`first_change_seen` 仍保持 120 秒。

## Job 4：收窄 next_poll=0 的 reason 白名单

| 字段 | 内容 |
| --- | --- |
| `job_id` | `492ed895fd2148d88b14ccaa74f3fa50` |
| `server_id` | `6c5a5924fd984653848535e4519dbcc3` |
| `session_id` | `ses_179897f12ffeSWzWpyZzLBdRZi` |
| 结果 | `completed` / `success=true` / `exit_code=0` |
| 运行时间 | 150.305 秒 |
| 本轮变更文件 | `opencode-coder.py`、`test_opencode_coder.py` |

| 偏移 | 交互 | 结果 / 观察 |
| --- | --- | --- |
| J4+0 | `opencode_coder` | 派发收窄修复任务，复用 Job 3 的健康 session。 |
| J4+1.875s | OpenCode 首次输出 | `first_output_at` 出现。 |
| J4+约 0 到 J4+约 1m30s | `opencode_coder_wait` | 期间观察到临时 policy violation / pycache dirty 相关状态。 |
| J4+1m47.872s | OpenCode 首次改动 | `first_change_at` 出现。 |
| J4+2m30.305s | OpenCode job 完成 | `status=completed`，`success=true`，最终无 policy violation。 |
| J4+约 2m33s | `opencode_coder_status` / `opencode_coder_diff` | 拉取最终状态和 diff，进入 FO review。 |

### Job 4 review 结论

本轮修复通过 review：

- `next_poll_after_seconds=0` 改为基于终态 / 风险 reason 白名单。
- `first_change_seen` 仍保留 `caller_update_recommended=true`，但 `next_poll_after_seconds=120`。
- 增加测试覆盖 first-change 不应触发 0 秒轮询建议。

## 本地验证与提交

本段以 Job 4 完成为 `V+0`。

| 偏移 | 事件 | 结果 |
| --- | --- | --- |
| V+约 30s | `python -m py_compile opencode-coder.py test_opencode_coder.py` | 通过。 |
| V+约 30s | `python -B -m unittest -v test_opencode_coder.py` | 通过，131 tests OK，1 integration skipped。 |
| V+约 1m30s | `git diff --check` | 通过，仅有 Git LF/CRLF 提示。 |
| V+约 1m30s | 清理 `__pycache__` 并复核 git 状态 | 工作区只保留目标文件变更。 |
| V+约 2m30s | `git add -- README.md README.zh-CN.md opencode-coder.py test_opencode_coder.py` | stage 本轮相关文件。 |
| V+约 2m30s | `git commit -m "Tune OpenCode wait polling defaults"` | 提交成功，commit `50bbb49`。提交后出现一次 `git maintenance run` 的 `detach` 参数 warning，但 commit 已成功。 |
| V+约 2m40s | `git status --short` / `git log -1 --oneline` | 工作区干净；最新提交为 `50bbb49 Tune OpenCode wait polling defaults`。 |

## 关键观察

1. 单次 MCP tool call 等待 120 秒会贴近客户端超时边界，本轮曾出现 `timed out awaiting tools/call after 120s`。这说明“用户侧低频汇报”和“MCP 单次阻塞等待时间”应分开设计。
2. 复用 session 有上下文收益，但上一 job 若出现 policy violation、failed、cancelled、timed_out、no-op 等风险状态，应新开 session。
3. `__pycache__` 这类临时生成物即使最终被清理，也可能在 job 期间进入 policy / dirty 记录，对 review 造成噪音。
4. 只降低主对话汇报频率，不能显著降低 MCP 交互本身的 token 成本；需要依赖 `opencode_coder_wait` 这类“阻塞到有意义事件”的工具减少轮询次数。
5. 本轮最后仍留下一个后续改进点：MCP 默认 wait 时间应给客户端硬超时留余量，例如 wrapper 内部默认等待 100-110 秒，同时把用户可见的建议轮询间隔维持在 120 秒或更长。
