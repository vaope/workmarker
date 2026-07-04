"""Correction workflow: append-only corrections for work event archive."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from workeventagent.ids import make_event_id
from workeventagent.index_store import init_db, rebuild_index
from workeventagent.markdown_store import write_project_atomically


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
    correction_event_id = now.strftime("%Y%m%d-%H%M%S") + "-correction"

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

    # Append correction event to ## Timeline section
    timeline_match = re.search(r"## Timeline\n", original_text)
    if not timeline_match:
        return {"ok": False, "kind": "invalid_doc", "error": "timeline section not found"}

    insert_at = timeline_match.end()
    new_text = original_text[:insert_at] + correction_block + "\n\n" + original_text[insert_at:]

    # Update target task in Work Map
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
