from pathlib import Path

from workeventagent.index_store import get_task
from workeventagent.task_completion import complete_task
from workeventagent.work_map_store import parse_work_map


PROJECT = """---
project_id: completion
title: Completion
doc_kind: work_project
schema_version: 2
status: active
phase: build
created: 2026-07-23
updated: 2026-07-23
---
## 工作地图 <!-- section:work-map -->
### 工作项：Cache <!-- item:cache -->

Validate cache behavior.

#### [ ] 任务：Verify cache <!-- task:verify-cache -->
- 下一步：Run edge cases.
<!-- task-meta:last_event_id=event-a -->

### 工作项：Reporting <!-- item:reporting -->

#### [ ] 任务：Write report <!-- task:write-report -->
- 下一步：
<!-- task-meta:last_event_id= -->
## 事件证据 <!-- section:timeline -->

- immutable timeline
"""


def setup_project(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "completion.md"
    project.write_text(PROJECT, encoding="utf-8")
    return project, tmp_path / "index.sqlite"


def test_complete_without_follow_up_is_one_current_state_write(tmp_path: Path) -> None:
    project, db = setup_project(tmp_path)
    result = complete_task(project, db, "verify-cache", "Cache behavior is stable.")
    text = project.read_text(encoding="utf-8")
    task = parse_work_map(text)[0]["tasks"][0]
    assert result["ok"] is True
    assert result["new_task"] is None
    assert task["status"] == "done"
    assert task["conclusion"] == "Cache behavior is stable."
    assert task["next_action"] == "Run edge cases."
    assert task["last_event_id"] == "event-a"
    assert text.split("## 事件证据", 1)[1] == PROJECT.split("## 事件证据", 1)[1]
    assert get_task(db, "verify-cache")["status"] == "done"


def test_complete_with_follow_up_inserts_after_current_task(tmp_path: Path) -> None:
    project, db = setup_project(tmp_path)
    result = complete_task(
        project,
        db,
        "verify-cache",
        "Cache behavior is stable.",
        "Document cache behavior",
    )
    items = parse_work_map(project.read_text(encoding="utf-8"))
    assert [task["title"] for task in items[0]["tasks"]] == [
        "Verify cache",
        "Document cache behavior",
    ]
    assert items[1]["tasks"][0]["task_id"] == "write-report"
    assert result["new_task"]["item_id"] == "cache"
    assert get_task(db, result["new_task"]["task_id"])["status"] == "in_progress"


def test_invalid_completion_leaves_file_unchanged(tmp_path: Path) -> None:
    project, db = setup_project(tmp_path)
    before = project.read_bytes()
    result = complete_task(project, db, "verify-cache", "   ")
    assert result["ok"] is False
    assert result["kind"] == "invalid_input"
    assert project.read_bytes() == before
    assert not db.exists()


def test_structural_follow_up_title_is_rejected_without_writes(tmp_path: Path) -> None:
    project, db = setup_project(tmp_path)
    before = project.read_bytes()

    result = complete_task(
        project,
        db,
        "verify-cache",
        "Cache behavior is stable.",
        "Follow <!-- task:phantom -->",
    )

    assert result["ok"] is False
    assert result["kind"] == "invalid_input"
    assert project.read_bytes() == before
    assert not db.exists()


def test_already_done_completion_does_not_duplicate_follow_up(tmp_path: Path) -> None:
    project, db = setup_project(tmp_path)
    first = complete_task(project, db, "verify-cache", "Stable.", "Document cache")
    before = project.read_bytes()
    second = complete_task(project, db, "verify-cache", "Stable.", "Document cache")
    assert first["ok"] is True
    assert second["ok"] is False
    assert second["kind"] == "invalid_state"
    assert project.read_bytes() == before
