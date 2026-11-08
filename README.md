# Native Harness API

## 当前开发线：通用多 Harness v0.3

目标是把用户本机官方 Harness 的真实执行与文本输出接入 404token / Sub2API。蓝图与实现分别保存在 `blueprint/`、`code/`；当前仍在 draft PR 开发审计，不自动部署服务器，也不替用户登录。

- [已独审定稿的开发文档](blueprint/DEVELOPMENT-v0.3-MULTI-HARNESS.md) 与 [设计审计](blueprint/DEVELOPMENT-AUDIT-v0.3.md)。
- [通用配置与旧任务恢复](code/docs/MULTI-HARNESS-CONFIG.md)：单 Node、单执行槽、一个账本；旧任务不会换 Harness 重跑。
- WorkBuddy：保留已实现的 public SFE 运行链、受控 profile、模型发现和流式文本。
- Codex：新增固定版本官方 App Server、用户本地 ChatGPT device-code 登录、模型发现、文本流及终态清理。源码/自编协议测试不等于真实账号模型验收。
- 豆包工作、千问办公：尚未实现 Adapter，不列入可用模型。

**真实账户验收仍由用户在本机完成。** Codex 当前只投影回答文本，commentary 显示任务运行状态而不转发原文，不是完整 Codex UI 镜像。工具审批不会自动放行；不回传凭据，不把模拟输出或心跳拼成模型答案。实际运行官方客户端本身并不能保证转供服务符合厂商条款，应单独核对账号和服务授权。

以下 v0.2 / P1 文档为历史阶段记录，不替代 v0.3 当前支持范围：

- [审定蓝图 v0.2](blueprint/BLUEPRINT-v0.2.md)：目标、模型绑定、能力边界与分阶段门槛。
- [独立设计复核](blueprint/DESIGN-AUDIT-v0.2.md)：允许开始离线实现，不是代码认证。
- [P1b 切片与合同补充](blueprint/P1B-ADDENDUM.md)：独立三仓分支、专用路由与未过门禁。
- [代码与运行说明](code/README.md)：Python Facade/Node 与跨仓集成测试。
- [HTTP 接缝合同](code/docs/HTTP_CONTRACT.md)：P1a 合同基线及 P1b 实施补充。
- [实现与测试状态](code/docs/IMPLEMENTATION_STATUS.md)：已测、未实现、后续阻断。

```bash
cd code
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src:. python examples/offline_demo.py
```

蓝图放在 `blueprint/`，实现放在 `code/`。原 `docs/`、`audit/` 是 v0.1 历史蓝图与抽象检查快照，不代表当前代码验收，也不是当前合同权威。

## 不变的方向

用户将来用 404token 的地址、Key 和已核实模型名。模型请求由本地真实 Harness 发出，不抽取厂商凭据，不把云端 Agent 任务结果冒充完整模型 API。Render Bridge 保持唯一执行账本，Sub2API 保持用户、渠道及收费权威。

`tests/fakes/` 的假网关与假账本只用于 P1a 测试，P1b Bridge 集成不导入它们。FakeHarness 只发布 `synthetic-*`/`fake-*` 标记。生产运行模式禁止启用；没有自动厂商回退、真实模型、收费或部署操作。旧 P1a 审计不覆盖本分支新增源码。

本项目分发许可证仍未选定；本轮自有实现未复制外部代码。参考仓库、官方软件许可及厂商服务授权分别核定。
