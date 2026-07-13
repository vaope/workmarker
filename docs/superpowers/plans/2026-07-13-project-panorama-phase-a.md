# F007 Phase A Project Document v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce a human-readable, safely governed Project Document v2, migrate existing v1 projects without losing evidence, and make the Electron project page a single project-panorama reading surface.

**Architecture:** Add two focused Python foundations: `project_schema.py` owns stable section anchors, frontmatter, section hashes, and Timeline/attachment parsing; `work_map_store.py` owns dual-v1/v2 Work Map parsing and deterministic mutation. A separate migration module produces previewable v1→v2 transformations and applies them only after source-hash validation, backup, atomic replacement, and identity verification. The Electron client consumes a typed `project_panorama` response, renders it through a pure browser module, and edits reviewed sections only through hash-guarded backend commands.

**Tech Stack:** Python 3.11+ standard library, pytest/unittest, SQLite, Electron 33, browser JavaScript, HTML/CSS, Node `vm` renderer tests.

## Global Constraints

- Phase A adds no LLM synthesis and does not call opencode for project-panorama content.
- `<workspace>/<project_id>.md` remains the one default project document and the Markdown source of truth; SQLite remains rebuildable.
- Registry continues to discover only workspace-root `*.md` with `doc_kind: work_project`.
- New projects use `schema_version: 2`; existing v1 projects remain readable and writable until the user explicitly migrates them.
- v2 parsing uses stable `<!-- section:... -->` anchors. Visible Chinese headings are presentation text and may not be parser keys.
- Work Map uses stable item/task anchors; `[ ]` maps to `in_progress`, `[x]` maps to `done`, and raw `status: in_progress` is absent from v2 visible text.
- Timeline remains in the project file, append-only, and compatible with capture, reports, search, correction, and index rebuild.
- `reviewed` content is never silently overwritten. Every non-append write validates a base hash and rejects stale input.
- Migration must preserve project/item/task/event IDs, Timeline event count, Decisions content, Attachments content, and unknown content. Unsafe conversion stops before replacing the file.
- Migration writes a timestamped backup under `.workeventagent/backups/<project_id>/`, then uses temp-file + `os.replace` atomic replacement.
- Do not build a generic Markdown AST or fuzzy semantic patch engine.
- Do not add task priority, due date, owner, new task states, npm dependencies, or Python dependencies.
- Preserve Electron security: `contextIsolation: true`, `nodeIntegration: false`, preload-only IPC.
- Tests use temporary workspaces only; never read or write production user workspaces.
- Do not use Clowder AI ports 3003/3004 or Redis ports 6389/6398.

## File Structure

- Create `workeventagent/project_schema.py`: v1/v2 frontmatter, stable section lookup, section/metadata hashes, Timeline and attachment parsing, reviewed-content validation.
- Create `workeventagent/work_map_store.py`: dual-v1/v2 Work Map parser and deterministic item/task render/mutation helpers.
- Create `workeventagent/project_migration.py`: pure preview plus guarded backup/atomic apply and identity verification.
- Create `client/windows/project-panorama.js`: pure escaping and panorama HTML rendering; no IPC or application state.
- Create `tests/test_project_schema.py`, `tests/test_work_map_store.py`, `tests/test_project_migration.py`, and `tests/test_project_panorama_renderer.py`.
- Modify `docs/WORKLOG_SCHEMA.md`: make schema v2 the current protocol and retain the v1 compatibility/migration contract.
- Modify `workeventagent/gui.py`: thin handlers for panorama read/edit/migration and adapters to shared parsers.
- Modify `workeventagent/markdown_store.py`, `index_store.py`, `registry.py`, `search_store.py`, and `correction_store.py`: use shared dual-schema helpers.
- Modify `client/main.js`, `client/preload.js`, `client/windows/main.html`, `main.css`, and `main.js`: IPC, panorama view, migration flow, reviewed-section editors.
- Modify existing backend and renderer tests to lock capture/report/search/correction/index compatibility.

---

### Task 1: Establish the stable section and protocol foundation

**Files:**
- Create: `workeventagent/project_schema.py`
- Create: `tests/test_project_schema.py`
- Modify: `docs/WORKLOG_SCHEMA.md`

**Interfaces:**
- Produces `SECTION_SPECS`, `parse_frontmatter(text)`, `schema_version(text)`, `find_section(text, section_id)`, `section_content(text, section_id)`, `section_hash(text, section_id)`, `metadata_hash(text)`, `replace_section_content(text, section_id, content)`, `update_frontmatter(text, fields)`, `parse_timeline_events(text)`, `parse_attachment_records(text)`, and `validate_reviewed_content(content)`.
- `find_section` first resolves a stable v2 section anchor; only when no v2 anchor exists may it use the exact v1 legacy heading declared in `SECTION_SPECS`.

- [ ] **Step 1: Write failing stable-section tests**

Create `tests/test_project_schema.py`:

```python
from hashlib import sha256

import pytest

from workeventagent.project_schema import (
    find_section,
    metadata_hash,
    parse_frontmatter,
    replace_section_content,
    schema_version,
    section_content,
    section_hash,
    validate_reviewed_content,
)


V2 = """---
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

## 任意可见标题 <!-- section:technical-overview -->

Electron 调度 Python。

## 事件证据 <!-- section:timeline -->

- 2026-07-13T10:00:00+08:00 <!-- event:event-a -->
  - task_id: task-a
  - summary: 完成基础验证
"""


def test_v2_section_lookup_uses_anchor_not_visible_title() -> None:
    section = find_section(V2, "technical-overview")
    assert section.heading == "## 任意可见标题 <!-- section:technical-overview -->"
    assert section_content(V2, "technical-overview").strip() == "Electron 调度 Python。"


def test_section_hash_covers_content_not_heading() -> None:
    content = "Electron 调度 Python。\n"
    assert section_hash(V2, "technical-overview") == "sha256:" + sha256(content.encode()).hexdigest()


def test_replace_section_preserves_neighbors_and_rejects_stale_control_text() -> None:
    updated = replace_section_content(V2, "technical-overview", "Python 负责确定性写入。\n")
    assert "Python 负责确定性写入。" in updated
    assert "<!-- event:event-a -->" in updated
    with pytest.raises(ValueError, match="control syntax"):
        validate_reviewed_content("## 伪造区块 <!-- section:timeline -->")


def test_frontmatter_and_metadata_hash_are_explicit() -> None:
    assert schema_version(V2) == 2
    assert parse_frontmatter(V2)["phase"] == "build"
    assert metadata_hash(V2).startswith("sha256:")
```

- [ ] **Step 2: Run the tests to verify red**

```powershell
python -m pytest tests/test_project_schema.py -q
```

Expected: collection fails because `workeventagent.project_schema` does not exist.

- [ ] **Step 3: Implement the section contract**

Create `workeventagent/project_schema.py` with these exact public types and rules:

```python
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SectionSpec:
    section_id: str
    title: str
    ownership: str
    legacy_heading: str | None


@dataclass(frozen=True)
class SectionSlice:
    section_id: str
    heading: str
    heading_start: int
    content_start: int
    content_end: int


SECTION_SPECS = (
    SectionSpec("project-profile", "项目档案", "reviewed", None),
    SectionSpec("current-panorama", "当前全景", "derived-reviewed", "Current Snapshot"),
    SectionSpec("work-map", "工作地图", "structured", "Work Map"),
    SectionSpec("technical-overview", "技术概览", "reviewed", None),
    SectionSpec("project-knowledge", "关键认知", "reviewed", None),
    SectionSpec("decisions", "关键决策", "append-only", "Decisions"),
    SectionSpec("attachments", "附件", "append-only", "Attachments"),
    SectionSpec("timeline", "事件证据", "append-only", "Timeline"),
    SectionSpec("rollups", "历史摘要", "derived", "Daily / Weekly Rollups"),
)
SECTION_BY_ID = {spec.section_id: spec for spec in SECTION_SPECS}


def parse_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    result: dict[str, str] = {}
    for raw in parts[1].splitlines():
        if ":" not in raw:
            continue
        key, _, value = raw.partition(":")
        result[key.strip()] = value.strip()
    return result


def schema_version(text: str) -> int:
    raw = parse_frontmatter(text).get("schema_version", "1")
    return int(raw) if raw.isdigit() else 1


def _sha256(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def find_section(text: str, section_id: str) -> SectionSlice:
    spec = SECTION_BY_ID.get(section_id)
    if spec is None:
        raise ValueError(f"unknown section: {section_id}")
    anchored = re.compile(
        rf"^##[^\n]*<!--\s*section:{re.escape(section_id)}\s*-->[^\n]*$",
        re.MULTILINE,
    )
    match = anchored.search(text)
    if match is None and spec.legacy_heading:
        match = re.search(rf"^## {re.escape(spec.legacy_heading)}\s*$", text, re.MULTILINE)
    if match is None:
        raise ValueError(f"section not found: {section_id}")
    next_heading = re.search(r"^##\s+", text[match.end():], re.MULTILINE)
    content_end = match.end() + next_heading.start() if next_heading else len(text)
    content_start = match.end()
    if content_start < len(text) and text[content_start] == "\n":
        content_start += 1
    return SectionSlice(section_id, match.group(0), match.start(), content_start, content_end)


def section_content(text: str, section_id: str) -> str:
    section = find_section(text, section_id)
    return text[section.content_start:section.content_end].lstrip("\n").rstrip("\n") + "\n"


def section_hash(text: str, section_id: str) -> str:
    return _sha256(section_content(text, section_id))


def metadata_hash(text: str) -> str:
    fm = parse_frontmatter(text)
    return _sha256(f"status={fm.get('status', '')}\nphase={fm.get('phase', '')}\n")


def validate_reviewed_content(content: str) -> None:
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    if "<!--" in normalized or re.search(r"(?m)^#{1,2}\s", normalized) or "\n---\n" in normalized:
        raise ValueError("reviewed content contains control syntax")


def replace_section_content(text: str, section_id: str, content: str) -> str:
    validate_reviewed_content(content)
    section = find_section(text, section_id)
    rendered = "\n" + content.strip("\n") + "\n\n"
    return text[:section.content_start - 1] + rendered + text[section.content_end:]
```

Also implement `update_frontmatter`, `parse_timeline_events`, and `parse_attachment_records` by moving the existing deterministic parsing rules out of `gui.py`/`index_store.py`. Preserve multiline Timeline fields and return newest-first event order.

- [ ] **Step 4: Rewrite the protocol document as schema v2**

Update `docs/WORKLOG_SCHEMA.md` so it contains the exact v2 frontmatter, nine section anchors, ownership table, v2 Work Map grammar, append-only Timeline grammar, reviewed-write hash rule, and a compatibility note: v1 remains supported until explicit migration. State that visible titles are never parser keys.

- [ ] **Step 5: Run focused verification**

```powershell
python -m pytest tests/test_project_schema.py -q
python -m pytest tests/test_gui.py::TimelineParserTest tests/test_index_store.py -q
```

Expected: all focused tests pass.

- [ ] **Step 6: Commit the schema foundation**

```powershell
git add workeventagent/project_schema.py tests/test_project_schema.py docs/WORKLOG_SCHEMA.md
git commit -m "feat: define project document v2 sections" -m "Why: Human-readable headings need stable machine anchors and explicit ownership before any migration or UI work."
```

---

### Task 2: Add one dual-schema Work Map implementation

**Files:**
- Create: `workeventagent/work_map_store.py`
- Create: `tests/test_work_map_store.py`

**Interfaces:**
- Consumes `schema_version(text)` and `find_section(text, "work-map")` from Task 1.
- Produces `parse_work_map(text, strict=False) -> list[dict]`, `render_v2_item(item)`, `render_v2_task(task)`, `insert_item`, `insert_task`, `delete_item`, `delete_task`, `update_task_field`, and atomic `update_task_state`.
- All mutation helpers preserve sibling blocks and non-control prose byte-for-byte.

- [ ] **Step 1: Write equivalent v1/v2 parser tests**

Create `tests/test_work_map_store.py` with fixtures that represent the same item/task in both grammars:

```python
import pytest

from workeventagent.work_map_store import parse_work_map, update_task_field


V1_MAP = """## Work Map
### Item: Capture <!-- item:capture -->
- background: Durable intake.
#### Task: Persist card <!-- task:persist-card -->
- status: in_progress
- next_action: Add retry.
- last_event_id: event-a
## Timeline
"""

V2_MAP = """## 工作地图 <!-- section:work-map -->
### 工作项：Capture <!-- item:capture -->

Durable intake.

#### [ ] 任务：Persist card <!-- task:persist-card -->
- 下一步：Add retry.
<!-- task-meta:last_event_id=event-a -->
## 事件证据 <!-- section:timeline -->
"""


def test_v1_and_v2_parse_to_the_same_typed_state() -> None:
    assert parse_work_map(V1_MAP) == parse_work_map(V2_MAP) == [{
        "item_id": "capture",
        "title": "Capture",
        "background": "Durable intake.",
        "tasks": [{
            "task_id": "persist-card",
            "title": "Persist card",
            "status": "in_progress",
            "next_action": "Add retry.",
            "last_event_id": "event-a",
        }],
    }]


def test_v2_status_update_changes_checkbox_only() -> None:
    updated = update_task_field(V2_MAP, "persist-card", "status", "done", "2026-07-13")
    assert "#### [x] 任务：Persist card <!-- task:persist-card -->" in updated
    assert "Add retry." in updated
    assert "event-a" in updated


def test_noncanonical_task_is_rejected_instead_of_guessed() -> None:
    broken = V1_MAP.replace("- status: in_progress\n", "")
    with pytest.raises(ValueError, match="canonical status"):
        parse_work_map(broken, strict=True)
```

- [ ] **Step 2: Run the tests to verify red**

```powershell
python -m pytest tests/test_work_map_store.py -q
```

Expected: import failure because `work_map_store.py` does not exist.

- [ ] **Step 3: Implement the dual grammar**

Use exact anchored heading patterns:

```python
V1_ITEM_RE = re.compile(r"^###\s+Item:\s+(.+?)\s*<!--\s*item:(.+?)\s*-->\s*$")
V2_ITEM_RE = re.compile(r"^###\s+工作项[：:]\s*(.+?)\s*<!--\s*item:(.+?)\s*-->\s*$")
V1_TASK_RE = re.compile(r"^####\s+Task:\s+(.+?)\s*<!--\s*task:(.+?)\s*-->\s*$")
V2_TASK_RE = re.compile(r"^####\s+\[([ xX])\]\s+任务[：:]\s*(.+?)\s*<!--\s*task:(.+?)\s*-->\s*$")
V2_NEXT_RE = re.compile(r"^-\s*下一步[：:]\s*(.*)$")
V2_META_RE = re.compile(r"^<!--\s*task-meta:last_event_id=(.*?)\s*-->$")
```

`parse_work_map` must return items in file order and tasks in item order. `strict=True` additionally rejects duplicate IDs, task headings outside an item, missing/duplicate v1 status, invalid checkbox markers, and missing task anchors.

Render v2 blocks deterministically:

```python
def render_v2_task(task: dict) -> str:
    checked = "x" if task.get("status") == "done" else " "
    next_action = str(task.get("next_action", "")).replace("\n", " ").strip()
    last_event = str(task.get("last_event_id", "")).strip()
    return (
        f"#### [{checked}] 任务：{task['title']} <!-- task:{task['task_id']} -->\n"
        f"- 下一步：{next_action}\n"
        f"<!-- task-meta:last_event_id={last_event} -->\n"
    )
```

Mutation functions locate blocks only through stable item/task anchors and structural heading boundaries. They must not match display titles.

- [ ] **Step 4: Run focused parser/mutation tests**

```powershell
python -m pytest tests/test_work_map_store.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit the Work Map core**

```powershell
git add workeventagent/work_map_store.py tests/test_work_map_store.py
git commit -m "feat: add dual-schema work map store" -m "Why: One parser and mutation rail prevents v1/v2 regex behavior from drifting across capture, index, correction, and UI handlers."
```

---

### Task 3: Make all read paths understand v1 and v2

**Files:**
- Modify: `workeventagent/gui.py`
- Modify: `workeventagent/index_store.py`
- Modify: `workeventagent/registry.py`
- Modify: `workeventagent/search_store.py`
- Modify: `tests/test_gui.py`
- Modify: `tests/test_index_store.py`
- Modify: `tests/test_registry.py`
- Modify: `tests/test_search_store.py`

**Interfaces:**
- Consumes `parse_work_map`, `parse_timeline_events`, `parse_attachment_records`, and stable section lookup.
- Preserves existing `projects`, `tasks`, `timeline`, `generate_report`, `search`, and `rebuild_index` response shapes.

- [ ] **Step 1: Add a shared v2 golden fixture helper**

Add `tests/fixtures/project-v2.md` containing two work items, one done task, one in-progress task, two Timeline events, one decision, and one attachment. Use the exact v2 section and Work Map grammar from `docs/WORKLOG_SCHEMA.md`.

- [ ] **Step 2: Add read-compatibility tests**

Add tests that copy the v2 fixture into a temporary workspace and assert:

```python
def test_v2_project_is_readable_by_all_existing_read_paths(tmp_path: Path) -> None:
    project = tmp_path / "project-v2.md"
    project.write_text(Path("tests/fixtures/project-v2.md").read_text(encoding="utf-8"), encoding="utf-8")
    db = tmp_path / "index.sqlite"

    projects = handle_projects({"workspace": str(tmp_path)})["projects"]
    tasks = handle_tasks({"project_path": str(project)})
    timeline = handle_timeline({"project_path": str(project)})
    rebuild_index(db, [project])
    results = search_workspace(tmp_path, "Capture Inbox")

    assert projects[0]["open_task_count"] == 1
    assert tasks["items"][0]["tasks"][0]["status"] in {"done", "in_progress"}
    assert len(timeline["events"]) == 2
    assert get_task(db, "persist-card")["next_action"] == "Add retry."
    assert any(result["kind"] == "task" for result in results)
```

Also add a report regression proving `handle_generate_report` reads v2 Timeline without changing output semantics.

- [ ] **Step 3: Run the new tests to verify red**

```powershell
python -m pytest tests/test_gui.py -k "v2_project or v2_report" tests/test_index_store.py tests/test_registry.py tests/test_search_store.py -q
```

Expected: failures show legacy English-heading/status regexes cannot read v2.

- [ ] **Step 4: Replace duplicated read regexes**

Make these exact adaptations:

- `gui.handle_tasks` calls `parse_work_map(text)` and flattens the returned item payload only where required by existing response code.
- `gui.handle_timeline` and report code call `parse_timeline_events(text)`.
- `index_store._parse_project_document` calls `parse_work_map(text)` and `parse_attachment_records(text)`.
- `registry._count_open_tasks` counts parsed tasks with `status == "in_progress"`.
- `search_store.build_search_documents` emits item/task documents from `parse_work_map(text)` and Timeline documents from `parse_timeline_events(text)`.
- Project scanning continues to require `doc_kind: work_project`; do not index `reports/` or future exports as projects.

Delete superseded private parsers only after every caller imports the shared implementation.

- [ ] **Step 5: Run focused and baseline read tests**

```powershell
python -m pytest tests/test_gui.py::TasksTest tests/test_gui.py::TimelineTest tests/test_gui.py::ReportTest tests/test_index_store.py tests/test_registry.py tests/test_search_store.py -q
```

Expected: v1 and v2 tests pass.

- [ ] **Step 6: Commit read compatibility**

```powershell
git add tests/fixtures/project-v2.md workeventagent/gui.py workeventagent/index_store.py workeventagent/registry.py workeventagent/search_store.py tests/test_gui.py tests/test_index_store.py tests/test_registry.py tests/test_search_store.py
git commit -m "feat: read project schema v2 across services" -m "Why: Migration is safe only when every existing consumer can read both old and new project documents."
```

---

### Task 4: Make capture and manual writes deterministic on v2

**Files:**
- Modify: `workeventagent/markdown_store.py`
- Modify: `workeventagent/correction_store.py`
- Modify: `workeventagent/gui.py`
- Modify: `tests/test_markdown_store.py`
- Modify: `tests/test_correction_store.py`
- Modify: `tests/test_gui.py`

**Interfaces:**
- Consumes the shared section and Work Map mutation rails.
- Produces v2 output for `init`; keeps `commit`, `create_item`, `create_task`, `update_item`, `update_task`, `delete_item`, `delete_task`, and correction compatible with both schemas.

- [ ] **Step 1: Add v2 write contract tests**

Add tests for these exact behaviors:

```python
from workeventagent.models import ArchiveProposal, TargetRef, TimelineEvent


def make_v2_project_and_proposal(tmp_path: Path) -> tuple[Path, ArchiveProposal]:
    project = tmp_path / "project-v2.md"
    project.write_text(
        Path("tests/fixtures/project-v2.md").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    proposal = ArchiveProposal(
        target=TargetRef(
            project_id="report-project",
            item_id="capture",
            task_id="persist-card",
            task_title="Persist card",
        ),
        confidence=1.0,
        reason="v2 write contract",
        event=TimelineEvent(
            event_id="20260713-120000000-persist-card",
            task_id="persist-card",
            input_text="Finished persistence.",
            summary="Persistence is complete.",
            status="done",
            next_action="Add retry.",
        ),
    )
    return project, proposal


def test_init_writes_v2_human_readable_document(tmp_path: Path) -> None:
    result = handle_init({
        "workspace": str(tmp_path),
        "db_path": str(tmp_path / "index.sqlite"),
        "title": "Demo",
        "project_id": "demo",
        "status": "active",
        "phase": "planning",
        "items": [{"title": "Capture", "tasks": ["Persist card"]}],
    })
    text = Path(result["project_path"]).read_text(encoding="utf-8")
    assert "schema_version: 2" in text
    assert "## 工作地图 <!-- section:work-map -->" in text
    assert "#### [ ] 任务：Persist card" in text
    assert "- status: in_progress" not in text


def test_v2_capture_updates_only_target_task_and_appends_timeline(tmp_path: Path) -> None:
    project, proposal = make_v2_project_and_proposal(tmp_path)
    before = project.read_text(encoding="utf-8")
    updated = ProjectDocument.from_text(before).apply_proposal(proposal, "2026-07-13")
    assert "#### [x] 任务：Persist card" in updated
    assert "<!-- event:" + proposal.event.event_id + " -->" in updated
    assert section_content(before, "decisions") == section_content(updated, "decisions")
```

Add correction tests proving the original v2 event remains present and only a new correction event is appended.

- [ ] **Step 2: Run the tests to verify red**

```powershell
python -m pytest tests/test_markdown_store.py tests/test_correction_store.py tests/test_gui.py -k "v2 or init_writes_v2" -q
```

Expected: failures at legacy heading/task metadata assumptions.

- [ ] **Step 3: Route writes through shared helpers**

Make these exact changes:

- `ProjectDocument._replace_task_block` calls `update_task_state(text, task_id, status, next_action, last_event_id)`.
- `_append_timeline` inserts after `find_section(text, "timeline").content_start`.
- `append_attachments` inserts after `find_section(text, "attachments").content_start`.
- GUI CRUD delegates item/task rendering and block boundaries to `work_map_store`; no duplicate language-specific heading regex remains in `gui.py`.
- `correction_store` appends to stable Timeline section and delegates task state changes to `work_map_store`.
- `_generate_init_markdown` becomes `render_new_project_v2` and requires explicit `status` and `phase` values supplied by the client. Reject blank values with `invalid_input`.

The v2 profile template must be deterministic:

```markdown
## 项目档案 <!-- section:project-profile -->

### 背景

### 目标

### 范围

### 成功标准
```

All nine anchored sections must be present in the approved order.

- [ ] **Step 4: Verify sibling and append-only preservation**

Run:

```powershell
python -m pytest tests/test_markdown_store.py tests/test_correction_store.py tests/test_gui.py::CommitTest tests/test_gui.py::ManualCreateTest tests/test_gui.py::DeleteItemTest tests/test_gui.py::DeleteTaskTest tests/test_gui.py::UpdateItemTest tests/test_gui.py::UpdateTaskTest -q
```

Expected: all v1 regressions and new v2 tests pass.

- [ ] **Step 5: Commit deterministic v2 writes**

```powershell
git add workeventagent/markdown_store.py workeventagent/correction_store.py workeventagent/gui.py workeventagent/work_map_store.py tests/test_markdown_store.py tests/test_correction_store.py tests/test_gui.py
git commit -m "feat: write human-readable project schema v2" -m "Why: Capture and manual edits must preserve the existing trust model while rendering readable v2 Markdown."
```

---

### Task 5: Add previewable and recoverable v1 migration

**Files:**
- Create: `workeventagent/project_migration.py`
- Create: `tests/test_project_migration.py`
- Modify: `workeventagent/gui.py`

**Interfaces:**
- Produces `preview_v1_to_v2(text, status, phase) -> MigrationPreview` and `apply_v1_to_v2(project_path, db_path, source_hash, status, phase, now) -> dict`.
- Adds GUI commands `project_migration_preview` and `project_migration_apply`.
- The client never submits generated Markdown; apply re-reads and re-generates from the current source.

- [ ] **Step 1: Write golden migration tests**

Create tests covering success, stale source, unsafe source, idempotency, and rollback:

```python
from datetime import datetime, timezone


def fixed_now() -> datetime:
    return datetime(2026, 7, 13, 12, 0, 0, tzinfo=timezone.utc)


def write_v1_fixture(tmp_path: Path) -> Path:
    project = tmp_path / "multimodal-labeling.md"
    project.write_text(
        Path("tests/fixtures/multimodal-labeling.md").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return project


def test_preview_preserves_identity_and_unknown_content() -> None:
    source = Path("tests/fixtures/multimodal-labeling.md").read_text(encoding="utf-8")
    source += "\n## Custom Notes\n\nkeep-this-byte-for-byte\n"
    preview = preview_v1_to_v2(source, status="active", phase="delivery")
    assert preview.source_schema == 1
    assert preview.target_schema == 2
    assert "schema_version: 2" in preview.migrated_text
    assert "keep-this-byte-for-byte" in preview.migrated_text
    assert preview.before_identity == preview.after_identity
    assert preview.diff.startswith("--- ")


def test_apply_rejects_stale_source_without_backup_or_write(tmp_path: Path) -> None:
    project = write_v1_fixture(tmp_path)
    original = project.read_text(encoding="utf-8")
    result = apply_v1_to_v2(
        project,
        tmp_path / "index.sqlite",
        source_hash="sha256:stale",
        status="active",
        phase="delivery",
        now=fixed_now(),
    )
    assert result["kind"] == "stale_source"
    assert project.read_text(encoding="utf-8") == original
    assert not (tmp_path / ".workeventagent" / "backups").exists()


def test_apply_writes_backup_then_verified_v2(tmp_path: Path) -> None:
    project = write_v1_fixture(tmp_path)
    preview = preview_v1_to_v2(project.read_text(encoding="utf-8"), "active", "delivery")
    result = apply_v1_to_v2(project, tmp_path / "index.sqlite", preview.source_hash, "active", "delivery", fixed_now())
    assert result["ok"] is True
    assert Path(result["backup_path"]).read_text(encoding="utf-8") == preview.original_text
    assert schema_version(project.read_text(encoding="utf-8")) == 2
```

Also monkeypatch `os.replace` to fail and assert the original project remains untouched and the backup remains readable.

- [ ] **Step 2: Run migration tests to verify red**

```powershell
python -m pytest tests/test_project_migration.py -q
```

Expected: module import fails.

- [ ] **Step 3: Implement pure preview and identity verification**

Use these exact dataclasses:

```python
@dataclass(frozen=True)
class IdentityManifest:
    project_id: str
    item_ids: tuple[str, ...]
    task_ids: tuple[str, ...]
    event_ids: tuple[str, ...]
    timeline_event_count: int


@dataclass(frozen=True)
class MigrationPreview:
    original_text: str
    migrated_text: str
    source_hash: str
    source_schema: int
    target_schema: int
    before_identity: IdentityManifest
    after_identity: IdentityManifest
    diff: str
    summary: dict[str, int | str]
```

Transformation rules:

1. Require v1 frontmatter and all six v1 required sections.
2. Require explicit nonblank `status` and `phase`; never infer them from tasks.
3. Add `schema_version`, `status`, and `phase` to frontmatter while preserving all existing frontmatter keys.
4. Insert empty Project Profile, Technical Overview, and Project Knowledge sections.
5. Convert Current Snapshot to Current Panorama and map the remaining legacy headings to anchored Chinese headings.
6. Convert only canonical Work Map item/task blocks; preserve item background as prose and preserve unknown prose within canonical blocks.
7. Copy Decisions, Attachments, Timeline, Rollups, unknown sections, and inter-section text without content rewrites.
8. Compare ordered ID manifests and Timeline event counts before returning a preview.
9. Produce `difflib.unified_diff` with `fromfile="schema-v1"` and `tofile="schema-v2"`.

- [ ] **Step 4: Implement guarded apply and GUI handlers**

`handle_project_migration_preview` returns:

```json
{"ok":true,"migration":{"source_schema":1,"target_schema":2,"source_hash":"sha256:...","diff":"...","summary":{"items":2,"tasks":4,"events":9},"status":"active","phase":"delivery"}}
```

`handle_project_migration_apply` must:

1. read the current file;
2. reject a source-hash mismatch before creating backup state;
3. recompute and verify the preview;
4. write `.workeventagent/backups/<project_id>/<YYYYMMDD-HHmmss>.md`;
5. atomically replace the project;
6. read back and verify the identity manifest;
7. restore the backup atomically if read-back verification fails;
8. rebuild SQLite;
9. return `backup_path`, `project_path`, and `schema_version: 2`.

- [ ] **Step 5: Run migration and full persistence regressions**

```powershell
python -m pytest tests/test_project_migration.py tests/test_markdown_store.py tests/test_index_store.py tests/test_correction_store.py -q
```

Expected: all pass.

- [ ] **Step 6: Commit migration**

```powershell
git add workeventagent/project_migration.py workeventagent/gui.py tests/test_project_migration.py
git commit -m "feat: add guarded project schema migration" -m "Why: Existing project memory must move to the readable schema only through a previewed, backed-up, identity-preserving operation."
```

---

### Task 6: Add typed panorama read and reviewed-section edits

**Files:**
- Modify: `workeventagent/project_schema.py`
- Modify: `workeventagent/gui.py`
- Modify: `tests/test_gui.py`

**Interfaces:**
- Adds GUI command `project_panorama`.
- Adds GUI commands `update_project_profile` and `update_project_section`.
- Produces typed section ownership, content, hashes, and source-event IDs; never returns raw control comments for display.

- [ ] **Step 1: Add failing handler tests**

```python
def write_v2_fixture(tmp_path: Path) -> Path:
    project = tmp_path / "project-v2.md"
    project.write_text(
        Path("tests/fixtures/project-v2.md").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return project


def test_project_panorama_returns_owned_sections_and_hashes(tmp_path: Path) -> None:
    project = write_v2_fixture(tmp_path)
    result = handle_project_panorama({"project_path": str(project)})
    assert result["ok"] is True
    assert result["schema_version"] == 2
    assert result["project"]["status"] == "active"
    assert result["sections"]["project-profile"]["ownership"] == "reviewed"
    assert result["sections"]["timeline"]["ownership"] == "append-only"
    assert result["sections"]["technical-overview"]["hash"].startswith("sha256:")


def test_reviewed_edit_rejects_stale_hash_without_write(tmp_path: Path) -> None:
    project = write_v2_fixture(tmp_path)
    before = project.read_text(encoding="utf-8")
    result = handle_update_project_section({
        "project_path": str(project),
        "db_path": str(tmp_path / "index.sqlite"),
        "section_id": "technical-overview",
        "base_section_hash": "sha256:stale",
        "content": "Python 负责写入。",
    })
    assert result["kind"] == "stale_section"
    assert project.read_text(encoding="utf-8") == before


def test_profile_edit_updates_explicit_metadata_and_fixed_subsections(tmp_path: Path) -> None:
    project = write_v2_fixture(tmp_path)
    current = handle_project_panorama({"project_path": str(project)})
    result = handle_update_project_profile({
        "project_path": str(project),
        "db_path": str(tmp_path / "index.sqlite"),
        "base_section_hash": current["sections"]["project-profile"]["hash"],
        "base_metadata_hash": current["project"]["metadata_hash"],
        "status": "active",
        "phase": "implementation",
        "background": "信息散落。",
        "goal": "形成可信项目全景。",
        "scope": "本地优先。",
        "success_criteria": "单文档可读。",
    })
    assert result["ok"] is True
    text = project.read_text(encoding="utf-8")
    assert "phase: implementation" in text
    assert "### 成功标准\n单文档可读。" in text
```

- [ ] **Step 2: Run tests to verify red**

```powershell
python -m pytest tests/test_gui.py -k "project_panorama or reviewed_edit or profile_edit" -q
```

Expected: handlers are missing.

- [ ] **Step 3: Implement the read payload**

`handle_project_panorama` returns `migration_required: true` plus project metadata for v1. For v2 it returns:

```json
{
  "ok": true,
  "schema_version": 2,
  "migration_required": false,
  "project": {
    "project_id": "demo",
    "title": "Demo",
    "status": "active",
    "phase": "implementation",
    "updated": "2026-07-13",
    "metadata_hash": "sha256:..."
  },
  "sections": {
    "project-profile": {"title":"项目档案","ownership":"reviewed","content":"...","hash":"sha256:...","source_event_ids":[]},
    "current-panorama": {"title":"当前全景","ownership":"derived-reviewed","content":"...","hash":"sha256:...","source_event_ids":[]}
  }
}
```

Strip `panorama-meta` and other control comments from visible `content`, but parse `source_events=` into `source_event_ids`.

- [ ] **Step 4: Implement hash-guarded writes**

- `update_project_section` allows only `technical-overview` and `project-knowledge`, validates `base_section_hash`, validates reviewed content, bumps `updated`, atomically writes, reparses, and rebuilds SQLite.
- `update_project_profile` validates both section and metadata hashes, renders the four fixed `###` subsections from typed fields, updates explicit `status`/`phase`, bumps `updated`, atomically writes, reparses, and rebuilds SQLite.
- Attempts to write `current-panorama`, `work-map`, `decisions`, `attachments`, `timeline`, or `rollups` through these handlers return `invalid_operation`.

- [ ] **Step 5: Run handler and stale-write tests**

```powershell
python -m pytest tests/test_gui.py -k "project_panorama or project_section or project_profile" -q
```

Expected: all pass.

- [ ] **Step 6: Commit panorama backend**

```powershell
git add workeventagent/project_schema.py workeventagent/gui.py tests/test_gui.py
git commit -m "feat: expose governed project panorama edits" -m "Why: The client needs typed ownership and stale-write protection instead of direct Markdown editing."
```

---

### Task 7: Add IPC and a pure panorama renderer

**Files:**
- Create: `client/windows/project-panorama.js`
- Create: `tests/test_project_panorama_renderer.py`
- Modify: `client/main.js`
- Modify: `client/preload.js`

**Interfaces:**
- Adds preload methods `getProjectPanorama`, `previewProjectMigration`, `applyProjectMigration`, `updateProjectProfile`, and `updateProjectSection`.
- Produces `globalThis.ProjectPanorama.render(data, workMapHtml)` and `globalThis.ProjectPanorama.renderReviewedContent(content)`.

- [ ] **Step 1: Write renderer and IPC tests**

Create Node-backed tests that render a fixture and assert:

```python
import json
import subprocess


def render_panorama_fixture(technical: str = "Electron 调度 Python。") -> str:
    data = {
        "project": {"project_id": "demo", "title": "Demo", "status": "active", "phase": "build"},
        "sections": {
            "project-profile": {"title": "项目档案", "ownership": "reviewed", "content": "### 背景\n信息散落。", "source_event_ids": []},
            "current-panorama": {"title": "当前全景", "ownership": "derived-reviewed", "content": "正在构建。", "source_event_ids": ["event-a"]},
            "technical-overview": {"title": "技术概览", "ownership": "reviewed", "content": technical, "source_event_ids": []},
            "project-knowledge": {"title": "关键认知", "ownership": "reviewed", "content": "- Markdown 是真相源。", "source_event_ids": []},
            "decisions": {"title": "关键决策", "ownership": "append-only", "content": "- 使用稳定锚点。", "source_event_ids": []},
            "attachments": {"title": "附件", "ownership": "append-only", "content": "", "source_event_ids": []},
            "timeline": {"title": "事件证据", "ownership": "append-only", "content": "<!-- section:timeline -->", "source_event_ids": []},
            "rollups": {"title": "历史摘要", "ownership": "derived", "content": "", "source_event_ids": []},
        },
    }
    script = (
        "const fs=require('fs');const vm=require('vm');"
        "vm.runInThisContext(fs.readFileSync('client/windows/project-panorama.js','utf8'));"
        f"const data={json.dumps(data, ensure_ascii=False)};"
        "process.stdout.write(ProjectPanorama.render(data,'<section class=\"item-group\">工作地图</section>'));"
    )
    return subprocess.run(["node", "-e", script], check=True, text=True, capture_output=True).stdout


def test_panorama_renderer_orders_sections_and_hides_control_metadata() -> None:
    rendered = render_panorama_fixture()
    assert rendered.index("项目档案") < rendered.index("当前全景") < rendered.index("工作地图")
    assert rendered.index("工作地图") < rendered.index("技术概览") < rendered.index("关键认知")
    assert "panorama-meta" not in rendered
    assert "section:timeline" not in rendered
    assert "data-section-id=\"timeline\"" in rendered
    assert "<details" in rendered


def test_panorama_renderer_escapes_user_content() -> None:
    rendered = render_panorama_fixture(technical="<script>alert(1)</script>")
    assert "&lt;script&gt;" in rendered
    assert "<script>alert" not in rendered


def test_ipc_exposes_only_typed_panorama_operations() -> None:
    main = Path("client/main.js").read_text(encoding="utf-8")
    preload = Path("client/preload.js").read_text(encoding="utf-8")
    for channel in ("projectPanorama", "previewProjectMigration", "applyProjectMigration", "updateProjectProfile", "updateProjectSection"):
        assert channel in main
        assert channel in preload
    assert "writeProjectMarkdown" not in preload
```

- [ ] **Step 2: Run tests to verify red**

```powershell
python -m pytest tests/test_project_panorama_renderer.py -q
```

Expected: renderer and IPC methods are absent.

- [ ] **Step 3: Implement pure rendering**

`project-panorama.js` must:

- escape every user-provided string;
- render Project Profile and Current Panorama first;
- insert the already-escaped `workMapHtml` only in the Work Map slot;
- render ownership badges (`需审阅`, `派生`, `结构化`, `只追加`);
- emit edit buttons only for `project-profile`, `technical-overview`, and `project-knowledge`;
- emit source buttons for `reviewed` and `derived-reviewed` sections;
- render Timeline and Rollups inside closed `<details>` elements;
- render empty reviewed sections with an explicit `尚未填写` state.

Expose only pure functions through `Object.freeze`.

- [ ] **Step 4: Add IPC adapters**

Use backend command mapping:

```javascript
ipcMain.handle('wea:projectPanorama', (_e, { projectPath }) =>
  callBackend('project_panorama', { project_path: projectPath }, cfg().pythonCmd));
ipcMain.handle('wea:previewProjectMigration', (_e, request) =>
  callBackend('project_migration_preview', {
    project_path: request.projectPath,
    status: request.status,
    phase: request.phase,
  }, cfg().pythonCmd));
```

`applyProjectMigration`, `updateProjectProfile`, and `updateProjectSection` inject `db_path` from config in the main process. Renderers never receive a writable DB path.

- [ ] **Step 5: Run renderer, IPC, and syntax checks**

```powershell
python -m pytest tests/test_project_panorama_renderer.py tests/test_main_renderer_static.py -q
node --check client/windows/project-panorama.js
node --check client/main.js
node --check client/preload.js
```

Expected: all pass.

- [ ] **Step 6: Commit renderer and bridge**

```powershell
git add client/windows/project-panorama.js client/main.js client/preload.js tests/test_project_panorama_renderer.py
git commit -m "feat: bridge and render project panorama" -m "Why: A pure renderer and narrow IPC keep project reading testable without exposing filesystem writes to the browser."
```

---

### Task 8: Integrate panorama reading, migration, and manual editing

**Files:**
- Modify: `client/windows/main.html`
- Modify: `client/windows/main.css`
- Modify: `client/windows/main.js`
- Modify: `tests/test_project_panorama_renderer.py`
- Modify: `tests/test_main_renderer_static.py`

**Interfaces:**
- Consumes `ProjectPanorama.render`, `WorkMap.render`, and Task 7 preload methods.
- Produces the default Project Panorama view, v1 migration preview/apply modal, profile editor, narrative section editor, source viewer, and stale-write recovery.

- [ ] **Step 1: Add failing integration guards**

```python
def test_main_window_uses_project_panorama_as_default_surface() -> None:
    html = Path("client/windows/main.html").read_text(encoding="utf-8")
    source = Path("client/windows/main.js").read_text(encoding="utf-8")
    assert 'data-view="tasks">项目全景<' in html
    assert '<script src="project-panorama.js"></script>' in html
    refresh = source[source.index("async function refreshCurrent"):source.index("function switchView")]
    assert "wea.getProjectPanorama(path)" in refresh
    assert "ProjectPanorama.render(state.panoramaData, workMapHtml)" in source
    assert "wea.previewProjectMigration" in source
    assert "wea.applyProjectMigration" in source


def test_reviewed_section_editors_send_base_hashes() -> None:
    source = Path("client/windows/main.js").read_text(encoding="utf-8")
    assert "base_section_hash" in source
    assert "base_metadata_hash" in source
    assert "stale_section" in source
    assert "stale_metadata" in source
```

- [ ] **Step 2: Run guards to verify red**

```powershell
python -m pytest tests/test_project_panorama_renderer.py tests/test_main_renderer_static.py -q
```

Expected: new integration tests fail.

- [ ] **Step 3: Add accessible modal markup**

Add three application modals, never native `prompt`/`confirm`:

1. `#migration-modal`: explicit status, phase, preview button, read-only diff, apply/cancel.
2. `#project-profile-modal`: status, phase, background, goal, scope, success criteria, save/cancel.
3. `#project-section-modal`: section title, content textarea, save/cancel.

Each modal has an inline error region, disables its action button during writes, supports Escape cancel, and restores focus to the invoking button.

- [ ] **Step 4: Load and render both schemas**

Change `refreshCurrent()` to fetch tasks and panorama concurrently:

```javascript
async function refreshCurrent() {
  if (!state.currentProject) return;
  const path = state.currentProject.path;
  const [tasks, panorama] = await Promise.all([
    wea.listTasks(path),
    wea.getProjectPanorama(path),
  ]);
  state.tasksData = tasks && tasks.ok ? tasks : { items: [] };
  state.panoramaData = panorama && panorama.ok ? panorama : null;
  renderProjectPanorama();
  const fresh = await wea.listProjects();
  if (fresh && fresh.ok) { state.projects = fresh.projects; renderProjectList(state.projects); }
  renderActionSummary();
}
```

For v1, `renderProjectPanorama()` renders the current Work Map plus a visible migration card; it must not block capture or existing task actions. For v2, it passes `WorkMap.render(items)` into the pure panorama renderer, then binds Work Map, edit, source, and migration actions.

- [ ] **Step 5: Implement preview/apply flow**

- Preview requires nonblank status and phase.
- Apply uses the exact `source_hash`, status, and phase returned by the preview.
- On `stale_source`, keep the original project visible, clear the old preview, and require a fresh preview.
- On success, close the modal, reload projects and panorama, and show the backup path in a success toast.
- Migration UI never sends migrated Markdown.

- [ ] **Step 6: Implement reviewed-section editing**

- Profile editor sends the current section and metadata hashes plus six typed fields.
- Technical Overview and Project Knowledge editor sends the current section hash and content.
- On `stale_section` or `stale_metadata`, do not overwrite; reload panorama and show `内容已变化，请基于最新版本重新编辑`.
- Source viewer lists source event IDs. When none exist in Phase A, show `暂无已记录来源；自动证据综合将在 Phase B 提供`.

- [ ] **Step 7: Style reading hierarchy and ownership**

The visual order must make the project narrative primary and the Work Map interactive:

- Project Profile and Current Panorama use full-width cards.
- Work Map keeps F004 checkbox density and action controls.
- Technical Overview, Project Knowledge, and Decisions follow in readable cards.
- Ownership badges are visually distinct but subordinate to headings.
- Timeline and Rollups are collapsed by default.
- Control metadata and stable anchors are never visible.
- At widths below 900px, cards remain one column and existing Today behavior remains usable.

- [ ] **Step 8: Update project creation inputs**

Add explicit `status` and `phase` fields to the init modal. Send them through `wea.initProject`; do not infer either field from initial tasks.

- [ ] **Step 9: Run focused renderer checks**

```powershell
python -m pytest tests/test_project_panorama_renderer.py tests/test_work_map_renderer.py tests/test_main_renderer_static.py -q
node --check client/windows/project-panorama.js
node --check client/windows/main.js
```

Expected: all pass.

- [ ] **Step 10: Commit the client integration**

```powershell
git add client/windows/main.html client/windows/main.css client/windows/main.js tests/test_project_panorama_renderer.py tests/test_main_renderer_static.py
git commit -m "feat: make project panorama the default client view" -m "Why: One project surface should explain the whole project while preserving direct Work Map actions and explicit ownership."
```

---

### Task 9: Compatibility audit, documentation, and runtime acceptance

**Files:**
- Modify: `docs/designs/F001-client-architecture.md`
- Modify: `client/README.md`
- Modify: `docs/designs/F007-project-panorama.md`
- Verify: all source and test files touched by Tasks 1–8.

**Interfaces:**
- Consumes the complete Phase A implementation.
- Produces reviewable test, migration, and Electron runtime evidence; does not begin Phase B.

- [ ] **Step 1: Update durable contracts**

Document the three new backend command families in F001 client architecture:

- `project_panorama` read;
- `update_project_profile` / `update_project_section` reviewed writes;
- `project_migration_preview` / `project_migration_apply` explicit migration.

Update the client README with v1 migration steps, backup location, section ownership meanings, and the rule that Timeline remains report/search/correction evidence.

- [ ] **Step 2: Run the complete Python suite**

```powershell
python -m pytest -q
```

Expected: at least the 180-test baseline plus all F007 Phase A tests pass with zero failures.

- [ ] **Step 3: Syntax-check every client JavaScript file**

```powershell
Get-ChildItem client -Recurse -Filter *.js | ForEach-Object { node --check $_.FullName }
```

Expected: every command exits 0.

- [ ] **Step 4: Run the focused trust-boundary suite**

```powershell
python -m pytest tests/test_project_schema.py tests/test_work_map_store.py tests/test_project_migration.py tests/test_project_panorama_renderer.py tests/test_markdown_store.py tests/test_index_store.py tests/test_correction_store.py tests/test_search_store.py tests/test_registry.py tests/test_work_map_renderer.py -q
```

Expected: zero failures.

- [ ] **Step 5: Run isolated Electron acceptance**

Create a temporary workspace containing one untouched v1 project and one native v2 project. Launch from the feature worktree:

```powershell
Set-Location client
npm.cmd start
```

Verify and capture evidence:

1. v1 project remains readable, Work Map actions and capture still work, and migration is offered rather than forced.
2. Migration preview shows status/phase and a complete diff; cancel changes no files.
3. Apply creates the timestamped backup and reloads the same IDs/tasks/events in v2.
4. Project Profile and Current Panorama lead the v2 page; Work Map remains interactive.
5. Profile, Technical Overview, and Project Knowledge edits persist through application modals.
6. A simulated stale hash is rejected without overwrite.
7. Timeline and Rollups are collapsed by default and can be opened.
8. Capture, reports, search, correction, index rebuild, settings, project creation, Inbox, and Today remain reachable.

Capture four screenshots: v1 migration card, migration diff, v2 default panorama, and a stale-edit error.

- [ ] **Step 6: Verify no Phase B/C scope leaked**

Run:

```powershell
rg -n "run_archivist|run_reporter|generate_compendium|project_compendium" workeventagent/project_schema.py workeventagent/work_map_store.py workeventagent/project_migration.py client/windows/project-panorama.js
```

Expected: no matches. Phase A contains no synthesis or compendium generation.

- [ ] **Step 7: Check diff quality**

```powershell
git diff --check
git status --short
git diff --stat master...HEAD
```

Expected: no whitespace errors, no tracked runtime data, no `node_modules`, and only F007 Phase A files.

- [ ] **Step 8: Commit documentation and evidence-ready state**

```powershell
git add docs/designs/F001-client-architecture.md docs/designs/F007-project-panorama.md client/README.md
git commit -m "docs: describe F007 project document v2" -m "Why: Migration, ownership, and compatibility behavior must remain reviewable after implementation details leave working memory."
```

## Self-Review Checklist

- [x] Every Phase A acceptance criterion maps to a task and test or runtime check.
- [x] New projects use v2; old projects remain usable before explicit migration.
- [x] Visible headings are not parser keys.
- [x] One shared Work Map implementation serves read, write, index, search, report, and correction paths.
- [x] Migration requires explicit status/phase, source hash, preview, backup, atomic write, and identity verification.
- [x] No client-submitted migrated Markdown is trusted.
- [x] Reviewed writes require current hashes and cannot target append-only/structured sections.
- [x] Timeline remains in-document and append-only.
- [x] The client hides control metadata and exposes ownership/source affordances.
- [x] Phase A includes no LLM synthesis and no Phase C compendium implementation.
- [x] No new dependency, task field, task state, or second truth source is introduced.

## Execution Handoff

The repository's confirmed lane assignment overrides the generic execution-choice prompt: 砚砚 performs independent spec/plan review first; after PASS, 金哥 implements Tasks 1–9 with TDD and compact per-task summaries; 砚砚 performs the independent code review and final audit. Phase B planning begins only after Phase A migration and reading-interface runtime acceptance.
