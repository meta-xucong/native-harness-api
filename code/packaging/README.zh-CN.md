# Windows x64 本机 WorkBuddy 入口测试包

本包是独立诊断/单次入口测试程序，不是模型 API、Bridge 服务、Sub2API 或完整渠道。不会下载或附带 WorkBuddy，不读取/导出厂商 token，不安装系统 Python，不需要 pip、数据库、管理员权限或 PowerShell 策略修改。

1. 解压整个 ZIP 到独立目录，不要在 ZIP 内运行。核对发布页提供的 ZIP SHA256。包内清单仅检查完整性，不能替代可信发布来源或代码签名。
2. 自行安装并登录官方 WorkBuddy。将 `config.example.json` 复制到自己的配置文件。也可通过 `Probe.cmd setup --config "C:\your\config.json"` 交互创建配置（不运行模型）。明确填入官方安装的 executable 和官方 codebuddy/codebuddy-lite-wb cli_entry、专用 data_dir、空白 workspace 及已核实的原生 model_id。示例安装路径只是占位，不是自动发现结果。不要使用生产目录、源代码或私人文件作为测试 workspace。`native_config_dir` 默认 null，仅在用户明确指定时传给厂商进程，本程序不读其内容。`timeout_seconds` 范围 10..600，默认 120。
3. 打开 cmd：`Probe.cmd doctor --config "C:\your\config.json"`。doctor 默认只做静态检查，不启动 WorkBuddy。明确加 `--launch` 才启动无 prompt 的 ACP 探测；探测也不代表真实选模或模型调用已成功。
4. 根据 doctor 输出把配置中的 `model_id` 改成实际候选 ID，不用 auto 或占位值。用户自行准备 UTF-8 prompt 文件，再执行 `Probe.cmd run --config "C:\your\config.json" --prompt-file "C:\your\prompt.txt"`。run 必须在交互终端输入 `RUN REAL WORKBUDDY` 确认一次，没有免确认参数，拒绝管道输入；原生审批也必须交互。真实调用可能产生厂商费用和工具操作。不要放敏感内容。
5. `inspect --config ...` 查看原状态；`export --config ... --output ...` 导出诊断。分享前人工检查导出内容。

不要删除 state、换 data_dir 或重复运行来绕过未知执行状态。关闭窗口或超时不证明 WorkBuddy/工具进程已停止。启动失败、权限限制、模型身份无法证实均不是成功，不代表可以静默重试。软件目录隔离不等于操作系统沙箱。

state 与 workspace、原生安装目录、可选原生配置目录不能相同或互为祖先，避免正常工作区清理/软件更新删除防重证据。目录链接也被拒。默认导出不包含原生任意 name/version/model 文本；只给白名单状态和摘要。doctor 显示的本机信息不等于可直接分享的脱敏报告。

首测显式要求原生 `default` 权限模式和模型选择回读，不接受 plan/fullAccess。已有原生 allow 规则仍然生效；只对原生实际发出的审批询问，不保证每个工具都弹审批。发送前会检查已收到的模式/模型更新，但无法为仍在飞行中的异步更新提供跨进程原子排序保证。不要在试验中从其他窗口修改同一原生会话/配置。

一个 data_dir 仅允许一次真实 prompt。相同命令重跑只读已有结果，未知状态不重发；没有 reset 命令。终态输出只说明 ACP turn 返回，不证明后台工具全部结束，也不证明厂商实际 provider 模型；JSON 导出明确保留这些限制。先用无副作用短文本，比如“请只回复 LOCAL WORKBUDDY TEST，不要调用工具”。该提示不是安全边界，仍须人工看原生界面和权限。

## 发行来源与许可

私有运行时：官方 CPython 3.13.17 Windows x64 embeddable package，不附加 pip、不改变 `_pth`。
- 下载：https://www.python.org/ftp/python/3.13.17/python-3.13.17-embed-amd64.zip
- SHA256：`9e35319bffedab8b36ad44d8f377cb4f1733bcec257a0b6d04d94f01486ddb38`
- 摘要来源：官方 https://www.python.org/downloads/release/python-31317/ 和同路径 `.spdx.json`，2026-11-07 核对下载字节一致。未声称独立验证 Authenticode/Sigstore 签名。
- 官方包 `LICENSE.txt` 原样保留为 `runtime/LICENSE.txt`，包含 Python 及所附组件许可；本程序不修改 Python 二进制。
- WorkBuddy 软件与服务许可另行适用；本包不赋予商业转供权限。本项目自身许可证未另行选定。

## 可复现构建（开发者）

源码冻结并审计后，使用 stdlib Python：
```
python code/packaging/build.py --runtime-zip python-3.13.17-embed-amd64.zip --source-revision FULL_AUDITED_COMMIT_SHA --output workbuddy-probe-windows-x64.zip
```
构建器离线，不执行下载的 Windows 程序；固定源码 allowlist、运行时官方摘要、排序及 ZIP 时间戳。CLI 离线核验 HEAD 精确等于 `--source-revision`，所有 allowlist 源码、资源及构建器自身必须逐字节等于该提交的 Git blob，否则拒绝。发布 checkout 必须保留提交的 LF（建议仓库设置 `.gitattributes`）；Windows 自动 CRLF 转换不放宽此检查。打包应用文本统一 UTF-8/LF，官方 runtime 二进制及许可字节不变。审计批准仍需独立确认，Git hash 本身不代表批准。构建器拒绝覆盖已有包。最终包生成前必须完成源码审计。

Windows 实机、官方 WorkBuddy 真实模型/审批/终态测试若尚无独立记录则为 NOT_RUN。Linux mock CLI 冒烟不能替代 Windows 测试。真实 PostgreSQL 接缝未验收，本包不包含或替代该验收。
