"""WorkEventAgent GUI backend — JSON-in/JSON-out commands for Electron frontend.

Contract: docs/designs/F001-client-architecture.md §3.
Entry: python -m workeventagent.gui <command>
  stdin: UTF-8 JSON request
  stdout: UTF-8 JSON response ({\"ok\":bool,...})
  exit 0 on business failure; exit non-zero only on crash.
No input() — zero interaction. Debug info → stderr.
"""
from __future__ import annotations

import json as _json
import re
import shutil
import sys
import traceback
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from workeventagent.ids import make_event_id, make_stable_id, make_unique_stable_id
from workeventagent.index_store import init_db, rebuild_index
from workeventagent.markdown_store import ProjectDocument, write_project_atomically
from workeventagent.models import ArchiveProposal, TargetRef, TimelineEvent
from workeventagent.opencode_runner import OpencodeRunnerError, parse_archivist_output, run_archivist
from workeventagent.registry import scan_workspace


def main() -> None:
    try:
        _main_impl()
    except Exception:
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)


def _main_impl() -> None:
    if len(sys.argv) < 2:
        _respond({"ok": False, "kind": "usage", "error": "command required"})
        return

    command = sys.argv[1]
    try:
        raw = sys.stdin.read()
    except (OSError, UnicodeDecodeError) as exc:
        _respond({"ok": False, "kind": "io_error", "error": str(exc)})
        return

    try:
        request = _json.loads(raw) if raw.strip() else {}
    except _json.JSONDecodeError:
        _respond({"ok": False, "kind": "invalid_input", "error": "stdin is not valid JSON"})
        return

    handlers = {
        "propose": handle_propose,
        "commit": handle_commit,
        "projects": handle_projects,
        "tasks": handle_tasks,
        "timeline": handle_timeline,
        "init": handle_init,
    }
    handler = handlers.get(command)
    if handler is None:
        _respond({"ok": False, "kind": "unknown_command", "error": f"unknown command: {command}"})
        return

    try:
        result = handler(request)
        _respond(result)
    except OpencodeRunnerError as exc:
        _respond({"ok": False, "kind": "opencode_error", "error": str(exc)})
    except Exception as exc:
        _respond({"ok": False, "kind": "internal_error", "error": str(exc)})


def _respond(data: dict) -> None:
    sys.stdout.write(_json.dumps(data, ensure_ascii=False))
    sys.stdout.flush()


# ── propose ──────────────────────────────────────────────

def handle_propose(request: dict) -> dict:
    text = request["text"]
    project_path = Path(request["project_path"])
    attachments = request.get("attachments", [])

    prompt = f"Archive this update: {text}"
    if attachments:
        paths_str = ", ".join(str(a) for a in attachments)
        prompt += f"\n\nAttachments: {paths_str}"

    raw = run_archivist(prompt, project_path)

    doc_text = project_path.read_text(encoding="utf-8")
    existing_event_ids = _collect_existing_event_ids(doc_text)
    tentative_task_id = _quick_extract_task_id(raw)
    now = datetime.now(timezone.utc)
    event_id = make_event_id(now, tentative_task_id, existing_event_ids)

    proposal = parse_archivist_output(raw, event_id)

    # Anti-collision for new_task
    if proposal.target.new_task:
        existing_task_ids = _collect_existing_task_ids(doc_text)
        unique_task_id = make_unique_stable_id(proposal.target.task_title, existing_task_ids)
        if unique_task_id != proposal.target.task_id:
            new_event_id = make_event_id(now, unique_task_id, existing_event_ids)
            new_target = replace(proposal.target, task_id=unique_task_id)
            new_event = replace(proposal.event, event_id=new_event_id, task_id=unique_task_id)
            proposal = replace(proposal, target=new_target, event=new_event)

    return {
        "ok": True,
        "proposal": {
            "target": {
                "project_id": proposal.target.project_id,
                "item_id": proposal.target.item_id,
                "task_id": proposal.target.task_id,
                "task_title": proposal.target.task_title,
                "new_item": proposal.target.new_item,
                "new_task": proposal.target.new_task,
            },
            "confidence": proposal.confidence,
            "reason": proposal.reason,
            "event": {
                "event_id": proposal.event.event_id,
                "task_id": proposal.event.task_id,
                "input_text": proposal.event.input_text,
                "summary": proposal.event.summary,
                "status": proposal.event.status,
                "next_action": proposal.event.next_action,
            },
            "attachment_paths": list(proposal.attachment_paths),
        },
        "low_confidence": proposal.confidence < 0.7,
    }


# ── commit ───────────────────────────────────────────────

def handle_commit(request: dict) -> dict:
    proposal_data = request["proposal"]
    project_path = Path(request["project_path"])
    db_path = Path(request["db_path"])
    pending_attachments = request.get("pending_attachments", [])

    # 1. Copy attachments from temp to project attachments dir
    archived_attachments: list[str] = []
    event = proposal_data["event"]
    task_id = event["task_id"]
    event_id = event["event_id"]
    event_ts = _event_id_timestamp(event_id)

    project_dir = project_path.parent

    if pending_attachments:
        dest_dir = project_dir / "attachments" / task_id
        dest_dir.mkdir(parents=True, exist_ok=True)

        for idx, pa in enumerate(pending_attachments):
            temp_path = Path(pa["temp_path"])
            filename = pa.get("filename", temp_path.name)
            ext = Path(filename).suffix
            dest_name = f"{event_ts}-{idx}{ext}"
            dest_path = dest_dir / dest_name
            shutil.copy2(temp_path, dest_path)
            try:
                rel_path = dest_path.relative_to(project_dir).as_posix()
            except ValueError:
                rel_path = dest_path.as_posix()
            archived_attachments.append(rel_path)

    # 2. Override attachment_paths with copied paths
    proposal_data["attachment_paths"] = list(archived_attachments)

    # 3. Reconstruct proposal object
    proposal = _dict_to_proposal(proposal_data)

    # 4. Write Markdown
    doc_text = project_path.read_text(encoding="utf-8")
    now = datetime.now(timezone.utc)
    now_str = now.strftime("%Y-%m-%d")

    if proposal.target.new_task:
        doc = ProjectDocument.from_text(doc_text)
        inserted = doc.insert_new_task(proposal)
        doc2 = ProjectDocument.from_text(inserted)
        final = doc2.apply_proposal(proposal, now_str)
    else:
        doc = ProjectDocument.from_text(doc_text)
        final = doc.apply_proposal(proposal, now_str)

    final = ProjectDocument.append_attachments(final, proposal, now)
    write_project_atomically(project_path, final)

    # 5. Rebuild SQLite
    init_db(db_path)
    rebuild_index(db_path, [project_path])

    return {
        "ok": True,
        "written_path": str(project_path),
        "archived_attachments": archived_attachments,
        "task_id": task_id,
    }


def _event_id_timestamp(event_id: str) -> str:
    """Extract YYYYMMDD-HHMMSSmmm prefix from event_id like '20260701-153000123-kv-cache-blockers'.

    Format: YYYYMMDD-HHMMSSmmm-<task-id>. Take the first two dash-separated parts.
    """
    parts = event_id.split("-")
    if len(parts) >= 2:
        return f"{parts[0]}-{parts[1]}"
    return event_id


# ── projects ─────────────────────────────────────────────

def handle_projects(request: dict) -> dict:
    workspace = Path(request["workspace"])
    projects = scan_workspace(workspace)
    return {"ok": True, "projects": projects}


# ── tasks ────────────────────────────────────────────────

def handle_tasks(request: dict) -> dict:
    project_path = Path(request["project_path"])
    text = project_path.read_text(encoding="utf-8")

    fm = _parse_frontmatter(text)
    project_id = fm.get("project_id", "")
    title = fm.get("title", "")

    work_map_tasks = _parse_work_map_tasks(text)
    timeline_events = _parse_timeline_events(text)

    # Build task_id → latest timestamp map
    task_updated: dict[str, str] = {}
    for te in timeline_events:
        tid = te.get("task_id", "")
        ts = te.get("timestamp", "")
        if tid and ts and tid not in task_updated:
            task_updated[tid] = ts

    # Group tasks by item
    items_map: dict[str, dict] = {}
    for wt in work_map_tasks:
        item_id = wt["item_id"]
        if item_id not in items_map:
            items_map[item_id] = {"item_id": item_id, "title": wt.get("item_title", ""), "tasks": []}
        items_map[item_id]["tasks"].append({
            "task_id": wt["task_id"],
            "title": wt["title"],
            "status": wt["status"],
            "next_action": wt["next_action"],
            "last_event_id": wt["last_event_id"],
            "updated_at": task_updated.get(wt["task_id"], ""),
        })

    # Preserve original item order from Work Map
    item_order: list[str] = []
    for wt in work_map_tasks:
        if wt["item_id"] not in item_order:
            item_order.append(wt["item_id"])

    items = [items_map[iid] for iid in item_order if iid in items_map]

    return {"ok": True, "project_id": project_id, "title": title, "items": items}


# ── timeline ─────────────────────────────────────────────

def handle_timeline(request: dict) -> dict:
    project_path = Path(request["project_path"])
    text = project_path.read_text(encoding="utf-8")

    events = _parse_timeline_events(text)
    work_map_tasks = _parse_work_map_tasks(text)
    attachment_task_ids = _parse_attachments_task_ids(text)

    # Build task_id → {item_id, task_title} map
    task_info: dict[str, dict] = {}
    for wt in work_map_tasks:
        task_info[wt["task_id"]] = {"item_id": wt["item_id"], "task_title": wt["title"]}

    result_events: list[dict] = []
    for te in reversed(events):  # reverse chronological
        tid = te.get("task_id", "")
        info = task_info.get(tid, {})
        result_events.append({
            "timestamp": te.get("timestamp", ""),
            "event_id": te.get("event_id", ""),
            "task_id": tid,
            "item_id": info.get("item_id", ""),
            "task_title": info.get("task_title", ""),
            "summary": te.get("summary", ""),
            "status": te.get("status", ""),
            "next_action": te.get("next_action", ""),
            "input": te.get("input", ""),
            "has_attachment": tid in attachment_task_ids,
        })

    return {"ok": True, "events": result_events}


# ── init ─────────────────────────────────────────────────

def handle_init(request: dict) -> dict:
    workspace = Path(request["workspace"])
    title = request["title"]
    project_id = request.get("project_id", "") or make_stable_id(title)
    items_spec = request.get("items", [])
    db_path = Path(request["db_path"])

    project_path = workspace / f"{project_id}.md"
    if project_path.exists():
        return {"ok": False, "kind": "exists", "error": f"project already exists: {project_path}"}

    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")

    markdown = _generate_init_markdown(project_id, title, date_str, items_spec)
    workspace.mkdir(parents=True, exist_ok=True)
    project_path.write_text(markdown, encoding="utf-8")

    # Create attachments directory
    (workspace / "attachments").mkdir(parents=True, exist_ok=True)

    # Initialize SQLite
    init_db(db_path)
    rebuild_index(db_path, [project_path])

    # Verify: read back and confirm it parses
    _text = project_path.read_text(encoding="utf-8")
    _fm = _parse_frontmatter(_text)
    if _fm.get("project_id") != project_id:
        return {"ok": False, "kind": "verify_failed", "error": "written project failed verify read-back"}

    return {"ok": True, "project_path": str(project_path), "project_id": project_id}


def _generate_init_markdown(
    project_id: str, title: str, date_str: str, items_spec: list[dict]
) -> str:
    lines: list[str] = []
    lines.append("---")
    lines.append(f"project_id: {project_id}")
    lines.append(f"title: {title}")
    lines.append("doc_kind: work_project")
    lines.append(f"created: {date_str}")
    lines.append(f"updated: {date_str}")
    lines.append("---")
    lines.append("")
    lines.append(f"# {title}")
    lines.append("")
    lines.append("## Current Snapshot")
    lines.append("")
    lines.append("")
    lines.append("## Work Map")
    lines.append("")

    for item_spec in items_spec:
        item_title = item_spec.get("title", "")
        item_id = make_stable_id(item_title) if item_title else "untitled"
        lines.append(f"### Item: {item_title} <!-- item:{item_id} -->")
        lines.append("")
        for task_title in item_spec.get("tasks", []):
            task_id = make_stable_id(task_title) if task_title else "untitled"
            lines.append(f"#### Task: {task_title} <!-- task:{task_id} -->")
            lines.append("- status: in_progress")
            lines.append("- next_action: ")
            lines.append(f"- last_event_id: ")
            lines.append("")
        if not item_spec.get("tasks"):
            lines.append("")

    lines.append("## Decisions")
    lines.append("")
    lines.append("")
    lines.append("## Attachments")
    lines.append("")
    lines.append("")
    lines.append("## Timeline")
    lines.append("")
    lines.append("")
    lines.append("## Daily / Weekly Rollups")
    lines.append("")
    lines.append("")

    return "\n".join(lines)


# ── Timeline parser (new capability) ────────────────────

def _parse_timeline_events(text: str) -> list[dict]:
    """Parse ## Timeline section into list of event dicts.

    Format per WORKLOG_SCHEMA.md:
        - 2026-06-29T15:30:00.123+08:00 <!-- event:20260629-153000123-kv-cache-blockers -->
          - task_id: kv-cache-blockers
          - input: ...
          - summary: ...
          - status: in_progress
          - next_action: ...
    """
    events: list[dict] = []
    in_timeline = False
    current_event: dict | None = None
    # Track parentages: event lines start with "- ", sub-items with "  - "
    _event_line_re = re.compile(r"^- (\S+)\s*<!--\s*event:(.+?)\s*-->")
    _sub_kv_re = re.compile(r"^  - ([a-z_]+):\s*(.*)")

    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "## Timeline":
            in_timeline = True
            continue
        if in_timeline and stripped.startswith("## ") and stripped != "## Timeline":
            if current_event:
                events.append(current_event)
                current_event = None
            break

        if in_timeline:
            ev_match = _event_line_re.match(line)
            if ev_match:
                if current_event:
                    events.append(current_event)
                current_event = {
                    "timestamp": ev_match.group(1),
                    "event_id": ev_match.group(2).strip(),
                }
                continue

            if current_event is not None:
                kv_match = _sub_kv_re.match(line)
                if kv_match:
                    current_event[kv_match.group(1).strip()] = kv_match.group(2).strip()

    if current_event:
        events.append(current_event)

    return events


# ── Work Map task parser ─────────────────────────────────

def _parse_work_map_tasks(text: str) -> list[dict]:
    """Parse ## Work Map section for item/task structure and current state."""
    tasks: list[dict] = []
    in_work_map = False
    current_item_id = ""
    current_item_title = ""
    current_task: dict | None = None

    task_re = re.compile(r"^####\s+Task:\s+(.+?)\s*<!--\s*task:(.+?)\s*-->")
    item_re = re.compile(r"^###\s+Item:\s+(.+?)\s*<!--\s*item:(.+?)\s*-->")
    status_re = re.compile(r"^-\s*status:\s*(.*)")
    next_action_re = re.compile(r"^-\s*next_action:\s*(.*)")
    last_event_re = re.compile(r"^-\s*last_event_id:\s*(.*)")

    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "## Work Map":
            in_work_map = True
            continue
        if in_work_map and stripped.startswith("## ") and stripped != "## Work Map":
            break

        if in_work_map:
            im = item_re.match(line)
            if im:
                current_item_title = im.group(1).strip()
                current_item_id = im.group(2).strip()
                current_task = None
                continue

            tm = task_re.match(line)
            if tm:
                if current_task:
                    tasks.append(current_task)
                current_task = {
                    "task_id": tm.group(2).strip(),
                    "item_id": current_item_id,
                    "item_title": current_item_title,
                    "title": tm.group(1).strip(),
                    "status": "",
                    "next_action": "",
                    "last_event_id": "",
                }
                continue

            if current_task is not None:
                sm = status_re.match(line)
                if sm:
                    current_task["status"] = sm.group(1).strip()
                    continue
                nm = next_action_re.match(line)
                if nm:
                    current_task["next_action"] = nm.group(1).strip()
                    continue
                em = last_event_re.match(line)
                if em:
                    current_task["last_event_id"] = em.group(1).strip()
                    continue

    if current_task:
        tasks.append(current_task)

    return tasks


def _parse_attachments_task_ids(text: str) -> set[str]:
    """Parse ## Attachments section and return set of task_ids with attachments."""
    task_ids: set[str] = set()
    in_attachments = False
    related_re = re.compile(r"^\s*-\s*related_task_id:\s*(.*)")

    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "## Attachments":
            in_attachments = True
            continue
        if in_attachments and stripped.startswith("## ") and stripped != "## Attachments":
            break

        if in_attachments:
            m = related_re.match(line)
            if m:
                task_ids.add(m.group(1).strip())

    return task_ids


# ── Helpers (duplicated from cli.py to keep gui.py self-contained) ──

def _collect_existing_event_ids(text: str) -> set[str]:
    event_ids: set[str] = set()
    for m in re.finditer(r"<!--\s*event:(.+?)\s*-->", text):
        event_ids.add(m.group(1).strip())
    for m in re.finditer(r"last_event_id:\s*(\S+)", text):
        val = m.group(1).strip()
        if val:
            event_ids.add(val)
    return event_ids


def _collect_existing_task_ids(text: str) -> set[str]:
    task_ids: set[str] = set()
    for m in re.finditer(r"<!--\s*task:(.+?)\s*-->", text):
        task_ids.add(m.group(1).strip())
    return task_ids


def _quick_extract_task_id(raw: str) -> str:
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = _json.loads(line)
        except _json.JSONDecodeError:
            continue
        if record.get("type") == "text":
            part = record.get("part", {})
            if part.get("type") == "text" and "text" in part:
                inner = _extract_json_from_fence(part["text"])
                try:
                    data = _json.loads(inner)
                    return data.get("event", {}).get("task_id", "unknown")
                except _json.JSONDecodeError:
                    continue

    inner = _extract_json_from_fence(raw)
    try:
        data = _json.loads(inner)
        return data.get("event", {}).get("task_id", "unknown")
    except _json.JSONDecodeError:
        return "unknown"


def _extract_json_from_fence(text: str) -> str:
    fence_re = re.compile(r"```(?:json)?\s*\n(.*?)\n\s*```", re.DOTALL)
    match = fence_re.search(text)
    return match.group(1) if match else text


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


def _dict_to_proposal(data: dict) -> ArchiveProposal:
    t = data.get("target", {})
    ev = data.get("event", {})
    return ArchiveProposal(
        target=TargetRef(
            project_id=t.get("project_id", ""),
            item_id=t.get("item_id", ""),
            task_id=t.get("task_id", ""),
            task_title=t.get("task_title", ""),
            new_item=t.get("new_item", False),
            new_task=t.get("new_task", False),
        ),
        confidence=float(data.get("confidence", 0)),
        reason=data.get("reason", ""),
        event=TimelineEvent(
            event_id=ev.get("event_id", ""),
            task_id=ev.get("task_id", ""),
            input_text=ev.get("input_text", ""),
            summary=ev.get("summary", ""),
            status=ev.get("status", "in_progress"),
            next_action=ev.get("next_action", ""),
            event_type=ev.get("event_type", "update"),
            corrects_event_id=ev.get("corrects_event_id"),
        ),
        attachment_paths=tuple(data.get("attachment_paths", [])),
    )


if __name__ == "__main__":
    main()
