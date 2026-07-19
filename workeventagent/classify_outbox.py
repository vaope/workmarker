"""Durable classification outbox.

Archive events are enqueued as classification jobs. The outbox survives process
restarts and supports idempotent enqueue, dequeue, mark_done, mark_failed, and
timeline reconciliation (find events that missed classification).
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def enqueue_classification(outbox_dir: Path, project_id: str, event_id: str) -> None:
    """Enqueue a classification job. Idempotent — no-op if already pending or failed."""
    outbox_dir.mkdir(parents=True, exist_ok=True)
    pending_dir = outbox_dir / "pending"
    pending_dir.mkdir(parents=True, exist_ok=True)
    failed_path = outbox_dir / "failed" / f"{project_id}.{event_id}.json"

    path = pending_dir / f"{project_id}.{event_id}.json"
    if path.exists() or failed_path.exists():
        return  # already queued or previously failed

    data = {
        "project_id": project_id,
        "event_id": event_id,
        "retries": 0,
        "last_error": "",
    }
    tmp = outbox_dir / f".pending.{project_id}.{event_id}.tmp"
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def dequeue_pending(outbox_dir: Path, limit: int = 10) -> list[dict]:
    """Return up to `limit` pending classification jobs (oldest first)."""
    pending_dir = outbox_dir / "pending"
    if not pending_dir.exists():
        return []
    jobs: list[dict] = []
    for entry in sorted(pending_dir.iterdir()):
        if not entry.suffix == ".json":
            continue
        jobs.append(json.loads(entry.read_text(encoding="utf-8")))
        if len(jobs) >= limit:
            break
    return jobs


def mark_done(outbox_dir: Path, project_id: str, event_id: str) -> None:
    """Move a successfully classified job from pending/ to done/."""
    path = outbox_dir / "pending" / f"{project_id}.{event_id}.json"
    if not path.exists():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    done_dir = outbox_dir / "done"
    done_dir.mkdir(parents=True, exist_ok=True)
    tmp = outbox_dir / f".done.{project_id}.{event_id}.tmp"
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, done_dir / f"{project_id}.{event_id}.json")
    path.unlink(missing_ok=True)


def mark_failed(outbox_dir: Path, project_id: str, event_id: str, error: str) -> None:
    """Move a failed job from pending/ to failed/ with retry count and error message."""
    path = outbox_dir / "pending" / f"{project_id}.{event_id}.json"
    if not path.exists():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    data["retries"] = data.get("retries", 0) + 1
    data["last_error"] = error

    failed_dir = outbox_dir / "failed"
    failed_dir.mkdir(parents=True, exist_ok=True)
    tmp = outbox_dir / f".failed.{project_id}.{event_id}.tmp"
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, failed_dir / f"{project_id}.{event_id}.json")
    path.unlink(missing_ok=True)


def reconcile_from_timeline(
    outbox_dir: Path, project_id: str, events: list[dict]
) -> list[str]:
    """Return event_ids from `events` that are NOT tracked in the outbox.

    'Tracked' means: exists in pending/, done/, or failed/.
    """
    seen: set[str] = set()

    for subdir in ("pending", "done", "failed"):
        d = outbox_dir / subdir
        if not d.exists():
            continue
        for entry in d.iterdir():
            if not entry.suffix == ".json":
                continue
            # Parse event_id from filename: {project_id}.{event_id}.json
            stem = entry.stem
            dot_idx = stem.find(".")
            if dot_idx > 0:
                seen.add(stem[dot_idx + 1:])

    missing: list[str] = []
    for ev in events:
        if ev["event_id"] not in seen:
            missing.append(ev["event_id"])
    return missing
