# 最简启动器独立实现审计

日期：2026-11-08 UTC。结论：**PASS_FOR_SCOPED_DEVELOPMENT**。基线 `c6b2f45` 上的本次工作树增量可进入 draft PR 与 Windows CI，不是实际账号或生产验收。

## 审查范围

逐项阅读 tracked diff 及新增 `quick_start.py`、向导测试；核对设计审计提出的恢复、身份、profile 与 owner 问题。主要实现审查快照：

| 文件 | SHA-256 |
|---|---|
| `code/packaging/quick_start.py` | `e2acbff02d4c2a0ea21e6756a132f19b3b1281ae48176e05142ffc8127027bef` |
| `code/packaging/client.py` | `aa3362cfee790798a9032b9c7f411ab144f1378bda53659a9171122f70d61397` |
| `code/tools/public_harness_models.py` | `bc8b7d7a58121661bceb8857234295d31dbfb26eaf7fb961c0bc4633d0762694` |

## 结论

- 新增的是薄向导，不重写登录/目录/部署/runtime，不增加服务、队列或重试。
- 既有配置不重写，Node 使用原身份与 journal；无登录或目录前置检查。限定 NativeTrial/MultiHarness 配置，错误配置不替换已有启动指针。
- 所有 prefix 保留候选均参与选择；未知或不完整 profile 不触发隐式新建。新部署发现已有 journal/native-evidence 会停止，并指向原配置恢复。
- 固定 connection 目录及不覆盖 write_plan 防止首次生成中断后静默再造身份。部分输出停止，完整输出可恢复。
- 模型目录成功后才选模型/export；账户指纹在目录调用前冻结。目录工具的未知 owner 在向导 finally 中保持原持有语义，包括后续终端输出失败。
- Node token 仅隐藏输入到当前进程环境，结束后移除，不写配置；空 token 不启动。
- 默认 WorkBuddy 入口调用向导，Codex 无参数仍只显示帮助。完整包清单包含新脚本，CI 新增 start --help，不新增厂商执行流程。
- 已有部署选择原配置的提示明确；不声称仅通过 profile/control 就能发现所有外置 v2 data_dir。服务器接入、准入和最小真实请求仍需用户操作。

## 初次独立运行证据

审计员新增 `code/tests/test_quick_start_review.py` 五项回归：目录之后输出异常保留 owner、坏配置不替换指针、空 token 不启动、Codex 默认入口不启动 WorkBuddy、错误参数不回显输入。

在 `code/` 实际运行：

- `PYTHONPATH=src python -m unittest discover -s tests -p 'test_quick_start*.py' -v`：23 项，22 通过、1 Windows 专项跳过。
- `PYTHONPATH=src python -m unittest discover -s tests -p test_client_launcher.py -v`：9 项全部通过。
- `git diff --check`：通过。

Windows 专项 `test_windows_setup_real_recipe_and_config_round_trip` 已源码审查：仅替换原生入口，实际运行 export/build_plan/write_plan/load_node。它在本 Linux 审计环境未运行，必须以精确提交的 Windows CI 结果单独验收。完整测试、离线 demo 与最终打包证据由交付记录另列，不能用上述聚焦测试代替。

## 未验证边界

实际官方登录、账户/积分归属、登录保留、真实模型及生产三仓端到端均 NOT_RUN；无部署、合并或真实模型费用。本次源码审计无未关闭阻断，不等于这些边界已通过。


## Windows 重定向编码修正复审

后续 `00ac55f` 的完整包流程在私有 Python `start --help` 暴露中文帮助输出的编码缺陷。交付协调者报告 Windows 包运行 `42486138361` 在该步骤失败；不将此前测试矩阵通过当作打包通过。

本次独审直接比较该 HEAD 和修正工作树，在隔离 Python 子进程中显式将 stdout/stderr 设为 cp1252：

- 原 HEAD：退出 1，捕获到 `UnicodeEncodeError`，没有成功输出中文帮助。
- 修正工作树：退出 0，帮助输出可严格按 UTF-8 解码并包含中文标题。

最小修正仅在向导入口将当前进程 stdout/stderr 重配为 UTF-8；不修改 stdin、系统代码页、账户、持久状态或执行协议。新增测试通过真实隔离子进程复现编码条件，不 mock 输出编码。源码复审 **PASS**，上表启动器 hash 已更新。

独立重跑 `PYTHONPATH=code/src python -m unittest discover -s code/tests -p 'test_quick_start*.py' -q`：24 项，23 通过、1 Windows 专项跳过；`git diff --check` 通过。修正后的精确提交仍须重新验收 Windows 完整包流程，不能从 Linux 重现推定打包已通过。真实登录、模型和部署范围不变。
