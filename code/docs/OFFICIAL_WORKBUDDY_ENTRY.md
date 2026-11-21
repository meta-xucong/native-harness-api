# 官方 WorkBuddy 入口证据与无账户 CI

2026-11-07。这里只核验官方发行物和受限启动面，不把内部实现当作对外稳定 API，不证明用户登录、模型调用或完整 404token 上游已可用。

**后续状态纠正：** 本文下方的“获准下一步”均为已执行的历史实验记录，不是持续重跑授权。后续官方 profiler 仍未取得 initialize 响应；见 [实际结果、模式分流线索与副作用边界](WORKBUDDY_STARTUP_PROFILE.md)。只发 initialize 证明助手没有发送 session/new、选模或 prompt，不保证厂商内部没有创建/保存会话或其他启动副作用。没有读取用户会话文件来验证这一点。不得据本文追加 --serve 或重试本机。

## 静态证据

官网 `https://www.workbuddy.cn/` 的下载模块引用公开更新接口，Windows user x64 版本为 `5.9.3.42398134`。固定发行物：

- URL: `https://download.codebuddy.cn/workbuddy/saas/win32-x64-user/WorkBuddy-win32-x64-user-5.9.3.42398134-d4692421.exe`
- 大小：450,174,440 bytes。
- SHA256：`b5edb52bd6c5b6c7296cf2fff697b019c86d11992a8a814c3010d611896852f9`，与官方更新 JSON 一致；不是独立 Authenticode/Sigstore 认证。
- 已有系统 libarchive 3.7.4 可只读识别其中的 7-Zip 载荷。没有安装额外解析器，没有执行安装器、厂商代码或读取用户凭据。
- 载荷 offset 276333，length 449257893；7z 起始/后续头 CRC 及边界验证通过，SHA256 `19d3e4ad5108a080ef755c4475eecb84d97e46cfe77334d5d5e645fb1408e7e9`。
- 共列出 7118 个成员。根 `WorkBuddy.exe`，`resources/app.asar`，实际 CLI 入口为 `resources/app.asar.unpacked/cli/bin/codebuddy`，不是臆测的 extensions 目录。
- 入口是 Node JS 启动器，15166 bytes，SHA256 `8df0f1c7bc6535e5df0b626450228241d874867023adb6c9c8f7b1a882590bf4`。可以选择包内 `dist/codebuddy-lite-wb.mjs`，不另装独立 CodeBuddy。

发行物中的 GUI 宿主使用 Electron Node 模式，启动 `--serve --no-session-persistence ...` 并提供产品配置与 credential-protection bootstrap。独立 `--acp` 虽在 bundle 中实现，**不能推断它等同 GUI 的受支持登录与执行路径**。不复制、解密或重建宿主秘密传递；不从日志捕获侧车密码来绕过这一缺口。

静态 ACP `session/set_model` 返回 `{modelId: ...}`，config option 路径返回 configOptions；选模还涉及设置持久化/respawn 状态。该副作用不单独证明当前 session 未固定，也不证明它已经与全局设置可靠隔离，须继续验证具体作用域。

发现的主响应 fallback 分支要求 print 模式且显式配置 fallbackModel。不能误报“默认 ACP 必定静默换模”；也没有证明所有其他路径都不存在换模。轻量审查/子代理模型与主响应模型必须分清。

同一 bundle（SHA256 `c57a02ee3f2ef813004262cd003774a6e71bd2a6f5005481359301b407977ea5`）会在原生结构化 ACP 元数据中提供 `codebuddy.ai/responseModelId`，但其来源不始终是响应模型：历史记录构造可回退到 telemetry 的模型，session 缓存又可被历史/子代理事件更新。当前请求 ID、session 和主代理事件关联是必要条件，却不能单靠字段名区分这些来源。不能把 selected 复制成 reported，也不能把陈旧缓存当本次运行证明；现有严格合同尚未放开。这是来源/作用域核验缺口，不是要求密码学证明厂商后台权重。

上述检查只保存内部研究快照和摘要，不将厂商 JS、安装包或版权内容复制进本项目或用户包。

## 专用 Windows CI 的允许范围

工作流 `Official WorkBuddy no-account entry smoke` 只在自身、探测工具或 owned Job 组件/专项测试变化的 push 上运行。它先通过真实 Windows Job 合成测试，再使用临时 Windows runner、已有 7-Zip，不安装 WorkBuddy；校验 installer/载荷/入口摘要后解压固定载荷，不执行 NSIS。

每个进程通过 [owned Job 组件](WINDOWS_JOB_COMPONENT.md) 创建和回收。使用全新临时配置目录及环境 allowlist，不传 GitHub token、代理、原生账号、用户配置或 API Key。环境重定向不是 OS sandbox，KnownFolder API 可能忽略它；真正边界是没有用户登录态的独立一次性 runner。

只运行两个动作：

1. `WorkBuddy.exe <官方包内 JS 入口> --help`，要求正常退出并观察到帮助标志。
2. 同一固定入口 `--acp`，只发送一条标准 `initialize`，client filesystem/terminal capabilities 为 false；不发送 authenticate、session/new、选模、prompt 或对任何服务端请求的确认。

任何新增权限/协议要求、身份验证请求、畸形帧、超长输出、超时、非预期退出或 owned Job 清理不确认，都失败而不是自动修复或改用其他登录模式。读写限长限时，报告仅含固定版本/摘要、有限状态和字节数，不输出原生任意文本、路径或配置。

工具及 fixture 单测分别位于 `tools/official_workbuddy_smoke.py`、`tests/test_official_workbuddy_smoke.py`。fixture 负例通过不代表厂商入口通过。

## 实际有限结果与后续诊断

精确提交 `b00af16a8772b14cc0c354400a691e9f4628b821` 的 [Windows CI](https://github.com/meta-xucong/native-harness-api/actions/runs/42404235577) 已实际运行固定官方包：Job 前置测试 23 PASS / 1 非 Windows guard SKIP；`--help` 正常退出码 0，观察到 help/ACP 标志，owned Job 归零。随后仅发出一条 `initialize`，得到 `INITIALIZE_EOF`，stdout/stderr 均为 0 字节，未收到协议响应。该阶段没有自然退出码证据，不能推断是认证拒绝、Job 不兼容或其他特定原因。独立取证核对了原日志及实际 739 字节摘要工件（SHA256 `b7fdeb06badb6e6f5366615eafb7dbca1b9f7cf3780a9aef6873aad1ff0b743f`）。当前仍为 **NOT_READY_FOR_DIRECT_REAL_USE**。

已获准的下一步仍是同一官方版本、无账户的相同 `initialize`，仅增加 EOF 后原总 deadline 内的有界自然退出/Job 计数观察，以及本次新建临时目录内明确日志位置的白名单错误分类。日志仅在本次 owned Job 停止、读取线程结束且清理成功后读取；不扫描账号或配置 store，不跟随链接/重解析点，不导出原始日志、文件名、路径或任意异常文本。分类结果、计数、字节数和摘要不能替代成功协议响应，也不能证明已建立真实认证。禁止凭猜测添加 owner/bootstrap/region 参数、放开 breakaway 或重发请求。

实际结果以精确提交对应的 CI 和独立报告为准。即便 help/initialize 都通过，也不能证明登录继承、模型选择、真实调用、原生工具停止或整链使用无障碍。

## 有界诊断的实际结果

精确提交 `66b7b077ce96bd62e220f0eefc328dc1a40ae83d` 的
[官方 Windows 诊断 CI](https://github.com/meta-xucong/native-harness-api/actions/runs/42405786723)
仍为 `INITIALIZE_EOF`，stdout/stderr 0 字节；新增证据是清理前自然 exit 0、
Job 计数 `[0,0]`，允许的确切日志候选中 files=[]。这不证明其他路径没有日志，
不定位为认证拒绝或事件循环错误，更不是 ACP 接通。30 个诊断 fixture 及
23 个真实 Windows Job 测试通过（另 1 非 Windows guard 跳过）。两套普通
Windows matrix 各 214 PASS / 1 SKIP，Ubuntu 各 196 PASS / 19 SKIP。
独立核对过原日志与 928 字节 summary 工件，SHA256
`decead26ab3eca9e1445e8387cce9045600d39e0cbe79dc6d07a7de371c0934e`。
上述数字只属于该提交，不覆盖后续改动，也不包含账户/模型验收。

## 后续限定实验：外部有限 Node preload

获准的下一项只检验事件循环活性假设：initialize 阶段使用 Node 官方
`--require` 加载本程序新建、摘要固定的 CJS preload，然后仍由 Node
以原官方文件作为 main 执行 `--acp`。preload 仅执行
`setTimeout(() => {}, 45000)`，不改厂商文件、不 require 厂商入口、不改变
process.argv、不截获退出/异常、不读配置或传递 owner 身份。help 命令不变。

依据是固定入口 await 了动态 import，但被导入 bundle 的 business/container
异步生命周期并未被入口 await；没有引用的 Promise 本身不保持 Node 存活。
这只是可检验假设，并未定位实际失败调用链。官方支持依据：
[Node --require](https://nodejs.org/api/cli.html#-r---require-module) 与
[Electron RUN_AS_NODE](https://www.electronjs.org/docs/latest/api/environment-variables#electron_run_as_node)。
这里没有宣称此组合已获 WorkBuddy 厂商支持。

preload 会增加 process.execArgv 和模块缓存状态，可能被 fork/worker 继承，
所以不称“完全无侵入”。timer 有限；观察仍使用原 45 秒总 deadline，成功后
立即按原规则清理，不等待 timer 到期。原 cleanup 等待预算不算额外 initialize
时间。只发一次 initialize，无 direct fallback/重试/第二探测；日志候选与
允许读取范围、输出白名单、权限/额外 RPC/残帧/非零退出失败规则全部不变。
summary 仅增加固定 profile、毫秒数与本程序 preload SHA，区分本轮与旧 direct
结果。自写 Node fixture 不执行厂商程序，只验证 argv/main、有限寿命及显式
exit 不被拦截。缺 Node 明确跳过，不安装其他运行时。

即使本轮 initialize 成功，也只证明此有限无账户组合可以初始化，不能证明
确切内部根因、GUI 登录继承、模型选择或用户可直接使用。未返回响应或超时仍
失败，不能借此追加私有参数、登录、session/new、模型或 prompt。
