from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from workeventagent.ids import make_stable_id

INBOX_DIR = ".workeventagent"
INBOX_FILE = "inbox.json"
PENDING_DIR = "pending"
TERMINAL_STATES = {"archived", "canceled"}
ACTIVE_STATES = {"processing", "needs_confirmation", "error"}


def _inbox_path(workspace: Path) -> Path:
    return workspace / INBOX_DIR / INBOX_FILE


def _read_inbox(workspace: Path) -> list[dict]:
    p = _inbox_path(workspace)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _write_inbox(workspace: Path, cards: list[dict]) -> None:
    p = _inbox_path(workspace)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.parent / f".{INBOX_FILE}.tmp"
    tmp.write_text(json.dumps(cards, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def create_capture(workspace: Path, text: str, attachments: list[dict]) -> dict:
    now = datetime.now(timezone.utc)
    slug = make_stable_id(text[:48])
    capture_id = now.strftime("cap-%Y%m%d-%H%M%S") + f"{now.microsecond // 1000:03d}-{slug}"

    card: dict = {
        "capture_id": capture_id,
        "text": text,
        "state": "processing",
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }

    if attachments:
        dest_dir = capture_pending_dir(workspace, capture_id)
        dest_dir.mkdir(parents=True, exist_ok=True)
        attrs: list[dict] = []
        for a in attachments:
            src = Path(a["temp_path"])
            filename = a.get("filename", src.name)
            safe = _safe_filename(filename)
            dest = dest_dir / safe
            shutil.copy2(src, dest)
            attrs.append({"filename": filename, "safe_filename": safe})
        card["attachments"] = attrs

    cards = _read_inbox(workspace)
    cards.append(card)
    _write_inbox(workspace, cards)
    return dict(card)


def list_captures(workspace: Path) -> list[dict]:
    return _read_inbox(workspace)


def _find_card(workspace: Path, capture_id: str) -> tuple[int, dict]:
    cards = _read_inbox(workspace)
    for i, c in enumerate(cards):
        if c.get("capture_id") == capture_id:
            return i, c
    raise ValueError("capture not found")


def update_capture(workspace: Path, capture_id: str, patch: dict) -> dict:
    cards = _read_inbox(workspace)
    for i, c in enumerate(cards):
        if c.get("capture_id") == capture_id:
            for k, v in patch.items():
                c[k] = v
            c["updated_at"] = datetime.now(timezone.utc).isoformat()
            cards[i] = c
            _write_inbox(workspace, cards)
            return dict(c)
    raise ValueError("capture not found")


def cancel_capture(workspace: Path, capture_id: str) -> dict:
    idx, card = _find_card(workspace, capture_id)
    card["state"] = "canceled"
    card["updated_at"] = datetime.now(timezone.utc).isoformat()
    _remove_pending_dir(workspace, capture_id)
    cards = _read_inbox(workspace)
    cards[idx] = card
    _write_inbox(workspace, cards)
    return dict(card)


def archive_capture(workspace: Path, capture_id: str, archived: dict) -> dict:
    idx, card = _find_card(workspace, capture_id)
    card["state"] = "archived"
    card["project_path"] = archived.get("project_path", "")
    card["event_id"] = archived.get("event_id", "")
    card["updated_at"] = datetime.now(timezone.utc).isoformat()
    _remove_pending_dir(workspace, capture_id)
    cards = _read_inbox(workspace)
    cards[idx] = card
    _write_inbox(workspace, cards)
    _trim_terminal(workspace, cards)
    return dict(card)


def capture_pending_dir(workspace: Path, capture_id: str) -> Path:
    return workspace / INBOX_DIR / PENDING_DIR / capture_id


def _remove_pending_dir(workspace: Path, capture_id: str) -> None:
    d = capture_pending_dir(workspace, capture_id)
    if d.exists():
        shutil.rmtree(d)


def _trim_terminal(workspace: Path, cards: list[dict]) -> None:
    terminal = [c for c in cards if c.get("state") in TERMINAL_STATES]
    if len(terminal) <= 100:
        return
    keep_ids = {c["capture_id"] for c in terminal[-100:]}
    trimmed = [c for c in cards if c.get("state") not in TERMINAL_STATES or c["capture_id"] in keep_ids]
    if len(trimmed) != len(cards):
        _write_inbox(workspace, trimmed)


def _safe_filename(filename: str) -> str:
    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    safe = "".join(c for c in name if c.isalnum() or c in "._- ")
    return safe.strip() or "attachment"
