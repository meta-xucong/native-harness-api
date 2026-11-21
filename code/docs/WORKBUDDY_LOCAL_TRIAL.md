# WorkBuddy 本机一次性真实使用试验

本包会在用户本机启动用户明确指定的 WorkBuddy 安装目录内的 bundled runtime，完成 ACP 初始化、模型选择回读和用户确认的一次 prompt。它不是 FakeHarness，也不把 CodeBuddy 独立安装版换名为 WorkBuddy；真实返回的 agent 名称/version 原样按安全字段展示。如果官方 bundle 在用户版本中不存在、不支持相应 ACP 能力或不能使用原生登录状态，程序停止并导出阻断，不能从账号文件补取 token。

## 下载后

解压完整 Windows x64 ZIP，核对随交付提供的 SHA256，再看包内 `README.zh-CN.md`。私有 Python 不需安装，不需管理员权限，不需 pip/PostgreSQL/Redis，不运行已有生产 Bridge 脚本。WorkBuddy 本体不随包提供，仍由用户自行从官方来源安装和登录。

1. `Probe.cmd setup --config "C:\your\workbuddy-trial.json"` 创建本机配置。填写实际 `WorkBuddy.exe` 和其官方 bundled CLI entry，选择新专用 workspace/data_dir。
2. `Probe.cmd doctor --config "C:\your\workbuddy-trial.json"` 只做静态检查；加 `--launch` 才启动原生进程并创建空 ACP 会话，不发 prompt。
3. 从诊断显示的实际候选 ID 设置 `model_id`，不能用 auto/default/占位值。只有模型与 default 权限模式可靠回读才允许发送。
4. 准备非敏感 UTF-8 prompt 文件，运行 `Probe.cmd run --config "C:\your\workbuddy-trial.json" --prompt-file "C:\your\prompt.txt"`，阅读费用/工具警告并输入 `RUN REAL WORKBUDDY`。首测建议短文本，不请求工具操作。
5. 原生程序发出审批时逐次询问，只能本次允许或拒绝，不自动永久允许。原生已有 allow 规则仍生效，不保证每工具都询问；default 不是沙箱。
6. `inspect` 读取同一状态；`export --output "C:\your\summary.json"` 仅导出白名单状态和摘要，不含 prompt/答案/原始事件/绝对路径，也不照搬原生 name/version/model 任意文本。分享前再人工检查。doctor 的本机显示不是默认脱敏导出。

## 保守恢复

每个 state 根只允许一次真实 prompt。相同命令再次执行只回读；不同 prompt/model 被拒；记录 intent 后超时、断线或退出均不重连、不 resume、不重发。不要删除数据库、标记、换路径或解压新副本绕过未知执行。没有 reset 命令。相应原生进程可能仍有工具/后台子进程，关闭客户端不等于全部停止。

state 与 workspace、原生安装/配置目录的相等或祖先重叠一律拒绝，避免正常工具清理或软件更新删除证据。Windows 按实际路径规范化与大小写语义判断，并拒绝目录链接；不承诺抵御拥有本机文件权限的恶意用户同时更换全部路径/文件。

`native_turn_returned` 只是 ACP 协议返回；只有 `end_turn` 和非空文本被标为文本轮次完成。状态始终不声称后台活性已停止，也不伪造 token 用量。模型回读仅证明原生会话配置，不证明 provider 级实际型号或子 Agent 全部用同一模型。

发送前和持久 intent 后检查已经到达的控制更新，模式/模型变更或证据删除即失败；stdio 不提供跨进程原子配置围栏，尚在飞行中的异步修改不能完全排序。试验时不要从其他窗口修改同一会话或原生全局配置。

原生文件路径、哈希和服务端自报身份不能代替官方发行签名核验；仅使用用户已从官方渠道安装的可信 WorkBuddy。程序不自动安装、更新、授权 OAuth、读取账号配置内容、提取 sidecar password 或请求内部模型 API。

## 与已有三仓的关系

原 P1b 的真实 Bridge HTTP 和 Sub2API 专用 synthetic 接缝保持原门禁，未因本包而放开真实模型。这个直接本机 Adapter 验证工具不对外监听、不接网络任务，也不是另一套生产派发器。等本机模型/权限/终态证据满足蓝图，再单独审计接入 Bridge。PostgreSQL、普通 CC account-channel、收费、生产部署与商业转供权限仍未验收。

本次官方入口证据与边界见 [P2 补充蓝图](../../blueprint/P2-LOCAL-TRIAL-ADDENDUM.md)。云端 mock/stdio/Windows 解释器测试不能替代用户真实账号运行。
