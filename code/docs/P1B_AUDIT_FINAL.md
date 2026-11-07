> 独立复审报告的发布副本。仅把临时证据目录绝对路径改为目录名；以下“未尝试 PostgreSQL”描述审计者的操作，不替代实施测试报告中的完整环境记录。证据目录由审计方留存，不是本源码包的运行依赖。原报告 SHA256：`9b2b86580ad26fb0125a4fd40453071475cf4397c10e4cde3d77ffb690376a84`。

# P1b 最终独立复审

日期：2026-11-08 UTC。结论：**PASS_FOR_SCOPED_DEVELOPMENT_BRANCH**。

可以将本次冻结内容推送为明确标注的、默认关闭的 synthetic P1b 开发分支。没有发现阻断这一限定范围的鉴权绕过、重复原生执行、未知状态错误释放槽或图片默认模式回归。不等于可以部署生产，不等于完整 P1b 持久验收，不等于已接入真实 WorkBuddy 或普通 Chat Completions 账户渠道。审计者未提交、推送、部署、启动 PostgreSQL 或调用真实模型。

## 冻结指纹

均已复核 manifest 本体 SHA-256，并逐文件通过校验。

| 工作树 | manifest SHA-256 | 文件数 |
|---|---|---:|
| native-harness-api-p1-prep/code/audit/source-manifest.sha256 | `49748928295619b370e20995ff00f82f085f85243a25ce3782d5b4470c8e83df` | 26 |
| native-harness-bridge-p1b-manifest.sha256 | `b00ef98818a1210571c6d03db5b53cb81a5893249e6096efef75ddf98c0d79a6` | 11 |
| native-harness-sub2api-p1b-manifest.sha256 | `e1b1a0fb5ba005fbba9935980b35fe25e58c55b57159594e63ba73f05da95cfc` | 18 |

基线 HEAD 分别为 `5f1bfd6611a3d3ebe0f6dbb2f2c56ee303931deb`、`cf1e72ec3830854fa0983eb9acb92fad62a604c8`、`2695b585aa7c2b9fed6fa50b9b8455c0ac2c1a14`，最终改动在未提交工作树中。发布操作必须包含 manifest 所列新增文件，不能仅提交 tracked diff。

## 既有问题闭合情况

### 1. 图片/Harness 心跳与混跑：通过明确隔离闭合，不冒称混跑实现

- Harness heartbeat 独立存储，不再覆写 image node_heartbeats。
- Harness 模式拒绝图片 heartbeat/create；含图片历史任务或心跳的账本不得启用 Harness。
- 所有新版本 BridgeStore 构造器即使面对空账本也在同一 `BEGIN IMMEDIATE` 固定 image/harness 模式，竞争仅一个模式成功；不能将已固定 Harness 账本以 image 模式重开。
- 独立复现检查 image heartbeat 被拒、image 表仍为空、类型状态正确、反向重开失败。
- 314 项旧 Bridge 回归保留。文档明确单独实例、单独账本和执行资源，不声称两个实例能协调同一物理机器/工具，也不支持独立 image/Harness runner 混用 next endpoint。

此处仍复用真实 BridgeStore 的 tasks/attempts/claims 与执行授权机制，不是恢复 P1a fake ledger。

### 2. Binding 删除阻塞旧结果 ACK：代码与反例均闭合

- `runtime.node_tick` 先调用同锁 `run_once(allow_new=False)`，只恢复旧证据/上传结果，未决时只报告空能力 attention；心跳异常不能阻止已发生的恢复。
- 只有恢复返回 idle 后，才能经过新能力 heartbeat，再进入新任务路径。
- 独立真实 Bridge 复现：结果已提交但 ACK 丢失，随后移除 bindings 并关新准入，node_tick 恢复 ACK 成功、累计启动仍 1。
- 独立负例把 next_task 替换成必失败函数，recovery-only 不调用它。没有为恢复放开新 claim/start。

### 3. Sub2API 畸形成功响应：严格窄 DTO 闭合

- 原复现缺 id/created、content 为 number 且带 legacy function_call。旧“应接受”overlay 现按预期失败，新的“必须拒绝且不返回 body”overlay 在 race 下 PASS，错误为 `NATIVE_INVALID_COMPLETION`。
- 严格检查必需字段/类型/单条 choice/终态/内容，并拒绝未知和工具调用字段；39 个真实 HTTP DTO 子场景通过。
- 该 DTO 是本专用 synthetic 通道的窄协议，不是完整 OpenAI response 兼容声明。例如一般 OpenAI 可选字段也可能被拒绝。
- 重新审阅鉴权、冻结路由、admission 计算未发生语义放宽；没有普通 provider 重试/failover。

## 新增 store_gate 与取消修补

原并发三仓测试偶发未完成日志仍保留；实施者随后将故障确定到 source GET 占唯一 gate 时 Node start 收到503，真实 start grant 为空但本地保守隔离。最终修补不是重试 start 或释放 unknown：

- 仅专用 Harness 模式 gate=2，仍只有一个 AnyIO 执行 limiter；最多一个运行、一个等待，第三个返回503。图片模式保持 gate=1。
- Harness 使用 shielded asyncio worker，已开始的同步操作在 HTTP 被取消后仍持有 limiter/permit 至真实完成。
- 未开始的调用在状态锁内标记 canceled、释放其 admission，并取消等待 worker；invoke guard 防迟到执行。
- 独立全量测试包含读/start 排队、第三请求拒绝、取消等待 start 不产生 grant、取消运行读不允许第二同步方法提前进入。
- 又重复上述三项与双模式构造竞争各10次，共40个测试全部 PASS，其中模式测试总计80轮双构造竞争。

这是有界可用性修补，不是通用高并发/服务关闭证明。更高竞争、网络丢失或进程退出仍可导致保守隔离，不能删 journal 或重试原生启动来“修复”。异步运行范围为 asyncio/Uvicorn，不包含 Trio。

## 独立执行结果

| 检查 | 最终结果 |
|---|---|
| Bridge 完整 Python 回归 | 354/354 PASS，无 skips；314旧+40 typed |
| Native 单元回归 | 89/89 PASS，无 skips |
| Native 真实 Bridge socket | 14/14 PASS，无 skips，含真实 Node CLI 子进程 |
| 三仓真实 HTTP 脚本 | 连续5轮 PASS，每轮 synthetic starts=1、node_fault_codes=[] |
| 额外 gate/取消/mode-pin 重复测试 | 40/40 PASS |
| 独立旧故障反向 Python 复现 | 专用模式、删除binding恢复、recovery-only 三项 PASS |
| Sub2API nativeharness race + DTO + 原反例拒绝 overlay | PASS；PostgreSQL 测试明确 SKIP |
| Sub2API handler/middleware/routes/config 四包 unit | 全部 PASS：126.794s / 0.053s / 10.353s / cached |
| Native compileall / 三仓 whitespace / manifests | PASS |

三仓 HTTP 使用实际 Sub2API APIKeyService/认证中间件/handler/出站 HTTP、实际 Facade/Bridge/Node。credential lookup 与 operation route store 是显式 fixture，因此不能证明数据库并发唯一性、迁移、断电或重启持久恢复。Go 测试使用已有官方 Go1.27.2 和已有依赖缓存，GOPROXY=off。

## 保留的发布限制与后续门槛

1. **真实 PostgreSQL NOT_RUN**：没有尝试被拒绝的启动或替代绕过。生产/持久 P1b 仍需迁移、真实100并发唯一行、进程重启与全链路恢复证据。
2. **数据库回滚/历史丢失**：唯一约束和 pin 不是旧快照恢复证明；操作路由历史或 Bridge 历史丢失后不得直接重开准入。旧版本进程也不受新 mode marker 围栏，升级前须停止。
3. **真实原生能力未验收**：仅 FakeHarness/synthetic；真实 WorkBuddy 选模、终态、子进程/取消、商业许可、Windows、OS租户隔离均未证明。
4. **不是普通 CC 上游**：当前是专用 `/v1/native-harness/chat/completions`、单授权 user/key、免费、usage unknown，不接普通账号调度/结算。
5. **隔离资源与容量限制**：专用实例及执行资源，不混跑图片，不跨实例共享物理槽。更高竞争仍可能安全隔离而不能自动恢复。
6. **Retention/reconciliation**：终态 unknown 不释放，结果体过期仍保留 tombstone；缺 ACK-of-ACK 与完整 supervisor reconciliation，某些损失会长期隔离。这是安全余项，不得宣传为自动恢复完成。

## 证据

同目录 `probe_final.py`；`logs/final/` 保存最终 Python suite、五轮跨仓 HTTP、额外竞争测试和独立回归日志。`AUDIT_INITIAL.md` 与旧失败日志保留，不能用最终绿灯覆盖旧失败历史。Sub2API 独立 overlay 原件在本目录及 `p1b-independent-audit/`。

Sub2API专项及四包测试的完整输出保留于独立审计子线程工具记录，未另存全文日志；不将其描述为整仓Go全量测试。
