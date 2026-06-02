# LiteOpenCodeMcp 使用反馈与建议

本文档用于记录使用反馈、风险观察和后续改进建议。这里的内容不表示当前行为已经修复，也不替代 README 中的正式使用说明。

## 验证字段可信度反馈

### 1. OpenCode 文本报告与 wrapper 验证状态不一致

反馈：

- OpenCode 文本报告可能声称已经执行 Unity Skills，且 Unity console 为零错误。
- 但 wrapper 仍返回 `validation_status=not_run_by_wrapper`。
- 调用方没有只信 OpenCode 文本报告，而是按 FO 流程继续复验。

当前判断：

- `validation_status=not_run_by_wrapper` 是保守语义，表示 wrapper 自己没有主动执行验证。
- OpenCode 文本报告只能作为线索，不能作为 wrapper 级验证结论。
- FO 侧复验仍必要，尤其是 Unity 编译 / console 错误这类高风险验收项。

建议：

- 保持 `validation_status=not_run_by_wrapper` 的语义不变。
- 后续可以增强说明字段，让调用方更容易区分“OpenCode 声称验证过”和“wrapper 观察到验证执行信号”。

### 2. observed validation 字段漏识别 Unity Skills 结果

反馈：

- `observed_validation_summary` 仍经常是 `none`。
- 但 `recent_events` 里能看到 Unity Skills 相关结果。
- 因此 wrapper 的 observed validation 字段目前不能直接当作完整可信结论。

当前判断：

- observed validation 字段只能作为辅助信号。
- 如果 `observed_validation_summary=none`，不能据此断定 OpenCode 没有执行 Unity Skills。
- 如果 recent events / OpenCode 文本 / 本地状态之间有不一致，FO 侧仍应复验。

建议：

- 后续增强 Unity Skills 验证事件提取，重点覆盖 `debug_force_recompile`、`debug_check_compilation`、`console_get_logs type=Error` 等事件形态。
- 增加针对 Unity Skills event JSON 的 fake-process 单测。
- 如条件允许，再补真实 Unity Skills smoke；但不要用 batchmode 替代 Unity Skills。
