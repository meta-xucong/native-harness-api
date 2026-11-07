# P2 续作：真实使用完成门槛与当前可实施范围

2026-11-07。用户纠正交付标准：不能把本机实验探针当成已开发完毕、无障碍可直接使用的完整程序；应先完成真实使用所需开发、审计和验证，再请用户操作。原 P2 试验包及其审计只覆盖显式单次本机试验，不提升为正式渠道验收。开发继续在云端进行，不访问用户电脑，不绕过已知 PostgreSQL 权限拒绝，不部署生产或触发付费推理。

基线：Native `64dbee298d321461bf72aee81ac67fa238382404`，Bridge `91ad18b2612899860edf89d985fe9d5ca638d0a1`，Sub2API `bf8c8143246ae59a6c95db91eb3a580e3332ea08`。本文件是待独立审阅的续作计划，不是门禁已通过声明，不修改 v0.2 原稿或现有 wire 合同。

## 一、不能靠修改名称消除的缺口

| 门槛 | 当前事实 | 完成所需证据 |
| --- | --- | --- |
| 官方 WorkBuddy 入口 | 有公开 bundled CLI/ACP 文档；用户安装结构和登录继承未核验 | 官方发行物结构、支持版本和启动参数；获准实机上无需抽取凭据的启动及登录行为 |
| 固定模型绑定 | session 选择与回读已有代码；原型结果验证仍要求 selected/reported 均匹配 | 证据来源分级、无静默 fallback/换模证明、重启与竞争修改测试；未证明不得启用精确型号别名 |
| 连续请求安全释放槽 | ACP 终态和 parent 退出不证明原生后台工具停止 | 受监督进程所有权与回收证据，以及原生外部 broker/后台行为的真实验证；未知仍隔离 |
| 真实 Adapter | 现有 Runner 只创建 FakeHarness，probe 只有一次总试验 | 按 Bridge task/grant 的 start 与只读 reconcile，持久 prompt intent、结果和恢复，不引入第二派发队列 |
| API 语义 | 当前一般文本 messages 不能直接等价映射 ACP 单 prompt | 实际支持的输入角色/轮次逐项验证；不支持的明确拒绝，不把拼接文本假称标准消息合同 |
| 三仓准入 | Native、Bridge、Sub2API 均仍限制 synthetic/fake | 版本化真实 capability/binding 与互相校验，不得简单删除前缀门禁 |
| 404token 正常入口 | 目前只有专用 native 路径 | 普通模型列表/CC 的明确渠道分流、可信用户归属、冻结路由、禁用重复生成 failover |
| 持久数据库 | 真实 PostgreSQL migration/竞争/恢复没有运行证据 | 明确允许的测试服务上实际验证；当前云端 EPERM 不允许换 socket/TCP/执行路径绕过 |
| 开箱配置与服务可达 | 下载包仅 probe；服务端未部署 | 版本配对、模型绑定、独立凭据引用、状态/审批界面、升级保留 journal、实际服务连通；不隐含生产部署许可 |

v0.2 第 5.2 节将 run-reported model 规定为“可用时”，第 5.3 节区分配置回读与运行 metadata，也明确不要求后台权重的密码学证明。当前原型的双字段严格校验是更窄实现，并非可以用相同值重复填充的许可证。任何能力分级调整须先核实官方接口和 fallback/子代理语义、单独审定，再做跨三仓合同变化；本轮不降低现有门槛。

## 二、先做的独立必要组件：Windows owned Job

目标是为本地启动提供真实 Windows 内核可观察的进程所有权，**不是**宣布 WorkBuddy 完整执行已经停止。它保持独立，不挂接 Runner、不修改现有结果合同、不打开真实渠道。

1. 使用标准 Win32 API 与 stdlib ctypes，不装原生扩展。只接受明确 argv/cwd/env，不开 shell，不继承整份环境或不相关句柄。
2. 创建 unnamed Job，启用 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`，不设置 breakaway 许可；以 `STARTUPINFOEX` handle list 限定 stdin/stdout/stderr 管道继承，并以 `PROC_THREAD_ATTRIBUTE_JOB_LIST` 在创建阶段分配该 Job。最低平台 Windows 10 / Server 2016；不支持则失败，不降级为创建后补归属。
3. `CreateProcessW(CREATE_SUSPENDED)` 成功后用 `IsProcessInJob` 确认精确 Job，再 `ResumeThread`。JOB_LIST 消除应用代码 create→assign 间控制进程被杀的孤儿窗口；不夸称 API 内部所有中断的内核回滚均已证明。归属不符的挂起进程不得运行；失败清理必须等待和检查，不能把不确定清理报成功。
4. 控制和等待均持有 process HANDLE，不通过可复用 PID 重新查找。通过 Job accounting 的 ActiveProcesses 判断该 Job 是否归零，parent 单独退出不够。
5. 显式结束/关闭后只返回 `owned_job_empty` 与宿主退出证据。它不覆盖由既有服务、COM broker、外部应用、远端服务执行的工作，也不撤销已发生副作用，**不能自动映射 `stopped_confirmed`**。
6. Linux 上模块可安全导入，明确返回平台不支持。实际 Win32 合同只由 Windows CI 的合成父/子/孙进程验证；无 WorkBuddy、模型请求或用户机器访问。

验收至少覆盖：带空格/Unicode argv；真实 stdio；父退出而子仍活；子孙归零；归属前不执行；Job 属性/归属校验/Resume 失败安全清理；句柄收尾；超时不伪造停止；关闭幂等；不靠 PID 身份；无法证明清理时显式错误。额外验证控制进程 hard crash 的 kill-on-close、无关 inheritable sentinel handle 不传入子进程，以及 close/terminate/query 失败后不丢所有权且保持 unknown。重启遗失 HANDLE 不得靠 PID 重附着恢复所有权。API 依据应引用微软官方 CreateProcess、Job Objects、handle inheritance 文档，并在报告中区分静态审查、Linux测试和真正 Windows 执行。

微软官方依据：[UpdateProcThreadAttribute](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-updateprocthreadattribute)、[创建阶段加入 Job 与旧窗口说明](https://devblogs.microsoft.com/oldnewthing/20230209-00/?p=107812)。支持版本与测试结果分别记录，不由来源存在推定实现通过。

## 三、云端官方发行物无账户验证

经协调者确认，允许在隔离 Windows CI 运行已核验官方 WorkBuddy 发行物的 `--help` 与标准 ACP `initialize`。只解压、不执行安装器；使用全新临时 HOME/APPDATA/配置根和明确环境 allowlist，不传账号凭据或 CI token，不调用 authenticate、session/new、选模或 prompt，不自动同意许可/授权。出现新权限或协议要求立即停止，不做回应。

来源与版本固定为官方更新接口取得的 WorkBuddy `5.9.3.42398134`；安装包 SHA256 `b5edb52bd6c5b6c7296cf2fff697b019c86d11992a8a814c3010d611896852f9`，450,174,440 字节。真实包静态证实 `resources/app.asar.unpacked/cli/bin/codebuddy` 为 Node JS 入口，可加载 `dist/codebuddy-lite-wb.mjs`；GUI 宿主实际使用 `--serve`、产品配置及 credential-protection bootstrap。不得把独立 `--acp` 的无账户启动结果解释成 GUI 登录继承。

探测进程使用受审 owned Job 组件监督，输出限长/限时，只保存白名单状态与原始字节摘要，不导出原生任意日志。CI 不做真实模型调用。即使实际 `initialize` 成功，登录继承、固定模型、真实结果和整链验收仍为未通过。

针对实际 `initialize` 空输出 EOF，协调者另批准有限无账户诊断：同一官方版本和相同唯一请求，原总 deadline 内短暂观察已有 HANDLE 自然退出及 Job 计数；只在确认本次进程组/读取线程停止、清理成功后，从本次全新临时目录的明确日志位置读取有界诊断，导出白名单错误类别/计数/字节摘要。不得读取账号数据或 store，不遍历用户目录，不跟随链接，不输出原文/文件名/路径，不构造私有 owner 身份，不扩大到登录、session/new、模型选择或 prompt。诊断不能把失败改成成功；变更须独立审计后执行。

## 四、后续顺序及停止宣称规则

先完成官方发行物/协议证据和上述组件审计，再决定真实 Adapter 的最小版本化合同。普通 CC 分流、配对和打包必须服务于已证实入口，不先建外围平台掩盖原生不可行性。

云端可完成源码和合成故障测试，不可能由这些结果推出用户真实登录、厂商调用或真实生产链已通过。凡必须用户机器、真实账号或实际模型请求的验收，只向协调者列具体需要、范围和费用风险；未获相应授权不执行。无法满足的条件如实保持阻断，不让用户替代开发或审计，也不以“已无障碍”包装待验证实验。
