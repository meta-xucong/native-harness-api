# Native Harness WorkBuddy Node 版

这是本地派生客户端。运行固定官方 Agent Harness beta16 的 Node 应用，并对官方 Product 配置中的空白 `enterpriseId` 应用一处兼容补丁。完整包包括 Python、Node、Harness、锁定依赖及原生资源，资产身份与原 SFE 版分开。

新版 `workbuddy.public-node.v2` 使用腾讯签名 WorkBuddy 5.11.3 Windows 包中的 SaaS CLI Product，和同一发行物的 WorkBuddy Host 版本及 CLI 2.176.0 身份参数。模型选项由官方 Harness 的会话回读提供。原 `workbuddy.public-node.v1` 配方及其资产校验继续支持原配置与任务恢复。

在 Windows 上完整解压到新目录，先运行 `Client.cmd verify-assets`。双击 `Client.cmd`，输入本工具原 WorkBuddy profile 的完整路径，或执行：

```powershell
.\Client.cmd recover --profile 'D:\原部署\profiles\workbuddy-managed-...'
```

恢复沿用该目录内由 WorkBuddy 保存的登录，官方组件可能续期并写回状态。此前登录未确认的 profile 会在本机终端显示恢复到的账号，再由你确认。成功后可用 `Client.cmd models --profile "<原绝对路径>"` 查看目录。这一步只创建和关闭一个空会话。

恢复会持有原 profile 锁。只允许已确认或原登录结果未知的 profile；处于执行、进程清理未知或上次恢复中断的 profile 需保留现场。若结果提示清理未确认，保留窗口。

已有部署可执行 `Client.cmd start --config "<原 windows-node.json>"`，对原 journal 做一轮恢复。该命令可向原 Bridge 回传已有结果，不取新任务。请保留原 source、ledger、owner、data_dir 和部署身份，避免同时启动第二个 Native Node。

发送前失败的 `PRE_PROMPT_ABORTED` 回执需要配套 Bridge 支持。它沿原任务授权提交，须有会话未申请或已关闭以及受管进程清理证明。缺少这些证明的历史任务仍处于未知状态。

本机失败诊断保存在 `data_dir/native-evidence/sfe-evidence.sqlite` 的 `protocol_diagnostics` 表中。它按执行授权保存首个失败帧的类别、已知方法、数字错误码或缺失的回执字段名。诊断不包含原始帧、提示词或凭据。任务终态仍以原生回执和清理证据为准。

外层帧诊断分为 `invalid_utf8`、`invalid_json`、`invalid_frame_structure` 和 `invalid_jsonrpc_version`，仅保存类别。历史 `invalid_frame` 记录继续保留。

新任务需要由 `Client.cmd models --profile "<原路径>" --target-model "<实际 ID>" --public-alias "workbuddy-..."` 导出的 v2 target。它会产生新的 binding。使用原部署身份及 data_dir 生成匹配的隔离部署配置，并保持准入关闭。获得账号、模型、prompt 和费用许可后，才可显式启动 `Client.cmd node --config "<隔离环境的 windows-node.json>"` 并提交实际模型请求。Ctrl+C 可停止本地 Node 观察。

Windows Named Pipe 可由 `Client.cmd models --named-pipe --profile "<原路径>"` 显式选择。与 `--target-model` 和 `--public-alias` 搭配时，导出使用 `workbuddy.public-node.pipe.v1` 的 target。该 target 用固定版腾讯 `createClient()` 通过本机命名管道连接受管官方 Harness，按官方示例使用一次性的管道 bearer。它沿用原 profile 认证和现有状态机。连接丢失保留未确认状态，无自动重连重发。旧 target 继续按原配方执行，已受理任务继续从原 journal 只读恢复。

既有 task 的恢复继续使用原冻结 binding 与 journal；未知任务仍占原槽。配置说明见同目录的 MULTI-HARNESS-CONFIG.md 和 OPENAI_PROVIDER.md。来源与锁文件见 assets/harness。

该 ZIP 供本机验收。包内保留各组件的许可和原始元数据，Windows sandbox 上游包标记为 `UNLICENSED`。
