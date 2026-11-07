"""API keys for the control plane's HTTP interfaces.

Keys come from the environment as `label:key` pairs separated by commas, the
same form ai-api-unified-http uses. The label names the caller in every task
and event. In the cloud the values come from Secret Manager.
"""

import hmac
import os

from fastapi import Header, HTTPException


def parse_keys(value: str) -> dict[str, str]:
    """`label:key,label:key` to {key: label}. A malformed entry stops the service."""
    keys: dict[str, str] = {}
    for entry in filter(None, (part.strip() for part in value.split(","))):
        label, sep, key = entry.partition(":")
        if not sep or not label or not key:
            raise ValueError(f"an API key entry is label:key, not {entry!r}")
        keys[key] = label
    return keys


def keys_from_env(name: str) -> dict[str, str]:
    return parse_keys(os.environ.get(name, ""))


def bearer(authorization: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="send Authorization: Bearer <API key>")
    return authorization[len("Bearer ") :]


def label_for(key: str, keys: dict[str, str]) -> str | None:
    for known, label in keys.items():
        if hmac.compare_digest(known, key):
            return label
    return None
