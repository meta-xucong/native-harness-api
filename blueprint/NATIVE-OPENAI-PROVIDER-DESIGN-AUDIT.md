# 普通 OpenAI Provider 独立设计审计

日期：2026-11-08 UTC。审计者：独立审计线程 `provider_plan_audit`。

## 阶段一：蓝图

审阅 `NATIVE-OPENAI-PROVIDER-BLUEPRINT.md` 初稿；独立核对 Native 基线 `3402cc1` 的 facade/contracts/event_contract/http_server，以及原生 `Wei-Shaw/sub2api` 提交 `6b4b37db43443b5e88e424fe3c69602cc3810dfb` 的 raw Chat Completions 路径。

**结论：核心架构通过；下面一项措辞修订后可进入开发文档。尚未放行编码。**

### 修订项

- §4.4：409 不是不可重试保证。常见 OpenAI SDK 默认会重试 409。应只把它描述为已受理/观察冲突的表达，不以选择该状态码来声称降低或消除重复执行。调用端关闭自动重试仍不等于原生网关具备幂等保证。

### 已通过的设计边界

1. 不修改原生 Sub2API；普通 OpenAI/API Key 账号与 `extra.openai_responses_mode=force_chat_completions` 是明确入口，不依赖私有 X-Native 头。
2. Provider 只做认证、协议和观察，保留 Bridge 唯一执行账本与 Node 单槽，不增加数据库、路由墓碑、后台重试或第二队列。
3. 普通 provider key 是固定资源域凭证，不是终端用户身份；不以 `user` 伪造多租户隔离。
4. 原生 raw CC 透传白名单仅 accept-language/user-agent，不能承诺 Idempotency-Key 穿透；无头每 HTTP 新 operation，不能按 prompt hash 合并请求。
5. 稳定 key 的恢复限于直接到达的同资源域请求，Bridge 接收后使用其冻结 binding；旧入口和已接收任务继续恢复，不偷偷迁移配置。
6. 标准流仅追加可证明后缀。快照改写/终态不符是显式失败，不输出成功 finish/[DONE]；原生 idle、断线、EOF 不算成功。
7. 原生 Sub2API 强制 include_usage，Native 仍不得伪造完整 usage。上游本身缺失值初始化为零不构成真实零消耗证据；限非收费试验。
8. 明确仅单 user 文本、无通用 tools/多轮/Responses；未实现语义拒绝，不隐式拼接。
9. TLS/可达性由既有部署层负责，loopback 不被误描述成容器可直连地址。
10. 源码、fixture、原生网关接缝、真实账号和商业许可各自独立，不虚报生产就绪。

### 开发文档复审门槛

- 精确字段、配置默认、凭证来源、HTTP/SSE 错误和超时处理。
- stream/include_usage 与规范执行请求分离，使同 key 的非流式最终读取不产生冲突；不同正文仍冲突。
- 重连/旧配置/旧 binding 的具体最小改动点。
- 原生上游版本、模型映射、Base URL 拼接及完整 upstream retry 限制。
- 单元/HTTP fixture、真实 Bridge synthetic、未改造原生网关接缝测试分层；若测试未运行明确标注，不由 fork 测试替代。

## 阶段二：开发文档

开发文档已独审通过，详见 `NATIVE-OPENAI-PROVIDER-DEVELOPMENT-AUDIT.md`。蓝图的 409 措辞已修订闭环，允许按审定文档编码；实现尚未验收。

## 阶段三：实现与回归

待实现，尚未审计；无真实账号、真实模型或生产部署验收结论。
