from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path

from workeventagent.models import ArchiveProposal
from workeventagent.ids import make_stable_id
from workeventagent.project_schema import find_section, schema_version
from workeventagent.work_map_store import (
    insert_item,
    insert_task,
    parse_work_map,
    update_task_state,
)


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
        target = proposal.target
        event = proposal.event
        updated = "".join(["---\n", self.frontmatter, "\n---", self.body])
        item_exists = any(
            item["item_id"] == target.item_id
            for item in parse_work_map(updated)
        )
        if target.new_item and not item_exists:
            updated = insert_item(
                updated,
                target.item_id,
                target.item_title or target.item_id,
            )
        updated = insert_task(updated, target.item_id, target.task_id, target.task_title)
        return update_task_state(
            updated,
            target.task_id,
            event.status,
            event.next_action,
            event.event_id,
        )

    # --- internal helpers ---

    def _replace_task_block(self, task_id: str, proposal: ArchiveProposal) -> str | None:
        full_text = "".join(["---\n", self.frontmatter, "\n---", "".join(self._body_lines)])

        try:
            return update_task_state(
                full_text,
                task_id,
                proposal.event.status,
                proposal.event.next_action,
                proposal.event.event_id,
            )
        except ValueError:
            return None

    def _append_timeline(self, body: str, proposal: ArchiveProposal) -> str:
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        event = proposal.event
        timeline_entry = (
            f"\n- {now_iso} <!-- event:{event.event_id} -->\n"
            f"{self._render_timeline_field('task_id', event.task_id)}"
            f"{self._render_timeline_field('input', event.input_text)}"
            f"{self._render_timeline_field('summary', event.summary)}"
            f"{self._render_timeline_field('status', event.status)}"
            f"{self._render_timeline_field('next_action', event.next_action)}"
        )
        insert_pos = find_section(body, "timeline").content_start
        return body[:insert_pos] + timeline_entry + body[insert_pos:]

    def _bump_updated(self, body: str, updated_date: str) -> str:
        return re.sub(
            r"(updated:\s*).*",
            rf"\g<1>{updated_date}",
            body,
            count=1,
        )

    @staticmethod
    def _render_timeline_field(key: str, value: object) -> str:
        text = str(value if value is not None else "").replace("\r\n", "\n").replace("\r", "\n")
        lines = text.split("\n")
        rendered = f"  - {key}: {lines[0]}\n"
        for line in lines[1:]:
            rendered += f"    {line}\n"
        return rendered

    @staticmethod
    def append_attachments(body: str, proposal: ArchiveProposal, now: "datetime | None" = None) -> str:
        """Append attachment path records to ## Attachments section (MVP minimal persistence).

        Format follows schema: timestamp line + indented sub-items.
        """
        if not proposal.attachment_paths:
            return body

        if now is None:
            now = datetime.now(timezone.utc)
        ts = now.strftime("%Y-%m-%dT%H:%M:%S+00:00")

        insert_pos = find_section(body, "attachments").content_start
        entries = ""
        for path in proposal.attachment_paths:
            if schema_version(body) >= 2:
                attachment_id = make_stable_id(f"{ts}-{path}")
                entries += (
                    f"- {path} <!-- attachment:{attachment_id} -->\n"
                    f"  - related_task_id: {proposal.target.task_id}\n"
                    f"  - note: \n\n"
                )
            else:
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
