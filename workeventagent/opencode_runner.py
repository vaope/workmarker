from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from workeventagent.models import ArchiveProposal, TargetRef, TimelineEvent


class OpencodeRunnerError(Exception):
    """Raised when the opencode archivist fails to produce a valid proposal."""


OPENCODE_TIMEOUT_SECONDS = 600


def run_archivist(
    prompt: str, project_doc: Path, opencode_bin: str = "opencode", model: str = ""
) -> str:
    return _run_opencode_agent(
        prompt=prompt,
        input_doc=project_doc,
        agent_name="workevent-archivist",
        opencode_bin=opencode_bin,
        model=model,
    )


def run_project_router(
    prompt: str, routing_doc: Path, opencode_bin: str = "opencode", model: str = ""
) -> str:
    return _run_opencode_agent(
        prompt=prompt,
        input_doc=routing_doc,
        agent_name="workevent-router",
        opencode_bin=opencode_bin,
        model=model,
    )


def run_reporter(
    prompt: str, report_doc: Path, opencode_bin: str = "opencode", model: str = ""
) -> str:
    return _run_opencode_agent(
        prompt=prompt,
        input_doc=report_doc,
        agent_name="workevent-reporter",
        opencode_bin=opencode_bin,
        model=model,
    )


def _run_opencode_agent(
    prompt: str,
    input_doc: Path,
    agent_name: str,
    opencode_bin: str = "opencode",
    model: str = "",
) -> str:
    cmd = [
        _resolve_executable(opencode_bin),
        "run",
    ]
    model = model.strip()
    if model:
        cmd.extend(["--model", model])
    cmd.extend([
        "--agent",
        agent_name,
        "--file",
        str(input_doc),
        "--format",
        "json",
        prompt,
    ])
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=OPENCODE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise OpencodeRunnerError(
            f"opencode timed out after {OPENCODE_TIMEOUT_SECONDS} seconds"
        ) from exc
    except FileNotFoundError as exc:
        raise OpencodeRunnerError(
            f"could not start opencode executable: {opencode_bin}"
        ) from exc
    if result.returncode != 0:
        raise OpencodeRunnerError(
            f"opencode exited {result.returncode}: {result.stderr.strip()}"
        )
    stdout = result.stdout or ""
    if not stdout.strip():
        raise OpencodeRunnerError("opencode returned empty stdout")
    return stdout


def _resolve_executable(opencode_bin: str) -> str:
    return shutil.which(opencode_bin) or opencode_bin


def parse_archivist_output(raw: str, event_id: str) -> ArchiveProposal:
    inner = _extract_json_text(raw)
    try:
        data = json.loads(inner)
    except json.JSONDecodeError as exc:
        raise OpencodeRunnerError(f"invalid JSON from archivist: {exc}") from exc

    _validate_required_keys(data)

    ev = data["event"]
    status = _normalize_status(ev.get("status", "in_progress"))

    target = data["target"]

    if target.get("new_task") and not target.get("task_title", "").strip():
        raise OpencodeRunnerError("task_title is required when new_task is true")

    proposal = ArchiveProposal(
        target=TargetRef(
            project_id=target["project_id"],
            item_id=target["item_id"],
            task_id=target["task_id"],
            task_title=target.get("task_title", ""),
            item_title=target.get("item_title", ""),
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
            status=status,
            next_action=ev.get("next_action", ""),
            event_type=ev.get("event_type", "update"),
            corrects_event_id=ev.get("corrects_event_id"),
        ),
        attachment_paths=tuple(data.get("attachment_paths", [])),
    )
    return proposal


_KNOWLEDGE_DIMENSIONS = {"goal", "scope", "architecture", "risk", "milestone"}


def parse_knowledge_impact(raw: str) -> dict:
    """Return bounded pre-confirmation impact metadata, failing closed.

    Archive parsing must remain available even when the optional impact object is
    malformed.  The returned object deliberately drops every field not owned by
    this adapter, including any agent-supplied IDs.
    """
    fallback = {
        "level": "ordinary",
        "dimensions": [],
        "reason": "Impact metadata was missing or invalid; treated as ordinary.",
    }
    try:
        data = json.loads(_extract_json_text(raw))
    except (json.JSONDecodeError, TypeError, ValueError):
        return fallback
    impact = data.get("knowledge_impact")
    if not isinstance(impact, dict):
        return fallback
    level = impact.get("level")
    dimensions = impact.get("dimensions")
    reason = impact.get("reason")
    if level not in {"ordinary", "high"}:
        return fallback
    if not isinstance(dimensions, list) or any(
        not isinstance(value, str) or value not in _KNOWLEDGE_DIMENSIONS
        for value in dimensions
    ):
        return fallback
    if not isinstance(reason, str):
        return fallback
    clean_reason = reason.strip()
    if level == "high" and (not dimensions or not clean_reason):
        return fallback
    return {
        "level": level,
        "dimensions": list(dict.fromkeys(dimensions)),
        "reason": clean_reason,
    }


def _normalize_status(raw_status: object) -> str:
    status = str(raw_status).strip().lower().replace("-", " ").replace("_", " ")
    done_aliases = {
        "done",
        "complete",
        "completed",
        "finished",
        "resolved",
        "closed",
        "abandoned",
        "cancelled",
        "canceled",
    }
    if status in done_aliases:
        return "done"
    return "in_progress"


def parse_project_route_output(raw: str, allowed_project_ids: set[str]) -> dict:
    inner = _extract_json_text(raw)
    try:
        data = json.loads(inner)
    except json.JSONDecodeError as exc:
        raise OpencodeRunnerError(f"invalid JSON from project router: {exc}") from exc

    project_id = str(data.get("project_id", "")).strip()
    if not project_id:
        raise OpencodeRunnerError("project router did not return project_id")
    if project_id not in allowed_project_ids:
        raise OpencodeRunnerError(f"project router returned unknown project_id: {project_id}")

    try:
        confidence = float(data.get("confidence", 0))
    except (TypeError, ValueError) as exc:
        raise OpencodeRunnerError("project router confidence is not numeric") from exc

    return {
        "project_id": project_id,
        "confidence": confidence,
        "reason": str(data.get("reason", "")),
    }


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
