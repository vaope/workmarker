// capture.js (renderer) — quick-capture floating window logic.
// Inbox model: multiple in-flight cards; state.proposal is gone.
const $ = (s) => document.querySelector(s);

const state = {
  config: null,
  projects: [],
  pending: [],
  cards: [],           // active inbox cards (needs_confirmation, processing, error)
  selectedCardId: '',  // which card's confirm view is showing
  busy: false,
  phase: 'input',      // 'input' | 'confirm' | 'processing' | 'error' — drives visibility/layout only
  lastText: '',
};

async function boot() {
  state.config = await wea.getConfig();
  bind();
  await loadProjects();
  wea.onShowCapture(() => {
    // Non-input phases carry user-visible state and must survive hide/show.
    if (state.phase !== 'input') {
      reloadCards();
      return;
    }
    reset();
  });
  wea.onArchived(() => reloadCards());
  wea.onInboxUpdated(() => reloadCards());
  reset();
}

async function loadProjects() {
  const res = await wea.listProjects();
  if (res && res.ok && res.projects && res.projects.length) {
    state.projects = res.projects;
  } else {
    state.projects = [];
  }
}

function bind() {
  const input = $('#cap-input');
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.ctrlKey && !e.shiftKey) { e.preventDefault(); submit(); }
    if (e.key === 'Escape') wea.hideCapture();
  });
  input.addEventListener('paste', handlePaste);
  $('#cap-submit').addEventListener('click', submit);
  $('#cap-cancel').addEventListener('click', () => wea.hideCapture());
}

function reset() {
  setCaptureState('input', { clearInput: true, clearAttachments: true, status: '' });
  loadProjects();
  reloadCards();
  setTimeout(() => $('#cap-input').focus(), 30);
  resize();
}

async function reloadCards() {
  const res = await wea.listCaptures();
  state.cards = (res && res.ok && res.cards) ? res.cards.filter(
    (c) => c.state === 'needs_confirmation' || c.state === 'processing' || c.state === 'error'
  ) : [];
  renderCardList();
  // If we're in confirm view and the selected card is gone, go back to input
  if (state.selectedCardId && !state.cards.find((c) => c.capture_id === state.selectedCardId)) {
    state.selectedCardId = '';
    if (state.phase === 'confirm') setCaptureState('input', { status: '' });
  }
}

function setCaptureState(phase, data = {}) {
  // Visibility/layout only — never store proposal data here.
  state.phase = phase;
  $('#cap-input-area').classList.remove('hidden');
  $('.cap-foot') && $('.cap-foot').classList.remove('hidden');

  if (phase === 'input') {
    $('#cap-confirm').classList.add('hidden');
    if (data.clearInput) $('#cap-input').value = '';
    if (data.clearAttachments) state.pending = [];
    state.selectedCardId = '';
    state.busy = false;
    state.lastText = '';
    $('#cap-submit').disabled = false;
    $('#cap-submit').textContent = '提交';
    renderThumbs();
    setStatus(data.status || '');
    resize();
    return;
  }

  if (phase === 'processing') {
    state.busy = true;
    state.lastText = data.text || '';
    $('#cap-input').value = '';
    $('#cap-submit').disabled = true;
    $('#cap-submit').textContent = '⏳';
    setStatus('正在后台解析…', 'loading');
    renderProcessing(data.text || '');
    return;
  }

  if (phase === 'confirm') {
    state.busy = false;
    state.selectedCardId = data.cardId || '';
    $('#cap-submit').disabled = true;
    $('#cap-submit').textContent = '确认中';
    setStatus(data.status || '');
    renderCardConfirm(data.card);
    return;
  }

  if (phase === 'error') {
    state.busy = false;
    $('#cap-submit').disabled = false;
    $('#cap-submit').textContent = '提交';
    setStatus('');
    renderError(data.message || '未知错误', data.kind || '', data.card);
  }
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
  const wrap = $('#cap-thumbs');
  if (!state.pending.length) { wrap.classList.add('hidden'); wrap.innerHTML = ''; resize(); return; }
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
  resize();
}

async function submit() {
  if (state.busy) return;
  const text = $('#cap-input').value.trim();
  if (!text) { setStatus('请输入进展内容', 'error'); return; }
  if (!state.projects.length) await loadProjects();
  if (!state.projects.length) { setStatus('请先在主窗口创建项目', 'error'); return; }

  setCaptureState('processing', { text });

  // Create inbox card with processing state
  const created = await wea.createCapture(text, state.pending.map((p) => ({ tempPath: p.tempPath || '', filename: p.filename || '' })));
  if (!created || !created.ok) {
    setCaptureState('error', { message: (created && created.error) || '创建失败', kind: (created && created.kind) || 'create_error' });
    return;
  }

  // Trigger backend processing (routing + archivist)
  const processed = await wea.processCapture(created.card.capture_id);
  if (!processed || !processed.ok) {
    setCaptureState('error', {
      message: (processed && processed.error) || '解析失败',
      kind: (processed && processed.kind) || 'process_error',
      card: created.card,
    });
    return;
  }

  // Success: clear input and pending, show card list
  state.pending = [];
  renderThumbs();
  $('#cap-input').value = '';
  setStatus('✅ 已提交，可以继续输入下一条', '');
  state.busy = false;
  $('#cap-submit').disabled = false;
  $('#cap-submit').textContent = '提交';
  await reloadCards();
  resize();
}

function renderProcessing(text) {
  const card = $('#cap-confirm');
  card.innerHTML =
    `<div class="ccc-head"><h3>正在解析</h3>
       <span class="ccc-conf low">opencode</span></div>
     <div class="cap-processing">
       <div class="processing-spinner"></div>
       <div>
         <div class="processing-title">正在判断项目并生成归档预览…</div>
         <div class="processing-text">${esc(text)}</div>
       </div>
     </div>`;
  card.classList.remove('hidden');
  resize();
}

function renderError(msg, kind, card) {
  const hint = kind === 'no_project'
    ? '请先在主窗口中创建或打开一个项目。'
    : kind === 'no_workspace'
    ? '请先在主窗口设置中配置项目库目录。'
    : '请检查后端是否正常运行，然后重试。';

  const confEl = $('#cap-confirm');
  confEl.innerHTML =
    `<div class="ccc-head"><h3>❌ 解析失败</h3></div>
     <div class="ccc-warn">${esc(msg)}</div>
     <div class="ccc-grid"><span class="v" style="color:var(--text-dim);font-size:13px;">${hint}</span></div>
     <div class="ccc-actions">
       <button class="ghost" id="ccc-retry">🔁 重试</button>
     </div>`;
  confEl.classList.remove('hidden');
  $('#ccc-retry').addEventListener('click', () => {
    const retryText = state.lastText || '';
    setCaptureState('input', { status: '' });
    $('#cap-input').value = retryText;
    setTimeout(() => $('#cap-input').focus(), 30);
  });
  resize();
}

// ---- card list (compact cards below input) ----

function renderCardList() {
  const wrap = $('#cap-card-list');
  if (!wrap) return;
  if (!state.cards.length) { wrap.innerHTML = ''; resize(); return; }
  wrap.innerHTML = state.cards.map((c) => {
    const statusIcon = c.state === 'processing' ? '⏳' : c.state === 'error' ? '❌' : '✅';
    const summary = c.proposal && c.proposal.event ? c.proposal.event.summary : c.text;
    return `<div class="inbox-card" id="card-${esc(c.capture_id)}">
      <span>${statusIcon} ${esc(summary || c.text).substring(0, 60)}</span>
      <div class="inbox-actions">
        ${c.state === 'needs_confirmation' ? `<button class="primary small-btn card-confirm" data-id="${esc(c.capture_id)}">确认</button>` : ''}
        ${c.state === 'error' ? `<button class="ghost small-btn card-retry" data-id="${esc(c.capture_id)}">重试</button>` : ''}
        <button class="ghost small-btn card-cancel" data-id="${esc(c.capture_id)}">取消</button>
      </div>
    </div>`;
  }).join('');
  resize();

  // Bind actions
  wrap.querySelectorAll('.card-confirm').forEach((btn) => {
    btn.addEventListener('click', () => openCardConfirm(btn.dataset.id));
  });
  wrap.querySelectorAll('.card-retry').forEach((btn) => {
    btn.addEventListener('click', async () => {
      btn.textContent = '重试中…';
      btn.disabled = true;
      await wea.processCapture(btn.dataset.id);
      reloadCards();
    });
  });
  wrap.querySelectorAll('.card-cancel').forEach((btn) => {
    btn.addEventListener('click', async () => {
      btn.textContent = '取消中…';
      btn.disabled = true;
      await wea.cancelCapture(btn.dataset.id);
      reloadCards();
    });
  });
}

function openCardConfirm(captureId) {
  const card = state.cards.find((c) => c.capture_id === captureId);
  if (!card) return;
  setCaptureState('confirm', { card, cardId: captureId, status: '' });
}

// ---- card confirm view (reads from card, not state.proposal) ----

function renderCardConfirm(card) {
  if (!card) return;
  const proposal = card.proposal;
  const selectedProject = card.selected_project;
  if (!selectedProject || !selectedProject.path) {
    setCaptureState('error', { message: '项目信息不完整，请取消后重新提交', kind: 'incomplete_project', card });
    return;
  }
  const confEl = $('#cap-confirm');
  const e = proposal.event;
  const t = proposal.target;
  const conf = Math.round((proposal.confidence || 0) * 100);
  const projTitle = (selectedProject && selectedProject.title) || t.project_id;
  const taskLabel = t.new_task ? `${esc(t.task_title)}（新建）` : esc(t.task_id);
  const lowConf = !!card.low_confidence;

  confEl.innerHTML =
    `<div class="ccc-head"><h3>归档预览</h3>
       <span class="ccc-conf ${lowConf ? 'low' : 'high'}">置信度 ${conf}%</span></div>` +
    (lowConf ? '<div class="ccc-warn">不太确定，请检查后确认。</div>' : '') +
    `<div class="ccc-grid">
       <span class="k">项目</span><span class="v">${esc(projTitle)}</span>
       <span class="k">任务</span><span class="v">${taskLabel}</span>
       <span class="k">状态</span>
       <select id="ccc-status">
         <option value="in_progress" ${e.status === 'in_progress' ? 'selected' : ''}>进行中</option>
         <option value="done" ${e.status === 'done' ? 'selected' : ''}>已完成</option>
       </select>
       <span class="k">摘要</span><input id="ccc-summary" value="${esc(e.summary)}" />
       <span class="k">下一步</span><input id="ccc-next" value="${esc(e.next_action)}" />
     </div>
     <div class="ccc-actions">
       <button class="ghost" id="ccc-cancel">取消</button>
       <button class="primary" id="ccc-confirm">确认归档</button>
     </div>`;
  confEl.classList.remove('hidden');
  $('#ccc-cancel').addEventListener('click', async () => {
    await wea.cancelCapture(card.capture_id);
    setCaptureState('input', { status: '' });
    reloadCards();
  });
  $('#ccc-confirm').addEventListener('click', () => commitCard(card.capture_id));
  resize();
}

async function commitCard(captureId) {
  const edits = {
    summary: $('#ccc-summary').value,
    status: $('#ccc-status').value,
    next_action: $('#ccc-next').value,
  };
  setStatus('正在写入…', 'loading');
  try {
    const res = await wea.commitCapture(captureId, edits);
    if (!res || !res.ok) {
      setCaptureState('error', { message: `写入失败：${(res && res.error) || '未知错误'}`, kind: (res && res.kind) || 'commit_error' });
      return;
    }
    showRecent('✅ 已归档');
    restoreInputAfterArchive();
  } catch (err) {
    setCaptureState('error', { message: `出错：${err.message || err}`, kind: 'commit_crash' });
  }
}

function restoreInputAfterArchive() {
  setCaptureState('input', { clearAttachments: true, status: '✅ 已归档，可以继续输入下一条' });
  reloadCards();
  setTimeout(() => $('#cap-input').focus(), 30);
}

function showRecent(text) {
  $('#cap-recent').textContent = `上次：刚刚 → ${text}`;
}

function setStatus(msg, kind) {
  const el = $('#cap-status');
  el.textContent = msg || '';
  el.className = 'cap-status' + (kind ? ' ' + kind : '');
}

function resize() {
  requestAnimationFrame(() => {
    const h = document.querySelector('.cap').scrollHeight + 28;
    wea.resizeCapture(h);
  });
}

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

window.addEventListener('DOMContentLoaded', boot);
