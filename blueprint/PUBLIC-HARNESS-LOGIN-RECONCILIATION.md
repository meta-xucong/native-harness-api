# 固定 beta16 登录交互与错误后结算

本增量仅修复独立 WorkBuddy 登录入口，不改变 Provider、Bridge、Node、模型适配器或原任务恢复。
基线为 `c831bf09e717269ae6059243db57edc3873f51ed`，保持原固定官方资产。

## 已核对的官方行为

来源：官方 npm `@tencent-ai/agent-harness@0.2.0-beta.16` 的
`lib/runtime/codebuddy-auth.js`、`lib/server/errors.js`、`lib/config/notifications.js`。
当前固定 Windows SFE 中内嵌的对应实现具有相同控制流程。

- `awaitManagedLogin` 独立观察浏览器登录；结束时撤销 URL 请求并发送 completion，
  无需先收到终端交接确认。匹配的取消/完成只关闭该交互，仍由明确账号/状态回读证明认证。
- 登录成功后的账号/Product 提交可能抛错并启动后台 reconciliation；RPC error 不是
  该后台过程已终止的证据，单次未认证快照也不是安全重试证明。
- 非结构化异常统一映射为 `-32603 / Internal error`，不带原错误 data；不可凭此猜测网络或账号原因。

## 最小实现合同

1. 保持单进程、同次登录；错误后最多观察 30 秒、15 对 account/status，间隔 2 秒，
   总登录期限、64 帧与原 IO 限制继续有效。没有再次发送 auth/login 的路径。
2. 跨两次读的账号变化要求重新取得一致快照；通知、ACK、暂时 false 均不代替最终判定。
   到期为 unknown，持久保留未知 profile，不自动复用或新建。
3. 成功仍要求本地身份确认、自然退出、owned cleanup 和完整 profile 校验。
4. 清理已证但完整 profile 校验失败时，仅在隔离控制记录持久写入、摘要撤销并复核后释放锁退出。
   不放宽 ACL，不恢复 unknown；清理或控制记录未证时仍保留现场。
5. 登录入口不声明未实现的 config.warning 订阅；新增诊断仅含布尔时序、有限计数及固定状态。

## 验收

源代码对应的合成时序必须能使旧版测试失败、新版通过。包括未答交接即完成、
error 后 false/false 再提交、两次读之间提交、持续错误到 unknown、取消与 EOF。
Windows 运行测试应使用真实 Job、管道、独立合成 profile、ACL 与锁；不依赖真实登录。
完整回归及独立审计之后才交用户实测，模拟不证明官方后台/真实账号可用。

实现与证据索引见 [登录审计](../code/docs/PUBLIC_HARNESS_LOGIN_AUDIT.md)。
