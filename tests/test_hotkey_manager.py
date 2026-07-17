import json
import subprocess
from pathlib import Path


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


def test_startup_registration_failure_is_exposed_in_config() -> None:
    source = Path("client/main.js").read_text(encoding="utf-8")
    get_config = source[source.index("ipcMain.handle('wea:getConfig'"):source.index("ipcMain.handle('wea:setWorkspace'")]
    helper = source[source.index("function withHotkeyRegistration"):source.index("// --- IPC")]
    startup = source[source.index("const config = cfg();"):source.index("startReportScheduler();")]
    assert "startupHotkeyRegistration" in source
    assert "startupHotkeyRegistration = hotkeyManager.registerStartupPair" in startup
    assert "withHotkeyRegistration(loadConfig())" in get_config
    assert "hotkeyRegistered:" in helper
    assert "mainHotkeyRegistered:" in helper
    assert "hotkeyErrorKind:" in helper
    assert "failedHotkey:" in helper


def test_renderer_surfaces_startup_hotkey_failure() -> None:
    source = Path("client/windows/main.js").read_text(encoding="utf-8")
    boot = source[source.index("async function boot()"):source.index("function enterSetup()")]
    assert "showStartupHotkeyWarning()" in boot
    assert "function showStartupHotkeyWarning()" in source
    assert "mainHotkeyRegistered" in source
    assert "hotkeyErrorKind" in source
