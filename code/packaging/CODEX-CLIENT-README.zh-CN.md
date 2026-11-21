# Codex 本地客户端（Windows x64）

## 默认：直接用你已经登录的官方 Codex CLI

双击 `Client.cmd`，或在本地终端运行：

```bat
Client.cmd start
```

- **已有部署**：选择原 `windows-node.json`。之后直接使用它启动同一个 Node 和原任务记录，不重复连接账号或重建 journal。
- **新部署**：选择电脑上已有的官方 Codex CLI。向导会显示实际 `codex.exe` 和 `CODEX_HOME`，你确认后才检查版本、读取官方账号状态与模型目录。已有登录可用时不用重新授权。默认选官方目录的默认模型，也可更改，然后填写服务器连接信息。
- 生成 `connection` 配置后准入仍关闭，本次不会发送测试 prompt。依 `DEPLOYMENT_CHAIN.zh-CN.md` 配置服务器后再启动；真实模型测试由你自行发起。

首次兼容范围是官方 CLI **0.167.0 / Windows x64 / ChatGPT 账号 / openai provider**。PATH 上常见 npm 安装会定位其官方包布局中的原生 exe；找不到时选实际 `codex.exe`，不会通过 shell 执行未知脚本。版本不匹配会停止，不自动下载、替换或降级你已有的 CLI。

程序与官方 CLI 在同一个 Windows 用户下运行，沿用原来的 `CODEX_HOME`，未设置时用该用户默认 `.codex`。不要以另一个用户或管理员身份启动期待复用当前账号。程序只调用官方 App Server，不读取、复制、迁移或上传 `auth.json`、系统凭据或 token。官方 Codex 自身可能读取、刷新和保存认证。

若未登录或认证失效，请在你正常使用 Codex 的同一环境自行处理官方登录；可手动执行 `codex login status` 排障，然后重新运行向导。向导不会为你发起新登录、登出、推理或自动重试。

## 保留的状态与恢复

本工具的新 `codex-user-cli-*` 目录仅保存自己的连接元数据、工作目录和执行状态，不是第二份 Codex 登录目录；不会修改原 `CODEX_HOME` 的 ACL、配置或凭据。本地 target 冻结实际 exe、哈希、home 和公开身份指纹，服务器只拿原有 Binding，不接收这些本机详情。

已有 Node 配置可显式选择：

```bat
Client.cmd start --config "C:\原部署目录\windows-node.json"
```

升级 CLI、改账号或改 home 后需建立新 target，不能替换旧任务的身份。保留原 Node `data_dir`、`deployment-identities.json` 和 journal。已接受 WorkBuddy/旧 managed Codex 任务仍按原记录只读恢复，不能删除状态后重跑，也不要同时启动第二个 Node。

## 旧独立登录模式：仅显式兼容

以前创建的 `codex-managed-*` profile 仍可使用原命令，不会自动迁移到用户 home。它与新默认模式不同：

```bat
Client.cmd verify-assets
Client.cmd login --profile-parent C:\NativeProfiles
Client.cmd models --profile-root C:\NativeProfiles\codex-managed-<实际ID>
```

`login` 明确创建新的独立 profile 并走官方设备登录，**不是默认已有 CLI 连接流程**。`models` 的 target 导出参数和旧配置保留，详见 `MULTI-HARNESS-CONFIG.md`。取消不等于退出账号或撤销登录。包内固定 App Server 资产只为这条旧兼容路径保留，新默认流程不偷偷换用它。

## 支持边界

继承现有 home 也会继承可能适用的用户配置、MCP、skills、插件和企业策略。现有 read-only/on-request 设置不等于完全无工具、无网络或 OS 沙箱；工具审批不会自动放行。运行中不要切换该 CLI 账号。本工具不接管你已经打开的 Codex 对话，不保证继承那个窗口的模型或思考强度。

回答文本与任务状态仍按既有投影输出，commentary 仅显示运行状态，不是完整 Codex UI 镜像。真实官方启动、已有登录复用、账号额度、模型调用及生产部署仍需本地验收；离线测试和文件哈希不是这几项的通过证据。

**官方 App Server 认证文档明确不允许将该认证用于商业或托管服务。** 本机复用登录、软件开源或技术可调用，不代表允许把账号能力商业转供给404token用户。应取得适用官方许可或改用允许该用途的正式供应路径；确认向导不会豁免此限制。见 [官方认证说明](https://learn.chatgpt.com/docs/app-server#auth-endpoints)。

## 官方许可证

本包 `licenses/codex/LICENSE` 和 `licenses/codex/NOTICE` 保留 OpenAI Codex 官方文本原始字节，来源为固定版本 `rust-v0.167.0` 对应提交 `086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a`：

- https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/LICENSE
- https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/NOTICE

这些是软件分发许可文本，不代表 ChatGPT 账号服务转售授权。
