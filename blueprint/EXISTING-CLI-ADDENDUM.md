# 已有官方 CLI 优先接入：v0.3 增量设计

日期：2026-11-08 UTC。状态：**独立设计审计通过，允许本文窄范围实施；见 [审计报告](EXISTING-CLI-DESIGN-AUDIT.md)**。本增量优先于 v0.3 中要求 Codex 新独立登录/profile 的默认体验；不追改历史证据，不改变旧配置或任务身份。

## 1. 基线与范围

Native `d324b4686dc09724a4ddd0319981c257e6abeb59`（PR #2 draft/open）、Bridge `52dc083207a8a02dfc14fe2ec1ea10f0cc0a54a8`、Sub2API `b25ff6169885c66308ed3dffd93da9c3cb3fcf79`。本轮只改 Native 仓；设计放 `blueprint/`，代码、运行说明和测试放 `code/`。

已有通用 Node、Codex App Server/受控独立 profile、WorkBuddy SFE、持久证据、单槽与双流均已实现并有离线测试，不是从零新建。真实 Codex/WorkBuddy 用户账号、模型与生产服务未验。本轮目标是让用户已有官方 Codex CLI 登录直接为本地适配器使用，而不是让用户重新登录一份账号。

- **Codex 默认**：同一 OS 用户，直接调用用户已有官方 CLI；解析现有 `CODEX_HOME`，未设置则使用该用户的默认 `.codex`。只由官方 `login status` 和 App Server 读取认证。项目不读取、复制、迁移、导出 `auth.json`、系统凭据或 token，不主动认证/登出。
- **WorkBuddy**：保留现有独立官方 public SFE 的同账号独立登录简化体验。它不等于复用 WorkBuddy GUI 会话。
- **豆包工作、千问办公**：依公开入口证据保持候选。Qwen Code/CodeBuddy 是独立产品，不冒充对应办公软件。此轮不做假 Adapter。
- 不增加平台认证服务、队列、插件框架、自动升级、自动重试或安全沙箱。不运行真实账号/模型，不部署/启用商业服务，不合并 PR。

## 2. 厂商服务限制与诚实声明

[官方 App Server 文档](https://developers.openai.com/codex/app-server/)的认证说明限制将此认证用于商业或托管服务。404token 对外商业转供不能据本机登录复用、软件开源许可、技术可调用或用户勾选确认推导为获准。商业/托管启用不属于此轮开发授权；后续须取得适用官方许可或另行采用允许该用途的正式供应路径。本轮本机连接和代码测试不作商业授权判断或豁免。

[官方认证说明](https://developers.openai.com/codex/auth/)说明 CLI 使用 CODEX_HOME 下文件或系统凭据存储，由官方程序管理。`account/read(refreshToken:false)` 只表示该请求不要求刷新，不承诺官方程序绝无刷新或持久化副作用。用户已有配置、MCP、skills、插件、企业配置和权限仍可能生效；read-only/on-request 不是绝对无工具、无网络或 OS 隔离。

## 3. 最小用户路径

1. 已有部署优先选原 `windows-node.json`：直接沿用原 source/ledger/data_dir/journal 恢复，不先跑登录、目录或重新绑定，不启动第二 Node。
2. 全新 Codex 连接优先找 PATH 上 Codex，或用户给出真实官方 `codex.exe`。首次显示解析后的实际 exe、CODEX_HOME 与继承现有配置的说明，用户确认后才运行。PATH 定位不等于官方来源认证。兼容限定 Windows x64、已审 `0.167.0`；不匹配就明确停止，不静默换包、下载或降级。
3. npm 常见 `codex.cmd` 只用来定位官方包既有平台二进制布局，不用 shell 执行任意脚本；找不到时请用户选择真实 exe。参考固定官方源码 [`codex-cli/bin/codex.js`](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-cli/bin/codex.js)。不解析/执行未知 shim 内容。
4. 官方 `--version` 有界执行；认证只使用后续 App Server `account/read`，不再额外运行或解析 `login status`。后者仅列作用户人工排障指引。不显示未经筛选的原始错误。未登录时给官方 CLI 登录指引，由用户自己在同一环境完成；工具不自动打开新登录流程，不运行一次推理作验证。
5. 用 `codex.exe app-server --listen stdio://`，复用现有 owned 进程/IO，完成 initialize、account/read、model/list。确认返回的 codexHome 是选定 home、公开身份及目录。用户确认账号并选择目录中的 `model`，默认采用目录唯一 `isDefault` 项，允许用户更改。这只能确认连接与目录，不证明额度、真实模型执行或商业可用。
6. 保存本工具自己的 sidecar/profile 元数据，生成新 target 和匹配部署配置，准入默认关闭。以后执行复用冻结 exe/home、原有单槽与任务链；不在每次任务前重新登录/拉目录。旧包独立登录命令保留为显式兼容路径，不是默认兜底。

## 4. 身份与兼容：只新增一个明确 Adapter

新增静态 `codex.user-cli.v1`。旧 `codex.app-server.v1`、`workbuddy.public-sfe.v1` 的 recipe、摘要、assets_root 含义和运行行为逐字不变。

新 adapter 使用既有 recipe 字段，`profile_path/profile_id` 仍指本工具拥有的独立 sidecar，不指外部 CODEX_HOME。额外只加 `cli_path`、`cli_sha256`、`codex_home` 三字段，并纳入 canonical recipe hash；`asset_identity` 表示已审 CLI/协议合同而不是冒称所有同版本 binary 都有固定归档哈希。sidecar 保存这份精确本地连接信息及公开账户确认指纹；不存秘密。`assets_root` 在新 target 仅为所选 CLI 父目录，不使用旧 bundled archive 校验。

新 sidecar 复用现有 profile 的文件锁、受控本地目录与 execution intent 状态，不复制其实现；不把 ACL、manifest、lock 文件写到用户现有 CODEX_HOME，不初始化/清空/迁移其中内容。sidecar 是本工具执行状态，不是新的认证 profile。环境继承当前 OS 用户的 HOME/USERPROFILE/APPDATA/PATH，显式 CODEX_HOME 设为用户已确认原路径；不让程序改用空 home。

冻结 exe SHA 是本地选择一致性证据，不是官方签名。首次确认后升级/替换 exe 或改 home 必须重建新 target，不能悄悄替换旧 binding；旧任务仍只读恢复。当前进程不读取任何认证文件来确认账号，由官方 account/read 核验已有公开身份指纹。共享 CLI home 不锁住或操纵用户其他 GUI/CLI 会话，不承诺 account/read 与后续执行间存在厂商账户事务锁；执行中的 account/updated 继续按现有协议拒绝。用户不要在本工具任务中切账号。

## 5. 执行与恢复不变

新 adapter 复用现有 CodexSession、CodexOwnedSession、transport、projector、DurableNativeAdapter、Node journal 和全局单槽，不另造协议或调度。唯一入口差异是 `codex.exe app-server ...`、同用户环境及 sidecar 的元数据验证。新 adapter **每次 initialize 回读的 codexHome 必须与冻结 recipe 一致后才允许 thread/start**，不是只在首次连接检查；旧 adapter 不追加该语义。thread/start 仍显式 openai/model/read-only/on-request，归属、终态、审批、预算和清理沿用已审合同。

旧配置和原 Node data_dir 不自动迁移。新 target 可加入已有 v2 配置，部署生成沿用原 identities。已受理 WorkBuddy/managed Codex 任务从原 evidence/journal 只读回放，不因 CLI 变化、target 移除或账号未登录重新执行；恢复不依赖新 connect/目录成功。未知仍占原槽，无删除状态解锁捷径。

## 6. 实施文件与验收

实现限制：一个已有 CLI 的本地连接工具/最小 profile 策略、现有 control protocol 的公开身份初次确认分支、registry/runtime 的新 adapter 分派、Codex 简化入口及必要打包 allowlist。Core/Bridge/Sub2API/WorkBuddy 执行不改。现有 Codex managed 登录仍兼容，默认向导改为已有 CLI。说明明确同账号复用与独立 SFE 登录的差异。

必须测试：
- PATH/显式 exe、官方 npm 平台布局、缺失/版本不兼容不 fallback；仅 synthetic 程序，不运行官方 vendor。
- 同用户环境和原 CODEX_HOME，显式 home 不被新 profile 改写；不读写 auth/凭据文件；sidecar 与外部 home 分离。
- 未登录、身份或 home 不匹配、目录失败、用户取消时不生成新渠道/不发 turn/start；状态原始输出不泄漏。
- 新 recipe 三字段参与 binding，旧 recipe/digest 字节不变；替换 exe/home/账号不新执行旧任务。
- 复用账号后的新 target 执行仍使用原 Codex 协议；旧 WorkBuddy/Codex 已受理任务移除目标后仅回放，启动次数为零；unknown 不释放槽。
- Client 默认 Codex 与已有配置恢复、明确旧 managed 命令兼容、模型选择和配置生成；包实际包含新增工具。
- 完整原生 Python 回归、offline demo、compileall；Windows 自写 peer/owned cleanup CI。分别报告 PASS/SKIP/NOT_RUN，不借历史测试。

真实官方启动、用户登录复用/额度/模型、生产及商业许可均 **NOT_RUN / NOT_VERIFIED**；本轮完成标准是最小可用代码、关键离线/Windows测试与独立审计，不追加不相干防御系统。
