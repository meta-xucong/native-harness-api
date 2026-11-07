# P1a 实现状态

基线：审定 v0.2 SHA256 `3ef768759f4e7b7f786d9badf721f42fb661e5e7d728ab724daf118577df7f08`。

## 已实现的范围

| 项目 | 状态 |
| --- | --- |
| Python stdlib 合同、严格输入、拒答/未知 usage、稳定响应 | IMPLEMENTED |
| 鉴权 Facade、受控可信身份、回放先于新准入 | IMPLEMENTED_FOR_P1A |
| 真实 HTTP Bridge/Node 客户端、能力门、响应绑定验证 | IMPLEMENTED_FOR_PROPOSED_CONTRACT |
| Node 受信策略、完整 offer 执行前校验；SQLite journal、跨进程锁、一次性 intent、只读恢复 | IMPLEMENTED_FOR_P1A |
| 合成 FakeHarness 及持久启动计数 | IMPLEMENTED_SYNTHETIC_ONLY |
| 测试假网关操作/路由冻结、测试假账本 | TEST_FIXTURE_ONLY |
| 真实 Bridge 原仓库文本 schema/结果/活性扩展 | NOT_IMPLEMENTED |
| 真实 Sub2API 身份/重试/操作绑定/Go 接缝 | NOT_IMPLEMENTED |
| WorkBuddy 官方入口、模型回读、真实模型执行 | NOT_IMPLEMENTED / NOT_RUN |
| Windows 生命周期、真实审批/取消、supervisor 围栏恢复 | NOT_RUN / NOT_IMPLEMENTED |
| 备份/恢复代际、数据库回滚、跨机器协调 | NOT_IMPLEMENTED |
| 生产用户权限、隔离、泄密防护、TLS 终止、部署运维 | NOT_VERIFIED |
| token 计量、钱包扣费、超时后账务对账 | NOT_IMPLEMENTED |
| 流式、tool_calls、多模态、长期会话 | NOT_IMPLEMENTED |

P1a 的独立路径可运行，不等于满足整个 v0.2 矩阵。真实取消/start 竞争、租约抢占、磁盘满/损坏恢复、账本旧快照、原有 Bridge 图片及 Sub2API 正常渠道回归都未运行，不能借测试夹具宣称通过。

## 本次测试

最终命令、计数与指纹见 [TEST_REPORT.md](TEST_REPORT.md)。所有 HTTP 都指向进程内创建的 loopback 测试服务；模型执行为 FakeHarness。

Node 实际跨进程测试包括：旧消费者停在 intent 后、adapter 调用前；第二消费者不能取得 OS 锁；终止旧消费者后 durable intent 仍阻止再次执行。测试不证明真实 WorkBuddy/子进程已停止。没有生产围栏时，不提供解除未知槽的按钮。

## 不可绕过的后续门槛

1. Bridge 和 Sub2API 必须在各自仓库做小范围独立兼容改造与旧功能回归。当前未动两仓源码。
2. ModelBinding 需从用户实际软件的受支持入口得出，不能使用本 fake 模型证据代替。
3. 只有实际可用、授权、模型固定与终态恢复验证完成，才能进入 P2/P3。当前真实测试/收费/部署均未发生。
4. 多租户或收费分别需要 v0.2 对应隔离/授权/账务门禁。P1a loopback server 禁止改 bind 后就当生产服务。
5. 未决 journal/账本不能删除重建来“修复”。旧实例的消费者没有被可靠围栏时，不得释放原槽或重跑原操作。

## 发布与审计

独立代码审计首轮提出 Node 执行前校验、HTTP framing、Facade map key/alias 三项实证，已修复并通过复审，结论为 [PASS_FOR_EXPLICIT_P1A_PROTOTYPE_PUBLICATION](CODE_AUDIT_FINAL.md)。允许明确标注的 P1a 源码发布，不批准部署或合并。远程提交与 draft PR 由发布步骤单独验证。设计审计是 blueprint/ 中的历史阶段证据，不覆盖后来代码。最终 GitHub 发布须核对实际远端提交后另报。
