# 已有 CLI 接入独立实现审计

日期：2026-11-08 UTC。基线：`d324b4686dc09724a4ddd0319981c257e6abeb59`，审查 `work/codex-existing-cli-20261108` 工作树候选。审查者独立于实现作者。

## 结论：PASS（本轮离线代码范围）

检查新增 `codex_user_cli.py`、control/session、registry/runtime 分派、quick_start/client、打包 allowlist 及新增测试。未发现阻断问题；这不是 Windows 真实账号或模型验收结论。

- 新连接仅运行版本、initialize/account/read/model/list，不调用 login/start、thread/start 或 turn/start。先显示 CLI/home 并确认，账号经官方协议回读后确认。没有默认重复 login status。
- 本工具读 exe 做冻结摘要，读写自己的 sidecar；没有读取、复制认证文件、OS keyring 或提取 token 的实现。官方 CLI 自身可能刷新认证，用户说明没有将其混称为绝不写登录文件。
- 同用户环境继承，明确原 CODEX_HOME；每次新执行核对冻结 exe/home、官方回读 home 与公开身份。SHA 是所选 exe 一致性，不是官方来源认证。现有配置/MCP 等可能生效，未假称绝对隔离。
- 只读目录检查已确认清理时，fresh/已确认 sidecar 可供用户下一次显式再试；未提交真实任务，不能将这条恢复用于 turn。清理未知仍 retain/quarantine，没有自动重试。
- 旧 adapter 的新可选 home 参数默认 None，旧 recipe 字段和摘要输入不变。已受理任务仍在 DurableNativeAdapter 证据与 Node journal 内恢复，不先连接新 CLI。旧 target 移除后完成结果回放，unknown 不释放槽。
- 默认 Codex 入口改为已有 CLI；原配置优先，旧 managed 登录仍为显式兼容路径。只新增一个 adapter 和薄工具，没有新队列/服务。

## 独立执行的测试

命令（仓库根目录）：

```sh
PYTHONPATH=code/src:code/tools:code/tests python -m unittest test_codex_user_cli test_codex_quick_start test_existing_cli_recovery test_codex_control test_codex_session test_multi_harness_runtime test_codex_client_packaging -q
```

结果：84 tests，83 PASS、1 SKIP，0 failure/error。运行使用自写 synthetic fixture/Mock，未运行官方二进制，未接触真实账号或发送模型请求。

覆盖：路径/npm 布局、版本限制、同用户环境、无 auth 文件读写、sidecar 分离、取消/失败后显式重试、cleanup unknown 保留、home 不符阻止派发、绑定冻结、向导/打包、旧 WorkBuddy 和 managed Codex 完成/unknown 任务恢复。

全量回归、offline demo、Windows CI 以本次发布证据中的独立结果为准，不把上述 84 项替代全量或 Windows 测试。真实 CLI 登录复用、模型/积分、生产服务和商业许可仍 NOT_RUN / NOT_VERIFIED。版本支持当前限定 0.167.0，不代表支持所有用户已安装版本。
