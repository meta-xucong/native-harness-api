# 多 Harness 来源与能力证据矩阵

日期：2026-11-08 UTC。v0.3 设计附录；非运行报告。

## 当前基线

| 仓库 | 实现审阅基线 |
|---|---|
| native-harness-api | `081e10261c86c61b97c122ff71211d5aeaaca32c` |
| alchemy-render-bridge | `03fd9020b1198013da4315746da5474855aee4cc` |
| sub2api-adapted | `9ffe3aa2fa83a1037cde995f6f266c4b6fc29ac2` |

本轮核对三个本地 checkout HEAD，直接读取 Native 代码/文档，未重跑三仓全部业务测试，也未重新对全部官方网页做独立抓取。下表外部接口结论来自本会话此前兼容性调查，作为后续适配输入而非本轮真实模型证据。源码/包/官方文档“含该入口”不代表已登录或已跑模型。

代码定位（上述 Native revision）：
- `code/src/native_harness_api/runtime.py` 只分流 `workbuddy_local_trial` 与 synthetic；没有通用动态 Adapter。
- `native_trial_runtime.py` 只接 public_sfe；`native_bindings.py` 最多32个、强制 `workbuddy-` 及非auto原生模型。
- `public_sfe_session.py` 有具体 WorkBuddy 产品校验；不能替换 exe 即通用。
- `code/docs/NATIVE_EVENT_STREAM.md` 为双协议实际合同；`USAGE_EVIDENCE.md` 为不收费证据合同。
- `code/packaging/README.zh-CN.md` 和现有部署/测试文档作为已交付范围，不以旧 P1b README 的历史缺失否定后续源码，也不把新设计说成已实现。

## 候选矩阵

| 产品/入口 | 已有证据 | 当前本项目支持 | 下阶段门槛 |
|---|---|---|---|
| WorkBuddy CN / public Harness SFE beta16 | 官方包匿名初始化/Product/目录及未认证读取有前期云端实测；本地登录/模型工具和原生会话有模拟测试 | 已实现受限试用；真实账号模型NOT_RUN | 用户本地版本/账户/模型和实际实时行为验收；不保证复用GUI登录 |
| Codex / App Server | 官方文档含stdio、模型目录、thread/turn、文本delta、完成、interrupt和认证 | 未实现 | 固定CLI版本/schema；空账号探针；登录与模型选择；终态/工具/恢复精确事件合同 |
| 豆包工作 / doubao-cli候选 | npm `@bytedance/doubao-cli@2.31.3` 指向官方CDN；静态包有stream-json、替换与终态；README称internal integration package | 未实现；不得标稳定公开支持 | 产品对应、发布支持级别、启动参数、认证、模型选择/读回、取消与远端存活实证 |
| 千问办公 | 官方企业jsBridge、Hooks、IM、BYOK文档 | 未实现；完整执行协议未建立 | 可靠本地提交、身份/模型、逐步输出、终态与恢复；Stop hook非最终证据 |
| Qwen Code | 官方headless/stream-json/ACP文档 | 未实现；**不是千问办公** | 固定版本、官方登录与模型目录及运行验证 |
| CodeBuddy Code | 官方ACP/SDK文档 | 未实现；**不是WorkBuddy** | 产品/地区/账户独立核验，模型与事件合同实测 |

## 公开来源导航

- Codex [App Server](https://learn.chatgpt.com/docs/app-server)、[SDK](https://learn.chatgpt.com/docs/codex-sdk)。接口名如 model/list、thread/start、turn/start、item/agentMessage/delta、turn/completed、turn/interrupt仅作为调查线索；实施必须按选定版本schema核对。
- 豆包 [npm](https://www.npmjs.com/package/@bytedance/doubao-cli)、[registry](https://registry.npmjs.org/@bytedance%2fdoubao-cli)。此前调查的Windows资产SHA256 `83223e7b264fdf143ae281d5a1a887e497adbd7e922c4345fbe59743426439b8`；本轮未重新下载或执行。取得包不等于官方长期接口支持承诺。
- 千问办公 [jsBridge](https://docs.qwenwork.cn/developer/misc/embedded-app-jsbridge)、[Hooks](https://docs.qwenwork.cn/desktop/hooks)、[IM](https://docs.qwenwork.cn/desktop/im-channels)、[BYOK](https://docs.qwenwork.cn/developer/integrations/overview)。BYOK是导入用户API供其使用，不是导出其模型；UI跳转/加消息不等于完成协议。
- [Qwen Code headless](https://qwenlm.github.io/qwen-code-docs/en/users/features/headless/)。
- [CodeBuddy ACP](https://www.codebuddy.ai/docs/cli/acp)、[SDK](https://www.codebuddy.ai/docs/cli/sdk)。

若用户说“Codex作为下游调用404token”，这与“导出本地Codex”不同：当前受限Chat Completions并不代表Codex需要的Responses/历史/工具循环已经成立。不可用上游App Server可适配的结论替代下游兼容性验证。

## 每项未来真实证明必须携带

官方来源/许可证及条款范围、产品/地区、固定原生资产版本及哈希、Adapter revision、OS、测试类型、认证路径、模型binding和回读证据、正反例结果、清理/恢复记录、未覆盖项及测试日期。原始账号信息/凭据/工作区内容不进入仓库，脱敏引用不能代替真实证据。已过时或升级后的证据不得自动沿用。
