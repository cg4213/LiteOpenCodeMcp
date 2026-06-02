# 已有对话接入 LiteOpenCodeMcp 提示词

把下面这段提示词发给一个已经存在的 Codex 对话，可让它了解本项目当前的 LiteOpenCodeMcp / opencode_coder 使用规则。

```text
请在本对话中更新 LiteOpenCodeMcp / opencode_coder 的使用规则。

使用前提：
1. 只有纯代码修改、纯文本/文档修改、配置文本修改这类任务，才可以默认考虑使用 LiteOpenCodeMcp / opencode_coder。
2. Unity 资产操作不要用 opencode_coder，包括但不限于 prefab、scene、asset、ScriptableObject 资产写入、导入设置、材质、动画、UI prefab 层级等。
3. 如果用户在当前任务里明确要求“使用 opencode_coder / opencode / MCP opencode / 让 OpenCode 执行”，则可以按用户要求使用；但如果涉及 Unity 资产操作，需要先提醒风险并确认边界。
4. 不要把 opencode_coder 当成默认执行器。常规 review、方案讨论、提示词编写、需求拆解时不要调用它。
5. 大工作提示词必须小步快跑。涉及多个文件、多条设计分支、迁移 + 验证 + 报告的任务，应拆成多个边界明确的 OpenCode job；每轮只交付一个明确目标，完成后 review，再派发下一轮。

推荐调用流程：
1. 如当前会话还没暴露工具，先通过 tool_search 查找 LiteOpenCodeMcp / opencode_coder。
2. 优先使用 managed server 流程，把 LiteOpenCodeMcp 当成“任务调度器 + 紧凑 review 面板”，不要当成同步终端输出流：
   - 先用 opencode_server_list 查看是否已有可复用 server。
   - 没有合适 server 时，用 opencode_server_start 启动。
   - 派发任务时用 opencode_coder(..., server_id=..., wait_policy="start_only" 或 "first_output")。
   - 派发后优先用 opencode_coder_wait(job_id, wait_seconds=90, return_on="interesting", include_status=false) 等待关键变化；wait 返回 interesting 更新后，再用 opencode_coder_status(job_id) 获取完整诊断。
   - 如果 opencode_coder_wait 不可用，或需要 cursor/delta 诊断，再回退到 opencode_coder_status(job_id, wait_seconds=...)；默认 compact status 不返回长 tail、legacy output 或 stdout/stderr delta 正文。
   - 完成后用 opencode_coder_diff(job_id) 辅助 review。
3. 默认复用 server_id。同一 working_dir、同一 feature/topic、上一 job 无 no_event_noop_risk、上一 job 无 failed/cancelled/timed_out 时，默认优先复用上一轮健康的 session_id，以减少重复读上下文；换任务主题、换 working_dir/仓库根、上一 job 异常或出现 no-op 风险时，必须新开 session。
4. 小任务也可以直接用 opencode_coder，但仍必须检查返回结果。
5. 普通轮询不要传 include_tail、include_output、include_delta。只有调试原始输出时才显式打开，并配合 tail_max_chars / delta_max_chars。
6. 降低询问/汇报频率：派发 job 后第一次状态查询建议等 60-90 秒，后续优先用
   opencode_coder_wait(job_id, wait_seconds=90, return_on="interesting", include_status=false) 等待关键变化；
   只有 wait 返回 interesting 更新时才调用 opencode_coder_status 获取完整诊断，替代频繁 status 查询。
   如果 opencode_coder_wait 不可用，可回退到 45-90 秒节奏用 opencode_coder_status 轮询；
   不要因为 next_poll_after_seconds=5/10 这类短建议就频繁追问或频繁向用户汇报；
   只有 caller_update_recommended=true、状态终止、首次变更、风险出现
   或需要用户决策时才向用户汇报。

强制检查要求：
每次调用 opencode_coder / opencode_coder_wait / opencode_coder_status 后，必须检查返回内容，不能只看工具调用是否成功，也不能只看 status=completed 或 success=true。使用 include_status=false 的 wait 时，先检查 wait 结果；若 interesting_update=true，再调用 status 获取完整诊断。至少检查：
- status
- success
- error
- job_id
- working_dir
- exit_code
- suggested_action
- progress_phase / progress_message
- caller_update_recommended / caller_update_reason / next_poll_after_seconds
- wait_return_reason / interesting_update / waited_seconds（使用 opencode_coder_wait 时）
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
- time_to_first_output_seconds / time_to_first_event_seconds / time_to_first_tool_seconds / time_to_first_change_seconds
- seconds_since_last_event / seconds_since_last_change
- tool_activity_summary / long_gap_segments / root_cause_guess
- is_stalled / stall_reason / suggested_action
- review_required / incomplete_changes_risk / potential_incomplete_changes_risk / preexisting_dirty_warning
- no_event_noop_risk / no_event_noop_reason
- session_reuse_detected / session_reuse_mode / session_reuse_risk / session_reuse_note
- same_session_recent_job_count / same_session_last_job_status / likely_preexisting_from_same_session
- validation_status / validation_note
- observed_validation_summary / observed_validation_tools / observed_validation_result / observed_validation_errors_count
- stdout_cursor / stderr_cursor
- attached_to_server / server_id / server_url

raw 输出规则：
- stdout_tail、stderr_tail、output、stdout_delta、stderr_delta 默认可能为空，这是正常的 compact 行为。
- 普通任务反馈优先看 work_summary_text / assistant_last_text / last_text_output、summary、changed files 和风险字段。
- 只有定位问题时才用 opencode_coder_status(..., include_tail=true 或 include_delta=true, tail_max_chars=..., delta_max_chars=...) 查看原始输出。
- 如果 include_delta=true 后出现 stdout_delta_response_truncated / stderr_delta_response_truncated，说明本次响应被截断；cursor 仍会推进，不要指望下一次自动补回同一段全文。

如果 status 是 timed_out / running：
- 不要直接判断任务完成。
- 必须继续用 opencode_coder_wait(..., include_status=false) 或 opencode_coder_status(job_id, wait_seconds=...) 查询，直到 completed / failed / cancelled，或明确向用户说明仍在运行。
- 必须检查 is_stalled、stall_reason、suggested_action；is_stalled 不是 failed，但表示应考虑查看 diff/status 后取消或继续轮询。
- 默认用 caller_update_recommended / caller_update_reason 控制汇报频率；普通 running 且没有重大新信号时可以静默继续轮询。
- 如果 status 里出现 next_poll_after_seconds=5/10，只把它当作 status fallback 的内部诊断提示；正常调用优先继续使用 opencode_coder_wait，不要把它转成对用户的快速轮询汇报。

如果 no_event_noop_risk=true：
- 不要把 completed / success=true 当成正常完成。
- 优先不传 session_id 重试，或新开 session/server。
- 最终回复必须说明这是 session 复用语义风险，而不是一次可信完成。

如果 session_reuse_risk=true：
- 必须说明 session 复用风险，例如 no-event no-op、同 session working_dir 不一致或上一同 session job 异常。
- 即使 session_reuse_risk=false，也不能把复用视为绝对安全；history unavailable 时只能说明当前 wrapper 内存看不到足够历史。

验证注意：
- 不要假设 prompt 要求的验证一定执行了；wrapper 不主动运行验证。
- validation_status / validation_note 只能说明 wrapper 的验证状态，不能替代 stdout/stderr、OpenCode report 或本地验证结果。
- observed_validation_* 只是 wrapper 从 OpenCode 执行型工具信号中观察到的验证迹象，不改变 validation_status=not_run_by_wrapper；普通文本、README 内容、报告或 read/search/list 工具读到验证命令不等于验证执行。
- observed_validation_result=inconclusive 或 none 时，必须按未可靠验证处理。
- 即使 observed_validation_result=passed，也不能替代 FO/用户最终验证判断；如果存在 failed-looking 和 passed-looking 混杂信号，应按失败或不确定处理。
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
