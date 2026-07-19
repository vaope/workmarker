from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from workeventagent.models import ArchiveProposal, TargetRef, TimelineEvent
from workeventagent.text_validation import is_single_printable_line


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


def run_project_synthesizer(
    prompt: str, project_doc: Path, opencode_bin: str = "opencode", model: str = ""
) -> str:
    return _run_opencode_agent(
        prompt=prompt,
        input_doc=project_doc,
        agent_name="workevent-synthesizer",
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


_SYNTHESIS_TARGETS = {"current-panorama", "technical-overview", "project-knowledge"}
_AGENT_FORBIDDEN_KEYS = {
    "project_id",
    "proposal_id",
    "job_id",
    "source_event_ids",
    "base_section_hash",
    "target_section_hash",
    "module_id",
    "filename",
    "order",
    "path",
    "file_path",
    "heading",
    "anchor",
    "comment",
}


def _bounded_narrative(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise OpencodeRunnerError(f"{field} must be a string")
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    if (
        "<!--" in text
        or re.search(r"(?m)^#{1,6}\s", text)
        or re.search(r"(?m)^---\s*$", text)
        or re.search(r"[A-Za-z]:[\\/]", text)
        or re.search(r"(?:^|\s)(?:\.\.?[\\/]|/[A-Za-z0-9_.-])", text)
        or "\\\\" in text
    ):
        raise OpencodeRunnerError(f"{field} contains forbidden structure or path")
    return text


def _parse_content_block(value: object, field: str) -> dict:
    if not isinstance(value, dict) or set(value) != {"paragraphs", "bullets"}:
        raise OpencodeRunnerError(f"{field} must contain exactly paragraphs and bullets")
    paragraphs = value["paragraphs"]
    bullets = value["bullets"]
    if not isinstance(paragraphs, list) or not isinstance(bullets, list):
        raise OpencodeRunnerError(f"{field} paragraphs and bullets must be arrays")
    return {
        "paragraphs": [
            _bounded_narrative(item, f"{field}.paragraphs") for item in paragraphs
        ],
        "bullets": [_bounded_narrative(item, f"{field}.bullets") for item in bullets],
    }


def _bounded_single_line(value: object, field: str) -> str:
    text = _bounded_narrative(value, field)
    if not is_single_printable_line(text):
        raise OpencodeRunnerError(f"{field} must be a non-empty single-line string")
    return text


def _reject_forbidden_agent_keys(value: object) -> None:
    if isinstance(value, dict):
        forbidden = sorted(set(value) & _AGENT_FORBIDDEN_KEYS)
        if forbidden:
            raise OpencodeRunnerError(f"agent returned wrapper-owned fields: {forbidden}")
        for nested in value.values():
            _reject_forbidden_agent_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_forbidden_agent_keys(nested)


def parse_synthesis_output(raw: str) -> dict:
    """Parse the read-only synthesizer response without accepting control data."""
    try:
        data = json.loads(_extract_json_text(raw))
    except json.JSONDecodeError as exc:
        raise OpencodeRunnerError(f"invalid JSON from synthesizer: {exc}") from exc
    if not isinstance(data, dict) or set(data) != {"changes", "document_suggestion"}:
        raise OpencodeRunnerError("synthesizer output must contain exactly changes and document_suggestion")
    _reject_forbidden_agent_keys(data)
    if not isinstance(data["changes"], list):
        raise OpencodeRunnerError("changes must be an array")

    targets: set[str] = set()
    changes: list[dict] = []
    for index, change in enumerate(data["changes"]):
        if not isinstance(change, dict) or set(change) != {"target_section", "reason", "content"}:
            raise OpencodeRunnerError(f"changes[{index}] has an invalid shape")
        target = change["target_section"]
        if target not in _SYNTHESIS_TARGETS:
            raise OpencodeRunnerError(f"unknown target section: {target}")
        if target in targets:
            raise OpencodeRunnerError(f"duplicate target section: {target}")
        targets.add(target)
        changes.append(
            {
                "target_section": target,
                "reason": _bounded_narrative(change["reason"], f"changes[{index}].reason"),
                "content": _parse_content_block(change["content"], f"changes[{index}].content"),
            }
        )

    suggestion = data["document_suggestion"]
    parsed_suggestion = None
    if suggestion is not None:
        required = {
            "purpose",
            "title",
            "retained_summary",
            "module_conclusion",
            "module_body",
        }
        if not isinstance(suggestion, dict) or set(suggestion) != required:
            raise OpencodeRunnerError("document_suggestion has an invalid shape")
        parsed_suggestion = {
            "purpose": _bounded_narrative(suggestion["purpose"], "document_suggestion.purpose"),
            "title": _bounded_single_line(suggestion["title"], "document_suggestion.title"),
            "retained_summary": _bounded_narrative(
                suggestion["retained_summary"], "document_suggestion.retained_summary"
            ),
            "module_conclusion": _parse_content_block(
                suggestion["module_conclusion"], "document_suggestion.module_conclusion"
            ),
            "module_body": _parse_content_block(
                suggestion["module_body"], "document_suggestion.module_body"
            ),
        }
    return {"changes": changes, "document_suggestion": parsed_suggestion}


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
