---
feature_ids: [F003]
topics: [implementation-plan, capture-inbox, search, correction, trust-workflow]
doc_kind: plan
created: 2026-07-04
---

# F003 Trust Workflows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the approved trust workflow layer: persistent Capture Inbox, deterministic Global Search, and append-only Correction Workflow.

**Architecture:** Python owns persisted user state, Markdown parsing/writes, search indexing, and correction recovery because Markdown remains the source of truth and SQLite remains rebuildable. Electron main process exposes narrow IPC commands and injects workspace/db config. Renderers become views over persisted backend state; quick capture no longer owns proposal truth in `state.proposal`.

**Tech Stack:** Python 3.13 standard library, existing `workeventagent.gui` JSON command boundary, Markdown source-of-truth project files, SQLite/FTS5 with deterministic fallback, Electron main/preload/renderer JavaScript, existing pytest suite and Node renderer harness.

## Global Constraints

- Source-of-truth: project Markdown files own confirmed work state; SQLite is rebuildable.
- Timeline is append-only; corrections append new events and never edit or delete the original event.
- Inbox cards are user-visible persisted state in `<workspace>/.workeventagent/inbox.json`, not in `index.sqlite`.
- Active Inbox cards (`processing`, `needs_confirmation`, `error`) are never automatically trimmed.
- Archived and canceled card records may be bounded only after all project writes or cancellation cleanup are confirmed.
- Pending attachment directories under `<workspace>/.workeventagent/pending/<capture_id>/` are deleted after successful commit or cancel.
- Quick capture migration: `setCaptureState()` may only drive confirmation surface visibility/layout and must not cache proposal fields.
- Archive commit reads proposal data by `capture_id` from the inbox store, not from hidden renderer-local proposal state.
- Search is deterministic first; opencode is not called per keystroke.
- Cross-project correction uses target-first writes plus `.workeventagent/corrections/<correction_id>.json` recovery journal.
- Cross-project correction journal includes deterministic `target_event_id` and deterministic `source_correction_event_id`.
- Existing commands from F001/F002 remain compatible unless this plan explicitly adds a new command.
- Do not use Clowder AI reserved ports 3003/3004 for any dev server.
- Every task ends with targeted tests, full relevant verification, and a commit with a Why body and the implementer's own signature.

---

## File Structure

- Create `workeventagent/inbox_store.py`: persisted capture-card storage, pending attachment copy/cleanup, retention trimming, and atomic `inbox.json` writes.
- Create `workeventagent/search_store.py`: deterministic search document extraction, SQLite FTS5 creation when available, substring fallback, snippets, and result ranking.
- Create `workeventagent/correction_store.py`: correction request validation, same-project correction writes, cross-project journal lifecycle, recovery scan, and deterministic correction event IDs.
- Modify `workeventagent/gui.py`: add `inbox_*`, `search`, `correct_event`, and `correction_recoveries` JSON handlers that wrap the new focused modules.
- Modify `client/main.js`: IPC handlers for inbox/search/correction commands and event broadcasts after card updates.
- Modify `client/preload.js`: expose `window.wea.inbox*`, `window.wea.search`, and `window.wea.correctEvent` methods.
- Modify `client/windows/capture.js`: replace single-slot proposal ownership with inbox card rendering and capture-card actions.
- Modify `client/windows/main.html`: add Inbox view, Search view, correction modal, and per-card/per-result action targets.
- Modify `client/windows/main.css`: Inbox card list, search results, correction modal, and compact quick-capture card styles.
- Modify `client/windows/main.js`: Inbox view loading/actions, global search, result navigation, and correction modal flow.
- Test `tests/test_inbox_store.py`: storage, retention, attachment cleanup, card state transitions.
- Test `tests/test_search_store.py`: search extraction, FTS/fallback behavior, report/inbox search, snippets.
- Test `tests/test_correction_store.py`: correction event rendering, target-first journal recovery, crash matrix.
- Modify `tests/test_gui.py`: JSON command coverage for inbox/search/correction handlers.
- Modify `tests/test_capture_renderer.py`: quick capture multiple in-flight cards and no hidden single-slot proposal ownership.

---

### Task 1: Capture Inbox Store

**Files:**
- Create: `workeventagent/inbox_store.py`
- Test: `tests/test_inbox_store.py`

**Interfaces:**
- Produces: `create_capture(workspace: Path, text: str, attachments: list[dict]) -> dict`
- Produces: `list_captures(workspace: Path) -> list[dict]`
- Produces: `update_capture(workspace: Path, capture_id: str, patch: dict) -> dict`
- Produces: `cancel_capture(workspace: Path, capture_id: str) -> dict`
- Produces: `archive_capture(workspace: Path, capture_id: str, archived: dict) -> dict`
- Produces: `capture_pending_dir(workspace: Path, capture_id: str) -> Path`

- [ ] **Step 1: Write failing storage tests**

Create `tests/test_inbox_store.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

from workeventagent.inbox_store import (
    archive_capture,
    cancel_capture,
    capture_pending_dir,
    create_capture,
    list_captures,
    update_capture,
)


def test_create_capture_persists_card_and_copies_attachment(tmp_path: Path) -> None:
    source = tmp_path / "clip.png"
    source.write_bytes(b"image-bytes")

    card = create_capture(tmp_path, "mapped KV cache blockers", [
        {"temp_path": str(source), "filename": "clip.png"},
    ])

    cards = list_captures(tmp_path)
    assert cards[0]["capture_id"] == card["capture_id"]
    assert cards[0]["state"] == "processing"
    assert cards[0]["text"] == "mapped KV cache blockers"
    pending_file = capture_pending_dir(tmp_path, card["capture_id"]) / "clip.png"
    assert pending_file.read_bytes() == b"image-bytes"


def test_update_capture_writes_proposal_without_losing_original_text(tmp_path: Path) -> None:
    card = create_capture(tmp_path, "original", [])

    updated = update_capture(tmp_path, card["capture_id"], {
        "state": "needs_confirmation",
        "proposal": {"event": {"summary": "summary"}},
        "selected_project": {"path": "project.md"},
    })

    assert updated["text"] == "original"
    assert updated["state"] == "needs_confirmation"
    assert updated["proposal"]["event"]["summary"] == "summary"


def test_cancel_capture_deletes_pending_directory_but_keeps_bounded_record(tmp_path: Path) -> None:
    source = tmp_path / "clip.png"
    source.write_bytes(b"image-bytes")
    card = create_capture(tmp_path, "cancel me", [{"temp_path": str(source), "filename": "clip.png"}])

    canceled = cancel_capture(tmp_path, card["capture_id"])

    assert canceled["state"] == "canceled"
    assert not capture_pending_dir(tmp_path, card["capture_id"]).exists()
    assert list_captures(tmp_path)[0]["state"] == "canceled"


def test_archive_capture_deletes_pending_directory_and_trims_only_terminal_cards(tmp_path: Path) -> None:
    for idx in range(105):
        card = create_capture(tmp_path, f"text {idx}", [])
        archive_capture(tmp_path, card["capture_id"], {
            "project_path": f"project-{idx}.md",
            "event_id": f"event-{idx}",
        })
    active = create_capture(tmp_path, "still active", [])

    cards = list_captures(tmp_path)

    assert any(c["capture_id"] == active["capture_id"] for c in cards)
    assert len([c for c in cards if c["state"] == "archived"]) <= 100
```

- [ ] **Step 2: Run tests to confirm red**

Run: `python -m pytest tests/test_inbox_store.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'workeventagent.inbox_store'`.

- [ ] **Step 3: Implement the inbox store**

Create `workeventagent/inbox_store.py` with these exact storage rules:

```python
INBOX_DIR = ".workeventagent"
INBOX_FILE = "inbox.json"
PENDING_DIR = "pending"
TERMINAL_STATES = {"archived", "canceled"}
ACTIVE_STATES = {"processing", "needs_confirmation", "error"}
```

Implementation requirements:

- `create_capture` generates `capture_id` as `cap-YYYYMMDD-HHMMSSmmm-<slug>` using UTC time and `make_stable_id(text[:48])`.
- It writes card state `processing` before route/propose starts.
- It copies each attachment into `<workspace>/.workeventagent/pending/<capture_id>/<safe filename>`.
- `inbox.json` writes through same-directory temp file plus `os.replace`.
- `update_capture` refuses unknown capture IDs with `ValueError("capture not found")`.
- `cancel_capture` sets state `canceled`, writes `updated_at`, and removes the pending directory.
- `archive_capture` sets state `archived`, stores `project_path` and `event_id`, writes `updated_at`, and removes the pending directory.
- Retention trims only terminal cards beyond the newest 100 terminal cards; active cards remain regardless of count.

- [ ] **Step 4: Run targeted tests**

Run: `python -m pytest tests/test_inbox_store.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add workeventagent/inbox_store.py tests/test_inbox_store.py
git commit -m "feat: add capture inbox store" -m "Why: Persist quick-capture lifecycle state before replacing renderer-local proposal ownership." -m "[金渐层/deepseek-v4-pro🐾]"
```

---

### Task 2: Inbox Backend Commands

**Files:**
- Modify: `workeventagent/gui.py`
- Test: `tests/test_gui.py`

**Interfaces:**
- Produces command: `inbox_create`
- Produces command: `inbox_list`
- Produces command: `inbox_process`
- Produces command: `inbox_commit`
- Produces command: `inbox_cancel`

- [ ] **Step 1: Add failing GUI command tests**

Add to `tests/test_gui.py`:

```python
def test_inbox_create_and_list_returns_processing_card(tmp_path: Path) -> None:
    result = handle_inbox_create({
        "workspace": str(tmp_path),
        "text": "mapped the cache blocker",
        "attachments": [],
    })

    assert result["ok"] is True
    assert result["card"]["state"] == "processing"

    listed = handle_inbox_list({"workspace": str(tmp_path)})
    assert listed["ok"] is True
    assert listed["cards"][0]["capture_id"] == result["card"]["capture_id"]


def test_inbox_cancel_cleans_pending_attachment(tmp_path: Path) -> None:
    src = tmp_path / "clip.png"
    src.write_bytes(b"image")
    created = handle_inbox_create({
        "workspace": str(tmp_path),
        "text": "cancel this",
        "attachments": [{"temp_path": str(src), "filename": "clip.png"}],
    })

    canceled = handle_inbox_cancel({
        "workspace": str(tmp_path),
        "capture_id": created["card"]["capture_id"],
    })

    assert canceled["ok"] is True
    assert canceled["card"]["state"] == "canceled"
    assert not (tmp_path / ".workeventagent" / "pending" / created["card"]["capture_id"]).exists()
```

- [ ] **Step 2: Run tests to confirm red**

Run: `python -m pytest tests/test_gui.py::test_inbox_create_and_list_returns_processing_card tests/test_gui.py::test_inbox_cancel_cleans_pending_attachment -q`

Expected: FAIL with missing handler names.

- [ ] **Step 3: Add handlers and command dispatch**

In `workeventagent/gui.py`, import inbox functions:

```python
from workeventagent.inbox_store import (
    archive_capture,
    cancel_capture,
    create_capture,
    get_capture,
    list_captures,
    update_capture,
)
```

Add commands to the `handlers` dict:

```python
"inbox_create": handle_inbox_create,
"inbox_list": handle_inbox_list,
"inbox_process": handle_inbox_process,
"inbox_commit": handle_inbox_commit,
"inbox_cancel": handle_inbox_cancel,
```

Implement:

```python
def handle_inbox_create(request: dict) -> dict:
    card = create_capture(
        Path(request["workspace"]),
        str(request["text"]),
        request.get("attachments", []),
    )
    return {"ok": True, "card": card}


def handle_inbox_list(request: dict) -> dict:
    return {"ok": True, "cards": list_captures(Path(request["workspace"]))}


def handle_inbox_cancel(request: dict) -> dict:
    card = cancel_capture(Path(request["workspace"]), request["capture_id"])
    return {"ok": True, "card": card}
```

Implement `handle_inbox_process` by loading the card text and pending attachment paths, calling existing `handle_route_propose`, and updating the card to `needs_confirmation` or `error`.

Implement `handle_inbox_commit` by loading the card, reading `proposal` and `selected_project.path` from the card, applying edited proposal fields from the request, calling existing `handle_commit`, and calling `archive_capture` only after `handle_commit` returns `ok: true`.

- [ ] **Step 4: Add process/commit tests with mocked opencode**

Add a test that patches `run_project_router` and `run_archivist` or uses a one-project workspace so routing is deterministic. Assert:

- `inbox_process` changes state to `needs_confirmation`.
- `inbox_commit` writes Markdown through existing commit path.
- archived card contains `project_path` and `event_id`.
- pending directory is deleted after commit.

Run: `python -m pytest tests/test_gui.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add workeventagent/gui.py tests/test_gui.py
git commit -m "feat: add inbox backend commands" -m "Why: Give Electron a persisted capture lifecycle API before renderer migration." -m "[金渐层/deepseek-v4-pro🐾]"
```

---

### Task 3: Electron Inbox IPC and Quick Capture Migration

**Files:**
- Modify: `client/main.js`
- Modify: `client/preload.js`
- Modify: `client/windows/capture.js`
- Test: `tests/test_capture_renderer.py`

**Interfaces:**
- Produces: `window.wea.createCapture(text, attachments)`
- Produces: `window.wea.listCaptures()`
- Produces: `window.wea.processCapture(captureId)`
- Produces: `window.wea.commitCapture(captureId, edits)`
- Produces: `window.wea.cancelCapture(captureId)`

- [ ] **Step 1: Add failing renderer test for multiple in-flight captures**

Add to `tests/test_capture_renderer.py`:

```python
def test_quick_capture_uses_cards_not_single_slot_proposal() -> None:
    script = textwrap.dedent(
        r"""
        const fs = require('fs');
        const vm = require('vm');

        class ClassList {
          constructor(classes = []) { this.classes = new Set(classes); }
          add(name) { this.classes.add(name); }
          remove(name) { this.classes.delete(name); }
          contains(name) { return this.classes.has(name); }
          toggle(name, force) {
            if (force === undefined) {
              if (this.classes.has(name)) this.classes.delete(name);
              else this.classes.add(name);
              return this.classes.has(name);
            }
            if (force) this.classes.add(name);
            else this.classes.delete(name);
            return force;
          }
        }

        class Element {
          constructor(id, classes = []) {
            this.id = id;
            this.classList = new ClassList(classes);
            this.value = '';
            this.textContent = '';
            this.disabled = false;
            this.listeners = {};
            this.children = [];
            this.scrollHeight = 260;
            this._innerHTML = '';
          }
          set innerHTML(value) {
            this._innerHTML = String(value);
            for (const match of this._innerHTML.matchAll(/id="([^"]+)"/g)) {
              if (!elements[match[1]]) elements[match[1]] = new Element(match[1]);
            }
          }
          get innerHTML() { return this._innerHTML; }
          addEventListener(name, cb) { this.listeners[name] = cb; }
          appendChild(child) { this.children.push(child); }
          focus() {}
        }

        const elements = {
          'cap-input': new Element('cap-input'),
          'cap-submit': new Element('cap-submit'),
          'cap-cancel': new Element('cap-cancel'),
          'cap-status': new Element('cap-status'),
          'cap-recent': new Element('cap-recent'),
          'cap-confirm': new Element('cap-confirm', ['hidden']),
          'cap-card-list': new Element('cap-card-list'),
          'cap-input-area': new Element('cap-input-area'),
          'cap-thumbs': new Element('cap-thumbs', ['hidden']),
          'cap-foot': new Element('cap-foot', ['cap-foot']),
          'cap': new Element('cap', ['cap']),
        };

        let onShowCapture = null;
        let createTexts = [];
        let listCalls = 0;
        let cards = [];
        const document = {
          querySelector(selector) {
            if (selector.startsWith('#')) return elements[selector.slice(1)] || null;
            if (selector === '.cap-foot') return elements['cap-foot'];
            if (selector === '.cap') return elements['cap'];
            return null;
          },
          createElement(tag) { return new Element(tag); },
        };

        function cardFor(id, text) {
          return {
            capture_id: id,
            state: 'needs_confirmation',
            text,
            selected_project: { project_id: 'p', title: 'Project', path: 'project.md' },
            proposal: {
              target: { project_id: 'p', task_id: id + '-task', task_title: '', new_task: false },
              confidence: 0.9,
              event: { status: 'in_progress', summary: text, next_action: 'next' },
            },
          };
        }

        const context = {
          console,
          document,
          requestAnimationFrame: (cb) => cb(),
          setTimeout: (cb) => cb(),
          window: { addEventListener: () => {} },
          wea: {
            getConfig: async () => ({}),
            listProjects: async () => ({ ok: true, projects: [{ project_id: 'p', title: 'Project', path: 'project.md' }] }),
            createCapture: async (text) => {
              createTexts.push(text);
              const card = cardFor(`cap-${createTexts.length}`, text);
              cards.push(card);
              return { ok: true, card };
            },
            processCapture: async (captureId) => ({ ok: true, card: cards.find((c) => c.capture_id === captureId) }),
            listCaptures: async () => {
              listCalls += 1;
              return { ok: true, cards };
            },
            commitCapture: async (captureId, edits) => {
              cards = cards.map((card) => card.capture_id === captureId
                ? { ...card, state: 'archived', archived: { event_id: card.proposal.event.summary } }
                : card);
              return { ok: true, card: cards.find((c) => c.capture_id === captureId) };
            },
            cancelCapture: async (captureId) => ({ ok: true, card: cards.find((c) => c.capture_id === captureId) }),
            onShowCapture: (cb) => { onShowCapture = cb; },
            onArchived: () => {},
            onInboxUpdated: () => {},
            resizeCapture: () => {},
            hideCapture: () => {},
            discardPending: () => {},
          },
        };
        context.globalThis = context;
        vm.createContext(context);
        vm.runInContext(fs.readFileSync('client/windows/capture.js', 'utf8'), context);

        (async () => {
          await context.boot();
          elements['cap-input'].value = 'first capture';
          await context.submit();
          elements['cap-input'].value = 'second capture';
          await context.submit();

          if (createTexts.join('|') !== 'first capture|second capture') {
            throw new Error(`createCapture texts wrong: ${createTexts.join('|')}`);
          }
          if (context.state.proposal) {
            throw new Error('state.proposal must not own capture proposal data after Inbox migration');
          }
          if (!elements['cap-card-list'].innerHTML.includes('cap-1') || !elements['cap-card-list'].innerHTML.includes('cap-2')) {
            throw new Error('both capture cards should render in the compact list');
          }

          await context.commitCard('cap-2');
          const first = cards.find((c) => c.capture_id === 'cap-1');
          const second = cards.find((c) => c.capture_id === 'cap-2');
          if (first.state !== 'needs_confirmation') throw new Error('committing cap-2 must not mutate cap-1');
          if (second.state !== 'archived') throw new Error('cap-2 should be archived');

          onShowCapture();
          await Promise.resolve();
          if (listCalls < 2) throw new Error('re-show should reload cards from the inbox store');
        })().catch((err) => {
          console.error(err && err.stack || err);
          process.exit(1);
        });
        """
    )
    result = subprocess.run(
        ["node", "-e", script],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
```

- [ ] **Step 2: Run renderer test to confirm red**

Run: `python -m pytest tests/test_capture_renderer.py::test_quick_capture_uses_cards_not_single_slot_proposal -q`

Expected: FAIL because `capture.js` still uses `state.proposal`.

- [ ] **Step 3: Add IPC handlers**

In `client/main.js`, add handlers near existing `wea:routePropose`:

```js
ipcMain.handle('wea:inboxCreate', async (_e, { text, attachments }) => {
  const c = cfg();
  if (!c.workspace) return { ok: false, kind: 'no_workspace', error: 'workspace not configured' };
  return callBackend('inbox_create', {
    workspace: c.workspace,
    text,
    attachments: (attachments || []).map((p) => ({ temp_path: p.tempPath || p, filename: p.filename || path.basename(p) })),
  }, c.pythonCmd);
});
```

Add `wea:inboxList`, `wea:inboxProcess`, `wea:inboxCommit`, and `wea:inboxCancel` using the backend commands from Task 2. Broadcast `wea:inbox-updated` to both windows after any `ok: true` update.

In `client/preload.js`, expose:

```js
createCapture: (text, attachments) => ipcRenderer.invoke('wea:inboxCreate', { text, attachments: attachments || [] }),
listCaptures: () => ipcRenderer.invoke('wea:inboxList'),
processCapture: (captureId) => ipcRenderer.invoke('wea:inboxProcess', { captureId }),
commitCapture: (captureId, edits) => ipcRenderer.invoke('wea:inboxCommit', { captureId, edits: edits || {} }),
cancelCapture: (captureId) => ipcRenderer.invoke('wea:inboxCancel', { captureId }),
onInboxUpdated: (cb) => ipcRenderer.on('wea:inbox-updated', (_e, payload) => cb(payload)),
```

- [ ] **Step 4: Migrate quick capture renderer**

In `client/windows/capture.js`:

- Replace `state.proposal` with `state.cards = []` and `state.selectedCardId = ""`.
- Keep `setCaptureState(phase, data)` only for visibility/layout; it must not store proposal data.
- `submit()` calls `wea.createCapture(text, state.pending)`, clears input/attachments after `ok`, renders the created card, then calls `wea.processCapture(capture_id)`.
- Confirmation card rendering receives a full card and reads `card.proposal` directly.
- Confirm button calls `wea.commitCapture(card.capture_id, edits)`.
- Retry button calls `wea.processCapture(card.capture_id)`.
- Cancel button calls `wea.cancelCapture(card.capture_id)`.
- `onShowCapture` and `onInboxUpdated` call `wea.listCaptures()` and render active cards.

- [ ] **Step 5: Run renderer tests**

Run: `python -m pytest tests/test_capture_renderer.py -q`

Expected: all capture renderer tests pass after updating old tests to assert card behavior instead of single-slot behavior.

- [ ] **Step 6: Run JS syntax checks**

Run:

```powershell
node --check client/main.js
node --check client/preload.js
node --check client/windows/capture.js
```

Expected: all commands exit `0`.

- [ ] **Step 7: Commit**

```powershell
git add client/main.js client/preload.js client/windows/capture.js tests/test_capture_renderer.py
git commit -m "feat: migrate quick capture to inbox cards" -m "Why: Support multiple in-flight captures without hidden renderer-local proposal state." -m "[金渐层/deepseek-v4-pro🐾]"
```

---

### Task 4: Main Window Inbox View

**Files:**
- Modify: `client/windows/main.html`
- Modify: `client/windows/main.css`
- Modify: `client/windows/main.js`

**Interfaces:**
- Consumes: `window.wea.listCaptures`, `commitCapture`, `cancelCapture`, `processCapture`
- Produces UI groups: Needs confirmation, Processing, Errors, Recent archived

- [ ] **Step 1: Add Inbox tab markup**

In `client/windows/main.html`, add a tab with `data-view="inbox"` and a view container:

```html
<section id="inbox-view" class="view hidden">
  <div class="view-head">
    <h2>Inbox</h2>
    <button class="ghost" id="inbox-refresh">Refresh</button>
  </div>
  <div id="inbox-body" class="inbox-body"></div>
</section>
```

- [ ] **Step 2: Add renderer functions**

In `client/windows/main.js`, add:

```js
async function loadInbox() {
  const res = await wea.listCaptures();
  state.inboxCards = res && res.ok ? (res.cards || []) : [];
  renderInbox();
}

function renderInbox() {
  const groups = {
    needs_confirmation: [],
    processing: [],
    error: [],
    archived: [],
    canceled: [],
  };
  (state.inboxCards || []).forEach((card) => {
    if (groups[card.state]) groups[card.state].push(card);
  });
  $('#inbox-body').innerHTML = [
    renderInboxGroup('Needs confirmation', groups.needs_confirmation),
    renderInboxGroup('Processing', groups.processing),
    renderInboxGroup('Errors', groups.error),
    renderInboxGroup('Recent archived', groups.archived.slice(0, 20)),
  ].join('');
  bindInboxActions();
}
```

`bindInboxActions()` must bind confirm/edit fields to `wea.commitCapture(card.capture_id, edits)`, retry to `wea.processCapture`, cancel to `wea.cancelCapture`, and open target to `selectProject` by matching `project_path`.

- [ ] **Step 3: Update `switchView`**

Add:

```js
$('#inbox-view').classList.toggle('hidden', view !== 'inbox');
if (view === 'inbox') loadInbox();
```

- [ ] **Step 4: Add CSS**

Add stable card dimensions and non-overlapping controls:

```css
.inbox-body { display: grid; gap: 14px; }
.inbox-group { display: grid; gap: 8px; }
.inbox-card { border: 1px solid var(--line); border-radius: 8px; padding: 12px; background: var(--panel); }
.inbox-card textarea { min-height: 64px; resize: vertical; }
.inbox-actions { display: flex; gap: 8px; flex-wrap: wrap; }
```

- [ ] **Step 5: Verify syntax and manual smoke path**

Run:

```powershell
node --check client/windows/main.js
node --check client/main.js
node --check client/preload.js
```

Expected: all exit `0`.

Manual smoke after starting Electron in a clean workspace:

- Submit two quick captures.
- Open Inbox tab.
- Confirm the second card before the first.
- Reopen quick capture and verify the first card remains active.

- [ ] **Step 6: Commit**

```powershell
git add client/windows/main.html client/windows/main.css client/windows/main.js
git commit -m "feat: add capture inbox view" -m "Why: Make asynchronous capture state visible and recoverable from the main app." -m "[金渐层/deepseek-v4-pro🐾]"
```

---

### Task 5: Deterministic Global Search Backend

**Files:**
- Create: `workeventagent/search_store.py`
- Modify: `workeventagent/gui.py`
- Test: `tests/test_search_store.py`
- Test: `tests/test_gui.py`

**Interfaces:**
- Produces: `build_search_documents(workspace: Path) -> list[dict]`
- Produces: `search_workspace(workspace: Path, query: str, limit: int = 50) -> list[dict]`
- Produces command: `search`

- [ ] **Step 1: Write failing search tests**

Create `tests/test_search_store.py`:

```python
from pathlib import Path

from workeventagent.search_store import build_search_documents, search_workspace


PROJECT = """---
project_id: search-project
title: Search Project
doc_kind: work_project
created: 2026-07-04
updated: 2026-07-04
---

# Search Project

## Current Snapshot

## Work Map
### Item: Retrieval Trust <!-- item:retrieval-trust -->
- background: user needs to find archived blockers later
#### Task: KV cache blocker search <!-- task:kv-cache-search -->
- status: in_progress
- next_action: Build deterministic search
- last_event_id: event-1

## Decisions

## Attachments

## Timeline
- 2026-07-04T12:00:00+00:00 <!-- event:event-1 -->
  - task_id: kv-cache-search
  - input: Looked at KV cache routing
  - summary: Fixed KV cache blocker notes
  - status: in_progress
  - next_action: Build deterministic search

## Daily / Weekly Rollups
"""


def test_search_finds_task_title_timeline_and_item_background(tmp_path: Path) -> None:
    (tmp_path / "project.md").write_text(PROJECT, encoding="utf-8")
    docs = build_search_documents(tmp_path)

    kinds = {d["kind"] for d in docs}

    assert {"project", "item", "task", "timeline"} <= kinds
    assert search_workspace(tmp_path, "KV cache blocker")[0]["kind"] in {"task", "timeline"}
    assert search_workspace(tmp_path, "archived blockers later")[0]["kind"] == "item"


def test_search_reads_report_files(tmp_path: Path) -> None:
    reports = tmp_path / "reports" / "daily"
    reports.mkdir(parents=True)
    (reports / "2026-07-04.md").write_text("# Daily\n\nInference chain summary", encoding="utf-8")

    results = search_workspace(tmp_path, "Inference chain")

    assert results[0]["kind"] == "report"
```

- [ ] **Step 2: Run tests to confirm red**

Run: `python -m pytest tests/test_search_store.py -q`

Expected: FAIL with missing module.

- [ ] **Step 3: Implement deterministic search**

`search_store.py` requirements:

- Parse projects through existing `scan_workspace`.
- Reuse `_parse_work_map_items`, `_parse_work_map_tasks`, and `_parse_timeline_events` from `gui.py` or move those parsers to a shared module if imports would create a cycle.
- Include report files under `<workspace>/reports/**/*.md`.
- Include `inbox.json` cards by text, error message, selected project title, and archived event_id.
- If SQLite FTS5 is available, use an in-memory FTS table for ranking.
- If FTS5 is unavailable, fallback to case-insensitive substring search.
- Return result dicts with `kind`, `title`, `snippet`, `path`, `project_id`, `item_id`, `task_id`, `event_id`, and `timestamp` when known.

- [ ] **Step 4: Add GUI handler**

In `workeventagent/gui.py`:

```python
def handle_search(request: dict) -> dict:
    query = str(request.get("query", "")).strip()
    if not query:
        return {"ok": False, "kind": "invalid_input", "error": "query is required"}
    results = search_workspace(Path(request["workspace"]), query, int(request.get("limit", 50)))
    return {"ok": True, "results": results}
```

Add `"search": handle_search` to command dispatch and GUI tests for empty query and successful result.

- [ ] **Step 5: Run tests**

Run:

```powershell
python -m pytest tests/test_search_store.py tests/test_gui.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add workeventagent/search_store.py workeventagent/gui.py tests/test_search_store.py tests/test_gui.py
git commit -m "feat: add deterministic global search backend" -m "Why: Let users retrieve archived work without invoking opencode for each query." -m "[金渐层/deepseek-v4-pro🐾]"
```

---

### Task 6: Search UI and Navigation

**Files:**
- Modify: `client/main.js`
- Modify: `client/preload.js`
- Modify: `client/windows/main.html`
- Modify: `client/windows/main.css`
- Modify: `client/windows/main.js`

**Interfaces:**
- Produces: `window.wea.search(query, limit)`
- Produces UI: global search box and results view

- [ ] **Step 1: Add IPC and preload API**

In `client/main.js`:

```js
ipcMain.handle('wea:search', async (_e, { query, limit }) => {
  const c = cfg();
  if (!c.workspace) return { ok: false, kind: 'no_workspace', error: 'workspace not configured' };
  return callBackend('search', { workspace: c.workspace, query, limit: limit || 50 }, c.pythonCmd);
});
```

In `client/preload.js`:

```js
search: (query, limit) => ipcRenderer.invoke('wea:search', { query, limit: limit || 50 }),
```

- [ ] **Step 2: Add Search view markup**

Add a tab `data-view="search"` and:

```html
<section id="search-view" class="view hidden">
  <div class="search-bar">
    <input id="global-search" aria-label="Search projects, tasks, timeline, reports" />
    <button class="primary" id="search-run">Search</button>
  </div>
  <div id="search-results" class="search-results"></div>
</section>
```

- [ ] **Step 3: Add renderer logic**

In `client/windows/main.js`:

```js
async function runSearch() {
  const query = $('#global-search').value.trim();
  if (!query) return;
  const res = await wea.search(query, 50);
  if (!res || !res.ok) {
    $('#search-results').innerHTML = `<div class="empty">${esc((res && res.error) || 'Search failed')}</div>`;
    return;
  }
  renderSearchResults(res.results || []);
}
```

Each result click:

- If `project_id`/`path` matches a project, call `selectProject(project)` then switch to `tasks` or `timeline`.
- If `kind === "report"`, show the report path and use `wea.openProjectDir(path)` for file location.

- [ ] **Step 4: Run JS syntax checks**

Run:

```powershell
node --check client/main.js
node --check client/preload.js
node --check client/windows/main.js
```

Expected: all exit `0`.

- [ ] **Step 5: Commit**

```powershell
git add client/main.js client/preload.js client/windows/main.html client/windows/main.css client/windows/main.js
git commit -m "feat: add global search UI" -m "Why: Make archived work retrievable from the desktop app." -m "[金渐层/deepseek-v4-pro🐾]"
```

---

### Task 7: Same-Project Correction Workflow

**Files:**
- Create: `workeventagent/correction_store.py`
- Modify: `workeventagent/gui.py`
- Test: `tests/test_correction_store.py`
- Test: `tests/test_gui.py`

**Interfaces:**
- Produces: `correct_event_same_project(project_path: Path, db_path: Path, request: dict) -> dict`
- Produces command: `correct_event`

- [ ] **Step 1: Write failing same-project correction test**

Create `tests/test_correction_store.py`:

```python
from pathlib import Path

from workeventagent.correction_store import correct_event_same_project


def test_same_project_correction_appends_event_and_preserves_original(tmp_path: Path) -> None:
    project = tmp_path / "project.md"
    project.write_text(PROJECT_WITH_ONE_EVENT, encoding="utf-8")
    db = tmp_path / "index.sqlite"

    result = correct_event_same_project(project, db, {
        "original_event_id": "event-1",
        "reason": "Wrong summary",
        "summary": "Corrected summary",
        "status": "done",
        "next_action": "",
        "target_task_id": "task-a",
    })

    text = project.read_text(encoding="utf-8")
    assert result["ok"] is True
    assert "<!-- event:event-1 -->" in text
    assert "event_type: correction" in text
    assert "corrects_event_id: event-1" in text
    assert "summary: Corrected summary" in text
```

Define `PROJECT_WITH_ONE_EVENT` in the test as a complete work_project Markdown with one item, one task, and one Timeline event.

- [ ] **Step 2: Run test to confirm red**

Run: `python -m pytest tests/test_correction_store.py -q`

Expected: FAIL with missing module.

- [ ] **Step 3: Implement same-project correction**

Implementation requirements:

- Validate original event exists in `## Timeline`.
- Validate `target_task_id` exists in Work Map.
- Append a new Timeline event with fields:
  - `event_type: correction`
  - `corrects_event_id: <original_event_id>`
  - `reason: <reason>`
  - `corrected_task_id: <target_task_id>`
  - `summary`, `status`, `next_action`
- Update only the affected target task block in Work Map.
- Do not edit the original event.
- Use `write_project_atomically`, then `init_db` and `rebuild_index`.

- [ ] **Step 4: Add GUI handler**

In `workeventagent/gui.py`:

```python
def handle_correct_event(request: dict) -> dict:
    project_path = Path(request["project_path"])
    db_path = Path(request["db_path"])
    if request.get("target_project_path") and Path(request["target_project_path"]) != project_path:
        return correct_event_cross_project(project_path, Path(request["target_project_path"]), db_path, request)
    return correct_event_same_project(project_path, db_path, request)
```

Add `"correct_event": handle_correct_event` to dispatch.

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/test_correction_store.py tests/test_gui.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add workeventagent/correction_store.py workeventagent/gui.py tests/test_correction_store.py tests/test_gui.py
git commit -m "feat: add same-project correction workflow" -m "Why: Preserve append-only history while letting users fix wrong archive details." -m "[金渐层/deepseek-v4-pro🐾]"
```

---

### Task 8: Cross-Project Correction Journal and Recovery

**Files:**
- Modify: `workeventagent/correction_store.py`
- Test: `tests/test_correction_store.py`

**Interfaces:**
- Produces: `correct_event_cross_project(source_path: Path, target_path: Path, db_path: Path, request: dict) -> dict`
- Produces: `list_pending_corrections(workspace: Path) -> list[dict]`
- Produces: `resume_correction(workspace: Path, correction_id: str) -> dict`

- [ ] **Step 1: Add crash matrix tests**

Add tests covering these four states:

```python
import json

from workeventagent.correction_store import list_pending_corrections, resume_correction


def _project_text(project_id: str, task_id: str, event_lines: list[str]) -> str:
    return "\n".join([
        "---",
        f"project_id: {project_id}",
        f"title: {project_id}",
        "doc_kind: work_project",
        "created: 2026-07-04",
        "updated: 2026-07-04",
        "---",
        "",
        "## Current Snapshot",
        "",
        "## Work Map",
        "### Item: Trust Item <!-- item:item-a -->",
        f"#### Task: Trust Task <!-- task:{task_id} -->",
        "- status: in_progress",
        "- next_action:",
        "- last_event_id:",
        "",
        "## Decisions",
        "",
        "## Attachments",
        "",
        "## Timeline",
        *event_lines,
        "",
        "## Daily / Weekly Rollups",
        "",
    ])


def _target_event() -> list[str]:
    return [
        "- 2026-07-04T12:40:00+00:00 <!-- event:corr-target-event -->",
        "  - task_id: target-task",
        "  - event_type: correction_move",
        "  - correction_id: corr-1",
        "  - source_event_id: source-event-1",
        "  - summary: Corrected target summary",
        "  - status: in_progress",
        "  - next_action: Check recovery",
    ]


def _source_correction_event() -> list[str]:
    return [
        "- 2026-07-04T12:40:01+00:00 <!-- event:corr-source-event -->",
        "  - task_id: source-task",
        "  - event_type: correction",
        "  - correction_id: corr-1",
        "  - corrects_event_id: source-event-1",
        "  - target_event_id: corr-target-event",
        "  - summary: Moved to target project",
        "  - status: in_progress",
        "  - next_action:",
    ]


def _write_cross_project_fixture(
    tmp_path: Path,
    stage: str,
    target_written: bool,
    source_written: bool,
) -> dict:
    source = tmp_path / "source.md"
    target = tmp_path / "target.md"
    source_events = [
        "- 2026-07-04T12:00:00+00:00 <!-- event:source-event-1 -->",
        "  - task_id: source-task",
        "  - input: Original",
        "  - summary: Original summary",
        "  - status: in_progress",
        "  - next_action:",
    ]
    if source_written:
        source_events.extend(_source_correction_event())
    target_events = _target_event() if target_written else []
    source.write_text(_project_text("source-project", "source-task", source_events), encoding="utf-8")
    target.write_text(_project_text("target-project", "target-task", target_events), encoding="utf-8")

    journal_dir = tmp_path / ".workeventagent" / "corrections"
    journal_dir.mkdir(parents=True)
    journal = {
        "correction_id": "corr-1",
        "source_project_path": str(source),
        "source_item_id": "item-a",
        "source_task_id": "source-task",
        "original_event_id": "source-event-1",
        "target_project_path": str(target),
        "target_item_id": "item-a",
        "target_task_id": "target-task",
        "summary": "Corrected target summary",
        "status": "in_progress",
        "next_action": "Check recovery",
        "target_event_id": "corr-target-event",
        "source_correction_event_id": "corr-source-event",
        "stage": stage,
        "last_error": "",
    }
    (journal_dir / "corr-1.json").write_text(json.dumps(journal), encoding="utf-8")
    return {"source": source, "target": target, "journal": journal_dir / "corr-1.json"}


def test_cross_project_intent_without_target_is_retryable(tmp_path: Path) -> None:
    fixture = _write_cross_project_fixture(tmp_path, "intent", target_written=False, source_written=False)

    result = resume_correction(tmp_path, "corr-1")

    assert result["ok"] is True
    assert "corr-target-event" in fixture["target"].read_text(encoding="utf-8")
    assert "corr-source-event" in fixture["source"].read_text(encoding="utf-8")
    assert "target_event_id: corr-target-event" in fixture["source"].read_text(encoding="utf-8")


def test_cross_project_intent_with_target_already_written_advances_to_target_written(tmp_path: Path) -> None:
    fixture = _write_cross_project_fixture(tmp_path, "intent", target_written=True, source_written=False)

    result = resume_correction(tmp_path, "corr-1")

    target_text = fixture["target"].read_text(encoding="utf-8")
    assert result["ok"] is True
    assert target_text.count("corr-target-event") == 1
    assert "corr-source-event" in fixture["source"].read_text(encoding="utf-8")


def test_cross_project_target_written_without_source_is_visible_and_resumable(tmp_path: Path) -> None:
    fixture = _write_cross_project_fixture(tmp_path, "target_written", target_written=True, source_written=False)

    pending = list_pending_corrections(tmp_path)
    result = resume_correction(tmp_path, "corr-1")

    assert pending[0]["correction_id"] == "corr-1"
    assert result["ok"] is True
    assert "corr-source-event" in fixture["source"].read_text(encoding="utf-8")
    assert fixture["source"].read_text(encoding="utf-8").count("corr-source-event") == 1


def test_cross_project_target_written_with_source_already_written_marks_done(tmp_path: Path) -> None:
    fixture = _write_cross_project_fixture(tmp_path, "target_written", target_written=True, source_written=True)

    result = resume_correction(tmp_path, "corr-1")

    source_text = fixture["source"].read_text(encoding="utf-8")
    target_text = fixture["target"].read_text(encoding="utf-8")
    journal = json.loads(fixture["journal"].read_text(encoding="utf-8"))
    assert result["ok"] is True
    assert journal["stage"] == "done"
    assert source_text.count("corr-source-event") == 1
    assert target_text.count("corr-target-event") == 1
```

- [ ] **Step 2: Run crash tests to confirm red**

Run: `python -m pytest tests/test_correction_store.py -q`

Expected: FAIL on missing cross-project functions.

- [ ] **Step 3: Implement journal schema**

Journal file:

```json
{
  "correction_id": "corr-20260704-124000000-event-1",
  "source_project_path": "D:/worklogs/source.md",
  "source_item_id": "source-item",
  "source_task_id": "source-task",
  "original_event_id": "event-1",
  "target_project_path": "D:/worklogs/target.md",
  "target_item_id": "target-item",
  "target_task_id": "target-task",
  "summary": "Corrected summary",
  "status": "in_progress",
  "next_action": "Next action",
  "target_event_id": "corr-20260704-124000000-target-task",
  "source_correction_event_id": "corr-20260704-124000000-source-task-correction",
  "stage": "intent",
  "last_error": ""
}
```

Use `correction_id` and deterministic event IDs for idempotency. If an event with the deterministic ID already exists, treat that write step as complete.

- [ ] **Step 4: Implement target-first write order**

`correct_event_cross_project` must:

1. Validate source original event and target task anchors.
2. Write journal at `intent`.
3. Append target event with `target_event_id`, `correction_id`, and `source_event_id`.
4. Atomically write target project and rebuild index.
5. Update journal to `target_written`.
6. Append source correction event with `source_correction_event_id`, `correction_id`, `corrects_event_id`, and target event reference.
7. Atomically write source project and rebuild index.
8. Update journal to `source_written`.
9. Verify both deterministic event IDs exist and mark journal `done`.

- [ ] **Step 5: Implement recovery**

`resume_correction` must:

- recover `intent` with no target by continuing at target write
- recover `intent` with target already written by advancing to `target_written`
- recover `target_written` without source by writing source correction
- recover `target_written` with source already written by marking `source_written` then `done`
- recover `source_written` by verifying both events and marking `done`
- return `{"ok": true, "stage": "done"}` after successful recovery

- [ ] **Step 6: Run correction tests**

Run: `python -m pytest tests/test_correction_store.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add workeventagent/correction_store.py tests/test_correction_store.py
git commit -m "feat: add cross-project correction recovery" -m "Why: Make cross-file corrections recoverable without dangling event references." -m "[金渐层/deepseek-v4-pro🐾]"
```

---

### Task 9: Correction UI

**Files:**
- Modify: `client/main.js`
- Modify: `client/preload.js`
- Modify: `client/windows/main.html`
- Modify: `client/windows/main.css`
- Modify: `client/windows/main.js`

**Interfaces:**
- Produces: `window.wea.correctEvent(request)`
- Produces: correction modal entry points from Timeline rows, archived Inbox cards, and Search results

- [ ] **Step 1: Add IPC/preload**

In `client/main.js`:

```js
ipcMain.handle('wea:correctEvent', async (_e, request) => {
  const c = cfg();
  return callBackend('correct_event', {
    ...request,
    db_path: dbPathFor(c.workspace),
  }, c.pythonCmd);
});
```

In `client/preload.js`:

```js
correctEvent: (request) => ipcRenderer.invoke('wea:correctEvent', request || {}),
```

- [ ] **Step 2: Add correction modal**

In `main.html`, add a modal with fields:

- original event summary
- source project/task
- target project select
- target item/task select
- summary input
- status select
- next_action input
- reason input
- before/after preview
- confirm/cancel buttons

- [ ] **Step 3: Add entry points**

In `renderTimeline`, add a `Correct` button per event row. In Inbox archived cards and Search timeline results, add the same action. The click handler calls:

```js
openCorrectionModal({
  originalEventId: event.event_id,
  sourceProjectPath: state.currentProject.path,
  sourceTaskId: event.task_id,
  summary: event.summary,
  status: event.status,
  nextAction: event.next_action || '',
});
```

- [ ] **Step 4: Submit correction**

`submitCorrection()` builds:

```js
{
  project_path: sourceProjectPath,
  target_project_path: selectedTargetProjectPath,
  original_event_id: originalEventId,
  target_task_id: selectedTargetTaskId,
  summary,
  status,
  next_action,
  reason
}
```

On success: close modal, refresh current project, reload Inbox/Search if those views are active, and toast success.

- [ ] **Step 5: Syntax check and manual smoke**

Run:

```powershell
node --check client/main.js
node --check client/preload.js
node --check client/windows/main.js
```

Manual smoke:

- Correct a timeline event summary in the same project.
- Correct an archived inbox card to another task.
- Correct an event to another project; kill and restart between target and source writes only if a temporary test hook from Task 8 is available.

- [ ] **Step 6: Commit**

```powershell
git add client/main.js client/preload.js client/windows/main.html client/windows/main.css client/windows/main.js
git commit -m "feat: add correction workflow UI" -m "Why: Let users repair wrong archive targets without editing Markdown manually." -m "[金渐层/deepseek-v4-pro🐾]"
```

---

### Task 10: Final Integration Verification and Docs

**Files:**
- Modify: `client/README.md`
- Modify: `docs/designs/F001-client-architecture.md` only if new IPC method list needs a source-of-truth update.
- Modify: `docs/designs/F003-trust-workflows.md` only if implementation names differ from this plan.

**Interfaces:**
- Produces: verified F003 acceptance checklist.

- [ ] **Step 1: Run backend tests**

Run: `python -m pytest -q`

Expected: all tests pass.

- [ ] **Step 2: Run JS syntax checks**

Run:

```powershell
node --check client/main.js
node --check client/preload.js
node --check client/windows/main.js
node --check client/windows/capture.js
```

Expected: all commands exit `0`.

- [ ] **Step 3: Run Electron smoke**

Start the client:

```powershell
Set-Location client
npm.cmd start
```

Smoke checklist:

- Quick capture submits two cards back-to-back.
- Inbox shows both cards.
- Confirming one card writes Markdown and leaves the other card intact.
- Search finds a task title, item background, timeline summary, and report phrase.
- Correction appends a correction event and original event remains unchanged.
- Cross-project correction recovery shows pending journal state after an injected failure.

- [ ] **Step 4: Update docs**

Document:

- where Inbox state is stored
- how archived/canceled retention works
- how pending attachment cleanup works
- how search behaves without opencode
- how correction journals recover after a crash

- [ ] **Step 5: Commit**

```powershell
git add client/README.md docs/designs/F001-client-architecture.md docs/designs/F003-trust-workflows.md
git commit -m "docs: add trust workflow usage and contracts" -m "Why: Make F003 storage, search, and correction recovery behavior reviewable before acceptance." -m "[金渐层/deepseek-v4-pro🐾]"
```

---

## Plan Self-Review

Spec coverage:

- Capture Inbox persistent card storage: Task 1.
- Multiple in-flight captures and quick-capture migration away from single-slot `state.proposal`: Task 3.
- Main Inbox view with confirmation/retry/cancel/open target: Task 4.
- Pending attachment directory cleanup after commit/cancel: Task 1 and Task 2.
- Deterministic search across projects, item backgrounds, tasks, timeline, reports, and inbox cards: Task 5.
- Search UI and navigation: Task 6.
- Same-project correction preserving original timeline event: Task 7.
- Cross-project target-first journal recovery with deterministic source and target event IDs: Task 8.
- Correction UI from Timeline, Inbox, and Search: Task 9.
- Final verification and docs: Task 10.

Reviewer refinements folded in:

- R1: source correction id and crash matrix are in Task 8.
- R2: positive `setCaptureState()` view-helper contract is in Global Constraints and Task 3.
- R3: pending attachment directory cleanup is in Global Constraints, Task 1, and Task 2.

Marker scan:

- The plan contains no unresolved marker phrases or dummy assertions.
- Each task defines exact files, interfaces, commands, and expected verification.

Execution note:

- Recommended execution mode is subagent-driven development with review after each task. Inbox, Search, and Correction are separable enough that one task can be rejected without invalidating the rest.
