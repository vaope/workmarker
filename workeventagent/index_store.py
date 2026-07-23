from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from workeventagent.project_schema import parse_frontmatter, schema_version
from workeventagent.work_map_store import parse_work_map


def _parse_v1_attachments(text: str) -> list[dict]:
    """Legacy v1 attachment parser for index rebuild."""
    attachments: list[dict] = []
    in_attachments = False
    attach_path_re = re.compile(r"^\s*-\s*path:\s*(.*)$")
    attach_task_re = re.compile(r"^\s*-\s*related_task_id:\s*(.*)$")
    attach_note_re = re.compile(r"^\s*-\s*note:\s*(.*)$")
    current_attachment: dict | None = None

    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "## Attachments":
            in_attachments = True
            continue
        if in_attachments and stripped.startswith("## ") and stripped != "## Attachments":
            break

        if in_attachments:
            path_match = attach_path_re.match(line)
            if path_match:
                if current_attachment is not None:
                    attachments.append(current_attachment)
                current_attachment = {"path": path_match.group(1).strip(), "task_id": "", "note": ""}
                continue

            if current_attachment is not None:
                task_match = attach_task_re.match(line)
                if task_match:
                    current_attachment["task_id"] = task_match.group(1).strip()
                    continue
                note_match = attach_note_re.match(line)
                if note_match:
                    current_attachment["note"] = note_match.group(1).strip()
                    continue

    if current_attachment is not None:
        attachments.append(current_attachment)

    return attachments


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
            conclusion TEXT NOT NULL DEFAULT '',
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
    task_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()
    }
    if "conclusion" not in task_columns:
        conn.execute(
            "ALTER TABLE tasks ADD COLUMN conclusion TEXT NOT NULL DEFAULT ''"
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
                "INSERT OR REPLACE INTO tasks "
                "(task_id, project_id, item_id, title, status, next_action, conclusion, "
                "doc_path, doc_anchor, last_event_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    task["task_id"],
                    project_id,
                    task["item_id"],
                    task["title"],
                    task["status"],
                    task["next_action"],
                    task.get("conclusion", ""),
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
    v = schema_version(text)

    tasks: list[dict] = []
    attachments_list: list[dict] = []

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
                    "conclusion": task.get("conclusion", ""),
                    "last_event_id": task.get("last_event_id", ""),
                    "doc_anchor": f"task:{task['task_id']}",
                })
    except ValueError:
        pass

    if v < 2:
        attachments_list = _parse_v1_attachments(text)

    return {
        "project_id": project_id,
        "title": title,
        "updated_at": updated_at,
        "tasks": tasks,
        "attachments": attachments_list,
    }
