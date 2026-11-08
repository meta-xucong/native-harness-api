"""Thin, user-run WorkBuddy setup/start. No GUI credential access or inference probe."""
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
    from prepare_native_deployment import build_plan, write_plan
    print('同账号独立官方登录，不复用 GUI 会话；登录保留多久由官方决定。')
    profile = choose_profile(root)
    target = discover_target(root, profile)
    spec = {'schema_version': 2, 'windows_data_dir': str(profile / 'control'), 'targets': [target],
        'bridge_origin': input('Bridge HTTPS 地址：').strip(),
        'user_id': int(input('Sub2API 用户数字 ID：')),
        'api_key_id': int(input('Sub2API 专用 Key 数字 ID（不是 Key 密钥）：')),
        'bridge_data_dir': input('服务器 Bridge 数据目录（如 /var/lib/native-bridge）：').strip()}
    output = root / 'connection'
    write_plan(output, build_plan(spec))
    path = output / 'windows-node.json'
    load_node(path)
    remember(root, path)
    print('配置已写入 connection，准入仍关闭；本次没有启动 Node 或发起模型请求。')
    print('将 bridge.json / facade.json / sub2api.native-harness.yaml 交服务端配置；其余文件留本机。')
    print('按 DEPLOYMENT_CHAIN.zh-CN.md 完成服务器接入后，再双击 Client.cmd。由你确认账号并发起最小请求。')
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


def main(root, argv=None):
    # Windows redirected stdio may default to cp1252, unlike the UTF-8 console.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = Parser(description='WorkBuddy 首次设置 / 使用原配置启动。不会自行发送模型请求。')
    parser.add_argument('--config', type=Path, help='明确选择已有 windows-node.json，保留原身份及任务记录')
    args = parser.parse_args(argv)
    root = Path(root).resolve()
    try:
        console_required()
        if args.config is not None:
            path = args.config.resolve()
        elif (root / 'launcher.json').exists():
            path = Path(json.loads((root / 'launcher.json').read_text(encoding='utf-8'))['config'])
        elif (root / 'connection').exists():
            # A previous setup may have finished before its pointer was saved.
            path = root / 'connection' / 'windows-node.json'
            if not all((root / 'connection' / name).is_file() for name in
                       ('windows-node.json', 'bridge.json', 'facade.json', 'sub2api.native-harness.yaml', 'deployment-identities.json')):
                raise SetupError('connection 是未完成的配置。请保留现场检查，不会重新生成部署身份。')
        else:
            print('已有部署必须选择原 Node 配置，不能用新部署替代旧任务或旧 data_dir。')
            supplied = input('已有 windows-node.json 的完整路径（只有全新部署才回车）：').strip().strip('"')
            if not supplied:
                return setup(root)
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
