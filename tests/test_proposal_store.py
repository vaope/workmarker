from pathlib import Path

import pytest

from workeventagent.models import (
    SectionProposal,
    SynthesisBundle,
)
from workeventagent.proposal_store import (
    create_proposal,
    get_proposal,
    list_proposals,
    supersede_proposals,
    update_proposal_state,
)


def _make_bundle(project_id: str = "demo") -> SynthesisBundle:
    return SynthesisBundle(
        project_id=project_id,
        kind="current-panorama",
        sections=(
            SectionProposal(
                section_id="current-panorama",
                content="\u9879\u76ee\u6b63\u5728\u63a8\u8fdb Phase B\u3002",
                base_section_hash="sha256:abc123",
                source_event_ids=("ev-1", "ev-2"),
                reason="\u4ece\u6700\u8fd1\u4e8b\u4ef6\u7efc\u5408\u3002",
            ),
        ),
        trigger_event_ids=("ev-1", "ev-2"),
    )


def _make_store(tmp_path: Path) -> Path:
    return tmp_path / "proposals"


def test_create_and_read_proposal(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    bundle = _make_bundle()
    p = create_proposal(store, bundle)
    assert p.proposal_id != ""
    assert p.state == "pending"
    assert p.version == 1

    got = get_proposal(store, p.proposal_id)
    assert got is not None
    assert got.proposal_id == p.proposal_id
    assert got.bundle == bundle


def test_list_proposals_filters_by_project_and_state(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    p1 = create_proposal(store, _make_bundle("a"))
    p2 = create_proposal(store, _make_bundle("a"))
    p3 = create_proposal(store, _make_bundle("b"))

    a_pending = list_proposals(store, project_id="a", state="pending")
    assert len(a_pending) == 2
    assert all(p.project_id == "a" for p in a_pending)

    b_pending = list_proposals(store, project_id="b", state="pending")
    assert len(b_pending) == 1


def test_update_state_and_version_guard(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    p = create_proposal(store, _make_bundle())

    updated = update_proposal_state(store, p.proposal_id, "applied", p.version)
    assert updated.state == "applied"
    assert updated.version == 2

    with pytest.raises(ValueError, match="stale version"):
        update_proposal_state(store, p.proposal_id, "rejected", 1)


def test_supersede_marks_older_pending_as_superseded(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    p1 = create_proposal(store, _make_bundle())
    p2 = create_proposal(store, _make_bundle())
    supersede_proposals(store, "demo", "current-panorama", p2.proposal_id)

    got1 = get_proposal(store, p1.proposal_id)
    got2 = get_proposal(store, p2.proposal_id)
    assert got1 is not None and got1.state == "superseded"
    assert got2 is not None and got2.state == "pending"


def test_proposal_error_state_stores_message(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    p = create_proposal(store, _make_bundle())
    updated = update_proposal_state(
        store, p.proposal_id, "error", p.version, error_message="LLM \u8d85\u65f6"
    )
    assert updated.state == "error"
    assert updated.error_message == "LLM \u8d85\u65f6"


def test_get_nonexistent_returns_none(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    assert get_proposal(store, "nonexistent") is None


def test_list_proposals_empty_store(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    assert list_proposals(store) == []


def test_list_proposals_without_filters(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    create_proposal(store, _make_bundle("a"))
    create_proposal(store, _make_bundle("b"))
    assert len(list_proposals(store)) == 2
