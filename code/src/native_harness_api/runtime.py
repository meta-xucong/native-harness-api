"""Explicit P1b loopback integration runtime. FakeHarness is NOT WorkBuddy.

No task database or fallback lives in this process. The source and node clients
use the separately running real Bridge HTTP service. Configuration contains
credential environment-variable names, never credential values.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import re
import sys
import time

from .adapters import FakeHarness
from .bridge_client import BridgeClient, NodeClient, origin
from .contracts import Binding, canonical, digest, identifier, parse_json
from .facade import Facade, Service
from .http_server import make_server
from .node import Journal, NodePolicy, Runner


@dataclass(frozen=True)
class RuntimeConfig:
    source: str
    ledger_instance: str
    bridge_url: str
    binding: Binding
    owner: tuple[str, str]
    data_dir: Path
    source_token_env: str = "NATIVE_BRIDGE_SOURCE_TOKEN"
    node_token_env: str = "NATIVE_BRIDGE_NODE_TOKEN"
    facade_token_env: str = "NATIVE_FACADE_TOKEN"
    accepting_new: bool = False
    port: int = 8790
    wait_seconds: float = 10
    poll_seconds: float = 0.25
    fake_mode: str = "success"

    @classmethod
    def load(cls, path):
        with Path(path).open("rb") as handle:
            value = parse_json(handle.read(65_537))
        if isinstance(value, dict) and value.get("mode") == "workbuddy_local_trial":
            from .native_trial_runtime import NativeTrialConfig
            return NativeTrialConfig.from_value(value)
        if not isinstance(value, dict) or value.pop("mode", None) != "p1b_integration":
            raise ValueError("Explicit p1b_integration mode required")
        if value.pop("adapter", None) != "fake":
            raise ValueError("Only an explicitly synthetic adapter is implemented")
        value["binding"] = Binding(**value["binding"])
        owner = value.pop("owner")
        if not isinstance(owner, list) or len(owner) != 2:
            raise ValueError("Exactly one trusted test owner is required")
        value["owner"] = tuple(owner)
        value["data_dir"] = Path(value["data_dir"]).expanduser()
        config = cls(**value)
        config.validate()
        return config

    def validate(self):
        identifier(self.source)
        identifier(self.ledger_instance)
        origin(self.bridge_url)
        if type(self.binding) is not Binding or type(self.owner) is not tuple or len(self.owner) != 2:
            raise ValueError("Invalid trusted binding or owner")
        for part in self.owner:
            if not isinstance(part, str):
                raise ValueError("Invalid test owner")
            identifier(part)
        self._validate_binding()
        if type(self.accepting_new) is not bool or type(self.port) is not int or not 0 <= self.port <= 65535:
            raise ValueError("Invalid admission or loopback port")
        if (type(self.wait_seconds) not in (int, float) or not 0 <= self.wait_seconds <= 30
                or type(self.poll_seconds) not in (int, float) or not 0.05 <= self.poll_seconds <= 5):
            raise ValueError("Invalid bounded timing")
        if self.fake_mode not in FakeHarness.MODES:
            raise ValueError("Invalid synthetic adapter mode")
        if not isinstance(self.data_dir, Path) or not self.data_dir.is_absolute() or self.data_dir.is_symlink():
            raise ValueError("An absolute dedicated data directory is required")
        names = (self.source_token_env, self.node_token_env, self.facade_token_env)
        if len(set(names)) != 3 or any(not isinstance(n, str) or not re.fullmatch(r"[A-Z_][A-Z0-9_]*", n) for n in names):
            raise ValueError("Distinct credential environment variable names are required")

    def _validate_binding(self):
        if not self.binding.model_alias.startswith("synthetic-") or not self.binding.native_model.startswith("fake-"):
            raise ValueError("Only synthetic fake bindings are enabled")

    def credential(self, name, environ=None):
        value = (os.environ if environ is None else environ).get(name)
        if (not isinstance(value, str) or not 16 <= len(value) <= 4096
                or any(ord(c) < 33 or ord(c) > 126 for c in value)):
            raise ValueError("Explicit service credential missing or invalid")
        return value

    def facade(self, environ=None):
        self.validate()
        source_token = self.credential(self.source_token_env, environ)
        facade_token = self.credential(self.facade_token_env, environ)
        if source_token == facade_token:
            raise ValueError("Source and Facade credentials must be distinct")
        bridge = BridgeClient(self.bridge_url, source_token, self.ledger_instance)
        service = Service(self.source, bridge, {self.binding.model_alias: self.binding}, self.accepting_new)
        return Facade({facade_token: service}, wait_seconds=self.wait_seconds, mode="p1b_integration")

    def runner(self, environ=None):
        self.validate()
        client = NodeClient(self.bridge_url, self.credential(self.node_token_env, environ))
        policy = NodePolicy(self.source, self.ledger_instance, frozenset({self.owner}), (self.binding,))
        return Runner(client, Journal(self.data_dir / "journal"),
                      FakeHarness(self.data_dir / "synthetic-native", mode=self.fake_mode), policy=policy)


def node_tick(runner):
    """Recover old evidence before current capability/new-admission checks.

    A removed model binding, stale readiness or failed heartbeat cannot block
    receipt recovery. The recovery-only pass holds the same journal lock and
    never polls/claims/starts new work. Heartbeat is not a start or release grant.
    """
    status = runner.run_once(allow_new=False)
    if status.get("outcome") != "idle":
        try:
            # No new capability is offered while handling unresolved evidence.
            runner.client.heartbeat("attention", [])
        except Exception:
            pass  # Readiness is advisory, not permission to discard recovery.
        return status
    runner.client.heartbeat("available", [digest(b.wire()) for b in runner.policy.bindings])
    return runner.run_once()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("facade", "node"))
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--once", action="store_true", help="One node heartbeat/recovery/poll cycle")
    args = parser.parse_args(argv)
    try:
        config = RuntimeConfig.load(args.config)
        if args.role == "facade":
            if args.once:
                raise ValueError("--once is only supported for node")
            with make_server(config.facade(), port=config.port) as server:
                server.serve_forever()
        else:
            runner = config.runner()
            while True:
                status = node_tick(runner)
                if args.once:
                    print(canonical({k: status[k] for k in ("outcome", "liveness", "code") if k in status}))
                    break
                time.sleep(config.poll_seconds)
    except KeyboardInterrupt:
        return 0  # Stopping the observer does not cancel or release native work.
    except Exception:
        print("Native integration runtime stopped; check configuration and recover the same journal. No retry or fallback was started.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
