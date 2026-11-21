# 普通 OpenAI Provider 开发文档独立审计

日期：2026-11-08 UTC。审计者：独立审计线程 `provider_plan_audit`。

对象：`NATIVE-OPENAI-PROVIDER-DEVELOPMENT.md` 首稿与修订后的蓝图。
基线：Native `3402cc1`；原生 Sub2API `6b4b37db43443b5e88e424fe3c69602cc3810dfb`。

## 结论

**开发设计通过，允许按本文档进行最小增量编码。** 这不是实现验收，也不是原生 Sub2API 全链路、真实厂商账号或商业服务通过。

蓝图审计的 409 问题已闭环：现在明确常见 OpenAI SDK 也会重试 409，状态码仅表达冲突；关闭 SDK 自动重试不提供网关端到端幂等。

## 核对结果

- 改动边界收敛在新 provider 投影、既有 HTTP 流体、配置/向导与测试。Bridge/Node/Adapter 共用且不新增任务账本、路由库或排队器。
- 沿用环境变量 credential，不生成、保存或打印真实凭证；provider 与 Bridge credential 分离。
- 新配置显式 openai、旧配置缺省 native；不迁移旧 owner/账本，恢复查找先于新准入及当前 binding。
- stream/include_usage/user 不进入执行摘要，同直接 key 切换非流式可以回读同任务；语义正文变化返回冲突，无 key 不伪幂等。
- 已受理观察错误和不确定 submit 不触发重提；SSE 错误不附带 finish/usage/DONE。终态与最终文本来自已验证 Bridge 结果，不把 idle/EOF/断线当成功。
- 原生上游证据固定到精确提交；普通 apikey 的 force_chat_completions、自动 include_usage、header 剥离和重试限制与前置独查一致。
- 缺失 usage 不补零。网关零值与 Native 计量证据分离，明确非收费试验。
- 合成单元、真实 Bridge、原生 forwarder 接缝与完整部署的证据层级明确。Go overlay 只加入测试，不改变原生生产实现；接缝不冒充 handler/鉴权/DB 验收。

## 实现独审必须逐项验证

1. 同 key 回放的每次 Bridge task/event 都校验原 task ID、owner/source/ledger、摘要与冻结 binding，移除当前 binding 仍能回读。
2. 真实 HTTP 断线仅停止观察；生成器关闭、超时和 SSE 已发送后的异常不取消/重提任务，亦不伪成功。
3. role/comment 或 error 不能被描述为防止原生重试的技巧；仍明确上游可能缓存前缀、重试失败请求。
4. text 与 refusal 的投影不得重复、混入状态或掩盖最终改写；最终一致性校验先于成功帧。
5. 普通请求未知字段、重复/不合法 Idempotency-Key、错误 stream_options 在 Bridge 准入前拒绝；这是边界合同校验，不扩张成冗余防御框架。
6. 原生 forwarder fixture 实际运行状态必须在证据中标明。未具备 Go 时 NOT_RUN，待隔离 CI 后记录实际结果；不能拿 fork 或 Python 仿制网关替代原生代码证据。
7. 旧 native 合同、旧向导恢复及现有 Windows 测试回归；不改真实账号、生产部署或第三方私有接口。

## 当前证据状态

文档顺序已完成：蓝图 → 蓝图独审及修订 → 开发文档 → 本独审。
代码与回归尚未进行。本文件只放行编码，不放行最终交付宣传或生产使用。
