"""Tests for Phase C compendium generation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from workeventagent.compendium import (
    assemble_compendium,
    discover_module_docs,
    generate_compendium,
    generate_source_manifest,
    list_compendiums,
    validate_module_conclusions,
)


# ── Helpers ─────────────────────────────────────────────────────

_PROJECT_MD = """---
project_id: {pid}
title: {title}
doc_kind: work_project
schema_version: 2
status: active
phase: build
---

# {title}

## 项目档案 <!-- section:project-profile -->

{pid} 是一个测试项目。

## 当前全景 <!-- section:current-panorama -->

项目正在开发中。

## 工作地图 <!-- section:work-map -->

### 工作项：核心功能 <!-- item:core -->

#### [ ] 任务：实现主流程 <!-- task:main-flow -->

## 技术概览 <!-- section:technical-overview -->

Python + Electron。

## 关键认知 <!-- section:project-knowledge -->

- 测试驱动开发是有效的。

## 关键决策 <!-- section:decisions -->

- 2026-01-01：采用 Markdown 作为存储格式。

## 附件 <!-- section:attachments -->

## 事件证据 <!-- section:timeline -->

- 2026-01-01 创建项目 <!-- event:ev-1 -->
  - event_kind: project_event
  - summary: 项目初始化。

## 历史摘要 <!-- section:rollups -->
"""

_MODULE_MD_TEMPLATE = """---
doc_kind: project_module
project_id: {pid}
module_id: {module_id}
title: {title}
order: {order}
include_in_compendium: true
---

# {title}

## 模块结论 <!-- section:module-conclusion -->

{conclusion}

## 详细内容 <!-- section:module-body -->

{body}
"""


def _make_project(tmp_path: Path, pid: str = "demo") -> Path:
    """Create a minimal project with module docs and return project_path."""
    project_file = tmp_path / f"{pid}.md"
    project_file.write_text(
        _PROJECT_MD.format(pid=pid, title=pid.capitalize()),
        encoding="utf-8",
    )

    docs_dir = tmp_path / pid / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)

    # Module 1
    mod1 = docs_dir / "module-a.md"
    mod1.write_text(
        _MODULE_MD_TEMPLATE.format(
            pid=pid,
            module_id="mod-a",
            title="模块 A",
            order=10,
            conclusion="模块 A 的结论：成功实现了功能 X。",
            body="模块 A 的详细实现说明。",
        ),
        encoding="utf-8",
    )

    # Module 2
    mod2 = docs_dir / "module-b.md"
    mod2.write_text(
        _MODULE_MD_TEMPLATE.format(
            pid=pid,
            module_id="mod-b",
            title="模块 B",
            order=20,
            conclusion="模块 B 的结论：解决了性能瓶颈。",
            body="模块 B 的详细实现说明。",
        ),
        encoding="utf-8",
    )

    # Excluded module (not a project_module)
    excluded = docs_dir / "notes.md"
    excluded.write_text(
        """---
doc_kind: meeting_notes
---

# 会议记录

一些非正式笔记。
""",
        encoding="utf-8",
    )

    return project_file


# ── discover_module_docs ─────────────────────────────────────────

def test_discover_module_docs(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    modules = discover_module_docs(project)
    assert len(modules) == 2
    assert modules[0]["module_id"] == "mod-a"
    assert modules[1]["module_id"] == "mod-b"
    assert modules[0]["order"] == 10
    assert modules[1]["order"] == 20


def test_discover_module_docs_empty(tmp_path: Path) -> None:
    project = tmp_path / "empty.md"
    project.write_text(_PROJECT_MD.format(pid="empty", title="Empty"), encoding="utf-8")
    modules = discover_module_docs(project)
    assert modules == []


# ── validate_module_conclusions ──────────────────────────────────

def test_validate_all_conclusions_present(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    result = validate_module_conclusions(project)
    assert result["ok"] is True
    assert len(result["modules"]) == 2
    assert result["issues"] == []


def test_validate_missing_conclusion(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    # Corrupt module-a to remove conclusion
    mod_a = tmp_path / "demo" / "docs" / "module-a.md"
    mod_a.write_text(
        """---
doc_kind: project_module
project_id: demo
module_id: mod-a
title: 模块 A
order: 10
include_in_compendium: true
---

# 模块 A

## 详细内容 <!-- section:module-body -->

没有结论的内容。
""",
        encoding="utf-8",
    )

    result = validate_module_conclusions(project)
    assert result["ok"] is False
    assert len(result["issues"]) == 1
    assert "mod-a" in result["issues"][0]


def test_validate_no_modules(tmp_path: Path) -> None:
    project = tmp_path / "solo.md"
    project.write_text(_PROJECT_MD.format(pid="solo", title="Solo"), encoding="utf-8")
    result = validate_module_conclusions(project)
    assert result["ok"] is True  # No modules to validate = ok
    assert result["modules"] == []


# ── generate_source_manifest ─────────────────────────────────────

def test_source_manifest(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    modules = discover_module_docs(project)
    manifest = generate_source_manifest(project, modules)

    assert manifest["project_id"] == "demo"
    assert manifest["source_file_count"] == 3  # project + 2 modules
    assert manifest["module_count"] == 2
    assert len(manifest["included"]) == 3
    assert len(manifest["excluded"]) == 1  # notes.md

    # Check project file
    project_entry = next(
        e for e in manifest["included"] if e["kind"] == "work_project"
    )
    assert project_entry["relative_path"] == "demo.md"

    # Check module files
    module_entries = [
        e for e in manifest["included"] if e["kind"] == "project_module"
    ]
    assert len(module_entries) == 2

    # Check excluded
    assert manifest["excluded"][0]["relative_path"].endswith("notes.md")


# ── assemble_compendium ──────────────────────────────────────────

def test_assemble_compendium_structure(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    modules = discover_module_docs(project)

    md = assemble_compendium(project, modules)

    # Check frontmatter
    assert "doc_kind: project_compendium" in md
    assert "project_id: demo" in md
    assert "generator_version: F007-C" in md
    assert "editable_source: false" in md

    # Check key sections present
    assert "## 项目总论" in md
    assert "## 项目背景、目标与范围" in md
    assert "## 项目状态与工作地图" in md
    assert "## 项目整体架构" in md
    assert "## 模块结论" in md
    assert "## 跨模块关键结论" in md
    assert "## 关键决策、风险与经验" in md
    assert "## 模块完整内容" in md
    assert "## 事件证据" in md
    assert "## 附件索引" in md
    assert "## 来源与覆盖索引" in md

    # Check module conclusions included
    assert "模块 A 的结论" in md
    assert "模块 B 的结论" in md

    # Check full module bodies included
    assert "模块 A 的详细实现说明" in md
    assert "模块 B 的详细实现说明" in md

    # Check timeline included
    assert "ev-1" in md
    assert "项目初始化" in md


def test_assemble_compendium_with_ai(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    modules = discover_module_docs(project)

    ai_intro = "这是一个综合性的项目总论，由 AI 生成。"
    ai_cross = "跨模块分析：模块 A 和模块 B 共享数据层。"

    md = assemble_compendium(project, modules, ai_intro, ai_cross)

    assert ai_intro in md
    assert ai_cross in md


def test_assemble_compendium_no_ai_no_modules(tmp_path: Path) -> None:
    project = tmp_path / "bare.md"
    project.write_text(_PROJECT_MD.format(pid="bare", title="Bare"), encoding="utf-8")
    md = assemble_compendium(project, [])

    assert "跨模块 AI 综合不可用" in md
    assert "*(无项目模块)*" in md


# ── generate_compendium ──────────────────────────────────────────

def test_generate_compendium_no_ai(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    result = generate_compendium(
        project, tmp_path, use_ai=False
    )

    assert result["ok"] is True
    assert result["module_count"] == 2
    assert result["ai_available"] is False

    compendium_path = Path(result["compendium_path"])
    assert compendium_path.exists()
    content = compendium_path.read_text(encoding="utf-8")
    assert "doc_kind: project_compendium" in content

    manifest_path = Path(result["manifest_path"])
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["project_id"] == "demo"


def test_generate_compendium_validation_fails(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    # Corrupt module
    mod_a = tmp_path / "demo" / "docs" / "module-a.md"
    mod_a.write_text(
        """---
doc_kind: project_module
project_id: demo
module_id: mod-a
title: 模块 A
order: 10
include_in_compendium: true
---

# 模块 A

没有结论。
""",
        encoding="utf-8",
    )

    result = generate_compendium(project, tmp_path, use_ai=False)
    assert result["ok"] is False
    assert result["kind"] == "validation_failed"


# ── list_compendiums ─────────────────────────────────────────────

def test_list_compendiums(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    generate_compendium(project, tmp_path, use_ai=False)

    snapshots = list_compendiums(tmp_path, "demo")
    assert len(snapshots) == 1
    assert snapshots[0]["has_compendium"] is True
    assert snapshots[0]["has_manifest"] is True
    assert snapshots[0]["source_file_count"] == 3


def test_list_compendiums_empty(tmp_path: Path) -> None:
    snapshots = list_compendiums(tmp_path, "nonexistent")
    assert snapshots == []


# ── Edge cases ───────────────────────────────────────────────────

def test_project_with_attachments(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    attachments = tmp_path / "attachments"
    attachments.mkdir(parents=True, exist_ok=True)
    (attachments / "screenshot.png").write_text("fake png", encoding="utf-8")

    result = generate_compendium(project, tmp_path, use_ai=False)
    assert result["ok"] is True
    assert result["attachments_copied"] == 1

    assets_dir = Path(result["snapshot_dir"]) / "assets"
    assert assets_dir.is_dir()
    assert (assets_dir / "screenshot.png").exists()
