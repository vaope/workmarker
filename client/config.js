// config.js — persistent client config in the Electron userData dir.
// Main-process only (depends on electron `app`).
const { app } = require('electron');
const path = require('path');
const fs = require('fs');

const CONFIG_PATH = path.join(app.getPath('userData'), 'config.json');

const DEFAULTS = {
  workspace: '',                                   // project library root (chosen on first run)
  hotkey: 'CommandOrControl+Shift+Space',          // global quick-capture hotkey
  mainHotkey: 'CommandOrControl+Shift+M',         // global main-window toggle
  pythonCmd: 'python',                             // python executable used by python_bridge
  opencodeModel: '',                               // optional provider/model; empty uses opencode default
  reportSchedule: {
    dailyEnabled: false,
    dailyTime: '23:30',
    weeklyEnabled: false,
    weeklyDay: 5,
    weeklyTime: '18:00',
    lastDailyRunDate: '',
    lastWeeklyRunKey: '',
    lastRunStatus: '',
  },
  synthesisSchedule: {
    dailyEnabled: true,
    dailyTime: '23:30',
    weeklyEnabled: true,
    weeklyDay: 5,
    weeklyTime: '18:00',
    lastDailySuccessDate: '',
    lastWeeklySuccessKey: '',
    lastRunStatus: '',
  },
};

function loadConfig() {
  try {
    const raw = fs.readFileSync(CONFIG_PATH, 'utf-8');
    return { ...DEFAULTS, ...JSON.parse(raw) };
  } catch {
    return { ...DEFAULTS };
  }
}

function saveConfig(patch) {
  const merged = { ...loadConfig(), ...patch };
  fs.mkdirSync(path.dirname(CONFIG_PATH), { recursive: true });
  fs.writeFileSync(CONFIG_PATH, JSON.stringify(merged, null, 2), 'utf-8');
  return merged;
}

// The global SQLite index lives at the workspace root.
function dbPathFor(workspace) {
  return workspace ? path.join(workspace, 'index.sqlite') : '';
}

module.exports = { loadConfig, saveConfig, dbPathFor, CONFIG_PATH };
