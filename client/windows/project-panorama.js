// project-panorama.js — pure rendering module for project panorama data.
// No IPC, no DOM mutation, no application state. Only escape + render.
(function () {
  'use strict';

  // ── HTML escaping ──────────────────────────────────────────────

  /** Entity-map for basic HTML escaping. */
  const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  function esc(s) {
    if (typeof s !== 'string') return '';
    return s.replace(/[&<>"']/g, function (c) { return ESC[c]; });
  }

  /** Simple Markdown-to-safe-HTML: only covers inline code, bold, and newline→br. */
  function safeMarkdown(s) {
    if (typeof s !== 'string') return '';
    var escaped = esc(s);
    escaped = escaped.replace(/`([^`]+)`/g, '<code>$1</code>');
    escaped = escaped.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    return escaped.replace(/\n/g, '<br>');
  }

  // ── Ownership badges ───────────────────────────────────────────

  var OWNERSHIP_BADGES = {
    'reviewed': '<span class="ownership-badge badge-reviewed">需审阅</span>',
    'derived-reviewed': '<span class="ownership-badge badge-derived">派生 · 需审阅</span>',
    'structured': '<span class="ownership-badge badge-structured">结构化</span>',
    'append-only': '<span class="ownership-badge badge-append">只追加</span>',
    'derived': '<span class="ownership-badge badge-derived">派生</span>',
  };

  // ── Section order (declarative) ────────────────────────────────

  var SECTION_ORDER = [
    'project-profile',
    'current-panorama',
    'work-map', // slot — filled by caller
    'technical-overview',
    'project-knowledge',
    'decisions',
    'attachments',
    'timeline',
    'rollups',
  ];

  var EDITABLE_SECTIONS = {
    'project-profile': true,
    'technical-overview': true,
    'project-knowledge': true,
  };

  /** Sections that should render inside <details> (collapsed by default). */
  var COLLAPSED_SECTIONS = {
    'timeline': true,
    'rollups': true,
  };

  // ── Rendering ──────────────────────────────────────────────────

  /**
   * Render a single reviewed/append-only section card.
   * @param {string} sectionId
   * @param {object} sec  {title, ownership, content, source_event_ids}
   * @param {string} workMapHtml  prerendered Work Map, only used for the slot
   * @returns {string} HTML
   */
  function renderSection(sectionId, sec, workMapHtml) {
    var isEmpty = !sec.content || sec.content.trim() === '';

    // Work Map slot — inject caller's HTML directly
    if (sectionId === 'work-map') {
      return (
        '<section class="panorama-section panorama-workmap" data-section-id="work-map">' +
        '<div class="section-header">' +
        '<h2 class="section-title">' + esc(sec.title) + '</h2>' +
        OWNERSHIP_BADGES['structured'] +
        '</div>' +
        '<div class="section-body">' + (workMapHtml || '') + '</div>' +
        '</section>'
      );
    }

    // Collapsed sections
    var isCollapsed = COLLAPSED_SECTIONS.hasOwnProperty(sectionId);
    var badge = OWNERSHIP_BADGES[sec.ownership] || '';

    // Editable?
    var editBtn = '';
    if (EDITABLE_SECTIONS.hasOwnProperty(sectionId)) {
      if (sectionId === 'project-profile') {
        editBtn = '<button class="edit-profile" data-section="' + esc(sectionId) + '" title="编辑项目档案">✎</button>';
      } else {
        editBtn = '<button class="edit-section" data-section="' + esc(sectionId) + '" title="编辑">✎</button>';
      }
    }

    // Source events button
    var sourceBtn = '';
    if (sec.source_event_ids && sec.source_event_ids.length > 0) {
      sourceBtn = '<button class="source-section" data-section="' + esc(sectionId) + '" title="查看来源事件">源</button>';
    } else if (sec.ownership === 'reviewed' || sec.ownership === 'derived-reviewed') {
      // Phase A: no source events yet
      sourceBtn = '<button class="source-section" data-section="' + esc(sectionId) + '" title="来源" disabled>暂无来源</button>';
    }

    var header =
      '<div class="section-header">' +
      '<h2 class="section-title">' + esc(sec.title) + '</h2>' +
      badge +
      editBtn +
      sourceBtn +
      '</div>';

    var body;
    if (isEmpty) {
      body = '<div class="section-body section-empty">尚未填写</div>';
    } else {
      body = '<div class="section-body">' + safeMarkdown(sec.content) + '</div>';
    }

    if (isCollapsed) {
      return (
        '<details class="panorama-section panorama-collapsed" data-section-id="' + esc(sectionId) + '">' +
        '<summary class="section-summary">' + esc(sec.title) + badge + '</summary>' +
        body +
        '</details>'
      );
    }

    return (
      '<section class="panorama-section" data-section-id="' + esc(sectionId) + '">' +
      header +
      body +
      '</section>'
    );
  }

  /**
   * Render the full project panorama as an HTML string.
   * @param {object} data  {project:{project_id,title,status,phase,...}, sections:{...}}
   * @param {string} workMapHtml  pre-rendered Work Map HTML (already escaped)
   * @returns {string} HTML
   */
  function render(data, workMapHtml) {
    if (!data || !data.sections) return '';

    var project = data.project || {};
    var sections = data.sections || {};
    var fragments = [];

    fragments.push(
      '<header class="panorama-header">' +
      '<h1 class="panorama-project-title">' + esc(project.title) + '</h1>' +
      '<div class="panorama-metadata">' +
      '<span class="meta-status">' + esc(project.status) + '</span>' +
      '<span class="meta-phase">' + esc(project.phase) + '</span>' +
      '<span class="meta-updated">更新：' + esc(project.updated) + '</span>' +
      '</div><button class="ghost small panorama-synthesize" type="button">从事件更新全景</button>' +
      '</header>'
    );

    for (var i = 0; i < SECTION_ORDER.length; i++) {
      var sectionId = SECTION_ORDER[i];
      var sec = sections[sectionId];
      // Work Map is a slot — always render if workMapHtml is provided
      if (!sec && sectionId !== 'work-map') continue;
      if (!sec) sec = { title: '工作地图', ownership: 'structured', content: '', source_event_ids: [] };
      fragments.push(renderSection(sectionId, sec, workMapHtml));
    }

    return '<div class="project-panorama">' + fragments.join('') + '</div>';
  }

  /**
   * Render reviewed content as safe HTML (for section editors).
   * @param {string} content
   * @returns {string}
   */
  function renderReviewedContent(content) {
    return safeMarkdown(content);
  }

  // ── Exports ────────────────────────────────────────────────────

  globalThis.ProjectPanorama = Object.freeze({
    render: render,
    renderReviewedContent: renderReviewedContent,
    esc: esc,
  });
})();
