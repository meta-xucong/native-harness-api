# 开发代理与协作者约束

本分支当前为 P1b 接缝开发，真实 Bridge HTTP 可测试，Sub2API PostgreSQL 完整闭环未验收，模型执行仍仅 synthetic。先读 README、blueprint/BLUEPRINT-v0.2.md、blueprint/P1B-ADDENDUM.md、blueprint/DESIGN-AUDIT-v0.2.md、code/README.md、code/docs/HTTP_CONTRACT.md 和 code/docs/IMPLEMENTATION_STATUS.md。docs/ 与 audit/ 是 v0.1 历史快照，不覆盖审定 v0.2。代码与蓝图必须分放 code/、blueprint/。

P2 独立本机试验工具另见 blueprint/P2-LOCAL-TRIAL-ADDENDUM.md 和 code/docs/WORKBUDDY_LOCAL_TRIAL.md。仅允许本机用户显式选择官方 WorkBuddy bundle、模型回读和一次 prompt；不放开 P1b synthetic 网络渠道，不读取厂商凭据，不自动推理/重试/永久审批。云端开发只跑离线协议 fixture，真实账号运行由本机用户明确确认。打包保持固定官方 Python SHA/许可和精确源码 revision，不能用可下载包本身冒称真实 WorkBuddy 已通过。

## 不可变边界

- 本地官方 Harness 执行模型调用；VPS 只做任务和 API 包装。禁止提取厂商凭据后自己请求内部模型接口，禁止云端直接推理 fallback。
- Sub2API 管用户/Key；Bridge 是唯一执行账本；Facade 不新建第二派发队列，PC journal 只作同任务恢复证据。
- 原生工具与审批保留；不默认全关工具，也不默认 fullAccess/自动批准。
- 同任务已可能开始时禁止重新生成；未知状态、网络超时、租约过期不是安全重试依据。
- 回放先于新准入；节点离线不能阻断已完成结果的合法回放。结果过期只能报过期，不能重跑。
- 首版一个可信测试租户、单节点、总槽数 1、每请求独立会话、非流式、非收费。目录/session 隔离不代表多租户安全。
- 每次调用归属由 Sub2API 认证上下文产生，不信任客户端 user/session_id/内部身份头。
- 空输出不得伪成功；usage 不明不得伪造零或精确估值；不能将本地已执行工具事件发成远端待执行 tool_calls。

## 工作方式

先固定三仓库实际提交，与蓝图快照做差异审计。只改最小必要接缝，保留已有未提交工作；禁止 reset/clean 原项目，禁止读写生产账本。蓝图没有宣称存在的 API 字段不得凭空配置。

本仓库放薄 Facade/本地 Adapter；测试假网关/假账本只在 code/tests/fakes/，不能成为生产 fallback。P1a 不声称真实 Bridge/Sub2API/WorkBuddy 已接通。Bridge 和 Sub2API 的必要改动进入各自独立 PR，默认关闭。不要为了接入重构全平台调度、权限、计费和供应商策略。

许可证未核定的外部代码只参考不复制；引用来源固定版本并保留版权/NOTICE。使用官方软件或开源 SDK 不等于获准多用户商业转供。

未经后续明确授权，不合并 PR、不部署 VPS、不安装或运行第三方可执行程序、不触发真实付费模型任务、不发布给不可信用户。不要把此文件当成无限执行授权。

## 证据与交付

运行 `node audit/check-blueprint.mjs` 只能验证文档和抽象状态模型，不代表产品测试。实现阶段另写可运行服务测试，执行 docs/DEVELOPMENT_AND_ACCEPTANCE.md 的适用项。

每次报告分开列出：改动、运行过的命令/测试及实际结果、仍未验证的项目、是否有真实模型费用、是否有部署。没有测试就写 NOT_RUN，不能借用历史通过数字。

修改协议要同步蓝图、合同、验收和审计，保持链接有效。只更新任务相关文档/代码；不要把本机路径、登录信息、令牌、聊天记录或客户数据写入仓库。

## P1a 专项约束

- 模型身份必须来自可核实原生配置/运行证据；FakeHarness 只用明确 synthetic/fake 名称。
- 观察错误保持 pending/unknown，不提前写不可变 failed。结果终态与执行活性分别处理。
- intent 后旧 worker 可能迟到启动；不能仅凭无进程/旧 epoch/租约超时释放槽。当前无强制释放入口，保守隔离未知槽。
- 同键在首次未准入、渠道变更和重启后仍冻结原路由；再次准入仍检查当前执行权限。
- 标准 API 语义与原生 Agent 能力分开，未知用量不填零。
- 最终提交前跑 code/ 的实际测试及离线 demo；文档抽象脚本通过不能代替它们。

## 后续显式 local-trial 渠道

用户要求完成代码后自行部署/认证。单用户原生试验模式的新合同见 `blueprint/LOCAL-TRIAL-CHANNEL-ADDENDUM.md`；该明确模式扩展上文 P1b synthetic-only 门禁，不改变默认模式或生产限制，也不代表真实账号/模型已验。
