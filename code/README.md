# Native Harness P1b 接缝代码

最新续作以 [真实使用完成门槛](../blueprint/P2-COMPLETION-GATES.md) 为准。现有 probe 只是一项实验工具，不是用户要求的完整产品；[差距清单](docs/REAL_READINESS_GAPS.md) 未关闭前状态始终为 `NOT_READY_FOR_DIRECT_REAL_USE`。

**当前是单用户、单 Key、单槽、合成执行的集成试验，不是 WorkBuddy 上游，也不是生产模型服务。** Python 3.12+，本仓运行组件只依赖标准库；真实 Bridge 测试另需其仓库锁定的依赖。不会读取原生账号或生产账本。

独立新增的 [WorkBuddy 本机试验程序](docs/WORKBUDDY_LOCAL_TRIAL.md) 可由用户明确选择安装的官方 bundle 并确认一次真实运行。它不是上述 P1b runner 的替代/开关，不启动网关，不放开 synthetic 门禁。Windows x64 包使用私有 CPython 3.13.17，构建与使用见 [packaging](packaging/README.zh-CN.md)。真实用户登录和模型行为需在用户电脑上测试。

## 本仓测试与 P1a demo

在 `code/` 中运行：

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src:. python examples/offline_demo.py
PYTHONPATH=src python -m compileall -q src tests examples integration
```

`offline_demo.py` 仍有意使用假网关/假账本，输出真实集成 false。它不是 P1b 验收。历史 [P1a 报告](docs/TEST_REPORT.md) 和 [独立 P1a 审计](docs/CODE_AUDIT_FINAL.md) 只对应旧提交，不覆盖新增源码。

## 可运行的 Facade/Node

`examples/p1b-config.example.json` 是默认不准入的配置样例。复制到工作目录，指定新的专用绝对 `data_dir`，与真实 Bridge/Sub2API 配置保持相同 source、ledger、owner 和完整 binding；测试准入需明确设置 `accepting_new: true`。`mode` 必须为 `p1b_integration`，`adapter` 必须为 `fake`，不允许将真实模型名填给 FakeHarness。

凭据只从配置指定的环境变量读取：`NATIVE_BRIDGE_SOURCE_TOKEN`、`NATIVE_BRIDGE_NODE_TOKEN`、`NATIVE_FACADE_TOKEN`。它们应由操作者在隔离测试环境提供，与另两仓配置一致，不写进 JSON/仓库。程序不生成或保存服务凭据。

分别运行：

```sh
PYTHONPATH=src python -m native_harness_api.runtime facade --config /path/to/test-config.json
PYTHONPATH=src python -m native_harness_api.runtime node --config /path/to/test-config.json
# 仅一次 heartbeat/恢复/poll，可用于诊断，不保证有任务：
PYTHONPATH=src python -m native_harness_api.runtime node --config /path/to/test-config.json --once
```

Facade 只监听 `127.0.0.1`，Node 经 HTTP 连接真实 Bridge typed 扩展。Bridge 必须为专用 Harness 实例/账本/执行资源，不支持与旧 image runner 混跑，也不声称多个实例能协调同一机器/工具的槽。心跳必须先到，Bridge 才准入新任务。网络/配置异常时 CLI 保守停止，没有自动换账本、换任务或本地 fallback。重新启动必须保留同一 journal；不删未决数据来解锁。只有明确 `offline_prototype` 或 `p1b_integration` 可启动，生产模式仍拒绝。

Sub2API 专用入口是 `POST /v1/native-harness/chat/completions`，不是普通账号渠道 `/v1/chat/completions`。真实认证后它产生内部身份及冻结路由头，客户端自填 `X-Native-*` 不可信。详见 [P1b 补充](../blueprint/P1B-ADDENDUM.md)。

## 真实 Bridge socket 测试

使用 Bridge 的 `work/native-harness-p1b` 实现分支和它声明的运行/测试依赖。设 `BRIDGE_SRC` 为其 `src` 目录，`BRIDGE_PYTHON` 为有依赖的 Python：

```sh
PYTHONPATH="src:$BRIDGE_SRC" "$BRIDGE_PYTHON" -m unittest discover -s integration -p test_real_bridge.py -v
```

这条测试启动真实 Bridge FastAPI/BridgeStore、真实 Facade/Node HTTP 客户端和临时 SQLite 账本；执行器是 FakeHarness。没有导入 `tests/fakes`。覆盖真实成功/回放、冲突、失联 ACK、未知活性、迟到启动、重建 journal、过期墓碑。

## 三仓 HTTP 链测试及持久性边界

使用 Sub2API `work/native-harness-p1b` 实现分支、Go 1.27.2，并预先准备其正常依赖/缓存：

```sh
PYTHONPATH="src:$BRIDGE_SRC" "$BRIDGE_PYTHON" integration/run_sub2api_chain.py \
  --sub2api-backend /path/to/sub2api/backend --go /path/to/go/bin/go
```

测试使用真实 Sub2API APIKeyService/中间件/handler → HTTP Facade → HTTP Bridge → Node/FakeHarness，检查恰一次合成启动和同结果回放。**API Key repository 查找与 operation route store 仍是测试替身。** 输出明确 `postgresql_verified=false`。脚本主动移除 DSN 环境变量，不会碰任何数据库服务。

真实 PostgreSQL migration/100 并发唯一插入/新连接恢复测试在 Sub2API 仓单列，必须由操作者提供已授权可用的隔离数据库。当前环境该阶段受阻，未运行，不能将 SQL mock、Go build 或此 HTTP 链当作持久三仓闭环通过。

## 不重复执行与剩余限制

- Bridge 唯一任务权威；Facade 无队列；Node SQLite 只是恢复证据。
- 旧 journal 的恢复/结果重传先于心跳和新能力检查；binding 下线不挡已接收操作恢复，但恢复阶段绝不 poll/claim/start 新任务。
- claim/start 响应不明、intent 后崩溃、旧 worker 暂停均不是再启动依据。
- 观察异常保持 pending/unknown；终态结果与活性分离。unknown 继续占槽，结果 ACK 丢失只重传。
- P1b 额外核对冻结 source/ledger，凭据引用轮换不能静默更换账本。
- 标准输出仅 text/refusal，未知 usage 省略。空结果、错模型不伪成功。
- 没有真实 WorkBuddy 适配器、Windows 实机验收、生产隔离、supervisor 停机围栏恢复、账本回滚安全、计费、SSE、多模态或外部 tool_calls。

完整状态见 [实现状态](docs/IMPLEMENTATION_STATUS.md) 和 [P1b 测试记录](docs/P1B_TEST_REPORT.md)。默认关闭、未知槽可能永久隔离，不合并、不部署、不付费推理。
