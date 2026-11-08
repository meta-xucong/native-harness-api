# P1b 当前测试记录

范围：独立三仓开发分支、真实 Bridge/HTTP 接缝、合成 FakeHarness。未发布新 P1b PR、未部署/合并、无真实模型费用。旧 P1a 审计不是本阶段独立复核。

## 当前运行结果

| 检查 | 结果与边界 |
| --- | --- |
| Native `PYTHONPATH=src python -m unittest discover -s tests -v` | **89/89 PASS**，0 skips；含79项P1a回归与10项runtime/冻结路由/恢复测试 |
| Native `... unittest discover -s integration -p test_real_bridge.py -v` | **14/14 PASS**，0 skips；真实BridgeStore/FastAPI与Facade/Node socket，含真实Node CLI子进程 |
| Native `integration/run_sub2api_chain.py --sub2api-backend ... --go ...` | **PASS**，Go外部HTTP测试明确运行而非skip；合成启动数1；auth lookup/route store为显式fixture |
| Native compileall（src/tests/examples/integration） | PASS |
| P1a offline demo | PASS，恰1次合成启动、回放一致；仍明确真实Bridge/Sub2API=false，不计为P1b验证 |
| Bridge 完整 `unittest discover -s tests` | **354/354 PASS**：314旧功能 + 40 typed harness，0 skips；详见Bridge仓测试记录 |
| Sub2API `go build ./cmd/server` | PASS，Go 1.27.2；正式server可编译，不代表运行/迁移完成 |
| Sub2API nativeharness专项 `-race` | 12项PASS（含39个strict DTO HTTP子场景）；真实PostgreSQL测试1项SKIP，不能计作通过 |
| Sub2API middleware/handler专项 `-race` | PASS；真实认证服务/中间件，repository查找仍fixture |
| Sub2API `go test -tags unit` 的handler/middleware/routes/config四包 | 全部PASS；handler 128.220s、middleware 0.066s、routes 10.570s、config cached |
| 真实PostgreSQL迁移、100并发唯一行、重启恢复、持久三仓闭环 | **NOT_RUN / BLOCKED** |

Sub2API四包unit回归不是整个Go仓库的全量测试；未运行全项目lint、前端构建或生产服务启动。完整记录在该仓 `docs/native-harness/P1B_PILOT.md` 与执行日志。

Python 3.12.14/Linux，Bridge使用其已安装锁定依赖并将PYTHONPATH显式指向本轮实现分支。Go 1.27.2来自官方 go.dev/dl → dl.google.com；按官方JSON公布的SHA256 `ecbadb99091a3f46e31f5f934b068b1864eafa7995211b39eaddf76996045fe5` 校验后使用。未修改go.mod降级，不绕过依赖校验。

## 真实 Bridge 跨仓覆盖

1. 真实HTTP成功/未知usage省略/同结果回放。
2. 同操作改body拒绝，不生成新任务。
3. Node真实CLI进程重启，复用journal，不再启动。
4. 旧consumer停在持久intent后、spawn前，真实Bridge不给第二消费者许可。
5. 真实start已授予但ACK丢失：零次原生启动，持续隔离。
6. 真实result已提交但ACK丢失：只重传，启动数保持1。
7. Node journal重建后只读恢复。
8. 新准入关闭/模型配置移除后已完成结果回放。
9. 未授权owner不产生执行。
10. 终态成功+unknown仍占槽。
11. 未start截止后墓碑返回410，不新生成。
12. Bridge binding移除之后旧结果ACK仍可恢复，启动数保持1。
13. recovery-only运行不claim已排队任务，不建立新journal。
14. 观察读取与尚未消费的start并发时，有界等待后正常授予一次，不因普通竞争误隔离。

原P1a中的真实跨进程锁/intent故障测试保留，仍只是synthetic原生证据，不声称真实WorkBuddy子进程被围栏。Bridge自身typed测试另覆盖专用实例拒绝混跑、原claim/grant回传、结果冲突、过期后时钟回退、取消与保留策略。

## 三仓HTTP链的准确含义

实际使用Sub2API `APIKeyService`、认证中间件、native handler、单次HTTP出站、真实Facade、真实Bridge、Node。请求使用真实HTTP credential验证流程，但 credential repository返回测试记录；operation route store是显式内存测试替身。退出/重建service后的回放只证明冻结路由使用接缝，不证明数据库进程恢复。

输出为：`actual_bridge_http=true`、`actual_sub2api_middleware_handler_http=true`、`synthetic_native_starts=1`、`auth_lookup_fixture=true`、`route_store_fixture=true`、`postgresql_verified=false`、`actual_native_model=false`。

## PostgreSQL环境阻断与中间结果

官方Debian PostgreSQL包可验证/解压，私有Unix socket启动被环境报 `Operation not permitted`。未获得可用于验收的已存在授权数据库服务，真实数据库测试停止，不用SQL mock冒充通过。过程中临时loopback测试进程曾短暂启动但未执行DB测试，收到停止指示后确认listener不存在，临时密码/DSN文件移除。没有生产数据库访问。

首次native扩展测试发现owner非法类型异常类与旧owned_by标签预期不一致，已修复后全量88项通过。一次从错误工作目录执行unittest导致discovery失败，纠正cwd后重跑。Sub2API全包config回归发现4个新增配置键未注册Viper默认值，修复后该包重跑通过。所有最终计数以最后运行日志为准，不包含这些失败尝试。

## 指纹和审计

`code/audit/source-manifest.sha256` 覆盖本轮26份源码/测试/样例/pyproject文件；在 `code/` 中用 `sha256sum -c audit/source-manifest.sha256` 复核。旧21文件manifest归档为 `p1a-source-manifest.sha256`，只适用于P1a旧commit。

当前已通过[限定开发分支独立复审](P1B_AUDIT_FINAL.md)。真实原生Harness选模/终态、Windows、supervisor恢复、数据库回滚代际、生产隔离、普通CC账户渠道、收费/商业授权均未完成。详细阶段边界见 [实现状态](IMPLEMENTATION_STATUS.md)。

## 独立 P1b 审计修复

- 原typed heartbeat覆盖image profiles，且共用next无法区分独立runner。首版收窄为专用Harness实例/账本/执行资源，拒绝混跑，不用简单profile union冒充修复。默认图片实例旧回归保留。额外复现空账本旧image对象在Harness后pin仍能写入的竞争，现构造器在同事务固定image/harness模式（包括空账本），竞争仅一方成功；已有旧版本进程仍需先离线停止。
- 原runtime先心跳再恢复，Bridge移除binding后422挡住已完成未ACK结果。现先在同journal锁内仅恢复/上传，不poll新任务，再处理新能力心跳；恢复阶段心跳失败不改变结果。真实Bridge负例已覆盖。
- 原Sub2API成功响应允许缺id/created、错误content类型及legacy function_call透传。现strict exact DTO，39个真实HTTP正负子场景验证拒绝异常、无自动重试、同键operation不变。原审计畸形响应已被NATIVE_INVALID_COMPLETION拒绝。

修复后的[独立复审](P1B_AUDIT_FINAL.md)已通过限定开发分支；这不是实现者自我审计或完整P1b持久验收。

### 末轮竞争回归

末轮三仓HTTP复跑曾在时限内未完成，未用重跑到绿掩盖。确定性复现为Facade读占唯一HTTP store gate时Node start收到503，实际grant为空、原生启动0次但本地保守隔离。现仅Harness专用实例允许1个执行+1个有界HTTP等待，仍单store worker、非任务队列；图片模式不变。第3请求仍拒绝，网络未知或真正过载仍不自动retry。

扩充等待容量后发现AnyIO取消已运行HTTP可能提前释放limiter，补仅Harness shield worker持有执行权直到同步完成；取消尚未started的waiter由状态锁标记并阻止迟到grant，后台异常不输出。新增确定性取消/容量测试已通过。修复后真实Bridge socket14项以及三仓HTTP重复5轮均通过，每轮仅1次合成启动、0 node fault。

发布准备仅新增独立复审副本并更新审计状态文档，不修改三仓被审计源码。Native26文件manifest与Bridge11文件/Sub2API18文件审计manifest保持一致。
