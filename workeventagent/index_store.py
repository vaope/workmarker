from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from workeventagent.project_schema import parse_frontmatter, parse_attachment_records
from workeventagent.work_map_store import parse_work_map


def init_db(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS projects (
            project_id TEXT PRIMARY KEY,
            title TEXT NOT NULL DEFAULT '',
            doc_path TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS tasks (
            task_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL DEFAULT '',
            item_id TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT '',
            next_action TEXT NOT NULL DEFAULT '',
            doc_path TEXT NOT NULL DEFAULT '',
            doc_anchor TEXT NOT NULL DEFAULT '',
            last_event_id TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS attachments (
            path TEXT PRIMARY KEY,
            project_id TEXT NOT NULL DEFAULT '',
            task_id TEXT NOT NULL DEFAULT '',
            note TEXT NOT NULL DEFAULT ''
        );
        """
    )
    conn.commit()
    conn.close()


def rebuild_index(db_path: Path, project_paths: list[Path]) -> None:
    conn = sqlite3.connect(str(db_path))
    init_db(db_path)

    for project_path in project_paths:
        text = project_path.read_text(encoding="utf-8")
        doc = _parse_project_document(text, project_path)
        project_id = doc["project_id"]

        conn.execute("DELETE FROM projects WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM tasks WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM attachments WHERE project_id = ?", (project_id,))

        conn.execute(
            "INSERT INTO projects (project_id, title, doc_path, updated_at) VALUES (?, ?, ?, ?)",
            (project_id, doc["title"], str(project_path), doc["updated_at"]),
        )

        for task in doc["tasks"]:
            conn.execute(
                "INSERT INTO tasks (task_id, project_id, item_id, title, status, next_action, doc_path, doc_anchor, last_event_id)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    task["task_id"],
                    project_id,
                    task["item_id"],
                    task["title"],
                    task["status"],
                    task["next_action"],
                    str(project_path),
                    task["doc_anchor"],
                    task["last_event_id"],
                ),
            )

        for attachment in doc["attachments"]:
            conn.execute(
                "INSERT INTO attachments (path, project_id, task_id, note) VALUES (?, ?, ?, ?)",
                (
                    attachment["path"],
                    project_id,
                    attachment["task_id"],
                    attachment["note"],
                ),
            )

    conn.commit()
    conn.close()


def get_task(db_path: Path, task_id: str) -> dict[str, str]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
    ).fetchone()
    conn.close()
    if row is None:
        raise KeyError(f"task not found: {task_id}")
    return dict(row)


def _parse_project_document(text: str, project_path: Path) -> dict:
    frontmatter = parse_frontmatter(text)
    project_id = frontmatter.get("project_id", "")
    title = frontmatter.get("title", "")
    updated_at = frontmatter.get("updated", "")

    tasks: list[dict] = []
    try:
        items = parse_work_map(text)
        for item in items:
            for task in item.get("tasks", []):
                tasks.append({
                    "task_id": task["task_id"],
                    "item_id": item["item_id"],
                    "title": task["title"],
                    "status": task.get("status", ""),
                    "next_action": task.get("next_action", ""),
                    "last_event_id": task.get("last_event_id", ""),
                    "doc_anchor": f"task:{task['task_id']}",
                })
    except ValueError:
        pass

    attachments_list: list[dict] = []
    try:
        records = parse_attachment_records(text)
        for rec in records:
            attachments_list.append({
                "path": rec.get("path", ""),
                "task_id": rec.get("related_task_id", ""),
                "note": rec.get("note", ""),
            })
    except ValueError:
        pass

    return {
        "project_id": project_id,
        "title": title,
        "updated_at": updated_at,
        "tasks": tasks,
        "attachments": attachments_list,
    }
