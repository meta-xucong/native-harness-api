# WorkBuddy 登录入口：先预演，不碰真实账号

`tools/workbuddy_login.py` 是独立的无凭据 CLI 向导。本阶段只有真实可运行的
**模拟模式**，没有原生登录 backend。`--mode native` 在创建目录前硬拒绝，
任何模拟确认、环境变量或成功结果都不能放开它。不要把这份预演当成已完成
真实登录、凭据保护、用户本机兼容或模型验收。

## 立即试用

在 `code/` 中，以已安装的 Python 运行。`--profile-parent` 必须是已有、绝对、
规范路径的受信本地普通目录，不能是符号链接或 junction。请选择专用于本次预演的
空父目录，不要选择 WorkBuddy、浏览器或其他现有账号目录。程序只新建随机
命名的 `native-login-sim-<id>` 子目录，不会读取、导入或复用父目录中的账号数据。

```text
python tools/workbuddy_login.py --explain-real-test
python tools/workbuddy_login.py --mode simulate --profile-parent /absolute/path/to/new-simulation-parent
```

交互过程中逐项输入 `yes` / `no`，只确认固定虚构身份
`synthetic-test-account`。**不要输入真实账号、密码、验证码、令牌、cookie
或登录链接。** 向导不会打开浏览器。人工认证步骤只是未来流程的预演。

开发者可用固定脚本场景测试界面和目录行为：

```text
python tools/workbuddy_login.py --mode simulate --scenario cancel-race --scripted --json --profile-parent /absolute/path/to/new-simulation-parent
python tools/workbuddy_login.py --mode simulate --scenario cancel-before-start --scripted --json
```

可选场景：`success`、`cancel-before-start`、`cancel-pending`、`cancel-race`、
`timeout`、`existing-state`、`protected-state`、`identity-rejected`。
`--scripted` 只使用固定的模拟确认，**不是用户对真实操作的预授权**。退出码 0
仅表示已完成所选预演，不表示登录成功。拒绝已有/受保护状态的场景不打开任何
实际旧目录，也不创建 profile。

## 已实现的安全交互

1. 先显示服务、虚构账号、浏览器隔离限制、存储保护限制、取消不是回滚、本人
   手动认证以及不执行模型等七项说明。全部确认后才创建专用模拟目录。
2. 登录 ACK、账号回读、status 回读、本人确认虚构身份是独立步骤。仅 ACK
   不能完成流程。
3. 取消发送不等于请求结算。模拟对端另行返回错误后，才能回读取消结果。
   取消时账号已出现的竞态明确显示“可能已完成”，保留目录，不继续使用账号。
4. 超时、矛盾、乱序或未知状态停止流程并保留目录；不自动重试、登出或删除。
   “quarantine”只是本工具拒绝复用的逻辑状态，不是 OS 安全隔离。
5. 成功、取消、拒绝身份、关闭、重复操作与迟到事件均保持模拟标志；
   `real_account_verified`、`real_model_ready`、`native_cleanup_verified` 永远 false。

## 实际写入什么

只有新建模拟目录会发生文件系统写入：七个空子目录
`home/config/appdata/localappdata/temp/workspace/output`，固定模拟清单
`simulation-profile.json`，以及完成时的 `output/simulation-result.json`。
结果仅含随机 profile ID、有限模拟结果和固定布尔标志，不含身份、凭据或 URL。
终端 JSON 摘要不含绝对路径；交互输出会告知用户自己的新目录位置。

创建使用排他方式；再次运行会创建新的随机子目录，而不是复用旧 profile。
清单、目录身份、文件单链接及预期空目录在写结果前再次校验。遇到目标名称已存在、链接、
被替换、清单变更或非预期文件时拒绝，保留现场，不读取非预期文件内容。
创建中断也保留可能留下的部分新目录，不递归删除或自行修复。没有 reopen、
resume、删除、原生环境生成或凭据读取 API。

新目录/文件请求 POSIX 0700/0600 权限；这不构成跨平台保护证明。尤其没有验证
Windows ACL、浏览器 cookies 隔离或原生凭据加密。元数据中的相关验证标志
始终 false。程序没有应用层网络客户端、浏览器、vendor import 或子进程执行路径；
但文件系统访问本身可能产生网络 IO，不能据此声称网络已隔离。Windows 在访问目录
元数据前拒绝 UNC、设备命名空间及非 fixed drive；POSIX 拒绝双斜杠根路径。
随后从祖先到子目录检查链接/reparse；这仍无法证明底层挂载一定在本地，不能使用
网络盘、远程挂载或会触发外部访问的文件系统。Python 正常加载源码与创建模拟文件
仍是实际 IO，不能称完全无副作用。
路径身份复核不是 OS 沙箱，也不能保证抵御可并发修改父目录的恶意进程。
只在受信、专用父目录中预演；本模块不是生产凭据存储器，不可用于真实凭据。

## 用户以后如何手动认证

真实模式尚未接入，以下是**准备与授权清单，不是现在可执行的登录指令**：

1. 核验完整官方来源、版本和区域。除早期 CN WorkBuddy 5.9.3 / beta15 桌面
   库诊断外，固定官方 public beta16 SFE 配合 CN 5.9.3 Product 已通过云端
   匿名入口，实际完成 Product/catalog/account/status 与自然退出。完整 pins、
   run 和范围见 [公开 SFE 诊断证据](PUBLIC_HARNESS_CATALOG_DIAGNOSTIC.md)。
   这不是登录、GUI 恢复、国际版或本机兼容证明，不能只移植几份库来冒充完整版本。
2. 用户明确选择并批准具体测试账号与官方服务。使用独立、已核验的 OS/浏览器
   测试会话，不能复制现有账号目录、浏览器 cookies、tokens 或保护密钥。
3. 用户明确批准新绝对存储路径、保留期限及实际保护方式。必须先核验 Windows
   ACL/存储保护；不能把一个空文件夹或确认勾选当作凭据已加密的证明。
4. 用户另行批准发起原生登录请求、打开浏览器及可能的账号持久化。本人只在
   经核实的官方页面手动完成认证，凭据不进入终端/聊天/日志，不粘贴登录链接。
5. 认证后须独立回读账号与 status 并由本人核对身份。取消时仍要核验结算；
   未知或已出现账号则保留专用目录，不自动登出、删除、重试或启动模型。
6. 真实模型、费用、工具权限与本机测试另行明确授权。没有登录后的自动推理。

**必须保留的限制：** 原生 provider 可以先打开系统默认浏览器，再出现外层
elicitation。Windows 使用系统 URL dispatch，没有已验证的独立浏览器 profile
参数。只设置 HOME 或使用 OwnedWindowsJob 不能保证浏览器 cookie 隔离、把已
打开的浏览器纳入 owned Job 或关闭外部窗口。取消 elicitation 也不能撤销先前的
请求/浏览器动作或已持久化登录。

现有 GUI 受保护账号没有已验证的公开交接契约；不能注入私有 bootstrap、伪造
宿主身份或降级保护。旧桌面 beta15 provider 的依赖装配问题不能概括所有发布包；
公开 beta16 源码在已识别的缺失依赖点使用不同路径，且其 SFE 匿名入口已实测。
但原生 fresh-account 完整登录仍未执行，源码差异和空账号成功都不等于登录已可用。
本向导继续拒绝 native 模式，不会自动改用新 SFE 或创建真实凭据目录。

官方桌面登录指引：
https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Installation-Win-Guide

官方 Open API/OAuth 是另一种应用集成契约，不是已证明的 beta15 GUI 凭据交接：
https://open.workbuddy.cn/en/docs/openapi

## 测试与发布范围

```text
PYTHONPATH=src python -m unittest discover -s tests -p 'test*login*.py' -v
PYTHONPATH=src python -m unittest discover -s tests -v
```

本批新增文件只允许普通 vendor-free 源码 CI，不修改已有 vendor-trigger workflow。
真实账户、原生登录、模型或本机运行仍未获本预演授权。不可把这里的模拟 profile
直接交给未来原生 backend；真实存储必须在独立审核和用户授权后新建。
