from __future__ import annotations

import copy
from datetime import datetime, timezone
from pathlib import Path

import pytest

from workeventagent.knowledge_store import (
    create_proposal,
    get_proposal,
    transition_proposal,
)
from workeventagent.project_schema import find_section, parse_frontmatter, section_content, section_hash
from workeventagent.project_synthesis import (
    _content_hash,
    _validate_module_contract,
    apply_document_proposal,
    apply_section_bundle,
    build_document_proposal,
    build_section_bundle,
    recover_applying_proposal,
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


def test_date_selection_uses_explicit_client_local_utc_boundaries_across_midnight(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    text = project.read_text(encoding="utf-8")
    text = text.replace("2026-07-13T10:00:00+08:00", "2026-07-19T16:30:00+00:00")
    text = text.replace("2026-07-13T11:00:00+08:00", "2026-07-20T16:00:00+00:00")

    selected = select_source_events(
        text,
        date_from="2026-07-20",
        date_to="2026-07-20",
        range_start_utc="2026-07-19T16:00:00.000Z",
        range_end_utc="2026-07-20T16:00:00.000Z",
    )

    assert [event["event_id"] for event in selected] == ["event-a"]


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


@pytest.mark.parametrize("separator", ["\n", "\u0085", "\u2028", "\u2029"])
def test_document_title_must_be_a_safe_single_line_frontmatter_scalar(
    tmp_path: Path, separator: str
) -> None:
    project = _project(tmp_path)
    sources = select_source_events(project.read_text(encoding="utf-8"), event_ids=["event-a"])
    retained = "Keep the concise architecture summary."
    bundle = build_section_bundle(
        project, "directed", sources, _agent_output("technical-overview", retained_summary=retained), now=NOW
    )
    suggestion = {
        "purpose": "Deep architecture",
        "title": f"Architecture{separator}---",
        "retained_summary": retained,
        "module_conclusion": {"paragraphs": ["Conclusion"], "bullets": []},
        "module_body": {"paragraphs": ["Body"], "bullets": []},
    }

    with pytest.raises(ValueError, match="title"):
        build_document_proposal(
            project, "directed", sources, suggestion, linked_section_bundle=bundle, now=NOW
        )


def test_module_apply_contract_rejects_extra_and_duplicate_frontmatter_keys(tmp_path: Path) -> None:
    project = _project(tmp_path)
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
    }
    proposal = build_document_proposal(
        project, "directed", sources, suggestion, linked_section_bundle=bundle, now=NOW
    )

    for injected in (
        "extra_control: agent-owned\n",
        "title: Duplicate title\n",
    ):
        malicious = copy.deepcopy(proposal)
        malicious["preview"] = malicious["preview"].replace(
            "title: Architecture\n", f"title: Architecture\n{injected}", 1
        )
        malicious["preview_hash"] = _content_hash(malicious["preview"])
        with pytest.raises(ValueError, match="frontmatter"):
            _validate_module_contract(malicious["preview"], malicious)


def _persisted_bundle(tmp_path: Path, *targets: str) -> tuple[Path, dict]:
    project = _project(tmp_path)
    sources = select_source_events(project.read_text(encoding="utf-8"), event_ids=["event-a"])
    bundle = build_section_bundle(project, "directed", sources, _agent_output(*targets), now=NOW)
    assert bundle is not None
    return project, create_proposal(tmp_path, bundle, now=NOW)


def _replace_raw(text: str, section_id: str, content: str) -> str:
    section = find_section(text, section_id)
    rendered = "\n" + content.strip("\n") + "\n\n"
    return text[: section.content_start - 1] + rendered + text[section.content_end :]


def test_apply_validates_all_sources_and_hashes_before_writing(tmp_path: Path) -> None:
    project, bundle = _persisted_bundle(tmp_path, "current-panorama")
    text = project.read_text(encoding="utf-8").replace("event:event-a", "event:event-removed")
    project.write_text(text, encoding="utf-8")
    before = project.read_bytes()

    result = apply_section_bundle(project, tmp_path / "index.sqlite", bundle, 1, "2026-07-20")

    assert not result["ok"]
    assert result["kind"] == "stale"
    assert project.read_bytes() == before
    assert get_proposal(tmp_path, bundle["proposal_id"])["state"] == "stale"


def test_one_stale_section_rejects_entire_bundle_with_zero_project_change(tmp_path: Path) -> None:
    project, bundle = _persisted_bundle(tmp_path, "current-panorama", "technical-overview")
    text = _replace_raw(project.read_text(encoding="utf-8"), "current-panorama", "Operator changed this section.")
    project.write_text(text, encoding="utf-8")
    before = project.read_bytes()

    result = apply_section_bundle(project, tmp_path / "index.sqlite", bundle, 1, "2026-07-20")

    assert not result["ok"]
    assert project.read_bytes() == before
    assert "Updated technical-overview" not in project.read_text(encoding="utf-8")


def test_apply_changes_only_allowed_sections_and_preserves_neighbors_byte_for_byte(tmp_path: Path) -> None:
    project, bundle = _persisted_bundle(tmp_path, "current-panorama", "project-knowledge")
    before = project.read_text(encoding="utf-8")
    preserved = {
        section_id: section_content(before, section_id)
        for section_id in ("project-profile", "work-map", "technical-overview", "decisions", "attachments", "timeline", "rollups")
    }

    result = apply_section_bundle(project, tmp_path / "index.sqlite", bundle, 1, "2026-07-20")

    assert result["ok"], result
    after = project.read_text(encoding="utf-8")
    for section_id, content in preserved.items():
        assert section_content(after, section_id) == content


def test_apply_uses_one_project_atomic_replace_for_multiple_sections(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, bundle = _persisted_bundle(tmp_path, "current-panorama", "technical-overview")
    from workeventagent.markdown_store import write_project_atomically as real_write
    calls: list[str] = []

    def counted(path: Path, text: str) -> None:
        calls.append(text)
        real_write(path, text)

    monkeypatch.setattr("workeventagent.project_synthesis.write_project_atomically", counted)

    result = apply_section_bundle(project, tmp_path / "index.sqlite", bundle, 1, "2026-07-20")

    assert result["ok"], result
    assert len(calls) == 1


def test_apply_injects_source_metadata_and_bumps_updated(tmp_path: Path) -> None:
    project, bundle = _persisted_bundle(tmp_path, "current-panorama")

    result = apply_section_bundle(project, tmp_path / "index.sqlite", bundle, 1, "2026-07-21")

    text = project.read_text(encoding="utf-8")
    assert result["ok"]
    assert "source_events=event-a" in section_content(text, "current-panorama")
    assert f"proposal={bundle['proposal_id']}" in text
    assert parse_frontmatter(text)["updated"] == "2026-07-21"


def test_readback_verifies_every_target_hash_before_marking_applied(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, bundle = _persisted_bundle(tmp_path, "current-panorama")

    def corrupt_write(path: Path, text: str) -> None:
        path.write_text(
            text.replace("Evidence-based update for current-panorama.", "Corrupted after write."),
            encoding="utf-8",
        )

    monkeypatch.setattr("workeventagent.project_synthesis.write_project_atomically", corrupt_write)

    result = apply_section_bundle(project, tmp_path / "index.sqlite", bundle, 1, "2026-07-20")

    assert not result["ok"]
    assert result["kind"] == "readback_failed"
    assert get_proposal(tmp_path, bundle["proposal_id"])["state"] == "applying"


def test_crash_after_project_write_recovers_applying_proposal_as_applied(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, bundle = _persisted_bundle(tmp_path, "current-panorama")
    from workeventagent.knowledge_store import transition_proposal as real_transition
    calls = 0

    def crash_on_final(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("crash after project write")
        return real_transition(*args, **kwargs)

    monkeypatch.setattr("workeventagent.project_synthesis.transition_proposal", crash_on_final)
    with pytest.raises(RuntimeError, match="crash after"):
        apply_section_bundle(project, tmp_path / "index.sqlite", bundle, 1, "2026-07-20")
    monkeypatch.setattr("workeventagent.project_synthesis.transition_proposal", real_transition)

    outcome = recover_applying_proposal(project, get_proposal(tmp_path, bundle["proposal_id"]))

    assert outcome == "applied"
    assert get_proposal(tmp_path, bundle["proposal_id"])["state"] == "applied"


def test_crash_before_project_write_resumes_applying_bundle_from_all_base_hashes(tmp_path: Path) -> None:
    project, bundle = _persisted_bundle(tmp_path, "current-panorama")
    transition_proposal(tmp_path, bundle["proposal_id"], 1, {"needs_confirmation"}, "applying")

    outcome = recover_applying_proposal(project, get_proposal(tmp_path, bundle["proposal_id"]))

    assert outcome == "resumed"
    assert get_proposal(tmp_path, bundle["proposal_id"])["state"] == "applied"
    assert section_hash(project.read_text(encoding="utf-8"), "current-panorama") == bundle["changes"][0]["target_section_hash"]


def test_recovery_marks_mixed_base_target_or_unknown_content_stale(tmp_path: Path) -> None:
    project, bundle = _persisted_bundle(tmp_path, "current-panorama", "technical-overview")
    transition_proposal(tmp_path, bundle["proposal_id"], 1, {"needs_confirmation"}, "applying")
    text = project.read_text(encoding="utf-8")
    text = _replace_raw(text, "current-panorama", bundle["changes"][0]["after"])
    project.write_text(text, encoding="utf-8")
    before = project.read_bytes()

    outcome = recover_applying_proposal(project, get_proposal(tmp_path, bundle["proposal_id"]))

    assert outcome == "stale"
    assert get_proposal(tmp_path, bundle["proposal_id"])["state"] == "stale"
    assert project.read_bytes() == before


def test_index_failure_returns_applied_with_warning_and_never_reapplies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, bundle = _persisted_bundle(tmp_path, "current-panorama")
    monkeypatch.setattr("workeventagent.project_synthesis.rebuild_index", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("index down")))

    first = apply_section_bundle(project, tmp_path / "index.sqlite", bundle, 1, "2026-07-20")
    before_retry = project.read_bytes()
    second = apply_section_bundle(project, tmp_path / "index.sqlite", bundle, 1, "2026-07-20")

    assert first["ok"] and first["kind"] == "applied_index_warning"
    assert get_proposal(tmp_path, bundle["proposal_id"])["state"] == "applied"
    assert not second["ok"]
    assert project.read_bytes() == before_retry


def test_wrong_state_or_version_cannot_apply(tmp_path: Path) -> None:
    project, bundle = _persisted_bundle(tmp_path, "current-panorama")

    wrong_version = apply_section_bundle(project, tmp_path / "index.sqlite", bundle, 99, "2026-07-20")
    transition_proposal(tmp_path, bundle["proposal_id"], 1, {"needs_confirmation"}, "rejected")
    wrong_state = apply_section_bundle(project, tmp_path / "index.sqlite", bundle, 2, "2026-07-20")

    assert not wrong_version["ok"]
    assert not wrong_state["ok"]


def _document_pair(tmp_path: Path) -> tuple[Path, dict, dict]:
    project = _project(tmp_path)
    sources = select_source_events(project.read_text(encoding="utf-8"), event_ids=["event-a"])
    retained = "Keep this technical summary."
    bundle = build_section_bundle(
        project, "directed", sources, _agent_output("technical-overview", retained_summary=retained), now=NOW
    )
    suggestion = {
        "purpose": "Deep technical module",
        "title": "Architecture",
        "retained_summary": retained,
        "module_conclusion": {"paragraphs": ["Conclusion"], "bullets": []},
        "module_body": {"paragraphs": ["Body"], "bullets": []},
    }
    document = build_document_proposal(
        project, "directed", sources, suggestion, linked_section_bundle=bundle, now=NOW
    )
    return project, create_proposal(tmp_path, bundle, now=NOW), create_proposal(tmp_path, document, now=NOW)


def test_unconfirmed_or_unsafe_document_proposal_cannot_create_file(tmp_path: Path) -> None:
    project, section_bundle, document = _document_pair(tmp_path)
    unsafe = copy.deepcopy(document)
    unsafe["target_path"] = "report-project/docs/nested/agent.md"

    unconfirmed = apply_document_proposal(project, document, 99, "2026-07-20")
    # Persisted payload is immutable; passing a forged copy must not change the trusted entity.
    forged = apply_document_proposal(project, unsafe, 1, "2026-07-20")

    assert not unconfirmed["ok"]
    assert not forged["ok"]
    assert not (tmp_path / "report-project" / "docs").exists()
    assert get_proposal(tmp_path, document["proposal_id"])["state"] == "needs_confirmation"


def test_document_confirmation_requires_applied_link_and_creates_one_valid_module(tmp_path: Path) -> None:
    project, section_bundle, document = _document_pair(tmp_path)
    before_main = project.read_bytes()
    blocked = apply_document_proposal(project, document, 1, "2026-07-20")
    assert not blocked["ok"]
    assert project.read_bytes() == before_main

    applied_section = apply_section_bundle(project, tmp_path / "index.sqlite", section_bundle, 1, "2026-07-20")
    assert applied_section["ok"]
    fresh_document = get_proposal(tmp_path, document["proposal_id"])
    created = apply_document_proposal(project, fresh_document, fresh_document["version"], "2026-07-20")

    target = tmp_path / document["target_path"]
    assert created["ok"], created
    assert target.is_file()
    assert target.read_text(encoding="utf-8") == document["preview"]
    assert parse_frontmatter(target.read_text(encoding="utf-8"))["doc_kind"] == "project_module"
    assert project.read_bytes() != before_main  # only the separately confirmed section bundle changed it


def test_existing_different_document_blocks_without_overwrite(tmp_path: Path) -> None:
    project, section_bundle, document = _document_pair(tmp_path)
    apply_section_bundle(project, tmp_path / "index.sqlite", section_bundle, 1, "2026-07-20")
    target = tmp_path / document["target_path"]
    target.parent.mkdir(parents=True)
    target.write_text("operator file", encoding="utf-8")

    result = apply_document_proposal(project, get_proposal(tmp_path, document["proposal_id"]), 1, "2026-07-20")

    assert not result["ok"]
    assert target.read_text(encoding="utf-8") == "operator file"
    assert get_proposal(tmp_path, document["proposal_id"])["state"] == "stale"


def test_document_created_before_ledger_transition_recovers_by_exact_hash(tmp_path: Path) -> None:
    project, section_bundle, document = _document_pair(tmp_path)
    apply_section_bundle(project, tmp_path / "index.sqlite", section_bundle, 1, "2026-07-20")
    transition_proposal(tmp_path, document["proposal_id"], 1, {"needs_confirmation"}, "applying")
    target = tmp_path / document["target_path"]
    target.parent.mkdir(parents=True)
    target.write_text(document["preview"], encoding="utf-8")

    outcome = recover_applying_proposal(project, get_proposal(tmp_path, document["proposal_id"]))

    assert outcome == "applied"
    assert get_proposal(tmp_path, document["proposal_id"])["state"] == "applied"


def test_absent_document_resumes_but_duplicate_module_identity_blocks(tmp_path: Path) -> None:
    project, section_bundle, document = _document_pair(tmp_path)
    apply_section_bundle(project, tmp_path / "index.sqlite", section_bundle, 1, "2026-07-20")
    transition_proposal(tmp_path, document["proposal_id"], 1, {"needs_confirmation"}, "applying")

    outcome = recover_applying_proposal(project, get_proposal(tmp_path, document["proposal_id"]))

    assert outcome == "resumed"
    target = tmp_path / document["target_path"]
    assert target.is_file()


def test_duplicate_module_identity_or_changed_retained_summary_blocks_creation(tmp_path: Path) -> None:
    project, section_bundle, document = _document_pair(tmp_path)
    apply_section_bundle(project, tmp_path / "index.sqlite", section_bundle, 1, "2026-07-20")
    duplicate = tmp_path / "report-project" / "docs" / "different-name.md"
    duplicate.parent.mkdir(parents=True)
    duplicate.write_text(
        "---\ndoc_kind: project_module\nproject_id: report-project\nmodule_id: architecture\n"
        "title: Duplicate\norder: 99\ninclude_in_compendium: true\nupdated: 2026-07-20\n---\n",
        encoding="utf-8",
    )

    duplicate_result = apply_document_proposal(
        project, get_proposal(tmp_path, document["proposal_id"]), 1, "2026-07-20"
    )

    assert not duplicate_result["ok"]
    assert not (tmp_path / document["target_path"]).exists()

    # A fresh pair proves the retained summary gate independently.
    other = tmp_path / "other"
    other.mkdir()
    project2, section2, document2 = _document_pair(other)
    apply_section_bundle(project2, other / "index.sqlite", section2, 1, "2026-07-20")
    text = _replace_raw(project2.read_text(encoding="utf-8"), "technical-overview", "Operator replaced summary.")
    project2.write_text(text, encoding="utf-8")

    summary_result = apply_document_proposal(
        project2, get_proposal(other, document2["proposal_id"]), 1, "2026-07-20"
    )

    assert not summary_result["ok"]
    assert get_proposal(other, document2["proposal_id"])["state"] == "needs_confirmation"
