# API、任务与恢复合同 v0.1

日期：2026-11-07。本文件定义待实现合同。除源码证据明确指出的已有内容外，字段、错误码和内部动作均为设计，不是当前 Sub2API/Render Bridge 已实现接口。

## 1. 对外协议的真实范围

通过 Sub2API 暴露 Chat Completions 的受限子集。Facade 提供 `/v1/chat/completions` 和经过鉴权的 `/v1/models`；不得开放厂商认证状态、原生会话目录、任意终端或调试转储端点。

首版支持字段：

| 字段 | 规则 |
| --- | --- |
| model | 已配置并通过该阶段验证的 Harness 别名，不以厂商裸模型名冒充能力 |
| messages | 非空文本消息数组；允许 user/assistant 历史、最后一条必须为 user；可选一个开头的 system 仅在 Adapter 支持原生高优先级指令映射时接受 |
| stream | 缺省或 false；真实流式验收前 true 返回 400 STREAM_NOT_SUPPORTED |
| n | 缺省或整数 1；布尔值不是整数 |
| user | 可作为不可信客户端标记忽略，不作为租户身份、不转给原生权限配置 |

首版不支持 developer、tool 角色、客户端 tools/functions/tool_choice/function_call、图片/音频/文件内容块、seed/logprobs、stop、response_format 以及未经原生能力验证的 temperature/top_p/max_tokens/max_completion_tokens。显式请求不支持的语义时在新任务接收前返回 400 UNSUPPORTED_PARAMETER，并指出字段；不能静默宣称已执行。缺省参数使用原生 profile 默认值，不额外制造覆盖项。SDK 默认自动附加字段须在接入测试中明确处理，而不是放宽到任意参数透传。

messages 按顺序保存，使用确定性序列化提供历史上下文；原生 system 能力可用时单独映射。不能把用户正文拼接进原生 system/CLI 参数。历史重建不是恢复旧原生会话，不保证裸模型角色语义完全等价，不能把历史中的工具操作自动执行一遍。

### 示例：首版请求与成功响应

```http
POST /v1/chat/completions
Authorization: Bearer <SUB2API_USER_KEY>
Idempotency-Key: example-op-001
Content-Type: application/json
```

```json
{"model":"native-codex","messages":[{"role":"user","content":"在本次隔离工作区内完成指定任务并总结结果。"}],"stream":false}
```

```json
{"id":"chatcmpl-example-task-001","object":"chat.completion","created":1794000000,"model":"native-codex","choices":[{"index":0,"message":{"role":"assistant","content":"这里必须是本地 Harness 的真实最终回答。"},"finish_reason":"stop"}]}
```

时间和 ID 仅为格式样例。成功响应的 ID/created/原始结果持久固定，回放不重新生成；已知 usage 才能附带 usage。合法的原生拒答如实返回，不因文字内容不符合用户期待而伪装为传输失败或重跑。空正文且没有本合同支持的有效结果时返回 NATIVE_EMPTY_RESULT，不编造一句回答。

## 2. 身份与 API Key

| 凭证 | 持有者 | 权限 |
| --- | --- | --- |
| Sub2API 用户 Key | 最终用户 | 请求用户已获授权的别名；不接触内部节点 |
| Facade 服务 Key | Sub2API 服务端 | 调用本 Facade 的许可来源/模型；不可供最终用户直接共享 |
| Bridge Source Key | Facade 服务端 | 按固定 source_id 创建/查询自身任务 |
| Bridge Node Key | 指定 PC 节点 | 仅认领其被授权来源/profile、上传所绑定任务结果 |
| 厂商登录态 | 本地官方 Harness 的隔离环境 | 仅原生软件访问厂商服务；不得出现在以上 Key 中 |

鉴权缺省拒绝；缺少有效 Key 时服务不能以匿名模式启动为可用上游。`/v1/models` 同样受保护。生产内部通道须有 TLS/网络访问限制；同机私网 loopback 的传输安排在部署门禁内验证。

Sub2API 新增的可信上下文采用以下建议字段（名称待实现时固定，不是现有功能）：

```text
X-Harness-Tenant-Id       <- 认证后的租户/用户归属，不使用邮箱等明文个人资料
X-Harness-Principal-Id    <- 认证后的稳定应用/调用主体，不能由客户端自填
X-Harness-Request-Id      <- 同一逻辑调用稳定不变
Idempotency-Key          <- 用户显式键，或由 Sub2API 为本次 HTTP 请求生成
```

Sub2API 必须先剥离所有客户端同名内部头，再从认证上下文重建。Facade 只在有效服务 Key 的授权 source 下信任这些字段；来源配置固定允许的 Bridge origin，用户不能指定上游地址。Key 轮换不应随意改变 source/主体身份，主体权限变化要独立检查。每次回放仍须经过用户认证及该主体访问权限检查。

## 3. 幂等合同：先查旧任务，再判断新准入

唯一操作范围：`(source_id, tenant_id, principal_id, idempotency_key)`。来源 ID 由凭证配置产生，不能来自用户输入。不同租户或主体使用相同客户端键不会复用彼此结果。

请求摘要覆盖规范化业务输入、用户选择的别名及实际影响语义的字段；JSON 对象键序不同可等价，messages 顺序和正文不能随意归一化。不得仅用 prompt 文本 hash 当操作身份。

处理顺序必须是：

```text
认证/归属检查 -> 计算请求身份与摘要 -> 查找既有操作
  已存在且输入不变 -> 返回/等待原任务，使用已冻结 profile 和结果
  已存在但输入变化 -> 409 IDEMPOTENCY_CONFLICT；不执行
  已存在且正文过期 -> 410 RESULT_EXPIRED；不执行
  不存在 -> 能力/节点/容量准入 -> 事务创建 -> 持久确认
```

因此，节点离线、新任务开关关闭、别名后续改版，都不能阻断已完成结果的合法回放。管理员撤销主体的结果读取权限属于鉴权拒绝，不应被绕过。不得为了回放重新解析别名并用新模型执行。

同一键的并发请求只能创建一个 task；数据库唯一性约束/CAS 是权威，进程锁只是优化。并发相同请求共享观察，重复 POST 不产生第二次 start。跨 Facade 重启也必须成立。

用户未提供 Idempotency-Key 时，Sub2API 生成的 ID 只能覆盖该次 HTTP 请求内部；用户 SDK 在响应丢失后新发一个没有键的 POST 会是新操作，服务器不能可靠猜测二者相同。接入示例必须指导调用方显式传稳定键，并关闭自动生成新操作的 SDK 重试；不得声称无键也能跨请求 exactly-once。

同键重试只查询/等待/回放。Facade 到 Bridge 的 create 响应丢失时，使用同身份查询/幂等提交，不分配新 task。存储不可用时 fail closed，不能临时用内存“先执行后补账”。

## 4. 文本任务扩展（概念 DTO）

```text
schema_version = harness.text.v1
identity = source_id / tenant_id / principal_id / operation_id
request_digest / accepted_at
model_alias / frozen_profile_revision / adapter_revision
messages_text / original_request_supported_fields
node_id / slot_id / claim_epoch / start_grant_id
execution_possible / logical_state / state_version
native_session_ref [仅节点受限存储，不向普通用户暴露]
result_ref / completion_metadata / usage_provenance
```

以上字段复用或扩展既有 Bridge 合同，不另建平行队列。原有 image/video task 按原 schema 解释；新增字段不得成为旧调用的强制条件。profile 与任务必须类型匹配，不允许文本结果冒充 PNG artifact。

Source 逻辑动作：submit/get/cancel/read-result。Node 逻辑动作：heartbeat/offer/claim/start/observe-or-upload/commit/ack。具体路径优先沿用已有 Bridge API，只有既有合同无法表达文本语义时增加版本化扩展。

已核实的现有 offer 路径只有本次读取合同所列 `GET /bridge/node/v1/tasks/next`。其余动作不得仅凭本文件的概念名猜造当前路径。

## 5. 启动与恢复的最小持久证据

VPS 原子保存已接收任务、claim_epoch、start_grant_id 和 execution_possible；Node 在本地 SQLite/等效持久 journal 保存同一任务身份、一次性 launch_intent、原生会话引用、已上传进度及最终结果。

start grant 发出前必须完成持久提交。Node 用本机互斥与 CAS 保证同一许可只消费一次；Adapter 启动前的 prepare/capabilities 必须无模型调用、无任务副作用。会触发模型的“创建会话”属于 start 后阶段。

重启先 reconcile journal，后拉取新任务。不以原生 PID 可重用、进程退出、lease 过期、返回 404 或任务文字相同作为“可以重新执行”的证据。原生会话不能只读恢复观察时，保持 RECONCILING 并要求人工处置。

排队任务取消与启动许可写入必须原子竞争。运行后取消等待确认，可能遗留外部副作用；最终状态和副作用事实分别记录。终态写入后不可被旧节点/旧 epoch/重复回执覆盖。

## 6. 输出与事件

首版只传最终回答、原生完成/失败/取消事实和必要运行状态，不要求先实现完整实时事件系统。所有正文均来自原生可对用户输出的通道；不导出私密推理块或用思维文本冒充最终答复。

后续事件合同：`task_id/start_grant_id/seq/type/payload`。seq 对同一执行单调，Node 先持久再发送；VPS 唯一键去重，同 seq 同内容幂等、不同内容拒绝并调查。过期/其他任务凭证不能注入事件。

工具已在本地执行的记录只是过程事件，不能改为让远端客户端再次执行的 tool_calls。二者语义不同。原生工具开启与请求中有没有 tools 字段无关，由 profile 决定。

最终成功必须具有原生终态、匹配任务/会话和完整最终结果。`exit_code == 0` 不是独立成功条件；若记录退出码必须严格整数，False、字符串 "0" 等不能作为成功证据。GUI 适配器没有 exit code 时应有其独立已验证的终态证据，不能伪造退出码。

### 6.1 SSE 后续合同

仅已证明有真实增量文本的 Adapter 才宣传实时流式；原生按段给出的就按段转发，不宣传逐 token。生成完整答案后人为逐字拆分不是实时能力。

首次 delta.role、真实 delta.content、最终 finish_reason 和 `[DONE]` 按实际完成发送。`[DONE]` 只能在成功结束帧之后发送，不能仅因 HTTP 连接关闭而生成。失败流发明确错误并终止，不补成功 finish_reason。

传输心跳使用 SSE 注释，不伪造 assistant 文本。没有原生增量的 Adapter 保持非流式，不要求它配合其他 Adapter 的流式宣传。

客户端断开不等于取消，也不清理任务。相同 POST 重放完整历史时以受控响应头标注 replay；不承诺标准 Chat Completions POST 自动支持 Last-Event-ID 续传。真正的增量续传需要额外明确合同，后置。

事件/结果超过磁盘或缓存限额时不能静默丢 token 后正常完成；停止新准入、保留未决证据并进入错误/取消协调。通过有界缓冲和磁盘留存测试后才开放流式。

## 7. 错误和等待时限

错误体采用 `{"error":{"message":"...","type":"...","code":"..."}}`，身份/状态按受控响应头或内部记录关联，不暴露本机路径、账号或原生 session token。

| 情况 | HTTP/业务码 | 是否已接收 | 是否允许新执行 |
| --- | --- | --- | --- |
| 服务/用户鉴权失败 | 401/403 | 否 | 否 |
| 字段/能力不支持 | 400 UNSUPPORTED_PARAMETER | 新操作否 | 否 |
| 未发任务且节点离线 | 503 NODE_UNAVAILABLE | 否 | 以后可新提交；本次不自动走其他厂商 |
| 未接收且单槽忙 | 429 CAPACITY_EXHAUSTED | 否 | 后续同键再试准入；本次不自动切换 |
| 同键输入不同 | 409 IDEMPOTENCY_CONFLICT | 原操作可能存在 | 否 |
| 已接收但本次等待结束 | 504 TASK_WAIT_TIMEOUT | 是 | 只恢复同任务 |
| 已接收后状态不明 | 504 EXECUTION_UNCERTAIN | 是 | 只恢复同任务 |
| 审批等待超过 HTTP 时限 | 504 APPROVAL_PENDING | 是 | 只观察/本地审批/取消同任务 |
| 已确认原生失败或空结果 | 502 NATIVE_FAILED / NATIVE_EMPTY_RESULT | 是 | 只回放失败，不自动重试 |
| 已确认取消 | 409 TASK_CANCELLED | 是 | 否；新操作须明确新键 |
| 结果已过留存期 | 410 RESULT_EXPIRED | 是 | 否 |

HTTP 码不能单独决定重试。Sub2API 必须为 Harness 路由禁用通用同账号重试、跨账号/跨渠道 failover、响应不完整重生成和模型别名回退；只允许明确的同任务观察/回放。普通模型渠道保持原有行为。

首版 Sub2API 到 Facade 只发一个 HTTP 尝试；所有重试需要持原操作身份。客户端 SDK 也必须按此约束配置，尤其 429/5xx 自动重试。未知是否接收时不得生成新身份。传输时限层级在联调中固定，不能仅调大无限等待来掩盖失联。

## 8. Usage、价格与重复计费

原生 Agent 一次任务可能包含多个模型调用和工具往返。最终文本长度估算不是厂商实际任务 token 用量；末次调用的 usage 也不一定是完整任务用量。

只在 Adapter 能证明口径时记录 native-reported 汇总与来源，避免对累计事件重复求和，并明确 cache/reasoning/tool 子调用包含关系。缺失用量为 unknown，标准响应省略 usage；不能填写整数 0 假装真实零消耗，也不能把估算当官方精确值。

第一阶段使用不计费测试 Key/明确零收费隔离配置，不改正常用户价格。商业计费前必须决定且验收：原生可验证 token 计费，或公开说明的按任务等服务计价。不得在 unknown 下偷偷套裸模型 token 价。

业务结算幂等键与 operation 身份绑定；结果回放只产生访问记录，不产生第二笔结算。Sub2API 普通“每个 HTTP 请求结算一次”若不满足该点，必须加本渠道的最小接缝。客户端超时后的原生完成需有受信后台协调/查询机制归入原操作，不能因用户断线永久漏算或重复扣费；未完成该机制前保持不收费试验。

Facade/Bridge 只提供事实及 provenance，不建立第二套钱包，不自行给用户扣款。

## 9. 合同演进

新增能力须升级 capability/contract 版本并保存向后兼容映射。新版本不重解释既有未决任务。真实租户隔离、审批交互、文件产物、并发和长期会话属于独立验收；不能以添加字段即宣称完成。

相关：[蓝图](BLUEPRINT.md) · [复用与接缝](REUSE_AND_INTEGRATION.md) · [验收](DEVELOPMENT_AND_ACCEPTANCE.md)
