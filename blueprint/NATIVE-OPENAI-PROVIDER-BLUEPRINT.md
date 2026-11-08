# Native Harness 普通 OpenAI-compatible Provider 蓝图

日期：2026-11-08 UTC。状态：**独立设计审计修订完成；见 [设计审计](NATIVE-OPENAI-PROVIDER-DESIGN-AUDIT.md)。尚未编码**。

## 1. 目标与覆盖关系

用户要求不再为接入 Native Harness 修改原生 Sub2API。Native 服务直接提供普通上游的 Base URL + API Key；保留已实现的 Node、Bridge 唯一执行账本、WorkBuddy 与 Codex Adapter。只修改 Native 仓库；本稿是对既有专用 X-Native / Sub2API 分支路径的显式替代入口，不修改或删除其旧任务恢复合同。

新普通入口叫 `ingress: openai`，旧配置缺省仍是 `ingress: native`。新增配置生成及首次连接默认输出普通入口；已有 `windows-node.json` 优先原样恢复，不迁移旧账本，不自动改写旧部署。先蓝图、独立审计修订，再开发文档及审计，最后实现和分层验证。

当前固定基线：

| 角色 | 提交 |
| --- | --- |
| Native implementation | `3402cc1dc75799ed64916f959de6982a7316c8ac`，与 GitHub `work/native-channel-p1b` 一致 |
| 已有 Bridge（本轮不改） | `52dc083207a8a02dfc14fe2ec1ea10f0cc0a54a8` |
| 历史 Sub2API fork（仅旧合同参考，本轮不改） | `b25ff6169885c66308ed3dffd93da9c3cb3fcf79` |
| **原生 Sub2API** `Wei-Shaw/sub2api` main | `6b4b37db43443b5e88e424fe3c69602cc3810dfb`，本轮只读核对 |

不是全量 OpenAI 实现，不新增 Responses、多租户沙箱、取消/审批 API、计费系统、路由数据库、队列或后台重试。仅一个可信本地资源域、一个 Node 总槽 1，非收费试验。

## 2. 架构与身份

```text
原生 Sub2API 普通 OpenAI / API Key 账号
  Base URL=https://<operator-provider>/v1 + provider API Key
  原生设置强制 Chat Completions + 显式模型映射
        ↓ Authorization: Bearer <provider key>
Native OpenAI Provider（HTTP/协议、观察，不执行模型、不另建账本）
        ↓ 原有 Bridge HTTP envelope / task / event
专用 Bridge 唯一账本与执行许可
        ↑ 原有 Node 主动连接、journal 与全局单槽
        ├ WorkBuddy public SFE
        └ Codex 已有 CLI / managed 兼容 Adapter
```

Provider key 是运营者配置的上游服务凭证，不是 Sub2API 终端用户 Key。新入口从服务器配置固定 source、ledger、owner 和模型集合；不要求、不信任客户端 `X-Native-*`，`body.user` 仅忽略，不当租户或会话身份。原生 Sub2API 继续做它自己的下游鉴权，但普通上游看不到可信终端用户身份。本期不声称按终端用户隔离、计费或授权本地工具。

新生成 owner 用稳定的 provider 资源域标识，无需询问 Sub2API 数字 user_id/api_key_id；它与 Bridge/Node 配置一致。已有 owner 仅作为不透明资源域标识保留。配置不能使两个独立 Bridge/Node 共用同一执行目录。默认准入仍关闭，操作者自行部署和开启。

Provider 继续只监听 IPv4 loopback；现有反向代理负责 TLS，容器网络下 Base URL 必须从 Sub2API 容器可达，不把其 127.0.0.1 误当宿主机。配置生成不部署、不创建持久凭据。

## 3. 最小 API 合同

- `GET /v1/models`：Bearer 鉴权，只列已配置、允许新准入的真实别名（fixture 用 synthetic 名）；标准 model 对象有 id/object/created/owned_by。
- `POST /v1/chat/completions`：同一 Bearer 鉴权，不需要专制内部头。支持一个非空 `user` 纯文本 message，`n=1`，`stream` 缺省/false/true，忽略可选 user 字符串；继承请求/结果预算。
- 接受 `stream_options: {include_usage: true|false}`（仅流式）；这是原生 Sub2API 流请求自动加入的字段，不保证存在真实用量。其他未知字段、system/developer、多轮、tools、图片/音频、采样参数明确 400，不能悄悄拼接/丢弃成伪模型语义。
- `/v1/responses` 等未实现路由明确 404。不要用路由探测触发推理；Sub2API 用其现成 `extra.openai_responses_mode=force_chat_completions`。
- 非流成功返回现有经过 native_completion/model_evidence 验证的 Chat Completion，输出为空/未确认/模型不符不成功。原生已执行工具不会变成远端 tool_calls。
- HTTP 错误沿用 OpenAI 风格 `{error:{message,type,code,param?}}`。新入口等待到期/已受理的观察失败返回明确错误，不取消、不重新执行；客户端断线同样只停观察。
- 单次观察等待默认 180 秒、最大 300 秒，内部轮询/写出有界。不是原生执行超时；原任务及其活性由 Bridge/Node 管理。

这是受限 Chat Completions provider，普通客户端必须按上述能力调用。不会声称任意聊天 UI 或原生 Sub2API 的所有探测/默认测试 payload 都已支持。

## 4. 操作身份、重试与恢复的真实边界

1. **有直接收到的 `Idempotency-Key`**：在固定 source/owner/ledger 范围内转为稳定 operation，查 Bridge 已有任务；相同键但规范请求不同返回 409。已经受理的冻结 binding 从该任务读取，模型下线、准入关闭或节点离线仍可回放。Key 不进入日志，不以 prompt hash 去重。
2. **没有该头**：为每次 HTTP 请求生成随机 operation。这是普通 OpenAI-compatible 请求，不强迫第三方带私有头；两次相同正文也允许是两个独立请求，绝不按正文偷偷合并。
3. **原生 Sub2API raw CC 不透传 Idempotency-Key**：只透传其通用 header 白名单。因此其自动重试、SDK 自动重试、断线重发或 failover 会成为新的 operation。Bridge 仅保证同 operation 不再执行，**不能保证经原生 Sub2API 的多次 HTTP 尝试只执行一次**。
4. Provider 本身不重试 submit、不换模型/渠道、不创建第二任务队列。已受理等待/未知状态使用 409 表达“已受理/观察冲突”，不表示不可重试；OpenAI SDK 常见默认也会重试 409。调用端必须显式 `max_retries=0`，使用独立测试账号组且避免 failover；Provider 无法约束网关/SDK/网络行为，不能把状态码或配置建议当成已证明的端到端幂等。
5. 同键且 Bridge 明确未接收时没有新增 route tombstone：下次请求使用当时有效配置。只有 Bridge 真正接收后冻结绑定；不为保持旧专用路径“首次未准入也冻结路由”的额外保证新增数据库。更换 source/owner/ledger 不构成恢复，旧任务保留原入口/原凭证资源域查询。
6. 标准 Sub2API 路径在失败后没有可用的端到端同键回读通道。不得让用户“重发就能恢复原任务”；如需这种保证，只能保留旧专用路径或未来另立设计，本期不偷偷扩展。

这是支持普通上游必须如实接受的取舍，不是用状态码或自填 user 字段伪造保证。

## 5. 标准流与快照改写

继续读取 Bridge `native-events-v1` 权威事件，但普通入口只输出标准 `data:` SSE，`chat.completion.chunk / choices.delta`。不把内部 native 命名事件冒充 OpenAI SSE，也不需要 Sub2API 新解析器。

- 验证任务身份及绑定后开始流；按需发送 role 和心跳注释，不把状态/心跳拼进回答。
- 仅当下一快照以已发正文为前缀时，输出新增后缀。native snapshot 不是 token 计量，也不是必须逐字到达的 token 流。
- 缩短/改写/最终正文不一致：输出标准 error `NATIVE_STREAM_REWRITE_REQUIRED` 后关闭；**不发 finish_reason、不发 usage、不发 [DONE]**。不能把更正内容追加到旧正文。
- 仅经过终态、模型、非空结果验证并与已发送文本一致，才发最终剩余文本/refusal、finish_reason=stop 和 [DONE]。终态前 EOF/观察失败都不是成功。
- 直接稳定 key 调用者可改 stream=false 读同任务权威结果；经原生 Sub2API 因 key 不透传不能承诺同样恢复。标准流不提供正游标续传。
- 旧 `ingress:native` 的内部事件、专用双流与恢复不变；新普通模式不暴露该私有事件路由。

## 6. 用量和商业边界

已有 WorkBuddy/Codex 结果完整 token 覆盖未被证明，继续省略标准 usage，不补零、不用字符估算、不把局部诊断计量当完整消耗。即使请求 include_usage=true 也如此。若原生 Sub2API 在自己用量结构/日志中把缺失计量显示为 0，那是网关的缺省值，不是 Native 宣称零消耗；不能据此开启收费或宣称准确结算。保留显式非收费试验说明。

官方 Codex App Server 认证说明限制用于商业/托管服务，复用本机登录、开源许可和技术可调用不等于 404token 商业转供授权。WorkBuddy 服务条件与项目/第三方许可证亦须独立核定。本轮不登录、不读取凭据、不调用真实模型、不部署、不开放商业服务、不复制上游实现源码。

## 7. 原生 Sub2API 接入事实与发布边界

已核对的上游机制：普通 openai/apikey 账号 Base URL 拼接为 `/v1/chat/completions`；显式 force_chat_completions 避免默认 Responses 路由；模型映射使用 Native 已核实别名；流式自动加 include_usage；不透传稳定操作头；普通错误与无有效输出可触发自动重试/failover。精确文件/行与限制由开发文档记录。

完成标准：最小入口 + 配置/新连接路径 + 标准 JSON/SSE + 核心回归与独立代码审计，草稿 PR，不合并。测试分开：单位/HTTP fixture、真实 Bridge + synthetic Node、原生 Sub2API 接缝（能运行则运行，否则明确 NOT_RUN）、Windows 原有 fixture CI。绝不把改造 fork 接缝通过算作原生 Sub2API 通过；不把可用源码算作用户真实账号/商业部署验收。
