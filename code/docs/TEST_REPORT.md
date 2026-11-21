> Historical P1a report for commit `5f1bfd6611a3d3ebe0f6dbb2f2c56ee303931deb`. Its manifest is now archived as `code/audit/p1a-source-manifest.sha256`; it is not a checksum of the current P1b working tree. See [P1b report](P1B_TEST_REPORT.md) for current results.

# P1a 测试报告

运行时间（UTC）：2026-11-07T15:45:39.089598+00:00
环境：Linux x86_64，Python 3.12.14，标准库；无新依赖安装。  
代码起点：`82e8cfeaf6ce05857dba590cf1d4bb63efd42ea2`。新增代码已经 [独立复核](CODE_AUDIT_FINAL.md) 通过限定 P1a 原型发布；远程发布由后续步骤核验。  
执行快照：[source-manifest.sha256](../audit/source-manifest.sha256)，manifest SHA256：`e76f35fadc872f4b1e9a1e5160018cf3a79c0099238dd6a61d66868f2632dfd8`。

## 实际执行

| 命令（code/ 内） | 结果 |
| --- | --- |
| `PYTHONPATH=src python -m unittest discover -s tests -v` | **79/79 PASS**，0 FAIL，0 ERROR，0 SKIP |
| `PYTHONPATH=src python -m compileall -q src tests examples` | PASS，退出码 0 |
| `PYTHONPATH=src:. python examples/offline_demo.py` | PASS，合成启动数 1，回放内容一致，真实 Bridge/Sub2API 标记 false |

最终执行仓库 `git diff --cached --check`：PASS。审定 Markdown 的显式换行双空格由 `.gitattributes` 保留；首次 staged 检查因此报告行尾空格，配置 Markdown 属性后复查通过，未改审定蓝图字节。源码/新文档的明显秘密模式与本机绝对路径扫描未发现命中；这不是穷尽秘密扫描或安全认证。

测试拆分：3 项稳定操作客户端、8 项合同、34 项真实 loopback HTTP + 显式假账本/假网关集成、28 项 Node/FakeHarness 单元/跨进程故障测试、6 项真实 socket framing 测试。100 次并发同键 POST 是其中一项的内部断言，不另算 100 个测试。

## 关键证据

- 假网关操作绑定 SQLite 事务与唯一键；同键对不同请求 409；冻结路由后更改当前渠道/URL仍回到原目标。
- 明确未准入和未知接收后，权限撤销不被已绑定记录绕过。节点离线/停止新准入仍允许旧结果回放。
- Bridge create 回包丢失后仍一个 task；结果 commit/ACK 丢失仅重传，FakeHarness 启动数保持 1。
- 每次 task 回读校验身份、客户端摘要、Bridge 实例及冻结模型绑定摘要。篡改模型并同时伪改输出标签仍被拒绝。
- 观察异常保持 pending/unknown；成功或失败且活性 unknown 都不释放槽。
- 两项真实子进程故障测试覆盖迟到 spawn 与 intent 后终止。OS 锁及持久 intent 阻止新消费者执行；没有强制释放入口。
- 拒答保真；空结果/模型错配不伪成功；usage unknown 省略；认证缺失/重复拒绝。
- HTTP 重定向拒绝；截断错误归为接收不明；错误体畸形不输出原异常。

## 独立审计首轮修复

首轮 62 项通过没有覆盖以下真实缺口；独立审计实证后修复，再运行本报告的 79 项：

1. **Node 缺少执行前完整验证。** 增加必填受信 NodePolicy 与绑定 offer ID 的 NodeOffer，claim 前校验来源/账本/允许 owner/完整冻结绑定、精确 DTO、规范化 client digest、消息与可启动状态。28 种变异输入子场景及自洽但越权身份均断言 claim/start/journal 写入数 0。旧 journal source/ledger 改变时隔离，不上传到新路由。
2. **有界 read(n) 不自动检查 Content-Length。** 独立核对声明字节数；真实 socket“完整 JSON + 声明多 100 字节 + 断开”现在报接收未知。成功和错误响应均覆盖，错误不得据此宣称未接收。P1a 对 chunked/无长度明确不支持，不靠宽松解析器判断完整。
3. **Facade 绑定映射 key 与 alias 不一致。** 初始化及处理请求前校验 key/alias/type/synthetic 一致，准入前再核对公开 model；错误配置不建任务。

期间尝试扩大 chunked framing 测试发现标准库接受缺尾部空行，本版选择显式拒绝 chunked，而非宣称完整支持。一次从仓库根误用 code/ 内测试命令造成 fixture import 错误，随后按文档在 code/ 中重跑全部 79 项通过。这些中间结果不计作通过项。

## 未测试 / 未实现

真实 Bridge main 和真实 Sub2API 集成、真实用户 Key、WorkBuddy/其他官方 Harness、Windows 实机、厂商模型身份/usage、生产账号/租户隔离、真实取消/审批/子进程停机证明、持久围栏恢复、备份回滚、实际磁盘满、三仓旧功能回归、账务和商业授权均未验收。详见 [实现状态](IMPLEMENTATION_STATUS.md)。

上述测试和审计期间没有付费模型调用、部署、合并或 GitHub 写入。源码发布是另一步骤，不改变测试范围。本报告不把合成执行计数解释为真实原生外部副作用 exactly-once。

发布准备仅增加独立复审报告与这些状态说明，未修改被审计的 21 份源码/测试/样例/依赖声明；源码 manifest 保持不变。独立报告中的审计目录与日志名是审计方留存证据，并非本源码包内的运行依赖。
