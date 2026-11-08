# P2 本机试验工具验证记录

2026-11-07，本轮在既有 P1b Native commit `c18809057f0c821bb95a452a32f237f13dc6ade6` 上新增独立工具。Bridge 固定 `91ad18b2612899860edf89d985fe9d5ca638d0a1`，Sub2API 固定 `bf8c8143246ae59a6c95db91eb3a580e3332ea08`，后两仓本轮未修改。

## 本地已执行

| 检查 | 结果 |
| --- | --- |
| 全部 Python unit/协议子进程测试 | 161 项，160 PASS / 1 项 Windows 路径用例在 Linux SKIP |
| ACP 真实 stdio 子进程 fixture | 28/28 PASS，全部为明确合成 peer |
| probe 配置/单次 intent/恢复/跨进程锁/导出 | 37 项，36 PASS / 1 项 Windows 路径用例在 Linux SKIP |
| 确定性打包/摘要/精确 Git source/篡改检查 | 7/7 PASS |
| 原真实 Bridge HTTP socket 回归 | 14/14 PASS，执行器仍是 FakeHarness |
| compileall、git diff --check | PASS |
| 官方 Python ZIP 下载摘要与文件结构 | SHA256 与官网/SPDX 匹配，34 文件，保留原 LICENSE |

`ACP` 和 `probe` 的计数已包含在 161 项中，不能重复累加。没有安装或运行真实 WorkBuddy，没有真实 prompt、真实模型费用或生产部署。

## 审计中发现并修正

- intent callback 改为调用线程同步提交，保留 OS 锁；提交返回后检查 deadline，过期零 prompt。不能安全取消 fsync，不再宣称其有强制时间上界。
- 只接受实际 default 权限回读。plan 会继承之前的权限基线，不能当无条件安全模式。
- 发送前及 intent 后消费已到达的控制更新。明确不保证尚在飞行中更新的跨进程原子排序。
- 模型证据跟踪来源，删除、歧义、变更立即拒绝，不能回退旧缓存假装已核验。
- 模式证据也固定所有已观察来源和 ID，删除或冲突不能回退旧 legacy default。
- 移除发布 CLI 免确认参数，输入/输出/错误提示均要求交互 TTY；管道确认不运行，管道审批不批准。
- 脱敏导出只含白名单枚举/摘要，不复制原生 name/version/model 文本；控制字符和双向格式符在本机显示转义。
- state 与 workspace、原生安装/配置目录的相等或祖先重叠拒绝，避免普通工作区清理删除防重证据。
- SQLite intent 与额外不覆盖的 intent 标记检测部分写入/行丢失。删除所有证据的恶意本地用户不在保护范围；程序无 reset 路径。

## 仍不能声称

Windows 私有解释器启动和跨进程合同需看本提交的 `WorkBuddy local trial kit (no real inference)` CI，不能用 Linux 通过代替。工作流只用 synthetic peer 和空命令帮助，不调用真实 WorkBuddy；源码通过审计后才推送触发。

即使 Windows CI 通过，也不等于真实 WorkBuddy 版本、原生登录继承、真实选模/provider 型号、工具/审批/取消/后代停止已验收。模型回读仅证明配置。真实 PostgreSQL 接缝、普通 CC 渠道、收费、生产与商业授权不在本次通过范围。

最终 ZIP 必须在干净、精确发布 HEAD 上构建，内部包含源码 revision 和每个文件摘要；官方解释器不经 pip 改造。独立审计须再核对 artifact 内容与源码。用户下载后由自己显式选择官方安装并确认一次真实运行。
