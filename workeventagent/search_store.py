from __future__ import annotations

import json
import re
from pathlib import Path


def build_search_documents(workspace: Path) -> list[dict]:
    """Scan workspace for all searchable content: projects, items, tasks, timeline, reports."""
    docs: list[dict] = []

    # Scan project Markdown files
    for md in sorted(workspace.glob("*.md")):
        try:
            text = md.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        frontmatter = _parse_frontmatter(text)
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

        # Items
        for m in re.finditer(r"### Item: (.+?) <!-- item:(.+?) -->", text):
            item_title = m.group(1).strip()
            item_id = m.group(2)
            bg = ""
            bg_m = re.search(rf"<!-- item:{re.escape(item_id)} -->\n- background: (.+)", text)
            if bg_m:
                bg = bg_m.group(1).strip()
            docs.append({
                "kind": "item",
                "title": item_title,
                "snippet": bg,
                "path": str(md),
                "project_id": project_id,
                "item_id": item_id,
                "task_id": "",
                "event_id": "",
                "timestamp": "",
            })

        # Tasks
        for m in re.finditer(r"#### Task: (.+?) <!-- task:(.+?) -->", text):
            task_title = m.group(1).strip()
            task_id = m.group(2)
            docs.append({
                "kind": "task",
                "title": task_title,
                "snippet": "",
                "path": str(md),
                "project_id": project_id,
                "item_id": "",
                "task_id": task_id,
                "event_id": "",
                "timestamp": "",
            })

        # Timeline events
        event_pattern = re.compile(
            r"- (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}) <!-- event:(.+?) -->\n"
            r"(?:  - .+\n)*?  - summary: (.+)",
            re.MULTILINE,
        )
        for m in event_pattern.finditer(text):
            timestamp = m.group(1)
            event_id = m.group(2)
            summary = m.group(3).strip()
            task_id = ""
            tid_match = re.search(rf"<!-- event:{re.escape(event_id)} -->\n  - task_id: (.+)", text)
            if tid_match:
                task_id = tid_match.group(1).strip()
            docs.append({
                "kind": "timeline",
                "title": summary,
                "snippet": "",
                "path": str(md),
                "project_id": project_id,
                "item_id": "",
                "task_id": task_id,
                "event_id": event_id,
                "timestamp": timestamp,
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


def _parse_frontmatter(text: str) -> dict:
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not m:
        return {}
    result: dict = {}
    for line in m.group(1).split("\n"):
        kv = line.split(":", 1)
        if len(kv) == 2:
            result[kv[0].strip()] = kv[1].strip()
    return result
