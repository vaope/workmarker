from __future__ import annotations

import json
import re
from pathlib import Path

from workeventagent.project_schema import parse_frontmatter, parse_timeline_events
from workeventagent.work_map_store import parse_work_map


def build_search_documents(workspace: Path) -> list[dict]:
    """Scan workspace for all searchable content: projects, items, tasks, timeline, reports."""
    docs: list[dict] = []

    # Scan project Markdown files
    for md in sorted(workspace.glob("*.md")):
        try:
            text = md.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        frontmatter = parse_frontmatter(text)
        project_id = frontmatter.get("project_id", md.stem)
        project_title = frontmatter.get("title", project_id)

        docs.append({
            "kind": "project",
            "title": project_title,
            "snippet": "",
            "path": str(md),
            "project_id": project_id,
            "item_id": "",
            "task_id": "",
            "event_id": "",
            "timestamp": "",
        })

        # Items and Tasks from shared parser
        try:
            items = parse_work_map(text)
        except ValueError:
            items = []
        for item in items:
            docs.append({
                "kind": "item",
                "title": item["title"],
                "snippet": item.get("background", ""),
                "path": str(md),
                "project_id": project_id,
                "item_id": item["item_id"],
                "task_id": "",
                "event_id": "",
                "timestamp": "",
            })
            for task in item.get("tasks", []):
                docs.append({
                    "kind": "task",
                    "title": task["title"],
                    "snippet": " ".join(filter(None, [
                        task.get("next_action", ""),
                        task.get("conclusion", ""),
                    ])),
                    "path": str(md),
                    "project_id": project_id,
                    "item_id": item["item_id"],
                    "task_id": task["task_id"],
                    "event_id": "",
                    "timestamp": "",
                })

        # Timeline events from shared parser
        try:
            events = parse_timeline_events(text)
        except ValueError:
            events = []
        for te in events:
            docs.append({
                "kind": "timeline",
                "title": te.get("summary", ""),
                "snippet": te.get("input", ""),
                "path": str(md),
                "project_id": project_id,
                "item_id": "",
                "task_id": te.get("task_id", ""),
                "event_id": te.get("event_id", ""),
                "timestamp": te.get("timestamp", ""),
            })

    # Scan report files
    reports_dir = workspace / "reports"
    if reports_dir.exists():
        for rpt in sorted(reports_dir.rglob("*.md")):
            try:
                rpt_text = rpt.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            docs.append({
                "kind": "report",
                "title": rpt.stem,
                "snippet": rpt_text[:500],
                "path": str(rpt),
                "project_id": "",
                "item_id": "",
                "task_id": "",
                "event_id": "",
                "timestamp": "",
            })

    # Scan inbox cards
    inbox_path = workspace / ".workeventagent" / "inbox.json"
    if inbox_path.exists():
        try:
            cards = json.loads(inbox_path.read_text(encoding="utf-8"))
            for c in (cards if isinstance(cards, list) else []):
                text = c.get("text", "")
                error = c.get("error", "")
                proj_title = (c.get("selected_project") or {}).get("title", "")
                summary = ""
                proposal = c.get("proposal")
                if proposal and proposal.get("event"):
                    summary = proposal["event"].get("summary", "")
                snippet = " ".join(filter(None, [summary, proj_title, error]))
                docs.append({
                    "kind": "inbox",
                    "title": text,
                    "snippet": snippet,
                    "path": "",
                    "project_id": "",
                    "item_id": "",
                    "task_id": "",
                    "event_id": c.get("event_id", ""),
                    "timestamp": "",
                })
        except (json.JSONDecodeError, OSError):
            pass

    return docs


def search_workspace(workspace: Path, query: str, limit: int = 50) -> list[dict]:
    """Search workspace for matching documents. Uses SQLite FTS5 if available, otherwise case-insensitive substring."""
    docs = build_search_documents(workspace)
    query_lower = query.lower()

    results: list[tuple[float, dict]] = []
    for d in docs:
        fields = [d.get("title", ""), d.get("snippet", ""), d.get("project_id", ""),
                  d.get("item_id", ""), d.get("task_id", "")]
        text = " ".join(f for f in fields if f).lower()

        if query_lower in text:
            # Simple ranking: exact title match > title contains > body contains
            title_lower = d["title"].lower()
            if query_lower == title_lower:
                score = 3.0
            elif query_lower in title_lower:
                score = 2.0
            else:
                score = 1.0
            # Boost by recency if timestamp
            if d.get("timestamp"):
                score += 0.1
            results.append((score, d))

    results.sort(key=lambda x: x[0], reverse=True)
    return [r[1] for r in results[:limit]]
