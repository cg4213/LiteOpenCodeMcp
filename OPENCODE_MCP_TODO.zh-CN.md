# LiteOpenCodeMcp TODO

## P0 / P1 待办

### P1：减少 wait 后立刻 status 的重复调用（基础改造已完成）

**背景**

在 `2026-06-02` 的 wait/polling 默认值优化过程中，日志显示多次出现 `opencode_coder_wait` 返回后，主对话几乎立刻调用 `opencode_coder_status` 的情况。

这说明当前 `wait` 虽然能减少盲目轮询，但调用方仍倾向于把它当成“事件提醒”，而不是可直接采信的状态快照。

**影响**

- 增加 MCP 调用次数。
- 增加主对话 token 消耗。
- 让“长等待减少轮询”的收益被部分抵消。
- 调用方需要自行判断 `wait` 返回后是否还要补 `status`，使用心智不够清晰。

**可能原因**

- `wait` 返回内容在调用体验上不像完整状态快照。
- `wait` / `status` 的职责边界不够明确。
- 调用方需要确认风险字段、文件列表、summary、validation、policy 等信息时，会自然补一次 `status`。
- 本轮开发本身是在调试 `wait/status`，存在额外验证性查询；但实际使用中仍可能复现这种模式。

**改进方向**

- 让 `opencode_coder_wait` 返回和 `opencode_coder_status` 同等级的 compact snapshot。
- 增加明确引导字段，例如：
  - `needs_status_refresh`
  - `suggested_next_tool`
  - `status_refresh_reason`
- 当 `wait` 已返回足够字段时，默认 `needs_status_refresh=false`。
- 只有以下场景建议补 `status`：
  - 需要 tail / delta / 原始输出诊断。
  - `wait` 返回字段不足。
  - 出现状态不一致或工具错误。
  - 出现 policy violation / stall / no-op / validation 风险，需要补充诊断。
  - 准备最终 review，且需要强制刷新最新 git snapshot。

**验收标准**

- 常规 completed / first_change_seen / validation_observed 等 `wait` 返回中，调用方可以直接读取关键字段，不需要立即补 `status`。
- 文档明确说明：`wait` 返回后不要默认立刻调用 `status`。
- 单元测试覆盖 `wait` 返回的 compact snapshot 字段完整性。
- 单元测试覆盖 `needs_status_refresh` / `suggested_next_tool` 的关键分支。

**备注**

这不是 OpenCode 执行慢的根因，但属于 MCP 调用层面的 token 和体验优化点。

当前状态：

- `opencode_coder_wait(include_status=false)` 已改为返回 compact snapshot。
- 已新增 `needs_status_refresh`、`suggested_next_tool`、`status_refresh_reason`。
- 已完成一次 wait-only smoke：不调用 `opencode_coder_status`，只用 wait 等到 first change 和 terminal，再用 diff review。
- 后续仍可继续优化 event timeline / event log，帮助解释首次输出到首次改动之间的长间隔。

### P1：wait-only 推荐使用 300 秒 MCP 客户端超时

**背景**

基于 `OPENCODE_MCP_EXECUTION_TIMELINE_20260602.zh-CN.md` 里的执行时间线，首个真实代码改动可能出现在任务开始后约 245 秒。默认 240 秒 MCP 客户端超时下，wrapper 为了保留 25 秒安全边距，effective wait 只能约 215 秒，可能在首次改动前先返回一次 `wait_timeout`。

**建议配置**

- MCP client timeout：`300` 秒。
- `OPENCODE_CODER_MCP_CLIENT_TIMEOUT_SECONDS=300`
- `OPENCODE_CODER_MCP_WAIT_MARGIN_SECONDS=25`
- `OPENCODE_CODER_MAX_WAIT_SECONDS` 默认不设置；如需显式设置，可设为 `275`。

**使用规则**

- wait-only 普通路径下，不手填短 `wait_seconds`，优先使用 wrapper 默认 effective wait。
- `wait_timeout` 且 `interesting_update=false` 时，不向用户汇报，不默认调用 `opencode_coder_status`，直接继续下一次 `opencode_coder_wait`。
- 只有 `first_change_seen`、`validation_observed`、`policy_violation`、`stalled`、`terminal_status` 等关键事件才触发用户可见更新或切换工具。

**后续验证**

- 用中等复杂任务再跑一次 wait-only smoke，验证 300 秒配置下是否减少无意义的 wait timeout。
