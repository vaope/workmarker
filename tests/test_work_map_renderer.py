import json
import subprocess
from pathlib import Path


def _render_fixture() -> dict:
    script = r"""
const fs = require('fs');
const vm = require('vm');
vm.runInThisContext(fs.readFileSync('client/windows/work-map.js', 'utf8'));
const items = [{
  item_id: 'frontend',
  title: '优化前端体验',
  tasks: [
    { task_id: 'checkbox', title: '任务改成 checkbox', status: 'in_progress', next_action: '补交互测试' },
    { task_id: 'hide-timeline', title: '弱化时间线', status: 'done', next_action: '' }
  ]
}];
process.stdout.write(JSON.stringify({
  progress: WorkMap.itemProgress(items[0]),
  html: WorkMap.render(items)
}));
"""
    result = subprocess.run(
        ["node", "-e", script],
        cwd=Path.cwd(),
        check=True,
        text=True,
        encoding="utf-8",
        capture_output=True,
    )
    return json.loads(result.stdout)


def test_work_map_renders_progress_and_native_checkboxes() -> None:
    rendered = _render_fixture()
    assert rendered["progress"] == {"done": 1, "total": 2}
    assert 'type="checkbox"' in rendered["html"]
    assert 'data-task-id="checkbox"' in rendered["html"]
    assert 'data-task-id="hide-timeline"' in rendered["html"]
    assert "checked" in rendered["html"]
    assert "1/2" in rendered["html"]


def test_work_map_contains_current_state_but_no_timeline_markup() -> None:
    html = _render_fixture()["html"]
    assert "补交互测试" in html
    assert "task-timeline" not in html
    assert "tl-event" not in html
    assert "status-tag" not in html
    assert "- status:" not in html


def test_work_map_escapes_titles_and_handles_empty_item() -> None:
    script = r"""
const fs = require('fs');
const vm = require('vm');
vm.runInThisContext(fs.readFileSync('client/windows/work-map.js', 'utf8'));
const item = { item_id: 'empty', title: '<script>alert(1)</script>', tasks: [] };
process.stdout.write(WorkMap.render([item]));
"""
    result = subprocess.run(["node", "-e", script], check=True, text=True, encoding="utf-8", capture_output=True)
    assert "&lt;script&gt;" in result.stdout
    assert "0/0" in result.stdout
    assert "+ 新建任务" in result.stdout
