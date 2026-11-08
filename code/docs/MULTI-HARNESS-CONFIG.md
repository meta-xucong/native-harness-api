# 通用本机配置（v2）

这是 `harness_local_trial` 配置生成接口，不是已通过真实账号验收的声明。WorkBuddy 和 Codex 共用一个 Node、一个 journal、一份证据库。原 `workbuddy_local_trial` 配置和生成入口保留不变。

## 生成输入

`prepare_native_deployment.py --spec specification.json --output new-configs` 接受以下精确字段：

- `schema_version`: 整数 `2`。
- `bridge_origin`: 该部署已有的可达 HTTPS Bridge origin。
- `user_id`、`api_key_id`: Sub2API 实际用户/专用 Key 数字 ID。
- `bridge_data_dir`: 已有 Linux Bridge 状态目录。
- `windows_data_dir`: 本执行资源原来的 Node control 目录。迁移时必须原样沿用，不能换目录解除未知任务。
- `targets`: 1 至 32 个 `{recipe, binding, assets_root}` 对象。不同 Harness 不是不同 Node。

每个 target 的 `recipe` 使用 `native_harness_api.harness_registry.make_recipe` 构造，`binding` 使用 `binding_for(recipe).wire()` 得到。生成器验证完整配方与四字段 Binding 的精确对应，不按 alias 或 model 显示名猜测执行目标。

`profile_path`、`profile_id`、`account_fingerprint` 必须来自本工具创建且用户确认身份后的本机 profile 元数据，不能猜测或使用示例指纹。WorkBuddy 由受控 profile 的目录身份及 `expected_account_digest` 提供；Codex 由自己的受控 profile 和公开 account/read 身份确认提供。不要读取、复制或粘贴原生 `auth.json`、token 或账户密码。正常 token 刷新不改配方；重新认证或切账号创建新的 profile ID，再生成新的 Binding。

`native_model` 来自相应 Harness 的模型发现结果，`public_alias` 分别以 `workbuddy-` 或 `codex-` 开头。资产、协议、产品和策略常量由静态 registry 提供，不能自行填写另一个版本冒充兼容。

## 从旧部署迁移

先停止旧 Node，保留 Bridge/Sub2API 状态和原 Node control 目录，再执行：

```text
python tools/prepare_native_deployment.py --spec specification-v2.json --identities old-configs/deployment-identities.json --output new-configs
```

`--identities` 复用原 source、ledger、workspace、node、executor 等身份。新输出目录必须不存在；工具不会覆盖旧配置，不启动程序、不登录、不推理、不部署。所有准入开关仍关闭。仅更换模型或 Harness 时也不要重造这些部署身份。

输出中的 `windows-node.json` 和 `deployment-identities.json` 包含本机路径及公开账户指纹，只留本机。只把 `bridge.json`、`facade.json`、`sub2api.native-harness.yaml` 分别交给对应服务器；这三份仅含 Binding，不含 profile 或账户指纹。旧 accepted 操作按原 journal/证据只读恢复，不能把它换一个 Harness 重跑。

## 不手算 WorkBuddy target

在用户已登录并确认身份的 WorkBuddy 包中，先运行 `models --profile C:\Profiles\实际profile` 查看官方目录。选定模型后运行：

```text
Client.cmd models --profile C:\Profiles\实际profile --target-model 实际模型ID --public-alias workbuddy-自定义别名
```

它再次创建并关闭一个空目录会话，不发送 prompt。只有本次官方目录包含该模型、身份核验和清理均通过后，才输出单个 target JSON。将其完整放入 spec 的 `targets` 数组，不修改其中 recipe、Binding 或指纹。此操作只输出本机配置，不保存部署、不启动 Node，也不证明该模型已完成真实推理。
