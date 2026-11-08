# WorkBuddy 最简本机启动器

基线：Native `c6b2f45`、Bridge `52dc083`、Sub2API `b25ff6169`，2026-11-08 已用远端分支核实。目标是隐藏重复 CLI 步骤，不增加桌面框架、认证层、调度或安全框架。

## 用户路径

- WorkBuddy 包双击 `Client.cmd`（或 `Client.cmd start`）。Codex 包保留原 CLI，不假装拥有 WorkBuddy 资产。
- 启动器只保存一个 `launcher.json`，内容仅为选定 Node 配置绝对路径。已配置时直接调用原 `runtime node`，**不先登录、读取目录或换 profile**，让原 journal 恢复先发生。缺少 Node token 时只在终端隐藏输入，留在本进程环境，不存盘。
- 首次问是否已有 `windows-node.json`。已有就选择其路径并原样使用，不复制/重写配置、不改资产/profile/data_dir、不生成新部署身份。路径保存前使用原配置加载器验证且限定 NativeTrialConfig/MultiHarnessConfig。已有配置的恢复不依赖当前 profile 已认证。
- 新部署：选择本工具已有 profile 路径，或回车使用包旁 `profiles/` 中唯一保留 profile；多个候选显示列表由用户选，unknown/login_started 等不会被过滤成无候选，选中后拒绝自动重试。确实没有任何保留 profile 时调用现有官方 `login`（保持原确认/身份/持有锁行为）。只扫描本工具 manifest，不读 GUI 目录或凭据；登录失败不再新建/重试。
- 登录成功后直接复用 `discover_models` 一次读取目录（空会话、无 prompt），用户选择确切模型 ID，别名提供默认。用现有 `export_target`，不重复发现或手算指纹。
- 新部署只询问不可推导的 Bridge HTTPS 地址、Sub2API 数字 user/key ID、服务器 Bridge 数据目录；只限从未运行过 Node 的 profile；若 control 已有 journal/native-evidence，要求选择原 Node 配置，不新建身份。用选定 profile/control 作为 Node data_dir，调用现有 v2 `build_plan` 和 `write_plan` 写到固定 `connection/` 目录；再次启动若目录已存在，优先加载完整 windows-node.json，缺失则停止保留现场，不再生成身份。保持全部准入关闭，不替用户部署、开启准入或提交模型请求。
- 配置写完后保存 Node 配置路径并结束，清楚指出三份服务器配置由操作者部署、Node token 由操作者提供，然后双击启动。启动 Node 可执行已授权服务器任务，启动器不会自行创建测试请求。用户自行检查官方账号，并用自己的 Key 明确发最小请求。

## 最小实现

只新增 `packaging/quick_start.py`，由 client 的 start 命令调用。包入口无参数改为 start，仅 WorkBuddy 启用；手动 login/models/node 保留。不新增后台进程、网络端点、重试、watchdog、凭据存储、模型切换器。将新脚本加入现有完整包清单，README 首页改为短路径，旧高级说明保留。

启动器复用现有 profile、资产核验、登录、目录、config parser、runtime 和部署生成函数，不复制其检查。首次 setup 中确认的 profile 必须保留 `authenticated_confirmed`；过期/取消/unknown 不自动换账号、新建 profile 或清理状态。恢复分支绝不以账号/目录检查阻止旧结果回放。调用 discover_models 的 finally 必须复用原工具 RETAINED 持有循环，输出失败也不释放未知 owner。不迁移现有 v1/v2 配置，不猜测旧执行资源目录。

## 验证

- 离线替身覆盖首次登录→一次目录→生成配置、已有 profile、多 profile 选择、登录取消/目录失败、既有 Node 配置直接恢复及 token 不落盘。
- 默认入口与显式 CLI 回归、精确来源打包测试；全量现有离线测试及 demo。
- Windows CI 跑自写 peer、入口/打包验证；不触发官方账号登录或真实模型，不新增 vendor smoke。
- 独立审计设计和最终 diff；推既有 draft PR，确认精确 commit 的 CI 和下载包。

真实登录、账号/积分归属、登录保留时长、真实模型和生产端到端仍由用户验收，开发测试不得冒称已通过。同账号独立官方登录不是复用 WorkBuddy GUI 会话。
