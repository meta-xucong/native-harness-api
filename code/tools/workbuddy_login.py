"""Credential-free login wizard. Native mode is deliberately unavailable."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from beta15_login_flow import LoginFlow, FlowError, REVIEW_ITEMS, SCENARIOS, SYNTHETIC_ACCOUNT
from beta15_login_profile import create_simulation_profile, ProfileError

REAL_TEST_REQUIREMENTS = (
    '真实登录未接入。本模拟确认不授权未来真实登录、保存凭据或模型调用。',
    '先核验完整官方来源、版本与账号区域。固定 public beta16 SFE + CN WorkBuddy 5.9.3 Product 已通过云端匿名入口；这不证明真实登录、GUI 恢复、国际版或本机兼容。',
    '明确批准具体测试账号及 WorkBuddy 官方服务；不要复制现有账号目录、凭据、浏览器 cookie 或私有保护 bootstrap。',
    '先验证专用 OS/浏览器会话的账号隔离。原生 provider 可能先打开系统默认浏览器、复用其登录状态；HOME/Job 不能隔离 cookies 或保证关闭外部窗口。',
    '明确批准新的绝对存储位置、保留期限与实际保护方式。目录隔离不证明 Windows ACL 或凭据加密；未核验保护前不能开始保存真实凭据。',
    '真实测试须另行批准原生登录请求、浏览器打开和可能持久化。本人只在经核实的官方页面认证；绝不向终端或聊天粘贴密码、验证码、令牌、cookie 或登录链接。',
    '授权范围须包括取消后的账号/status 核验及有界关闭。取消发送不等于终态或回滚；状态不明或账号已出现时保留目录，不自动登出、删除或重试。',
    '现有 GUI 保护状态没有已验证的公开交接方案；公开 beta16 的完整登录仍未执行，不能把匿名成功当成凭据保护证据，也不能用私有注入或厂商补丁绕过。',
    '模型、费用、工具权限和本机测试必须另行明确授权；登录结果绝不自动启动模型或正式渠道。',
)

_OUTCOME_STORAGE = {
    'simulated_complete': 'SIMULATED_AUTHENTICATED',
    'cancelled_before_start': 'SIMULATED_CANCELLED_BEFORE_START',
    'cancelled_no_account': 'SIMULATED_CANCELLED_UNAUTHENTICATED',
    'cancelled_account_present': 'SIMULATED_CANCELLED_ACCOUNT_PRESENT',
    'account_present_unconfirmed': 'SIMULATED_ACCOUNT_PRESENT_UNCONFIRMED',
    'unauthenticated': 'SIMULATED_UNAUTHENTICATED',
    'identity_rejected': 'SIMULATED_IDENTITY_REJECTED',
    'unknown_retained': 'SIMULATED_UNKNOWN_RETAINED',
    'invalid': 'SIMULATED_UNKNOWN_RETAINED',
    'profile_refused': 'SIMULATED_REFUSED',
}


class ArgumentFailure(Exception):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default diagnostics echo arbitrary supplied values. Never do so.
        raise ArgumentFailure()


def _say(text, output, silent):
    if not silent:
        print(text, file=output)


def _yes(prompt, source, output):
    print(prompt + ' [yes/no]', file=output, flush=True)
    answer = source.readline(65)
    if not answer:
        return False
    if len(answer) > 64:
        raise ArgumentFailure()
    answer = answer.strip().lower()
    if answer in ('yes', 'y', '是', '同意'):
        return True
    if answer in ('no', 'n', '否', '取消', 'cancel'):
        return False
    raise ArgumentFailure()


def _drive_authored_peer(flow, scenario, scripted, source, output, silent):
    """Finite authored events only. No executable backend, transport, URL or RPC."""
    processed = 0
    while True:
        intents = flow.drain_intents()
        if not intents:
            return
        for intent in intents:
            processed += 1
            if processed > 16:
                raise FlowError('SIMULATION_BOUND_EXCEEDED')
            if intent == 'synthetic.begin_login':
                _say(flow.view()['message'], output, silent)
                if scenario in ('cancel-pending', 'cancel-race'):
                    flow.cancel()
                elif scenario == 'timeout':
                    flow.timeout()
                elif not scripted and not _yes('模拟手动认证已完成？输入 no 只会请求模拟取消，不要输入任何凭据。', source, output):
                    flow.cancel()
                else:
                    flow.peer_event('login_ack')
            elif intent == 'synthetic.cancel_request':
                flow.peer_event('cancel_delivered')
                _say(flow.view()['message'], output, silent)
                # The simulated peer must settle separately. Sending cancel alone
                # cannot create a terminal account result.
                flow.peer_event('login_error')
            elif intent == 'synthetic.account_read':
                present = not flow.summary()['cancel_requested'] or scenario == 'cancel-race'
                flow.peer_event('account_present' if present else 'account_absent')
            elif intent == 'synthetic.auth_status':
                flow.peer_event('status_authenticated')
                _say(flow.view()['message'], output, silent)
                accepted = scenario != 'identity-rejected'
                if accepted and not scripted:
                    accepted = _yes('只确认固定虚构身份 synthetic-test-account？', source, output)
                flow.confirm_identity(SYNTHETIC_ACCOUNT if accepted else 'reject')
            elif intent not in ('synthetic.close_client', 'synthetic.close_harness'):
                raise FlowError('UNKNOWN_SYNTHETIC_INTENT')


def run_simulation(scenario, parent, *, scripted=False, json_output=False, source=None, output=None):
    if type(scenario) is not str or scenario not in SCENARIOS:
        raise ArgumentFailure()
    if json_output and not scripted:
        raise ArgumentFailure()
    source = sys.stdin if source is None else source
    output = sys.stdout if output is None else output
    flow = LoginFlow(mode='simulate')
    profile = None
    storage = None
    try:
        _say('WorkBuddy 登录流程预演：仅虚构事件，不连接服务、不打开浏览器、不读取账号。', output, json_output)
        if scenario == 'cancel-before-start':
            flow.cancel()
        else:
            for key, disclosure in REVIEW_ITEMS:
                _say(disclosure, output, json_output)
                if not scripted and not _yes('是否理解这项模拟说明？', source, output):
                    flow.cancel()
                    break
                flow.acknowledge(key)
            if flow.phase == 'profile_required':
                if scenario in ('existing-state', 'protected-state'):
                    flow.profile_result('existing' if scenario == 'existing-state' else 'protected')
                else:
                    if not isinstance(parent, Path):
                        raise ArgumentFailure()
                    profile = create_simulation_profile(parent)
                    profile.verify_fresh()
                    flow.profile_result('fresh')
                    _say('新模拟目录（会保留，不复用）：' + json.dumps(str(profile.root), ensure_ascii=True), output, json_output)
                    flow.start()
                    _drive_authored_peer(flow, scenario, scripted, source, output, json_output)
    finally:
        flow.close()
        # Close labels are simulation markers, not evidence of native cleanup.
        remaining = flow.drain_intents()
        if any(item not in ('synthetic.cancel_request', 'synthetic.close_client', 'synthetic.close_harness') for item in remaining):
            raise FlowError('UNSETTLED_SIMULATION_INTENT')
    summary = json.loads(flow.canonical_summary_bytes())
    if profile is not None:
        storage = profile.record_outcome(_OUTCOME_STORAGE[summary['outcome']])
    report = {'schema_version': 1, 'scenario': scenario, 'flow': summary, 'storage': storage,
              'simulation_only': True, 'native_backend_available': False}
    encoded = json.dumps(report, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
    if len(encoded.encode('ascii')) > 8192:
        raise FlowError('SIMULATION_REPORT_TOO_LARGE')
    if json_output:
        print(encoded, file=output)
    else:
        _say(flow.view()['message'], output, False)
        if profile is not None:
            _say('仅保存固定模拟结果。目录保留；不证明 ACL、浏览器隔离或凭据加密。', output, False)
        _say('真实登录未发生，真实账号与模型仍未验证。', output, False)
    return report


def main(argv=None):
    parser = Parser(description=__doc__)
    parser.add_argument('--mode', help='simulate only; native is explicitly blocked')
    parser.add_argument('--scenario', default='success', help='fixed authored scenario')
    parser.add_argument('--profile-parent', help='canonical existing parent for a new simulation-only child')
    parser.add_argument('--scripted', action='store_true', help='fixed simulated acknowledgments, never real authorization')
    parser.add_argument('--json', action='store_true', help='bounded sanitized report; requires --scripted')
    parser.add_argument('--explain-real-test', action='store_true', help='read-only manual authentication and authorization checklist')
    try:
        args = parser.parse_args(argv)
        if args.mode == 'native':
            print('NATIVE_LOGIN_NOT_AVAILABLE: 真实登录未接入；未创建目录，也未读取账号或启动浏览器。', file=sys.stderr)
            return 2
        if args.explain_real_test:
            if args.mode not in (None, 'simulate'):
                raise ArgumentFailure()
            for item in REAL_TEST_REQUIREMENTS:
                print(item)
            return 0
        if args.mode != 'simulate' or args.scenario not in SCENARIOS:
            raise ArgumentFailure()
        if args.json and not args.scripted:
            raise ArgumentFailure()
        parent = None
        if args.profile_parent is not None:
            if not 0 < len(args.profile_parent) <= 4096:
                raise ArgumentFailure()
            parent = Path(args.profile_parent)
        if args.scenario not in ('cancel-before-start', 'existing-state', 'protected-state') and parent is None:
            raise ArgumentFailure()
        run_simulation(args.scenario, parent, scripted=args.scripted, json_output=args.json)
        return 0  # A completed rehearsal is not a real login success code.
    except KeyboardInterrupt:
        print('SIMULATION_INTERRUPTED: 如已创建模拟目录，它会保留；未启动真实登录。', file=sys.stderr)
        return 130
    except ArgumentFailure:
        print('INVALID_ARGUMENTS_OR_CHOICE: 请仅使用固定模拟选项，不要输入凭据；如已创建模拟目录，它会保留且不会自动重试。', file=sys.stderr)
        return 2
    except ProfileError as error:
        print('SIMULATION_PROFILE_REJECTED: 未读取账号；已有或部分新建的目录保留且不复用。', file=sys.stderr)
        if error.profile_id is not None:
            print('retained_simulation_profile=' + error.profile_id, file=sys.stderr)
        return 1
    except (FlowError, OSError, ValueError):
        print('SIMULATION_STOPPED: 未启动真实登录；如已创建模拟目录，它会保留。', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
