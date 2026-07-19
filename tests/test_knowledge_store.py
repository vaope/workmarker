from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from workeventagent.knowledge_store import (
    create_proposal,
    create_schedule_run,
    enqueue_job,
    ensure_schedule_children,
    evaluate_schedule_run,
    get_job,
    get_proposal,
    get_schedule_run,
    job_id_for,
    list_jobs,
    list_proposals,
    recover_jobs,
    transition_job,
    transition_proposal,
)


NOW = datetime(2026, 7, 20, 8, 30, tzinfo=timezone.utc)


def _project(tmp_path: Path, project_id: str = "alpha", event_id: str = "event-a") -> Path:
    path = tmp_path / f"{project_id}.md"
    path.write_text(
        "\n".join(
            [
                "---",
                f"project_id: {project_id}",
                "title: Alpha",
                "doc_kind: work_project",
                "schema_version: 2",
                "status: active",
                "phase: implementation",
                "created: 2026-07-20",
                "updated: 2026-07-20",
                "---",
                "# Alpha",
                "",
                "## 事件证据 <!-- section:timeline -->",
                "",
                f"- 2026-07-20T08:00:00+00:00 <!-- event:{event_id} -->",
                "  - task_id: task-a",
                "  - input: Captured evidence.",
                "  - summary: Evidence captured.",
                "  - status: in_progress",
                "  - next_action: Continue.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _job_spec(project: Path, *, state: str = "queued", key: str = "directed:alpha:event-a") -> dict:
    return {
        "idempotency_key": key,
        "state": state,
        "project_id": project.stem,
        "project_path": str(project),
        "trigger": "directed",
        "source_event_ids": ["event-a"],
    }


def test_enqueue_is_idempotent_and_uses_one_file_per_job(tmp_path: Path) -> None:
    project = _project(tmp_path)
    first = enqueue_job(tmp_path, _job_spec(project), now=NOW)
    second = enqueue_job(tmp_path, _job_spec(project), now=NOW)

    assert first == second
    assert first["job_id"] == job_id_for("directed:alpha:event-a")
    files = list((tmp_path / ".workeventagent" / "knowledge" / "jobs").glob("*.json"))
    assert len(files) == 1
    assert files[0].stem == first["job_id"]


def test_job_transition_rejects_wrong_version_or_state(tmp_path: Path) -> None:
    project = _project(tmp_path)
    job = enqueue_job(tmp_path, _job_spec(project), now=NOW)

    with pytest.raises(ValueError, match="version"):
        transition_job(tmp_path, job["job_id"], 99, {"queued"}, "processing")
    with pytest.raises(ValueError, match="state"):
        transition_job(tmp_path, job["job_id"], 1, {"failed"}, "queued")

    claimed = transition_job(tmp_path, job["job_id"], 1, {"queued"}, "processing")
    assert claimed["state"] == "processing"
    assert claimed["version"] == 2


def test_proposal_transition_preserves_immutable_payload(tmp_path: Path) -> None:
    proposal = create_proposal(
        tmp_path,
        {
            "proposal_id": "kp-alpha",
            "proposal_kind": "section_bundle",
            "project_id": "alpha",
            "project_path": str(tmp_path / "alpha.md"),
            "trigger": "directed",
            "source_events": [{"event_id": "event-a", "summary": "Evidence"}],
            "changes": [{"target_section": "current-panorama", "before": "", "after": "Now"}],
        },
        now=NOW,
    )

    updated = transition_proposal(
        tmp_path,
        "kp-alpha",
        expected_version=1,
        from_states={"needs_confirmation"},
        to_state="applying",
        patch={"apply_started_at": "2026-07-20T08:31:00Z"},
    )

    assert updated["changes"] == proposal["changes"]
    assert updated["source_events"] == proposal["source_events"]
    assert updated["project_path"] == proposal["project_path"]
    with pytest.raises(ValueError, match="immutable"):
        transition_proposal(
            tmp_path,
            "kp-alpha",
            expected_version=2,
            from_states={"applying"},
            to_state="applied",
            patch={"changes": []},
        )


def test_recover_promotes_awaiting_source_when_event_exists(tmp_path: Path) -> None:
    project = _project(tmp_path)
    waiting = enqueue_job(tmp_path, _job_spec(project, state="awaiting_source"), now=NOW)

    recovered = recover_jobs(tmp_path)

    assert [item["job_id"] for item in recovered] == [waiting["job_id"]]
    assert get_job(tmp_path, waiting["job_id"])["state"] == "queued"


def test_recover_resets_interrupted_processing_to_queued(tmp_path: Path) -> None:
    project = _project(tmp_path)
    job = enqueue_job(tmp_path, _job_spec(project), now=NOW)
    processing = transition_job(tmp_path, job["job_id"], 1, {"queued"}, "processing")

    recover_jobs(tmp_path)

    recovered = get_job(tmp_path, job["job_id"])
    assert recovered["state"] == "queued"
    assert recovered["version"] == processing["version"] + 1


def test_terminal_entities_are_never_trimmed(tmp_path: Path) -> None:
    project = _project(tmp_path)
    job = enqueue_job(tmp_path, _job_spec(project), now=NOW)
    job = transition_job(tmp_path, job["job_id"], 1, {"queued"}, "processing")
    transition_job(tmp_path, job["job_id"], 2, {"processing"}, "completed")
    proposal = create_proposal(
        tmp_path,
        {"proposal_id": "kp-terminal", "proposal_kind": "section_bundle", "project_id": "alpha",
         "project_path": str(project), "trigger": "directed", "source_events": [], "changes": []},
        now=NOW,
    )
    transition_proposal(tmp_path, proposal["proposal_id"], 1, {"needs_confirmation"}, "rejected")

    recover_jobs(tmp_path)

    assert [item["state"] for item in list_jobs(tmp_path)] == ["completed"]
    assert [item["state"] for item in list_proposals(tmp_path)] == ["rejected"]


def test_atomic_replace_failure_preserves_previous_entity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    job = enqueue_job(tmp_path, _job_spec(project), now=NOW)
    before = get_job(tmp_path, job["job_id"])

    def fail_replace(_src: object, _dst: object) -> None:
        raise OSError("replace blocked")

    monkeypatch.setattr("workeventagent.knowledge_store.os.replace", fail_replace)
    with pytest.raises(OSError, match="replace blocked"):
        transition_job(tmp_path, job["job_id"], 1, {"queued"}, "processing")

    assert get_job(tmp_path, job["job_id"]) == before


def test_schedule_manifest_snapshots_all_v2_projects_before_first_child(tmp_path: Path) -> None:
    alpha = _project(tmp_path, "alpha")
    beta = _project(tmp_path, "beta")
    run = create_schedule_run(
        tmp_path,
        "daily",
        "2026-07-20",
        [{"project_id": "alpha", "project_path": str(alpha)}, {"project_id": "beta", "project_path": str(beta)}],
        now=NOW,
    )

    assert run["state"] == "enqueuing"
    assert len(run["expected_children"]) == 2
    assert list_jobs(tmp_path) == []
    persisted = json.loads(
        (tmp_path / ".workeventagent" / "knowledge" / "runs" / f"{run['run_id']}.json").read_text(encoding="utf-8")
    )
    assert persisted["expected_children"] == run["expected_children"]


def test_schedule_recovery_fills_children_after_crash_on_first_enqueue(tmp_path: Path) -> None:
    alpha = _project(tmp_path, "alpha")
    beta = _project(tmp_path, "beta")
    run = create_schedule_run(
        tmp_path, "daily", "2026-07-20",
        [{"project_id": "alpha", "project_path": str(alpha)}, {"project_id": "beta", "project_path": str(beta)}],
        now=NOW,
    )
    first = run["expected_children"][0]
    enqueue_job(tmp_path, first["job_spec"], now=NOW)

    recovered = ensure_schedule_children(tmp_path, run["run_id"])

    assert recovered["state"] == "processing"
    assert {job["job_id"] for job in list_jobs(tmp_path)} == {
        child["job_id"] for child in run["expected_children"]
    }


def test_schedule_run_with_one_failed_child_never_completes(tmp_path: Path) -> None:
    alpha = _project(tmp_path, "alpha")
    beta = _project(tmp_path, "beta")
    run = create_schedule_run(
        tmp_path, "weekly", "2026-W30",
        [{"project_id": "alpha", "project_path": str(alpha)}, {"project_id": "beta", "project_path": str(beta)}],
        now=NOW,
    )
    ensure_schedule_children(tmp_path, run["run_id"])
    first, second = [get_job(tmp_path, child["job_id"]) for child in run["expected_children"]]
    first = transition_job(tmp_path, first["job_id"], first["version"], {"queued"}, "processing")
    transition_job(tmp_path, first["job_id"], first["version"], {"processing"}, "failed")
    second = transition_job(tmp_path, second["job_id"], second["version"], {"queued"}, "processing")
    transition_job(tmp_path, second["job_id"], second["version"], {"processing"}, "completed")

    evaluated = evaluate_schedule_run(tmp_path, run["run_id"])

    assert evaluated["state"] == "processing"


def test_failed_child_retry_reopens_run_and_advances_marker_only_after_completion(tmp_path: Path) -> None:
    alpha = _project(tmp_path, "alpha")
    run = create_schedule_run(
        tmp_path, "daily", "2026-07-20",
        [{"project_id": "alpha", "project_path": str(alpha)}],
        now=NOW,
    )
    ensure_schedule_children(tmp_path, run["run_id"])
    child = get_job(tmp_path, run["expected_children"][0]["job_id"])
    child = transition_job(tmp_path, child["job_id"], child["version"], {"queued"}, "processing")
    child = transition_job(tmp_path, child["job_id"], child["version"], {"processing"}, "failed")
    assert evaluate_schedule_run(tmp_path, run["run_id"])["state"] == "processing"

    child = transition_job(tmp_path, child["job_id"], child["version"], {"failed"}, "queued")
    child = transition_job(tmp_path, child["job_id"], child["version"], {"queued"}, "processing")
    transition_job(tmp_path, child["job_id"], child["version"], {"processing"}, "completed")

    assert evaluate_schedule_run(tmp_path, run["run_id"])["state"] == "completed"
    assert get_schedule_run(tmp_path, run["run_id"])["state"] == "completed"
