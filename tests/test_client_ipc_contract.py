from pathlib import Path


def test_create_item_ipc_forwards_background() -> None:
    source = Path("client/main.js").read_text(encoding="utf-8")
    start = source.index("ipcMain.handle('wea:createItem'")
    end = source.index("ipcMain.handle('wea:createTask'", start)
    handler = source[start:end]

    assert "{ projectPath, title, background }" in handler
    assert "background," in handler
