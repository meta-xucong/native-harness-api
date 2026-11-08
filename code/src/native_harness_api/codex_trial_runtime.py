"""One frozen Codex execution target; user-managed local authentication only."""
from pathlib import Path
import sys
import uuid
from .native_trial_runtime import _QUARANTINED_PROFILES, _QUARANTINED_ENGINES
from .codex_owned_session import CodexOwnedSession
from .supervised_acp_transport import SupervisedACPTransport


def invoke(target, *, execution_timeout, cleanup_timeout, model_id, messages,
           on_event=None, cancellation=None):
    recipe = target.recipe
    if model_id != recipe['native_model'] or len(messages) != 1 or messages[0]['role'] != 'user':
        raise ValueError('One bound user prompt required')
    tools = Path(__file__).resolve().parents[2] / 'tools'
    if str(tools) not in sys.path:
        sys.path.insert(0, str(tools))
    from codex_profile import open_profile, environment
    from codex_assets import verify_assets
    profile = open_profile(Path(recipe['profile_path']))
    begun = reusable = clean = False
    engine = None
    try:
        if (profile.state != 'authenticated_confirmed' or
                profile.root.name != recipe['profile_id'] or
                profile.expected_account_digest != recipe['account_fingerprint']):
            raise ValueError('Frozen Codex profile confirmation mismatch')
        executable = verify_assets(target.assets_root)
        workspace = profile.root / 'workspace' / ('request-' + uuid.uuid4().hex)
        workspace.mkdir()
        profile.verify()
        begun = True
        try:
            profile.begin_execution()
            engine = CodexOwnedSession(SupervisedACPTransport())
            result = engine.run(
                [str(executable), '--listen', 'stdio://'], workspace, environment(profile),
                workspace=str(workspace), model_id=model_id, prompt=messages[0]['content'],
                expected_account_digest=profile.expected_account_digest,
                timeout=execution_timeout, cleanup_timeout=cleanup_timeout,
                on_event=on_event, cancellation=cancellation)
            clean = result.get('cleanup_confirmed') is True
            return result
        finally:
            if clean:
                profile.finish_execution(cleanup_confirmed=True)
                reusable = True
            else:
                profile.quarantine()
    finally:
        if begun and not reusable:
            _QUARANTINED_PROFILES.append(profile)
            if engine is not None:
                _QUARANTINED_ENGINES.append(engine)
        else:
            profile.close()
