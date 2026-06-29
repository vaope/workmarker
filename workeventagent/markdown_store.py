from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path

from workeventagent.models import ArchiveProposal


class ProjectDocument:
    def __init__(self, frontmatter: str, body: str, body_lines: list[str]) -> None:
        self.frontmatter = frontmatter
        self.body = body
        self._body_lines = body_lines

    @classmethod
    def from_text(cls, text: str) -> ProjectDocument:
        parts = text.split("---", 2)
        if len(parts) < 3:
            raise ValueError("Missing YAML frontmatter delimited by ---")
        frontmatter = parts[1].strip()
        body = parts[2]
        body_lines = body.splitlines(keepends=True)
        return cls(frontmatter, body, body_lines)

    @property
    def project_id(self) -> str:
        m = re.search(r"^project_id:\s*(.+)$", self.frontmatter, re.MULTILINE)
        if not m:
            raise ValueError("frontmatter missing project_id")
        return m.group(1).strip()

    def apply_proposal(self, proposal: ArchiveProposal, updated_date: str) -> str:
        task_id = proposal.target.task_id
        anchor = f"<!-- task:{task_id} -->"

        # 1. Replace task block content (preserve title line)
        body = self._replace_task_block(task_id, proposal)
        if body is None:
            raise ValueError(f"Task anchor not found: {anchor}")

        # 2. Append timeline event
        body = self._append_timeline(body, proposal)

        # 3. Bump frontmatter updated date
        body = self._bump_updated(body, updated_date)

        return body

    def insert_new_task(self, proposal: ArchiveProposal) -> str:
        item_id = proposal.target.item_id
        item_anchor = f"<!-- item:{item_id} -->"

        # Find item heading line number in body
        item_line_idx = None
        body_lines = self._body_lines
        for i, line in enumerate(body_lines):
            if item_anchor in line:
                item_line_idx = i
                break

        if item_line_idx is None:
            raise ValueError(f"Item anchor not found: {item_anchor}")

        # Find insertion point: after the item heading, before next heading
        insert_idx = item_line_idx + 1
        for j in range(item_line_idx + 1, len(body_lines)):
            stripped = body_lines[j].strip()
            if stripped.startswith("#### Task:") or stripped.startswith("### Item:") or stripped.startswith("## "):
                insert_idx = j
                break
        else:
            insert_idx = len(body_lines)

        # Render task block from structured fields
        task_block = self._render_new_task_block(proposal)

        new_lines = (
            body_lines[:insert_idx]
            + [task_block + "\n"]
            + (["\n"] if insert_idx < len(body_lines) and body_lines[insert_idx - 1].strip() != "" else [])
            + body_lines[insert_idx:]
        )

        return "".join(["---\n", self.frontmatter, "\n---", "".join(new_lines)])

    # --- internal helpers ---

    def _replace_task_block(self, task_id: str, proposal: ArchiveProposal) -> str | None:
        full_text = "".join(["---\n", self.frontmatter, "\n---", "".join(self._body_lines)])

        match = re.search(
            rf"(#### Task:.*?<!-- task:{re.escape(task_id)} -->.*?\n)"
            rf"((?:-\s+(?:status|next_action|last_event_id):.*?\n)+)",
            full_text,
            re.MULTILINE,
        )
        if not match:
            return None

        title_line = match.group(1)
        replacement = (
            f"{title_line}"
            f"- status: {proposal.event.status}\n"
            f"- next_action: {proposal.event.next_action}\n"
            f"- last_event_id: {proposal.event.event_id}\n"
        )
        return full_text[: match.start()] + replacement + full_text[match.end() :]

    def _append_timeline(self, body: str, proposal: ArchiveProposal) -> str:
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        event = proposal.event
        timeline_entry = (
            f"\n- {now_iso} <!-- event:{event.event_id} -->\n"
            f"  - task_id: {event.task_id}\n"
            f"  - input: {event.input_text}\n"
            f"  - summary: {event.summary}\n"
            f"  - status: {event.status}\n"
            f"  - next_action: {event.next_action}\n"
        )
        # Insert before the first ##  after ## Timeline
        timeline_match = re.search(r"(## Timeline\s*\n)", body)
        if not timeline_match:
            raise ValueError("## Timeline section not found")
        insert_pos = timeline_match.end()
        return body[:insert_pos] + timeline_entry + body[insert_pos:]

    def _bump_updated(self, body: str, updated_date: str) -> str:
        return re.sub(
            r"(updated:\s*).*",
            rf"\g<1>{updated_date}",
            body,
            count=1,
        )

    def _render_new_task_block(self, proposal: ArchiveProposal) -> str:
        event = proposal.event
        target = proposal.target
        return (
            f"#### Task: {target.task_title} <!-- task:{target.task_id} -->\n"
            f"- status: {event.status}\n"
            f"- next_action: {event.next_action}\n"
            f"- last_event_id: {event.event_id}"
        )

    @staticmethod
    def append_attachments(body: str, proposal: ArchiveProposal, now: "datetime | None" = None) -> str:
        """Append attachment path records to ## Attachments section (MVP minimal persistence).

        Format follows schema: timestamp line + indented sub-items.
        """
        if not proposal.attachment_paths:
            return body

        attach_match = re.search(r"(## Attachments\s*\n)", body)
        if not attach_match:
            return body  # no section yet — skip rather than create (MVP)

        if now is None:
            now = datetime.now(timezone.utc)
        ts = now.strftime("%Y-%m-%dT%H:%M:%S+00:00")

        insert_pos = attach_match.end()
        entries = ""
        for path in proposal.attachment_paths:
            entries += (
                f"- {ts}\n"
                f"  - path: {path}\n"
                f"  - related_task_id: {proposal.target.task_id}\n"
                f"  - note: \n\n"
            )
        return body[:insert_pos] + entries + body[insert_pos:]


def write_project_atomically(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
