# 双协议实时流与原生事件合同

标准路径不再使用 buffered-final SSE。缺省 openai-append-v1 将可追加的原生快照
映射为受限 OpenAI choices.delta；显式选择 native-events-v1 才输出完整快照及状态。
这不是原始模型 token 流，也不代表完整 OpenAI API 兼容。
只支持既有单可信用户、未知用量不收费、固定模型绑定和唯一 Bridge 账本边界。
未验证真实账号，不以离线事件测试宣称实际 WorkBuddy 行为已验收。

## 来源与时序

Node 只发布经官方 prompt ACK 归属核验后的 root 消息投影：

- `text_snapshot {text}`：**替换**全部已显示的 root 文本，可缩短、覆盖或清空。
  并非 append，不可把每次快照再次拼接。消息更正是事实，不是失败重生成。
- `native_status {state, stop_reason?}`：running / requires_action / idle / unknown。
  stop_reason 为 end_turn / refusal / cancelled / `_codebuddy.ai/error`。
  idle、end_turn 或本地连接断开都不单独证明任务已完成/进程已退出。
- Bridge 另按真实账本事务写 `task_status {outcome,liveness,code?}` 和唯一
  `task_terminal`。只有终态事件经 Facade 重新读取、验证同任务的原生完成证据，
  才投影为 `completion`（完整受限 Chat Completion）或 `task_error {code,status}`。

所有事件的 seq 来自 Bridge 持久日志，按任务连续递增。原始工具/凭据/路径字段、任意
vendor对象及非本任务消息不直接透传；模型输出正文不保证自动脱敏，仍可能包含用户
输入或模型生成的敏感内容。工具执行不会伪造成客户端待执行 tool_calls。

## 内部分页与身份

Facade `POST /v1/native-harness/events` 使用与非流式请求完全相同的规范 JSON
正文、冻结模型摘要和 X-Native 身份头，新增单个 `X-Native-Event-Cursor: N`。
stream 在摘要前规范为 false；模式切换不会产生第二个任务。

Bridge `GET /bridge/v1/tasks/{task_id}/harness-events?after=N&limit=8` 为不授予新执行、不创建任务的观察接口；
可按账本规则记录过期或 unknown 活性检查点。
响应字段严格为：

```
{schema_version: "native-events-v1", remote_task_id,
 events: [{seq, type, data}], next_cursor, has_more, terminal_seq: null | integer}
```

页数及字节均有限；task_terminal 独占一页，避免其完成结果投影挤出字节预算。
终态后日志不再增长。缺少旧历史、日志过期、cursor超前、日志配额耗尽都明确报错，
不补写虚构历史、不新执行、不默默丢帧。既有无事件日志的历史任务仍可非流式回放。

## 下游 Sub2 SSE 与恢复

专用 Key 的标准 `/v1/chat/completions` 配置开启后，`stream:true` 缺省选择
`openai-append-v1`，返回普通 `data:` SSE、`chat.completion.chunk` / `choices.delta`。
仅当前快照以已发文本为前缀时发送新增后缀；只有最终结果验证且与已发内容一致，
才发送结束帧及 `[DONE]`。缩短、改写或最终不一致时发送
`NATIVE_STREAM_REWRITE_REQUIRED` 标准 error 后关闭，不发送成功终止帧。
已显示文本只是临时内容，错误后需原 Key/正文/Idempotency-Key 改 `stream:false`
恢复权威结果，不自动重生任务。切换 stream:false 时须移除显式
X-Native-Stream-Protocol 头，否则返回 STREAM_PROTOCOL_REQUIRES_STREAM。普通 SDK 需显式提供稳定幂等头，建议关闭自动重试。
标准流的任务状态仅为 SSE 注释，普通 SDK 通常忽略，不能宣称它显示审批/unknown。
标准流只允许 `Last-Event-ID:0` 从头只读恢复；正游标在准入前拒绝。
未知 usage 不造假，stream_options 不支持，仍仅单 user 纯文本。

需要完整原生状态、快照更正或正游标恢复时，显式发送请求头
`X-Native-Stream-Protocol: native-events-v1`，响应
`Content-Type: text/event-stream` 与 `X-Native-Stream-Mode: native-events-v1`。
以下命名事件和正游标规则仅适用于此显式模式。
每条 durable 事件编码成 `id: seq`、`event: type`、`data: JSON(data)`，立即 flush。
内容以 text_snapshot 和最终 completion 的完整结果为权威；不发送伪造 token delta
或 usage。不要把此流交给仅认识 OpenAI choices.delta 的客户端。

客户端收到事件并应用后保存其 id。重连必须保持同 Key、同正文、同
Idempotency-Key，携带 `Last-Event-ID`。即使该头为0也禁止创建缺失任务；丢失游标时应带 Last-Event-ID:0。
不带该头的新HTTP请求会重新检查准入，仍由原操作键与Bridge唯一任务去重，
不等于执行只读恢复。
首次请求之后的内部观察一律禁止准入；已受理错误或断线不能切号/换键重生。
重复连接可能重放客户端尚未确认的事件，客户端按 id 去重。传输不能保证客户端
网络收包 exactly-once，但账本不因此产生第二次执行。

`: observing` 为连接保活注释，不代表 WorkBuddy 正在产出。
`observation_error {code,status,task_cancelled:false}` 不带 id，仅表示本次观察中断。
`observation_end {reason:"terminal_cursor_reached"}` 不带 id，表示已读到终态游标；
当从最终 id 恢复时不会再次复制 completion。普通 EOF 不等于成功。

总观察预算为 standard_wait_seconds（stream模式须1–300秒，默认180）；单次
HTTP写最多5秒且不超过总/请求deadline。断开只停止观察，不取消原生任务。
审批等待、unknown活性可通过状态事件及时看到；本流不替用户审批或宣称已停止。
反向代理必须禁用缓冲并支持SSE；不支持有界流写的transport拒绝准入。
