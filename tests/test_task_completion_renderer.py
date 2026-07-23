import json
import subprocess
from pathlib import Path


def run_node(script: str) -> str:
    return subprocess.run(
        ["node", "-e", script],
        cwd=Path.cwd(),
        check=True,
        text=True,
        encoding="utf-8",
        capture_output=True,
    ).stdout


def test_task_rows_switch_between_resume_action_and_conclusion() -> None:
    script = r"""
const fs = require('fs');
const vm = require('vm');
vm.runInThisContext(fs.readFileSync('client/windows/work-map.js', 'utf8'));
const html = WorkMap.render([{
  item_id: 'cache',
  title: 'Cache',
  tasks: [
    { task_id: 'active', title: 'Active', status: 'in_progress',
      next_action: 'Run tests', conclusion: '' },
    { task_id: 'done', title: 'Done', status: 'done',
      next_action: 'Old action', conclusion: 'Validated safely' }
  ]
}]);
process.stdout.write(html);
"""
    html = run_node(script)
    active = html[html.index('data-task-id="active"'):html.index('data-task-id="done"')]
    done = html[html.index('data-task-id="done"'):]
    assert "task-next" in active
    assert "Run tests" in active
    assert "task-conclusion" not in active
    assert "task-conclusion" in done
    assert "Validated safely" in done
    assert "Old action" not in done


def test_completion_panel_escapes_task_content() -> None:
    script = r"""
const fs = require('fs');
const vm = require('vm');
vm.runInThisContext(fs.readFileSync('client/windows/task-completion.js', 'utf8'));
process.stdout.write(TaskCompletion.panelMarkup({ title: '<script>x</script>' }));
"""
    html = run_node(script)
    assert "&lt;script&gt;" in html
    assert "<script>" not in html
    assert "完成结论" in html
    assert "后续任务" in html


def test_completion_controller_opens_without_writing_and_saves_single_flight() -> None:
    script = r"""
const fs = require('fs');
const vm = require('vm');
vm.runInThisContext(fs.readFileSync('client/windows/task-completion.js', 'utf8'));

class Control {
  constructor(value = '') {
    this.value = value;
    this.disabled = false;
    this.checked = false;
    this.focused = false;
    this.handler = null;
    this.classList = { add() {}, remove() {} };
  }
  addEventListener(_name, handler) { this.handler = handler; }
  focus() { this.focused = true; }
}

const conclusion = new Control();
const nextTask = new Control();
const cancel = new Control();
const save = new Control();
const errorBox = new Control();
errorBox.textContent = '';
const controls = [conclusion, nextTask, cancel, save];
const editor = {
  querySelector(selector) {
    return {
      '.completion-conclusion': conclusion,
      '.completion-next-task': nextTask,
      '.completion-error': errorBox,
      '.completion-cancel': cancel,
      '.completion-save': save,
    }[selector];
  },
  querySelectorAll() { return controls; },
};
const row = {
  markup: '',
  insertAdjacentHTML(_where, markup) { this.markup = markup; },
  querySelector(selector) {
    return selector === '.task-completion-editor' ? editor : null;
  },
};
const input = new Control();
input.checked = true;
globalThis.document = { querySelectorAll() { return []; } };

let completeCalls = 0;
let refreshCalls = 0;
let resolveComplete;
const pending = new Promise((resolve) => { resolveComplete = resolve; });
const controller = TaskCompletion.createController({
  getProjectPath: () => 'project.md',
  completeTask: () => { completeCalls += 1; return pending; },
  updateTask: async () => ({ ok: true }),
  refresh: async () => { refreshCalls += 1; },
  notify: () => {},
});

(async () => {
  await controller.handleToggle(input, row, {
    task_id: 'verify-cache',
    title: 'Verify cache',
    status: 'in_progress',
  });
  const beforeSave = {
    completeCalls,
    checked: input.checked,
    disabled: input.disabled,
    focused: conclusion.focused,
    hasMarkup: row.markup.includes('task-completion-editor'),
  };
  conclusion.value = 'Validated safely';
  nextTask.value = 'Document result';
  const first = save.handler();
  const second = save.handler();
  const duringSave = {
    completeCalls,
    allDisabled: controls.every((control) => control.disabled),
  };
  resolveComplete({ ok: true });
  await Promise.all([first, second]);
  process.stdout.write(JSON.stringify({ beforeSave, duringSave, refreshCalls }));
})();
"""
    result = json.loads(run_node(script))
    assert result["beforeSave"] == {
        "completeCalls": 0,
        "checked": False,
        "disabled": True,
        "focused": True,
        "hasMarkup": True,
    }
    assert result["duringSave"] == {"completeCalls": 1, "allDisabled": True}
    assert result["refreshCalls"] == 1


def test_typed_completion_bridge_is_bounded() -> None:
    main = Path("client/main.js").read_text(encoding="utf-8")
    preload = Path("client/preload.js").read_text(encoding="utf-8")
    assert "ipcMain.handle('wea:completeTask'" in main
    assert "callBackend('complete_task'" in main
    assert "completeTask:" in preload
    assert "ipcRenderer.invoke('wea:completeTask'" in preload
