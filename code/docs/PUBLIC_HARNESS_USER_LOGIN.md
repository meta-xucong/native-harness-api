# 用户自己登录：公开 Harness 的本地 Windows 入口

本入口的目标是：用户下载完整客户端，在自己的 Windows 电脑上启动，亲自在官方
页面认证。开发者或远程代理不代登录，不需要用户交出密码、MFA、cookie 或 token。
与 `tools/workbuddy_login.py` 的无凭据模拟向导不同，新入口是
`tools/public_harness_login.py`。**真实账号登录及真实模型端到端仍为 NOT_RUN**；
测试里的账号与浏览器交互都是自写的模拟数据。

## 范围

当前只支持固定官方 public Harness `0.2.0-beta.16` Windows x64 SFE、完整 Node
`22.16.0` 与 CN WorkBuddy `5.9.3` Product。包内必须保留完整运行树和许可证，
不是单独复制一个 EXE。资产校验不通过即停止。已有的
[匿名原生入口实测](PUBLIC_HARNESS_CATALOG_DIAGNOSTIC.md)证明初始化与空账号读取
可工作，不证明新登录、用户电脑兼容或账号权限。国际版 WorkBuddy AI 的账号、
Product 与登录方法不同，不能在此入口静默替换。

不读取或复用现有 WorkBuddy 账号目录，不恢复 GUI 受保护状态，不注入私有
bootstrap，不提取或转发厂商凭据。原生程序本身仍可写入新目录、访问官方服务或
初始化后台资源，不能称为无副作用或无网络。

## 本地使用

以下是用户完成部署后自行运行的命令，不是开发阶段已执行的登录记录。打包客户端
可封装相同入口，`assets` 目录由打包器提供：

```text
python tools/public_harness_login.py --explain
python tools/public_harness_login.py --assets-root C:\WorkBuddyClient\assets --profile-parent C:\WorkBuddyProfiles
```

`--profile-parent` 必须是已存在、受信的本地绝对目录。不要选择网络盘、映射盘、
junction、符号链接、设备路径或他人可以改动的目录。程序只创建随机命名的
`workbuddy-managed-<id>` 新子目录，不接受旧账号目录作为登录输入，也不自动恢复
一次未结算的登录。终端会显示这个新目录的位置，供以后显式 `--profile` 选择。

入口只接受 Windows 的真实本地交互终端。没有无人值守 `--yes`、脚本确认或环境变量
绕过。每次提示只输入 `yes` 或 `no`；不要在终端输入密码、验证码、token 或登录链接。

1. 阅读并确认服务、浏览器、存储与保留说明，然后创建新目录并启动已校验的原生程序。
2. 核验 Product/catalog 与空账号后，再明确确认发起一次官方 CN 登录。
3. 在原生打开的官方页面亲自认证。原生可能在外层交互提示之前打开默认浏览器。
   本入口不另开浏览器，不打印或保存登录 URL，不把任意表单转成终端凭据输入。
4. 原生请求结算后，分别回读账号与 `auth/status`，再在本地终端确认必要的公开账号
   身份。ACK 不是成功证明；`auth/status` 也不是远端套餐或模型权限证明。
5. 只有身份确认、自然退出 0、owned Job 为空、IO/句柄清理完成及目录权限复核都通过，
   才保存无凭据的账号身份摘要供后续匹配。不会自动创建模型会话或发送 prompt。

## 浏览器与存储边界

默认浏览器可能复用当前 Windows 账号已有的浏览器登录状态。`HOME`、新目录和
Windows Job 均不能隔离 cookies，也不能控制已存在的浏览器窗口。需要隔离时，用户
应先自行准备专用 OS/浏览器上下文。本工具没有已核验的公开原生浏览器 profile
参数，不使用内部 opener 或 `BROWSER` 技巧冒充隔离。

新 profile 在创建时使用仅当前 Windows 用户和 SYSTEM 的受保护 ACL，并核验所有
子目录/文件的 ACL 与普通文件属性。独立目录包括 `home/config/appdata/localappdata/`
`temp/workspace/logs/control`。`control` 不加入原生环境变量，也不是原生 cwd，用于
客户端自己的控制记录。整个目录仍属于同一 OS 用户，这不是多租户或恶意同用户的
安全边界，也不能抵抗有权修改父目录、管理员或正在并发篡改文件的进程。
受保护根目录 owner 必须为当前 TokenUser；新文件可能采用当前进程的实际 TokenOwner
（例如提升权限时的 Administrators）。只接受这个 OS 查询所得的 owner 备选，不增加
DACL 读取主体；owner 本身仍有修改 DACL 的能力，不能据此声称隔离管理员。

ACL **不是原生凭据加密**。公开 beta16 的保护源码存在默认关闭路径，当前没有已
核验的公开独立密钥初始化合同，未测量编译 SFE 的真实登录存储。新目录中的原生
凭据、备份、临时文件与日志可能敏感，应保留在用户认可的 OS/磁盘保护条件下。
不要同步、上传或分享整个 profile。工具只读取自己的有限 metadata，不读取原生
凭据或日志内容。原生登出也不等于安全擦除；本入口不会自动登出或删除目录。

## 取消、崩溃和后续执行

取消仅发送协议通知，不代表请求已结算或账号没有保存。取消/超时后等待有限的
结算与账号回读；不明状态保留目录且禁止自动重试。如果 owned 进程或 IO 仍无法
确认清理，保留 owner 和 profile 锁，不把强制终止当作成功。

每次模型执行前，客户端必须先持久写入 `execution_started`，并在实际初始化时重新
核验 Product、账号摘要和 `auth/status`。metadata 里写着已确认不能代替真实回读。
只有同一 profile handle 本轮持有者，且确认 owned cleanup 后，才能恢复可用标记；
崩溃留下的 `execution_started` 或 `execution_unknown` 不能由重启自动恢复。
这层目录门禁不替代 Bridge 的执行账本，不授权重跑可能已执行的同一请求。

模型目录发现、模型选择、工具授权与实际 prompt 是后续由用户单独触发的操作。
登录成功不自动批准任何工具，也不证明模型调用可用。开发测试不产生真实模型费用，
未部署到用户电脑或 VPS。

## 用户单独读取模型目录

登录确认后，使用终端显示的新 profile 路径：

```text
python tools/public_harness_models.py --assets-root C:\WorkBuddyClient\assets --profile C:\WorkBuddyProfiles\workbuddy-managed-<id>
```

此命令实时核验账号后创建并关闭一个空原生 session，读取真实 `configOptions`，
不选择模型、不发送 prompt，也不自动写绑定。空 session 仍可能初始化服务、缓存或
网络，不是无副作用操作。只有自然退出及完整清理验证后才显示原生模型 ID；用户
再选择一个精确 ID 填入本地配置的 `native_model`。目录存在不保证套餐、额度或模型
调用成功。失败不自动重试，清理/状态不明时保留并隔离该 profile。

开发期间只测试自写对端；**真实已登录模型目录及模型调用均为 NOT_RUN**。
