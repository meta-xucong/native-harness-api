# 官方 WorkBuddy 入口证据与无账户 CI

2026-11-07。这里只核验官方发行物和受限启动面，不把内部实现当作对外稳定 API，不证明用户登录、模型调用或完整 404token 上游已可用。

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

工具及 fixture 单测分别位于 `tools/official_workbuddy_smoke.py`、`tests/test_official_workbuddy_smoke.py`。fixture 负例通过不代表厂商入口通过。初次源码冻结时厂商程序尚未执行；实际结果以对应精确提交的专用 CI 和独立报告为准。即便 help/initialize 真正通过，也不能证明登录继承、模型选择、真实调用、原生工具停止或整链使用无障碍。
