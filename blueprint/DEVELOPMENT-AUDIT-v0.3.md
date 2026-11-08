# v0.3 开发文档独立审计

日期：2026-11-08 UTC。结论：**ACCEPT，允许按下列冻结开发文档实施 D1/D2。** 这是开发设计审计，不代表代码、真实登录/模型或生产环境验收。审计期间未执行厂商程序、未访问账号秘密、未修改实现代码。

## 审查对象

| 文件 | SHA-256 |
|---|---|
| `DEVELOPMENT-v0.3-MULTI-HARNESS.md` | `6c6f2ef21aab096064299bd07a27ec66bc2a4f52db90c78124bd77fd29f6cf79` |
| `CODEX-APP-SERVER-CONTRACT.md` | `a74955d88351611f517cadf60d5c8ece2f8b3f3c6966002a5dc1632556ca2836` |

基线 Native `694e2bd`（实现 `081e102`）、Bridge `03fd9020`、Sub2API `9ffe3aa2`。

## 最简性结论

- 保留四字段 Binding、现有 canonical、Bridge 唯一账本、Node journal/全局单槽，不增加表、服务、队列或调度器。
- 仅一个执行配方对象与静态 Adapter 表；不把蓝图的每个概念都实现成框架。
- 持久 Adapter 只引入完整 Binding 到固定 invoke/projector 对的依赖；复用现有存储、实时泵和 owned IO，不复制厂商无关循环。
- 校验保留在配置、厂商协议、账号/模型选择和执行所有权等已有边界；不新增 watchdog、重试、checksum 叠层、动态插件 DSL、通用凭据或审批框架。
- 既有重复检查不在本期顺手重构。不能以“最少行数”为理由删掉归属/幂等/清理事实，也不能以审计名义堆无关防御层。

## 已关闭问题

1. **配方身份未具体。** 已固定 canonical 字段、Windows路径规范化、profile随机身份与已确认公开账号属性。token 刷新不换身份；重新认证/换账号新profile。没有稳定accountId的限制明确。
2. **跨厂商同名模型可能错用 projector。** 改为完整冻结 Binding 一次解析固定 invoke/projector 对，不能以输出自报或可变current目标派发。
3. **迁移恢复语义不够具体。** 旧mode、旧Binding和已受理操作走原身份只读恢复；当前目标移除不得重建任务，新增真实旧操作跨三仓回放测试。
4. **Codex正文phase及partial视图存在歧义。** 明确item首次观察顺序、最终快照替换、final优先与partial/null候选、commentary仅状态、不凭部分itemsView删除结果。null不是终态。
5. **Codex与SFE退出语义不同。** Codex匹配turn终态和无协议fault后可做有界owned收尾，必须区分自然退出/强制清理；不能由强制制造终态或清除fault。WorkBuddy自然exit0规则保持。
6. **账号/provider范围未固定。** 首期仅device-code ChatGPT，明确请求并回读openai provider；read-only/on-request不是全禁工具或系统沙箱，企业配置影响不被隐瞒。
7. **诊断与事件存储措辞过强。** 已纠正未知超时根因，并将恢复限定为本项目已核验投影/结果证据，未引入原始敏感事件存储。

## 独立核验

审计员直接阅读现有 `Binding`、`NativeTrialConfig.invoke`、`PublicSfeAdapter`、`Runner` 与 `PublicSfeOwnedSession`，确认复用边界和旧协议约束。另直接核对固定 Codex commit 的 `Account`、`ThreadStartParams/Response`、`Turn`、`MessagePhase`、`LoginAccountParams`、`GetAccountResponse` schema 与 stdio `ConnectionClosed` 收尾源码。

完整Windows官方归档仅做字节核验，SHA-256为 `16f99769bd4accc519cf22079645db445907182c8b96bc626f496b7390f5ad46`，与合同一致；未执行。

文档UTF-8及代码块结构已核验。没有用文档审计代替源码回归或真实运行。

## 实施与交付边界

无未关闭的开发设计阻断。实现应按文档切片独审与测试，固定旧WorkBuddy兼容、同资源单槽、旧任务只读恢复、Codex归属/取消/phase/模型错配和Windows生命周期。既有HTTP测试线程清理缺口单独最小修复，不能用延长超时或删除断言掩盖。

Codex真实入口、登录、模型和系统策略仍NOT_RUN；豆包/千问未实现。首期commentary不展示原文，不是完整Codex UI镜像。此结论不新增厂商执行、账户或部署许可。
