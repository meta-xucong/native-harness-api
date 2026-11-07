"""Manual, single-attempt client for a LOOPBACK P1a fixture only.

Persist one operation file per intentional invocation. Reuse that file to observe
or replay; a different prompt with the same file must fail with 409. The file
contains an operation key only, never a bearer credential or user text.
"""
import argparse
import json
import os
from pathlib import Path
import uuid
from native_harness_api.bridge_client import HTTPTransport
from native_harness_api.contracts import identifier, parse_json
from native_harness_api.errors import ChannelError


def stable_key(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("Operation file cannot be a symlink")
    key = "client-" + uuid.uuid4().hex
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        value = parse_json(path.read_bytes(), maximum=1024)
        return identifier(value["operation_key"])
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump({"operation_key": key}, handle)
        handle.flush()
        os.fsync(handle.fileno())
    return key


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Explicit loopback fixture origin")
    parser.add_argument("--operation-file", type=Path, required=True)
    parser.add_argument("--text", default="Synthetic offline request")
    args = parser.parse_args()
    from urllib.parse import urlsplit
    if urlsplit(args.url).hostname not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("P1a example only permits loopback test fixtures")
    key = stable_key(args.operation_file)
    client = HTTPTransport(args.url, os.environ.get("NATIVE_TEST_USER_KEY", ""))
    try:
        response = client.request("POST", "/v1/chat/completions", {
            "model": "synthetic-model", "messages": [{"role": "user", "content": args.text}]},
            extra_headers={"Idempotency-Key": key})
        print(json.dumps(response, ensure_ascii=False))
    except ChannelError as exc:
        print(json.dumps(exc.body()))
        raise SystemExit(1) from None  # No implicit retry; caller reuses the same file deliberately.

if __name__ == "__main__":
    main()
