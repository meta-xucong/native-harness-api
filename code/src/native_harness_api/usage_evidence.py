"""Bounded, non-billing beta16 model-request evidence, never usage estimation.

Source: official 0.2.0-beta.16 docs/acp-v2.md usage contract (SHA256
97c612e2682ab1ed235ec3797f8bbab7ccd8f9f918a6204e34b8ca75d1fec9c2).
Scalar names below are an explicit conservative projection allowlist, NOT a
promise every provider supplies them or that similarly named counters add up.
No complete-request manifest exists here: aggregate coverage always unproven.
"""
import hashlib
import json
import math

TOKEN_FIELDS = frozenset({
    'input_tokens', 'output_tokens', 'prompt_tokens', 'completion_tokens', 'total_tokens',
    'cache_read_input_tokens', 'cache_creation_input_tokens',
    'inputTokens', 'outputTokens', 'totalTokens', 'cacheReadTokens', 'cacheCreationTokens',
})
CREDIT_FIELDS = frozenset({'credit', 'credits'})


def _identity(value):
    return type(value) is str and 0 < len(value) <= 512


def _number(value, *, integer=False):
    if type(value) not in (int, float) or (integer and type(value) is not int):
        return False
    return 0 <= value <= (2**53 - 1 if integer else 10**12) and math.isfinite(value)


class UsageEvidence:
    """Capture after bounded JSON/frame validation; expose only ACK-bound scope.

    Does not throw observer failures into native execution. Records and reasons
    are bounded. Conflicting revisions are flagged, never silently last-wins.
    Cost/context/session-total frames are deliberately not added to turn totals.
    """
    def __init__(self, *, max_records=512, max_events=4096):
        if (type(max_records) is not int or not 1 <= max_records <= 512
                or type(max_events) is not int or not 1 <= max_events <= 4096):
            raise ValueError('USAGE_EVIDENCE_LIMIT_INVALID')
        self.max_records, self.max_events = max_records, max_events
        self._records = {}
        self._events = 0
        self._issues = set()
        self._scope_only = 0

    def observe(self, envelope):
        self._events += 1
        if self._events > self.max_events:
            self._issues.add('event_limit')
            return
        try:
            if type(envelope) is not dict or type(envelope.get('update')) is not dict:
                self._issues.add('invalid_envelope')
                return
            update = envelope['update']
            meta = update.get('_meta')
            if type(meta) is not dict:
                if update.get('sessionUpdate') == 'usage_update':
                    self._scope_only += 1
                return
            if 'usage' not in meta:
                if update.get('sessionUpdate') == 'usage_update':
                    self._scope_only += 1
                return
            raw = meta['usage']
            if type(raw) is not dict or len(raw) > 64:
                self._issues.add('invalid_usage')
                return
            root_session = envelope.get('sessionId')
            root_run = meta.get('codebuddy.ai/rootRequestId')
            run = meta.get('codebuddy.ai/requestId')
            message = meta.get('codebuddy.ai/messageId')
            child = meta.get('codebuddy.ai/subagent')
            session = root_session
            if child is not None:
                if type(child) is not dict:
                    self._issues.add('unattributed_usage')
                    return
                session = child.get('sessionId')
            if not all(_identity(v) for v in (root_session, root_run, session, run, message)):
                self._issues.add('unattributed_usage')
                return
            if (child is None and root_run != run) or (child is not None and session == root_session):
                self._issues.add('identity_conflict')
                return
            key = (root_session, root_run, session, run, message)
            fields = {}
            issues = set()
            for name, value in raw.items():
                if name in TOKEN_FIELDS:
                    if _number(value, integer=True):
                        fields[name] = value
                    else:
                        issues.add('invalid_counter')
                elif name in CREDIT_FIELDS:
                    if _number(value):
                        fields[name] = value
                    else:
                        issues.add('invalid_counter')
                else:
                    # Do not export arbitrary metadata (including numeric IDs),
                    # nested provider objects, prompt text or credentials.
                    issues.add('unsupported_native_fields')
            if not fields:
                issues.add('no_supported_counters')
            previous = self._records.get(key)
            if previous is None:
                if len(self._records) >= self.max_records:
                    self._issues.add('record_limit')
                    return
                self._records[key] = {'fields': fields, 'issues': issues, 'duplicates': 0}
            else:
                previous['issues'].update(issues)
                if previous['fields'] == fields:
                    previous['duplicates'] += 1
                else:
                    # Additional non-conflicting counters enrich the same model
                    # request. Changed/removed values have no official sequence
                    # version here; flag a conflict rather than choose a price.
                    if any(k in previous['fields'] and previous['fields'][k] != v for k, v in fields.items()) or not set(previous['fields']) <= set(fields):
                        previous['issues'].add('conflicting_revision')
                    else:
                        previous['fields'].update(fields)
        except (ValueError, TypeError, OverflowError, UnicodeError, RecursionError):
            self._issues.add('invalid_usage')

    def summary(self, root_session, root_run, *, accepted=False, terminal_observed=False):
        result = {'schema_version': 'native-metering-evidence-v1', 'state': 'unknown',
                  'coverage': 'unproven', 'billing_eligible': False, 'total_usage': None,
                  'records': [], 'issues': sorted(self._issues),
                  'terminal_observed': terminal_observed is True,
                  'context_or_session_frames_ignored': self._scope_only}
        if accepted is not True or not _identity(root_session) or not _identity(root_run):
            result['issues'] = sorted(set(result['issues']) | {'accepted_identity_unavailable'})
            return result
        for key, record in sorted(self._records.items()):
            if key[:2] != (root_session, root_run):
                continue
            opaque = hashlib.sha256(json.dumps(key, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
            result['records'].append({'request_evidence_id': opaque,
                                      'scope': 'root' if key[2] == root_session else 'subagent',
                                      'native_fields': dict(record['fields']),
                                      'issues': sorted(record['issues']),
                                      'duplicate_observations': record['duplicates']})
        if result['records']:
            result['state'] = 'partial'
        # Idle/terminal does not supply an exhaustive model-request manifest or
        # prove late descendants and transport gaps absent. Never assert complete.
        result['issues'] = sorted(set(result['issues']) | {'coverage_not_proven'})
        return result
