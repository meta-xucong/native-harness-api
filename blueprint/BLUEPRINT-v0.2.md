# 404token 本地原生算力渠道优化蓝图

版本：v0.2 建议稿  
日期：2026-11-07 UTC  
审阅基线：`meta-xucong/native-harness-api` 的 `82e8cfeaf6ce05857dba590cf1d4bb63efd42ea2`  
交付性质：可供开发采用的优化蓝图。用户已授权审计后开发首版代码、在云端工作并上传 GitHub，蓝图与代码分目录。本文是蓝图与研究阶段记录；具体代码改动、测试与上传结果另记于实现交付报告，不由本文预先宣称完成。未部署、未运行真实 Harness、未调用付费推理。

## 先看结论

**项目应定位为 404token 的本地原生算力渠道适配层，而不是另一套 Agent 平台，也不是厂商凭据反代。** 用户继续使用 404token 的地址、API Key 和模型名；Sub2API 继续负责用户、渠道、模型路由和计费；本地程序驱动真实电脑中已登录、真实运行的 WorkBuddy 等 Harness；Render Bridge 继续作为唯一执行账本。

v0.1 的本地执行、唯一账本、禁止不确定状态重跑、可信身份、用量不造假等原则值得保留。需要重点纠正的是产品中心和模型合同：

1. 从“一个 Harness 对应一个别名”提升为“一个**可验证的原生模型绑定 × 能力配置**对应一个可运营渠道”。WorkBuddy 是执行器，不是模型名。
2. 标准模型 API 能力和原生 Agent 能力分别声明。真实支持什么就提供什么，不能因为首版只有文本而永久锁死，也不能因为能输出 CC 格式 JSON 就宣称完整 OpenAI 兼容。
3. 首版仍可单 PC、单槽、单模型、非流式、可信用户、不收费。预留结构而非提前实现多模态、工具回调、多节点调度或独立运营平台。
4. 首先核验 WorkBuddy 的**官方可用入口、模型选择与回读、终态、权限及服务条款**，再决定适配器。社区 ACP 实现只是技术线索，不自动等于稳定官方能力。
5. Sub2API 不是只填一个 base URL 就完成。至少需要本渠道专用的可信身份、稳定操作身份、禁止新执行型重试、用量未知处理和幂等结算接缝。
6. 对外承诺限定为“同一已接受操作不因桥接层恢复而再次启动”，不是任意本地工具副作用的全局 exactly-once。拿不准是否执行，就保留事实并协调，不重新生成答案掩盖问题。

建议下一步不是完整开发，而是完成两个短路径：先做 WorkBuddy 入口与模型绑定的无付费证据核验，再以 FakeHarness 跑通 404token 用户 Key 的单模型端到端合同。若 WorkBuddy 的模型绑定门槛不成立，及时作产品决策，不把时间耗在外围平台建设上。

## 1 本文如何区分证据和设计

本文使用四种标记：

- **已核实**：本次读取了具体版本的仓库代码或官方文档。只证明该来源包含相应内容。
- **待实测**：来源支持这种实现方向，但尚未在用户的电脑、账号、404token 部署中验证。
- **设计要求**：建议 v0.2 新增或修改的合同，不是现有产品已经支持的配置项。
- **开放决策**：需要由真实能力、商业授权或运营选择最终确定，不能靠文档补齐。

真实模型调用、WorkBuddy 本机行为、生产隔离、404token 联调和商业授权均没有在本次完成。v0.1 的 `48/48 PASS` 是历史文档与抽象模型检查，不作为本稿实现已通过的证据。[S1]

## 2 目标和不做什么

### 2.1 用户最终拿到的体验

```text
用户的应用
  POST 404token 的模型 API
  Authorization: Bearer <404token 用户 Key>
  model: <已发布且实际验证的模型别名>
        |
        v
Sub2API 认证、模型路由、额度与结算
        |
        v
本项目提供的受限标准 API 上游
        |
        v
真实 PC 上的原生 Harness 完成实际执行
```

用户不需要知道本地机器地址、不拿 Node Key、不直接访问 WorkBuddy 账号，也不需要学一套新的 Agent 平台才能获得基本模型服务。与此同时，404token 必须公开该渠道的真实语义、支持字段及已知限制。

本地执行的含义是：**模型请求由真实本地 Harness 按其原生方式发出**。它不意味着模型权重在本机运行。厂商模型计算本来在其云端，不改变这一边界；禁止的是我们抽取凭据、自己实现或云端回退成厂商内部模型请求。

### 2.2 明确排除

- 不抽取 Cookie、订阅 token 或内部认证材料后做模型反代。
- 不在 VPS 上安装 Harness 充当“本地执行”，不在 PC 离线时改走厂商直连。
- 不再建钱包、用户体系、通用 Agent 编排器、双重队列或第二套模型路由平台。
- 不以一个电脑/一个账号能登录为依据，推定有多用户商业转供权。
- 不把能选到模型名等同于具备该模型所有裸 API 参数和性能。
- 不默认开放任意本地文件、终端、插件、桌面控制或自动批准。
- 不为了首版扩展多节点调度、长期会话、多模态 UI 或完整管理后台。

## 3 相比现有蓝图的实质变化

| 维度 | v0.1 | v0.2 建议 |
| --- | --- | --- |
| 产品定义 | 直接定义为原生 Agent 执行服务 | 定义为本地原生算力渠道；原生 Agent 是显式能力模式之一 |
| 模型粒度 | `native-codex` 等 Harness 别名为主 | 模型绑定、执行器版本、能力配置分别管理，绑定真实模型证据 |
| API 长期边界 | 主体围绕 `harness.text.v1` | 首版仍 text-only，但内核采用可扩展的类型化输入输出；不预先实现新模态 |
| 工具 | 保留原生工具，拒绝伪造远端 tool_calls | 增加原生工具和客户端工具调用的两份独立能力合同 |
| 模型选择 | profile 有版本，但模型选择证据不足 | 冻结 requested、selected、reported 与选择证据；不允许隐式切模型 |
| 请求恢复 | Bridge 内的幂等与启动窗口较完整 | 补 Sub2API 路由前操作绑定，避免换账户/映射绕开去重 |
| 终态 | 业务结果和执行状态容易混淆 | 结果状态与原生活性分别保存；失败不自动释放仍可能执行的槽 |
| 计费 | 提出幂等结算原则 | 复用现有结算去重，明确 operation ID 优先级、超时对账与换 Key 范围 |
| WorkBuddy 路线 | 以社区 ACP/sidecar 为候选 | 官方入口优先核验，云端任务 API 与本地执行严格区分 |
| 实施节奏 | P1 到 P5 | 增加短而明确的模型与入口证据门，先判断能否做成该模型渠道 |

保留 v0.1 的核心安全与恢复约束，不因重新定位产品而降低它们；也不把所有生产门槛提前塞进 FakeHarness 阶段。

## 4 最小架构和职责

```text
                         VPS 或现有服务环境
┌──────────────────────────────────────────────────────────┐
│ Sub2API / 404token                                        │
│ 用户 Key、模型映射、操作路由绑定、额度、计费、调用日志      │
│            │ 服务鉴权 + 可信调用归属 + 稳定 operation      │
│            v                                             │
│ Native API Facade                                        │
│ 协议校验、能力检查、消息/事件映射、观察和回放同任务         │
│            │ 版本化 Bridge HTTP 合同                      │
│            v                                             │
│ Render Bridge 独立实例与数据目录                          │
│ 唯一执行账本、原子准入、claim/start、结果、节点恢复         │
└───────────────────────────▲──────────────────────────────┘
                            │ PC 主动 HTTPS 拉取/回传
                            │ 不开放 PC 公网入口
┌───────────────────────────┴──────────────────────────────┐
│ 本地 Native Channel Node                                 │
│ 绑定配置、总槽数 1、持久 journal、恢复、原生适配器         │
│            │ 获准的本地控制入口                          │
│            v                                             │
│ WorkBuddy / 其他实际运行的原生 Harness                    │
│ 原生登录态、真实模型选择、原生上下文、工具及审批           │
└──────────────────────────────────────────────────────────┘
```

“独立 Bridge 实例”是复用现有代码，使用新数据目录和本项目服务凭证，不是另造调度系统。首版不占用现有 Alchemy 生产任务槽，不复用其生产账本，不把聊天结果塞进图片 artifact 字段。[S2]

| 组件 | 负责 | 禁止承担 |
| --- | --- | --- |
| Sub2API | 用户、Key、模型渠道、业务操作路由、最终收费 | 厂商登录态、原生执行、不确定后换模型重试 |
| Facade | 外部协议、明确的能力校验、观察和结果包装 | 自建执行队列、第二个钱包、模型直连 SDK |
| Bridge | 唯一任务权威、启动许可、执行事实、结果留存 | 重复建立用户计费系统、代替 Harness 思考 |
| Node | 本地执行槽、持久 journal、原生生命周期、结果回传 | 持有用户 Key、失联后自己新建同意图任务 |
| Adapter | 验证模型选择、提交/观察/取消真实原生执行 | 拿 token 直接请求模型、伪造完成和 usage |

配置登记不需要单独一个 Registry 服务。首版使用版本化配置文件和少量管理记录，由 Facade/Node 校验版本哈希即可。Sub2API 的 operation 路由记录是“这次业务请求归哪个上游”的绑定，不是第二份执行队列；Bridge 仍独占任务调度事实。

## 5 把模型绑定作为一级对象

### 5.1 四个概念不能混成一个 model 字符串

1. **Harness**：运行软件，例如 WorkBuddy。描述执行环境，不代表底层模型身份。
2. **ModelBinding**：如何把某个已验证原生模型，固定选择到某个执行配置中。
3. **CapabilityProfile**：该绑定实际能够兑现的 API 字段、输入输出、流式、工具、usage 和权限范围。
4. **Channel**：Sub2API 管理的上游路由/运营单元，指向 Facade 和已启用绑定。

多个模型可共用一个 Facade、一个 Node 和一套适配器，但不能因此共享一个不加锁的全局模型选择器。一个模型对应一个可管理渠道，不意味着每个模型都要写一份 provider 驱动或起一个服务进程。若 Sub2API 现有账户能可靠承载多个已冻结映射，就复用；确需独立限流、定价或开关时再拆渠道。

### 5.2 建议的绑定记录

下列是**设计结构，不是当前 Sub2API 可导入配置**。尖括号必须用真实核验证据替换；未填完不得发布：

```yaml
binding_id: wb-verified-model-chat-r1
public_model_alias: "wb-<verified-model-id>-chat"
harness:
  kind: workbuddy
  version: "<tested-version>"
  adapter_revision: "<commit-or-build-id>"
native_model:
  catalog_id: "<id-returned-by-supported-native-interface>"
  display_name: "<native-catalog-display-name>"
  selection_scope: session        # 或 process；不明则不能固定模型发布
  selection_method: "<verified-method>"
  verification_method: "<verified-readback-or-run-metadata>"
  implicit_fallback: prohibited
capability_profile: chat-basic-r1
execution_profile: isolated-chat-r1
revision: 1
publication_state: disabled       # 验收后才启用
```

每次任务冻结至少：客户端请求别名、别名版本、binding revision、Harness/Adapter 版本、native requested model、实际回读的 selected model、可用时的 run-reported model、能力版本、权限配置、价格版本及证据引用。它们不都对最终用户公开，但必须能审计。

### 5.3 模型身份的证据门槛

| 证据 | 能证明什么 | 不能证明什么 |
| --- | --- | --- |
| 模型目录/官方说明 | 有该选择项及其厂商声明 | 当前任务实际选择了它 |
| 设置成功后原生回读 | 原生会话/进程确认选中该 ID | 厂商后台实际权重的密码学证明 |
| 每次运行的原生 metadata | 本次运行自报的模型身份 | 所有内部子任务都用了相同模型，除非 metadata 明确覆盖 |
| GUI 标签与截图 | 人在界面看到当前选项 | 接口参数被采纳、运行中未自动改动 |
| 让 AI 自报“你是什么模型” | 仅是生成文本 | 不是模型身份凭据 |

固定模型别名至少需要可靠的选择控制及回读/运行元数据证据，并测试多次运行、重启和竞争修改。若原生软件会静默 fallback、自动路由或子 Agent 换模型，必须禁用该行为并验证，或者只能明确发布为 `workbuddy-auto-agent` 这样的原生自动模式。它**不等于**用户要求的固定模型渠道，不能当作该目标已经完成。

不能证明模型固定时，不销售某个精确型号、不用 prompt 猜身份、不在 API 响应里填想要的模型名掩盖偏差。标准响应中的 `model` 可以是 404token 公开服务别名，但产品说明及内部证据必须让这个别名可解释、可核查。

### 5.4 全局模型设置的竞争问题

若只能切全局模型：首版整个进程和该设置归单槽独占。操作顺序为“锁定槽与原生配置 → 切换 → 回读 → 提交 → 运行中禁止外部切换 → 校验终态/模型记录”。不得让用户同时在同一 WorkBuddy 进程中交互切模型。

若无法阻止或检测外部修改，则该配置不能承诺固定模型。以后多槽优先独立原生进程/OS 隔离环境，不用“一模型一个 mutex”冒充全局设置隔离。所有模型共享单节点总槽数 1，不能每新增一个别名就悄悄多一个并发。

### 5.5 适配器最小接口

接口名是本项目设计，不要求不同厂商 SDK 使用相同名称：

```text
inspect_capabilities() -> 被动能力与版本证据
prepare(binding, request) -> 隔离环境与无推理准备
start(start_grant, prepared) -> 消费一次许可并提交原生执行
observe(native_run_ref) -> 原生增量、模型证据、审批和终态
reconcile(native_run_ref, journal) -> 只读找回既有执行与结果
cancel(native_run_ref) -> 请求取消并返回停止是否已经确认
```

会触发新一轮推理的 resume/continue 不能冒充 reconcile。准备或创建 session 若会调用模型，必须移入 start 之后。返回只读可用不等于允许自动接收新任务，仍以 binding/capability/槽和版本门禁为准。

## 6 API 能力分层而不是永久文本化

### 6.1 两种明确的服务语义

**模型 API 配置**：面向现有 SDK 的调用。只兑现通过测试的标准消息、模态和输出能力。无声明时不自主做有外部副作用的操作。可保留确实符合产品语义且已隔离的原生检索/计算能力，但必须说明其存在，不能把 Agent 工具循环包装成一轮裸模型调用而不告知。

**原生 Agent 配置**：调用方明确请求 Harness 原生执行能力，允许在固定工作区按受信权限配置使用工具和审批。其最终回答可以经 CC 返回，但该模式不承诺完整裸模型 API 语义。使用单独别名或明确配置区别，不能默默替换模型 API 渠道。

这两种配置共用账本、Node 和适配器，不另建 Agent 平台。某个 Harness 如果只有第二种能力，就如实发布第二种或保持未发布，不能靠改名宣称第一种已完成。

### 6.2 每个绑定独立出具能力清单

能力状态至少区分 `verified_native`、`verified_translation`、`unsupported`、`unverified`。翻译层的限制必须描述；未验证按不可用处理。不能用一个 `supports_tools=true` 覆盖所有工具语义。

| 能力 | 首版建议 | 开启门槛 |
| --- | --- | --- |
| 文本输入与最终回答 | 开启 | 原生输入、真实终态、非空有效结果 |
| user/assistant 历史 | 有限开启 | 保序映射、无上下文泄漏；注明原生角色等价性 |
| system/developer | 条件开启 | 真正高优先级入口；不能降为普通 prompt 后宣称等价 |
| 实时文本 SSE | 后置 | 有原生增量及正确中断/终态，非事后逐字拆分 |
| 原生工具 | 按 profile | 受信白名单、原生审批、隔离和副作用说明 |
| 客户端 tools/tool_calls | 默认关闭 | 原生可暂停、将调用交给客户端、接回结果并继续同一次交互 |
| JSON/schema 输出 | 条件开启 | 实际约束与验证；“提示它输出 JSON”不等于 strict schema |
| 图像/音频/文件输入 | 预留但不启用 | 原生可传输该模态、范围/格式/鉴权实测 |
| 文件/图像等输出 | 预留但不启用 | 类型化结果、归属、安全下载与保留策略 |
| temperature/top_p/seed | 默认不支持 | 原生提供对应控制且证明被采纳 |
| max tokens/context 限制 | 只声明已知限制 | 区分总任务预算、单轮 token 上限、原生上下文限额 |
| usage | unknown 或原生证据 | 明确单轮/整任务、累计/增量、缓存等计量口径 |
| 取消与恢复 | 独立声明 | 能确认停止/只读恢复；不能由“有 start”推断 |

`GET /v1/models` 只列启用且获准的模型别名，不把营销目录当在线能力表。详细能力表可由管理端、静态文档或受保护的扩展端点提供，不依赖普通 OpenAI 客户端识别自定义字段。

### 6.3 首版请求规则

首版默认只接受一个已启用模型、文本消息、`n=1`、`stream=false`。`user` 只能作为不可信标记，不作为身份。不支持的显式语义在接受新操作之前返回 400 并指出字段，不静默吞掉。

如果原生入口只接一个 prompt，允许确定性序列化历史作为**已声明的语义转换**，但不能宣称原生支持 Chat Completions 的完整角色层级。客户要求 strict system/developer/tool 语义而入口不能兑现时，拒绝该请求或不发布相应能力。

标准 HTTP 外形、角色语义、采样控制、真实流式、工具轮次和 token 口径分别验收。不要用“兼容 OpenAI”一个标签替代六份不同事实。

### 6.4 外部 HTTP 合同

首版提供受鉴权的 `POST /v1/chat/completions` 与 `GET /v1/models`。以下只表达调用形状，不是某个真实模型已经可用：

```http
POST /v1/chat/completions
Authorization: Bearer <404token-user-key>
Idempotency-Key: <one-stable-key-per-logical-operation>
Content-Type: application/json
```

```json
{"model":"<enabled-public-model-alias>","messages":[{"role":"user","content":"请回答这个测试问题。"}],"stream":false}
```

成功响应保持标准 CC `id/object/created/model/choices` 结构，只写真实原生输出；id、created、绑定和最终结果持久固定。只有已验证的 usage 才附带 usage。原生合法拒答如实返回；空正文仅在确有该 profile 支持的其他有效结果类型时合法，否则不能编造通用答案补成功。

| 情况 | 建议 HTTP 与业务码 | 后续动作 |
| --- | --- | --- |
| 未认证或越权 | 401/403 | 拒绝；不创建任务 |
| 不支持字段或能力 | 400 `UNSUPPORTED_PARAMETER` | 新执行前拒绝 |
| 同键不同输入 | 409 `IDEMPOTENCY_CONFLICT` | 保留原操作，不执行新输入 |
| 忙且未接收 | 429 `CAPACITY_EXHAUSTED` | 后续同键可再准入，不切其他厂商 |
| 离线且未接收 | 503 `NODE_UNAVAILABLE` | 后续同键可再准入，不云端回退 |
| 已接收，本次等待超时 | 504 `TASK_WAIT_TIMEOUT` | 只恢复原操作 |
| 执行/观察不明 | 504 `EXECUTION_UNCERTAIN` | 保持事实和槽，只协调原操作 |
| 等待本地审批 | 504 `APPROVAL_PENDING` | 原审批/同操作观察或取消 |
| 确认失败或无有效结果 | 502 `NATIVE_FAILED` / `NATIVE_EMPTY_RESULT` | 回放失败，不自动重跑 |
| 已确认取消 | 409 `TASK_CANCELLED` | 不重跑；另开操作需新键和明确意图 |
| 正文已过期 | 410 `RESULT_EXPIRED` | 不重跑 |

错误体采用标准 `error.message/type/code`。操作关联、是否已接收及是否可能执行通过受控响应头/记录传递，并由 Sub2API 保留需要给客户端的信息；不公开原生会话 token 或路径。具体内部头名在实现时固定。这些业务码是设计要求，不声称已存在。

HTTP 状态码本身不能决定是否重试。标准 CC 路径不返回自定义 202 然后伪装成正常 completion；长任务恢复按 §9.4 的同键示例或明确扩展合同进行。

## 7 工具调用和多模态的演进合同

### 7.1 原生工具与远端工具不能混用

原生 Harness 已调用 shell、浏览器、MCP 或文件工具，这些是**已发生的执行事件**。将它们转换成 `assistant.tool_calls` 发给客户端，会让客户端再次执行，属于错误。

只有以下闭环真实成立，才能对外开启客户端 function calling：

```text
客户端声明工具 schema
→ 原生运行选择工具但不在本地抢先执行
→ 返回稳定 call_id + name + 原始 arguments
→ 客户端执行并回传 tool result
→ 可信绑定同一租户/原生交互，校验 call_id
→ 原生继续，直至下一次工具调用或最终回答
```

这是“请求下一步推理”，不是只读恢复。每一轮续接都有独立的操作身份和启动许可，同时引用父交互；不能拿旧 operation 的幂等回放机制执行下一轮。重复工具结果不得引发重复续接。工具定义、schema、参数和结果都应有大小与权限检查，不能因此开放任意本地命令。

若当前入口仅支持原生内部工具，就支持原生内部工具，不造一层看起来像 function calling 的假协议。

### 7.2 内核类型化结果

建议新合同使用 `harness.invoke.v1` 或等价的通用版本化 envelope；最终名称在实施时固定。v0.1 尚无业务实现，不必为未实现的字段制造复杂迁移。

```text
request: identity + binding_revision + capability_revision + input_blocks
result: output_blocks + native_completion + model_evidence + usage_evidence
block.type: text | refusal | tool_call | asset_ref | structured
```

**首版只允许 text/refusal，其余全部关掉。** 这样数据模型可以扩展，首版执行路径仍然简单。若为最大复用先保留 `harness.text.v1`，它应是该通用 envelope 的受限子型，而不是把整个项目永远定成字符串服务。

未来添加 `asset_ref` 时，它指向本项目受鉴权的资产，不是 PC 路径或公开目录。单独验证 MIME/大小、租户归属、目录穿越、链接/挂载逃逸、上传下载期限和恶意内容风险。通用内核不等于所有外部协议都能无损承载；不能把二进制硬塞进 CC content。需要 Images、Responses 或其他协议时，按实际能力另加薄映射层。

### 7.3 流式合同

实时输出只转原生公开增量。段级就叫段级，不叫逐 token；完整结果生成后拆字发送不算实时。心跳用 SSE 注释，不算模型内容。

事件用 `(task_id, execution_id, sequence)` 去重，先持久再发送；相同序号不同内容进入冲突处理。最终 chunk 的结束原因必须来自真实终态。失败流不能补 `finish_reason=stop` 和成功 `[DONE]`。客户端断线不取消任务、不释放槽、不自动重跑。

相同 POST 回放可以重放整个已存储输出，但不能声称标准 CC POST 默认支持 Last-Event-ID 增量续传。若后续确需游标恢复，增加明确、可鉴权的扩展读取合同，不要求首版建设事件平台。

## 8 请求身份必须先于渠道选择冻结

这是 v0.2 对 v0.1 最重要的可靠性补充之一。

如果只在 Bridge 按键去重，但 Sub2API 在重试前改变了别名映射、上游账户、服务 Key 所属 source 或 Facade 实例，同一用户操作可能到达另一份账本，旧幂等记录根本看不到。因此操作必须在**模型重映射和新上游选择之前**得到稳定绑定。

### 8.1 最小操作绑定

Sub2API 持久记录：

```text
operation_id
owner_scope + client_idempotency_key
client_request_digest
chosen_native_channel + stable_source_id + bridge_instance_id
frozen_binding_revision
billing_owner + pricing_revision
accepted_or_unknown_receipt
```

它不派发本地任务、不拥有运行状态机，只把同一业务请求固定到同一执行权威，并把结算归到同一操作。用数据库 `UNIQUE(owner_scope, client_idempotency_key)` 与事务/CAS 决定唯一绑定胜者；并发输家读取同一 operation/渠道，不能各生成 operation 后指望 Bridge 猜出二者相同。必须先持久此绑定，再向 Facade 提交。内部请求 ID 用于一次 HTTP trace，不替代跨重试 operation ID。

Bridge 的唯一性范围仍包含可信 source/租户/主体与 operation。source 是逻辑来源，不随服务 Key 轮换改变。替换凭证不等于替换来源身份。

### 8.2 身份范围和 Key 轮换

用户、应用主体、API Key 是三层不同对象。建议幂等归属于稳定应用主体及租户；API Key 仅是该主体的可轮换认证凭证。若当前 Sub2API 不能表达稳定应用主体，PoC 可以明确将去重范围限定为同一 API Key，但**不能宣称换 Key 后仍自动去重**。

若要跨 Key 恢复，由服务端确认新 Key 仍属于相同授权主体，查回原 operation；业务结算仍锚定原操作的账务归属，不因为新 Key ID 不同再扣一笔。不得让客户端任填 principal_id 来扩大读取权限。

### 8.3 处理顺序

```text
当前认证和结果读取权限
→ 可信主体 + 客户端请求摘要
→ 查找已绑定操作
   存在且摘要相同：访问原渠道、原绑定、原任务
   存在且摘要不同：409，不执行
   存在且结果过期：410，不执行
→ 仅对全新操作检查当前模型权限、能力、容量和额度
→ 冻结操作路由
→ Bridge 原子幂等创建与准入
```

去重前使用客户端层的规范化摘要，不能先用新的模型映射改写输入，再把旧请求误判为冲突。有效执行配置也保存摘要，用于证明首次接收时究竟执行了什么。客户端业务摘要不包含每次动态生成的 trace ID、等待 deadline、价格时间戳等服务端元数据，否则合法重试会误报冲突；真正影响语义的用户输入和参数仍须纳入。

即使冻结渠道后首次提交收到明确未接收的 429/503，同一键仍保留原路由绑定；后续只向原绑定重新尝试准入，不能因“还没开始”悄悄换渠道。若原绑定长期失效，明确报不可用；改用新渠道属于另一个明确的新操作。 **已绑定但 Bridge 明确确认未接收**的重试仍是新执行准入：每次重新检查当前执行授权、冻结能力配置是否仍被安全允许、容量与额度，不能因已有绑定绕过权限撤销或余额变化。真正已接收操作的只读观察/回放才不重复做新执行预留。接收结果不明时先向原账本查询/幂等协调，不能擅自视为未接收。

回放仍做当前鉴权、归属及合理读取限流，但不重新消耗执行槽或预留新任务额度。关闭新任务、PC 离线、价格变化或余额不足，不能挡住本已获准读取的旧结果。

### 8.4 无键调用的真实保证

用户不提供幂等键时，服务端生成的 operation 只保护该 HTTP 请求内部。响应丢失后 SDK 再发一个无键 POST，不能可靠判断它是否同一操作。上线文档和示例必须说明这一点。

首版受信 SDK 示例自动生成并持久复用一个逻辑操作键，同时关闭会新建操作的默认重试；不能只写一句“支持幂等”就宣称所有 OpenAI 客户端天然安全。文本相同也可能是用户有意第二次调用，不用 prompt hash 去猜用户意图。

## 9 复用 Bridge 的执行与恢复合同

继续采用 VPS 唯一账本、PC 主动拉取、claim/start 许可、Node journal 和结果重传。不得把 Facade 内存 semaphore 当权威容量控制，不另叠 HAPI/Hatchet 队列。[S2]

### 9.1 主流程

1. Bridge 在同一事务内确认唯一 operation、准入、绑定和容量，持久化后接受。
2. Node 领取只读 offer，原子 claim；首版所有模型总槽数为 1。
3. Bridge 持久保存启动许可和 `execution_possible=true` 后才返回。
4. Node 持久写 `launch_intent`，用本机互斥/CAS 保证许可只消费一次，再触发原生执行。
5. Node 持久记录原生会话、模型回读、输出与事件，网络恢复只重传这些证据。
6. Bridge 原子提交结果和终态，Facade 以固定 id/created/model 返回或回放。
7. Sub2API 以同 operation 结算。HTTP 是否还连着不改变原生执行事实。

prepare、capabilities、模型目录和 readiness 检查不能偷偷触发模型调用。创建原生 session 若本身触发生成，也属于 start 许可之后的执行阶段。

### 9.2 结果状态与执行活性分离

| 轴 | 建议取值 | 用途 |
| --- | --- | --- |
| 业务结果 | pending / succeeded / failed / cancelled / expired | 用户此次操作交付什么 |
| 执行活性 | not_started / may_be_running / running / waiting_approval / stopped_confirmed / unknown | 是否能安全释放执行槽 |

适配器异常、网络失联或 wrapper 退出首先属于 `observation_error`，本次 HTTP 可以返回错误，但操作结果应保持 pending/协调中，不能因暂时观察不到原生结果就写不可变 FAILED。只有确认原生失败或最终无法交付有效结果时，才按合同提交失败终态；即便结果确已失败，也不代表原生程序/子进程已停止。只有证明未启动或原生/子任务已停止，才能释放槽；未知活性隔离该槽并协调。这样不会把稍后找回的真实成功结果误当作与过早失败矛盾的回执。

业务终态不可改写，但执行活性可以随后补充 `stopped_confirmed` 等更强证据，不把合法停机确认误当终态覆盖。最终回答已输出也不单独证明原生子进程停止；结果提交与释放槽分别满足各自条件。

旧许可、旧 epoch 的回执不得修改当前事实。完全相同的终态可幂等重复提交；矛盾终态必须拒绝并留痕。完成与取消竞态由账本原子裁决，取消不回滚已发生的文件修改或外部操作。

### 9.3 不可消除的启动窗口

`launch_intent` 落盘后、spawn 确认前崩溃时，程序可能没启动，也可能已启动。若无法只读找回原生会话，不能再次 spawn。进入协调/隔离状态，由运维确认真实情况。

这里接受“某次操作不可自动完成”的可用性损失，避免重复工具副作用。不宣传外部工具 exactly-once。证明没开始的 claim 才能撤销并重领同任务；单纯心跳超时或租约过期不是证明。

另一个必须覆盖的窗口是 **late spawn**：旧 Node 已拿到 grant 并写 intent，但暂停在 spawn 前。运维此时没看到原生进程，释放槽后旧 Node 恢复，仍可能启动并产生副作用。服务端拒绝旧 epoch 的回执只能保护账本，不能阻止这个本机动作。

因此“停止已确认”必须同时证明：原生执行已停止，且**旧启动许可的所有本地消费者再无能力启动**。首版没有这种证据时就保持槽隔离，不提供强制释放/原操作重跑按钮。需要恢复时，在本地持久撤销旧 grant/执行代际，通过同一启动临界区或受控 supervisor 围栏并终止旧 Node/worker 及子进程，确认其静默后才报告可释放。启动路径必须校验本地持久撤销/代际，并与撤销操作互斥；单独做一次“spawn 前查 epoch”仍有查后被撤销的竞争窗口，不能替代消费者围栏和停机证明。重启后也不能消费已撤销许可。

如果旧机器失联到无法完成上述围栏，就不能把该机器原槽标成空闲或把原任务迁到别的节点重跑。可停掉原槽并为**新的独立操作**准备另一隔离环境，但不能抹掉原任务的执行不确定性。

### 9.4 超时与取消

- HTTP 等待时限：停止本次等待，操作仍可能运行。返回稳定 operation 关联及 `TASK_WAIT_TIMEOUT`，只允许恢复同操作。
- 原生执行预算：到期请求取消，停止确认前不释放槽。
- 排队到期：仅在尚未发启动许可时可原子过期。
- 父进程退出：不是所有子进程、远端异步任务和副作用均停止的证明。
- 未知状态：运维只能查看、请求取消、隔离或在有证据后完成协调；不能点击“恢复”就重新执行 prompt。

首版可以不提供用户异步任务平台。同键 POST 的等待/回放足够跑通基本闭环；管理侧必须能查看未决操作、确认停机并处理事件。若普通客户端的时限普遍短于实际任务，后续再提供经 404token 鉴权的最小 status/result/cancel 扩展，不让用户绕过 Sub2API。

### 9.5 留存和备份

要明确去重保证窗口。结果正文可短期保留，精简 operation 墓碑应更长；只有有限墓碑，就只能承诺有限去重期。推荐首个运营版本保留不含正文的身份墓碑，不在仍支持恢复的窗口内复用相同键。

若未来清理墓碑，应采用带服务端可验证有效期的键/明确命名空间退役等机制，对过期身份拒绝，不用“记录找不到”当作安全新执行。未决任务及启动证据不得按普通结果 TTL 删除。

从旧快照恢复、账本丢失或损坏时，先停新准入、核对所有 Node journal 和启动 epoch。不能恢复出一份“从未执行过”的旧账本后继续派发。换实例和升级保留原任务的归属与受限收尾入口，不把未决任务复制到新实例重跑。

同一门槛适用于 **Sub2API operation 路由绑定表**。若只恢复了旧网关数据库，Bridge 可能仍保留实际执行，但路由表查不到该键，新的路由选择就可能把它送到另一 Bridge/source。恢复、丢失或回滚该表时必须 fail closed，冻结新准入并与历史 Bridge/Node 身份协调；不能把“恢复后的表中不存在”当全新操作。绑定记录的备份、留存和代际必须覆盖任务去重保证期；删除渠道不能删除旧操作的恢复映射。

## 10 Sub2API 最小接缝

本项目应该使用 Sub2API 已有 OpenAI API-key 上游和自定义 URL 能力，增加**仅作用于 Native Harness 渠道的小开关/能力策略**，不重写网关。

本轮源码审阅发现，当前 Chat 入站对于普通 OpenAI API-key 账户已默认走原始 Chat Completions 路径，显式 `force_responses` 等分支例外。因此不应继续依据旧注释，要求先做会触发模型的 Responses 能力探测。仍必须用 Mock Facade 捕获真实 outbound URL、headers、body 和全部失败路径。[S3]

| 接缝 | 最小要求 |
| --- | --- |
| 路由 | 固定 CC 或已验收协议，不自动探测/转换为不同语义的推理调用 |
| 内部身份 | 去除客户端伪造的内部头，用认证上下文重建 |
| 操作绑定 | 先查稳定 operation，再选账户/改模型；绑定原账本 |
| 重试 | 本渠道禁用会创建新执行的同账号重试、跨账号 failover、换模型、空响应重生 |
| 同操作重传 | 只允许同身份向同账本查询/幂等提交；不得改成新 operation |
| usage | unknown 不当 0、不触发“没有输出所以安全重试”的推断 |
| 结算 | 采用受信 operation billing ID；回放不重复收费 |
| 响应 | 支持受控操作关联、真实错误和合法拒答；不泄漏原生 session/本机路径 |

**特别注意**：现有 raw streaming 代码存在“无下游输出且 usage 为零”可安全重试的判断。原生 Harness 即使尚未输出，也可能已经执行工具或消耗模型调用。这条推断不适用于本渠道，必须专门隔离。silent refusal、transport error 也有共享 failover 分支，不能只拦 5xx。[S4]

内部身份注入必须晚于或禁止覆盖 account header overrides，否则账户级自定义头可能覆盖服务端生成的可信身份。不能用静态账户头代替每个用户的动态身份。现有转发前余额检查和预留也要区分新执行与旧结果读取，否则 Facade 已做到 replay-first，用户仍可能因余额耗尽拿不到旧结果。[S3]

同理，仅由 Facade 返回相同 `x-request-id` 并不足以保证现有 Sub2API 不重扣。现有计费请求 ID 选择会优先客户端/本地上下文，特定稳定 ID 优先路径尚无 native 类型。可以复用现有原子去重结算能力，但要把受信 operation 作为此渠道的结算身份，并解决 API Key 轮换的归属范围。[S5]

配置页面只展示实际实现的字段。本稿给出的是语义要求，不是保证存在 `native_harness=true`、`disable_retry=true` 等现成 JSON 参数。

## 11 身份隔离和运行权限

### 11.1 凭证分工

| 凭证 | 所在位置 | 权限边界 |
| --- | --- | --- |
| 404token 用户 Key | 最终用户及 Sub2API | 用户可用模型和结果归属 |
| Facade 服务凭证 | Sub2API | 调用许可来源及模型，不交给用户 |
| Bridge Source 凭证 | Facade | 创建/观察该逻辑来源任务 |
| Node 凭证 | 指定 PC | 认领允许 profile，上传已绑定任务结果 |
| 原生登录态/获准控制凭证 | 本地获准运行环境 | 原生软件或官方控制流程；不抽取成模型上游 Key |

缺省拒绝认证；`/v1/models` 也鉴权。内部 source/tenant/principal 头只在受信服务通道生效。Node Key 还必须受 claim/grant/task 归属约束，不能凭“这是一台合法电脑”就允许覆写任意任务。

### 11.2 两个阶段两种安全范围

**可信 PoC**：专用低权限环境、测试目录、单可信操作者、无私密材料。每请求新 session/cwd，关闭共享记忆等污染源。不以此声称多租户安全。

**对外服务**：隔离范围必须覆盖 OS 身份/进程、原生历史与记忆、缓存、MCP/插件、网络、桌面焦点、文件系统和凭据。独立目录、串行运行或一段安全提示都不够。可选专用 VM/独立系统用户或厂商支持的等效隔离，但必须证明原生软件及登录方式在该方案中受支持。

不默认禁用所有原生工具，也不默认 fullAccess。工具权限属于已审阅的服务端配置；不可信 prompt 不能改变可执行文件、工作根、环境变量、插件、模型或审批策略。原生工具能读出登录态或任意宿主秘密而没有有效隔离时，不向不可信用户开放该配置。

凭证轮换和撤销需区分。泄露的旧凭证立即失效；保留“收尾能力”不等于继续接受已泄露 Key，而是由新受信凭证按同一逻辑来源/主体收尾。

模型 API 配置默认不做有外部副作用的动作；原生 Agent 配置明确告知并按授权开放。不能利用“用户发了 prompt”默认允许发送邮件、购买、任意外传文件等动作。

### 11.3 数据与日志

日志以 operation/task、版本、模型证据摘要、时延和错误码为主；不记录完整认证头、Cookie、环境变量或私密推理。用户正文/输出是否留存、多久留存，在 404token 服务说明中明确。

返回结果本身也可能包含敏感内容，需租户归属校验；调试接口不向客户开放。新文件能力默认禁止任意 URL 抓取和本机绝对路径，等专门验证后再增加。

## 12 用量 价格和运营

### 12.1 三种数值分开

1. 原生报告用量：记录来源、单轮/整任务、增量/累计、缓存及子 Agent 范围。
2. 404token 计费量：依据对客户公开的计费政策计算，不伪装成厂商原始 token。
3. 估算：仅用于容量/成本参考，明确估算，不写进标准 usage 冒充精确值。

unknown 不是 0。最终文本长度不能证明整任务 token；多个累计 usage 事件不能直接求和。原生拒答、工具失败和客户端断线也可能有实际成本，收费规则必须事先明确。

### 12.2 首版和商业版

首版不收费，使用隔离测试账户/明确零收费渠道；不影响正常渠道价格。无可信 usage 时不为通过兼容性测试填一组假 token。

商业版可以选择：

- 有可信整任务原生用量时，按公开口径计费，并说明不等同于一次裸模型调用。
- 无可信 token 时，按任务等服务单位计价，但需 404token 实际支持，并在客户同意的政策中说明成功、失败、取消和超时后完成如何处理。
- 暂不收费，直到计量链路可验证。

不要通过“估算成 token”偷渡另一种收费，也不借本项目扩建独立钱包。

### 12.3 结算闭环

每个 operation 冻结价格版本与账务主体。Bridge 提供不可变完成事实/计量记录；Sub2API 以受信 billing operation ID 幂等结算。

若原生入口没有硬性调用数/token/成本上限，不承诺精确最大原生成本。按任务服务价意味着运营方承担成本波动，应设置执行时长与可用原生预算控制，但不把软取消超时说成硬成本上限。模型身份不匹配、结果损坏、拒答、失败和取消的收费/退款规则也要在上线前明确，不能靠临时判断。

客户端超时后仍要有最小后台协调：读取该渠道尚未结算的已知 operation/完成记录并应用现有账务事务，失败可重试，同一操作至多一笔最终结算。可采用轮询游标/可重放完成记录，不需要再建大型消息平台。客户端重新 GET/POST 回放仅是读取，不是新消费。

客户额度预留、完成结算、确认失败后的释放与账务冲突都在 Sub2API。不能让 Facade/Bridge直接扣余额，不能在结果已过期且没有可信用量时补造一笔账。

### 12.4 运营最低配置

首版只需要：每绑定开关、节点 online/busy/approval/reconciling 状态、一个总槽、请求大小、结果大小、等待和执行预算、结果留存、原生版本固定、故障日志和停新任务开关。

后续再增加容量池。单节点吞吐由实际平均占槽时长决定；理论上限约 `3600 / 平均任务秒数` 次/小时，还需为审批、故障和波动留余量。不能按厂商裸模型的 tokens/s 宣传本地 Harness 通道吞吐。

不自动按需拉起更多登录账号，不用多别名规避同账号限制。渠道下线时停新准入，继续允许原操作收尾、结果回放和结算。

## 13 WorkBuddy 入口与授权决策

优先级是：**官方受支持本地入口 → 经厂商确认的控制接口 → 经明确许可且版本固定的其他本地适配 → GUI 仅作最后候选**。

本次读取官方 WorkBuddy Open API，确认文档将“本地助理 API”和“云端任务 API”分开。已读本地助理请求表以 `content/msg_type` 为主，提供 PC 状态、发送消息、历史与审批回复，**尚未从这份合同证明每请求选模型、独立会话、取消、幂等与可靠运行终态**。它是优先候选，不是已经满足本项目的证明；云端 tasks/ACP 不能作为 PC 离线回退。[S6]

官方 CodeBuddy Code HTTP API Beta 则明确列出 runs 创建/查询/流/取消，以及 sessions/stats 等接口，因此有更好的生命周期适配线索。但它属于 CodeBuddy Code，不能由此认定用户已登录的 WorkBuddy 桌面运行时也有同样入口或许可。其进程、环境、settings 等管理能力绝不对用户透出；文档中的尾部 transcript/stream 不可代替完整持久结果仓库。[S7]

官方 WorkBuddy 模型说明展示内置/自定义模型及受版本/管理员约束的配置。CodeBuddy 环境变量文档还区分主模型、轻量模型与子代理模型，说明“选中主模型”不能自动证明整个 Agent 只用了该模型。此行为在 WorkBuddy 的具体对应关系仍待独立实测。[S9]

官方控制 API 如果需要独立凭证，只能按官方方式取得并授权使用，不能从原生进程偷取 token。控制面经过厂商服务不自动违反本地执行边界，但必须证明**实际任务是在指定本机 Harness 执行**，且凭证权限、数据流向和计费符合预期；VPS 不因此获得直接推理后门。优先将必要控制调用保留在本地 Adapter。

社区 ACP 项目可用于理解协议/发现风险，不能把非公开字段、内部路径、自动批准设置直接当产品合同。本次读取的 Desktop Bridge `configure_session` 会设置 model，却没有确认返回 `currentValue` 与请求一致；其默认 fullAccess/allow_always 不可照搬。另一项目说明内置 Gateway 并非承诺稳定的公开接口，并采用默认 auto/全局会话。这些都是需改造或排除的证据，不是现成可发布适配器。[S8]

ACP 的配置协议提供候选值和 `currentValue` 回执机制，可作为选择后回读设计的依据。ACP 的 tool call 更新描述 Agent 的工具执行进度，不等于 OpenAI 的客户端待执行函数调用。两者均须在具体版本真实映射，不能仅因术语相同而直接转发。[S10]

### 13.1 不调用模型也能先核验的事项

- 用户安装的产品名、版本、官方支持 OS 和启动方式。
- 官方文档是否明确本地执行、账号权限、可用 API 与生命周期。
- 原生模型目录/配置回读是否不触发推理。
- 固定模型入口、session 模型设置及 metadata 是否存在。
- 会话历史/任务状态是否可只读取得，不会自动 resume 发起新推理。
- 审批、取消、输出和 usage 的接口定义。
- 服务条款、订阅/席位、自动化、共享/转售、API 商用限制。

真实 PC、真实模型、取消及恢复演示属于后续获准测试；不能用只读文档核验代替。

### 13.2 合规是独立门槛

真实电脑和官方客户端有助于保留原生执行路径，但不自行构成“更合规”的结论。至少分别确认软件许可、服务条款、账号/席位范围、自动化、给第三方提供服务、转售、数据处理和地域限制。

已读的 WorkBuddy 企业订购协议把权利范围与订单/产品文件关联，企业微信联合订阅协议还包含未经许可向组织外商业提供的限制。这些协议未被证明适用于用户实际套餐，因此不能据此认定用户一定违规；它们也足以说明“官方客户端”本身不构成全面转供授权。[S11]

本稿不作“已获商业转供许可”的法律结论，也不把没有找到禁令当作许可。若条款不清，正式对外收费前取得适用授权或选择允许该商业模式的原生通道。不开规避路径，不把 API/企业账号的许可套给个人订阅。

## 14 分阶段实施和明确停止点

| 阶段 | 实施内容 | 退出条件 | 暂不做 |
| --- | --- | --- | --- |
| P0 证据门 | 固定三仓版本；核验 WorkBuddy 官方入口、模型选择、协议和授权线索 | 明确首个可行入口与绑定，或明确阻断原因；能力矩阵填实 | 不推理、不部署，不先建平台 |
| P1a 离线首版原型 | 单模型 FakeHarness、Facade/Bridge 客户端、测试假网关/假账本、本地 journal 和合同测试 | 云端可运行和测试；明确真实三仓接缝尚未完成；禁止生产模式误启 | 不把 fake 网关称为 Sub2API，不把测试账本称为已复用 Bridge |
| P1b 三仓合同闭环 | 真实 Bridge 最小扩展、真实 Sub2API 专用接缝，执行器仍用 FakeHarness | 用户 Key 经真实 Sub2API 和真实 Bridge 到 fake node，关键故障测试通过 | 不启用真实账号或收费 |
| P2 原生单槽 | 一个获准 Harness、固定一个模型、真实 Windows/目标 OS | 模型选择、真实结果、工具策略、取消与恢复有证据 | 不借别的 Harness 结果宣称 WorkBuddy 已完成 |
| P3 404token 可信内测 | 真实 Sub2API Key、一个渠道、非流式、稳定操作恢复 | 单模型完整链路、失联无重复启动、离线可回放 | 不公开、不收费，不扩展所有模型 |
| P4 按需求扩能力 | 优先真实流式，再按已证实能力开 tool_calls/模态 | 每一项独立验收和更新能力表 | 不要求一次补齐所有能力 |
| P5 对外运营 | 服务授权、租户隔离、结算对账、留存和支持流程 | 对应生产门槛全部通过且获发布授权 | 不将内测成功当生产上线 |

**本轮可落地的首版代码应明确标记 P1a 离线原型；可用真实模型渠道的最小标准则是 P1b 到 P3，不是把 P4/P5 全做完。** P0 若发现 WorkBuddy 不能固定模型，应停在这个产品阻断点，提出“等待受支持接口、改为明确自动原生模式、或选另一个获准 Harness”的决策，不扩散做无关安全或平台工程。

## 15 可直接交给开发者的工作包

### 工作包 A 原生入口与绑定证明

交付 `adapter-evidence.md` 和一份禁用状态 capability manifest。列出支持的产品/版本、实际接口、固定模型的方法、读回证据、工具/审批/取消/usage 缺口。先只读，真实推理另获授权。该包决定 WorkBuddy 分支能否进入 P2。

### 工作包 B 合同和 FakeHarness

在 `native-harness-api/code/` 实现薄 Facade、类型化合同、FakeHarness、本地 journal 和针对性故障测试。不重写生产 Bridge 账本；离线假网关/假账本只置于 `code/tests/fakes/` 等显式测试目录，不能静默成为生产 fallback，生产路径只使用真实 Bridge HTTP 合同。P1a 可验证本项目协议与恢复逻辑，但不能宣称真实 Bridge/Sub2API 已接入。FakeHarness 应能制造延迟、断线、部分输出、审批、异常退出、模型错配、未知 usage 和持久副作用计数。

### 工作包 C Bridge 兼容扩展

在 Bridge 独立 PR 增加通用 Harness 任务/结果类型、冻结绑定、可信归属、执行活性、结果读取和完成事实。优先复用现有 claim/start/commit/fencing。原 image/video 合同不变，独立数据目录，默认关闭新能力。

### 工作包 D Sub2API 专用接缝

独立 PR 增加仅对本渠道生效的操作路由绑定、内部身份、禁止新执行型重试、未知 usage 和幂等结算适配。用 Mock Facade 捕获实际流量，验证普通渠道回归不变。先不收费，计费接缝可在商业化前完成，但不得宣称已具备收费能力。

### 工作包 E 真实 Adapter 与本地程序

首版只实现一个原生入口和一个绑定；本地程序至少支持配置、连接状态、单槽执行、原生版本检查、恢复和可读错误。可先命令行/托盘最小形式，不先做复杂桌面 UI。工具权限与模型设置不能由客户端自由输入。

### 工作包 F 交付和运营门

提交真实链路证据、已测/未测清单、配置样例、客户端幂等示例、停新任务/恢复操作手册。对外发布前才完成相应授权、隔离和收费门。本轮允许云端开发并上传 GitHub；仍不隐含合并其他仓库 PR、部署或真实付费模型调用。上传结果由实施方核对远程提交并单独报告。

## 16 验收矩阵

以下均为**待实现/待实测**，不是本次通过记录。可以沿用 v0.1 的 T01–T36 作为底表，新增下列关键覆盖；测试结果以可重复的计数/状态断言为主，不只看截图或 API 200。

| 编号 | 测试 | 必须观察到 |
| --- | --- | --- |
| V01 | 首版一个真实模型绑定运行；扩第二模型时再做双模型交叉测试 | 原生选择、回读、可用 run metadata 与所请求绑定一致；不是只改返回 model 字符串 |
| V02 | 模型不可用/静默 fallback/全局设置被改 | 拒绝准入或明确模型错配，不当固定模型成功收费 |
| V03 | 模型/profile/渠道/服务 Key 在同键重试前改变 | 仍定位原操作、原账本、原绑定；原生启动数不增 |
| V04 | 伪造 tenant/principal/operation 头、跨租户同键 | 网关剥离重建；跨租户不可读不可覆盖 |
| V05 | 100 个并发同键 POST | 一个操作、一个任务、最多一次原生启动、最多一次结算 |
| V06 | 同键输入不同与 JSON 键顺序变化 | 前者 409；后者按规范化合同回放；消息顺序不可忽略 |
| V07 | Bridge 接收应答丢失、Facade 重启 | 同身份找回原任务，不建立第二任务 |
| V08 | claim 前后、grant 落盘、launch_intent、spawn 确认各崩溃点 | 未授权才可安全重领；可能执行后只协调原任务 |
| V09 | 适配器失联但原生子进程仍运行 | 记录观察错误、结果保持待协调，槽仍隔离；不误终态失败或启动下一任务 |
| V10 | HTTP 超时、携带稳定键的 SDK 429/5xx 重试、无输出且零/未知 usage | 同一已绑定操作不触发新执行或跨渠道推理；无键新 POST 不在跨请求去重保证内 |
| V11 | 结果上传/ACK 丢失及重复事件 | 只重传；序号冲突拒绝；输出不截断后伪成功 |
| V12 | PC 离线，分别回放旧键和提交新键 | 旧结果可读；新请求明确不可用；VPS 不访问厂商模型 |
| V13 | 排队取消与 start、运行取消与完成竞态 | 原子裁决；停止未确认不释放槽，终态不可互相覆盖 |
| V14 | result TTL、墓碑策略、备份回滚、Node journal 不一致 | 过期不重跑；账本旧快照恢复先停准入 |
| V15 | 历史角色、system、采样参数、strict schema 不支持 | 在新执行前明确拒绝，不静默吞参数 |
| V16 | 原生工具已执行 | 只记过程，不发成让客户端再执行的 tool_calls |
| V17 | 真实远端 tools 多轮及重复 tool result | 仅启用该能力时测试；call_id/父交互归属正确，续接不重复 |
| V18 | 真流式、部分输出后失败、断开、磁盘满 | 只转真实增量；错误不伪造成功 DONE；不丢正文后正常结束 |
| V19 | 原生拒答、空正文、只有有效非文本结果 | 按该 profile 的结果类型判断；不补虚假文本，不错判所有空文本失败 |
| V20 | 原生 usage 累计/增量、多子任务、unknown | 不重复求和；不把末次调用当全任务；unknown 不写 0 |
| V21 | 用户超时后完成，回放多次，结算请求重复 | 事实最终对账，最多一次收费；结果读取不新增账务 |
| V22 | 同主体 Key 轮换/不同主体相同键 | 按已声明范围恢复或拒绝；不越权、不重复收费 |
| V23 | 跨工作区/记忆/插件/凭据/桌面污染 | 对外环境无法跨租户读取或导出；独立 cwd 不算通过 |
| V24 | 新模型别名并发、原生升级、停新任务 | 总槽数不突破；旧任务仍可收尾；未验版本不自动放行 |
| V25 | 原有 Sub2API 与 Bridge 业务回归 | 非本渠道的路由、重试、计费和 image/video 行为不变 |
| V26 | 已授权 worker 暂停在 spawn 前，撤销/运维恢复后再放行旧 worker | 不得仅凭无进程释放槽；旧消费者被持久撤销且围栏后不能 late-spawn，或槽持续隔离 |
| V27 | 仅回滚网关 operation 表，或删渠道/换 Bridge 后同键重试 | 停新准入并协调旧绑定，不能因记录缺失向另一账本新执行 |

每项保存：三个仓库提交、原生版本、脱敏配置、操作/任务/许可身份、模型选择证据、时间线、原生启动计数、结果摘要和账务计数。不要求模型自己编造哈希、证明身份或输出测试所需系统事实。

## 17 发布门槛按用途适用

| 门槛 | 可信真实 PoC | 不可信用户开放 | 收费 |
| --- | --- | --- | --- |
| 原生入口、固定模型与真实终态 | 必须 | 必须 | 必须 |
| 同操作不重复启动与恢复 | 必须 | 必须 | 必须 |
| 适用使用授权 | 必须 | 多用户范围明确 | 商业供给范围明确 |
| 专用环境与权限范围 | 必须 | 强租户与凭据隔离 | 同公开门槛 |
| Sub2API 真实端到端 | P3 必须 | 必须 | 必须 |
| usage/价格/幂等对账 | 不收费时可关闭 | 免费服务仍须正确标示 | 必须 |
| 流式/tools/新模态 | 未启用无需实现 | 启用才必须验收 | 启用才必须验收 |

不把所有未来功能变成首版阻塞项，但已知会导致重复执行、串租户、模型冒名、未经许可供给或重复收费的风险不是“以后再修”的细节。

## 18 开放决策和推荐默认值

| 决策 | 推荐默认 | 何时需要改 |
| --- | --- | --- |
| 首个 Harness | WorkBuddy 优先做入口证据核验 | 若固定模型或合法入口不成立，再由用户选择替代 |
| 首个模型 | 官方目录中能明确固定并验证的一种 | 不由文档猜一个具体型号 |
| API | 非流式 Chat Completions 有限子集 | 原生真实能力与客户需求支持时扩展 |
| 工具 | 模型 API 无隐式外部副作用；Agent profile 显式授权 | 不全关，也不全开 |
| 部署 | 独立 Bridge 实例、单 PC、总槽 1 | P3 成功且有吞吐需求后再扩 |
| 计费 | 不收费 | G07 等价门槛通过且价格公开后 |
| 模型自动回退 | 禁止 | 只能作为另一个明确自动模式产品，不能混入固定模型 |
| 长期会话 | 首版每请求新会话 | 远端 tool roundtrip 或明确持续上下文需求后 |
| 幂等范围 | 稳定租户与应用主体 | 当前实现仅 Key 级时必须明确局限 |
| 原生版本升级 | 固定版本，升级先验能力与回归 | 不自动沿用旧版本能力证明 |

需要用户真正决定的是商业模式和在 WorkBuddy 能力受限时接受哪条产品路线。字段名、DTO 目录、日志格式等 routine 工程选择无需反复向用户提问。

## 19 仓库组织和本轮代码交付边界

按用户最新要求，蓝图与代码分目录，云端开发、审计后上传 GitHub。建议结构如下；根 README 只保留定位、状态和导航：

```text
native-harness-api/
  README.md
  AGENTS.md
  blueprint/
    BLUEPRINT_V0_2.md
    CONTRACTS.md
    EVIDENCE.md
    IMPLEMENTATION_AND_ACCEPTANCE.md
    AUDIT_V0_2.md
  code/
    src/                 薄 Facade、Bridge HTTP 客户端、合同、本地适配器
    tests/
      fakes/             离线假网关、假账本、FakeHarness 支撑代码
    examples/            明确为测试的请求与配置
    README.md            运行、测试、能力和限制
```

历史 `docs/`、`audit/` 若保留，明确标注 v0.1，不能与 v0.2 当前合同互相矛盾；若迁移则修正引用，不删改无关工作。后续每次协议变动同时更新蓝图、实现和测试，不能只有文档通过。

首版代码发布标签建议 `0.1.0-prototype` 或同等清楚的非生产说明。其必须具备可重复测试、失败关闭和明确真实能力缺口。示例凭证只用测试值，不能把真实 token、厂商配置或用户工作区提交到 GitHub。

**本轮不能提前宣称的结果**：已经支持真实 WorkBuddy、已经接通生产 404token、已经具备商业授权、已经验证多租户隔离、已经通过实际 token 结算。只有真实三仓联调、真实 Adapter 和对应门槛完成后才能更新状态。

本蓝图审阅不修改远程仓库；实现与上传由同一任务的实施阶段处理，避免并行人员相互覆盖。GitHub 上传是用户已授权步骤，不需要再问一次；仍须保留已有工作、检查提交内容并核对远程 SHA。没有权限或上传失败时准确报告，不把本地 commit 当远程成功。

旧的离线检查脚本可以随新版合同更新，但它始终只是结构/抽象模型辅助。真正防止重复执行，要测试实际 Bridge、Sub2API、Node 和原生入口，不能靠脚本中重写一份理想状态机自证。

## 20 来源和验证范围

下列固定源码为本次读取依据；官方网页可能持续更新，应在开发取证时保存日期和适用产品版本。

### S1 现有蓝图

- [native-harness-api 固定提交](https://github.com/meta-xucong/native-harness-api/tree/82e8cfeaf6ce05857dba590cf1d4bb63efd42ea2)
- [BLUEPRINT](https://github.com/meta-xucong/native-harness-api/blob/82e8cfeaf6ce05857dba590cf1d4bb63efd42ea2/docs/BLUEPRINT.md)
- [CONTRACTS](https://github.com/meta-xucong/native-harness-api/blob/82e8cfeaf6ce05857dba590cf1d4bb63efd42ea2/docs/CONTRACTS.md)
- [REUSE_AND_INTEGRATION](https://github.com/meta-xucong/native-harness-api/blob/82e8cfeaf6ce05857dba590cf1d4bb63efd42ea2/docs/REUSE_AND_INTEGRATION.md)
- [DEVELOPMENT_AND_ACCEPTANCE](https://github.com/meta-xucong/native-harness-api/blob/82e8cfeaf6ce05857dba590cf1d4bb63efd42ea2/docs/DEVELOPMENT_AND_ACCEPTANCE.md)
- [AUDIT](https://github.com/meta-xucong/native-harness-api/blob/82e8cfeaf6ce05857dba590cf1d4bb63efd42ea2/docs/AUDIT.md)

本次读取上述全部核心文档，并核对远程 main 仍为此提交。本地原有 `audit/results.json` 已有未提交改动，本次没有覆盖，也没有在原 checkout 重跑会改写它的检查。

### S2 Render Bridge

固定提交：`cf1e72ec3830854fa0983eb9acb92fad62a604c8`。

- [Node pull 合同](https://github.com/meta-xucong/alchemy-render-bridge/blob/cf1e72ec3830854fa0983eb9acb92fad62a604c8/docs/shared-contract/04_API_PULL_NODE_RUNTIME.md)
- [Node API 与当前图像 profile/MIME 限制](https://github.com/meta-xucong/alchemy-render-bridge/blob/cf1e72ec3830854fa0983eb9acb92fad62a604c8/src/render_bridge/node_api.py#L35-L55)
- [现有 profiles](https://github.com/meta-xucong/alchemy-render-bridge/blob/cf1e72ec3830854fa0983eb9acb92fad62a604c8/src/render_bridge/profiles.py#L90-L99)

该底座值得复用，但当前列出的 profile 是 fake-image/codex-image/midjourney-manual，上传 MIME 是 PNG/JPEG；Harness typed schema 需要新增。不能把 image/video 能力、源代码中存在恢复函数或历史文档等同于新 Harness API 已验收。

### S3 Sub2API 路由和头处理

固定提交：`2695b585aa7c2b9fed6fa50b9b8455c0ac2c1a14`。

- [Chat Completions 路由](https://github.com/meta-xucong/sub2api-adapted/blob/2695b585aa7c2b9fed6fa50b9b8455c0ac2c1a14/backend/internal/service/openai_gateway_chat_completions.go)
- [原始 Chat Completions 转发](https://github.com/meta-xucong/sub2api-adapted/blob/2695b585aa7c2b9fed6fa50b9b8455c0ac2c1a14/backend/internal/service/openai_gateway_chat_completions_raw.go)

- [出站头构建与账户覆盖](https://github.com/meta-xucong/sub2api-adapted/blob/2695b585aa7c2b9fed6fa50b9b8455c0ac2c1a14/backend/internal/service/openai_gateway_cc_pipeline.go#L201-L244)
- [转发前额度与余额预留](https://github.com/meta-xucong/sub2api-adapted/blob/2695b585aa7c2b9fed6fa50b9b8455c0ac2c1a14/backend/internal/handler/openai_chat_completions.go#L140-L160)
- [现有模型映射](https://github.com/meta-xucong/sub2api-adapted/blob/2695b585aa7c2b9fed6fa50b9b8455c0ac2c1a14/backend/internal/service/openai_model_mapping.go#L8-L22)

### S4 Sub2API 流式和重试

- [raw streaming 与失败处理](https://github.com/meta-xucong/sub2api-adapted/blob/2695b585aa7c2b9fed6fa50b9b8455c0ac2c1a14/backend/internal/service/openai_gateway_chat_completions_raw.go#L529-L585)

### S5 Sub2API 结算身份

- [gateway_usage_billing](https://github.com/meta-xucong/sub2api-adapted/blob/2695b585aa7c2b9fed6fa50b9b8455c0ac2c1a14/backend/internal/service/gateway_usage_billing.go#L217-L244)

- [现有结算仓库去重](https://github.com/meta-xucong/sub2api-adapted/blob/2695b585aa7c2b9fed6fa50b9b8455c0ac2c1a14/backend/internal/repository/usage_billing_repo.go#L72-L98)

### S6 WorkBuddy 官方入口

- [WorkBuddy Open API](https://open.workbuddy.cn/docs/openapi)

应分别读取本地助理和云端任务部分；本稿没有因此宣称用户当前电脑版本、账号或模型绑定已经验证。

### S7 相关但不可混同的 CodeBuddy 入口

- [CodeBuddy Code HTTP API Beta 官方文档](https://www.codebuddy.cn/docs/cli/http-api)
- [腾讯云对应 HTTP API 文档](https://cloud.tencent.cn/document/product/1831/137064)

它不能替代 WorkBuddy 桌面应用自身的能力证明。

### S8 社区入口研究候选

- [Desktop Bridge acp.py 固定版本](https://github.com/gosick233-cloud/Codex-WorkBuddy-Desktop-Bridge/blob/c00a8e5eab809a97963277e71ec008abb3d372ae/workbuddy_bridge/acp.py)
- [Desktop Bridge multiplexer 固定版本](https://github.com/gosick233-cloud/Codex-WorkBuddy-Desktop-Bridge/blob/c00a8e5eab809a97963277e71ec008abb3d372ae/workbuddy_bridge/multiplexer.py)
- [workbuddy-acp-bridge 固定版本 README](https://github.com/jie023/workbuddy-acp-bridge/blob/e2327499273efa093ebdcbffde9a4c8833e26e76/README.md)

社区代码是技术参考，不是腾讯官方支持、许可或实际终态/模型真实性保证。复制前另做许可证核验。

### S9 模型配置和子调用模型

- [WorkBuddy 官方模型配置](https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Model)
- [CodeBuddy 主模型 轻量模型 子代理模型配置](https://www.codebuddy.cn/docs/cli/env-vars)

### S10 ACP 协议语义

- [Session config options](https://agentclientprotocol.com/protocol/v1/session-config-options)
- [Tool calls](https://agentclientprotocol.com/protocol/v1/tool-calls)

协议规范不证明用户当前 Harness 实现了所有选项。

### S11 需按用户实际套餐判定的协议

- [WorkBuddy Enterprise 订购协议](https://cloud.tencent.com/document/product/301/137433)
- [WorkBuddy 与企业微信联合订阅协议](https://cloud.tencent.com/document/product/301/140217)

仅作为授权核验线索，不假定其适用于用户当前账号，也不作个人套餐的法律结论。

## 21 本次蓝图阶段交付检查

- 已完成：旧蓝图全部核心文档审阅；关键依赖固定版本核对；目标与架构纠偏；模型、接口、恢复、隔离、计费和阶段验收设计。
- 已完成：本 Markdown 的 UTF-8、标题结构、代码块闭合、来源引用与占位示例检查。
- 未执行：实际业务服务单元/集成测试、WorkBuddy/其他 Harness 实机、真实模型调用、生产安全测试、收费对账测试。
- 本文编写与审阅阶段未执行：远程仓库改写/推送、PR、合并、软件安装、VPS/本地部署。后续已授权代码与 GitHub 阶段以独立实现报告为准。
- 未产生：真实模型推理费用。

**采用本稿意味着有一份更贴近 404token 场景的实施合同，不意味着该渠道已经建成或获准商用。**
