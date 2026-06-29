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
