from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass


# ── Section definitions ─────────────────────────────────────

@dataclass(frozen=True)
class SectionSpec:
    section_id: str
    title: str
    ownership: str
    legacy_heading: str | None


@dataclass(frozen=True)
class SectionSlice:
    section_id: str
    heading: str
    heading_start: int
    content_start: int
    content_end: int


SECTION_SPECS = (
    SectionSpec("project-profile", "项目档案", "reviewed", None),
    SectionSpec("current-panorama", "当前全景", "derived-reviewed", "Current Snapshot"),
    SectionSpec("work-map", "工作地图", "structured", "Work Map"),
    SectionSpec("technical-overview", "技术概览", "reviewed", None),
    SectionSpec("project-knowledge", "关键认知", "reviewed", None),
    SectionSpec("decisions", "关键决策", "append-only", "Decisions"),
    SectionSpec("attachments", "附件", "append-only", "Attachments"),
    SectionSpec("timeline", "事件证据", "append-only", "Timeline"),
    SectionSpec("rollups", "历史摘要", "derived", "Daily / Weekly Rollups"),
)
SECTION_BY_ID = {spec.section_id: spec for spec in SECTION_SPECS}


# ── Frontmatter ─────────────────────────────────────────────

def parse_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    result: dict[str, str] = {}
    for raw in parts[1].splitlines():
        if ":" not in raw:
            continue
        key, _, value = raw.partition(":")
        result[key.strip()] = value.strip()
    return result


def schema_version(text: str) -> int:
    raw = parse_frontmatter(text).get("schema_version", "1")
    return int(raw) if raw.isdigit() else 1


def update_frontmatter(text: str, fields: dict[str, str]) -> str:
    if not text.startswith("---"):
        raise ValueError("missing frontmatter")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise ValueError("malformed frontmatter")
    fm = parse_frontmatter(text)
    fm.update(fields)
    lines = ["---"]
    for key in sorted(fm):
        lines.append(f"{key}: {fm[key]}")
    lines.append("---")
    return "\n".join(lines) + text[text.index(parts[2], len(parts[0]) + 3 + len(parts[1])) - 3:]


def metadata_hash(text: str) -> str:
    fm = parse_frontmatter(text)
    return _sha256(f"status={fm.get('status', '')}\nphase={fm.get('phase', '')}\n")


# ── Section lookup ──────────────────────────────────────────

def _sha256(content: str) -> str:
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


def find_section(text: str, section_id: str) -> SectionSlice:
    spec = SECTION_BY_ID.get(section_id)
    if spec is None:
        raise ValueError(f"unknown section: {section_id}")
    anchored = re.compile(
        rf"^##[^\n]*<!--\s*section:{re.escape(section_id)}\s*-->[^\n]*$",
        re.MULTILINE,
    )
    match = anchored.search(text)
    if match is None and spec.legacy_heading:
        match = re.search(rf"^## {re.escape(spec.legacy_heading)}\s*$", text, re.MULTILINE)
    if match is None:
        raise ValueError(f"section not found: {section_id}")
    next_heading = re.search(r"^##\s+", text[match.end():], re.MULTILINE)
    content_end = match.end() + next_heading.start() if next_heading else len(text)
    content_start = match.end()
    if content_start < len(text) and text[content_start] == "\n":
        content_start += 1
    return SectionSlice(section_id, match.group(0), match.start(), content_start, content_end)


def section_content(text: str, section_id: str) -> str:
    section = find_section(text, section_id)
    return text[section.content_start:section.content_end].lstrip("\n").rstrip("\n") + "\n"


def section_hash(text: str, section_id: str) -> str:
    return _sha256(section_content(text, section_id))


# ── Content validation & replacement ────────────────────────

def validate_reviewed_content(content: str) -> None:
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    if "<!--" in normalized or re.search(r"(?m)^#{1,6}\s", normalized) or "\n---\n" in normalized:
        raise ValueError("reviewed content contains control syntax")


def replace_section_content(text: str, section_id: str, content: str) -> str:
    validate_reviewed_content(content)
    section = find_section(text, section_id)
    rendered = "\n" + content.strip("\n") + "\n\n"
    return text[:section.content_start - 1] + rendered + text[section.content_end:]


# ── Timeline parser ─────────────────────────────────────────

_EVENT_LINE_RE = re.compile(r"^- (\S+)\s*<!--\s*event:(.+?)\s*-->")
_SUB_KV_RE = re.compile(r"^  - ([a-z_]+):\s*(.*)")


def parse_timeline_events(text: str) -> list[dict]:
    """Parse the Timeline section into a list of event dicts, newest-first.
    
    For v2, uses the stable ``timeline`` section anchor (``<!-- section:timeline -->``).
    For v1, falls back to ``## Timeline`` heading.
    The parsing logic is the same for both schemas.
    """
    section = find_section(text, "timeline")
    body = text[section.content_start:section.content_end]
    events: list[dict] = []
    current_event: dict | None = None
    current_key: str | None = None

    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("## ") and "section:" not in stripped:
            if current_event:
                events.append(current_event)
                current_event = None
                current_key = None
            break

        ev_match = _EVENT_LINE_RE.match(line)
        if ev_match:
            if current_event:
                events.append(current_event)
            current_event = {
                "timestamp": ev_match.group(1),
                "event_id": ev_match.group(2).strip(),
            }
            current_key = None
            continue

        if current_event is not None:
            kv_match = _SUB_KV_RE.match(line)
            if kv_match:
                current_key = kv_match.group(1).strip()
                current_event[current_key] = kv_match.group(2).strip()
                continue
            if current_key and line.startswith("    "):
                current_event[current_key] = (
                    current_event.get(current_key, "") + "\n" + line[4:].rstrip()
                )

    if current_event:
        events.append(current_event)

    return events


# ── Attachment parser ───────────────────────────────────────

_ATTACHMENT_LINE_RE = re.compile(r"^-\s+(\S+)\s*<!--\s*attachment:(.+?)\s*-->")


def parse_attachment_records(text: str) -> list[dict]:
    """Parse the Attachments section into a list of attachment records."""
    section = find_section(text, "attachments")
    body = text[section.content_start:section.content_end]
    records: list[dict] = []
    current: dict | None = None

    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            break

        att_match = _ATTACHMENT_LINE_RE.match(line)
        if att_match:
            if current:
                records.append(current)
            current = {
                "path": att_match.group(1).strip(),
                "attachment_id": att_match.group(2).strip(),
            }
            continue

        if current is not None:
            kv_match = _SUB_KV_RE.match(line)
            if kv_match:
                current[kv_match.group(1).strip()] = kv_match.group(2).strip()

    if current:
        records.append(current)

    return records
