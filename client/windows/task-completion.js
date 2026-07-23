(function exposeTaskCompletion(root) {
  function esc(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function panelMarkup(task) {
    return `<div class="task-completion-editor task-editor" data-task-id="${esc(task.task_id || '')}">
      <div class="completion-heading">完成「${esc(task.title || '')}」</div>
      <label class="te-row">
        <span>完成结论 <b aria-hidden="true">*</b></span>
        <input class="completion-conclusion" type="text"
          placeholder="这次验证、交付或决策得出了什么结论？" />
      </label>
      <label class="te-row">
        <span>后续任务 <em>（可选）</em></span>
        <input class="completion-next-task" type="text"
          placeholder="需要继续推进时，直接创建一个新任务" />
      </label>
      <div class="completion-error hidden" role="alert"></div>
      <div class="te-acts">
        <button class="ghost small completion-cancel" type="button">取消</button>
        <button class="primary small-btn completion-save" type="button">完成任务</button>
      </div>
    </div>`;
  }

  function createController(deps) {
    const {
      getProjectPath,
      completeTask,
      updateTask,
      refresh,
      notify,
    } = deps;
    let completionBusy = false;

    function closeEditors() {
      if (completionBusy) return false;
      document.querySelectorAll('.task-completion-editor').forEach((editor) => {
        const row = editor.closest('.task-row');
        const checkbox = row && row.querySelector('.task-check');
        if (checkbox) checkbox.disabled = false;
        editor.remove();
      });
      return true;
    }

    function setBusy(editor, busy) {
      editor.querySelectorAll('input, button').forEach((control) => {
        control.disabled = busy;
      });
    }

    async function reopen(input, task) {
      input.disabled = true;
      try {
        const result = await updateTask(
          getProjectPath(),
          task.task_id,
          'status',
          'in_progress',
        );
        if (!result || !result.ok) {
          input.checked = true;
          input.disabled = false;
          notify(`重新打开任务失败：${(result && result.error) || '后端错误'}`, 'err');
          return;
        }
        await refresh();
      } catch (error) {
        input.checked = true;
        input.disabled = false;
        notify(`重新打开任务出错：${error.message || error}`, 'err');
      }
    }

    function openEditor(input, row, task) {
      if (!closeEditors()) {
        input.checked = false;
        return;
      }
      input.checked = false;
      input.disabled = true;
      row.insertAdjacentHTML('beforeend', panelMarkup(task));
      const editor = row.querySelector('.task-completion-editor');
      const conclusion = editor.querySelector('.completion-conclusion');
      const nextTask = editor.querySelector('.completion-next-task');
      const errorBox = editor.querySelector('.completion-error');
      const saveButton = editor.querySelector('.completion-save');

      editor.querySelector('.completion-cancel').addEventListener('click', () => {
        if (completionBusy) return;
        editor.remove();
        input.disabled = false;
        input.focus();
      });
      saveButton.addEventListener('click', async () => {
        if (saveButton.disabled) return;
        const conclusionValue = conclusion.value.trim();
        const nextTaskValue = nextTask.value.trim();
        if (!conclusionValue) {
          errorBox.textContent = '请填写完成结论';
          errorBox.classList.remove('hidden');
          conclusion.focus();
          return;
        }

        errorBox.classList.add('hidden');
        completionBusy = true;
        setBusy(editor, true);
        try {
          const result = await completeTask(
            getProjectPath(),
            task.task_id,
            conclusionValue,
            nextTaskValue,
          );
          if (!result || !result.ok) {
            errorBox.textContent = `完成失败：${(result && result.error) || '后端错误'}`;
            errorBox.classList.remove('hidden');
            completionBusy = false;
            setBusy(editor, false);
            return;
          }
          await refresh();
          completionBusy = false;
        } catch (error) {
          errorBox.textContent = `完成出错：${error.message || error}`;
          errorBox.classList.remove('hidden');
          completionBusy = false;
          setBusy(editor, false);
        }
      });
      conclusion.focus();
    }

    async function handleToggle(input, row, task) {
      if (completionBusy) {
        input.checked = task.status === 'done';
        return;
      }
      if (task.status === 'done') {
        await reopen(input, task);
        return;
      }
      openEditor(input, row, task);
    }

    return Object.freeze({ closeEditors, handleToggle });
  }

  root.TaskCompletion = Object.freeze({ createController, panelMarkup });
})(globalThis);
