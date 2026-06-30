from __future__ import annotations

import re
from pathlib import Path


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

        fm = _parse_frontmatter(text)
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


def _parse_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    fm: dict[str, str] = {}
    for line in parts[1].splitlines():
        line = line.strip()
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        fm[key.strip()] = value.strip()
    return fm


def _count_open_tasks(text: str) -> int:
    """Count tasks with status: in_progress in the Work Map section."""
    count = 0
    in_work_map = False
    status_re = re.compile(r"^-\s*status:\s*(.*)$")

    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "## Work Map":
            in_work_map = True
            continue
        if in_work_map and stripped.startswith("## ") and stripped != "## Work Map":
            break

        if in_work_map:
            m = status_re.match(line)
            if m and m.group(1).strip() == "in_progress":
                count += 1

    return count
