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
const { createHotkeyManager } = require('./hotkey_manager');
const KnowledgeSchedule = require('./knowledge_schedule');

let mainWindow = null;
let captureWindow = null;
let tray = null;
let isQuitting = false;

const PENDING_DIR = path.join(os.tmpdir(), 'workeventagent', 'pending');
const cfg = () => loadConfig();

let knowledgeWorkerChain = Promise.resolve();
const activeKnowledgeJobs = new Set();

function emitKnowledgeUpdated(payload) {
  if (mainWindow) mainWindow.webContents.send('wea:knowledge-updated', payload || {});
}

function markKnowledgeRunSuccessful(run) {
  if (!run || run.state !== 'completed') return;
  const current = cfg();
  const schedule = KnowledgeSchedule.markSuccessful(current.synthesisSchedule || {}, {
    cadence: run.cadence,
    scheduleKey: run.schedule_key,
    state: run.state,
  });
  saveConfig({ synthesisSchedule: schedule });
}

async function processKnowledgeJobNow(jobId) {
  const c = cfg();
  if (!c.workspace) return { ok: false, kind: 'no_workspace', error: 'workspace not configured' };
  const result = await callBackend('knowledge_process_job', {
    workspace: c.workspace,
    job_id: jobId,
    opencode_model: c.opencodeModel || '',
  }, c.pythonCmd);
  if (result && result.schedule_run) markKnowledgeRunSuccessful(result.schedule_run);
  emitKnowledgeUpdated({ kind: 'job_transition', job_id: jobId, result });
  return result;
}

function enqueueKnowledgeJob(jobId) {
  if (!jobId) return Promise.resolve({ ok: false, kind: 'invalid_job' });
  if (activeKnowledgeJobs.has(jobId)) return knowledgeWorkerChain;
  activeKnowledgeJobs.add(jobId);
  emitKnowledgeUpdated({ kind: 'job_queued', job_id: jobId });
  const work = async () => {
    try {
      return await processKnowledgeJobNow(jobId);
    } catch (error) {
      const result = { ok: false, kind: 'worker_error', error: String(error) };
      console.error('knowledge worker failed', error);
      emitKnowledgeUpdated({ kind: 'job_error', job_id: jobId, result });
      return result;
    } finally {
      activeKnowledgeJobs.delete(jobId);
    }
  };
  const result = knowledgeWorkerChain.then(work, work);
  knowledgeWorkerChain = result.then(() => undefined, () => undefined);
  return result;
}

async function recoverKnowledgeWork() {
  const c = cfg();
  if (!c.workspace) return { ok: false, kind: 'no_workspace' };
  const recovered = await callBackend('knowledge_recover', { workspace: c.workspace }, c.pythonCmd);
  const state = await callBackend('knowledge_state', { workspace: c.workspace }, c.pythonCmd);
  emitKnowledgeUpdated({ kind: 'recovered', recovered, state });
  if (state && state.ok) {
    for (const job of state.jobs || []) {
      if (job.state === 'queued') enqueueKnowledgeJob(job.job_id);
    }
    for (const run of state.runs || []) markKnowledgeRunSuccessful(run);
  }
  return { ok: true, recovered, state };
}

async function runScheduledKnowledge(now = new Date()) {
  const c = cfg();
  if (!c.workspace) return [];
  const schedule = c.synthesisSchedule || {};
  const due = KnowledgeSchedule.dueRuns(now, knowledgeSchedulerStartedAt || now, schedule);
  const results = [];
  for (const planned of due) {
    const enqueued = await callBackend('knowledge_enqueue_schedule', {
      workspace: c.workspace,
      cadence: planned.cadence,
      schedule_key: planned.scheduleKey,
      date_from: planned.dateFrom,
      date_to: planned.dateTo,
    }, c.pythonCmd);
    results.push(enqueued);
    if (!enqueued || !enqueued.ok) {
      emitKnowledgeUpdated({ kind: 'schedule_error', planned, result: enqueued });
      continue;
    }
    emitKnowledgeUpdated({ kind: 'schedule_enqueued', run: enqueued.run });
    markKnowledgeRunSuccessful(enqueued.run);
    const state = await callBackend('knowledge_state', { workspace: c.workspace }, c.pythonCmd);
    const jobs = new Map((state.jobs || []).map((job) => [job.job_id, job]));
    const pending = [];
    for (const child of enqueued.run.expected_children || []) {
      const job = jobs.get(child.job_id);
      if (job && job.state === 'queued') pending.push(enqueueKnowledgeJob(job.job_id));
    }
    if (pending.length) await Promise.all(pending);
  }
  return results;
}

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

const hotkeyManager = createHotkeyManager(globalShortcut, {
  capture: showCaptureWindow,
  main: toggleMainWindow,
});
let startupHotkeyRegistration = null;

function withHotkeyRegistration(config) {
  if (!startupHotkeyRegistration) {
    return { ...config, hotkeyRegistered: true, mainHotkeyRegistered: true };
  }
  return {
    ...config,
    hotkeyRegistered: !!startupHotkeyRegistration.captureRegistered,
    mainHotkeyRegistered: !!startupHotkeyRegistration.mainRegistered,
    hotkeyErrorKind: startupHotkeyRegistration.ok ? undefined : startupHotkeyRegistration.kind,
    failedHotkey: startupHotkeyRegistration.ok ? undefined : startupHotkeyRegistration.failed,
  };
}

// --- IPC -------------------------------------------------------------------

function attachIpc() {
  ipcMain.handle('wea:getConfig', () => withHotkeyRegistration(loadConfig()));

  ipcMain.handle('wea:setWorkspace', (_e, { workspace }) => {
    const merged = saveConfig({ workspace });
    try { fs.mkdirSync(path.join(workspace, 'attachments'), { recursive: true }); } catch { /* ignore */ }
    return merged;
  });

  ipcMain.handle('wea:updateConfig', (_e, patch) => {
    const before = loadConfig();
    const hasHotkey = patch && (Object.prototype.hasOwnProperty.call(patch, 'hotkey') || Object.prototype.hasOwnProperty.call(patch, 'mainHotkey'));
    const candidate = { ...before, ...(patch || {}) };
    if (patch && Object.prototype.hasOwnProperty.call(patch, 'workspace') && patch.workspace) {
      try { fs.mkdirSync(path.join(patch.workspace, 'attachments'), { recursive: true }); } catch { /* ignore */ }
    }
    if (!hasHotkey) {
      const merged = saveConfig(patch || {});
      return withHotkeyRegistration(merged);
    }
    const registration = hotkeyManager.registerPair({
      capture: candidate.hotkey,
      main: candidate.mainHotkey,
    });
    if (registration.ok) {
      startupHotkeyRegistration = { ok: true, captureRegistered: true, mainRegistered: true, active: { ...registration.active } };
      const merged = saveConfig(patch || {});
      return { ...merged, hotkeyRegistered: true, mainHotkeyRegistered: true };
    }
    startupHotkeyRegistration = {
      ok: false,
      kind: registration.kind,
      failed: registration.failed,
      captureRegistered: !!registration.active.capture,
      mainRegistered: !!registration.active.main,
      active: { ...registration.active },
    };
    const reverted = saveConfig({ ...patch, hotkey: registration.active.capture, mainHotkey: registration.active.main });
    return {
      ...reverted,
      hotkeyRegistered: false,
      mainHotkeyRegistered: false,
      hotkeyErrorKind: registration.kind,
      failedHotkey: registration.failed,
    };
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

  ipcMain.handle('wea:propose', async (_e, { text, projectPath, attachments }) => {
    const c = cfg();
    return callBackend('propose', {
      text,
      project_path: projectPath,
      attachments: attachments || [],
      opencode_model: c.opencodeModel || '',
    }, c.pythonCmd);
  });

  ipcMain.handle('wea:routePropose', async (_e, { text, attachments }) => {
    const c = cfg();
    if (!c.workspace) return { ok: false, kind: 'no_workspace', error: 'workspace 未设置' };
    return callBackend('route_propose', {
      text,
      workspace: c.workspace,
      attachments: attachments || [],
      opencode_model: c.opencodeModel || '',
    }, c.pythonCmd);
  });

  // ---- inbox ------------------------------------------------------------
  ipcMain.handle('wea:inboxCreate', async (_e, { text, attachments }) => {
    const c = cfg();
    if (!c.workspace) return { ok: false, kind: 'no_workspace', error: 'workspace not configured' };
    return callBackend('inbox_create', {
      workspace: c.workspace,
      text,
      attachments: (attachments || []).map((p) => ({ temp_path: p.tempPath || p, filename: p.filename || path.basename(p) })),
    }, c.pythonCmd);
  });

  ipcMain.handle('wea:inboxList', async () => {
    const c = cfg();
    if (!c.workspace) return { ok: false, kind: 'no_workspace', error: 'workspace not configured' };
    return callBackend('inbox_list', { workspace: c.workspace }, c.pythonCmd);
  });

  ipcMain.handle('wea:inboxProcess', async (_e, { captureId }) => {
    const c = cfg();
    if (!c.workspace) return { ok: false, kind: 'no_workspace', error: 'workspace not configured' };
    return callBackend('inbox_process', {
      workspace: c.workspace,
      capture_id: captureId,
      opencode_model: c.opencodeModel || '',
    }, c.pythonCmd);
  });

  ipcMain.handle('wea:inboxCommit', async (_e, { captureId, edits }) => {
    const c = cfg();
    if (!c.workspace) return { ok: false, kind: 'no_workspace', error: 'workspace not configured' };
    const res = await callBackend('inbox_commit', {
      workspace: c.workspace, capture_id: captureId, edits: edits || {},
    }, c.pythonCmd);
    if (res && res.ok) {
      const payload = { capture_id: captureId };
      if (mainWindow) mainWindow.webContents.send('wea:inbox-updated', payload);
      if (captureWindow) captureWindow.webContents.send('wea:inbox-updated', payload);
      if (res.knowledge_job_id) enqueueKnowledgeJob(res.knowledge_job_id);
    }
    return res;
  });

  ipcMain.handle('wea:inboxCancel', async (_e, { captureId }) => {
    const c = cfg();
    if (!c.workspace) return { ok: false, kind: 'no_workspace', error: 'workspace not configured' };
    return callBackend('inbox_cancel', { workspace: c.workspace, capture_id: captureId }, c.pythonCmd);
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

  ipcMain.handle('wea:generateReport', async (_e, request) => {
    const c = cfg();
    return callBackend('generate_report', {
      workspace: c.workspace,
      type: request.type || 'daily',
      project_id: request.projectId || request.project_id || null,
      date_from: request.dateFrom || request.date_from || request.date || null,
      date_to: request.dateTo || request.date_to || request.date || null,
      range_label: request.rangeLabel || request.range_label || '',
      persist: request.persist !== false,
      mode: request.mode || 'manual',
      include_ai: request.includeAi !== false,
      opencode_model: c.opencodeModel || '',
    }, c.pythonCmd);
  });

  ipcMain.handle('wea:search', async (_e, { query, limit }) => {
    const c = cfg();
    if (!c.workspace) return { ok: false, kind: 'no_workspace', error: 'workspace not configured' };
    return callBackend('search', { workspace: c.workspace, query, limit: limit || 50 }, c.pythonCmd);
  });

  ipcMain.handle('wea:correctEvent', async (_e, request) => {
    const c = cfg();
    return callBackend('correct_event', { ...request, db_path: dbPathFor(c.workspace) }, c.pythonCmd);
  });

  ipcMain.handle('wea:correctionRecoveries', async () => {
    const c = cfg();
    if (!c.workspace) return { ok: false, kind: 'no_workspace', error: 'workspace not configured' };
    return callBackend('correction_recoveries', { workspace: c.workspace }, c.pythonCmd);
  });

  ipcMain.handle('wea:resumeCorrection', async (_e, { correctionId }) => {
    const c = cfg();
    if (!c.workspace) return { ok: false, kind: 'no_workspace', error: 'workspace not configured' };
    return callBackend('resume_correction', { workspace: c.workspace, correction_id: correctionId, db_path: dbPathFor(c.workspace) }, c.pythonCmd);
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

  ipcMain.handle('wea:getReportScheduleStatus', async () => {
    const c = cfg();
    return { ok: true, reportSchedule: c.reportSchedule || {} };
  });

  // ---- durable knowledge synthesis -------------------------------------

  ipcMain.handle('wea:getKnowledgeState', async (_e, { projectPath }) => {
    const c = cfg();
    if (!c.workspace) return { ok: false, kind: 'no_workspace', error: 'workspace not configured' };
    return callBackend('knowledge_state', {
      workspace: c.workspace,
      project_path: projectPath || null,
    }, c.pythonCmd);
  });

  ipcMain.handle('wea:enqueueKnowledge', async (_e, request) => {
    const c = cfg();
    if (!c.workspace) return { ok: false, kind: 'no_workspace', error: 'workspace not configured' };
    const enqueued = await callBackend('knowledge_enqueue', {
      workspace: c.workspace,
      trigger: 'directed',
      project_path: request.projectPath,
      event_ids: request.eventIds || [],
      regenerate_of: request.regenerateOf || '',
    }, c.pythonCmd);
    if (!enqueued || !enqueued.ok) return enqueued;
    emitKnowledgeUpdated({ kind: 'job_queued', job_id: enqueued.job.job_id, job: enqueued.job });
    return enqueueKnowledgeJob(enqueued.job.job_id);
  });

  ipcMain.handle('wea:processKnowledgeJob', async (_e, { jobId }) =>
    enqueueKnowledgeJob(jobId));

  ipcMain.handle('wea:retryKnowledgeJob', async (_e, request) => {
    const c = cfg();
    if (!c.workspace) return { ok: false, kind: 'no_workspace', error: 'workspace not configured' };
    const retried = await callBackend('knowledge_retry_job', {
      workspace: c.workspace,
      job_id: request.jobId,
      expected_version: request.expectedVersion,
    }, c.pythonCmd);
    emitKnowledgeUpdated({ kind: 'job_retry', result: retried });
    if (!retried || !retried.ok) return retried;
    return enqueueKnowledgeJob(retried.job.job_id);
  });

  ipcMain.handle('wea:reviseKnowledgeProposal', async (_e, request) => {
    const c = cfg();
    const result = await callBackend('knowledge_revise_proposal', {
      workspace: c.workspace,
      proposal_id: request.proposalId,
      expected_version: request.expectedVersion,
      included_change_ids: request.includedChangeIds || [],
    }, c.pythonCmd);
    emitKnowledgeUpdated({ kind: 'proposal_revision', result });
    return result;
  });

  ipcMain.handle('wea:rejectKnowledgeProposal', async (_e, request) => {
    const c = cfg();
    const result = await callBackend('knowledge_reject_proposal', {
      workspace: c.workspace,
      proposal_id: request.proposalId,
      expected_version: request.expectedVersion,
    }, c.pythonCmd);
    emitKnowledgeUpdated({ kind: 'proposal_rejection', result });
    return result;
  });

  ipcMain.handle('wea:applyKnowledgeProposal', async (_e, request) => {
    const c = cfg();
    const result = await callBackend('knowledge_apply_proposal', {
      workspace: c.workspace,
      project_path: request.projectPath,
      db_path: dbPathFor(c.workspace),
      proposal_id: request.proposalId,
      expected_version: request.expectedVersion,
    }, c.pythonCmd);
    emitKnowledgeUpdated({ kind: 'proposal_apply', result });
    return result;
  });

  ipcMain.handle('wea:applyKnowledgeDocument', async (_e, request) => {
    const c = cfg();
    const result = await callBackend('knowledge_apply_document', {
      workspace: c.workspace,
      project_path: request.projectPath,
      proposal_id: request.proposalId,
      expected_version: request.expectedVersion,
    }, c.pythonCmd);
    emitKnowledgeUpdated({ kind: 'document_apply', result });
    return result;
  });

  // ---- project panorama --------------------------------------------------

  ipcMain.handle('wea:projectPanorama', async (_e, { projectPath }) =>
    callBackend('project_panorama', { project_path: projectPath }, cfg().pythonCmd));

  ipcMain.handle('wea:previewProjectMigration', async (_e, request) =>
    callBackend('project_migration_preview', {
      project_path: request.projectPath,
      status: request.status,
      phase: request.phase,
    }, cfg().pythonCmd));

  ipcMain.handle('wea:applyProjectMigration', async (_e, request) => {
    const c = cfg();
    return callBackend('project_migration_apply', {
      project_path: request.projectPath,
      db_path: dbPathFor(c.workspace),
      source_hash: request.sourceHash,
      status: request.status,
      phase: request.phase,
    }, c.pythonCmd);
  });

  ipcMain.handle('wea:updateProjectProfile', async (_e, request) => {
    const c = cfg();
    return callBackend('update_project_profile', {
      project_path: request.projectPath,
      db_path: dbPathFor(c.workspace),
      base_section_hash: request.baseSectionHash,
      base_metadata_hash: request.baseMetadataHash,
      status: request.status,
      phase: request.phase,
      background: request.background || '',
      goal: request.goal || '',
      scope: request.scope || '',
      success_criteria: request.successCriteria || '',
    }, c.pythonCmd);
  });

  ipcMain.handle('wea:updateProjectSection', async (_e, request) => {
    const c = cfg();
    return callBackend('update_project_section', {
      project_path: request.projectPath,
      db_path: dbPathFor(c.workspace),
      section_id: request.sectionId,
      base_section_hash: request.baseSectionHash,
      content: request.content,
    }, c.pythonCmd);
  });

  ipcMain.on('wea:hideCapture', () => { if (captureWindow) captureWindow.hide(); });
  ipcMain.on('wea:resizeCapture', (_e, height) => {
    if (!captureWindow) return;
    const [w] = captureWindow.getSize();
    captureWindow.setSize(w, Math.max(180, Math.min(680, Math.round(height))));
  });
}

// --- scheduler -------------------------------------------------------------

function localDateString(d = new Date()) {
  const year = d.getFullYear();
  const month = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function mondayOfLocalWeek(d = new Date()) {
  const copy = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const day = copy.getDay() || 7;
  copy.setDate(copy.getDate() - day + 1);
  return copy;
}

function addDays(d, days) {
  const copy = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  copy.setDate(copy.getDate() + days);
  return copy;
}

let reportScheduleTimer = null;
let reportSchedulerStartedAt = null;
let knowledgeScheduleTimer = null;
let knowledgeSchedulerStartedAt = null;
let knowledgeScheduleTick = Promise.resolve();

function startReportScheduler() {
  if (reportScheduleTimer) clearInterval(reportScheduleTimer);
  reportSchedulerStartedAt = new Date();
  reportScheduleTimer = setInterval(() => {
    runScheduledReports().catch((err) => {
      console.error('scheduled report failed', err);
    });
  }, 60 * 1000);
}

function startKnowledgeScheduler() {
  if (knowledgeScheduleTimer) clearInterval(knowledgeScheduleTimer);
  knowledgeSchedulerStartedAt = new Date();
  const tick = () => {
    knowledgeScheduleTick = knowledgeScheduleTick
      .then(() => runScheduledKnowledge(new Date()))
      .catch((error) => {
        console.error('scheduled knowledge synthesis failed', error);
        emitKnowledgeUpdated({ kind: 'schedule_error', error: String(error) });
      });
    return knowledgeScheduleTick;
  };
  tick();
  knowledgeScheduleTimer = setInterval(tick, 60 * 1000);
}

function scheduledTimeForLocalDate(date, hhmm) {
  const [hour, minute] = hhmm.split(':').map((x) => Number(x));
  return new Date(date.getFullYear(), date.getMonth(), date.getDate(), hour, minute, 0, 0);
}

function canRunScheduledAt(now, scheduledAt) {
  return reportSchedulerStartedAt && reportSchedulerStartedAt <= scheduledAt && now >= scheduledAt;
}

async function runScheduledReports(now = new Date()) {
  const c = cfg();
  if (!c.workspace) return;
  const schedule = c.reportSchedule || {};
  const today = localDateString(now);

  const dailyAt = scheduledTimeForLocalDate(now, schedule.dailyTime || '23:30');
  if (schedule.dailyEnabled && canRunScheduledAt(now, dailyAt) && schedule.lastDailyRunDate !== today) {
    const res = await callBackend('generate_report', {
      workspace: c.workspace,
      type: 'daily',
      date_from: today,
      date_to: today,
      persist: true,
      mode: 'scheduled',
      include_ai: true,
      opencode_model: c.opencodeModel || '',
    }, c.pythonCmd);
    saveConfig({ reportSchedule: { ...schedule, lastDailyRunDate: today, lastRunStatus: JSON.stringify(res) } });
  }

  const weekStart = mondayOfLocalWeek(now);
  const weekEnd = addDays(weekStart, 6);
  const weekKey = `${localDateString(weekStart)}_to_${localDateString(weekEnd)}`;
  const weeklyOffset = schedule.weeklyDay === 0 ? 6 : Number(schedule.weeklyDay || 5) - 1;
  const weeklyDate = addDays(weekStart, weeklyOffset);
  const weeklyAt = scheduledTimeForLocalDate(weeklyDate, schedule.weeklyTime || '18:00');
  if (schedule.weeklyEnabled && canRunScheduledAt(now, weeklyAt) && schedule.lastWeeklyRunKey !== weekKey) {
    const res = await callBackend('generate_report', {
      workspace: c.workspace,
      type: 'weekly',
      date_from: localDateString(weekStart),
      date_to: localDateString(weekEnd),
      persist: true,
      mode: 'scheduled',
      include_ai: true,
      opencode_model: c.opencodeModel || '',
    }, c.pythonCmd);
    saveConfig({ reportSchedule: { ...schedule, lastWeeklyRunKey: weekKey, lastRunStatus: JSON.stringify(res) } });
  }
}

// --- lifecycle -------------------------------------------------------------

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    toggleMainWindow();
  });

  app.whenReady().then(async () => {
    ensurePendingDir();
    attachIpc();
    try {
      await recoverKnowledgeWork();
    } catch (error) {
      console.error('knowledge recovery failed', error);
    }
    createMainWindow();
    createCaptureWindow();
    setupTray();
    const config = cfg();
    startupHotkeyRegistration = hotkeyManager.registerStartupPair({ capture: config.hotkey, main: config.mainHotkey });
    startReportScheduler();
    startKnowledgeScheduler();

    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) createMainWindow();
    });
  });

  // Keep running in the tray after the main window is closed.
  app.on('window-all-closed', () => { /* tray-resident; do not quit */ });
  app.on('before-quit', () => { isQuitting = true; });
  app.on('will-quit', () => { hotkeyManager.dispose(); });
}
