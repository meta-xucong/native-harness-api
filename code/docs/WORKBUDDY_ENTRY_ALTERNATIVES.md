# WorkBuddy 真实本地入口比较

2026-11-07，只读官方文档及固定开源版本核查。未调用账户 API、注册应用、
取得 OAuth 授权、运行社区程序或访问用户电脑。结论是开发选型，不是实机验收。

## 官方 Local Assistant：确为本地运行，但不是模型 API

[Open API](https://open.workbuddy.cn/docs/openapi) 和
[第三方应用](https://open.workbuddy.cn/docs/third-party-app) 明确：
`localassistant` 驱动用户电脑上的 WorkBuddy，区别于 `/tasks` 云端沙箱。
公开接口为 GET 在线状态；POST message(content,msg_type) 返回 message_id；
GET message 历史/增量；permission_response 用于审批答复。

公开合同没有模型指定/可靠回报、目标电脑/独立会话选择、当前请求终态、取消、
幂等键或执行活性证明。metadata 任意对象不是已定义模型证据。online 不是
任务结束，message_id 不是完成证明，停止历史轮询不等于取消原生任务。
[助理说明](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Assistant)
描述单共享且保留历史的会话；仅串行本程序的发送，仍不能排除桌面/IM 交错。
手机 UI 支持选电脑、模型和停止，不证明第三方 API 暴露同样参数。

因此它可成为诚实的异步本地消息 relay，但连单请求都不能在公开合同上保证
独立归属/结束/精确模型。不能把后续 assistant 文本包装成普通 CC 成功响应。
官方接入还需要审核启用的应用、用户 OAuth 同意和服务端 client_secret；
不能把 secret 放到下载包，也不能用桌面账号文件代替官方授权。

本项目不优先开发这个无法满足原目标的新 OAuth 服务，也不把注册/授权工作
转给用户当作所谓已解决。若未来增加 relay，它必须是独立能力，不进入模型准入。

## 官方 CLI/ACP 与 HTTP serve

[ACP](https://www.workbuddy.cn/docs/cli/acp)、
[HTTP API](https://www.workbuddy.cn/docs/cli/http-api) 的本地 `--serve`
提供 ACP HTTP/SSE 和 runs/status/cancel 等入口，是合法备用传输候选。
这些 CodeBuddy CLI 文档不自动证明 WorkBuddy GUI 的登录继承或私有侧车可被接管。
只能使用明确授权启动的 endpoint，不能抓取 GUI sidecar 密码、伪造 owner
bootstrap、使用私有 internal RPC 或关闭认证来绕过缺口。HTTP 也不会自动
修复模型来源、提交响应丢失和外部工作停止的问题。

保留最小的官方 bundled-runtime + stdio ACP 路线及严格门禁，继续独立实现
受监督进程、逐任务防重证据与真实协议兼容组件。实际官方 initialize 仍
失败，不以增加外围代码掩盖它。详情见 [官方入口证据](OFFICIAL_WORKBUDDY_ENTRY.md)。

## 公开项目的实际机制

- [Ianzhyh/workbuddy-to-dsh](https://github.com/Ianzhyh/workbuddy-to-dsh/tree/790f34e1dd9caae816515b50a6efe73cd40515ea)：
  核对 bridge/workbuddy-bridge.mjs，实际读取桌面 OAuth 并直连内部模型接口。
  不是原生 Agent/工具审批执行；不采用凭据抽取或该路由。
- [ywu73/codex-workbuddy-subagent](https://github.com/ywu73/codex-workbuddy-subagent/tree/c595e88756db9e745027167bd29a3b8062028857)：
  native-provider.ts 确有 spawn bundled CLI 的 stdio ACP。可参考组合，但其
  响应使用请求的 model，不能充当独立 reported 证据；角色拼接、权限模式和
  host 信号关闭也不等于本项目合同。未复制或运行外部代码。
- [jie023/workbuddy-acp-bridge](https://github.com/jie023/workbuddy-acp-bridge/tree/e2327499273efa093ebdcbffde9a4c8833e26e76)：
  核对版本通过本机 session discovery 文件找 HTTP ACP；未见提取账号 token
  的代码，但也未建立当前 GUI 密码网关的授权/所有权保证。不得把发现地址当
  认证许可，不能把 session 恢复当成安全重发。
- OpenWorkBuddy 是独立/BYOK 产品，不是腾讯 WorkBuddy 替代证据；社区开放平台
  devkit 不会扩展官方 API 合同。云沙箱实现不满足本地原生 harness 目标。

## 不可通过命名消除的验收条件

最终需要官方支持且已登录的原生运行上下文，及获准的一次会话/模型/提示验证。
当前无账户云端 CI 无法证明它们；应用、账号、模型费用、真实工具行为必须分别
授权并实际测试。现在不要求用户运行探针，也不称“全部开发完毕、没有障碍”。
数据库、整链普通 CC、服务配对与部署缺口另见 [就绪差距](REAL_READINESS_GAPS.md)。
