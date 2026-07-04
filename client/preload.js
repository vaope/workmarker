// preload.js — safe bridge between renderer and main process.
// contextIsolation:true + nodeIntegration:false; renderer only sees window.wea.
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('wea', {
  // --- backend (workspace/dbPath are injected by main from config) ---
  propose: (text, projectPath, attachments) =>
    ipcRenderer.invoke('wea:propose', { text, projectPath, attachments: attachments || [] }),
  routePropose: (text, attachments) =>
    ipcRenderer.invoke('wea:routePropose', { text, attachments: attachments || [] }),
  commit: (proposal, projectPath, pendingAttachments) =>
    ipcRenderer.invoke('wea:commit', { proposal, projectPath, pendingAttachments: pendingAttachments || [] }),
  listProjects: () => ipcRenderer.invoke('wea:listProjects'),
  listTasks: (projectPath) => ipcRenderer.invoke('wea:listTasks', { projectPath }),
  listTimeline: (projectPath) => ipcRenderer.invoke('wea:listTimeline', { projectPath }),
  initProject: (spec) => ipcRenderer.invoke('wea:initProject', spec),
  createItem: (projectPath, title, background) => ipcRenderer.invoke('wea:createItem', { projectPath, title, background }),
  createTask: (projectPath, itemId, title) =>
    ipcRenderer.invoke('wea:createTask', { projectPath, itemId, title }),
  deleteItem: (projectPath, itemId) => ipcRenderer.invoke('wea:deleteItem', { projectPath, itemId }),
  deleteTask: (projectPath, taskId) => ipcRenderer.invoke('wea:deleteTask', { projectPath, taskId }),
  updateItem: (projectPath, itemId, title, background) =>
    ipcRenderer.invoke('wea:updateItem', { projectPath, itemId, title, background }),
  updateTask: (projectPath, taskId, field, value) =>
    ipcRenderer.invoke('wea:updateTask', { projectPath, taskId, field, value }),
  generateReport: (request) => ipcRenderer.invoke('wea:generateReport', request || {}),

  // --- inbox ---
  createCapture: (text, attachments) => ipcRenderer.invoke('wea:inboxCreate', { text, attachments: attachments || [] }),
  listCaptures: () => ipcRenderer.invoke('wea:inboxList'),
  processCapture: (captureId) => ipcRenderer.invoke('wea:inboxProcess', { captureId }),
  commitCapture: (captureId, edits) => ipcRenderer.invoke('wea:inboxCommit', { captureId, edits: edits || {} }),
  cancelCapture: (captureId) => ipcRenderer.invoke('wea:inboxCancel', { captureId }),

  // --- search ---
  search: (query, limit) => ipcRenderer.invoke('wea:search', { query, limit: limit || 50 }),

  // --- correction ---
  correctEvent: (request) => ipcRenderer.invoke('wea:correctEvent', request || {}),

  // --- clipboard / attachments ---
  readClipboardImage: () => ipcRenderer.invoke('wea:readClipboardImage'),
  discardPending: (tempPaths) => ipcRenderer.invoke('wea:discardPending', { tempPaths: tempPaths || [] }),

  // --- config / dialogs ---
  getConfig: () => ipcRenderer.invoke('wea:getConfig'),
  setWorkspace: (workspace) => ipcRenderer.invoke('wea:setWorkspace', { workspace }),
  updateConfig: (patch) => ipcRenderer.invoke('wea:updateConfig', patch || {}),
  pickWorkspaceDir: () => ipcRenderer.invoke('wea:pickWorkspaceDir'),
  openProjectDir: (projectPath) => ipcRenderer.invoke('wea:openProjectDir', { projectPath }),
  getReportScheduleStatus: () => ipcRenderer.invoke('wea:getReportScheduleStatus'),

  // --- quick-capture window control ---
  hideCapture: () => ipcRenderer.send('wea:hideCapture'),
  resizeCapture: (height) => ipcRenderer.send('wea:resizeCapture', height),

  // --- main -> renderer events ---
  onShowCapture: (cb) => ipcRenderer.on('wea:show-capture', () => cb()),
  onArchived: (cb) => ipcRenderer.on('wea:archived', (_e, payload) => cb(payload)),
  onInboxUpdated: (cb) => ipcRenderer.on('wea:inbox-updated', (_e, payload) => cb(payload)),
});
