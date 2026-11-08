# 普通 OpenAI Provider 开发与验收文档

日期：2026-11-08 UTC。状态：**独立开发设计审计通过，允许本文最小范围编码；见 [开发审计](NATIVE-OPENAI-PROVIDER-DEVELOPMENT-AUDIT.md)**。
依据：[当前蓝图](NATIVE-OPENAI-PROVIDER-BLUEPRINT.md)及[设计审计](NATIVE-OPENAI-PROVIDER-DESIGN-AUDIT.md)。代码基线/范围同蓝图。本文件不扩大到账号/生产部署或改动 Sub2API/Bridge。

## 1. 实施顺序与最小文件范围

1. 独立审计本文件，修正具体阻断后再编码。
2. 新增 `code/src/native_harness_api/openai_provider.py`，只做标准入口身份、原 Bridge 查询/提交/观察及标准 SSE 投影。复用 `Facade` 的已有鉴权/绑定校验、`contracts` 规范化/envelope/checked_task/completion、`event_contract.project_events`。不复制 Adapter、Node 或 Bridge 的账本实现。
3. `http_server.py` 增加一个明确 SSE body 类型，复用现有鉴权前 body 解析/HTTP 错误处理；普通 JSON 返回原行为不变。逐帧 flush、写超时 5 秒且不超过观察期限，断线关闭生成器但不改任务。SSE 不发送 Content-Length，使用 Connection:close，`Content-Type:text/event-stream`、`Cache-Control:no-store`、`X-Accel-Buffering:no`。
4. `NativeFacadeConfig` 增加 `ingress`：缺省 native（旧行为），显式 openai 构造新 Provider。沿用 `NATIVE_FACADE_TOKEN` 环境变量的已有 credential 读取，不自动生成/保存/打印密钥，不增加凭据数据库。Bridge source token 与 provider token 必须不同。只有普通入口 `wait_seconds` 可到 300 秒，示例/新生成配置设 180；旧 native 保留 30 秒上限。
5. `prepare_native_deployment.py` 新增 v2 普通 provider specification：原 v2 去掉 `user_id/api_key_id`，加入 `ingress:"openai"`。其余 source/ledger/target/目录模型不变；owner=`["provider", identities.source]`。生成原 `bridge.json`、`facade.json`、`windows-node.json`、identity 文件，另输出无秘密的 `openai-provider.json` 配置说明，**不输出或要求** `sub2api.native-harness.yaml`。旧 spec 结构/输出不变。
6. `quick_start.write_connection` 新连接默认用该标准 spec，只问 Bridge origin/目录，不再问 Sub2数字ID；保留已存在配置优先恢复。半成品连接检测同时识别新旧说明文件，不覆盖重建。打包 allowlist 加入新的接入说明，源码模块由既有 tracked allowlist 自动纳入；不换 vendor 资产。
7. 同步 README、AGENTS 当前入口说明及 `code/docs/OPENAI_PROVIDER.md`、HTTP合同/状态导航。旧历史文档只加准确指向，不大规模改写旧审计。无需重构全部旧 source package 工具，旧三仓包明确是 legacy 专用路径。
8. 新单位/HTTP/真实 Bridge 测试与原生 Sub2API 接缝 fixture；完整现有 Python tests/offline_demo/compileall/diff检查后独立代码审计。草稿 PR 基于旧 `work/native-channel-p1b`，不合并或部署。

## 2. 请求、身份与冻结的实现规则

- 先 Bearer 鉴权，再路径/输入校验，再任何 Bridge IO。支持路径仅 models 和 chat/completions，未知 API 404。
- Provider只允许一个 credential→一个配置 owner，固定资源域；只列当前模式允许的 binding。客户端内部身份头完全忽略。`user` 校验后沿用 normalize_request忽略，不参与digest。
- 输入先检查 stream 为严格 bool；stream_options仅允许 include_usage 严格 bool且要求stream=true；去掉stream_options、把stream置false，再调用现有normalize_request。随后要求一条user纯文本消息。unsupported字段保留400及param，不能静默丢弃模型参数。
- **execution digest不包含stream或stream_options或user**；因此直接相同key可切 stream true/false、include_usage true/false读取同任务。model/messages/n语义改变返回IDEMPOTENCY_CONFLICT。不会为每一种观察模式新建任务。
- 可选 Idempotency-Key必须单头、非空、有限长度（最多256个ASCII可见字符）。operation=`provider-`+SHA256(key)，身份仍含固定source/owner；无头时`provider-`+随机UUID。相同正文无key必须是独立operation。
- `lookup(identity.origin_key)`先于准入/binding当前可用性检查。存在时 `checked_task`验证原归属、请求摘要、ledger，并保存其中原binding摘要和task_id供所有后续观察对照；不存在才检查accepting_new和当前binding、执行一次submit。新submit回执与所选binding核对。不另建route tombstone/队列。
- submit返回明确accepted=false错误时保留原错误；submit响应不确定保守报告409 `EXECUTION_UNCERTAIN`，不重发submit。随后Bridge读取失败、已受理等待到期/unknown/审批等待均返回明确409或流内error；结果过期410、摘要冲突409等语义保留。**409仍可能被SDK重试**，文档示例关闭SDK自动重试。
- task终态、native停止、观察失败彼此独立。返回回答或错误都不能释放槽、取消任务或允许晚到旧worker重启。

## 3. JSON/SSE 生命周期

### JSON

在单次有限观察期限内轮询同一task ID。每次验证identity/digest/ledger/原binding和ID；只有现有completion验证通过才返回200。等待超时不改变任务。服务器例外输出固定错误码，不返回异常/路径/原始内部数据。

### SSE

1. 鉴权、payload、查找/首次提交完成后返回SSE body；响应开始后任何观察错误作为`data: {"error":...}`后EOF，不再尝试改HTTP状态。
2. 先发一个标准role chunk；读取现有Bridge分页事件，由project_events验证所有seq/task归属与原冻结binding。task_status/native_status只产生注释，不映射正文或成功。
3. text_snapshot只发送已发文本之后的新增后缀；非前缀立即NATIVE_STREAM_REWRITE_REQUIRED，不发送新快照或成功终态。
4. completion事件只能来自现有Bridge task_terminal验证。核对最终文本与已发文本前缀一致，然后补齐正文或refusal，最后发finish_reason=stop与[DONE]。最终正文缺失/错误/改写时无finish_reason/usage/DONE。重复相同快照不重复正文。
5. 已完成任务首次读取可直接用权威completion生成标准chunks，无需依赖保留的旧事件日志；这允许在binding移除/节点离线时直接key回放。
6. keepalive只是注释，观察到期/队列无进展不生成空成功。每次观察只向Bridge取页/同task，绝不再次submit。
7. 不支持正游标恢复；`Last-Event-ID`非空非0提前400，无新准入。0仅允许直接有稳定key且已有任务的只读回放；缺任务不创建。新模式不支持native-events协议头/私有事件路由，不把headers当权限。
8. 输出与请求沿用现有字节限制，Bridge每页最多8个有界事件；不新增无限缓冲，旧HTTP JSON路径不受影响。

unknown用量无usage字段（包括流），include_usage只是被接受的请求偏好，不是制造用量的许可。不能用`usage:null/{}`作为终态标记。

## 4. 原生 Sub2API 配置和源代码证据

固定 `Wei-Shaw/sub2api@6b4b37db43443b5e88e424fe3c69602cc3810dfb`。仅参考协议，不复制其实现。

| 实际合同 | 上游源码路径及行 |
| --- | --- |
| 普通 `platform=openai/type=apikey` 通过 `extra.openai_responses_mode=force_chat_completions` 强制CC；不是CN的api_protocol字段 | `backend/internal/pkg/openai_compat/upstream_capability.go:74-114`；`frontend/src/components/account/CreateAccountModal.vue:4814-4816,5813-5820` |
| Base URL允许origin或/v1；正确追加chat/completions | `backend/internal/service/openai_gateway_cc_pipeline.go:155-165`；`openai_endpoint_url.go:8-27` |
| 模型映射后用已核实Native别名 | `openai_model_mapping.go:8-21`；`openai_gateway_chat_completions_raw.go:84-114` |
| Bearer/JSON/Accept由Sub2自己生成；只透传accept-language,user-agent | `openai_gateway_cc_pipeline.go:205-230`；`openai_gateway_chat_completions_raw.go:20-37` |
| 不透传Idempotency-Key，静态header override不能代替每请求幂等 | `openai_gateway_cc_pipeline.go:223-230,246-249` |
| stream强制include_usage=true | `openai_gateway_chat_completions_raw.go:165-170,479-486` |
| 缺usage可透传当前workbuddy-/codex-别名，但网关内部保留零值计数 | `openai_gateway_chat_completions_raw.go:320,368-370,542-545`；`openai_gateway_usage.go:180-195,390-405` |
| Grok特定billing guard，不是所有OpenAI-compatible通用限制；不改名绕过它 | `openai_gateway_usage_integrity.go:30-43`；`openai_gateway_chat_completions_raw_test.go:203-207` |
| HTTP/网络failover与handler重试存在 | `openai_gateway_upstream_errors.go:303-344`；`openai_upstream_transport_error.go:147-160`；`backend/internal/handler/openai_chat_completions.go:332-392` |
| 过早EOF未输出时可failover；usage对象/finish_reason/DONE任一可被认为终态 | `openai_gateway_chat_completions_raw.go:430-450`；`openai_raw_stream_truncation.go:42-78` |

接入示例仅是配置指引，不代用户操作真实Sub2账号。不导入自定义渠道类型、不修改其源码、不启用provider探测模型任务。Base URL和key之外选择已有CC模式、模型映射是正常上游配置，不是专制适配分支。

## 5. 测试层级与通过条件

| 层级 | 必测内容 | 不能替代的证明 |
| --- | --- | --- |
| 新Provider单位/HTTP fixture | 仅Bearer、models、非法key/特殊头伪造、unsupported参数先拒绝、include_usage、JSON、SSE追加/改写/拒答/无终态/超时、disconnect不cancel；同key一致/冲突/不同stream复用；无key相同正文两操作；同key完成后移除binding仍回放 | 不代表真正Bridge或厂商 |
| 新配置/向导/打包测试 | 默认普通配置无Sub2数字ID/专用yaml，三方owner/binding一致、准入关闭、token仅env；native旧配置/旧向导恢复无改变；新说明被打包 | 不代表部署可达 |
| 实际Bridge HTTP + synthetic Node | no-X-Native标准请求真正经Bridge执行；同直接key只执行一次；无key两个HTTP操作可执行两次；单槽/unknown/断线不重派；SSE与终态投影 | 不代表真实Harness |
| 原生Sub2API普通forwarder接缝 | 固定原生代码，普通apikey/account fixture，真实raw CC→Provider→Bridge→合成Node，模型映射、header剥离、stream_options、JSON/SSE/unknownusage。测试fixture仅存Native；用Go overlay虚拟加入test文件，不改上游源码。分别说明账号repository/数据库/handler是否实际参与 | 不代表完整Sub2用户/DB/生产链或真实计费 |
| 回归/CI | 全Python suite、compileall、offline demo、diff check、Windows既有自写peer/owned cleanup矩阵 | skips不能算pass，历史数字不能复用 |

本云环境已发现 `/usr/bin/go` 不是Go编译器，且无已安装可用Go工具链；真实Sub2接缝在工具链可用前为NOT_RUN。可以在草稿PR的隔离CI通过官方Go工具链和固定原生上游运行同一合成测试，不因本机缺工具编造通过。没有可运行证据就明确报告，而不是退回改造fork冒充验收。

发布前独审只检查本增量关键路径、兼容恢复、事实声明及上述实际结果；不无限扩展邻接防御工作。真实账号/厂商配额/本机登录复用/模型/收费/TLS/生产部署/商业许可始终另列NOT_RUN或NOT_VERIFIED。

观察循环与HTTP写出共用同一截止时刻，末尾错误flush预留在预算内，不加额外1秒。Bridge单次IO仍使用既有有界超时，正在进行的IO可能晚于观察截止才返回；这不是端到端硬实时deadline。预算用尽或客户端已断开时可能只收到不完整EOF，没有成功finish/[DONE]，不代表任务取消。
