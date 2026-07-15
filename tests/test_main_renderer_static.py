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


def test_reviewed_section_editors_send_base_hashes() -> None:
    source = Path("client/windows/main.js").read_text(encoding="utf-8")
    assert "baseSectionHash" in source
    assert "baseMetadataHash" in source
    assert "stale_section" in source
    assert "stale_metadata" in source
