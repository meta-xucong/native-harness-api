# 三仓部署链：VPS 服务，Windows 仅出站

本文描述代码支持的部署组成，不代表已部署、已接通生产 404token 或已完成真实账号/模型验收。不要直接替换已有 Render Bridge 镜像、配置或数据库。新 Harness 必须使用独立 Bridge 数据目录：旧图像账本不能原地转为 Harness 账本。

## 拓扑

```text
API client -> Sub2API /v1/native-harness/chat/completions
              -> 127.0.0.1:8790 Facade -> trusted HTTPS Bridge
Windows Node --------------------------> trusted HTTPS Bridge
Windows Node -> official local Harness -> user's WorkBuddy account
```

Bridge、Facade 在 VPS；Windows 只向 Bridge 发起 HTTPS 请求，不开放入站端口、不做内网穿透。Facade 没有原生资产、Windows profile 或 WorkBuddy 凭据，也不会执行模型。

- Sub2API 是宿主机进程：Facade 同样在宿主机，使用 `code/deploy/native-facade.service` 模板。
- Sub2API 是 Docker：Facade 必须与它共享网络命名空间，使用 `code/deploy/facade-sidecar.example.yaml` 合并片段。`network_mode: service:sub2api` 中名称必须匹配真实 Compose service。不要把容器自己的 `127.0.0.1` 误当成宿主机；也不要用未加密 `http://host.docker.internal` 绕过约束。共享网络命名空间不等于共享环境变量或文件系统，双方仍分别挂载配置和传递密钥。Sub2API容器重建后必须同时重建Facade并验证它附着于新网络命名空间；普通depends_on不保证自动跟随已替换的容器。
- Facade 默认只绑定 `127.0.0.1:8790`。不向公网发布该端口。Bridge 在宿主机 `127.0.0.1:8787`，前面使用已有 Render Bridge 的 `config/source-https.nginx.example.conf` 模板配置可信 HTTPS，仅代理 `/bridge/v1/` 和 `/bridge/node/v1/`，不自动重试请求。域名、证书及网络规则需管理员按真实部署确认。

## 1. 固定代码与数据库，不复用旧 image 模式账本

使用包含 `workbuddy_local_trial` 的三仓代码，不能用 upstream/latest 镜像代替自己的分支。不要自动 merge、升级生产或清库。Sub2API migration 248 随应用正常迁移流程处理，先按既有生产流程备份并审查；操作路由表必须持久保留。Bridge 和 Windows journal 同样不得删除来解决重试问题。

Facade Docker 构建命令（仅服务器 stdlib 源码，无 vendor 资产）：

```sh
docker build -f code/deploy/facade.Dockerfile -t native-facade:YOUR_REVIEWED_REVISION code
```

把构建产物的不可变 digest 配入 `NATIVE_FACADE_IMAGE`；Dockerfile 的官方 Python 版本标签不是镜像 digest。已有 Sub2API 服务要使用对应审查分支构建的镜像，并保留原数据库、Redis、端口、卷和其他配置，不用示例覆盖它们。宿主机部署用独立 Python 环境及仓库的 pinned requirements 安装 Bridge；Facade 本身无第三方 Python 依赖。

## 2. 只填写一次真实信息，生成一致配置

先在 Windows 运行客户端的显式 login 和 models 命令；这是用户本地动作，不读取旧桌面凭据。记录 profile 路径，并从目录选一个或多个明确模型 ID。用户/API-key 的 numeric ID 从现有 Sub2API 管理后台对应用户及 key 的记录取得，不填 API-key 字符串，不猜数字；示例的 7/9 必须换成真实 ID。

复制 `code/deploy/deployment-spec.example.json`，修改 HTTPS Bridge origin、真实 user/key ID、模型 alias/native ID、Windows 安装/profile 路径和独立 VPS 数据目录。执行：

```sh
python code/tools/prepare_native_deployment.py --spec YOUR-SPEC.json --output NEW-PLAN-DIRECTORY
```

程序不联网、不登录、不部署、不生成模型请求，也不生成或保存密钥；新目录已存在时拒绝覆盖。只在首次建立渠道生成一次：`deployment-identities.json` 内 source、ledger、workspace、node、executor、binding 必须稳定保存。不要为未知任务重新生成身份或重建目录。

输出：
- `bridge.json` 放 VPS Bridge，设置专用 node_pull Harness 账本。历史 runtime 要求的 `fake-image` 配置项在 Harness 账本中不启用图像执行；Harness 模式会拒绝 image API，不存在 fake 推理回退。
- `facade.json` 放 VPS Facade，无 Windows 路径，使用 `adapter=facade_only`；误用它启动 Node 会拒绝。
- `windows-node.json` 放 Windows，使用完整包 assets 和刚创建的私有 profile。
- `sub2api.native-harness.yaml` 是 JSON 语法的合法 YAML **合并片段**，只把 `gateway.native_harness` 合入现有 Sub2API 配置，不要替换整个文件。用 `CONFIG_FILE` 指向实际挂载后的合并配置；仅设置 ENV 不能凭空建立动态 routes map。

生成文件在Linux默认600，仅创建者可读。部署时按实际服务身份授予最小读取权限：宿主systemd的native-harness用户或容器UID65532必须能读对应facade.json；可用专属组和640配置权限，或将非秘密配置所有者设为服务用户。不要为了修复读取失败把secret文件改成全局可读。Compose的env_file由运行Compose的受信管理员读取并注入，不能挂载到Windows或公开目录。

生成器自动把数字 IDs 映射成 `user-<ID>` / `key-<ID>`，并在三仓使用同一组完整 bindings。生成的所有新请求开关都关闭，Sub2API 功能 enabled 但不准入新请求。

## 3. 三段服务密钥分别配置

为这次接入建立三个不同的高熵服务密钥。只用受限本地文件或部署系统 secret 管理，不写入公开仓库、JSON、命令行参数、截图和工单。不要复用 WorkBuddy token 或终端用户 Sub2API key。

| 相同秘密值 | 两端环境变量 |
| --- | --- |
| Source secret | Bridge `BRIDGE_SOURCE_TOKEN` = Facade `NATIVE_BRIDGE_SOURCE_TOKEN` |
| Node secret | Bridge `BRIDGE_NODE_TOKEN` = Windows `NATIVE_BRIDGE_NODE_TOKEN` |
| Facade secret | Facade `NATIVE_FACADE_TOKEN` = Sub2API `NATIVE_FACADE_SERVICE_TOKEN` |

Windows 只需要 Node secret，不能复制 VPS 的 Source/Facade secret。Facade 的 env 文件只放 Source/Facade 两项。Docker 的 `environment`/`env_file` 必须显式传入对应值；宿主机终端变量不会自动进入已有容器。Bridge node_pull 不需要 dispatcher secret。

## 4. 先无推理预检，再开新请求

先保持所有 admission=false。启动 Bridge HTTPS 和 Facade。在 **Facade 所在网络命名空间** 内运行：

```sh
PYTHONPATH=code/src python code/tools/public_harness_preflight.py --config /etc/native-harness/facade.json
```

检查 Bridge contract/ledger 和 Facade GET `/v1/models` 的认证。Docker 在同 sidecar 内运行 `python /opt/native-harness/tools/public_harness_preflight.py --config /etc/native-harness/facade.json`，不用宿主机 curl 代替。关闭 admission 时返回空模型列表是正常情况。

Windows 运行：

```text
Client.cmd preflight --config C:\path\windows-node.json
```

该检查验证资产、profile 元数据、Windows→HTTPS Bridge 的 Node 认证；最多读取已排队任务，**不 claim、不 start、不运行官方 Harness**，不输出任务内容。它不能证明实时厂商认证或模型可用。

可选的真实 Sub2API 认证预检：在运行预检的终端仅设置 `NATIVE_SUB2API_TEST_KEY` 为允许的用户 API key，再给同一命令增加 `--sub2api-origin https://你的404token域名`。只 GET `/v1/native-harness/models`，验证真实 API-key middleware/单owner允许列表和当前路由配置，不请求模型。`sub2api_accepting_new=false` 时列表为空正常；`sub2api_live_readiness_checked` 必须为 false。未提供该参数时完全不读取该 key。此入口须使用包含只读models接口的Sub2API版本。`checks_passed` 只表示本地配置及已执行网络检查通过；即使退出0，也不表示模型已就绪、实时厂商认证成功或端到端推理通过。profile元数据未确认则退出1；关闭admission时模型未列出不被当成认证失败。

启动 Windows Node：`Client.cmd node --config C:\path\windows-node.json`。保持此进程运行，让 Bridge 收到匹配 binding 的心跳。确认独立试验渠道和单 owner 后，管理员再显式打开以下开关并正常重启/重载对应服务：Bridge 顶层 `accepting_new_tasks`、`harness.accepting_new`；Facade `accepting_new`；Sub2API `gateway.native_harness.accepting_new`。不要改稳定身份或 ledger 路径。Node 配置的 accepting_new 不是远端准入授权。

## 5. 第一条真实操作与恢复

默认请求走专用 `/v1/native-harness/chat/completions`。生成器显式输出 `standard_api:false`、`standard_wait_seconds:180`。可由管理员对该专用允许列表 Key 显式开启 `standard_api:true`，使客户端使用 `base_url=https://你的Sub2API/v1` 的 `/v1/chat/completions` 和 `/v1/models`；这不是普通 provider 管理 UI，也不改变其他 Key 的路由。使用允许的 Sub2API API key，body 仅单条 user 文本、`stream:false`、`n:1`，model 为匹配的 `workbuddy-*` alias。为一次逻辑操作保存一个稳定 `Idempotency-Key` 和原始 body。

Facade 的有限等待可能先返回 504。此时不能生成新 key、换模型或换 body；稍后仅重发**同 key、同 body**获取相同操作的状态/结果。相同请求不会换路由重跑。未知进程或 cleanup 情况需要保留 owner 进程与全部账本，不能删除 profile、清库或新建任务伪装重试。

当前是单 owner、单槽、非收费、单 user 文本模式；专用路径仍非流式，标准路径可选下述实时原生事件SSE；下游工具 schema、tool-role 和需要原生交互审批的请求不支持。目录/账号隔离不是多租户安全沙箱，官方程序可能运行不需要额外审批的原生工具。完成这些软件配置不证明商业转供许可或生产服务已验收。

## 多模型共用一台电脑和一个账号

首次 spec 可用 `models` 列表，每项仅 `model_alias` 与 `native_model`，最多32项；每个别名独立生成 Sub2API route、冻结 binding 和 Bridge 能力，但 Node/profile/journal/账号/物理执行槽只有一个。`model_alias` 必须唯一且以 `workbuddy-` 开头。旧顶层单个 model_alias/native_model 仍兼容；不得同时提供两种形式。

生成器以保存的 binding identity seed 和别名生成稳定binding ID，列表排序不改变 ID。配置加载接受 `bindings` 数组或旧 `binding` 单项，不能混填。Facade列出全部准入别名，Node心跳一次报告全部绑定。不同模型不是并行槽：Runner持有同一OS锁直到当前原生调用及证据写回完成；Bridge仍以现有单槽规则拒忙。未决/未知执行将阻止所有模型继续启动，不自动跨模型重试或复制profile来避开锁。

后续修改映射时先停止新准入并处理已有任务；保持source、ledger、Node/profile/journal和binding身份，改变某映射语义必须升级对应revision并同步三仓，不重新生成ledger来重试。移除模型只禁止新任务，已有冻结操作仍优先只读恢复其证据；缺少证据永不触发重新推理。多模型路由不代表多租户/计费/外部工具调用兼容，也不代表真实模型已经实测。

可选Sub2API GET预检的 `sub2api_model_listed` 现表示所有本地配置别名都出现在返回目录中，而非仅第一项。关闭admission时可为false；该检查仍不做推理或证明实时账号状态。

## 标准路径的等待、SSE与排空

`standard_wait_seconds` 默认为180秒，允许0–300；0表示不追加观察。仅明确504 `TASK_WAIT_TIMEOUT` 后，在同一请求预算内以只读方式观察同一冻结任务，不创建新任务或跨provider重试。客户端断连只停止观察，不代表原生任务取消。客户端及Sub2API前置反代超时必须覆盖配置等待预算与网络余量（180秒配置建议至少240秒），应由部署管理员验证实际每一跳。

标准路径 `stream:true` 缺省为 **openai-append-v1** 普通 data SSE / choices.delta，响应 X-Native-Stream-Mode 标识模式。只将可追加原生快照的真实新增后缀发送给 SDK；已发文本在最终核验前均为临时内容。原生缩短/改写或最终不一致时发 NATIVE_STREAM_REWRITE_REQUIRED 标准 error 并关闭，无成功结束帧或 [DONE]；原 Key/正文/Idempotency-Key 改 stream:false 恢复权威结果，不重新执行；切换 stream:false 时须移除显式协议头，否则返回 STREAM_PROTOCOL_REQUIRES_STREAM。普通 SDK 必须显式配置稳定幂等头，建议 max_retries=0；不是任意客户端零配置兼容。任务状态在标准流中仅为注释，SDK通常忽略，不保证显示审批/unknown。未知usage不伪造。

要显示完整快照更正和任务状态，请显式请求 `X-Native-Stream-Protocol: native-events-v1`。此模式每条命名持久事件使用Bridge seq作为id；text_snapshot覆盖全部文本（可缩短/更正/清空），native_status/task_status反映观察事实，idle/end_turn不证明完成。只有验证后的completion或task_error是结果终态。该显式模式不能交给仅识别 choices.delta 的 SDK。

断线保持同Key/正文/Idempotency-Key并携带 Last-Event-ID 只读恢复。标准流仅允许0从头重放，正游标在准入前拒绝；显式原生流支持正游标并按id去重。没有该头的请求仍去重但会重新检查准入，不称只读恢复。两模式共用规范操作摘要，切换stream或协议不新生任务。原生模式无id的observation_error仅表示观察中断，observation_end表示终态游标到达；标准模式error后关闭无成功终止帧。EOF从不等于成功。原生日志不是全部原样公开，模型正文不保证自动脱敏。完整合同见 NATIVE_EVENT_STREAM.md；SDK示例见配套Sub2API源码 docs/native-harness/STANDARD_API_OPT_IN.md。

流模式 `standard_wait_seconds` 须1–300，默认180；预算或单次写入超时只中断观察，不取消/重派原生工作。前置代理必须禁用响应缓冲/缓存/上游重试，允许SSE并合理配置read timeout；`: observing`仅为连接保活注释，不证明WorkBuddy仍在生成。专用native-harness非流式接口仍不接受stream:true，stream_options尚不支持。

排空必须设置 `accepting_new:false`，同时保留 `standard_api:true` 和原专用Key/owner，直到已受理和unknown任务恢复结束。不能通过关闭standard_api或更换allowlisted Key来排空，否则会撤销标准路径保留、使旧请求进入普通路由；确需撤销先禁用该Key，禁止将它重用于普通provider。两种路径共用操作身份，恢复始终使用同Key/同正文/同Idempotency-Key。完整合同见配套Sub2API源码 `docs/native-harness/STANDARD_API_OPT_IN.md`。
