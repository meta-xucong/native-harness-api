# 普通 Provider 并发观察修复验收

2026-11-08 UTC；修复基线 Native `c7f6d9e75b486151425b33ed2b5f2a2dc3fd2991`，Bridge 固定 `52dc083207a8a02dfc14fe2ec1ea10f0cc0a54a8` 未改。仅普通入口、测试、文档、CI；没有修改 Node、Bridge、Adapter 或原生 Sub2API。

## 发现和修复

原复审四个同 key 请求并发可占满 Bridge 两个有界 store 位置，Node 心跳收到 `store_busy` 后退出，未造成重复执行。普通 Provider 现在使用非阻塞源端 IO 锁，只允许一个源端调用，竞争即拒绝，不增加队列或重试。事件投影内的终态 task 回读同样受保护；流式 yield 不持锁。

首次独审发现该隐式回读漏锁，已修正并新增确定性回归。初始 lookup 竞争为 `503/PROVIDER_BUSY`；submit 阶段沿用 `409/EXECUTION_UNCERTAIN`；已接受观察竞争为 `409/PROVIDER_BUSY`。拒绝不是成功，也不证明重发无幂等键安全。

## 实际结果

| 检查 | 结果 |
| --- | --- |
| 全 Python suite（独立复跑） | PASS：1028 total，968 pass / 60 skip |
| Provider 单测 | PASS：25/25 |
| 真实 Bridge HTTP 新旧路径 | PASS：23/23，含阻塞源 lookup、并发请求、Node 心跳确定性回归 |
| 独立真实 socket 4/12 并发、JSON/SSE 混合各三轮 | PASS：源 IO 峰值1；无 store_busy；Node 不退出；每轮原任务 start=1，同 key 回读200 |
| compileall / diff check / 离线 demo | PASS，demo仅合成 |
| 新精确提交 Windows/Linux CI | PENDING，须以实际运行结果补充，未用历史CI冒充 |
| 原生 Sub2API Go 接缝本机重跑 | NOT_RUN，本机无可用Go编译器；等待精确CI |
| Windows真实已有CLI、WorkBuddy/Codex账号和模型 | NOT_RUN |
| 完整 Sub2鉴权/DB/scheduler/重试/计费/TLS | NOT_RUN |

命令见 `code/docs/OPENAI_PROVIDER_TESTS.md`；真实HTTP组新增 `test_concurrent_observers_leave_node_store_capacity`。并发测试证明忙时有界拒绝且 Node 可继续，不承诺所有并发请求均返回200。仅单 Provider 进程/单 Node，额外源客户端、多副本或超时后仍执行的远端 store 操作不在该局部保证内。

原生 Sub2 不透传幂等键，关闭重试/failover仍是首次隔离试验条件。未知 usage 不伪造 token，不用于收费。真实模型请求须由用户确认账号、模型和最小请求。无凭据读取、真实模型费用、生产部署或合并。
