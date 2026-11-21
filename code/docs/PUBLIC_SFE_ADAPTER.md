# Public SFE 本地执行证据与资产接缝

`PublicSfeAdapter` 接收 Bridge Runner 已授权的 task/grant 和显式 `invoke(model_id, messages)`。调用者负责正式资产、登录 profile、实时账户身份及执行权限；本模块不自行发现、登录、批准工具或启用生产渠道。

首版只接收一条 user 文本消息，不将多角色聊天悄悄拼成 prompt。单任务及单 grant 在 SQLite 中唯一，FULL 同步事务提交 intent 后才调用原生 owner。该文件只保存不可重复执行证据及脱敏结果，不是派发账本或队列。重启、同键并发、失败与未知结果均不能再次执行。reconcile 只读现有记录。未知状态没有自动解锁/清理功能。

终态要求公开 SFE protocol/account/model 证据、prompt ACK、匹配终态以及完整 owned 退出清理同时成立。否则返回 pending/unknown，而非伪造失败或重试许可。未知用量不填零。模型结果仅保存 text/refusal。目录由调用者在专用受保护 profile 中创建；拒绝已存在的 reparse/硬链接/非普通数据库和 sidecar。此检查不是对可并发改写该目录的攻击者的文件系统沙箱。

WorkBuddy 对应请求的 JSON-RPC 错误记为 `rpc_error`。`$/cancel_request` 通知指向对端发出的 RPC，当前客户端同步处理反向 RPC，无需回包。该通知不撤销本端同编号的 prompt。提示词回执缺少 `messageId`、`_meta` 或 `_meta.codebuddy.ai/requestId` 时单独记为 `prompt_ack_missing_fields`，等待证据不足的任务保持未知。

首个失败帧的脱敏摘要按 grant 写入本机 `native-evidence/sfe-evidence.sqlite` 的 `protocol_diagnostics` 表。只保存固定的类别、方法、数字错误码或缺失字段名。原始帧、异常、错误消息、`error.data`、原生 ID 和认证数据均不会写入该表。摘要供本机排障，任务恢复仍按原结果、journal 和授权进行。

外层帧在进入请求关联前失败时，只保存类别。无法解码的字节为 `invalid_utf8`，空行或 JSON 解析失败为 `invalid_json`，非对象、重复键或当前帧合同无法表示的值为 `invalid_frame_structure`。对象的 `jsonrpc` 缺失或值不等于字符串 `2.0` 时为 `invalid_jsonrpc_version`。历史 `invalid_frame` 依原样保留，新的类别不改变失败、清理或恢复规则。

WorkBuddy Node 的发送前终止单独保存为 `PRE_PROMPT_ABORTED`。同一次已授权调用必须确认尚无 prompt 意图、未构造 turn、原程序自然零退出、受监督进程及 IO 已清理。还要确认没有发出建会话意图，或已收到对应会话的关闭回执。仅有空 session ID 时仍可能丢失建会话回执。完整结果随原 task/grant 意图行写入 SQLite，Node 沿既有回执与 ACK 流程恢复。旧 `pending` 记录及独立诊断日志保持原样。

`session/new` 超时后，WorkBuddy Node 在原清理时限内给同一连接上的匹配回执留出一部分时间。迟到回执只用于取回会话 ID 并发送一次 `session/close`。只有关闭回执和受管进程清理全部确认时才提交发送前终止。任务保留超时结果，后续不进行模型选择或提示词发送。

`tools/public_harness_runtime_assets.verify_runtime_assets(assets_root)` 仅只读检查完整固定资产树及具体执行文件、Product 的哈希。布局：

- `assets/sfe/package/bin/agent-harness.exe`，完整官方 SFE 包树。
- `assets/node/node-v22.16.0-win-x64/node.exe`，完整官方 Node 包树。
- `assets/product.json`，已核验的官方 WorkBuddy CN Product 投影，原字节。

返回路径和复制的 pins 元数据；不下载、不运行、不检查用户账户。Windows 要求本地固定盘，所有祖先目录非 reparse。完整性校验不等于权限保护、TOCTOU 防护或真实模型验收。

本切片没有改变现有 synthetic-only 网络门禁；运行时装配、用户自行登录、Windows 下载包和完整 Sub2API 本地验收另行完成。测试使用自写证据 fixture，没有原生推理或费用。
