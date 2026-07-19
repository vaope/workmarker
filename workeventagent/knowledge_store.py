"""Durable per-entity storage for F007 knowledge jobs, proposals, and runs.

Every entity has TTL=0, is versioned, and is replaced atomically.  The module
does not own scheduling configuration or project writes; it only persists the
state needed to recover them safely.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from workeventagent.project_schema import parse_timeline_events


_JOB_IMMUTABLE = {
    "schema_version",
    "entity_kind",
    "job_id",
    "idempotency_key",
    "project_id",
    "project_path",
    "trigger",
    "source_event_ids",
    "date_from",
    "date_to",
    "range_start_utc",
    "range_end_utc",
    "schedule_run_id",
    "capture_id",
    "created_at",
}
_PROPOSAL_IMMUTABLE = {
    "schema_version",
    "entity_kind",
    "proposal_id",
    "proposal_kind",
    "project_id",
    "project_path",
    "trigger",
    "source_events",
    "changes",
    "document",
    "title",
    "purpose",
    "retained_summary",
    "module_id",
    "filename",
    "order",
    "target_path",
    "preview",
    "preview_hash",
    "module_updated",
    "linked_section_proposal_id",
    "linked_technical_overview_hash",
    "supersedes",
    "created_at",
}
_SUCCESS_STATES = {"completed", "skipped_no_evidence", "skipped_no_change"}


def _iso(now: datetime | None = None) -> str:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _root(workspace: Path) -> Path:
    return Path(workspace) / ".workeventagent" / "knowledge"


def _entity_path(workspace: Path, kind: str, entity_id: str) -> Path:
    return _root(workspace) / kind / f"{entity_id}.json"


def _read(path: Path) -> dict:
    if not path.exists():
        raise ValueError(f"entity not found: {path.stem}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"invalid entity: {path}")
    return value


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _list(workspace: Path, kind: str) -> list[dict]:
    directory = _root(workspace) / kind
    if not directory.exists():
        return []
    return [_read(path) for path in sorted(directory.glob("*.json"))]


def job_id_for(idempotency_key: str) -> str:
    if not idempotency_key.strip():
        raise ValueError("idempotency_key is required")
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:20]
    return f"kj-{digest}"


def run_id_for(cadence: str, schedule_key: str) -> str:
    if cadence not in {"daily", "weekly"}:
        raise ValueError(f"unsupported cadence: {cadence}")
    if not schedule_key.strip():
        raise ValueError("schedule_key is required")
    digest = hashlib.sha256(f"{cadence}:{schedule_key}".encode("utf-8")).hexdigest()[:20]
    return f"kr-{digest}"


def enqueue_job(
    workspace: Path, spec: dict, now: datetime | None = None
) -> dict:
    key = str(spec.get("idempotency_key", ""))
    job_id = job_id_for(key)
    path = _entity_path(workspace, "jobs", job_id)
    if path.exists():
        return _read(path)

    state = str(spec.get("state", "queued"))
    if state not in {"awaiting_source", "queued"}:
        raise ValueError(f"invalid initial job state: {state}")
    created_at = _iso(now)
    job = {
        "schema_version": 1,
        "entity_kind": "knowledge_job",
        "job_id": job_id,
        "idempotency_key": key,
        "state": state,
        "version": 1,
        "project_id": str(spec["project_id"]),
        "project_path": str(spec["project_path"]),
        "trigger": str(spec["trigger"]),
        "source_event_ids": list(spec.get("source_event_ids", [])),
        "created_at": created_at,
        "updated_at": created_at,
    }
    for key_name in (
        "date_from",
        "date_to",
        "range_start_utc",
        "range_end_utc",
        "schedule_run_id",
        "capture_id",
    ):
        if key_name in spec and spec[key_name] is not None:
            job[key_name] = spec[key_name]
    _write(path, job)
    return job


def get_job(workspace: Path, job_id: str) -> dict:
    return _read(_entity_path(workspace, "jobs", job_id))


def list_jobs(workspace: Path, project_path: str | None = None) -> list[dict]:
    jobs = _list(workspace, "jobs")
    if project_path is not None:
        jobs = [job for job in jobs if job.get("project_path") == project_path]
    return jobs


def _transition(
    path: Path,
    *,
    expected_version: int,
    from_states: set[str],
    to_state: str,
    patch: dict | None,
    immutable: set[str],
) -> dict:
    current = _read(path)
    if current.get("version") != expected_version:
        raise ValueError(
            f"version conflict for {path.stem}: expected {expected_version}, "
            f"got {current.get('version')}"
        )
    if current.get("state") not in from_states:
        raise ValueError(
            f"state conflict for {path.stem}: expected one of {sorted(from_states)}, "
            f"got {current.get('state')}"
        )
    updates = dict(patch or {})
    forbidden = sorted(key for key in updates if key in immutable)
    if forbidden:
        raise ValueError(f"immutable payload fields cannot change: {forbidden}")
    updates.pop("state", None)
    updates.pop("version", None)
    updates.pop("updated_at", None)
    result = dict(current)
    result.update(updates)
    result["state"] = to_state
    result["version"] = expected_version + 1
    result["updated_at"] = _iso()
    _write(path, result)
    return result


def transition_job(
    workspace: Path,
    job_id: str,
    expected_version: int,
    from_states: set[str],
    to_state: str,
    patch: dict | None = None,
) -> dict:
    return _transition(
        _entity_path(workspace, "jobs", job_id),
        expected_version=expected_version,
        from_states=from_states,
        to_state=to_state,
        patch=patch,
        immutable=_JOB_IMMUTABLE,
    )


def _source_events_exist(job: dict) -> bool:
    project_path = Path(str(job.get("project_path", "")))
    if not project_path.is_file():
        return False
    try:
        events = parse_timeline_events(project_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return False
    present = {str(event.get("event_id", "")) for event in events}
    return all(str(event_id) in present for event_id in job.get("source_event_ids", []))


def recover_jobs(workspace: Path) -> list[dict]:
    recovered: list[dict] = []
    for job in list_jobs(workspace):
        if job["state"] == "processing":
            recovered.append(
                transition_job(
                    workspace,
                    job["job_id"],
                    job["version"],
                    {"processing"},
                    "queued",
                    {"recovery_reason": "interrupted_processing"},
                )
            )
        elif job["state"] == "awaiting_source" and _source_events_exist(job):
            recovered.append(
                transition_job(
                    workspace,
                    job["job_id"],
                    job["version"],
                    {"awaiting_source"},
                    "queued",
                    {"recovery_reason": "source_event_present"},
                )
            )
    return recovered


def create_proposal(
    workspace: Path, proposal: dict, now: datetime | None = None
) -> dict:
    proposal_id = str(proposal.get("proposal_id", ""))
    if not proposal_id:
        raise ValueError("proposal_id is required")
    path = _entity_path(workspace, "proposals", proposal_id)
    if path.exists():
        existing = _read(path)
        comparable = {
            key: value
            for key, value in proposal.items()
            if key not in {"state", "version", "created_at", "updated_at"}
        }
        if any(existing.get(key) != value for key, value in comparable.items()):
            raise ValueError(f"proposal_id conflict: {proposal_id}")
        return existing
    created_at = _iso(now)
    result = {
        "schema_version": 1,
        "entity_kind": "knowledge_proposal",
        **proposal,
        "proposal_id": proposal_id,
        "state": "needs_confirmation",
        "version": 1,
        "created_at": created_at,
        "updated_at": created_at,
    }
    _write(path, result)
    return result


def get_proposal(workspace: Path, proposal_id: str) -> dict:
    return _read(_entity_path(workspace, "proposals", proposal_id))


def list_proposals(workspace: Path, project_path: str | None = None) -> list[dict]:
    proposals = _list(workspace, "proposals")
    if project_path is not None:
        proposals = [item for item in proposals if item.get("project_path") == project_path]
    return proposals


def transition_proposal(
    workspace: Path,
    proposal_id: str,
    expected_version: int,
    from_states: set[str],
    to_state: str,
    patch: dict | None = None,
) -> dict:
    return _transition(
        _entity_path(workspace, "proposals", proposal_id),
        expected_version=expected_version,
        from_states=from_states,
        to_state=to_state,
        patch=patch,
        immutable=_PROPOSAL_IMMUTABLE,
    )


def create_schedule_run(
    workspace: Path,
    cadence: str,
    schedule_key: str,
    projects: list[dict],
    now: datetime | None = None,
) -> dict:
    run_id = run_id_for(cadence, schedule_key)
    path = _entity_path(workspace, "runs", run_id)
    if path.exists():
        return _read(path)
    expected_children: list[dict] = []
    for project in sorted(projects, key=lambda item: (str(item["project_id"]), str(item["project_path"]))):
        idempotency_key = f"schedule:{cadence}:{schedule_key}:{project['project_id']}"
        job_spec = {
            "idempotency_key": idempotency_key,
            "state": "queued",
            "project_id": str(project["project_id"]),
            "project_path": str(project["project_path"]),
            "trigger": cadence,
            "source_event_ids": [],
            "schedule_run_id": run_id,
        }
        for key_name in ("date_from", "date_to", "range_start_utc", "range_end_utc"):
            if key_name in project:
                job_spec[key_name] = project[key_name]
        expected_children.append(
            {"job_id": job_id_for(idempotency_key), "project_id": str(project["project_id"]), "job_spec": job_spec}
        )
    created_at = _iso(now)
    run = {
        "schema_version": 1,
        "entity_kind": "knowledge_schedule_run",
        "run_id": run_id,
        "cadence": cadence,
        "schedule_key": schedule_key,
        "state": "enqueuing",
        "version": 1,
        "expected_children": expected_children,
        "created_at": created_at,
        "updated_at": created_at,
    }
    _write(path, run)
    return run


def get_schedule_run(workspace: Path, run_id: str) -> dict:
    return _read(_entity_path(workspace, "runs", run_id))


def list_schedule_runs(workspace: Path) -> list[dict]:
    return _list(workspace, "runs")


def _write_run(workspace: Path, run: dict, *, state: str) -> dict:
    updated = dict(run)
    updated["state"] = state
    updated["version"] = int(run["version"]) + 1
    updated["updated_at"] = _iso()
    _write(_entity_path(workspace, "runs", str(run["run_id"])), updated)
    return updated


def ensure_schedule_children(workspace: Path, run_id: str) -> dict:
    run = get_schedule_run(workspace, run_id)
    for child in run.get("expected_children", []):
        enqueue_job(workspace, child["job_spec"])
    if run["state"] == "enqueuing":
        return _write_run(workspace, run, state="processing")
    return run


def evaluate_schedule_run(workspace: Path, run_id: str) -> dict:
    run = get_schedule_run(workspace, run_id)
    if run["state"] == "completed":
        return run
    children = [get_job(workspace, child["job_id"]) for child in run.get("expected_children", [])]
    if all(child.get("state") in _SUCCESS_STATES for child in children):
        return _write_run(workspace, run, state="completed")
    return run
