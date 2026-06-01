# 已有对话接入 LiteOpenCodeMcp 提示词

把下面这段提示词发给一个已经存在的 Codex 对话，可让它了解本项目当前的 LiteOpenCodeMcp / opencode_coder 使用规则。

```text
请在本对话中更新 LiteOpenCodeMcp / opencode_coder 的使用规则。

使用前提：
1. 只有纯代码修改、纯文本/文档修改、配置文本修改这类任务，才可以默认考虑使用 LiteOpenCodeMcp / opencode_coder。
2. Unity 资产操作不要用 opencode_coder，包括但不限于 prefab、scene、asset、ScriptableObject 资产写入、导入设置、材质、动画、UI prefab 层级等。
3. 如果用户在当前任务里明确要求“使用 opencode_coder / opencode / MCP opencode / 让 OpenCode 执行”，则可以按用户要求使用；但如果涉及 Unity 资产操作，需要先提醒风险并确认边界。
4. 不要把 opencode_coder 当成默认执行器。常规 review、方案讨论、提示词编写、需求拆解时不要调用它。

推荐调用流程：
1. 如当前会话还没暴露工具，先通过 tool_search 查找 LiteOpenCodeMcp / opencode_coder。
2. 优先使用 managed server 流程，把 LiteOpenCodeMcp 当成“任务调度器 + 紧凑 review 面板”，不要当成同步终端输出流：
   - 先用 opencode_server_list 查看是否已有可复用 server。
   - 没有合适 server 时，用 opencode_server_start 启动。
   - 派发任务时用 opencode_coder(..., server_id=..., wait_policy="start_only" 或 "first_output")。
   - 用 opencode_coder_status(job_id, wait_seconds=...) 轮询结果；默认 compact status 不返回长 tail、legacy output 或 stdout/stderr delta 正文。
   - 完成后用 opencode_coder_diff(job_id) 辅助 review。
3. 默认复用 server_id，不默认复用 session_id。只有明确需要延续 OpenCode 会话上下文时才传 session_id、continue_last 或 fork_session；不要跨不同 working_dir 或不同仓库根目录复用 session_id。
4. 小任务也可以直接用 opencode_coder，但仍必须检查返回结果。
5. 普通轮询不要传 include_tail、include_output、include_delta。只有调试原始输出时才显式打开，并配合 tail_max_chars / delta_max_chars。

强制检查要求：
每次调用 opencode_coder / opencode_coder_status 后，必须检查返回内容，不能只看工具调用是否成功，也不能只看 status=completed 或 success=true。至少检查：
- status
- success
- error
- job_id
- working_dir
- exit_code
- suggested_action
- summary
- work_summary_text / assistant_last_text / last_text_output
- new_changed_files
- all_changed_files
- preexisting_changed_files
- policy_violation
- extra_changed_files
- forbidden_changed_files
- git_status_available / git_status_error
- runtime_seconds / idle_seconds
- is_stalled / stall_reason / suggested_action
- review_required / incomplete_changes_risk / potential_incomplete_changes_risk / preexisting_dirty_warning
- no_event_noop_risk / no_event_noop_reason
- validation_status / validation_note
- stdout_cursor / stderr_cursor
- attached_to_server / server_id / server_url

raw 输出规则：
- stdout_tail、stderr_tail、output、stdout_delta、stderr_delta 默认可能为空，这是正常的 compact 行为。
- 普通任务反馈优先看 work_summary_text / assistant_last_text / last_text_output、summary、changed files 和风险字段。
- 只有定位问题时才用 opencode_coder_status(..., include_tail=true 或 include_delta=true, tail_max_chars=..., delta_max_chars=...) 查看原始输出。
- 如果 include_delta=true 后出现 stdout_delta_response_truncated / stderr_delta_response_truncated，说明本次响应被截断；cursor 仍会推进，不要指望下一次自动补回同一段全文。

如果 status 是 timed_out / running：
- 不要直接判断任务完成。
- 必须继续用 opencode_coder_status(job_id, wait_seconds=...) 查询，直到 completed / failed / cancelled，或明确向用户说明仍在运行。
- 必须检查 is_stalled、stall_reason、suggested_action；is_stalled 不是 failed，但表示应考虑查看 diff/status 后取消或继续轮询。

如果 no_event_noop_risk=true：
- 不要把 completed / success=true 当成正常完成。
- 优先不传 session_id 重试，或新开 session/server。
- 最终回复必须说明这是 session 复用语义风险，而不是一次可信完成。

验证注意：
- 不要假设 prompt 要求的验证一定执行了；wrapper 不主动运行验证。
- validation_status / validation_note 只能说明 wrapper 的验证状态，不能替代 stdout/stderr、OpenCode report 或本地验证结果。
- job 未 completed 时，prompt 内要求的验证很可能没有执行。

如果有文件变更：
- 必须用 opencode_coder_diff(job_id) 或本地 git diff 进行 review。
- 如果 includes_preexisting_dirty_changes=true，要提醒用户 diff 可能混入任务前已有脏改动。
- 如果 opencode_coder_diff 返回 success=false、diff_empty_reason 非空、undiffed_files 非空或 diff_command_errors 非空，必须回退本地 git status / git diff 复核。
- opencode_coder_diff 也要检查 review_required / incomplete_changes_risk / preexisting_dirty_warning；如果任一提示风险，不能只看 diff 文本就接受结果。
- 如果 policy_violation=true，要优先报告越界文件和风险。
- 如果 review_required=true 或 incomplete_changes_risk=true，必须明确说明需要人工 review。
- failed / cancelled / timed_out 后如果存在文件变更，必须 review diff / git status；不要假设这些状态会自动回滚或保持原子性。
- 如果 preexisting_dirty_warning 非空，必须说明 all_changed_files 中可能包含任务前已有脏改动。

最终回复要求：
- 用中文总结 opencode_coder 的执行结果。
- 说明 status、是否成功、OpenCode 自己反馈完成了什么、改了哪些文件、是否有 policy violation、是否存在 no-op/stall/半成品风险、是否跑了测试。
- 如果没有完成、失败、超时、diff 不完整或 git status 不可用，必须明确说明。
```
