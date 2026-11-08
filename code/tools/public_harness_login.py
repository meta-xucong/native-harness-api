"""User-run Windows TTY login. No automatic authentication on import or --explain.

Native login/end-to-end: NOT_RUN by development fixtures. No model/session path.
"""
from __future__ import annotations
import argparse
import json
import math
import os
from pathlib import Path
import queue
import sys
import threading
import time

# Script invocation and installed-package invocation share the same authored code.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from native_harness_api.windows_job import OwnedWindowsJob
from public_harness_login_protocol import LoginProtocol

STREAM_LIMIT = 1024 * 1024
FRAME_LIMIT = 65536
STDIN_LIMIT = 16384
RETAINED = []
EXPLANATION = (
    'This is a single user-run CN WorkBuddy login to the official CN service.\n'
    'The native default browser may open before a prompt and reuse this OS account browser cookies.\n'
    'A new restricted-ACL profile is not native credential encryption or an OS/network sandbox.\n'
    'Native credentials, backups and logs may persist. The profile is retained, not deleted.\n'
    'Cancel is not rollback: a login may already have committed. Unknown outcomes are not retried.\n'
    'No model, session, provision or logout operation is sent. Complete passwords/MFA only on the official website.\n'
    'Existing WorkBuddy credentials are not intentionally imported. Use a dedicated OS/browser profile if isolation is required.\n'
)


def require(value):
    if not value:
        raise ValueError('LOGIN_REJECTED')


def safe_label(value):
    """Escape controls, bidi and non-ASCII before a private terminal identity display."""
    if type(value) is not str:
        return '(not supplied)'
    value = value[:256]
    return ''.join(c if 32 <= ord(c) < 127 else f'\\u{ord(c):04x}' for c in value)


class WindowsTTY:
    def __init__(self):
        require(os.name == 'nt' and sys.stdin.isatty() and sys.stdout.isatty() and sys.stderr.isatty())
        import msvcrt
        self.console = msvcrt
        self.pending = False
        self.buffer = ''

    def begin(self, action, event=None):
        require(not self.pending)
        # Discard typeahead before displaying each distinct consent. Never echo it.
        for _ in range(256):
            if not self.console.kbhit():
                break
            self.console.getwch()
        require(not self.console.kbhit())
        self.buffer = ''; self.pending = True
        if action == 'initial_consent':
            print(EXPLANATION)
            print('Create a NEW retained profile and start the verified native client? Type yes or no:')
        elif action == 'confirm_login_intent':
            print('The new profile is unauthenticated. Start ONE native CN WorkBuddy sign-in now? yes/no:')
        elif action == 'confirm_url_handoff':
            # Never print a URL/state or open another browser from this runner.
            print('Native sign-in requested a browser handoff. Verify the official website and intended account in your browser. No link is printed or opened here. Continue waiting for this sign-in? yes/no:')
        elif action == 'confirm_account_identity':
            account = event['account']
            print('Native account reported. Confirm this is the account you intended:')
            print('Account ID: ' + safe_label(account.get('id')))
            print('Display name: ' + safe_label(account.get('displayName')))
            org = account.get('organization') or {}
            print('Organization ID: ' + safe_label(org.get('id')))
            print('Confirm this account for this retained profile? yes/no:')
        else:
            raise ValueError('UI_ACTION_REJECTED')

    def abandon(self):
        self.pending = False; self.buffer = ''

    def poll(self):
        """Never readline/getwch unless kbhit; input is only a bounded yes/no token."""
        if not self.pending:
            return None
        for _ in range(16):
            if not self.console.kbhit():
                break
            char = self.console.getwch()
            if char in ('\x00', '\xe0'):
                self.buffer = '!'; continue
            if char == '\x03':
                self.abandon(); return False
            if char in ('\r', '\n'):
                answer = self.buffer.lower(); self.abandon()
                if answer in ('yes', 'no'):
                    return answer == 'yes'
                return False
            if char == '\b':
                self.buffer = self.buffer[:-1]
            elif len(self.buffer) < 4 and char.lower() in 'yesno':
                self.buffer += char
            else:
                self.buffer = '!'
        return None


def supervise(assets, profile, ui, *, timeout=600, cancel_grace=10, cleanup_timeout=10,
              job_class=OwnedWindowsJob):
    """Injected collaborators are test-only Python APIs, never CLI bypass options."""
    for value, maximum in ((timeout, 600), (cancel_grace, 30), (cleanup_timeout, 15)):
        require(type(value) in (int, float) and math.isfinite(value) and 0 < value <= maximum)
    protocol = LoginProtocol()
    result = dict(schema_version=1, success=False, failure_code='none', stdin_bytes=0,
                  stdout_bytes=0, stderr_bytes=0, stdin_closed=False, natural_exit_zero=False,
                  natural_job_empty=False, host_exited=False, owned_job_empty=False,
                  handles_closed=False, io_threads_stopped=False, cleanup_complete=False,
                  profile_verified=False, profile_recorded=False, profile_closed=False,
                  quarantine_attempted=False, quarantine_verified=False,
                  native_account_verified=False, real_model_ready=False)
    incoming, outgoing = queue.Queue(64), queue.Queue(16)
    fault, stop = threading.Event(), threading.Event()
    totals = dict(stdout=0, stderr=0, stdin=0)
    writer_state = {'closed': False}
    threads = []; owner = None; uncertain = False; closing = False
    current_action = None; identity_digest = None; private_event = None
    cancel_deadline = None; eof = set(); late_frame = False

    def enqueue(kind, value=None):
        try: incoming.put_nowait((kind, value))
        except queue.Full: fault.set()

    def reader(label, stream):
        pending = bytearray(); frames = 0
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    if pending: fault.set()
                    enqueue(label + '_eof'); return
                totals[label] += len(chunk)
                if totals[label] > STREAM_LIMIT:
                    fault.set(); return
                if label == 'stderr': continue
                pending.extend(chunk)
                while b'\n' in pending:
                    line, _, rest = pending.partition(b'\n'); pending = bytearray(rest); frames += 1
                    if not line or len(line) > FRAME_LIMIT or frames > 64:
                        fault.set(); return
                    enqueue('frame', bytes(line))
                if len(pending) > FRAME_LIMIT:
                    fault.set(); return
        except BaseException: fault.set()

    def writer(stream):
        try:
            while not stop.is_set():
                try: frame = outgoing.get(timeout=.02)
                except queue.Empty: continue
                if frame is None: break
                require(type(frame) is bytes and 0 < len(frame) <= 2048)
                require(totals['stdin'] + len(frame) <= STDIN_LIMIT)
                view = memoryview(frame)
                while view:
                    n = stream.write(view); require(type(n) is int and 0 < n <= len(view))
                    totals['stdin'] += n; view = view[n:]
                stream.flush()
        except BaseException: fault.set()
        finally:
            try: stream.close(); writer_state['closed'] = True
            except BaseException: fault.set()

    def effects(effect):
        nonlocal private_event, identity_digest
        wires, events = effect
        for wire in wires:
            require(type(wire) is bytes and 0 < len(wire) <= 2048)
            outgoing.put_nowait(wire)
        for event in events:
            require(event['kind'] in ('url_handoff', 'account_identity'))
            private_event = event
            if event['kind'] == 'account_identity':
                identity_digest = event['digest']

    def close_input():
        nonlocal closing
        if not closing:
            outgoing.put_nowait(None); closing = True

    try:
        require(profile.state == 'fresh' and profile.verify() is True)
        env = profile.environment(assets['host_node'])
        command = [str(assets['executable']), '--stdio', '--strict-mcp-config',
                   '--product-config', str(assets['product_config'])]
        # Durable intent precedes even the first native process attempt.
        profile.record('login_started')
        deadline = time.monotonic() + timeout
        owner = job_class.spawn(command, profile.root / 'workspace', env)
        for target, args in ((writer,(owner.stdin,)), (reader,('stdout',owner.stdout)), (reader,('stderr',owner.stderr))):
            thread = threading.Thread(target=target, args=args, daemon=True)
            threads.append(thread); uncertain = True; thread.start(); uncertain = False
        effects(protocol.start())
        while True:
            now = time.monotonic()
            if fault.is_set():
                protocol.closed(); result['failure_code'] = 'transport'; break
            if now >= deadline and cancel_deadline is None:
                result['failure_code'] = 'timeout'
                if protocol.login_sent and not protocol.done:
                    effects(protocol.cancel(timeout=True)); cancel_deadline = now + cancel_grace
                    ui.abandon(); current_action = None; private_event = None
                else:
                    protocol.closed(); close_input(); cancel_deadline = now + cancel_grace
            if cancel_deadline is not None and now >= cancel_deadline:
                protocol.closed(); break
            try: kind, value = incoming.get(timeout=.01)
            except queue.Empty: kind, value = None, None
            if kind == 'frame':
                if closing or protocol.done:
                    protocol.closed(); late_frame = True; result['failure_code'] = 'late_frame'; break
                effects(protocol.receive(value))
            elif kind in ('stdout_eof','stderr_eof'):
                eof.add(kind)
                if kind == 'stdout_eof' and not protocol.done:
                    protocol.closed(); close_input()
            action = protocol.next_action
            if action != current_action:
                ui.abandon(); current_action = None
            if action.startswith('confirm_') and cancel_deadline is None:
                if current_action is None:
                    if action in ('confirm_url_handoff','confirm_account_identity'):
                        require(private_event is not None)
                    ui.begin(action, private_event)
                    private_event = None; current_action = action
                answer = ui.poll()
                if answer is not None:
                    require(type(answer) is bool)
                    current_action = None
                    if action == 'confirm_login_intent': effects(protocol.intent(answer))
                    elif action == 'confirm_url_handoff': effects(protocol.elicitation_reply(answer))
                    else: effects(protocol.confirm_identity(answer))
                    if protocol.cancelled and cancel_deadline is None:
                        cancel_deadline = time.monotonic() + cancel_grace
            if protocol.cancelled and cancel_deadline is None:
                cancel_deadline = time.monotonic() + cancel_grace
            if protocol.done:
                ui.abandon(); private_event = None; close_input()
                if cancel_deadline is None: cancel_deadline = time.monotonic() + cancel_grace
            if owner.host_exited():
                result['natural_exit_zero'] = owner.exit_code() == 0
                if len(eof) == 2 and all(not t.is_alive() for t in threads):
                    result['natural_job_empty'] = owner.active_processes() == 0
                    break
    except KeyboardInterrupt:
        result['failure_code'] = 'interrupted'
        try:
            if protocol.login_sent and not protocol.done:
                effects(protocol.cancel())
                grace = time.monotonic() + cancel_grace
                while time.monotonic() < grace and not protocol.done and not fault.is_set():
                    try: kind, value = incoming.get(timeout=.01)
                    except queue.Empty: continue
                    if kind == 'frame': effects(protocol.receive(value))
                    elif kind == 'stdout_eof': break
            if not protocol.done: protocol.closed()
            close_input()
        except BaseException:
            protocol.closed()
    except BaseException as error:
        protocol.closed(); result['failure_code'] = 'transport'
        if owner is None: owner = getattr(error, 'owner', None)
    finally:
        try: ui.abandon()
        except BaseException: pass
        private_event = None
        stop.set(); end = time.monotonic() + cleanup_timeout
        if owner is not None:
            try:
                result['owned_job_empty'] = owner.terminate(max(0,end-time.monotonic())).get('owned_job_empty') is True
                result['host_exited'] = owner.host_exited() is True
            except BaseException: pass
        for thread in threads:
            try:
                if thread.ident is not None: thread.join(max(0,end-time.monotonic()))
            except BaseException: pass
        result['io_threads_stopped'] = not uncertain and all(not t.is_alive() for t in threads)
        if owner is not None and result['io_threads_stopped']:
            try:
                closed = owner.close(max(0,end-time.monotonic()))
                result['owned_job_empty'] = closed.get('owned_job_empty') is True
                result['handles_closed'] = True
            except BaseException: pass
        result['cleanup_complete'] = all(result[k] for k in ('host_exited','owned_job_empty','handles_closed','io_threads_stopped'))
        result['stdin_closed'] = writer_state['closed']
        if owner is None and not threads:
            result['cleanup_complete'] = True  # No process was created or returned by spawn.
        safe = result['cleanup_complete']
        try:
            if safe:
                result['profile_verified'] = profile.verify() is True
                require(result['profile_verified'])
                verified = (protocol.state == 'verified' and identity_digest is not None and not late_frame
                            and not fault.is_set() and result['failure_code'] == 'none'
                            and result['natural_exit_zero'] and result['natural_job_empty'] and result['stdin_closed'])
                terminal = ('authenticated_confirmed' if verified else
                            'cancelled_unauthenticated' if protocol.state == 'cancelled' else
                            'unauthenticated' if protocol.state == 'not_authenticated' else
                            'identity_rejected' if protocol.state == 'identity_rejected' else 'unknown_retained')
                if profile.state == 'login_started':
                    profile.record(terminal, identity_digest if verified else None)
                    result['profile_recorded'] = True
                    require(profile.verify() is True)
                result['success'] = result['native_account_verified'] = verified and result['profile_recorded']
                profile.close(); result['profile_closed'] = True
        except BaseException:
            result['success'] = result['native_account_verified'] = False
            result['failure_code'] = 'profile'
            # record() may have replaced the manifest before its verification
            # failed. Revoke any digest best-effort; never release on this path.
            result['quarantine_attempted'] = True
            try:
                profile.quarantine()
                result['quarantine_verified'] = (profile.verify() is True
                    and profile.state in ('unknown_retained', 'execution_unknown')
                    and profile.expected_account_digest is None)
            except BaseException:
                pass
        if not result['profile_closed']:
            RETAINED.append((owner, profile, threads))
            result['failure_code'] = 'cleanup_uncertain' if not safe else 'profile'
        for label in totals: result[label+'_bytes'] = min(totals[label],STREAM_LIMIT+4096)
        result['protocol'] = protocol.summary()
        # Protocol confirmation is not owned-process/ACL evidence.
        if not result['success']: result['protocol']['native_account_verified'] = False
    return result


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default error embeds unknown argv, potentially credentials.
        raise ValueError('ARGUMENTS_REJECTED')


def _hold_retained():
    """Preserve uncertain process ownership and profile locks, even if UI fails."""
    while RETAINED:
        try:
            time.sleep(1)
        except KeyboardInterrupt:
            continue


def main(argv=None):
    parser = SafeParser(description=__doc__)
    parser.add_argument('--explain', action='store_true')
    parser.add_argument('--assets-root', type=Path)
    parser.add_argument('--profile-parent', type=Path)
    try:
        args = parser.parse_args(argv)
        if args.explain:
            print(EXPLANATION); return 0
        require(args.assets_root is not None and args.profile_parent is not None)
        ui = WindowsTTY()
        from public_harness_runtime_assets import verify_runtime_assets
        from public_harness_login_profile import create_profile
        assets = verify_runtime_assets(args.assets_root)
        ui.begin('initial_consent'); end = time.monotonic()+60; answer = None
        while answer is None and time.monotonic() < end:
            answer = ui.poll(); time.sleep(.02)
        ui.abandon()
        if answer is not True:
            print('Not started.'); return 1
        # Recheck pinned assets after the user's bounded consent interval.
        assets = verify_runtime_assets(args.assets_root)
        profile = create_profile(args.profile_parent)
        print('New retained profile: ' + ascii(str(profile.root)))
        result = supervise(assets, profile, ui)
        print('Retained profile for later --profile: ' + ascii(str(profile.root)))
        print('Login confirmed and retained.' if result['success'] else 'Login not confirmed. Profile retained; no automatic retry, logout or deletion.')
        if not result['profile_closed']:
            print('Cleanup is uncertain. Keep this owner process open and resolve owned-process cleanup before reusing the profile.')
        return 0 if result['success'] else 1
    except (Exception, KeyboardInterrupt):
        try:
            print('Stopped safely; no automatic retry. Check the retained profile before another attempt.')
        except (Exception, KeyboardInterrupt):
            pass  # Broken output must not release an uncertain process owner.
        return 1
    finally:
        _hold_retained()


if __name__ == '__main__':
    raise SystemExit(main())
