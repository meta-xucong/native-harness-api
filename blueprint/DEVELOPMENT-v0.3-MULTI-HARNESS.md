# v0.3 通用 Harness 开发文档（D1 / D2）

状态：开发定稿；独立审计见 [DEVELOPMENT-AUDIT-v0.3.md](DEVELOPMENT-AUDIT-v0.3.md)，允许按本文实施，不代表运行验证。基线 Native `694e2bd`（代码 `081e102`），Bridge `03fd9020`，Sub2API `9ffe3aa2`。本文件把 v0.3 的概念收敛为实际改动，不将每个名词变成组件。

## 1. 本次交付与不做事项

目标：一个 Node 能在 WorkBuddy 和 Codex 模型绑定间选择，仍全局一槽、同一 Bridge 账本、同一 journal、同一持久事件和双流协议。用户自己认证；开发期不拿真实账号推理。D1 完成通用配置/派发及 WorkBuddy 回归；D2 接官方 Codex App Server。豆包/千问仍为候选，本期不写假 Adapter。

不新建队列、注册服务、调度器、插件安装器、统一厂商认证层、任意命令模板或通用审批引擎。不做多租户、商业扣费、GUI 自动化、账户迁移、全平台重构。保持受限单 user 文本输入；没有官方角色合同不拼接冒充 system/history。

## 2. 最小身份方案：复用现有四字段 Binding

**不修改 `harness.invoke.v1`、四字段 Binding、Bridge task、Sub2 operation 表和旧摘要算法。** 新模式是 `harness_local_trial`；旧 `workbuddy_local_trial` 和配置全部保留，不原地升级语义。

通用配置显式 `schema_version:2`，包含公共 Node/服务配置、唯一 `data_dir`、若干 `targets`。每个 target 是一条具体执行配方及一个模型映射，不再增加 Descriptor/Profile/Capability 三套独立注册表。

执行配方是精确 JSON 对象，字段只有：`recipe_version`（本项目固定整数1）、`adapter_id`、`platform`（windows-x64）、`product_version`、`protocol_version`、`asset_identity`（该Adapter固定资产清单摘要）、`profile_path`、`profile_id`、`account_fingerprint`、`policy_id`、`native_model`、`public_alias`。静态adapter表定义product/protocol/policy常量，不接受任意命令/导入/权限对象。

`profile_path` 为用户选择的真实绝对固定盘路径；配置生成时用 Windows `ntpath.normcase(ntpath.normpath(path))` 规范化，拒绝相对路径/变量/父级跳转。打开profile时仍由已有具体工具验证真实路径，不另建路径安全层。`profile_id` 来自本工具创建profile的随机ID；`account_fingerprint` 来自本工具在用户确认后写入的无凭据metadata。它是官方可公开回读属性的确认指纹，不宣称全球唯一accountId。Codex公开account/read无稳定ID时明确记录其限度。token刷新不改变它；用户重新认证/切账号创建新profile ID，不原地复用身份代际。执行前具体Adapter核对当前metadata及官方account回应与冻结指纹匹配。

只放本机配置，不上传路径、账号资料或秘密。新binding的revision沿用字段并固定为1；身份变更以完整配方hash形成新binding_id，不要求操作者手动再增加冗余版本计数。

新 Binding 的 `binding_id = 'h2-' + sha256(canonical(recipe))`，`revision` 与 alias/model 仍是现有字段。配方变化必须得到不同 binding_id；配置加载时重算一次并与声明的 Binding 对应。部署生成器负责产生同一 Binding 给三仓。服务器不理解本机配方，只冻结已经存在的 binding_id/revision/native_model，这已足够阻止更换配方后对旧操作重新准入。不是把身份寄托于 alias 前缀。

本期只注册 `workbuddy.public-sfe.v1`、`codex.app-server.v1` 两个静态实现。未完成 Codex 切片前它不能准入；测试用第二协议替身只在测试，不写入生产注册表。别名 `workbuddy-*`/`codex-*` 用于可读性，真实派发由完整 Binding 对应的配方决定。

### 旧任务与更新

通用 Node 的 `data_dir` 必须沿用该执行资源原 journal 路径；迁移向导要求停旧 Node，不创建第二 journal 来解锁。公共 adapter 继续使用同一 evidence SQLite 和原 request fingerprint 算法，reconcile 首先读取旧记录，不需要当前配方存在，也不导入/启动厂商。新任务才查当前 Binding→配方。Sub2先Lookup旧operation并用其中旧execution_mode/route观察；新Facade/Bridge须先lookup/replay，不要求旧Binding仍在当前targets。恢复已有journal先于NodePolicy新offer准入。三仓切换新mode/移除旧binding后，用真实旧accepted operation做一次只读回放回归。旧配置原样跑，旧结果摘要字节不变；不能丢账本、重造 source/ledger 或以另一个 Harness 重执行。

这里沿用既有单 Node 配置和 journal 文件锁，不添加跨机器锁服务。运维合同仍是一资源域只允许一个 Bridge/Node。单槽限制任务，不限制一个任务的受控子进程。

## 3. 代码接缝：只抽已经确实相同的部分

### 3.1 持久 Adapter

`PublicSfeAdapter` 中 grant intent/SQLite/reconcile/LiveEventPump 是协议无关的。最小改法是抽出或参数化一个 `DurableNativeAdapter`：
- 保持表名、文件路径、fingerprint、start/reconcile 及 pending 语义不变。
- 仅把 `resolve(binding) → (invoke, project_result)` 作为明确依赖；start前按完整冻结Binding从不可变配置选出同一对函数，持有至本次完成。不得按model显示名、evidence自报adapter或执行中变动的current目标重新选择projector。
- 旧 `PublicSfeAdapter` 保留原构造和 WorkBuddy projector，作为兼容薄封装；不修改其原生字段解释。
- Codex 有自己的 projector，不制造 `_codebuddy.ai/error`、SFE session ACK 或模型字段来骗过旧 projector。

只读恢复无新 invoke；事件泵复用已有合并/顺序/预算，Adapter 不再加队列。

### 3.2 Node runtime

新增 `multi_harness_runtime.py` 负责 v2 配置、静态路由和现有 Runner 的组装。`runtime.py` 仅新增模式分派。每个目标依赖自己既有或新增 profile/资产工具：
- WorkBuddy 直接复用已审的 `NativeTrialConfig.invoke` 及 profile 功能，不重写登录/ACL/资产校验。
- Codex 独立实现官方协议和自己的 CODEX_HOME，不读 WorkBuddy 资料，也不读取 Codex auth 文件。用户认证后由官方 account/read 核验身份。profile实现复用已有WinSecurity/文件锁/持久execution标记；只允许为manifest常量与环境构建添加明确策略参数，不复制ACL/Ctypes安全实现，不构建通用凭据管理器。旧WorkBuddy策略为默认且旧metadata格式保持不变。
- 通用 data_dir 内一个 journal、一个原生 evidence 库；profile 锁仍由具体 invoke 持有。切换 Harness 不释放未知槽。

不改现有 Runner 状态机、Bridge 调度、Windows Job 实现或事件格式。NodePolicy/Facade 在新模式只验证已经声明的两种前缀和绑定、单 owner/单 user 输入；旧模式条件不变。

### 3.3 三仓最小变化

Bridge、Sub2API 仅增加显式 `harness_local_trial` 模式和当前已注册 alias 前缀，在现有 mode 冻结字段中保存；复用原队列、专用 Key、标准/原生 SSE、恢复与非收费规则。不新增表、endpoint 或 billing 策略。逐一检查所有 `workbuddy_local_trial` 条件，凡属于通用单租户/单 user/非收费行为同时覆盖新模式，厂商特有行为不扩大。

部署生成器增加 v2 specification 分支，旧输入输出逐字节语义保持。生成的 source/ledger/node/owner 仍单一，targets 不按 Harness 分成多个 Node。Facade 只获得 Binding，不获得资产路径、profile 身份或账户摘要。

## 4. Codex 接口切片（官方固定 schema）

Codex 锁定官方 `rust-v0.167.0` 完整 Windows App Server 包，直接运行 `bin/codex-app-server.exe --listen stdio://`（不加子命令）。资产hash、schema、登录与EOF依据见 [固定合同](CODEX-APP-SERVER-CONTRACT.md)。不用GUI点击/私有token/云模型替代。合同只有静态已验证，真实执行仍NOT_RUN。

最小流程：initialize → account/read → thread/start（每请求独立，显式模型）→ turn/start → 所属 turn 的 agentMessage 增量/完成 → turn/completed → EOF/已有 owned cleanup。只读恢复仍是本项目已保存证据，不以 thread/resume 自动续跑。取消只发原 turn interrupt，不把ACK当终态。

`model/list` 仅在用户模型发现工具调用，不在每次请求重复拉目录；执行通过thread响应/有效选择证据回读所选模型，首期明确请求并核对`modelProvider:"openai"`，防止同名模型被配置路由到另一provider。只声明该字段能证明的配置身份，不从输出文本猜实际模型。正文按 thread/turn/item 归属；工具、推理摘要和子任务不混入正文。具体phase算法：
- 每个agentMessage item按**本次首次观察顺序**排位，不声称厂商提供所有帧的全序。delta只更新其item，item/completed.text替换同item快照。
- 已明确commentary的item只发布running状态，不显示其原文；首版不是完整Codex UI镜像。null表示未知，不作为final证明；后续phase改变会重新计算快照。
- 存在final_answer时，只以所有final_answer item的文本按首次观察顺序用`\n\n`连接成权威候选；否则使用partial_answer/null候选同样连接。不是把commentary/旧partial重复拼到最终答案。
- turn/completed中实际出现的agentMessage可合并修正对应item，但其itemsView可能partial，绝不因缺项清空已确认文本。completed且文本非空才可成功；只有commentary的完成不伪造回答。
- 多item顺序、phase迟到/替换、partial终态视图、空完成与标准流改写错误必须有fixture。

Codex server生命周期与SFE不同：先等明确turn终态再EOF；有界等待后允许owned Job收尾idle server。Codex成功要求终态、账号/模型归属、完整正文、没有协议/归属/输出fault及全部本地清理确认，不额外强求自然exit0；强制动作发生在终态之前不能补成成功。收尾不证明远端副作用回滚。记录graceful_exit/forced_cleanup事实，不把强制说成自然退出；增加终态前强杀unknown、终态后强制清理成功、清理不明仍unknown、终态后协议冲突不可洗白四项fixture。该Codex projector策略独立，旧WorkBuddy natural_exit_zero规则保持不变。

Codex第一期只支持用户自行device-code登录的ChatGPT账号；API key/Bedrock/GCP/外部auth token不在本期。项目只存用户确认的公开身份属性指纹（type/email/workspaceName，不含可变planType），不存/读原生认证秘密，不将其称唯一accountId。read-only + on-request由官方thread/start传入并回读；自动批准被禁，收到审批cancel/interrupt。read-only并非全禁命令/读文件/MCP/网络，独立CODEX_HOME也不排除企业配置；不声称严格无工具或OS安全沙箱，不静默fullAccess。

UI先做CLI，不新建桌面框架：选择Codex资产与新profile → 官方登录 → 身份确认 → 模型列表 → 生成targets。Windows仅复用已审 owned transport；其它OS只做fixture，不冒称实机支持。

## 5. 校验精简清单

新增检查仅保留以下边界，各有唯一责任；不在内部每层再次检查相同不变量：

| 边界 | 检查 | 必要原因 |
|---|---|---|
| v2配置加载 | schema、静态adapter、配方hash、唯一alias/Binding、最多32、单data_dir | 防错配/歧义，限制现有合同范围 |
| 具体厂商invoke | 资产身份、官方账号/模型回应、profile锁 | 正确调用用户选择的软件/账号/模型；沿用既有工具 |
| 厂商协议入口 | JSON frame大小、请求ID、thread/turn归属、终态 | 不把其他会话或半条响应当结果 |
| Core | 现有grant、持久intent、单journal/Windows owner、事件预算 | 防重复执行和提前宣告完成，不新加同义gate |
| 三仓外部请求 | 现有认证、固定Binding、合同与幂等 | 沿用，不再增加所谓“通用保险层” |

不添加新watchdog/心跳、新checksum叠层、任意超时重试、自动杀外部进程、自动修复账号、测试专用生产开关、动态插件DSL。不能为追求最少行数删除既有正确性保障；也不顺手重构历史重复校验。

## 6. 分阶段验收与文件范围

**S1 文档**：本文+Codex具体合同，独立审计。只在审计关闭后编码。

**S2 通用接缝**：`multi_harness_runtime.py`、最小registry/recipe帮助函数、durable adapter接缝、runtime/NodePolicy/Facade分支、三仓mode检查及部署生成。测试两个不同协议替身共享真实同journal：A执行B拒忙，A未知阻B，A移除后只读回放、配方变化拒旧新执行、旧v1摘要与结果回放不变。Codex未实现时静态条目不准入。

**S3 Codex**：协议状态机、通用owned IO中的最小协议工厂/cleanup hook，Codex profile/login/models CLI与官方资产清单。复用IO而非复制几百行读写/线程/清理；WorkBuddy协议工厂默认保持完全原行为。若协议结束方式不同，仅明确hook，不搭建泛型状态机DSL。

**S4 实际用户交付**：两种Harness部署生成、选择式包/说明，跨三仓真实socket+synthetic上游，Windows owned fixture，精确CI、实物核验。真实官方入口检查仅在明确获准范围，真实账号模型留用户自行试用；不借WorkBuddy历史实测给Codex加“已真用”标签。

测试只加新行为和已发现边界：旧WorkBuddy完整回归；单槽/冻结/恢复；Codex乱序ACK/foreign turn/模型错误/取消和审批/多个item/空终态/EOF；实时覆盖与游标；Windows清理；配置生成三仓一致。不新增与源码无关的仪式化哈希表或每项重复审批。

## 7. 已知CI问题的独立最小修复

694e2bd的push Windows请求失败耗时与FakeGateway3秒期限吻合，并伴随临时SQLite锁；具体原始超时原因未证实。已确定性复现测试服务context只join监听线程，未等daemon请求线程的清理缺口。此问题与通用模式无关；单独修测试server有界排空与阶段证据，保持原timeout/结果断言。不得用扩大timeout、删断言或盲重跑当修复，不修改生产http线程政策来掩盖测试缺陷。

## 8. 定稿与交付规则

发布前按实际变更路径核对现有workflow：通用unit/Windows自写peer与data-only包正常跑，不修改或触发旧vendor smoke。新增Codex真实入口不作为普通push副作用，若需执行单独明确范围。

逐切片给出精确commit与实际测试、源码/fixture/native/account四种状态。文档与代码仍分目录；只推既有draft PR，不合并/部署。每次实质进展向用户汇报。最终结果必须说明支持哪些Harness/版本与未实现能力，不能将本期Codex代码等同豆包/千问已接入。
