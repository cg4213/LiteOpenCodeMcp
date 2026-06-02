# LiteOpenCodeMcp 本轮执行时间线（修正版）

说明：

- 本修正版以用户在客户端看到的总处理时间 `21m51s` 为锚点。
- 上一版把工具执行阶段压缩成约 `6m`，没有按客户端 wall-clock 校准；该版本不应作为准确偏移时间线使用。
- 本文件中的偏移时间仍是近似值：对话系统没有为每一次内部阅读、编辑和回复提供可导出的秒级 wall-clock timestamp。
- 已知长耗时命令按实际工具输出估算：完整 unittest 多次约 `66~68s`，单测约 `2.3s`。
- “思考/判断”记录的是可审计的高层决策，不包含逐字内部推理。

## 时间线

| 偏移时间 | 阶段 | 思考/判断 | 实际操作 | 涉及文件 |
| --- | --- | --- | --- | --- |
| T+00:00 | 接收任务 | 明确目标是改造 `LiteOpenCodeMcp` 的 OpenCode 调用和 wait 返回机制，且不实现 event log、不重构 policy。 | 确认工作目录应切到 `D:\Develop\LiteOpenCodeMcp`。 | 无 |
| T+00:35 | 仓库盘点 | 需要先看现有 wrapper、测试、README，避免破坏已有状态/诊断字段。 | 查看目录、git 状态、文件列表；发现已有未跟踪文档 `OPENCODE_MCP_TODO.zh-CN.md`、`OPENCODE_WAIT_POLLING_DEV_LOG_20260602.zh-CN.md`，本轮不纳入改动。 | 无 |
| T+01:20 | 代码定位 | 判断核心改动点在 `build_opencode_command`、`opencode_coder`、`start_job`、`job_to_result`、`opencode_coder_wait`。 | 搜索并阅读 wait、status、command build、job result 相关代码段。 | `opencode-coder.py` |
| T+02:20 | 测试定位 | 需要覆盖命令构造、默认参数、显式覆盖、wait compact snapshot 和风险分支。 | 阅读 fake subprocess 测试和现有 wait/status 测试。 | `test_opencode_coder.py` |
| T+03:20 | 设计决策 | 保留现有 `job_to_result` 作为唯一诊断来源，不新建 event log，不改 policy violation 判断逻辑。 | 决定通过 compact snapshot helper 从 status result 中截取关键字段。 | `opencode-coder.py` |
| T+04:05 | OpenCode 参数实现 | 默认 model/variant 应进 CLI；agent/show_thinking 仅显式非空/true 时进 CLI。 | 新增 `DEFAULT_OPENCODE_MODEL`、`DEFAULT_OPENCODE_VARIANT` 和 `append_opencode_run_option_flags`；扩展 `build_opencode_command` 参数。 | `opencode-coder.py` |
| T+05:10 | Job schema 贯通 | 启动成功、启动失败、server missing、server not running 都应记录 requested 参数，避免 schema 不一致。 | 给 `OpenCodeJob`、`make_start_failed_result`、`start_job`、`opencode_coder` 增加 model/variant/agent/show_thinking 传递。 | `opencode-coder.py` |
| T+06:15 | Status 返回补齐 | 调用方需要在结果中看到本轮请求的 OpenCode 配置。 | 在 `job_to_result` 和 `make_job_not_found_result` 增加 `requested_model`、`requested_variant`、`requested_agent`、`requested_show_thinking`。 | `opencode-coder.py` |
| T+07:00 | 默认 wait 时长调整 | MCP 客户端按 240 秒考虑，默认 wait 不应贴近 240 秒，也不应继续偏短。 | 将默认 wait cap 调整为 215 秒；新增 `OPENCODE_CODER_MCP_CLIENT_TIMEOUT_SECONDS` 和 `OPENCODE_CODER_MCP_WAIT_MARGIN_SECONDS` 计算 helper。 | `opencode-coder.py` |
| T+07:55 | Wait compact snapshot | `include_status=false` 当前信息不足，是本轮核心问题之一。 | 新增 `WAIT_COMPACT_SNAPSHOT_FIELDS`、`compact_wait_snapshot`；让 wait 在 `include_status=false` 时仍返回关键 snapshot。 | `opencode-coder.py` |
| T+08:45 | Wait 引导字段 | 普通 wait 后不应默认要求立即 status；只有 debug、不一致或诊断不足才建议 status。 | 新增 `needs_status_refresh`、`suggested_next_tool`、`status_refresh_reason` 计算逻辑。 | `opencode-coder.py` |
| T+09:35 | 单元测试补充 | fake command 需要接收新参数；测试要覆盖默认和显式覆盖。 | 更新 fake command 签名和记录字段；新增 build command 默认/覆盖/空值跳过测试。 | `test_opencode_coder.py` |
| T+10:35 | Wait 测试补充 | 需要覆盖 completed、first_change_seen、policy_violation、stalled、debug tail 和 compact snapshot 字段。 | 更新/新增 wait 相关测试，断言普通分支 `needs_status_refresh=false`，风险分支给合理 `suggested_next_tool`。 | `test_opencode_coder.py` |
| T+11:40 | README 更新 | 文档需要说明 wait-first，不默认 wait 后 status；也要说明默认 model/variant 和覆盖方式。 | 更新英文 README 的 MCP env、调用契约、`opencode_coder` 参数、`opencode_coder_wait` 返回规则。 | `README.md` |
| T+12:35 | 中文 README 更新 | 中文文档需要与英文一致，且明确默认 model/variant、默认 215 秒和 wait 引导字段。 | 更新中文 README 的配置、推荐流程、参数、wait compact snapshot 和兼容性说明。 | `README.zh-CN.md` |
| T+13:30 | 首次编译验证 | 先确认 Python 文件语法无误。 | 运行 `python -m py_compile opencode-coder.py test_opencode_coder.py`，结果通过，耗时约 `0.7s`。 | 无 |
| T+13:35 | 首次单元测试 | 需要完整跑现有 fake-process 测试，观察新增逻辑是否影响旧行为。 | 运行 `python -B -m unittest -v test_opencode_coder.py`，耗时约 `67.6s`，失败 1 个 wait first-change 断言。 | `test_opencode_coder.py` |
| T+14:45 | 失败分析与修正 | first-change 场景中 job 可能已 completed，此时建议 diff 比继续 wait 更合理。 | 将断言改为按终态选择 `opencode_coder_diff` 或 `opencode_coder_wait`。 | `test_opencode_coder.py` |
| T+15:05 | 二次单元测试 | 验证断言调整是否解决问题。 | 重跑完整 unittest，耗时约 `67.5s`，结果通过，`138` 个测试，`1` 个 integration smoke 跳过。 | 无 |
| T+16:15 | diff 检查 | 需要确认没有 whitespace 问题。 | 运行 `git diff --check`，通过，仅有 Git 行尾转换 warning。 | 无 |
| T+16:25 | 缓存清理 | `py_compile` 可能生成 `__pycache__`，需要清理避免污染。 | 查找并删除 `D:\Develop\LiteOpenCodeMcp\__pycache__`。 | 无 |
| T+16:40 | 最终审查 | 需要确认只改预期文件且没有缓存目录。 | 查看 `git status --short`、`git diff --stat`、关键 symbol 搜索。 | `opencode-coder.py`、`test_opencode_coder.py`、`README.md`、`README.zh-CN.md` |
| T+17:05 | 补充测试 | 注意到新增 wait margin env 需要直接测试覆盖。 | 新增 `test_effective_timeout_uses_client_timeout_margin_env`。 | `test_opencode_coder.py` |
| T+17:30 | 最终编译验证 | 补测试后重新验证语法。 | 运行 `python -m py_compile opencode-coder.py test_opencode_coder.py`，通过，耗时约 `0.6s`。 | 无 |
| T+17:35 | 最终单元测试第一次 | 重新完整验证，发现 first-change 测试在整套跑慢时可能滑到 terminal。 | 运行 unittest，耗时约 `68s`，失败 1 个 first-change 断言。 | `test_opencode_coder.py` |
| T+18:45 | 稳定性修正 | 需要让 first-change 场景保持运行态更久，避免整套测试耗时导致竞态。 | 将该测试 prompt 从 `delayed_write` 改为 `double_write_same_file`。 | `test_opencode_coder.py` |
| T+19:00 | 单测定点验证 | 先确认被修正的 first-change 测试稳定。 | 运行 `python -B -m unittest -v test_opencode_coder.OpenCodeCoderTests.test_wait_first_change_returns_immediately`，耗时约 `2.3s`，通过。 | 无 |
| T+19:08 | 最终三件套开始 | 按验收要求重新跑完整验证。 | 运行 `py_compile`，通过，耗时约 `0.6s`。 | 无 |
| T+19:15 | 最终完整单元测试 | 最终完整验证 fake-process 体系。 | 运行 `python -B -m unittest -v test_opencode_coder.py`，耗时约 `67.8s`，通过，`139` 个测试，`1` 个 integration smoke 跳过。 | 无 |
| T+20:25 | 最终 diff check | 检查 whitespace 和 patch 格式。 | 运行 `git diff --check`，通过，仅出现 Git 行尾转换 warning。 | 无 |
| T+20:35 | 最终清理与状态确认 | 需要确保没有 `__pycache__`，并确认 git 状态只包含预期改动。 | 删除缓存目录；确认无 `__pycache__`；查看 `git status --short` 和 `git diff --stat`。 | 无 |
| T+21:05 | 汇总回复 | 需要按用户要求用中文报告修改文件、schema、wait 返回、默认 model/variant、测试结果和未做事项。 | 输出最终中文总结。 | 无 |
| T+21:51 | 客户端观测结束 | 用户在客户端看到本轮总处理时间约 `21m51s`。 | 该时间作为本修正版时间线的总耗时锚点。 | 无 |

## 本轮改动文件摘要

- `opencode-coder.py`
  - 新增默认 OpenCode model/variant 常量。
  - 新增 model/variant/agent/show_thinking 参数和 CLI flag 构造。
  - 新增 requested_* 返回字段。
  - 将默认 wait 预算调整为 215 秒，并支持客户端超时和 margin 环境变量。
  - 改造 `opencode_coder_wait`，使 `include_status=false` 返回 compact snapshot。
  - 新增 wait 引导字段：`needs_status_refresh`、`suggested_next_tool`、`status_refresh_reason`。

- `test_opencode_coder.py`
  - 更新 fake command 参数记录。
  - 新增 build command 默认/显式覆盖/空值跳过测试。
  - 新增 requested_* 返回记录测试。
  - 新增 wait compact snapshot 和普通/风险/调试分支测试。
  - 新增 wait margin env 测试。

- `README.md`
  - 更新默认 wait/env 说明。
  - 增加默认 model/variant 和覆盖方式。
  - 更新 wait-first 使用规则：不默认 wait 后 status。
  - 说明 compact snapshot 和新增 wait 引导字段。

- `README.zh-CN.md`
  - 与英文文档同步中文说明。
  - 明确 `include_status=false` 仍返回关键快照。
  - 明确普通 wait 返回后优先继续 wait、diff 或 cancel，而不是默认 status。

## 验证记录

- `python -m py_compile opencode-coder.py test_opencode_coder.py`：最终通过。
- `python -B -m unittest -v test_opencode_coder.py`：最终通过，`139` 个测试，`1` 个真实 integration smoke 因未设置 `OPENCODE_CODER_RUN_INTEGRATION=1` 跳过。
- `git diff --check`：最终通过，仅出现 Git 行尾转换 warning。
- 已清理 `__pycache__`。

## 明确未做

- 未实现 event log。
- 未重构 policy 系统。
- 未修改 policy violation 判断逻辑。
- 未运行真实 wait-only smoke。
