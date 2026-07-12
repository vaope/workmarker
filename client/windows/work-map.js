(function exposeWorkMap(root) {
  function esc(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function itemProgress(item) {
    const tasks = Array.isArray(item && item.tasks) ? item.tasks : [];
    return {
      done: tasks.filter((task) => task.status === 'done').length,
      total: tasks.length,
    };
  }

  function renderTask(task) {
    const done = task.status === 'done';
    const taskId = esc(task.task_id);
    return `<div class="task-row ${done ? 'done' : ''}" data-task-id="${taskId}">
      <label class="task-check-label">
        <input class="task-check" type="checkbox" data-task-id="${taskId}" data-status="${done ? 'done' : 'in_progress'}" aria-label="完成任务：${esc(task.title)}" ${done ? 'checked' : ''} />
        <span class="task-check-box" aria-hidden="true"></span>
        <span class="task-copy">
          <span class="task-name">${esc(task.title)}</span>
          ${task.next_action ? `<span class="task-next">${esc(task.next_action)}</span>` : ''}
        </span>
      </label>
      <span class="task-acts">
        <button class="icon-btn task-edit-btn" type="button" title="编辑任务" aria-label="编辑任务：${esc(task.title)}">✏️</button>
        <button class="icon-btn task-del-btn" type="button" title="删除任务" aria-label="删除任务：${esc(task.title)}">🗑️</button>
      </span>
    </div>`;
  }

  function renderItem(item) {
    const progress = itemProgress(item);
    const itemId = esc(item.item_id);
    const tasks = Array.isArray(item.tasks) ? item.tasks : [];
    return `<section class="item-group" data-item-id="${itemId}">
      <header class="item-head">
        <span class="item-head-title">${esc(item.title)}</span>
        <span class="item-progress" aria-label="已完成 ${progress.done} 个，共 ${progress.total} 个任务">${progress.done}/${progress.total}</span>
        <span class="item-head-acts">
          <button class="icon-btn item-edit-btn" type="button" title="编辑工作项" aria-label="编辑工作项：${esc(item.title)}">✏️</button>
          <button class="icon-btn item-del-btn" type="button" title="删除工作项" aria-label="删除工作项：${esc(item.title)}">🗑️</button>
          <button class="ghost add-task-mini item-add-task" type="button">+ 新建任务</button>
        </span>
      </header>
      <div class="task-list">${tasks.length ? tasks.map(renderTask).join('') : '<div class="empty-tasks">暂无任务</div>'}</div>
    </section>`;
  }

  function render(items) {
    return (Array.isArray(items) ? items : []).map(renderItem).join('');
  }

  root.WorkMap = Object.freeze({ itemProgress, render });
})(globalThis);
