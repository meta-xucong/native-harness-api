> 后续真实增量扩展以 [native-events-v1](NATIVE_EVENT_STREAM.md) 为准；下文非流式为历史基线。

# P1b 实现状态

新增 P2 独立 WorkBuddy bundled-runtime 本机试验程序见 [本机试验指南](WORKBUDDY_LOCAL_TRIAL.md)。它支持用户本地显式确认一次原生 ACP prompt，但不改下列 P1b 正式渠道门禁，真实账号/模型尚未运行；源码和发行包另行审计，不能用旧 P1b 审计覆盖。

审定蓝图 v0.2 保持原 SHA256 `3ef768759f4e7b7f786d9badf721f42fb661e5e7d728ab724daf118577df7f08`；本阶段差异见 [P1b 补充](../../blueprint/P1B-ADDENDUM.md)。旧 P1a 审计只对应 commit `5f1bfd6611a3d3ebe0f6dbb2f2c56ee303931deb`，不批准当前新增代码。

| 项目 | 当前状态 |
| --- | --- |
| 严格文本 CC、拒答、未知 usage、受信 NodePolicy、journal/单槽/intent | IMPLEMENTED_SYNTHETIC_ONLY |
| Facade/Node 可运行配置与 heartbeat、冻结 source/ledger 校验 | IMPLEMENTED_FOR_P1B |
| 真实 Bridge 同 tasks/attempts/claims typed 扩展及 HTTP | IMPLEMENTED_ON_SEPARATE_BRANCH / SOCKET_TESTED |
| 真实 Sub2API APIKey middleware/专用 handler/单次 HTTP 出站 | IMPLEMENTED_ON_SEPARATE_BRANCH / HTTP_TESTED_WITH_EXPLICIT_REPOSITORY_FIXTURES |
| Sub2API PostgreSQL migration/持久唯一操作路由 | IMPLEMENTED / REAL_DATABASE_NOT_RUN |
| 完整持久三仓闭环 | BLOCKED_ON_AUTHORIZED_DATABASE_TEST_ENVIRONMENT |
| 普通 `/v1/chat/completions` account-channel 接入 | NOT_IMPLEMENTED（当前仅专用 native 路径） |
| WorkBuddy 官方入口、选模/readback、真实模型任务 | NOT_IMPLEMENTED / NOT_RUN |
| Windows、真实审批/取消/后台子进程停机证明 | NOT_RUN |
| 备份回滚/恢复代际、跨机器围栏、强制释放 | NOT_IMPLEMENTED |
| 生产权限/隔离、TLS 终止、运维部署、商业授权 | NOT_VERIFIED |
| 收费、SSE、tool_calls、多模态、长期会话 | NOT_IMPLEMENTED |

## 已证明与没有证明

真实 Bridge HTTP 故障测试不依赖 P1a 假账本。它证明合成执行时的任务/结果/恢复接缝，不能证明 WorkBuddy 进程或工具已停止。Sub2API 真实中间件/handler HTTP 测试仍明确注入认证 repository 与路由 store 替身；它不证明实际 PostgreSQL 并发、迁移或恢复。

真实数据库阶段因当前环境与授权约束停下，未使用其他数据库路径替代验收。Node 未决 journal 和 Bridge 墓碑不得删除重建来“修复”。未知活性保持隔离，没有人工强制清空接口。

## 审计与发布

当前 P1b 已独立复审，结论为 [PASS_FOR_SCOPED_DEVELOPMENT_BRANCH](P1B_AUDIT_FINAL.md)，仅允许默认关闭的 synthetic 开发分支，不批准完整持久验收、合并或部署。每仓独立分支；发布步骤须另行核对远端 SHA 和 CI。测试计数和命令见 [P1b 报告](P1B_TEST_REPORT.md)，不得用过去通过数字冒充当前结果。
