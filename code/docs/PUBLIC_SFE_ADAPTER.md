# Public SFE 本地执行证据与资产接缝

`PublicSfeAdapter` 接收 Bridge Runner 已授权的 task/grant 和显式 `invoke(model_id, messages)`。调用者负责正式资产、登录 profile、实时账户身份及执行权限；本模块不自行发现、登录、批准工具或启用生产渠道。

首版只接收一条 user 文本消息，不将多角色聊天悄悄拼成 prompt。单任务及单 grant 在 SQLite 中唯一，FULL 同步事务提交 intent 后才调用原生 owner。该文件只保存不可重复执行证据及脱敏结果，不是派发账本或队列。重启、同键并发、失败与未知结果均不能再次执行。reconcile 只读现有记录。未知状态没有自动解锁/清理功能。

终态要求公开 SFE protocol/account/model 证据、prompt ACK、匹配终态以及完整 owned 退出清理同时成立。否则返回 pending/unknown，而非伪造失败或重试许可。未知用量不填零。仅保存 text/refusal，不保存原始 vendor 输出、异常或凭据。目录由调用者在专用受保护 profile 中创建；拒绝已存在的 reparse/硬链接/非普通数据库和 sidecar。此检查不是对可并发改写该目录的攻击者的文件系统沙箱。

`tools/public_harness_runtime_assets.verify_runtime_assets(assets_root)` 仅只读检查完整固定资产树及具体执行文件、Product 的哈希。布局：

- `assets/sfe/package/bin/agent-harness.exe`，完整官方 SFE 包树。
- `assets/node/node-v22.16.0-win-x64/node.exe`，完整官方 Node 包树。
- `assets/product.json`，已核验的官方 WorkBuddy CN Product 投影，原字节。

返回路径和复制的 pins 元数据；不下载、不运行、不检查用户账户。Windows 要求本地固定盘，所有祖先目录非 reparse。完整性校验不等于权限保护、TOCTOU 防护或真实模型验收。

本切片没有改变现有 synthetic-only 网络门禁；运行时装配、用户自行登录、Windows 下载包和完整 Sub2API 本地验收另行完成。测试使用自写证据 fixture，没有原生推理或费用。
