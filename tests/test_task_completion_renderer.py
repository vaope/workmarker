from pathlib import Path


def test_typed_completion_bridge_is_bounded() -> None:
    main = Path("client/main.js").read_text(encoding="utf-8")
    preload = Path("client/preload.js").read_text(encoding="utf-8")
    assert "ipcMain.handle('wea:completeTask'" in main
    assert "callBackend('complete_task'" in main
    assert "completeTask:" in preload
    assert "ipcRenderer.invoke('wea:completeTask'" in preload
