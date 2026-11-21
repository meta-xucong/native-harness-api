# Sub2API 模型渠道的工具与审批边界

当前渠道提供 **按模型绑定的本地 Harness Agent 文本调用**，不是完整的
OpenAI Chat Completions / Responses 工具调用实现。真实账号及实际模型、工具
行为仍需用户部署后验收，不能用离线 fixture 通过代替。

## 已实现的请求合同

- 仅一条 `user` 文本消息、一次模型选择回读、非流式单次执行。
- `tools`、`tool_choice`、`parallel_tool_calls`、旧式 `functions` /
  `function_call` 等参数返回 400，不会静默忽略。即使 `tool_choice: none`
  或空工具列表也不接受，避免误解为已实现该 API 语义。
- `tool` / `function` 消息、assistant `tool_calls` 不接受；本地文本首版也不
  接受 system/developer、多轮拼接、多模态。不要让 Sub2API 把不支持的请求
  伪装成普通文本转入，也不要为此渠道启用失败后自动换渠道重新生成。
- 只返回已经核验的最终文本或拒绝文本；usage 未知，不伪造 token 计数。

## 原生工具不是下游 tool_calls

WorkBuddy/SFE 内部工具可能由官方 Agent 自行选择和执行。这不同于向下游
客户端发出待执行 OpenAI `tool_calls`。已经在本机执行的工具事件不能再转换
成下游待执行调用，否则可能重复执行文件写入、命令或其他副作用。
本实现不做这种映射，也不返回工具调用 ID 供客户端提交伪续接结果。

**取消审批不等于关闭所有原生工具。** 无需审批或官方策略已许可的原生操作
是否执行取决于官方 Harness 的实际行为，目前未作真实模型验证。
`strictMcpConfig`、独立 workspace、独立 profile 都不等于操作系统沙箱，
不能据此提供不可信多租户服务。请使用专用 Windows 用户环境和最小授权目录，
不要将敏感工作目录作为模型 workspace。

## 遇到交互审批时

当前非交互 session 收到 `session/request_permission`，只返回
`{outcome:{outcome:'cancelled'}}`；收到 `elicitation/create`，只返回
`{action:'cancel'}`。不会批准一次或永久权限，不打开远端审批页面，也不会把
原生请求内容、URL 或凭据转发给 Sub2API 客户端。该任务随后保守进入不确定
状态，并尝试 session close / EOF / owned Job 清理。后续出现的完成事件也不能
把已拒绝的交互变成成功。

这不是“等待用户稍后批准”的可续接 UI。当前 provider 不支持恢复审批、在
Sub2API 界面点击批准或继续同一 native turn。账本已有执行 intent 的未知任务
保持 pending/unknown，不能删除 journal、换 grant、重启或重发来自动再生成。
权限取消本身不能证明此前没有发生原生副作用。

## 未来若实现交互工具支持

必须独立设计本机可信审批 UI、逐请求/session/root 绑定、超时与撤销、断线恢复、
数据最小化和完整审计，再决定哪些交互适合远端 API。下游 function calling
则需要独立的严格协议和端到端执行归属设计，不能通过字符串重命名或伪造
`tool_calls` 来补齐。以上均不包含在本版能力声明中。

开发回归：`test_public_sfe_tool_contract.py` 验证不支持的下游参数/消息在
native invocation 前被拒绝，审批取消不会成为成功或外部工具调用。
这些测试仅使用自写模拟协议数据，不运行官方程序或真实工具。
