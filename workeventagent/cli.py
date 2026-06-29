from __future__ import annotations

import argparse
import json as _json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from workeventagent.confirm import edit_proposal_with_editor, parse_confirmation_input, render_confirmation_card
from workeventagent.ids import make_event_id, make_unique_stable_id
from workeventagent.index_store import init_db, rebuild_index
from workeventagent.markdown_store import ProjectDocument, write_project_atomically
from workeventagent.opencode_runner import OpencodeRunnerError, parse_archivist_output, run_archivist

CONFIDENCE_THRESHOLD = 0.6


def main(argv: list[str] | None = None, now: datetime | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if now is None:
        now = datetime.now(timezone.utc)

    parser = argparse.ArgumentParser(prog="workeventagent")
    subparsers = parser.add_subparsers(dest="command")

    capture = subparsers.add_parser("capture")
    capture.add_argument("--project", required=True, type=Path)
    capture.add_argument("--db", required=True, type=Path)
    capture.add_argument("--text", required=True)
    capture.add_argument("--attach", action="append", default=[], type=Path)
    capture.add_argument("--dry-run", action="store_true")

    args = parser.parse_args(argv)

    if args.command != "capture":
        parser.print_help()
        return 1

    project_path = args.project.resolve()
    db_path = args.db.resolve()

    # Build prompt with optional attachments
    prompt = f"Archive this update: {args.text}"
    if args.attach:
        paths_str = ", ".join(str(p) for p in args.attach)
        prompt += f"\n\nAttachments: {paths_str}"

    # 1. Run archivist
    try:
        raw = run_archivist(prompt, project_path)
    except OpencodeRunnerError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    # 2. Pre-extract task_id for event_id generation
    doc_text = project_path.read_text(encoding="utf-8")
    existing_event_ids = _collect_existing_event_ids(doc_text)
    tentative_task_id = _quick_extract_task_id(raw)
    event_id = make_event_id(now, tentative_task_id, existing_event_ids)

    # 3. Full parse
    try:
        proposal = parse_archivist_output(raw, event_id)
    except OpencodeRunnerError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    # 3.5. Anti-collision: new_task must not reuse existing task_id
    if proposal.target.new_task:
        existing_task_ids = _collect_existing_task_ids(doc_text)
        unique_task_id = make_unique_stable_id(proposal.target.task_title, existing_task_ids)
        if unique_task_id != proposal.target.task_id:
            from dataclasses import replace

            new_event_id = make_event_id(now, unique_task_id, existing_event_ids)
            new_target = replace(proposal.target, task_id=unique_task_id)
            new_event = replace(proposal.event, event_id=new_event_id, task_id=unique_task_id)
            proposal = replace(proposal, target=new_target, event=new_event)

    # 4. Confidence check — reject low-confidence proposals
    if proposal.confidence < CONFIDENCE_THRESHOLD:
        print(
            f"Confidence too low ({proposal.confidence:.0%} < {CONFIDENCE_THRESHOLD:.0%})."
        )
        print("Please provide a more specific target or manually specify the task.")
        return 1

    # 5. Render confirmation card
    print(render_confirmation_card(proposal))

    if args.dry_run:
        return 0

    # 6. Interactive confirmation loop
    while True:
        try:
            choice = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.")
            return 2

        decision = parse_confirmation_input(choice)

        if decision.kind == "cancel":
            print("Cancelled.")
            return 2

        if decision.kind == "edit":
            proposal = edit_proposal_with_editor(proposal)
            print(render_confirmation_card(proposal))
            continue

        if decision.kind == "confirm":
            break

    # 7. Write Markdown
    now_str = now.strftime("%Y-%m-%d")

    if proposal.target.new_task:
        # A-point: insert structure + append timeline + bump updated
        doc = ProjectDocument.from_text(doc_text)
        inserted = doc.insert_new_task(proposal)
        doc2 = ProjectDocument.from_text(inserted)
        final = doc2.apply_proposal(proposal, now_str)
    else:
        doc = ProjectDocument.from_text(doc_text)
        final = doc.apply_proposal(proposal, now_str)

    # 7.5. Append attachment paths to ## Attachments
    final = ProjectDocument.append_attachments(final, proposal)

    write_project_atomically(project_path, final)

    # 8. Rebuild SQLite index
    init_db(db_path)
    rebuild_index(db_path, [project_path])

    print("Markdown written")
    print("SQLite index updated")
    return 0


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
    """Extract task_id from raw NDJSON/JSON without full validation."""
    # Try NDJSON lines first
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

    # Fallback: treat raw as plain JSON (possibly fence-wrapped)
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
