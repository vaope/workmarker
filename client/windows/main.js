// main.js (renderer) — main window logic. Talks to main process only via window.wea (preload).
const $ = (sel) => document.querySelector(sel);

const state = {
  config: null,
  projects: [],
  currentProject: null, // {project_id, title, path}
  tasksData: null,      // {items:[{item_id,title,tasks:[...]}]}
  timelineData: null,   // {events:[...]}
  pending: [],          // [{tempPath, filename}]
  proposal: null,       // proposal awaiting confirmation
  view: 'tasks',
  busy: false,
  manualMode: null,     // "item" | "task"
  manualItemId: '',
  settingsWorkspace: '',
};

// ---- boot ----------------------------------------------------------------
async function boot() {
  bindStaticHandlers();
  state.config = await wea.getConfig();
  if (!state.config.workspace) {
    enterSetup();
  } else {
    enterApp();
    await loadProjects();
  }
  wea.onArchived(() => { if (state.currentProject) refreshCurrent(); });
  loadReportSchedule();
}

function enterSetup() {
  $('#setup').classList.remove('hidden');
  $('#app').classList.add('hidden');
}
function enterApp() {
  $('#setup').classList.add('hidden');
  $('#app').classList.remove('hidden');
}

// ---- static handlers (bound once) ----------------------------------------
function bindStaticHandlers() {
  let picked = '';
  $('#setup-pick').addEventListener('click', async () => {
    const dir = await wea.pickWorkspaceDir();
    if (dir) { picked = dir; $('#setup-path').value = dir; $('#setup-confirm').disabled = false; }
  });
  $('#setup-confirm').addEventListener('click', async () => {
    if (!picked) return;
    state.config = await wea.setWorkspace(picked);
    enterApp();
    await loadProjects();
  });

  $('#change-workspace').addEventListener('click', openSettingsModal);

  $('#new-project').addEventListener('click', openInitModal);
  $('#new-item').addEventListener('click', () => openManualModal('item'));
  $('#init-cancel').addEventListener('click', () => $('#init-modal').classList.add('hidden'));
  $('#init-add-item').addEventListener('click', () => addInitItem());
  $('#init-create').addEventListener('click', createProject);
  $('#init-title').addEventListener('input', (e) => {
    $('#init-id').value = slugify(e.target.value);
  });
  $('#manual-cancel').addEventListener('click', closeManualModal);
  $('#manual-create').addEventListener('click', createManualEntry);
  $('#manual-name').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); createManualEntry(); }
  });

  // edit-item modal
  $('#edit-item-cancel').addEventListener('click', closeEditItemModal);
  $('#edit-item-save').addEventListener('click', saveEditItem);
  $('#edit-item-title').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); saveEditItem(); }
    if (e.key === 'Escape') { closeEditItemModal(); }
  });
  $('#edit-item-background').addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { closeEditItemModal(); }
  });
  $('#settings-cancel').addEventListener('click', () => $('#settings-modal').classList.add('hidden'));
  $('#settings-pick-workspace').addEventListener('click', pickSettingsWorkspace);
  $('#settings-save').addEventListener('click', saveSettings);

  // tabs
  document.querySelectorAll('.tab').forEach((tab) => {
    tab.addEventListener('click', () => switchView(tab.dataset.view));
  });

  // composer
  const input = $('#composer-input');
  input.addEventListener('input', autoGrow);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.ctrlKey && !e.shiftKey) { e.preventDefault(); submitUpdate(); }
  });
  input.addEventListener('paste', handlePaste);
  $('#submit-btn').addEventListener('click', submitUpdate);
  $('#attach-hint').addEventListener('click', async () => {
    const img = await wea.readClipboardImage();
    if (img) { state.pending.push(img); renderThumbs(); }
    else toast('剪贴板没有图片，复制图片后再点（或直接 Ctrl+V）', 'err');
  });

  // report
  $('#report-generate').addEventListener('click', generateReport);
  $('#report-save-schedule').addEventListener('click', saveReportSchedule);
  $('#report-date-from').value = todayStr();
  $('#report-date-to').value = todayStr();
}

// ---- projects ------------------------------------------------------------
async function loadProjects() {
  const res = await wea.listProjects();
  if (!res || !res.ok) {
    renderProjectList([]);
    if (res && res.kind === 'no_workspace') return;
    toast(`加载项目失败：${(res && res.error) || '后端未就绪'}`, 'err');
    return;
  }
  state.projects = res.projects || [];
  renderProjectList(state.projects);
  if (state.projects.length) {
    const keep = state.currentProject &&
      state.projects.find((p) => p.path === state.currentProject.path);
    selectProject(keep || state.projects[0]);
  } else {
    state.currentProject = null;
    $('#project-title').textContent = '';
    $('#tasks-body').innerHTML = '<div class="empty">还没有项目。点左下角「+ 新建项目」开始。</div>';
    $('#timeline-body').innerHTML = '';
  }
}

function renderProjectList(projects) {
  const ul = $('#project-list');
  ul.innerHTML = '';
  projects.forEach((p) => {
    const li = document.createElement('li');
    li.className = 'project-item' +
      (state.currentProject && state.currentProject.path === p.path ? ' active' : '');
    const badge = p.open_task_count > 0 ? `<span class="badge">${p.open_task_count}</span>` : '';
    li.innerHTML =
      `<span class="name">${esc(p.title || p.project_id)}</span>` +
      `<span class="meta">${badge}<span>${esc(p.updated_at || '')}</span></span>`;
    li.addEventListener('click', () => selectProject(p));
    li.addEventListener('contextmenu', (e) => { e.preventDefault(); wea.openProjectDir(p.path); });
    ul.appendChild(li);
  });
}

async function selectProject(p) {
  state.currentProject = p;
  $('#project-title').textContent = p.path;
  document.querySelectorAll('.project-item').forEach((el) => el.classList.remove('active'));
  renderProjectList(state.projects); // re-mark active
  hideConfirmCard();
  await refreshCurrent();
}

async function refreshCurrent() {
  if (!state.currentProject) return;
  const path = state.currentProject.path;
  const [tasks, timeline] = await Promise.all([
    wea.listTasks(path), wea.listTimeline(path),
  ]);
  state.tasksData = tasks && tasks.ok ? tasks : { items: [] };
  state.timelineData = timeline && timeline.ok ? timeline : { events: [] };
  renderTasks();
  renderTimeline();
  // refresh sidebar counts quietly
  const fresh = await wea.listProjects();
  if (fresh && fresh.ok) { state.projects = fresh.projects; renderProjectList(state.projects); }
}

function switchView(view) {
  state.view = view;
  document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('active', t.dataset.view === view));
  $('#tasks-view').classList.toggle('hidden', view !== 'tasks');
  $('#timeline-view').classList.toggle('hidden', view !== 'timeline');
  $('#reports-view').classList.toggle('hidden', view !== 'reports');
  if (view === 'reports' && state.currentProject) {
    $('#report-date-from').value = todayStr();
    $('#report-date-to').value = todayStr();
  }
}

// ---- tasks view ----------------------------------------------------------
function renderTasks() {
  const body = $('#tasks-body');
  const items = (state.tasksData && state.tasksData.items) || [];
  if (!items.length) { body.innerHTML = '<div class="empty">这个项目还没有事项/任务。</div>'; return; }
  body.innerHTML = '';
  items.forEach((item) => {
    const group = document.createElement('div');
    group.className = 'item-group';
    const head = document.createElement('div');
    head.className = 'item-head';
    head.innerHTML =
      `<span class="item-head-title">${esc(item.title)}</span>` +
      `<span class="item-head-acts">
         <button class="icon-btn item-edit-btn" title="重命名">✏️</button>
         <button class="icon-btn item-del-btn" title="删除需求">🗑️</button>
         <button class="ghost add-task-mini" type="button">+ 工作项</button>
       </span>`;
    head.querySelector('.add-task-mini').addEventListener('click', (e) => {
      e.stopPropagation();
      openManualModal('task', item.item_id);
    });
    head.querySelector('.item-edit-btn').addEventListener('click', (e) => {
      e.stopPropagation();
      promptEditItem(item);
    });
    head.querySelector('.item-del-btn').addEventListener('click', (e) => {
      e.stopPropagation();
      confirmDeleteItem(item);
    });
    group.appendChild(head);
    const tasks = item.tasks || [];
    if (!tasks.length) {
      const empty = document.createElement('div');
      empty.className = 'empty-tasks';
      empty.textContent = '暂无工作项';
      group.appendChild(empty);
    } else {
      tasks.forEach((task) => group.appendChild(taskRow(task)));
    }
    body.appendChild(group);
  });
}

function taskRow(task) {
  const row = document.createElement('div');
  row.className = 'task-row';
  const st = task.status === 'done' ? 'done' : 'in_progress';
  const stLabel = st === 'done' ? '已完成' : '进行中';
  const events = ((state.timelineData && state.timelineData.events) || [])
    .filter((e) => e.task_id === task.task_id);
  row.innerHTML =
    `<div class="task-top">
       <span class="task-name"><span class="dot ${st}"></span>${esc(task.title)}</span>
       <span class="task-acts">
         <button class="icon-btn task-edit-btn" title="编辑">✏️</button>
         <button class="icon-btn task-del-btn" title="删除">🗑️</button>
         <span class="status-tag ${st}">${stLabel}</span>
       </span>
     </div>` +
    (task.next_action ? `<div class="task-next">${esc(task.next_action)}</div>` : '') +
    (task.updated_at ? `<div class="task-updated">${esc(relTime(task.updated_at))}</div>` : '') +
    `<div class="task-timeline">${events.map(eventLine).join('') || '<div class="tl-event">暂无归档事件</div>'}</div>`;
  row.querySelector('.task-edit-btn').addEventListener('click', (e) => {
    e.stopPropagation();
    showTaskEditor(row, task);
  });
  row.querySelector('.task-del-btn').addEventListener('click', (e) => {
    e.stopPropagation();
    confirmDeleteTask(row, task);
  });
  row.addEventListener('click', () => row.classList.toggle('expanded'));
  return row;
}

function eventLine(e) {
  return `<div class="tl-event"><span class="tl-time">${esc(fmtTime(e.timestamp))}</span> — ${esc(e.summary)}</div>`;
}

// ---- timeline view -------------------------------------------------------
// ---- inbox view ----------------------------------------------------------

async function loadInbox() {
  try {
    const res = await wea.listCaptures();
    state.inboxCards = (res && res.ok) ? (res.cards || []) : [];
    renderInbox();
  } catch (_) {
    state.inboxCards = [];
    $('#inbox-body').innerHTML = '<div class="empty">Inbox load failed</div>';
  }
}

function renderInbox() {
  const groups = { needs_confirmation: [], processing: [], error: [], archived: [], canceled: [] };
  (state.inboxCards || []).forEach(function(card) {
    if (groups[card.state]) groups[card.state].push(card);
  });
  $('#inbox-body').innerHTML = [
    renderInboxGroup('Needs confirmation', groups.needs_confirmation, true),
    renderInboxGroup('Processing', groups.processing, false),
    renderInboxGroup('Errors', groups.error, false),
    renderInboxGroup('Recent archived', groups.archived.slice(0, 20), false),
  ].join('');
  bindInboxActions();
}

function renderInboxGroup(label, cards, showConfirm) {
  if (!cards.length) return '';
  return '<div class="inbox-group"><h3 class="inbox-group-title">' + label + ' (' + cards.length + ')</h3>' +
    cards.map(function(c) {
      var s = (c.proposal && c.proposal.event) ? c.proposal.event.summary : c.text;
      var icon = c.state === 'processing' ? '\u23F3' : c.state === 'error' ? '\u274C' : c.state === 'archived' ? '\uD83D\uDCC1' : '\u2705';
      var proj = c.selected_project ? esc(c.selected_project.title || '') : '';
      return '<div class="inbox-card"><div class="inbox-card-title">' + icon + ' ' + esc(s || c.text).substring(0, 80) + '</div>' +
        '<div class="inbox-card-meta">' + (proj ? 'Project: ' + proj : '') + (c.state === 'error' ? ' ' + esc(c.error || '') : '') + '</div>' +
        '<div class="inbox-actions">' + renderInboxCardActions(c, showConfirm) + '</div></div>';
    }).join('') + '</div>';
}

function renderInboxCardActions(card, showConfirm) {
  var parts = [];
  var id = esc(card.capture_id);
  if (showConfirm && card.state === 'needs_confirmation') parts.push('<button class="small inbox-confirm" data-id="' + id + '">Confirm</button>');
  if (card.state === 'needs_confirmation' && card.selected_project && card.selected_project.path) parts.push('<button class="ghost small inbox-open" data-id="' + id + '" data-path="' + esc(card.selected_project.path) + '">Open Project</button>');
  if (card.state === 'error') parts.push('<button class="ghost small inbox-retry" data-id="' + id + '">Retry</button>');
  if (card.state !== 'archived') parts.push('<button class="ghost small inbox-cancel" data-id="' + id + '">Cancel</button>');
  return parts.join('');
}

function bindInboxActions() {
  document.querySelectorAll('.inbox-confirm').forEach(function(btn) { btn.addEventListener('click', async function() { btn.textContent = '...'; btn.disabled = true; await wea.commitCapture(btn.dataset.id, {}); loadInbox(); }); });
  document.querySelectorAll('.inbox-retry').forEach(function(btn) { btn.addEventListener('click', async function() { btn.textContent = '...'; btn.disabled = true; await wea.processCapture(btn.dataset.id); loadInbox(); }); });
  document.querySelectorAll('.inbox-cancel').forEach(function(btn) { btn.addEventListener('click', async function() { btn.textContent = '...'; btn.disabled = true; await wea.cancelCapture(btn.dataset.id); loadInbox(); }); });
  document.querySelectorAll('.inbox-open').forEach(function(btn) { btn.addEventListener('click', function() { var proj = state.projectList.find(function(p) { return p.path === btn.dataset.path; }); if (proj) selectProject(proj); }); });
}
function renderTimeline() {
  const body = $('#timeline-body');
  const events = (state.timelineData && state.timelineData.events) || [];
  if (!events.length) { body.innerHTML = '<div class="empty">还没有归档事件。</div>'; return; }
  body.innerHTML = '';
  events.forEach((e) => {
    const row = document.createElement('div');
    row.className = 'tl-row';
    row.innerHTML =
      `<div class="tl-when">${esc(fmtTime(e.timestamp))}</div>
       <div class="tl-main">
         <div class="tl-path">${esc(e.task_title || e.task_id)}</div>
         <div class="tl-summary">${esc(e.summary)}</div>
         ${e.has_attachment ? '<div class="tl-clip">📎 含附件</div>' : ''}
       </div>`;
    body.appendChild(row);
  });
}

// ---- inline edit / delete helpers ----------------------------------------

function openEditItemModal(item) {
  state.editingItem = item;
  $('#edit-item-title').value = item.title || '';
  $('#edit-item-background').value = item.background || '';
  $('#edit-item-error').classList.add('hidden');
  $('#edit-item-save').disabled = false;
  $('#edit-item-modal').classList.remove('hidden');
  $('#edit-item-title').focus();
}

function closeEditItemModal() {
  $('#edit-item-modal').classList.add('hidden');
  state.editingItem = null;
}

async function saveEditItem() {
  const title = $('#edit-item-title').value.trim();
  if (!title) {
    $('#edit-item-error').textContent = '需求名称不能为空';
    $('#edit-item-error').classList.remove('hidden');
    return;
  }

  $('#edit-item-save').disabled = true;
  $('#edit-item-error').classList.add('hidden');
  const background = $('#edit-item-background').value.trim();

  try {
    const res = await wea.updateItem(
      state.currentProject.path,
      state.editingItem.item_id,
      title,
      background
    );
    if (!res || !res.ok) {
      $('#edit-item-error').textContent = `保存失败：${(res && res.error) || '后端错误'}`;
      $('#edit-item-error').classList.remove('hidden');
      return;
    }
    closeEditItemModal();
    toast('需求已更新', 'ok');
    await refreshCurrent();
  } catch (err) {
    $('#edit-item-error').textContent = `保存出错：${err.message || err}`;
    $('#edit-item-error').classList.remove('hidden');
  } finally {
    $('#edit-item-save').disabled = false;
  }
}

function promptEditItem(item) {
  // Replaced by in-app modal — kept for compatibility, routed to modal
  openEditItemModal(item);
}

async function doUpdateItem(itemId, title) {
  try {
    const res = await wea.updateItem(state.currentProject.path, itemId, title);
    if (!res || !res.ok) { toast(`重命名失败：${(res && res.error) || '后端错误'}`, 'err'); return; }
    toast('需求已重命名', 'ok');
    await refreshCurrent();
  } catch (err) { toast(`重命名出错：${err.message || err}`, 'err'); }
}

function confirmDeleteItem(item) {
  const taskCount = item.tasks ? item.tasks.length : 0;
  const msg = taskCount > 0
    ? `删除「${item.title}」及其下 ${taskCount} 个工作项？此操作不可撤销。`
    : `删除空需求「${item.title}」？`;
  showDeleteConfirm(msg, () => doDeleteItem(item.item_id));
}

async function doDeleteItem(itemId) {
  try {
    const res = await wea.deleteItem(state.currentProject.path, itemId);
    if (!res || !res.ok) { toast(`删除失败：${(res && res.error) || '后端错误'}`, 'err'); return; }
    toast('需求已删除', 'ok');
    await refreshCurrent();
  } catch (err) { toast(`删除出错：${err.message || err}`, 'err'); }
}

function showTaskEditor(row, task) {
  // Remove any existing editor first
  const existing = row.querySelector('.task-editor');
  if (existing) { existing.remove(); return; }

  const editor = document.createElement('div');
  editor.className = 'task-editor';
  const doneSel = task.status === 'done' ? 'selected' : '';
  const progSel = task.status === 'in_progress' ? 'selected' : '';
  editor.innerHTML =
    `<div class="te-row">
       <label>名称</label>
       <input id="te-title" value="${esc(task.title)}" />
     </div>
     <div class="te-row">
       <label>状态</label>
       <select id="te-status">
         <option value="in_progress" ${progSel}>进行中</option>
         <option value="done" ${doneSel}>已完成</option>
       </select>
     </div>
     <div class="te-row">
       <label>下一步</label>
       <input id="te-next" value="${esc(task.next_action || '')}" placeholder="下一步要做什么…" />
     </div>
     <div class="te-acts">
       <button class="ghost small" id="te-cancel">取消</button>
       <button class="primary small-btn" id="te-save">保存</button>
     </div>`;
  row.appendChild(editor);
  row.classList.add('expanded');

  editor.querySelector('#te-cancel').addEventListener('click', (e) => {
    e.stopPropagation();
    editor.remove();
  });
  editor.querySelector('#te-save').addEventListener('click', (e) => {
    e.stopPropagation();
    const newTitle = editor.querySelector('#te-title').value.trim();
    const newStatus = editor.querySelector('#te-status').value;
    const newNext = editor.querySelector('#te-next').value.trim();
    saveTaskEdits(task, newTitle, newStatus, newNext, row);
  });

  editor.addEventListener('click', (e) => e.stopPropagation());
  editor.querySelector('#te-title').focus();
}

async function saveTaskEdits(task, newTitle, newStatus, newNext, row) {
  const proj = state.currentProject.path;
  const errors = [];

  try {
    if (newTitle && newTitle !== task.title) {
      const r = await wea.updateTask(proj, task.task_id, 'title', newTitle);
      if (!r || !r.ok) errors.push(`名称：${(r && r.error) || '失败'}`);
    }
    if (newStatus !== task.status) {
      const r = await wea.updateTask(proj, task.task_id, 'status', newStatus);
      if (!r || !r.ok) errors.push(`状态：${(r && r.error) || '失败'}`);
    }
    if (newNext !== (task.next_action || '')) {
      const r = await wea.updateTask(proj, task.task_id, 'next_action', newNext);
      if (!r || !r.ok) errors.push(`下一步：${(r && r.error) || '失败'}`);
    }

    if (errors.length) { toast(`部分更新失败：${errors.join('；')}`, 'err'); }
    else { toast('已保存', 'ok'); }
    await refreshCurrent();
  } catch (err) {
    toast(`保存出错：${err.message || err}`, 'err');
    // Remove editor on error so user sees the original state
    const editor = row.querySelector('.task-editor');
    if (editor) editor.remove();
  }
}

function confirmDeleteTask(row, task) {
  showDeleteConfirm(`删除工作项「${task.title}」？时间线归档记录会保留。此操作不可撤销。`, () => doDeleteTask(task.task_id, row));
}

async function doDeleteTask(taskId, row) {
  try {
    const res = await wea.deleteTask(state.currentProject.path, taskId);
    if (!res || !res.ok) { toast(`删除失败：${(res && res.error) || '后端错误'}`, 'err'); return; }
    toast('工作项已删除', 'ok');
    await refreshCurrent();
  } catch (err) { toast(`删除出错：${err.message || err}`, 'err'); }
}

// ---- composer / propose --------------------------------------------------
function autoGrow() {
  const ta = $('#composer-input');
  ta.style.height = 'auto';
  ta.style.height = Math.min(160, ta.scrollHeight) + 'px';
}

async function handlePaste(e) {
  const items = (e.clipboardData && e.clipboardData.items) || [];
  for (const it of items) {
    if (it.type && it.type.startsWith('image/')) {
      e.preventDefault();
      const img = await wea.readClipboardImage();
      if (img) { state.pending.push(img); renderThumbs(); }
      return;
    }
  }
}

function renderThumbs() {
  const wrap = $('#thumbs');
  if (!state.pending.length) { wrap.classList.add('hidden'); wrap.innerHTML = ''; return; }
  wrap.classList.remove('hidden');
  wrap.innerHTML = '';
  state.pending.forEach((p, idx) => {
    const div = document.createElement('div');
    div.className = 'thumb';
    div.innerHTML = `<img src="${p.dataUrl}" /><button class="rm">×</button>`;
    div.querySelector('.rm').addEventListener('click', () => {
      wea.discardPending([p.tempPath]);
      state.pending.splice(idx, 1);
      renderThumbs();
    });
    wrap.appendChild(div);
  });
}

async function submitUpdate() {
  if (state.busy) return;
  const text = $('#composer-input').value.trim();
  if (!text) { setStatus('请输入进展内容', 'error'); return; }
  if (!state.currentProject) { setStatus('请先选择或新建一个项目', 'error'); return; }
  state.busy = true;
  setStatus('正在归档…正在调用 opencode 解析（约 10 秒）', 'loading');
  try {
    const res = await wea.propose(text, state.currentProject.path, state.pending.map((p) => p.tempPath));
    if (!res || !res.ok) {
      setStatus(`解析失败：${(res && res.error) || '后端未就绪'}`, 'error');
      return;
    }
    state.proposal = res.proposal;
    setStatus('');
    renderConfirmCard(res.proposal, !!res.low_confidence);
  } catch (err) {
    setStatus(`出错：${err.message || err}`, 'error');
  } finally {
    state.busy = false;
  }
}

// ---- in-app delete confirm (replaces native confirm() — avoids OS focus loss in Electron) ----
let _deleteCallback = null;
let _deleteEscHandler = null;

function showDeleteConfirm(message, onConfirm) {
  if (_deleteEscHandler) document.removeEventListener('keydown', _deleteEscHandler);
  _deleteCallback = onConfirm;
  const card = $('#confirm-card');
  card.innerHTML =
    `<div class="cc-head"><h3>确认删除</h3></div>
     <div style="color:var(--text-dim);margin-bottom:14px;font-size:14px;white-space:pre-wrap;">${esc(message)}</div>
     <div class="cc-actions">
       <button class="ghost" id="dc-cancel">取消</button>
       <button class="danger small-btn" id="dc-confirm">确认删除</button>
     </div>`;
  card.classList.remove('hidden');

  $('#dc-cancel').addEventListener('click', cancelDeleteConfirm);
  $('#dc-confirm').addEventListener('click', commitDeleteConfirm);
  _deleteEscHandler = (e) => { if (e.key === 'Escape') cancelDeleteConfirm(); };
  document.addEventListener('keydown', _deleteEscHandler);
}

function cancelDeleteConfirm() {
  hideConfirmCard();
}

function commitDeleteConfirm() {
  const cb = _deleteCallback;
  hideConfirmCard();
  if (cb) cb();
}

// ---- confirm card (archival) -----------------------------------------------
function renderConfirmCard(proposal, lowConf) {
  const card = $('#confirm-card');
  const t = proposal.target;
  const e = proposal.event;
  const conf = Math.round((proposal.confidence || 0) * 100);
  const allTasks = collectTaskOptions();

  const taskSelect = lowConf
    ? `<select id="cc-task">
         ${allTasks.map((o) => `<option value="${esc(o.task_id)}" ${o.task_id === t.task_id ? 'selected' : ''}>${esc(o.label)}</option>`).join('')}
       </select>`
    : `<span class="v">${esc(taskLabel(t.task_id) || t.task_id)}</span>`;

  card.innerHTML =
    `<div class="cc-head">
       <h3>归档预览</h3>
       <span class="cc-conf ${lowConf ? 'low' : 'high'}">置信度 ${conf}%</span>
     </div>` +
    (lowConf ? '<div class="cc-warn">不太确定归到哪个任务，请确认或下拉修正。</div>' : '') +
    `<div class="cc-grid">
       <span class="k">项目</span><span class="v">${esc(state.currentProject.title || t.project_id)}</span>
       <span class="k">任务</span>${taskSelect}
       <span class="k">状态</span>
       <select id="cc-status">
         <option value="in_progress" ${e.status === 'in_progress' ? 'selected' : ''}>进行中</option>
         <option value="done" ${e.status === 'done' ? 'selected' : ''}>已完成</option>
       </select>
       <span class="k">摘要</span><input id="cc-summary" value="${esc(e.summary)}" />
       <span class="k">下一步</span><input id="cc-next" value="${esc(e.next_action)}" />
       ${state.pending.length ? `<span class="k">附件</span><span class="v">${state.pending.map((p) => esc(p.filename)).join(', ')}</span>` : ''}
     </div>
     <div class="cc-actions">
       <button class="ghost" id="cc-cancel">取消</button>
       <button class="primary" id="cc-confirm">确认归档</button>
     </div>`;
  card.classList.remove('hidden');

  $('#cc-cancel').addEventListener('click', hideConfirmCard);
  $('#cc-confirm').addEventListener('click', commitProposal);
}

function collectTaskOptions() {
  const out = [];
  const items = (state.tasksData && state.tasksData.items) || [];
  items.forEach((it) => (it.tasks || []).forEach((tk) =>
    out.push({ task_id: tk.task_id, label: `${it.title} / ${tk.title}` })));
  return out;
}
function taskLabel(taskId) {
  const o = collectTaskOptions().find((x) => x.task_id === taskId);
  return o ? o.label : '';
}

function hideConfirmCard() {
  $('#confirm-card').classList.add('hidden');
  state.proposal = null;
  _deleteCallback = null;
  if (_deleteEscHandler) { document.removeEventListener('keydown', _deleteEscHandler); _deleteEscHandler = null; }
}

async function commitProposal() {
  if (!state.proposal) return;
  const p = JSON.parse(JSON.stringify(state.proposal));
  // apply edits
  const taskSel = $('#cc-task');
  if (taskSel) { p.target.task_id = taskSel.value; p.event.task_id = taskSel.value; }
  p.event.status = $('#cc-status').value;
  p.event.summary = $('#cc-summary').value;
  p.event.next_action = $('#cc-next').value;

  setStatus('正在写入…', 'loading');
  try {
    const res = await wea.commit(p, state.currentProject.path, state.pending);
    if (!res || !res.ok) { setStatus(`写入失败：${(res && res.error) || '未知错误'}`, 'error'); return; }
    // cleanup
    hideConfirmCard();
    $('#composer-input').value = '';
    autoGrow();
    state.pending = [];
    renderThumbs();
    setStatus('');
    toast('✅ 已归档', 'ok');
    await refreshCurrent();
  } catch (err) {
    setStatus(`出错：${err.message || err}`, 'error');
  }
}

// ---- manual item/task creation -------------------------------------------
function openManualModal(mode, itemId = '') {
  if (!state.currentProject) {
    toast('请先选择或新建一个项目', 'err');
    return;
  }

  state.manualMode = mode;
  state.manualItemId = itemId || '';
  $('#manual-error').classList.add('hidden');
  $('#manual-name').value = '';
  $('#manual-background').value = '';
  $('#manual-name').disabled = false;
  $('#manual-name').readOnly = false;

  // Defensive reset: ensure input is fully interactable
  const input = $('#manual-name');
  input.disabled = false;
  input.removeAttribute('readonly');
  input.readOnly = false;

  $('#manual-create').disabled = false;

  const isTask = mode === 'task';
  $('#manual-title').textContent = isTask ? '新建工作项' : '新建需求';
  $('#manual-name-label').textContent = isTask ? '工作项名称' : '需求名称';
  $('#manual-name').placeholder = isTask ? '例如：梳理推理链路' : '例如：明确项目需求';
  $('#manual-item-field').classList.toggle('hidden', !isTask);

  if (isTask) {
    const items = (state.tasksData && state.tasksData.items) || [];
    const select = $('#manual-item');
    select.innerHTML = items
      .map((item) => `<option value="${esc(item.item_id)}">${esc(item.title)}</option>`)
      .join('');
    if (!items.length) {
      toast('请先新建一个需求，再添加工作项', 'err');
      return;
    }
    select.value = itemId || items[0].item_id;
  }

  $('#manual-modal').classList.remove('hidden');
  requestAnimationFrame(() => $('#manual-name').focus());
}

function closeManualModal() {
  $('#manual-modal').classList.add('hidden');
  state.manualMode = null;
  state.manualItemId = '';
}

async function createManualEntry() {
  const title = $('#manual-name').value.trim();
  const mode = state.manualMode;
  if (!title) {
    showManualError(mode === 'task' ? '请填写工作项名称' : '请填写需求名称');
    return;
  }

  $('#manual-create').disabled = true;
  try {
    const background = $('#manual-background').value.trim();
    const res = mode === 'task'
      ? await wea.createTask(state.currentProject.path, $('#manual-item').value, title)
      : await wea.createItem(state.currentProject.path, title, background);

    if (!res || !res.ok) {
      showManualError(`创建失败：${(res && res.error) || '后端未返回结果'}`);
      return;
    }

    closeManualModal();
    toast(mode === 'task' ? '工作项已创建' : '需求已创建', 'ok');
    await refreshCurrent();
  } catch (err) {
    showManualError(`创建失败：${err.message || err}`);
  } finally {
    $('#manual-create').disabled = false;
  }
}

function showManualError(msg) {
  const el = $('#manual-error');
  el.textContent = msg;
  el.classList.remove('hidden');
}

// ---- settings -------------------------------------------------------------

function _keyCodeToElectron(code, key) {
  const map = {
    Space: 'Space', Backspace: 'Backspace', Delete: 'Delete',
    Enter: 'Return', Escape: 'Escape', Tab: 'Tab',
    ArrowUp: 'Up', ArrowDown: 'Down', ArrowLeft: 'Left', ArrowRight: 'Right',
    Home: 'Home', End: 'End', PageUp: 'PageUp', PageDown: 'PageDown',
  };
  if (map[code]) return map[code];
  if (/^F\d+$/.test(code)) return code;
  if (/^Digit(\d)$/.test(code)) return code.replace('Digit', '');
  if (/^Key([A-Z])$/.test(code)) return code.replace('Key', '');
  if (key.length === 1) return key.toUpperCase();
  return null;
}

function openSettingsModal() {
  state.settingsWorkspace = (state.config && state.config.workspace) || '';
  $('#settings-workspace').value = state.settingsWorkspace;
  $('#settings-hotkey').value = (state.config && state.config.hotkey) || 'CommandOrControl+Shift+Space';
  $('#settings-error').classList.add('hidden');
  $('#settings-modal').classList.remove('hidden');
  // Re-attach keydown capture each time modal opens (prevents duplicate listeners)
  const hotkeyInput = $('#settings-hotkey');
  hotkeyInput.onkeydown = null; // clear previous
  hotkeyInput.addEventListener('keydown', function _captureHotkey(e) {
    if (e.key === 'Tab') return; // allow Tab to move focus
    if (e.key === 'Backspace' || e.key === 'Delete') {
      hotkeyInput.value = '';
      e.preventDefault();
      return;
    }
    // Only capture when at least one modifier is held
    if (!e.ctrlKey && !e.metaKey && !e.altKey && !e.shiftKey) return;
    e.preventDefault();
    e.stopPropagation();
    const keys = [];
    if (e.ctrlKey || e.metaKey) keys.push('CommandOrControl');
    if (e.altKey) keys.push('Alt');
    if (e.shiftKey) keys.push('Shift');
    const mainKey = _keyCodeToElectron(e.code, e.key);
    if (mainKey) {
      keys.push(mainKey);
      hotkeyInput.value = keys.join('+');
    }
  });
}

async function pickSettingsWorkspace() {
  const dir = await wea.pickWorkspaceDir();
  if (!dir) return;
  state.settingsWorkspace = dir;
  $('#settings-workspace').value = dir;
}

async function saveSettings() {
  const hotkey = $('#settings-hotkey').value.trim() || 'CommandOrControl+Shift+Space';
  const patch = { hotkey };
  if (state.settingsWorkspace) patch.workspace = state.settingsWorkspace;
  $('#settings-save').disabled = true;
  try {
    const updated = await wea.updateConfig(patch);
    state.config = updated;
    if (!updated.hotkeyRegistered) {
      showSettingsError('快捷键注册失败，请换一个组合键');
      return;
    }
    $('#settings-modal').classList.add('hidden');
    toast('设置已保存', 'ok');
    await loadProjects();
  } catch (err) {
    showSettingsError(`保存失败：${err.message || err}`);
  } finally {
    $('#settings-save').disabled = false;
  }
}

function showSettingsError(msg) {
  const el = $('#settings-error');
  el.textContent = msg;
  el.classList.remove('hidden');
}

// ---- init project modal --------------------------------------------------
function openInitModal() {
  $('#init-title').value = '';
  $('#init-id').value = '';
  $('#init-items').innerHTML = '';
  $('#init-error').classList.add('hidden');
  addInitItem();
  $('#init-modal').classList.remove('hidden');
}

function addInitItem() {
  const wrap = $('#init-items');
  const div = document.createElement('div');
  div.className = 'init-item';
  div.innerHTML =
    `<div class="row">
       <input class="item-title" placeholder="事项名，如：使用 KV cache 优化 few-shot" />
       <button class="rm-x" title="删除事项">×</button>
     </div>
     <div class="tasks"></div>
     <button class="ghost add-task">+ 添加任务</button>`;
  div.querySelector('.rm-x').addEventListener('click', () => div.remove());
  div.querySelector('.add-task').addEventListener('click', () => addInitTask(div.querySelector('.tasks')));
  wrap.appendChild(div);
  addInitTask(div.querySelector('.tasks'));
}

function addInitTask(tasksWrap) {
  const row = document.createElement('div');
  row.className = 'row';
  row.innerHTML = `<input class="task-title" placeholder="任务名，如：查看当前阻塞点" /><button class="rm-x">×</button>`;
  row.querySelector('.rm-x').addEventListener('click', () => row.remove());
  tasksWrap.appendChild(row);
}

async function createProject() {
  const title = $('#init-title').value.trim();
  const projectId = $('#init-id').value.trim() || slugify(title);
  if (!title) { showInitError('请填写项目名称'); return; }
  const items = [];
  document.querySelectorAll('#init-items .init-item').forEach((el) => {
    const itTitle = el.querySelector('.item-title').value.trim();
    if (!itTitle) return;
    const tasks = [];
    el.querySelectorAll('.task-title').forEach((ti) => { const v = ti.value.trim(); if (v) tasks.push(v); });
    items.push({ title: itTitle, tasks });
  });
  const res = await wea.initProject({ title, project_id: projectId, items });
  if (!res || !res.ok) {
    showInitError(res && res.kind === 'exists' ? '已存在同名项目 ID，请换一个' : `创建失败：${(res && res.error) || '后端未就绪'}`);
    return;
  }
  $('#init-modal').classList.add('hidden');
  toast('✅ 项目已创建', 'ok');
  await loadProjects();
  const created = state.projects.find((p) => p.project_id === res.project_id);
  if (created) selectProject(created);
}

function showInitError(msg) { const el = $('#init-error'); el.textContent = msg; el.classList.remove('hidden'); }

// ---- helpers -------------------------------------------------------------
function setStatus(msg, kind) {
  const el = $('#composer-status');
  el.textContent = msg || '';
  el.className = 'composer-status' + (kind ? ' ' + kind : '');
}
let toastTimer = null;
function toast(msg, kind) {
  const el = $('#toast');
  el.textContent = msg;
  el.className = 'toast' + (kind ? ' ' + kind : '');
  el.classList.remove('hidden');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add('hidden'), 2400);
}
function slugify(s) {
  return (s || '').toLowerCase().trim().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
}
function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
function fmtTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}
function relTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return '刚刚';
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  return `${Math.floor(diff / 86400)} 天前`;
}

// ---- reports --------------------------------------------------------------
function todayStr() { return new Date().toISOString().slice(0, 10); }

async function generateReport() {
  const type = $('#report-type').value;
  const dateFrom = $('#report-date-from').value || todayStr();
  const dateTo = $('#report-date-to').value || dateFrom;
  const projectId = (type === 'project_summary' && state.currentProject)
    ? state.currentProject.project_id : null;
  const statusEl = $('#report-status');
  statusEl.textContent = '生成中...';
  try {
    const res = await wea.generateReport({
      type,
      projectId,
      dateFrom,
      dateTo,
      persist: true,
      mode: 'manual',
      includeAi: true,
    });
    if (!res || !res.ok) {
      $('#reports-body').innerHTML =
        `<div class="empty">生成失败：${esc((res && res.error) || '未知错误')}</div>`;
      statusEl.textContent = '';
      return;
    }
    statusEl.textContent = res.skipped
      ? '无记录，已跳过'
      : `${res.event_count || 0} 条记录 · ${res.project_count || 0} 个项目`;
    const pathHtml = res.written_path
      ? `<div class="report-path">${esc(res.written_path)}</div>`
      : '';
    $('#reports-body').innerHTML =
      `${pathHtml}<pre class="report-md">${esc(res.report || '')}</pre>`;
  } catch (e) {
    $('#reports-body').innerHTML =
      `<div class="empty">生成失败：${esc(e.message || String(e))}</div>`;
    statusEl.textContent = '';
  }
}

async function saveReportSchedule() {
  const reportSchedule = {
    dailyEnabled: $('#report-daily-enabled').checked,
    dailyTime: $('#report-daily-time').value || '23:30',
    weeklyEnabled: $('#report-weekly-enabled').checked,
    weeklyDay: Number($('#report-weekly-day').value),
    weeklyTime: $('#report-weekly-time').value || '18:00',
  };
  const res = await wea.updateConfig({ reportSchedule });
  $('#report-status').textContent = res && res.ok ? '定时设置已保存' : '定时设置保存失败';
}

async function loadReportSchedule() {
  const res = await wea.getReportScheduleStatus();
  if (res && res.ok && res.reportSchedule) {
    const s = res.reportSchedule;
    $('#report-daily-enabled').checked = s.dailyEnabled || false;
    $('#report-daily-time').value = s.dailyTime || '23:30';
    $('#report-weekly-enabled').checked = s.weeklyEnabled || false;
    $('#report-weekly-day').value = String(s.weeklyDay != null ? s.weeklyDay : 5);
    $('#report-weekly-time').value = s.weeklyTime || '18:00';
  }
}

window.addEventListener('DOMContentLoaded', boot);
