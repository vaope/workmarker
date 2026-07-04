---
feature_ids: [F003]
topics: [capture-inbox, search, correction, trust-workflow]
doc_kind: design
created: 2026-07-04
---

# F003 Trust Workflows Design

This document covers the next WorkEventAgent product layer after the core capture, manual structure management, and report generation flows.

Co-creator selected these three priorities:

1. Capture Inbox / archive queue.
2. Global search.
3. Correction workflow.

The shared product goal is trust: the user should always know where a captured thought went, be able to find it later, and be able to correct wrong archives without corrupting history.

## Product Principle

WorkEventAgent should not become a generic project-management app. These features serve the original promise: low-friction work memory that is traceable, searchable, and repairable.

## Feature A: Capture Inbox

### Problem

Quick capture is asynchronous. The user can submit text, keep working, and later wonder:

- Is it still parsing?
- Did it fail?
- Is there a proposal waiting for confirmation?
- What did I already archive?

Transient confirmation cards are not enough for a work-memory product.

### Design

Add a persistent Capture Inbox that stores capture attempts as cards.

Card states:

- `processing`: opencode route/propose is running.
- `needs_confirmation`: proposal is ready and waiting for user confirmation.
- `error`: route/propose/commit failed and can be retried.
- `archived`: successfully committed.
- `canceled`: user dismissed it.

Each card contains:

- original input text
- created_at / updated_at
- attachments copied into a pending area
- selected project if routed
- proposal if available
- error message if failed
- archived event_id / project path if committed

### Storage

Pending and historical inbox cards are user-visible state, not rebuildable Markdown index state. Do not store them inside the rebuildable `index.sqlite`.

Use:

```text
<workspace>/.workeventagent/inbox.json
<workspace>/.workeventagent/pending/<capture_id>/<filename>
```

Writes must be atomic. Keep the latest archived cards for a bounded count such as 100 cards to avoid unbounded growth. Never delete `processing`, `needs_confirmation`, or `error` cards automatically.

### UI

Main window:

- Add an Inbox entry near Reports/Search.
- Show grouped sections: Needs confirmation, Processing, Errors, Recent archived.
- Each card supports confirm, edit, retry, cancel, and open target.

Quick capture window:

- After submit, immediately creates an inbox card and clears input.
- Shows a compact list of latest active cards.
- Closing/reopening quick capture must not lose cards.

### Acceptance Criteria

- Submitting from quick capture creates a persistent inbox card before opencode starts.
- Multiple captures can be in-flight at the same time.
- A proposal can be confirmed later from the Inbox even after the quick capture window closes.
- Failed route/propose/commit shows an error card with retry.
- Pending attachments survive app restart until confirmed or canceled.
- Archived cards link to the written project and event.

## Feature B: Global Search

### Problem

The original product value depends on retrieval. If the user cannot find past work quickly, the archive becomes a passive log instead of a working memory.

### Design

Add global search across:

- projects
- item titles and optional backgrounds
- task titles, statuses, and next actions
- Timeline input and summaries
- report files under `reports/`
- archived attachment filenames

MVP search is local and deterministic. It should not require opencode for every keystroke.

### Index

Use SQLite FTS if available in the bundled Python SQLite. Keep the existing rebuildable-index principle:

- confirmed project/search data is rebuildable from Markdown and report files
- inbox cards may be searched from `inbox.json`, but are not part of the rebuildable Markdown index

Recommended table:

```sql
CREATE VIRTUAL TABLE search_docs USING fts5(
  kind,
  project_id,
  item_id,
  task_id,
  title,
  body,
  path,
  timestamp
);
```

If FTS5 is unavailable, fallback to deterministic substring search over parsed Markdown records. The fallback must be explicit and tested.

### UI

- Add a global search box in the top bar.
- Results show type, project path, title, snippet, and timestamp.
- Clicking a result opens the project and highlights or expands the relevant item/task/timeline entry when possible.

### AI Search Summary

Do not use AI for every search. Add an optional "Summarize selected results" action after deterministic results are shown. This keeps search fast and avoids hiding source evidence.

### Acceptance Criteria

- Searching a task title returns the task.
- Searching a Timeline summary returns the event.
- Searching an item background returns the item.
- Searching a report phrase returns the report file.
- Results show enough path context for the user to trust why it matched.
- Search works without opencode.

## Feature C: Correction Workflow

### Problem

AI can route a capture to the wrong project, item, or task. If correcting it means manually editing Markdown, trust breaks.

### Design

Add an explicit correction flow from Timeline events and archived inbox cards.

The correction flow must preserve history:

- Never edit or delete the original Timeline event.
- Append a correction event that references the original `event_id`.
- Update Work Map current state deterministically after user confirmation.

### MVP Scope

Support these corrections:

1. Fix summary text.
2. Fix status and next action.
3. Reassign to another task in the same project.
4. Reassign to another project/task in the same workspace.

Cross-project correction writes:

- a correction event in the original project marking the original event as corrected or moved
- a new Timeline event in the target project with the corrected target
- Work Map updates in both affected projects when a status or next_action changed current state

### Safety Rule

If the system cannot reconstruct the previous Work Map state for the original task, the correction UI must show an explicit "original task current state" field for the user to confirm. Do not silently guess a rollback.

### UI

Correction entry points:

- Timeline event row: "Correct"
- Archived inbox card: "Correct archive"
- Search result that points to a Timeline event: "Correct"

Correction modal:

- Shows original event and current target.
- Lets user choose project, item, task.
- Lets user edit summary, status, next_action.
- Shows before/after impact before writing.

### Acceptance Criteria

- Correcting an event appends a correction event; original event remains unchanged.
- Same-project reassignment updates the target task and leaves an auditable correction trail.
- Cross-project reassignment writes to both source and target projects atomically enough that partial failure is visible and recoverable.
- Work Map changes are scoped to affected tasks only.
- Correction is confirmed by the user before any write.
- Search and Timeline views show that an event was corrected.

## Recommended Implementation Order

1. Capture Inbox data model and main-window Inbox view.
2. Wire quick capture to create inbox cards and allow later confirmation.
3. Deterministic global search over projects, tasks, timeline, reports, and item backgrounds.
4. Search result navigation and optional AI summary of selected results.
5. Same-project correction workflow.
6. Cross-project correction workflow.

Rationale: Inbox creates the stable queue that corrections and search can point to. Search makes archived content useful. Correction completes the trust loop after retrieval exposes wrong archives.

## Non-Goals

- No multi-user collaboration.
- No cloud sync.
- No kanban board, gantt view, or complex task-status model.
- No automatic correction without user confirmation.
- No editing stable IDs.
- No background OS service.

