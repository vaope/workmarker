// main.js (renderer) — main window logic. Talks to main process only via window.wea (preload).
const $ = (sel) => document.querySelector(sel);

const state = {
  config: null,
  projects: [],
  currentProject: null, // {project_id, title, path}
  tasksData: null,      // {items:[{item_id,title,tasks:[...]}]}
  pending: [],          // [{tempPath, filename}]
  inboxCards: [],       // Inbox cards for Today summary
  knowledgeState: { jobs: [], proposals: [], runs: [] },
  searchResults: [],
  view: 'tasks',
  panoramaData: null,
  migrationPreview: null,
  busy: false,
  manualMode: null,     // "item" | "task"
  manualItemId: '',
  settingsWorkspace: '',
};

const taskCompletion = TaskCompletion.createController({
  getProjectPath: () => state.currentProject.path,
  completeTask: (projectPath, taskId, conclusion, nextTaskTitle) =>
    wea.completeTask(projectPath, taskId, conclusion, nextTaskTitle),
  updateTask: (projectPath, taskId, field, value) =>
    wea.updateTask(projectPath, taskId, field, value),
  refresh: () => refreshCurrent(),
  notify: (message, kind) => toast(message, kind),
});

// ---- boot ----------------------------------------------------------------
async function boot() {
  bindStaticHandlers();
  state.config = await wea.getConfig();
  showStartupHotkeyWarning();
  if (!state.config.workspace) {
    enterSetup();
  } else {
    enterApp();
    await loadProjects();
  }
  wea.onArchived(() => { if (state.currentProject) refreshCurrent(); });
  wea.onInboxUpdated(() => {
    if (state.currentProject) refreshCurrent();
    if (state.view === 'inbox') loadInbox();
    refreshActionSummary();
  });
  wea.onKnowledgeUpdated((payload) => {
    const kind = (payload && payload.kind) || 'updated';
    const message = kind === 'job_queued' || kind === 'schedule_enqueued'
      ? '知识综合已排队，可在收件箱查看进度'
      : kind === 'job_error' || kind === 'schedule_error'
        ? '知识综合遇到错误，可在收件箱重试'
        : '知识综合状态已更新，请在收件箱审核';
    toast(message, kind.includes('error') ? 'err' : 'info');
    if (state.view === 'inbox') loadInbox();
    else loadKnowledgeState(state.currentProject && state.currentProject.path).then(renderActionSummary);
  });
  wea.onUpdateState(handleUpdateState);
  loadReportSchedule();
  // Check for unfinished cross-project corrections on startup
  if (state.config.workspace) checkPendingCorrections();
}

function enterSetup() {
  $('#setup').classList.remove('hidden');
  $('#app').classList.add('hidden');
}
function enterApp() {
  $('#setup').classList.add('hidden');
  $('#app').classList.remove('hidden');
}

function showStartupHotkeyWarning() {
  if (!state.config) return;
  if (state.config.hotkeyRegistered !== false && state.config.mainHotkeyRegistered !== false) return;
  const kind = state.config.hotkeyErrorKind || 'registration_conflict';
  const failed = state.config.failedHotkey || (state.config.hotkeyRegistered === false ? 'capture' : 'main');
  toast(`快捷键注册失败(${kind}: ${failed})，请到设置更换组合键`, 'err');
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
  $('#update-check').addEventListener('click', checkForApplicationUpdate);
  $('#update-download').addEventListener('click', downloadApplicationUpdate);
  $('#update-install').addEventListener('click', installApplicationUpdate);

  // tabs
  document.querySelectorAll('.tab').forEach((tab) => {
    tab.addEventListener('click', () => switchView(tab.dataset.view));
  });

  // search button and Enter key
  const srBtn = $('#search-run');
  if (srBtn) srBtn.addEventListener('click', runSearch);
  const gsInput = $('#global-search');
  if (gsInput) gsInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') runSearch(); });
  $('#search-knowledge-select').addEventListener('click', synthesizeSelectedSearchEvents);

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

  // correction modal
  $('#corr-cancel').addEventListener('click', closeCorrectionModal);
  $('#corr-submit').addEventListener('click', submitCorrection);
  $('#corr-target-project').addEventListener('change', onCorrectionTargetProjectChange);

  // panorama modals
  $('#migration-cancel').addEventListener('click', closeMigrationModal);
  $('#migration-preview').addEventListener('click', previewMigration);
  $('#migration-apply').addEventListener('click', applyMigration);
  $('#profile-save').addEventListener('click', saveProfile);
  $('#profile-cancel').addEventListener('click', closeProfileModal);
  $('#section-save').addEventListener('click', saveSection);
  $('#section-cancel').addEventListener('click', closeSectionModal);
  $('#knowledge-event-cancel').addEventListener('click', closeKnowledgeEventModal);
  $('#knowledge-event-submit').addEventListener('click', submitKnowledgeEventSelection);

  // report
  $('#report-generate').addEventListener('click', generateReport);
  $('#report-save-schedule').addEventListener('click', saveReportSchedule);
  $('#report-date-from').value = todayStr();
  $('#report-date-to').value = todayStr();

  // recovery banner dismiss (bound once; handler reads dynamic state)
  $('#recovery-dismiss').addEventListener('click', hideRecoveryBanner);

  // today rail
  $('#today-focus').addEventListener('click', focusTodayRail);
  $('#sidebar-today').addEventListener('click', focusTodayRail);
  $('#pending-focus').addEventListener('click', () => switchView('inbox'));
  $('#today-pending').addEventListener('click', () => switchView('inbox'));
  $('#today-open').addEventListener('click', () => {
    switchView('tasks');
    $('#tasks-body').scrollTop = 0;
  });
  $('#today-reports').addEventListener('click', () => switchView('reports'));
  $('#quick-record-focus').addEventListener('click', () => $('#composer-input').focus());
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
  const [tasks, panorama, knowledge] = await Promise.all([
    wea.listTasks(path),
    wea.getProjectPanorama(path),
    wea.getKnowledgeState(path),
  ]);
  state.tasksData = tasks && tasks.ok ? tasks : { items: [] };
  state.panoramaData = panorama && panorama.ok ? panorama : null;
  state.knowledgeState = knowledge && knowledge.ok
    ? knowledge : { jobs: [], proposals: [], runs: [] };
  renderProjectPanorama();
  const fresh = await wea.listProjects();
  if (fresh && fresh.ok) { state.projects = fresh.projects; renderProjectList(state.projects); }
  renderActionSummary();
}

function switchView(view) {
  state.view = view;
  document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('active', t.dataset.view === view));
  $('#tasks-view').classList.toggle('hidden', view !== 'tasks');
  $('#reports-view').classList.toggle('hidden', view !== 'reports');
  $('#inbox-view').classList.toggle('hidden', view !== 'inbox');
  if (view === 'inbox') loadInbox();
  $('#search-view').classList.toggle('hidden', view !== 'search');
  if (view === 'reports' && state.currentProject) {
    $('#report-date-from').value = todayStr();
    $('#report-date-to').value = todayStr();
  }
}

// ---- search ---------------------------------------------------------------

function runSearch() {
  const q = $('#global-search');
  if (!q) return;
  const query = q.value.trim();
  if (!query) return;
  wea.search(query, 50).then((r) => {
    if (!r || !r.ok) {
      $('#search-results').innerHTML = '<div class="empty">搜索失败: ' + esc((r && r.error) || '') + '</div>';
      return;
    }
    if (!r.results || !r.results.length) {
      $('#search-results').innerHTML = '<div class="empty">没有找到: ' + esc(query) + '</div>';
      return;
    }
    $('#search-results').innerHTML =
      '<div class="search-header">' + r.results.length + ' 条结果</div>' +
      r.results.map((d) => {
        const kindLabel = { project: '项目', item: '工作项', task: '任务', timeline: '时间线', report: '报告', inbox: '收件箱' }[d.kind] || d.kind;
        const title = esc(d.title || d.snippet || '').substring(0, 120);
        const path = esc(d.path || '');
        const eventSelector = d.kind === 'timeline' && d.event_id
          ? '<input class="search-event-checkbox" type="checkbox" data-event-id="' + esc(d.event_id) +
            '" data-project-id="' + esc(d.project_id || '') + '" data-path="' + path + '" />'
          : '';
        return '<div class="search-result" data-path="' + path + '" data-kind="' + d.kind + '" data-proj-id="' + esc(d.project_id || '') + '" data-event-id="' + esc(d.event_id || '') + '">' +
          eventSelector +
          '<span class="search-kind">' + kindLabel + '</span>' +
          '<div class="search-title">' + title + '</div>' +
          (d.snippet ? '<div class="search-snippet">' + esc(d.snippet).substring(0, 200) + '</div>' : '') +
          (path ? '<div class="search-path">' + path + '</div>' : '') +
        '</div>';
      }).join('');
    state.searchResults = r.results;
    bindSearchResults();
  }).catch((err) => {
    $('#search-results').innerHTML = '<div class="empty">错误: ' + esc(err.message || '') + '</div>';
  });
}

function bindSearchResults() {
  document.querySelectorAll('.search-result').forEach((el) => {
    el.addEventListener('click', (event) => {
      if (event.target.classList.contains('search-event-checkbox')) return;
      const path = el.dataset.path;
      const kind = el.dataset.kind;
      const projId = el.dataset.projId;
      if (kind === 'report' && path) { wea.openProjectDir(path); return; }
      const proj = state.projects.find((p) => p.path === path || p.project_id === projId);
      if (proj) { selectProject(proj); switchView('tasks'); }
    });
  });
  document.querySelectorAll('.search-event-checkbox').forEach((input) => {
    input.addEventListener('change', () => {
      const selected = Array.from(document.querySelectorAll('.search-event-checkbox:checked'));
      const projectIds = new Set(selected.map((candidate) => candidate.dataset.projectId));
      if (projectIds.size > 1) {
        input.checked = false;
        toast('知识综合只能选择同一项目的 Timeline 事件', 'err');
      }
      $('#search-knowledge-select').classList.toggle(
        'hidden', !document.querySelector('.search-event-checkbox:checked')
      );
    });
  });
}

async function synthesizeSelectedSearchEvents() {
  const selected = Array.from(document.querySelectorAll('.search-event-checkbox:checked'));
  if (!selected.length) { toast('请至少选择一个事件', 'err'); return; }
  const projectIds = new Set(selected.map((input) => input.dataset.projectId));
  if (projectIds.size !== 1) { toast('请选择同一项目的 Timeline 事件', 'err'); return; }
  const project = state.projects.find((candidate) => candidate.project_id === selected[0].dataset.projectId);
  if (!project) { toast('找不到事件所属项目', 'err'); return; }
  await startDirectedKnowledge(project.path, selected.map((input) => input.dataset.eventId));
}

function renderProjectPanorama() {
  var panoramaBody = $('#panorama-body');
  var tasksBody = $('#tasks-body');
  if (!state.panoramaData || !state.panoramaData.sections) {
    // Fallback: v1 project or no panorama, show work map only
    panoramaBody.innerHTML = '';
    tasksBody.classList.remove('hidden');
    renderTasks();
    return;
  }
  // v2: panorama is the primary surface
  tasksBody.classList.add('hidden');
  var items = (state.tasksData && state.tasksData.items) || [];
  var workMapHtml = items.length ? WorkMap.render(items) : '';
  panoramaBody.innerHTML = ProjectPanorama.render(state.panoramaData, workMapHtml);
  panoramaBody.insertAdjacentHTML(
    'afterbegin',
    KnowledgeProposals.renderBanner(state.knowledgeState.proposals, state.knowledgeState.jobs)
  );
  bindPanoramaActions();
  if (state.panoramaData.migration_required) {
    showMigrationPrompt();
  }
}

function renderTasks() {
  var body = $('#tasks-body');
  var items = (state.tasksData && state.tasksData.items) || [];
  if (!items.length) {
    body.innerHTML = '<div class="empty">这个项目还没有工作项。点击「+ 新建工作项」开始。</div>';
    return;
  }
  body.innerHTML = WorkMap.render(items);
  bindWorkMapActions();
}

function findItem(itemId) {
  return ((state.tasksData && state.tasksData.items) || [])
    .find((item) => item.item_id === itemId) || null;
}

function findTask(taskId) {
  for (const item of ((state.tasksData && state.tasksData.items) || [])) {
    const task = (item.tasks || []).find((candidate) => candidate.task_id === taskId);
    if (task) return task;
  }
  return null;
}

function bindWorkMapActions() {
  document.querySelectorAll('.item-group').forEach((group) => {
    const item = findItem(group.dataset.itemId);
    if (!item) return;
    group.querySelector('.item-add-task').addEventListener('click', () => openManualModal('task', item.item_id));
    group.querySelector('.item-edit-btn').addEventListener('click', () => openEditItemModal(item));
    group.querySelector('.item-del-btn').addEventListener('click', () => confirmDeleteItem(item));
  });
  document.querySelectorAll('.task-row').forEach((row) => {
    const task = findTask(row.dataset.taskId);
    if (!task) return;
    row.querySelector('.task-check').addEventListener('change', (event) => {
      taskCompletion.handleToggle(event.currentTarget, row, task);
    });
    row.querySelector('.task-edit-btn').addEventListener('click', () => showTaskEditor(row, task));
    row.querySelector('.task-del-btn').addEventListener('click', () => confirmDeleteTask(row, task));
  });
}

function bindPanoramaActions() {
  var synthesizeBtn = document.querySelector('.panorama-synthesize');
  if (synthesizeBtn) synthesizeBtn.addEventListener('click', openKnowledgeEventModal);
  var migrationPreviewBtn = document.querySelector('.migration-preview-btn');
  if (migrationPreviewBtn) {
    migrationPreviewBtn.addEventListener('click', openMigrationModal);
  }
  document.querySelectorAll('.edit-profile').forEach(function(btn) {
    btn.addEventListener('click', function() { openProfileModal(); });
  });
  document.querySelectorAll('.edit-section').forEach(function(btn) {
    btn.addEventListener('click', function() {
      openSectionModal(btn.dataset.section);
    });
  });
  document.querySelectorAll('.source-section').forEach(function(btn) {
    btn.addEventListener('click', function() {
      var sectionId = btn.dataset.section;
      var sec = (state.panoramaData && state.panoramaData.sections && state.panoramaData.sections[sectionId]) || {};
      var ids = sec.source_event_ids || [];
      if (!ids.length) {
        toast('暂无已记录来源；可从 Timeline 事件生成待审核提案', 'info');
        return;
      }
      toast('来源事件: ' + ids.join(', '), 'info');
    });
  });
  bindWorkMapActions();
}

async function openKnowledgeEventModal() {
  if (!state.currentProject) return;
  const list = $('#knowledge-event-list');
  const error = $('#knowledge-event-error');
  list.innerHTML = '<div class="empty">正在读取 Timeline…</div>';
  error.classList.add('hidden');
  $('#knowledge-event-modal').classList.remove('hidden');
  const result = await wea.listTimeline(state.currentProject.path);
  const events = result && result.ok ? (result.events || []) : [];
  list.innerHTML = events.length ? events.map((event) =>
    '<label class="knowledge-event-row"><input class="knowledge-event-select" type="checkbox" data-event-id="' +
    esc(event.event_id || '') + '" /><span><b>' + esc(event.event_id || '') + '</b> ' +
    esc(event.summary || '') + '</span></label>'
  ).join('') : '<div class="empty">当前项目没有可选事件</div>';
}

function closeKnowledgeEventModal() {
  $('#knowledge-event-modal').classList.add('hidden');
}

async function submitKnowledgeEventSelection() {
  const selected = Array.from(document.querySelectorAll('.knowledge-event-select:checked'))
    .map((input) => input.dataset.eventId);
  if (!selected.length) {
    const error = $('#knowledge-event-error');
    error.textContent = '请至少选择一个事件';
    error.classList.remove('hidden');
    return;
  }
  $('#knowledge-event-submit').disabled = true;
  try {
    await startDirectedKnowledge(state.currentProject.path, selected);
    closeKnowledgeEventModal();
  } finally {
    $('#knowledge-event-submit').disabled = false;
  }
}

async function startDirectedKnowledge(projectPath, eventIds, regenerateOf) {
  toast('知识综合正在生成，完成后会进入待审核知识', 'info');
  const result = await wea.enqueueKnowledge({ projectPath, eventIds, regenerateOf: regenerateOf || '' });
  if (!result || !result.ok) {
    toast('知识综合生成失败：' + ((result && result.error) || '未知错误'), 'err');
    return result;
  }
  await loadKnowledgeState(projectPath);
  switchView('inbox');
  toast('知识综合已生成，请核对证据与差异后确认', 'ok');
  return result;
}

// ---- inbox view ----------------------------------------------------------

async function loadInbox() {
  try {
    const [res, knowledge] = await Promise.all([
      wea.listCaptures(),
      wea.getKnowledgeState(null),
    ]);
    state.inboxCards = (res && res.ok) ? (res.cards || []) : [];
    state.knowledgeState = knowledge && knowledge.ok
      ? knowledge : { jobs: [], proposals: [], runs: [] };
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
    '<div class="inbox-group knowledge-review-group"><h3 class="inbox-group-title">待审核知识</h3>' +
      '<div id="knowledge-review">' + KnowledgeProposals.renderReview(
        state.knowledgeState.proposals, state.knowledgeState.jobs
      ) + '</div></div>',
    renderInboxGroup('Recent archived', groups.archived.slice(0, 20), false),
  ].join('');
  bindInboxActions();
  bindKnowledgeActions();
}

function renderInboxGroup(label, cards, showConfirm) {
  if (!cards.length) return '';
  return '<div class="inbox-group"><h3 class="inbox-group-title">' + label + ' (' + cards.length + ')</h3>' +
    cards.map(function(c) {
      var s = (c.proposal && c.proposal.event) ? c.proposal.event.summary : c.text;
      var icon = c.state === 'processing' ? '\u23F3' : c.state === 'error' ? '\u274C' : c.state === 'archived' ? '\uD83D\uDCC1' : '\u2705';
      var proj = c.selected_project ? esc(c.selected_project.title || '') : '';
      return '<div class="inbox-card"><div class="inbox-card-title">' + icon + ' ' + esc(s || c.text).substring(0, 80) + '</div>' +
        KnowledgeProposals.renderImpactBadge(c.knowledge_impact) +
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
  document.querySelectorAll('.inbox-open').forEach(function(btn) { btn.addEventListener('click', function() { var proj = state.projects.find(function(p) { return p.path === btn.dataset.path; }); if (proj) selectProject(proj); }); });
}

async function loadKnowledgeState(projectPath) {
  try {
    const result = await wea.getKnowledgeState(projectPath || null);
    state.knowledgeState = result && result.ok
      ? result : { jobs: [], proposals: [], runs: [] };
  } catch (_) {
    state.knowledgeState = { jobs: [], proposals: [], runs: [] };
  }
  return state.knowledgeState;
}

function findKnowledgeProposal(proposalId) {
  return (state.knowledgeState.proposals || [])
    .find((proposal) => proposal.proposal_id === proposalId) || null;
}

function bindKnowledgeActions() {
  document.querySelectorAll('.knowledge-confirm').forEach((button) => {
    button.addEventListener('click', () => confirmKnowledgeProposal(button));
  });
  document.querySelectorAll('.knowledge-confirm-document').forEach((button) => {
    button.addEventListener('click', () => confirmKnowledgeDocument(button));
  });
  document.querySelectorAll('.knowledge-reject').forEach((button) => {
    button.addEventListener('click', () => rejectKnowledgeProposal(button));
  });
  document.querySelectorAll('.knowledge-retry').forEach((button) => {
    button.addEventListener('click', () => retryKnowledgeJob(button));
  });
  document.querySelectorAll('.knowledge-regenerate').forEach((button) => {
    button.addEventListener('click', () => regenerateKnowledgeProposal(button));
  });
}

async function confirmKnowledgeProposal(button) {
  let proposal = findKnowledgeProposal(button.dataset.proposalId);
  if (!proposal) return;
  const card = button.closest('.knowledge-card');
  const includedChangeIds = Array.from(card.querySelectorAll('.knowledge-change-select:checked'))
    .map((input) => input.dataset.changeId);
  if (!includedChangeIds.length) { toast('至少保留一个变更；也可以直接拒绝提案', 'err'); return; }
  button.disabled = true;
  if (includedChangeIds.length !== (proposal.changes || []).length) {
    const revised = await wea.reviseKnowledgeProposal({
      proposalId: proposal.proposal_id,
      expectedVersion: proposal.version,
      includedChangeIds,
    });
    if (!revised || !revised.ok) {
      toast('修订提案失败：' + ((revised && revised.error) || '版本冲突'), 'err');
      await loadInbox();
      return;
    }
    proposal = revised.proposal;
  }
  const result = await wea.applyKnowledgeProposal({
    projectPath: proposal.project_path,
    proposalId: proposal.proposal_id,
    expectedVersion: proposal.version,
  });
  if (!result || !result.ok) {
    const stale = result && (result.kind === 'stale' || result.kind === 'apply_conflict');
    toast(stale ? '提案已过期，请刷新后重新生成' : '应用失败：' + ((result && result.error) || ''), 'err');
    await loadInbox();
    return;
  }
  toast(result.kind === 'applied_index_warning' ? '已应用；搜索索引稍后恢复' : '知识提案已应用', 'ok');
  await Promise.all([loadInbox(), refreshCurrent()]);
}

async function confirmKnowledgeDocument(button) {
  const proposal = findKnowledgeProposal(button.dataset.proposalId);
  if (!proposal) return;
  button.disabled = true;
  const result = await wea.applyKnowledgeDocument({
    projectPath: proposal.project_path,
    proposalId: proposal.proposal_id,
    expectedVersion: proposal.version,
  });
  toast(result && result.ok ? '模块文档已创建' : '模块文档未创建：' + ((result && result.error) || ''),
    result && result.ok ? 'ok' : 'err');
  await loadInbox();
}

async function rejectKnowledgeProposal(button) {
  const proposal = findKnowledgeProposal(button.dataset.proposalId);
  if (!proposal) return;
  button.disabled = true;
  const result = await wea.rejectKnowledgeProposal({
    proposalId: proposal.proposal_id,
    expectedVersion: proposal.version,
  });
  toast(result && result.ok ? '提案已拒绝并保留审计记录' : '拒绝失败', result && result.ok ? 'info' : 'err');
  await loadInbox();
}

async function retryKnowledgeJob(button) {
  button.disabled = true;
  const result = await wea.retryKnowledgeJob({
    jobId: button.dataset.jobId,
    expectedVersion: Number(button.dataset.version),
  });
  toast(result && result.ok ? '任务已重试' : '重试失败', result && result.ok ? 'info' : 'err');
  await loadInbox();
}

async function regenerateKnowledgeProposal(button) {
  const proposal = findKnowledgeProposal(button.dataset.proposalId);
  if (!proposal) return;
  button.disabled = true;
  await startDirectedKnowledge(
    proposal.project_path,
    (proposal.source_events || []).map((event) => event.event_id),
    proposal.proposal_id
  );
}

// ---- today summary ---------------------------------------------------------

async function refreshActionSummary() {
  try {
    const [result, knowledge] = await Promise.all([
      wea.listCaptures(),
      wea.getKnowledgeState(null),
    ]);
    state.inboxCards = result && result.ok ? (result.cards || []) : [];
    state.knowledgeState = knowledge && knowledge.ok
      ? knowledge : { jobs: [], proposals: [], runs: [] };
  } catch (_) {
    state.inboxCards = [];
    state.knowledgeState = { jobs: [], proposals: [], runs: [] };
  }
  renderActionSummary();
}

function renderActionSummary() {
  const capturePending = (state.inboxCards || []).filter((card) => card.state === 'needs_confirmation').length;
  const knowledgePending = (state.knowledgeState.proposals || [])
    .filter((proposal) => proposal.state === 'needs_confirmation').length;
  const pending = capturePending + knowledgePending;
  const open = ((state.tasksData && state.tasksData.items) || [])
    .flatMap((item) => item.tasks || [])
    .filter((task) => task.status === 'in_progress').length;
  $('#today-pending-count').textContent = String(pending);
  $('#pending-badge').textContent = String(pending);
  $('#today-open-count').textContent = String(open);
}

function focusTodayRail() {
  const rail = $('#today-rail');
  rail.classList.toggle('open');
  rail.focus({ preventScroll: true });
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
    $('#edit-item-error').textContent = '工作项名称不能为空';
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
    toast('工作项已更新', 'ok');
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
    toast('工作项已重命名', 'ok');
    await refreshCurrent();
  } catch (err) { toast(`重命名出错：${err.message || err}`, 'err'); }
}

function confirmDeleteItem(item) {
  const taskCount = item.tasks ? item.tasks.length : 0;
  const msg = taskCount > 0
    ? `删除「${item.title}」及其下 ${taskCount} 个任务？此操作不可撤销。`
    : `删除空工作项「${item.title}」？`;
  showDeleteConfirm(msg, () => doDeleteItem(item.item_id));
}

async function doDeleteItem(itemId) {
  try {
    const res = await wea.deleteItem(state.currentProject.path, itemId);
    if (!res || !res.ok) { toast(`删除失败：${(res && res.error) || '后端错误'}`, 'err'); return; }
    toast('工作项已删除', 'ok');
    await refreshCurrent();
  } catch (err) { toast(`删除出错：${err.message || err}`, 'err'); }
}

function showTaskEditor(row, task) {
  // Remove any existing editor first
  const existing = row.querySelector('.task-editor');
  if (existing) { existing.remove(); return; }

  const editor = document.createElement('div');
  editor.className = 'task-editor';
  const lifecycleField = task.status === 'done'
    ? `<div class="te-row">
         <label>结论</label>
         <input id="te-lifecycle" value="${esc(task.conclusion || '')}"
           placeholder="记录完成结论…" />
       </div>`
    : `<div class="te-row">
         <label>下一步</label>
         <input id="te-lifecycle" value="${esc(task.next_action || '')}"
           placeholder="下一步要做什么…" />
       </div>`;
  editor.innerHTML =
    `<div class="te-row">
       <label>名称</label>
       <input id="te-title" value="${esc(task.title)}" />
      </div>
      ${lifecycleField}
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
    const lifecycleValue = editor.querySelector('#te-lifecycle').value.trim();
    saveTaskEdits(task, newTitle, lifecycleValue, row);
  });

  editor.addEventListener('click', (e) => e.stopPropagation());
  editor.querySelector('#te-title').focus();
}

async function saveTaskEdits(task, newTitle, lifecycleValue, row) {
  const projectPath = state.currentProject.path;
  const errors = [];
  const lifecycleField = task.status === 'done' ? 'conclusion' : 'next_action';
  const previousValue = task[lifecycleField] || '';

  try {
    if (newTitle && newTitle !== task.title) {
      const result = await wea.updateTask(
        projectPath,
        task.task_id,
        'title',
        newTitle,
      );
      if (!result || !result.ok) {
        errors.push(`名称：${(result && result.error) || '失败'}`);
      }
    }
    if (lifecycleValue !== previousValue) {
      const result = await wea.updateTask(
        projectPath,
        task.task_id,
        lifecycleField,
        lifecycleValue,
      );
      if (!result || !result.ok) {
        errors.push(`${lifecycleField === 'conclusion' ? '结论' : '下一步'}：${
          (result && result.error) || '失败'
        }`);
      }
    }

    if (errors.length) toast(`部分更新失败：${errors.join('；')}`, 'err');
    else toast('已保存', 'ok');
    await refreshCurrent();
  } catch (error) {
    toast(`保存出错：${error.message || error}`, 'err');
    const editor = row.querySelector('.task-editor');
    if (editor) editor.remove();
  }
}

function confirmDeleteTask(row, task) {
  showDeleteConfirm(`删除任务「${task.title}」？时间线归档记录会保留。此操作不可撤销。`, () => doDeleteTask(task.task_id, row));
}

async function doDeleteTask(taskId, row) {
  try {
    const res = await wea.deleteTask(state.currentProject.path, taskId);
    if (!res || !res.ok) { toast(`删除失败：${(res && res.error) || '后端错误'}`, 'err'); return; }
    toast('任务已删除', 'ok');
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
  const input = $('#composer-input');
  const text = input.value.trim();
  if (!text) { setStatus('请输入进展内容', 'error'); return; }
  const pending = state.pending.slice();
  state.busy = true;
  setStatus('正在加入收件箱…', 'loading');
  try {
    const created = await wea.createCapture(text, pending);
    if (!created || !created.ok || !created.card) {
      setStatus(`创建收件箱记录失败：${(created && created.error) || '后端未就绪'}`, 'error');
      return;
    }
    const captureId = created.card.capture_id;
    input.value = '';
    input.style.height = 'auto';
    state.pending = [];
    renderThumbs();
    await wea.discardPending(pending.map((attachment) => attachment.tempPath)).catch(() => {});
    setStatus('已加入收件箱，正在后台解析');
    input.focus();
    await refreshActionSummary();
    if (state.view === 'inbox') renderInbox();
    processMainCaptureInBackground(captureId);
  } catch (error) {
    setStatus(`创建收件箱记录出错：${error.message || error}`, 'error');
  } finally {
    state.busy = false;
  }
}

function processMainCaptureInBackground(captureId) {
  wea.processCapture(captureId).finally(async () => {
    await refreshActionSummary();
    if (state.view === 'inbox') renderInbox();
  });
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

// ---- confirm card (delete confirmation only; archival uses inbox) --------
function hideConfirmCard() {
  $('#confirm-card').classList.add('hidden');
  _deleteCallback = null;
  if (_deleteEscHandler) { document.removeEventListener('keydown', _deleteEscHandler); _deleteEscHandler = null; }
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
  $('#manual-title').textContent = isTask ? '新建任务' : '新建工作项';
  $('#manual-name-label').textContent = isTask ? '任务名称' : '工作项名称';
  $('#manual-name').placeholder = isTask ? '例如：梳理推理链路' : '例如：明确项目需求';
  $('#manual-item-field').classList.toggle('hidden', !isTask);

  if (isTask) {
    const items = (state.tasksData && state.tasksData.items) || [];
    const select = $('#manual-item');
    select.innerHTML = items
      .map((item) => `<option value="${esc(item.item_id)}">${esc(item.title)}</option>`)
      .join('');
    if (!items.length) {
      toast('请先新建一个工作项，再添加任务', 'err');
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
    showManualError(mode === 'task' ? '请填写任务名称' : '请填写工作项名称');
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
    toast(mode === 'task' ? '任务已创建' : '工作项已创建', 'ok');
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

async function openSettingsModal() {
  state.settingsWorkspace = (state.config && state.config.workspace) || '';
  $('#settings-workspace').value = state.settingsWorkspace;
  $('#settings-hotkey').value = (state.config && state.config.hotkey) || 'CommandOrControl+Shift+Space';
  $('#settings-main-hotkey').value = (state.config && state.config.mainHotkey) || 'CommandOrControl+Shift+M';
  $('#settings-model').value = (state.config && state.config.opencodeModel) || '';
  const synthesisSchedule = (state.config && state.config.synthesisSchedule) || {};
  $('#settings-knowledge-daily-enabled').checked = synthesisSchedule.dailyEnabled !== false;
  $('#settings-knowledge-daily-time').value = synthesisSchedule.dailyTime || '23:30';
  $('#settings-knowledge-weekly-enabled').checked = synthesisSchedule.weeklyEnabled !== false;
  $('#settings-knowledge-weekly-day').value = String(
    synthesisSchedule.weeklyDay === undefined ? 5 : synthesisSchedule.weeklyDay
  );
  $('#settings-knowledge-weekly-time').value = synthesisSchedule.weeklyTime || '18:00';
  $('#settings-error').classList.add('hidden');
  $('#settings-modal').classList.remove('hidden');
  bindAcceleratorCapture($('#settings-hotkey'));
  bindAcceleratorCapture($('#settings-main-hotkey'));
  try {
    renderUpdateState(await wea.getUpdateState());
  } catch (err) {
    renderUpdateState({ status: 'error', message: err.message || String(err) });
  }
}

function handleUpdateState(update = {}) {
  renderUpdateState(update);
  if (update.status === 'available') {
    toast(`发现新版本 ${update.version || ''}，可在设置中下载`, 'info');
  } else if (update.status === 'ready') {
    toast(`版本 ${update.version || ''} 已下载，可在设置中重启安装`, 'ok');
  }
}

function formatUpdateBytes(value) {
  const bytes = Number(value || 0);
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function renderUpdateState(update = {}) {
  const status = update.status || 'idle';
  const version = update.currentVersion || '—';
  const statusEl = $('#update-status');
  const progressEl = $('#update-progress');
  const notesEl = $('#update-release-notes');
  const checkButton = $('#update-check');
  const downloadButton = $('#update-download');
  const installButton = $('#update-install');
  if (!statusEl || !progressEl || !notesEl) return;

  $('#settings-app-version').textContent = version;
  const messages = {
    idle: '尚未检查更新',
    development_mode: '开发模式不检查更新；安装后的应用可在这里更新。',
    checking: '正在检查更新…',
    available: `发现新版本 ${update.version || ''}`,
    not_available: '当前已是最新版本。',
    downloading: update.progress
      ? `正在下载 ${update.progress.percent.toFixed(2)}%（${formatUpdateBytes(update.progress.transferred)} / ${formatUpdateBytes(update.progress.total)}）`
      : '正在准备下载…',
    ready: `版本 ${update.version || ''} 已下载，可重启安装。`,
    error: `更新失败：${update.message || '未知错误'}`,
  };
  statusEl.textContent = messages[status] || messages.idle;

  const progress = update.progress || {};
  progressEl.value = Number(progress.percent || 0);
  progressEl.classList.toggle('hidden', status !== 'downloading');

  notesEl.textContent = update.releaseNotes || '';
  notesEl.classList.toggle('hidden', !update.releaseNotes);

  checkButton.classList.toggle('hidden', status === 'available' || status === 'downloading' || status === 'ready');
  checkButton.disabled = status === 'checking' || status === 'development_mode';
  downloadButton.classList.toggle('hidden', status !== 'available');
  downloadButton.disabled = status !== 'available';
  installButton.classList.toggle('hidden', status !== 'ready');
  installButton.disabled = status !== 'ready';
}

async function checkForApplicationUpdate() {
  renderUpdateState({ status: 'checking', currentVersion: $('#settings-app-version').textContent });
  try {
    const result = await wea.checkForUpdates();
    if (result && result.state) renderUpdateState(result.state);
  } catch (err) {
    renderUpdateState({
      status: 'error',
      currentVersion: $('#settings-app-version').textContent,
      message: err.message || String(err),
    });
  }
}

async function downloadApplicationUpdate() {
  try {
    const result = await wea.downloadUpdate();
    if (result && result.state) renderUpdateState(result.state);
  } catch (err) {
    renderUpdateState({
      status: 'error',
      currentVersion: $('#settings-app-version').textContent,
      message: err.message || String(err),
    });
  }
}

async function installApplicationUpdate() {
  try {
    const result = await wea.installUpdate();
    if (result && result.state) renderUpdateState(result.state);
  } catch (err) {
    renderUpdateState({
      status: 'error',
      currentVersion: $('#settings-app-version').textContent,
      message: err.message || String(err),
    });
  }
}

async function pickSettingsWorkspace() {
  const dir = await wea.pickWorkspaceDir();
  if (!dir) return;
  state.settingsWorkspace = dir;
  $('#settings-workspace').value = dir;
}

async function saveSettings() {
  const captureAcceleratorInput = $('#settings-hotkey');
  const mainAcceleratorInput = $('#settings-main-hotkey');
  const hotkey = captureAcceleratorInput.value.trim();
  const mainHotkey = mainAcceleratorInput.value.trim();
  if (!hotkey || !mainHotkey) { showSettingsError('两个快捷键都不能为空'); return; }
  if (hotkey === mainHotkey) { showSettingsError('快速捕获和主窗口不能使用同一个快捷键'); return; }
  const existingSchedule = (state.config && state.config.synthesisSchedule) || {};
  const patch = {
    hotkey,
    mainHotkey,
    opencodeModel: $('#settings-model').value.trim(),
    synthesisSchedule: {
      ...existingSchedule,
      dailyEnabled: $('#settings-knowledge-daily-enabled').checked,
      dailyTime: $('#settings-knowledge-daily-time').value || '23:30',
      weeklyEnabled: $('#settings-knowledge-weekly-enabled').checked,
      weeklyDay: Number($('#settings-knowledge-weekly-day').value),
      weeklyTime: $('#settings-knowledge-weekly-time').value || '18:00',
    },
  };
  if (state.settingsWorkspace) patch.workspace = state.settingsWorkspace;
  $('#settings-save').disabled = true;
  try {
    const updated = await wea.updateConfig(patch);
    state.config = updated;
    if (!updated.hotkeyRegistered || !updated.mainHotkeyRegistered) {
      captureAcceleratorInput.value = updated.hotkey || hotkey;
      mainAcceleratorInput.value = updated.mainHotkey || mainHotkey;
      const kind = updated.hotkeyErrorKind || 'registration_conflict';
      const failed = updated.failedHotkey || 'main';
      showSettingsError(`快捷键注册失败 (${kind}: ${failed})，请换一个组合键`);
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
       <input class="item-title" placeholder="工作项名，如：使用 KV cache 优化 few-shot" />
       <button class="rm-x" title="删除工作项">×</button>
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
  const res = await wea.initProject({
    title, project_id: projectId, items,
    status: $('#init-status').value,
    phase: $('#init-phase').value,
  });
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

// ---- correction modal ---------------------------------------------------

let _correctionEvent = null;

function openCorrectionModal(event) {
  _correctionEvent = event;
  // Populate read-only source info
  const projTitle = (state.currentProject && state.currentProject.title) || (state.currentProject && state.currentProject.project_id) || '';
  $('#corr-original-summary').textContent = event.summary || '';
  $('#corr-source-project').textContent = projTitle;
  $('#corr-source-task').textContent = event.task_title || event.task_id || '';

  // Populate editable fields
  $('#corr-summary').value = event.summary || '';
  $('#corr-status').value = event.status || 'in_progress';
  $('#corr-next-action').value = event.next_action || '';
  $('#corr-reason').value = '';
  $('#corr-error').classList.add('hidden');

  // Load project list into target dropdown
  const projSelect = $('#corr-target-project');
  projSelect.innerHTML = (state.projects || []).map((p) =>
    `<option value="${esc(p.path)}" data-project-id="${esc(p.project_id)}">${esc(p.title || p.project_id)}</option>`
  ).join('');
  // Select current project by default
  if (state.currentProject) {
    const opt = projSelect.querySelector(`option[value="${esc(state.currentProject.path)}"]`);
    if (opt) opt.selected = true;
  }
  // Load tasks for the selected project
  loadCorrectionTargetTasks();

  $('#corr-preview').classList.add('hidden');
  $('#correction-modal').classList.remove('hidden');
}

function closeCorrectionModal() {
  $('#correction-modal').classList.add('hidden');
  _correctionEvent = null;
}

async function loadCorrectionTargetTasks() {
  const projSelect = $('#corr-target-project');
  const selectedPath = projSelect.value;
  if (!selectedPath) return;

  try {
    const res = await wea.listTasks(selectedPath);
    const items = (res && res.ok) ? (res.items || []) : [];

    // Build item→tasks map: prioritize same item_id as the original event
    const itemSelect = $('#corr-target-item');
    const taskSelect = $('#corr-target-task');
    itemSelect.innerHTML = '';
    taskSelect.innerHTML = '';

    // Group tasks by item
    const groups = {};
    items.forEach((item) => {
      const itemId = item.item_id || '';
      itemSelect.innerHTML += `<option value="${esc(itemId)}">${esc(item.title || itemId)}</option>`;
      groups[itemId] = item.tasks || [];
    });

    // Select the same item_id if possible
    if (_correctionEvent && _correctionEvent.item_id) {
      const match = itemSelect.querySelector(`option[value="${esc(_correctionEvent.item_id)}"]`);
      if (match) match.selected = true;
    }
    onCorrectionTargetItemChange();

    // Bind item change to reload tasks
    itemSelect.onchange = onCorrectionTargetItemChange;
  } catch (e) {
    // Ignore — dropdowns stay empty
  }
}

function onCorrectionTargetProjectChange() {
  loadCorrectionTargetTasks();
}

function onCorrectionTargetItemChange() {
  const taskSelect = $('#corr-target-task');
  taskSelect.innerHTML = '';
  const itemId = $('#corr-target-item').value;
  // Re-fetch task list is expensive; store last results
  // For now, rebind from the server
  loadCorrectionTaskOptions(itemId);
}

async function loadCorrectionTaskOptions(itemId) {
  const projSelect = $('#corr-target-project');
  const selectedPath = projSelect.value;
  if (!selectedPath || !itemId) return;

  try {
    const res = await wea.listTasks(selectedPath);
    const items = (res && res.ok) ? (res.items || []) : [];
    const item = items.find((i) => i.item_id === itemId);
    const tasks = item ? (item.tasks || []) : [];
    const taskSelect = $('#corr-target-task');
    taskSelect.innerHTML = tasks.map((t) =>
      `<option value="${esc(t.task_id)}">${esc(t.title || t.task_id)}</option>`
    ).join('');

    // Select same task_id if possible
    if (_correctionEvent && _correctionEvent.task_id) {
      const match = taskSelect.querySelector(`option[value="${esc(_correctionEvent.task_id)}"]`);
      if (match) match.selected = true;
    }
  } catch (e) {
    // Ignore
  }
}

async function submitCorrection() {
  if (!_correctionEvent) return;
  $('#corr-submit').disabled = true;
  $('#corr-error').classList.add('hidden');

  const sourcePath = state.currentProject ? state.currentProject.path : '';
  const targetPath = $('#corr-target-project').value;
  const targetTaskId = $('#corr-target-task').value;
  const summary = $('#corr-summary').value.trim();
  const status = $('#corr-status').value;
  const nextAction = $('#corr-next-action').value.trim();
  const reason = $('#corr-reason').value.trim();

  if (!targetTaskId) {
    $('#corr-error').textContent = '请选择目标任务';
    $('#corr-error').classList.remove('hidden');
    $('#corr-submit').disabled = false;
    return;
  }

  try {
    const request = {
      project_path: sourcePath,
      target_project_path: targetPath !== sourcePath ? targetPath : undefined,
      original_event_id: _correctionEvent.event_id,
      source_task_id: _correctionEvent.task_id,
      source_item_id: _correctionEvent.item_id,
      target_task_id: targetTaskId,
      summary: summary || _correctionEvent.summary,
      status: status || 'in_progress',
      next_action: nextAction,
      reason: reason || '用户手动纠正',
    };

    const res = await wea.correctEvent(request);
    if (!res || !res.ok) {
      $('#corr-error').textContent = `纠正失败：${(res && res.error) || '后端错误'}`;
      $('#corr-error').classList.remove('hidden');
      return;
    }

    closeCorrectionModal();
    toast('纠正已提交', 'ok');
    await refreshCurrent();
  } catch (err) {
    $('#corr-error').textContent = `纠正出错：${err.message || err}`;
    $('#corr-error').classList.remove('hidden');
  } finally {
    $('#corr-submit').disabled = false;
  }
}

// ---- recovery banner ----------------------------------------------------

async function checkPendingCorrections() {
  try {
    const res = await wea.correctionRecoveries();
    if (!res || !res.ok || !res.pending || !res.pending.length) return;
    showRecoveryBanner(res.pending);
  } catch (_) { /* recovery check is best-effort on boot */ }
}

function showRecoveryBanner(pending) {
  const banner = $('#recovery-banner');
  if (!banner) return;

  const count = pending.length;
  const first = pending[0];
  const sourceProj = (first.source_project_path || '').split(/[\\/]/).pop().replace(/\.md$/, '') || '?';
  const targetProj = (first.target_project_path || '').split(/[\\/]/).pop().replace(/\.md$/, '') || '?';

  $('#recovery-text').textContent =
    count === 1
      ? `有 1 条未完成的跨项目纠错：${sourceProj} → ${targetProj}`
      : `有 ${count} 条未完成的跨项目纠错`;

  const actions = $('#recovery-actions');
  pending.forEach((p) => {
    const btn = document.createElement('button');
    btn.className = 'resume-btn';
    btn.textContent = '恢复';
    btn.addEventListener('click', (e) => resumePendingCorrection(p.correction_id, e.currentTarget));
    actions.appendChild(btn);
  });

  banner.classList.remove('hidden');
}

function hideRecoveryBanner() {
  const banner = $('#recovery-banner');
  if (banner) banner.classList.add('hidden');
  const actions = $('#recovery-actions');
  if (actions) actions.innerHTML = '';
}

async function resumePendingCorrection(correctionId, btn) {
  if (btn) { btn.disabled = true; btn.textContent = '恢复中…'; }

  try {
    const res = await wea.resumeCorrection(correctionId);
    if (!res || !res.ok) {
      toast(`恢复失败：${(res && res.error) || '后端错误'}`, 'err');
      if (btn) { btn.disabled = false; btn.textContent = '重试'; }
      return;
    }
    toast('纠错已恢复', 'ok');
    // Re-check: if no more pending, hide banner; otherwise refresh list
    const check = await wea.correctionRecoveries();
    if (!check || !check.ok || !check.pending || !check.pending.length) {
      hideRecoveryBanner();
    } else {
      $('#recovery-actions').innerHTML = '';
      showRecoveryBanner(check.pending);
    }
    if (state.currentProject) await refreshCurrent();
  } catch (err) {
    toast(`恢复出错：${err.message || err}`, 'err');
    if (btn) { btn.disabled = false; btn.textContent = '重试'; }
  }
}

window.addEventListener('DOMContentLoaded', boot);

// ---- panorama migration -------------------------------------------------

function showMigrationPrompt() {
  var body = $('#panorama-body');
  if (!body) return;
  var card = document.createElement('div');
  card.className = 'migration-card';
  card.innerHTML = '<div class="migration-card-content">' +
    '<h3>项目文档可升级</h3>' +
    '<p>当前项目使用旧版格式。升级后可获得项目全景视图和区块编辑功能。</p>' +
    '<button class="migration-preview-btn primary">开始升级</button>' +
    '</div>';
  body.insertBefore(card, body.firstChild);
  bindPanoramaActions();
}

function openMigrationModal() {
  $('#migration-modal').classList.remove('hidden');
  $('#migration-diff').classList.add('hidden');
  $('#migration-apply').classList.add('hidden');
  $('#migration-error').classList.add('hidden');
}

function closeMigrationModal() {
  $('#migration-modal').classList.add('hidden');
}

async function previewMigration() {
  var status = $('#migration-status').value;
  var phase = $('#migration-phase').value;
  if (!state.currentProject) return;
  try {
    var res = await wea.previewProjectMigration(state.currentProject.path, status, phase);
    if (!res || !res.ok) {
      $('#migration-error').textContent = (res && res.error) || '预览失败';
      $('#migration-error').classList.remove('hidden');
      return;
    }
    state.migrationPreview = res.migration;
    $('#migration-diff').textContent = res.migration.diff;
    $('#migration-diff').classList.remove('hidden');
    $('#migration-apply').classList.remove('hidden');
    $('#migration-error').classList.add('hidden');
  } catch (err) {
    $('#migration-error').textContent = err.message || '预览出错';
    $('#migration-error').classList.remove('hidden');
  }
}

async function applyMigration() {
  if (!state.currentProject || !state.migrationPreview) return;
  var btn = $('#migration-apply');
  btn.disabled = true;
  try {
    var res = await wea.applyProjectMigration(
      state.currentProject.path,
      state.migrationPreview.source_hash,
      state.migrationPreview.status,
      state.migrationPreview.phase
    );
    if (!res || !res.ok) {
      var msg = (res && res.error) || '迁移失败';
      if (res && res.kind === 'stale_source') msg = '项目内容已变化，请重新预览';
      $('#migration-error').textContent = msg;
      $('#migration-error').classList.remove('hidden');
      if (res && res.kind === 'stale_source') {
        $('#migration-diff').classList.add('hidden');
        $('#migration-apply').classList.add('hidden');
      }
      btn.disabled = false;
      return;
    }
    closeMigrationModal();
    toast('迁移成功。备份: ' + (res.backup_path || ''), 'ok');
    await refreshCurrent();
  } catch (err) {
    $('#migration-error').textContent = err.message || '迁移出错';
    $('#migration-error').classList.remove('hidden');
  } finally {
    btn.disabled = false;
  }
}

// ---- panorama profile ---------------------------------------------------

function extractProfileField(content, label) {
  if (!content) return '';
  var lines = content.split('\n');
  var inField = false;
  var values = [];
  for (var i = 0; i < lines.length; i++) {
    if (lines[i].startsWith('### ' + label)) { inField = true; continue; }
    if (inField && lines[i].startsWith('### ')) break;
    if (inField) values.push(lines[i]);
  }
  return values.join('\n').trim();
}

function openProfileModal() {
  if (!state.panoramaData) return;
  var profile = state.panoramaData.sections['project-profile'] || {};
  var content = profile.content || '';
  var project = state.panoramaData.project || {};
  $('#profile-status').value = project.status || 'active';
  $('#profile-phase').value = project.phase || 'planning';
  $('#profile-background').value = extractProfileField(content, '背景');
  $('#profile-goal').value = extractProfileField(content, '目标');
  $('#profile-scope').value = extractProfileField(content, '范围');
  $('#profile-criteria').value = extractProfileField(content, '成功标准');
  $('#project-profile-modal').classList.remove('hidden');
  $('#profile-error').classList.add('hidden');
}

function closeProfileModal() {
  $('#project-profile-modal').classList.add('hidden');
}

async function saveProfile() {
  if (!state.currentProject || !state.panoramaData) return;
  var btn = $('#profile-save');
  btn.disabled = true;
  var profile = state.panoramaData.sections['project-profile'] || {};
  var project = state.panoramaData.project || {};
  try {
    var res = await wea.updateProjectProfile({
      projectPath: state.currentProject.path,
      baseSectionHash: profile.hash || '',
      baseMetadataHash: project.metadata_hash || '',
      status: $('#profile-status').value,
      phase: $('#profile-phase').value,
      background: $('#profile-background').value.trim(),
      goal: $('#profile-goal').value.trim(),
      scope: $('#profile-scope').value.trim(),
      successCriteria: $('#profile-criteria').value.trim(),
    });
    if (!res || !res.ok) {
      var kind = (res && res.kind) || '';
      var msg = '保存失败';
      if (kind === 'stale_section') msg = '项目档案已变化，请基于最新版本重新编辑';
      else if (kind === 'stale_metadata') msg = '项目元数据已变化，请基于最新版本重新编辑';
      else if (res && res.error) msg = res.error;
      $('#profile-error').textContent = msg;
      $('#profile-error').classList.remove('hidden');
      if (kind === 'stale_section' || kind === 'stale_metadata') {
        closeProfileModal();
        await refreshCurrent();
      }
      return;
    }
    closeProfileModal();
    await refreshCurrent();
  } catch (err) {
    $('#profile-error').textContent = err.message || '保存出错';
    $('#profile-error').classList.remove('hidden');
  } finally {
    btn.disabled = false;
  }
}

// ---- panorama section edit ----------------------------------------------

var _currentSectionId = '';

function openSectionModal(sectionId) {
  if (!state.panoramaData) return;
  var sec = state.panoramaData.sections[sectionId];
  if (!sec) return;
  _currentSectionId = sectionId;
  $('#section-modal-title').textContent = sec.title || sectionId;
  $('#section-modal-content').value = sec.content || '';
  $('#project-section-modal').classList.remove('hidden');
  $('#section-error').classList.add('hidden');
  $('#section-modal-content').focus();
}

function closeSectionModal() {
  $('#project-section-modal').classList.add('hidden');
  _currentSectionId = '';
}

async function saveSection() {
  if (!state.currentProject || !_currentSectionId) return;
  var btn = $('#section-save');
  btn.disabled = true;
  var sec = state.panoramaData.sections[_currentSectionId];
  if (!sec) { btn.disabled = false; return; }
  try {
    var res = await wea.updateProjectSection(
      _currentSectionId,
      sec.hash || '',
      $('#section-modal-content').value,
      state.currentProject.path
    );
    if (!res || !res.ok) {
      var kind = (res && res.kind) || '';
      var msg = '保存失败';
      if (kind === 'stale_section') msg = '内容已变化，请基于最新版本重新编辑';
      else if (kind === 'invalid_operation') msg = '该区块不能通过此方式编辑';
      else if (res && res.error) msg = res.error;
      $('#section-error').textContent = msg;
      $('#section-error').classList.remove('hidden');
      if (kind === 'stale_section') {
        closeSectionModal();
        await refreshCurrent();
      }
      return;
    }
    closeSectionModal();
    await refreshCurrent();
  } catch (err) {
    $('#section-error').textContent = err.message || '保存出错';
    $('#section-error').classList.remove('hidden');
  } finally {
    btn.disabled = false;
  }
}
