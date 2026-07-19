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
from workeventagent.project_schema import (
    SECTION_BY_ID,
    SECTION_SPECS,
    find_section,
    metadata_hash,
    parse_attachment_records,
    parse_frontmatter,
    parse_timeline_events,
    replace_section_content,
    schema_version,
    section_content,
    section_hash,
    update_frontmatter,
    validate_reviewed_content,
)
from workeventagent.work_map_store import (
    parse_work_map,
    delete_task as wm_delete_task,
    delete_item as wm_delete_item,
    update_item as wm_update_item,
    update_task_state as wm_update_task_state,
    update_task_field as wm_update_task_field,
)
from workeventagent.inbox_store import (
    archive_capture,
    cancel_capture,
    create_capture,
    list_captures,
    update_capture,
)
from workeventagent.search_store import search_workspace
from workeventagent.correction_store import (
    correct_event_cross_project,
    correct_event_same_project,
    list_pending_corrections,
    resume_correction,
)
from workeventagent.markdown_store import ProjectDocument, write_project_atomically
from workeventagent.models import ArchiveProposal, TargetRef, TimelineEvent
from workeventagent.opencode_runner import (
    OpencodeRunnerError,
    parse_archivist_output,
    parse_knowledge_impact,
    parse_project_route_output,
    run_archivist,
    run_project_router,
    run_reporter,
    run_project_synthesizer,
    parse_synthesis_output,
)
from workeventagent.knowledge_store import (
    create_proposal,
    enqueue_job,
    get_job,
    get_proposal,
    list_jobs,
    list_proposals,
    list_schedule_runs,
    transition_job,
    transition_proposal,
)
from workeventagent.project_synthesis import (
    apply_document_proposal,
    apply_section_bundle,
    build_document_proposal,
    build_section_bundle,
    recover_applying_proposal,
    revise_section_bundle,
    select_source_events,
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
        "inbox_create": handle_inbox_create,
        "inbox_list": handle_inbox_list,
        "inbox_process": handle_inbox_process,
        "inbox_commit": handle_inbox_commit,
        "inbox_cancel": handle_inbox_cancel,
        "knowledge_recover": handle_knowledge_recover,
        "knowledge_enqueue": handle_knowledge_enqueue,
        "knowledge_enqueue_schedule": handle_knowledge_enqueue_schedule,
        "knowledge_process_job": handle_knowledge_process_job,
        "knowledge_state": handle_knowledge_state,
        "knowledge_retry_job": handle_knowledge_retry_job,
        "knowledge_revise_proposal": handle_knowledge_revise_proposal,
        "knowledge_reject_proposal": handle_knowledge_reject_proposal,
        "knowledge_apply_proposal": handle_knowledge_apply_proposal,
        "knowledge_apply_document": handle_knowledge_apply_document,
        "search": handle_search,
        "correct_event": handle_correct_event,
        "correction_recoveries": handle_correction_recoveries,
        "resume_correction": handle_resume_correction,
        "project_migration_preview": handle_project_migration_preview,
        "project_migration_apply": handle_project_migration_apply,
        "project_panorama": handle_project_panorama,
        "update_project_section": handle_update_project_section,
        "update_project_profile": handle_update_project_profile,
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
    opencode_model = request.get("opencode_model", "")

    prompt = f"Archive this update: {text}"
    if attachments:
        paths_str = ", ".join(str(a) for a in attachments)
        prompt += f"\n\nAttachments: {paths_str}"

    raw = run_archivist(prompt, project_path, model=opencode_model)

    doc_text = project_path.read_text(encoding="utf-8")
    existing_event_ids = _collect_existing_event_ids(doc_text)
    tentative_task_id = _quick_extract_task_id(raw)
    now = datetime.now(timezone.utc)
    event_id = make_event_id(now, tentative_task_id, existing_event_ids)

    proposal = parse_archivist_output(raw, event_id)
    knowledge_impact = parse_knowledge_impact(raw)

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
                "item_title": proposal.target.item_title,
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
        "knowledge_impact": knowledge_impact,
        "low_confidence": proposal.confidence < 0.7,
    }


# ── commit ───────────────────────────────────────────────

def handle_route_propose(request: dict) -> dict:
    workspace = Path(request["workspace"])
    text = request["text"]
    attachments = request.get("attachments", [])
    opencode_model = request.get("opencode_model", "")

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
                model=opencode_model,
            )
        route = parse_project_route_output(raw, allowed_project_ids)
        selected = next(p for p in projects if p["project_id"] == route["project_id"])

    result = handle_propose({
        "text": text,
        "project_path": selected["path"],
        "attachments": attachments,
        "opencode_model": opencode_model,
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

    event = proposal_data["event"]
    task_id = event["task_id"]
    event_id = event["event_id"]
    event_ts = _event_id_timestamp(event_id)
    project_dir = project_path.parent
    doc_text = project_path.read_text(encoding="utf-8")

    # Event identity is the durable commit key.  Check it before attachments or
    # Markdown writes so a crash/retry cannot duplicate either side effect.
    existing = next(
        (item for item in parse_timeline_events(doc_text) if item.get("event_id") == event_id),
        None,
    )
    if existing is not None:
        expected = {
            "task_id": event.get("task_id", ""),
            "input": event.get("input_text", ""),
            "summary": event.get("summary", ""),
            "status": event.get("status", "in_progress"),
            "next_action": event.get("next_action", ""),
        }
        comparable = {
            key: str(existing.get(key, "")).strip()
            for key in ("task_id", "input", "summary", "status", "next_action")
        }
        expected = {key: str(value).strip() for key, value in expected.items()}
        if comparable != expected:
            return {
                "ok": False,
                "kind": "event_id_conflict",
                "error": f"event_id already exists with different content: {event_id}",
                "event_id": event_id,
            }
        return {
            "ok": True,
            "written_path": str(project_path),
            "archived_attachments": [],
            "task_id": task_id,
            "event_id": event_id,
            "event": {"event_id": event_id},
            "idempotent": True,
        }

    # 1. Copy attachments from temp to project attachments dir
    archived_attachments: list[str] = []

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
        "event_id": event_id,
        "event": {"event_id": event_id},
        "idempotent": False,
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
    items = parse_work_map(text)
    timeline_events = parse_timeline_events(text)

    # Build task_id → latest timestamp map
    task_updated: dict[str, str] = {}
    for te in timeline_events:
        tid = te.get("task_id", "")
        ts = te.get("timestamp", "")
        if tid and ts and tid not in task_updated:
            task_updated[tid] = ts

    # Add updated_at to each task from timeline events
    for item in items:
        for task in item.get("tasks", []):
            task["updated_at"] = task_updated.get(task["task_id"], "")

    return {"ok": True, "project_id": project_id, "title": title, "items": items}


# ── timeline ─────────────────────────────────────────────

def handle_timeline(request: dict) -> dict:
    project_path = Path(request["project_path"])
    text = project_path.read_text(encoding="utf-8")

    events = parse_timeline_events(text)
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
                    model=request.get("opencode_model", ""),
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
                    model=request.get("opencode_model", ""),
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


# ── inbox commands ────────────────────────────────────────

def handle_inbox_create(request: dict) -> dict:
    card = create_capture(
        Path(request["workspace"]),
        str(request["text"]),
        request.get("attachments", []),
    )
    return {"ok": True, "card": card}


def handle_inbox_list(request: dict) -> dict:
    return {"ok": True, "cards": list_captures(Path(request["workspace"]))}


def handle_inbox_cancel(request: dict) -> dict:
    card = cancel_capture(Path(request["workspace"]), request["capture_id"])
    return {"ok": True, "card": card}


def handle_inbox_process(request: dict) -> dict:
    workspace = Path(request["workspace"])
    capture_id = request["capture_id"]
    cards = list_captures(workspace)
    card = next((c for c in cards if c["capture_id"] == capture_id), None)
    if card is None:
        return {"ok": False, "kind": "not_found", "error": "capture not found"}

    try:
        result = handle_route_propose({
            "workspace": str(workspace),
            "text": card["text"],
            "attachments": _inbox_attachment_paths(workspace, card),
            "opencode_model": request.get("opencode_model", ""),
        })
    except Exception as exc:
        update_capture(workspace, capture_id, {"state": "error", "error": str(exc)})
        return {"ok": False, "kind": "opencode_error", "error": str(exc)}

    if result.get("ok"):
        patch = {
            "state": "needs_confirmation",
            "proposal": result.get("proposal", result),
            "selected_project": result.get("selected_project"),
            "knowledge_impact": result.get(
                "knowledge_impact",
                {"level": "ordinary", "dimensions": [], "reason": "Impact metadata unavailable."},
            ),
        }
        if result.get("low_confidence"):
            patch["low_confidence"] = True
        updated = update_capture(workspace, capture_id, patch)
        return {"ok": True, "card": updated}
    else:
        update_capture(workspace, capture_id, {"state": "error", "error": result.get("error", "route_propose failed")})
        return {"ok": False, "kind": result.get("kind", "internal_error"), "error": result.get("error", "route_propose failed")}


def handle_inbox_commit(request: dict) -> dict:
    workspace = Path(request["workspace"])
    capture_id = request["capture_id"]
    cards = list_captures(workspace)
    card = next((c for c in cards if c["capture_id"] == capture_id), None)
    if card is None:
        return {"ok": False, "kind": "not_found", "error": "capture not found"}

    proposal = card.get("proposal", {})
    if not proposal:
        return {"ok": False, "kind": "no_proposal", "error": "card has no proposal to commit"}

    edits = request.get("edits", {})
    if edits:
        event = proposal.get("event", {})
        for k in ("summary", "status", "next_action", "task_id"):
            if k in edits:
                event[k] = edits[k]
        proposal["event"] = event

    selected = card.get("selected_project", {})
    project_path = selected.get("path", "")
    if not project_path:
        return {"ok": False, "kind": "no_project", "error": "card has no selected project"}

    event_id = str(proposal.get("event", {}).get("event_id", ""))
    project_id = str(proposal.get("target", {}).get("project_id", ""))
    impact = card.get("knowledge_impact", {})
    knowledge_job = None
    if impact.get("level") == "high":
        from workeventagent.knowledge_store import enqueue_job

        knowledge_job = enqueue_job(
            workspace,
            {
                "idempotency_key": f"high-impact:{project_id}:{event_id}",
                "state": "awaiting_source",
                "project_id": project_id,
                "project_path": project_path,
                "trigger": "high_impact",
                "source_event_ids": [event_id],
                "capture_id": capture_id,
            },
        )

    try:
        result = handle_commit({
            "proposal": proposal,
            "project_path": project_path,
            "db_path": str(workspace / "index.sqlite"),
            "pending_attachments": _inbox_attachment_paths(workspace, card),
        })
    except Exception as exc:
        update_capture(workspace, capture_id, {"state": "error", "error": str(exc)})
        return {"ok": False, "kind": "commit_error", "error": str(exc)}

    if result.get("ok"):
        archived_event_id = str(result.get("event_id", event_id))
        if knowledge_job is not None:
            from workeventagent.knowledge_store import get_job, transition_job

            current = get_job(workspace, knowledge_job["job_id"])
            source_ids = {
                item.get("event_id")
                for item in parse_timeline_events(Path(project_path).read_text(encoding="utf-8"))
            }
            if archived_event_id not in source_ids:
                update_capture(
                    workspace,
                    capture_id,
                    {"state": "error", "error": "committed event is not readable from Timeline"},
                )
                return {
                    "ok": False,
                    "kind": "source_not_visible",
                    "error": "committed event is not readable from Timeline",
                    "knowledge_job_id": current["job_id"],
                }
            if current["state"] == "awaiting_source":
                current = transition_job(
                    workspace,
                    current["job_id"],
                    current["version"],
                    {"awaiting_source"},
                    "queued",
                )
            knowledge_job = current
        archived = archive_capture(workspace, capture_id, {
            "project_path": project_path,
            "event_id": archived_event_id,
        })
        response = {"ok": True, "card": archived, "event_id": archived_event_id}
        if knowledge_job is not None:
            response["knowledge_job_id"] = knowledge_job["job_id"]
        return response
    else:
        update_capture(workspace, capture_id, {"state": "error", "error": result.get("error", "commit failed")})
        return {"ok": False, "kind": result.get("kind", "commit_error"), "error": result.get("error", "commit failed")}


def handle_knowledge_recover(request: dict) -> dict:
    """Recover interrupted high-impact source commits before worker startup."""
    workspace = Path(request["workspace"])
    import workeventagent.knowledge_store as knowledge_store

    recovered = knowledge_store.recover_jobs(workspace)
    recovered_proposal_ids: list[str] = []
    for proposal in knowledge_store.list_proposals(workspace):
        if proposal.get("state") != "applying":
            continue
        project_path = Path(str(proposal.get("project_path", "")))
        if not project_path.is_file() or not _project_within_workspace(workspace, project_path):
            continue
        recover_applying_proposal(project_path, proposal)
        recovered_proposal_ids.append(proposal["proposal_id"])
    recovered_run_ids: list[str] = []
    for run in knowledge_store.list_schedule_runs(workspace):
        ensured = knowledge_store.ensure_schedule_children(workspace, run["run_id"])
        knowledge_store.evaluate_schedule_run(workspace, ensured["run_id"])
        recovered_run_ids.append(ensured["run_id"])
    cards = {card.get("capture_id"): card for card in list_captures(workspace)}
    archived_ids: list[str] = []
    for job in knowledge_store.list_jobs(workspace):
        capture_id = str(job.get("capture_id", ""))
        source_ids = list(job.get("source_event_ids", []))
        card = cards.get(capture_id)
        source_exists = False
        project_path = Path(str(job.get("project_path", "")))
        if project_path.is_file():
            source_exists = all(
                event_id
                in {
                    event.get("event_id")
                    for event in parse_timeline_events(project_path.read_text(encoding="utf-8"))
                }
                for event_id in source_ids
            )
        if (
            job.get("state") == "queued"
            and capture_id
            and source_ids
            and source_exists
            and card is not None
            and card.get("state") not in {"archived", "canceled"}
        ):
            archive_capture(
                workspace,
                capture_id,
                {"project_path": job.get("project_path", ""), "event_id": source_ids[0]},
            )
            archived_ids.append(capture_id)
    return {
        "ok": True,
        "recovered_job_ids": [job["job_id"] for job in recovered],
        "recovered_proposal_ids": recovered_proposal_ids,
        "recovered_run_ids": recovered_run_ids,
        "archived_capture_ids": archived_ids,
    }


def _knowledge_error(kind: str, error: str) -> dict:
    return {"ok": False, "kind": kind, "error": error}


def _project_within_workspace(workspace: Path, project_path: Path) -> bool:
    try:
        project_path.resolve().relative_to(workspace.resolve())
        return True
    except ValueError:
        return False


def handle_knowledge_enqueue(request: dict) -> dict:
    """Enqueue a directed job only; trusted capture owns high-impact enqueue."""
    workspace = Path(request["workspace"])
    trigger = str(request.get("trigger", ""))
    if trigger != "directed":
        return _knowledge_error("invalid_trigger", "manual enqueue supports only directed synthesis")
    project_path = Path(str(request.get("project_path", "")))
    if not project_path.is_file() or not _project_within_workspace(workspace, project_path):
        return _knowledge_error("invalid_project", "project_path must be a project inside workspace")
    text = project_path.read_text(encoding="utf-8")
    if schema_version(text) < 2:
        return _knowledge_error("schema_v2_required", "Phase B requires schema v2")
    event_ids = request.get("event_ids")
    if not isinstance(event_ids, list) or not event_ids or any(not isinstance(value, str) for value in event_ids):
        return _knowledge_error("invalid_events", "one or more event_ids are required")
    try:
        select_source_events(text, event_ids=event_ids)
    except ValueError as exc:
        return _knowledge_error("invalid_events", str(exc))
    project_id = parse_frontmatter(text).get("project_id", "")
    idempotency_key = f"directed:{project_id}:{','.join(event_ids)}"
    job = enqueue_job(
        workspace,
        {
            "idempotency_key": idempotency_key,
            "state": "queued",
            "project_id": project_id,
            "project_path": str(project_path),
            "trigger": "directed",
            "source_event_ids": list(event_ids),
        },
    )
    return {"ok": True, "job": job}


def handle_knowledge_enqueue_schedule(request: dict) -> dict:
    workspace = Path(request["workspace"])
    cadence = str(request.get("cadence", ""))
    schedule_key = str(request.get("schedule_key", ""))
    if cadence not in {"daily", "weekly"} or not schedule_key:
        return _knowledge_error("invalid_schedule", "cadence and schedule_key are required")
    date_from = str(request.get("date_from", ""))
    date_to = str(request.get("date_to", ""))
    projects: list[dict] = []
    for candidate in scan_workspace(workspace):
        project_path = Path(candidate["path"])
        text = project_path.read_text(encoding="utf-8")
        if schema_version(text) < 2:
            continue
        projects.append(
            {
                "project_id": candidate["project_id"],
                "project_path": str(project_path),
                "date_from": date_from,
                "date_to": date_to,
            }
        )
    import workeventagent.knowledge_store as knowledge_store

    run = knowledge_store.create_schedule_run(workspace, cadence, schedule_key, projects)
    run = knowledge_store.ensure_schedule_children(workspace, run["run_id"])
    return {"ok": True, "run": run}


def _knowledge_prompt(job: dict, source_events: list[dict]) -> str:
    lines = [f"trigger={job['trigger']}"]
    if job["trigger"] == "weekly":
        lines.append("Perform a full Phase B review of current panorama, technical overview, and project knowledge.")
    lines.append("Use only these wrapper-selected source events:")
    for event in source_events:
        lines.append(
            f"- {event.get('event_id')}: {event.get('timestamp', '')} | "
            f"{event.get('summary', '')}"
        )
    lines.append("Return the bounded JSON contract only.")
    return "\n".join(lines)


def handle_knowledge_process_job(request: dict) -> dict:
    workspace = Path(request["workspace"])
    job_id = str(request.get("job_id", ""))
    try:
        job = get_job(workspace, job_id)
    except ValueError as exc:
        return _knowledge_error("not_found", str(exc))
    if job["state"] != "queued":
        return _knowledge_error("invalid_state", f"job is {job['state']}, not queued")
    job = transition_job(workspace, job_id, job["version"], {"queued"}, "processing")
    project_path = Path(job["project_path"])
    try:
        text = project_path.read_text(encoding="utf-8")
        if schema_version(text) < 2:
            raise ValueError("Phase B requires schema v2")
        if job["trigger"] in {"directed", "high_impact"}:
            source_events = select_source_events(text, event_ids=list(job.get("source_event_ids", [])))
        elif job["trigger"] in {"daily", "weekly"}:
            source_events = select_source_events(
                text,
                date_from=job.get("date_from"),
                date_to=job.get("date_to"),
            )
        else:
            raise ValueError(f"unsupported knowledge trigger: {job['trigger']}")
        if not source_events:
            finished = transition_job(
                workspace,
                job_id,
                job["version"],
                {"processing"},
                "skipped_no_evidence",
            )
            return {"ok": True, "job": finished, "proposal_ids": []}

        raw = run_project_synthesizer(
            _knowledge_prompt(job, source_events),
            project_path,
            opencode_bin=str(request.get("opencode_bin", "opencode")),
            model=str(request.get("model", request.get("opencode_model", ""))),
        )
        parsed = parse_synthesis_output(raw)
        bundle = build_section_bundle(
            project_path,
            job["trigger"],
            source_events,
            parsed,
        )
        if bundle is None:
            finished = transition_job(
                workspace,
                job_id,
                job["version"],
                {"processing"},
                "skipped_no_change",
            )
            return {"ok": True, "job": finished, "proposal_ids": []}
        document = None
        if parsed.get("document_suggestion") is not None:
            document = build_document_proposal(
                project_path,
                job["trigger"],
                source_events,
                parsed["document_suggestion"],
                linked_section_bundle=bundle,
            )

        section_proposal = create_proposal(workspace, bundle)
        proposal_ids = [section_proposal["proposal_id"]]
        if document is not None:
            document_proposal = create_proposal(workspace, document)
            proposal_ids.append(document_proposal["proposal_id"])
        finished = transition_job(
            workspace,
            job_id,
            job["version"],
            {"processing"},
            "completed",
            {"proposal_ids": proposal_ids},
        )
        return {"ok": True, "job": finished, "proposal_ids": proposal_ids}
    except Exception as exc:
        current = get_job(workspace, job_id)
        if current["state"] == "processing":
            current = transition_job(
                workspace,
                job_id,
                current["version"],
                {"processing"},
                "failed",
                {"last_error": str(exc)},
            )
        return {"ok": False, "kind": "processing_failed", "error": str(exc), "job": current}


def handle_knowledge_state(request: dict) -> dict:
    workspace = Path(request["workspace"])
    project_path = request.get("project_path")
    return {
        "ok": True,
        "jobs": list_jobs(workspace, project_path=project_path),
        "proposals": list_proposals(workspace, project_path=project_path),
        "runs": list_schedule_runs(workspace),
    }


def handle_knowledge_retry_job(request: dict) -> dict:
    workspace = Path(request["workspace"])
    try:
        job = transition_job(
            workspace,
            str(request["job_id"]),
            int(request["expected_version"]),
            {"failed"},
            "queued",
            {"last_error": ""},
        )
        return {"ok": True, "job": job}
    except (KeyError, TypeError, ValueError) as exc:
        return _knowledge_error("retry_conflict", str(exc))


def handle_knowledge_revise_proposal(request: dict) -> dict:
    workspace = Path(request["workspace"])
    try:
        original = get_proposal(workspace, str(request["proposal_id"]))
        if original["version"] != int(request["expected_version"]):
            raise ValueError("proposal version conflict")
        if original["state"] != "needs_confirmation":
            raise ValueError("proposal is not awaiting confirmation")
        revised, _superseded = revise_section_bundle(
            original,
            list(request.get("included_change_ids", [])),
        )
        revised = create_proposal(workspace, revised)
        old = transition_proposal(
            workspace,
            original["proposal_id"],
            original["version"],
            {"needs_confirmation"},
            "superseded",
            {"superseded_by": revised["proposal_id"]},
        )
        return {"ok": True, "proposal": revised, "superseded": old}
    except (KeyError, TypeError, ValueError) as exc:
        return _knowledge_error("revision_conflict", str(exc))


def handle_knowledge_reject_proposal(request: dict) -> dict:
    workspace = Path(request["workspace"])
    try:
        proposal = transition_proposal(
            workspace,
            str(request["proposal_id"]),
            int(request["expected_version"]),
            {"needs_confirmation"},
            "rejected",
        )
        return {"ok": True, "proposal": proposal}
    except (KeyError, TypeError, ValueError) as exc:
        return _knowledge_error("rejection_conflict", str(exc))


def _trusted_apply_request(request: dict, proposal_kind: str) -> tuple[Path, Path, dict, int]:
    workspace = Path(request["workspace"])
    project_path = Path(str(request.get("project_path", "")))
    if not project_path.is_file() or not _project_within_workspace(workspace, project_path):
        raise ValueError("project_path must be a project inside workspace")
    proposal = get_proposal(workspace, str(request["proposal_id"]))
    if proposal.get("proposal_kind") != proposal_kind:
        raise ValueError(f"proposal is not a {proposal_kind}")
    if Path(str(proposal.get("project_path", ""))).resolve() != project_path.resolve():
        raise ValueError("proposal project identity does not match request")
    return workspace, project_path, proposal, int(request["expected_version"])


def handle_knowledge_apply_proposal(request: dict) -> dict:
    try:
        _workspace, project_path, proposal, expected_version = _trusted_apply_request(
            request, "section_bundle"
        )
        return apply_section_bundle(
            project_path,
            Path(str(request["db_path"])),
            proposal,
            expected_version,
            str(request.get("today") or datetime.now(timezone.utc).strftime("%Y-%m-%d")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        return _knowledge_error("apply_conflict", str(exc))


def handle_knowledge_apply_document(request: dict) -> dict:
    try:
        _workspace, project_path, proposal, expected_version = _trusted_apply_request(
            request, "module_document"
        )
        return apply_document_proposal(
            project_path,
            proposal,
            expected_version,
            str(request.get("today") or datetime.now(timezone.utc).strftime("%Y-%m-%d")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        return _knowledge_error("apply_conflict", str(exc))


def _inbox_attachment_paths(workspace: Path, card: dict) -> list[dict]:
    attachments = card.get("attachments", [])
    if not attachments:
        return []
    pending = workspace / ".workeventagent" / "pending" / card["capture_id"]
    result: list[dict] = []
    for att in attachments:
        safe = att.get("safe_filename", att.get("filename", ""))
        p = pending / safe
        if p.exists():
            result.append({"temp_path": str(p), "filename": att.get("filename", safe)})
    return result


# ── search ─────────────────────────────────────────────────

def handle_search(request: dict) -> dict:
    query = str(request.get("query", "")).strip()
    if not query:
        return {"ok": False, "kind": "invalid_input", "error": "query is required"}
    results = search_workspace(Path(request["workspace"]), query, int(request.get("limit", 50)))
    return {"ok": True, "results": results}


# ── correction ─────────────────────────────────────────────

def handle_correct_event(request: dict) -> dict:
    project_path = Path(request["project_path"])
    db_path = Path(request["db_path"])
    if request.get("target_project_path"):
        target_path = Path(request["target_project_path"])
        return correct_event_cross_project(project_path, target_path, db_path, request)
    return correct_event_same_project(project_path, db_path, request)


def handle_correction_recoveries(request: dict) -> dict:
    workspace = Path(request["workspace"])
    return {"ok": True, "pending": list_pending_corrections(workspace)}


def handle_resume_correction(request: dict) -> dict:
    workspace = Path(request["workspace"])
    correction_id = request["correction_id"]
    db_path = Path(request["db_path"])
    return resume_correction(workspace, correction_id, db_path)


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
    use_v2 = request.get("schema_version") == 2 or request.get("status") or request.get("phase")
    if use_v2:
        status = request.get("status", "active")
        phase = request.get("phase", "planning")
        markdown = render_new_project_v2(project_id, title, date_str, items_spec, status, phase)
    else:
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
    if schema_version(text) >= 2:
        items = parse_work_map(text)
        target = next((it for it in items if it["item_id"] == item_id), None)
        if target is None:
            raise ValueError(f"Item anchor not found: <!-- item:{item_id} -->")
        task_count = len(target["tasks"])
        updated = wm_delete_item(text, item_id)
        updated = _bump_updated_text(updated, datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        return updated, task_count

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
    if schema_version(text) >= 2:
        updated = wm_delete_task(text, task_id)
        return _bump_updated_text(updated, datetime.now(timezone.utc).strftime("%Y-%m-%d"))

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

    if schema_version(text) >= 2:
        updated = wm_update_item(text, item_id, new_title, background)
        return _bump_updated_text(
            updated, datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        )

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
    if schema_version(text) >= 2:
        if field == "status":
            updated = wm_update_task_state(text, task_id, value)
        elif field == "title":
            updated = wm_update_task_field(text, task_id, "title", value)
        elif field == "next_action":
            updated = wm_update_task_field(text, task_id, "next_action", value)
        else:
            raise ValueError(f"unsupported field for v2: {field}")
        return _bump_updated_text(updated, datetime.now(timezone.utc).strftime("%Y-%m-%d"))

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


def render_new_project_v2(
    project_id: str, title: str, date_str: str, items_spec: list[dict],
    status: str = "active", phase: str = "planning",
) -> str:
    if not status or not phase:
        raise ValueError("status and phase are required")
    lines: list[str] = []
    lines.append("---")
    lines.append(f"project_id: {project_id}")
    lines.append(f"title: {title}")
    lines.append("doc_kind: work_project")
    lines.append("schema_version: 2")
    lines.append(f"status: {status}")
    lines.append(f"phase: {phase}")
    lines.append(f"created: {date_str}")
    lines.append(f"updated: {date_str}")
    lines.append("---")
    lines.append("")
    lines.append(f"# {title}")
    lines.append("")

    # All nine anchored sections in approved order
    sections = [
        ("project-profile", "## 项目档案 <!-- section:project-profile -->\n\n### 背景\n\n### 目标\n\n### 范围\n\n### 成功标准\n"),
        ("current-panorama", "## 当前全景 <!-- section:current-panorama -->\n"),
        ("work-map", "## 工作地图 <!-- section:work-map -->\n"),
        ("technical-overview", "## 技术概览 <!-- section:technical-overview -->\n"),
        ("project-knowledge", "## 关键认知 <!-- section:project-knowledge -->\n"),
        ("decisions", "## 关键决策 <!-- section:decisions -->\n"),
        ("attachments", "## 附件 <!-- section:attachments -->\n"),
        ("timeline", "## 事件证据 <!-- section:timeline -->\n"),
        ("rollups", "## 历史摘要 <!-- section:rollups -->\n"),
    ]

    for section_id, heading in sections:
        if section_id == "work-map":
            lines.append(heading)
            lines.append("")
            existing_item_ids: set[str] = set()
            existing_task_ids: set[str] = set()
            for item_spec in items_spec:
                item_title = item_spec.get("title", "")
                item_id = make_unique_stable_id(item_title, existing_item_ids)
                existing_item_ids.add(item_id)
                lines.append(f"### 工作项：{item_title} <!-- item:{item_id} -->")
                lines.append("")
                for task_title in item_spec.get("tasks", []):
                    task_id = make_unique_stable_id(task_title, existing_task_ids)
                    existing_task_ids.add(task_id)
                    lines.append(f"#### [ ] 任务：{task_title} <!-- task:{task_id} -->")
                    lines.append("- 下一步：")
                    lines.append(f"<!-- task-meta:last_event_id= -->")
                    lines.append("")
                if not item_spec.get("tasks"):
                    lines.append("")
        else:
            lines.append(heading)
            lines.append("")

    return "\n".join(lines)


def _generate_init_markdown(
    project_id: str, title: str, date_str: str, items_spec: list[dict]
) -> str:
    """Legacy v1 init — preserved for test compatibility."""
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

        for item in parse_work_map(text):
            lines.append(f"- item: {item['title']} ({item['item_id']})")
            for task in item.get("tasks", [])[:20]:
                next_action = task.get("next_action", "")
                lines.append(
                    f"  - task: {task['title']} ({task['task_id']}), "
                    f"status={task.get('status', '')}, next_action={next_action}"
                )
        lines.append("")
    return "\n".join(lines)



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
            item_title=t.get("item_title", ""),
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


# ── migration ───────────────────────────────────────────────

def handle_project_migration_preview(request: dict) -> dict:
    from workeventagent.project_migration import preview_v1_to_v2
    project_path = Path(request["project_path"])
    text = project_path.read_text(encoding="utf-8")
    status = request.get("status", "active")
    phase = request.get("phase", "planning")
    try:
        preview = preview_v1_to_v2(text, status, phase)
    except ValueError as e:
        return {"ok": False, "kind": "invalid_input", "error": str(e)}
    return {
        "ok": True,
        "migration": {
            "source_schema": preview.source_schema,
            "target_schema": preview.target_schema,
            "source_hash": preview.source_hash,
            "diff": preview.diff,
            "summary": preview.summary,
            "status": status,
            "phase": phase,
        },
    }


def handle_project_migration_apply(request: dict) -> dict:
    from workeventagent.project_migration import apply_v1_to_v2
    project_path = Path(request["project_path"])
    db_path = Path(request["db_path"])
    source_hash = request["source_hash"]
    status = request.get("status", "active")
    phase = request.get("phase", "planning")
    return apply_v1_to_v2(project_path, db_path, source_hash, status, phase)


# ── project_panorama ────────────────────────────────────────

def handle_project_panorama(request: dict) -> dict:
    """Return typed project panorama data: metadata + all section content/hashes/ownership."""
    project_path = Path(request["project_path"])
    text = project_path.read_text(encoding="utf-8")
    ver = schema_version(text)

    fm = parse_frontmatter(text)
    project = {
        "project_id": fm.get("project_id", ""),
        "title": fm.get("title", ""),
        "status": fm.get("status", "active"),
        "phase": fm.get("phase", "planning"),
        "updated": fm.get("updated", ""),
        "metadata_hash": metadata_hash(text),
    }

    if ver < 2:
        return {
            "ok": True,
            "schema_version": ver,
            "migration_required": True,
            "project": project,
            "sections": {},
        }

    sections: dict[str, dict] = {}
    for spec in SECTION_SPECS:
        try:
            content = section_content(text, spec.section_id)
        except ValueError:
            continue
        visible = _strip_panorama_control(content)
        source_event_ids = _parse_panorama_source_events(content)
        sections[spec.section_id] = {
            "title": spec.title,
            "ownership": spec.ownership,
            "content": visible,
            "hash": section_hash(text, spec.section_id),
            "source_event_ids": source_event_ids,
        }

    return {
        "ok": True,
        "schema_version": 2,
        "migration_required": False,
        "project": project,
        "sections": sections,
    }


_PANORAMA_CONTROL_RE = re.compile(r"<!--\s*panorama-meta[^>]*-->", re.IGNORECASE)


def _replace_section_raw(text: str, section_id: str, content: str) -> str:
    """Replace section content without validate_reviewed_content (for profile subsections)."""
    section = find_section(text, section_id)
    rendered = "\n" + content.strip("\n") + "\n\n"
    return text[:section.content_start - 1] + rendered + text[section.content_end:]


def _strip_panorama_control(content: str) -> str:
    """Remove panorama-meta control comments from visible content."""
    cleaned = _PANORAMA_CONTROL_RE.sub("", content)
    # Remove leading blank lines that were adjacent to removed comments
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip("\n") + "\n"


_SOURCE_EVENTS_RE = re.compile(r"source_events=([a-zA-Z0-9_,-]+)")


def _parse_panorama_source_events(content: str) -> list[str]:
    """Extract source event IDs from panorama-meta comments."""
    ids: list[str] = []
    for match in _PANORAMA_CONTROL_RE.finditer(content):
        raw = match.group(0)
        ev_match = _SOURCE_EVENTS_RE.search(raw)
        if ev_match:
            for eid in ev_match.group(1).split(","):
                eid = eid.strip()
                if eid:
                    ids.append(eid)
    return ids


# ── update_project_section ─────────────────────────────────

_EDITABLE_SECTIONS = {"technical-overview", "project-knowledge"}


def handle_update_project_section(request: dict) -> dict:
    """Hash-guarded edit of a reviewed section (technical-overview, project-knowledge)."""
    project_path = Path(request["project_path"])
    db_path = Path(request["db_path"])
    section_id = request["section_id"]
    base_hash = request["base_section_hash"]
    content = request["content"]

    if section_id not in _EDITABLE_SECTIONS:
        return {"ok": False, "kind": "invalid_operation",
                "error": f"section {section_id} cannot be edited through this handler"}

    text = project_path.read_text(encoding="utf-8")
    if schema_version(text) < 2:
        return {"ok": False, "kind": "invalid_operation",
                "error": "project must be migrated to v2 first"}

    # Validate hash
    if section_hash(text, section_id) != base_hash:
        return {"ok": False, "kind": "stale_section",
                "error": "section has changed — reload and re-edit"}

    # Validate and apply
    validate_reviewed_content(content)
    updated = replace_section_content(text, section_id, content)

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    updated = _bump_updated_text(updated, date_str)

    write_project_atomically(project_path, updated)
    init_db(db_path)
    rebuild_index(db_path, [project_path])
    return {"ok": True, "section_id": section_id}


# ── update_project_profile ─────────────────────────────────

_PROFILE_FIELDS = ("background", "goal", "scope", "success_criteria")
_PROFILE_SUBSECTION_TITLES = {
    "background": "背景",
    "goal": "目标",
    "scope": "范围",
    "success_criteria": "成功标准",
}


def handle_update_project_profile(request: dict) -> dict:
    """Hash-guarded edit of project profile: metadata + 4 subsections."""
    project_path = Path(request["project_path"])
    db_path = Path(request["db_path"])
    base_section_hash = request["base_section_hash"]
    base_metadata_hash = request["base_metadata_hash"]
    status = request.get("status", "")
    phase = request.get("phase", "")

    text = project_path.read_text(encoding="utf-8")
    if schema_version(text) < 2:
        return {"ok": False, "kind": "invalid_operation",
                "error": "project must be migrated to v2 first"}

    # Validate hashes
    if section_hash(text, "project-profile") != base_section_hash:
        return {"ok": False, "kind": "stale_section",
                "error": "project profile has changed — reload and re-edit"}
    if metadata_hash(text) != base_metadata_hash:
        return {"ok": False, "kind": "stale_metadata",
                "error": "project metadata has changed — reload and re-edit"}

    # Build profile content from typed fields
    lines: list[str] = []
    for field_key in _PROFILE_FIELDS:
        value = request.get(field_key, "").strip()
        # Validate each field value individually (profile subsections are structural)
        if "<!--" in value or "\n---\n" in value:
            return {"ok": False, "kind": "invalid_input",
                    "error": f"field {field_key} contains control syntax"}
        lines.append(f"### {_PROFILE_SUBSECTION_TITLES[field_key]}\n{value}\n")

    profile_content = "\n".join(lines)

    updated = _replace_section_raw(text, "project-profile", profile_content)

    # Update status/phase in frontmatter
    fm_updates: dict[str, str] = {}
    if status:
        fm_updates["status"] = status
    if phase:
        fm_updates["phase"] = phase
    if fm_updates:
        updated = update_frontmatter(updated, fm_updates)

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    updated = _bump_updated_text(updated, date_str)

    write_project_atomically(project_path, updated)
    init_db(db_path)
    rebuild_index(db_path, [project_path])
    return {"ok": True, "section_id": "project-profile"}
