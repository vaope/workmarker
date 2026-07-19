"""Typed CAS ledger for synthesis proposals. TTL=0 (permanent storage).

States: pending → applied | rejected | stale | superseded | error
Each proposal is versioned; state transitions require matching expected version (CAS).
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from workeventagent.models import (
    SectionProposal,
    SynthesisBundle,
    SynthesisProposal,
    SynthesisKind,
    ProposalState,
)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _proposal_path(store_dir: Path, proposal_id: str) -> Path:
    return store_dir / f"{proposal_id}.json"


def create_proposal(store_dir: Path, bundle: SynthesisBundle) -> SynthesisProposal:
    """Create a new pending proposal in the ledger."""
    store_dir.mkdir(parents=True, exist_ok=True)
    proposal_id = uuid.uuid4().hex[:12]
    now = _now()
    proposal = SynthesisProposal(
        proposal_id=proposal_id,
        project_id=bundle.project_id,
        bundle=bundle,
        state="pending",
        version=1,
        created_at=now,
        updated_at=now,
    )
    _write(store_dir, proposal)
    return proposal


def get_proposal(store_dir: Path, proposal_id: str) -> SynthesisProposal | None:
    """Read a single proposal by ID."""
    path = _proposal_path(store_dir, proposal_id)
    if not path.exists():
        return None
    return _read(path)


def list_proposals(
    store_dir: Path,
    project_id: str | None = None,
    state: ProposalState | None = None,
) -> list[SynthesisProposal]:
    """List proposals, optionally filtered by project_id and/or state."""
    if not store_dir.exists():
        return []
    proposals: list[SynthesisProposal] = []
    for entry in sorted(store_dir.iterdir()):
        if not entry.suffix == ".json":
            continue
        p = _read(entry)
        if project_id is not None and p.project_id != project_id:
            continue
        if state is not None and p.state != state:
            continue
        proposals.append(p)
    return proposals


def update_proposal_state(
    store_dir: Path,
    proposal_id: str,
    state: ProposalState,
    expected_version: int,
    error_message: str = "",
) -> SynthesisProposal:
    """Update proposal state with CAS guard — rejects stale versions."""
    existing = get_proposal(store_dir, proposal_id)
    if existing is None:
        raise ValueError(f"proposal not found: {proposal_id}")
    if existing.version != expected_version:
        raise ValueError(
            f"stale version: expected {expected_version}, got {existing.version}"
        )
    updated = SynthesisProposal(
        proposal_id=existing.proposal_id,
        project_id=existing.project_id,
        bundle=existing.bundle,
        state=state,
        version=existing.version + 1,
        created_at=existing.created_at,
        updated_at=_now(),
        error_message=error_message,
    )
    _write(store_dir, updated)
    return updated


def supersede_proposals(
    store_dir: Path,
    project_id: str,
    kind: str,
    except_proposal_id: str,
) -> None:
    """Mark older pending proposals of the same project+kind as superseded."""
    for p in list_proposals(store_dir, project_id=project_id, state="pending"):
        if p.proposal_id == except_proposal_id:
            continue
        if p.bundle.kind == kind:
            update_proposal_state(store_dir, p.proposal_id, "superseded", p.version)


def _write(store_dir: Path, proposal: SynthesisProposal) -> None:
    data = {
        "proposal_id": proposal.proposal_id,
        "project_id": proposal.project_id,
        "bundle": {
            "project_id": proposal.bundle.project_id,
            "kind": proposal.bundle.kind,
            "sections": [
                {
                    "section_id": s.section_id,
                    "content": s.content,
                    "base_section_hash": s.base_section_hash,
                    "source_event_ids": list(s.source_event_ids),
                    "reason": s.reason,
                }
                for s in proposal.bundle.sections
            ],
            "trigger_event_ids": list(proposal.bundle.trigger_event_ids),
        },
        "state": proposal.state,
        "version": proposal.version,
        "created_at": proposal.created_at,
        "updated_at": proposal.updated_at,
        "error_message": proposal.error_message,
    }
    tmp = store_dir / f".{proposal.proposal_id}.tmp"
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, _proposal_path(store_dir, proposal.proposal_id))


def _read(path: Path) -> SynthesisProposal:
    data = json.loads(path.read_text(encoding="utf-8"))
    b = data["bundle"]
    return SynthesisProposal(
        proposal_id=data["proposal_id"],
        project_id=data["project_id"],
        bundle=SynthesisBundle(
            project_id=b["project_id"],
            kind=b["kind"],
            sections=tuple(
                SectionProposal(
                    section_id=s["section_id"],
                    content=s["content"],
                    base_section_hash=s["base_section_hash"],
                    source_event_ids=tuple(s.get("source_event_ids", [])),
                    reason=s["reason"],
                )
                for s in b["sections"]
            ),
            trigger_event_ids=tuple(b.get("trigger_event_ids", [])),
        ),
        state=data["state"],
        version=data["version"],
        created_at=data["created_at"],
        updated_at=data["updated_at"],
        error_message=data.get("error_message", ""),
    )
