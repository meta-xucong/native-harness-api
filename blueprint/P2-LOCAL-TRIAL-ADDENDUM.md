# P2 本机 WorkBuddy 试验包边界

2026-11-07。用户要求继续至可下载到本地进行真实 WorkBuddy 使用测试。本补充不更改审定 v0.2 的正式渠道准入门槛，也不将 P1b 的 synthetic 分支改名为真实模型通道。

## 可执行交付

- 提供 Windows x64 私有 Python + stdlib-only 试验程序。WorkBuddy 本体不随包分发，用户明确指定已安装的官方 WorkBuddy executable 和 bundled CLI entry。
- 官方发布说明确认 WorkBuddy 专属 `codebuddy-lite-wb` 产物；官方 CLI 文档提供 stdio ACP。此证据不等于任意版本 bundle 的独立启动、登录继承或工作台 UI 集成已经受支持或验收。
- `doctor` 静态检查文件、配置与包；`doctor --launch` 启动用户指定原生 bundle，只做 ACP 初始化及空会话能力探测，不提交 prompt。
- `run` 必须由本机用户明确确认费用和本地工具访问，设置并回读实际会话模型后只提交一次 prompt。保留原生工具，只对原生程序实际发出的审批逐次询问，不自动选择全权限或永久批准。已有原生 allow 规则仍然生效，不能保证每次工具操作都询问。
- `inspect` 重读同一试验的本地证据；`export` 只输出脱敏摘要，默认不含 prompt、答案、原始协议事件、凭据或绝对机器路径。

## 认证、执行与权限

程序不读取或复制 WorkBuddy 账号凭据，不捕获侧车日志/password，不连接内部模型端点，不自动 OAuth、authenticate、下载 WorkBuddy 或安装插件。厂商程序只使用它自己被用户选择的本机配置；若无法继承有效登录，返回明确阻断，让用户在官方应用完成登录，不从其他产品搬运账号状态。

WorkBuddy OpenAPI 的本地助理入口虽为真实 PC 消息通道，但目前公开合同缺按请求选模、独立会话、幂等和可靠终态；云端 tasks/ACP 不是本机回退。因此本包优先实验 WorkBuddy bundled stdio runtime，不把 OpenAPI 消息历史冒充完整 Chat Completions。

每次原生启动均使用参数数组、不开 shell、只传必需 OS 环境和明确选择的原生配置目录。完整账号状态由原生程序管理，不能承诺独立目录构成安全沙箱。只在新建专用工作目录进行首测。显式启动 default 并要求实际回读为 default；plan 会继承此前权限基线，首测不接受。发送前和 intent 提交后处理已收到的控制通知，再验证模型和模式；这不是跨进程原子屏障，无法绝对判断尚在飞行中的异步修改顺序。

## 不重复执行与结果语义

试验包不是第二个生产派发器，也不接受网络客户端。正式 Facade/Bridge/Sub2API 的 synthetic 门禁保持不变。

一个配置数据根只接受一次真实 prompt。跨进程 OS 锁覆盖检查、原生会话、intent、prompt 和证据持久化；intent 在唯一发送前 fsync/数据库提交。重复命令只读原证据，不再生成。intent 后断线、超时、进程退出、机器重启均保留未知状态，不自动重连、resume 或重发，没有 reset/强制释放命令。

ACP prompt 返回只证明相应原生 turn 的协议结束。它不证明后台工具全部停止；程序分别记录 `stop_reason`、非空输出、原生配置 readback 以及 host 退出事实，不生成 `stopped_confirmed` 或虚构 token usage。没有真实模型运行记录时不将配置值当独立模型溯源。每个状态根只有一次试验，防止这些未证门槛被循环准入绕过。

## 测试和验收

云端测试使用明确的 stdio 协议 fixture，不触发真实推理。覆盖真实子进程传输、模型候选/回读不一致、错 session/id、超长/畸形消息、审批不自动永久允许、超时/EOF、intent 持久化失败、重复命令、跨进程锁、损坏状态、导出不含内容/路径、打包 allowlist/hash/路径遍历、Windows 私有解释器离线启动。

独立审计后发布源码和带 SHA256 的可下载测试包。真实 WorkBuddy、用户版本/登录、模型实际执行、工具/取消、PostgreSQL 和正式固定模型渠道仍需本机独立验收。默认不合并、不部署、不收费；包可运行真实试验不等于已获商业转供授权。

## 来源

- [WorkBuddy OpenAPI](https://open.workbuddy.cn/docs/openapi)
- [WorkBuddy 专属精简 CLI 产物发布说明](https://www.workbuddy.cn/docs/cli/release-notes/v2.143.0)
- [官方 ACP 协议](https://www.codebuddy.cn/docs/cli/acp)
- [官方 CLI 参考](https://www.codebuddy.cn/docs/cli/cli-reference)
- [WorkBuddy 模型配置](https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Model)

以上最后核验于 2026-11-07。官方资料与本次实现证据分开记录；第三方社区实现仅作风险线索，未复制其凭据发现或自动批准实现。
