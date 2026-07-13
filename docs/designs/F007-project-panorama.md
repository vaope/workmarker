---
feature_ids: [F007]
related_features: [F001, F002, F003, F004]
topics: [project-panorama, markdown, knowledge-governance, synthesis, migration]
doc_kind: design
created: 2026-07-13
---

# F007 Project Panorama and Knowledge Governance

> Status: draft for co-creator review | Owner: @cat-z8iqdgtj

## Why

WorkEventAgent already captures progress into a durable Timeline and maintains a current Work Map. The remaining gap is project-level understanding: a user can see tasks and events, but the project document does not yet explain the whole project clearly.

The product vision is:

> A user records small daily facts through one low-friction entry point. WorkEventAgent continuously turns those facts into a trustworthy, traceable project panorama.

The project document must serve two needs without creating two truths:

- a person should understand the project from one document;
- the system must update that document safely and deterministically.

External-product research and the independent architecture review converged on the same shape: one concise overview, small atomic metadata, optional deeper material, append-only evidence, and explicit ownership for every writable section.

## Confirmed Product Decisions

The co-creator confirmed these decisions during the 2026-07-12 discussion:

1. A project must have one default document that can independently explain the whole project.
2. The user continues to capture through one entry point; the user does not classify an input as ordinary or technical before submitting it.
3. Explicit facts may be archived automatically through the existing trust workflow.
4. Inferred project conclusions must show evidence and a before/after diff before they are applied.
5. The user can select one or more events and request a targeted panorama update.
6. High-impact events may prompt an immediate panorama proposal; ordinary events only enter the evidence layer.
7. The panorama uses a structured skeleton plus a readable narrative, not a property-heavy project-management form.
8. Technical documentation may exist, but the default project document must retain enough technical context to remain independently understandable.
9. One document is the daily reading surface. Other files are optional detail, never required prerequisites.
10. A separate configurable global shortcut will toggle the main client window. It is an independent client enhancement and is not coupled to the panorama storage migration.

## Product Boundary

F007 strengthens project memory, not project administration.

It does not add:

- task priority;
- due dates;
- assignees;
- kanban or gantt planning;
- additional task status values;
- AI-generated daily priority ranking.

A proposed field is accepted only when it directly helps answer one of these questions:

1. What is this project and why does it exist?
2. Where are we now?
3. What has been learned or decided?
4. What is risky or blocked?
5. What happens next?

## One-Document Reading Contract

The daily reading contract is simple:

> Open the project's `<project_id>.md`; do not require any other file to understand the project.

A new reader should be able to answer the following within five minutes:

- project background, goal, scope, and success criteria;
- current phase and overall state;
- important completed progress and current focus;
- high-level technical architecture;
- important knowledge and decisions;
- current risks, blockers, milestones, and next step.

Optional files may explain implementation details, but they cannot own information required to answer those questions. The default project document contains a sufficient summary and links only for deeper reading.

Small projects may never create a `docs/` directory.

### Physical Layout

F007 does not change the existing registry rule that discovers one root Markdown file per project:

```text
<workspace>/
  <project_id>.md                         # the one default project document
  <project_id>/docs/<topic>.md            # optional detail proposed later
  attachments/                            # existing attachment storage
  reports/                                # existing derived reports
```

In product discussion, `project.md` is shorthand for the selected project's `<project_id>.md`; F007 does not silently move every project into a new folder.

## Project Document v2

### Frontmatter

Frontmatter remains small and atomic:

```yaml
---
project_id: workeventagent
title: WorkEventAgent
doc_kind: work_project
schema_version: 2
status: active
phase: project-knowledge-design
created: 2026-06-29
updated: 2026-07-13
---
```

Rules:

- `status` and `phase` are explicit project-level facts. Task completion must not infer them automatically.
- Frontmatter does not contain narrative knowledge, nested task data, risk lists, or technical architecture.
- The client may hide frontmatter in its reading view.

### Stable Section Anchors

Parsers and writers use stable HTML anchors instead of visible heading text:

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

Visible headings may be translated or renamed in a later migration without changing parser semantics. Section IDs are immutable.

### Human-Readable Work Map

Schema v2 represents a task as a stable heading with a visible checkbox:

```markdown
### 工作项：统一捕获 <!-- item:unified-capture -->

让主窗口与快速捕获使用同一套持久化 Inbox 生命周期。

#### [x] 任务：主窗口先写 Inbox <!-- task:main-capture-inbox -->

- 下一步：补充解析完成通知
<!-- task-meta:last_event_id=20260712-main-capture-inbox -->
```

Rules:

- `[ ]` maps to `in_progress`; `[x]` maps to `done`.
- `item_id` and `task_id` anchors remain stable.
- The wrapper owns checkbox and metadata rendering.
- The user-facing document does not show raw `status: in_progress` fields.
- The parser uses anchors and heading boundaries, not title matching.

### Example Shape

```markdown
# WorkEventAgent

> 将日常细碎进展持续转化为可信、可追溯的项目全景。

## 项目档案 <!-- section:project-profile -->

### 背景
工作进展散落在聊天、代码和临时笔记中，难以形成连续的项目认知。

### 目标
通过统一捕获，将日常事实持续整理成工作状态、历史证据和项目知识。

### 范围
本地优先；Markdown 是真相源；opencode 是唯一 LLM 执行入口。

### 成功标准
用户只读本文件即可理解项目，并能追溯每个推导结论的来源。

## 当前全景 <!-- section:current-panorama -->

项目已完成统一捕获、持久化 Inbox、搜索、纠错和工作地图。
当前重点是让 Agent 从事件中持续维护项目全景，同时不覆盖人工内容。

- 当前阶段：项目知识模型设计
- 最近成果：F004 工作地图已验收
- 当前风险：自动综合与人工编辑可能发生所有权冲突
- 下一步：落地 Project Document v2 和区块治理协议

<!-- panorama-meta:generated_at=2026-07-13T09:00:00+08:00;source_events=event-a,event-b -->

## 工作地图 <!-- section:work-map -->

### 工作项：项目知识体系 <!-- item:project-knowledge -->

#### [x] 任务：明确统一事件流 <!-- task:unified-event-flow -->

#### [ ] 任务：实现项目全景文档 <!-- task:project-panorama -->
- 下一步：完成 schema v2 迁移设计

## 技术概览 <!-- section:technical-overview -->

Electron 负责桌面交互与调度，Python 负责确定性归档，opencode 负责语义判断。
输入先进入持久化 Inbox，再异步生成归档或知识更新提案。

## 关键认知 <!-- section:project-knowledge -->

- 统一事件流是唯一事实入口。
- 项目全景是带来源的综合视图，不是第二份历史。

## 关键决策 <!-- section:decisions -->

- 2026-07-13：采用单文档全景和区块级所有权。

## 附件 <!-- section:attachments -->

## 事件证据 <!-- section:timeline -->

## 历史摘要 <!-- section:rollups -->
```

## Section Ownership Contract

The conflict is not "human document versus agent document". The conflict appears when one section has two uncontrolled writers. Every v2 section therefore has one explicit mutation class.

| Section | Mutation class | Write rule |
|---|---|---|
| Project Profile | reviewed | Human edits directly; Agent may only propose a diff |
| Current Panorama | derived-reviewed | Generate the whole anchored section or a fixed anchored subsection; show sources and diff before applying |
| Work Map | structured | Existing typed data and deterministic renderer only |
| Technical Overview | reviewed | Agent proposal + evidence + user confirmation |
| Project Knowledge | reviewed | Agent proposal + evidence + user confirmation |
| Decisions | append-only | Append explicit decisions; inferred decisions require confirmation |
| Attachments | append-only | Existing attachment protocol |
| Timeline | append-only | Existing event and correction protocol |
| Rollups | derived | Deterministic/report synthesis may regenerate |

The client must visually distinguish:

- derived content that can be regenerated;
- human-controlled content where Agent changes require approval;
- append-only evidence.

No background operation may silently overwrite a reviewed section.

## Write Architecture

F007 does not introduce a general Markdown AST or fuzzy semantic patch engine.

The write rails are:

1. **Structured rail**: Work Map and Timeline continue to use typed JSON and deterministic rendering.
2. **Derived rail**: generate the complete contents between known stable anchors; AI-derived changes become a pending proposal and require confirmation before replacement.
3. **Reviewed rail**: Agent returns a typed proposal; the client shows evidence and before/after diff; the wrapper applies the confirmed replacement between stable anchors.
4. **Append rail**: append a deterministically rendered record to the target section.

Narrative Agent output may contain restricted paragraph/list content, but it must not contain section headings, stable anchors, HTML comments, or file paths. The wrapper owns document structure and metadata comments.

### Knowledge Proposal

```json
{
  "project_id": "workeventagent",
  "target_section": "technical-overview",
  "operation": "replace_section_content",
  "base_section_hash": "sha256:...",
  "reason": "Both capture windows now share the durable Inbox lifecycle.",
  "source_event_ids": [
    "20260712-main-capture-inbox",
    "20260712-quick-capture-inbox"
  ],
  "content": {
    "paragraphs": ["..."],
    "bullets": ["..."]
  }
}
```

Before applying a non-append proposal, the wrapper must:

1. verify the project and section anchors;
2. verify every source event exists;
3. compare `base_section_hash` with the current section;
4. reject a stale proposal without writing;
5. render the before/after diff;
6. require confirmation for reviewed sections;
7. write Markdown atomically;
8. reparse the document and rebuild SQLite.

## Event-to-Knowledge Flow

```text
one user capture
  -> persist Inbox card and original evidence
  -> route/archive through the existing F003 trust workflow
  -> classify knowledge impact
     -> ordinary fact: no panorama synthesis
     -> explicit task fact: update Work Map + Timeline
     -> explicit decision: append decision + Timeline
     -> inferred project/technical conclusion: create reviewed proposal
     -> high-impact event: offer immediate panorama proposal
```

Supported synthesis triggers:

- immediate proposal after a high-impact event affecting goal, scope, architecture, risk, or milestone;
- manual synthesis from one or more selected Timeline/search events;
- daily generation of a pending Current Panorama proposal while the app/tray scheduler is alive;
- weekly full panorama review using the existing F002 scheduling foundation.

Ordinary captures must not invoke full-project synthesis.

## Technical Documentation

The default project document always includes a sufficient Technical Overview.

An optional document such as `<project_id>/docs/architecture.md` may be proposed only when:

- implementation detail can no longer be summarized clearly in a few paragraphs;
- the material needs an independent lifecycle or audience;
- the user confirms creation.

The proposal must show:

- proposed filename and purpose;
- the Project Overview summary that remains in the default project document;
- source event IDs;
- initial document diff.

Agent must not automatically create a tree of technical documents. Optional documents deepen the project; they do not complete a missing panorama.

## Timeline Placement

Phase A keeps Timeline physically inside `<project_id>.md` because reports, search, correction, and current parsers depend on the in-document section.

The client collapses Timeline by default, so it does not dominate daily reading. Markdown readers can fold the section by heading.

Physical Timeline splitting is deferred until the existing split rule is separately designed and migration-tested. A rendering concern alone is not sufficient reason to change the storage contract.

## Schema v1 to v2 Migration

Existing projects must remain readable before migration.

Migration is explicit and previewable:

1. Detect `schema_version` missing or equal to `1`.
2. Parse the existing required sections and stable IDs.
3. Build a v2 document in memory. Preserve all unrecognized content byte-for-byte; if a non-canonical task block cannot be transformed safely, stop instead of guessing.
4. Show a migration summary and full diff.
5. On confirmation, write a timestamped backup under `.workeventagent/backups/<project_id>/`.
6. Atomically replace the project file.
7. Reparse and verify all project/item/task/event IDs and Timeline event counts.
8. Rebuild SQLite.

Migration must be idempotent. A v2 document is never migrated again.

If any anchor, event, or section cannot be preserved, migration returns a visible error and performs no replacement.

New projects use schema v2 after Phase A ships.

## Client Experience

The project workspace becomes a single Panorama reading surface while preserving F004's Work Map emphasis:

- Project Profile and Current Panorama appear first;
- Work Map remains directly visible and interactive;
- Technical Overview, Project Knowledge, Decisions, and historical sections follow;
- Project Profile, Technical Overview, and Project Knowledge have explicit in-app manual edit actions;
- Timeline and Rollups are collapsed by default;
- source metadata and stable anchors are hidden in the rendered view;
- every derived/reviewed section exposes "view sources";
- reviewed sections expose "review proposed change" rather than silent background mutation.

Reports, Search, Inbox, Settings, and correction remain separate application tools. They are not required reading for understanding the selected project.

## Failure and Conflict Handling

- Agent synthesis failure leaves the current document unchanged.
- Missing source events invalidate the proposal.
- A changed section hash makes the proposal stale and requires regeneration.
- Migration failure preserves the original and backup state.
- SQLite failure after Markdown write is recoverable through rebuild.
- Existing correction rules continue to preserve historical evidence.
- MVP remains single-writer; concurrent external edits are detected through the section hash before non-append writes.

## Delivery Phases

### Phase A: Readable and Governable Document Foundation

- schema v2 parser and renderer;
- stable section anchors and human-readable Work Map;
- explicit v1 migration with backup/diff/verification;
- client Panorama reading surface;
- in-app manual editing for reviewed sections with stale-content detection;
- section ownership labels and source affordances;
- compatibility with capture, reports, search, correction, and indexing.

Phase A does not require new LLM synthesis. It creates the safe document model first.

### Phase B: Project Knowledge Synthesis

- impact classification;
- Current Panorama generation;
- selected-event targeted synthesis;
- reviewed proposals for Technical Overview and Project Knowledge;
- evidence validation, section hashes, and diff confirmation;
- high-impact, daily, and weekly triggers;
- optional technical-document creation proposals.

### Independent Client Enhancement

The main-window global shortcut is implemented and reviewed separately:

- a dedicated configurable accelerator distinct from quick capture;
- first invocation shows/focuses the main window;
- second invocation hides it to tray;
- registration conflict keeps the prior valid shortcut and shows an error;
- it does not change project-document or synthesis contracts.

## Acceptance Criteria

### Phase A

- A v2 `<project_id>.md` independently satisfies all six reading outcomes without opening another file.
- Frontmatter remains small and contains no narrative project knowledge.
- Visible headings are human-readable while stable section IDs drive parsing.
- Work Map remains editable through existing client actions and no raw status code is shown in the document body.
- Timeline remains append-only and report/search/correction behavior is preserved.
- The client hides control metadata and collapses Timeline/Rollups by default.
- v1 migration preserves every project/item/task/event ID, attachment record, decision, and Timeline event.
- Migration failure produces no partial replacement.
- SQLite rebuild from the migrated Markdown succeeds.

### Phase B

- An ordinary captured event does not trigger full panorama synthesis.
- A high-impact event can produce an immediate evidence-backed proposal.
- The user can select events and request a targeted panorama update.
- A derived-section proposal can be generated and confirmed without changing reviewed or append-only sections.
- A reviewed-section proposal shows source events and before/after diff before confirmation.
- A stale section hash blocks the write.
- Project status and phase are never inferred solely from task completion.
- No optional technical document is created without confirmation.

## Non-Goals

- A general-purpose Markdown editor or semantic patch engine.
- Replacing Markdown with a database source of truth.
- A second editable human-readable project file.
- Automatic physical splitting of Timeline or Items in this feature.
- Property-heavy project management.
- Automatic rewriting of human-controlled project goals or technical principles.
- Multi-user or multi-writer merge resolution.

## Risks and Mitigations

1. **Derived and human-authored content collide.** Section ownership, stable anchors, hashes, and client labeling make the boundary explicit.
2. **Schema migration damages historical evidence.** Preview, backup, atomic replacement, ID/event-count verification, and golden migration tests are mandatory.
3. **Panorama becomes another untrusted AI summary.** Every inferred conclusion carries source events and reviewed changes require diff confirmation.
4. **The document becomes a project-management form.** Metadata stays small; rich understanding lives in narrative synthesis, not new task fields.
5. **The feature grows into several systems at once.** Phase A establishes the document foundation; Phase B adds synthesis; the global shortcut remains independent.

## Implementation Handoff

After the co-creator approves this written design:

1. write a Phase A implementation plan;
2. implement Phase A in an isolated feature worktree with TDD;
3. run independent specification/code review;
4. complete runtime acceptance before merging;
5. plan Phase B only after the migrated document and reading surface are accepted.
