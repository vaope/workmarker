from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from workeventagent.models import ArchiveProposal, TargetRef, TimelineEvent


class OpencodeRunnerError(Exception):
    """Raised when the opencode archivist fails to produce a valid proposal."""


def run_archivist(
    prompt: str, project_doc: Path, opencode_bin: str = "opencode"
) -> str:
    cmd = [
        opencode_bin,
        "run",
        "--agent",
        "workevent-archivist",
        "--file",
        str(project_doc),
        "--format",
        "json",
        prompt,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise OpencodeRunnerError(
            f"opencode exited {result.returncode}: {result.stderr.strip()}"
        )
    if not result.stdout.strip():
        raise OpencodeRunnerError("opencode returned empty stdout")
    return result.stdout


def parse_archivist_output(raw: str, event_id: str) -> ArchiveProposal:
    inner = _extract_json_text(raw)
    try:
        data = json.loads(inner)
    except json.JSONDecodeError as exc:
        raise OpencodeRunnerError(f"invalid JSON from archivist: {exc}") from exc

    _validate_required_keys(data)

    target = data["target"]
    ev = data["event"]

    proposal = ArchiveProposal(
        target=TargetRef(
            project_id=target["project_id"],
            item_id=target["item_id"],
            task_id=target["task_id"],
            task_title=target.get("task_title", ""),
            new_item=target.get("new_item", False),
            new_task=target.get("new_task", False),
        ),
        confidence=float(data["confidence"]),
        reason=data["reason"],
        event=TimelineEvent(
            event_id=event_id,
            task_id=ev["task_id"],
            input_text=ev["input_text"],
            summary=ev["summary"],
            status=ev.get("status", "in_progress"),
            next_action=ev.get("next_action", ""),
            event_type=ev.get("event_type", "update"),
            corrects_event_id=ev.get("corrects_event_id"),
        ),
        attachment_paths=tuple(data.get("attachment_paths", [])),
    )
    return proposal


def _extract_json_text(raw: str) -> str:
    """Extract JSON payload from opencode NDJSON output.

    opencode --format json outputs NDJSON lines. We look for type=text
    lines and extract .part.text, which may contain a ```json``` fence.
    If no NDJSON structure is detected, treat the raw input as plain JSON/NDJSON.
    """
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("type") == "text":
            part = record.get("part", {})
            if part.get("type") == "text" and "text" in part:
                text = part["text"]
                return _extract_json_from_fence(text)

    # Fallback: treat raw text as plain JSON (no NDJSON wrapper / no fence)
    return _extract_json_from_fence(raw)


def _extract_json_from_fence(text: str) -> str:
    fence_re = re.compile(r"```(?:json)?\s*\n(.*?)\n\s*```", re.DOTALL)
    match = fence_re.search(text)
    if match:
        return match.group(1)
    return text


_REQUIRED_TOP_KEYS = {"target", "confidence", "reason", "event"}
_REQUIRED_TARGET_KEYS = {"project_id", "item_id", "task_id"}
_REQUIRED_EVENT_KEYS = {"task_id", "input_text", "summary", "status", "next_action"}


def _validate_required_keys(data: dict) -> None:
    missing = _REQUIRED_TOP_KEYS - data.keys()
    if missing:
        raise OpencodeRunnerError(f"missing top-level keys: {sorted(missing)}")
    target = data.get("target", {})
    missing_target = _REQUIRED_TARGET_KEYS - target.keys()
    if missing_target:
        raise OpencodeRunnerError(f"missing target keys: {sorted(missing_target)}")
    ev = data.get("event", {})
    missing_event = _REQUIRED_EVENT_KEYS - ev.keys()
    if missing_event:
        raise OpencodeRunnerError(f"missing event keys: {sorted(missing_event)}")
