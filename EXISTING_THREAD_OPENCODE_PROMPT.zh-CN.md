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
2. 优先使用 managed server 流程：
   - 先用 opencode_server_list 查看是否已有可复用 server。
   - 没有合适 server 时，用 opencode_server_start 启动。
   - 派发任务时用 opencode_coder(..., server_id=..., wait_policy="start_only" 或 "first_output")。
   - 用 opencode_coder_status(job_id, wait_seconds=...) 轮询结果；默认 compact status 不返回长 tail。
   - 完成后用 opencode_coder_diff(job_id) 辅助 review。
3. 小任务也可以直接用 opencode_coder，但仍必须检查返回结果。

强制检查要求：
每次调用 opencode_coder 后，必须检查返回内容，不能只看工具调用是否成功。至少检查：
- status
- success
- error
- job_id
- working_dir
- exit_code
- new_changed_files
- all_changed_files
- preexisting_changed_files
- policy_violation
- extra_changed_files
- forbidden_changed_files
- git_status_available / git_status_error
- stdout_delta / stderr_delta / stdout_cursor / stderr_cursor
- summary；必要时再用 opencode_coder_status(..., include_tail=true, tail_max_chars=...) 查看 stdout_tail / stderr_tail
- attached_to_server / server_id / server_url

如果 status 是 timed_out / running：
- 不要直接判断任务完成。
- 必须继续用 opencode_coder_status(job_id, wait_seconds=...) 查询，直到 completed / failed / cancelled，或明确向用户说明仍在运行。

如果有文件变更：
- 必须用 opencode_coder_diff(job_id) 或本地 git diff 进行 review。
- 如果 includes_preexisting_dirty_changes=true，要提醒用户 diff 可能混入任务前已有脏改动。
- 如果 opencode_coder_diff 返回 success=false、diff_empty_reason 非空、undiffed_files 非空或 diff_command_errors 非空，必须回退本地 git status / git diff 复核。
- 如果 policy_violation=true，要优先报告越界文件和风险。

最终回复要求：
- 用中文总结 opencode_coder 的执行结果。
- 说明 status、是否成功、改了哪些文件、是否有 policy violation、是否跑了测试。
- 如果没有完成、失败、超时、diff 不完整或 git status 不可用，必须明确说明。
```
