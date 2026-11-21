# P1b 最小接缝实施补充

基线仍为审定 v0.2，SHA256 `3ef768759f4e7b7f786d9badf721f42fb661e5e7d728ab724daf118577df7f08`。不修改该蓝图字节，也不借旧 P1a 审计批准新源码。以下为当前实现切片，需独立代码复核。

## 三仓分工

| 仓库 | 基线 | 本地独立分支 | 变化 |
| --- | --- | --- | --- |
| native-harness-api | `5f1bfd6611a3d3ebe0f6dbb2f2c56ee303931deb` | `work/native-channel-p1b` | 可运行 Facade/Node、冻结 source/ledger 核对、heartbeat、真实 socket 集成 |
| alchemy-render-bridge | `cf1e72ec3830854fa0983eb9acb92fad62a604c8` | `work/native-harness-p1b` | 原 tasks/attempts/claims 账本的 typed harness 请求/结果/活性扩展 |
| sub2api-adapted | `2695b585aa7c2b9fed6fa50b9b8455c0ac2c1a14` | `work/native-harness-p1b` | 真实 APIKey 中间件、专用 native handler、PostgreSQL 操作绑定迁移、单次出站 |

各仓默认关闭。未合并、未部署、未进行付费模型调用。当前连接的是上述实现分支，而非宣称原 main 已支持本合同。

## 专用 Sub2API 路径

`POST /v1/native-harness/chat/completions` 使用受限 Chat Completions body/response 与现有 API Key。暂不接管普通 `/v1/chat/completions` 账户调度。原因是现有中间件在 handler 前已检查余额/配额，仅更换上游 URL 无法保证合法回放。

专用路径是首版安全切片，不等于普通渠道账户管理、模型列表、完整 OpenAI API 或收费已经接入。它仅允许明确配置的一名测试用户、一把 API Key、synthetic/fake 模型，无普通 provider 选择、重试、failover、余额预留或结算。停用用户/Key、IP 等认证限制仍生效；仅执行权限耗尽时允许已接收操作的合法回放，缺失操作不准入。

操作绑定仍属 Sub2API 的 PostgreSQL 数据库，不另建队列。唯一键 `(user_id, api_key_id, client_key)`，记录 operation ID、原始规范化摘要、冻结 origin/source/ledger/model binding、凭据引用。无 prompt、结果或 token 快照；墓碑不自动删除。真实数据库竞争/重启/恢复未运行，SQL mock 不能替代这一验收。

## 新增 wire 要求

- P1b Facade POST 额外要求 `X-Native-Source`、`X-Native-Ledger`。Sub2API 最后从冻结路由生成；Facade 在任何 Bridge IO 前与服务凭据绑定的 source/ledger 精确比对。凭据引用轮换不能静默迁往新账本。
- Node 新增 `POST /bridge/node/v1/harness-heartbeat`，body 为 `state` 与 `binding_digests`。心跳只申报存在/能力，不是 start grant，也不释放未知槽。已存在 journal 的只读恢复/结果重传先于心跳与当前能力准入，不能因下线 binding 阻断旧 ACK 恢复。
- 独立审计后收窄为 **Harness 专用实例/账本/执行资源**，明确不支持 image/Harness 独立 runner 混跑。两类心跳不互相覆写；Harness 配置拒图片心跳、准入和复用图片账本；默认图片实例保持原逻辑。不同实例没有跨实例物理槽围栏，不可共用同一执行资源。
- 原候选 source task 路径、claim/start、harness-observation/result 已在 Bridge 实现分支落地。文本结果不进入 image upload。
- Bridge 显式 `HarnessConfig`、NodePolicy 和 Facade 均只允许 synthetic/fake 测试绑定。本地原生模型入口没有因 HTTP 链通过而自动解禁。

## 仍不过门禁

真实 PostgreSQL 环境受阻，完整持久三仓闭环仍 NOT_RUN；WorkBuddy 官方选模/readback/终态、Windows 生命周期、生产隔离、真实 supervisor 迟到消费者围栏、备份回滚代际、账务/商业许可仍未完成。P1b 的真实 Bridge + FakeHarness 测试只能证明该合成执行链，不证明原生模型外部副作用 exactly-once。
