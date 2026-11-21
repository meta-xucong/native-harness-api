# 单用户 WorkBuddy local-trial 渠道门禁补充

这是 P1b synthetic-only 合同的显式扩展，不修改既有蓝图历史字节。用户将自行部署并连接账号；开发及模拟验证不以代用户登录为前提。此门禁代码不代表真实账号、模型或生产收费已验收。

## 精确配置

- Facade `mode="workbuddy_local_trial"`，只接受一个 service token 对应一个 `Service.owner=(tenant,principal)`。source/ledger 冻结头与 owner 在任何 Bridge IO 前核对。
- Node `NodePolicy(mode="workbuddy_local_trial")` 限一个 owner；Bridge `HarnessConfig.execution_mode="workbuddy_local_trial"` 亦限一个 owner。两者仍使用既有单槽、intent、账本与不确定状态恢复流程。
- Sub2API route `execution_mode: workbuddy_local_trial`，仍显式 `enabled` / `accepting_new` 及单用户/单 API Key 白名单。mode 随完整 route 写入 PostgreSQL operation tombstone，重配不能改变既有操作身份。路由撤除仍可查询旧回执，新准入=false。
- 仅 `workbuddy-*` 模型别名，`native_model` 不得以 `fake-` 开头。真实模型身份由原生 runtime 的模型选择/readback 验证，不由前缀证明。
- 默认未给模式仍是 synthetic-only；拼错或 production 均拒绝。原 synthetic/fake 配置不能静默当作 WorkBuddy 输出。

## 首版能力

一个新请求仅一条 user 文本消息、非流式、n=1、独立会话。多角色/多轮内容在新准入前拒绝，绝不拼成一段伪装原生语义。标准多模态、tools、system、流式及计费仍未开放。

专用 Sub2API 地址仍是 `POST /v1/native-harness/chat/completions`，要求 Idempotency-Key。不是普通 `/v1/chat/completions` 通用账号渠道。用户/API Key 身份只取认证上下文，外部 `user` 与内部头不能授权。响应 `X-Native-Billing: disabled-local-trial`，usage未知省略，不走收费/普通provider/failover。

回放先于新准入。去掉绑定、禁止新准入或节点离线不应使已接收请求重生。旧操作 mode 不匹配当前配置不能授予新执行。没有强制释放未知任务或删除日志解锁入口。

## 验证边界

测试用自写执行替身；真实原生账号/模型仍由用户本地认证后测试。新增临时 PostgreSQL 测试仅接显式 disposable DSN，不自动猜测或访问应用数据库。三仓 HTTP 测试仍用 API Key repository fixture；该限制即使 route store 换成真实 PostgreSQL 也不消失。

运行时原生资产/账户profile/子进程绑定由独立 adapter/runtime 切片实现和审计，不能只修改这些模式字段就宣称整包可用。
