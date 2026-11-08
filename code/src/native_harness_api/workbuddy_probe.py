"""Local, explicit, single-prompt WorkBuddy bundled-runtime experiment.

Not a model API, not a dispatcher, and not a claim of WorkBuddy compatibility.
No credential discovery, login, automatic retry, or state reset is provided.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
import unicodedata

from .workbuddy_acp import ACPClient, ACPError

SCHEMA = "workbuddy-local-trial-v1"
MODE = "workbuddy_bundled_stdio_experiment"
MAX_PROMPT = 65536
MAX_STATE = 2 * 1024 * 1024
SAFE_STOP_REASONS = frozenset({"end_turn", "max_tokens", "max_turn_requests", "refusal", "cancelled"})
SAFE_ERROR_CODES = frozenset({
    "ACP_TIMEOUT", "ACP_EOF", "ACP_MODEL_CHANGED", "ACP_MODEL_UNVERIFIED",
    "ACP_PERMISSION_MODE_UNVERIFIED", "ACP_OUTPUT_LIMIT", "ACP_INVALID_JSON",
    "ACP_INVALID_FRAME", "ACP_UNEXPECTED_RESPONSE", "ACP_WRONG_SESSION", "ACP_INVALID_EVENT",
    "ACP_REMOTE_ERROR", "ACP_AUTH_REQUIRED", "ACP_CALLBACK_FAILED", "ACP_COMPLETION_UNVERIFIED",
    "ACP_EMPTY_OUTPUT", "ACP_UNSUPPORTED_EVENT", "ACP_INVALID_PERMISSION", "ACP_UNSAFE_PERMISSION",
    "ACP_LATE_MESSAGE", "ACP_INVALID_STATE", "NATIVE_OBSERVATION_UNKNOWN", "INTERRUPTED_NO_RETRY",
    "TRIAL_STATE_WRITE_FAILED",
})


class TrialError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _parse(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("duplicate key")
            value[key] = item
        return value
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))


def _hash(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _path(value):
    if not isinstance(value, str) or not value or any(ord(c) < 32 for c in value):
        raise TrialError("INVALID_PATH")
    path = Path(os.path.expandvars(value)).expanduser()
    if not path.is_absolute():
        raise TrialError("ABSOLUTE_PATH_REQUIRED")
    if ".." in path.parts:
        raise TrialError("PATH_TRAVERSAL_REJECTED")
    return path


def _no_links(path):
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise TrialError("LINK_PATH_REJECTED")


def _directory(path, *, create=False):
    _no_links(path)
    if path == Path(path.anchor) or path == Path.home():
        raise TrialError("DEDICATED_DIRECTORY_REQUIRED")
    if create:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.is_dir():
        raise TrialError("DIRECTORY_NOT_FOUND")


def _canonical_path(path):
    """Use host filesystem semantics, including Windows case and existing aliases."""
    _no_links(path)
    value = str(path.resolve(strict=False))
    if os.name == "nt":
        # Normal and extended Windows spellings must not evade overlap checks.
        if value.startswith("\\\\?\\UNC\\"):
            value = "\\\\" + value[8:]
        elif value.startswith("\\\\?\\"):
            value = value[4:]
    return Path(os.path.normcase(value))


def _overlap(first, second):
    a, b = _canonical_path(first), _canonical_path(second)
    if a == b or a in b.parents or b in a.parents:
        return True
    # samefile catches existing filesystem aliases independently of spelling.
    try:
        return first.exists() and second.exists() and os.path.samefile(first, second)
    except OSError:
        raise TrialError("PATH_IDENTITY_UNVERIFIED") from None


def _file_hash(path):
    _no_links(path)
    if not path.is_file() or not 0 < path.stat().st_size <= 1024 ** 3:
        raise TrialError("BUNDLE_FILE_NOT_FOUND")
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


@dataclass(frozen=True)
class TrialConfig:
    executable: Path
    cli_entry: Path
    data_dir: Path
    workspace: Path
    model_id: str
    timeout_seconds: float = 120
    native_config_dir: Path | None = None

    @classmethod
    def load(cls, path):
        try:
            with Path(path).open("rb") as handle:
                raw = handle.read(65537)
            if len(raw) > 65536:
                raise ValueError()
            value = _parse(raw)
            if not isinstance(value, dict) or value.pop("schema", None) != SCHEMA or value.pop("mode", None) != MODE:
                raise ValueError()
            for key in ("executable", "cli_entry", "data_dir", "workspace"):
                value[key] = _path(value[key])
            if value.get("native_config_dir") is not None:
                value["native_config_dir"] = _path(value["native_config_dir"])
            config = cls(**value)
            config.validate()
            return config
        except TrialError:
            raise
        except (OSError, ValueError, TypeError, KeyError):
            raise TrialError("INVALID_CONFIG") from None

    def validate(self):
        for path in (self.executable, self.cli_entry, self.data_dir, self.workspace):
            if not isinstance(path, Path) or not path.is_absolute() or ".." in path.parts:
                raise TrialError("ABSOLUTE_PATH_REQUIRED")
        if self.executable.name.lower() not in {"workbuddy.exe", "workbuddy"}:
            raise TrialError("WORKBUDDY_EXECUTABLE_REQUIRED")
        if self.cli_entry.name.lower() not in {"codebuddy", "codebuddy.js", "codebuddy.cjs", "codebuddy-lite-wb", "codebuddy-lite-wb.js"}:
            raise TrialError("BUNDLED_ENTRY_REQUIRED")
        try:
            self.cli_entry.relative_to(self.executable.parent)
        except ValueError:
            raise TrialError("ENTRY_OUTSIDE_WORKBUDDY_BUNDLE") from None
        if not isinstance(self.model_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}", self.model_id):
            raise TrialError("INVALID_MODEL_ID")
        if (type(self.timeout_seconds) not in (int, float) or not math.isfinite(self.timeout_seconds)
                or not 10 <= self.timeout_seconds <= 600):
            raise TrialError("INVALID_TIMEOUT")
        if self.native_config_dir is not None and (not isinstance(self.native_config_dir, Path) or not self.native_config_dir.is_absolute()):
            raise TrialError("INVALID_NATIVE_CONFIG_DIRECTORY")
        if _overlap(self.data_dir, self.workspace) or _overlap(self.data_dir, self.executable.parent):
            raise TrialError("DEDICATED_DIRECTORY_REQUIRED")
        if self.native_config_dir is not None and _overlap(self.data_dir, self.native_config_dir):
            raise TrialError("DEDICATED_DIRECTORY_REQUIRED")

    def command(self):
        return [str(self.executable), str(self.cli_entry), "--acp", "--permission-mode", "default"]

    def environment(self, source=None):
        source = os.environ if source is None else source
        # Do not inherit API keys, bridge tokens, proxy settings or shell hooks.
        names = {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP",
                 "USERPROFILE", "APPDATA", "LOCALAPPDATA", "HOME", "HOMEDRIVE", "HOMEPATH",
                 "LANG", "LC_ALL", "NUMBER_OF_PROCESSORS", "PROGRAMFILES", "PROGRAMFILES(X86)"}
        env = {k: v for k, v in source.items() if k.upper() in names}
        env["ELECTRON_RUN_AS_NODE"] = "1"
        env["CODEBUDDY_DISABLE_IDE"] = "1"
        env["CODEBUDDY_CODE_DISABLE_TERMINAL_TITLE"] = "1"
        if self.native_config_dir is not None:
            env["CODEBUDDY_CONFIG_DIR"] = str(self.native_config_dir)
        return env

    def binding(self):
        # Used only as a local digest input. Never exported as a machine path.
        return {"executable": str(self.executable), "entry": str(self.cli_entry),
                "workspace": str(self.workspace), "native_config_dir":
                str(self.native_config_dir) if self.native_config_dir else None,
                "model_id": self.model_id}


@contextmanager
def trial_lock(root):
    _directory(root, create=True)
    path = root / "trial.lock"
    _no_links(path)
    with path.open("a+b") as handle:
        if handle.seek(0, 2) == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        acquired = False
        try:
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError:
                raise TrialError("TRIAL_BUSY") from None
            yield
        finally:
            if acquired:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class TrialStore:
    """Single immutable invocation intent, not an executable queue.

    A mismatched marker or corrupt store is not permission to repeat a send.
    Manual deletion of both evidence and marker is unsupported, not detectable.
    Full Windows power-loss and hostile-local-user protection are not claimed.
    """
    def __init__(self, root, *, create=False):
        self.root = Path(root)
        self.db, self.marker = self.root / "trial.sqlite", self.root / "created"
        self.intent_marker = self.root / "prompt-intent"
        _no_links(self.root)
        for path in (self.db, self.marker, self.intent_marker):
            _no_links(path)
        if self.db.exists() != self.marker.exists() or self.intent_marker.exists() and not self.db.exists():
            raise TrialError("TRIAL_STATE_MISSING")
        if not self.db.exists():
            if not create:
                return
            _directory(self.root, create=True)
            with self.marker.open("x", encoding="utf-8") as handle:
                handle.write(SCHEMA + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            try:
                with self.connection() as con:
                    con.execute("CREATE TABLE trial (singleton INTEGER PRIMARY KEY CHECK(singleton=1), record TEXT NOT NULL)")
            except Exception:
                raise TrialError("TRIAL_STATE_WRITE_FAILED") from None
        try:
            if self.marker.read_text(encoding="utf-8") != SCHEMA + "\n":
                raise ValueError()
            with self.connection() as con:
                if con.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise ValueError()
                con.execute("SELECT record FROM trial LIMIT 1")
        except Exception:
            raise TrialError("TRIAL_STATE_CORRUPT") from None

    @contextmanager
    def connection(self):
        _no_links(self.db)
        con = sqlite3.connect(self.db, timeout=5)
        try:
            con.execute("PRAGMA synchronous=FULL")
            with con:
                yield con
        finally:
            con.close()

    def load(self):
        if not self.db.exists():
            if self.marker.exists():
                raise TrialError("TRIAL_STATE_MISSING")
            return None
        try:
            with self.connection() as con:
                row = con.execute("SELECT record FROM trial WHERE singleton=1").fetchone()
            if row is None:
                if self.intent_marker.exists():
                    raise TrialError("TRIAL_INTENT_INCOMPLETE")
                return None
            if not isinstance(row[0], str) or len(row[0].encode("utf-8")) > MAX_STATE:
                raise ValueError()
            record = _parse(row[0])
            if (not isinstance(record, dict) or record.get("schema") != SCHEMA
                    or record.get("prompt_intent") is not True
                    or not re.fullmatch(r"[a-f0-9]{64}", record.get("request_digest", ""))
                    or record.get("status") not in {"prompt_intent", "native_turn_returned", "unknown"}):
                raise ValueError()
            if not isinstance(record.get("model_id"), str) or not isinstance(record.get("bundle"), dict):
                raise ValueError()
            if "result" in record and (not isinstance(record["result"], dict)
                    or not isinstance(record["result"].get("text"), str)
                    or not isinstance(record["result"].get("stop_reason"), str)):
                raise ValueError()
            if (not self.intent_marker.is_file() or
                    self.intent_marker.read_text(encoding="ascii") != record["request_digest"] + "\n"):
                raise TrialError("TRIAL_INTENT_MARKER_MISMATCH")
            return record
        except TrialError:
            raise
        except Exception:
            raise TrialError("TRIAL_STATE_CORRUPT") from None

    def intent(self, record):
        encoded = _json(record)
        try:
            # Independent, append-only evidence also detects a lost/empty DB row.
            # A crash between these two durable writes stays blocked, never retryable.
            _no_links(self.intent_marker)
            with self.intent_marker.open("x", encoding="ascii") as handle:
                handle.write(record["request_digest"] + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            with self.connection() as con:
                con.execute("INSERT INTO trial VALUES(1,?)", (encoded,))
        except Exception:
            raise TrialError("TRIAL_INTENT_NOT_COMMITTED") from None

    def finish(self, digest, **fields):
        old = self.load()
        if old is None or old["request_digest"] != digest:
            raise TrialError("TRIAL_STATE_CHANGED")
        if old["status"] == "native_turn_returned" and any(k in fields for k in ("status", "result", "error_code")):
            raise TrialError("IMMUTABLE_RESULT")
        value = dict(old, **fields)
        encoded = _json(value)
        if len(encoded.encode("utf-8")) > MAX_STATE:
            raise TrialError("TRIAL_RESULT_TOO_LARGE")
        try:
            with self.connection() as con:
                result = con.execute("UPDATE trial SET record=? WHERE singleton=1 AND record=?", (encoded, _json(old)))
                if result.rowcount != 1:
                    raise TrialError("TRIAL_STATE_CHANGED")
        except Exception:
            raise TrialError("TRIAL_STATE_WRITE_FAILED") from None


def _make_client(config, factory, permission_handler=None):
    return factory(command=config.command(), cwd=config.workspace, env=config.environment(),
                   timeout=config.timeout_seconds, permission_handler=permission_handler)


def check_bundle(config):
    config.validate()
    hashes = {"executable_sha256": _file_hash(config.executable), "entry_sha256": _file_hash(config.cli_entry)}
    _directory(config.workspace)
    if config.native_config_dir is not None:
        _directory(config.native_config_dir)  # Metadata only; never enumerate or read its files.
    return {"schema": SCHEMA, "kind": MODE, "bundle": hashes,
            "identity_basis": "user_selected_installation_not_independently_verified",
            "real_inference_run": False, "formal_model_channel": False}


def doctor(config, *, launch=False, client_factory=ACPClient):
    report = check_bundle(config)
    if not launch:
        report["native_probe"] = "NOT_RUN"
        return report
    with trial_lock(config.data_dir):
        if TrialStore(config.data_dir).load() is not None:
            raise TrialError("NO_NATIVE_RELAUNCH_AFTER_INTENT")
        client = _make_client(config, client_factory)
        try:
            client.start()
            identity = client.initialize()
            session = client.new_session(config.workspace)
            report["native_summary"] = _native_summary(identity, session)
            report["native_probe"] = "ACP_EMPTY_SESSION_CREATED_NO_PROMPT"
            report["login_inheritance"] = "NOT_PROVEN_BY_INITIALIZE"
            return report
        finally:
            client.close()


def run_trial(config, text, *, client_factory=ACPClient, permission_handler=None):
    config.validate()
    if not isinstance(text, str) or not text.strip() or "\x00" in text or len(text.encode("utf-8")) > MAX_PROMPT:
        raise TrialError("INVALID_PROMPT")
    request_digest = _hash({"binding": config.binding(), "prompt": text})
    with trial_lock(config.data_dir):
        store = TrialStore(config.data_dir, create=True)
        existing = store.load()
        if existing is not None:
            if existing["request_digest"] != request_digest:
                raise TrialError("TRIAL_ALREADY_USED")
            return dict(existing, replay=True)
        if config.model_id.lower() in {"auto", "default", "model_id_from_doctor"}:
            raise TrialError("EXPLICIT_MODEL_REQUIRED")
        bundle = check_bundle(config)
        client = _make_client(config, client_factory, permission_handler)
        intent_written = False
        native_identity = {}
        def before_send():
            nonlocal intent_written
            if {"executable_sha256": _file_hash(config.executable), "entry_sha256": _file_hash(config.cli_entry)} != bundle["bundle"]:
                raise TrialError("BUNDLE_CHANGED_BEFORE_PROMPT")
            store.intent({"schema": SCHEMA, "prompt_intent": True, "status": "prompt_intent",
                          "request_digest": request_digest, "model_id": config.model_id,
                          "session_id": client.session_id, "created": int(time.time()),
                          "bundle": bundle["bundle"], "liveness": "unknown",
                          "native_agent": native_identity,
                          "model_evidence": "native_session_selection_readback_only"})
            intent_written = True
        try:
            client.start()
            identity = client.initialize()
            session = client.new_session(config.workspace)
            summary = _native_summary(identity, session)
            native_identity = {"name": summary["agent_name"], "version": summary["agent_version"]}
            client.set_model(config.model_id)
            result = client.prompt(text, before_send)
            store.finish(request_digest, status="native_turn_returned", result=result,
                         finished=int(time.time()), liveness="unknown")
        except BaseException as exc:
            if intent_written:
                code = exc.code if isinstance(exc, (ACPError, TrialError)) else "NATIVE_OBSERVATION_UNKNOWN"
                # Never overwrite a committed result because later cleanup failed.
                current = store.load()
                if current and current["status"] != "native_turn_returned":
                    store.finish(request_digest, status="unknown", error_code=code, liveness="unknown")
            if isinstance(exc, (ACPError, TrialError)):
                raise
            if isinstance(exc, KeyboardInterrupt):
                raise TrialError("INTERRUPTED_NO_RETRY") from None
            raise TrialError("NATIVE_OBSERVATION_UNKNOWN") from None
        finally:
            try:
                closed = client.close()
                if intent_written:
                    store.finish(request_digest, host_exited=closed.get("host_exited") is True,
                                 descendants_stopped=False)
            except Exception:
                pass  # Original durable intent/result remains authoritative, never resend.
        return dict(store.load(), replay=False)


def export_summary(record):
    if record is None:
        return {"schema": SCHEMA, "status": "NOT_RUN", "real_inference_run": False}
    result = record.get("result") or {}
    text = result.get("text", "")
    def sha(value):
        return value if isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) else None
    bundle = record.get("bundle") if isinstance(record.get("bundle"), dict) else {}
    error = record.get("error_code")
    stop = result.get("stop_reason")
    status = record.get("status")
    return {"schema": SCHEMA, "kind": MODE,
            "status": status if status in {"prompt_intent", "native_turn_returned", "unknown"} else "unknown",
            "prompt_may_have_run": True, "request_digest": sha(record.get("request_digest")),
            "bundle": {key: sha(bundle.get(key)) for key in ("executable_sha256", "entry_sha256")},
            "requested_model_digest": _hash(record.get("model_id")),
            "native_agent_self_report_recorded": isinstance(record.get("native_agent"), dict),
            "identity_basis": "user_selected_bundle_and_server_self_report_not_independently_verified",
            "model_evidence": "native_session_selection_readback_only",
            "provider_model_independently_verified": False,
            "native_stop_reason": stop if stop in SAFE_STOP_REASONS else "unverified",
            "text_turn_complete": result.get("stop_reason") == "end_turn" and isinstance(text, str) and bool(text.strip()),
            "output_nonempty": isinstance(text, str) and bool(text.strip()),
            "output_bytes": len(text.encode("utf-8")) if isinstance(text, str) else 0,
            "error_code": error if error in SAFE_ERROR_CODES else (None if error is None else "UNRECOGNIZED_ERROR"),
            "host_exited": record.get("host_exited") is True,
            "descendants_stopped": False, "liveness": "unknown", "usage": "unknown",
            "formal_model_channel": False, "automatic_retry": False}


def _display(text):
    return "".join(c if c in "\n\t" or unicodedata.category(c) not in {"Cc", "Cf", "Cs"}
                   else (f"\\u{ord(c):04x}" if ord(c) <= 0xffff else f"\\U{ord(c):08x}")
                   for c in str(text))


def _native_summary(identity, session):
    """Project known metadata only; never dump arbitrary native protocol objects."""
    def small(value):
        return _display(value)[:200] if isinstance(value, str) else None
    def obj(value):
        return value if isinstance(value, dict) else {}
    def arr(value):
        return value if isinstance(value, list) else []
    info = obj(identity.get("agentInfo"))
    models = obj(session.get("models"))
    modes = obj(session.get("modes"))
    result = {"agent_name": small(info.get("name")), "agent_version": small(info.get("version")),
              "current_model_id": small(models.get("currentModelId")),
              "current_mode_id": small(modes.get("currentModeId")),
              "available_model_ids": [], "available_mode_ids": [], "config_options": []}
    for item in arr(models.get("availableModels"))[:100]:
        if isinstance(item, dict) and isinstance(item.get("modelId"), str):
            result["available_model_ids"].append(small(item["modelId"]))
    for item in arr(modes.get("availableModes"))[:50]:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            result["available_mode_ids"].append(small(item["id"]))
    for item in arr(session.get("configOptions"))[:50]:
        if isinstance(item, dict) and item.get("category") in {"model", "mode"}:
            values = []
            for option in arr(item.get("options"))[:100]:
                if isinstance(option, dict) and isinstance(option.get("value"), str):
                    values.append(small(option["value"]))
                elif isinstance(option, dict):
                    for nested in arr(option.get("options"))[:100]:
                        if isinstance(nested, dict) and isinstance(nested.get("value"), str):
                            values.append(small(nested["value"]))
            result["config_options"].append({"category": item["category"], "id": small(item.get("id")),
                                               "current_value": small(item.get("currentValue")), "values": values})
    return result


def _permission(request):
    if not all(stream.isatty() for stream in (sys.stdin, sys.stdout, sys.stderr)):
        return "reject_once"  # Never turn piped input into a native tool approval.
    print("\nNative tool approval (local only):", _display(_json(request)), file=sys.stderr)
    answer = input("Type ALLOW ONCE for this request, anything else denies: ").strip()
    return "allow_once" if answer == "ALLOW ONCE" else "reject_once"


def _setup(path):
    if path.exists():
        raise TrialError("CONFIG_ALREADY_EXISTS")
    def ask(label, default=None):
        value = input(label + (f" [{default}]" if default else "") + ": ").strip().strip('"')
        return value or default
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local" / "share"))) / "NativeHarnessTrial"
    value = {"schema": SCHEMA, "mode": MODE,
             "executable": ask("Official installed WorkBuddy.exe absolute path"),
             "cli_entry": ask("Bundled codebuddy/codebuddy-lite-wb entry absolute path"),
             "data_dir": ask("New dedicated state directory", str(base / "state")),
             "workspace": ask("New empty test workspace", str(base / "workspace")),
             "model_id": ask("Exact native model ID (doctor --launch can list candidates)", "MODEL_ID_FROM_DOCTOR"),
             "timeout_seconds": 120,
             "native_config_dir": ask("Optional original WorkBuddy config directory (not a token)")}
    _no_links(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Validate before writing a configuration, then create only explicitly chosen directories.
    args = {k: _path(value[k]) for k in ("executable", "cli_entry", "data_dir", "workspace")}
    args.update(model_id=value["model_id"], native_config_dir=_path(value["native_config_dir"]) if value["native_config_dir"] else None)
    config = TrialConfig(**args)
    config.validate()
    _directory(config.data_dir, create=True)
    _directory(config.workspace, create=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    print("Configuration saved. No native process or model was run.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("setup", "doctor", "run", "inspect", "export"):
        command = sub.add_parser(name)
        command.add_argument("--config", type=Path, required=True)
        if name == "doctor":
            command.add_argument("--launch", action="store_true", help="Explicitly launch selected native bundle; create empty ACP session, never send a prompt")
        if name == "run":
            command.add_argument("--prompt-file", type=Path, required=True)
        if name == "export":
            command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "setup":
            _setup(args.config)
            return 0
        config = TrialConfig.load(args.config)
        if args.command == "doctor":
            value = doctor(config, launch=args.launch)
        elif args.command == "run":
            if not all(stream.isatty() for stream in (sys.stdin, sys.stdout, sys.stderr)):
                raise TrialError("INTERACTIVE_TERMINAL_REQUIRED")
            print("This can use your WorkBuddy quota and native tools. Existing native allow rules still apply; only emitted approvals are prompted. It is NOT a sandbox or a production model API.", file=sys.stderr)
            if input("Type RUN REAL WORKBUDDY to submit one prompt: ").strip() != "RUN REAL WORKBUDDY":
                raise TrialError("REAL_RUN_NOT_CONFIRMED")
            with args.prompt_file.open("rb") as handle:
                raw = handle.read(MAX_PROMPT + 1)
            if len(raw) > MAX_PROMPT:
                raise TrialError("INVALID_PROMPT")
            record = run_trial(config, raw.decode("utf-8-sig"), permission_handler=_permission)
            value = export_summary(record)
            value["replay"] = record["replay"]
            print(_display(_json(value)))
            if record.get("result", {}).get("text"):
                print("\nNative output (local only, not included in export):\n" + _display(record["result"]["text"]))
            return 0 if record["status"] == "native_turn_returned" and value["text_turn_complete"] else 2
        else:
            record = TrialStore(config.data_dir).load()
            value = export_summary(record)
            if args.command == "export":
                _no_links(args.output)
                with args.output.open("x", encoding="utf-8") as handle:
                    handle.write(_json(value) + "\n")
                print("Redacted summary exported. Review it before sharing.")
                return 0
        print(_display(_json(value)))
        return 0
    except (TrialError, ACPError) as exc:
        print(_json({"error": exc.code, "automatic_retry": False,
                     "guidance": "Keep the same state. Inspect/export existing evidence; do not delete or bypass it."}), file=sys.stderr)
        return 2
    except (OSError, ValueError, UnicodeError, EOFError):
        print(_json({"error": "LOCAL_INPUT_OR_STATE_ERROR", "automatic_retry": False}), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print(_json({"error": "INTERRUPTED_NO_RETRY", "automatic_retry": False}), file=sys.stderr)
        return 130
    except Exception:
        # A native peer must not make the CLI print raw frames, tokens or paths.
        print(_json({"error": "LOCAL_PROTOCOL_OR_STATE_ERROR", "automatic_retry": False}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
