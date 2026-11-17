"""Restore an existing managed WorkBuddy profile using the local Node package."""
import argparse
import sys
from pathlib import Path

from native_harness_api.supervised_acp_transport import SupervisedACPTransport
from native_harness_api.workbuddy_node_session import WorkBuddyRecoverySession
from public_harness_login import safe_label
from public_harness_login_profile import open_profile
from public_harness_models import RETAINED, hold_retained
from public_harness_runtime_assets import verify_node_runtime_assets, workbuddy_command


def recover(assets, profile, confirm, *, transport=None):
    """Hold the profile lock across restore, process cleanup and confirmation."""
    engine = None
    cleaned = False
    uncertain = profile.state == 'unknown_retained'
    try:
        if profile.state not in ('unknown_retained', 'authenticated_confirmed'):
            return 'PROFILE_STATE_REFUSED'
        if uncertain:
            profile.begin_recovery()
        else:
            profile.begin_execution()
        workspace = profile.root / 'workspace'
        env = profile.environment(assets['host_node'], node_runtime=True)
        engine = WorkBuddyRecoverySession(transport or SupervisedACPTransport(), capture_account=uncertain,
                                          product_version=assets['product_version'])
        result = engine.run(workbuddy_command(assets), workspace, env, workspace=str(workspace),
                            model_id=None, prompt=None, expected_account_digest=profile.expected_account_digest,
                            timeout=60, cleanup_timeout=10)
        cleaned = all(result['cleanup'].get(key) is True for key in
                      ('host_exited', 'owned_job_empty', 'handles_closed', 'cleanup_complete', 'io_threads_stopped'))
        if not cleaned:
            return 'CLEANUP_UNCONFIRMED'
        protocol = engine.protocol
        if not result['cleanup_confirmed'] or result['fault'] or not protocol.ready:
            if uncertain:
                profile.finish_recovery(None)
            else:
                profile.finish_execution(cleanup_confirmed=True)
            return 'RECOVERY_CANCELLED' if result['fault'] == 'CANCEL_REQUESTED' else 'ACCOUNT_OR_PRODUCT_NOT_READY'
        if uncertain:
            accepted = False
            try:
                accepted = confirm(protocol.identity) is True
            finally:
                profile.finish_recovery(protocol.restored_digest if accepted else None)
            return 'RESTORED' if accepted else 'ACCOUNT_NOT_CONFIRMED'
        profile.finish_execution(cleanup_confirmed=True)
        return 'RESTORED'
    finally:
        if engine is not None and engine._attempted and not cleaned:
            RETAINED.append((engine, profile))
        else:
            profile.close()


def confirm_account(identity):
    identifier, display_name, organization = identity
    print('已恢复的账号：' + safe_label(display_name) + '  ID: ' + safe_label(identifier))
    if organization is not None:
        print('企业 ID：' + safe_label(organization))
    return input('确认这是你的账号（yes 后按回车）：').strip().lower() == 'yes'


def main(assets_root, argv=None):
    parser = argparse.ArgumentParser(description='恢复本工具原 WorkBuddy profile。使用官方保存的登录状态。')
    parser.add_argument('--profile', required=True, type=Path)
    args = parser.parse_args(argv)
    if not all(stream.isatty() for stream in (sys.stdin, sys.stdout, sys.stderr)):
        parser.error('请在 Windows 终端运行恢复命令。')
    assets = verify_node_runtime_assets(assets_root)
    print('正在恢复原 profile。WorkBuddy 可能续期并写回账号状态。', flush=True)
    try:
        status = recover(assets, open_profile(args.profile), confirm_account)
        messages = {
            'RESTORED': '原 profile 的账号和 Product 已就绪，进程已退出。可以运行 models 查看目录。',
            'ACCOUNT_OR_PRODUCT_NOT_READY': '官方账号或 Product 未就绪。原 profile 已保留。',
            'ACCOUNT_NOT_CONFIRMED': '账号未确认。原 profile 已保留。',
            'RECOVERY_CANCELLED': '恢复已取消，进程已退出，原 profile 已保留。',
            'PROFILE_STATE_REFUSED': '此 profile 状态不允许恢复。请保留原任务及现场。',
            'CLEANUP_UNCONFIRMED': '进程清理未确认。请保留此窗口以便排查。',
        }
        print(messages[status], flush=True)
        return 0 if status == 'RESTORED' else 1
    finally:
        hold_retained()
