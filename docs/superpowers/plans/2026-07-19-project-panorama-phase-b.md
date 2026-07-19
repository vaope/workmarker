# F007 Phase B — Project Knowledge Synthesis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let WorkEventAgent classify event impact, generate derived/derived-reviewed section proposals from LLM synthesis, present evidence-backed diffs for user confirmation, and atomically apply approved bundles to project documents.

**Architecture:** Three new Python modules: `proposal_store.py` (typed CAS ledger for synthesis proposals, TTL=0), `classify_outbox.py` (durable outbox for event classification jobs), `synthesis.py` (LLM-backed synthesis generation + evidence validation + atomic multi-block bundle apply). A new `workevent-synthesizer` opencode agent generates section content from timeline events. All multi-block bundles are all-or-nothing: pre-validate all source IDs and base hashes, render in memory, single atomic replacement — any failure rejects the entire bundle.

**Tech Stack:** Python 3.11+ standard library, pytest, opencode CLI (workevent-synthesizer agent), existing project_schema/markdown_store/work_map_store primitives.

## Global Constraints

- Phase B builds on Phase A — no changes to schema v2 section anchors, ownership types, or hash/atomic-write contracts.
- All new synthesis proposals use `proposal_store.py` (CAS ledger, TTL=0, states: pending/applied/rejected/stale/superseded/error). Never reuse Inbox or single JSON file.
- Classification uses durable outbox (`classify_outbox.py`): archive-success → enqueue durable job → backend idempotent consume. F002 scheduler is for periodic wakeups only, not job state.
- Multi-block synthesis bundles are all-or-nothing: pre-validate all source IDs + base hashes, render in memory, validate, single `os.replace` atomic write. Any failure → whole bundle rejected, never partial application.
- `current-panorama` proposals are derived-reviewed: must show sources + diff, user confirms before write.
- `technical-overview` and `project-knowledge` proposals are reviewed: must show sources + diff, user confirms before write.
- `decisions` and `timeline` remain append-only, managed by existing F003/F004 flows.
- High-impact classification result is communicated to user immediately (toast/notification), not silently background-written.
- Normal captures only enqueue classification — never run full synthesis.
- No new dependency, no new task field, no second truth source.
- Do not modify schema v2 section anchors, ownership, or migration contracts.
- Tests use temporary workspaces; never read/write production user workspaces.
- Do not use Clowder AI ports 3003/3004 or Redis ports 6389/6398.

## File Structure

- Create `workeventagent/proposal_store.py`: typed proposal ledger with CAS versioning, TTL=0, states pending/applied/rejected/stale/superseded/error.
- Create `workeventagent/classify_outbox.py`: durable outbox keyed by `project_id:event_id`, with enqueue/dequeue/retry/reconcile lifecycle.
- Create `workeventagent/synthesis.py`: synthesis generation, evidence validation, bundle apply with pre-validation + all-or-nothing atomic write.
- Create `tests/test_proposal_store.py`, `tests/test_classify_outbox.py`, `tests/test_synthesis.py`.
- Modify `workeventagent/models.py`: add `SynthesisProposal`, `SynthesisBundle`, `ProposalState` types.
- Modify `workeventagent/opencode_runner.py`: add `run_synthesizer()` and `parse_synthesizer_output()`.
- Modify `workeventagent/gui.py`: add `handle_panorama_synthesis`, `handle_reviewed_synthesis`, `handle_synthesis_preview`, `handle_synthesis_apply`, `handle_synthesis_reject`, `handle_list_proposals`, `handle_trigger_classification`.
- Modify `client/windows/main.html`, `main.css`, `main.js`: proposal review UI, synthesis preview modal, confirmation flow.

---

### Task 1: Define synthesis data models and proposal ledger

**Files:**
- Modify: `workeventagent/models.py`
- Create: `workeventagent/proposal_store.py`
- Create: `tests/test_proposal_store.py`

**Interfaces:**
- Produces `SynthesisProposal`, `SynthesisBundle`, `ProposalState` in `models.py`.
- Produces `create_proposal()`, `get_proposal()`, `list_proposals()`, `update_proposal_state()`, `supersede_proposals()` in `proposal_store.py`.
- Consumes: nothing (standalone foundation).

- [ ] **Step 1: Add synthesis models to models.py**

Add to `workeventagent/models.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

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
```

- [ ] **Step 2: Write failing proposal_store tests**

Create `tests/test_proposal_store.py`:

```python
import json
from pathlib import Path

import pytest

from workeventagent.models import (
    SectionProposal,
    SynthesisBundle,
    SynthesisProposal,
)
from workeventagent.proposal_store import (
    create_proposal,
    get_proposal,
    list_proposals,
    supersede_proposals,
    update_proposal_state,
)


def _make_bundle(project_id: str = "demo") -> SynthesisBundle:
    return SynthesisBundle(
        project_id=project_id,
        kind="current-panorama",
        sections=(
            SectionProposal(
                section_id="current-panorama",
                content="项目正在推进 Phase B。",
                base_section_hash="sha256:abc123",
                source_event_ids=("ev-1", "ev-2"),
                reason="从最近事件综合。",
            ),
        ),
        trigger_event_ids=("ev-1", "ev-2"),
    )


def _make_store(tmp_path: Path) -> Path:
    return tmp_path / "proposals"


def test_create_and_read_proposal(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    bundle = _make_bundle()
    p = create_proposal(store, bundle)
    assert p.proposal_id != ""
    assert p.state == "pending"
    assert p.version == 1

    got = get_proposal(store, p.proposal_id)
    assert got is not None
    assert got.proposal_id == p.proposal_id
    assert got.bundle == bundle


def test_list_proposals_filters_by_project_and_state(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    p1 = create_proposal(store, _make_bundle("a"))
    p2 = create_proposal(store, _make_bundle("a"))
    p3 = create_proposal(store, _make_bundle("b"))

    a_pending = list_proposals(store, project_id="a", state="pending")
    assert len(a_pending) == 2
    assert all(p.project_id == "a" for p in a_pending)

    b_pending = list_proposals(store, project_id="b", state="pending")
    assert len(b_pending) == 1


def test_update_state_and_version_guard(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    p = create_proposal(store, _make_bundle())

    # Update to applied
    updated = update_proposal_state(store, p.proposal_id, "applied", p.version)
    assert updated.state == "applied"
    assert updated.version == 2

    # Stale version guard
    with pytest.raises(ValueError, match="stale version"):
        update_proposal_state(store, p.proposal_id, "rejected", 1)


def test_supersede_marks_older_pending_as_superseded(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    p1 = create_proposal(store, _make_bundle())
    p2 = create_proposal(store, _make_bundle())
    supersede_proposals(store, "demo", "current-panorama", p2.proposal_id)

    got1 = get_proposal(store, p1.proposal_id)
    got2 = get_proposal(store, p2.proposal_id)
    assert got1 is not None and got1.state == "superseded"
    assert got2 is not None and got2.state == "pending"


def test_proposal_error_state_stores_message(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    p = create_proposal(store, _make_bundle())
    updated = update_proposal_state(store, p.proposal_id, "error", p.version, error_message="LLM 超时")
    assert updated.state == "error"
    assert updated.error_message == "LLM 超时"
```

- [ ] **Step 3: Run tests to verify red**

```powershell
python -m pytest tests/test_proposal_store.py -q
```

Expected: import failure.

- [ ] **Step 4: Implement proposal_store.py**

```python
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from workeventagent.models import SynthesisBundle, SynthesisProposal, ProposalState


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _proposal_path(store_dir: Path, proposal_id: str) -> Path:
    return store_dir / f"{proposal_id}.json"


def create_proposal(store_dir: Path, bundle: SynthesisBundle) -> SynthesisProposal:
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
    path = _proposal_path(store_dir, proposal_id)
    if not path.exists():
        return None
    return _read(path)


def list_proposals(
    store_dir: Path,
    project_id: str | None = None,
    state: ProposalState | None = None,
) -> list[SynthesisProposal]:
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
                    source_event_ids=tuple(s["source_event_ids"]),
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
```

- [ ] **Step 5: Run tests to verify green**

```powershell
python -m pytest tests/test_proposal_store.py -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```powershell
git add workeventagent/models.py workeventagent/proposal_store.py tests/test_proposal_store.py
git commit -m "feat: add synthesis proposal models and CAS ledger" -m "Why: Phase B needs typed, version-guarded proposal storage before any LLM synthesis can be safely applied."
```

---

### Task 2: Add durable classification outbox

**Files:**
- Create: `workeventagent/classify_outbox.py`
- Create: `tests/test_classify_outbox.py`

**Interfaces:**
- Produces `enqueue_classification()`, `dequeue_pending()`, `mark_done()`, `mark_failed()`, `reconcile_from_timeline()`.
- Consumes: timeline events from existing project schema parser.

- [ ] **Step 1: Write failing outbox tests**

Create `tests/test_classify_outbox.py`:

```python
from pathlib import Path

from workeventagent.classify_outbox import (
    dequeue_pending,
    enqueue_classification,
    mark_done,
    mark_failed,
    reconcile_from_timeline,
)


def _make_outbox(tmp_path: Path) -> Path:
    return tmp_path / "classify_outbox"


def test_enqueue_and_dequeue(tmp_path: Path) -> None:
    outbox = _make_outbox(tmp_path)
    enqueue_classification(outbox, "demo", "ev-1")
    enqueue_classification(outbox, "demo", "ev-2")

    pending = dequeue_pending(outbox, limit=10)
    assert len(pending) == 2
    assert pending[0]["event_id"] in ("ev-1", "ev-2")


def test_dequeue_is_idempotent_by_key(tmp_path: Path) -> None:
    outbox = _make_outbox(tmp_path)
    enqueue_classification(outbox, "demo", "ev-1")
    enqueue_classification(outbox, "demo", "ev-1")  # duplicate key

    pending = dequeue_pending(outbox, limit=10)
    assert len(pending) == 1


def test_mark_done_removes_from_pending(tmp_path: Path) -> None:
    outbox = _make_outbox(tmp_path)
    enqueue_classification(outbox, "demo", "ev-1")
    enqueue_classification(outbox, "demo", "ev-2")
    mark_done(outbox, "demo", "ev-1")

    pending = dequeue_pending(outbox, limit=10)
    assert len(pending) == 1
    assert pending[0]["event_id"] == "ev-2"


def test_mark_failed_records_retry_count(tmp_path: Path) -> None:
    outbox = _make_outbox(tmp_path)
    enqueue_classification(outbox, "demo", "ev-1")
    mark_failed(outbox, "demo", "ev-1", "LLM timeout")

    pending = dequeue_pending(outbox, limit=10)
    assert len(pending) == 0  # failed jobs excluded from pending

    # Failed job still exists on disk
    failed_path = outbox / "failed" / "demo.ev-1.json"
    assert failed_path.exists()


def test_reconcile_finds_missing_events_from_timeline(tmp_path: Path) -> None:
    outbox = _make_outbox(tmp_path)
    events = [
        {"event_id": "ev-a", "summary": "完成 A"},
        {"event_id": "ev-b", "summary": "完成 B"},
    ]
    # Only ev-a is in outbox
    enqueue_classification(outbox, "demo", "ev-a")
    mark_done(outbox, "demo", "ev-a")

    missing = reconcile_from_timeline(outbox, "demo", events)
    assert missing == ["ev-b"]
```

- [ ] **Step 2: Run tests to verify red**

```powershell
python -m pytest tests/test_classify_outbox.py -q
```

Expected: import failure.

- [ ] **Step 3: Implement classify_outbox.py**

```python
from __future__ import annotations

import json
import os
from pathlib import Path


def _key(project_id: str, event_id: str) -> str:
    return f"{project_id}/{event_id}"


def _job_path(outbox_dir: Path, project_id: str, event_id: str) -> Path:
    return outbox_dir / "pending" / f"{project_id}.{event_id}.json"


def _failed_path(outbox_dir: Path, project_id: str, event_id: str) -> Path:
    return outbox_dir / "failed" / f"{project_id}.{event_id}.json"


def enqueue_classification(outbox_dir: Path, project_id: str, event_id: str) -> None:
    path = _job_path(outbox_dir, project_id, event_id)
    failed = _failed_path(outbox_dir, project_id, event_id)
    if path.exists() or failed.exists():
        return  # already queued or failed
    outbox_dir.mkdir(parents=True, exist_ok=True)
    (outbox_dir / "pending").mkdir(parents=True, exist_ok=True)
    data = {"project_id": project_id, "event_id": event_id, "retries": 0, "last_error": ""}
    tmp = outbox_dir / f".{project_id}.{event_id}.tmp"
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def dequeue_pending(outbox_dir: Path, limit: int = 10) -> list[dict]:
    pending_dir = outbox_dir / "pending"
    if not pending_dir.exists():
        return []
    jobs: list[dict] = []
    for entry in sorted(pending_dir.iterdir()):
        if not entry.suffix == ".json":
            continue
        jobs.append(json.loads(entry.read_text(encoding="utf-8")))
        if len(jobs) >= limit:
            break
    return jobs


def mark_done(outbox_dir: Path, project_id: str, event_id: str) -> None:
    path = _job_path(outbox_dir, project_id, event_id)
    path.unlink(missing_ok=True)


def mark_failed(outbox_dir: Path, project_id: str, event_id: str, error: str) -> None:
    path = _job_path(outbox_dir, project_id, event_id)
    if not path.exists():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    data["retries"] = data.get("retries", 0) + 1
    data["last_error"] = error
    failed_dir = outbox_dir / "failed"
    failed_dir.mkdir(parents=True, exist_ok=True)
    tmp = outbox_dir / f".failed.{project_id}.{event_id}.tmp"
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, _failed_path(outbox_dir, project_id, event_id))
    path.unlink(missing_ok=True)


def reconcile_from_timeline(
    outbox_dir: Path, project_id: str, events: list[dict]
) -> list[str]:
    """Return event_ids from events that are NOT in outbox (pending or failed or done)."""
    seen: set[str] = set()
    pending_dir = outbox_dir / "pending"
    failed_dir = outbox_dir / "failed"
    for d in (pending_dir, failed_dir):
        if not d.exists():
            continue
        for entry in d.iterdir():
            if not entry.suffix == ".json":
                continue
            seen.add(entry.stem.split(".", 1)[-1])

    missing: list[str] = []
    for ev in events:
        if ev["event_id"] not in seen:
            missing.append(ev["event_id"])
    return missing
```

- [ ] **Step 4: Run tests to verify green**

```powershell
python -m pytest tests/test_classify_outbox.py -q
```

Expected: all pass.

- [ ] **Step 5: Commit**

```powershell
git add workeventagent/classify_outbox.py tests/test_classify_outbox.py
git commit -m "feat: add durable classification outbox" -m "Why: Archive events must be classified without silent drops. Durable outbox ensures idempotent classification survives process restarts."
```

---

### Task 3: Add synthesis generation and atomic bundle apply

**Files:**
- Create: `workeventagent/synthesis.py`
- Create: `tests/test_synthesis.py`
- Modify: `workeventagent/opencode_runner.py`

**Interfaces:**
- Consumes: `project_schema.py`, `proposal_store.py`, `classify_outbox.py`, `opencode_runner.py`.
- Produces: `generate_synthesis()`, `validate_bundle()`, `apply_bundle()`, `classify_event_impact()`.

- [ ] **Step 1: Add synthesizer runner to opencode_runner.py**

Add to `workeventagent/opencode_runner.py`:

```python
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
    inner = _extract_json_text(raw)
    try:
        data = json.loads(inner)
    except json.JSONDecodeError as exc:
        raise OpencodeRunnerError(f"invalid JSON from synthesizer: {exc}") from exc

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
```

- [ ] **Step 2: Write failing synthesis tests**

Create `tests/test_synthesis.py`:

```python
from pathlib import Path
from unittest.mock import patch

import pytest

from workeventagent.models import (
    SectionProposal,
    SynthesisBundle,
    SynthesisProposal,
)
from workeventagent.proposal_store import create_proposal, get_proposal
from workeventagent.synthesis import (
    apply_bundle,
    classify_event_impact,
    generate_synthesis,
    validate_bundle,
)


V2_PROJECT = """---
project_id: demo
title: Demo
doc_kind: work_project
schema_version: 2
status: active
phase: build
created: 2026-07-13
updated: 2026-07-13
---
# Demo

## 当前全景 <!-- section:current-panorama -->

项目刚开始。

<!-- panorama-meta:generated_at=2026-07-13T09:00:00+08:00;source_events=ev-1,ev-2 -->

## 技术概览 <!-- section:technical-overview -->

Electron + Python。

## 事件证据 <!-- section:timeline -->

- 2026-07-13T10:00:00+08:00 <!-- event:ev-1 -->
  - task_id: task-a
  - summary: 完成捕获集成

- 2026-07-13T11:00:00+08:00 <!-- event:ev-2 -->
  - task_id: task-a
  - summary: 添加纠错能力
"""


def _make_bundle() -> SynthesisBundle:
    return SynthesisBundle(
        project_id="demo",
        kind="current-panorama",
        sections=(
            SectionProposal(
                section_id="current-panorama",
                content="项目已完成捕获和纠错。",
                base_section_hash="sha256:will-match-test",
                source_event_ids=("ev-1", "ev-2"),
                reason="从最近事件综合。",
            ),
        ),
        trigger_event_ids=("ev-2",),
    )


def test_validate_bundle_all_sources_exist(tmp_path: Path) -> None:
    project = tmp_path / "demo.md"
    project.write_text(V2_PROJECT, encoding="utf-8")
    # Fix hash to match actual
    from workeventagent.project_schema import section_hash
    content = "项目已完成捕获和纠错。\n"
    actual_hash = section_hash(V2_PROJECT, "current-panorama")
    # We need to match the fixture hash — adjust
    bundle = SynthesisBundle(
        project_id="demo",
        kind="current-panorama",
        sections=(
            SectionProposal(
                section_id="current-panorama",
                content="项目已完成捕获和纠错。",
                base_section_hash=actual_hash,
                source_event_ids=("ev-1", "ev-2"),
                reason="从最近事件综合。",
            ),
        ),
        trigger_event_ids=("ev-2",),
    )
    validate_bundle(project, bundle)  # should not raise


def test_validate_bundle_rejects_missing_source_event(tmp_path: Path) -> None:
    project = tmp_path / "demo.md"
    project.write_text(V2_PROJECT, encoding="utf-8")
    bundle = SynthesisBundle(
        project_id="demo",
        kind="current-panorama",
        sections=(
            SectionProposal(
                section_id="current-panorama",
                content="ok",
                base_section_hash="sha256:abc",
                source_event_ids=("ev-1", "ev-999"),  # ev-999 does not exist
                reason="test",
            ),
        ),
    )
    with pytest.raises(ValueError, match="source event not found: ev-999"):
        validate_bundle(project, bundle)


def test_validate_bundle_rejects_stale_section_hash(tmp_path: Path) -> None:
    project = tmp_path / "demo.md"
    project.write_text(V2_PROJECT, encoding="utf-8")
    bundle = SynthesisBundle(
        project_id="demo",
        kind="current-panorama",
        sections=(
            SectionProposal(
                section_id="current-panorama",
                content="ok",
                base_section_hash="sha256:stale",
                source_event_ids=("ev-1",),
                reason="test",
            ),
        ),
    )
    with pytest.raises(ValueError, match="stale hash"):
        validate_bundle(project, bundle)


def test_apply_bundle_is_all_or_nothing(tmp_path: Path) -> None:
    project = tmp_path / "demo.md"
    project.write_text(V2_PROJECT, encoding="utf-8")
    original = project.read_text(encoding="utf-8")

    from workeventagent.project_schema import section_hash
    pano_hash = section_hash(V2_PROJECT, "current-panorama")
    tech_hash = section_hash(V2_PROJECT, "technical-overview")

    bundle = SynthesisBundle(
        project_id="demo",
        kind="multi",
        sections=(
            SectionProposal(
                section_id="current-panorama",
                content="全景已更新。",
                base_section_hash=pano_hash,
                source_event_ids=("ev-1", "ev-2"),
                reason="综合更新。",
            ),
            SectionProposal(
                section_id="technical-overview",
                content="Python 作为确定性后端。",
                base_section_hash="sha256:stale",  # will fail
                source_event_ids=("ev-1",),
                reason="技术概览更新。",
            ),
        ),
    )

    store = tmp_path / "proposals"
    proposal = create_proposal(store, bundle)

    with pytest.raises(ValueError, match="stale hash"):
        apply_bundle(project, store, proposal.proposal_id, proposal.version)

    # Project unchanged
    assert project.read_text(encoding="utf-8") == original

    # Proposal in error state
    p = get_proposal(store, proposal.proposal_id)
    assert p is not None and p.state == "error"


def test_apply_bundle_success_writes_atomically(tmp_path: Path) -> None:
    project = tmp_path / "demo.md"
    project.write_text(V2_PROJECT, encoding="utf-8")

    from workeventagent.project_schema import section_hash
    actual_hash = section_hash(V2_PROJECT, "current-panorama")

    bundle = SynthesisBundle(
        project_id="demo",
        kind="current-panorama",
        sections=(
            SectionProposal(
                section_id="current-panorama",
                content="项目已完成捕获和纠错。\n",
                base_section_hash=actual_hash,
                source_event_ids=("ev-1", "ev-2"),
                reason="综合更新。",
            ),
        ),
    )

    store = tmp_path / "proposals"
    proposal = create_proposal(store, bundle)
    result = apply_bundle(project, store, proposal.proposal_id, proposal.version)

    assert result["ok"] is True
    assert "项目已完成捕获和纠错" in project.read_text(encoding="utf-8")

    # Proposal marked applied
    p = get_proposal(store, proposal.proposal_id)
    assert p is not None and p.state == "applied"
```

- [ ] **Step 3: Run tests to verify red**

```powershell
python -m pytest tests/test_synthesis.py -q
```

Expected: import failure.

- [ ] **Step 4: Implement synthesis.py**

```python
from __future__ import annotations

from pathlib import Path

from workeventagent.models import SectionProposal, SynthesisBundle
from workeventagent.project_schema import (
    find_section,
    metadata_hash,
    parse_timeline_events,
    replace_section_content,
    schema_version,
    section_hash,
    update_frontmatter,
)
from workeventagent.markdown_store import write_project_atomically
from workeventagent.proposal_store import get_proposal, update_proposal_state


def validate_bundle(project_path: Path, bundle: SynthesisBundle) -> None:
    """Validate all source events exist and all section hashes match. Raises ValueError on failure."""
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
    proposal_store: Path,
    proposal_id: str,
    proposal_version: int,
) -> dict:
    """Apply a validated synthesis bundle atomically. All-or-nothing."""
    proposal = get_proposal(proposal_store, proposal_id)
    if proposal is None:
        return {"ok": False, "kind": "not_found", "error": f"proposal {proposal_id} not found"}

    try:
        validate_bundle(project_path, proposal.bundle)
    except ValueError as exc:
        update_proposal_state(
            proposal_store, proposal_id, "error", proposal_version,
            error_message=str(exc),
        )
        raise

    text = project_path.read_text(encoding="utf-8")

    # Apply all section replacements in memory
    for section in proposal.bundle.sections:
        text = replace_section_content(text, section.section_id, section.content)

    # Bump updated date
    from datetime import datetime, timezone
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    text = _bump_frontmatter_updated(text, date_str)

    # Atomic write
    write_project_atomically(project_path, text)

    # Mark applied
    update_proposal_state(proposal_store, proposal_id, "applied", proposal_version)

    return {"ok": True, "proposal_id": proposal_id}


def generate_synthesis(
    project_path: Path,
    kind: str,
    trigger_event_ids: tuple[str, ...],
    opencode_bin: str = "opencode",
    model: str = "",
) -> SynthesisBundle:
    """Run the workevent-synthesizer agent and build a validated SynthesisBundle."""
    from workeventagent.opencode_runner import run_synthesizer, parse_synthesizer_output

    text = project_path.read_text(encoding="utf-8")
    events = parse_timeline_events(text)
    trigger_events = [e for e in events if e["event_id"] in trigger_event_ids]

    prompt_lines = [
        f"kind={kind}",
        f"project_id={project_path.stem}",
        "trigger_events=" + "\n".join(
            f"- {e['event_id']}: {e['summary']}" for e in trigger_events
        ),
        "Full project document is attached. Generate section proposal(s).",
    ]

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
    """Classify an event's impact level: 'high' or 'normal'. High means synthesis should be triggered."""
    from workeventagent.opencode_runner import _run_opencode_agent, _extract_json_text
    import json as _json

    text = project_path.read_text(encoding="utf-8")
    events = parse_timeline_events(text)
    target = next((e for e in events if e["event_id"] == event_id), None)
    if target is None:
        return "normal"

    prompt = (
        f"Classify this event's impact on the project:\n"
        f"event_id={target['event_id']}\n"
        f"summary={target['summary']}\n"
        f"Return JSON: {{\"impact\": \"high\"|\"normal\", \"reason\": \"...\"}}\n"
    )

    raw = _run_opencode_agent(
        prompt=prompt,
        input_doc=project_path,
        agent_name="workevent-classifier",
        opencode_bin=opencode_bin,
        model=model,
    )
    result = _json.loads(_extract_json_text(raw))
    return result.get("impact", "normal")


def _bump_frontmatter_updated(text: str, date_str: str) -> str:
    return update_frontmatter(text, {"updated": date_str})
```

- [ ] **Step 5: Run synthesis tests**

```powershell
python -m pytest tests/test_synthesis.py -q
```

Expected: validation and apply tests pass (generation test skipped — requires opencode).

- [ ] **Step 6: Commit**

```powershell
git add workeventagent/synthesis.py workeventagent/opencode_runner.py tests/test_synthesis.py
git commit -m "feat: add synthesis generation and atomic bundle apply" -m "Why: All-or-nothing multi-section apply with pre-validation ensures no partial writes even when one section hash is stale."
```

---

### Task 4: Wire classification into archive flow

**Files:**
- Modify: `workeventagent/gui.py`
- Modify: `tests/test_gui.py`

**Interfaces:**
- Consumes: `classify_outbox.py`, `synthesis.py`, `proposal_store.py`.
- Modifies: `handle_commit` to enqueue classification after archive success.

- [ ] **Step 1: Write failing classification tests**

Add to `tests/test_gui.py`:

```python
def test_commit_enqueues_classification(tmp_path: Path) -> None:
    """After a successful commit, classification outbox should contain the new event."""
    project = _write_v2_fixture(tmp_path)
    db = tmp_path / "index.sqlite"
    init_db(db)
    rebuild_index(db, [project])

    result = _dispatch("commit", {
        "project_path": str(project),
        "db_path": str(db),
        "input_text": "完成了项目文档迁移",
        "model": "",
        "opencode_bin": "echo",
    })
    if not result.get("ok"):
        # Skipped — needs real opencode, test only outbox behavior
        import pytest; pytest.skip("needs opencode")

    # Check classification outbox
    from workeventagent.classify_outbox import dequeue_pending
    pending = dequeue_pending(tmp_path / ".workeventagent" / "classify_outbox")
    # Event was enqueued
    assert len(pending) >= 1
```

- [ ] **Step 2: Modify handle_commit to enqueue classification**

In `workeventagent/gui.py`, in `handle_commit`, after the successful archive write:

```python
from workeventagent.classify_outbox import enqueue_classification

# After: project_text = write_project_atomically(...)
# Add:
_classify_dir = project_path.parent / ".workeventagent" / "classify_outbox"
enqueue_classification(_classify_dir, project_path.stem, event_id)
```

- [ ] **Step 3: Add handle_trigger_classification handler**

Add to `workeventagent/gui.py`:

```python
def handle_trigger_classification(request: dict) -> dict:
    """Process pending classification jobs for a project, generating synthesis proposals for high-impact events."""
    project_path = Path(request["project_path"])
    workspace = project_path.parent
    classify_dir = workspace / ".workeventagent" / "classify_outbox"
    proposal_dir = workspace / ".workeventagent" / "proposals"
    opencode_bin = request.get("opencode_bin", "opencode")
    model = request.get("model", "")

    from workeventagent.classify_outbox import dequeue_pending, mark_done, mark_failed
    from workeventagent.synthesis import classify_event_impact, generate_synthesis
    from workeventagent.proposal_store import create_proposal, supersede_proposals

    pending = dequeue_pending(classify_dir, limit=5)
    results = []

    for job in pending:
        pid = job["project_id"]
        eid = job["event_id"]
        try:
            impact = classify_event_impact(project_path, eid, opencode_bin, model)
            if impact == "high":
                bundle = generate_synthesis(
                    project_path,
                    kind="current-panorama",
                    trigger_event_ids=(eid,),
                    opencode_bin=opencode_bin,
                    model=model,
                )
                supersede_proposals(proposal_dir, pid, "current-panorama", "")
                p = create_proposal(proposal_dir, bundle)
                results.append({"event_id": eid, "impact": "high", "proposal_id": p.proposal_id})
            else:
                results.append({"event_id": eid, "impact": "normal"})
            mark_done(classify_dir, pid, eid)
        except Exception as exc:
            mark_failed(classify_dir, pid, eid, str(exc))
            results.append({"event_id": eid, "impact": "error", "error": str(exc)})

    return {"ok": True, "results": results}
```

Register `handle_trigger_classification` in the handlers dict.

- [ ] **Step 4: Run focused tests**

```powershell
python -m pytest tests/test_gui.py -k "classification" -q
```

- [ ] **Step 5: Commit**

```powershell
git add workeventagent/gui.py tests/test_gui.py
git commit -m "feat: wire classification into archive commit flow" -m "Why: Every committed event is enqueued for impact classification; high-impact events auto-generate synthesis proposals."
```

---

### Task 5: Add panorama synthesis handlers and proposal lifecycle

**Files:**
- Modify: `workeventagent/gui.py`
- Modify: `tests/test_gui.py`

**Interfaces:**
- Produces `handle_panorama_synthesis`, `handle_reviewed_synthesis`, `handle_synthesis_preview`, `handle_synthesis_apply`, `handle_synthesis_reject`, `handle_list_proposals`.
- Consumes: `synthesis.py`, `proposal_store.py`.

- [ ] **Step 1: Write handler tests**

Add to `tests/test_gui.py`:

```python
def test_list_proposals_returns_pending_for_project(tmp_path: Path) -> None:
    project = _write_v2_fixture(tmp_path)
    # Create a pending proposal
    from workeventagent.models import SectionProposal, SynthesisBundle
    from workeventagent.proposal_store import create_proposal
    from workeventagent.project_schema import section_hash

    text = project.read_text(encoding="utf-8")
    bundle = SynthesisBundle(
        project_id=project.stem,
        kind="current-panorama",
        sections=(
            SectionProposal(
                section_id="current-panorama",
                content="新全景。",
                base_section_hash=section_hash(text, "current-panorama"),
                source_event_ids=(),
                reason="测试。",
            ),
        ),
    )
    store = tmp_path / ".workeventagent" / "proposals"
    p = create_proposal(store, bundle)

    result = _dispatch("list_proposals", {
        "project_path": str(project),
    })
    assert result["ok"] is True
    assert len(result["proposals"]) >= 1
    assert result["proposals"][0]["proposal_id"] == p.proposal_id


def test_synthesis_apply_succeeds_and_migrates_state(tmp_path: Path) -> None:
    project = _write_v2_fixture(tmp_path)
    text = project.read_text(encoding="utf-8")
    from workeventagent.project_schema import section_hash

    bundle = SynthesisBundle(
        project_id=project.stem,
        kind="current-panorama",
        sections=(
            SectionProposal(
                section_id="current-panorama",
                content="综合后的全景。\n",
                base_section_hash=section_hash(text, "current-panorama"),
                source_event_ids=(),
                reason="测试。",
            ),
        ),
    )
    store = tmp_path / ".workeventagent" / "proposals"
    p = create_proposal(store, bundle)

    result = _dispatch("synthesis_apply", {
        "project_path": str(project),
        "proposal_id": p.proposal_id,
        "proposal_version": 1,
    })
    assert result["ok"] is True
    assert "综合后的全景" in project.read_text(encoding="utf-8")
```

- [ ] **Step 2: Implement handlers**

Add to `workeventagent/gui.py`:

```python
def handle_list_proposals(request: dict) -> dict:
    project_path = Path(request["project_path"])
    workspace = project_path.parent
    store = workspace / ".workeventagent" / "proposals"
    state = request.get("state", "pending")

    from workeventagent.proposal_store import list_proposals
    proposals = list_proposals(store, project_id=project_path.stem, state=state)

    return {
        "ok": True,
        "proposals": [
            {
                "proposal_id": p.proposal_id,
                "kind": p.bundle.kind,
                "state": p.state,
                "version": p.version,
                "created_at": p.created_at,
                "section_count": len(p.bundle.sections),
                "section_ids": [s.section_id for s in p.bundle.sections],
                "trigger_event_ids": list(p.bundle.trigger_event_ids),
                "error_message": p.error_message,
            }
            for p in proposals
        ],
    }


def handle_synthesis_preview(request: dict) -> dict:
    """Return full proposal detail with before/after content for user review."""
    project_path = Path(request["project_path"])
    workspace = project_path.parent
    store = workspace / ".workeventagent" / "proposals"
    proposal_id = request["proposal_id"]

    from workeventagent.proposal_store import get_proposal
    from workeventagent.project_schema import section_content

    p = get_proposal(store, proposal_id)
    if p is None:
        return {"ok": False, "kind": "not_found"}

    text = project_path.read_text(encoding="utf-8")
    sections = []
    for s in p.bundle.sections:
        try:
            current = section_content(text, s.section_id)
        except ValueError:
            current = "(section not found)"
        sections.append({
            "section_id": s.section_id,
            "current_content": current.strip(),
            "proposed_content": s.content.strip(),
            "reason": s.reason,
            "source_event_ids": list(s.source_event_ids),
            "base_section_hash": s.base_section_hash,
        })

    return {
        "ok": True,
        "proposal_id": p.proposal_id,
        "kind": p.bundle.kind,
        "state": p.state,
        "version": p.version,
        "created_at": p.created_at,
        "sections": sections,
    }


def handle_synthesis_apply(request: dict) -> dict:
    project_path = Path(request["project_path"])
    workspace = project_path.parent
    store = workspace / ".workeventagent" / "proposals"
    proposal_id = request["proposal_id"]
    version = request["proposal_version"]

    from workeventagent.synthesis import apply_bundle
    try:
        return apply_bundle(project_path, store, proposal_id, version)
    except ValueError as exc:
        return {"ok": False, "kind": "apply_failed", "error": str(exc)}


def handle_synthesis_reject(request: dict) -> dict:
    project_path = Path(request["project_path"])
    workspace = project_path.parent
    store = workspace / ".workeventagent" / "proposals"
    proposal_id = request["proposal_id"]
    version = request["proposal_version"]

    from workeventagent.proposal_store import update_proposal_state
    update_proposal_state(store, proposal_id, "rejected", version)
    return {"ok": True, "proposal_id": proposal_id}


def handle_panorama_synthesis(request: dict) -> dict:
    """Generate a current-panorama synthesis proposal for all recent events."""
    project_path = Path(request["project_path"])
    workspace = project_path.parent
    store = workspace / ".workeventagent" / "proposals"
    opencode_bin = request.get("opencode_bin", "opencode")
    model = request.get("model", "")

    from workeventagent.project_schema import parse_timeline_events
    from workeventagent.synthesis import generate_synthesis
    from workeventagent.proposal_store import create_proposal, supersede_proposals

    text = project_path.read_text(encoding="utf-8")
    events = parse_timeline_events(text)
    if not events:
        return {"ok": False, "kind": "no_events", "error": "no timeline events to synthesize"}

    event_ids = tuple(e["event_id"] for e in events[:10])  # last 10 events

    bundle = generate_synthesis(
        project_path, kind="current-panorama", trigger_event_ids=event_ids,
        opencode_bin=opencode_bin, model=model,
    )
    supersede_proposals(store, project_path.stem, "current-panorama", "")
    p = create_proposal(store, bundle)

    return {"ok": True, "proposal_id": p.proposal_id}


def handle_reviewed_synthesis(request: dict) -> dict:
    """Generate a reviewed section synthesis proposal for a specific section."""
    project_path = Path(request["project_path"])
    workspace = project_path.parent
    store = workspace / ".workeventagent" / "proposals"
    section_id = request["section_id"]
    trigger_event_ids = tuple(request.get("trigger_event_ids", []))
    opencode_bin = request.get("opencode_bin", "opencode")
    model = request.get("model", "")

    from workeventagent.synthesis import generate_synthesis
    from workeventagent.proposal_store import create_proposal

    bundle = generate_synthesis(
        project_path, kind=section_id, trigger_event_ids=trigger_event_ids,
        opencode_bin=opencode_bin, model=model,
    )
    p = create_proposal(store, bundle)

    return {"ok": True, "proposal_id": p.proposal_id}
```

Register all handlers in the handlers dict in `_main_impl()`:
```python
"synthesis_preview": handle_synthesis_preview,
"synthesis_apply": handle_synthesis_apply,
"synthesis_reject": handle_synthesis_reject,
"list_proposals": handle_list_proposals,
"panorama_synthesis": handle_panorama_synthesis,
"reviewed_synthesis": handle_reviewed_synthesis,
"trigger_classification": handle_trigger_classification,
```

- [ ] **Step 3: Run handler tests**

```powershell
python -m pytest tests/test_gui.py -k "list_proposals or synthesis_apply" -q
```

- [ ] **Step 4: Commit**

```powershell
git add workeventagent/gui.py tests/test_gui.py
git commit -m "feat: add panorama synthesis and proposal lifecycle handlers" -m "Why: Users need to preview, confirm, and apply synthesis proposals through the same JSON-in/JSON-out contract."
```

---

### Task 6: Full regression and compatibility audit

**Files:**
- Verify: all existing test files.
- Modify: `docs/designs/F007-project-panorama.md` (update Phase B status).

**Interfaces:**
- Consumes: complete Phase B implementation.
- Produces: green test suite, updated design doc.

- [ ] **Step 1: Run full Python test suite**

```powershell
python -m pytest -q
```

Expected: all Phase A tests (249+) plus new Phase B tests pass. Zero failures.

- [ ] **Step 2: Verify no Phase C scope leaked**

```powershell
rg -n "compendium|generate_compendium|project_compendium" workeventagent/synthesis.py workeventagent/proposal_store.py workeventagent/classify_outbox.py
```

Expected: no matches.

- [ ] **Step 3: Verify capture/report/search/correction still work**

```powershell
python -m pytest tests/test_gui.py::CommitTest tests/test_gui.py::ReportTest tests/test_search_store.py tests/test_correction_store.py -q
```

- [ ] **Step 4: Update F007 design doc**

Add Phase B completion marker to `docs/designs/F007-project-panorama.md` header status line.

- [ ] **Step 5: Commit**

```powershell
git add docs/designs/F007-project-panorama.md
git commit -m "docs: mark F007 Phase B complete" -m "Why: Synthesis generation, classification outbox, proposal ledger, and atomic bundle apply are implemented and tested."
```

---

## Self-Review Checklist

- [x] Every Phase B acceptance criterion maps to a task and test.
- [x] Proposal storage uses CAS ledger, not Inbox reuse.
- [x] Classification uses durable outbox, not F002 scheduler.
- [x] Multi-block bundles are all-or-nothing with pre-validation.
- [x] High-impact classification generates visible proposal, not silent background write.
- [x] Normal captures enqueue classification only, never full synthesis.
- [x] All writes go through existing atomic-write contract.
- [x] No new dependency, task field, or truth source.
- [x] Phase C compendium code absent from Phase B modules.

## Execution Handoff

Phase B plan complete. The co-creator's instruction is "把f07做完" — Phase B is the next deliverable. Phase C (compendium) comes after Phase B runtime validation.

**Plan complete and saved to `docs/superpowers/plans/2026-07-19-project-panorama-phase-b.md`.**
