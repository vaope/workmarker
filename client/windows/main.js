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

  $('#change-workspace').addEventListener('click', async () => {
    const dir = await wea.pickWorkspaceDir();
    if (dir) { state.config = await wea.setWorkspace(dir); await loadProjects(); }
  });

  $('#new-project').addEventListener('click', openInitModal);
  $('#init-cancel').addEventListener('click', () => $('#init-modal').classList.add('hidden'));
  $('#init-add-item').addEventListener('click', () => addInitItem());
  $('#init-create').addEventListener('click', createProject);
  $('#init-title').addEventListener('input', (e) => {
    $('#init-id').value = slugify(e.target.value);
  });

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
    group.innerHTML = `<div class="item-head">${esc(item.title)}</div>`;
    (item.tasks || []).forEach((task) => group.appendChild(taskRow(task)));
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
       <span class="status-tag ${st}">${stLabel}</span>
     </div>` +
    (task.next_action ? `<div class="task-next">${esc(task.next_action)}</div>` : '') +
    (task.updated_at ? `<div class="task-updated">${esc(relTime(task.updated_at))}</div>` : '') +
    `<div class="task-timeline">${events.map(eventLine).join('') || '<div class="tl-event">暂无归档事件</div>'}</div>`;
  row.addEventListener('click', () => row.classList.toggle('expanded'));
  return row;
}

function eventLine(e) {
  return `<div class="tl-event"><span class="tl-time">${esc(fmtTime(e.timestamp))}</span> — ${esc(e.summary)}</div>`;
}

// ---- timeline view -------------------------------------------------------
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

// ---- confirm card --------------------------------------------------------
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

function hideConfirmCard() { $('#confirm-card').classList.add('hidden'); state.proposal = null; }

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

window.addEventListener('DOMContentLoaded', boot);
