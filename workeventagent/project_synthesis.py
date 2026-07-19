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
import os
import re
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from workeventagent.ids import make_unique_stable_id
from workeventagent.project_schema import (
    find_section,
    parse_frontmatter,
    parse_timeline_events,
    schema_version,
    section_content,
    section_hash,
    validate_reviewed_content,
    update_frontmatter,
)
from workeventagent.markdown_store import write_project_atomically
from workeventagent.index_store import init_db, rebuild_index
from workeventagent.knowledge_store import get_proposal, transition_proposal


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


def _datetime_of(timestamp: str) -> datetime:
    try:
        value = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid Timeline timestamp: {timestamp}") from exc
    if value.tzinfo is None:
        raise ValueError(f"Timeline timestamp requires timezone: {timestamp}")
    return value


def _date_of(timestamp: str) -> date:
    return _datetime_of(timestamp).astimezone().date()


def select_source_events(
    project_text: str,
    *,
    event_ids: list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    range_start_utc: str | None = None,
    range_end_utc: str | None = None,
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
    if bool(range_start_utc) != bool(range_end_utc):
        raise ValueError("UTC range requires both start and end")
    if range_start_utc and range_end_utc:
        range_start = _datetime_of(range_start_utc).astimezone(timezone.utc)
        range_end = _datetime_of(range_end_utc).astimezone(timezone.utc)
        if range_end <= range_start:
            raise ValueError("UTC range end must be after start")
        return [
            dict(event)
            for event in events
            if range_start
            <= _datetime_of(str(event.get("timestamp", ""))).astimezone(timezone.utc)
            < range_end
        ]
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


def _safe_module_title(value: object) -> str:
    title = str(value).strip()
    if (
        not title
        or any(ord(character) < 32 for character in title)
        or ":" in title
        or "#" in title
        or title[0] in "-?[]{}&*!|>'\"%@`"
    ):
        raise ValueError("document title must be a safe single-line frontmatter scalar")
    return title


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

    title = _safe_module_title(suggestion.get("title", ""))
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
        "title": title,
        "retained_summary": retained,
        "module_id": module_id,
        "filename": filename,
        "order": order,
        "target_path": f"{project_id}/docs/{filename}",
        "preview": preview,
        "preview_hash": _content_hash(preview),
        "module_updated": updated,
        "linked_section_proposal_id": linked_section_bundle["proposal_id"],
        "linked_technical_overview_hash": technical_change["target_section_hash"],
        "created_at": created_at,
        "updated_at": created_at,
        "supersedes": None,
    }


def _whole_hash(content: str) -> str:
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


def _replace_controlled_section(text: str, proposal_id: str, source_ids: list[str], change: dict) -> str:
    target = str(change.get("target_section", ""))
    if target not in ALLOWED_TARGETS:
        raise ValueError(f"unsupported target section: {target}")
    after = str(change.get("after", ""))
    lines = after.lstrip("\n").splitlines()
    expected_meta = (
        f"<!-- panorama-meta source_events={','.join(source_ids)} "
        f"proposal={proposal_id} -->"
    )
    if not lines or lines[0] != expected_meta:
        raise ValueError(f"invalid wrapper metadata for {target}")
    narrative = "\n".join(lines[1:]).strip("\n")
    validate_reviewed_content(narrative)
    if _content_hash(after) != change.get("target_section_hash"):
        raise ValueError(f"target hash mismatch for {target}")
    section = find_section(text, target)
    rendered = "\n" + after.strip("\n") + "\n\n"
    return text[: section.content_start - 1] + rendered + text[section.content_end :]


def _validate_source_snapshots(text: str, source_events: list[dict]) -> list[dict]:
    event_ids = [str(event.get("event_id", "")) for event in source_events]
    current = select_source_events(text, event_ids=event_ids)
    if current != source_events:
        raise ValueError("source event snapshot changed")
    return current


def _mark_proposal_stale(workspace: Path, proposal: dict, reason: str) -> dict:
    current = get_proposal(workspace, proposal["proposal_id"])
    if current["state"] == "applying":
        return transition_proposal(
            workspace,
            current["proposal_id"],
            current["version"],
            {"applying"},
            "stale",
            {"stale_reason": reason},
        )
    return current


def _apply_section_from_applying(
    project_path: Path,
    db_path: Path,
    proposal: dict,
    today: str,
) -> dict:
    workspace = project_path.parent
    try:
        text = project_path.read_text(encoding="utf-8")
        if schema_version(text) < 2:
            raise ValueError("Phase B requires schema v2")
        metadata = parse_frontmatter(text)
        if metadata.get("project_id") != proposal.get("project_id"):
            raise ValueError("project identity changed")
        current_sources = _validate_source_snapshots(text, list(proposal.get("source_events", [])))
        source_ids = [str(event["event_id"]) for event in current_sources]
        changes = list(proposal.get("changes", []))
        if not changes:
            raise ValueError("section bundle is empty")
        for change in changes:
            target = str(change.get("target_section", ""))
            if target not in ALLOWED_TARGETS:
                raise ValueError(f"unsupported target section: {target}")
            if section_hash(text, target) != change.get("base_section_hash"):
                raise ValueError(f"stale base hash for {target}")
        rendered = text
        for change in changes:
            rendered = _replace_controlled_section(
                rendered, proposal["proposal_id"], source_ids, change
            )
        rendered = update_frontmatter(rendered, {"updated": today})
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        stale = _mark_proposal_stale(workspace, proposal, str(exc))
        return {"ok": False, "kind": "stale", "error": str(exc), "proposal": stale}

    write_project_atomically(project_path, rendered)
    readback = project_path.read_text(encoding="utf-8")
    failed = [
        change["target_section"]
        for change in proposal["changes"]
        if section_hash(readback, change["target_section"]) != change["target_section_hash"]
        or f"proposal={proposal['proposal_id']}" not in section_content(readback, change["target_section"])
    ]
    if failed:
        return {
            "ok": False,
            "kind": "readback_failed",
            "error": f"read-back verification failed for: {', '.join(failed)}",
            "proposal": get_proposal(workspace, proposal["proposal_id"]),
        }
    current = get_proposal(workspace, proposal["proposal_id"])
    applied = transition_proposal(
        workspace,
        current["proposal_id"],
        current["version"],
        {"applying"},
        "applied",
        {"applied_project_hash": _whole_hash(readback)},
    )
    try:
        init_db(db_path)
        rebuild_index(db_path, [project_path])
    except Exception as exc:
        return {
            "ok": True,
            "kind": "applied_index_warning",
            "warning": str(exc),
            "proposal": applied,
        }
    return {"ok": True, "kind": "applied", "proposal": applied}


def apply_section_bundle(
    project_path: Path,
    db_path: Path,
    bundle: dict,
    expected_version: int,
    today: str,
) -> dict:
    project_path = Path(project_path)
    workspace = project_path.parent
    try:
        trusted = get_proposal(workspace, str(bundle.get("proposal_id", "")))
        if trusted != bundle:
            raise ValueError("proposal payload does not match durable ledger")
        applying = transition_proposal(
            workspace,
            trusted["proposal_id"],
            expected_version,
            {"needs_confirmation"},
            "applying",
            {"apply_started_at": _iso()},
        )
    except (TypeError, ValueError) as exc:
        return {"ok": False, "kind": "apply_conflict", "error": str(exc)}
    return _apply_section_from_applying(project_path, Path(db_path), applying, today)


def _validate_module_contract(preview: str, proposal: dict) -> None:
    if not preview.startswith("---\n"):
        raise ValueError("module preview has invalid frontmatter")
    header, separator, _body = preview[4:].partition("\n---\n")
    if not separator:
        raise ValueError("module preview has invalid frontmatter")
    entries: list[tuple[str, str]] = []
    for line in header.splitlines():
        key, marker, value = line.partition(":")
        if not marker or key != key.strip() or not key:
            raise ValueError("module preview has invalid frontmatter")
        entries.append((key, value.strip()))
    expected_keys = (
        "doc_kind",
        "project_id",
        "module_id",
        "title",
        "order",
        "include_in_compendium",
        "updated",
    )
    if tuple(key for key, _value in entries) != expected_keys:
        raise ValueError("module preview frontmatter keys do not match the module contract")
    metadata = dict(entries)
    required = {
        "doc_kind": "project_module",
        "project_id": str(proposal["project_id"]),
        "module_id": str(proposal["module_id"]),
        "title": _safe_module_title(proposal.get("title", "")),
        "order": str(proposal["order"]),
        "include_in_compendium": "true",
        "updated": str(proposal.get("module_updated", "")),
    }
    if metadata != required:
        raise ValueError("module preview frontmatter values do not match the module contract")
    conclusion = re.search(
        r"<!-- section:module-conclusion -->\s*(.*?)\s*## .*?<!-- section:module-body -->",
        preview,
        re.DOTALL,
    )
    body = re.search(r"<!-- section:module-body -->\s*(.+?)\s*$", preview, re.DOTALL)
    if not conclusion or not conclusion.group(1).strip() or not body or not body.group(1).strip():
        raise ValueError("module conclusion and body are required")
    if _content_hash(preview) != proposal.get("preview_hash"):
        raise ValueError("module preview hash changed")


def _document_target(project_path: Path, proposal: dict) -> Path:
    relative = Path(str(proposal.get("target_path", "")))
    expected = Path(str(proposal["project_id"])) / "docs" / str(proposal["filename"])
    if relative.is_absolute() or relative != expected or len(relative.parts) != 3:
        raise ValueError("document target must be one wrapper-owned file under project docs")
    target = project_path.parent / relative
    docs = project_path.parent / str(proposal["project_id"]) / "docs"
    if target.parent.resolve() != docs.resolve():
        raise ValueError("document target escapes project docs")
    return target


def _validate_document_link(project_path: Path, proposal: dict) -> None:
    workspace = project_path.parent
    linked = get_proposal(workspace, str(proposal.get("linked_section_proposal_id", "")))
    if linked.get("state") != "applied":
        raise ValueError("linked Technical Overview proposal is not applied")
    technical = next(
        (
            change
            for change in linked.get("changes", [])
            if change.get("target_section") == "technical-overview"
        ),
        None,
    )
    text = project_path.read_text(encoding="utf-8")
    if technical is None or technical.get("target_section_hash") != proposal.get("linked_technical_overview_hash"):
        raise ValueError("linked Technical Overview identity changed")
    if section_hash(text, "technical-overview") != proposal.get("linked_technical_overview_hash"):
        raise ValueError("current Technical Overview no longer matches the confirmed proposal")
    if _normalized(str(proposal.get("retained_summary", ""))) not in _normalized(
        section_content(text, "technical-overview")
    ):
        raise ValueError("retained Technical Overview summary is missing")


def _atomic_create_module(target: Path, preview: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise ValueError("document target already exists")
    tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(preview, encoding="utf-8")
    if target.exists():
        tmp.unlink(missing_ok=True)
        raise ValueError("document target appeared during create")
    os.replace(tmp, target)


def _apply_document_from_applying(project_path: Path, proposal: dict) -> dict:
    workspace = project_path.parent
    try:
        target = _document_target(project_path, proposal)
        preview = str(proposal.get("preview", ""))
        _validate_module_contract(preview, proposal)
        _validate_document_link(project_path, proposal)
        if target.exists():
            if _content_hash(target.read_text(encoding="utf-8")) == proposal.get("preview_hash"):
                current = get_proposal(workspace, proposal["proposal_id"])
                applied = transition_proposal(
                    workspace,
                    current["proposal_id"],
                    current["version"],
                    {"applying"},
                    "applied",
                    {"applied_document_hash": proposal["preview_hash"]},
                )
                return {"ok": True, "kind": "applied", "proposal": applied}
            raise ValueError("document target exists with different content")
        module_ids, filenames, _orders = _scan_modules(project_path)
        if proposal["module_id"] in module_ids or proposal["filename"] in filenames:
            raise ValueError("duplicate module identity or filename")
        _atomic_create_module(target, preview)
        if _content_hash(target.read_text(encoding="utf-8")) != proposal["preview_hash"]:
            return {
                "ok": False,
                "kind": "readback_failed",
                "error": "module read-back verification failed",
                "proposal": get_proposal(workspace, proposal["proposal_id"]),
            }
        current = get_proposal(workspace, proposal["proposal_id"])
        applied = transition_proposal(
            workspace,
            current["proposal_id"],
            current["version"],
            {"applying"},
            "applied",
            {"applied_document_hash": proposal["preview_hash"]},
        )
        return {"ok": True, "kind": "applied", "proposal": applied}
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        stale = _mark_proposal_stale(workspace, proposal, str(exc))
        return {"ok": False, "kind": "stale", "error": str(exc), "proposal": stale}


def apply_document_proposal(
    project_path: Path,
    proposal: dict,
    expected_version: int,
    today: str,
) -> dict:
    del today  # The immutable preview already owns its confirmed updated date.
    project_path = Path(project_path)
    workspace = project_path.parent
    try:
        trusted = get_proposal(workspace, str(proposal.get("proposal_id", "")))
        if trusted != proposal:
            raise ValueError("proposal payload does not match durable ledger")
        # A premature click is a confirmation conflict, not a permanently stale
        # proposal. Revalidate the same link again after the CAS transition.
        _document_target(project_path, trusted)
        _validate_module_contract(str(trusted.get("preview", "")), trusted)
        _validate_document_link(project_path, trusted)
        applying = transition_proposal(
            workspace,
            trusted["proposal_id"],
            expected_version,
            {"needs_confirmation"},
            "applying",
            {"apply_started_at": _iso()},
        )
    except (TypeError, ValueError) as exc:
        return {"ok": False, "kind": "apply_conflict", "error": str(exc)}
    return _apply_document_from_applying(project_path, applying)


def recover_applying_proposal(project_path: Path, proposal: dict) -> str:
    project_path = Path(project_path)
    workspace = project_path.parent
    trusted = get_proposal(workspace, str(proposal.get("proposal_id", "")))
    if trusted != proposal or trusted.get("state") != "applying":
        raise ValueError("proposal is not the durable applying entity")
    if trusted.get("proposal_kind") == "module_document":
        target = _document_target(project_path, trusted)
        if target.exists():
            if _content_hash(target.read_text(encoding="utf-8")) == trusted.get("preview_hash"):
                transition_proposal(
                    workspace,
                    trusted["proposal_id"],
                    trusted["version"],
                    {"applying"},
                    "applied",
                    {"applied_document_hash": trusted["preview_hash"]},
                )
                return "applied"
            _mark_proposal_stale(workspace, trusted, "document target has different content")
            return "stale"
        result = _apply_document_from_applying(project_path, trusted)
        return "resumed" if result.get("ok") else str(result.get("kind", "stale"))

    text = project_path.read_text(encoding="utf-8")
    current_hashes = [section_hash(text, change["target_section"]) for change in trusted["changes"]]
    target_hashes = [change["target_section_hash"] for change in trusted["changes"]]
    base_hashes = [change["base_section_hash"] for change in trusted["changes"]]
    if current_hashes == target_hashes:
        transition_proposal(
            workspace,
            trusted["proposal_id"],
            trusted["version"],
            {"applying"},
            "applied",
            {"applied_project_hash": _whole_hash(text)},
        )
        return "applied"
    if current_hashes == base_hashes:
        result = _apply_section_from_applying(
            project_path,
            workspace / "index.sqlite",
            trusted,
            datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        )
        return "resumed" if result.get("ok") else str(result.get("kind", "stale"))
    _mark_proposal_stale(workspace, trusted, "mixed base/target or unknown section hashes")
    return "stale"
