# P1a 离线原型

**可运行的协议/恢复原型，不是可部署模型服务。** Python 3.12+，运行和测试仅用标准库，不需 pip 安装。当前只运行合成 FakeHarness，不读取原生账号、登录态或生产账本。

## 运行

在 `code/` 目录：

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src:. python examples/offline_demo.py
PYTHONPATH=src python -m compileall -q src tests examples
```

PowerShell：

```powershell
$env:PYTHONPATH="src;."
python -m unittest discover -s tests -v
python examples/offline_demo.py
```

Windows 命令是使用方式，**本轮没有在 Windows 实机验收**。

Demo 建立临时测试数据库并起三个 loopback HTTP 服务：假网关、Facade、假账本。客户端首次 POST 得到真实等待超时；Node 执行一次 FakeHarness；同键 POST 取回结果，再次同键回放完全一致。输出明确包含 `all_execution_synthetic=true`、两个真实集成标记 false、`native_starts=1`。退出后清理临时目录。

## 已实现

- 严格文本 CC 子集、受鉴权 models、明确错误码。`user` 不是身份；system/developer/tools/采样/多模态/stream 直接拒绝。
- 不持久化任务的 Facade，经真实 HTTP 调用固定 Bridge origin；有限超时/响应大小、核对单一 Content-Length 与实际字节、禁重定向/环境代理、外部明文禁止。P1a 暂拒绝 chunked 和 close-delimited 响应。
- Bridge 客户端使用已有任务路径及明确的待实现 harness 扩展；实际 Bridge main 缺少能力握手时 fail closed。
- Node 必填受信 `NodePolicy`（source、ledger 实例、租户/主体白名单、完整允许绑定）；offer ID/完整 envelope/请求摘要/消息/模型绑定在 claim 前严格验证。SQLite 证据、跨进程单槽 OS 锁、一次性 launch intent、只读恢复、结果重传；没有任务重跑/强制清空按钮。
- 合成 FakeHarness 的持久启动计数、延迟、审批、观察中断、失败、模型错配、空结果、未知活性。
- 测试假网关的 SQLite 唯一操作绑定：先冻结原始请求摘要与目标快照，后出站；同键在未准入和渠道变更后不换路由，未知接收仍检查新的执行权限。

`src/` 不导入 `tests/fakes/`。没有可选择 fake 作为生产 fallback 的配置。Facade 仅允许显式 `offline_prototype` 模式且只列 synthetic 模型；默认拒绝启动。HTTP server 只绑定 127.0.0.1，不是生产 web server。

## 关键恢复行为

- 观察异常：pending + unknown，不封死稍后找回的成功结果。
- 已有最终回答但活性 unknown：允许回放真实结果，继续占槽。
- grant 响应丢失或 intent 后崩溃：不再次 start/spawn。找不到可靠原生证据时持续隔离。
- 暂停在 spawn 前的旧消费者持有整个启动临界区的 OS 锁；第二消费者不能释放或抢占它。旧进程退出后 intent 仍阻止重启执行。
- upload/ACK 丢失：只重传持久结果。客户端断开不取消任务。

当前没有经过验证的 supervisor、持久撤销代际或人工解除隔离流程，所以未知槽可能永久阻塞。这是明确的可用性限制，不是自动“修复”旧任务的理由。多个机器/多个 state 目录不由本地锁协调，禁止这样部署。

## 限制和后续

详见 [实现状态](docs/IMPLEMENTATION_STATUS.md) 与 [限定 P1a 发布的独立复核](docs/CODE_AUDIT_FINAL.md)。**未实现**真实 Bridge schema 扩展、真实 Sub2API 插件/Go 接缝、WorkBuddy 选择/回读/终态、真实工具审批/取消、生产权限与租户隔离、收费、SSE、多模态、外部 function calling。

测试假账本不是“复用了 Bridge”；假用户 Key 不是“404token 真实 Key”。P1b 必须在各原仓库独立改造和回归，P2/P3 必须另做获准的真实环境验证。

## 稳定操作客户端

`examples/stable_operation_client.py` 演示将逻辑操作键保存到一个调用方指定文件，每次只发一个 HTTP 尝试，不自动重试。文件只含键，不保存 API Key 或正文。每个有意的新操作使用新文件；响应丢失后的观察/回放复用同文件。无键新 POST 没有跨请求去重保证。

该例子只允许 loopback 测试端点。测试凭证从 `NATIVE_TEST_USER_KEY` 读取；不得把真实密钥写进样例或仓库。
