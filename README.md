# Native Harness API

本地原生 Harness 执行，VPS 只负责任务与结果包装，通过 Sub2API 向用户提供 API Key。

**文档基线：v0.1 / 2026-11-07。当前交付仅为开发蓝图，不包含可运行服务，不代表真实 Harness、Sub2API 联调、商业授权或生产安全验收已经通过。**

## 唯一允许的推理链路

```text
用户程序 + Sub2API 用户 Key
                |
                v
Sub2API：认证、模型路由、可信身份、同请求标识、计费归属
                |
                v
Harness API Facade [VPS]：标准接口、参数映射、等待/包装结果
                |
                v
Render Bridge [VPS]：唯一任务账本，node_pull，独立实例/数据目录
                ^
                | 本地节点主动 HTTPS 领取、回传、恢复
                v
Local Node Agent [PC]：会话/工作区/执行槽、原生 Adapter
                |
                v
本地官方 Harness：原生模型访问、原生工具、原生审批
```

VPS 不运行 Harness、不保存厂商登录凭证、不直连厂商模型接口；本地离线不得切成云端直接推理。Sub2API 转发到我们自己的执行服务，不是提取厂商凭据后反代其内部接口。

## 阅读顺序

| 文件 | 用途 |
| --- | --- |
| [总体蓝图](docs/BLUEPRINT.md) | 边界、职责、架构、最小版本、任务生命周期、隔离 |
| [接口与恢复合同](docs/CONTRACTS.md) | API 子集、可信身份、幂等、事件、取消、计费 |
| [源码复用与 Sub2API 接缝](docs/REUSE_AND_INTEGRATION.md) | 固定源码快照、已知缺口、复用和禁止搬用的代码 |
| [阶段实施与验收](docs/DEVELOPMENT_AND_ACCEPTANCE.md) | 分阶段交付、真实故障测试、上线前门禁 |
| [蓝图审计记录](docs/AUDIT.md) | 发现、修订、证据等级与尚未运行的验证 |
| [开发代理约束](AGENTS.md) | 后续 Codex/开发人员必须遵守的范围 |
| [离线文档检查](audit/check-blueprint.mjs) | 文档链接/约束检查与抽象状态机反例测试 |
| [检查结果](audit/results.json) | 可重跑的检查记录；不是业务程序测试 |
| [文件摘要](audit/manifest.sha256) | 本次文档及检查脚本的内容指纹 |

## 首版与后续

首版：一台 Windows 电脑、一个执行槽、一个可信测试租户、每请求一个原生会话，文本输入/回答，非流式 Chat Completions。先用 FakeHarness 验证合同，再接获准的真实原生入口。接口必须实际经过 Sub2API 用户 Key 闭环，不能只演示直连 Facade。

后续：真实 SSE、审批交互、多执行槽、多用户隔离、长期会话。没有通过对应门禁的能力不展示为已支持。WorkBuddy/豆包仍是待验证 Adapter，不以其他 CLI 的能力替代证明。

## 仓库边界

本仓库承载新项目蓝图，以及后续薄 API 外壳和原生 Adapter。现有 Render Bridge、Sub2API 的必要接缝应在各自仓库独立 PR，默认关闭，不顺带改变原有渠道。不得把旧渲染协议的 task kind 或 artifact 内容伪装成聊天任务来绕过合同。

本次未复制第三方实现代码，未安装第三方依赖，未调用模型，未修改现有业务仓库，未部署服务。项目分发许可证尚未选择；引用项目的许可和厂商服务授权分别确认，不能把“公开仓库”理解为商业转供授权。

## 重跑蓝图检查

使用具备内置模块的 Node.js 运行以下命令；无需安装 npm 包、无需联网，也不读取本机厂商账号：

```bash
node audit/check-blueprint.mjs
```

审计状态应以 `docs/AUDIT.md` 和实际检查输出为准。任何 `PASS` 仅适用于明确列出的文档/抽象模型范围，不是生产可用承诺。
