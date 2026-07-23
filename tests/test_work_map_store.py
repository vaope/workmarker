import pytest

from workeventagent.project_schema import find_section
from workeventagent.work_map_store import (
    complete_task_block,
    insert_task_after,
    parse_work_map,
    update_task_field,
    update_task_state,
)


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

V1_WITH_CONCLUSION = V1_MAP.replace(
    "- next_action: Add retry.\n",
    "- next_action: Add retry.\n- conclusion: Persistence is stable.\n",
)

V2_WITH_CONCLUSION = V2_MAP.replace(
    "- 下一步：Add retry.\n",
    "- 下一步：Add retry.\n- 结论：Persistence is stable.\n",
)


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
            "conclusion": "",
            "last_event_id": "event-a",
        }],
    }]


def test_v1_and_v2_parse_conclusion_to_the_same_state() -> None:
    v1 = parse_work_map(V1_WITH_CONCLUSION)[0]["tasks"][0]
    v2 = parse_work_map(V2_WITH_CONCLUSION)[0]["tasks"][0]
    assert v1["conclusion"] == v2["conclusion"] == "Persistence is stable."


@pytest.mark.parametrize("source", [V1_MAP, V2_MAP])
def test_missing_conclusion_is_backward_compatible(source: str) -> None:
    assert parse_work_map(source)[0]["tasks"][0]["conclusion"] == ""


@pytest.mark.parametrize("source", [V1_MAP, V2_MAP])
def test_complete_task_block_preserves_resume_and_event_fields(source: str) -> None:
    updated = complete_task_block(source, "persist-card", "Persistence is stable.")
    task = parse_work_map(updated)[0]["tasks"][0]
    assert task == {
        "task_id": "persist-card",
        "title": "Persist card",
        "status": "done",
        "next_action": "Add retry.",
        "conclusion": "Persistence is stable.",
        "last_event_id": "event-a",
    }


def test_insert_task_after_keeps_same_item_and_following_item() -> None:
    new_task = {
        "task_id": "follow-up",
        "title": "Follow up",
        "status": "in_progress",
        "next_action": "",
        "conclusion": "",
        "last_event_id": "",
    }
    updated = insert_task_after(V2_MULTI_ITEM_MAP, "a-task", new_task)
    alpha = parse_work_map(updated)[0]
    assert [task["task_id"] for task in alpha["tasks"]] == ["a-task", "follow-up"]
    assert "<!-- item:beta -->" in updated
    assert "<!-- task:b-task -->" in updated


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


def test_v2_update_task_field_preserves_following_section_heading() -> None:
    updated = update_task_field(V2_MAP, "persist-card", "status", "done", "2026-07-13")
    assert "-->## " not in updated
    assert "<!-- section:timeline -->" in find_section(updated, "timeline").heading


def test_v2_update_task_state_preserves_following_section_heading() -> None:
    updated = update_task_state(V2_MAP, "persist-card", "done", "Ship fix.", "event-b")
    assert "-->## " not in updated
    assert "<!-- section:timeline -->" in find_section(updated, "timeline").heading


@pytest.mark.parametrize(
    ("source", "mutate"),
    [
        (
            V1_MAP.replace("- last_event_id: event-a\n", "- last_event_id: event-a\nKeep this operator note.\n"),
            lambda text: update_task_field(text, "persist-card", "status", "done", "2026-07-13"),
        ),
        (
            V1_MAP.replace("- last_event_id: event-a\n", "- last_event_id: event-a\nKeep this operator note.\n"),
            lambda text: update_task_state(text, "persist-card", "done", "Ship fix.", "event-b"),
        ),
        (
            V2_MAP.replace(
                "<!-- task-meta:last_event_id=event-a -->\n",
                "<!-- task-meta:last_event_id=event-a -->\nKeep this operator note.\n",
            ),
            lambda text: update_task_field(text, "persist-card", "status", "done", "2026-07-13"),
        ),
        (
            V2_MAP.replace(
                "<!-- task-meta:last_event_id=event-a -->\n",
                "<!-- task-meta:last_event_id=event-a -->\nKeep this operator note.\n",
            ),
            lambda text: update_task_state(text, "persist-card", "done", "Ship fix.", "event-b"),
        ),
    ],
)
def test_task_mutations_preserve_non_control_prose_byte_for_byte(source, mutate) -> None:
    updated = mutate(source)
    assert "\nKeep this operator note.\n## " in updated


@pytest.mark.parametrize("source", [V1_MAP, V2_MAP])
def test_repeated_task_updates_do_not_accumulate_blank_lines_before_heading(source: str) -> None:
    original_prefix = source[:source.index("####")]

    updated_once = update_task_state(source, "persist-card", "done", "Ship fix.", "event-b")
    updated_twice = update_task_state(updated_once, "persist-card", "done", "Ship fix.", "event-b")

    assert updated_once[:updated_once.index("####")] == original_prefix
    assert updated_twice[:updated_twice.index("####")] == original_prefix
    assert updated_twice == updated_once
