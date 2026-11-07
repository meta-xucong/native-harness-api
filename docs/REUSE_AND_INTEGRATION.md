# 源码复用与 Sub2API 接入

核对日期：2026-11-07。以下是仓库读取证据，不是生产部署版本证明，也不是已运行这些项目测试的声明。

## 1. 固定源码快照

| 项目 | 本轮读取快照 | 角色 |
| --- | --- | --- |
| meta-xucong/alchemy-render-bridge | `cf1e72ec3830854fa0983eb9acb92fad62a604c8`，main | 优先复用任务/节点/恢复基础 |
| meta-xucong/sub2api-adapted | `2695b585aa7c2b9fed6fa50b9b8455c0ac2c1a14`，main | 下游用户/API Key 与上游渠道集成目标 |
| RichardAtCT/claude-code-openai-wrapper | `a6c820d9a843f73c2e09aad3b4dead1544c25006`，默认分支 `add-claude-github-actions-1752711459735` | API 结构参考，不整仓部署或复制 |

正式开发开始时再次核对分支变化；未经差异审计不能把新的 main 自动当作这些证据相同的版本。

## 2. Render Bridge：真正值得复用的部分

已读取 [node_pull 合同](https://github.com/meta-xucong/alchemy-render-bridge/blob/cf1e72ec3830854fa0983eb9acb92fad62a604c8/docs/shared-contract/04_API_PULL_NODE_RUNTIME.md) 和 [README](https://github.com/meta-xucong/alchemy-render-bridge/blob/cf1e72ec3830854fa0983eb9acb92fad62a604c8/README.md)。合同明确 VPS 为唯一任务/产物账本，本地节点主动 HTTPS offer/claim/start/上传/确认，未知执行恢复同任务；当前仍是单节点串行，不是已有多节点调度。

本轮目录定位到以下代码入口，但未完成全量实现审计：

- [Node API](https://github.com/meta-xucong/alchemy-render-bridge/blob/cf1e72ec3830854fa0983eb9acb92fad62a604c8/src/render_bridge/node_api.py)
- [本地 Agent/sidecar](https://github.com/meta-xucong/alchemy-render-bridge/blob/cf1e72ec3830854fa0983eb9acb92fad62a604c8/src/render_bridge/agent/hatchet_sidecar.py)

采用方式：独立部署一个相同版本的 Bridge 实例，以 `node_pull` 运行；通过独立 PR 增加 `harness.text.v1`、可信调用归属、文本结果和原生状态映射。复用 claim/start/commit 的持久化机制，不新建相同作用的任务队列。

特别注意：现有 README 主要覆盖图片/视频任务且保留大量未验收说明；不能据此宣称原生文本 API、所有 Harness 或生产级存储已经完成。`node_pull` 禁止把同账本残留 Hatchet outbox 当作自动迁移；本项目不运行两个派发模式消费同一任务。

现有 Alchemy 链路允许的特定 pre-admission API fallback 不属于本项目能力；本项目始终禁止改走厂商直接模型接口。原有渲染业务策略保持不动。

## 3. Wrapper：参考接口，不继承执行与安全策略

读取 [main.py 1–240 行](https://github.com/RichardAtCT/claude-code-openai-wrapper/blob/a6c820d9a843f73c2e09aad3b4dead1544c25006/main.py#L1-L240) 和 [400–650 行](https://github.com/RichardAtCT/claude-code-openai-wrapper/blob/a6c820d9a843f73c2e09aad3b4dead1544c25006/main.py#L400-L650)。

可参考：`POST /v1/chat/completions` 路由、调用鉴权依赖、非流式响应结构、SSE chunk 外形。路由调用的是本机执行器；我们的 Facade 必须把该依赖换成 Bridge 客户端，VPS 不安装 `claude_cli` 或厂商认证管理。

本轮观察到的风险：

| 行为 | 本项目决定 |
| --- | --- |
| 未配置 Key 可交互选择不鉴权，异常时也可默认不鉴权 | 强制服务鉴权，缺 Key 不准入 |
| debug middleware 记录完整 headers 和请求正文 | 不复制；日志只允许脱敏元数据 |
| 默认禁用一组原生工具并压成一次调用 | 不照搬；工具/审批由受信 profile 与隔离环境控制 |
| 无流式正文时补通用回复后发 stop/[DONE] | 禁止伪成功；按原生终态/有效结果判断 |
| 非流式按文本估算 token 后填入 usage | 禁止把估算当实际用量 |
| main 导入并调用厂商认证模块 | 不能搬到 VPS；服务 Key 与厂商登录分离 |

上一轮源码索引还记录旧 SDK 依赖及执行代码修改全局环境变量的问题，本轮没有重审该实现，不将其列为经当前版本完整复现的缺陷。部署时不可整仓采用来跳过这些验证。

本轮根目录未见独立 LICENSE；上一轮 pyproject 曾声明 MIT，两者不构成完整复制许可核定。蓝图阶段不复制第三方代码，开发首选参考接口后实现薄外壳；后续确需复制则固定提交、确认许可、保留版权/NOTICE。项目名称中的 wrapper/proxy 不等于协议路线相同，必须看它实际执行路径。

## 4. Sub2API：本轮核实的实际接缝

上一轮引用的 `backend/internal/service/openai_chat_completions_service.go` 在本次固定快照读取返回 404，不能继续当开发入口。

本轮实际读取的是：

[openai_gateway_chat_completions_raw.go](https://github.com/meta-xucong/sub2api-adapted/blob/2695b585aa7c2b9fed6fa50b9b8455c0ac2c1a14/backend/internal/service/openai_gateway_chat_completions_raw.go#L1-L410)。该文件说明 APIKey 上游在相应能力路由下可直接使用 Chat Completions，不必先转为 Responses；SSE/JSON 有对应处理。源码同时显示：

1. `openaiCCRawAllowedHeaders` 只列 `accept-language` 和 `user-agent`，不能假定客户端 Idempotency-Key、租户身份能自动透传。其他层是否注入需继续核实。
2. 错误分支会进入 `failoverOpenAIUpstreamHTTPError` 等共享逻辑，不能假定增加一个普通账户就自动具备 Harness 的禁止重执行语义。
3. 流式路径会强制请求 usage，且有完整性、拒答、用量处理；unknown usage 与合法原生拒答必须专项联调，不能靠伪造 token/成功正文适配。
4. URL 委托 `openAIChatCompletionsTargetURL` 构建；base URL 是否带 `/v1` 必须捕获实际发出的请求核验，不凭管理界面文案猜测。

目录定位还包括 [openai_gateway_chat_completions.go](https://github.com/meta-xucong/sub2api-adapted/blob/2695b585aa7c2b9fed6fa50b9b8455c0ac2c1a14/backend/internal/service/openai_gateway_chat_completions.go)、[openai_chat_completions.go](https://github.com/meta-xucong/sub2api-adapted/blob/2695b585aa7c2b9fed6fa50b9b8455c0ac2c1a14/backend/internal/handler/openai_chat_completions.go)、[account.go](https://github.com/meta-xucong/sub2api-adapted/blob/2695b585aa7c2b9fed6fa50b9b8455c0ac2c1a14/backend/internal/service/account.go)。本轮仅摘查 account 相关方法，不构成网关全路径审计。

### 4.1 最小必要改造，禁止宣称“零改造保证可用”

在本 Harness 渠道的显式开关下完成：

| 接缝 | 要求 |
| --- | --- |
| 渠道路由 | APIKey 类型、自定义服务地址、明确 CC 路由、不自动 Responses 探测降级；优先显式能力配置，探测不能触发真实模型任务 |
| 身份 | 从已认证用户/应用上下文构建受信来源、租户、主体，去除用户伪造头 |
| 操作身份 | 稳定逻辑请求标识与 Idempotency-Key 到达 Facade，并跨同操作重试不变 |
| 重试隔离 | 本渠道禁用共享的新请求重试/切账号/切模型/切厂商；其他渠道行为原样保留 |
| 结果回传 | 允许受控任务/回放状态头；不得向用户透传本机内部标识或凭据 |
| 用量/计费 | unknown 不被默认为实际零；同操作回放不重复结算；未完成前只用不收费测试 Key |
| 终态语义 | 原生失败/审批/未知状态明确返回，不因拒答、空结果或超时自动生成替代回答 |

这些是开发要求，尚未新增管理端字段。实施时优先复用既有配置接缝，只有缺少明确能力才添加小范围标记；不能凭本文把不存在的 JSON 字段写进生产配置。

首个 Sub2API PR 应只包含 Harness 路由小接缝和隔离回归测试；不得借机重构全平台重试、账户优先级、价格、缓存或其他供应商协议。

### 4.2 配置含义，不是可直接导入的账户 JSON

上游地址指向自建 Facade；上游 Key 是 Facade 服务 Key；用户仍拿 Sub2API 用户 Key；示例模型别名为 `native-codex` / `native-claude-code`，仅验证通过的 profile 启用。WorkBuddy/豆包不凭名称自动出现。

下游用户不能选 endpoint、原生登录账号或工作路径。首版一个测试租户/单槽/非流式/不计费/关闭自动重试。使用 mock HTTP 捕获确认路径仅到 Facade `/v1/chat/completions`，未触达任何厂商域名。

## 5. 原生执行候选与证据等级

以下来源来自 2026-11-06 的源码索引，本轮未重新固定其最新版本，不作为当前 SDK 参数或服务授权证明：

| 项目 | 参考入口 | 决策 |
| --- | --- | --- |
| [openai/codex](https://github.com/openai/codex) | `sdk/typescript/README.md`、`sdk/typescript/src/exec.ts`、app-server 文档 | 优先官方原生入口；实施时固定版本和许可 |
| [anthropics/claude-agent-sdk-python](https://github.com/anthropics/claude-agent-sdk-python) | `src/claude_agent_sdk/_internal/transport/subprocess_cli.py` | 本地 CLI 子进程实现方向，授权与审批另验 |
| [gosick233-cloud/Codex-WorkBuddy-Desktop-Bridge](https://github.com/gosick233-cloud/Codex-WorkBuddy-Desktop-Bridge) | `workbuddy_bridge/acp.py` / `server.py` / `multiplexer.py` | 本地入口研究候选；不继承 fullAccess/自动批准，不认定稳定公开接口 |
| [jie023/workbuddy-acp-bridge](https://github.com/jie023/workbuddy-acp-bridge) | `src/workbuddy_acp_bridge/` | 行为对照；共享全局会话不适合用户隔离，复制前许可核定 |
| [tiann/hapi](https://github.com/tiann/hapi) | 原生会话/hub/runner | 替代底座候选，不与现有 Bridge 叠加为双调度 |
| [pywinauto/pywinauto](https://github.com/pywinauto/pywinauto) | Windows UI Automation | 底层工具参考，不能替代厂商许可/可靠终态；不列入首版依赖 |

不采用任何读取厂商登录凭据后从云端直接请求其内部模型接口的项目。豆包仍缺已核实 Adapter；这是当前证据缺口，不是声称不存在相关实现。

## 6. 三个仓库的工作边界

`native-harness-api`：本次文档；后续 Facade、Harness Adapter 与合同测试。

`alchemy-render-bridge`：通用文本任务类型、可信归属、文本结果/状态映射；通过小范围兼容 PR，不破坏渲染任务。

`sub2api-adapted`：新渠道的身份/幂等/禁止重执行/计费语义接缝；通过独立 PR，所有正常渠道的旧测试不变。

本次没有对后两个仓库写入、更改配置或部署。任何“API 接通即完成”的验收结论都必须同时满足 [端到端清单](DEVELOPMENT_AND_ACCEPTANCE.md)。
