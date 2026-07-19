import json
import subprocess
import sys
import tempfile
from pathlib import Path


def test_gui_module_entrypoint_serves_python_bridge_command() -> None:
    with tempfile.TemporaryDirectory() as td:
        result = subprocess.run(
            [sys.executable, "-m", "workeventagent.gui", "projects"],
            input=json.dumps({"workspace": str(Path(td))}),
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip(), "python bridge command produced no JSON response"
    assert json.loads(result.stdout) == {"ok": True, "projects": []}
