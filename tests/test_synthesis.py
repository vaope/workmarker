from pathlib import Path

import pytest

from workeventagent.models import (
    SectionProposal,
    SynthesisBundle,
)
from workeventagent.proposal_store import create_proposal, get_proposal
from workeventagent.project_schema import section_hash
from workeventagent.synthesis import (
    apply_bundle,
    validate_bundle,
)


V2_PROJECT = """---
project_id: demo
title: Demo
doc_kind: work_project
schema_version: 2
status: active
phase: build
created: 2026-07-13
updated: 2026-07-13
---
# Demo

## 当前全景 <!-- section:current-panorama -->

项目刚开始。

<!-- panorama-meta:generated_at=2026-07-13T09:00:00+08:00;source_events=ev-1,ev-2 -->

## 技术概览 <!-- section:technical-overview -->

Electron + Python。

## 事件证据 <!-- section:timeline -->

- 2026-07-13T10:00:00+08:00 <!-- event:ev-1 -->
  - task_id: task-a
  - summary: 完成捕获集成

- 2026-07-13T11:00:00+08:00 <!-- event:ev-2 -->
  - task_id: task-a
  - summary: 添加纠错能力
"""


def _write_project(tmp_path: Path) -> Path:
    project = tmp_path / "demo.md"
    project.write_text(V2_PROJECT, encoding="utf-8")
    return project


def _make_store(tmp_path: Path) -> Path:
    return tmp_path / "proposals"


class TestValidateBundle:
    def test_valid_bundle_passes(self, tmp_path: Path) -> None:
        project = _write_project(tmp_path)
        actual_hash = section_hash(V2_PROJECT, "current-panorama")
        bundle = SynthesisBundle(
            project_id="demo",
            kind="current-panorama",
            sections=(
                SectionProposal(
                    section_id="current-panorama",
                    content="\u9879\u76ee\u5df2\u5b8c\u6210\u6355\u83b7\u548c\u7ea0\u9519\u3002\n",
                    base_section_hash=actual_hash,
                    source_event_ids=("ev-1", "ev-2"),
                    reason="\u7efc\u5408\u66f4\u65b0\u3002",
                ),
            ),
        )
        validate_bundle(project, bundle)  # should not raise

    def test_missing_source_event_raises(self, tmp_path: Path) -> None:
        project = _write_project(tmp_path)
        bundle = SynthesisBundle(
            project_id="demo",
            kind="current-panorama",
            sections=(
                SectionProposal(
                    section_id="current-panorama",
                    content="ok",
                    base_section_hash="sha256:abc",
                    source_event_ids=("ev-1", "ev-999"),
                    reason="test",
                ),
            ),
        )
        with pytest.raises(ValueError, match="source event not found: ev-999"):
            validate_bundle(project, bundle)

    def test_stale_section_hash_raises(self, tmp_path: Path) -> None:
        project = _write_project(tmp_path)
        bundle = SynthesisBundle(
            project_id="demo",
            kind="current-panorama",
            sections=(
                SectionProposal(
                    section_id="current-panorama",
                    content="ok",
                    base_section_hash="sha256:stale",
                    source_event_ids=("ev-1",),
                    reason="test",
                ),
            ),
        )
        with pytest.raises(ValueError, match="stale hash"):
            validate_bundle(project, bundle)

    def test_v1_project_raises(self, tmp_path: Path) -> None:
        project = tmp_path / "v1.md"
        project.write_text(
            "---\nproject_id: v1\ntitle: V1\ndoc_kind: work_project\n---\n# V1\n",
            encoding="utf-8",
        )
        bundle = SynthesisBundle(
            project_id="v1",
            kind="current-panorama",
            sections=(),
        )
        with pytest.raises(ValueError, match="must be v2"):
            validate_bundle(project, bundle)


class TestApplyBundle:
    def test_apply_success_writes_and_marks_applied(self, tmp_path: Path) -> None:
        project = _write_project(tmp_path)
        store = _make_store(tmp_path)
        actual_hash = section_hash(V2_PROJECT, "current-panorama")

        bundle = SynthesisBundle(
            project_id="demo",
            kind="current-panorama",
            sections=(
                SectionProposal(
                    section_id="current-panorama",
                    content="\u7efc\u5408\u540e\u7684\u5168\u666f\u3002\n",
                    base_section_hash=actual_hash,
                    source_event_ids=("ev-1", "ev-2"),
                    reason="\u6d4b\u8bd5\u3002",
                ),
            ),
        )
        proposal = create_proposal(store, bundle)
        result = apply_bundle(project, store, proposal.proposal_id, proposal.version)

        assert result["ok"] is True
        assert "\u7efc\u5408\u540e\u7684\u5168\u666f" in project.read_text(encoding="utf-8")

        p = get_proposal(store, proposal.proposal_id)
        assert p is not None and p.state == "applied"

    def test_apply_all_or_nothing_on_stale_hash(self, tmp_path: Path) -> None:
        project = _write_project(tmp_path)
        store = _make_store(tmp_path)
        original = project.read_text(encoding="utf-8")

        pano_hash = section_hash(V2_PROJECT, "current-panorama")
        tech_hash = section_hash(V2_PROJECT, "technical-overview")

        bundle = SynthesisBundle(
            project_id="demo",
            kind="multi",
            sections=(
                SectionProposal(
                    section_id="current-panorama",
                    content="\u5168\u666f\u5df2\u66f4\u65b0\u3002\n",
                    base_section_hash=pano_hash,
                    source_event_ids=("ev-1", "ev-2"),
                    reason="\u7efc\u5408\u66f4\u65b0\u3002",
                ),
                SectionProposal(
                    section_id="technical-overview",
                    content="Python \u4f5c\u4e3a\u786e\u5b9a\u6027\u540e\u7aef\u3002\n",
                    base_section_hash="sha256:stale",
                    source_event_ids=("ev-1",),
                    reason="\u6280\u672f\u6982\u89c8\u66f4\u65b0\u3002",
                ),
            ),
        )
        proposal = create_proposal(store, bundle)

        with pytest.raises(ValueError, match="stale hash"):
            apply_bundle(project, store, proposal.proposal_id, proposal.version)

        # Project unchanged
        assert project.read_text(encoding="utf-8") == original

        # Proposal in error state
        p = get_proposal(store, proposal.proposal_id)
        assert p is not None and p.state == "error"

    def test_apply_nonexistent_proposal(self, tmp_path: Path) -> None:
        project = _write_project(tmp_path)
        store = _make_store(tmp_path)
        result = apply_bundle(project, store, "nonexistent", 1)
        assert result["ok"] is False
        assert result["kind"] == "not_found"

    def test_multi_section_apply_writes_all(self, tmp_path: Path) -> None:
        project = _write_project(tmp_path)
        store = _make_store(tmp_path)

        pano_hash = section_hash(V2_PROJECT, "current-panorama")
        tech_hash = section_hash(V2_PROJECT, "technical-overview")

        bundle = SynthesisBundle(
            project_id="demo",
            kind="multi",
            sections=(
                SectionProposal(
                    section_id="current-panorama",
                    content="\u65b0\u5168\u666f\u3002\n",
                    base_section_hash=pano_hash,
                    source_event_ids=("ev-1", "ev-2"),
                    reason="\u7efc\u5408\u3002",
                ),
                SectionProposal(
                    section_id="technical-overview",
                    content="\u65b0\u6280\u672f\u6982\u89c8\u3002\n",
                    base_section_hash=tech_hash,
                    source_event_ids=("ev-1",),
                    reason="\u66f4\u65b0\u3002",
                ),
            ),
        )
        proposal = create_proposal(store, bundle)
        result = apply_bundle(project, store, proposal.proposal_id, proposal.version)

        assert result["ok"] is True
        updated = project.read_text(encoding="utf-8")
        assert "\u65b0\u5168\u666f" in updated
        assert "\u65b0\u6280\u672f\u6982\u89c8" in updated

        p = get_proposal(store, proposal.proposal_id)
        assert p is not None and p.state == "applied"
