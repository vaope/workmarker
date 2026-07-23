# Task Completion and Follow-up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make task completion atomically persist a required conclusion and optionally create the next task in the same work item.

**Architecture:** Extend the dual-schema Work Map grammar with a backward-compatible `conclusion` field, then add a focused Python application service that computes completion and optional insertion before one atomic Markdown write. Expose that service through one bounded Electron IPC method and keep the inline completion controller in a separate renderer module so the already-large `main.js` does not absorb the workflow.

**Tech Stack:** Python 3.11, pytest/unittest, Markdown source documents, SQLite, Electron CommonJS IPC/preload, browser JavaScript, HTML/CSS.

## Global Constraints

- Markdown is the source of truth; SQLite is a rebuildable index.
- Existing schema v1 and v2 documents without `conclusion` remain readable and writable without migration.
- Manual completion requires a non-empty conclusion.
- `next_action` continues to mean how to resume the same in-progress task.
- Follow-up creation always uses the completed task's existing work item.
- Completing or reopening a task must not append or edit Timeline content.
- F009 must not create work items automatically and must not remove the existing manual work-item creation control.
- A failed completion must not leave a partial status/conclusion/follow-up write.
- Use ports other than reserved frontend `3003`, API `3004`, and Redis `6389`; this plan starts no server.

---

## File Structure

### New files

- `workeventagent/task_completion.py` — validates and applies the complete-current/optional-create-next use case.
- `client/windows/task-completion.js` — owns the inline completion editor and async interaction state.
- `tests/test_task_completion.py` — backend atomicity and compatibility tests.
- `tests/test_task_completion_renderer.py` — row rendering, typed bridge, and renderer integration tests.

### Modified files

- `docs/WORKLOG_SCHEMA.md` — canonical v1/v2 conclusion grammar and semantics.
- `workeventagent/work_map_store.py` — parse/render/mutate conclusion and insert a task after another task.
- `workeventagent/gui.py` — register `complete_task`, reject generic completion, preserve task fields on reopen.
- `workeventagent/index_store.py` — additive SQLite column migration and conclusion indexing.
- `workeventagent/search_store.py` — include task conclusions in search.
- `client/main.js` — map typed completion IPC to the Python command.
- `client/preload.js` — expose only `completeTask(...)` to the renderer.
- `client/windows/work-map.js` — show `next_action` for active tasks and conclusion for completed tasks.
- `client/windows/main.js` — delegate checkbox interaction to the controller and remove status from the generic task editor.
- `client/windows/main.html` — load the completion controller before `main.js`.
- `client/windows/main.css` — style conclusion text and the inline editor.
- `tests/test_work_map_store.py` — dual-schema conclusion and insertion tests.
- `tests/test_gui.py` — generic completion rejection and reopen preservation tests.
- `tests/test_index_store.py` — old-database migration and indexed conclusion tests.
- `tests/test_search_store.py` — conclusion search test.
- `tests/test_work_map_renderer.py` — replace the old direct-toggle contract assertions.

---

### Task 1: Extend the Work Map task contract

**Files:**

- Modify: `docs/WORKLOG_SCHEMA.md:74`
- Modify: `workeventagent/work_map_store.py:18`
- Test: `tests/test_work_map_store.py`

**Interfaces:**

- Consumes: existing `parse_work_map(text)`, `_mutate_task_fields(text, task_id, updates)`, `render_v1_task(task)`, and `render_v2_task(task)`.
- Produces: task dictionaries with `conclusion: str`, `complete_task_block(text, task_id, conclusion) -> str`, and `insert_task_after(text, after_task_id, task) -> str`.

- [ ] **Step 1: Write failing dual-schema conclusion tests**

Add these fixtures and tests to `tests/test_work_map_store.py`:

```python
V1_WITH_CONCLUSION = V1_MAP.replace(
    "- next_action: Add retry.\n",
    "- next_action: Add retry.\n- conclusion: Persistence is stable.\n",
)

V2_WITH_CONCLUSION = V2_MAP.replace(
    "- 下一步：Add retry.\n",
    "- 下一步：Add retry.\n- 结论：Persistence is stable.\n",
)


def test_v1_and_v2_parse_conclusion_to_the_same_state() -> None:
    v1 = parse_work_map(V1_WITH_CONCLUSION)[0]["tasks"][0]
    v2 = parse_work_map(V2_WITH_CONCLUSION)[0]["tasks"][0]
    assert v1["conclusion"] == v2["conclusion"] == "Persistence is stable."


@pytest.mark.parametrize("source", [V1_MAP, V2_MAP])
def test_missing_conclusion_is_backward_compatible(source: str) -> None:
    assert parse_work_map(source)[0]["tasks"][0]["conclusion"] == ""
```

- [ ] **Step 2: Run the parser tests and verify red**

Run:

```powershell
python -m pytest tests/test_work_map_store.py::test_v1_and_v2_parse_conclusion_to_the_same_state tests/test_work_map_store.py::test_missing_conclusion_is_backward_compatible -q
```

Expected: FAIL because parsed tasks do not contain `conclusion`.

- [ ] **Step 3: Add conclusion parsing and rendering**

In `workeventagent/work_map_store.py`, add:

```python
V2_CONCLUSION_RE = re.compile(r"^-\s*结论[：:]\s*(.*)$")
V1_CONCLUSION_RE = re.compile(r"^-\s*conclusion:\s*(.*)")
```

Initialize every parsed task with:

```python
"next_action": "",
"conclusion": "",
"last_event_id": "",
```

Update the existing `test_v1_and_v2_parse_to_the_same_typed_state` expected task dictionary with `"conclusion": ""` so the canonical typed shape is explicit.

Parse the new field next to `next_action`:

```python
if v < 2:
    cm = V1_CONCLUSION_RE.match(line)
    if cm:
        current_task["conclusion"] = cm.group(1).strip()
        continue
else:
    cm2 = V2_CONCLUSION_RE.match(line)
    if cm2:
        current_task["conclusion"] = cm2.group(1).strip()
        continue
```

Replace `render_v2_task` with:

```python
def render_v2_task(task: dict) -> str:
    """Render a single task block in v2 Markdown."""
    checked = "x" if task.get("status") == "done" else " "
    next_action = str(task.get("next_action", "")).replace("\n", " ").strip()
    conclusion = str(task.get("conclusion", "")).replace("\n", " ").strip()
    last_event = str(task.get("last_event_id", "")).strip()
    return (
        f"#### [{checked}] 任务：{task['title']} <!-- task:{task['task_id']} -->\n"
        f"- 下一步：{next_action}\n"
        f"- 结论：{conclusion}\n"
        f"<!-- task-meta:last_event_id={last_event} -->"
    )
```

Replace `render_v1_task` with:

```python
def render_v1_task(task: dict) -> str:
    """Render a single task block in v1 Markdown."""
    return "\n".join([
        f"#### Task: {task['title']} <!-- task:{task['task_id']} -->",
        f"- status: {task.get('status', 'in_progress')}",
        f"- next_action: {task.get('next_action', '')}",
        f"- conclusion: {task.get('conclusion', '')}",
        f"- last_event_id: {task.get('last_event_id', '')}",
    ])
```

Extend the mutation helpers:

```python
if field == "conclusion":
    match = (V2_CONCLUSION_RE if schema_ver >= 2 else V1_CONCLUSION_RE).match(content)
    return (match, 1) if match else None
```

```python
if field in {"next_action", "conclusion"}:
    return rendered.replace("\n", " ").strip()
```

```python
if schema_ver >= 2 and field == "conclusion":
    return f"- 结论：{value}"
if schema_ver < 2 and field == "conclusion":
    return f"- conclusion: {value}"
```

Use these control orders:

```python
def _task_control_order(schema_ver: int) -> tuple[str, ...]:
    if schema_ver >= 2:
        return ("next_action", "conclusion", "last_event_id")
    return ("status", "next_action", "conclusion", "last_event_id")
```

- [ ] **Step 4: Run the parser tests and verify green**

Run:

```powershell
python -m pytest tests/test_work_map_store.py::test_v1_and_v2_parse_conclusion_to_the_same_state tests/test_work_map_store.py::test_missing_conclusion_is_backward_compatible -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Write failing completion and insertion tests**

Update the imports:

```python
from workeventagent.work_map_store import (
    complete_task_block,
    insert_task_after,
    parse_work_map,
    update_task_field,
    update_task_state,
)
```

Add:

```python
@pytest.mark.parametrize("source", [V1_MAP, V2_MAP])
def test_complete_task_block_preserves_resume_and_event_fields(source: str) -> None:
    updated = complete_task_block(source, "persist-card", "Persistence is stable.")
    task = parse_work_map(updated)[0]["tasks"][0]
    assert task == {
        "task_id": "persist-card",
        "title": "Persist card",
        "status": "done",
        "next_action": "Add retry.",
        "conclusion": "Persistence is stable.",
        "last_event_id": "event-a",
    }


def test_insert_task_after_keeps_same_item_and_following_item() -> None:
    new_task = {
        "task_id": "follow-up",
        "title": "Follow up",
        "status": "in_progress",
        "next_action": "",
        "conclusion": "",
        "last_event_id": "",
    }
    updated = insert_task_after(V2_MULTI_ITEM_MAP, "a-task", new_task)
    alpha = parse_work_map(updated)[0]
    assert [task["task_id"] for task in alpha["tasks"]] == ["a-task", "follow-up"]
    assert "<!-- item:beta -->" in updated
    assert "<!-- task:b-task -->" in updated
```

- [ ] **Step 6: Run the mutation tests and verify red**

Run:

```powershell
python -m pytest tests/test_work_map_store.py::test_complete_task_block_preserves_resume_and_event_fields tests/test_work_map_store.py::test_insert_task_after_keeps_same_item_and_following_item -q
```

Expected: collection FAIL because the two functions do not exist.

- [ ] **Step 7: Implement focused Work Map primitives**

Add below `update_task_state`:

```python
def complete_task_block(text: str, task_id: str, conclusion: str) -> str:
    """Set done + conclusion without overwriting unrelated task fields."""
    normalized = str(conclusion).replace("\n", " ").strip()
    if not normalized:
        raise ValueError("completion conclusion is required")
    return _mutate_task_fields(text, task_id, {
        "status": "done",
        "conclusion": normalized,
    })


def insert_task_after(text: str, after_task_id: str, task: dict) -> str:
    """Insert one rendered task immediately after an anchored task."""
    schema_ver, _, heading_end = _find_task_heading(text, after_task_id)
    insert_pos = _find_next_block_boundary(text, heading_end)
    block = render_v2_task(task) if schema_ver >= 2 else render_v1_task(task)
    before = text[:insert_pos]
    if before.endswith("\n\n"):
        prefix = ""
    elif before.endswith("\n"):
        prefix = "\n"
    else:
        prefix = "\n\n"
    return before + prefix + block + "\n\n" + text[insert_pos:]
```

Ensure `insert_task` initializes the new field:

```python
new_task = {
    "task_id": task_id,
    "title": title,
    "status": "in_progress",
    "next_action": "",
    "conclusion": "",
    "last_event_id": "",
}
```

- [ ] **Step 8: Document the grammar**

In `docs/WORKLOG_SCHEMA.md`, change the v2 example and grammar to:

```markdown
#### [x] 任务：主窗口先写 Inbox <!-- task:main-capture-inbox -->

- 下一步：补充解析完成通知
- 结论：主窗口已经使用持久化 Inbox
<!-- task-meta:last_event_id=20260712-main-capture-inbox -->
```

```text
task       = (task_v2_heading next_action_line conclusion_line task_meta_line)
conclusion_line = "- 结论：" text
```

Add `- conclusion: text` to the v1 `meta` alternatives, and state that missing conclusion lines remain valid legacy input while all newly rendered task blocks include the field.

- [ ] **Step 9: Run the Work Map suite**

Run:

```powershell
python -m pytest tests/test_work_map_store.py -q
```

Expected: all tests pass.

- [ ] **Step 10: Commit the schema unit**

```powershell
git add docs/WORKLOG_SCHEMA.md workeventagent/work_map_store.py tests/test_work_map_store.py
git commit -m "feat: add task conclusion to work map schema" -m "Why: completion results need a durable field distinct from resume-oriented next_action."
```

---

### Task 2: Add the atomic completion application service and bounded command

**Files:**

- Create: `workeventagent/task_completion.py`
- Create: `tests/test_task_completion.py`
- Modify: `workeventagent/gui.py:39`
- Modify: `client/main.js:428`
- Modify: `client/preload.js:18`
- Modify: `tests/test_gui.py:1545`

**Interfaces:**

- Consumes: `complete_task_block`, `insert_task_after`, `parse_work_map`, `make_unique_stable_id`, `write_project_atomically`, `init_db`, and `rebuild_index`.
- Produces: `complete_task(project_path, db_path, task_id, conclusion, next_task_title="") -> dict`, GUI command `complete_task`, and preload method `completeTask(projectPath, taskId, conclusion, nextTaskTitle)`.

- [ ] **Step 1: Write failing backend use-case tests**

Create `tests/test_task_completion.py`:

```python
from pathlib import Path

from workeventagent.index_store import get_task
from workeventagent.task_completion import complete_task
from workeventagent.work_map_store import parse_work_map


PROJECT = """---
project_id: completion
title: Completion
doc_kind: work_project
schema_version: 2
status: active
phase: build
created: 2026-07-23
updated: 2026-07-23
---
## 工作地图 <!-- section:work-map -->
### 工作项：Cache <!-- item:cache -->

Validate cache behavior.

#### [ ] 任务：Verify cache <!-- task:verify-cache -->
- 下一步：Run edge cases.
<!-- task-meta:last_event_id=event-a -->

### 工作项：Reporting <!-- item:reporting -->

#### [ ] 任务：Write report <!-- task:write-report -->
- 下一步：
<!-- task-meta:last_event_id= -->
## 事件证据 <!-- section:timeline -->

- immutable timeline
"""


def setup_project(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "completion.md"
    project.write_text(PROJECT, encoding="utf-8")
    return project, tmp_path / "index.sqlite"


def test_complete_without_follow_up_is_one_current_state_write(tmp_path: Path) -> None:
    project, db = setup_project(tmp_path)
    result = complete_task(project, db, "verify-cache", "Cache behavior is stable.")
    text = project.read_text(encoding="utf-8")
    task = parse_work_map(text)[0]["tasks"][0]
    assert result["ok"] is True
    assert result["new_task"] is None
    assert task["status"] == "done"
    assert task["conclusion"] == "Cache behavior is stable."
    assert task["next_action"] == "Run edge cases."
    assert task["last_event_id"] == "event-a"
    assert text.split("## 事件证据", 1)[1] == PROJECT.split("## 事件证据", 1)[1]
    assert get_task(db, "verify-cache")["status"] == "done"


def test_complete_with_follow_up_inserts_after_current_task(tmp_path: Path) -> None:
    project, db = setup_project(tmp_path)
    result = complete_task(
        project,
        db,
        "verify-cache",
        "Cache behavior is stable.",
        "Document cache behavior",
    )
    items = parse_work_map(project.read_text(encoding="utf-8"))
    assert [task["title"] for task in items[0]["tasks"]] == [
        "Verify cache",
        "Document cache behavior",
    ]
    assert items[1]["tasks"][0]["task_id"] == "write-report"
    assert result["new_task"]["item_id"] == "cache"
    assert get_task(db, result["new_task"]["task_id"])["status"] == "in_progress"


def test_invalid_completion_leaves_file_unchanged(tmp_path: Path) -> None:
    project, db = setup_project(tmp_path)
    before = project.read_bytes()
    result = complete_task(project, db, "verify-cache", "   ")
    assert result["ok"] is False
    assert result["kind"] == "invalid_input"
    assert project.read_bytes() == before
    assert not db.exists()


def test_already_done_completion_does_not_duplicate_follow_up(tmp_path: Path) -> None:
    project, db = setup_project(tmp_path)
    first = complete_task(project, db, "verify-cache", "Stable.", "Document cache")
    before = project.read_bytes()
    second = complete_task(project, db, "verify-cache", "Stable.", "Document cache")
    assert first["ok"] is True
    assert second["ok"] is False
    assert second["kind"] == "invalid_state"
    assert project.read_bytes() == before
```

- [ ] **Step 2: Run the use-case tests and verify red**

Run:

```powershell
python -m pytest tests/test_task_completion.py -q
```

Expected: collection FAIL because `workeventagent.task_completion` does not exist.

- [ ] **Step 3: Implement the application service**

Create `workeventagent/task_completion.py`:

```python
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from workeventagent.ids import make_unique_stable_id
from workeventagent.index_store import init_db, rebuild_index
from workeventagent.markdown_store import write_project_atomically
from workeventagent.work_map_store import (
    complete_task_block,
    insert_task_after,
    parse_work_map,
)


def _one_line(value: str) -> str:
    return " ".join(str(value).split()).strip()


def _bump_updated(text: str) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return re.sub(r"(?m)^(updated:\s*).*$", rf"\g<1>{today}", text, count=1)


def complete_task(
    project_path: Path,
    db_path: Path,
    task_id: str,
    conclusion: str,
    next_task_title: str = "",
) -> dict:
    normalized_conclusion = _one_line(conclusion)
    normalized_next_title = _one_line(next_task_title)
    if not normalized_conclusion:
        return {
            "ok": False,
            "kind": "invalid_input",
            "error": "completion conclusion is required",
        }

    original = project_path.read_text(encoding="utf-8")
    try:
        items = parse_work_map(original, strict=True)
    except ValueError as exc:
        return {"ok": False, "kind": "invalid_project", "error": str(exc)}

    parent_item = None
    target_task = None
    existing_task_ids: list[str] = []
    for item in items:
        for task in item.get("tasks", []):
            existing_task_ids.append(task["task_id"])
            if task["task_id"] == task_id:
                parent_item = item
                target_task = task

    if target_task is None or parent_item is None:
        return {"ok": False, "kind": "not_found", "error": f"task not found: {task_id}"}
    if target_task["status"] == "done":
        return {"ok": False, "kind": "invalid_state", "error": "task is already done"}

    try:
        updated = complete_task_block(original, task_id, normalized_conclusion)
        new_task = None
        if normalized_next_title:
            next_task_id = make_unique_stable_id(normalized_next_title, existing_task_ids)
            task_record = {
                "task_id": next_task_id,
                "title": normalized_next_title,
                "status": "in_progress",
                "next_action": "",
                "conclusion": "",
                "last_event_id": "",
            }
            updated = insert_task_after(updated, task_id, task_record)
            new_task = {
                "item_id": parent_item["item_id"],
                "task_id": next_task_id,
                "title": normalized_next_title,
            }
        updated = _bump_updated(updated)
    except ValueError as exc:
        return {"ok": False, "kind": "invalid_project", "error": str(exc)}

    write_project_atomically(project_path, updated)
    init_db(db_path)
    rebuild_index(db_path, [project_path])
    return {
        "ok": True,
        "task_id": task_id,
        "status": "done",
        "conclusion": normalized_conclusion,
        "new_task": new_task,
    }
```

- [ ] **Step 4: Run the use-case tests and verify green**

Run:

```powershell
python -m pytest tests/test_task_completion.py -q
```

Expected: all four tests pass; Task 3 will add conclusion itself to the indexed row.

- [ ] **Step 5: Write failing GUI contract tests**

In `tests/test_gui.py`, import `handle_complete_task`, then add:

```python
def test_complete_task_command_requires_conclusion(self):
    tmp, ws, db, proj = self._setup()
    try:
        task = handle_tasks({"project_path": str(proj)})["items"][0]["tasks"][0]
        before = proj.read_bytes()
        result = handle_complete_task({
            "project_path": str(proj),
            "db_path": str(db),
            "task_id": task["task_id"],
            "conclusion": " ",
            "next_task_title": "",
        })
        self.assertFalse(result["ok"])
        self.assertEqual(result["kind"], "invalid_input")
        self.assertEqual(proj.read_bytes(), before)
    finally:
        tmp.cleanup()


def test_generic_status_done_requires_completion_command(self):
    tmp, ws, db, proj = self._setup()
    try:
        task = handle_tasks({"project_path": str(proj)})["items"][0]["tasks"][0]
        result = handle_update_task({
            "project_path": str(proj),
            "db_path": str(db),
            "task_id": task["task_id"],
            "field": "status",
            "value": "done",
        })
        self.assertFalse(result["ok"])
        self.assertEqual(result["kind"], "completion_required")
    finally:
        tmp.cleanup()
```

Replace existing tests that complete through `handle_update_task` with:

```python
completed = handle_complete_task({
    "project_path": str(proj),
    "db_path": str(db),
    "task_id": task_id,
    "conclusion": "Completed for reopen test.",
    "next_task_title": "",
})
self.assertTrue(completed["ok"], completed)
result = handle_update_task({
    "project_path": str(proj),
    "db_path": str(db),
    "task_id": task_id,
    "field": "status",
    "value": "in_progress",
})
self.assertTrue(result["ok"], result)
reopened = handle_tasks({"project_path": str(proj)})["items"][0]["tasks"][0]
self.assertEqual(reopened["status"], "in_progress")
self.assertEqual(reopened["conclusion"], "Completed for reopen test.")
```

- [ ] **Step 6: Run the GUI contract tests and verify red**

Run:

```powershell
python -m pytest tests/test_gui.py -k "complete_task_command or generic_status_done or update_task_status or checkbox_status" -q
```

Expected: FAIL because `handle_complete_task` is absent and generic completion is still accepted.

- [ ] **Step 7: Register the command and make generic updates safe**

In `workeventagent/gui.py`, import:

```python
from workeventagent.task_completion import complete_task as complete_task_service
```

Register:

```python
"complete_task": handle_complete_task,
```

Add:

```python
def handle_complete_task(request: dict) -> dict:
    return complete_task_service(
        Path(request["project_path"]),
        Path(request["db_path"]),
        str(request.get("task_id", "")),
        str(request.get("conclusion", "")),
        str(request.get("next_task_title", "")),
    )
```

Update `handle_update_task` validation:

```python
valid_fields = {"status", "title", "next_action", "conclusion"}
if field not in valid_fields:
    return {
        "ok": False,
        "kind": "invalid_input",
        "error": f"field must be one of: {', '.join(sorted(valid_fields))}",
    }
if field == "status" and value == "done":
    return {
        "ok": False,
        "kind": "completion_required",
        "error": "use complete_task to finish a task",
    }
if field == "status" and value != "in_progress":
    return {
        "ok": False,
        "kind": "invalid_input",
        "error": "status update only supports in_progress",
    }
if field == "conclusion" and not str(value).strip():
    return {
        "ok": False,
        "kind": "invalid_input",
        "error": "conclusion must not be empty",
    }
```

Replace `_update_task_attr`'s schema-specific task-field branches with the shared single-field primitive for both schemas:

```python
if field in {"status", "title", "next_action", "conclusion"}:
    updated = wm_update_task_field(text, task_id, field, value)
else:
    raise ValueError(f"unsupported task field: {field}")
return _bump_updated_text(
    updated,
    datetime.now(timezone.utc).strftime("%Y-%m-%d"),
)
```

This delegation is required for legacy v1 blocks because `wm_update_task_field` can insert a missing conclusion control line; the old fixed-window loop silently skipped absent fields.

- [ ] **Step 8: Add the bounded Electron bridge**

In `client/main.js`, add next to `wea:updateTask`:

```javascript
ipcMain.handle('wea:completeTask', async (_e, {
  projectPath, taskId, conclusion, nextTaskTitle,
}) => {
  const c = cfg();
  return callBackend('complete_task', {
    project_path: projectPath,
    db_path: dbPathFor(c.workspace),
    task_id: taskId,
    conclusion,
    next_task_title: nextTaskTitle || '',
  }, c.pythonCmd);
});
```

In `client/preload.js`, expose:

```javascript
completeTask: (projectPath, taskId, conclusion, nextTaskTitle) =>
  ipcRenderer.invoke('wea:completeTask', {
    projectPath,
    taskId,
    conclusion,
    nextTaskTitle: nextTaskTitle || '',
  }),
```

- [ ] **Step 9: Run backend and bridge tests**

Run:

```powershell
python -m pytest tests/test_task_completion.py tests/test_gui.py -k "task_completion or complete_task or update_task_status or checkbox_status" -q
```

Expected: selected tests pass.

- [ ] **Step 10: Commit the atomic command**

```powershell
git add workeventagent/task_completion.py workeventagent/gui.py client/main.js client/preload.js tests/test_task_completion.py tests/test_gui.py
git commit -m "feat: complete tasks atomically with conclusions" -m "Why: status, conclusion, and optional follow-up must never persist as a partial workflow."
```

---

### Task 3: Index and search completion conclusions

**Files:**

- Modify: `workeventagent/index_store.py:52`
- Modify: `workeventagent/search_store.py:42`
- Test: `tests/test_index_store.py`
- Test: `tests/test_search_store.py`

**Interfaces:**

- Consumes: task `conclusion` values parsed from Markdown.
- Produces: additive SQLite `tasks.conclusion` column and task search snippets containing both resume action and completion conclusion.

- [ ] **Step 1: Write a failing existing-database migration test**

Add to `tests/test_index_store.py`:

```python
def test_init_db_adds_conclusion_to_existing_task_table(self):
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "legacy.sqlite"
        conn = sqlite3.connect(db_path)
        conn.execute(
            """CREATE TABLE tasks (
                task_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL DEFAULT '',
                item_id TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT '',
                next_action TEXT NOT NULL DEFAULT '',
                doc_path TEXT NOT NULL DEFAULT '',
                doc_anchor TEXT NOT NULL DEFAULT '',
                last_event_id TEXT NOT NULL DEFAULT ''
            )"""
        )
        conn.commit()
        conn.close()

        init_db(db_path)

        conn = sqlite3.connect(db_path)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
        conn.close()
        self.assertIn("conclusion", columns)
```

- [ ] **Step 2: Write a failing indexed-conclusion test**

Add a temporary v2 project with:

```markdown
#### [x] 任务：Verify cache <!-- task:verify-cache -->
- 下一步：Run edge cases.
- 结论：Prefix reuse is stable.
<!-- task-meta:last_event_id=event-a -->
```

Then assert:

```python
init_db(db_path)
rebuild_index(db_path, [project_path])
self.assertEqual(get_task(db_path, "verify-cache")["conclusion"], "Prefix reuse is stable.")
```

- [ ] **Step 3: Run index tests and verify red**

Run:

```powershell
python -m pytest tests/test_index_store.py -q
```

Expected: FAIL because the column does not exist and rebuild does not write it.

- [ ] **Step 4: Implement additive migration and rebuild support**

Add `conclusion` to the create schema:

```sql
conclusion TEXT NOT NULL DEFAULT '',
```

After `executescript`, add:

```python
task_columns = {
    row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()
}
if "conclusion" not in task_columns:
    conn.execute(
        "ALTER TABLE tasks ADD COLUMN conclusion TEXT NOT NULL DEFAULT ''"
    )
```

Change the task insert:

```python
conn.execute(
    "INSERT OR REPLACE INTO tasks "
    "(task_id, project_id, item_id, title, status, next_action, conclusion, "
    "doc_path, doc_anchor, last_event_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
    (
        task["task_id"],
        project_id,
        task["item_id"],
        task["title"],
        task["status"],
        task["next_action"],
        task.get("conclusion", ""),
        str(project_path),
        task["doc_anchor"],
        task["last_event_id"],
    ),
)
```

Include conclusion in both v2 and v1 parsed task dictionaries. Add:

```python
conclusion_re = re.compile(r"^-\s*conclusion:\s*(.*)$")
```

and parse it into `current_task["conclusion"]`.

- [ ] **Step 5: Run index tests and verify green**

Run:

```powershell
python -m pytest tests/test_index_store.py tests/test_task_completion.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Write a failing conclusion-search test**

Add to `tests/test_search_store.py`:

```python
def test_search_finds_task_conclusion(tmp_path: Path) -> None:
    project = PROJECT.replace(
        "- next_action: Build deterministic search\n",
        "- next_action: Build deterministic search\n"
        "- conclusion: Prefix reuse is stable under concurrency\n",
        1,
    )
    (tmp_path / "project.md").write_text(project, encoding="utf-8")

    results = search_workspace(tmp_path, "stable under concurrency")

    assert results
    assert results[0]["kind"] == "task"
    assert results[0]["task_id"] == "kv-cache-search"
```

- [ ] **Step 7: Run search test and verify red**

Run:

```powershell
python -m pytest tests/test_search_store.py::test_search_finds_task_conclusion -q
```

Expected: FAIL with an empty result.

- [ ] **Step 8: Include conclusion in task search snippets**

In `workeventagent/search_store.py`, build the task snippet with:

```python
"snippet": " ".join(filter(None, [
    task.get("next_action", ""),
    task.get("conclusion", ""),
])),
```

- [ ] **Step 9: Run index and search suites**

Run:

```powershell
python -m pytest tests/test_index_store.py tests/test_search_store.py tests/test_task_completion.py -q
```

Expected: all tests pass.

- [ ] **Step 10: Commit indexing and retrieval**

```powershell
git add workeventagent/index_store.py workeventagent/search_store.py tests/test_index_store.py tests/test_search_store.py
git commit -m "feat: index and search task conclusions" -m "Why: validation results must remain retrievable even when completion does not create a Timeline event."
```

---

### Task 4: Ship the inline completion interaction

**Files:**

- Create: `client/windows/task-completion.js`
- Create: `tests/test_task_completion_renderer.py`
- Modify: `client/windows/work-map.js:19`
- Modify: `client/windows/main.js:392`
- Modify: `client/windows/main.html:402`
- Modify: `client/windows/main.css:162`
- Modify: `tests/test_work_map_renderer.py:63`

**Interfaces:**

- Consumes: `wea.completeTask(...)`, `wea.updateTask(...)`, `refreshCurrent()`, task rows rendered by `WorkMap`.
- Produces: `TaskCompletion.createController(deps)`, completion editor markup, single-flight save, and safe reopen behavior.

- [ ] **Step 1: Write failing row-rendering tests**

Create `tests/test_task_completion_renderer.py`:

```python
import json
import subprocess
from pathlib import Path


def run_node(script: str) -> str:
    return subprocess.run(
        ["node", "-e", script],
        cwd=Path.cwd(),
        check=True,
        text=True,
        encoding="utf-8",
        capture_output=True,
    ).stdout


def test_task_rows_switch_between_resume_action_and_conclusion() -> None:
    script = r"""
const fs = require('fs');
const vm = require('vm');
vm.runInThisContext(fs.readFileSync('client/windows/work-map.js', 'utf8'));
const html = WorkMap.render([{
  item_id: 'cache',
  title: 'Cache',
  tasks: [
    { task_id: 'active', title: 'Active', status: 'in_progress',
      next_action: 'Run tests', conclusion: '' },
    { task_id: 'done', title: 'Done', status: 'done',
      next_action: 'Old action', conclusion: 'Validated safely' }
  ]
}]);
process.stdout.write(html);
"""
    html = run_node(script)
    active = html[html.index('data-task-id="active"'):html.index('data-task-id="done"')]
    done = html[html.index('data-task-id="done"'):]
    assert "task-next" in active
    assert "Run tests" in active
    assert "task-conclusion" not in active
    assert "task-conclusion" in done
    assert "Validated safely" in done
    assert "Old action" not in done


def test_completion_panel_escapes_task_content() -> None:
    script = r"""
const fs = require('fs');
const vm = require('vm');
vm.runInThisContext(fs.readFileSync('client/windows/task-completion.js', 'utf8'));
process.stdout.write(TaskCompletion.panelMarkup({ title: '<script>x</script>' }));
"""
    html = run_node(script)
    assert "&lt;script&gt;" in html
    assert "<script>" not in html
    assert "完成结论" in html
    assert "后续任务" in html


def test_typed_completion_bridge_is_bounded() -> None:
    main = Path("client/main.js").read_text(encoding="utf-8")
    preload = Path("client/preload.js").read_text(encoding="utf-8")
    assert "ipcMain.handle('wea:completeTask'" in main
    assert "callBackend('complete_task'" in main
    assert "completeTask:" in preload
    assert "ipcRenderer.invoke('wea:completeTask'" in preload
```

- [ ] **Step 2: Replace the obsolete renderer contract assertion**

In `tests/test_work_map_renderer.py`, replace:

```python
assert "wea.updateTask(projectPath, task.task_id, 'status', nextStatus)" in source
```

with:

```python
assert '<script src="task-completion.js"></script>' in html
assert "taskCompletion.handleToggle" in source
assert "wea.completeTask" in source
assert "'status', 'in_progress'" in source
assert "'status', 'done'" not in source[source.index("function bindWorkMapActions"):source.index("// ---- inbox view")]
```

- [ ] **Step 3: Run renderer tests and verify red**

Run:

```powershell
python -m pytest tests/test_task_completion_renderer.py tests/test_work_map_renderer.py -q
```

Expected: FAIL because the controller, conclusion row, and new integration do not exist.

- [ ] **Step 4: Render the correct lifecycle field**

In `client/windows/work-map.js`, replace the task metadata expression with:

```javascript
${done
  ? `<span class="task-conclusion">${esc(task.conclusion || '未记录结论')}</span>`
  : (task.next_action ? `<span class="task-next">${esc(task.next_action)}</span>` : '')}
```

Keep all values passed through `esc`.

- [ ] **Step 5: Implement the focused completion controller**

Create `client/windows/task-completion.js`:

```javascript
(function exposeTaskCompletion(root) {
  function esc(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function panelMarkup(task) {
    return `<div class="task-completion-editor task-editor" data-task-id="${esc(task.task_id || '')}">
      <div class="completion-heading">完成「${esc(task.title || '')}」</div>
      <label class="te-row">
        <span>完成结论 <b aria-hidden="true">*</b></span>
        <input class="completion-conclusion" type="text"
          placeholder="这次验证、交付或决策得出了什么结论？" />
      </label>
      <label class="te-row">
        <span>后续任务 <em>（可选）</em></span>
        <input class="completion-next-task" type="text"
          placeholder="需要继续推进时，直接创建一个新任务" />
      </label>
      <div class="completion-error hidden" role="alert"></div>
      <div class="te-acts">
        <button class="ghost small completion-cancel" type="button">取消</button>
        <button class="primary small-btn completion-save" type="button">完成任务</button>
      </div>
    </div>`;
  }

  function createController(deps) {
    const {
      getProjectPath,
      completeTask,
      updateTask,
      refresh,
      notify,
    } = deps;

    function closeEditors() {
      document.querySelectorAll('.task-completion-editor').forEach((editor) => {
        const row = editor.closest('.task-row');
        const checkbox = row && row.querySelector('.task-check');
        if (checkbox) checkbox.disabled = false;
        editor.remove();
      });
    }

    function setBusy(editor, busy) {
      editor.querySelectorAll('input, button').forEach((control) => {
        control.disabled = busy;
      });
    }

    async function reopen(input, task) {
      input.disabled = true;
      try {
        const result = await updateTask(
          getProjectPath(),
          task.task_id,
          'status',
          'in_progress',
        );
        if (!result || !result.ok) {
          input.checked = true;
          input.disabled = false;
          notify(`重新打开任务失败：${(result && result.error) || '后端错误'}`, 'err');
          return;
        }
        await refresh();
      } catch (error) {
        input.checked = true;
        input.disabled = false;
        notify(`重新打开任务出错：${error.message || error}`, 'err');
      }
    }

    function openEditor(input, row, task) {
      input.checked = false;
      closeEditors();
      input.disabled = true;
      row.insertAdjacentHTML('beforeend', panelMarkup(task));
      const editor = row.querySelector('.task-completion-editor');
      const conclusion = editor.querySelector('.completion-conclusion');
      const nextTask = editor.querySelector('.completion-next-task');
      const errorBox = editor.querySelector('.completion-error');

      editor.querySelector('.completion-cancel').addEventListener('click', () => {
        editor.remove();
        input.disabled = false;
        input.focus();
      });
      editor.querySelector('.completion-save').addEventListener('click', async () => {
        const conclusionValue = conclusion.value.trim();
        const nextTaskValue = nextTask.value.trim();
        if (!conclusionValue) {
          errorBox.textContent = '请填写完成结论';
          errorBox.classList.remove('hidden');
          conclusion.focus();
          return;
        }

        errorBox.classList.add('hidden');
        setBusy(editor, true);
        try {
          const result = await completeTask(
            getProjectPath(),
            task.task_id,
            conclusionValue,
            nextTaskValue,
          );
          if (!result || !result.ok) {
            errorBox.textContent = `完成失败：${(result && result.error) || '后端错误'}`;
            errorBox.classList.remove('hidden');
            setBusy(editor, false);
            return;
          }
          await refresh();
        } catch (error) {
          errorBox.textContent = `完成出错：${error.message || error}`;
          errorBox.classList.remove('hidden');
          setBusy(editor, false);
        }
      });
      conclusion.focus();
    }

    async function handleToggle(input, row, task) {
      if (task.status === 'done') {
        await reopen(input, task);
        return;
      }
      openEditor(input, row, task);
    }

    return Object.freeze({ handleToggle });
  }

  root.TaskCompletion = Object.freeze({ createController, panelMarkup });
})(globalThis);
```

- [ ] **Step 6: Load and wire the controller**

In `client/windows/main.html`, load it before `main.js`:

```html
<script src="work-map.js"></script>
<script src="project-panorama.js"></script>
<script src="knowledge-proposals.js"></script>
<script src="task-completion.js"></script>
<script src="main.js"></script>
```

In `client/windows/main.js`, add after `state`:

```javascript
const taskCompletion = TaskCompletion.createController({
  getProjectPath: () => state.currentProject.path,
  completeTask: (projectPath, taskId, conclusion, nextTaskTitle) =>
    wea.completeTask(projectPath, taskId, conclusion, nextTaskTitle),
  updateTask: (projectPath, taskId, field, value) =>
    wea.updateTask(projectPath, taskId, field, value),
  refresh: () => refreshCurrent(),
  notify: (message, kind) => toast(message, kind),
});
```

Replace the checkbox binding:

```javascript
row.querySelector('.task-check').addEventListener('change', (event) => {
  taskCompletion.handleToggle(event.currentTarget, row, task);
});
```

Delete `toggleTaskCompletion`; the controller is the single owner of completion/reopen interaction.

- [ ] **Step 7: Remove the generic status-edit bypass**

Replace the status block in `showTaskEditor` with a lifecycle-specific field:

```javascript
const lifecycleField = task.status === 'done'
  ? `<div class="te-row">
       <label>结论</label>
       <input id="te-lifecycle" value="${esc(task.conclusion || '')}"
         placeholder="记录完成结论…" />
     </div>`
  : `<div class="te-row">
       <label>下一步</label>
       <input id="te-lifecycle" value="${esc(task.next_action || '')}"
         placeholder="下一步要做什么…" />
     </div>`;
```

Render only name, `lifecycleField`, and actions. Save with:

```javascript
const newTitle = editor.querySelector('#te-title').value.trim();
const lifecycleValue = editor.querySelector('#te-lifecycle').value.trim();
saveTaskEdits(task, newTitle, lifecycleValue, row);
```

Replace `saveTaskEdits` with:

```javascript
async function saveTaskEdits(task, newTitle, lifecycleValue, row) {
  const projectPath = state.currentProject.path;
  const errors = [];
  const lifecycleField = task.status === 'done' ? 'conclusion' : 'next_action';
  const previousValue = task[lifecycleField] || '';

  try {
    if (newTitle && newTitle !== task.title) {
      const result = await wea.updateTask(
        projectPath,
        task.task_id,
        'title',
        newTitle,
      );
      if (!result || !result.ok) {
        errors.push(`名称：${(result && result.error) || '失败'}`);
      }
    }
    if (lifecycleValue !== previousValue) {
      const result = await wea.updateTask(
        projectPath,
        task.task_id,
        lifecycleField,
        lifecycleValue,
      );
      if (!result || !result.ok) {
        errors.push(`${lifecycleField === 'conclusion' ? '结论' : '下一步'}：${
          (result && result.error) || '失败'
        }`);
      }
    }

    if (errors.length) toast(`部分更新失败：${errors.join('；')}`, 'err');
    else toast('已保存', 'ok');
    await refreshCurrent();
  } catch (error) {
    toast(`保存出错：${error.message || error}`, 'err');
    const editor = row.querySelector('.task-editor');
    if (editor) editor.remove();
  }
}
```

For a legacy completed task with no conclusion, allow title-only edits and show `未记录结论` in the row. If the user enters a conclusion, persist it through the generic `conclusion` update; never invent one.

- [ ] **Step 8: Style the conclusion and editor states**

Add to `client/windows/main.css`:

```css
.task-conclusion { color: var(--text-dim); font-size: 12px; }
.task-conclusion::before { content: "结论："; color: var(--text-faint); }
.task-row .task-completion-editor { width: 100%; flex-basis: 100%; }
.completion-heading { margin-bottom: 10px; color: var(--text); font-weight: 700; }
.te-row span { font-size: 12px; color: var(--text-faint); }
.te-row em { font-style: normal; font-weight: 400; }
.completion-error { margin: 4px 0 8px; color: var(--red); font-size: 12px; }
.task-completion-editor input:disabled,
.task-completion-editor button:disabled { opacity: .6; cursor: wait; }
```

- [ ] **Step 9: Run renderer tests and verify green**

Run:

```powershell
python -m pytest tests/test_task_completion_renderer.py tests/test_work_map_renderer.py tests/test_main_renderer_static.py -q
```

Expected: all tests pass.

- [ ] **Step 10: Run focused end-to-end verification**

Run:

```powershell
python -m pytest tests/test_work_map_store.py tests/test_task_completion.py tests/test_gui.py tests/test_index_store.py tests/test_search_store.py tests/test_task_completion_renderer.py tests/test_work_map_renderer.py -q
```

Expected: all selected tests pass.

- [ ] **Step 11: Run the complete regression suite**

Run:

```powershell
python -m pytest -q
```

Expected: all tests pass with no new failures.

Run:

```powershell
git diff --check
```

Expected: no output and exit code `0`.

- [ ] **Step 12: Commit the user interaction**

```powershell
git add client/windows/task-completion.js client/windows/work-map.js client/windows/main.js client/windows/main.html client/windows/main.css tests/test_task_completion_renderer.py tests/test_work_map_renderer.py
git commit -m "feat: add inline task completion flow" -m "Why: users need to preserve a result and continue into a real task without leaving the work map."
```

---

## Final Acceptance Checklist

- [ ] A whitespace-only conclusion is rejected before any write.
- [ ] A valid completion updates status and conclusion through one Markdown replacement.
- [ ] Optional follow-up creation stays in the same work item and immediately follows the completed task.
- [ ] Reopen preserves conclusion, `next_action`, `last_event_id`, sibling blocks, and Timeline bytes.
- [ ] Generic `update_task(status=done)` cannot bypass the conclusion requirement.
- [ ] Old v1/v2 documents without conclusion still parse.
- [ ] Existing SQLite databases gain the conclusion column without data loss.
- [ ] Search finds conclusion text.
- [ ] Manual work-item creation remains visible; no automatic work-item creation path is added.
- [ ] No completion/reopen path appends Timeline content.
- [ ] Full test suite and `git diff --check` pass.
