from __future__ import annotations

import base64
import hashlib
import json
import re
from typing import cast

SYNCV2_PREFIX = "SYNCV2:"
SYNCV2_PATTERN = re.compile(r"SYNCV2:([A-Za-z0-9_-]+={0,2})")
CURRENT_VERSION = 2


def encode_syncv2(meta: dict) -> str:
    """JSON serialize with sort_keys, base64url encode, prefix with SYNCV2:"""
    payload = json.dumps(meta, sort_keys=True, separators=(",", ":"))
    encoded = base64.urlsafe_b64encode(payload.encode()).decode()
    return f"{SYNCV2_PREFIX}{encoded}"


def decode_syncv2(text: str | None) -> dict | None:
    """Regex search for SYNCV2 tag, base64url decode, reject non-v2."""
    if not text:
        return None
    match = SYNCV2_PATTERN.search(text)
    if not match:
        return None
    try:
        decoded = base64.urlsafe_b64decode(match.group(1)).decode()
        meta = cast(dict, json.loads(decoded))
    except (ValueError, json.JSONDecodeError):
        return None
    if meta.get("version") != CURRENT_VERSION:
        return None
    return meta


def compute_hash(fields: dict) -> str:
    """JSON canonical form, sha256, truncate to 16 chars."""
    canonical = json.dumps(fields, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def append_syncv2(description: str | None, meta: dict) -> str:
    """Prepends SYNCV2:...\\n---\\n to description to survive truncation."""
    tag = encode_syncv2(meta)
    desc = description or ""
    return f"{tag}\n---\n{desc}"


def strip_syncv2(description: str | None) -> str:
    """Removes SYNCV2 tag + separator, returns clean description."""
    if not description:
        return ""
    # Remove SYNCV2 tag + separator (handles both prepend and append positions)
    result = re.sub(r"SYNCV2:[A-Za-z0-9_-]+={0,2}\n---\n?", "", description)
    result = re.sub(r"\n---\nSYNCV2:[A-Za-z0-9_-]+={0,2}", "", result)
    result = SYNCV2_PATTERN.sub("", result)
    return result.strip()
