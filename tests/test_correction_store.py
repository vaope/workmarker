from __future__ import annotations

import json
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


# ── helpers ────────────────────────────────────────────────────


def _project_text(project_id: str, task_id: str, event_lines: list[str]) -> str:
    return "\n".join([
        "---",
        f"project_id: {project_id}",
        f"title: {project_id}",
        "doc_kind: work_project",
        "created: 2026-07-04",
        "updated: 2026-07-04",
        "---",
        "",
        f"# {project_id}",
        "",
        "## Current Snapshot",
        "",
        "## Work Map",
        "### Item: Trust Item <!-- item:item-a -->",
        f"#### Task: Trust Task <!-- task:{task_id} -->",
        "- status: in_progress",
        "- next_action:",
        "- last_event_id:",
        "",
        "## Decisions",
        "",
        "## Attachments",
        "",
        "## Timeline",
        *event_lines,
        "",
        "## Daily / Weekly Rollups",
        "",
    ])


def _target_event() -> list[str]:
    return [
        "- 2026-07-04T12:40:00+00:00 <!-- event:corr-target-event -->",
        "  - task_id: target-task",
        "  - event_type: correction_move",
        "  - correction_id: corr-1",
        "  - source_event_id: source-event-1",
        "  - source_project_id: source-project",
        "  - summary: Corrected target summary",
        "  - status: in_progress",
        "  - next_action: Check recovery",
    ]


def _source_correction_event() -> list[str]:
    return [
        "- 2026-07-04T12:40:01+00:00 <!-- event:corr-source-event -->",
        "  - task_id: source-task",
        "  - event_type: correction",
        "  - correction_id: corr-1",
        "  - corrects_event_id: source-event-1",
        "  - target_event_id: corr-target-event",
        "  - summary: Moved to target project",
        "  - status: in_progress",
        "  - next_action:",
    ]


def _write_cross_project_fixture(
    tmp_path: Path,
    stage: str,
    target_written: bool,
    source_written: bool,
) -> dict:
    source = tmp_path / "source.md"
    target = tmp_path / "target.md"
    source_events = [
        "- 2026-07-04T12:00:00+00:00 <!-- event:source-event-1 -->",
        "  - task_id: source-task",
        "  - input: Original",
        "  - summary: Original summary",
        "  - status: in_progress",
        "  - next_action:",
    ]
    if source_written:
        source_events.extend(_source_correction_event())
    target_events = _target_event() if target_written else []
    source.write_text(_project_text("source-project", "source-task", source_events), encoding="utf-8")
    target.write_text(_project_text("target-project", "target-task", target_events), encoding="utf-8")

    journal_dir = tmp_path / ".workeventagent" / "corrections"
    journal_dir.mkdir(parents=True)
    journal = {
        "correction_id": "corr-1",
        "source_project_path": str(source),
        "source_item_id": "item-a",
        "source_task_id": "source-task",
        "original_event_id": "source-event-1",
        "target_project_path": str(target),
        "target_item_id": "item-a",
        "target_task_id": "target-task",
        "summary": "Corrected target summary",
        "status": "in_progress",
        "next_action": "Check recovery",
        "target_event_id": "corr-target-event",
        "source_correction_event_id": "corr-source-event",
        "stage": stage,
        "last_error": "",
    }
    (journal_dir / "corr-1.json").write_text(json.dumps(journal), encoding="utf-8")
    return {"source": source, "target": target, "journal": journal_dir / "corr-1.json"}


# ── crash matrix tests ─────────────────────────────────────────


def test_cross_project_intent_without_target_is_retryable(tmp_path: Path) -> None:
    from workeventagent.correction_store import resume_correction

    fixture = _write_cross_project_fixture(tmp_path, "intent", target_written=False, source_written=False)
    db = tmp_path / "index.sqlite"

    result = resume_correction(tmp_path, "corr-1", db)

    assert result["ok"] is True
    assert "corr-target-event" in fixture["target"].read_text(encoding="utf-8")
    assert "corr-source-event" in fixture["source"].read_text(encoding="utf-8")
    assert "target_event_id: corr-target-event" in fixture["source"].read_text(encoding="utf-8")


def test_cross_project_intent_with_target_already_written_advances(tmp_path: Path) -> None:
    from workeventagent.correction_store import resume_correction

    fixture = _write_cross_project_fixture(tmp_path, "intent", target_written=True, source_written=False)
    db = tmp_path / "index.sqlite"

    result = resume_correction(tmp_path, "corr-1", db)

    target_text = fixture["target"].read_text(encoding="utf-8")
    assert result["ok"] is True
    assert target_text.count("corr-target-event") == 1
    assert "corr-source-event" in fixture["source"].read_text(encoding="utf-8")


def test_cross_project_target_written_without_source_visible_and_resumable(tmp_path: Path) -> None:
    from workeventagent.correction_store import list_pending_corrections, resume_correction

    fixture = _write_cross_project_fixture(tmp_path, "target_written", target_written=True, source_written=False)
    db = tmp_path / "index.sqlite"

    pending = list_pending_corrections(tmp_path)
    result = resume_correction(tmp_path, "corr-1", db)

    assert pending[0]["correction_id"] == "corr-1"
    assert result["ok"] is True
    assert "corr-source-event" in fixture["source"].read_text(encoding="utf-8")
    assert fixture["source"].read_text(encoding="utf-8").count("corr-source-event") == 1


def test_cross_project_target_written_with_source_already_written_marks_done(tmp_path: Path) -> None:
    from workeventagent.correction_store import resume_correction

    fixture = _write_cross_project_fixture(tmp_path, "target_written", target_written=True, source_written=True)
    db = tmp_path / "index.sqlite"

    result = resume_correction(tmp_path, "corr-1", db)

    source_text = fixture["source"].read_text(encoding="utf-8")
    target_text = fixture["target"].read_text(encoding="utf-8")
    journal = json.loads(fixture["journal"].read_text(encoding="utf-8"))
    assert result["ok"] is True
    assert journal["stage"] == "done"
    assert source_text.count("corr-source-event") == 1
    assert target_text.count("corr-target-event") == 1


# ── integration: handler chain (crash → visible → resume → cleared) ──────


def test_handler_chain_crash_recovery_visible_and_resumable(tmp_path: Path) -> None:
    """Integration: after simulated crash, recoveries handler returns pending,
    resume handler recovers it, and recoveries returns empty thereafter."""
    from workeventagent.gui import handle_correction_recoveries, handle_resume_correction

    fixture = _write_cross_project_fixture(
        tmp_path, "target_written", target_written=True, source_written=False,
    )
    db = tmp_path / "index.sqlite"

    # 1. After crash, pending corrections are visible
    recoveries_res = handle_correction_recoveries({"workspace": str(tmp_path)})
    assert recoveries_res["ok"] is True
    assert len(recoveries_res["pending"]) == 1
    assert recoveries_res["pending"][0]["correction_id"] == "corr-1"
    assert recoveries_res["pending"][0]["stage"] == "target_written"

    # 2. Resume recovers the correction
    resume_res = handle_resume_correction({
        "workspace": str(tmp_path),
        "correction_id": "corr-1",
        "db_path": str(db),
    })
    assert resume_res["ok"] is True, f"resume failed: {resume_res}"
    assert "corr-source-event" in fixture["source"].read_text(encoding="utf-8")

    # 3. After recovery, no more pending
    recoveries_res2 = handle_correction_recoveries({"workspace": str(tmp_path)})
    assert recoveries_res2["ok"] is True
    assert len(recoveries_res2["pending"]) == 0
