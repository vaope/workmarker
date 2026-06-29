from __future__ import annotations

import re
from datetime import datetime


def make_stable_id(title: str) -> str:
    lowered = title.strip().lower()
    normalized = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    return normalized or "untitled"


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
