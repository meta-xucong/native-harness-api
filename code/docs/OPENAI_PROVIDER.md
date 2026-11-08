# 原生 Sub2API 普通 OpenAI 上游接入

本入口只改 Native Harness。原生 Sub2API 无需专制分支、私有 header 或数据库扩展；Node、Bridge 单账本、WorkBuddy/Codex 原生执行保留。

**范围：单可信资源域、单 Node 总槽 1、非收费试验。** 只支持一个 user 纯文本 message，非流式或标准 SSE，不是完整模型 API。真实账号、模型、用户部署与商业许可未验收。代码及测试状态见 [本次测试记录](OPENAI_PROVIDER_TESTS.md)；设计见 [蓝图](../../blueprint/NATIVE-OPENAI-PROVIDER-BLUEPRINT.md)。

## 首次设置

已有 `windows-node.json` 的用户继续选择原配置恢复，不重新登录/生成部署身份/删除 journal。新连接向导默认生成普通 provider，不再询问 Sub2API 用户或 Key 数字 ID。生成结果：

- 本机保留 `windows-node.json` 与 `deployment-identities.json`，使用原 Node 单槽及任务目录。
- 服务端配置 `bridge.json`、`facade.json`；`openai-provider.json` 是无秘密的操作说明，**不是 Sub2API 账号导入文件**。
- 新 Facade 是 `ingress:"openai"`、`wait_seconds:180`；owner 是固定 provider 资源域，不是终端用户。缺省 ingress 的旧 Facade 仍走原私有合同。
- 所有准入默认关闭。账号/官方资产/模型绑定由已有本机流程核验；生成文件不会部署、创建密钥或调用模型。

已有服务器可由操作者为同一 source/ledger/owner/binding 配置另一个普通 Facade 入口，不把 source/owner/ledger 换掉当作恢复。旧专用操作的 key 映射不同，仍通过原入口恢复。不要运行第二个 Node 操作同一资源域。

## 服务端配置

使用当前 Native 源码及已审 Bridge `52dc083207a8a02dfc14fe2ec1ea10f0cc0a54a8`。无需原 `sub2api-adapted` fork。

在服务的受控环境中配置原有 `NATIVE_BRIDGE_SOURCE_TOKEN` 和 `NATIVE_FACADE_TOKEN`，两者必须不同；Node 使用独立的 `NATIVE_BRIDGE_NODE_TOKEN`。工具不生成、保存、打印或复制密钥。

```sh
cd native-harness-api/code
PYTHONPATH=src python -m native_harness_api.runtime facade --config /operator/path/facade.json
```

配置示例为 `code/examples/openai-provider-facade.example.json`。必须复制自己已生成且三方一致的绑定，不把示例 model/binding 当成真实型号。Bridge 的 `accepting_new_tasks` 与 `harness.accepting_new`、Facade 的 `accepting_new` 由操作者在准备好后显式打开，Node 沿用原配置及本机授权。不要改 production/多槽限制。

Facade 只监听 `127.0.0.1:8790`。使用既有反向代理提供 HTTPS，流式时关闭代理缓冲，并使请求/读取超时大于观察预算。**Sub2API 容器的 127.0.0.1 通常不是宿主机**，Base URL 必须从该容器可达。旧 `facade-sidecar.example.yaml` / 三仓源码包是专用渠道历史方案，不是本普通入口的必需配置。

## 原生 Sub2API 管理界面

已按 `Wei-Shaw/sub2api@6b4b37db43443b5e88e424fe3c69602cc3810dfb` 核查：

1. 新增普通 **OpenAI / API Key** 账号，而不是 OAuth 或 Native 自定义渠道。
2. Base URL 填操作者配置的 `https://<provider-host>/v1`；API Key 填该服务的 `NATIVE_FACADE_TOKEN` 值，不是 Bridge token 或下游用户 Key。
3. 使用已有 **Force Chat Completions** 模式，对应 `extra.openai_responses_mode="force_chat_completions"`。否则默认可能走尚不支持的 Responses。不要误填国产渠道的 `credentials.api_protocol`。
4. 模型映射目标设为这个 Provider `GET /v1/models` 实际列出的别名。`codex-local` / `workbuddy-local` 只在自己确实配置该别名时可用。
5. 使用独立可信试验账号组、避免 failover，调用端关闭自动重试。不要把未知 token 显示为零理解成真实免费用量或开启收费。

不需要 X-Native 头。Provider key只能识别固定资源域，不能识别下游用户；客户端 user字段和自填身份header不能提供多租户隔离。不要用静态header override塞一个公共 Idempotency-Key，否则不同请求会相互冲突。

`GET /v1/models` 仅查询配置，不调用模型。普通 Sub2API 的“测试账号/探测能力”可能实际发起模型请求，只有用户明确准备好后自行操作。不要用它冒充无推理预检。

## 请求与流式

```json
{
  "model": "<真实配置的别名>",
  "messages": [{"role": "user", "content": "你的请求"}],
  "stream": true,
  "stream_options": {"include_usage": true}
}
```

支持 n缺省或1；stream缺省/false返回JSON。未知参数、system/developer、多轮、tools、图片/音频、采样参数返回400，不静默忽略。`/v1/responses`为404。

原生 Sub2API会自动加入 `stream_options.include_usage=true`，Native接受它，但真实完整用量仍unknown，**不会输出伪造的usage或零token**。Sub2API当前内部会把缺失计量记为零值；它不是Native测量证据，也不代表已具备准确计费。

SSE是标准choices.delta追加投影，不是厂商原始token流。快照缩短/改写或最终不一致时返回`NATIVE_STREAM_REWRITE_REQUIRED`错误然后关闭，无finish_reason、usage或[DONE]。终态验证成功才输出stop与[DONE]。心跳/状态只在注释中，普通SDK可能不显示；工具审批仍在本机，不会自动放行。原生Sub2API可能追加截断错误，不应把EOF或临时文字当完整成功。

## 重试与恢复限制必须保留

**原生 Sub2API普通raw CC不透传 Idempotency-Key。** 因而每次上游HTTP尝试都是新operation，包括网关/SDK重试。Bridge只保证同operation恢复，不保证整条普通网关路径exactly-once。相同prompt不能被偷偷合并。

- Provider自身不重试提交、不切渠道，不新增执行队列。
- HTTP409只是已受理/观察冲突语义。常见OpenAI SDK也会自动重试409，应显式设`max_retries=0`；这仍不能控制网关的全部failover/网络行为。
- 观察超时/断线不会取消已接收任务。经原生Sub2API失败后，**不要认为重发就会恢复同一任务**；未决时先检查本机/Bridge状态，不能换key或删journal“解锁”。
- 直接访问Provider且保留同一Idempotency-Key时，stream/user/include_usage不影响执行摘要，可改stream=false读取原任务最终结果；换正文/model会409。节点离线、当前binding下线或准入关闭不阻断已完成任务回放。结果过期410，不重新生成。
- 直接流只读重放可携带`Last-Event-ID:0`及同key；缺任务不创建。正游标不支持。普通Sub2API不提供这个端到端恢复保证。

## 许可与未验证项

Codex官方 App Server认证说明限制商业/托管用途；本机复用登录、开源许可或技术调用成功都不是404token商业转供授权。WorkBuddy条款和第三方许可证需分别核定。本轮不提取认证凭据、不调用厂商内部接口、不运行真实模型、不部署生产、不开放收费。

观察循环与HTTP写出共用同一截止时刻，末尾错误flush预留在预算内，不加额外1秒。Bridge单次IO仍使用既有有界超时，正在进行的IO可能晚于观察截止才返回；这不是端到端硬实时deadline。预算用尽或客户端已断开时可能只收到不完整EOF，没有成功finish/[DONE]，不代表任务取消。
