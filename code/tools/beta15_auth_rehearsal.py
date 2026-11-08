"""Credential-free, vendor-free authentication boundary rehearsal.

This module has no transport, filesystem, process, browser or credential adapter.
It emits *synthetic intents*, never executable commands. It is not a native
provider implementation, a login tool, or a generic parser for real account data.
"""
from __future__ import annotations

from enum import Enum
import json


class Phase(Enum):
    NEW = "new"
    AVAILABLE = "available"
    PENDING = "pending"
    AWAITING_READ = "awaiting_read"
    SIMULATED_AUTHENTICATED = "simulated_authenticated"
    STOPPED = "stopped"


class Outcome(Enum):
    INCOMPLETE = "incomplete"
    SIMULATED_AUTHENTICATED = "simulated_authenticated"
    UNAUTHENTICATED = "unauthenticated"
    CANCELLED_UNVERIFIED = "cancelled_unverified"
    TIMED_OUT = "timed_out"
    DENIED = "denied"
    INVALID = "invalid"
    PROTECTED_RESTORE_BLOCKED = "protected_restore_blocked"


METHOD_ID = "workbuddy-desktop"
PROVIDER_TYPE = "account-scoped-external-link"
SYNTHETIC_ACCOUNT_ID = "synthetic-auth-rehearsal-only"


def synthetic_catalog():
    return [{"methodId": METHOD_ID, "providerType": PROVIDER_TYPE,
             "name": "TencentCloud", "type": "agent", "description": None}]


def synthetic_snapshot(authenticated=False):
    return ({"state": "authenticated", "mode": "managed",
             "account": {"id": SYNTHETIC_ACCOUNT_ID}}
            if authenticated else
            {"state": "unauthenticated", "mode": None, "account": None})


class AuthRehearsal:
    """Single-attempt deterministic fixture. Never connect this to a real peer.

    The only accepted authenticated identity is a fixed fictional fixture.
    Native readiness and rollback guarantees are deliberately always false.
    """

    def __init__(self):
        self.phase = Phase.NEW
        self.outcome = Outcome.INCOMPLETE
        self.closed = False
        self.catalog_matched = False
        self.login_acknowledged = False
        self.intents = []
        self.cancel_replies = 0
        self.late_events = 0

    def _active(self):
        if self.closed or self.phase is Phase.STOPPED:
            self.late_events = min(255, self.late_events + 1)
            return False
        return True

    def _stop(self, outcome):
        self.phase = Phase.STOPPED
        self.outcome = outcome

    def advertise(self, catalog):
        if not self._active():
            return
        if self.phase is not Phase.NEW or type(catalog) is not list or len(catalog) > 16:
            self._stop(Outcome.INVALID)
            return
        matches = 0
        for item in catalog:
            if (type(item) is not dict or not all(type(key) is str for key in item)
                    or not set(item) <= {
                "methodId", "providerType", "name", "type", "description"
            } or type(item.get("methodId")) is not str):
                self._stop(Outcome.INVALID)
                return
            # This is an exact synthetic contract fixture, not a complete SDK
            # schema validator. Unknown methods are not selected or retained.
            if item.get("methodId") == METHOD_ID:
                if (type(item.get("providerType")) is not str or
                        type(item.get("type")) is not str or
                        item.get("providerType") != PROVIDER_TYPE or
                        item.get("type") != "agent"):
                    self._stop(Outcome.INVALID)
                    return
                matches += 1
        if matches != 1:
            self._stop(Outcome.INVALID)
            return
        self.catalog_matched = True
        self.phase = Phase.AVAILABLE

    def begin_login(self):
        if not self._active():
            return
        if self.phase is not Phase.AVAILABLE:
            self._stop(Outcome.INVALID)
            return
        self.intents.append("synthetic_login_intent")
        self.phase = Phase.PENDING

    def acknowledge_login(self):
        if not self._active():
            return
        if self.phase is not Phase.PENDING:
            self._stop(Outcome.INVALID)
            return
        self.login_acknowledged = True
        self.phase = Phase.AWAITING_READ
        self.intents.append("synthetic_account_read_intent")

    def account_snapshot(self, value):
        if not self._active():
            return
        if self.phase is not Phase.AWAITING_READ or type(value) is not dict:
            self._stop(Outcome.INVALID)
            return
        if (not all(type(key) is str for key in value) or
                set(value) != {"state", "mode", "account"} or
                type(value["state"]) is not str or
                value["mode"] is not None and type(value["mode"]) is not str):
            self._stop(Outcome.INVALID)
            return
        account = value["account"]
        if (value["state"] == "unauthenticated" and value["mode"] is None
                and account is None):
            self._stop(Outcome.UNAUTHENTICATED)
            return
        if (value["state"] != "authenticated" or value["mode"] != "managed"
                or type(account) is not dict or
                not all(type(key) is str for key in account) or set(account) != {"id"}
                or type(account["id"]) is not str
                or account["id"] != SYNTHETIC_ACCOUNT_ID):
            self._stop(Outcome.INVALID)
            return
        self.phase = Phase.SIMULATED_AUTHENTICATED
        self.outcome = Outcome.SIMULATED_AUTHENTICATED

    def cancel_elicitation(self, ignored_payload=None):
        # Never read/render/open/retain the URL/form payload, even if it is a
        # hostile object. A native cancel can race persistence: NOT rollback.
        self.cancel_replies = min(255, self.cancel_replies + 1)
        if self._active():
            self._stop(Outcome.CANCELLED_UNVERIFIED)
        return {"action": "cancel"}

    def timeout(self):
        if self._active():
            self._stop(Outcome.TIMED_OUT)

    def deny(self):
        if self._active():
            self._stop(Outcome.DENIED)

    def request_protected_restore(self, ignored_payload=None):
        # No decoder, key bootstrap, migration, file access or downgrade exists.
        if self._active():
            self._stop(Outcome.PROTECTED_RESTORE_BLOCKED)

    def close(self):
        if not self.closed:
            self.intents.extend(("synthetic_client_close_intent", "synthetic_server_close_intent"))
            self.closed = True

    def summary(self):
        return {
            "schema_version": 1,
            "all_authentication_synthetic": True,
            "native_account_verified": False,
            "real_model_ready": False,
            "cancellation_proves_native_rollback": False,
            "outcome": self.outcome.value,
            "catalog_matched": self.catalog_matched,
            "login_acknowledged": self.login_acknowledged,
            "simulated_authenticated": self.outcome is Outcome.SIMULATED_AUTHENTICATED,
            "synthetic_login_intents": self.intents.count("synthetic_login_intent"),
            "synthetic_account_read_intents": self.intents.count("synthetic_account_read_intent"),
            "synthetic_close_intents": sum(item.endswith("close_intent") for item in self.intents),
            "cancel_replies": self.cancel_replies,
            "late_events": self.late_events,
            "synthetic_fixture_closed": self.closed,
        }


def run_rehearsals():
    """Run fixed self-authored event sequences; no input or external callbacks."""
    reports = []
    for case in range(7):
        fixture = AuthRehearsal()
        try:
            fixture.advertise(synthetic_catalog())
            if case == 6:
                fixture.request_protected_restore()
            else:
                fixture.begin_login()
                if case == 0:
                    fixture.acknowledge_login()  # ACK alone remains incomplete.
                elif case in (1, 2):
                    fixture.acknowledge_login()
                    fixture.account_snapshot(synthetic_snapshot(case == 2))
                elif case == 3:
                    fixture.cancel_elicitation()
                    fixture.account_snapshot(synthetic_snapshot(True))
                elif case == 4:
                    fixture.timeout()
                    fixture.acknowledge_login()
                elif case == 5:
                    fixture.deny()
        finally:
            fixture.close()
        reports.append(fixture.summary())
    return reports


def main():
    print(json.dumps({"all_authentication_synthetic": True, "cases": run_rehearsals()},
                     separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
