"""Correction workflow: append-only corrections for work event archive."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from workeventagent.ids import make_event_id
from workeventagent.index_store import init_db, rebuild_index
from workeventagent.markdown_store import write_project_atomically
from workeventagent.project_schema import schema_version
from workeventagent.work_map_store import update_task_state as _wms_update_task_state


_PROJECT_WITH_ONE_EVENT = """---
project_id: corr-project
title: Correction Project
doc_kind: work_project
created: 2026-07-04
updated: 2026-07-04
---

# Correction Project

## Current Snapshot

## Work Map
### Item: Item A <!-- item:item-a -->
#### Task: Task A <!-- task:task-a -->
- status: in_progress
- next_action: Keep going
- last_event_id: event-1

## Decisions

## Attachments

## Timeline
- 2026-07-04T12:00:00+00:00 <!-- event:event-1 -->
  - task_id: task-a
  - input: Original input
  - summary: Original summary
  - status: in_progress
  - next_action: Keep going

## Daily / Weekly Rollups
"""


def correct_event_same_project(
    project_path: Path, db_path: Path, request: dict
) -> dict:
    """Append a correction event and update target task, preserving the original."""
    if not project_path.exists():
        return {"ok": False, "kind": "not_found", "error": "project file not found"}

    original_text = project_path.read_text(encoding="utf-8")
    original_event_id = request["original_event_id"]
    reason = request.get("reason", "")
    summary = request.get("summary", "")
    status = request.get("status", "in_progress")
    next_action = request.get("next_action", "")
    target_task_id = request.get("target_task_id", "")

    # Validate original event exists
    if f"<!-- event:{original_event_id} -->" not in original_text:
        return {"ok": False, "kind": "not_found", "error": f"event {original_event_id} not found in timeline"}

    # Validate target task exists
    if f"<!-- task:{target_task_id} -->" not in original_text:
        return {"ok": False, "kind": "not_found", "error": f"task {target_task_id} not found in Work Map"}

    now = datetime.now(timezone.utc)
    correction_event_id = make_event_id(now, "correction", set())

    # Build correction event string
    correction_lines = [
        f"- {now.strftime('%Y-%m-%dT%H:%M:%S+00:00')} <!-- event:{correction_event_id} -->",
        f"  - task_id: {target_task_id}",
        "  - event_type: correction",
        f"  - corrects_event_id: {original_event_id}",
    ]
    if reason:
        correction_lines.append(f"  - reason: {reason}")
    if summary:
        correction_lines.append(f"  - summary: {summary}")
    correction_lines.append(f"  - status: {status}")
    if next_action:
        correction_lines.append(f"  - next_action: {next_action}")
    else:
        correction_lines.append("  - next_action:")

    correction_block = "\n".join(correction_lines)

    # Append correction event to ## Timeline section (v1) or ## 事件证据 section (v2)
    timeline_match = re.search(r"## Timeline\n", original_text)
    if not timeline_match:
        timeline_match = re.search(r"## 事件证据.*\n", original_text)
    if not timeline_match:
        return {"ok": False, "kind": "invalid_doc", "error": "timeline section not found"}
    insert_at = timeline_match.end()
    new_text = original_text[:insert_at] + correction_block + "\n\n" + original_text[insert_at:]

    # Update target task in Work Map
    if schema_version(new_text) >= 2:
        try:
            new_text = _wms_update_task_state(
                new_text, target_task_id, status, next_action, correction_event_id,
            )
        except ValueError:
            pass  # task not found; skip update
    else:
        task_pattern = re.compile(
            rf"(#### Task: .+? <!-- task:{re.escape(target_task_id)} -->\n"
            rf"- status: )[^\n]*(\n- next_action: )[^\n]*(\n- last_event_id: )[^\n]*",
            re.MULTILINE,
        )
        task_match = task_pattern.search(new_text)
        if task_match:
            new_text = (
                new_text[: task_match.start(1)] + task_match.group(1) + status +
                new_text[task_match.start(2) : task_match.start(3)] + task_match.group(3) + correction_event_id +
                new_text[task_match.end(3) :]
            )

    write_project_atomically(project_path, new_text)
    init_db(db_path)
    rebuild_index(db_path, [project_path])

    return {
        "ok": True,
        "correction_event_id": correction_event_id,
        "original_event_id": original_event_id,
    }


# ── cross-project correction ───────────────────────────────────

_CORRECTIONS_DIR = ".workeventagent"
_CORRECTIONS_SUBDIR = "corrections"


def _corrections_dir(workspace: Path) -> Path:
    return workspace / _CORRECTIONS_DIR / _CORRECTIONS_SUBDIR


def _journal_path(workspace: Path, correction_id: str) -> Path:
    return _corrections_dir(workspace) / f"{correction_id}.json"


def _read_journal(workspace: Path, correction_id: str) -> dict | None:
    path = _journal_path(workspace, correction_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _write_journal(workspace: Path, journal: dict) -> None:
    journal_dir = _corrections_dir(workspace)
    journal_dir.mkdir(parents=True, exist_ok=True)
    path = _journal_path(workspace, journal["correction_id"])
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(journal, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _event_exists_in_project(text: str, event_id: str) -> bool:
    return f"<!-- event:{event_id} -->" in text


def _task_exists_in_project(text: str, task_id: str) -> bool:
    return f"<!-- task:{task_id} -->" in text


def _append_event_to_timeline(
    text: str, event_lines: list[str], insert_after_original_event_id: str = "",
) -> str:
    """Append event lines right after ## Timeline header (v1 or v2)."""
    timeline_match = re.search(r"## Timeline\n", text)
    if not timeline_match:
        timeline_match = re.search(r"## 事件证据.*\n", text)
    if not timeline_match:
        raise ValueError("timeline section not found")
    insert_at = timeline_match.end()
    block = "\n".join(event_lines)
    return text[:insert_at] + block + "\n\n" + text[insert_at:]


def _update_task_in_work_map(text: str, task_id: str, status: str, next_action: str, event_id: str) -> str:
    """Update status, next_action, and last_event_id for a task in Work Map (v1/v2)."""
    if schema_version(text) >= 2:
        try:
            return _wms_update_task_state(text, task_id, status, next_action, event_id)
        except ValueError:
            return text
    # v1 path
    task_pattern = re.compile(
        rf"(#### Task: .+? <!-- task:{re.escape(task_id)} -->\n"
        rf"- status: )[^\n]*(\n- next_action: )[^\n]*(\n- last_event_id: )[^\n]*",
        re.MULTILINE,
    )
    task_match = task_pattern.search(text)
    if not task_match:
        return text
    return (
        text[: task_match.start(1)] + task_match.group(1) + status +
        text[task_match.start(2) : task_match.start(3)] + task_match.group(3) + event_id +
        text[task_match.end(3) :]
    )


def _build_target_event_lines(
    timestamp_str: str,
    target_event_id: str,
    target_task_id: str,
    correction_id: str,
    source_event_id: str,
    source_project_id: str,
    summary: str,
    status: str,
    next_action: str,
) -> list[str]:
    lines = [
        f"- {timestamp_str} <!-- event:{target_event_id} -->",
        f"  - task_id: {target_task_id}",
        "  - event_type: correction_move",
        f"  - correction_id: {correction_id}",
        f"  - source_event_id: {source_event_id}",
        f"  - source_project_id: {source_project_id}",
    ]
    if summary:
        lines.append(f"  - summary: {summary}")
    lines.append(f"  - status: {status}")
    if next_action:
        lines.append(f"  - next_action: {next_action}")
    else:
        lines.append("  - next_action:")
    return lines


def _build_source_correction_event_lines(
    timestamp_str: str,
    source_correction_event_id: str,
    source_task_id: str,
    correction_id: str,
    original_event_id: str,
    target_event_id: str,
    summary: str,
    status: str,
    next_action: str,
) -> list[str]:
    lines = [
        f"- {timestamp_str} <!-- event:{source_correction_event_id} -->",
        f"  - task_id: {source_task_id}",
        "  - event_type: correction",
        f"  - correction_id: {correction_id}",
        f"  - corrects_event_id: {original_event_id}",
        f"  - target_event_id: {target_event_id}",
    ]
    if summary:
        lines.append(f"  - summary: {summary}")
    lines.append(f"  - status: {status}")
    if next_action:
        lines.append(f"  - next_action: {next_action}")
    else:
        lines.append("  - next_action:")
    return lines


def _extract_project_id(text: str) -> str:
    """Extract project_id from frontmatter."""
    m = re.search(r"^project_id:\s*(.+)$", text, re.MULTILINE)
    return m.group(1).strip() if m else ""


def correct_event_cross_project(
    source_path: Path,
    target_path: Path,
    db_path: Path,
    request: dict,
) -> dict:
    """Cross-project correction: target-first writes with recovery journal.

    Never writes a source correction event before the target event exists.
    Uses deterministic target_event_id and source_correction_event_id.
    """
    if not source_path.exists():
        return {"ok": False, "kind": "not_found", "error": "source project file not found"}
    if not target_path.exists():
        return {"ok": False, "kind": "not_found", "error": "target project file not found"}

    source_text = source_path.read_text(encoding="utf-8")
    target_text = target_path.read_text(encoding="utf-8")
    original_event_id = request["original_event_id"]
    source_task_id = request.get("source_task_id", "")
    target_task_id = request.get("target_task_id", "")
    summary = request.get("summary", "")
    status = request.get("status", "in_progress")
    next_action = request.get("next_action", "")
    reason = request.get("reason", "")
    source_item_id = request.get("source_item_id", "")

    # Validate source event exists
    if not _event_exists_in_project(source_text, original_event_id):
        return {"ok": False, "kind": "not_found", "error": f"event {original_event_id} not found in source timeline"}
    # Validate target task exists
    if not _task_exists_in_project(target_text, target_task_id):
        return {"ok": False, "kind": "not_found", "error": f"task {target_task_id} not found in target Work Map"}
    # Validate source task exists
    if not _task_exists_in_project(source_text, source_task_id):
        return {"ok": False, "kind": "not_found", "error": f"task {source_task_id} not found in source Work Map"}

    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    correction_id = make_event_id(now, f"corr-{original_event_id}", set())

    source_project_id = _extract_project_id(source_text)
    target_event_id = make_event_id(now, f"move-{target_task_id}", set())
    source_correction_event_id = make_event_id(now, f"fix-{source_task_id}", set())

    # MVP: same-workspace assumption — both source and target are flat files under the same workspace
    workspace = source_path.parent
    journal_dir = _corrections_dir(workspace)

    # ── Step 1: Write intent journal ──
    journal = {
        "correction_id": correction_id,
        "source_project_path": str(source_path),
        "source_item_id": source_item_id,
        "source_task_id": source_task_id,
        "original_event_id": original_event_id,
        "target_project_path": str(target_path),
        # target_item_id is not resolved cross-project; recovery uses task_id+event_id only
        "target_item_id": "",
        "target_task_id": target_task_id,
        "summary": summary,
        "status": status,
        "next_action": next_action,
        "target_event_id": target_event_id,
        "source_correction_event_id": source_correction_event_id,
        "stage": "intent",
        "last_error": "",
    }
    _write_journal(workspace, journal)

    # ── Step 2: Append target event (target-first) ──
    if not _event_exists_in_project(target_text, target_event_id):
        target_event_lines = _build_target_event_lines(
            timestamp, target_event_id, target_task_id, correction_id,
            original_event_id, source_project_id, summary, status, next_action,
        )
        target_text = _append_event_to_timeline(target_text, target_event_lines)
        target_text = _update_task_in_work_map(target_text, target_task_id, status, next_action, target_event_id)
        write_project_atomically(target_path, target_text)
        init_db(db_path)

    # ── Step 3: Journal → target_written ──
    journal["stage"] = "target_written"
    _write_journal(workspace, journal)

    # ── Step 4: Append source correction event ──
    source_text = source_path.read_text(encoding="utf-8")
    if not _event_exists_in_project(source_text, source_correction_event_id):
        source_corr_lines = _build_source_correction_event_lines(
            timestamp, source_correction_event_id, source_task_id, correction_id,
            original_event_id, target_event_id, summary, status, next_action,
        )
        source_text = _append_event_to_timeline(source_text, source_corr_lines)
        source_text = _update_task_in_work_map(source_text, source_task_id, status, next_action, source_correction_event_id)
        write_project_atomically(source_path, source_text)

    # ── Step 5: Journal → source_written → done ──
    journal["stage"] = "source_written"
    _write_journal(workspace, journal)

    # Verify both events exist
    final_source = source_path.read_text(encoding="utf-8")
    final_target = target_path.read_text(encoding="utf-8")
    if _event_exists_in_project(final_source, source_correction_event_id) and _event_exists_in_project(final_target, target_event_id):
        journal["stage"] = "done"
        _write_journal(workspace, journal)

    rebuild_index(db_path, [source_path, target_path])

    return {
        "ok": True,
        "correction_id": correction_id,
        "target_event_id": target_event_id,
        "source_correction_event_id": source_correction_event_id,
    }


def list_pending_corrections(workspace: Path) -> list[dict]:
    """List all incomplete correction journals (stage != done)."""
    journal_dir = _corrections_dir(workspace)
    if not journal_dir.exists():
        return []

    pending = []
    for entry in sorted(journal_dir.glob("*.json")):
        try:
            journal = json.loads(entry.read_text(encoding="utf-8"))
            if journal.get("stage") != "done":
                pending.append(journal)
        except (json.JSONDecodeError, KeyError):
            continue
    return pending


def resume_correction(workspace: Path, correction_id: str, db_path: Path) -> dict:
    """Resume or recover an incomplete cross-project correction."""
    journal = _read_journal(workspace, correction_id)
    if journal is None:
        return {"ok": False, "kind": "not_found", "error": f"correction journal {correction_id} not found"}

    stage = journal.get("stage", "intent")
    source_path = Path(journal["source_project_path"])
    target_path = Path(journal["target_project_path"])
    target_event_id = journal["target_event_id"]
    source_correction_event_id = journal["source_correction_event_id"]

    if not source_path.exists():
        journal["last_error"] = "source project not found"
        _write_journal(workspace, journal)
        return {"ok": False, "kind": "not_found", "error": "source project not found"}
    if not target_path.exists():
        journal["last_error"] = "target project not found"
        _write_journal(workspace, journal)
        return {"ok": False, "kind": "not_found", "error": "target project not found"}

    source_text = source_path.read_text(encoding="utf-8")
    target_text = target_path.read_text(encoding="utf-8")

    target_exists = _event_exists_in_project(target_text, target_event_id)
    source_correction_exists = _event_exists_in_project(source_text, source_correction_event_id)

    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y-%m-%dT%H:%M:%S+00:00")

    # Stage-dependent recovery
    if stage in ("intent",):
        if not target_exists:
            # Write target event
            target_event_lines = _build_target_event_lines(
                timestamp, target_event_id, journal["target_task_id"],
                correction_id, journal["original_event_id"],
                _extract_project_id(source_text),
                journal.get("summary", ""), journal.get("status", "in_progress"),
                journal.get("next_action", ""),
            )
            target_text = _append_event_to_timeline(target_text, target_event_lines)
            target_text = _update_task_in_work_map(
                target_text, journal["target_task_id"],
                journal.get("status", "in_progress"),
                journal.get("next_action", ""),
                target_event_id,
            )
            write_project_atomically(target_path, target_text)
            init_db(db_path)

        journal["stage"] = "target_written"
        _write_journal(workspace, journal)
        # Re-read to pick up new state, fall through to target_written

    if journal["stage"] in ("target_written",):
        source_text = source_path.read_text(encoding="utf-8")
        source_correction_exists = _event_exists_in_project(source_text, source_correction_event_id)

        if not source_correction_exists:
            source_corr_lines = _build_source_correction_event_lines(
                timestamp, source_correction_event_id, journal["source_task_id"],
                correction_id, journal["original_event_id"], target_event_id,
                journal.get("summary", ""), journal.get("status", "in_progress"),
                journal.get("next_action", ""),
            )
            source_text = _append_event_to_timeline(source_text, source_corr_lines)
            source_text = _update_task_in_work_map(
                source_text, journal["source_task_id"],
                journal.get("status", "in_progress"),
                journal.get("next_action", ""),
                source_correction_event_id,
            )
            write_project_atomically(source_path, source_text)

        journal["stage"] = "source_written"
        _write_journal(workspace, journal)

    # Verify and finalize
    final_source = source_path.read_text(encoding="utf-8")
    final_target = target_path.read_text(encoding="utf-8")
    if _event_exists_in_project(final_source, source_correction_event_id) and _event_exists_in_project(final_target, target_event_id):
        journal["stage"] = "done"
        _write_journal(workspace, journal)

    rebuild_index(db_path, [source_path, target_path])

    return {
        "ok": True,
        "stage": journal["stage"],
        "correction_id": correction_id,
    }
