---
feature_ids: [F001]
related_features: []
topics: [workeventagent, worklog, opencode, markdown, sqlite]
doc_kind: spec
created: 2026-06-29
---

# F001: WorkEventAgent Vision and MVP

> Status: spec | Owner: @cat-tv94q87o

## Why

WorkEventAgent exists to turn low-friction daily work updates into durable personal work memory.

The target user should be able to send one short progress update, optionally with archived image/file paths, and get a confirmed write into a project memory document. The system is not a Todo app, not a desktop productivity suite, and not a report generator first. Its core value is trustworthy continuity: what happened, where it belongs, what changed, and what the next action is.

North star:

> Every work update becomes traceable project memory that can answer: where are we, what happened, and what should happen next.

## What

MVP scope is the progress archiving loop:

1. Capture a free-form text update and optional attachment paths through a thin local wrapper.
2. Call opencode as the only LLM/agent execution entry.
3. Ask the agent to produce a structured archive proposal.
4. Show the proposal as a terminal confirmation card with `confirm`, `edit`, and `cancel`.
5. After confirmation, write the update into the project Markdown document.
6. Update the SQLite index from the confirmed Markdown state.

The company constraint has been resolved as option B: local wrapper code is allowed, but the LLM/agent path must go through opencode.

## Non-Goals

- No GUI or hotkey window in MVP.
- No automatic project creation.
- No image understanding in MVP; attachments are archived as files/paths only.
- No complex task state machine beyond `in_progress` and `done`.
- No global Markdown index as a second source of truth.
- No automatic daily/weekly rollup generation in MVP; rollup sections are reserved derived views.
- No implementation work before this spec is reviewed.

## Core Model

- Project: a manually created long-running work area.
- Item: a major work stream inside a project.
- Task: a concrete unit of progress inside an item.
- Update: one captured user statement plus optional attachments.
- Timeline event: append-only record of confirmed updates and corrections.
- Work Map: source of truth for item/task structure and current task state.
- Current Snapshot: replaceable summary derived from Work Map and Timeline.

Truth model:

- Work Map owns structure and current state: which items/tasks exist, where they belong, status, and next action.
- Timeline owns history: what happened, when it happened, and which task IDs were affected.
- Current Snapshot is derived convenience text and may be regenerated.
- SQLite rebuild merges Work Map structure/current state with Timeline history.

Work Map and Timeline are complementary truth sources. They must not be described as a one-way projection. When a progress update changes a task, the Work Map change should be traceable to a Timeline event. Initial manually created structure may exist before any Timeline event.

## Document Protocol

MVP uses one Markdown file per project. Each project document follows `docs/WORKLOG_SCHEMA.md`.

Recommended project document shape:

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

## Decisions

## Attachments

## Timeline

## Daily / Weekly Rollups
```

Stable IDs are required:

- `project_id`
- `item_id`
- `task_id`
- `event_id`

Titles may change. IDs must not change unless a migration is recorded.

Event IDs must include enough entropy to avoid collision. Recommended format:

`YYYYMMDD-HHMMSSmmm-task-id` with a numeric suffix if more than one event occurs in the same millisecond.

## SQLite Role

SQLite is an index and cache, not the source of truth.

Minimum indexed fields:

- `project_id`
- `item_id`
- `task_id`
- `status`
- `next_action`
- `updated_at`
- `doc_path`
- `doc_anchor`
- `attachment_paths`
- `last_event_id`

Write order:

1. Confirm the proposal with the user.
2. Write Markdown first.
3. Update SQLite second.
4. If SQLite update fails, keep Markdown and rebuild the index later.

## Interaction Contract

MVP confirmation card is terminal text, not GUI.

The card must show:

- Project, item, and task target.
- Confidence and reason.
- Timeline event to append.
- Work Map block to update.
- Next action and status change.
- Attachment paths to archive.
- Exact file path and anchor that will be changed.

If the target project, item, or task is uncertain, the agent must ask a question instead of writing.

## Item and Task Creation Policy

Project creation is out of scope for the agent. Item/task creation uses option B: the agent may propose a new item/task in the confirmation card, but the write occurs only after explicit user confirmation.

The confirmation card must show:

- `new_item` or `new_task`
- generated stable IDs
- target parent project/item
- exact Markdown block to insert
- timeline event that will be appended

## Correction Protocol

Timeline history is never edited in place. If a confirmed update was archived incorrectly, correction must be represented as a new Timeline event that references the original `event_id`.

Minimum correction fields:

- `event_type: correction`
- `corrects_event_id`
- `reason`
- corrected target IDs or corrected summary

The original event remains visible.

## MVP Concurrency Assumption

MVP assumes a single writer per project document. Parallel `opencode run` sessions and simultaneous manual edits are out of scope until locking or conflict detection is designed.

## Acceptance Criteria

- [ ] AC-1: A user can submit a free-form text update and receive a structured archive proposal.
- [ ] AC-2: The proposal can be confirmed, edited, or canceled before any write.
- [ ] AC-3: Confirmed updates append a Timeline event and update only the targeted Work Map task block.
- [ ] AC-4: Current Snapshot can be regenerated and is never treated as authoritative history.
- [ ] AC-5: SQLite can be rebuilt from project Markdown documents.
- [ ] AC-6: Attachments are archived by path without image understanding.
- [ ] AC-7: No new project is created by the agent without explicit manual setup.
- [ ] AC-8: Golden examples verify expected Markdown and SQLite changes.
- [ ] AC-9: New item/task proposals require explicit confirmation with generated IDs and exact Markdown insertion preview.

## Golden Examples

Example input:

> Reviewed blockers for KV cache few-shot optimization today. The main issue is unclear prefix reuse strategy. Next step is to map the current inference chain.

Expected proposal:

- Target project: `multimodal-labeling`
- Target item: `kv-cache-few-shot`
- Target task: `kv-cache-blockers`
- Status: `in_progress`
- Next action: Map the current inference chain.
- Timeline event: append one event linked to `task_id=kv-cache-blockers`
- Work Map update: replace only the `kv-cache-blockers` task block

Example ambiguous input:

> Made some progress on the data part today and also changed the script.

Expected behavior:

- Do not write.
- Ask which project/item/task the update belongs to, or present candidates for confirmation.

Chinese input example:

> 今天看了 KV cache 优化 few-shot 的阻塞点，发现主要卡在 prefix 复用策略不清楚，下一步整理当前推理链路。

Expected behavior:

- Treat the input as a normal free-form update.
- Propose the same target task as the English blocker example when the Work Map contains `task_id=kv-cache-blockers`.
- Preserve the original Chinese input in the Timeline event.

## Dependencies

- opencode CLI for agent execution.
- A thin local wrapper for capture, confirmation, and attachment path handling.
- Local filesystem project workspace.
- SQLite for rebuildable indexing.

## Risk

- LLM writes may overwrite or move content incorrectly.
- Title-based matching can drift when tasks are renamed.
- Single project Markdown files may grow too large.
- SQLite can drift from Markdown if write order is wrong.
- GUI pressure can expand MVP scope before the archive loop is proven.
- Concurrent writes or manual edits can corrupt the Markdown/SQLite relationship.

Mitigations:

- Require stable IDs and anchors.
- Make Timeline append-only.
- Scope Work Map edits to one anchored task block.
- Back up before writes or keep project docs under git.
- Define split-page migration rules but do not implement them in MVP.
- State the single-writer MVP assumption before implementation.

## Open Questions

- What is the exact project folder layout for user-created projects?
- Confirm Python 3.11 standard-library wrapper is acceptable in the company environment.
- What line-count or event-count threshold should trigger item page splitting later?

## Next Action

Review this spec with @cat-772lxe06 before implementation planning.
