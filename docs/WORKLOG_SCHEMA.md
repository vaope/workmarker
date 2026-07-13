---
topics: [worklog-schema, markdown, agent-protocol]
doc_kind: protocol
created: 2026-06-29
updated: 2026-07-13
---

# Worklog Schema

This file is the operation protocol for WorkEventAgent. Project Markdown files are the source of truth. SQLite is a rebuildable index.

**Rendering ownership:** Markdown blocks are deterministically rendered by the wrapper from structured fields (`target`, `event`, `status`, `next_action`). The agent outputs structured JSON only; it does not produce Markdown block content directly.

## Schema Version

The current protocol is **schema v2**. New projects use `schema_version: 2`. Existing v1 projects (`schema_version` missing or `1`) remain readable and writable until explicit migration; see "v1 Compatibility" below.

---

## Schema v2

### Required Frontmatter

```yaml
---
project_id: stable-kebab-id
title: Human readable project title
doc_kind: work_project
schema_version: 2
status: active
phase: project-knowledge-design
created: YYYY-MM-DD
updated: YYYY-MM-DD
---
```

- `status` and `phase` are explicit project-level facts. Never infer them from task completion.
- Frontmatter holds no narrative knowledge, nested task data, risk lists, or technical architecture.

### Stable Section Anchors

Parsers and writers use stable HTML anchors, not visible heading text:

```markdown
## 项目档案 <!-- section:project-profile -->
## 当前全景 <!-- section:current-panorama -->
## 工作地图 <!-- section:work-map -->
## 技术概览 <!-- section:technical-overview -->
## 关键认知 <!-- section:project-knowledge -->
## 关键决策 <!-- section:decisions -->
## 附件 <!-- section:attachments -->
## 事件证据 <!-- section:timeline -->
## 历史摘要 <!-- section:rollups -->
```

Section IDs are immutable. Visible headings may be translated or rewritten without changing parse semantics.

### Block Ownership

| Section | Ownership | Write Rule |
|---------|-----------|-----------|
| project-profile | `reviewed` | Human edits directly; Agent proposes diff |
| current-panorama | `derived-reviewed` | Generated; confirmed before write |
| work-map | `structured` | Typed data & deterministic renderer only |
| technical-overview | `reviewed` | Agent proposal + evidence + confirmation |
| project-knowledge | `reviewed` | Agent proposal + evidence + confirmation |
| decisions | `append-only` | Append explicit decisions |
| attachments | `append-only` | Use existing attachment protocol |
| timeline | `append-only` | Use existing event & correction protocol |
| rollups | `derived` | Deterministic report/synthesis may regenerate |

### Human-Readable Work Map

Schema v2 uses stable headings and visible checkboxes:

```markdown
### 工作项：统一捕获 <!-- item:unified-capture -->

让主窗口与快速捕获使用同一套持久化 Inbox 生命周期。

#### [x] 任务：主窗口先写 Inbox <!-- task:main-capture-inbox -->

- 下一步：补充解析完成通知
<!-- task-meta:last_event_id=20260712-main-capture-inbox -->
```

- `[ ]` maps to `in_progress`, `[x]` maps to `done`.
- `item_id` and `task_id` anchors remain stable.
- Checkbox and control metadata are deterministically rendered by the wrapper.
- Raw `status: in_progress` fields are absent from visible text.
- Parsers depend on anchors and heading boundaries, not visible title text.

### Work Map Grammar (v2)

```
item       = (item_v2_heading bg_line* task*)
item_v2_heading = "### 工作项：" title "<!-- item:" item_id "-->"
bg_line    = any non-heading, non-"- " structured line before first task
task       = (task_v2_heading next_action_line task_meta_line)
task_v2_heading = "#### [" (" " / "x") "] 任务：" title "<!-- task:" task_id "-->"
next_action_line = "- 下一步：" text
task_meta_line = "<!-- task-meta:last_event_id=" event_id "-->"
```

### Review-Protected Writes

- `reviewed` content must never be silently overwritten.
- Every non-append write validates a base hash and rejects stale input.
- The client exposes ownership badges and source affordances; never shows control metadata.

---

## Schema v1 (Compatibility)

V1 projects remain supported until explicit migration. The sections below describe the v1 protocol. V1 uses legacy English headings without stable anchors.

### Required Sections (v1)

1. `Current Snapshot`
2. `Work Map`
3. `Decisions`
4. `Attachments`
5. `Timeline`
6. `Daily / Weekly Rollups`

### Work Map Grammar (v1)

```
item       = (item_v1_heading bg_line* task*)
item_v1_heading = "### Item: " title "<!-- item:" item_id "-->"
bg_line    = "- background: " text
task       = (task_v1_heading meta*)
task_v1_heading = "#### Task: " title "<!-- task:" task_id "-->"
meta       = "- status: " ("in_progress" / "done") / "- next_action: " text / "- last_event_id: " id
```

### Migration

V1→v2 migration is explicit: preview → confirm → backup → atomic replace → identity verification. See F007 design for the full migration contract.

---

## Common Rules (v1 + v2)

### Mutability

- `Timeline`: append-only. Never edit or delete prior events.
- `Work Map`: update only the targeted anchored task block.
- `Current Snapshot` / `Current Panorama`: may be regenerated (derived).
- `Decisions`: append new decisions; do not rewrite old decisions without a correction event.
- `Attachments`: append attachment records.
- `Rollups`: derived view; may be regenerated.

### Truth Model

Work Map and Timeline are complementary truth sources:
- Work Map owns item/task existence, hierarchy, status, and next action.
- Timeline owns append-only history of updates and corrections.
- Current Snapshot / Panorama and rollups are derived views.
- SQLite rebuild uses Work Map for current state and Timeline for history.

### Stable IDs

- Format: lowercase kebab-case, ASCII preferred, stable across title changes, unique within parent scope.
- Event ID format: `YYYYMMDD-HHMMSSmmm-task-id` (with collision suffix `-2`, `-3`).
- Event IDs are generated by the wrapper, never by the LLM agent.

### Timeline Events

```md
- 2026-06-29T15:30:00.123+08:00 <!-- event:20260629-153000123-kv-cache-blockers -->
  - task_id: kv-cache-blockers
  - input: Reviewed blockers for KV cache few-shot optimization...
  - summary: Confirmed that prefix reuse strategy is the main unclear point.
  - status: in_progress
  - next_action: Map the current inference chain.
```

### Correction Events

Never edit or remove the original Timeline event. Append a correction:

```md
- 2026-06-29T16:00:00.000+08:00 <!-- event:20260629-160000000-kv-cache-blockers-correction -->
  - event_type: correction
  - corrects_event_id: 20260629-153000123-kv-cache-blockers
  - reason: Original update was attached to the wrong task.
  - corrected_task_id: kv-cache-prefix-reuse
```

### Confirmation Proposal

Before writing, show: target `project_id`/`item_id`/`task_id`, confidence, reason, append-only Timeline event, targeted Work Map block update, status, next action, attachment paths, changed file path and anchor. User chooses `confirm` | `edit` | `cancel`.

### Ambiguity Rules

Ask instead of writing when: project/item/task match uncertain, multiple tasks plausible, progress with no clear target, or auto-creating a project.

### SQLite Rebuild

SQLite indexes Markdown state. Rebuild from frontmatter (metadata), Work Map anchors (structure, status), Timeline events (history, corrections), and Attachments (file paths). Markdown wins over SQLite on conflict.

### Concurrency

MVP assumes single writer per project document.

### Split Rule

MVP keeps one Markdown file per project. Reserved future rule: keep project file as overview, move Item into own file, preserve all IDs, record migration in Timeline/Decisions.
