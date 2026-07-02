// main.js — Electron main process: windows, global hotkey, tray, clipboard, IPC, python bridge.
// Architecture: docs/designs/F001-client-architecture.md §5
const {
  app, BrowserWindow, Tray, Menu, globalShortcut,
  clipboard, ipcMain, dialog, shell, nativeImage, screen,
} = require('electron');
const path = require('path');
const fs = require('fs');
const os = require('os');

const { callBackend } = require('./python_bridge');
const { loadConfig, saveConfig, dbPathFor } = require('./config');

let mainWindow = null;
let captureWindow = null;
let tray = null;
let isQuitting = false;

const PENDING_DIR = path.join(os.tmpdir(), 'workeventagent', 'pending');

function ensurePendingDir() {
  try { fs.mkdirSync(PENDING_DIR, { recursive: true }); } catch { /* ignore */ }
}

// --- windows ---------------------------------------------------------------

function createMainWindow() {
  mainWindow = new BrowserWindow({
    width: 1040, height: 700, minWidth: 780, minHeight: 500,
    title: 'WorkEventAgent',
    icon: path.join(__dirname, 'assets', 'icon.png'),
    backgroundColor: '#0f172a',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  mainWindow.removeMenu();
  mainWindow.loadFile(path.join(__dirname, 'windows', 'main.html'));
  mainWindow.on('closed', () => { mainWindow = null; });
}

function createCaptureWindow() {
  captureWindow = new BrowserWindow({
    width: 480, height: 300, show: false, frame: false, resizable: false,
    alwaysOnTop: true, skipTaskbar: true, fullscreenable: false,
    icon: path.join(__dirname, 'assets', 'icon.png'),
    backgroundColor: '#0f172a',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  captureWindow.removeMenu();
  captureWindow.loadFile(path.join(__dirname, 'windows', 'capture.html'));
  captureWindow.on('blur', () => {
    if (captureWindow && !captureWindow.webContents.isDevToolsOpened()) captureWindow.hide();
  });
  captureWindow.on('close', (e) => {
    if (!isQuitting) { e.preventDefault(); captureWindow.hide(); }
  });
}

function showCaptureWindow() {
  if (!captureWindow) createCaptureWindow();
  const display = screen.getPrimaryDisplay();
  const { width, height } = display.workAreaSize;
  const [w] = captureWindow.getSize();
  captureWindow.setPosition(Math.round((width - w) / 2), Math.round(height * 0.18));
  captureWindow.show();
  captureWindow.focus();
  captureWindow.webContents.send('wea:show-capture');
}

// --- tray + hotkey ---------------------------------------------------------

function setupTray() {
  const iconPath = path.join(__dirname, 'assets', 'tray.png');
  let icon = nativeImage.createFromPath(iconPath);
  if (icon.isEmpty()) icon = nativeImage.createEmpty();
  tray = new Tray(icon);
  tray.setToolTip('WorkEventAgent');
  tray.setContextMenu(Menu.buildFromTemplate([
    {
      label: '打开主窗口',
      click: () => {
        if (!mainWindow) createMainWindow();
        else { mainWindow.show(); mainWindow.focus(); }
      },
    },
    { label: '快速捕获', click: () => showCaptureWindow() },
    { type: 'separator' },
    { label: '退出', click: () => { isQuitting = true; app.quit(); } },
  ]));
  tray.on('click', () => showCaptureWindow());
}

function registerHotkey() {
  const { hotkey } = loadConfig();
  globalShortcut.unregisterAll();
  const accelerator = hotkey || 'CommandOrControl+Shift+Space';
  const ok = globalShortcut.register(accelerator, () => showCaptureWindow());
  if (!ok) console.error(`[wea] failed to register global hotkey: ${accelerator}`);
  return { ok, hotkey: accelerator };
}

// --- IPC -------------------------------------------------------------------

function attachIpc() {
  const cfg = () => loadConfig();

  ipcMain.handle('wea:getConfig', () => loadConfig());

  ipcMain.handle('wea:setWorkspace', (_e, { workspace }) => {
    const merged = saveConfig({ workspace });
    try { fs.mkdirSync(path.join(workspace, 'attachments'), { recursive: true }); } catch { /* ignore */ }
    return merged;
  });

  ipcMain.handle('wea:updateConfig', (_e, patch) => {
    const before = loadConfig();
    const merged = saveConfig(patch || {});
    if (patch && Object.prototype.hasOwnProperty.call(patch, 'workspace') && patch.workspace) {
      try { fs.mkdirSync(path.join(patch.workspace, 'attachments'), { recursive: true }); } catch { /* ignore */ }
    }
    const hotkeyStatus = patch && Object.prototype.hasOwnProperty.call(patch, 'hotkey')
      ? registerHotkey()
      : null;
    if (hotkeyStatus && !hotkeyStatus.ok) {
      const reverted = saveConfig({ hotkey: before.hotkey });
      registerHotkey();
      return { ...reverted, hotkeyRegistered: false };
    }
    return { ...merged, hotkeyRegistered: hotkeyStatus ? hotkeyStatus.ok : true };
  });

  ipcMain.handle('wea:pickWorkspaceDir', async () => {
    const res = await dialog.showOpenDialog(mainWindow || undefined, {
      title: '选择项目库目录',
      properties: ['openDirectory', 'createDirectory'],
    });
    if (res.canceled || !res.filePaths.length) return null;
    return res.filePaths[0];
  });

  ipcMain.handle('wea:listProjects', async () => {
    const c = cfg();
    if (!c.workspace) return { ok: false, kind: 'no_workspace', error: 'workspace 未设置' };
    return callBackend('projects', { workspace: c.workspace, db_path: dbPathFor(c.workspace) }, c.pythonCmd);
  });

  ipcMain.handle('wea:listTasks', async (_e, { projectPath }) =>
    callBackend('tasks', { project_path: projectPath }, cfg().pythonCmd));

  ipcMain.handle('wea:listTimeline', async (_e, { projectPath }) =>
    callBackend('timeline', { project_path: projectPath }, cfg().pythonCmd));

  ipcMain.handle('wea:propose', async (_e, { text, projectPath, attachments }) =>
    callBackend('propose', { text, project_path: projectPath, attachments: attachments || [] }, cfg().pythonCmd));

  ipcMain.handle('wea:routePropose', async (_e, { text, attachments }) => {
    const c = cfg();
    if (!c.workspace) return { ok: false, kind: 'no_workspace', error: 'workspace 未设置' };
    return callBackend('route_propose', {
      text,
      workspace: c.workspace,
      attachments: attachments || [],
    }, c.pythonCmd);
  });

  ipcMain.handle('wea:commit', async (_e, { proposal, projectPath, pendingAttachments }) => {
    const c = cfg();
    const res = await callBackend('commit', {
      proposal,
      project_path: projectPath,
      db_path: dbPathFor(c.workspace),
      // renderer sends pending as {tempPath, filename}; backend expects snake_case {temp_path, filename}
      pending_attachments: (pendingAttachments || []).map((p) => ({ temp_path: p.tempPath, filename: p.filename })),
    }, c.pythonCmd);
    if (res && res.ok) {
      const payload = { taskId: res.task_id, projectPath };
      if (mainWindow) mainWindow.webContents.send('wea:archived', payload);
      if (captureWindow) captureWindow.webContents.send('wea:archived', payload);
    }
    return res;
  });

  ipcMain.handle('wea:initProject', async (_e, spec) => {
    const c = cfg();
    if (!c.workspace) return { ok: false, kind: 'no_workspace', error: 'workspace 未设置' };
    return callBackend('init', { ...spec, workspace: c.workspace, db_path: dbPathFor(c.workspace) }, c.pythonCmd);
  });

  ipcMain.handle('wea:createItem', async (_e, { projectPath, title }) => {
    const c = cfg();
    return callBackend('create_item', {
      project_path: projectPath,
      db_path: dbPathFor(c.workspace),
      title,
    }, c.pythonCmd);
  });

  ipcMain.handle('wea:createTask', async (_e, { projectPath, itemId, title }) => {
    const c = cfg();
    return callBackend('create_task', {
      project_path: projectPath,
      db_path: dbPathFor(c.workspace),
      item_id: itemId,
      title,
    }, c.pythonCmd);
  });

  ipcMain.handle('wea:deleteItem', async (_e, { projectPath, itemId }) => {
    const c = cfg();
    return callBackend('delete_item', {
      project_path: projectPath,
      db_path: dbPathFor(c.workspace),
      item_id: itemId,
    }, c.pythonCmd);
  });

  ipcMain.handle('wea:deleteTask', async (_e, { projectPath, taskId }) => {
    const c = cfg();
    return callBackend('delete_task', {
      project_path: projectPath,
      db_path: dbPathFor(c.workspace),
      task_id: taskId,
    }, c.pythonCmd);
  });

  ipcMain.handle('wea:updateItem', async (_e, { projectPath, itemId, title }) => {
    const c = cfg();
    return callBackend('update_item', {
      project_path: projectPath,
      db_path: dbPathFor(c.workspace),
      item_id: itemId,
      title,
    }, c.pythonCmd);
  });

  ipcMain.handle('wea:updateTask', async (_e, { projectPath, taskId, field, value }) => {
    const c = cfg();
    return callBackend('update_task', {
      project_path: projectPath,
      db_path: dbPathFor(c.workspace),
      task_id: taskId,
      field,
      value,
    }, c.pythonCmd);
  });

  ipcMain.handle('wea:readClipboardImage', async () => {
    const img = clipboard.readImage();
    if (img.isEmpty()) return null;
    ensurePendingDir();
    const filename = `clip-${Date.now()}.png`;
    const dest = path.join(PENDING_DIR, filename);
    try {
      fs.writeFileSync(dest, img.toPNG());
      // dataUrl is used for the renderer thumbnail (avoids file:// cross-origin load issues);
      // tempPath is what the backend copies into the project on commit.
      return { tempPath: dest, filename, dataUrl: img.toDataURL() };
    } catch (e) {
      console.error('[wea] failed to write clipboard image:', e);
      return null;
    }
  });

  ipcMain.handle('wea:discardPending', (_e, { tempPaths }) => {
    for (const p of tempPaths || []) {
      try { if (p && p.startsWith(PENDING_DIR)) fs.unlinkSync(p); } catch { /* ignore */ }
    }
    return { ok: true };
  });

  ipcMain.handle('wea:openProjectDir', (_e, { projectPath }) => {
    try { shell.showItemInFolder(projectPath); return { ok: true }; }
    catch (e) { return { ok: false, error: String(e) }; }
  });

  ipcMain.on('wea:hideCapture', () => { if (captureWindow) captureWindow.hide(); });
  ipcMain.on('wea:resizeCapture', (_e, height) => {
    if (!captureWindow) return;
    const [w] = captureWindow.getSize();
    captureWindow.setSize(w, Math.max(180, Math.min(680, Math.round(height))));
  });
}

// --- lifecycle -------------------------------------------------------------

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (mainWindow) { mainWindow.show(); mainWindow.focus(); }
    else createMainWindow();
  });

  app.whenReady().then(() => {
    ensurePendingDir();
    attachIpc();
    createMainWindow();
    createCaptureWindow();
    setupTray();
    registerHotkey();

    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) createMainWindow();
    });
  });

  // Keep running in the tray after the main window is closed.
  app.on('window-all-closed', () => { /* tray-resident; do not quit */ });
  app.on('before-quit', () => { isQuitting = true; });
  app.on('will-quit', () => { globalShortcut.unregisterAll(); });
}
