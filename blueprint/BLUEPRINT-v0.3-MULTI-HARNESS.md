# 通用本地 Harness 渠道蓝图 v0.3

> 当前增量：见 [已有官方 CLI 优先接入设计](EXISTING-CLI-ADDENDUM.md)。Codex 默认改为同用户、同 CODEX_HOME 的现有官方 CLI；本文独立 managed profile 仅保留兼容语义。历史基线及测试不追改。

日期：2026-11-08 UTC。状态：**设计稿；独立审计结论见 [DESIGN-AUDIT-v0.3.md](DESIGN-AUDIT-v0.3.md)；不是新适配器发布或生产许可。**

本轮仅修改 `blueprint/`。用户要求先调整通用多 Harness 蓝图，再审计；不以本稿授权新的登录、推理、安装、部署或运行第三方程序。原实现与下载包保持不变。

## 1. 目标、定位与本轮决策

目标是一个本地程序，可接多个真实本地 Harness，并把各自可核实的模型能力接入 404token / Sub2API。用户自行安装、选择产品并通过官方入口认证。VPS 只做用户 API、任务账本和观察，不提取厂商凭据或自行调用内部模型 API。

用户最新重点是**结果正确、原生输出和任务状态及时如实反映**，不强求不同 Agent 与裸模型 API 完全等价。因此：

1. 通用的是生命周期、任务身份、模型绑定、事件、恢复和部署管理，不是声称所有厂商拥有同一协议。
2. 保留已有 WorkBuddy 专用实现作为第一个 Adapter，不重写已经审计的 Bridge/Node/流式链路。
3. 下一个实现优先 Codex App Server；豆包原生 CLI 进入入口验证；千问办公进入证据调查。Qwen Code 与 CodeBuddy 是独立产品候选，不能偷换对应桌面产品或账号。
4. 不用 GUI 自动点击、抓 token、内部模型反代弥补缺少公开执行合同。
5. 通用第一阶段仍是一个可信 owner、一个 PC、全 Node 总执行槽 1、多 Harness 多模型可选。多 Harness 不等于多个独立槽，更不等于多租户。
6. 对固定模型、原生自动模型、文本、工具、系统指令、历史、流式和计量分别声明能力；不能以输出 JSON 的外形冒充完整 OpenAI/Responses 支持。

## 2. 真实基线与文档优先级

代码基线：Native `081e10261c86c61b97c122ff71211d5aeaaca32c`。配套 Bridge/Sub2API 及证据清单见 [来源与能力矩阵](MULTI-HARNESS-EVIDENCE.md)。本稿不重新认证全部历史测试；已交付包是 WorkBuddy 受限试用版，真实账号、模型及用户生产部署仍未验收。

当前已实现的设计底座包括：固定官方 WorkBuddy public Harness SFE、本地用户登录/模型目录工具、最多 32 个模型绑定共享单槽、Bridge 持久事件、双流协议、Windows 自有进程清理、旁路不收费计量。**Codex、豆包、千问适配器尚未实现。**

`BLUEPRINT-v0.2.md`、P1b/P2 文档及其审计是历史阶段事实。v0.3 规定下一阶段设计，不能改变运行中 v1 配置或使已禁用能力自动启用。具体已实现合同仍以当前源码及代码文档为准。发生历史叙述冲突时，按“精确代码版本及专项实证 → 当前代码合同 → 本稿目标设计 → 历史阶段说明”解释；不能用最新愿景反向冒充旧包支持。根 README/AGENTS 中的旧阶段文字应在后续获准实现阶段更新，本轮不越界修改。

## 3. 架构：一条账本，多个专用 Adapter

```text
调用方 → Sub2API（Key/模型/冻结操作/后续计费）
       → Facade（能力与协议/观察/回放）
       → 专用 Render Bridge（唯一执行许可、槽、持久事件与结果）
                     ↑ PC 主动 HTTPS；不暴露 PC 公网端口
             通用 Node / 单 journal / 单全局槽
                  ├ WorkBuddy SFE Adapter
                  ├ Codex App Server Adapter（计划）
                  ├ Doubao CLI Adapter（待证）
                  └ 其他审定 Adapter（默认禁用）
```

不新增 Registry 服务、钱包、队列或第二个调度器。Adapter 注册表首先是随发行包审计的静态白名单，不允许远程任意插件安装、任意 Python import、任意 shell command 或客户端指定 executable。

职责：

- **Core/Node**：执行许可、持久 launch intent、唯一槽、进程所有权、有限 IO/超时、账户 profile 锁、事件上传与恢复。
- **Adapter**：原生协议、认证交互、模型发现/选择回读、任务与消息归属、原生状态与完成证据。
- **Bridge**：唯一 task/attempt/start-grant 与事件序号；不得由 Adapter 另造可重派队列。
- **Facade/Sub2API**：可信调用归属、幂等冻结、能力拒绝、事件消费与公开结果。不能看到 Harness 离线就改走另一 Harness。

## 4. 通用身份及配置（设计结构，不可直接粘入现有配置）

新增版本化配置必须与 v1 显式区分，旧 `workbuddy_local_trial` 不原地改语义。建议对象：

| 对象 | 含义/必须冻结的信息 |
|---|---|
| `AdapterDescriptor` | adapter_id、版本/源码修订、Harness 产品线/地区、原生资产版本和哈希、协议版本、支持 OS、能力证据版本 |
| `ExecutionProfile` | profile_id、资源域 resource_domain_id、工作区/OS 权限/工具政策、账户引用及受保护存储方式；不含账号秘密 |
| `AccountBinding` | 本机 profile 引用、产品/地区、已确认用户身份指纹、认证代际；不包含 token、Cookie 或原始账号资料 |
| `ModelBinding` | binding_id、全局唯一 public_alias、adapter_id、profile_id、原生 model_id、selection_scope、能力版本、版本化摘要 |
| `Operation` | 现有可信 owner/稳定幂等键、原 source/ledger/binding 摘要，并冻结 adapter/profile/resource/account generation/能力版本 |

同一底层模型经两个 Harness 提供是两个独立绑定，不能只以模型显示名合并。别名建议 `codex-<公开别名>`、`workbuddy-<公开别名>` 等，但前缀不是身份或安全证据。全局唯一性由注册表检查。固定模型不可静默降级为 auto；原生 auto 只能以明确自动模式别名发布。

账户切换、产品地区切换或 profile 重建必须产生新绑定代际。旧操作继续定位旧不可执行恢复记录；找不到原恢复条件就保持 unknown，不借新账号补跑。移除/升级适配器前须排空，未决操作需要保留原安全恢复代码与资产元数据；若原版本存在安全风险不得为恢复强行运行，应隔离并保留只读服务器回放。

## 5. Adapter 合同与能力门

下列是项目接口设计，不是厂商 API 名称：

- `inspect`：无推理的版本/安装/合同证据；可能有 IO 的行为必须显式声明。
- `login` / `discover_models`：用户发起、本地官方认证；浏览器可能先于提示打开、账户保存、模型目录可能联网均需告知。不得默认重用 GUI 密钥。
- `prepare`：只执行已证实无推理的准备；无法证明的 session 创建/恢复移入 start grant 之后。
- `execute`：一次消费持久许可，模型选择回读，提交一个已授权原生任务；提交确认不等于完成。
- `events`：仅投影所属 root 任务；子任务另有归属且不串正文；各 Adapter 明确定义 ACK 前缓冲和关联方式，不强加 WorkBuddy 字段到 Codex。
- `reconcile`：只读找回同一任务/结果；厂商 resume/continue 若会产生推理则不属于本方法。
- `request_cancel`：报告请求是否送达；必须独立验证停止/结果，不能由 interrupt ACK 推定完成。
- `close`：Core 管理全部自有进程终止证据；进程退出只证明本地结束，不证明厂商远端任务或工具副作用回滚。

每项能力四态：`documented_candidate`、`implemented_fixture_verified`、`native_entry_verified`、`account_model_verified`；另外明确 `unsupported`。这不是单一成熟度总分，每个 OS/版本/认证方式/模型绑定独立记录。仅 fixture 通过允许开发，不允许显示“真实模型已支持”。

共同必需能力：可信任务关联、固定模型证据或显式 auto 模式、正确正文/终态、有限输出、清理与不重执行。未实现取消/只读恢复时可以声明不支持，但 unknown 必须保持隔离，不以缺能力为借口重新提交。无流接口的 Adapter 可以提供明确 final-only 能力，但不能进入用户要求实时反馈的渠道名单。

## 6. 单槽与跨 Harness 资源安全

通用第一阶段沿用整个 Node 一个执行槽：WorkBuddy 正在执行时 Codex/豆包的新任务拒忙或由 Bridge 既有有界队列处理，不在每个 Adapter 内再排队。跨 Harness 的资源共享（同 OS 用户、桌面、凭据目录、工作区、全局模型设置）由 `resource_domain_id` 表达，不能因软件名字不同认为隔离。

唯一 Bridge 实例协调一个资源域；禁止两个 Bridge/Node 以不同 journal 操作同一域。持久 journal 锁、profile 锁与启动代际检查必须覆盖完整进程树；未知活性锁住整个共享域。后续多槽需每域独立隔离、真实跨进程/重启围栏及配额验收，是另一期，不由配置数字直接打开。

## 7. 正确且及时的统一事件

继续复用 `native-events-v1`：权威 `text_snapshot`、受控 `native_status`、Bridge task 状态和最终 completion/error。Codex 的 append delta 在 Adapter 中累积为快照；豆包可能重写，必须覆盖而非拼接；未知原生事件不能未经审核直接透传。

- 只发布已证明属于本次任务的内容。原生 thread/turn/item/message ID 的对应规则按 Adapter 固定，历史回放和其他前台会话不得混入。
- idle、Stop hook、CLI exit0、request ACK 都不单独等于成功。结果终态、执行活性、观察可用性分别记录。
- 工具内容不能冒充 assistant 正文或客户端待执行 tool_calls。安全投影可表达 requires_action/工具活动，但不泄露路径、参数、凭据或别人的记录；正文也不能承诺自动去敏。
- Bridge 持久 seq 是外部恢复游标；原生序号只用于本地关联。断线只停观察、不取消或重派。
- 保留当前有限合并窗口与背压原则：首个可归属内容尽快发送，连续尚未发送快照可合并，状态屏障与最终完整快照不得越序。声明内部合并最多 1 秒不是网络端到端 SLA。
- 原始帧、解析队列、最终文本、持久日志、单页和慢客户端写出均有独立预算。超限明确报错/unknown，不截断伪成功、不无界扩容。每 Adapter 根据实测事件频率指定预算并回归长答。

双协议保持：默认受限 `openai-append-v1` 只对已发文本的前缀追加生成 choices.delta；重写/缩短发送明确错误，不发成功 DONE，可同键非流式取权威终稿。显式 `native-events-v1` 完整显示改写、状态与正游标。普通 SDK 通常忽略状态注释，不声称其能显示全部任务状态。两种模式共用原操作，不各自执行。

## 8. 输入、工具与审批

逐绑定声明单 user、历史、system/developer、schema、文件、工具等。真正角色入口不存在时不得暗中拼接。若将来支持“把历史作为普通任务材料”的非等价转换，必须另立显式 capability/profile、得到调用方选择并通过测试；不是本轮已实现功能。

原生工具执行与客户端 tools 是两套合同。当前非交互工作模式继续拒绝需人工批准的请求，不默认 fullAccess，也不能声称无需审批的工具全被关闭。未来本机审批 UI 单列开发：绑定原 operation/原生审批 ID/具体动作/有效期，一次回答，拒绝迟到和跨任务批准；浏览器/消息渠道只负责展示，不能经模型输出自动授予权限。缺少原生暂停续接能力就标不支持。

## 9. 恢复与版本迁移

同键首次请求原子冻结 owner、路由、Adapter 与模型能力摘要；同键不同请求冲突。接受状态未知、电脑离线、网络超时、升级、能力下线都不触发换 Harness/账号/模型补跑。回放先于新准入；认证撤销仍禁止非法读取。

v2 配置迁移步骤：
1. 停新准入，记录三仓版本、现有绑定摘要和未决任务，不删除 journal/墓碑。
2. 旧模式的 canonical digest、operation key 范围、source/ledger 保持不变；用**外部版本映射**识别 legacy WorkBuddy，不能把新字段塞进旧冻结摘要使旧键失效。
3. 已受理操作仍用旧 codec 和已冻结 adapter 版本读取；新通用配置建立新 binding revision，默认关闭。
4. 未知版本/字段/mode 全面拒绝新准入。新旧 runtime 不同时运行同资源域。
5. 回滚仅停新准入并恢复代码，不回滚账本或丢失新操作身份；新操作若旧 runtime 不理解只能只读或保持隔离，不能重新创建。

新增字段涉及 Native/Bridge/Sub2API 的 canonical 与存储迁移，必须三仓合同一起审计，不能仅去掉 `workbuddy-` 校验就称通用。

## 10. 404token 管理、隔离与收费

当前专用 Key 标准路径是受限试用接缝，不等于普通供应商 UI、自动切号或收费已实现。下一阶段可以新增显式 native-harness 渠道管理类型，复用 Sub2API 用户、Key、组和模型目录，但执行保持特殊账本路径，绝不进入普通已受理后 failover。

第一期单可信 owner，不可信多客户必须专用 OS/VM、账号与工具权限隔离并实际验证；cwd/session/profile 目录、单槽、API Key 分组均不构成安全沙箱。跨租户不可共享原生记忆、插件或桌面权限。多 Key 同信任域也需明确批准，不能由“一账号多个模型”默认扩大受众。

usage 以原生证据记录，单位/累计与增量/缓存/子调用归属逐 Adapter 定义。当前 WorkBuddy `coverage=unproven`、`total_usage=null`、`billing_eligible=false` 保持，不因接入 Codex 自动解除。没有完整计量时不填0，不估值冒充实际token。

收费是独立发布门：用户决定计费单位/价格/拒答、改写与晚到结果规则；Sub2API按原 operation 做一次结算及持久预留/释放。Unknown不能自动退款后重新扣款重派；具体保留期限及晚到结算须业务决定。商业转供许可依产品、地区和账号套餐核实，本机运行/官方 SDK 不自动提供许可。

## 11. 用户体验与交付形态

目标本地程序有“选择 Harness → 核验版本/来源 → 用户官方认证 → 列出并验证模型 → 生成匹配部署配置 → 无推理预检 → 用户发起真实测试”的完整路径。每个 Harness 的认证/存储/profile 分开，不复制厂商凭据，不隐式切地区或产品。

第一版通用包可用 CLI/启动脚本完成，不以新建复杂桌面 UI 为阻断。安装资产按 Adapter 选择，禁止默默下载运行未审插件；显示官方来源、精确版本、许可证及所需权限。Windows先保持已有审计的 owned-process 路径；Linux/macOS 必须独立生命周期实测后标支持。

服务器配置保持 Bridge+Facade 在 VPS（或与Sub2API共享网络命名空间）、PC主动HTTPS。统一部署生成器输出 matching owner/source/ledger/binding/digest，标准路径开关默认关闭；不强迫修改现有Sub2API容器网络。新增 Adapter 不能破坏 WorkBuddy 已有登录、资源与旧任务。

## 12. 分期、验收与审计

完整步骤与反例见 [实施与验收计划](MULTI-HARNESS-ACCEPTANCE.md)。

- **D0 当前**：文档设计与独立审计，零代码变更。输出问题清单/修订/未决项，不称多 Harness 已开发。
- **D1 通用接缝**：WorkBuddy 回归保持，Fake 第二 Adapter 做跨 Harness 单槽、冻结、流与升级反例。不是第二真实产品支持。
- **D2 Codex**：官方固定 App Server 空账号入口，再用户本机认证/模型真实试用；新 Adapter 与底层共用合同，不改变用户账户。
- **D3 豆包**：验证发行来源、公开参数、身份、模型选择、终态/取消；不成立则保持候选。千问办公同期只做明确接口验证。
- **D4 扩展产品**：Qwen Code/CodeBuddy 独立产品和账户证据，不冒称千问办公/WorkBuddy替代。
- **D5 商业部署**：真实404token路由/账号链、权限隔离、费用与许可、运营回滚通过后才开放不可信或付费用户。

验收分开记录：源码/模拟、真实空账号、用户授权真实模型、生产部署。真实账号测试留给用户自行部署，不阻止完成代码和模拟审计；但也不能提前保证该账号/套餐/版本必然可用。每次开发进展及时汇报；新发现问题不等到最终交付才说。
