# 本地 WorkBuddy 试验渠道运行时

`mode=workbuddy_local_trial`、`adapter=public_sfe` 是独立于 synthetic 测试的显式模式。默认配置仍不准入；不自动升级旧配置、不收费、不使用 fake 回退。只有一个可信 owner、一个槽和单 user 文本消息，alias 以 `workbuddy-` 开头，native_model 必须从用户本地原生目录选择并在每请求中选择和回读。拒绝已知 auto/default 动态选项。

配置形状（占位值必须替换，秘密不写 JSON）：

```json
{
  "mode": "workbuddy_local_trial",
  "adapter": "public_sfe",
  "source": "configured-source",
  "ledger_instance": "configured-ledger",
  "bridge_url": "https://your-authorized-bridge.example",
  "owner": ["authenticated-user-id", "authenticated-key-id"],
  "binding": {
    "binding_id": "configured-binding",
    "revision": 1,
    "model_alias": "workbuddy-your-model",
    "native_model": "<copy-from-local-native-catalog>"
  },
  "assets_root": "C:/NativeHarness/assets",
  "profile_root": "C:/YourProfiles/profile-created-by-login",
  "accepting_new": false,
  "execution_timeout": 120,
  "cleanup_timeout": 10
}
```

操作者确认配置后才将 `accepting_new` 改为 true。Bridge/Sub2API 的 source、ledger、owner、完整 binding 和 mode 必须一致；Sub2API 当前专用 `/v1/native-harness/chat/completions`，不是普通 provider 路由已全面支持。服务密钥来自 `NATIVE_BRIDGE_SOURCE_TOKEN`、`NATIVE_BRIDGE_NODE_TOKEN`、`NATIVE_FACADE_TOKEN`，与三仓配置对应，不使用 WorkBuddy 账号 token。

Node 数据根固定为 profile 内 `control`，不把账本放进原生任务 workspace。Runner 仍持有单槽 journal 锁，adapter 另存原生单次执行证据；Bridge 仍为唯一任务账本。回放不以当前账号能否新执行为前提。新执行前持 profile 锁、校验全部资产与 ACL、持久记录 execution_started，再以原生程序实时验证 Product/account/status、选择模型并执行一次。原生终态与进程/EOF/Job 清理独立校验；清理不明时 profile 永久隔离、持锁，不换任务重试。

首版原生工具权限与 elicitation 均拒绝，不提供隐式全权工具许可；需要交互工具审批的请求不能完成。真实工具副作用并不由单个标准 HTTP 响应变成全局 exactly-once。

`NativeTrialConfig.facade()` 仅构造网关观察服务，无账号读取；`runner()` 启动时只验证自有 profile 的元数据/ACL，不读取原生凭据。账号读取由官方 SFE 自己完成，程序不复制或解密账号文件。保存登录态与本地访问保护已由独立用户登录流程明确告知。

源码集成/fixture 通过不是用户真实账号、真实模型或商业授权验收。最终实际 Windows 包和普通 CI 结果另列，不使用历史测试数代替。
