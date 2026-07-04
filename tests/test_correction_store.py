from __future__ import annotations

from pathlib import Path

from workeventagent.correction_store import correct_event_same_project

PROJECT_WITH_ONE_EVENT = """---
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


def test_same_project_correction_appends_event_and_preserves_original(tmp_path: Path) -> None:
    project = tmp_path / "project.md"
    project.write_text(PROJECT_WITH_ONE_EVENT, encoding="utf-8")
    db = tmp_path / "index.sqlite"

    result = correct_event_same_project(project, db, {
        "original_event_id": "event-1",
        "reason": "Wrong summary",
        "summary": "Corrected summary",
        "status": "done",
        "next_action": "",
        "target_task_id": "task-a",
    })

    text = project.read_text(encoding="utf-8")
    assert result["ok"] is True
    assert "<!-- event:event-1 -->" in text
    assert "event_type: correction" in text
    assert "corrects_event_id: event-1" in text
    assert "summary: Corrected summary" in text
