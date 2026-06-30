from __future__ import annotations

import re
import hashlib
from datetime import datetime


def make_stable_id(title: str) -> str:
    stripped = title.strip()
    lowered = stripped.lower()
    normalized = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    if normalized:
        return normalized
    if stripped:
        digest = hashlib.sha1(stripped.encode("utf-8")).hexdigest()[:8]
        return f"id-{digest}"
    return "untitled"


def make_unique_stable_id(title: str, existing: set[str]) -> str:
    base = make_stable_id(title)
    if base not in existing:
        return base
    suffix = 2
    while f"{base}-{suffix}" in existing:
        suffix += 1
    return f"{base}-{suffix}"


def make_event_id(now: datetime, task_id: str, existing: set[str]) -> str:
    base = now.strftime("%Y%m%d-%H%M%S") + f"{now.microsecond // 1000:03d}-{task_id}"
    if base not in existing:
        return base
    suffix = 2
    while f"{base}-{suffix}" in existing:
        suffix += 1
    return f"{base}-{suffix}"
