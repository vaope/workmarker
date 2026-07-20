import json
from pathlib import Path


def test_client_package_builds_and_publishes_windows_updates() -> None:
    package = json.loads(Path("client/package.json").read_text(encoding="utf-8"))

    assert "electron-updater" in package["dependencies"]
    assert "electron-builder" in package["devDependencies"]
    assert package["scripts"]["test:update"] == "node --test tests/update_manager.test.js"
    assert package["scripts"]["dist:win"] == "electron-builder --win nsis --publish never"
    assert package["scripts"]["release:win"] == "electron-builder --win nsis --publish always"

    build = package["build"]
    assert build["appId"] == "ai.clowder.workeventagent"
    assert build["electronDist"] == "node_modules/electron/dist"
    assert build["win"] == {"target": ["nsis"], "icon": "assets/icon.png"}
    assert build["nsis"]["artifactName"] == "${productName}-Setup-${version}.${ext}"
    assert build["publish"] == {
        "provider": "github",
        "owner": "vaope",
        "repo": "workmarker",
    }
    assert any(resource["to"] == "workeventagent" for resource in build["extraResources"])


def test_packaged_client_runs_python_from_electron_resources() -> None:
    bridge = Path("client/python_bridge.js").read_text(encoding="utf-8")

    assert "app.isPackaged" in bridge
    assert "process.resourcesPath" in bridge
    assert "backendRoot" in bridge


def test_update_ipc_is_bounded_by_preload_bridge() -> None:
    main = Path("client/main.js").read_text(encoding="utf-8")
    preload = Path("client/preload.js").read_text(encoding="utf-8")

    for channel in (
        "wea:getUpdateState",
        "wea:checkForUpdates",
        "wea:downloadUpdate",
        "wea:installUpdate",
    ):
        assert f"ipcMain.handle('{channel}'" in main

    for method in (
        "getUpdateState",
        "checkForUpdates",
        "downloadUpdate",
        "installUpdate",
        "onUpdateState",
    ):
        assert method in preload

    assert "createUpdateManager" in main
    assert "app.isPackaged" in main
    assert "scheduleInitialUpdateCheck" in main
    assert "5_000" in main


def test_settings_surface_exposes_manual_update_flow() -> None:
    html = Path("client/windows/main.html").read_text(encoding="utf-8")
    renderer = Path("client/windows/main.js").read_text(encoding="utf-8")

    for control in (
        "settings-app-version",
        "update-status",
        "update-progress",
        "update-check",
        "update-download",
        "update-install",
    ):
        assert f'id="{control}"' in html
        assert control in renderer

    assert "wea.getUpdateState" in renderer
    assert "wea.onUpdateState(handleUpdateState)" in renderer
    update_handler = renderer[
        renderer.index("function handleUpdateState") : renderer.index("function formatUpdateBytes")
    ]
    assert "toast(" in update_handler
    assert "available" in update_handler
    assert "ready" in update_handler
    assert "textContent" in renderer[renderer.index("function renderUpdateState") :]
