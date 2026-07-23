from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from workeventagent.ids import make_unique_stable_id
from workeventagent.index_store import init_db, rebuild_index
from workeventagent.markdown_store import write_project_atomically
from workeventagent.work_map_store import (
    complete_task_block,
    insert_task_after,
    parse_work_map,
)


def _one_line(value: str) -> str:
    return " ".join(str(value).split()).strip()


def _bump_updated(text: str) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return re.sub(r"(?m)^(updated:\s*).*$", rf"\g<1>{today}", text, count=1)


def complete_task(
    project_path: Path,
    db_path: Path,
    task_id: str,
    conclusion: str,
    next_task_title: str = "",
) -> dict:
    normalized_conclusion = _one_line(conclusion)
    normalized_next_title = _one_line(next_task_title)
    if not normalized_conclusion:
        return {
            "ok": False,
            "kind": "invalid_input",
            "error": "completion conclusion is required",
        }
    if "<!--" in normalized_next_title or "-->" in normalized_next_title:
        return {
            "ok": False,
            "kind": "invalid_input",
            "error": "follow-up task title contains reserved Markdown structure",
        }

    original = project_path.read_text(encoding="utf-8")
    try:
        items = parse_work_map(original, strict=True)
    except ValueError as exc:
        return {"ok": False, "kind": "invalid_project", "error": str(exc)}

    parent_item = None
    target_task = None
    existing_task_ids: set[str] = set()
    for item in items:
        for task in item.get("tasks", []):
            existing_task_ids.add(task["task_id"])
            if task["task_id"] == task_id:
                parent_item = item
                target_task = task

    if target_task is None or parent_item is None:
        return {"ok": False, "kind": "not_found", "error": f"task not found: {task_id}"}
    if target_task["status"] == "done":
        return {"ok": False, "kind": "invalid_state", "error": "task is already done"}

    try:
        updated = complete_task_block(original, task_id, normalized_conclusion)
        new_task = None
        if normalized_next_title:
            next_task_id = make_unique_stable_id(normalized_next_title, existing_task_ids)
            task_record = {
                "task_id": next_task_id,
                "title": normalized_next_title,
                "status": "in_progress",
                "next_action": "",
                "conclusion": "",
                "last_event_id": "",
            }
            updated = insert_task_after(updated, task_id, task_record)
            new_task = {
                "item_id": parent_item["item_id"],
                "task_id": next_task_id,
                "title": normalized_next_title,
            }
        updated = _bump_updated(updated)
    except ValueError as exc:
        return {"ok": False, "kind": "invalid_project", "error": str(exc)}

    write_project_atomically(project_path, updated)
    init_db(db_path)
    rebuild_index(db_path, [project_path])
    return {
        "ok": True,
        "task_id": task_id,
        "status": "done",
        "conclusion": normalized_conclusion,
        "new_task": new_task,
    }
