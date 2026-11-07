# P1a 修复版独立复核

审计对象：staged manifest SHA256
`e76f35fadc872f4b1e9a1e5160018cf3a79c0099238dd6a61d66868f2632dfd8`

结论：**PASS_FOR_EXPLICIT_P1A_PROTOTYPE_PUBLICATION**。

允许作为明确标注的 P1a 离线原型发布源码。原先三个已复现问题全部关闭；本次未发现阻断这一限定用途发布的新回归。此结论不批准部署，也不表示真实 Bridge/Sub2API/WorkBuddy 已接通。

## 三项修复复核

| 原问题 | 修复设计与独立验证 | 状态 |
| --- | --- | --- |
| F1 Node 执行前完整性与授权不足 | 强制不可变 NodePolicy；NodeOffer 绑定 offer ID 与 task body；claim/start/journal 写入前核对受信 source、ledger、允许 owner、精确 DTO、client 摘要、messages 一致性、允许冻结 Binding、可启动状态。原消息篡改复现变为零执行；自洽越权 owner 也拒绝。 | CLOSED |
| F2 完整 JSON 掩盖 HTTP 短读 | 先验证唯一合法 Content-Length 与实际长度；错误响应同样先验 framing，不能带 accepted=false 逃过短读检查。原真实 socket 复现现在抛受控错误；不是只 mock 异常。 | CLOSED |
| F3 别名映射配置错配 | Facade 构造及每请求处理均核对 key/alias/type/synthetic 一致，提交前再核对别名。原不匹配配置构造即拒绝，运行期错误配置不新建任务。 | CLOSED |

HTTP framing 的修复有意缩小支持范围：P1a 非流式只接受明确 Content-Length，拒绝 chunked、无长度、重复/冲突长度。合同与测试已同步，不是隐式兼容回退。后续真实上游若要求 chunked，需要另行实现严格分帧与验收，不能直接放宽检查。

NodePolicy 保护的是已认证可信通道下的任务完整性和授权配置，不是防被完全攻陷 Bridge 的密码学证明。文档已说明这一边界。

## 独立实际执行

- manifest 自身 SHA 匹配，21/21 文件内容匹配。
- 工作树与 index 一致，无未纳入审计的代码差异。
- `git diff --cached --check`：PASS。
- `PYTHONPATH=src python -m unittest discover -s tests -v`：**79/79 PASS，0 SKIP**。
- 原独立复现脚本：**3/3 PASS**。
- `compileall`：PASS。
- offline demo：PASS，合成启动数 1、回放完全相同，真实 Bridge/Sub2API 集成标记均 false。
- 独立 adapter.start 调用计数探针：正常完成后回放、上传 ACK 丢失、adapter 返回丢失三个场景，各多次恢复后调用次数仍为 1。计数发生在 FakeHarness 自身 grant 唯一键之前，避免单靠 FakeHarness 内部防重掩盖重复调用。

修复后的 79 项保留原跨进程 pause/intent 崩溃测试，新增真实 socket framing 与启动前任务变异测试。确认测试确实执行，不是文档继承的 PASS 数字。

固定源码副本：`final-staged/`。
最终运行日志：`final-validation.txt`。
独立回归：`reproduce_findings.py`。
原始失败证据继续保留在 `original-staged/`、`original-reproduction-output.txt` 和 `CODE_AUDIT_INITIAL.md`。

## 未改变的限制

- P1a 只有合成模型和显式测试假网关/假账本。发布说明不能称真实 404token 渠道已建成。
- 真实 Bridge 类型扩展、真实 Sub2API Go 接缝、WorkBuddy 模型固定/终态、真实账号授权仍未实现或实测。
- Windows、真实工具/子进程停机、supervisor/持久撤销恢复、数据库回滚、磁盘满/掉电、生产多租户与收费未验收。
- 当前没有强制释放，未知槽可能一直隔离。此可用性限制已明确，不应发布时删除这些说明。
- 发布后远程 SHA、实际上传成败需由负责发布者单独确认；本审计没有推送、部署或合并。

本次只读复核项目源码，写入的是独立审计目录和临时测试证据；没有编辑被审项目代码。没有真实推理费用、生产数据访问或部署。
