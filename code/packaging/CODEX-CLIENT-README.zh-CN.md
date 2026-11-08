# Codex 本地客户端（Windows x64）

这是独立的 Codex 选择包，包含两种适配器的轻量工具，但不包含 WorkBuddy 官方运行资产。`Client` 根据包清单默认选择 Codex，不需要指定 `--harness`。需要 WorkBuddy 时下载对应包；两类目标可接入同一个 Bridge。混合使用时分别解压两个资产包，将各 target 指向对应资产目录，只启动一个共享 Node；详见 `MULTI-HARNESS-CONFIG.md`。

## 验证与登录

在解压后的本地命令提示符中运行，路径请换成自己的绝对路径：

```bat
Client.cmd verify-assets
mkdir C:\NativeProfiles
Client.cmd login --profile-parent C:\NativeProfiles
```

第二步由你明确确认才启动官方 Codex App Server。程序在本地终端显示官方设备登录地址和一次性代码；只在官方网站完成登录。登录完成、进程清理确认之后，再确认终端显示的账号与工作区。程序会打印新建的 `codex-managed-...` profile 路径。

不读取已有 Codex 账号文件、不导入 token、不把账号凭据上传云端。profile 保留在本地；取消并不等于退出账号或撤销登录。重新认证请新建 profile。ACL 限制不是加密或网络沙箱。

## 发现模型并导出目标

```bat
Client.cmd models --profile-root C:\NativeProfiles\codex-managed-<实际ID>
Client.cmd models --profile-root C:\NativeProfiles\codex-managed-<实际ID> --target-model <目录中的model字段> --public-alias codex-my-model
```

第二条命令输出可复制的 target JSON，已根据真实本地 profile 和本次模型目录计算 recipe 与 Binding。请使用 `model`，不要使用显示名称或目录 `id`。别名以 `codex-` 开头。命令需要本地交互终端，不通过重定向自动登录。

将 target 复制到 `client-config.example.json` 的 `targets` 数组，填写自己的 Bridge 地址、Sub2API 用户和 key ID、Windows 持久数据目录与 Bridge 数据目录。这个示例是**部署生成规格**，不能直接作为 Node 配置运行。

```bat
runtime\python.exe -I tools\prepare_native_deployment.py --spec client-config.example.json --output C:\NativeDeployment
```

生成器不修改生产部署。按 `MULTI-HARNESS-CONFIG.md` 与 `DEPLOYMENT_CHAIN.zh-CN.md` 分别放置 Node、Facade、Bridge、Sub2API 配置和凭据，保持原持久数据目录。不要把本地 profile 或生成的完整凭据目录上传到 GitHub。初始化 `accepting_new=false`；配置与账号确认后再由你启用测试入口。

## 支持边界

固定官方 `rust-v0.167.0` Windows x64 App Server 包，首期只支持 ChatGPT device-code 账号。执行绑定官方 `openai` provider、所选模型、read-only sandbox、on-request approval；遇到需要交互的工具操作不会代你批准。不要把它视为彻底禁用全部工具或企业策略的证明。

输出为本项目支持的文本快照与任务状态；commentary 仅反映运行状态，不镜像其原文。支持 partial/final 文本与结束/错误状态，不是完整 Codex UI 镜像。Windows 官方进程、设备登录、真实账号模型执行仍需本地验收；发布包中的合成测试及哈希验证不能代替真实运行验证。包不宣称账号服务转售获得授权。

`preflight` 是旧 WorkBuddy 检查，不适用于此包。Codex 使用 `verify-assets`、登录、模型目录和实际任务分层验收。
