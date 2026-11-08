"""Thin, user-run WorkBuddy / existing Codex CLI setup/start. No GUI credential access or inference probe."""
import argparse
import getpass
import json
import os
from pathlib import Path
import sys


class SetupError(ValueError):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, _message):
        self.exit(2, "参数无效。使用 start --help 查看说明；未显示输入值。\n")


def console_required():
    if os.name != 'nt' or not all(s.isatty() for s in (sys.stdin, sys.stdout, sys.stderr)):
        raise SetupError('请在 Windows 终端双击 Client.cmd 运行。')


def load_node(path):
    from native_harness_api.runtime import RuntimeConfig
    from native_harness_api.native_trial_runtime import NativeTrialConfig
    from native_harness_api.multi_harness_runtime import MultiHarnessConfig
    config = RuntimeConfig.load(path)
    if not isinstance(config, (NativeTrialConfig, MultiHarnessConfig)):
        raise SetupError('请选择原 windows-node.json，不是 Facade 或模拟配置。')
    return config


def remember(root, path):
    # The pointer is not execution state; an interrupted write leaves the old one.
    temporary = root / 'launcher.json.tmp'
    temporary.write_text(json.dumps({'config': str(path)}, ensure_ascii=False) + '\n', encoding='utf-8')
    temporary.replace(root / 'launcher.json')


def retained_profiles(parent):
    from public_harness_login_profile import PREFIX
    # Include partial/unknown profiles, not only successful ones. Never silently
    # create a replacement after a cancelled or uncertain native operation.
    return sorted(parent.glob(PREFIX + '*'))


def choose_profile(root):
    import public_harness_login as login
    parent = root / 'profiles'
    supplied = input('已有本工具 profile 的完整路径（回车自动选择；不是 WorkBuddy GUI 目录）：').strip().strip('"')
    if supplied:
        return Path(supplied).resolve()
    parent.mkdir(exist_ok=True)
    candidates = retained_profiles(parent)
    if not candidates:
        if login.main(['--assets-root', str(root / 'assets'), '--profile-parent', str(parent)]) != 0:
            raise SetupError('登录未确认。已保留 profile，不会自动重试或另建账户。')
        candidates = retained_profiles(parent)
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise SetupError('没有找到本次登录保留的 profile。请保留现场。')
    for number, path in enumerate(candidates, 1):
        print(f'{number}. {path.name}')
    choice = int(input('选择要保留使用的 profile 编号：'))
    if not 1 <= choice <= len(candidates):
        raise SetupError('未选择有效 profile。')
    return candidates[choice - 1]


def discover_target(root, profile_path):
    import public_harness_models as models
    from public_harness_login_profile import open_profile
    from public_harness_runtime_assets import verify_runtime_assets
    assets = verify_runtime_assets(root / 'assets')
    print('读取官方模型目录，不发送测试 prompt；这不证明账号积分或模型可调用。')
    profile = open_profile(profile_path)
    # Only pre-native failures close here. discover_models owns cleanup from then on.
    try:
        if profile.state != 'authenticated_confirmed':
            raise SetupError('保留的 profile 未确认可用。请检查原登录结果，不会自动重新登录。')
        if any((profile.root / 'control' / name).exists() for name in ('journal', 'native-evidence')):
            raise SetupError('此 profile 已有执行记录。请用 Client.cmd start --config "原 windows-node.json 路径" 恢复。')
        identity = (profile.root, profile.root.name, profile.expected_account_digest)
    except BaseException:
        profile.close()
        raise
    try:
        result = models.discover_models(assets, profile)
        if not result['success']:
            raise SetupError('模型目录未确认。保留 profile，不重试、不生成配置。')
        choices = result['catalog']['models']
        for number, model in enumerate(choices, 1):
            print(f"{number}. {model['id']}  {ascii(model['name'])}")
        choice = int(input('选择模型编号：'))
        if not 1 <= choice <= len(choices):
            raise SetupError('未选择有效模型。')
        alias = input('API 模型别名（回车 workbuddy-local）：').strip() or 'workbuddy-local'
        path, profile_id, fingerprint = identity
        return models.export_target(result, profile_root=path, profile_id=profile_id,
            account_fingerprint=fingerprint, assets_root=root / 'assets',
            native_model=choices[choice - 1]['id'], public_alias=alias)
    finally:
        models.hold_retained()


def setup(root):
    print('同账号独立官方登录，不复用 GUI 会话；登录保留多久由官方决定。')
    profile = choose_profile(root)
    target = discover_target(root, profile)
    return write_connection(root, profile, target)


def choose_codex_model(choices):
    if not choices:
        raise SetupError('官方目录未提供可选模型；没有生成配置。')
    defaults = [number for number, model in enumerate(choices, 1) if model.get('isDefault') is True]
    default = defaults[0] if len(defaults) == 1 else None
    for number, model in enumerate(choices, 1):
        marker = '（默认）' if number == default else ''
        print(f"{number}. {ascii(model['model'])}  {ascii(model.get('displayName'))} {marker}")
    choice = input('选择模型编号' + (f'（回车 {default}）' if default else '') + '：').strip()
    number = default if not choice and default else int(choice)
    if not 1 <= number <= len(choices):
        raise SetupError('未选择有效模型。')
    return choices[number - 1]['model']


def setup_codex(root):
    import codex_user_cli as cli
    print('复用当前用户已有官方 Codex CLI 与 CODEX_HOME，不复制凭据、不重新登录、不发送测试 prompt。')
    print('已有配置、MCP、skills 和企业策略仍可能生效；官方程序可能刷新自身认证。')
    print('官方 App Server 认证不允许用于商业或托管服务；本机连接成功不等于获准向 404token 转供。')
    parent = root / 'profiles'
    supplied = input('已有本工具 Codex sidecar 路径（不是 CODEX_HOME；回车自动选择）：').strip().strip('"')
    candidates = [Path(supplied).resolve()] if supplied else sorted(parent.glob(cli.PREFIX + '*'))
    if len(candidates) > 1:
        for number, candidate in enumerate(candidates, 1):
            print(f'{number}. {candidate.name}')
        number = int(input('选择保留的 Codex sidecar 编号：'))
        if not 1 <= number <= len(candidates):
            raise SetupError('未选择有效 sidecar。')
        candidates = [candidates[number - 1]]
    profile = None
    try:
        if candidates:
            profile = cli.open_profile(candidates[0])
            if profile.state not in ('fresh', 'authenticated_confirmed'):
                raise SetupError('保留的 Codex 连接尚未确认。请检查原状态，不会重建或自动登录。')
            if any((profile.root / 'control' / name).exists() for name in ('journal', 'native-evidence')):
                raise SetupError('此 sidecar 已有任务记录。请选择原 windows-node.json 恢复。')
            context = cli.read_context(profile)
        else:
            executable = input('官方 codex.exe 路径（回车使用 PATH 中已有安装）：').strip().strip('"')
            context = cli.resolve_cli(executable or None)
        print('实际 CLI：' + context['cli_path'])
        print('原 CODEX_HOME：' + context['codex_home'])
        if input('确认这是你要使用的官方 CLI 和原登录环境？输入 yes：').strip().lower() != 'yes':
            raise SetupError('已取消；未启动 Codex。')
        cli.verify_version(context)
        if profile is None:
            parent.mkdir(exist_ok=True)
            profile = cli.create_profile(parent, context)
        confirmed = profile.state == 'authenticated_confirmed'
        result = cli.discover(profile, context)
        account = result['account']
        print('账号：' + ascii(account.get('email')))
        print('工作区：' + ascii(account.get('workspaceName')))
        if not confirmed:
            if input('确认这是你要使用的账号？输入 yes：').strip().lower() != 'yes':
                raise SetupError('账号未确认；sidecar 保留，没有生成配置。')
            cli.confirm_profile(profile, account)
        native_model = choose_codex_model(result['models'])
        alias = input('API 模型别名（回车 codex-local）：').strip() or 'codex-local'
        target = cli.export_target(profile, context, result['models'], native_model, alias)
        return write_connection(root, profile.root, target)
    except ValueError as error:
        messages = {
            'CODEX_CLI_NOT_FOUND': '没有找到已有 Codex CLI。请选择已安装的官方 codex.exe；不会自动下载。',
            'SELECT_OFFICIAL_CODEX_EXE': '未能定位 npm 安装的原生入口。请明确选择已有官方 codex.exe，不执行未知脚本。',
            'EXISTING_CODEX_HOME_REQUIRED': '没有找到当前用户原 CODEX_HOME；请在原 Codex 登录环境运行，不会创建空登录目录。',
            'CODEX_HOME_NOT_FOUND': '无法确定原 CODEX_HOME。请使用原 Codex 登录环境。',
            'CODEX_VERSION_UNSUPPORTED': '当前 CLI 版本未受此适配器支持（需要 0.167.0）；没有换包、降级或生成配置。',
            'CODEX_CONTEXT_CHANGED': '所选 CLI 或登录环境已经变化；没有替换原任务或生成配置。',
            'CODEX_CONNECTION_NOT_VERIFIED': '账号、目录或连接未确认。请保留 sidecar，并在同用户、同 CODEX_HOME 下用官方 codex login status 排查；未登录时由你完成官方登录。没有自动重试或生成配置。',
        }
        if str(error) in messages:
            raise SetupError(messages[str(error)]) from None
        raise
    finally:
        if profile is not None:
            cli.close_profile(profile)
        cli.hold_retained()


def write_connection(root, profile, target):
    from prepare_native_deployment import build_plan, write_plan
    spec = {'schema_version': 2, 'windows_data_dir': str(profile / 'control'), 'targets': [target],
        'bridge_origin': input('Bridge HTTPS 地址：').strip(),
        'ingress': 'openai',
        'bridge_data_dir': input('服务器 Bridge 数据目录（如 /var/lib/native-bridge）：').strip()}
    output = root / 'connection'
    write_plan(output, build_plan(spec))
    path = output / 'windows-node.json'
    load_node(path)
    remember(root, path)
    print('配置已写入 connection，准入仍关闭；本次没有启动 Node 或发起模型请求。')
    print('将 bridge.json / facade.json / openai-provider.json 交服务端配置；其余文件留本机。')
    print('按 OPENAI_PROVIDER.md 配置原生 Sub2API 普通上游后，再双击 Client.cmd。由你确认账号并发起最小请求。')
    return 0


def start_node(path, config):
    from native_harness_api.runtime import main as runtime
    name = config.node_token_env
    provided = name not in os.environ
    if provided:
        token = getpass.getpass('Node 连接 token（只在本次进程使用，不保存）：')
        if not token:
            raise SetupError('未提供 Node token；没有启动。')
        os.environ[name] = token
    try:
        print('启动唯一 Node，沿用原配置和任务记录。Ctrl+C 停止；不要删除未决记录。')
        return runtime(['node', '--config', str(path)])
    finally:
        if provided:
            os.environ.pop(name, None)


def main(root, argv=None, *, harness='workbuddy'):
    # Windows redirected stdio may default to cp1252, unlike the UTF-8 console.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = Parser(description='首次设置 WorkBuddy / 已有 Codex CLI，或使用原配置启动。不会自行发送模型请求。')
    parser.add_argument('--config', type=Path, help='明确选择已有 windows-node.json，保留原身份及任务记录')
    args = parser.parse_args(argv)
    root = Path(root).resolve()
    try:
        if harness not in ('workbuddy', 'codex'):
            raise SetupError('未知客户端类型。')
        console_required()
        if args.config is not None:
            path = args.config.resolve()
        elif (root / 'launcher.json').exists():
            path = Path(json.loads((root / 'launcher.json').read_text(encoding='utf-8'))['config'])
        elif (root / 'connection').exists():
            # A previous setup may have finished before its pointer was saved.
            path = root / 'connection' / 'windows-node.json'
            connection = root / 'connection'
            if (not all((connection / name).is_file() for name in
                        ('windows-node.json', 'bridge.json', 'facade.json', 'deployment-identities.json'))
                    or not any((connection / name).is_file() for name in
                               ('sub2api.native-harness.yaml', 'openai-provider.json'))):
                raise SetupError('connection 是未完成的配置。请保留现场检查，不会重新生成部署身份。')
        else:
            print('已有部署必须选择原 Node 配置，不能用新部署替代旧任务或旧 data_dir。')
            supplied = input('已有 windows-node.json 的完整路径（只有全新部署才回车）：').strip().strip('"')
            if not supplied:
                return setup_codex(root) if harness == 'codex' else setup(root)
            path = Path(supplied).resolve()
        config = load_node(path)
        remember(root, path)
        return start_node(path, config)
    except SetupError as error:
        print(str(error), file=sys.stderr)
        return 1
    except (Exception, KeyboardInterrupt):
        print('启动已停止。请检查输入、原配置和保留的 profile；未自动重试、换账号或删除状态。', file=sys.stderr)
        return 1
