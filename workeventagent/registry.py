from __future__ import annotations

import re
from pathlib import Path

from workeventagent.project_schema import parse_frontmatter
from workeventagent.work_map_store import parse_work_map


def scan_workspace(workspace: Path) -> list[dict]:
    """Scan workspace for work_project Markdown files.

    File system is the source of truth — no separate registry file.
    Returns project metadata sorted by updated_at descending.
    """
    workspace = workspace.resolve()
    if not workspace.is_dir():
        return []

    projects: list[dict] = []
    for md_path in sorted(workspace.glob("*.md")):
        try:
            text = md_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        fm = parse_frontmatter(text)
        if fm.get("doc_kind") != "work_project":
            continue

        project_id = fm.get("project_id", "")
        title = fm.get("title", md_path.stem)
        updated_at = fm.get("updated", "")
        open_count = _count_open_tasks(text)

        projects.append({
            "project_id": project_id,
            "title": title,
            "path": str(md_path),
            "open_task_count": open_count,
            "updated_at": updated_at,
        })

    # Sort by updated_at descending (most recent first)
    projects.sort(key=lambda p: p["updated_at"], reverse=True)
    return projects


def _count_open_tasks(text: str) -> int:
    """Count tasks with status in_progress in the Work Map section."""
    count = 0
    try:
        items = parse_work_map(text)
    except ValueError:
        return 0
    for item in items:
        for task in item.get("tasks", []):
            if task.get("status") == "in_progress":
                count += 1
    return count
