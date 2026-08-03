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


def parse_archivist_output(
    raw: str,
    event_id: str,
    source_text: str | None = None,
) -> ArchiveProposal:
    data = _load_json_object(raw, "archivist", _REQUIRED_TOP_KEYS)

    _validate_required_keys(data)

    ev = data["event"]
    status = _normalize_status(ev.get("status", "in_progress"))

    target = data["target"]
    if source_text is not None:
        input_text = source_text
    else:
        input_text = str(ev.get("input_text", ""))
        if not input_text.strip():
            raise OpencodeRunnerError(
                "source text is required when archivist output omits input_text"
            )
    summary = _faithful_summary(input_text, str(ev["summary"]))

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
            input_text=input_text,
            summary=summary,
            status=status,
            next_action=ev.get("next_action", ""),
            event_type=ev.get("event_type", "update"),
            corrects_event_id=ev.get("corrects_event_id"),
        ),
        attachment_paths=tuple(data.get("attachment_paths", [])),
    )
    return proposal


def _faithful_summary(source_text: str, proposed_summary: str) -> str:
    """Keep a specific summary, or fall back to a lossless normalized source."""
    source = source_text.strip()
    summary = proposed_summary.strip()
    if not source:
        return summary

    normalized_summary = _comparison_text(summary)
    missing_terms = [
        term
        for term in _protected_source_terms(source)
        if _comparison_text(term) not in normalized_summary
    ]
    source_for_summary = _normalize_source_as_summary(source)
    overcompressed = (
        len(source_for_summary) <= 240
        and len(summary) < int(len(source_for_summary) * 0.45)
    )
    unrepresented_bullet = any(
        not _bullet_is_represented(bullet, summary)
        for bullet in _source_bullets(source)
    )
    if not summary or missing_terms or overcompressed or unrepresented_bullet:
        return source_for_summary
    return summary


def _protected_source_terms(source_text: str) -> list[str]:
    """Extract explicit technical facts that a summary must not silently drop."""
    terms: list[str] = []
    terms.extend(re.findall(
        r"(?<![A-Za-z0-9])[A-Z][A-Z0-9.+#_-]*(?:/[A-Z0-9.+#_-]+)*(?![A-Za-z0-9])",
        source_text,
    ))
    for match in re.finditer(r"[（(]([^）)]{2,80})[）)]", source_text):
        terms.extend(re.split(r"[、,，；;]", match.group(1)))
    for match in re.finditer(
        r"(?:对|将)([^。；\n]{2,80}?)等?(?:进行|执行|生成|用于)",
        source_text,
    ):
        terms.extend(re.split(r"[、,，；;]", match.group(1)))
    terms.extend(re.findall(
        r"(?:降低|减少|提升|提高|避免|确保|实现)[^，。；\n]{2,40}",
        source_text,
    ))
    cleaned = [term.strip() for term in terms if len(term.strip()) >= 2]
    return list(dict.fromkeys(cleaned))


def _normalize_source_as_summary(source_text: str) -> str:
    parts: list[str] = []
    for raw_line in source_text.splitlines():
        line = re.sub(r"^\s*(?:[-*•·]+|\d+[.)、])\s*", "", raw_line).strip()
        line = line.rstrip("；;。").strip()
        if line:
            parts.append(line)
    normalized = "；".join(parts) or source_text.strip()
    return normalized.rstrip("；;。") + "。"


def _source_bullets(source_text: str) -> list[str]:
    bullets: list[str] = []
    for line in source_text.splitlines():
        match = re.match(r"^\s*(?:[-*•·]+|\d+[.)、])\s*(.+)", line)
        if match:
            bullet = match.group(1).strip()
            if bullet:
                bullets.append(bullet)
    return bullets


def _bullet_is_represented(bullet: str, summary: str) -> bool:
    """Require a small lexical anchor from every explicit source bullet."""
    bullet_markers = _cjk_bigrams(bullet) - _GENERIC_CJK_BIGRAMS
    if not bullet_markers:
        return True
    summary_markers = _cjk_bigrams(summary)
    required_matches = min(2, len(bullet_markers))
    return len(bullet_markers & summary_markers) >= required_matches


def _cjk_bigrams(value: str) -> set[str]:
    markers: set[str] = set()
    for segment in re.findall(r"[\u3400-\u9fff]+", value):
        markers.update(segment[index:index + 2] for index in range(len(segment) - 1))
    return markers


_GENERIC_CJK_BIGRAMS = {
    "任务",
    "工作",
    "开始",
    "进行",
    "继续",
    "相关",
    "问题",
    "处理",
    "检查",
    "确认",
    "记录",
    "系统",
}


def _comparison_text(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


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
    data = _load_json_object(
        raw,
        "synthesizer",
        {"changes", "document_suggestion"},
    )
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
    data = _load_json_object(raw, "project router", {"project_id"})

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

    opencode versions and providers may emit more than one text part, or wrap
    the final JSON in prose. Return the last decodable JSON candidate so legacy
    callers receive the final answer instead of the first explanatory fragment.
    """
    candidates = list(_iter_json_candidates(raw))
    for candidate in reversed(candidates):
        try:
            json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        return candidate

    text_parts = _opencode_text_parts(raw)
    fallback = text_parts[-1] if text_parts else raw
    return _extract_json_from_fence(fallback)


def _load_json_object(
    raw: str,
    source: str,
    required_keys: set[str] | frozenset[str] = frozenset(),
) -> dict:
    """Return the last JSON object that satisfies an output contract.

    A model may emit valid but non-final JSON (for example a progress note)
    before the actual response. Prefer the last object containing the required
    top-level keys, while preserving the existing field-specific validation
    errors when only incomplete objects were returned.
    """
    objects: list[dict] = []
    for candidate in _iter_json_candidates(raw):
        try:
            value = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(value, dict):
            objects.append(value)

    for value in reversed(objects):
        if required_keys <= value.keys():
            return value
    if objects:
        return objects[-1]
    raise OpencodeRunnerError(f"invalid JSON from {source}: no JSON object found")


def _opencode_text_parts(raw: str) -> list[str]:
    """Collect every assistant text part from opencode NDJSON output."""
    parts: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict) and record.get("type") == "text":
            part = record.get("part", {})
            if (
                isinstance(part, dict)
                and part.get("type") == "text"
                and isinstance(part.get("text"), str)
            ):
                text = part["text"]
                parts.append(text)
    return parts


def _iter_json_candidates(raw: str):
    """Yield fenced, plain, and prose-embedded JSON candidates in order."""
    text_parts = _opencode_text_parts(raw)
    sources = list(text_parts) if text_parts else [raw]
    if len(text_parts) > 1:
        # Some opencode/provider combinations stream one JSON document across
        # several type=text records. Joining without a separator reconstructs it.
        sources.append("".join(text_parts))

    decoder = json.JSONDecoder()
    for text in sources:
        for match in _JSON_FENCE_RE.finditer(text):
            candidate = match.group(1).strip()
            if candidate:
                yield candidate

        stripped = text.strip()
        if stripped:
            yield stripped

        # raw_decode lets us recover an unfenced JSON object after explanatory
        # prose without accepting the prose itself as part of the payload.
        position = 0
        while position < len(text):
            object_start = text.find("{", position)
            array_start = text.find("[", position)
            starts = [start for start in (object_start, array_start) if start >= 0]
            if not starts:
                break
            start = min(starts)
            try:
                _, end = decoder.raw_decode(text, start)
            except json.JSONDecodeError:
                position = start + 1
                continue
            yield text[start:end]
            position = end


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


def _extract_json_from_fence(text: str) -> str:
    match = _JSON_FENCE_RE.search(text)
    if match:
        return match.group(1).strip()
    return text


_REQUIRED_TOP_KEYS = {"target", "confidence", "reason", "event"}
_REQUIRED_TARGET_KEYS = {"project_id", "item_id", "task_id"}
_REQUIRED_EVENT_KEYS = {"task_id", "summary", "status", "next_action"}


# ── Phase B synthesis agent ───────────────────────────────────

def run_synthesizer(
    prompt: str, project_doc: Path, opencode_bin: str = "opencode", model: str = ""
) -> str:
    return _run_opencode_agent(
        prompt=prompt,
        input_doc=project_doc,
        agent_name="workevent-synthesizer",
        opencode_bin=opencode_bin,
        model=model,
    )


def parse_synthesizer_output(raw: str) -> dict:
    """Parse synthesizer JSON output: {kind, sections: [{section_id, content, reason, source_event_ids}]}"""
    data = _load_json_object(raw, "synthesizer", {"kind", "sections"})

    required = {"kind", "sections"}
    missing = required - data.keys()
    if missing:
        raise OpencodeRunnerError(f"missing keys in synthesizer output: {sorted(missing)}")

    if not isinstance(data["sections"], list) or len(data["sections"]) == 0:
        raise OpencodeRunnerError("synthesizer returned empty sections")

    for i, s in enumerate(data["sections"]):
        for field in ("section_id", "content", "reason"):
            if field not in s:
                raise OpencodeRunnerError(f"section[{i}] missing field: {field}")

    return data


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
