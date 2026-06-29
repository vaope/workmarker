from __future__ import annotations

import re
import sqlite3
from pathlib import Path


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
    frontmatter = _parse_frontmatter(text)
    project_id = frontmatter.get("project_id", "")
    title = frontmatter.get("title", "")
    updated_at = frontmatter.get("updated", "")

    tasks: list[dict] = []
    attachments: list[dict] = []

    current_item_id = ""
    in_work_map = False
    in_attachments = False

    task_re = re.compile(r"^####\s+Task:\s+(.+?)\s*<!--\s*task:(.+?)\s*-->\s*$")
    item_re = re.compile(r"^###\s+Item:\s+(.+?)\s*<!--\s*item:(.+?)\s*-->\s*$")
    status_re = re.compile(r"^-\s*status:\s*(.*)$")
    next_action_re = re.compile(r"^-\s*next_action:\s*(.*)$")
    last_event_re = re.compile(r"^-\s*last_event_id:\s*(.*)$")
    attach_path_re = re.compile(r"^-\s*path:\s*(.*)$")
    attach_task_re = re.compile(r"^-\s*related_task_id:\s*(.*)$")
    attach_note_re = re.compile(r"^-\s*note:\s*(.*)$")

    current_task: dict | None = None
    current_attachment: dict | None = None

    for line in text.splitlines():
        if line.strip() == "## Work Map":
            in_work_map = True
            in_attachments = False
            continue
        if line.strip() == "## Attachments":
            in_work_map = False
            in_attachments = True
            continue
        if line.strip().startswith("## ") and line.strip() not in ("## Work Map", "## Attachments"):
            in_work_map = False
            in_attachments = False

        if in_work_map:
            item_match = item_re.match(line)
            if item_match:
                current_item_id = item_match.group(2).strip()
                current_task = None
                continue

            task_match = task_re.match(line)
            if task_match:
                if current_task is not None:
                    tasks.append(current_task)
                current_task = {
                    "task_id": task_match.group(2).strip(),
                    "item_id": current_item_id,
                    "title": task_match.group(1).strip(),
                    "status": "",
                    "next_action": "",
                    "last_event_id": "",
                    "doc_anchor": f"task:{task_match.group(2).strip()}",
                }
                continue

            if current_task is not None:
                status_match = status_re.match(line)
                if status_match:
                    current_task["status"] = status_match.group(1).strip()
                    continue
                next_match = next_action_re.match(line)
                if next_match:
                    current_task["next_action"] = next_match.group(1).strip()
                    continue
                event_match = last_event_re.match(line)
                if event_match:
                    current_task["last_event_id"] = event_match.group(1).strip()
                    continue

        if in_attachments:
            path_match = attach_path_re.match(line)
            if path_match:
                if current_attachment is not None:
                    attachments.append(current_attachment)
                current_attachment = {
                    "path": path_match.group(1).strip(),
                    "task_id": "",
                    "note": "",
                }
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

    if current_task is not None:
        tasks.append(current_task)
    if current_attachment is not None:
        attachments.append(current_attachment)

    return {
        "project_id": project_id,
        "title": title,
        "updated_at": updated_at,
        "tasks": tasks,
        "attachments": attachments,
    }


def _parse_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    fm = {}
    for line in parts[1].splitlines():
        line = line.strip()
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        fm[key.strip()] = value.strip()
    return fm
