from pathlib import Path


def test_main_window_refreshes_current_project_on_inbox_update_event() -> None:
    source = Path("client/windows/main.js").read_text(encoding="utf-8")

    assert "wea.onInboxUpdated" in source
    handler_start = source.index("wea.onInboxUpdated")
    handler_body = source[handler_start:handler_start + 220]
    assert "refreshCurrent()" in handler_body
