# Codex App Server 固定协议证据（开发合同定稿）

> 当前增量：见 [已有官方 CLI 优先接入设计](EXISTING-CLI-ADDENDUM.md)。Codex 默认改为同用户、同 CODEX_HOME 的现有官方 CLI；本文独立 managed profile 仅保留兼容语义。历史基线及测试不追改。

状态：开发定稿；独立审计见 [DEVELOPMENT-AUDIT-v0.3.md](DEVELOPMENT-AUDIT-v0.3.md)，允许按本文实施，不代表运行验证。

核验日期：2026-11-08。范围：官方公开发行元数据、下载归档哈希与固定版本静态源码/schema。**未执行厂商二进制、未登录、未推理、未做 Windows 真机验证。** 本文件不能代替真实接入验收。

## 1. 固定发行与入口

- 官方发行：[`rust-v0.167.0`](https://github.com/openai/codex/releases/tag/rust-v0.167.0)，核验时 latest，2026-11-06 发布。
- 固定源码 commit：`086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a`。下列源码路径均相对此 commit，而非 main。
- Windows x64 完整 App Server 包：[codex-app-server-package-x86_64-pc-windows-msvc.tar.gz](https://github.com/openai/codex/releases/download/rust-v0.167.0/codex-app-server-package-x86_64-pc-windows-msvc.tar.gz)。164336310 bytes；发行声明及实际下载计算一致的 SHA-256：`16f99769bd4accc519cf22079645db445907182c8b96bc626f496b7390f5ad46`。
- 归档实际包含 `bin/codex-app-server.exe`、`bin/codex-code-mode-host.exe`、`bin/codex-exec-server.exe`、`codex-package.json`、`codex-path/rg.exe`、`codex-resources/codex-command-runner.exe`、`codex-resources/codex-windows-sandbox-setup.exe`。保留布局，不只拷贝一个 exe。
- standalone 入口：`bin/codex-app-server.exe --listen stdio://`。它**不带** `app-server` 子命令；只有完整 `codex.exe` 使用 `codex app-server`。
- stdio 为默认入口，读写逐行 JSON。不开 WebSocket 监听。独立进程的 stdin/stdout 仅由本地适配器持有；stderr 不是模型输出。

来源：[main.rs](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/app-server/src/main.rs)、[发行 API](https://api.github.com/repos/openai/codex/releases/tags/rust-v0.167.0)。

## 2. 固定交互合同

以下类型在 [固定 TypeScript schema 目录](https://github.com/openai/codex/tree/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/app-server-protocol/schema/typescript)；未注明前缀的 v2 方法类型位于 `v2/`。JSON 请求使用 `id/method/params`，响应按 id 配对，通知按 method 分流。不要混用 WorkBuddy SFE 方法。

| 阶段 | 固定字段与约束 |
|---|---|
| initialize | `initialize` params `clientInfo:{name,title,version}`（按 InitializeParams 可选提供 capabilities）；收到 InitializeResponse 后发送 `initialized` 通知。响应包含 `userAgent,codexHome,platformFamily,platformOs`；本地路径不对外发布。 |
| 登录检查 | `account/read` params `{refreshToken:false}` → `{account,requiresOpenaiAuth}`。`account:null` 不是已登录。API 的 refreshToken=false 不等于进程完全不联网。 |
| 用户登录 | `account/login/start` params `{type:"chatgptDeviceCode"}` → `{type:"chatgptDeviceCode",loginId,verificationUrl,userCode}`。仅在本地 UI 展示 URL/code，由用户完成授权；等待同 loginId 的 `account/login/completed` 成功通知，再 read。不要自动登出或复制其他程序的凭据。 |
| 模型目录 | `model/list` params `{cursor:null,limit:100,includeHidden:false}`，按返回 `nextCursor` 翻页；响应 `data`。Model 的 `id`、`model`、`displayName` 不可混淆：执行使用 `model` 值。保存实际支持的 reasoning effort，不能猜型号或把展示名称当路由。 |
| 新任务 | `thread/start` 设置明确 `model`、独立 `cwd`、`sandbox:"read-only"`、`approvalPolicy:"on-request"`。ThreadStartResponse 直接含 `model/modelProvider/thread/cwd/approvalPolicy/sandbox`，核对实际回读。每个任务新 thread，不靠 resume 重发推理。 |
| 启动 turn | `turn/start` params `{threadId,input:[{type:"text",text:prompt,text_elements:[]}]}`（`text_elements` 在固定 UserInput schema 中为必需数组）；响应 `{turn}`，锁定 `turn.id`。事件可能先于响应，现有 runner 的有限预确认缓存不能被当成任务完成。 |
| 文本增量 | `item/agentMessage/delta` 的 `threadId,turnId,itemId,delta`。必须关联当前执行；不得把 reasoning、工具 stdout 或其他 turn 的内容当回答。 |
| item 快照 | `item/completed` 的 agentMessage item 含 `id,text,phase` 等；`text` 是该 item 的最终快照。若与已发送增量不同，保留原事件并发 correction/snapshot，不可伪装为纯追加。 |
| turn 终态 | `turn/completed` params `{threadId,turn}`。TurnStatus 仅 `completed/interrupted/failed/inProgress`；只有匹配 turn 且 completed 才可成功。读取 `turn.error`；`itemsView` 可能是部分视图，不能认为 `turn.items` 一定是完整答案，更不能用空/部分 items 擦除已经确认的 item 快照。 |
| 模型变更 | 收到 `model/rerouted`（threadId/turnId/fromModel/toModel/reason）不能仍宣称固定模型成功；记录真实路由并进入既有模型不一致处理。模型回读不是模型在正文自报的名称。 |
| 取消 | `turn/interrupt` params `{threadId,turnId}`；RPC 成功只是请求已接收，不是停止确认。等待匹配 `turn/completed` interrupted，或受控进程终止证据。 |

`MessagePhase` 为 `commentary | partial_answer | final_answer`，且官方说明 provider 不总是提供，null 表示未知而非 final。按 item 保留原始文本/phase，commentary 用任务状态通道呈现；不得简单把 commentary 和 final_answer 拼成一段最终答案，也不得在 phase 为空时丢掉所有真实文本。现有响应投影应明确兼容策略。

登录另有 browser `type:"chatgpt"`（返回 authUrl）及 `type:"apiKey"`（要求传 apiKey）；第一版只需 device-code。`chatgptAuthTokens` 是实验性外部令牌入口，不在本方案使用范围。

## 3. 身份与本地配置边界

`Account` 稳定返回类型：`apiKey`、`chatgpt`、`amazonBedrock`、`gcp`。chatgpt 分支只给 `email:null|string,planType,workspaceName:null|string`，**没有稳定 accountId**。因此 email/workspaceName 哈希只能作展示变化提示，不能作为唯一账户身份证明。采用独立本地 profile/CODEX_HOME 与用户确认的 profile revision；账号状态变化时重新确认。实验性的 workspaceRouting 不应为制造唯一 ID 而默认启用。

每个 adapter profile 使用独立 `CODEX_HOME` 和独立空工作目录，不读用户原 Codex home、不复制 auth.json、不继承原个人 config.toml/MCP/项目 AGENTS.md。通过官方登录创建此 profile 的授权。CODEX_HOME 不是沙箱：系统/企业策略、环境变量和托管配置仍可能影响运行；环境传递须用已有进程启动边界，不能声称换目录就排除了所有系统配置。

**未找到可据固定 schema 承诺的“一键禁用所有 MCP” release 开关。** 不应把 `-c mcp_servers={}` 当作已证明能清除所有配置层的办法；空表的合并语义不能猜。独立 home 排除已有个人 MCP 配置，但不能承诺忽略企业强制配置。`disabledPluginIds` 也不能冒充全面关闭插件能力。若产品要求严格无工具模式，必须另做源代码确认/实际验证后再宣称支持。

默认 `read-only` 明确限制文件修改，不代表禁止所有命令、MCP、网络或数据读取。`on-request` 保留审批请求。非交互 provider 不自动 accept：

- command/file approval 按对应固定 Response 返回 `{decision:"cancel"}`，记录需要用户动作并停止本任务。`cancel` 已存在于两种 Decision 类型。
- MCP elicitation 的固定 Response 为 `{action,content,_meta}`，不能用 command 的 decision 字段冒充；未实现的交互请求按未支持请求处理并停止/interrupt，不伪造用户输入。
- 权限请求、工具用户输入等也不能默认授权或伪造成功。统一归为现有 `requires_action`/unsupported 能力边界，不新增通用审批产品。
- 不使用 `danger-full-access`、不自动改用户安全设置；系统策略拒绝只如实报告。

`--disable-plugin-startup-tasks-for-tests` 与 `CODEX_APP_SERVER_DISABLE_MANAGED_CONFIG` 在该源码中是 debug/test 限定，不可作为正式 Windows 包的设计依赖。`--strict-config` 不是绕过配置的开关。

## 4. EOF 与进程所有权

固定源码 [stdio.rs](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/app-server-transport/src/transport/stdio.rs) 在 stdin EOF 发送 `ConnectionClosed`；[lib.rs](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/app-server/src/lib.rs) 的 stdio single-client 分支随后退出主循环、drain cleanup/background tasks、shutdown threads，再收尾 transport。

因此正常关闭为：已终态后关 stdin → 等待所拥有进程退出。EOF 本身不是 turn 成功，也不是所有子进程已退出的证据。源码的 45 秒 watchdog 是 Unix 条件代码，不能套用到 Windows。Windows 必须复用项目既有有界等待/Job 所有权回收，不新增猜测式 kill 任意 PID，不承诺 EOF 后必定立刻退出。不存在已核实的 SFE `session/close` 等价 RPC。

## 5. 验证等级与实施边界

已验证：发行固定 commit、完整 Windows 包内容及 SHA-256、以上公开 schema 与 EOF 静态控制流。

**NOT_RUN**：官方 exe 启动握手、Windows 沙箱初始化、device-code 真实登录、账户模型目录、真实模型增量/终态/取消、Windows EOF/子进程清理、所有系统配置层的 MCP 排除、实际 sub2api 联调。因此可先按合同做 fixture/offline 测试，不能把离线通过描述为真实 Codex 账号推理通过。

最小新增仅应覆盖 Codex 进程协议适配、目录/登录、上述事件归一化；复用现有 runner、证据存储、任务恢复和 UI，不创建另一个任务系统或通用插件框架。本项目已保存且归属核验的投影/结果证据只读回放是恢复事实，重启后自动再次 turn/start 不是恢复。

补充阅读：[官方 App Server 文档](https://learn.chatgpt.com/docs/app-server)（动态文档，仅辅助；版本合同以固定源码/schema为准）。

### 固定 provider 补核

首期 ChatGPT device-code profile 显式传 `thread/start.modelProvider:"openai"`，并核验 `ThreadStartResponse.modelProvider === "openai"` 与预期 `model`，防止同名模型被默认配置选到其他 provider。[ThreadStartParams.ts](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/app-server-protocol/schema/typescript/v2/ThreadStartParams.ts) 定义 `modelProvider?: string | null`；[ThreadStartResponse.ts](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/app-server-protocol/schema/typescript/v2/ThreadStartResponse.ts) 定义必需 `modelProvider: string`。固定 [model-provider-info/src/lib.rs](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/model-provider-info/src/lib.rs) 第 85 行定义 `OPENAI_PROVIDER_ID = "openai"`，内建目录采用此 ID；第 799 行起的合并逻辑保留 openai 内建身份、推理路由和认证，仅允许受限 catalog URL override。此核对确认选择的 provider ID，不单独构成完整网络端点/环境隔离证明；仍遵守前述独立 profile 和环境边界。此次仅静态核验，未运行。
