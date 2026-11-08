# 普通 Provider 实现验证记录

本轮基线 Native `3402cc1`，先蓝图/设计审计/开发文档/开发审计，独立文档提交 `9098cd9`，之后才实现。Bridge `52dc0832` 与原生 Sub2API `6b4b37db` 仅作固定依赖/只读核查，未修改。

## 本机实际运行

| 检查 | 实际结果 |
| --- | --- |
| 改动前完整 Python 基线 | 997 total：937 pass / 60 skip |
| 新普通 Provider 单位/真实 HTTP 协议测试 | 22/22 pass，0 skip |
| 配置/向导/打包相关 focused 组 | 85 total：83 pass / 2 Windows skip；与全量重叠，不累加 |
| 改动后完整 Python suite | 1025 total：965 pass / 60 skip |
| 真 Bridge HTTP 新 Provider 场景 | 6/6 pass：Bearer、同key回放、binding下线、无key两次执行、SSE、断线/unknown槽 |
| 真 Bridge + 旧路径回归合计 | 22/22 pass，0 skip（新增6 + 旧14 + 多Harness2） |
| `compileall`、`git diff --check` | PASS |
| 原有 `offline_demo.py` | PASS；仍仅合成P1a demo，1次start与一致回放，不当原生网关证明 |

命令从 `code/` 运行：

```sh
PYTHONPATH=src python -m unittest discover -s tests
PYTHONPATH=src python -m unittest tests.test_openai_provider -v
PYTHONPATH=src:integration:tests:../../alchemy-render-bridge/src \
  python -m unittest test_openai_provider_bridge test_real_bridge test_multi_harness_bridge -v
PYTHONPATH=src:. python examples/offline_demo.py
python -m compileall -q src tests integration tools packaging
git diff --check
```

一次新增fixture把task_terminal漏写liveness而被既有事件校验拒绝；修正fixture完整status后22项全部通过，没有放宽产品校验。独审指出初稿stream额外1秒与文档总预算不符，已改为在原预算内预留短错误flush，再跑相关回归。Bridge在途IO保持既有有界timeout，不虚称硬实时deadline。

## 原生 Sub2API 层

当前云环境 `/usr/bin/go` 不是Go编译器，所以本机 **Go编译/原生forwarder：NOT_RUN**。新增隔离CI `native-provider.yml`，固定原生Sub2源码、Bridge和上游要求的Go1.27.3；测试只通过Go overlay加入自写fixture，不修改原生实现。远端CI状态应另按精确提交核查，不把本节计划写成通过。

```sh
PYTHONPATH=code/src:code/integration:../alchemy-render-bridge/src \
python code/integration/run_native_provider_chain.py \
  --upstream ../sub2api-upstream --bridge ../alchemy-render-bridge --go /path/to/go
```

该测试覆盖真实原生 **service forwarder** → Native HTTP → Bridge HTTP → FakeHarness Node；验证模型映射、仅Bearer、私头/Idempotency-Key被剥离、自动include_usage、JSON/SSE及两次独立执行。它不运行Sub2API用户鉴权middleware、数据库scheduler或计费流水，不是完整生产闭环。

## 未验证与未执行

Windows本机执行：本云不可运行，相应60项skip，须看精确提交CI。真实官方账号/模型/积分、工具副作用、商业授权、TLS/容器网络/用户生产配置、真实计费：NOT_RUN或NOT_VERIFIED。未知usage不输出0；原生网关内部零值不是测量证据。无真实模型费用，无账号登录/凭据读取，无生产部署或合并。
