# WorkBuddy beta16 派生构建状态

## 已验证补丁

固定官方 `@tencent-ai/agent-harness@0.2.0-beta.16` 的 Product 响应中，空白字符串
`enterpriseId` 会在 `normalizeRemoteProductConfiguration` 被拒绝，最终表现为公开登录
`-32603`；账号认证可能已经保存，但 Product 尚未完成提交。

[唯一候选补丁](../packaging/patches/workbuddy-beta16-blank-enterprise-id.patch) 在远端 normalize
入口仅把该空白字符串转换为字段缺省，再执行原有完整校验。它不修改全局 schema、认证 tenant、
有效企业 ID、租户冲突检查、HTTP 或超时。补丁含上游发布代码，许可证随附于同目录。

原 authority.js SHA256：`d3cdae041457c7345ffad62a2a0948d3d8cc4a33a25bd06f1ab3be47afa9c445`。
候选 SHA256：`96d09736c8bdb8c0fd1dbaf7db7af5d87fa016f311ee61f91a581ea6f2bf7327`。

原版/候选完整源码链对照、8项企业字段与路由合同均通过；经用户授权的同一独立 profile
无推理恢复中，候选已观察到 Product commit、消费者设置、账号更新及官方 ready 终态。
没有发起新登录或模型请求。此证据不等同最终 Windows SFE 或完整客户端包验收。

## 正式 SFE 构建阻断

用户已授权将唯一补丁作为明确标记的本地派生基线。当前尚未生成新 EXE 或客户端 ZIP，
所有现有运行资产与 pin 保持不变。

固定官方发布包的 package.json 声明 `build:sfe` / `package:sfe` 调用
`scripts/build-sfe.mjs`，但 npm 发布内容没有该脚本、Windows Bun 编译入口、
`tsconfig.sfe.json`、原依赖锁及 workspace 构建配置，也没有完整的 embedded
Agent/CodeBuddy/node-pty 注册和资源映射构建材料。

能确认 Bun 1.4.3 及若干编译参数，原平台包侧车资源与许可证完整。但这不构成完整构建配方；
Darwin/beta14 的 benchmark 参数不能冒作 beta16 Windows 配方。`bin/agent-harness.js`
只查找并启动已发布的 EXE，编译该 npm 启动器不会产生修复后的 Harness。
诊断过程中生成的 npm runtime 锁也不等同原 SFE 的依赖图。

因此，需要官方完整 beta16 Windows SFE 构建素材，或包含该补丁、可核验来源的新平台产物。
不得临时改写二进制、关掉远端配置/校验，或暗换 Node/internal composition 后宣称仍是同架构 SFE。

## 取得构建素材后必须完成的具体兼容工作

- 原 `catalog_assets.py` 的官方下载 URL/哈希保留。派生资产另有明确身份，不能下载原包却用
  新 tree/EXE 哈希检验，也不能把本地派生二进制冒称来自原 npm URL。
- 原 profile manifest 的 pin、ID、owner、状态保留。新运行资产必须精确校验；profile
  只允许已明确审定的原 beta16/派生兼容集合，不能泛化为跳过资产检查。
- `unknown_retained` 当前不能进入 login/models/quick-start。需要明确的无重新登录恢复路径，
  真实就绪、账号回读与进程清理完成后才确认状态，不能手填 fresh 或复制认证文件。
- 资产身份参与 recipe/binding；新资产不得冒用旧执行身份。旧 journal 使用原身份只读恢复，
  unknown 不解锁、不推理。回归要固定旧版序列化 recipe，避免测试随常量一起变化而假通过。
- 最终完整新包须记录精确源码 revision、唯一补丁、构建配方、依赖、许可证及真实新哈希；
  通过包内 runtime、启动/停止、资产、profile 和旧任务恢复验收后再交付入口。

当前这些生产兼容改动和最终包测试均为 NOT_RUN/未实施，不以源码候选通过替代。
模型请求仍需用户另行确认账号、模型、prompt 和费用。
