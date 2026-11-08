# 最简启动器测试记录

2026-11-08 UTC，基线 Native c6b2f45，独审见 SIMPLE-LAUNCHER-AUDIT.md。Bridge/Sub2API 未改动。

## 本地 Linux 实际执行

- `cd code; PYTHONPATH=src python -m unittest discover -s tests -v`：954 项，895 通过、59 平台/条件跳过。
- 向导及独审聚焦：24 项，23 通过、1 Windows 路径专项跳过。
- 入口回归：9 项通过；Codex 无参数保持帮助，不进入 WorkBuddy 向导。
- `PYTHONPATH=src:. python examples/offline_demo.py`：通过，synthetic 单次启动、相同结果回放，真实 Bridge/Sub2API 集成 false。
- `PYTHONPATH=src python -m compileall -q src tests examples integration packaging tools`：通过。
- `git diff --check`：通过。

覆盖首次 setup、保留/多 profile、未知/部分 profile 不另建、旧配置原样直达恢复、部分 connection 不重造身份、真实 Windows 配置生成专项、目录失败与输出异常的原 owner 保留、token 仅本进程及参数脱敏。

## 分离验收

此提交时 Windows CI/完整包待运行，结果将在 draft PR 的精确提交记录更新。Windows roundtrip 使用自写原生替身，真实执行 export/build_plan/write_plan/load_node；不是账号验收。既有官方资产只校验和打包，不运行官方登录或模型。

真实官方账号登录、积分/配额归属、登录保持时长、真实模型与生产部署：NOT_RUN / NOT_VERIFIED。无真实模型费用，无合并或部署。首次服务端接入仍需要用户提供实际 Bridge 地址、数字 user/key ID、服务端目录及 Node token，不能宣称双击完成整套部署。

## Windows 实测发现与修正

首版 `00ac55f9` 的 push/PR 矩阵均通过；Windows 944 项、6 条件跳过，独立 controller 9 项通过，真实 Windows 配置往返专项实际通过。完整客户端包 run `42486138361` 在随包私有 Python 执行 `start --help` 时失败，未上传这个包。

本地强制 stdout 为 cp1252 确定性复现 `UnicodeEncodeError`：Windows 重定向输出默认编码无法显示新增中文帮助。修正仅在薄向导入口将 stdout/stderr 配为 UTF-8，不修改系统或 stdin，不放松断言。新增子进程 cp1252 回归，保留相同 Windows 打包命令验证。修正提交的 CI/包结果另在 PR 精确记录，不将首版打包失败改写为成功。
