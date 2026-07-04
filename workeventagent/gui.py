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
import tempfile
import traceback
from dataclasses import replace
from datetime import datetime, timezone, timedelta
from pathlib import Path

from workeventagent.ids import make_event_id, make_stable_id, make_unique_stable_id
from workeventagent.index_store import init_db, rebuild_index
from workeventagent.markdown_store import ProjectDocument, write_project_atomically
from workeventagent.models import ArchiveProposal, TargetRef, TimelineEvent
from workeventagent.opencode_runner import (
    OpencodeRunnerError,
    parse_archivist_output,
    parse_project_route_output,
    run_archivist,
    run_project_router,
    run_reporter,
)
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
        "route_propose": handle_route_propose,
        "commit": handle_commit,
        "projects": handle_projects,
        "tasks": handle_tasks,
        "timeline": handle_timeline,
        "init": handle_init,
        "create_item": handle_create_item,
        "create_task": handle_create_task,
        "delete_item": handle_delete_item,
        "delete_task": handle_delete_task,
        "update_item": handle_update_item,
        "update_task": handle_update_task,
        "generate_report": handle_generate_report,
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

def handle_route_propose(request: dict) -> dict:
    workspace = Path(request["workspace"])
    text = request["text"]
    attachments = request.get("attachments", [])

    projects = scan_workspace(workspace)
    if not projects:
        return {
            "ok": False,
            "kind": "no_project",
            "error": "No work projects found in the workspace.",
        }

    if len(projects) == 1:
        selected = projects[0]
        route = {
            "project_id": selected["project_id"],
            "confidence": 1.0,
            "reason": "Only one project exists in the workspace.",
        }
    else:
        allowed_project_ids = {p["project_id"] for p in projects}
        with tempfile.TemporaryDirectory(prefix="wea-router-") as tmp:
            routing_doc = Path(tmp) / "project-index.md"
            routing_doc.write_text(_build_project_route_context(projects), encoding="utf-8")
            raw = run_project_router(
                f"Route this work update to one existing project:\n\n{text}",
                routing_doc,
            )
        route = parse_project_route_output(raw, allowed_project_ids)
        selected = next(p for p in projects if p["project_id"] == route["project_id"])

    result = handle_propose({
        "text": text,
        "project_path": selected["path"],
        "attachments": attachments,
    })
    if result.get("ok"):
        result["selected_project"] = {
            "project_id": selected["project_id"],
            "title": selected["title"],
            "path": selected["path"],
        }
        result["route"] = route
        if route["confidence"] < 0.7:
            result["low_confidence"] = True
    return result


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

    work_map_items = _parse_work_map_items(text)
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
    items_map: dict[str, dict] = {
        item["item_id"]: {
            "item_id": item["item_id"],
            "title": item["title"],
            "background": item.get("background", ""),
            "tasks": [],
        }
        for item in work_map_items
    }
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
    item_order: list[str] = [item["item_id"] for item in work_map_items]
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
    for te in events:  # events already newest-first from _append_timeline
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


# ── generate_report ───────────────────────────────────────

import os


def _safe_component(raw: str | None) -> str:
    """Sanitize a user-controlled path component so it cannot escape its parent directory.

    Keeps [A-Za-z0-9_-]; replaces everything else with '_'.  If the result is
    empty (including when *raw* is None / empty), returns ``"x"``.
    """
    if not raw:
        return "x"
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "_", raw)
    return cleaned or "x"


def _report_output_path(
    workspace: Path,
    report_type: str,
    date_from: str,
    date_to: str,
    project_id: str | None,
    range_label: str,
) -> Path:
    """Compute output path for a generated report file under workspace/reports/."""
    reports_dir = workspace / "reports"
    if report_type == "daily":
        return reports_dir / "daily" / f"{date_from}.md"
    if report_type == "weekly":
        return reports_dir / "weekly" / f"{date_from}_to_{date_to}.md"
    if report_type == "project_summary":
        safe_project = _safe_component(project_id)
        return reports_dir / "project" / f"{safe_project}-summary-{date_to}.md"
    safe_label = _safe_component(range_label)
    return reports_dir / "range" / f"{date_from}_to_{date_to}-{safe_label}.md"


def _write_report_atomically(path: Path, content: str) -> None:
    """Write report content atomically via temp file + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def _parse_local_date_range(date_from_str: str, date_to_str: str) -> tuple[datetime, datetime]:
    """Parse inclusive local-date range from YYYY-MM-DD strings.
    
    Returns naive datetime boundaries in local time (start of date_from,
    end of date_to).  These are compared against event timestamps that
    have been converted to local time via .astimezone().
    """
    start_day = datetime.strptime(date_from_str, "%Y-%m-%d").date()
    end_day = datetime.strptime(date_to_str, "%Y-%m-%d").date()
    if end_day < start_day:
        raise ValueError("date_to must be on or after date_from")
    start = datetime.combine(start_day, datetime.min.time()).astimezone()
    end = datetime.combine(end_day, datetime.max.time()).astimezone()
    return start, end


def _filter_events_by_local_range(
    events: list[dict], date_from: datetime, date_to: datetime
) -> list[dict]:
    """Filter timeline events whose local timestamp falls within [date_from, date_to].
    
    Each event's UTC timestamp is converted to local time via .astimezone()
    (per-event DST-correct offset), then compared against the local boundaries.
    """
    result: list[dict] = []
    for ev in events:
        ts = ev.get("timestamp", "")
        if not ts:
            continue
        try:
            ev_dt = datetime.fromisoformat(ts).astimezone()
        except ValueError:
            continue
        if date_from <= ev_dt <= date_to:
            result.append(ev)
    return result


def _report_title(report_type: str, date_from_str: str, date_to_str: str, project_title: str = "") -> str:
    title_map: dict[str, str] = {
        "daily": f"日报 · {date_from_str}",
        "weekly": f"周报 · {date_from_str} → {date_to_str}",
        "range": f"报告 · {date_from_str} → {date_to_str}",
        "project_summary": f"项目总结 · {project_title or date_from_str}",
    }
    return title_map.get(report_type, f"报告 · {date_from_str} → {date_to_str}")


def _reporter_context(
    report_type: str, date_from: str, date_to: str, report_body: str,
) -> str:
    """Build a deterministic context document for the reporter agent."""
    return "\n".join([
        f"report_type: {report_type}",
        f"date_from: {date_from}",
        f"date_to: {date_to}",
        "",
        report_body,
    ])


def _parse_reporter_json(raw: str) -> dict:
    """Extract reporter JSON from opencode NDJSON output.
    
    Follows the same NDJSON→fence extraction convention as the archivist.
    """
    from workeventagent.opencode_runner import _extract_json_text
    inner = _extract_json_text(raw)
    try:
        data = _json.loads(inner)
    except _json.JSONDecodeError:
        return {"highlight": "", "narrative": "", "risks": [], "next_actions": []}
    return {
        "highlight": str(data.get("highlight", "")),
        "narrative": str(data.get("narrative", "")),
        "risks": [_ensure_str(r) for r in data.get("risks", [])],
        "next_actions": [_ensure_str(a) for a in data.get("next_actions", [])],
    }


def _ensure_str(val: object) -> str:
    return str(val) if val is not None else ""


def _build_project_report(
    project: dict, events: list[dict], report_type: str,
) -> str:
    """Build markdown report block for a single project."""
    project_id = project.get("project_id", "")
    title = project.get("title", project_id)

    # Build per-item → per-task event summary
    item_tasks: dict[str, dict] = {}  # item_id → {title, tasks: {task_id → {title, events[]}}}

    for ev in events:
        item_id = ev.get("item_id", "")
        task_id = ev.get("task_id", "")
        if item_id not in item_tasks:
            item_tasks[item_id] = {"title": "", "tasks": {}}
        if task_id not in item_tasks[item_id]["tasks"]:
            item_tasks[item_id]["tasks"][task_id] = {
                "title": ev.get("task_title", task_id),
                "events": [],
            }
        item_tasks[item_id]["tasks"][task_id]["events"].append(ev)
        # Fallback: use item_id until Work Map override supplies real title
        if not item_tasks[item_id]["title"] and ev.get("item_id"):
            item_tasks[item_id]["title"] = ev.get("item_id", "")

    # Also incorporate Work Map tasks to fill in item titles
    tasks_data = handle_tasks({"project_path": project["path"]})
    if tasks_data.get("ok"):
        for item in tasks_data.get("items", []):
            iid = item["item_id"]
            if iid in item_tasks:
                item_tasks[iid]["title"] = item["title"]

    lines: list[str] = []
    lines.append(f"### {title}")
    lines.append("")

    if not events:
        lines.append("*本周期无记录*")
        lines.append("")
        return "\n".join(lines)

    # Status summary
    done_count = sum(1 for ev in events if ev.get("status") == "done")
    total_count = len(events)
    lines.append(f"- 活动记录：{total_count} 条 · 已完成：{done_count}")
    lines.append("")

    for item_id, item_data in item_tasks.items():
        item_title = item_data["title"] or item_id
        lines.append(f"#### {item_title}")
        for task_id, task_data in item_data["tasks"].items():
            task_title = task_data["title"]
            task_events = task_data["events"]
            # Show latest status
            latest_status = task_events[-1].get("status", "")
            status_label = "✅ 已完成" if latest_status == "done" else "🔄 进行中"
            lines.append(f"- **{task_title}** {status_label}")
            for ev in task_events:
                ev_summary = ev.get("summary", ev.get("input", ""))[:120]
                ev_ts = ev.get("timestamp", "")[:16].replace("T", " ")
                lines.append(f"  - {ev_ts} {ev_summary}")
        lines.append("")

    return "\n".join(lines)


def handle_generate_report(request: dict) -> dict:
    """Generate daily/weekly/range/project_summary report.

    request: {
        "workspace": str,
        "type": "daily"|"weekly"|"range"|"project_summary",
        "project_id": str | None,
        "date_from": str | None,    # YYYY-MM-DD, defaults to today (local)
        "date_to": str | None,      # YYYY-MM-DD, defaults to date_from
        "persist": bool,            # write reports/*.md file
        "mode": "manual"|"scheduled",
        "include_ai": bool,         # AI highlight/narrative (Task 3)
        "range_label": str | None,  # e.g. "quarterly" / "semi_annual" for file naming
    }
    """
    workspace = Path(request["workspace"])
    project_id = request.get("project_id")

    # ── type normalization ──
    raw_type = request.get("type", "daily")
    # Legacy types → range (backend no longer hard-codes calendar math)
    if raw_type in {"quarterly", "semi_annual"}:
        report_type = "range"
        range_label = raw_type
    else:
        report_type = raw_type
        range_label = str(request.get("range_label", "custom"))

    if report_type not in ("daily", "weekly", "range", "project_summary"):
        return {"ok": False, "kind": "invalid_input",
                "error": f"unsupported report type: {raw_type}"}

    # ── date range (local computer time) ──
    default_date = datetime.now().astimezone().strftime("%Y-%m-%d")
    date_from_str = request.get("date_from") or request.get("date") or default_date
    date_to_str = request.get("date_to") or date_from_str
    try:
        date_from, date_to = _parse_local_date_range(date_from_str, date_to_str)
    except ValueError as exc:
        return {"ok": False, "kind": "invalid_input", "error": str(exc)}

    # ── scan workspace ──
    projects = scan_workspace(workspace)

    if project_id:
        projects = [p for p in projects if p.get("project_id") == project_id]
    if report_type == "project_summary" and not project_id:
        return {"ok": False, "kind": "invalid_input",
                "error": "project_id is required for project_summary"}

    # ── build report ──
    report_lines: list[str] = []
    title = _report_title(report_type, date_from_str, date_to_str)
    report_lines.append(f"# {title}")
    report_lines.append("")
    report_lines.append(f"周期：{date_from_str} → {date_to_str}")
    report_lines.append("")

    total_events = 0
    project_count = 0
    included_project_ids: list[str] = []

    for project in projects:
        try:
            events_data = handle_timeline({"project_path": project["path"]})
        except Exception:
            continue
        if not events_data.get("ok"):
            continue

        all_events = events_data.get("events", [])
        filtered = _filter_events_by_local_range(all_events, date_from, date_to)
        if not filtered and report_type != "project_summary":
            continue

        project_count += 1
        total_events += len(filtered)
        if project.get("project_id"):
            included_project_ids.append(project["project_id"])
        block = _build_project_report(project, filtered, report_type)
        report_lines.append(block)
        report_lines.append("")

    if project_count == 0:
        report_lines.append("*所选周期内无活动记录*")
        report_lines.append("")

    mode = request.get("mode", "manual")
    persist = request.get("persist", False)

    # ── scheduled no-event skip ──
    if mode == "scheduled" and report_type in {"daily", "weekly"} and total_events == 0:
        return {
            "ok": True,
            "report": "",
            "written_path": "",
            "date_range": {"from": date_from_str, "to": date_to_str},
            "project_count": 0,
            "event_count": 0,
            "skipped": True,
            "skip_reason": "no_events",
        }

    # ── frontmatter + deterministic body ──
    deterministic_body = "\n".join(report_lines) if report_lines else ""

    frontmatter = [
        "---",
        "doc_kind: work_report",
        f"report_type: {report_type}",
        f"date_from: {date_from_str}",
        f"date_to: {date_to_str}",
        f"generated_at: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        "timezone: local",
        f"source_project_ids: [{', '.join(included_project_ids)}]",
        f"event_count: {total_events}",
        "generator_version: F002",
        "---",
        "",
    ]

    # ── AI synthesis ──
    include_ai = request.get("include_ai", False)
    ai_block = ""

    if report_type == "project_summary":
        if not include_ai:
            return {"ok": False, "kind": "invalid_input",
                    "error": "project_summary requires include_ai=true"}
        # project_summary: AI narrative is required (fail-closed)
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", suffix=".md", delete=False,
            ) as fh:
                context_path = Path(fh.name)
                fh.write(_reporter_context(
                    report_type, date_from_str, date_to_str, deterministic_body,
                ))
            try:
                raw = run_reporter(
                    "Summarize this report context as JSON.",
                    context_path,
                    opencode_bin=request.get("opencode_bin", "opencode"),
                )
            finally:
                context_path.unlink(missing_ok=True)
            ai_data = _parse_reporter_json(raw)
            narrative = ai_data.get("narrative", "")
            if not narrative:
                return {"ok": False, "kind": "opencode_error",
                        "error": "reporter returned empty narrative for project_summary"}
            ai_block = f"\n## AI Narrative\n\n{narrative}\n"
        except OpencodeRunnerError as exc:
            return {"ok": False, "kind": "opencode_error", "error": str(exc)}
    elif include_ai and report_type in {"daily", "weekly"}:
        # daily/weekly: AI highlight is optional (fail-open)
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", suffix=".md", delete=False,
            ) as fh:
                context_path = Path(fh.name)
                fh.write(_reporter_context(
                    report_type, date_from_str, date_to_str, deterministic_body,
                ))
            try:
                raw = run_reporter(
                    "Write a short highlight for this report context as JSON.",
                    context_path,
                    opencode_bin=request.get("opencode_bin", "opencode"),
                )
            finally:
                context_path.unlink(missing_ok=True)
            ai_data = _parse_reporter_json(raw)
            highlight = ai_data.get("highlight", "")
            if highlight:
                ai_block = f"\n## AI Highlight\n\n{highlight}\n"
        except OpencodeRunnerError:
            ai_block = "\n## AI Highlight\n\n*AI highlight unavailable.*\n"

    report = "\n".join(frontmatter + [deterministic_body, ai_block])
    report = report.strip() + "\n"

    # ── persist ──
    written_path = ""
    if persist:
        path = _report_output_path(workspace, report_type, date_from_str, date_to_str,
                                    project_id, range_label)
        _write_report_atomically(path, report)
        written_path = str(path)

    return {
        "ok": True,
        "report": report,
        "written_path": written_path,
        "date_range": {"from": date_from_str, "to": date_to_str},
        "project_count": project_count,
        "event_count": total_events,
        "skipped": False,
        "skip_reason": "",
    }


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


def handle_create_item(request: dict) -> dict:
    project_path = Path(request["project_path"])
    db_path = Path(request["db_path"])
    title = request.get("title", "").strip()
    background = request.get("background", "").strip() or ""
    if not title:
        return {"ok": False, "kind": "invalid_input", "error": "item title is required"}

    text = project_path.read_text(encoding="utf-8")
    item_id = make_unique_stable_id(title, _collect_existing_item_ids(text))
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        updated = _insert_item_block(text, title, item_id, date_str, background)
    except ValueError as exc:
        return {"ok": False, "kind": "invalid_project", "error": str(exc)}

    write_project_atomically(project_path, updated)
    init_db(db_path)
    rebuild_index(db_path, [project_path])
    return {"ok": True, "item_id": item_id, "title": title}


def handle_create_task(request: dict) -> dict:
    project_path = Path(request["project_path"])
    db_path = Path(request["db_path"])
    item_id = request["item_id"]
    title = request.get("title", "").strip()
    if not title:
        return {"ok": False, "kind": "invalid_input", "error": "task title is required"}

    text = project_path.read_text(encoding="utf-8")
    task_id = make_unique_stable_id(title, _collect_existing_task_ids(text))
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        updated = _insert_task_block(text, item_id, title, task_id, date_str)
    except ValueError as exc:
        return {"ok": False, "kind": "invalid_project", "error": str(exc)}

    write_project_atomically(project_path, updated)
    init_db(db_path)
    rebuild_index(db_path, [project_path])
    return {"ok": True, "item_id": item_id, "task_id": task_id, "title": title}


# ── delete_item ────────────────────────────────────────────

def handle_delete_item(request: dict) -> dict:
    project_path = Path(request["project_path"])
    db_path = Path(request["db_path"])
    item_id = request["item_id"]

    text = project_path.read_text(encoding="utf-8")
    try:
        updated, task_count = _delete_item_block(text, item_id)
    except ValueError as exc:
        return {"ok": False, "kind": "invalid_project", "error": str(exc)}

    write_project_atomically(project_path, updated)
    init_db(db_path)
    rebuild_index(db_path, [project_path])
    return {"ok": True, "item_id": item_id, "deleted_task_count": task_count}


def _delete_item_block(text: str, item_id: str) -> tuple[str, int]:
    """Delete an item and all its tasks from the Work Map section.

    Returns (updated_text, deleted_task_count).
    """
    item_anchor = f"<!-- item:{item_id} -->"
    lines = text.splitlines(keepends=True)

    start_idx = None
    for i, line in enumerate(lines):
        if item_anchor in line:
            start_idx = i
            break
    if start_idx is None:
        raise ValueError(f"Item anchor not found: {item_anchor}")

    # Find end: next ### Item: / #### Task: or next ## section boundary
    end_idx = len(lines)
    for i in range(start_idx + 1, len(lines)):
        stripped = lines[i].strip()
        if stripped.startswith("### Item:") or stripped.startswith("## "):
            end_idx = i
            break

    task_count = sum(1 for j in range(start_idx, end_idx) if "<!-- task:" in lines[j])

    # Build result, trimming trailing blanks before the next heading
    result_lines = lines[:start_idx]
    while result_lines and result_lines[-1].strip() == "":
        result_lines.pop()
    result_lines.append("\n")
    result_lines.extend(lines[end_idx:])

    updated = "".join(result_lines)
    updated = _bump_updated_text(updated, datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    return updated, task_count


# ── delete_task ────────────────────────────────────────────

def handle_delete_task(request: dict) -> dict:
    project_path = Path(request["project_path"])
    db_path = Path(request["db_path"])
    task_id = request["task_id"]

    text = project_path.read_text(encoding="utf-8")
    try:
        updated = _delete_task_block(text, task_id)
    except ValueError as exc:
        return {"ok": False, "kind": "invalid_project", "error": str(exc)}

    write_project_atomically(project_path, updated)
    init_db(db_path)
    rebuild_index(db_path, [project_path])
    return {"ok": True, "task_id": task_id}


def _delete_task_block(text: str, task_id: str) -> str:
    """Delete a single task block from the Work Map.

    Detects the block boundary structurally (next #### Task: / ### Item: / ## ),
    same as _delete_item_block — not a fixed line-count offset.
    Timeline events referencing this task_id are preserved.
    """
    task_anchor = f"<!-- task:{task_id} -->"
    lines = text.splitlines(keepends=True)

    start_idx = None
    for i, line in enumerate(lines):
        if task_anchor in line:
            start_idx = i
            break
    if start_idx is None:
        raise ValueError(f"Task anchor not found: {task_anchor}")

    # Find end: next heading (#### Task:, ### Item:) or next ## section boundary
    end_idx = len(lines)
    for i in range(start_idx + 1, len(lines)):
        stripped = lines[i].strip()
        if (stripped.startswith("#### Task:") or
                stripped.startswith("### Item:") or
                stripped.startswith("## ")):
            end_idx = i
            break

    # Build result, trimming trailing blanks before the next heading
    result_lines = lines[:start_idx]
    while result_lines and result_lines[-1].strip() == "":
        result_lines.pop()
    result_lines.append("\n")
    result_lines.extend(lines[end_idx:])

    result = "".join(result_lines)
    return _bump_updated_text(
        result, datetime.now(timezone.utc).strftime("%Y-%m-%d")
    )


# ── update_item ────────────────────────────────────────────

def handle_update_item(request: dict) -> dict:
    project_path = Path(request["project_path"])
    db_path = Path(request["db_path"])
    item_id = request["item_id"]
    title = request.get("title", "").strip()
    background = request.get("background")
    # background: None = no change; str = set/update; "" = clear
    if background is not None:
        background = background.strip() or ""

    if not title:
        return {"ok": False, "kind": "invalid_input", "error": "item title is required"}

    text = project_path.read_text(encoding="utf-8")
    try:
        updated = _update_item_block(text, item_id, title, background)
    except ValueError as exc:
        return {"ok": False, "kind": "invalid_project", "error": str(exc)}

    write_project_atomically(project_path, updated)
    init_db(db_path)
    rebuild_index(db_path, [project_path])
    return {"ok": True, "item_id": item_id, "title": title}


def _update_item_block(
    text: str, item_id: str, new_title: str, background: str | None,
) -> str:
    """Update an item — title and/or background. Preserves the anchor id."""
    item_anchor = f"<!-- item:{item_id} -->"
    if item_anchor not in text:
        raise ValueError(f"Item anchor not found: {item_anchor}")

    # 1. Rename title
    pattern = rf"(### Item:\s+).+?(\s*<!--\s*item:{re.escape(item_id)}\s*-->)"
    updated = re.sub(pattern, lambda m: f"{m.group(1)}{new_title}{m.group(2)}", text, count=1)

    # 2. Update background (if requested)
    if background is not None:
        updated = _set_item_background(updated, item_id, background)

    # 3. If nothing changed, succeed as no-op (user opened edit modal and saved without changes)
    if updated == text:
        return text

    return _bump_updated_text(
        updated, datetime.now(timezone.utc).strftime("%Y-%m-%d")
    )


def _set_item_background(text: str, item_id: str, background: str) -> str:
    """Insert, update, or remove the ``- background:`` line for *item_id*.

    *background* is already stripped: empty string means "remove".
    """
    item_anchor = f"<!-- item:{item_id} -->"
    lines = text.splitlines(keepends=True)

    # Locate the item heading line
    item_idx = None
    for i, line in enumerate(lines):
        if item_anchor in line:
            item_idx = i
            break
    if item_idx is None:
        raise ValueError(f"Item anchor not found: {item_anchor}")

    # Find existing background line after the heading (before next heading)
    bg_idx = None
    for j in range(item_idx + 1, len(lines)):
        stripped = lines[j].strip()
        if (stripped.startswith("### ") or
                stripped.startswith("#### ") or
                stripped.startswith("## ")):
            break
        if re.match(r"^-\s*background:", stripped):
            bg_idx = j
            break

    if background:
        bg_line = f"- background: {background}\n"
        if bg_idx is not None:
            lines[bg_idx] = bg_line
        else:
            # Insert after item heading line, before next heading / blank line
            insert_at = item_idx + 1
            # Skip blank lines right after the heading
            while insert_at < len(lines) and lines[insert_at].strip() == "":
                insert_at += 1
            lines.insert(insert_at, bg_line)
    elif bg_idx is not None:
        # Remove: drop the line and any trailing blank that follows
        del lines[bg_idx]

    return "".join(lines)


# ── update_task ────────────────────────────────────────────

def handle_update_task(request: dict) -> dict:
    project_path = Path(request["project_path"])
    db_path = Path(request["db_path"])
    task_id = request["task_id"]
    # Accept any subset of {status, title, next_action}
    field = request.get("field", "")
    value = request.get("value", "")

    valid_fields = {"status", "title", "next_action"}
    if field not in valid_fields:
        return {"ok": False, "kind": "invalid_input",
                "error": f"field must be one of: {', '.join(sorted(valid_fields))}"}
    if field == "status" and value not in ("in_progress", "done"):
        return {"ok": False, "kind": "invalid_input",
                "error": "status must be in_progress or done"}

    text = project_path.read_text(encoding="utf-8")
    try:
        updated = _update_task_attr(text, task_id, field, value)
    except ValueError as exc:
        return {"ok": False, "kind": "invalid_project", "error": str(exc)}

    write_project_atomically(project_path, updated)
    init_db(db_path)
    rebuild_index(db_path, [project_path])
    return {"ok": True, "task_id": task_id, "field": field, "value": value}


def _update_task_attr(text: str, task_id: str, field: str, value: str) -> str:
    """Update one attribute of a task block. Preserves anchor id."""
    task_anchor = f"<!-- task:{task_id} -->"
    lines = text.splitlines(keepends=True)

    task_idx = None
    for i, line in enumerate(lines):
        if task_anchor in line:
            task_idx = i
            break
    if task_idx is None:
        raise ValueError(f"Task anchor not found: {task_anchor}")

    if field == "title":
        # Replace display text in heading, keep anchor
        lines[task_idx] = re.sub(
            rf"(#### Task:\s+).+?(\s*<!--\s*task:{re.escape(task_id)}\s*-->)",
            lambda m: f"{m.group(1)}{value}{m.group(2)}",
            lines[task_idx],
        )
    else:
        # Find the sub-item line within the next few lines
        for j in range(task_idx + 1, min(task_idx + 5, len(lines))):
            stripped = lines[j].strip()
            if stripped.startswith(f"- {field}:"):
                lines[j] = f"- {field}: {value}\n"
                break

    return _bump_updated_text(
        "".join(lines), datetime.now(timezone.utc).strftime("%Y-%m-%d")
    )


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

    existing_item_ids: set[str] = set()
    existing_task_ids: set[str] = set()

    for item_spec in items_spec:
        item_title = item_spec.get("title", "")
        item_id = make_unique_stable_id(item_title, existing_item_ids)
        existing_item_ids.add(item_id)
        lines.append(f"### Item: {item_title} <!-- item:{item_id} -->")
        lines.append("")
        for task_title in item_spec.get("tasks", []):
            task_id = make_unique_stable_id(task_title, existing_task_ids)
            existing_task_ids.add(task_id)
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


def _insert_item_block(text: str, title: str, item_id: str, updated_date: str, background: str = "") -> str:
    work_map_match = re.search(r"(## Work Map\s*\n)", text)
    if not work_map_match:
        raise ValueError("## Work Map section not found")

    section_match = re.search(r"^## (?!Work Map\b).*$", text[work_map_match.end():], re.MULTILINE)
    insert_pos = work_map_match.end() + section_match.start() if section_match else len(text)

    prefix = text[:insert_pos].rstrip() + "\n\n"
    suffix = text[insert_pos:].lstrip("\n")
    block = f"### Item: {title} <!-- item:{item_id} -->\n"
    if background:
        block += f"- background: {background}\n"
    block += "\n"
    return _bump_updated_text(prefix + block + suffix, updated_date)


def _insert_task_block(text: str, item_id: str, title: str, task_id: str, updated_date: str) -> str:
    item_anchor = f"<!-- item:{item_id} -->"
    lines = text.splitlines(keepends=True)

    item_idx = None
    for idx, line in enumerate(lines):
        if item_anchor in line:
            item_idx = idx
            break
    if item_idx is None:
        raise ValueError(f"Item anchor not found: {item_anchor}")

    insert_idx = len(lines)
    for idx in range(item_idx + 1, len(lines)):
        stripped = lines[idx].strip()
        if stripped.startswith("### Item:") or stripped.startswith("## "):
            insert_idx = idx
            break

    new_lines = list(lines[:insert_idx])
    if new_lines and new_lines[-1].strip() != "":
        new_lines.append("\n")
    new_lines.extend([
        f"#### Task: {title} <!-- task:{task_id} -->\n",
        "- status: in_progress\n",
        "- next_action:\n",
        "- last_event_id:\n",
        "\n",
    ])
    new_lines.extend(lines[insert_idx:])
    return _bump_updated_text("".join(new_lines), updated_date)


def _bump_updated_text(text: str, updated_date: str) -> str:
    return re.sub(r"(updated:\s*).*", rf"\g<1>{updated_date}", text, count=1)


def _build_project_route_context(projects: list[dict]) -> str:
    lines = [
        "# WorkEventAgent Project Index",
        "",
        "Choose exactly one existing project_id for the user's update.",
        "",
    ]
    for project in projects:
        path = Path(project["path"])
        lines.append(f"## Project: {project.get('title', '')}")
        lines.append(f"- project_id: {project.get('project_id', '')}")
        lines.append(f"- path: {path}")
        lines.append(f"- open_task_count: {project.get('open_task_count', 0)}")
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            lines.append("")
            continue

        for item in _parse_work_map_items(text):
            lines.append(f"- item: {item['title']} ({item['item_id']})")
        for task in _parse_work_map_tasks(text)[:20]:
            next_action = task.get("next_action", "")
            lines.append(
                f"  - task: {task['title']} ({task['task_id']}), "
                f"status={task.get('status', '')}, next_action={next_action}"
            )
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

def _parse_work_map_items(text: str) -> list[dict]:
    """Parse ## Work Map item headings, including items that have no tasks yet.

    Also captures optional ``- background:`` lines between the item heading and
    the next heading (next Item / Task / section).
    """
    items: list[dict] = []
    in_work_map = False
    item_re = re.compile(r"^###\s+Item:\s+(.+?)\s*<!--\s*item:(.+?)\s*-->")
    bg_re = re.compile(r"^-\s*background:\s*(.*)")

    lines = text.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "## Work Map":
            in_work_map = True
            continue
        if in_work_map and stripped.startswith("## ") and stripped != "## Work Map":
            break

        if in_work_map:
            match = item_re.match(line)
            if match:
                item: dict = {
                    "item_id": match.group(2).strip(),
                    "title": match.group(1).strip(),
                }
                # Look ahead for - background: before the next heading
                for ahead in range(i + 1, len(lines)):
                    next_stripped = lines[ahead].strip()
                    if (next_stripped.startswith("### ") or
                            next_stripped.startswith("#### ") or
                            next_stripped.startswith("## ")):
                        break
                    bg_match = bg_re.match(next_stripped)
                    if bg_match:
                        bg_val = bg_match.group(1).strip()
                        if bg_val:
                            item["background"] = bg_val
                        break
                items.append(item)

    return items


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
                if current_task:
                    tasks.append(current_task)
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


def _collect_existing_item_ids(text: str) -> set[str]:
    item_ids: set[str] = set()
    for m in re.finditer(r"<!--\s*item:(.+?)\s*-->", text):
        item_ids.add(m.group(1).strip())
    return item_ids


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
