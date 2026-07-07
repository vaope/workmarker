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
