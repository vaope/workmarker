"""Phase C project compendium generator.

Deterministic assembly of all project sources into a standalone deliverable.
AI is optional and only generates the project introduction and cross-module
conclusions — the rest is deterministic extraction from source documents.

Truth-source boundary: compendium files are read-only snapshots. They are never
reverse-imported into the project. Registry, synthesis, and future compendium
runs exclude doc_kind=project_compendium.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from workeventagent.project_schema import (
    SECTION_BY_ID,
    find_section,
    parse_frontmatter,
    section_content,
)


# ── Module discovery ────────────────────────────────────────────────

_MODULE_FM_REQUIRED = {"doc_kind": "project_module", "include_in_compendium": "true"}


def discover_module_docs(project_path: Path) -> list[dict]:
    """Scan ``<project_id>/docs/`` for compendium-eligible module documents.

    Returns list of dicts with keys: path, module_id, title, order, frontmatter.
    Sorted by ``order`` ascending, then ``module_id``.
    """
    docs_dir = project_path.parent / project_path.stem / "docs"
    if not docs_dir.is_dir():
        return []

    modules: list[dict] = []
    for md_path in sorted(docs_dir.glob("*.md")):
        try:
            text = md_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        fm = parse_frontmatter(text)
        if fm.get("doc_kind") != "project_module":
            continue
        if fm.get("project_id", "") != project_path.stem:
            continue
        if fm.get("include_in_compendium", "").lower() != "true":
            continue

        try:
            order = int(fm.get("order", "999"))
        except ValueError:
            order = 999

        modules.append({
            "path": md_path,
            "module_id": fm.get("module_id", md_path.stem),
            "title": fm.get("title", md_path.stem),
            "order": order,
            "frontmatter": fm,
            "text": text,
            "content_hash": _sha256(text),
        })

    modules.sort(key=lambda m: (m["order"], m["module_id"]))
    return modules


# ── Validation ──────────────────────────────────────────────────────

class ModuleValidationError(Exception):
    """A module document is missing required content for compendium inclusion."""


def validate_module_conclusions(project_path: Path) -> dict:
    """Check all eligible module docs have required ``## 模块结论`` section.

    Returns:
        {"ok": True, "modules": [...], "issues": [...]}
        or {"ok": False, "modules": [...], "issues": [...]} if validation fails.
    """
    modules = discover_module_docs(project_path)
    results = []
    issues = []

    for mod in modules:
        text = mod["text"]
        has_conclusion = False
        try:
            _ = find_section(text, "module-conclusion")
            has_conclusion = True
        except ValueError:
            pass

        results.append({
            "module_id": mod["module_id"],
            "title": mod["title"],
            "path": str(mod["path"]),
            "has_conclusion": has_conclusion,
        })
        if not has_conclusion:
            issues.append(
                f"module '{mod['module_id']}' ({mod['title']}) missing "
                f"## 模块结论 <!-- section:module-conclusion -->"
            )

    return {
        "ok": len(issues) == 0,
        "modules": results,
        "issues": issues,
    }


# ── Content extraction ─────────────────────────────────────────────

def _raw_section_text(text: str, section_id: str) -> str:
    """Return raw section content without the heading line."""
    try:
        return section_content(text, section_id).strip()
    except ValueError:
        return ""


def _extract_project_sections(project_text: str) -> dict[str, str]:
    """Extract all known sections from the project document."""
    extracted: dict[str, str] = {}
    for sid in _SECTION_IDS:
        try:
            content = section_content(project_text, sid)
        except ValueError:
            content = ""
        extracted[sid] = content.strip()
    return extracted


def _find_section_heading(text: str, section_id: str) -> str:
    """Return the original heading line for a section."""
    try:
        sl = find_section(text, section_id)
        return sl.heading
    except ValueError:
        spec = SECTION_BY_ID.get(section_id)
        return f"## {spec.title if spec else section_id}"


# ── Assembly ────────────────────────────────────────────────────────

def _sha256(content: str) -> str:
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


def generate_source_manifest(
    project_path: Path, modules: list[dict]
) -> dict:
    """Generate source manifest for all files included in the compendium."""
    base = project_path.parent
    files = []

    # Main project file
    try:
        project_text = project_path.read_text(encoding="utf-8")
        files.append({
            "relative_path": project_path.name,
            "content_hash": _sha256(project_text),
            "module_id": None,
            "kind": "work_project",
        })
    except OSError:
        pass

    # Module docs
    for mod in modules:
        files.append({
            "relative_path": str(mod["path"].relative_to(base)),
            "content_hash": mod.get("content_hash", _sha256(mod.get("text", ""))),
            "module_id": mod["module_id"],
            "kind": "project_module",
        })

    # Scan docs/ for un-included Markdown files
    docs_dir = project_path.parent / project_path.stem / "docs"
    included_paths = {str(m["path"]) for m in modules}
    excluded: list[dict] = []
    if docs_dir.is_dir():
        for md_path in sorted(docs_dir.glob("*.md")):
            if str(md_path) in included_paths:
                continue
            try:
                md_text = md_path.read_text(encoding="utf-8")
                fm = parse_frontmatter(md_text)
            except (OSError, UnicodeDecodeError):
                continue
            reason = _exclusion_reason(fm, project_path.stem)
            excluded.append({
                "relative_path": str(md_path.relative_to(base)),
                "reason": reason,
            })

    return {
        "project_id": project_path.stem,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator_version": "F007-C",
        "source_file_count": len(files),
        "module_count": len(modules),
        "included": files,
        "excluded": excluded,
    }


def _exclusion_reason(fm: dict, project_id: str) -> str:
    if fm.get("doc_kind") != "project_module":
        return f"doc_kind is '{fm.get('doc_kind', 'missing')}', not 'project_module'"
    if fm.get("project_id", "") != project_id:
        return f"project_id '{fm.get('project_id', '')}' does not match '{project_id}'"
    if fm.get("include_in_compendium", "").lower() != "true":
        return "include_in_compendium is not 'true'"
    return "unknown"


# ── Compendium assembly ─────────────────────────────────────────────

_SECTION_IDS = (
    "project-profile",
    "current-panorama",
    "work-map",
    "technical-overview",
    "project-knowledge",
    "decisions",
    "timeline",
    "rollups",
)

SECTION_BY_IDS = {spec.section_id: spec for spec in (
    type("Spec", (), {"section_id": s[0], "title": s[1]})()
    for s in [
        ("project-profile", "项目档案"),
        ("current-panorama", "当前全景"),
        ("work-map", "工作地图"),
        ("technical-overview", "技术概览"),
        ("project-knowledge", "关键认知"),
        ("decisions", "关键决策"),
        ("attachments", "附件"),
        ("timeline", "事件证据"),
        ("rollups", "历史摘要"),
    ]
)}


def assemble_compendium(
    project_path: Path,
    modules: list[dict],
    ai_intro: str | None = None,
    ai_cross_module: str | None = None,
    project_sections: dict[str, str] | None = None,
) -> str:
    """Deterministically assemble a full compendium Markdown document.

    Args:
        project_path: Path to the project Markdown file.
        modules: List of module dicts from discover_module_docs().
        ai_intro: Optional AI-generated project introduction (总论).
        ai_cross_module: Optional AI-generated cross-module conclusions.
        project_sections: Optional pre-extracted project sections.

    Returns:
        Complete compendium Markdown string.
    """
    pid = project_path.stem
    title = pid

    if project_sections is None:
        project_text = project_path.read_text(encoding="utf-8")
        project_sections = _extract_project_sections(project_text)
    else:
        project_text = ""

    fm = parse_frontmatter(project_text) if project_text else {}
    project_title = fm.get("title", pid)
    project_status = fm.get("status", "unknown")
    project_phase = fm.get("phase", "unknown")

    now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")

    lines: list[str] = []

    # ── Frontmatter ──
    lines.append("---")
    lines.append(f"doc_kind: project_compendium")
    lines.append(f"project_id: {pid}")
    lines.append(f"generated_at: {now_ts}")
    lines.append("generator_version: F007-C")
    lines.append(f"source_file_count: {1 + len(modules)}")
    lines.append(f"module_count: {len(modules)}")
    lines.append(f"source_manifest: sources.json")
    lines.append("editable_source: false")
    lines.append("---")
    lines.append("")

    # ── Title ──
    lines.append(f"# {project_title} 项目完整文档")
    lines.append("")

    # ── Project introduction ──
    lines.append("## 项目总论")
    lines.append("")
    if ai_intro:
        lines.append(ai_intro.strip())
        lines.append("")
    else:
        # Fallback: deterministic summary from project-profile section
        profile = project_sections.get("project-profile", "")
        if profile:
            lines.append(profile.strip())
            lines.append("")
        lines.append("")
        lines.append("> ⚠ 跨模块 AI 综合不可用，项目总论从项目档案中提取。")
        lines.append("")

    # ── Project background, goals, scope ──
    lines.append("## 项目背景、目标与范围")
    lines.append("")
    panorama = project_sections.get("current-panorama", "")
    if panorama:
        lines.append(panorama.strip())
        lines.append("")
    else:
        lines.append("*(无全景摘要)*")
        lines.append("")

    # ── Project status & work map ──
    lines.append("## 项目状态与工作地图")
    lines.append("")
    lines.append(f"- 状态: {project_status}")
    lines.append(f"- 阶段: {project_phase}")
    lines.append("")

    work_map = project_sections.get("work-map", "")
    if work_map:
        lines.append(work_map.strip())
        lines.append("")

    # ── Architecture overview ──
    lines.append("## 项目整体架构")
    lines.append("")
    tech = project_sections.get("technical-overview", "")
    if tech:
        lines.append(tech.strip())
        lines.append("")
    else:
        lines.append("*(无技术概览)*")
        lines.append("")

    # ── Module conclusions ──
    lines.append("## 模块结论")
    lines.append("")
    if modules:
        for mod in modules:
            lines.append(f"### {mod['title']}")
            lines.append("")
            conclusion = _raw_section_text(mod.get("text", ""), "module-conclusion")
            if conclusion:
                lines.append(conclusion.strip())
            else:
                lines.append("*(该模块尚未撰写结论)*")
            lines.append("")
    else:
        lines.append("*(无项目模块)*")
        lines.append("")

    # ── Cross-module conclusions ──
    lines.append("## 跨模块关键结论")
    lines.append("")
    if ai_cross_module:
        lines.append(ai_cross_module.strip())
        lines.append("")
    else:
        lines.append("")
        lines.append("> ⚠ 跨模块 AI 综合不可用。")
        lines.append("")

    # ── Key decisions, risks, lessons ──
    lines.append("## 关键决策、风险与经验")
    lines.append("")
    decisions = project_sections.get("decisions", "")
    if decisions:
        lines.append(decisions.strip())
        lines.append("")
    else:
        lines.append("*(无关键决策记录)*")
        lines.append("")

    knowledge = project_sections.get("project-knowledge", "")
    if knowledge:
        lines.append("### 关键认知")
        lines.append("")
        lines.append(knowledge.strip())
        lines.append("")

    # ── Full module content ──
    lines.append("## 模块完整内容")
    lines.append("")
    if modules:
        for mod in modules:
            lines.append(f"### {mod['title']} 完整内容")
            lines.append("")
            body = _raw_section_text(mod.get("text", ""), "module-body")
            if body:
                lines.append(body.strip())
            else:
                # Use full text minus frontmatter
                full = mod.get("text", "")
                parts = full.split("---", 2)
                if len(parts) >= 3:
                    lines.append(parts[2].strip())
                elif full:
                    lines.append(full.strip())
            lines.append("")
    else:
        lines.append("*(无项目模块)*")
        lines.append("")

    # ── Timeline evidence ──
    lines.append("## 事件证据")
    lines.append("")
    timeline = project_sections.get("timeline", "")
    if timeline:
        lines.append(timeline.strip())
        lines.append("")
    else:
        lines.append("*(无时间线事件)*")
        lines.append("")

    # ── Attachments appendix ──
    lines.append("## 附件索引")
    lines.append("")
    attachments = project_sections.get("attachments", "")
    if attachments:
        lines.append(attachments.strip())
        lines.append("")
    else:
        lines.append("*(无附件)*")
        lines.append("")

    # ── Source coverage ──
    lines.append("## 来源与覆盖索引")
    lines.append("")
    lines.append(f"本汇编包含 {len(modules) + 1} 个来源文件：")
    lines.append("")
    lines.append(f"1. 项目主文档: `{project_path.name}`")
    for i, mod in enumerate(modules, 2):
        rel = str(mod["path"].relative_to(project_path.parent))
        lines.append(f"{i}. 模块文档: `{rel}`")
    lines.append("")

    return "\n".join(lines) + "\n"


# ── Orchestrator ────────────────────────────────────────────────────

_EXPORTS_ROOT = "exports"


def generate_compendium(
    project_path: Path,
    workspace: Path,
    opencode_bin: str = "opencode",
    model: str = "",
    use_ai: bool = True,
) -> dict:
    """Full compendium generation pipeline.

    1. Discover and validate module docs.
    2. Optionally run AI for project intro and cross-module conclusions.
    3. Deterministically assemble all content.
    4. Atomically write to ``exports/<project_id>/<snapshot>/``.

    Returns:
        {"ok": True, "snapshot_dir": "...", "compendium_path": "...",
         "manifest_path": "...", "ai_available": bool}
        or {"ok": False, "kind": "...", "error": "..."}
    """
    # Validate
    validation = validate_module_conclusions(project_path)
    if not validation["ok"]:
        return {
            "ok": False,
            "kind": "validation_failed",
            "error": f"module conclusion validation failed",
            "details": validation,
        }

    modules = discover_module_docs(project_path)

    # Extract project sections
    project_text = project_path.read_text(encoding="utf-8")
    project_sections = _extract_project_sections(project_text)

    # AI synthesis (optional)
    ai_intro: str | None = None
    ai_cross_module: str | None = None
    ai_available = False

    if use_ai and modules:
        try:
            ai_intro, ai_cross_module = _run_ai_synthesis(
                project_path, modules, project_sections, opencode_bin, model
            )
            ai_available = True
        except Exception:
            # AI failure is non-fatal — compendium still generates
            ai_available = False

    # Assemble
    compendium_md = assemble_compendium(
        project_path, modules, ai_intro, ai_cross_module, project_sections
    )

    # Generate manifest
    manifest = generate_source_manifest(project_path, modules)

    # Write to exports directory
    pid = project_path.stem
    snapshot = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    snapshot_dir = workspace / _EXPORTS_ROOT / pid / snapshot
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    compendium_path = snapshot_dir / f"{pid}-compendium.md"
    manifest_path = snapshot_dir / "sources.json"

    # Atomic writes via temp file + rename
    _atomic_write(compendium_path, compendium_md)
    _atomic_write(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")

    # Copy attachments if they exist
    attachments_dir = workspace / "attachments"
    if attachments_dir.is_dir():
        assets_dir = snapshot_dir / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)
        copied = 0
        for f in attachments_dir.iterdir():
            if f.is_file():
                shutil.copy2(f, assets_dir / f.name)
                copied += 1
    else:
        copied = 0

    return {
        "ok": True,
        "snapshot_dir": str(snapshot_dir),
        "compendium_path": str(compendium_path),
        "manifest_path": str(manifest_path),
        "ai_available": ai_available,
        "module_count": len(modules),
        "attachments_copied": copied,
    }


def _atomic_write(path: Path, content: str) -> None:
    """Write content atomically via temp file + os.replace."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def _run_ai_synthesis(
    project_path: Path,
    modules: list[dict],
    project_sections: dict[str, str],
    opencode_bin: str,
    model: str,
) -> tuple[str, str]:
    """Call opencode to generate project intro and cross-module conclusions.

    Returns (intro, cross_module) strings.
    """
    import subprocess
    import tempfile

    pid = project_path.stem
    title = project_path.stem

    # Build context for AI
    context_lines = [f"# Project: {title}", ""]

    # Project profile + panorama
    profile = project_sections.get("project-profile", "")
    panorama = project_sections.get("current-panorama", "")
    if profile or panorama:
        context_lines.append("## 项目概况")
        context_lines.append("")
        if profile:
            context_lines.append(profile.strip())
        if panorama:
            context_lines.append("")
            context_lines.append(panorama.strip())
        context_lines.append("")

    # Module conclusions summary
    if modules:
        context_lines.append("## 模块结论摘要")
        context_lines.append("")
        for mod in modules:
            conclusion = _raw_section_text(mod.get("text", ""), "module-conclusion")
            context_lines.append(f"### {mod['title']}")
            context_lines.append("")
            if conclusion:
                context_lines.append(conclusion.strip())
            else:
                context_lines.append("*(无结论)*")
            context_lines.append("")

    context_text = "\n".join(context_lines)

    prompt = (
        "你是一个项目知识综合助手。请基于以下项目内容，生成两部分输出：\n\n"
        "## 1. 项目总论\n"
        "用 2-4 段话概述：项目是什么、解决了什么问题、当前状态、最重要的成果和下一步方向。\n\n"
        "## 2. 跨模块关键结论\n"
        "分析各模块之间的关系，总结跨模块的共性模式、关键依赖、架构决策和可复用经验。\n\n"
        "要求：仅基于提供的项目内容进行综合，不要编造信息。\n"
        "输出格式：\n"
        "```introduction\n"
        "...\n"
        "```\n"
        "```cross-module\n"
        "...\n"
        "```\n\n"
        f"项目内容：\n{context_text}"
    )

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    ) as f:
        f.write(prompt)
        prompt_file = f.name

    try:
        cmd = [opencode_bin, "run", prompt_file]
        if model:
            cmd.extend(["--model", model])

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(project_path.parent),
        )

        output = result.stdout

        # Parse AI output
        intro = _extract_fenced_block(output, "introduction")
        cross = _extract_fenced_block(output, "cross-module")

        if not intro:
            # Try to use the whole output as intro if no fenced block
            intro = output.strip()[:2000]
        if not cross:
            cross = ""

        return intro, cross
    finally:
        try:
            os.unlink(prompt_file)
        except OSError:
            pass


def _extract_fenced_block(text: str, tag: str) -> str:
    """Extract content from a fenced code block with the given tag."""
    import re
    pattern = rf"```{re.escape(tag)}\s*\n(.*?)```"
    match = re.search(pattern, text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return ""


# ── Snapshot listing ────────────────────────────────────────────────

def list_compendiums(workspace: Path, project_id: str) -> list[dict]:
    """List existing compendium snapshots for a project."""
    exports_dir = workspace / _EXPORTS_ROOT / project_id
    if not exports_dir.is_dir():
        return []

    snapshots: list[dict] = []
    for snap_dir in sorted(exports_dir.iterdir(), reverse=True):
        if not snap_dir.is_dir():
            continue
        compendium_file = snap_dir / f"{project_id}-compendium.md"
        manifest_file = snap_dir / "sources.json"

        info: dict = {
            "snapshot": snap_dir.name,
            "path": str(snap_dir),
            "has_compendium": compendium_file.exists(),
            "has_manifest": manifest_file.exists(),
        }

        if manifest_file.exists():
            try:
                manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
                info["source_file_count"] = manifest.get("source_file_count", 0)
                info["module_count"] = manifest.get("module_count", 0)
                info["generated_at"] = manifest.get("generated_at", "")
            except (json.JSONDecodeError, OSError):
                pass

        snapshots.append(info)

    return snapshots
