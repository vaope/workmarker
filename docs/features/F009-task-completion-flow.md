---
feature_ids: [F009]
related_features: [F001, F004, F007]
topics: [task, conclusion, completion, follow-up, work-map]
doc_kind: spec
created: 2026-07-23
---

# F009: Task Completion and Follow-up

> Status: approved for implementation | Owner: @cat-z8iqdgtj | Implementer: @金哥

## Why

The current task checkbox optimizes for changing state, not for continuing work. It can mark a task `done`, but it cannot preserve the result that justified completion or turn the next piece of work into a real task. The user must edit `next_action`, check the task, and then separately create another task. That is a high-frequency interruption.

The underlying concepts are also being conflated:

- `next_action` is useful while a task is still in progress: it tells the user how to resume that task.
- A completion conclusion explains what was learned or delivered by a finished task.
- Work that follows a finished task is a new task, not text stored on the old task.

F009 makes completion a single, durable transition: finish the current task with a required conclusion and optionally create its follow-up task.

## Confirmed Decisions

The co-creator confirmed:

1. Completion is a high-frequency flow.
2. A completion conclusion is required.
3. Completion uses an inline editor rather than a modal.
4. `next_action` remains available for in-progress tasks.
5. A completion may or may not have a follow-up task.
6. Automatic work-item creation is not enabled in this iteration.

## Evaluated Approaches

### A. Dedicated atomic completion command — selected

The renderer sends the conclusion and optional follow-up title to one typed backend command. The backend computes all Work Map changes and performs one atomic Markdown replacement.

This preserves the low-friction checkbox interaction while preventing partially applied completion state.

### B. Chain existing renderer calls — rejected

The renderer could call `update_task(status)`, `update_task(conclusion)`, and `create_task` in sequence. This is smaller locally, but any intermediate failure leaves a completed task without its conclusion or without the requested follow-up task.

### C. Route completion through Inbox/Timeline confirmation — rejected for this phase

This would make every completion reportable history, but it would add AI processing and another confirmation step to the highest-frequency interaction. Users can still capture a reportable progress update separately.

## Product Semantics

### Field meanings

| Field | Meaning | Row visibility |
|---|---|---|
| `status` | Current task state: `in_progress` or `done` | Checkbox |
| `next_action` | The next concrete action for resuming this same in-progress task | Shown only while `in_progress` |
| `conclusion` | The current completion result or learning for this task | Shown only while `done` |

Completing a task preserves its existing `next_action`, `last_event_id`, stable ID, and any non-control prose. The completed row stops showing `next_action` and shows `conclusion` instead. Reopening the task preserves the conclusion in storage but returns the row to its in-progress `next_action` view. This avoids destructive data loss; F009 does not model multiple completion cycles.

### State transitions

| Starting state | User action | Result |
|---|---|---|
| `in_progress` | Check task | Open inline completion editor; no write yet |
| `in_progress` | Save valid conclusion, no follow-up | Set `status=done`, store conclusion |
| `in_progress` | Save valid conclusion and follow-up title | Complete current task and create one new task in the same work item |
| `in_progress` | Cancel editor | Leave task unchanged |
| `done` | Uncheck task | Set `status=in_progress`; preserve conclusion, `next_action`, and `last_event_id` |

The optional follow-up task:

- belongs to the completed task's existing work item;
- is inserted immediately after the completed task;
- receives a new stable `task_id`;
- starts with `status=in_progress`, empty `next_action`, empty `conclusion`, and empty `last_event_id`.

## Data Contract

### Markdown Work Map

Schema v2 task blocks gain a conclusion control line:

```markdown
#### [x] 任务：验证缓存策略 <!-- task:verify-cache-strategy -->
- 下一步：运行三组边界用例
- 结论：前缀复用策略在并发场景下稳定
<!-- task-meta:last_event_id=event-123 -->
```

Schema v1 uses:

```markdown
- conclusion: Prefix reuse is stable under concurrency.
```

Compatibility rules:

- parsers default a missing conclusion to `""`;
- existing v1/v2 documents remain readable without migration;
- newly rendered tasks include an empty conclusion line;
- completion inserts the conclusion line when an old task does not have one;
- control values are normalized to one line, matching the existing `next_action` contract.

Markdown remains the source of truth. SQLite remains a rebuildable index and gains a non-null `conclusion TEXT NOT NULL DEFAULT ''` task column. `init_db` must add the column to existing databases when it is absent.

Task search combines `next_action` and `conclusion` in the searchable snippet so validation results remain retrievable without creating a Timeline event.

## Backend Contract

Add a typed GUI command:

```json
{
  "command": "complete_task",
  "request": {
    "project_path": "D:/workspace/project.md",
    "db_path": "D:/workspace/.workeventagent/index.sqlite",
    "task_id": "verify-cache-strategy",
    "conclusion": "前缀复用策略在并发场景下稳定",
    "next_task_title": "整理缓存策略设计"
  }
}
```

Success:

```json
{
  "ok": true,
  "task_id": "verify-cache-strategy",
  "status": "done",
  "conclusion": "前缀复用策略在并发场景下稳定",
  "new_task": {
    "item_id": "cache-design",
    "task_id": "document-cache-strategy",
    "title": "整理缓存策略设计"
  }
}
```

`new_task` is `null` when no follow-up title is supplied.

Validation failures perform no write:

- conclusion is empty after trimming;
- target task does not exist;
- target task is already `done`;
- generated follow-up task would violate document structure.

The backend must:

1. read the project once;
2. locate the task and its parent work item by stable anchors;
3. compute completion and optional insertion in memory;
4. bump the frontmatter `updated` date;
5. atomically replace the Markdown file once;
6. rebuild the SQLite index from the resulting Markdown.

The generic `update_task` command remains available for title, `next_action`, `conclusion`, and reopening to `in_progress`. A request to set `status=done` through the generic command is rejected with `kind=completion_required`, preventing future UI paths from bypassing the required conclusion.

## Renderer Contract

Checking an in-progress task:

1. returns the visible checkbox to unchecked until persistence succeeds;
2. closes any other completion editor;
3. expands an editor directly below the task row;
4. focuses the required `完成结论` input;
5. offers an optional `后续任务` input;
6. exposes `取消` and `完成任务` actions.

Saving:

- trims both fields;
- shows an inline validation error for an empty conclusion;
- disables the inputs and actions while the request is in flight;
- calls `wea.completeTask(projectPath, taskId, conclusion, nextTaskTitle)`;
- keeps entered values and re-enables the editor after failure;
- refreshes the current project, work-item progress, sidebar counts, and Today counts after success.

Unchecking a completed task is an immediate reopen operation. It calls the generic update command with `status=in_progress` and restores the prior checked state on failure.

The existing task editor no longer changes status. Checkbox completion/reopening is the single status interaction. The editor exposes:

- title plus `next_action` for in-progress tasks;
- title plus `conclusion` for completed tasks.

## Timeline and Reports

F009 does not append a Timeline event. It upgrades current Work Map state and searchability while retaining F004's distinction between structural task interaction and reportable captured work.

If a completion must appear in a daily or weekly report, the user still records a progress update through capture. Automatic deterministic completion events may be evaluated later from observed use; they are not silently introduced here.

## Error and Recovery Rules

- No renderer-side sequence may simulate atomic completion.
- A failed validation or transform leaves Markdown and SQLite unchanged.
- A Markdown write succeeds or fails as one atomic replacement.
- SQLite failure does not change Markdown's role as source of truth; normal index rebuild recovery remains applicable.
- The save button is single-flight to prevent duplicate follow-up tasks from double clicks.
- Existing completed tasks without conclusions remain readable and show `未记录结论`; they are not mass-migrated or guessed from historical events.

## Out of Scope

- Automatically creating a work item.
- Removing or disabling the existing manual `+ 新建工作项` control.
- Turning a free-form `next_action` into a task automatically.
- Adding task dependencies or parent-child task graphs.
- Multiple completion-cycle history.
- Automatically appending completion Timeline events.
- Backfilling conclusions from prior Timeline summaries.

## Acceptance Criteria

- [ ] AC-1: Checking an in-progress task opens one inline completion editor and performs no write before save.
- [ ] AC-2: Completion cannot be saved with an empty or whitespace-only conclusion.
- [ ] AC-3: Saving without a follow-up performs one atomic Markdown replacement that sets `status=done` and stores the conclusion.
- [ ] AC-4: Saving with a follow-up also creates exactly one in-progress task immediately after the completed task in the same work item.
- [ ] AC-5: A completion failure leaves status, conclusion, sibling blocks, following work items, and Timeline content unchanged.
- [ ] AC-6: Completed rows show conclusion instead of `next_action`; in-progress rows show `next_action`.
- [ ] AC-7: Reopening a completed task preserves conclusion, `next_action`, `last_event_id`, stable IDs, non-control prose, and Timeline content.
- [ ] AC-8: Generic `update_task(status=done)` is rejected; all shipped manual completion UI uses `complete_task`.
- [ ] AC-9: Existing v1/v2 task blocks without conclusion remain readable; new task blocks include the conclusion field.
- [ ] AC-10: SQLite upgrades existing task tables with `conclusion`, rebuilds it from Markdown, and task search matches conclusion text.
- [ ] AC-11: No completion or reopen interaction appends a Timeline event.
- [ ] AC-12: The existing manual work-item creation control remains available, while F009 never creates a work item automatically.
- [ ] AC-13: Backend unit tests, renderer/static tests, and the complete existing test suite pass.

## Dependencies

- F004 work-map renderer and checkbox interaction.
- F007 schema v2 Work Map parser and mutation primitives.
- Existing atomic Markdown writer and rebuildable SQLite index.

## Risks

- Existing status writes currently use a multi-field mutation that can clear `next_action` and `last_event_id`; implementation must change status-only writes to the single-field primitive before relying on reopen preservation.
- Existing SQLite databases do not gain columns from `CREATE TABLE IF NOT EXISTS`; an explicit additive migration is required.
- The main renderer is already large; completion behavior should live in a focused module rather than further concentrating it in `client/windows/main.js`.

## Open Questions

None for implementation. Reportable completion history and automatic work-item creation require later usage evidence.
