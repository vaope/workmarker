---
feature_ids: [F004]
related_features: [F001, F003, F006]
topics: [client, work-map, task-completion, inbox, today-entry]
doc_kind: design
created: 2026-07-12
---

# F004 Project Work Map and Unified Capture

> Status: spec | Owner: @cat-z8iqdgtj

## Why

The current desktop client exposes backend concepts instead of the user's daily mental model:

- a task row behaves like an expandable Timeline browser rather than a task that can be completed;
- the visible labels map `Item` to “需求” and `Task` to “工作项”, while the confirmed product hierarchy is “项目 → 工作项 → 任务”;
- the main-window composer still owns one transient proposal, while quick capture already uses the durable F003 Inbox;
- Timeline occupies a primary project tab even though the user wants it to remain an implementation log for reports, search, correction, and audit.

F004 turns the project page into a current-state work map. It preserves WorkEventAgent's core promise—low-friction, trustworthy work memory—without turning the product into a generic project-management suite.

## Confirmed Product Decisions

The co-creator confirmed these decisions in the F004 discussion:

1. The product hierarchy is **Project → Work Item → Task**.
2. Tasks are directly completable with a checkbox.
3. Checking a task is a lightweight structural edit: update `status` only and **do not append a Timeline event**.
4. The project page shows no Timeline or recent-event summary under tasks.
5. The default main view is the current project's work map.
6. Today is a light top/side entry, not the main page.
7. The visual direction in `docs/mockups/F004-work-map-today-entry.svg` is approved.
8. Checkbox completion is intentionally not reportable history: a task checked off without a captured update will not appear as completed work in daily or weekly reports.

## Product Model and User-Facing Language

| Storage term | User-facing Chinese | Meaning |
|---|---|---|
| Project | 项目 | A long-running context, such as WorkEventAgent. |
| Item | 工作项 | A goal, feature, problem, or stage inside a project. |
| Task | 任务 | A concrete action that can be checked complete. |
| Timeline event | Not shown on project page | An append-only work-memory record used by reports, search, correction, and audit. |

Stable IDs and Markdown headings do not change. This is a presentation and interaction correction, not a storage migration.

## Information Architecture

```text
WorkEventAgent
├─ Left rail
│  ├─ Today entry
│  ├─ Projects
│  ├─ New project
│  └─ Settings
├─ Top bar
│  ├─ Global search
│  ├─ Today entry
│  ├─ Pending-confirmation badge
│  └─ Quick record action
├─ Project workspace (default)
│  ├─ Work Map
│  │  └─ Work Item
│  │     ├─ progress: completed / total
│  │     ├─ Task checkbox rows
│  │     └─ add/edit/delete controls
│  ├─ Reports
│  ├─ Search
│  └─ Inbox
├─ Today summary rail
└─ Persistent capture composer
```

Timeline is removed from the project navigation and task rows. The backend `timeline` command and stored Timeline section remain unchanged because reports, search, correction, and audit depend on them.

## Project Work Map

### Work Item

Each work item displays:

- work item title;
- optional background through the existing edit flow, not expanded by default;
- progress as `done tasks / total tasks`;
- add task, edit work item, and delete work item actions;
- its task rows in document order.

An empty work item remains visible and offers `+ 新建任务`.

### Task Row

Each task row displays only current state:

- checkbox;
- task title;
- optional `next_action` as one muted line;
- edit and delete actions.

It does not display:

- Timeline events;
- last-event timestamp;
- status pills duplicating the checkbox;
- expandable history.

Completed tasks remain in their original order, use a checked checkbox and subdued/struck-through title, and can be unchecked back to `in_progress`.

### Checkbox Write Contract

Clicking a checkbox calls the existing IPC/backend path:

```text
renderer wea.updateTask(projectPath, taskId, "status", nextStatus)
  → ipcMain wea:updateTask
  → python gui update_task
  → atomically update the target task block
  → bump frontmatter updated
  → rebuild index.sqlite
```

Rules:

- `in_progress → done` when checked;
- `done → in_progress` when unchecked;
- disable the checkbox while the write is in flight;
- refresh the selected project and sidebar counts after success;
- restore the previous visible state and show an error toast after failure;
- preserve `task_id`, title, `next_action`, `last_event_id`, sibling task blocks, and the complete Timeline section;
- never append a Timeline event for this interaction.

This is deliberately different from an archived progress update. If the user wants a completion explanation to appear in a report, they record a progress update through capture.

The UI must make this consequence visible in plain language where completion is introduced: checking a task changes the current Work Map only; reportable work still comes from captured updates.

## Unified Capture

The main-window composer must use the same durable F003 Inbox lifecycle as quick capture:

```text
submit text + pending attachments
  → inbox_create (durable processing card first)
  → clear the composer after card creation succeeds
  → inbox_process asynchronously
  → needs_confirmation or error
  → confirm later in Inbox / quick-capture card
```

The main renderer must not keep `state.proposal` as an authoritative single slot and must not call `propose` directly from the composer.

Behavior:

- the user can immediately type the next update after the Inbox card is created;
- attachment ownership moves to the Inbox card after successful creation;
- if card creation fails, keep text and attachments in the composer;
- if processing fails, the durable card becomes `error` and remains retryable;
- show `已加入收件箱，正在后台解析` after card creation;
- Inbox updates refresh the pending badge, Today summary, and current project when a card is committed.

The existing confirmation card container may remain for destructive-action confirmation, but it no longer owns archive proposals in the main window.

## Today Entry

F004 ships a light Today summary rail using existing project and Inbox data. It is an entry surface, not a second planning system.

The rail shows:

1. **待确认捕获**: count of Inbox cards in `needs_confirmation`; click opens Inbox.
2. **当前项目待推进**: count of tasks in `in_progress`; click returns to the Work Map.
3. **报表**: shortcut to Reports with the explanation that reports are generated from underlying records.

Top and left Today controls focus/reveal this rail. The pending-confirmation badge opens Inbox directly. Counts are refreshed after project load, task status change, Inbox update, and workspace change.

The numeric values in the approved SVG are illustrative. F004 does not fabricate a “today score” or infer priorities.

## Mockup Interpretation

The approved mockup is the information-architecture reference, not a pixel-perfect theme migration:

- preserve “work map dominates, Today is lighter”;
- preserve a clear project rail, top actions, dense work-item/task list, and persistent capture bar;
- do not require a full light-theme rewrite in this feature;
- do not ship the mockup's `打开 Today 工作台` as a dead button. A full cross-project Today workbench remains F006 scope.

## Navigation and Existing Features

- Reports, Search, Inbox, project initialization, manual editing, correction recovery, settings, and quick capture remain reachable.
- Search still indexes Timeline events and may navigate a Timeline hit to its project Work Map. Precise item/task highlighting belongs to F005.
- Timeline-based correction APIs remain intact even though the project Timeline tab is removed.
- `listTimeline` remains in preload/backend for reports, correction, tests, and future targeted navigation, but the default project refresh no longer calls it.

## Accessibility and Keyboard Behavior

- Checkbox is a native `input[type="checkbox"]` with an accessible label containing the task title.
- The checkbox is keyboard toggleable with Space.
- Icon-only actions retain meaningful `title` and `aria-label` values.
- Enter in the composer submits to Inbox; Ctrl+Enter inserts a newline; attachment paste behavior remains unchanged.
- Focus remains in the composer after successful card creation so consecutive capture stays fast.

## Acceptance Criteria

- [ ] AC-1: The main page defaults to a work map grouped as Project → Work Item → Task.
- [ ] AC-2: All main-window labels use 项目 / 工作项 / 任务 consistently.
- [ ] AC-3: Checking a task writes `status: done`, updates SQLite/sidebar counts, and creates no Timeline event.
- [ ] AC-4: Unchecking a completed task writes `status: in_progress` and creates no Timeline event.
- [ ] AC-5: Checkbox failure restores the previous visual state and presents a visible error.
- [ ] AC-6: Task rows contain no event list, last-updated text, or expandable history.
- [ ] AC-7: The project navigation has no Timeline tab; Timeline storage and backend APIs remain intact.
- [ ] AC-8: Work-item progress shows correct completed/total counts, including `0/0` for an empty item.
- [ ] AC-9: Main-window capture creates an Inbox card before processing, clears only after durable creation, and permits immediate next input.
- [ ] AC-10: Main-window processing failure leaves a retryable durable Inbox error card.
- [ ] AC-11: Today summary counts are derived from current Inbox/task data and its three shortcuts navigate correctly.
- [ ] AC-12: Reports, Search, Inbox, editing, deletion, project creation, settings, correction recovery, and quick capture remain functional.
- [ ] AC-13: Existing Python tests and renderer tests pass; all client JavaScript passes syntax checks.

## Non-Goals

- Kanban, Gantt, task drag-and-drop, due dates, priorities, assignees, or extra task states.
- A full cross-project Today workbench or an AI-generated daily priority list; that remains F006.
- Generating Timeline events from checkbox changes.
- Deleting Timeline data or removing Timeline/search/correction backend contracts.
- Reordering work items or tasks.
- Pixel-perfect reproduction of the SVG or a full theme rewrite.
- Search-to-task highlighting; that remains F005.

## Risks and Mitigations

- **Reports may miss lightweight completion actions.** This is intentional and co-creator-visible: checkbox changes express current state; captured updates express reportable work. The UI copy must state that checking a task will not add an item to daily or weekly reports.
- **Removing the Timeline tab can hide correction entry points.** Preserve Search/Inbox correction paths and backend APIs; F005 will improve direct navigation.
- **Inbox unification can lose attachments if ownership is unclear.** Clear `state.pending` only after `inbox_create` succeeds and copies files into the durable pending directory.
- **Rapid checkbox clicks can race.** Disable the control until the write and refresh complete.

## Implementation Order

1. Lock the no-Timeline checkbox contract and add work-map renderer tests.
2. Replace task cards with the work-map checkbox presentation.
3. Remove project Timeline navigation and normalize user-facing terminology.
4. Move the main composer to the F003 Inbox lifecycle.
5. Add the derived Today summary rail and navigation shortcuts.
6. Run regression, syntax, and visual acceptance checks.
