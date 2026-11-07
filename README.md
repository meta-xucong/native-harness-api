# Native Harness API

404token 本地原生算力渠道适配项目。**当前交付是 P1a 离线原型，不是真实 WorkBuddy 上游，也未接通真实 Render Bridge 或 Sub2API。**

- [审定蓝图 v0.2](blueprint/BLUEPRINT-v0.2.md)：目标、模型绑定、能力边界与分阶段门槛。
- [独立设计复核](blueprint/DESIGN-AUDIT-v0.2.md)：允许开始离线实现，不是代码认证。
- [代码与运行说明](code/README.md)：无第三方运行依赖的 Python 原型。
- [P1a HTTP 接缝合同](code/docs/HTTP_CONTRACT.md)：实际客户端和待 Bridge 实现的扩展清单。
- [实现与测试状态](code/docs/IMPLEMENTATION_STATUS.md)：已测、未实现、后续阻断。

```bash
cd code
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src:. python examples/offline_demo.py
```

蓝图放在 `blueprint/`，实现放在 `code/`。原 `docs/`、`audit/` 是 v0.1 历史蓝图与抽象检查快照，不代表当前代码验收，也不是当前合同权威。

## 不变的方向

用户将来用 404token 的地址、Key 和已核实模型名。模型请求由本地真实 Harness 发出，不抽取厂商凭据，不把云端 Agent 任务结果冒充完整模型 API。Render Bridge 保持唯一执行账本，Sub2API 保持用户、渠道及收费权威。

当前 `tests/fakes/` 的假网关与假账本只用于测试，不能重命名或部署为上述两个真实系统。FakeHarness 只发布 `synthetic-*`/`fake-*` 标记。生产运行模式禁止启用；没有自动厂商回退、真实模型、收费或部署操作。

本项目分发许可证仍未选定；本轮自有实现未复制外部代码。参考仓库、官方软件许可及厂商服务授权分别核定。
