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
  createItem: (projectPath, title) => ipcRenderer.invoke('wea:createItem', { projectPath, title }),
  createTask: (projectPath, itemId, title) =>
    ipcRenderer.invoke('wea:createTask', { projectPath, itemId, title }),
  deleteItem: (projectPath, itemId) => ipcRenderer.invoke('wea:deleteItem', { projectPath, itemId }),
  deleteTask: (projectPath, taskId) => ipcRenderer.invoke('wea:deleteTask', { projectPath, taskId }),
  updateItem: (projectPath, itemId, title) =>
    ipcRenderer.invoke('wea:updateItem', { projectPath, itemId, title }),
  updateTask: (projectPath, taskId, field, value) =>
    ipcRenderer.invoke('wea:updateTask', { projectPath, taskId, field, value }),
  generateReport: (request) => ipcRenderer.invoke('wea:generateReport', request || {}),

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
});
