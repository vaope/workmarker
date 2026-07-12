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


def test_main_window_uses_work_map_without_project_timeline() -> None:
    html = Path("client/windows/main.html").read_text(encoding="utf-8")
    source = Path("client/windows/main.js").read_text(encoding="utf-8")
    assert '<script src="work-map.js"></script>' in html
    assert 'data-view="timeline"' not in html
    assert 'id="timeline-view"' not in html
    assert 'timeline-body' not in source
    refresh = source[source.index("async function refreshCurrent"):source.index("function switchView")]
    assert "wea.listTimeline" not in refresh
    assert "WorkMap.render(items)" in source
    assert "wea.updateTask(projectPath, task.task_id, 'status', nextStatus)" in source


def test_main_window_uses_confirmed_hierarchy_labels() -> None:
    html = Path("client/windows/main.html").read_text(encoding="utf-8")
    source = Path("client/windows/main.js").read_text(encoding="utf-8")
    renderer = Path("client/windows/work-map.js").read_text(encoding="utf-8")
    combined = html + source + renderer
    assert "+ 新建工作项" in combined
    assert "+ 新建任务" in combined
    assert "工作项名称" in combined
    assert "任务名称" in combined
    assert "删除需求" not in combined
    assert "所属需求" not in combined


def test_main_composer_uses_durable_inbox_not_single_proposal() -> None:
    source = Path("client/windows/main.js").read_text(encoding="utf-8")
    submit = source[source.index("async function submitUpdate"):source.index("// ---- in-app delete confirm")]
    assert "wea.createCapture(text, pending)" in submit
    assert "wea.processCapture(captureId)" in source
    assert "wea.propose(" not in submit
    assert "state.proposal" not in source
    assert "state.pending = []" in submit
    assert "wea.discardPending" in submit


def test_main_composer_transfers_attachment_ownership_after_durable_create() -> None:
    source = Path("client/windows/main.js").read_text(encoding="utf-8")
    submit = source[source.index("async function submitUpdate"):source.index("// ---- in-app delete confirm")]
    assert "const pending = state.pending.slice();" in submit
    create_idx = submit.index("const created = await wea.createCapture(text, pending);")
    clear_idx = submit.index("state.pending = [];")
    discard_idx = submit.index("wea.discardPending(pending.map((attachment) => attachment.tempPath))")
    assert create_idx < clear_idx < discard_idx
    create_failure_branch = submit[create_idx:clear_idx]
    assert "if (!created || !created.ok || !created.card)" in create_failure_branch
    assert "return;" in create_failure_branch


def test_today_summary_uses_existing_task_and_inbox_data() -> None:
    html = Path("client/windows/main.html").read_text(encoding="utf-8")
    source = Path("client/windows/main.js").read_text(encoding="utf-8")
    assert 'id="today-rail"' in html
    assert 'id="today-pending-count"' in html
    assert 'id="today-open-count"' in html
    assert "card.state === 'needs_confirmation'" in source
    assert "task.status === 'in_progress'" in source
    assert "switchView('inbox')" in source
    assert "switchView('reports')" in source
    assert "wea.listCaptures()" in source
