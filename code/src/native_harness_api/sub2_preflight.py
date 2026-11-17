"""Check an isolated Sub2API test route without submitting a completion."""

import argparse
import ipaddress
import json
import os
from urllib.parse import urlsplit

from .bridge_client import HTTPTransport, origin
from .errors import ChannelError


class PreflightError(Exception):
    """A fixed diagnostic that never includes an upstream response."""


def _get(transport, path, failure):
    try:
        value = transport.request("GET", path)
    except ChannelError as exc:
        raise PreflightError(f"{failure} (HTTP {exc.status})") from None
    if not isinstance(value, dict):
        raise PreflightError(f"{failure} (invalid response)")
    return value


def _rpm(admin, user_id, group_id):
    value = _get(admin, f"/api/v1/admin/users/{user_id}/rpm-status", "SUB2_RPM_READ_FAILED")
    data = value.get("data")
    if value.get("code") != 0 or not isinstance(data, dict) or not isinstance(data.get("per_group"), list):
        raise PreflightError("SUB2_RPM_STATUS_INVALID")
    groups = [row for row in data["per_group"] if isinstance(row, dict) and row.get("group_id") == group_id]
    if len(groups) != 1 or groups[0].get("source") not in ("group", "override"):
        raise PreflightError("SUB2_RPM_GROUP_UNVERIFIED")
    group = groups[0]
    counts = {"user_used": data.get("user_rpm_used"), "user_limit": data.get("user_rpm_limit"),
              "group_used": group.get("used"), "group_limit": group.get("limit")}
    if any(type(count) is not int or count < 0 for count in counts.values()):
        raise PreflightError("SUB2_RPM_STATUS_INVALID")
    if (counts["user_limit"] and counts["user_used"] >= counts["user_limit"]
            or counts["group_limit"] and counts["group_used"] >= counts["group_limit"]):
        raise PreflightError("SUB2_RPM_EXHAUSTED")
    return counts


def check(sub2_url, api_key, admin_token, *, user_id, group_id, model):
    """Check model visibility and RPM headroom for the supplied test user and group."""
    try:
        base_url = origin(sub2_url)
        host = urlsplit(base_url).hostname
        if host != "localhost" and not ipaddress.ip_address(host).is_loopback:
            raise ValueError("Loopback required")
        user = HTTPTransport(base_url, api_key)
        admin = HTTPTransport(base_url, admin_token)
    except ValueError:
        raise PreflightError("SUB2_LOOPBACK_OR_CREDENTIAL_INVALID") from None
    if api_key == admin_token:
        raise PreflightError("SUB2_DISTINCT_CREDENTIALS_REQUIRED")
    if any(type(value) is not int or not 0 < value < 2**63 for value in (user_id, group_id)):
        raise PreflightError("SUB2_USER_OR_GROUP_INVALID")
    before = _rpm(admin, user_id, group_id)
    catalog = _get(user, "/v1/models", "SUB2_MODELS_READ_FAILED")
    models = catalog.get("data")
    if not isinstance(models, list) or not any(isinstance(item, dict) and item.get("id") == model for item in models):
        raise PreflightError("SUB2_MODEL_NOT_VISIBLE")
    after = _rpm(admin, user_id, group_id)
    return {"model_visible": True, "rpm_before": before, "rpm_after": after}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sub2-url", required=True, help="Loopback Sub2API origin")
    parser.add_argument("--user-id", required=True, type=int)
    parser.add_argument("--group-id", required=True, type=int)
    parser.add_argument("--model", required=True, help="Expected public model ID")
    args = parser.parse_args()
    try:
        result = check(args.sub2_url, os.environ.get("SUB2_PREFLIGHT_API_KEY"),
                       os.environ.get("SUB2_PREFLIGHT_ADMIN_TOKEN"), user_id=args.user_id,
                       group_id=args.group_id, model=args.model)
    except PreflightError as exc:
        parser.exit(1, f"{exc}\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
