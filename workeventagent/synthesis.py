"""Synthesis generation and atomic bundle apply for Phase B.

generate_synthesis() calls the workevent-synthesizer opencode agent to produce
section proposals from timeline events.

validate_bundle() checks all source events exist and section hashes match.

apply_bundle() validates, renders in memory, and atomically writes all-or-nothing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from workeventagent.models import SectionProposal, SynthesisBundle
from workeventagent.project_schema import (
    parse_timeline_events,
    replace_section_content,
    schema_version,
    section_hash,
    update_frontmatter,
)
from workeventagent.markdown_store import write_project_atomically
from workeventagent.proposal_store import get_proposal, update_proposal_state


def validate_bundle(project_path: Path, bundle: SynthesisBundle) -> None:
    """Validate all source events exist and all section hashes match.

    Raises ValueError on any failure — callers should treat this as a pre-check
    before apply_bundle.
    """
    text = project_path.read_text(encoding="utf-8")
    if schema_version(text) < 2:
        raise ValueError("project must be v2")

    # Validate all source events exist in timeline
    events = parse_timeline_events(text)
    event_ids = {e["event_id"] for e in events}
    for section in bundle.sections:
        for eid in section.source_event_ids:
            if eid not in event_ids:
                raise ValueError(f"source event not found: {eid}")

    # Validate all section hashes match current document
    for section in bundle.sections:
        current_hash = section_hash(text, section.section_id)
        if current_hash != section.base_section_hash:
            raise ValueError(
                f"stale hash for {section.section_id}: "
                f"expected {section.base_section_hash}, got {current_hash}"
            )


def apply_bundle(
    project_path: Path,
    proposal_store_dir: Path,
    proposal_id: str,
    proposal_version: int,
) -> dict:
    """Apply a validated synthesis bundle atomically. All-or-nothing.

    Pre-validates all source events and section hashes. Renders all section
    replacements in memory. Writes via atomic os.replace. On any failure,
    marks the proposal as 'error' and leaves the project document unchanged.
    """
    proposal = get_proposal(proposal_store_dir, proposal_id)
    if proposal is None:
        return {"ok": False, "kind": "not_found", "error": f"proposal {proposal_id} not found"}

    try:
        validate_bundle(project_path, proposal.bundle)
    except ValueError as exc:
        update_proposal_state(
            proposal_store_dir, proposal_id, "error", proposal_version,
            error_message=str(exc),
        )
        raise

    text = project_path.read_text(encoding="utf-8")

    # Apply all section replacements in memory
    for section in proposal.bundle.sections:
        text = replace_section_content(text, section.section_id, section.content)

    # Bump updated date in frontmatter
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    text = update_frontmatter(text, {"updated": date_str})

    # Atomic write
    write_project_atomically(project_path, text)

    # Mark applied
    update_proposal_state(proposal_store_dir, proposal_id, "applied", proposal_version)

    return {"ok": True, "proposal_id": proposal_id}


def generate_synthesis(
    project_path: Path,
    kind: str,
    trigger_event_ids: tuple[str, ...],
    opencode_bin: str = "opencode",
    model: str = "",
) -> SynthesisBundle:
    """Run the workevent-synthesizer agent and build a SynthesisBundle.

    The agent receives the project document as context and a prompt describing
    which kind of synthesis to produce (current-panorama, technical-overview, etc.)
    and which events triggered it.
    """
    from workeventagent.opencode_runner import run_synthesizer, parse_synthesizer_output

    text = project_path.read_text(encoding="utf-8")
    events = parse_timeline_events(text)
    trigger_events = [e for e in events if e["event_id"] in trigger_event_ids]

    prompt_lines = [
        f"kind={kind}",
        f"project_id={project_path.stem}",
    ]
    if trigger_events:
        prompt_lines.append("trigger_events=")
        for e in trigger_events:
            prompt_lines.append(f"- {e['event_id']}: {e['summary']}")

    prompt_lines.append("")
    prompt_lines.append("Return JSON with kind and sections array. Each section has "
                        "section_id, content, reason, and optional source_event_ids.")
    prompt_lines.append("Full project document is attached as context.")

    raw = run_synthesizer(
        prompt="\n".join(prompt_lines),
        project_doc=project_path,
        opencode_bin=opencode_bin,
        model=model,
    )
    data = parse_synthesizer_output(raw)

    sections = tuple(
        SectionProposal(
            section_id=s["section_id"],
            content=s["content"],
            base_section_hash=section_hash(text, s["section_id"]),
            source_event_ids=tuple(s.get("source_event_ids", [])),
            reason=s["reason"],
        )
        for s in data["sections"]
    )

    return SynthesisBundle(
        project_id=project_path.stem,
        kind=data["kind"],
        sections=sections,
        trigger_event_ids=trigger_event_ids,
    )


def classify_event_impact(
    project_path: Path,
    event_id: str,
    opencode_bin: str = "opencode",
    model: str = "",
) -> str:
    """Classify an event's impact level: 'high' or 'normal'.

    High-impact events should trigger automatic synthesis generation.
    Returns 'normal' on any failure — never blocks the archive flow.
    """
    from workeventagent.opencode_runner import _run_opencode_agent, _extract_json_text
    import json as _json

    text = project_path.read_text(encoding="utf-8")
    events = parse_timeline_events(text)
    target = next((e for e in events if e["event_id"] == event_id), None)
    if target is None:
        return "normal"

    prompt = (
        f"Classify this event's impact on the project.\n"
        f"event_id={target['event_id']}\n"
        f"summary={target['summary']}\n"
        f"Return JSON: {{\"impact\": \"high\"|\"normal\", \"reason\": \"short explanation\"}}\n"
    )

    try:
        raw = _run_opencode_agent(
            prompt=prompt,
            input_doc=project_path,
            agent_name="workevent-classifier",
            opencode_bin=opencode_bin,
            model=model,
        )
        result = _json.loads(_extract_json_text(raw))
        impact = result.get("impact", "normal")
        return impact if impact in ("high", "normal") else "normal"
    except Exception:
        return "normal"
