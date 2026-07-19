---
feature_ids: [F007]
topics: [project-panorama, knowledge-synthesis, evidence, proposals, scheduler]
doc_kind: plan
created: 2026-07-19
---

# F007 Phase B Project Knowledge Synthesis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to implement this plan task-by-task. Keep the red → green → commit sequence and stop at every stated checkpoint.

**Goal:** Turn trusted Timeline evidence into durable, reviewable project-knowledge proposals for Current Panorama, Technical Overview, Project Knowledge, and explicitly confirmed optional module documents—without silently changing project truth.

**Architecture:** Extend the existing Archivist response with a lightweight high-impact classification that is visible before capture confirmation. A high-impact confirmed capture, a directed event selection, or a daily/weekly clock tick creates a durable, idempotent job before synthesis begins. Jobs and immutable proposal bundles live as separate atomic JSON entities under `.workeventagent/knowledge/`; the client aggregates them into the same review surface as Capture Inbox without sharing Capture Inbox retention semantics. A read-only opencode synthesizer returns only bounded narrative content. The wrapper injects project identity, source event IDs, section hashes, controlled metadata, and diffs. One confirmed section bundle is validated and rendered fully in memory, then replaces the project Markdown exactly once with `os.replace`. Optional document creation is a separate confirmation and atomic write.

**Tech Stack:** Python 3.11+ standard library, pytest/unittest, dataclasses, atomic JSON/Markdown files, opencode 1.18.x read-only agents, Electron 33 main/preload/renderer JavaScript, Node `vm` renderer tests.

## Decisions Locked by the Approved Design

- The only automatic high-impact dimensions are `goal`, `scope`, `architecture`, `risk`, and `milestone`.
- Task completion, event count, task status, and next-action state never infer project `status` or `phase`.
- The already-running Archivist call classifies impact. Ordinary captures do not invoke the full synthesizer.
- The capture confirmation card shows a high-impact badge before the user confirms. After confirmation, synthesis may run in the background, but the durable job and visible status exist first.
- Storage and presentation are separate concerns: knowledge jobs/proposals use their own TTL=0 ledger, while the client presents Capture and Knowledge items in one review surface.
- F002 timing helpers are clocks only. A schedule tick enqueues durable work; Electron config is not the job source of truth.
- A proposal bundle is immutable after creation. The user may remove changes before confirmation only by creating a new bundle and superseding the old one.
- Applying a section bundle is all-or-nothing. Every selected source ID and base hash is checked before one full-document atomic replacement.
- An optional module document is never in the same atomic transaction as a project-section bundle. It receives its own proposal and confirmation.
- Proposal apply does not append a new capture event, so applying synthesis cannot recursively trigger more synthesis.

## Global Constraints

- `<workspace>/<project_id>.md` remains the Markdown source of truth; SQLite remains rebuildable.
- Only schema-v2 projects can receive Phase B section proposals.
- Allowed section targets are exactly `current-panorama`, `technical-overview`, and `project-knowledge`.
- `project-profile`, `work-map`, `decisions`, `attachments`, `timeline`, and `rollups` are never modified by a Phase B section proposal.
- Agent output never owns `project_id`, proposal/job IDs, source event IDs, base hashes, target hashes, file paths, headings, stable anchors, or control comments.
- Every source event must exist at generation time and again at apply time.
- `reviewed` and `derived-reviewed` changes require explicit confirmation and a visible before/after diff.
- A stale source event set or section hash rejects the entire bundle; there is no automatic rebase or partial apply.
- Knowledge jobs and proposals have TTL=0. They are never automatically trimmed.
- Every entity write uses a temp file plus `os.replace`; every state transition uses an expected version.
- Electron serializes knowledge-job consumers. The product continues to use the approved single-writer assumption.
- Agent failure, client exit, or app restart must not lose queued work or a generated proposal.
- Daily/weekly success markers advance only after all jobs for that run reach `completed` or an explicit no-evidence/no-change terminal state.
- No new Python or npm dependency is allowed.
- Preserve Electron security: `contextIsolation: true`, `nodeIntegration: false`, typed preload IPC only.
- Tests and runtime probes use temporary workspaces/user-data directories only. Never read or write production user data.
- Do not use Clowder AI ports 3003/3004 or Redis ports 6389/6398.

## Durable Entity Contracts

```text
<workspace>/.workeventagent/knowledge/
  jobs/<job_id>.json
  proposals/<proposal_id>.json
  runs/<run_id>.json
```

Job states:

```text
awaiting_source -> queued -> processing -> completed
                         \-> skipped_no_evidence
                         \-> skipped_no_change
                         \-> failed -> queued (explicit retry)
```

Proposal states:

```text
needs_confirmation -> applying -> applied
                  \-> rejected
                  \-> superseded
                  \-> stale
```

Schedule-run states:

```text
enqueuing -> processing -> completed
```

A schedule-run manifest is written before its first child job. It snapshots every schema-v2 project ID/path and the deterministic child-job ID expected for that cadence key. A restart fills in missing children from the manifest before it evaluates run completion. A failed child leaves the run in `processing`; explicit child retry may later satisfy that slot. The daily/weekly success marker can advance only from a `completed` manifest, never by counting whatever child jobs happen to exist after a partial enqueue.

Section proposal bundle shape:

```json
{
  "schema_version": 1,
  "proposal_id": "kp-...",
  "proposal_kind": "section_bundle",
  "state": "needs_confirmation",
  "version": 1,
  "project_id": "workeventagent",
  "project_path": ".../workeventagent.md",
  "trigger": "high_impact",
  "source_events": [
    {"event_id": "...", "timestamp": "...", "summary": "...", "input": "..."}
  ],
  "changes": [
    {
      "change_id": "change-current-panorama",
      "target_section": "current-panorama",
      "reason": "...",
      "base_section_hash": "sha256:...",
      "target_section_hash": "sha256:...",
      "before": "...",
      "after": "...",
      "diff": "..."
    }
  ],
  "created_at": "...",
  "supersedes": null
}
```

The controlled `after` content contains one wrapper-generated comment before the visible narrative:

```markdown
<!-- panorama-meta source_events=event-a,event-b proposal=kp-... -->
```

The agent cannot emit this comment.

## File Structure

- Create `.opencode/agent/workevent-synthesizer.md`: read-only bounded JSON contract.
- Create `workeventagent/knowledge_store.py`: per-entity job/proposal ledger, idempotency, CAS transitions, recovery.
- Create `workeventagent/project_synthesis.py`: impact/output validation, source selection, deterministic content rendering, bundle creation/revision/apply, optional module document creation.
- Create `client/knowledge_schedule.js`: pure due-run and success-marker helpers.
- Create `client/windows/knowledge-proposals.js`: pure escaped renderer for jobs, proposals, evidence, and diffs.
- Create `tests/test_knowledge_store.py`, `tests/test_project_synthesis.py`, `tests/test_knowledge_schedule.py`, and `tests/test_knowledge_proposals_renderer.py`.
- Modify `.opencode/agent/workevent-archivist.md`: add the bounded `knowledge_impact` object.
- Modify `workeventagent/opencode_runner.py`: synthesizer runner and strict JSON parsing adapters.
- Modify `workeventagent/gui.py`: thin enqueue/process/state/retry/revise/reject/apply handlers and durable high-impact outbox integration.
- Modify `workeventagent/markdown_store.py`: return/preserve the event identity needed by the outbox; do not add synthesis writes here.
- Modify `client/config.js`, `client/main.js`, `client/preload.js`: schedule config, serial worker, startup recovery, typed IPC, update events.
- Modify `client/windows/main.html`, `main.css`, `main.js`, and `project-panorama.js`: unified review entry, event selection, proposal preview/confirmation, schedule controls, visible background status.
- Modify `tests/test_opencode_runner.py`, `tests/test_gui.py`, `tests/test_main_renderer_static.py`, and `tests/test_project_panorama_renderer.py` for integration guards.
- Modify `README.md` and `docs/designs/F007-project-panorama.md` only after Phase B acceptance evidence exists.

---

### Task 0: Prove the two agent contracts against real opencode

**Files:**
- Create: `.opencode/agent/workevent-synthesizer.md`
- Modify: `.opencode/agent/workevent-archivist.md`

**Interfaces:**
- Archivist adds `knowledge_impact.level`, `.dimensions`, and `.reason` while retaining its existing archive proposal contract.
- Synthesizer returns only `changes` and an optional `document_suggestion`.
- Neither agent returns wrapper-owned IDs, hashes, paths, anchors, or Markdown structure.

- [ ] **Step 1: Extend the Archivist prompt contract**

Add this required object to the existing JSON example:

```json
"knowledge_impact": {
  "level": "ordinary",
  "dimensions": [],
  "reason": "This changes only the current task evidence."
}
```

Rules:

- `level` is `ordinary` or `high`.
- `high` is allowed only when the input changes a project goal, scope, architecture, risk, or milestone.
- A task becoming done, a status change, or a growing event count is not sufficient.
- The object classifies the proposed capture; it does not write project knowledge.

- [ ] **Step 2: Create the synthesizer prompt contract**

Create `.opencode/agent/workevent-synthesizer.md` with read-only tools and this exact output shape:

```json
{
  "changes": [
    {
      "target_section": "current-panorama",
      "reason": "string",
      "content": {
        "paragraphs": ["string"],
        "bullets": ["string"]
      }
    }
  ],
  "document_suggestion": null
}
```

`document_suggestion`, when present, has exactly:

```json
{
  "purpose": "string",
  "title": "Architecture",
  "retained_summary": "string",
  "module_conclusion": {"paragraphs": ["string"], "bullets": ["string"]},
  "module_body": {"paragraphs": ["string"], "bullets": ["string"]}
}
```

The prompt must state:

- use only the wrapper-supplied source event IDs;
- allowed targets are the three Phase B sections;
- return no change when evidence does not support one;
- do not infer project status/phase from task completion;
- do not emit headings, HTML comments, frontmatter, file paths, IDs, or hashes;
- suggest at most one optional document and only when concise Technical Overview is insufficient.

The wrapper derives and collision-checks `module_id`, filename, and order from the title plus the current set of project modules. A document suggestion is valid only when the same synthesis output also proposes a `technical-overview` change whose rendered content contains `retained_summary`; the wrapper links the document proposal to that section bundle.

- [ ] **Step 3: Run the real Archivist contract probe**

Use `opencode.cmd` explicitly on Windows because PowerShell script execution may block `opencode.ps1`:

```powershell
opencode.cmd run --agent workevent-archivist --file tests/fixtures/project-v2.md --format json "Archive this ordinary task update: verified one existing unit test; no project goal, scope, architecture, risk, or milestone changed."
```

Expected: exit 0; NDJSON contains one text payload; JSON contains the existing archive fields and `knowledge_impact.level == "ordinary"`.

- [ ] **Step 4: Run the real Synthesizer contract probe**

```powershell
opencode.cmd run --agent workevent-synthesizer --file tests/fixtures/project-v2.md --format json "Directed synthesis. Use only source event event-a. Return bounded JSON for any supported section change."
```

Expected: exit 0; JSON matches the declared shape; it contains no `project_id`, `source_event_ids`, `base_section_hash`, `proposal_id`, `module_id`, filename, order, heading, comment, or path.

If either real probe fails, stop before Task 1 and report the exact command/output. Do not substitute mocked success.

- [ ] **Step 5: Commit**

```powershell
git add .opencode/agent/workevent-archivist.md .opencode/agent/workevent-synthesizer.md
git commit -m "feat: define F007 knowledge synthesis agents" -m "Why: Phase B needs a proven read-only agent boundary before durable jobs or project writes depend on it."
```

---

### Task 1: Build the durable knowledge ledger and outbox

**Files:**
- Create: `workeventagent/knowledge_store.py`
- Create: `tests/test_knowledge_store.py`

**Interfaces:**

```python
job_id_for(idempotency_key: str) -> str
enqueue_job(workspace: Path, spec: dict, now: datetime | None = None) -> dict
get_job(workspace: Path, job_id: str) -> dict
list_jobs(workspace: Path, project_path: str | None = None) -> list[dict]
transition_job(workspace: Path, job_id: str, expected_version: int,
               from_states: set[str], to_state: str, patch: dict | None = None) -> dict
recover_jobs(workspace: Path) -> list[dict]

create_proposal(workspace: Path, proposal: dict) -> dict
get_proposal(workspace: Path, proposal_id: str) -> dict
list_proposals(workspace: Path, project_path: str | None = None) -> list[dict]
transition_proposal(workspace: Path, proposal_id: str, expected_version: int,
                    from_states: set[str], to_state: str, patch: dict | None = None) -> dict

run_id_for(cadence: str, schedule_key: str) -> str
create_schedule_run(workspace: Path, cadence: str, schedule_key: str,
                    projects: list[dict], now: datetime | None = None) -> dict
get_schedule_run(workspace: Path, run_id: str) -> dict
list_schedule_runs(workspace: Path) -> list[dict]
ensure_schedule_children(workspace: Path, run_id: str) -> dict
evaluate_schedule_run(workspace: Path, run_id: str) -> dict
```

- [ ] **Step 1: Write failing ledger tests**

Cover at least:

```python
def test_enqueue_is_idempotent_and_uses_one_file_per_job(tmp_path): ...
def test_job_transition_rejects_wrong_version_or_state(tmp_path): ...
def test_proposal_transition_preserves_immutable_payload(tmp_path): ...
def test_recover_promotes_awaiting_source_when_event_exists(tmp_path): ...
def test_recover_resets_interrupted_processing_to_queued(tmp_path): ...
def test_terminal_entities_are_never_trimmed(tmp_path): ...
def test_atomic_replace_failure_preserves_previous_entity(tmp_path, monkeypatch): ...
def test_schedule_manifest_snapshots_all_v2_projects_before_first_child(tmp_path): ...
def test_schedule_recovery_fills_children_after_crash_on_first_enqueue(tmp_path): ...
def test_schedule_run_with_one_failed_child_never_completes(tmp_path): ...
def test_failed_child_retry_reopens_run_and_advances_marker_only_after_completion(tmp_path): ...
```

Use a temporary schema-v2 project with one Timeline event for recovery tests.

- [ ] **Step 2: Verify red**

```powershell
python -m pytest tests/test_knowledge_store.py -q
```

Expected: collection fails because `workeventagent.knowledge_store` does not exist.

- [ ] **Step 3: Implement per-entity atomic storage**

Rules:

- IDs are wrapper-owned and derived from an explicit idempotency key plus SHA-256, never raw paths.
- `enqueue_job` returns the existing entity for the same idempotency key.
- Every entity contains `schema_version`, `version`, `created_at`, and `updated_at`.
- A transition reads the entity, checks version/state, writes a temp sibling, and calls `os.replace` once.
- Payload fields (`project_path`, trigger, source/date range, changes, evidence) cannot be changed by a state-only transition.
- `recover_jobs` treats `processing` as interrupted and requeues it; `awaiting_source` becomes `queued` only if all referenced events now exist, otherwise it remains visible.
- No cleanup function deletes any knowledge entity.

Schedule-run rules:

- `create_schedule_run` receives a wrapper-generated snapshot of all schema-v2 projects before any child job is written.
- The manifest stores every expected deterministic child ID, not only children already enqueued.
- `ensure_schedule_children` is idempotent and creates any child missing after a crash.
- A child in `failed`, `awaiting_source`, `queued`, or `processing` prevents run completion but does not move the run out of `processing`.
- Only `completed`, `skipped_no_evidence`, and `skipped_no_change` satisfy a child slot.
- `evaluate_schedule_run` always recomputes from every expected child ID in the manifest. After a failed child is explicitly retried, the same run can become `completed` once all child slots are terminal-success/no-op.
- The manifest itself is versioned and atomic; config success markers are never written by this module.

- [ ] **Step 4: Run focused tests**

```powershell
python -m pytest tests/test_knowledge_store.py -q
```

Expected: all pass.

- [ ] **Step 5: Commit**

```powershell
git add workeventagent/knowledge_store.py tests/test_knowledge_store.py
git commit -m "feat: add durable knowledge proposal ledger" -m "Why: synthesis jobs and review decisions must survive restarts without sharing Capture Inbox retention or a race-prone monolithic JSON file."
```

---

### Task 2: Classify impact before confirmation and create the high-impact outbox safely

**Files:**
- Modify: `workeventagent/opencode_runner.py`
- Modify: `workeventagent/gui.py`
- Modify: `tests/test_opencode_runner.py`
- Modify: `tests/test_gui.py`

**Interfaces:**

```python
parse_knowledge_impact(raw: str) -> dict
# returns {"level": "ordinary"|"high", "dimensions": list[str], "reason": str}
```

`handle_propose` includes `knowledge_impact` beside the archive proposal. `handle_commit` returns its committed `event_id`. `handle_inbox_commit` creates a high-impact job in `awaiting_source` before the project write and promotes it after the event is verifiably present.

- [ ] **Step 1: Write failing impact parser tests**

Add tests proving:

- valid ordinary/high objects parse;
- unknown levels/dimensions, empty high reason, or missing object fail closed to ordinary;
- agent-supplied source IDs/job IDs are ignored;
- a `done` event with no supported dimension remains ordinary.

- [ ] **Step 2: Write failing outbox-order tests**

Add integration tests:

```python
def test_ordinary_capture_commit_creates_no_knowledge_job(...): ...
def test_high_impact_job_exists_before_project_commit(...): ...
def test_high_impact_job_is_queued_only_after_source_event_exists(...): ...
def test_failed_project_commit_leaves_visible_recoverable_job(...): ...
def test_commit_returns_and_inbox_archives_real_event_id(...): ...
def test_manual_task_status_update_never_enqueues_synthesis(...): ...
def test_retry_same_event_id_and_content_is_idempotent(...): ...
def test_retry_same_event_id_with_different_content_is_hard_conflict(...): ...
def test_startup_recovers_event_written_before_inbox_archive_and_job_promote(...): ...
```

The order test must patch `handle_commit` and assert that the deterministic job file already exists in `awaiting_source` when the patched commit is entered.

- [ ] **Step 3: Verify red**

```powershell
python -m pytest tests/test_opencode_runner.py tests/test_gui.py -q
```

Expected: new impact/outbox tests fail.

- [ ] **Step 4: Implement fail-closed impact parsing**

Allowed dimensions are exactly the five approved values. Do not fail the archive workflow when impact metadata is malformed; return ordinary and preserve an internal diagnostic reason.

- [ ] **Step 5: Implement the commit outbox order**

For high-impact Capture Inbox proposals:

1. derive idempotency key `high-impact:<project_id>:<event_id>`;
2. enqueue `awaiting_source` before calling `handle_commit`; the job stores `capture_id` so recovery can finish the Inbox transition;
3. commit the source event atomically through the existing path;
4. verify the event exists in Timeline;
5. transition the job to `queued`;
6. return `knowledge_job_id` so Electron can start the worker visibly.

If source commit fails, do not synthesize. Leave a visible job with source-write diagnostic for recovery/retry. Ordinary capture returns no job ID.

`handle_commit` must be idempotent by event identity before it copies attachments or mutates Markdown:

- if `event_id` is absent, use the normal commit path;
- if `event_id` exists and the canonical `task_id`, input, summary, status, and next action equal the requested event, return the original success result without a second Timeline/Work Map/attachment write;
- if the same ID exists with different canonical content, return `event_id_conflict` and do not write.

Startup recovery scans `awaiting_source` jobs. If the exact event exists, it promotes the job and archives the referenced Capture Inbox card idempotently. If the event is absent, the job remains visible/retryable. This closes the crash window after project write but before Inbox archive/job promotion; a repeated user confirmation is safe through the event-idempotent commit path.

- [ ] **Step 6: Run focused and capture regressions**

```powershell
python -m pytest tests/test_opencode_runner.py tests/test_gui.py tests/test_inbox_store.py tests/test_markdown_store.py -q
```

- [ ] **Step 7: Commit**

```powershell
git add workeventagent/opencode_runner.py workeventagent/gui.py tests/test_opencode_runner.py tests/test_gui.py
git commit -m "feat: queue high-impact synthesis after trusted capture" -m "Why: impact is visible before confirmation and durable before background synthesis, while ordinary captures never invoke full synthesis."
```

---

### Task 3: Build the bounded synthesis domain and immutable proposal bundles

**Files:**
- Create: `workeventagent/project_synthesis.py`
- Create: `tests/test_project_synthesis.py`
- Modify: `workeventagent/opencode_runner.py`
- Modify: `tests/test_opencode_runner.py`

**Interfaces:**

```python
run_project_synthesizer(prompt: str, project_doc: Path,
                        opencode_bin: str = "opencode", model: str = "") -> str
parse_synthesis_output(raw: str) -> dict
select_source_events(project_text: str, *, event_ids: list[str] | None = None,
                     date_from: str | None = None, date_to: str | None = None) -> list[dict]
build_section_bundle(project_path: Path, trigger: str, source_events: list[dict],
                     agent_output: dict, now: datetime | None = None) -> dict | None
build_document_proposal(project_path: Path, trigger: str, source_events: list[dict],
                        suggestion: dict, now: datetime | None = None) -> dict | None
revise_section_bundle(bundle: dict, included_change_ids: list[str],
                      now: datetime | None = None) -> tuple[dict, dict]
```

- [ ] **Step 1: Write failing runner/parser tests**

Prove the runner selects `workevent-synthesizer`, passes `--file`, model, JSON format, timeout, and no stdin. Prove the parser rejects:

- unknown sections;
- duplicate target sections;
- non-string content;
- headings, comments, frontmatter delimiters, paths, or wrapper-owned fields;
- more than one document suggestion;
- any agent-supplied filename, `module_id`, order, or other wrapper-owned identity field.

- [ ] **Step 2: Write failing source and bundle tests**

Cover:

```python
def test_directed_selection_preserves_requested_order_and_rejects_missing_id(...): ...
def test_date_selection_uses_timeline_timestamps_inclusive(...): ...
def test_bundle_injects_wrapper_ids_hashes_evidence_and_control_metadata(...): ...
def test_bundle_contains_before_after_unified_diff_and_target_hash(...): ...
def test_bundle_never_targets_profile_work_map_append_only_or_rollups(...): ...
def test_bundle_does_not_change_status_or_phase(...): ...
def test_revision_creates_new_id_and_supersedes_old_without_mutation(...): ...
def test_document_proposal_renders_project_module_contract_but_writes_nothing(...): ...
def test_document_identity_filename_and_order_are_wrapper_derived_and_collision_free(...): ...
def test_document_suggestion_requires_linked_technical_overview_with_retained_summary(...): ...
```

- [ ] **Step 3: Verify red**

```powershell
python -m pytest tests/test_project_synthesis.py tests/test_opencode_runner.py -q
```

- [ ] **Step 4: Implement strict agent adapters**

Parse the opencode NDJSON using the existing shared extraction path. Reject malformed synthesis output rather than guessing. Normalize nothing except newline form; narrative strings are evidence-bearing reviewed content.

- [ ] **Step 5: Implement deterministic content and bundle rendering**

Visible content rules:

- paragraphs are joined with one blank line;
- bullets render as `- <text>` after paragraphs;
- empty paragraphs/bullets are removed;
- `validate_reviewed_content` runs before wrapper metadata is added;
- source snapshots come from the current Timeline parser, not the agent;
- base/target hashes use the same `section_hash` content normalization as apply;
- unified diffs use stable labels `before/<section_id>` and `after/<section_id>`;
- zero valid changes returns `None` and later becomes `skipped_no_change`.

- [ ] **Step 6: Implement optional module proposal rendering**

The wrapper—not the agent—renders frontmatter plus:

```markdown
## 模块结论 <!-- section:module-conclusion -->

...

## 详细内容 <!-- section:module-body -->

...
```

Before rendering, scan existing `project_module` documents for IDs, filenames, and order values. The wrapper derives a unique stable `module_id` from title, uses `<module_id>.md`, and chooses the next deterministic order slot; the agent cannot override any of them.

The same agent response must contain a `technical-overview` change whose wrapper-rendered `after` includes the normalized `retained_summary`. Persist that section bundle first and store its proposal ID plus target section hash on the separate document proposal. Without this linkage, reject the document suggestion.

The document proposal stores the rendered preview, evidence, purpose, retained main-document summary, wrapper-derived identity/path, linked Technical Overview proposal ID/hash, and target path relative to `<project_id>/docs/`, but creates no directory or file.

- [ ] **Step 7: Run focused tests and commit**

```powershell
python -m pytest tests/test_project_synthesis.py tests/test_opencode_runner.py tests/test_project_schema.py -q
git add workeventagent/project_synthesis.py workeventagent/opencode_runner.py tests/test_project_synthesis.py tests/test_opencode_runner.py
git commit -m "feat: build evidence-bound synthesis proposals" -m "Why: agent prose must become an immutable wrapper-owned proposal with real sources, hashes, and deterministic diffs before it can approach project truth."
```

---

### Task 4: Enqueue and process directed, high-impact, daily, and weekly jobs

**Files:**
- Modify: `workeventagent/gui.py`
- Modify: `tests/test_gui.py`

**Interfaces / commands:**

```text
knowledge_enqueue
knowledge_enqueue_schedule
knowledge_process_job
knowledge_state
knowledge_recover
knowledge_retry_job
knowledge_revise_proposal
knowledge_reject_proposal
```

- [ ] **Step 1: Write failing handler tests**

Cover all four triggers and failure paths:

```python
def test_directed_enqueue_rejects_cross_project_or_missing_events(...): ...
def test_high_impact_process_requires_committed_source(...): ...
def test_daily_job_selects_only_local_date_range_evidence(...): ...
def test_weekly_job_runs_full_review_prompt_with_week_evidence(...): ...
def test_schedule_enqueue_persists_full_project_manifest_before_children(...): ...
def test_schedule_recovery_completes_missing_children_after_partial_enqueue(...): ...
def test_process_persists_proposal_before_completing_job(...): ...
def test_agent_failure_marks_job_failed_and_leaves_project_unchanged(...): ...
def test_no_evidence_and_no_change_are_explicit_terminal_states(...): ...
def test_retry_is_explicit_idempotent_and_cas_guarded(...): ...
def test_knowledge_state_aggregates_jobs_and_proposals_without_capture_retention(...): ...
def test_document_suggestion_is_persisted_as_separate_confirmation(...): ...
```

- [ ] **Step 2: Verify red**

```powershell
python -m pytest tests/test_gui.py -q
```

- [ ] **Step 3: Implement thin enqueue/state handlers**

`knowledge_enqueue` accepts only typed non-schedule trigger inputs:

- `directed`: project path + one or more explicit event IDs;
- high-impact is created only by the trusted capture commit path from Task 2.

The handler validates schema v2 and creates an idempotent job. It does not call opencode.

`knowledge_enqueue_schedule` accepts cadence, inclusive local date range, and schedule key. It scans all current schema-v2 projects once, creates the complete run manifest first, then idempotently ensures every expected child job. `knowledge_recover` completes interrupted capture/outbox transitions, proposal/document applies, and schedule manifests before returning actionable state.

- [ ] **Step 4: Implement the job processor**

Processing order:

1. CAS claim `queued -> processing`;
2. read and validate the current project/source evidence;
3. construct a prompt containing trigger and wrapper-selected IDs;
4. call `run_project_synthesizer` read-only;
5. parse and build one immutable section bundle plus an optional separate document proposal;
6. persist proposal entity/entities;
7. only then transition job to `completed` with proposal IDs;
8. on exception, transition to `failed` with a visible diagnostic; never modify project Markdown.

- [ ] **Step 5: Implement revision/rejection**

- Rejection is a CAS transition from `needs_confirmation` to `rejected`.
- A subset revision builds a new immutable bundle, persists it, then marks the old one `superseded` with `superseded_by`.
- Revision cannot add a target or alter generated content/source evidence.

- [ ] **Step 6: Run focused tests and commit**

```powershell
python -m pytest tests/test_gui.py tests/test_knowledge_store.py tests/test_project_synthesis.py -q
git add workeventagent/gui.py tests/test_gui.py
git commit -m "feat: process durable project synthesis jobs" -m "Why: every automatic or directed trigger must be recoverable, idempotent, and visible before any project change is possible."
```

---

### Task 5: Apply confirmed section bundles and optional documents safely

**Files:**
- Modify: `workeventagent/project_synthesis.py`
- Modify: `workeventagent/gui.py`
- Modify: `tests/test_project_synthesis.py`
- Modify: `tests/test_gui.py`

**Interfaces / commands:**

```python
apply_section_bundle(project_path: Path, db_path: Path, bundle: dict,
                     expected_version: int, today: str) -> dict
apply_document_proposal(project_path: Path, proposal: dict,
                        expected_version: int, today: str) -> dict
recover_applying_proposal(project_path: Path, proposal: dict) -> str
```

```text
knowledge_apply_proposal
knowledge_apply_document
```

- [ ] **Step 1: Write failing all-or-nothing apply tests**

Cover:

```python
def test_apply_validates_all_sources_and_hashes_before_writing(...): ...
def test_one_stale_section_rejects_entire_bundle_with_zero_project_change(...): ...
def test_apply_changes_only_allowed_sections_and_preserves_neighbors_byte_for_byte(...): ...
def test_apply_uses_one_project_atomic_replace_for_multiple_sections(...): ...
def test_apply_injects_source_metadata_and_bumps_updated(...): ...
def test_readback_verifies_every_target_hash_before_marking_applied(...): ...
def test_crash_after_project_write_recovers_applying_proposal_as_applied(...): ...
def test_crash_before_project_write_resumes_applying_bundle_from_all_base_hashes(...): ...
def test_recovery_marks_mixed_base_target_or_unknown_content_stale(...): ...
def test_index_failure_returns_applied_with_warning_and_never_reapplies(...): ...
def test_wrong_state_or_version_cannot_apply(...): ...
```

Assert byte-for-byte preservation of project profile, Work Map, Decisions, Attachments, Timeline, Rollups, and any unknown operator prose.

- [ ] **Step 2: Write failing document-creation tests**

Cover:

- unconfirmed proposals cannot create a directory/file;
- target must stay inside `<workspace>/<project_id>/docs/`;
- an existing different/unowned destination blocks apply and is never overwritten;
- confirmation creates exactly one schema-valid `project_module` file atomically;
- missing module conclusion/body is rejected;
- creating a module never changes the main project document;
- no agent-supplied nested document tree is accepted.
- a file created before ledger transition is recovered as applied when its hash equals the proposal preview;
- an absent file resumes creation from `applying`, while an existing different file becomes stale;
- duplicate `module_id` or filename across existing project modules blocks creation;
- the linked Technical Overview bundle must be applied and its retained summary must still be present at the linked target hash.

- [ ] **Step 3: Verify red**

```powershell
python -m pytest tests/test_project_synthesis.py tests/test_gui.py -q
```

- [ ] **Step 4: Implement the single-file apply transaction**

1. CAS transition proposal `needs_confirmation -> applying`.
2. Read the project once.
3. Verify schema/project identity, every source event, and every base hash.
4. Apply all changes in memory using wrapper-controlled raw section replacement after narrative validation.
5. Bump `updated` without changing status/phase.
6. Call `write_project_atomically` exactly once.
7. Read back and verify every target hash/source metadata.
8. Mark proposal `applied`, recording applied project content hash.
9. Rebuild SQLite. If rebuild fails, return an `applied_index_warning`; do not make the proposal retryable.

If validation fails before step 6, mark `stale` with per-section evidence and leave the project byte-identical.

Startup and pre-apply recovery use one explicit three-way matrix for every `applying` section bundle:

- every current section hash equals its recorded target hash: finish the ledger transition to `applied` without writing again;
- every current section hash equals its recorded base hash: the confirmed apply never reached the project, so resume the same immutable in-memory transaction idempotently;
- any mixed base/target state or any unknown hash: mark the whole bundle `stale` and do not write.

The Electron startup worker must invoke this recovery before it exposes confirm/retry controls.

- [ ] **Step 5: Implement separate module-document apply**

Use one atomic create only after path, nonexistence, preview hash, module contract, and proposal state are revalidated. Scan every existing matching `project_module` to reject duplicate IDs/filenames. Require the linked Technical Overview proposal to be `applied`, require the current Technical Overview hash to equal the linked target hash, and require its current visible content to contain the normalized retained summary.

Document recovery uses the same three-way rule: exact preview hash means mark `applied`; missing target means resume the already-confirmed atomic create; any existing different content means `stale`. The output is a source module document, not a compendium. Do not add it to Registry as a root work project.

- [ ] **Step 6: Run safety regressions and commit**

```powershell
python -m pytest tests/test_project_synthesis.py tests/test_gui.py tests/test_project_schema.py tests/test_markdown_store.py tests/test_index_store.py tests/test_search_store.py tests/test_correction_store.py -q
git add workeventagent/project_synthesis.py workeventagent/gui.py tests/test_project_synthesis.py tests/test_gui.py
git commit -m "feat: apply confirmed knowledge proposals atomically" -m "Why: one confirmation must either update every agreed section with current evidence or leave project truth untouched."
```

---

### Task 6: Add typed IPC, serial recovery worker, and durable schedules

**Files:**
- Create: `client/knowledge_schedule.js`
- Create: `tests/test_knowledge_schedule.py`
- Modify: `client/config.js`
- Modify: `client/main.js`
- Modify: `client/preload.js`
- Modify: `tests/test_main_renderer_static.py`

**Interfaces:**

```javascript
KnowledgeSchedule.dueRuns(now, startedAt, schedule)
KnowledgeSchedule.markSuccessful(schedule, run)
```

Preload methods:

```text
getKnowledgeState, enqueueKnowledge, processKnowledgeJob, retryKnowledgeJob,
reviseKnowledgeProposal, rejectKnowledgeProposal, applyKnowledgeProposal,
applyKnowledgeDocument, onKnowledgeUpdated
```

- [ ] **Step 1: Write failing pure schedule tests**

Use Node from pytest to prove:

- default daily and weekly times are evaluated in local time;
- app startup before the due time can run the due tick;
- app startup after the due time enqueues the missed current daily/weekly run once;
- idempotency keys prevent duplicate enqueue;
- failed processing does not advance a success marker;
- completed/no-evidence/no-change processing does;
- daily and weekly can both be due without overwriting each other's config.
- a two-project run writes a manifest for both projects before the first child enqueue;
- crashing after the first child enqueue is recovered by creating the missing second child;
- one failed child keeps the manifest and config marker incomplete.
- retrying that child to terminal success allows the same manifest—and only then its config marker—to complete.

- [ ] **Step 2: Write failing IPC/worker static guards**

Assert typed channels exist in main/preload, no generic Markdown write is exposed, and high-impact `knowledge_job_id` is passed into the serial worker after inbox commit.

- [ ] **Step 3: Verify red**

```powershell
python -m pytest tests/test_knowledge_schedule.py tests/test_main_renderer_static.py -q
```

- [ ] **Step 4: Implement a pure schedule helper and config**

Add:

```json
"synthesisSchedule": {
  "dailyEnabled": true,
  "dailyTime": "23:30",
  "weeklyEnabled": true,
  "weeklyDay": 5,
  "weeklyTime": "18:00",
  "lastDailySuccessDate": "",
  "lastWeeklySuccessKey": "",
  "lastRunStatus": ""
}
```

The existing interval invokes the helper, but each due cadence first asks the backend to create a durable schedule-run manifest containing the complete current schema-v2 project snapshot and every expected child job ID. Only after the manifest exists may child jobs be enqueued. Restart recovery completes missing children from the manifest. The helper marks config success only after the backend reports that exact manifest `completed`; it never infers success from a partial/global job list.

- [ ] **Step 5: Implement the serial worker and recovery**

- One promise chain owns all `knowledge_process_job` calls.
- Inbox commit enqueues the returned high-impact job on that chain and immediately emits visible queued status.
- Manual directed synthesis awaits the same chain and displays progress.
- App startup calls backend recovery/state, then queues recoverable jobs.
- Startup first recovers interrupted capture commits, `applying` section/document proposals, and incomplete schedule-run manifests; only then does it queue jobs or render actionable controls.
- Scheduled work and manual work use the same queue.
- Every job/proposal transition emits `wea:knowledge-updated` to the main window.
- A worker error is caught, persisted by the backend, and leaves the queue usable for the next job.

- [ ] **Step 6: Run syntax and focused tests**

```powershell
node --check client/knowledge_schedule.js
node --check client/main.js
node --check client/preload.js
python -m pytest tests/test_knowledge_schedule.py tests/test_main_renderer_static.py -q
```

- [ ] **Step 7: Commit**

```powershell
git add client/knowledge_schedule.js client/config.js client/main.js client/preload.js tests/test_knowledge_schedule.py tests/test_main_renderer_static.py
git commit -m "feat: schedule durable knowledge synthesis jobs" -m "Why: timers should enqueue recoverable work and never masquerade as the source of truth for proposal completion."
```

---

### Task 7: Build the unified evidence-selection and proposal-review experience

**Files:**
- Create: `client/windows/knowledge-proposals.js`
- Create: `tests/test_knowledge_proposals_renderer.py`
- Modify: `client/windows/project-panorama.js`
- Modify: `client/windows/main.html`
- Modify: `client/windows/main.css`
- Modify: `client/windows/main.js`
- Modify: `tests/test_project_panorama_renderer.py`
- Modify: `tests/test_main_renderer_static.py`

- [ ] **Step 1: Write failing pure renderer tests**

Render fixture jobs/proposals through Node `vm` and prove:

- all user/agent text is escaped;
- each pending proposal shows trigger, evidence IDs/summaries, reason, before/after diff, and target sections;
- applying/rejected/stale/superseded/failed states are visually distinct;
- an optional document card shows filename, purpose, retained summary, module conclusion/body preview;
- no control comment/path from raw data becomes executable HTML;
- only `needs_confirmation` proposals emit confirm/reject controls.

- [ ] **Step 2: Write failing interaction/static guards**

Assert:

- Project Panorama has a “从事件更新全景” entry;
- the event picker loads real `listTimeline` results and requires at least one event;
- Search timeline results carry `event_id` checkboxes and can seed the same directed flow;
- knowledge items appear in the existing Inbox review view as a separate “待审核知识” group while remaining backed by their own IPC/state;
- high-impact capture cards display the five-dimensional impact badge before confirmation;
- queued/generated background work produces a toast and review badge rather than a silent proposal;
- proposal confirmation submits expected proposal version and whole bundle;
- deselecting changes calls revision first, then applies the new bundle;
- stale results refresh and offer regenerate, never partial apply;
- schedule controls are visible in Settings.

- [ ] **Step 3: Verify red**

```powershell
python -m pytest tests/test_knowledge_proposals_renderer.py tests/test_project_panorama_renderer.py tests/test_main_renderer_static.py -q
```

- [ ] **Step 4: Implement the pure proposal renderer**

Keep HTML generation free of IPC/global state. Render a compact panorama banner/count plus full review cards used in Inbox/modal. Use `<pre>` for unified diff and explicit evidence chips.

- [ ] **Step 5: Implement event selection and manual generation**

- Panorama action opens a modal populated from the current project's typed Timeline response.
- Search permits selecting only Timeline results from one project at a time; mixed-project selection is rejected visibly.
- Submit enqueues a `directed` job, shows processing, and waits through the serial worker.
- Closing/reopening the window reloads durable state.

- [ ] **Step 6: Implement proposal confirmation/retry/rejection**

- Confirm always applies an immutable whole bundle with version.
- If the user removes a proposed target, create/reload the revised bundle before confirm.
- Optional document confirmation is a separate button and warning.
- Failed jobs expose Retry; rejected/stale proposals remain auditable.
- Successful apply refreshes Panorama and source buttons immediately.

- [ ] **Step 7: Checkpoint before styling**

```powershell
node --check client/windows/knowledge-proposals.js
node --check client/windows/project-panorama.js
node --check client/windows/main.js
python -m pytest tests/test_knowledge_proposals_renderer.py tests/test_project_panorama_renderer.py tests/test_main_renderer_static.py -q
```

Do not continue if the renderer can confirm without showing evidence/diff or if any background path can apply automatically.

- [ ] **Step 8: Add focused styles and commit**

Keep proposal state/evidence/diff legible at the default 1040×700 window and reuse the restored vertical scroll contract.

```powershell
git add client/windows/knowledge-proposals.js client/windows/project-panorama.js client/windows/main.html client/windows/main.css client/windows/main.js tests/test_knowledge_proposals_renderer.py tests/test_project_panorama_renderer.py tests/test_main_renderer_static.py
git commit -m "feat: add evidence-bound knowledge review UI" -m "Why: automatic synthesis is trustworthy only when its trigger, sources, diff, state, and confirmation are visible in one durable review experience."
```

---

### Task 8: Prove Phase B end to end and record the acceptance boundary

**Files:**
- Modify: `README.md`
- Modify: `docs/designs/F007-project-panorama.md`
- Modify: relevant tests only if runtime acceptance exposes a real missing guard

- [ ] **Step 1: Run the full automated suite**

```powershell
python -m pytest -q
```

Expected: all existing 249 tests plus every Phase B test pass.

- [ ] **Step 2: Run every client syntax check**

```powershell
Get-ChildItem client -Recurse -Filter *.js | ForEach-Object { node --check $_.FullName }
```

Expected: every client JS file exits 0.

- [ ] **Step 3: Run diff and protocol guards**

```powershell
git diff --check
rg -n "TODO|placeholder|Phase B will|automatic evidence synthesis will" workeventagent client tests docs README.md
rg -n "project_id|source_event_ids|base_section_hash|proposal_id" .opencode/agent/workevent-synthesizer.md
```

Expected: no implementation placeholder; any agent-contract hits are explicit prohibitions, not output fields.

- [ ] **Step 4: Run isolated backend acceptance scenarios**

In a temporary workspace with a schema-v2 project, use a deterministic fake opencode executable for repeatable data and the real opencode contract for one smoke run. Prove:

1. ordinary confirmed capture archives normally and creates no synthesis job;
2. a high-impact capture shows impact before confirmation, then leaves a durable queued job before synthesis;
3. killing after event write but before Inbox archive/job promotion recovers both without a duplicate event;
4. killing before and after section-bundle project write exercises the base/target/conflict recovery matrix;
5. killing after optional module create but before ledger transition recovers by exact content hash;
6. killing after the first child of a two-project schedule run recovers the manifest and missing child;
7. directed selection of one and multiple events generates evidence-bound proposals;
8. daily/weekly enqueue is idempotent, snapshots all projects, and failures do not mark success;
9. agent failure leaves project Markdown byte-identical and a retryable error;
10. stale one-of-many base hash rejects the whole bundle;
11. confirmed bundle changes only the three allowed sections, preserves all other bytes, and exposes source IDs;
12. optional module identity/path/order are wrapper-owned, the file does not exist before separate confirmation, and Technical Overview still contains the retained summary afterward;
13. applying synthesis never creates a new synthesis job.

- [ ] **Step 5: Run isolated Electron acceptance at 1040×700**

Use a temporary Electron `userData` directory and temporary workspace. Verify:

- high-impact badge is visible on capture confirmation;
- queued/failed/pending counts survive window close/reopen;
- event picker and Search selection both reach directed synthesis;
- evidence and full diff are readable and vertically scrollable;
- reject/retry/revise/apply controls reach the correct durable states;
- stale bundle displays a clear regenerate path with no partial write;
- successful apply refreshes Panorama and source-event controls;
- optional document requires its own confirmation;
- Reports, Capture Inbox, Search, correction, Work Map, and Settings remain reachable.

Save screenshots/logs outside production data and record window size plus proposal/job IDs in the review handoff.

- [ ] **Step 6: Update documentation only after evidence passes**

- README: describe automatic/manual knowledge proposals, durable review/retry, and schedule controls.
- F007 design: mark Phase B implemented with commit range and acceptance evidence. Do not mark Phase C complete.
- State explicitly that Phase C planning is now unblocked only if module-conclusion governance and Phase B review pass.

- [ ] **Step 7: Final verification and commit**

```powershell
python -m pytest -q
Get-ChildItem client -Recurse -Filter *.js | ForEach-Object { node --check $_.FullName }
git diff --check
git status --short --branch
git add README.md docs/designs/F007-project-panorama.md
git commit -m "docs: record F007 Phase B acceptance" -m "Why: Phase C may be planned only after evidence-bound project knowledge governance has passed merged-code and runtime acceptance."
git show --check --stat --oneline -1
```

---

## Cross-Cutting Test Matrix

| Requirement | Primary tests |
|---|---|
| Ordinary capture never invokes full synthesis | impact parser + inbox commit integration + Electron capture guard |
| High-impact proposal is immediate, visible, durable | Archivist contract probe + outbox order + UI badge/recovery |
| Directed Timeline/Search synthesis | source selector + GUI handler + event picker/Search guards |
| Evidence and diff before confirmation | bundle tests + pure proposal renderer + runtime screenshot |
| Reviewed/derived ownership preserved | all-or-nothing apply byte-preservation tests |
| Stale hash/source rejection | domain apply + GUI integration + runtime stale scenario |
| No status/phase inference from completion | agent prompt probe + bundle frontmatter preservation test |
| Daily/weekly triggers survive partial enqueue/failure/restart | durable run manifest + child recovery + pure schedule + Electron startup worker |
| Optional documents require confirmation and preserve the main summary | wrapper-owned module identity + linked Technical Overview/hash + document recovery + separate UI confirmation |
| No recursive synthesis loop | apply integration asserts no new job |

## Self-Review Checklist Before Implementation Handoff

- [ ] Every Phase B acceptance criterion in `docs/designs/F007-project-panorama.md` maps to at least one test/runtime scenario above.
- [ ] Capture Inbox retention cannot delete or mutate a knowledge entity.
- [ ] There is no monolithic knowledge JSON read-modify-write race.
- [ ] The high-impact job exists before its source commit can become externally visible without an outbox record.
- [ ] Retrying or recovering a committed `event_id` is content-idempotent and cannot duplicate or silently conflict.
- [ ] The agent owns no ID, source set, hash, path, anchor, heading, or control comment.
- [ ] Ordinary capture performs no synthesizer call.
- [ ] No deterministic heuristic treats task completion as project impact/status/phase.
- [ ] Bundle application has one full-document atomic write and no partial success response.
- [ ] Optional document creation is a separate atomic confirmation.
- [ ] Optional module identity/order/path are wrapper-owned, unique across modules, and linked to an applied retained Technical Overview summary.
- [ ] Proposal/job states are durable, versioned, visible, retryable where safe, and never trimmed.
- [ ] Every `applying` project/document state has exact target/base/conflict recovery behavior and startup invokes it.
- [ ] Scheduler markers represent a completed durable run manifest with a predeclared project/child set, not timer invocation or a partial job list.
- [ ] All client IPC is typed and preload-scoped.
- [ ] Real opencode and real Electron probes are explicit gates, not optional follow-ups.
- [ ] The plan does not begin Phase C before Phase B independent review and merged-runtime acceptance.

## Execution Handoff

Execute Tasks 0–8 in order. Each task must show red evidence before implementation, green focused tests after implementation, and its own commit. After Task 8, request independent code/spec review. Do not merge on self-review. Only after the reviewed Phase B branch is merged and accepted against `master` may a new Phase C compendium implementation plan be written.
