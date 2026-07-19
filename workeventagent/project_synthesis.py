"""Deterministic F007 knowledge proposal construction.

Agents supply bounded narrative only.  This module owns source selection,
identities, hashes, diffs, control metadata, and optional module previews.
Nothing in this module writes project or module Markdown.
"""

from __future__ import annotations

import copy
import difflib
import hashlib
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

from workeventagent.ids import make_unique_stable_id
from workeventagent.project_schema import (
    parse_frontmatter,
    parse_timeline_events,
    schema_version,
    section_content,
    section_hash,
    validate_reviewed_content,
)


ALLOWED_TARGETS = {"current-panorama", "technical-overview", "project-knowledge"}


def _iso(now: datetime | None = None) -> str:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _id(prefix: str, payload: object, now: datetime | None = None) -> str:
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S")
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return f"{prefix}-{stamp}-{hashlib.sha256(encoded).hexdigest()[:12]}"


def _content_hash(content: str) -> str:
    normalized = content.lstrip("\n").rstrip("\n") + "\n"
    return "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _date_of(timestamp: str) -> date:
    try:
        return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).date()
    except ValueError as exc:
        raise ValueError(f"invalid Timeline timestamp: {timestamp}") from exc


def select_source_events(
    project_text: str,
    *,
    event_ids: list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict]:
    events = parse_timeline_events(project_text)
    if event_ids is not None:
        by_id = {str(event.get("event_id", "")): event for event in events}
        selected: list[dict] = []
        for event_id in event_ids:
            if event_id not in by_id:
                raise ValueError(f"missing source event: {event_id}")
            selected.append(dict(by_id[event_id]))
        return selected
    if date_from is None and date_to is None:
        return [dict(event) for event in events]
    try:
        start = date.fromisoformat(date_from) if date_from else date.min
        end = date.fromisoformat(date_to) if date_to else date.max
    except ValueError as exc:
        raise ValueError("date range must use YYYY-MM-DD") from exc
    if end < start:
        raise ValueError("date_to must be on or after date_from")
    return [
        dict(event)
        for event in events
        if start <= _date_of(str(event.get("timestamp", ""))) <= end
    ]


def _render_content(content: dict) -> str:
    if not isinstance(content, dict):
        raise ValueError("content must be an object")
    paragraphs = content.get("paragraphs")
    bullets = content.get("bullets")
    if not isinstance(paragraphs, list) or not isinstance(bullets, list):
        raise ValueError("content paragraphs and bullets must be arrays")
    if any(not isinstance(value, str) for value in paragraphs + bullets):
        raise ValueError("content values must be strings")
    clean_paragraphs = [value.strip() for value in paragraphs if value.strip()]
    clean_bullets = [value.strip() for value in bullets if value.strip()]
    parts: list[str] = []
    if clean_paragraphs:
        parts.append("\n\n".join(clean_paragraphs))
    if clean_bullets:
        parts.append("\n".join(f"- {value}" for value in clean_bullets))
    rendered = "\n\n".join(parts)
    validate_reviewed_content(rendered)
    return rendered


def _current_sources(project_text: str, source_events: list[dict]) -> list[dict]:
    event_ids = [str(event.get("event_id", "")) for event in source_events]
    if any(not event_id for event_id in event_ids):
        raise ValueError("source events require event_id")
    return select_source_events(project_text, event_ids=event_ids)


def _unified_diff(section_id: str, before: str, after: str) -> str:
    return "\n".join(
        difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile=f"before/{section_id}",
            tofile=f"after/{section_id}",
            lineterm="",
        )
    )


def build_section_bundle(
    project_path: Path,
    trigger: str,
    source_events: list[dict],
    agent_output: dict,
    now: datetime | None = None,
) -> dict | None:
    project_path = Path(project_path)
    text = project_path.read_text(encoding="utf-8")
    if schema_version(text) < 2:
        raise ValueError("Phase B requires a schema-v2 project")
    metadata = parse_frontmatter(text)
    project_id = metadata.get("project_id", "")
    if not project_id:
        raise ValueError("project_id is required")
    current_sources = _current_sources(text, source_events)
    raw_changes = agent_output.get("changes")
    if not isinstance(raw_changes, list):
        raise ValueError("agent changes must be an array")
    targets = [change.get("target_section") for change in raw_changes if isinstance(change, dict)]
    if len(targets) != len(raw_changes) or len(set(targets)) != len(targets):
        raise ValueError("duplicate or malformed target section")
    forbidden = [target for target in targets if target not in ALLOWED_TARGETS]
    if forbidden:
        raise ValueError(f"unsupported target section: {forbidden[0]}")
    if not raw_changes:
        return None

    proposal_id = _id(
        "kp",
        {
            "project_id": project_id,
            "trigger": trigger,
            "source_event_ids": [event["event_id"] for event in current_sources],
            "changes": raw_changes,
            "created_at": _iso(now),
        },
        now,
    )
    source_ids = [str(event["event_id"]) for event in current_sources]
    changes: list[dict] = []
    for raw in raw_changes:
        target = str(raw["target_section"])
        narrative = _render_content(raw.get("content", {}))
        if not narrative:
            continue
        before = section_content(text, target)
        meta = (
            f"<!-- panorama-meta source_events={','.join(source_ids)} "
            f"proposal={proposal_id} -->"
        )
        after = meta + ("\n\n" + narrative if narrative else "") + "\n"
        changes.append(
            {
                "change_id": f"change-{target}",
                "target_section": target,
                "reason": str(raw.get("reason", "")),
                "base_section_hash": section_hash(text, target),
                "target_section_hash": _content_hash(after),
                "before": before,
                "after": after,
                "diff": _unified_diff(target, before, after),
            }
        )
    if not changes:
        return None
    created_at = _iso(now)
    return {
        "schema_version": 1,
        "proposal_id": proposal_id,
        "proposal_kind": "section_bundle",
        "state": "needs_confirmation",
        "version": 1,
        "project_id": project_id,
        "project_path": str(project_path),
        "trigger": trigger,
        "source_events": current_sources,
        "changes": changes,
        "created_at": created_at,
        "updated_at": created_at,
        "supersedes": None,
    }


def revise_section_bundle(
    bundle: dict,
    included_change_ids: list[str],
    now: datetime | None = None,
) -> tuple[dict, dict]:
    if bundle.get("proposal_kind") != "section_bundle":
        raise ValueError("only section bundles can be revised")
    selected = set(included_change_ids)
    changes = [copy.deepcopy(change) for change in bundle.get("changes", []) if change.get("change_id") in selected]
    if not changes or len(changes) != len(selected):
        raise ValueError("revision must select one or more existing changes")
    old_id = str(bundle["proposal_id"])
    created_at = _iso(now)
    new_id = _id("kp", {"supersedes": old_id, "changes": included_change_ids, "created_at": created_at}, now)
    for change in changes:
        change["after"] = re.sub(
            r"proposal=[^\s>]+",
            f"proposal={new_id}",
            str(change["after"]),
            count=1,
        )
        change["target_section_hash"] = _content_hash(change["after"])
        change["diff"] = _unified_diff(change["target_section"], change["before"], change["after"])
    revised = copy.deepcopy(bundle)
    revised.update(
        {
            "proposal_id": new_id,
            "state": "needs_confirmation",
            "version": 1,
            "changes": changes,
            "created_at": created_at,
            "updated_at": created_at,
            "supersedes": old_id,
        }
    )
    superseded = copy.deepcopy(bundle)
    superseded["state"] = "superseded"
    superseded["version"] = int(bundle.get("version", 1)) + 1
    superseded["updated_at"] = created_at
    return revised, superseded


def _scan_modules(project_path: Path) -> tuple[set[str], set[str], list[int]]:
    project_id = parse_frontmatter(project_path.read_text(encoding="utf-8")).get("project_id", "")
    docs = project_path.parent / project_id / "docs"
    module_ids: set[str] = set()
    filenames: set[str] = set()
    orders: list[int] = []
    if not docs.exists():
        return module_ids, filenames, orders
    for path in sorted(docs.rglob("*.md")):
        try:
            metadata = parse_frontmatter(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        if metadata.get("doc_kind") != "project_module" or metadata.get("project_id") != project_id:
            continue
        module_ids.add(metadata.get("module_id", ""))
        filenames.add(path.name)
        try:
            orders.append(int(metadata.get("order", "0")))
        except ValueError:
            continue
    return module_ids, filenames, orders


def _normalized(value: str) -> str:
    return " ".join(value.split())


def build_document_proposal(
    project_path: Path,
    trigger: str,
    source_events: list[dict],
    suggestion: dict,
    now: datetime | None = None,
    *,
    linked_section_bundle: dict | None = None,
) -> dict | None:
    if not suggestion:
        return None
    project_path = Path(project_path)
    text = project_path.read_text(encoding="utf-8")
    metadata = parse_frontmatter(text)
    project_id = metadata.get("project_id", "")
    current_sources = _current_sources(text, source_events)
    retained = str(suggestion.get("retained_summary", "")).strip()
    technical_change = None
    if linked_section_bundle is not None:
        technical_change = next(
            (
                change
                for change in linked_section_bundle.get("changes", [])
                if change.get("target_section") == "technical-overview"
            ),
            None,
        )
    if (
        not retained
        or technical_change is None
        or _normalized(retained) not in _normalized(str(technical_change.get("after", "")))
    ):
        raise ValueError("document suggestion requires a linked Technical Overview retained summary")

    title = str(suggestion.get("title", "")).strip()
    purpose = str(suggestion.get("purpose", "")).strip()
    conclusion = _render_content(suggestion.get("module_conclusion", {}))
    body = _render_content(suggestion.get("module_body", {}))
    if not title or not purpose or not conclusion or not body:
        raise ValueError("document suggestion is incomplete")
    module_ids, filenames, orders = _scan_modules(project_path)
    module_id = make_unique_stable_id(title, module_ids)
    filename = f"{module_id}.md"
    while filename in filenames:
        module_id = make_unique_stable_id(title, module_ids | {module_id})
        filename = f"{module_id}.md"
    order = ((max(orders) // 10) + 1) * 10 if orders else 10
    updated = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    preview = (
        "---\n"
        "doc_kind: project_module\n"
        f"project_id: {project_id}\n"
        f"module_id: {module_id}\n"
        f"title: {title}\n"
        f"order: {order}\n"
        "include_in_compendium: true\n"
        f"updated: {updated}\n"
        "---\n"
        f"# {title}\n\n"
        "## 模块结论 <!-- section:module-conclusion -->\n\n"
        f"{conclusion}\n\n"
        "## 详细内容 <!-- section:module-body -->\n\n"
        f"{body}\n"
    )
    created_at = _iso(now)
    proposal_id = _id(
        "kd",
        {
            "project_id": project_id,
            "module_id": module_id,
            "source_event_ids": [event["event_id"] for event in current_sources],
            "linked_proposal_id": linked_section_bundle["proposal_id"],
            "created_at": created_at,
        },
        now,
    )
    return {
        "schema_version": 1,
        "proposal_id": proposal_id,
        "proposal_kind": "module_document",
        "state": "needs_confirmation",
        "version": 1,
        "project_id": project_id,
        "project_path": str(project_path),
        "trigger": trigger,
        "source_events": current_sources,
        "purpose": purpose,
        "retained_summary": retained,
        "module_id": module_id,
        "filename": filename,
        "order": order,
        "target_path": f"{project_id}/docs/{filename}",
        "preview": preview,
        "preview_hash": _content_hash(preview),
        "linked_section_proposal_id": linked_section_bundle["proposal_id"],
        "linked_technical_overview_hash": technical_change["target_section_hash"],
        "created_at": created_at,
        "updated_at": created_at,
        "supersedes": None,
    }
