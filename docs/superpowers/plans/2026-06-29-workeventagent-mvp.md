---
feature_ids: [F001]
topics: [implementation-plan, workeventagent, opencode, markdown, sqlite]
doc_kind: plan
created: 2026-06-29
---

# WorkEventAgent MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the MVP progress archiving loop: capture a work update, get an opencode-generated archive proposal, confirm it in the terminal, write Markdown first, then update a rebuildable SQLite index.

**Architecture:** A thin Python standard-library wrapper owns CLI input, attachment paths, confirmation, Markdown writes, and SQLite indexing. opencode remains the only LLM/agent execution entry. `docs/WORKLOG_SCHEMA.md` is the write protocol; project Markdown files remain the source of truth.

**Tech Stack:** Python 3.11 standard library, `unittest`, `sqlite3`, `argparse`, `subprocess`, `json`, Markdown files, opencode CLI.

## Global Constraints

- Use opencode as the only LLM/agent execution entry.
- Python 3.11 standard-library wrapper is approved for MVP implementation.
- Local wrapper code is allowed, but no GUI or hotkey window in MVP.
- Do not automatically create projects.
- The agent may propose new items/tasks only with explicit terminal confirmation showing generated IDs and exact Markdown insertion blocks.
- Timeline is append-only.
- Work Map owns item/task structure and current state.
- Current Snapshot is derived and may be regenerated.
- Markdown writes happen before SQLite updates.
- SQLite is rebuildable from Markdown and never supersedes Markdown.
- Images/files are archived as paths only; no image understanding in MVP.
- MVP assumes a single writer per project document.
- Daily/weekly rollup auto-generation is not in MVP.
- Task 0 opencode spike must pass before implementing package modules.

---

## File Structure

- Create `pyproject.toml`: package metadata and Python version floor.
- Create `workeventagent/__init__.py`: package marker.
- Create `workeventagent/models.py`: dataclasses for IDs, archive proposals, timeline events, and confirmation decisions.
- Create `workeventagent/ids.py`: stable ID and event ID generation.
- Create `workeventagent/markdown_store.py`: project document parsing, anchored block replacement, timeline append, and item/task insertion.
- Create `workeventagent/index_store.py`: SQLite schema, rebuild, and update logic.
- Create `workeventagent/opencode_runner.py`: subprocess wrapper for `opencode run`.
- Create `workeventagent/confirm.py`: terminal confirmation card rendering and `confirm/edit/cancel` parsing.
- Create `workeventagent/cli.py`: command entry point for capture and dry-run.
- Create `.opencode/agent/workevent-archivist.md`: opencode agent instructions constrained by `WORKLOG_SCHEMA.md`.
- Create `spikes/f001-opencode-project.md`: minimal project doc used to verify real opencode behavior.
- Create `spikes/f001-opencode-input.txt`: golden input used by the opencode spike.
- Create `spikes/f001-opencode-output.json`: captured opencode JSON output from the spike.
- Create `tests/fixtures/multimodal-labeling.md`: sample project document.
- Create `tests/test_ids.py`, `tests/test_markdown_store.py`, `tests/test_index_store.py`, `tests/test_confirm.py`, `tests/test_opencode_runner.py`, `tests/test_cli.py`.

## Task 0: Real opencode Contract Spike

**Files:**
- Create: `.opencode/agent/workevent-archivist.md`
- Create: `spikes/f001-opencode-project.md`
- Create: `spikes/f001-opencode-input.txt`
- Create: `spikes/f001-opencode-output.json`

**Interfaces:**
- Produces: confirmed opencode command shape for Task 6.
- Produces: JSON contract with keys `target`, `confidence`, `reason`, `event`, `attachment_paths`, `markdown_preview`.

- [ ] **Step 1: Create the minimal archivist agent**

```md
---
description: WorkEventAgent archivist spike
mode: primary
tools:
  read: true
  write: false
  edit: false
  bash: false
---

You are the WorkEventAgent archivist.

Read the project document passed through --file.
Return JSON only. Do not write files.

Required JSON shape:
{
  "target": {
    "project_id": "string",
    "item_id": "string",
    "task_id": "string",
    "new_item": false,
    "new_task": false
  },
  "confidence": 0.0,
  "reason": "string",
  "event": {
    "task_id": "string",
    "input_text": "string",
    "summary": "string",
    "status": "in_progress",
    "next_action": "string"
  },
  "attachment_paths": [],
  "markdown_preview": "string"
}
```

- [ ] **Step 2: Create the spike project and input**

```md
---
project_id: multimodal-labeling
title: Multimodal Labeling System
doc_kind: work_project
created: 2026-06-29
updated: 2026-06-29
---

# Multimodal Labeling System

## Current Snapshot

## Work Map

### Item: KV cache few-shot optimization <!-- item:kv-cache-few-shot -->
#### Task: Review current blockers <!-- task:kv-cache-blockers -->
- status: in_progress
- next_action: Review current blocker list.
- last_event_id:

## Decisions

## Attachments

## Timeline

## Daily / Weekly Rollups
```

```text
Reviewed blockers for KV cache few-shot optimization today. The main issue is unclear prefix reuse strategy. Next step is to map the current inference chain.
```

- [ ] **Step 3: Run real opencode once**

Run:

```powershell
$inputText = Get-Content -Raw spikes/f001-opencode-input.txt
opencode run --agent workevent-archivist --file spikes/f001-opencode-project.md --format json "Archive this update: $inputText" > spikes/f001-opencode-output.json
```

Expected: exit code `0` and `spikes/f001-opencode-output.json` contains valid JSON. If `--agent`, `--file`, or `--format json` fails, stop and update this plan before implementing Python modules.

- [ ] **Step 4: Validate JSON shape**

Run:

```powershell
python -c "import json, pathlib; data=json.loads(pathlib.Path('spikes/f001-opencode-output.json').read_text(encoding='utf-8')); assert {'target','confidence','reason','event','attachment_paths','markdown_preview'} <= data.keys(); assert {'project_id','item_id','task_id'} <= data['target'].keys(); assert {'task_id','input_text','summary','status','next_action'} <= data['event'].keys(); assert 'event_id' not in data['event']"
```

Expected: exit code `0`.

- [ ] **Step 5: Commit**

```bash
git add .opencode/agent/workevent-archivist.md spikes/f001-opencode-project.md spikes/f001-opencode-input.txt spikes/f001-opencode-output.json
git commit -m "test: verify opencode archivist contract" -m "Why: Prove the real opencode CLI contract before building wrapper modules." -m "[宪宪/GPT-5.5🐾]"
```

## Task 1: Python Package Skeleton and Fixture

**Files:**
- Create: `pyproject.toml`
- Create: `workeventagent/__init__.py`
- Create: `tests/fixtures/multimodal-labeling.md`
- Test: `tests/test_package_import.py`

**Interfaces:**
- Produces: importable package `workeventagent`.
- Produces: fixture document with `project_id=multimodal-labeling`, `item_id=kv-cache-few-shot`, and `task_id=kv-cache-blockers`.

- [ ] **Step 1: Write the failing import test**

```python
# tests/test_package_import.py
import unittest


class PackageImportTest(unittest.TestCase):
    def test_package_imports(self):
        import workeventagent

        self.assertEqual(workeventagent.__all__, [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_package_import -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'workeventagent'`.

- [ ] **Step 3: Add minimal package files**

```toml
# pyproject.toml
[project]
name = "workeventagent"
version = "0.1.0"
requires-python = ">=3.11"
```

```python
# workeventagent/__init__.py
__all__ = []
```

```md
---
project_id: multimodal-labeling
title: Multimodal Labeling System
doc_kind: work_project
created: 2026-06-29
updated: 2026-06-29
---

# Multimodal Labeling System

## Current Snapshot

- Current focus: KV cache few-shot optimization

## Work Map

### Item: KV cache few-shot optimization <!-- item:kv-cache-few-shot -->
#### Task: Review current blockers <!-- task:kv-cache-blockers -->
- status: in_progress
- next_action: Review current blocker list.
- last_event_id:

#### Task: Read KV cache fundamentals <!-- task:kv-cache-fundamentals -->
- status: in_progress
- next_action: Read current architecture notes.
- last_event_id:

## Decisions

- Keep current few-shot baseline until blocker review is complete.

## Attachments

- path: attachments/2026-06-29/baseline.png
- related_task_id: kv-cache-blockers
- note: Existing archived image.

## Timeline

## Daily / Weekly Rollups
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_package_import -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml workeventagent/__init__.py tests/fixtures/multimodal-labeling.md tests/test_package_import.py
git commit -m "chore: scaffold WorkEventAgent package" -m "Why: Establish a testable Python package before implementing MVP behavior." -m "[宪宪/GPT-5.5🐾]"
```

## Task 2: IDs and Proposal Models

**Files:**
- Create: `workeventagent/models.py`
- Create: `workeventagent/ids.py`
- Test: `tests/test_ids.py`

**Interfaces:**
- Produces: `make_stable_id(title: str) -> str`
- Produces: `make_unique_stable_id(title: str, existing: set[str]) -> str`
- Produces: `make_event_id(now: datetime, task_id: str, existing: set[str]) -> str`
- Produces: dataclasses `ArchiveProposal`, `TimelineEvent`, `TargetRef`, `ConfirmationDecision`

- [ ] **Step 1: Write failing tests for stable IDs and event collision handling**

```python
# tests/test_ids.py
import unittest
from datetime import datetime, timezone

from workeventagent.ids import make_event_id, make_stable_id, make_unique_stable_id


class IdTest(unittest.TestCase):
    def test_stable_id_normalizes_titles(self):
        self.assertEqual(make_stable_id("KV Cache Few Shot"), "kv-cache-few-shot")
        self.assertEqual(make_stable_id("  Review   Blockers  "), "review-blockers")

    def test_unique_stable_id_adds_suffix_on_collision(self):
        existing = {"kv-cache-few-shot", "kv-cache-few-shot-2"}
        self.assertEqual(make_unique_stable_id("KV Cache Few Shot", existing), "kv-cache-few-shot-3")

    def test_event_id_uses_milliseconds_and_suffix(self):
        now = datetime(2026, 6, 29, 15, 30, 0, 123000, tzinfo=timezone.utc)
        first = make_event_id(now, "kv-cache-blockers", set())
        second = make_event_id(now, "kv-cache-blockers", {first})

        self.assertEqual(first, "20260629-153000123-kv-cache-blockers")
        self.assertEqual(second, "20260629-153000123-kv-cache-blockers-2")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_ids -v`

Expected: FAIL with missing `workeventagent.ids`.

- [ ] **Step 3: Implement IDs and models**

```python
# workeventagent/ids.py
from __future__ import annotations

import re
from datetime import datetime


def make_stable_id(title: str) -> str:
    lowered = title.strip().lower()
    normalized = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    return normalized or "untitled"


def make_unique_stable_id(title: str, existing: set[str]) -> str:
    base = make_stable_id(title)
    if base not in existing:
        return base
    suffix = 2
    while f"{base}-{suffix}" in existing:
        suffix += 1
    return f"{base}-{suffix}"


def make_event_id(now: datetime, task_id: str, existing: set[str]) -> str:
    base = now.strftime("%Y%m%d-%H%M%S") + f"{now.microsecond // 1000:03d}-{task_id}"
    if base not in existing:
        return base
    suffix = 2
    while f"{base}-{suffix}" in existing:
        suffix += 1
    return f"{base}-{suffix}"
```

```python
# workeventagent/models.py
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
    markdown_preview: str = ""


@dataclass(frozen=True)
class ConfirmationDecision:
    kind: DecisionKind
    edited_proposal: ArchiveProposal | None = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m unittest tests.test_ids -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add workeventagent/models.py workeventagent/ids.py tests/test_ids.py
git commit -m "feat: add worklog IDs and proposal models" -m "Why: Give Markdown anchors, timeline events, and confirmation cards stable data shapes." -m "[宪宪/GPT-5.5🐾]"
```

## Task 3: Markdown Store

**Files:**
- Create: `workeventagent/markdown_store.py`
- Test: `tests/test_markdown_store.py`

**Interfaces:**
- Consumes: `ArchiveProposal`, `TimelineEvent`
- Produces: `ProjectDocument.from_text(text: str) -> ProjectDocument`
- Produces: `ProjectDocument.apply_proposal(proposal: ArchiveProposal, updated_date: str) -> str`
- Produces: `ProjectDocument.insert_new_task(proposal: ArchiveProposal) -> str`
- Produces: `write_project_atomically(path: Path, text: str) -> None`

- [ ] **Step 1: Write failing tests for anchored task update, timeline append, and new task insertion**

```python
# tests/test_markdown_store.py
import unittest
import tempfile
from pathlib import Path

from workeventagent.markdown_store import ProjectDocument, write_project_atomically
from workeventagent.models import ArchiveProposal, TargetRef, TimelineEvent


FIXTURE = Path("tests/fixtures/multimodal-labeling.md")


class MarkdownStoreTest(unittest.TestCase):
    def proposal(self, new_task=False):
        return ArchiveProposal(
            target=TargetRef(
                project_id="multimodal-labeling",
                item_id="kv-cache-few-shot",
                task_id="kv-cache-blockers-2" if new_task else "kv-cache-blockers",
                new_task=new_task,
            ),
            confidence=0.91,
            reason="Matched KV cache item.",
            event=TimelineEvent(
                event_id="20260629-153000123-kv-cache-blockers",
                task_id="kv-cache-blockers-2" if new_task else "kv-cache-blockers",
                input_text="Reviewed blockers.",
                summary="Prefix reuse strategy is unclear.",
                status="in_progress",
                next_action="Map current inference chain.",
            ),
            markdown_preview="#### Task: Review blocker details <!-- task:kv-cache-blockers-2 -->",
        )

    def test_apply_existing_task_updates_block_and_appends_timeline(self):
        doc = ProjectDocument.from_text(FIXTURE.read_text(encoding="utf-8"))
        updated = doc.apply_proposal(self.proposal(), updated_date="2026-06-30")

        self.assertIn("last_event_id: 20260629-153000123-kv-cache-blockers", updated)
        self.assertIn("Map current inference chain.", updated)
        self.assertIn("<!-- event:20260629-153000123-kv-cache-blockers -->", updated)
        self.assertIn("updated: 2026-06-30", updated)
        self.assertIn("#### Task: Read KV cache fundamentals <!-- task:kv-cache-fundamentals -->", updated)
        self.assertIn("Keep current few-shot baseline until blocker review is complete.", updated)
        self.assertIn("attachments/2026-06-29/baseline.png", updated)

    def test_new_task_requires_new_task_marker(self):
        doc = ProjectDocument.from_text(FIXTURE.read_text(encoding="utf-8"))
        updated = doc.insert_new_task(self.proposal(new_task=True))

        self.assertIn("<!-- task:kv-cache-blockers-2 -->", updated)
        self.assertIn("#### Task: Review blocker details", updated)

    def test_atomic_write_replaces_whole_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "project.md"
            path.write_text("old", encoding="utf-8")

            write_project_atomically(path, "new")

            self.assertEqual(path.read_text(encoding="utf-8"), "new")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_markdown_store -v`

Expected: FAIL with missing `workeventagent.markdown_store`.

- [ ] **Step 3: Implement minimal Markdown store**

Implement exact behavior:

- parse `project_id` from frontmatter
- locate task blocks by `<!-- task:<task_id> -->`
- replace only the target task block until the next `#### Task:`, `### Item:`, or `## `
- append timeline events under `## Timeline`
- insert new task under the matching item anchor when `proposal.target.new_task` is true
- update frontmatter `updated:` with the write date
- write project files with a same-directory temporary file and `os.replace`
- raise `ValueError` if project ID, item anchor, or task anchor is missing

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m unittest tests.test_markdown_store -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add workeventagent/markdown_store.py tests/test_markdown_store.py
git commit -m "feat: add Markdown worklog store" -m "Why: Apply confirmed proposals to the Markdown source of truth with anchored writes." -m "[宪宪/GPT-5.5🐾]"
```

## Task 4: SQLite Index Store

**Files:**
- Create: `workeventagent/index_store.py`
- Test: `tests/test_index_store.py`

**Interfaces:**
- Consumes: project Markdown text
- Produces: `init_db(path: Path) -> None`
- Produces: `rebuild_index(db_path: Path, project_paths: list[Path]) -> None`
- Produces: `get_task(db_path: Path, task_id: str) -> dict[str, str]`

- [ ] **Step 1: Write failing rebuild test**

```python
# tests/test_index_store.py
import tempfile
import unittest
from pathlib import Path

from workeventagent.index_store import get_task, init_db, rebuild_index


class IndexStoreTest(unittest.TestCase):
    def test_rebuild_indexes_task_state_from_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workeventagent.sqlite"
            project_path = Path("tests/fixtures/multimodal-labeling.md")

            init_db(db_path)
            rebuild_index(db_path, [project_path])
            rebuild_index(db_path, [project_path])
            task = get_task(db_path, "kv-cache-blockers")

            self.assertEqual(task["project_id"], "multimodal-labeling")
            self.assertEqual(task["item_id"], "kv-cache-few-shot")
            self.assertEqual(task["status"], "in_progress")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_index_store -v`

Expected: FAIL with missing `workeventagent.index_store`.

- [ ] **Step 3: Implement SQLite schema and rebuild**

Create tables:

- `projects(project_id primary key, title, doc_path, updated_at)`
- `tasks(task_id primary key, project_id, item_id, title, status, next_action, doc_path, doc_anchor, last_event_id)`
- `attachments(path primary key, project_id, task_id, note)`

Parse Work Map anchors and nearby `status`, `next_action`, `last_event_id` lines. This task does not create a separate timeline-history table. Rebuild must be idempotent: delete indexed rows for scanned project IDs before inserting, or use `INSERT OR REPLACE` consistently.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m unittest tests.test_index_store -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add workeventagent/index_store.py tests/test_index_store.py
git commit -m "feat: add rebuildable SQLite index" -m "Why: Keep task lookup fast while preserving Markdown as the source of truth." -m "[宪宪/GPT-5.5🐾]"
```

## Task 5: Terminal Confirmation Cards

**Files:**
- Create: `workeventagent/confirm.py`
- Test: `tests/test_confirm.py`

**Interfaces:**
- Consumes: `ArchiveProposal`
- Produces: `render_confirmation_card(proposal: ArchiveProposal) -> str`
- Produces: `parse_confirmation_input(raw: str) -> ConfirmationDecision`
- Produces: `edit_proposal_with_editor(proposal: ArchiveProposal, editor: str) -> ArchiveProposal`

- [ ] **Step 1: Write failing confirmation tests**

```python
# tests/test_confirm.py
import unittest

from workeventagent.confirm import parse_confirmation_input, render_confirmation_card
from workeventagent.models import ArchiveProposal, TargetRef, TimelineEvent


class ConfirmTest(unittest.TestCase):
    def test_card_shows_new_task_and_markdown_preview(self):
        proposal = ArchiveProposal(
            target=TargetRef("multimodal-labeling", "kv-cache-few-shot", "new-task", new_task=True),
            confidence=0.8,
            reason="User mentioned a new task.",
            event=TimelineEvent("event-1", "new-task", "input", "summary", "in_progress", "next"),
            markdown_preview="#### Task: New task <!-- task:new-task -->",
        )

        card = render_confirmation_card(proposal)

        self.assertIn("new_task: true", card)
        self.assertIn("#### Task: New task", card)
        self.assertIn("confirm / edit / cancel", card)

    def test_parse_confirmation_input(self):
        self.assertEqual(parse_confirmation_input("confirm").kind, "confirm")
        self.assertEqual(parse_confirmation_input(" CONFIRM ").kind, "confirm")
        self.assertEqual(parse_confirmation_input("edit").kind, "edit")
        self.assertEqual(parse_confirmation_input("cancel").kind, "cancel")
        self.assertEqual(parse_confirmation_input("").kind, "cancel")
        self.assertEqual(parse_confirmation_input("y").kind, "cancel")
        self.assertEqual(parse_confirmation_input("???").kind, "cancel")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_confirm -v`

Expected: FAIL with missing `workeventagent.confirm`.

- [ ] **Step 3: Implement renderer and parser**

The renderer must include target IDs, confidence, reason, new item/task flags, timeline preview, attachment paths, and Markdown block preview. Confirmation parsing must strip whitespace and lowercase known commands; unknown input must return `cancel`. `edit_proposal_with_editor` must serialize the proposal to a temporary JSON file, run the configured editor, reload JSON, validate the proposal shape, and return the edited proposal for another confirmation render.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m unittest tests.test_confirm -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add workeventagent/confirm.py tests/test_confirm.py
git commit -m "feat: add terminal confirmation cards" -m "Why: Require explicit user confirmation before Markdown writes." -m "[宪宪/GPT-5.5🐾]"
```

## Task 6: opencode Runner and Archivist Agent

**Files:**
- Create: `workeventagent/opencode_runner.py`
- Modify: `.opencode/agent/workevent-archivist.md`
- Test: `tests/test_opencode_runner.py`

**Interfaces:**
- Produces: `run_archivist(prompt: str, project_doc: Path, opencode_bin: str = "opencode") -> str`
- Produces: `parse_archivist_output(raw: str, event_id: str) -> ArchiveProposal`
- Produces: exception `OpencodeRunnerError`
- Consumes: opencode CLI output as JSON proposal text

- [ ] **Step 1: Write failing subprocess command test**

```python
# tests/test_opencode_runner.py
import unittest
from pathlib import Path
from unittest.mock import patch

from workeventagent.opencode_runner import OpencodeRunnerError, parse_archivist_output, run_archivist


class OpencodeRunnerTest(unittest.TestCase):
    @patch("workeventagent.opencode_runner.subprocess.run")
    def test_run_archivist_calls_opencode_agent_with_file(self, run):
        run.return_value.stdout = '{"ok": true}'
        run.return_value.returncode = 0

        output = run_archivist("input", Path("project.md"), opencode_bin="opencode")

        self.assertEqual(output, '{"ok": true}')
        args = run.call_args.args[0]
        self.assertEqual(args[0], "opencode")
        self.assertIn("run", args)
        self.assertIn("--agent", args)
        self.assertIn("workevent-archivist", args)
        self.assertIn("--file", args)

    @patch("workeventagent.opencode_runner.subprocess.run")
    def test_run_archivist_raises_on_nonzero_exit(self, run):
        run.return_value.stdout = ""
        run.return_value.stderr = "bad flag"
        run.return_value.returncode = 2

        with self.assertRaises(OpencodeRunnerError):
            run_archivist("input", Path("project.md"), opencode_bin="opencode")

    def test_parse_archivist_output_rejects_empty_or_invalid_json(self):
        with self.assertRaises(OpencodeRunnerError):
            parse_archivist_output("", "event-1")
        with self.assertRaises(OpencodeRunnerError):
            parse_archivist_output("{not json", "event-1")

    def test_parse_archivist_output_uses_wrapper_event_id(self):
        raw = """
{
  "target": {"project_id": "multimodal-labeling", "item_id": "kv-cache-few-shot", "task_id": "kv-cache-blockers"},
  "confidence": 0.91,
  "reason": "Matched KV cache item",
  "event": {"event_id": "agent-must-not-own-this", "task_id": "kv-cache-blockers", "input_text": "input", "summary": "summary", "status": "in_progress", "next_action": "next"},
  "attachment_paths": [],
  "markdown_preview": ""
}
"""
        proposal = parse_archivist_output(raw, "wrapper-event-id")

        self.assertEqual(proposal.event.event_id, "wrapper-event-id")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_opencode_runner -v`

Expected: FAIL with missing `workeventagent.opencode_runner`.

- [ ] **Step 3: Implement subprocess wrapper and agent file**

Agent file requirements:

- read `docs/WORKLOG_SCHEMA.md`
- produce JSON only
- never write files directly
- propose Markdown changes only
- ask for clarification when project/item/task target is uncertain

Runner requirements:

- check `subprocess.run(...).returncode`
- raise `OpencodeRunnerError` for non-zero exit, empty stdout, invalid JSON, or missing required keys
- convert valid JSON into `ArchiveProposal`
- set `ArchiveProposal.event.event_id` from the wrapper-generated `event_id` argument
- ignore any `event.event_id` field returned by the agent

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m unittest tests.test_opencode_runner -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add workeventagent/opencode_runner.py .opencode/agent/workevent-archivist.md tests/test_opencode_runner.py
git commit -m "feat: add opencode archivist runner" -m "Why: Keep LLM proposal generation inside the opencode boundary." -m "[宪宪/GPT-5.5🐾]"
```

## Task 7: CLI Dry-Run and Confirmed Write Flow

**Files:**
- Create: `workeventagent/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: previous modules
- Produces: `python -m workeventagent.cli capture --project docs/project.md --db workeventagent.sqlite --text "..." --attach path/to/image.png`
- Produces: `main(argv: list[str], now: datetime | None = None) -> int`

- [ ] **Step 1: Write failing dry-run CLI test**

```python
# tests/test_cli.py
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from workeventagent.cli import main
from workeventagent.index_store import get_task


class CliTest(unittest.TestCase):
    @patch("workeventagent.cli.run_archivist")
    def test_capture_dry_run_prints_confirmation_card(self, run_archivist):
        run_archivist.return_value = """
{
  "target": {"project_id": "multimodal-labeling", "item_id": "kv-cache-few-shot", "task_id": "kv-cache-blockers"},
  "confidence": 0.91,
  "reason": "Matched KV cache item",
  "event": {"task_id": "kv-cache-blockers", "input_text": "input", "summary": "summary", "status": "in_progress", "next_action": "next"},
  "attachment_paths": [],
  "markdown_preview": ""
}
"""
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project.md"
            project.write_text(Path("tests/fixtures/multimodal-labeling.md").read_text(encoding="utf-8"), encoding="utf-8")
            code = main(["capture", "--project", str(project), "--db", str(Path(tmp) / "index.sqlite"), "--text", "input", "--dry-run"])

        self.assertEqual(code, 0)

    @patch("workeventagent.cli.input", return_value="confirm")
    @patch("workeventagent.cli.run_archivist")
    def test_capture_confirmed_write_updates_markdown_and_sqlite(self, run_archivist, input_mock):
        run_archivist.return_value = """
{
  "target": {"project_id": "multimodal-labeling", "item_id": "kv-cache-few-shot", "task_id": "kv-cache-blockers"},
  "confidence": 0.91,
  "reason": "Matched KV cache item",
  "event": {"task_id": "kv-cache-blockers", "input_text": "Reviewed blockers for KV cache few-shot optimization today.", "summary": "Prefix reuse strategy is unclear.", "status": "in_progress", "next_action": "Map current inference chain."},
  "attachment_paths": ["attachments/baseline.png"],
  "markdown_preview": ""
}
"""
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project.md"
            db = Path(tmp) / "index.sqlite"
            attachment = Path(tmp) / "baseline.png"
            attachment.write_bytes(b"not-analyzed")
            project.write_text(Path("tests/fixtures/multimodal-labeling.md").read_text(encoding="utf-8"), encoding="utf-8")

            fixed_now = datetime(2026, 6, 29, 15, 30, 0, 123000, tzinfo=timezone.utc)
            code = main(["capture", "--project", str(project), "--db", str(db), "--text", "Reviewed blockers for KV cache few-shot optimization today.", "--attach", str(attachment)], now=fixed_now)
            updated = project.read_text(encoding="utf-8")
            task = get_task(db, "kv-cache-blockers")

        self.assertEqual(code, 0)
        self.assertIn("20260629-153000123-kv-cache-blockers", updated)
        self.assertIn("Map current inference chain.", updated)
        self.assertEqual(task["next_action"], "Map current inference chain.")

    @patch("workeventagent.cli.edit_proposal_with_editor")
    @patch("workeventagent.cli.input", side_effect=["edit", "confirm"])
    @patch("workeventagent.cli.run_archivist")
    def test_capture_edit_reconfirms_before_write(self, run_archivist, input_mock, edit_mock):
        run_archivist.return_value = """
{
  "target": {"project_id": "multimodal-labeling", "item_id": "kv-cache-few-shot", "task_id": "kv-cache-blockers"},
  "confidence": 0.91,
  "reason": "Matched KV cache item",
  "event": {"task_id": "kv-cache-blockers", "input_text": "input", "summary": "summary", "status": "in_progress", "next_action": "next"},
  "attachment_paths": [],
  "markdown_preview": ""
}
"""
        edit_mock.side_effect = lambda proposal: proposal

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project.md"
            project.write_text(Path("tests/fixtures/multimodal-labeling.md").read_text(encoding="utf-8"), encoding="utf-8")
            code = main(["capture", "--project", str(project), "--db", str(Path(tmp) / "index.sqlite"), "--text", "input"])

        self.assertEqual(code, 0)
        edit_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_cli -v`

Expected: FAIL with missing `workeventagent.cli`.

- [ ] **Step 3: Implement CLI**

CLI behavior:

- `--dry-run` renders the confirmation card and exits without writing.
- without `--dry-run`, prompt for `confirm`, `edit`, or `cancel`.
- `--attach PATH` may be repeated; paths are archived as paths only and not parsed as images.
- before parsing agent output, collect existing Timeline event IDs and generate the next event ID with `make_event_id`.
- `confirm` writes Markdown, then updates SQLite.
- `cancel` exits with code `2`.
- `edit` opens the proposal editor, reloads the edited proposal, and renders confirmation again before any write.
- Markdown writes use `write_project_atomically`.
- Confirmed write tests must assert Markdown content changed and SQLite can read the updated task.

- [ ] **Step 4: Run all tests**

Run: `python -m unittest discover -s tests -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add workeventagent/cli.py tests/test_cli.py
git commit -m "feat: add WorkEventAgent capture CLI" -m "Why: Provide the terminal MVP loop without introducing a GUI." -m "[宪宪/GPT-5.5🐾]"
```

## Task 8: Documentation Review Gate

**Files:**
- Modify: `docs/features/F001-workeventagent-vision.md`
- Modify: `docs/WORKLOG_SCHEMA.md`
- Create: `docs/features/F001-mvp-usage.md`

**Interfaces:**
- Produces: a usage document with exact CLI examples and expected outputs.

- [ ] **Step 1: Add usage document**

````md
---
feature_ids: [F001]
topics: [usage, workeventagent]
doc_kind: guide
created: 2026-06-29
---

# F001 MVP Usage

Dry run:

```bash
python -m workeventagent.cli capture --project projects/multimodal-labeling.md --db .workeventagent/index.sqlite --text "Reviewed blockers." --dry-run
```

Expected dry-run output contains:

```text
Archive proposal
project_id: multimodal-labeling
confirm / edit / cancel
```

Confirmed write:

```bash
python -m workeventagent.cli capture --project projects/multimodal-labeling.md --db .workeventagent/index.sqlite --text "Reviewed blockers."
```

Expected confirmed write behavior:

```text
Markdown written
SQLite index updated
```
````

- [ ] **Step 2: Run full verification**

Run: `python -m unittest discover -s tests -v`

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add docs/features/F001-mvp-usage.md docs/features/F001-workeventagent-vision.md docs/WORKLOG_SCHEMA.md
git commit -m "docs: add WorkEventAgent MVP usage" -m "Why: Document how to verify the terminal archiving loop before review." -m "[宪宪/GPT-5.5🐾]"
```

## Plan Self-Review

Spec coverage:

- Real opencode contract: Task 0.
- Capture loop: Task 7.
- opencode boundary: Task 6.
- Terminal confirmation: Task 5 and Task 7.
- Markdown source of truth: Task 3, including atomic writes and sibling-block preservation.
- SQLite rebuildable index: Task 4, including idempotent rebuild.
- Strong-confirmed item/task creation: Task 3 and Task 5.
- Wrapper-owned event ID generation: Task 2, Task 6, and Task 7.
- Confirmed-write path: Task 7.
- Golden end-to-end behavior: Task 7.
- Attachments as paths only: model support in Task 2, display in Task 5, and CLI `--attach` input in Task 7.
- Frontmatter `updated:` bump: Task 3 and Task 7.
- Correction events: schema documented; implementation can be added after initial update flow unless co-creator prioritizes correction before first MVP run.

Known implementation sequencing:

- Do not implement GUI.
- Do not implement automatic rollups.
- Do not implement parallel write locking beyond single-writer guard.
- Do not start execution until this plan and F001 spec are reviewed.
