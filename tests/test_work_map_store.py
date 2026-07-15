import pytest

from workeventagent.work_map_store import parse_work_map, update_task_field, update_task_state


V1_MAP = """## Work Map
### Item: Capture <!-- item:capture -->
- background: Durable intake.
#### Task: Persist card <!-- task:persist-card -->
- status: in_progress
- next_action: Add retry.
- last_event_id: event-a
## Timeline
"""

V2_MAP = """---
project_id: demo
doc_kind: work_project
schema_version: 2
status: active
phase: build
---
## 工作地图 <!-- section:work-map -->
### 工作项：Capture <!-- item:capture -->

Durable intake.

#### [ ] 任务：Persist card <!-- task:persist-card -->
- 下一步：Add retry.
<!-- task-meta:last_event_id=event-a -->
## 事件证据 <!-- section:timeline -->
"""

V2_MULTI_ITEM_MAP = """---
project_id: multi
doc_kind: work_project
schema_version: 2
status: active
phase: build
---
## 工作地图 <!-- section:work-map -->
### 工作项：Alpha <!-- item:alpha -->

Alpha background.

#### [ ] 任务：A-task <!-- task:a-task -->
- 下一步：Do A stuff.
<!-- task-meta:last_event_id=ev-a -->

### 工作项：Beta <!-- item:beta -->

Beta background.

#### [ ] 任务：B-task <!-- task:b-task -->
- 下一步：Do B stuff.
<!-- task-meta:last_event_id=ev-b -->
## 事件证据 <!-- section:timeline -->
"""


def test_v1_and_v2_parse_to_the_same_typed_state() -> None:
    assert parse_work_map(V1_MAP) == parse_work_map(V2_MAP) == [{
        "item_id": "capture",
        "title": "Capture",
        "background": "Durable intake.",
        "tasks": [{
            "task_id": "persist-card",
            "title": "Persist card",
            "status": "in_progress",
            "next_action": "Add retry.",
            "last_event_id": "event-a",
        }],
    }]


def test_v2_status_update_changes_checkbox_only() -> None:
    updated = update_task_field(V2_MAP, "persist-card", "status", "done", "2026-07-13")
    assert "#### [x] 任务：Persist card <!-- task:persist-card -->" in updated
    assert "Add retry." in updated
    assert "event-a" in updated


def test_noncanonical_task_is_rejected_instead_of_guessed() -> None:
    broken = V1_MAP.replace("- status: in_progress\n", "")
    with pytest.raises(ValueError, match="canonical status"):
        parse_work_map(broken, strict=True)


def test_multi_item_update_task_field_does_not_delete_next_item() -> None:
    """B3 regression: update_task_field must not duplicate the heading or delete the next item."""
    updated = update_task_field(V2_MULTI_ITEM_MAP, "a-task", "status", "done", "2026-07-13")
    # Should only have ONE heading for a-task
    assert updated.count("<!-- task:a-task -->") == 1
    # Beta must still exist
    assert "<!-- item:beta -->" in updated
    assert "<!-- task:b-task -->" in updated
    # Heading should show [x]
    assert "#### [x] 任务：A-task <!-- task:a-task -->" in updated


def test_multi_item_update_task_state_does_not_delete_next_item() -> None:
    """B3 regression: update_task_state must preserve sibling items."""
    updated = update_task_state(V2_MULTI_ITEM_MAP, "a-task", "done", "Retry A.", "ev-a2")
    assert updated.count("<!-- task:a-task -->") == 1
    assert "<!-- item:beta -->" in updated
    assert "<!-- task:b-task -->" in updated
    assert "#### [x] 任务：A-task <!-- task:a-task -->" in updated
    assert "Retry A." in updated
