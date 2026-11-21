"""Credential-free login wizard contract. No IO, native backend, or credentials.

Only fixed synthetic events are admitted. Intents are inert test labels, not RPC
commands or authority to perform real login. The CLI owns authored simulation
and profile orchestration; this module never calls an injected executable port.
"""
from __future__ import annotations

import json

SYNTHETIC_ACCOUNT = 'synthetic-test-account'
SCENARIOS = ('success', 'cancel-before-start', 'cancel-pending', 'cancel-race',
             'timeout', 'existing-state', 'protected-state', 'identity-rejected')
REVIEW_ITEMS = (
    ('service_workbuddy', '服务是 WorkBuddy；这里只模拟，不连接任何登录服务。'),
    ('fictional_account', '账号仅为 synthetic-test-account；不要输入真实账号、密码或验证码。'),
    ('browser_not_isolated', '未来原生登录可能先打开系统默认浏览器，并复用其现有登录状态；目录隔离不等于浏览器隔离。'),
    ('storage_not_encrypted', '未来专用目录不等于凭据加密；真实保存位置、保护方式及授权仍需另行确认。'),
    ('retention_cancel_not_rollback', '取消不保证登录回滚。状态不明时保留并隔离专用目录，不自动删除、登出或重试。'),
    ('manual_vendor_auth_only', '未来认证只能由本人在经核实的官方页面手动完成；此向导不接收密码、验证码、令牌或登录链接。'),
    ('simulation_and_no_model', '这些确认只适用于模拟，不授权真实登录或保存凭据，也不会启动模型或正式渠道。'),
)
_REVIEW_KEYS = tuple(item[0] for item in REVIEW_ITEMS)
_EVENTS = frozenset(('login_ack', 'login_error', 'cancel_delivered',
                     'account_present', 'account_absent',
                     'status_authenticated', 'status_unauthenticated'))
_TERMINAL = 'finished'
_INTENTS = frozenset(('synthetic.begin_login', 'synthetic.cancel_request',
                      'synthetic.account_read', 'synthetic.auth_status',
                      'synthetic.close_client', 'synthetic.close_harness'))
_MESSAGES = {
    'review': '逐项阅读模拟范围；不同意可随时取消，取消不会创建目录。',
    'profile_required': '模拟确认已完成；现在只可创建全新专用模拟目录，不能读取或复用现有账号目录。',
    'ready': '新模拟目录已确认。尚未开始认证；开始只会产生内存模拟事件。',
    'auth_pending': '模拟等待本人在官方页面手动认证。本次不会打开浏览器；不要粘贴任何凭据或链接。',
    'read_pending': '模拟登录请求已结算，尚不能认定账号存在；正在等待单独账号回读。',
    'status_pending': '模拟账号回读已收到，还需独立状态回读及本人身份确认。',
    'identity_review': '只确认固定虚构账号 synthetic-test-account。真实账号身份不在本次流程中处理。',
    'cancel_pending': '已请求模拟取消，但请求可能尚未结算；取消发送不是失败终态，也不是回滚。',
    'cancel_read_pending': '取消后的模拟请求已结算；仍需账号回读确认是否发生完成竞态。',
}
_OUTCOME_MESSAGES = {
    'incomplete': '模拟尚未完成。',
    'simulated_complete': '模拟完成，未发生真实登录。保留模拟目录；真实账号、模型和原生登录可达性均未验证。',
    'cancelled_before_start': '已在开始前取消；没有发起模拟登录，也不会自动创建、删除或复用目录。',
    'cancelled_no_account': '模拟取消后回读未认证；这不是对原生凭据回滚的保证。保留目录，不自动重试。',
    'cancelled_account_present': '模拟取消时账号已出现，可能已完成登录。保留目录，不自动登出、删除或继续执行模型。',
    'account_present_unconfirmed': '模拟错误响应后仍发现账号，不能按失败重试；保留目录等待人工核实。',
    'unauthenticated': '模拟回读未认证。保留目录；不会重复发起登录。',
    'identity_rejected': '未确认模拟身份；保留目录，不登出、删除或继续使用该账号。',
    'unknown_retained': '模拟状态未知；目录保留并隔离。不要重试、删除或假定已登出，需人工核实。',
    'profile_refused': '拒绝读取或复用已有、受保护的账号目录；原目录保持不动。',
    'invalid': '收到无效或乱序操作，流程已停止；如已创建目录则保留并隔离，不处理输入内容。',
}


class FlowError(ValueError):
    """Fixed diagnostic only; never contains caller input."""


class LoginFlow:
    """Single-attempt wizard. Simulation consent is never real authorization."""

    def __init__(self, mode='simulate'):
        if type(mode) is not str or mode != 'simulate':
            raise FlowError('NATIVE_LOGIN_NOT_AVAILABLE')
        self._phase = 'review'
        self._outcome = 'incomplete'
        self._acknowledgments = 0
        self._profile_created = False
        self._started = False
        self._cancel_requested = False
        self._cancel_delivered = False
        self._login_settled = False
        self._login_ack = False
        self._account_read = False
        self._account_present = False
        self._status_read = False
        self._status_authenticated = False
        self._identity_confirmed = False
        self._close_requested = False
        self._late_events = 0
        self._intents = []
        self._emitted = set()

    @property
    def phase(self):
        return self._phase

    def _active(self):
        if self._phase == _TERMINAL or self._close_requested:
            self._late_events = min(255, self._late_events + 1)
            return False
        return True

    def _finish(self, outcome):
        self._phase = _TERMINAL
        self._outcome = outcome

    def _invalid(self):
        self._finish('invalid')
        raise FlowError('INVALID_SIMULATION_ACTION')

    def _emit(self, intent):
        if intent not in _INTENTS or intent in self._emitted:
            self._invalid()
        self._emitted.add(intent)
        self._intents.append(intent)

    def acknowledge(self, key):
        if not self._active():
            return
        if (type(key) is not str or self._phase != 'review'
                or key != _REVIEW_KEYS[self._acknowledgments]):
            self._invalid()
        self._acknowledgments += 1
        if self._acknowledgments == len(REVIEW_ITEMS):
            self._phase = 'profile_required'

    def profile_result(self, result):
        if not self._active():
            return
        if type(result) is not str or self._phase != 'profile_required':
            self._invalid()
        if result in ('existing', 'protected'):
            self._finish('profile_refused')
        elif result == 'fresh':
            self._profile_created = True
            self._phase = 'ready'
        else:
            self._invalid()

    def start(self):
        if not self._active():
            return
        if self._phase != 'ready' or not self._profile_created:
            self._invalid()
        self._started = True
        self._phase = 'auth_pending'
        self._emit('synthetic.begin_login')

    def peer_event(self, event):
        if not self._active():
            return
        if type(event) is not str or event not in _EVENTS:
            self._invalid()
        phase = self._phase
        if event == 'cancel_delivered':
            if phase != 'cancel_pending' or self._cancel_delivered:
                self._invalid()
            self._cancel_delivered = True
            return  # Sending cancellation never settles the request.
        if event in ('login_ack', 'login_error'):
            if phase not in ('auth_pending', 'cancel_pending') or self._login_settled:
                self._invalid()
            self._login_settled = True
            self._login_ack = event == 'login_ack'
            self._phase = 'cancel_read_pending' if self._cancel_requested else 'read_pending'
            self._emit('synthetic.account_read')
            return
        if event in ('account_present', 'account_absent'):
            if phase not in ('read_pending', 'cancel_read_pending') or self._account_read:
                self._invalid()
            self._account_read = True
            self._account_present = event == 'account_present'
            if self._cancel_requested:
                self._finish('cancelled_account_present' if self._account_present else 'cancelled_no_account')
            elif not self._account_present:
                self._finish('unauthenticated')
            elif not self._login_ack:
                self._finish('account_present_unconfirmed')
            else:
                self._phase = 'status_pending'
                self._emit('synthetic.auth_status')
            return
        if phase != 'status_pending' or self._status_read:
            self._invalid()
        self._status_read = True
        self._status_authenticated = event == 'status_authenticated'
        if self._status_authenticated:
            self._phase = 'identity_review'
        else:
            self._finish('unknown_retained')

    def confirm_identity(self, choice):
        if not self._active():
            return
        if type(choice) is not str or self._phase != 'identity_review':
            self._invalid()
        if choice == SYNTHETIC_ACCOUNT:
            self._identity_confirmed = True
            self._finish('simulated_complete')
        elif choice == 'reject':
            self._finish('identity_rejected')
        else:
            self._invalid()

    def cancel(self):
        if not self._active():
            return
        if not self._started:
            self._finish('cancelled_before_start')
            return
        if self._cancel_requested:
            return  # Repeated cancel never dispatches another request.
        self._cancel_requested = True
        if self._phase == 'auth_pending':
            self._phase = 'cancel_pending'
            self._emit('synthetic.cancel_request')
        elif self._phase == 'read_pending':
            self._phase = 'cancel_read_pending'  # Already issued the sole read.
        elif self._phase in ('status_pending', 'identity_review'):
            self._finish('cancelled_account_present')
        else:
            self._invalid()

    def timeout(self):
        if self._active():
            self._finish('unknown_retained' if self._started else 'cancelled_before_start')

    def close(self):
        if self._close_requested:
            return
        if self._phase != _TERMINAL:
            if self._started:
                self.cancel()
                if self._phase != _TERMINAL:
                    self._finish('unknown_retained')
            else:
                self._finish('cancelled_before_start')
        self._close_requested = True
        if self._started:
            self._emit('synthetic.close_client')
            self._emit('synthetic.close_harness')

    def drain_intents(self):
        result = tuple(self._intents)
        self._intents.clear()
        return result

    def view(self):
        review = REVIEW_ITEMS[self._acknowledgments] if self._phase == 'review' else None
        return {
            'phase': self._phase,
            'message': (_OUTCOME_MESSAGES[self._outcome] if self._phase == _TERMINAL
                        else _MESSAGES[self._phase]),
            'review_key': review[0] if review else None,
            'review_text': review[1] if review else None,
            'simulation_only': True,
        }

    def summary(self):
        quarantine = self._profile_created and self._outcome in ('unknown_retained', 'invalid')
        return {
            'schema_version': 1, 'phase': self._phase, 'outcome': self._outcome,
            'acknowledgments': self._acknowledgments,
            'profile_created': self._profile_created,
            'profile_disposition': ('quarantine' if quarantine else 'retain') if self._profile_created else 'not_created',
            'started': self._started, 'cancel_requested': self._cancel_requested,
            'cancel_delivered': self._cancel_delivered, 'login_settled': self._login_settled,
            'login_acknowledged': self._login_ack, 'account_read': self._account_read,
            'simulated_account_present': self._account_present,
            'status_read': self._status_read, 'simulated_status_authenticated': self._status_authenticated,
            'manual_identity_confirmed': self._identity_confirmed,
            'close_requested': self._close_requested, 'late_events': self._late_events,
            'intents_emitted': len(self._emitted), 'all_account_data_synthetic': True,
            'simulation_consent_only': True, 'native_backend_available': False,
            'real_account_verified': False, 'real_model_ready': False,
            'cancellation_proves_rollback': False, 'native_cleanup_verified': False,
            'automatic_retry_allowed': False, 'automatic_logout_allowed': False,
            'automatic_profile_delete_allowed': False,
        }

    def canonical_summary_bytes(self):
        """Serialize only validated bounded fields, never caller data."""
        payload = json.dumps(validate_summary(self.summary()), sort_keys=True,
                             separators=(',', ':'), ensure_ascii=True).encode('ascii') + b'\n'
        if len(payload) > 4096:
            raise FlowError('INVALID_SUMMARY')
        return payload


_BOOLEAN_KEYS = (
    'profile_created', 'started', 'cancel_requested', 'cancel_delivered', 'login_settled',
    'login_acknowledged', 'account_read', 'simulated_account_present', 'status_read',
    'simulated_status_authenticated', 'manual_identity_confirmed', 'close_requested',
    'all_account_data_synthetic', 'simulation_consent_only', 'native_backend_available',
    'real_account_verified', 'real_model_ready', 'cancellation_proves_rollback',
    'native_cleanup_verified', 'automatic_retry_allowed', 'automatic_logout_allowed',
    'automatic_profile_delete_allowed',
)
_TRUE_KEYS = ('all_account_data_synthetic', 'simulation_consent_only')
_FALSE_KEYS = ('native_backend_available', 'real_account_verified', 'real_model_ready',
               'cancellation_proves_rollback', 'native_cleanup_verified',
               'automatic_retry_allowed', 'automatic_logout_allowed',
               'automatic_profile_delete_allowed')
_NUMBER_BOUNDS = {'schema_version': 1, 'acknowledgments': len(REVIEW_ITEMS),
                  'late_events': 255, 'intents_emitted': len(_INTENTS)}
_STRING_KEYS = ('phase', 'outcome', 'profile_disposition')
SUMMARY_KEYS = frozenset(_BOOLEAN_KEYS) | frozenset(_NUMBER_BOUNDS) | frozenset(_STRING_KEYS)


def validate_summary(value):
    """Exact bounded DTO, suitable for a separate profile writer. No IO."""
    def require(condition):
        if not condition:
            raise FlowError('INVALID_SUMMARY')
    require(type(value) is dict)
    require(len(value) == len(SUMMARY_KEYS))
    require(all(type(key) is str for key in value))
    require(set(value) == SUMMARY_KEYS)
    require(all(type(value[key]) is bool for key in _BOOLEAN_KEYS))
    require(all(type(value[key]) is int and 0 <= value[key] <= bound
                for key, bound in _NUMBER_BOUNDS.items()))
    require(all(type(value[key]) is str and len(value[key]) <= 64 for key in _STRING_KEYS))
    require(value['schema_version'] == 1)
    require(value['phase'] in (*_MESSAGES, _TERMINAL))
    require(value['outcome'] in _OUTCOME_MESSAGES)
    require(all(value[key] is True for key in _TRUE_KEYS))
    require(all(value[key] is False for key in _FALSE_KEYS))
    require(not value['profile_created'] or value['acknowledgments'] == len(REVIEW_ITEMS))
    for later, earlier in (
        ('started', 'profile_created'), ('cancel_requested', 'started'),
        ('cancel_delivered', 'cancel_requested'), ('login_settled', 'started'),
        ('login_acknowledged', 'login_settled'), ('account_read', 'login_settled'),
        ('simulated_account_present', 'account_read'), ('status_read', 'simulated_account_present'),
        ('status_read', 'login_acknowledged'), ('simulated_status_authenticated', 'status_read'),
        ('manual_identity_confirmed', 'simulated_status_authenticated'),
    ):
        require(not value[later] or value[earlier])
    require(not value['close_requested'] or value['phase'] == _TERMINAL)
    require((value['outcome'] == 'incomplete') == (value['phase'] != _TERMINAL))
    require((value['outcome'] == 'simulated_complete') == value['manual_identity_confirmed'])
    quarantine = value['profile_created'] and value['outcome'] in ('unknown_retained', 'invalid')
    expected = ('quarantine' if quarantine else 'retain') if value['profile_created'] else 'not_created'
    require(value['profile_disposition'] == expected)
    if value['outcome'] == 'profile_refused':
        require(not value['profile_created'] and not value['started'])
    if value['outcome'] in ('cancelled_account_present', 'cancelled_no_account'):
        require(value['cancel_requested'])
        require(value['simulated_account_present'] == (value['outcome'] == 'cancelled_account_present'))
    return {key: value[key] for key in sorted(SUMMARY_KEYS)}
