from __future__ import annotations

import copy
from datetime import datetime, timezone
from pathlib import Path

import pytest

from workeventagent.project_schema import parse_frontmatter, section_hash
from workeventagent.project_synthesis import (
    build_document_proposal,
    build_section_bundle,
    revise_section_bundle,
    select_source_events,
)


NOW = datetime(2026, 7, 20, 9, 0, tzinfo=timezone.utc)
FIXTURE = Path("tests/fixtures/project-v2.md")


def _project(tmp_path: Path) -> Path:
    path = tmp_path / "report-project.md"
    path.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    return path


def _agent_output(*targets: str, retained_summary: str | None = None) -> dict:
    changes = []
    for target in targets:
        paragraphs = [
            retained_summary
            if target == "technical-overview" and retained_summary
            else f"Evidence-based update for {target}."
        ]
        changes.append(
            {
                "target_section": target,
                "reason": f"Update {target}",
                "content": {"paragraphs": paragraphs, "bullets": ["Keep evidence visible."]},
            }
        )
    return {"changes": changes, "document_suggestion": None}


def test_directed_selection_preserves_requested_order_and_rejects_missing_id(tmp_path: Path) -> None:
    project = _project(tmp_path)
    text = project.read_text(encoding="utf-8")

    selected = select_source_events(text, event_ids=["event-b", "event-a"])

    assert [event["event_id"] for event in selected] == ["event-b", "event-a"]
    with pytest.raises(ValueError, match="missing source event"):
        select_source_events(text, event_ids=["event-missing"])


def test_date_selection_uses_timeline_timestamps_inclusive(tmp_path: Path) -> None:
    project = _project(tmp_path)
    text = project.read_text(encoding="utf-8").replace(
        "2026-07-13T11:00:00+08:00", "2026-07-14T00:00:00+08:00"
    )

    only_first = select_source_events(text, date_from="2026-07-13", date_to="2026-07-13")
    both = select_source_events(text, date_from="2026-07-13", date_to="2026-07-14")

    assert [event["event_id"] for event in only_first] == ["event-a"]
    assert [event["event_id"] for event in both] == ["event-a", "event-b"]


def test_bundle_injects_wrapper_ids_hashes_evidence_and_control_metadata(tmp_path: Path) -> None:
    project = _project(tmp_path)
    before_text = project.read_text(encoding="utf-8")
    sources = select_source_events(before_text, event_ids=["event-a"])

    bundle = build_section_bundle(
        project, "directed", sources, _agent_output("current-panorama"), now=NOW
    )

    assert bundle is not None
    assert bundle["proposal_id"].startswith("kp-")
    assert bundle["project_id"] == "report-project"
    assert bundle["source_events"][0]["event_id"] == "event-a"
    change = bundle["changes"][0]
    assert change["change_id"] == "change-current-panorama"
    assert change["base_section_hash"] == section_hash(before_text, "current-panorama")
    assert f"proposal={bundle['proposal_id']}" in change["after"]
    assert "source_events=event-a" in change["after"]
    assert parse_frontmatter(project.read_text(encoding="utf-8"))["status"] == "active"


def test_bundle_contains_before_after_unified_diff_and_target_hash(tmp_path: Path) -> None:
    project = _project(tmp_path)
    sources = select_source_events(project.read_text(encoding="utf-8"), event_ids=["event-a"])

    bundle = build_section_bundle(
        project, "directed", sources, _agent_output("project-knowledge"), now=NOW
    )

    change = bundle["changes"][0]
    assert "--- before/project-knowledge" in change["diff"]
    assert "+++ after/project-knowledge" in change["diff"]
    assert change["before"] != change["after"]
    assert change["target_section_hash"].startswith("sha256:")


def test_bundle_never_targets_profile_work_map_append_only_or_rollups(tmp_path: Path) -> None:
    project = _project(tmp_path)
    sources = select_source_events(project.read_text(encoding="utf-8"), event_ids=["event-a"])

    for forbidden in ("project-profile", "work-map", "decisions", "attachments", "timeline", "rollups"):
        with pytest.raises(ValueError, match="target"):
            build_section_bundle(project, "directed", sources, _agent_output(forbidden), now=NOW)


def test_bundle_does_not_change_status_or_phase(tmp_path: Path) -> None:
    project = _project(tmp_path)
    before = project.read_text(encoding="utf-8")
    before_meta = parse_frontmatter(before)
    sources = select_source_events(before, event_ids=["event-a"])

    build_section_bundle(project, "directed", sources, _agent_output("current-panorama"), now=NOW)

    after = project.read_text(encoding="utf-8")
    after_meta = parse_frontmatter(after)
    assert after == before
    assert after_meta["status"] == before_meta["status"]
    assert after_meta["phase"] == before_meta["phase"]


def test_revision_creates_new_id_and_supersedes_old_without_mutation(tmp_path: Path) -> None:
    project = _project(tmp_path)
    sources = select_source_events(project.read_text(encoding="utf-8"), event_ids=["event-a"])
    original = build_section_bundle(
        project,
        "directed",
        sources,
        _agent_output("current-panorama", "project-knowledge"),
        now=NOW,
    )
    snapshot = copy.deepcopy(original)

    revised, superseded = revise_section_bundle(
        original,
        ["change-project-knowledge"],
        now=datetime(2026, 7, 20, 9, 1, tzinfo=timezone.utc),
    )

    assert original == snapshot
    assert revised["proposal_id"] != original["proposal_id"]
    assert revised["supersedes"] == original["proposal_id"]
    assert [change["change_id"] for change in revised["changes"]] == ["change-project-knowledge"]
    assert superseded["state"] == "superseded"
    assert superseded["proposal_id"] == original["proposal_id"]


def test_document_proposal_renders_project_module_contract_but_writes_nothing(tmp_path: Path) -> None:
    project = _project(tmp_path)
    sources = select_source_events(project.read_text(encoding="utf-8"), event_ids=["event-a"])
    retained = "The main document keeps this architecture summary."
    bundle = build_section_bundle(
        project, "directed", sources, _agent_output("technical-overview", retained_summary=retained), now=NOW
    )
    suggestion = {
        "purpose": "Explain architecture in depth.",
        "title": "Architecture",
        "retained_summary": retained,
        "module_conclusion": {"paragraphs": ["The architecture is now explicit."], "bullets": []},
        "module_body": {"paragraphs": ["Detailed verified design."], "bullets": ["Evidence-backed."]},
    }

    proposal = build_document_proposal(
        project,
        "directed",
        sources,
        suggestion,
        linked_section_bundle=bundle,
        now=NOW,
    )

    assert proposal["proposal_kind"] == "module_document"
    assert "doc_kind: project_module" in proposal["preview"]
    assert "<!-- section:module-conclusion -->" in proposal["preview"]
    assert "<!-- section:module-body -->" in proposal["preview"]
    assert not Path(proposal["target_path"]).exists()
    assert not (tmp_path / "report-project" / "docs").exists()


def test_document_identity_filename_and_order_are_wrapper_derived_and_collision_free(tmp_path: Path) -> None:
    project = _project(tmp_path)
    docs = tmp_path / "report-project" / "docs"
    docs.mkdir(parents=True)
    (docs / "architecture.md").write_text(
        "---\ndoc_kind: project_module\nproject_id: report-project\nmodule_id: architecture\n"
        "title: Existing\norder: 10\ninclude_in_compendium: true\nupdated: 2026-07-19\n---\n",
        encoding="utf-8",
    )
    sources = select_source_events(project.read_text(encoding="utf-8"), event_ids=["event-a"])
    retained = "Keep the concise architecture summary."
    bundle = build_section_bundle(
        project, "directed", sources, _agent_output("technical-overview", retained_summary=retained), now=NOW
    )
    suggestion = {
        "purpose": "Deep architecture",
        "title": "Architecture",
        "retained_summary": retained,
        "module_conclusion": {"paragraphs": ["Conclusion"], "bullets": []},
        "module_body": {"paragraphs": ["Body"], "bullets": []},
        "module_id": "agent-owned",
        "filename": "agent.md",
        "order": 999,
    }

    proposal = build_document_proposal(
        project,
        "directed",
        sources,
        suggestion,
        linked_section_bundle=bundle,
        now=NOW,
    )

    assert proposal["module_id"] == "architecture-2"
    assert proposal["filename"] == "architecture-2.md"
    assert proposal["order"] == 20
    assert "agent-owned" not in proposal["preview"]
    assert "agent.md" not in proposal["preview"]


def test_document_suggestion_requires_linked_technical_overview_with_retained_summary(tmp_path: Path) -> None:
    project = _project(tmp_path)
    sources = select_source_events(project.read_text(encoding="utf-8"), event_ids=["event-a"])
    wrong_bundle = build_section_bundle(
        project, "directed", sources, _agent_output("current-panorama"), now=NOW
    )
    suggestion = {
        "purpose": "Deep architecture",
        "title": "Architecture",
        "retained_summary": "Required retained summary.",
        "module_conclusion": {"paragraphs": ["Conclusion"], "bullets": []},
        "module_body": {"paragraphs": ["Body"], "bullets": []},
    }

    with pytest.raises(ValueError, match="Technical Overview"):
        build_document_proposal(
            project,
            "directed",
            sources,
            suggestion,
            linked_section_bundle=wrong_bundle,
            now=NOW,
        )
