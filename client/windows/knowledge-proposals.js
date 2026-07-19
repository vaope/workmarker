// Pure renderer for durable knowledge jobs and proposals. No IPC or DOM mutation.
(function () {
  'use strict';

  var ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  function esc(value) {
    if (value === null || value === undefined) return '';
    return String(value).replace(/[&<>"']/g, function (character) { return ESC[character]; });
  }

  var STATE_LABELS = {
    needs_confirmation: '待确认', applying: '应用中', applied: '已应用',
    rejected: '已拒绝', stale: '已过期', superseded: '已修订',
    queued: '排队中', processing: '生成中', failed: '失败',
    completed: '已生成', skipped_no_evidence: '无证据', skipped_no_change: '无变化',
  };
  var TRIGGER_LABELS = {
    directed: '定向事件', high_impact: '高影响捕获', daily: '每日综合', weekly: '每周综合',
  };
  var DIMENSION_LABELS = {
    goal: '目标', scope: '范围', architecture: '架构', risk: '风险', milestone: '里程碑',
  };

  function evidenceHtml(sourceEvents) {
    if (!sourceEvents || !sourceEvents.length) return '<span class="knowledge-empty">无来源事件</span>';
    return sourceEvents.map(function (event) {
      return '<span class="evidence-chip"><b>' + esc(event.event_id) + '</b> ' +
        esc(event.summary || '') + '</span>';
    }).join('');
  }

  function stateBadge(state) {
    return '<span class="knowledge-state state-' + esc(state) + '">' +
      esc(STATE_LABELS[state] || state) + '</span>';
  }

  function renderChange(change, selectable) {
    var changeId = esc(change.change_id || '');
    var selector = selectable
      ? '<input class="knowledge-change-select" type="checkbox" checked data-change-id="' + changeId + '" />'
      : '';
    return '<section class="knowledge-change" data-change-id="' + changeId + '">' +
      '<div class="knowledge-change-head"><label>' + selector +
      '<strong>' + esc(change.target_section || '') + '</strong></label>' +
      '<span>' + esc(change.reason || '') + '</span></div>' +
      '<details><summary>变更前 / 变更后</summary>' +
      '<div class="knowledge-before-after"><pre>' + esc(change.before || '') + '</pre>' +
      '<pre>' + esc(change.after || '') + '</pre></div></details>' +
      '<pre class="knowledge-diff">' + esc(change.diff || '') + '</pre>' +
      '</section>';
  }

  function renderSectionProposal(proposal) {
    var pending = proposal.state === 'needs_confirmation';
    var controls = '';
    if (pending) {
      controls = '<div class="knowledge-actions">' +
        '<button class="primary small knowledge-confirm" data-proposal-id="' + esc(proposal.proposal_id) +
        '" data-version="' + esc(proposal.version) + '">确认整包应用</button>' +
        '<button class="ghost small knowledge-reject" data-proposal-id="' + esc(proposal.proposal_id) +
        '" data-version="' + esc(proposal.version) + '">拒绝</button></div>';
    } else if (proposal.state === 'stale') {
      controls = '<div class="knowledge-actions"><button class="ghost small knowledge-regenerate" data-proposal-id="' +
        esc(proposal.proposal_id) + '">重新生成</button></div>';
    }
    return '<article class="knowledge-card state-' + esc(proposal.state) + '" data-proposal-id="' +
      esc(proposal.proposal_id) + '" data-version="' + esc(proposal.version) + '">' +
      '<header><div><strong>知识综合</strong><span class="knowledge-trigger">' +
      esc(TRIGGER_LABELS[proposal.trigger] || proposal.trigger) + '</span></div>' +
      stateBadge(proposal.state) + '</header>' +
      '<div class="knowledge-evidence"><span>来源证据</span>' + evidenceHtml(proposal.source_events) + '</div>' +
      (proposal.changes || []).map(function (change) { return renderChange(change, pending); }).join('') +
      controls + '</article>';
  }

  function renderDocumentProposal(proposal) {
    var pending = proposal.state === 'needs_confirmation';
    var controls = pending
      ? '<div class="knowledge-actions"><button class="primary small knowledge-confirm-document" data-proposal-id="' +
        esc(proposal.proposal_id) + '" data-version="' + esc(proposal.version) +
        '">单独确认创建模块</button><button class="ghost small knowledge-reject" data-proposal-id="' +
        esc(proposal.proposal_id) + '" data-version="' + esc(proposal.version) + '">拒绝</button></div>'
      : '';
    return '<article class="knowledge-card knowledge-document state-' + esc(proposal.state) +
      '" data-proposal-id="' + esc(proposal.proposal_id) + '">' +
      '<header><div><strong>可选模块文档</strong><span class="knowledge-trigger">' +
      esc(TRIGGER_LABELS[proposal.trigger] || proposal.trigger) + '</span></div>' +
      stateBadge(proposal.state) + '</header>' +
      '<dl><dt>文件名</dt><dd>' + esc(proposal.filename) + '</dd>' +
      '<dt>用途</dt><dd>' + esc(proposal.purpose) + '</dd>' +
      '<dt>主文档保留摘要</dt><dd>' + esc(proposal.retained_summary) + '</dd></dl>' +
      '<div class="knowledge-evidence"><span>来源证据</span>' + evidenceHtml(proposal.source_events) + '</div>' +
      '<details><summary>模块结论与正文预览</summary><pre class="knowledge-document-preview">' +
      esc(proposal.preview || '') + '</pre></details>' + controls + '</article>';
  }

  function renderJob(job) {
    var retry = job.state === 'failed'
      ? '<button class="ghost small knowledge-retry" data-job-id="' + esc(job.job_id) +
        '" data-version="' + esc(job.version) + '">重试</button>'
      : '';
    return '<article class="knowledge-card knowledge-job state-' + esc(job.state) + '">' +
      '<header><div><strong>综合任务</strong><span class="knowledge-trigger">' +
      esc(TRIGGER_LABELS[job.trigger] || job.trigger) + '</span></div>' + stateBadge(job.state) + '</header>' +
      (job.last_error ? '<p class="knowledge-error">' + esc(job.last_error) + '</p>' : '') +
      (retry ? '<div class="knowledge-actions">' + retry + '</div>' : '') + '</article>';
  }

  function renderReview(proposals, jobs) {
    var proposalCards = (proposals || []).map(function (proposal) {
      return proposal.proposal_kind === 'module_document'
        ? renderDocumentProposal(proposal) : renderSectionProposal(proposal);
    });
    var activeJobs = (jobs || []).filter(function (job) {
      return ['queued', 'processing', 'failed'].indexOf(job.state) >= 0;
    }).map(renderJob);
    if (!proposalCards.length && !activeJobs.length) {
      return '<div class="knowledge-empty">暂无知识综合任务或提案</div>';
    }
    return activeJobs.concat(proposalCards).join('');
  }

  function renderBanner(proposals, jobs) {
    var pending = (proposals || []).filter(function (proposal) {
      return proposal.state === 'needs_confirmation';
    }).length;
    var failed = (jobs || []).filter(function (job) { return job.state === 'failed'; }).length;
    return '<div class="knowledge-banner"><strong>待审核知识 ' + pending + '</strong>' +
      (failed ? '<span class="knowledge-failed">失败 ' + failed + '</span>' : '') + '</div>';
  }

  function renderImpactBadge(impact) {
    if (!impact || impact.level !== 'high') return '';
    var dimensions = (impact.dimensions || []).map(function (dimension) {
      return '<span>' + esc(DIMENSION_LABELS[dimension] || dimension) + '</span>';
    }).join('');
    return '<div class="impact-badge impact-high"><strong>高影响</strong>' + dimensions +
      '<small>' + esc(impact.reason || '') + '</small></div>';
  }

  globalThis.KnowledgeProposals = Object.freeze({
    esc: esc,
    renderReview: renderReview,
    renderBanner: renderBanner,
    renderImpactBadge: renderImpactBadge,
  });
})();
