from pathlib import Path


def test_main_window_refreshes_current_project_on_inbox_update_event() -> None:
    source = Path("client/windows/main.js").read_text(encoding="utf-8")

    assert "wea.onInboxUpdated" in source
    handler_start = source.index("wea.onInboxUpdated")
    handler_body = source[handler_start:handler_start + 220]
    assert "refreshCurrent()" in handler_body


def test_settings_modal_exposes_opencode_model_choice() -> None:
    html = Path("client/windows/main.html").read_text(encoding="utf-8")
    renderer = Path("client/windows/main.js").read_text(encoding="utf-8")
    config = Path("client/config.js").read_text(encoding="utf-8")

    assert "opencodeModel: ''" in config
    assert 'id="settings-model"' in html
    assert "$('#settings-model').value" in renderer
    assert "opencodeModel" in renderer


def test_main_process_passes_configured_opencode_model_to_backend() -> None:
    source = Path("client/main.js").read_text(encoding="utf-8")

    assert "opencode_model: c.opencodeModel || ''" in source
    assert source.count("opencode_model: c.opencodeModel || ''") >= 5


def test_main_process_config_reader_is_available_to_scheduler() -> None:
    source = Path("client/main.js").read_text(encoding="utf-8")

    cfg_pos = source.index("const cfg = () => loadConfig();")
    attach_pos = source.index("function attachIpc()")
    scheduler_pos = source.index("async function runScheduledReports")
    assert cfg_pos < attach_pos < scheduler_pos
    assert source.count("const cfg = () => loadConfig();") == 1


def test_settings_exposes_two_labeled_hotkey_capture_inputs() -> None:
    html = Path("client/windows/main.html").read_text(encoding="utf-8")
    source = Path("client/windows/main.js").read_text(encoding="utf-8")
    assert 'id="settings-hotkey"' in html
    assert 'id="settings-main-hotkey"' in html
    assert "快速捕获快捷键" in html
    assert "主窗口快捷键" in html
    assert "mainHotkey" in source
    assert "captureAcceleratorInput" in source
    assert "mainAcceleratorInput" in source


def test_main_window_uses_project_panorama_as_default_surface() -> None:
    html = Path("client/windows/main.html").read_text(encoding="utf-8")
    source = Path("client/windows/main.js").read_text(encoding="utf-8")
    assert '项目全景' in html
    assert '<script src="project-panorama.js"></script>' in html
    refresh = source[source.index("async function refreshCurrent"):source.index("function switchView")]
    assert "wea.getProjectPanorama(path)" in refresh
    assert "ProjectPanorama.render(state.panoramaData, workMapHtml)" in source
    assert "wea.previewProjectMigration" in source
    assert "wea.applyProjectMigration" in source


def test_project_panorama_reuses_the_vertical_scroll_container() -> None:
    html = Path("client/windows/main.html").read_text(encoding="utf-8")
    css = Path("client/windows/main.css").read_text(encoding="utf-8")

    assert 'id="panorama-body" class="panorama-body scroll-body"' in html
    content_rule = css[css.index(".content {"):css.index(".content-head {")]
    assert "min-height: 0" in content_rule


def test_reviewed_section_editors_send_base_hashes() -> None:
    source = Path("client/windows/main.js").read_text(encoding="utf-8")
    assert "baseSectionHash" in source
    assert "baseMetadataHash" in source
    assert "stale_section" in source
    assert "stale_metadata" in source


def test_knowledge_preload_exposes_only_typed_operations() -> None:
    source = Path("client/preload.js").read_text(encoding="utf-8")
    expected = {
        "getKnowledgeState": "wea:getKnowledgeState",
        "enqueueKnowledge": "wea:enqueueKnowledge",
        "processKnowledgeJob": "wea:processKnowledgeJob",
        "retryKnowledgeJob": "wea:retryKnowledgeJob",
        "reviseKnowledgeProposal": "wea:reviseKnowledgeProposal",
        "rejectKnowledgeProposal": "wea:rejectKnowledgeProposal",
        "applyKnowledgeProposal": "wea:applyKnowledgeProposal",
        "applyKnowledgeDocument": "wea:applyKnowledgeDocument",
        "onKnowledgeUpdated": "wea:knowledge-updated",
    }
    for method, channel in expected.items():
        assert method in source
        assert channel in source
    assert "writeMarkdown" not in source
    assert "writeProjectFile" not in source


def test_main_process_has_serial_recoverable_knowledge_worker() -> None:
    source = Path("client/main.js").read_text(encoding="utf-8")

    assert "let knowledgeWorkerChain = Promise.resolve()" in source
    assert "function enqueueKnowledgeJob" in source
    assert "knowledge_process_job" in source
    assert "wea:knowledge-updated" in source
    commit = source[source.index("wea:inboxCommit"):source.index("wea:inboxCancel")]
    assert "knowledge_job_id" in commit
    assert "enqueueKnowledgeJob" in commit
    recovery = source[source.index("async function recoverKnowledgeWork"):
                      source.index("async function runScheduledKnowledge")]
    assert recovery.index("knowledge_recover") < recovery.index("knowledge_state")
    assert "enqueueKnowledgeJob" in recovery


def test_main_process_sends_client_local_utc_boundaries_with_scheduled_jobs() -> None:
    source = Path("client/main.js").read_text(encoding="utf-8")
    scheduled = source[
        source.index("async function runScheduledKnowledge"):
        source.index("function ensurePendingDir")
    ]

    assert "range_start_utc: planned.rangeStartUtc" in scheduled
    assert "range_end_utc: planned.rangeEndUtc" in scheduled


def test_main_process_maps_typed_knowledge_ipc_to_bounded_backend_commands() -> None:
    source = Path("client/main.js").read_text(encoding="utf-8")
    expected = {
        "wea:getKnowledgeState": "knowledge_state",
        "wea:enqueueKnowledge": "knowledge_enqueue",
        "wea:processKnowledgeJob": "knowledge_process_job",
        "wea:retryKnowledgeJob": "knowledge_retry_job",
        "wea:reviseKnowledgeProposal": "knowledge_revise_proposal",
        "wea:rejectKnowledgeProposal": "knowledge_reject_proposal",
        "wea:applyKnowledgeProposal": "knowledge_apply_proposal",
        "wea:applyKnowledgeDocument": "knowledge_apply_document",
    }
    for channel, command in expected.items():
        assert f"ipcMain.handle('{channel}'" in source
        assert f"'{command}'" in source


def test_knowledge_review_ui_uses_timeline_and_search_event_ids() -> None:
    html = Path("client/windows/main.html").read_text(encoding="utf-8")
    source = Path("client/windows/main.js").read_text(encoding="utf-8")
    panorama = Path("client/windows/project-panorama.js").read_text(encoding="utf-8")

    assert "从事件更新全景" in panorama
    assert "wea.listTimeline" in source
    assert "knowledge-event-select" in source
    assert "请至少选择一个事件" in source
    assert "data-event-id" in source
    assert "search-knowledge-select" in source
    assert "同一项目" in source
    assert 'id="knowledge-event-modal"' in html


def test_inbox_aggregates_durable_knowledge_without_reusing_capture_store() -> None:
    html = Path("client/windows/main.html").read_text(encoding="utf-8")
    source = Path("client/windows/main.js").read_text(encoding="utf-8")

    assert '<script src="knowledge-proposals.js"></script>' in html
    assert "wea.getKnowledgeState" in source
    assert "待审核知识" in source
    assert "KnowledgeProposals.renderReview" in source
    assert "KnowledgeProposals.renderImpactBadge" in source
    assert "knowledge_impact" in source
    assert "wea.onKnowledgeUpdated" in source
    assert "toast(" in source[source.index("wea.onKnowledgeUpdated"):]


def test_proposal_confirmation_revises_before_whole_bundle_apply() -> None:
    source = Path("client/windows/main.js").read_text(encoding="utf-8")
    start = source.index("async function confirmKnowledgeProposal")
    body = source[start:source.index("async function confirmKnowledgeDocument")]

    assert "expectedVersion" in body
    assert "includedChangeIds" in body
    assert "wea.reviseKnowledgeProposal" in body
    assert body.index("wea.reviseKnowledgeProposal") < body.index("wea.applyKnowledgeProposal")
    assert "stale" in body
    assert "regenerate" in source
    assert "regenerateOf" in source


def test_settings_exposes_daily_and_weekly_knowledge_schedule_controls() -> None:
    html = Path("client/windows/main.html").read_text(encoding="utf-8")
    source = Path("client/windows/main.js").read_text(encoding="utf-8")
    for control in (
        "settings-knowledge-daily-enabled",
        "settings-knowledge-daily-time",
        "settings-knowledge-weekly-enabled",
        "settings-knowledge-weekly-day",
        "settings-knowledge-weekly-time",
    ):
        assert f'id="{control}"' in html
        assert control in source
