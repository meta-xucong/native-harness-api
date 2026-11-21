# 普通 Provider 实现独立审计

日期：2026-11-08 UTC。审计者：独立审计线程 `provider_plan_audit`。
范围：Native `3402cc1` 之后审定文档与本次未提交工作树实现；原生上游 `6b4b37db43443b5e88e424fe3c69602cc3810dfb`、Bridge `52dc083207a8a02dfc14fe2ec1ea10f0cc0a54a8` 未修改。

## 结论

**本增量代码审计及可运行云端 Python/真实 Bridge 合成回归通过，无已知阻断。可提交草稿 PR 并运行固定源码 CI。** 原生 Go forwarder、Windows 精确提交 CI 仍待其实际结果；不能把本审计说成用户账号已可用、原生 Sub2API 全链路或商业验收通过。

审阅：`openai_provider.py`、HTTP SSE 改动、Native runtime 配置、部署生成/向导/打包变更、新测试、原生 Go overlay/CI 与运行说明。检查了固定上游的请求 header 白名单、用量和错误/截断处理路径。

审阅时关键文件 SHA256：

- openai_provider.py：`195f7d748f7d9308b4eeb730bf2f3b12988685147b6a71df8787fe92eae1fa82`
- http_server.py：`4e646dde6abf2d726dfd27ae7a357377610013dd9c43ecca540eecad60074aec`

## 独立执行结果

| 项目 | 结果 |
| --- | --- |
| Provider + config 单测 | 27/27 PASS |
| 实际 Bridge HTTP Provider 测试 | 6/6 PASS |
| 额外故障注入 | 4/4 PASS：流中 task ID、owner、ledger 被替换均 error 且无成功 DONE；过期结果 410 且不重提 |
| 完整 Python suite | 1025 total，965 PASS、60 SKIP；49.380 秒 |
| git diff --check | PASS |

全量日志：工作区 `provider-independent-audit/python-full.log`。这些测试有重叠，不相加形成独立总数。首次测试调用遇到 Python 模块路径/自写探针缩进错误，修正调用后取得以上结果，未修改产品代码以通过测试。

## 问题闭环

- 初稿 SSE 额外增加一秒写出预算，与审定上限不一致。现已移除，在原观察期限中预留短错误 flush；说明明确既有 Bridge 在途 IO 仍受其独立有界 timeout 控制，不虚称端到端硬实时 deadline。
- 蓝图 409 可能被理解为防重试的问题已在设计阶段修正。代码和指南都明确常见 SDK 也重试 409，原生网关没有稳定端到端 operation 身份。

## 核心不变量

- 标准请求仅以配置 credential/owner 定位可信资源域；内部 X-Native 头和 user 不会授权另一主体。
- 查询先于新准入与当前模型校验；已接受 task 的 ID、identity、ledger、摘要和冻结 binding 在后续读取中检查。旧配置缺省 native、旧恢复路径保留。
- 无 key 每 HTTP 新操作；同直接 key 的 stream/user/include_usage 不改变执行语义。Provider 不自动重提 submit、不自动取消、不产生第二执行账本。
- SSE 按已发送正文前缀追加，改写/最终不一致输出错误且无成功终态。真实终态与模型证据才允许 finish/[DONE]，未知用量不填零。
- disconnect、unknown、结果过期不解除原执行槽或重推；真实 Bridge 合成测试与独立异常注入均覆盖对应行为。

## 仍需保持的证据边界

1. Go 工具链本机不可用，原生 service forwarder 接缝本机 NOT_RUN，等待固定 SHA CI。该 fixture 成功路径验证 JSON/SSE、模型映射、自动 include_usage 与私有头剥离，不运行完整用户认证、DB、scheduler、计费或 handler 自动 failover/retry。
2. 即使该接缝 CI 成功，也不能给经原生 Sub2API 重试的多个 HTTP 尝试提供 exactly-once 保证。
3. 本机 60 个跳过项不能计为通过；Windows 以精确提交 CI 结果为准。
4. 真实 WorkBuddy/Codex 账号、模型积分、容器/TLS部署、计费精度和商业许可未验收。Codex 官方认证使用限制不能由本地复用绕过。
