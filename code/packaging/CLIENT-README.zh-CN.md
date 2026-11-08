# Native Harness Windows 本地客户端

这是用户自行连接 WorkBuddy 账号的原生客户端，不读取现有 WorkBuddy 桌面登录文件、不提取 token，也不直接访问厂商内部模型接口。模型由官方公开 Harness 程序运行；不要求安装系统 Python/Node。

## 顺序

1. 将整个 ZIP 解压至本地固定磁盘的普通目录。保留 `runtime`、`assets`、`src`、`tools` 的全部文件，不只复制 EXE。不要覆盖已有 profile 或数据库。
2. 命令行运行 `Client.cmd verify-assets`，检查全部固定官方资产。
3. 阅读 `Client.cmd login --explain`，再运行 `Client.cmd login --profile-parent "C:\你的专用账户父目录"`。父目录须已存在；程序会新建受限 profile。由你在官方页面认证，浏览器可能使用默认浏览器已有网站会话，请核对正确账号。程序不会从旧 WorkBuddy 目录迁移凭据。
4. 单独运行 `Client.cmd models --profile "刚创建的profile绝对路径"`。该动作验证当前账号并新建空会话读取模型目录，不发送prompt；完成关闭后才显示可用模型ID。选择一个明确模型ID，不使用auto/default。
5. 复制 `client-config.example.json` 为自己的配置文件，替换所有 `<...>` 占位值。source/ledger/owner/完整binding须与Bridge/Sub2API相同；assets_root指向当前包的assets，profile_root指向刚创建的目录。服务密钥在当前终端以环境变量 `NATIVE_BRIDGE_SOURCE_TOKEN`、`NATIVE_BRIDGE_NODE_TOKEN`、`NATIVE_FACADE_TOKEN` 提供，不写入配置或分享截图；不要填WorkBuddy登录token。确认只接受这一个可信用户后才将accepting_new改为true。登录目录、模型绑定和本地试验服务配置完成后，使用 `Client.cmd node --config "配置绝对路径"`。Facade 可通过 `Client.cmd facade --config "配置绝对路径"` 独立运行。

真实渠道还需要相匹配的 Bridge 与 Sub2API 实现和配置，不能仅把 ZIP 当作已部署的 404token 服务。当前只提供单可信用户、单槽、单 user 文本、非流式、非收费试验，不支持多租户安全隔离，也不保证厂商商业转供许可。需要人工审批的原生工具请求会被拒绝，不自动全权批准。

取消、超时、登录不明、进程清理不明或已有未决 journal 时，不要删目录、重建操作或自动重试。保留现场，只恢复同一任务的已有证据。模型用量未知时不伪造 token 数。

开发测试与用户真实账号/模型验收分开记录。下载包和模拟测试通过本身不证明真实登录、模型输出或 Sub2API 生产端到端已经验收。不要把上游返回的模型自述当作模型绑定证据。

## 第三方许可

完整保留官方 SFE 包的 `assets/sfe/package/LICENSE`（Tencent CodeBuddy MIT）、Node 发行包中的 `LICENSE` 及全部随包文件、Python 的 `runtime/LICENSE.txt`。软件分发许可不等于账号配额可向第三方商业转供；服务条款和账号授权需单独遵守。
