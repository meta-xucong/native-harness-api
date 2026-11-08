# 最简启动器独立设计审计

日期：2026-11-08 UTC。结论：**ACCEPT，允许按已修订设计实现**，不是源码、Windows 或真实账户验收。

对象：`SIMPLE-LAUNCHER.md`，SHA-256 `c368fd39c8fb17ab621060c3683726a260bf105841b30dd56013647fd8816a61`。实现基线 Native `c6b2f45`；Bridge/Sub2API 无需新增接缝。

## 已核对

独立阅读既有 `client.py`、`public_harness_login` / `public_harness_login_profile`、`public_harness_models`、`prepare_native_deployment`、`RuntimeConfig`、`NativeTrialConfig` 和 `MultiHarnessConfig`。现有函数能承担登录确认、目录发现、target 导出、配置生成及 Node 恢复，不需要新认证层或调度器。

- 已保存配置直接进原 Node，不以登录或目录探测挡住 journal 恢复。
- 首次配置只生成本机文件，准入关闭，不自动部署或提交 prompt。
- GUI 凭据、私有 RPC、账号配额和持久登录保证不在实现承诺内。
- 单个薄向导、一个路径指针、固定配置输出目录足够；不新增队列、watchdog、重试或通用凭据框架。

## 审计中关闭的问题

1. **既有执行资源误建新身份。** 新部署只限未运行 Node 的 profile；已有 journal/native-evidence 要求选择原配置，不能生成新 source/ledger。已有 v1/v2 配置不迁移，执行资源路径不猜测。
2. **未知 profile 被过滤后隐式新建。** 所有保留候选都参与选择，包括未完成登录或不完整创建目录。选中未知状态停止，不能把它当作不存在并自动换 profile。
3. **目录工具未知 owner 被向导退出释放。** 即使输出或向导后续操作失败，也必须保留原 `RETAINED` 持有行为。
4. **配置写完、指针未保存的中断。** 固定 `connection/` 输出目录；下次复用已生成配置，部分输出保持现场，不重新生成身份。
5. **把 Facade 或 synthetic 配置当成用户 Node。** 复用原加载器并限定原生 Node 配置类型，不增加一套字段校验。

## 实现复核重点

必须用离线回归验证上述分支，并核对 Windows 默认入口和包内容。首次流程应明确告知已有部署用户选择原配置，因为任意外置 v2 data_dir 不能单靠 profile 内目录推断。模型发现完成后再生成配置，失败不可保存 target；原生异常所有权保留须覆盖异常和中断。真实登录、模型与生产状态仍 NOT_RUN。

本次审计只读取源文件并编写此文档，未运行厂商程序、登录、模型或部署。
