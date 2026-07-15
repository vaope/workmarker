# F007 Main Window Global Hotkey Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a separate configurable global shortcut that focuses the main WorkEventAgent window and hides it to the tray on the next focused-window trigger, without weakening the existing quick-capture shortcut.

**Architecture:** Extract global-shortcut registration into a pure manager that can be tested with a fake Electron `globalShortcut`. Startup registration treats quick-capture and main-window shortcuts independently so a new `mainHotkey` conflict cannot disable the existing capture shortcut. Settings updates use a transactional pair registration and restore the previous valid registrations if either new accelerator conflicts. The existing `hotkey` config key remains the quick-capture shortcut for backward compatibility; `mainHotkey` is added independently.

**Tech Stack:** Electron 33 main process, browser JavaScript settings UI, Node-backed pytest tests, no new dependencies.

## Global Constraints

- `hotkey` remains the quick-capture accelerator and defaults to `CommandOrControl+Shift+Space`.
- `mainHotkey` is independent and defaults to `CommandOrControl+Shift+M`.
- The two configured accelerators must be nonblank and different.
- Triggering the main shortcut when the main window is absent creates, shows, and focuses it.
- Triggering it when the main window exists but is hidden or not focused shows and focuses it.
- Triggering it when the main window is visible and focused hides it to the tray.
- Startup registration is independent: if `mainHotkey` conflicts, keep the existing quick-capture `hotkey` registered and return a visible main-hotkey failure state.
- During settings updates, if registration of either requested shortcut fails, restore the previous valid registrations and persist the previous configuration.
- Do not call `globalShortcut.unregisterAll()` during a settings update.
- Preserve `contextIsolation: true`, `nodeIntegration: false`, and preload-only config IPC.
- Do not change project Markdown, schema, capture, Inbox, reports, or synthesis contracts.
- Do not add npm dependencies.

## File Structure

- Create `client/hotkey_manager.js`: transactional pair registration with injected `globalShortcut` and callbacks.
- Create `tests/test_hotkey_manager.py`: Node tests using a fake shortcut registry.
- Modify `client/config.js`: add `mainHotkey` default while preserving old config files.
- Modify `client/main.js`: main-window toggle action, pair registration, rollback-aware config updates.
- Modify `client/windows/main.html` and `main.js`: two labeled key-capture inputs and inline error handling.
- Modify `tests/test_main_renderer_static.py`: integration guards.
- Modify `client/README.md`: document both shortcuts.

---

### Task 1: Build a transactional hotkey pair manager

**Files:**
- Create: `client/hotkey_manager.js`
- Create: `tests/test_hotkey_manager.py`

**Interfaces:**
- Produces `createHotkeyManager(globalShortcut, actions)`.
- Returned manager exposes `registerStartupPair(pair)`, `registerPair(pair)`, `activePair()`, and `dispose()`.
- `pair` shape is `{ capture: string, main: string }`; `actions` shape is `{ capture: Function, main: Function }`.

- [ ] **Step 1: Write failing manager tests**

Create `tests/test_hotkey_manager.py`:

```python
import json
import subprocess


def _run(script: str) -> dict:
    result = subprocess.run(["node", "-e", script], check=True, text=True, capture_output=True)
    return json.loads(result.stdout)


def test_registers_distinct_capture_and_main_actions() -> None:
    result = _run(r"""
const { createHotkeyManager } = require('./client/hotkey_manager');
const callbacks = new Map();
const shortcut = {
  register(key, cb) { if (callbacks.has(key)) return false; callbacks.set(key, cb); return true; },
  unregister(key) { callbacks.delete(key); },
};
const calls = [];
const manager = createHotkeyManager(shortcut, {
  capture: () => calls.push('capture'),
  main: () => calls.push('main'),
});
const registered = manager.registerPair({ capture: 'Ctrl+Shift+Space', main: 'Ctrl+Shift+M' });
callbacks.get('Ctrl+Shift+Space')();
callbacks.get('Ctrl+Shift+M')();
process.stdout.write(JSON.stringify({ registered, calls, active: manager.activePair() }));
""")
    assert result["registered"]["ok"] is True
    assert result["calls"] == ["capture", "main"]
    assert result["active"] == {"capture": "Ctrl+Shift+Space", "main": "Ctrl+Shift+M"}


def test_failed_pair_restores_previous_valid_registration() -> None:
    result = _run(r"""
const { createHotkeyManager } = require('./client/hotkey_manager');
const callbacks = new Map();
const blocked = new Set(['Ctrl+Alt+X']);
const shortcut = {
  register(key, cb) { if (blocked.has(key) || callbacks.has(key)) return false; callbacks.set(key, cb); return true; },
  unregister(key) { callbacks.delete(key); },
};
const manager = createHotkeyManager(shortcut, { capture() {}, main() {} });
manager.registerPair({ capture: 'Ctrl+Shift+Space', main: 'Ctrl+Shift+M' });
const failed = manager.registerPair({ capture: 'Ctrl+Alt+C', main: 'Ctrl+Alt+X' });
process.stdout.write(JSON.stringify({ failed, active: manager.activePair(), keys: [...callbacks.keys()].sort() }));
""")
    assert result["failed"]["ok"] is False
    assert result["active"] == {"capture": "Ctrl+Shift+Space", "main": "Ctrl+Shift+M"}
    assert result["keys"] == ["Ctrl+Shift+M", "Ctrl+Shift+Space"]


def test_startup_main_conflict_keeps_capture_shortcut() -> None:
    result = _run(r"""
const { createHotkeyManager } = require('./client/hotkey_manager');
const callbacks = new Map();
const blocked = new Set(['Ctrl+Shift+M']);
const shortcut = {
  register(key, cb) { if (blocked.has(key) || callbacks.has(key)) return false; callbacks.set(key, cb); return true; },
  unregister(key) { callbacks.delete(key); },
};
const calls = [];
const manager = createHotkeyManager(shortcut, {
  capture: () => calls.push('capture'),
  main: () => calls.push('main'),
});
const startup = manager.registerStartupPair({ capture: 'Ctrl+Shift+Space', main: 'Ctrl+Shift+M' });
callbacks.get('Ctrl+Shift+Space')();
process.stdout.write(JSON.stringify({ startup, calls, active: manager.activePair(), keys: [...callbacks.keys()].sort() }));
""")
    assert result["startup"]["ok"] is False
    assert result["startup"]["failed"] == "main"
    assert result["startup"]["captureRegistered"] is True
    assert result["startup"]["mainRegistered"] is False
    assert result["calls"] == ["capture"]
    assert result["active"] == {"capture": "Ctrl+Shift+Space", "main": ""}
    assert result["keys"] == ["Ctrl+Shift+Space"]


def test_rejects_duplicate_or_blank_pair_without_unregistering() -> None:
    result = _run(r"""
const { createHotkeyManager } = require('./client/hotkey_manager');
let unregisters = 0;
const shortcut = { register() { return true; }, unregister() { unregisters += 1; } };
const manager = createHotkeyManager(shortcut, { capture() {}, main() {} });
const blank = manager.registerPair({ capture: '', main: 'Ctrl+M' });
const duplicate = manager.registerPair({ capture: 'Ctrl+M', main: 'Ctrl+M' });
process.stdout.write(JSON.stringify({ blank, duplicate, unregisters }));
""")
    assert result["blank"]["kind"] == "invalid_accelerator"
    assert result["duplicate"]["kind"] == "duplicate_accelerator"
    assert result["unregisters"] == 0
```

- [ ] **Step 2: Run tests to verify red**

```powershell
python -m pytest tests/test_hotkey_manager.py -q
```

Expected: all tests fail because `client/hotkey_manager.js` is absent.

- [ ] **Step 3: Implement the manager**

Create `client/hotkey_manager.js`:

```javascript
function createHotkeyManager(globalShortcut, actions) {
  let active = { capture: '', main: '' };

  function normalized(pair) {
    return {
      capture: String(pair && pair.capture || '').trim(),
      main: String(pair && pair.main || '').trim(),
    };
  }

  function unregisterPair(pair) {
    if (pair.capture) globalShortcut.unregister(pair.capture);
    if (pair.main && pair.main !== pair.capture) globalShortcut.unregister(pair.main);
  }

  function tryRegister(pair) {
    if (!globalShortcut.register(pair.capture, actions.capture)) {
      return { ok: false, failed: 'capture' };
    }
    if (!globalShortcut.register(pair.main, actions.main)) {
      globalShortcut.unregister(pair.capture);
      return { ok: false, failed: 'main' };
    }
    return { ok: true };
  }

  function tryRegisterIndependent(pair) {
    const result = { capture: false, main: false };
    if (pair.capture) result.capture = globalShortcut.register(pair.capture, actions.capture);
    if (pair.main && pair.main !== pair.capture) result.main = globalShortcut.register(pair.main, actions.main);
    return result;
  }

  function registerStartupPair(candidate) {
    const next = normalized(candidate);
    if (!next.capture || !next.main) {
      return { ok: false, kind: 'invalid_accelerator', failed: !next.capture ? 'capture' : 'main', captureRegistered: false, mainRegistered: false, active: { ...active } };
    }
    if (next.capture === next.main) {
      const captureRegistered = globalShortcut.register(next.capture, actions.capture);
      active = { capture: captureRegistered ? next.capture : '', main: '' };
      return { ok: false, kind: 'duplicate_accelerator', failed: 'main', captureRegistered, mainRegistered: false, active: { ...active } };
    }
    const registered = tryRegisterIndependent(next);
    active = {
      capture: registered.capture ? next.capture : '',
      main: registered.main ? next.main : '',
    };
    return {
      ok: registered.capture && registered.main,
      kind: registered.capture && registered.main ? undefined : 'registration_conflict',
      failed: !registered.capture ? 'capture' : (!registered.main ? 'main' : undefined),
      captureRegistered: registered.capture,
      mainRegistered: registered.main,
      active: { ...active },
    };
  }

  function registerPair(candidate) {
    const next = normalized(candidate);
    if (!next.capture || !next.main) return { ok: false, kind: 'invalid_accelerator', active: { ...active } };
    if (next.capture === next.main) return { ok: false, kind: 'duplicate_accelerator', active: { ...active } };

    const previous = { ...active };
    unregisterPair(previous);
    const attempt = tryRegister(next);
    if (attempt.ok) {
      active = next;
      return { ok: true, active: { ...active } };
    }

    unregisterPair(next);
    if (previous.capture || previous.main) {
      const restored = tryRegisterIndependent(previous);
      if ((previous.capture && !restored.capture) || (previous.main && !restored.main)) {
        throw new Error('failed to restore previous global shortcuts');
      }
    }
    active = previous;
    return { ok: false, kind: 'registration_conflict', failed: attempt.failed, active: { ...active } };
  }

  return Object.freeze({
    registerStartupPair,
    registerPair,
    activePair: () => ({ ...active }),
    dispose() { unregisterPair(active); active = { capture: '', main: '' }; },
  });
}

module.exports = { createHotkeyManager };
```

- [ ] **Step 4: Run manager tests and syntax**

```powershell
python -m pytest tests/test_hotkey_manager.py -q
node --check client/hotkey_manager.js
```

Expected: `4 passed`; Node exits 0.

- [ ] **Step 5: Commit the manager**

```powershell
git add client/hotkey_manager.js tests/test_hotkey_manager.py
git commit -m "feat: add transactional global hotkey manager" -m "Why: Adding a main-window shortcut must not drop the working capture shortcut when a new accelerator conflicts."
```

---

### Task 2: Register and persist the main-window toggle shortcut

**Files:**
- Modify: `client/config.js`
- Modify: `client/main.js`
- Modify: `tests/test_hotkey_manager.py`
- Modify: `tests/test_main_renderer_static.py`

**Interfaces:**
- Consumes `createHotkeyManager` from Task 1.
- Produces `toggleMainWindow()`, independent startup registration via `registerStartupPair`, and rollback-aware transactional `wea:updateConfig` behavior via `registerPair`.

- [ ] **Step 1: Add integration guards**

```python
def test_main_process_has_independent_main_window_hotkey() -> None:
    config = Path("client/config.js").read_text(encoding="utf-8")
    source = Path("client/main.js").read_text(encoding="utf-8")
    assert "mainHotkey: 'CommandOrControl+Shift+M'" in config
    assert "function toggleMainWindow()" in source
    assert "mainWindow.isVisible() && mainWindow.isFocused()" in source
    assert "hotkeyManager.registerStartupPair" in source
    assert "hotkeyManager.registerPair" in source
    assert "globalShortcut.unregisterAll()" not in source


def test_hotkey_config_failure_returns_previous_pair() -> None:
    source = Path("client/main.js").read_text(encoding="utf-8")
    update = source[source.index("ipcMain.handle('wea:updateConfig'"):source.index("ipcMain.handle('wea:pickWorkspaceDir'")]
    assert "registration.active.capture" in update
    assert "registration.active.main" in update
    assert "hotkeyRegistered: false" in update
    assert "mainHotkeyRegistered: false" in update
```

- [ ] **Step 2: Run guards to verify red**

```powershell
python -m pytest tests/test_hotkey_manager.py tests/test_main_renderer_static.py -q
```

Expected: new static tests fail.

- [ ] **Step 3: Add configuration and toggle behavior**

Add to `DEFAULTS`:

```javascript
mainHotkey: 'CommandOrControl+Shift+M', // global main-window toggle
```

In `client/main.js`, create the manager once:

```javascript
const { createHotkeyManager } = require('./hotkey_manager');

function toggleMainWindow() {
  if (!mainWindow) {
    createMainWindow();
    mainWindow.show();
    mainWindow.focus();
    return;
  }
  if (mainWindow.isVisible() && mainWindow.isFocused()) {
    mainWindow.hide();
    return;
  }
  mainWindow.show();
  mainWindow.focus();
}

const hotkeyManager = createHotkeyManager(globalShortcut, {
  capture: showCaptureWindow,
  main: toggleMainWindow,
});
```

At startup call `hotkeyManager.registerStartupPair({capture: config.hotkey, main: config.mainHotkey})`. Do not use transactional `registerPair` at startup: a `mainHotkey` conflict must leave quick capture registered. Replace `registerHotkey()` and all `unregisterAll()` calls. `will-quit` calls `hotkeyManager.dispose()`.

- [ ] **Step 4: Make config update transactional**

When either `hotkey` or `mainHotkey` is present in a patch:

1. merge the requested patch in memory without saving;
2. call `hotkeyManager.registerPair` with both values;
3. on success persist the merged config and return both registration flags true;
4. on failure persist the manager's active pair over the requested values and return both flags false plus `hotkeyErrorKind` and `failedHotkey`.

Do not save the candidate before successful registration.

- [ ] **Step 5: Run main-process tests and syntax**

```powershell
python -m pytest tests/test_hotkey_manager.py tests/test_main_renderer_static.py -q
node --check client/hotkey_manager.js
node --check client/config.js
node --check client/main.js
```

Expected: all pass.

- [ ] **Step 6: Commit main-process integration**

```powershell
git add client/config.js client/main.js tests/test_hotkey_manager.py tests/test_main_renderer_static.py
git commit -m "feat: register main-window global shortcut" -m "Why: The main workspace needs the same low-friction recall as quick capture without sharing or destabilizing its accelerator."
```

---

### Task 3: Add two-key settings UX and runtime acceptance

**Files:**
- Modify: `client/windows/main.html`
- Modify: `client/windows/main.js`
- Modify: `client/windows/main.css`
- Modify: `tests/test_main_renderer_static.py`
- Modify: `client/README.md`

**Interfaces:**
- Consumes existing `_keyCodeToElectron` key capture and `wea.updateConfig`.
- Produces separate quick-capture and main-window hotkey inputs with one atomic save.

- [ ] **Step 1: Add settings guards**

```python
def test_settings_exposes_two_labeled_hotkey_capture_inputs() -> None:
    html = Path("client/windows/main.html").read_text(encoding="utf-8")
    source = Path("client/windows/main.js").read_text(encoding="utf-8")
    assert 'id="settings-hotkey"' in html
    assert 'id="settings-main-hotkey"' in html
    assert "快速捕获快捷键" in html
    assert "主窗口快捷键" in html
    assert "mainHotkey" in source
    assert "captureAcceleratorInput" in source
    assert "mainAcceleratorInput" in source
```

- [ ] **Step 2: Run the guard to verify red**

```powershell
python -m pytest tests/test_main_renderer_static.py -q
```

Expected: the second input is absent.

- [ ] **Step 3: Add the second settings input**

Use explicit labels:

```html
<label for="settings-hotkey">快速捕获快捷键</label>
<input id="settings-hotkey" type="text" placeholder="CommandOrControl+Shift+Space" />
<label for="settings-main-hotkey">主窗口快捷键</label>
<input id="settings-main-hotkey" type="text" placeholder="CommandOrControl+Shift+M" />
```

- [ ] **Step 4: Reuse one key-capture helper**

Extract the existing inline listener into:

```javascript
function bindAcceleratorCapture(input) {
  input.onkeydown = (event) => {
    if (event.key === 'Tab') return;
    if (event.key === 'Backspace' || event.key === 'Delete') {
      input.value = '';
      event.preventDefault();
      return;
    }
    if (!event.ctrlKey && !event.metaKey && !event.altKey && !event.shiftKey) return;
    event.preventDefault();
    event.stopPropagation();
    const keys = [];
    if (event.ctrlKey || event.metaKey) keys.push('CommandOrControl');
    if (event.altKey) keys.push('Alt');
    if (event.shiftKey) keys.push('Shift');
    const mainKey = _keyCodeToElectron(event.code, event.key);
    if (mainKey) { keys.push(mainKey); input.value = keys.join('+'); }
  };
}
```

In `openSettingsModal`, bind both inputs and load both current values. In `saveSettings`, reject blank or identical values before invoking IPC and submit one patch:

```javascript
const captureAcceleratorInput = $('#settings-hotkey');
const mainAcceleratorInput = $('#settings-main-hotkey');
const hotkey = captureAcceleratorInput.value.trim();
const mainHotkey = mainAcceleratorInput.value.trim();
if (!hotkey || !mainHotkey) { showSettingsError('两个快捷键都不能为空'); return; }
if (hotkey === mainHotkey) { showSettingsError('快速捕获和主窗口不能使用同一个快捷键'); return; }
const patch = { hotkey, mainHotkey, opencodeModel: $('#settings-model').value.trim() };
```

If registration fails, keep the modal open, restore both input values from the returned active config, and show which shortcut failed.

- [ ] **Step 5: Run static and syntax checks**

```powershell
python -m pytest tests/test_main_renderer_static.py tests/test_hotkey_manager.py -q
node --check client/windows/main.js
```

Expected: all pass.

- [ ] **Step 6: Perform isolated runtime acceptance**

Launch Electron from the feature worktree and verify:

1. Default capture shortcut opens quick capture.
2. Default main shortcut focuses the main window.
3. Triggering main shortcut again while focused hides it to tray.
4. Triggering while the main window is visible but not focused focuses it instead of hiding it.
5. Setting two distinct alternatives takes effect immediately.
6. Setting a known conflicting accelerator leaves both previous shortcuts working and keeps the modal open with an error.
7. Restart preserves both valid shortcuts.

Capture one screenshot of the two-shortcut settings UI and record the conflicting-accelerator error text.

- [ ] **Step 7: Run full verification**

```powershell
python -m pytest -q
Get-ChildItem client -Recurse -Filter *.js | ForEach-Object { node --check $_.FullName }
git diff --check
```

Expected: all tests pass, all JavaScript parses, and no whitespace errors exist.

- [ ] **Step 8: Commit UX and documentation**

```powershell
git add client/windows/main.html client/windows/main.js client/windows/main.css tests/test_main_renderer_static.py client/README.md
git commit -m "feat: configure independent client shortcuts" -m "Why: Users should set capture and main-window access by pressing keys, with clear conflict recovery."
```

## Self-Review Checklist

- [x] Capture and main-window shortcuts are independently configurable.
- [x] Main-window visibility behavior distinguishes focused from merely visible.
- [x] Pair registration is transactional and restores the previous valid pair.
- [x] Old config files without `mainHotkey` receive the default through `DEFAULTS`.
- [x] Settings reject blank and duplicate values before IPC.
- [x] No project, capture, Inbox, report, or schema behavior changed.
- [x] No new dependency or native dialog was introduced.

## Execution Handoff

This plan is independent of the F007 Project Document v2 plan. 砚砚 reviews it separately; after PASS, 金哥 may implement it before or after Phase A, but it must be committed and runtime-accepted independently so shortcut failures cannot block schema migration review.
