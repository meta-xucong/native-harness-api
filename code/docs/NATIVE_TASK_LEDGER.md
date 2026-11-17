# Native Harness 独立任务账本

Sub2API 继续使用普通 OpenAI Provider。Provider 和 Windows Node 可以通过原有 HTTP 合同连接 Native 自己的本机任务服务。任务服务保存请求身份、尝试、领取和启动授权、原生事件以及结果。在迁移后的 WorkBuddy 部署中，Render Bridge 可以独立运行 Alchemy 生图。

Native 任务合同和事件事务来自同项目的 Render Bridge `c63479e135ac49d7a25a1c0d261b647d16679dbe`。服务保留 `/bridge/v1` 和 `/bridge/node/v1` 下 Provider 和 Node 使用的路径。服务端运行于需要 Pydantic 2 的 Python 环境。Windows Node 包继续使用自己的 Python 和 WorkBuddy 资产。任务服务在 `127.0.0.1` 上监听并使用独立 TLS 证书配置及 Source、Node 凭据。原 Sub2API Key 和 Provider bearer 仍属于各自的接入层。

## 导入和启动

先关闭旧 Bridge 与 Provider 的新准入，核对 Node journal 的回执并停止原任务服务。用 SQLite backup 分别备份原本机隔离账本与 journal。配置包含 `schema_version: 1`、新数据库的绝对路径 `database`、端口、`tls_certfile`、`tls_keyfile` 和 `harness`。将原 `source_id`、`workspace_id`、`node_id`、`executor_instance` 与原 `harness` 配置放在同一个 `harness` 对象内，保留原 ledger、owner、binding，并设置 `accepting_new: false`。可以显式设置两个凭据的环境变量名 `source_token_env` 和 `node_token_env`。配置文件里只写变量名。

```powershell
python -m native_harness_api.ledger_runtime --config path/to/native-ledger.json --import-bridge path/to/bridge-snapshot.sqlite --journal-snapshot path/to/node-snapshot.sqlite
python -m native_harness_api.ledger_runtime --config path/to/native-ledger.json
```

导入只读取快照并新建独立数据库。要求原逻辑身份和原 Node 匹配，任务均已收尾，已有启动授权的每条任务与 journal 已确认的回执一致。保留原始 ID、幂等索引、冻结 binding、领取、启动授权、终态、结果摘要和事件游标。旧本机停止回执可以用原授权补收 ACK。导入后 Node 使用原 journal、原 profile 和原配置目录。让新任务服务接管原本机 TLS 端口可沿用 Provider 和 Node 地址。两套服务的准入在验证期间均关闭。

## 切换验证与回退

在副本上先验证旧请求查回同一任务、结果回执丢失后的补写、事件游标续读以及并发拒忙。启动回执丢失时，授权记录仍占用槽，Node 只核对同一任务。切换后的无推理验证包括原 Node 心跳、回放、有效和无效凭据，以及关闭准入时经 Sub2API 的模型映射。Sub2API 的用户鉴权、目录与 RPM 检查使用 [只读预检](OPENAI_PROVIDER.md#本机-sub2api-只读预检)。

`native-provider.yml` 在 Linux 和 Windows 运行 `test_native_task_ledger`，使用固定版本 Bridge 生成临时任务，导入 Native 后检查原 ID、原 Idempotency-Key、事件游标、拒忙和未确认回执的恢复。测试执行器只使用 FakeHarness。

在 Native 接单前，停止 Native 服务并用原 `bridge.json` 启动同一个本机隔离 Bridge，保持准入关闭，沿用同一端口和 journal。Native 账本已有新任务时，要先对账新任务并保留原状态，再规划反向迁移。准入或超时都不提供重放已授权推理的许可。
