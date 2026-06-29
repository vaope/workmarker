from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from workeventagent.models import ArchiveProposal, ConfirmationDecision, TargetRef, TimelineEvent


def render_confirmation_card(proposal: ArchiveProposal) -> str:
    lines: list[str] = []
    t = proposal.target
    e = proposal.event

    lines.append("=" * 60)
    lines.append("  Archive proposal")
    lines.append("=" * 60)
    lines.append(f"  project_id:   {t.project_id}")
    lines.append(f"  item_id:      {t.item_id}")
    lines.append(f"  task_id:      {t.task_id}")
    if t.task_title:
        lines.append(f"  task_title:   {t.task_title}")
    lines.append(f"  new_item: {str(t.new_item).lower()}")
    lines.append(f"  new_task: {str(t.new_task).lower()}")
    lines.append(f"  confidence:   {proposal.confidence:.0%}")
    lines.append(f"  reason:       {proposal.reason}")
    lines.append("")
    lines.append("  --- Event preview ---")
    lines.append(f"  event_id:     {e.event_id}")
    lines.append(f"  summary:      {e.summary}")
    lines.append(f"  status:       {e.status}")
    lines.append(f"  next_action:  {e.next_action}")
    if e.corrects_event_id:
        lines.append(f"  corrects:     {e.corrects_event_id}")

    if proposal.attachment_paths:
        lines.append("")
        lines.append("  --- Attachments ---")
        for path in proposal.attachment_paths:
            lines.append(f"  - {path}")

    lines.append("")
    lines.append("  --- Markdown block preview ---")
    lines.append("  ```md")
    block = _render_markdown_preview(proposal)
    for bline in block.splitlines():
        lines.append(f"  {bline}")
    lines.append("  ```")
    lines.append("")
    lines.append("  confirm / edit / cancel")
    lines.append("=" * 60)

    return "\n".join(lines)


def parse_confirmation_input(raw: str) -> ConfirmationDecision:
    normalized = raw.strip().lower()
    if normalized == "confirm":
        return ConfirmationDecision(kind="confirm")
    if normalized == "edit":
        return ConfirmationDecision(kind="edit")
    return ConfirmationDecision(kind="cancel")


def edit_proposal_with_editor(
    proposal: ArchiveProposal, editor: str = ""
) -> ArchiveProposal:
    if not editor:
        editor = os.environ.get("EDITOR", os.environ.get("VISUAL", "notepad"))

    data = {
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
            "event_type": proposal.event.event_type,
        },
        "attachment_paths": list(proposal.attachment_paths),
    }
    if proposal.event.corrects_event_id:
        data["event"]["corrects_event_id"] = proposal.event.corrects_event_id

    fd, tmp_path = tempfile.mkstemp(suffix=".json", prefix="wea-edit-")
    os.close(fd)
    try:
        Path(tmp_path).write_text(json.dumps(data, indent=2), encoding="utf-8")
        subprocess.run([editor, tmp_path], check=True)
        edited_data = json.loads(Path(tmp_path).read_text(encoding="utf-8"))
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    t = edited_data["target"]
    ev = edited_data["event"]
    edited_proposal = ArchiveProposal(
        target=TargetRef(
            project_id=t["project_id"],
            item_id=t["item_id"],
            task_id=t["task_id"],
            task_title=t.get("task_title", ""),
            new_item=t.get("new_item", False),
            new_task=t.get("new_task", False),
        ),
        confidence=float(edited_data["confidence"]),
        reason=edited_data["reason"],
        event=TimelineEvent(
            event_id=proposal.event.event_id,
            task_id=ev["task_id"],
            input_text=ev["input_text"],
            summary=ev["summary"],
            status=ev.get("status", "in_progress"),
            next_action=ev.get("next_action", ""),
            event_type=ev.get("event_type", "update"),
            corrects_event_id=ev.get("corrects_event_id"),
        ),
        attachment_paths=tuple(edited_data.get("attachment_paths", [])),
    )
    return edited_proposal


def _render_markdown_preview(proposal: ArchiveProposal) -> str:
    t = proposal.target
    e = proposal.event
    lines: list[str] = []

    if t.new_task:
        lines.append(f"#### Task: {t.task_title} <!-- task:{t.task_id} -->")
        lines.append(f"- status: {e.status}")
        lines.append(f"- next_action: {e.next_action}")
        lines.append(f"- last_event_id: {e.event_id}")
    else:
        lines.append(f"#### Task: (existing) <!-- task:{t.task_id} -->")
        lines.append(f"- status: {e.status}")
        lines.append(f"- next_action: {e.next_action}")
        lines.append(f"- last_event_id: {e.event_id}")

    return "\n".join(lines)

