// capture.js (renderer) — quick-capture floating window logic.
const $ = (s) => document.querySelector(s);

const state = { config: null, projects: [], pending: [], proposal: null, busy: false };

async function boot() {
  state.config = await wea.getConfig();
  bind();
  await loadProjects();
  wea.onShowCapture(() => reset());
  wea.onArchived(() => {/* keep recent line; nothing else */});
  reset();
}

async function loadProjects() {
  const res = await wea.listProjects();
  const sel = $('#cap-project');
  const prev = sel.value;
  sel.innerHTML = '';
  if (res && res.ok && res.projects && res.projects.length) {
    state.projects = res.projects;
    res.projects.forEach((p) => {
      const o = document.createElement('option');
      o.value = p.path; o.textContent = p.title || p.project_id;
      sel.appendChild(o);
    });
    if (prev) sel.value = prev;
  } else {
    state.projects = [];
    const o = document.createElement('option');
    o.value = ''; o.textContent = '（无项目，请先在主窗口创建）';
    sel.appendChild(o);
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
  $('#cap-input').value = '';
  $('#cap-confirm').classList.add('hidden');
  $('#cap-input-area').classList.remove('hidden');
  $('.cap-foot') && $('.cap-foot').classList.remove('hidden');
  state.pending = [];
  state.proposal = null;
  renderThumbs();
  setStatus('');
  loadProjects();
  setTimeout(() => $('#cap-input').focus(), 30);
  resize();
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
  const projectPath = $('#cap-project').value;
  if (!text) { setStatus('请输入进展内容', 'error'); return; }
  if (!projectPath) { setStatus('请先在主窗口创建项目', 'error'); return; }
  state.busy = true;
  setStatus('正在解析…调用 opencode（约 10 秒）', 'loading');
  try {
    const res = await wea.propose(text, projectPath, state.pending.map((p) => p.tempPath));
    if (!res || !res.ok) { setStatus(`解析失败：${(res && res.error) || '后端未就绪'}`, 'error'); return; }
    state.proposal = res.proposal;
    setStatus('');
    renderConfirm(res.proposal, !!res.low_confidence, projectPath);
  } catch (err) {
    setStatus(`出错：${err.message || err}`, 'error');
  } finally {
    state.busy = false;
  }
}

function renderConfirm(proposal, lowConf, projectPath) {
  $('#cap-input-area').classList.add('hidden');
  const card = $('#cap-confirm');
  const e = proposal.event;
  const t = proposal.target;
  const conf = Math.round((proposal.confidence || 0) * 100);
  const proj = state.projects.find((p) => p.path === projectPath);
  const projTitle = (proj && proj.title) || t.project_id;
  const taskLabel = t.new_task ? `${esc(t.task_title)}（新建）` : esc(t.task_id);

  card.innerHTML =
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
       ${state.pending.length ? `<span class="k">附件</span><span class="v">${state.pending.map((p) => esc(p.filename)).join(', ')}</span>` : ''}
     </div>
     <div class="ccc-actions">
       <button class="ghost" id="ccc-cancel">取消</button>
       <button class="primary" id="ccc-confirm">确认归档</button>
     </div>`;
  card.classList.remove('hidden');
  $('#ccc-cancel').addEventListener('click', () => { $('#cap-confirm').classList.add('hidden'); $('#cap-input-area').classList.remove('hidden'); resize(); });
  $('#ccc-confirm').addEventListener('click', () => commit(projectPath));
  resize();
}

async function commit(projectPath) {
  if (!state.proposal) return;
  const p = JSON.parse(JSON.stringify(state.proposal));
  p.event.status = $('#ccc-status').value;
  p.event.summary = $('#ccc-summary').value;
  p.event.next_action = $('#ccc-next').value;
  setStatus('正在写入…', 'loading');
  try {
    const res = await wea.commit(p, projectPath, state.pending);
    if (!res || !res.ok) { setStatus(`写入失败：${(res && res.error) || '未知错误'}`, 'error'); return; }
    state.pending = [];
    const proj = state.projects.find((x) => x.path === projectPath);
    showRecent(`${(proj && proj.title) || ''} · ${p.event.summary}`);
    setStatus('✅ 已归档，窗口即将关闭', '');
    setTimeout(() => wea.hideCapture(), 1100);
  } catch (err) {
    setStatus(`出错：${err.message || err}`, 'error');
  }
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
  // ask main process to fit window height to content
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
