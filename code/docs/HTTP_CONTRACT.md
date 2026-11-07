# P1a HTTP 合同与真实接缝状态

这是本仓库实际实现的**候选 Harness 扩展合同**，不是宣称现有 Bridge 已支持这些字段。固定参考 Bridge `cf1e72ec3830854fa0983eb9acb92fad62a604c8` 当前仍是图片 schema；直接连接它会拒绝 harness 能力，而不会退到 fake。

## 1. HTTP 传输

`BridgeClient` 和 `NodeClient` 使用真实 HTTP 请求，无内部数据库/import 旁路。Origin 由服务配置提供，禁止含 userinfo/path/query/fragment；非 loopback 必须 HTTPS。禁 HTTP 重定向、环境代理、隐式重试；读取超时和响应上限有界。P1a 非流式响应必须有唯一、合法的 Content-Length，实际字节数必须一致；chunked、无长度、重复长度、长度与 Transfer-Encoding 并存均 fail closed，暂不支持。完整 JSON 不足以证明 HTTP framing 完整。

任何 transport/framing/截断错误视为接收状态未知。只读查找的精确 `404 + not_found` 表示该查询没找到；它本身不允许换账本或新操作。所有请求保持原身份。

**P1a server 只支持 loopback，不提供 TLS 终止、生产并发容量、反向代理信任、身份服务或公网部署。** HTTP 客户端可连接配置的 HTTPS origin，不表示真实后端接缝已实现。

## 2. Facade 入站

- `GET /v1/models`：必须服务鉴权，只列启用的 synthetic 模型。
- `POST /v1/chat/completions`：同样服务鉴权；有界严格 JSON。
- 无取消、异步查询、SSE、Responses、认证状态或本机管理端点。

服务 Bearer credential 固定 source 与允许的 Bridge 实例。来自真实网关的可信头在 P1a 测试假网关中模拟：

| 头 | 语义 |
| --- | --- |
| `X-Native-Tenant` | 已认证租户，不从 body.user 派生 |
| `X-Native-Principal` | 已认证稳定主体；当前 fake 测试默认模拟 Key 级范围 |
| `X-Native-Operation` | 路由前持久分配的操作 ID，同键重试保持不变 |
| `X-Native-Binding-Digest` | 首次选择的完整 Binding 规范化 SHA256；所有旧任务/新提交/轮询结果均核验 |
| `X-Native-Admission-Allowed` | `true`/`false`；只控制新准入，不阻断已接收回放 |

这些不是“现有 Sub2API 自动透传的头”。P1b 必须从已认证上下文构建，剥离客户同名头，并防止账户 header overrides 覆盖。客户端 body.user 和自填上述头不构成权限。

Facade 不持久化 operation 或任务。它从服务配置选择固定 Bridge，按身份 origin key 查询旧任务，验证 client digest 和冻结绑定；不存在时再检查新准入与当前允许的精确绑定，然后提交。HTTP 超时只结束等待。

输入：model + 非空 user/assistant 文本 messages，最后一条 user；stream 缺省/false，n 缺省/严格整数 1；user 忽略。不支持字段返回 400，常规字段名通过 `error.param` 指出。角色不假称完整等价。标准 usage 省略，因为 P1a 只支持显式 unknown。

## 3. Bridge 路径与当前状态

| 路径 | P1a 客户端 | 现有 Bridge main |
| --- | --- | --- |
| `GET /bridge/v1/capabilities` | 要求 `harness_contract`、`ledger_instance` | 路径存在；缺本合同能力字段 |
| `POST /bridge/v1/tasks` | 提交 harness envelope | 路径存在；仅接收图片 schema |
| `GET /bridge/v1/tasks/by-origin/{origin_key}` | 以稳定身份查询 | 路径存在；harness 内容未实现 |
| `GET /bridge/v1/tasks/{task_id}` | 观察同一任务 | 路径存在；harness 内容未实现 |
| `GET /bridge/node/v1/tasks/next` | 读取 task_id，再读 task | 路径存在；图片 profile |
| `GET /bridge/node/v1/tasks/{task_id}` | Node 读取绑定任务 | 路径存在；harness 内容未实现 |
| `POST .../tasks/{task_id}/claim` | `mode=execute`，读取 claim/epoch/attempt | 路径存在；需 harness 类型扩展 |
| `POST .../tasks/{task_id}/start` | claim_id → grant/attempt/may_execute | 路径存在；需 harness 类型扩展 |
| `POST .../tasks/{task_id}/harness-observation` | pending + 活性/受控错误码 | **新候选路径，尚未在 Bridge 实现** |
| `POST .../tasks/{task_id}/harness-result` | 不可变类型化终态与结果 | **新候选路径，尚未在 Bridge 实现** |

P1b 若采用不同兼容路径，需同时改客户端/合同/测试，不能在环境配置中假定已有字段。没有复用 image upload MIME/尺寸通道伪装文本。

## 4. Envelope 与查询归属

`schema_version = harness.invoke.v1`。Envelope 保存：

- `identity = {source, tenant, principal, operation}`。
- `origin_key = 'h_' + SHA256(canonical(identity))`，不是 prompt hash。
- `request_digest = SHA256(canonical(normalized client request))`。忽略不参与执行的 user；默认 n/stream 规范化；正文和消息顺序原样保留。
- `ledger_instance` 为配置冻结的实例 ID；不从用户参数接受。
- `request.binding = {binding_id, revision, model_alias, native_model}`。
- `request.messages` 与 `request.client_request`。回读验证摘要、消息一致性和公开别名。

任务还保存 remote_task_id、attempt_id、稳定 created、claim_id/epoch/start_grant_id、outcome、liveness、result/result_expired。Task origin/identity/ledger/client digest/frozen binding 任一不匹配均拒绝，不重试生成。

本实例 ID 尚非可信备份代际/硬件围栏。真实三仓恢复、数据库回滚与历史实例协调未实现，不能凭字段名声称解决。

## 4.1 Node 执行前信任配置

`Runner` 强制显式传入不可变 `NodePolicy(source, ledger_instance, allowed_owners, bindings)`。配置由本地操作者提供，不从任务派生；owner 是 `(tenant, principal)` 白名单，bindings 是完整冻结 Binding 元组。没有默认“允许任意来源/模型”的策略。

`NodeClient` 将 next 返回的 task ID 与随后获取的 body.remote_task_id 绑定为 `NodeOffer`。Runner 在任何 claim、journal 写入或 start 之前，验证精确顶层字段、请求/身份/绑定字段、可信 source/ledger/owner、origin 身份摘要、规范化 client_request 摘要、messages 一致性、公开别名及允许绑定全文摘要，并要求 pending/not_started/无 grant/result/expiry。无效 offer 不进行任何原生启动。

旧 journal 恢复不重新执行。source/ledger 改变时，旧记录返回 `ROUTE_RECONCILIATION_REQUIRED`，不向新路由上传或新启动。仅当前同一 source/ledger 的 binding 被下线时，已接收操作仍可只读恢复/重传其不可变结果。

这些是已认证 Bridge 通道下的完整性和授权配置检查，不是对被攻陷 Bridge 的密码学远程证明；可信 TLS、服务凭证和真实三仓身份构建仍是后续门槛。

## 5. 结果与活性

首版输出块只允许 `text` 或 `refusal`。成功必须有严格布尔 native_completion.confirmed=true、模型选择与报告证据匹配、有效非空块。thought/progress/tool 输出不当最终正文。usage 仅 `{state:'unknown'}`；不填零。

`outcome` 与 `liveness` 分离。Node 观察异常保留 pending/unknown，业务终态不可变。成功或失败且活性 unknown 都继续占槽。返回最终回答不等于安全释放本地执行能力。

Node 在上报结果后检查返回回执的 schema/task/claim/grant/result 全部一致，才持久确认 ACK。ACK 丢失只重传结果。

当前 Node 不提供围栏恢复/强制释放，未决 intent 和未知活性持续隔离。测试假账本接受观察更新只是夹具行为，**不能当生产停机证明协议**。真实停止必须同时证明原生执行已停止和旧许可消费者不能迟到 spawn。

## 6. 测试假网关的路由冻结

`tests/fakes/gateway.py` 的 SQLite 唯一键覆盖 tenant/principal/client key，事务胜者保存 operation、client digest 和实际目标快照（测试 origin、测试 credential、binding digest）。并发败者读取胜者，不重新选渠道。

明确未接收的 429/503 后保留相同路由，后续重新检查执行授权；unknown receipt 也携带实时 admission 权限到 Facade；当前不允许执行时只能查询已接收任务，不能借未知状态绕过新准入权限。已接收结果回放仍须当前用户鉴权，不重复预留执行资格。

此夹具没有真实账户路由、余额预留、结算、数据库恢复代际或生产凭证存储。只用硬编码且明确 test-only 的无效凭证；生产不得存储真实服务凭证快照，应使用受控凭证引用与稳定逻辑来源。
