# 已有 Codex CLI 最小接入测试

日期：2026-11-08 UTC。基线 `d324b468`，最终提交及远端矩阵在 Draft PR #2 中记录。本报告只覆盖此次已有 CLI 增量，不重新宣称历史真实验收。

## 本次已运行

- `cd code && PYTHONPATH=src python -m unittest discover -s tests -v`：**996 tests，936 passed / 60 skipped**，43.920 秒。跳过项为现有平台/固定运行时限制及新 Windows 配置 round-trip；没有失败。
- `PYTHONPATH=src:. python examples/offline_demo.py`：通过，`all_execution_synthetic=true`、一次启动、同结果回放；明确 `actual_bridge_integrated=false`、`actual_sub2api_integrated=false`。
- `PYTHONPATH=src python -m compileall -q src tests examples integration tools packaging`：通过。
- `git diff --check`：通过。

新增覆盖：已有 exe/PATH/npm 平台布局与版本不符停止；同用户环境/原 CODEX_HOME；不读取凭据的 sidecar；官方公开账号/目录初次确认；每次 initialize 回读 home；新 recipe 冻结 CLI/home，旧 recipe 形状与路由不变；新 runtime 复用原 Codex 协议；取消确认/未登录后同 sidecar 显式重入；Codex 默认向导/旧 managed 显式命令/原配置优先；新工具打包 allowlist。

四项专项恢复回归：旧 WorkBuddy 和旧 managed Codex 的完成结果在旧 target 移除后只读回放；两者未知任务均不会因加入已有 CLI target 而解锁或新执行。断言当前 adapter resolver 零调用、旧启动次数不增加、证据数据库字节不变。

## 审查中修正

初稿把无推理目录失败和取消确认留在 setup intent，导致用户手工登录或回到向导后仍不能继续。已修改为：只有**该目录观察自有进程清理确认**后恢复原可观察状态，由用户下次明确重入；清理未知仍保留锁，真实 task 的 unknown/执行围栏未改。补充了未登录后重入、取消身份后重入及既有身份观察失败保留三类测试。

一次测试脚本首次写入时工作目录多加 `code/`，文件未创建，产生 import error；修正路径后上述四项恢复测试4/4通过，最终全量已包含它们。此为测试调用错误，不算产品通过证据。

## 未运行 / 不作结论

- 官方 Codex CLI/App Server 启动、真实账户登录复用、额度、模型调用：**NOT_RUN**。
- 本机用户账号、认证文件、系统凭据：未读取、复制或修改。
- 真实 WorkBuddy 登录/模型、本轮三仓真实 HTTP/数据库联调、生产部署/收费：**NOT_RUN**；Bridge/Sub2API 源码无变更。
- Windows 自写 peer、实际 ACL/owned process、配置 round-trip、完整 ZIP：由精确提交远端 CI 另记；此处 Linux skips 不当作 Windows 通过。
- App Server 认证商业/托管服务限制仍存在，测试通过不是404token商业转供许可。
