# 已有 CLI 增量设计独立审计

日期：2026-11-08 UTC。基线：`d324b4686dc09724a4ddd0319981c257e6abeb59`。审查对象：`EXISTING-CLI-ADDENDUM.md`，独立审查者，不是实现作者。

## 结论：PASS（允许限定范围编码）

- 新增 `codex.user-cli.v1`，不改变旧 adapter/recipe/binding，符合最小改动。
- 同用户、同 CODEX_HOME，由官方 CLI 自行管理认证；工具只保存自己的 sidecar，不读取/复制用户认证材料，不改外部 home 的 ACL、manifest 或状态。
- 复用既有 Codex 协议、owned IO、Node 单槽及 journal，不新增服务、队列、通用插件框架。
- 原任务优先按持久证据只读恢复，不能用新连接、移除 target 或重建 profile 消除 unknown。
- WorkBuddy 保留官方独立 SFE，不声称附着 GUI；豆包工作、千问办公仍为缺官方接口证据的候选，不用同厂商其他产品冒名。

审计提出并已纳入：删除默认重复 `login status`，以官方 `account/read` 判断认证；新 adapter 每次 initialize 都核对回读 CODEX_HOME；模型目录有唯一 `isDefault` 时允许回车选默认。没有追加额外防御框架。

## 来源复核与边界

- [官方认证文档](https://developers.openai.com/codex/auth/)说明 CLI 缓存认证可复用，可能为文件或 OS 凭据存储。只承诺本工具不读取凭据；不承诺官方程序不会自动刷新或写自己的认证。
- [官方 App Server 文档](https://developers.openai.com/codex/app-server/)明确 App Server 认证不允许用于商业/托管服务。404token 商业转供不能凭用户确认、开源许可或本机登录推导获准。本轮不启用商业服务。
- 固定源码 [`086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-cli/bin/codex.js`](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-cli/bin/codex.js)复核 Windows x64 npm 平台包与 `vendor/x86_64-pc-windows-msvc/bin/codex.exe` 布局。其他安装布局找不到时显式选 exe，不执行 shim。

通过设计审计不是账号、真实推理、额度、Windows 真机或商业授权验收。这些仍 NOT_RUN / NOT_VERIFIED。
