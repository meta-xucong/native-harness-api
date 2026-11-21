"""Codex protocol on the existing owned stdio transport and cleanup machinery."""
from .codex_session import CodexSession
from .public_sfe_owned_session import PublicSfeOwnedSession


class CodexOwnedSession(PublicSfeOwnedSession):
    def __init__(self, transport, *, protocol_factory=CodexSession, stages_factory=None):
        super().__init__(transport)
        self._protocol_factory = protocol_factory
        self._stages_factory = stages_factory
        self.forced_cleanup = self.forced_before_terminal = False

    def _make_protocol(self, **kwargs):
        return self._protocol_factory(**kwargs)

    def _stages(self, workspace, model_id, prompt):
        if self._stages_factory is not None:
            return self._stages_factory(self.protocol, workspace, model_id, prompt)
        return [
            (self.protocol.start, lambda: self.protocol.ready),
            (lambda: self.protocol.new_session(workspace, model_id),
             lambda: self.protocol.phase == 'thread_ready'),
            (lambda: self.protocol.prompt(prompt), lambda: self.protocol.terminal_observed),
        ]

    def _begin_close(self, deadline):
        if self.protocol.turn_id is not None and not self.protocol.terminal_observed:
            if self._send(self.protocol.cancel(), deadline):
                self._pump_until(lambda: self.protocol.terminal_observed, deadline, honor_turn=False)

    def _before_cleanup(self, natural):
        self.forced_cleanup = not natural
        self.forced_before_terminal = not natural and not self.protocol.terminal_observed

    def _cleanup_confirmed(self, cleanup, io_stopped, zero):
        return io_stopped and all(cleanup.get(key) is True for key in (
            'host_exited', 'owned_job_empty', 'handles_closed', 'cleanup_complete'))

    def _close_confirmed(self):
        return True  # Codex has no SFE session/close acknowledgement.

    def run(self, *args, **kwargs):
        result = super().run(*args, **kwargs)
        if result['session'].get('fault') is not None:
            result['outcome'] = 'indeterminate'
            if result['fault'] is None:
                result['fault'] = result['session']['fault']
        result.update(forced_cleanup=self.forced_cleanup,
                      forced_before_terminal=self.forced_before_terminal,
                      graceful_exit=result['natural_owned_exit'])
        return result
