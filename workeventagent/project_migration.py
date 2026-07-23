"""Previewable and recoverable v1→v2 project migration.

Transformation is pure: preview produces a diff and identity comparison;
apply creates a backup, atomically replaces the file, and verifies.
"""
from __future__ import annotations

import difflib
import hashlib
import os
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from workeventagent.project_schema import (
    SECTION_SPECS,
    parse_frontmatter,
    schema_version,
)
from workeventagent.work_map_store import parse_work_map, render_v2_item, render_v2_task
from workeventagent.index_store import init_db, rebuild_index


# ── Identity manifest ───────────────────────────────────────

@dataclass(frozen=True)
class IdentityManifest:
    project_id: str
    item_ids: tuple[str, ...]
    task_ids: tuple[str, ...]
    event_ids: tuple[str, ...]
    timeline_event_count: int


_TL_EVENT_RE = re.compile(r"<!--\s*event:(.+?)\s*-->")


def identity_manifest(text: str) -> IdentityManifest:
    fm = parse_frontmatter(text)
    items = parse_work_map(text)
    item_ids = tuple(sorted(it["item_id"] for it in items))
    task_ids = tuple(sorted(
        t["task_id"] for it in items for t in it.get("tasks", [])
    ))
    event_ids = tuple(sorted(set(_TL_EVENT_RE.findall(text))))
    return IdentityManifest(
        project_id=fm.get("project_id", ""),
        item_ids=item_ids,
        task_ids=task_ids,
        event_ids=event_ids,
        timeline_event_count=len(event_ids),
    )


# ── Preview ─────────────────────────────────────────────────

@dataclass(frozen=True)
class MigrationPreview:
    original_text: str
    migrated_text: str
    source_hash: str
    source_schema: int
    target_schema: int
    before_identity: IdentityManifest
    after_identity: IdentityManifest
    diff: str
    summary: dict[str, int | str]


def preview_v1_to_v2(text: str, status: str, phase: str) -> MigrationPreview:
    if not status:
        raise ValueError("status is required")
    if not phase:
        raise ValueError("phase is required")
    if schema_version(text) != 1:
        raise ValueError("source must be schema v1")

    before = identity_manifest(text)
    migrated = _transform_v1_to_v2(text, status, phase)
    after = identity_manifest(migrated)
    source_hash = _sha256(text)

    diff = "\n".join(difflib.unified_diff(
        text.splitlines(keepends=True),
        migrated.splitlines(keepends=True),
        fromfile="schema-v1",
        tofile="schema-v2",
    ))

    return MigrationPreview(
        original_text=text,
        migrated_text=migrated,
        source_hash=source_hash,
        source_schema=1,
        target_schema=2,
        before_identity=before,
        after_identity=after,
        diff=diff,
        summary={
            "items": len(before.item_ids),
            "tasks": len(before.task_ids),
            "events": before.timeline_event_count,
        },
    )


def _transform_v1_to_v2(text: str, status: str, phase: str) -> str:
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise ValueError("missing frontmatter")
    fm_text = parts[1]
    body = parts[2]
    fm = {}
    for raw in fm_text.splitlines():
        if ":" not in raw:
            continue
        k, _, v = raw.partition(":")
        fm[k.strip()] = v.strip()
    fm["schema_version"] = "2"
    fm["status"] = status
    fm["phase"] = phase

    # Build frontmatter
    fm_lines = ["---"]
    for key in sorted(fm):
        fm_lines.append(f"{key}: {fm[key]}")
    fm_lines.append("---")

    # Transform sections
    # 1. Replace Current Snapshot → Current Panorama + add empty sections
    # 2. Convert Work Map headings
    # 3. Convert Timeline heading
    lines_out: list[str] = []

    for line in body.splitlines(keepends=True):
        stripped = line.strip()

        # Current Snapshot → 当前全景
        if stripped == "## Current Snapshot":
            lines_out.append("## 当前全景 <!-- section:current-panorama -->\n")
            # Insert Project Profile before it
            lines_out.insert(
                lines_out.index(lines_out[-1]),
                "## 项目档案 <!-- section:project-profile -->\n\n"
                "### 背景\n\n### 目标\n\n### 范围\n\n### 成功标准\n\n",
            )
            continue

        # Work Map → 工作地图
        if stripped == "## Work Map":
            lines_out.append("## 工作地图 <!-- section:work-map -->\n")
            continue

        # Convert Work Map items and tasks
        item_match = re.match(r"^###\s+Item:\s+(.+?)\s*<!--\s*item:(.+?)\s*-->\s*$", stripped)
        if item_match:
            lines_out.append(f"### 工作项：{item_match.group(1).strip()} <!-- item:{item_match.group(2).strip()} -->\n")
            continue

        task_match = re.match(r"^####\s+Task:\s+(.+?)\s*<!--\s*task:(.+?)\s*-->\s*$", stripped)
        if task_match:
            lines_out.append(f"#### [ ] 任务：{task_match.group(1).strip()} <!-- task:{task_match.group(2).strip()} -->\n")
            continue

        # Convert v1 metadata lines
        status_match = re.match(r"^-\s*status:\s*(.*)$", stripped)
        if status_match:
            # Skip - handled by checkbox (parsed after migration)
            continue

        next_match = re.match(r"^-\s*next_action:\s*(.*)$", stripped)
        if next_match:
            lines_out.append(f"- 下一步：{next_match.group(1).strip()}\n")
            continue

        conclusion_match = re.match(r"^-\s*conclusion:\s*(.*)$", stripped)
        if conclusion_match:
            lines_out.append(f"- 结论：{conclusion_match.group(1).strip()}\n")
            continue

        last_match = re.match(r"^-\s*last_event_id:\s*(.*)$", stripped)
        if last_match:
            lines_out.append(f"<!-- task-meta:last_event_id={last_match.group(1).strip()} -->\n")
            continue

        # Decisions → 关键决策
        if stripped == "## Decisions":
            lines_out.append("## 关键决策 <!-- section:decisions -->\n")
            continue

        # Attachments → 附件
        if stripped == "## Attachments":
            lines_out.append("## 附件 <!-- section:attachments -->\n")
            continue

        # Timeline → 事件证据
        if stripped == "## Timeline":
            lines_out.append("## 事件证据 <!-- section:timeline -->\n")
            continue

        # Daily / Weekly Rollups → 历史摘要
        if stripped == "## Daily / Weekly Rollups":
            lines_out.append("## 历史摘要 <!-- section:rollups -->\n")
            continue

        lines_out.append(line)

    result = "\n".join(fm_lines) + "\n" + "".join(lines_out)

    # Inject missing v2 sections after frontmatter
    result = _inject_missing_sections(result)

    return result


def _inject_missing_sections(text: str) -> str:
    """Inject any sections that don't exist yet (Tech Overview, Project Knowledge)."""
    sections_to_inject = [
        ("## 关键认知 <!-- section:project-knowledge -->\n\n", "decisions"),
        ("## 技术概览 <!-- section:technical-overview -->\n\n", "project-knowledge"),
    ]
    for section_line, after_section_id in sections_to_inject:
        if section_line.strip() not in text:
            # Insert before the after_section_id section
            pattern = re.compile(rf"^##.*<!--\s*section:{re.escape(after_section_id)}\s*-->.*$", re.MULTILINE)
            m = pattern.search(text)
            if m:
                text = text[:m.start()] + section_line + text[m.start():]

    return text


def _sha256(content: str) -> str:
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


def _restore_backup(backup_path: Path, project_path: Path) -> None:
    """Restore backup; raise a visible hard error with both paths if it fails."""
    try:
        os.replace(str(backup_path), str(project_path))
    except OSError as exc:
        raise OSError(
            f"migration restore failed: cannot restore backup {backup_path} "
            f"to {project_path} — original error: {exc}"
        ) from exc


# ── Apply ───────────────────────────────────────────────────

def apply_v1_to_v2(
    project_path: Path,
    db_path: Path,
    source_hash: str,
    status: str,
    phase: str,
    now: datetime | None = None,
) -> dict:
    if now is None:
        now = datetime.now(timezone.utc)

    original = project_path.read_text(encoding="utf-8")
    current_hash = _sha256(original)
    if current_hash != source_hash:
        return {"ok": False, "kind": "stale_source", "error": "source hash mismatch"}

    preview = preview_v1_to_v2(original, status, phase)
    if preview.before_identity != preview.after_identity:
        return {"ok": False, "kind": "identity_mismatch", "error": "identity manifests differ"}

    # Create backup
    timestamp = now.strftime("%Y%m%d-%H%M%S")
    backup_dir = project_path.parent / ".workeventagent" / "backups" / project_path.stem
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"{timestamp}.md"
    backup_path.write_text(original, encoding="utf-8")

    # Atomic replace
    tmp_path = project_path.with_suffix(".migrating.tmp")
    tmp_path.write_text(preview.migrated_text, encoding="utf-8")
    try:
        os.replace(tmp_path, project_path)
    except OSError:
        tmp_path.unlink(missing_ok=True)
        raise

    # Verify read-back
    migrated_text = project_path.read_text(encoding="utf-8")
    try:
        after_identity = identity_manifest(migrated_text)
    except ValueError:
        # Restore backup
        _restore_backup(backup_path, project_path)
        return {"ok": False, "kind": "migration_verify_failed", "restored": True,
                "backup_path": str(backup_path), "project_path": str(project_path)}

    if after_identity != preview.after_identity:
        # Restore backup
        _restore_backup(backup_path, project_path)
        return {"ok": False, "kind": "migration_verify_failed", "restored": True,
                "backup_path": str(backup_path), "project_path": str(project_path)}

    # Rebuild SQLite
    init_db(db_path)
    rebuild_index(db_path, [project_path])

    return {"ok": True, "backup_path": str(backup_path), "project_path": str(project_path),
            "schema_version": 2}
