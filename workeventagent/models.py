from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


Status = Literal["in_progress", "done"]
DecisionKind = Literal["confirm", "edit", "cancel"]


@dataclass(frozen=True)
class TargetRef:
    project_id: str
    item_id: str
    task_id: str
    task_title: str = ""
    item_title: str = ""
    new_item: bool = False
    new_task: bool = False


@dataclass(frozen=True)
class TimelineEvent:
    event_id: str
    task_id: str
    input_text: str
    summary: str
    status: Status
    next_action: str
    event_type: str = "update"
    corrects_event_id: str | None = None


@dataclass(frozen=True)
class ArchiveProposal:
    target: TargetRef
    confidence: float
    reason: str
    event: TimelineEvent
    attachment_paths: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ConfirmationDecision:
    kind: DecisionKind
    edited_proposal: ArchiveProposal | None = None


# ── Phase B synthesis types ─────────────────────────────────

ProposalState = Literal["pending", "applied", "rejected", "stale", "superseded", "error"]
SynthesisKind = Literal["current-panorama", "technical-overview", "project-knowledge", "multi"]


@dataclass(frozen=True)
class SectionProposal:
    """A single section's proposed content with evidence."""
    section_id: str
    content: str
    base_section_hash: str
    source_event_ids: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class SynthesisBundle:
    """One or more section proposals bundled together for atomic apply."""
    project_id: str
    kind: SynthesisKind
    sections: tuple[SectionProposal, ...]
    trigger_event_ids: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class SynthesisProposal:
    """Persisted synthesis proposal with CAS version and state."""
    proposal_id: str
    project_id: str
    bundle: SynthesisBundle
    state: ProposalState
    version: int
    created_at: str
    updated_at: str
    error_message: str = ""
