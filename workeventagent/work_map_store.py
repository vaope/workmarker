"""Dual-schema Work Map parser and mutation helpers.

Consumes ``find_section(text, "work-map")`` from ``project_schema`` to prefix
the parsing window, then applies one of two grammars depending on
``schema_version(text)``.

All mutation functions locate blocks through stable item/task anchors and
structural heading boundaries; they never match display titles.
"""
from __future__ import annotations

import re

from workeventagent.project_schema import find_section, schema_version

# ── Anchor regular expressions ──────────────────────────────

V1_ITEM_RE = re.compile(r"^###\s+Item:\s+(.+?)\s*<!--\s*item:(.+?)\s*-->\s*$")
V2_ITEM_RE = re.compile(r"^###\s+工作项[：:]\s*(.+?)\s*<!--\s*item:(.+?)\s*-->\s*$")
V1_TASK_RE = re.compile(r"^####\s+Task:\s+(.+?)\s*<!--\s*task:(.+?)\s*-->\s*$")
V2_TASK_RE = re.compile(r"^####\s+\[([ xX])\]\s+任务[：:]\s*(.+?)\s*<!--\s*task:(.+?)\s*-->\s*$")
V1_BG_RE = re.compile(r"^-\s*background:\s*(.*)")
V2_NEXT_RE = re.compile(r"^-\s*下一步[：:]\s*(.*)$")
V2_CONCLUSION_RE = re.compile(r"^-\s*结论[：:]\s*(.*)$")
V2_META_RE = re.compile(r"^<!--\s*task-meta:last_event_id=(.*?)\s*-->$")
V1_STATUS_RE = re.compile(r"^-\s*status:\s*(.*)")
V1_NEXT_ACTION_RE = re.compile(r"^-\s*next_action:\s*(.*)")
V1_CONCLUSION_RE = re.compile(r"^-\s*conclusion:\s*(.*)")
V1_LAST_EVENT_RE = re.compile(r"^-\s*last_event_id:\s*(.*)")


# ── Public parse ────────────────────────────────────────────

def parse_work_map(text: str, strict: bool = False) -> list[dict]:
    """Parse Work Map section into typed item/task state.

    Returns items in file order, tasks in item order.  For v1 documents the
    parser consumes English-status lines; for v2 documents it consumes the
    human-readable checkbox / Chinese-next-action grammar.  Both grammars
    produce identical typed output.

    When *strict* is ``True`` the parser additionally rejects duplicate IDs,
    task headings outside an item block, missing or duplicate v1 status
    fields, and invalid checkbox markers.
    """
    v = schema_version(text)
    section = find_section(text, "work-map")
    body = text[section.content_start:section.content_end]
    lines = body.splitlines()

    items: list[dict] = []
    seen_item_ids: set[str] = set()
    seen_task_ids: set[str] = set()
    current_item: dict | None = None
    current_task: dict | None = None
    in_background = False

    def _flush_task() -> None:
        nonlocal current_task
        if current_task is None:
            return
        if strict and not current_task.get("_has_status"):
            raise ValueError(f"task {current_task['task_id']} missing canonical status field")
        current_task.pop("_has_status", None)
        assert current_item is not None
        current_item["tasks"].append(current_task)
        current_task = None

    def _flush_item() -> None:
        nonlocal current_item
        if current_item is None:
            return
        _flush_task()
        items.append(current_item)
        current_item = None
        in_background = False  # type: ignore[assignment]

    for line in lines:
        stripped = line.strip()

        # --- end-of-section guard ---
        if stripped.startswith("## ") and "section:" not in stripped:
            _flush_item()
            break

        # --- item heading ---
        if v >= 2:
            im = V2_ITEM_RE.match(line)
        else:
            im = V1_ITEM_RE.match(line)
        if im:
            _flush_item()
            item_id = im.group(2).strip()
            if strict:
                if item_id in seen_item_ids:
                    raise ValueError(f"duplicate item id: {item_id}")
                seen_item_ids.add(item_id)
            current_item = {
                "item_id": item_id,
                "title": im.group(1).strip(),
                "background": "",
                "tasks": [],
            }
            in_background = True
            continue

        # --- task heading ---
        if current_item is not None:
            if v >= 2:
                tm = V2_TASK_RE.match(line)
            else:
                tm = V1_TASK_RE.match(line)
            if tm:
                _flush_task()
                task_id = tm.group(3 if v >= 2 else 2).strip()
                if strict:
                    if task_id in seen_task_ids:
                        raise ValueError(f"duplicate task id: {task_id}")
                    seen_task_ids.add(task_id)
                status = "in_progress"
                if v >= 2:
                    status = "done" if tm.group(1).lower() == "x" else "in_progress"
                    _has_status = True
                else:
                    _has_status = False
                current_task = {
                    "task_id": task_id,
                    "title": (tm.group(2) if v >= 2 else tm.group(1)).strip(),
                    "status": status,
                    "next_action": "",
                    "conclusion": "",
                    "last_event_id": "",
                    "_has_status": _has_status,
                }
                in_background = False
                continue

        if current_task is not None:
            # --- v1 task metadata ---
            if v < 2:
                sm = V1_STATUS_RE.match(line)
                if sm:
                    val = sm.group(1).strip()
                    if val not in ("in_progress", "done") and strict:
                        raise ValueError(f"canonical status required, got {val!r}")
                    current_task["status"] = val
                    current_task["_has_status"] = True
                    continue
                nm = V1_NEXT_ACTION_RE.match(line)
                if nm:
                    current_task["next_action"] = nm.group(1).strip()
                    continue
                cm = V1_CONCLUSION_RE.match(line)
                if cm:
                    current_task["conclusion"] = cm.group(1).strip()
                    continue
                lm = V1_LAST_EVENT_RE.match(line)
                if lm:
                    current_task["last_event_id"] = lm.group(1).strip()
                    continue
            else:
                # --- v2 task metadata ---
                nm2 = V2_NEXT_RE.match(line)
                if nm2:
                    current_task["next_action"] = nm2.group(1).strip()
                    continue
                cm2 = V2_CONCLUSION_RE.match(line)
                if cm2:
                    current_task["conclusion"] = cm2.group(1).strip()
                    continue
                mm2 = V2_META_RE.match(line)
                if mm2:
                    current_task["last_event_id"] = mm2.group(1).strip()
                    continue
            continue

        # --- item background (prose between item heading and first task) ---
        if current_item is not None and in_background and not current_item["tasks"] and current_task is None:
            if v >= 2:
                bg_line = stripped
                if bg_line and not bg_line.startswith("-"):
                    if current_item["background"]:
                        current_item["background"] += "\n"
                    current_item["background"] += bg_line
            else:
                bm = V1_BG_RE.match(line)
                if bm:
                    current_item["background"] = bm.group(1).strip()

    _flush_item()

    if strict:
        for item in items:
            for task in item["tasks"]:
                if not task.get("_has_status"):
                    pass  # already checked in _flush_task
                task.pop("_has_status", None)

    return items


# ── Rendering ───────────────────────────────────────────────

def render_v2_item(item: dict) -> str:
    """Render a single item block in v2 Markdown (without leading blank)."""
    lines = [f"### 工作项：{item['title']} <!-- item:{item['item_id']} -->"]
    bg = str(item.get("background", "")).strip()
    if bg:
        lines.append("")
        lines.append(bg)
    lines.append("")
    for task in item.get("tasks", []):
        lines.append(render_v2_task(task))
        lines.append("")
    return "\n".join(lines)


def render_v2_task(task: dict) -> str:
    """Render a single task block in v2 Markdown."""
    checked = "x" if task.get("status") == "done" else " "
    next_action = str(task.get("next_action", "")).replace("\n", " ").strip()
    conclusion = str(task.get("conclusion", "")).replace("\n", " ").strip()
    last_event = str(task.get("last_event_id", "")).strip()
    return (
        f"#### [{checked}] 任务：{task['title']} <!-- task:{task['task_id']} -->\n"
        f"- 下一步：{next_action}\n"
        f"- 结论：{conclusion}\n"
        f"<!-- task-meta:last_event_id={last_event} -->"
    )


def render_v1_task(task: dict) -> str:
    """Render a single task block in v1 Markdown."""
    return "\n".join([
        f"#### Task: {task['title']} <!-- task:{task['task_id']} -->",
        f"- status: {task.get('status', 'in_progress')}",
        f"- next_action: {task.get('next_action', '')}",
        f"- conclusion: {task.get('conclusion', '')}",
        f"- last_event_id: {task.get('last_event_id', '')}",
    ])


# ── Mutation helpers ───────────────────────────────────────

def _find_task_heading(text: str, task_id: str) -> tuple[int, int, int]:
    """Return (schema_version, heading_start, heading_end) for the task heading line."""
    v = schema_version(text)
    if v >= 2:
        pattern = re.compile(
            rf"^####\s+\[[ xX]\]\s+任务[：:].*<!--\s*task:{re.escape(task_id)}\s*-->\s*$",
            re.MULTILINE,
        )
    else:
        pattern = re.compile(
            rf"^####\s+Task:.*<!--\s*task:{re.escape(task_id)}\s*-->\s*$",
            re.MULTILINE,
        )
    m = pattern.search(text)
    if m is None:
        raise ValueError(f"task not found: {task_id}")
    return v, m.start(), m.end()


def _find_next_block_boundary(text: str, pos: int) -> int:
    """Return the byte position of the next ``####``, ``###``, or ``##`` heading, or end of text."""
    m = re.search(r"^(#{2,4})\s", text[pos:], re.MULTILINE)
    return pos + m.start() if m else len(text)


def _split_line_ending(line: str) -> tuple[str, str]:
    content = line.rstrip("\r\n")
    return content, line[len(content):]


def _task_field_match(line: str, schema_ver: int, field: str) -> tuple[re.Match[str], int] | None:
    content, _ = _split_line_ending(line)
    if field == "title":
        match = (V2_TASK_RE if schema_ver >= 2 else V1_TASK_RE).match(content)
        return (match, 2 if schema_ver >= 2 else 1) if match else None
    if field == "status":
        match = (V2_TASK_RE if schema_ver >= 2 else V1_STATUS_RE).match(content)
        return (match, 1) if match else None
    if field == "next_action":
        match = (V2_NEXT_RE if schema_ver >= 2 else V1_NEXT_ACTION_RE).match(content)
        return (match, 1) if match else None
    if field == "conclusion":
        match = (V2_CONCLUSION_RE if schema_ver >= 2 else V1_CONCLUSION_RE).match(content)
        return (match, 1) if match else None
    if field == "last_event_id":
        match = (V2_META_RE if schema_ver >= 2 else V1_LAST_EVENT_RE).match(content)
        return (match, 1) if match else None
    raise ValueError(f"unsupported task field: {field}")


def _task_field_value(schema_ver: int, field: str, value: str) -> str:
    rendered = str(value)
    if schema_ver >= 2 and field == "status":
        return "x" if rendered == "done" else " "
    if field in {"next_action", "conclusion"}:
        return rendered.replace("\n", " ").strip()
    if field == "last_event_id":
        return rendered.strip()
    return rendered


def _render_task_control_line(schema_ver: int, field: str, value: str) -> str:
    if schema_ver >= 2:
        if field == "next_action":
            return f"- 下一步：{value}"
        if field == "conclusion":
            return f"- 结论：{value}"
        if field == "last_event_id":
            return f"<!-- task-meta:last_event_id={value} -->"
    else:
        if field == "status":
            return f"- status: {value}"
        if field == "next_action":
            return f"- next_action: {value}"
        if field == "conclusion":
            return f"- conclusion: {value}"
        if field == "last_event_id":
            return f"- last_event_id: {value}"
    raise ValueError(f"task field must exist in heading: {field}")


def _task_control_order(schema_ver: int) -> tuple[str, ...]:
    if schema_ver >= 2:
        return ("next_action", "conclusion", "last_event_id")
    return ("status", "next_action", "conclusion", "last_event_id")


def _preferred_newline(lines: list[str]) -> str:
    for line in lines:
        _, ending = _split_line_ending(line)
        if ending:
            return ending
    return "\n"


def _insert_task_control_line(lines: list[str], schema_ver: int, field: str, value: str) -> None:
    order = _task_control_order(schema_ver)
    if field not in order:
        raise ValueError(f"task field must exist in heading: {field}")
    insert_at = 1
    for preceding_field in order[:order.index(field)]:
        for index, line in enumerate(lines):
            if _task_field_match(line, schema_ver, preceding_field):
                insert_at = max(insert_at, index + 1)
    newline = _preferred_newline(lines)
    if insert_at > 0:
        content, ending = _split_line_ending(lines[insert_at - 1])
        if not ending:
            lines[insert_at - 1] = content + newline
    lines.insert(insert_at, _render_task_control_line(schema_ver, field, value) + newline)


def _mutate_task_fields(text: str, task_id: str, updates: dict[str, str]) -> str:
    schema_ver, start, heading_end = _find_task_heading(text, task_id)
    block_end = _find_next_block_boundary(text, heading_end)
    lines = text[start:block_end].splitlines(keepends=True)
    for field, raw_value in updates.items():
        value = _task_field_value(schema_ver, field, raw_value)
        for index, line in enumerate(lines):
            field_match = _task_field_match(line, schema_ver, field)
            if field_match is None:
                continue
            match, group = field_match
            content, ending = _split_line_ending(line)
            value_start, value_end = match.span(group)
            replacement = value
            if (
                schema_ver < 2
                and replacement
                and value_start > 0
                and content[value_start - 1] == ":"
            ):
                replacement = " " + replacement
            lines[index] = content[:value_start] + replacement + content[value_end:] + ending
            break
        else:
            _insert_task_control_line(lines, schema_ver, field, value)
    return text[:start] + "".join(lines) + text[block_end:]


def update_task_field(text: str, task_id: str, field: str, value: str, updated_date: str = "") -> str:
    """Atomically update one field of a task block, preserving all sibling blocks byte-for-byte."""
    return _mutate_task_fields(text, task_id, {field: value})


def update_task_state(text: str, task_id: str, status: str, next_action: str = "", last_event_id: str = "") -> str:
    """Atomically update status, next_action, and last_event_id on a task."""
    return _mutate_task_fields(text, task_id, {
        "status": status,
        "next_action": next_action,
        "last_event_id": last_event_id,
    })


def complete_task_block(text: str, task_id: str, conclusion: str) -> str:
    """Set done + conclusion without overwriting unrelated task fields."""
    normalized = str(conclusion).replace("\n", " ").strip()
    if not normalized:
        raise ValueError("completion conclusion is required")
    return _mutate_task_fields(text, task_id, {
        "status": "done",
        "conclusion": normalized,
    })


def insert_task_after(text: str, after_task_id: str, task: dict) -> str:
    """Insert one rendered task immediately after an anchored task."""
    schema_ver, _, heading_end = _find_task_heading(text, after_task_id)
    insert_pos = _find_next_block_boundary(text, heading_end)
    block = render_v2_task(task) if schema_ver >= 2 else render_v1_task(task)
    before = text[:insert_pos]
    if before.endswith("\n\n"):
        prefix = ""
    elif before.endswith("\n"):
        prefix = "\n"
    else:
        prefix = "\n\n"
    return before + prefix + block + "\n\n" + text[insert_pos:]


def insert_item(text: str, item_id: str, title: str, background: str = "") -> str:
    """Insert a new item into the Work Map section."""
    section = find_section(text, "work-map")
    v = schema_version(text)
    if v >= 2:
        block = render_v2_item({"item_id": item_id, "title": title, "background": background, "tasks": []})
    else:
        bg_line = f"- background: {background}\n" if background else ""
        block = f"### Item: {title} <!-- item:{item_id} -->\n{bg_line}"
    insert_pos = section.content_end
    if insert_pos < len(text) and text[insert_pos - 1] != "\n":
        prefix = "\n\n"
    else:
        prefix = "\n"
    return text[:insert_pos] + prefix + block + text[insert_pos:]


def insert_task(text: str, item_id: str, task_id: str, title: str) -> str:
    """Insert a new task into the target item block."""
    items = parse_work_map(text)
    target = None
    for it in items:
        if it["item_id"] == item_id:
            target = it
            break
    if target is None:
        raise ValueError(f"item not found: {item_id}")
    v = schema_version(text)
    # Find the item heading position
    if v >= 2:
        pattern = re.compile(rf"^###\s+工作项[：:].*<!--\s*item:{re.escape(item_id)}\s*-->", re.MULTILINE)
    else:
        pattern = re.compile(rf"^###\s+Item:.*<!--\s*item:{re.escape(item_id)}\s*-->", re.MULTILINE)
    m = pattern.search(text)
    if m is None:
        raise ValueError(f"item not found: {item_id}")
    # Insert before next heading after this item
    next_pos = _find_next_block_boundary(text, m.end())
    new_task = {
        "task_id": task_id,
        "title": title,
        "status": "in_progress",
        "next_action": "",
        "conclusion": "",
        "last_event_id": "",
    }
    if v >= 2:
        block = "\n" + render_v2_task(new_task) + "\n"
    else:
        block = "\n" + render_v1_task(new_task) + "\n"
    return text[:next_pos] + block + text[next_pos:]


def delete_item(text: str, item_id: str) -> str:
    """Delete an item block and all its tasks, preserving siblings."""
    items = parse_work_map(text)
    target_idx = None
    for i, it in enumerate(items):
        if it["item_id"] == item_id:
            target_idx = i
            break
    if target_idx is None:
        raise ValueError(f"item not found: {item_id}")
    v = schema_version(text)
    if v >= 2:
        item_pat = re.compile(rf"^###\s+工作项[：:].*<!--\s*item:{re.escape(item_id)}\s*-->", re.MULTILINE)
    else:
        item_pat = re.compile(rf"^###\s+Item:.*<!--\s*item:{re.escape(item_id)}\s*-->", re.MULTILINE)
    m = item_pat.search(text)
    if m is None:
        raise ValueError(f"item not found: {item_id}")
    item_start = m.start()
    # Find next item or end of section
    section = find_section(text, "work-map")
    next_item = re.search(
        r"^###\s+(?:Item:|工作项[：:])",
        text[item_start + 1:],
        re.MULTILINE,
    )
    if next_item:
        item_end = item_start + 1 + next_item.start()
    else:
        item_end = section.content_end
    # Strip leading whitespace before the deleted block
    before = text[:item_start].rstrip()
    after = text[item_end:]
    if not after.startswith("\n"):
        after = "\n" + after
    return before + "\n" + after.lstrip("\n")


def update_item(text: str, item_id: str, title: str, background: str | None = None) -> str:
    """Update an item's title and/or background, preserving siblings byte-for-byte.

    When *background* is ``None`` the background line is left unchanged.
    An empty string ``""`` removes the existing background line.
    """
    items = parse_work_map(text)
    target = None
    for it in items:
        if it["item_id"] == item_id:
            target = it
            break
    if target is None:
        raise ValueError(f"item not found: {item_id}")
    v = schema_version(text)
    if v >= 2:
        item_pat = re.compile(rf"^###\s+工作项[：:]\s*.+?\s*<!--\s*item:{re.escape(item_id)}\s*-->", re.MULTILINE)
    else:
        item_pat = re.compile(rf"^###\s+Item:\s*.+?\s*<!--\s*item:{re.escape(item_id)}\s*-->", re.MULTILINE)
    m = item_pat.search(text)
    if m is None:
        raise ValueError(f"item heading not found for {item_id}")
    heading_start, heading_end = m.start(), m.end()

    # 1. Replace heading title
    if v >= 2:
        new_heading = f"### 工作项：{title} <!-- item:{item_id} -->"
    else:
        new_heading = f"### Item: {title} <!-- item:{item_id} -->"
    result = text[:heading_start] + new_heading + text[heading_end:]

    # 2. Update background, if requested
    if background is not None:
        result = _set_item_background(result, item_id, background, v)
    return result


def _set_item_background(text: str, item_id: str, background: str, schema_ver: int) -> str:
    """Insert, update, or remove a background line after the item heading.

    *background* of ``""`` removes any existing background line.
    """
    if schema_ver >= 2:
        item_pat = re.compile(rf"^###\s+工作项[：:].*<!--\s*item:{re.escape(item_id)}\s*-->", re.MULTILINE)
    else:
        item_pat = re.compile(rf"^###\s+Item:.*<!--\s*item:{re.escape(item_id)}\s*-->", re.MULTILINE)
    m = item_pat.search(text)
    if m is None:
        raise ValueError(f"item heading not found for {item_id}")
    heading_end = m.end()
    lines = text.splitlines(keepends=True)
    # Find line index of heading
    heading_line_idx = None
    for i, line in enumerate(lines):
        if f"<!-- item:{item_id} -->" in line:
            heading_line_idx = i
            break
    if heading_line_idx is None:
        raise ValueError(f"item anchor not found: {item_id}")

    # Find existing background line after heading (before next heading)
    heading_re = re.compile(r"^(#{2,4})\s")
    bg_idx = None
    for j in range(heading_line_idx + 1, len(lines)):
        stripped = lines[j].strip()
        if heading_re.match(stripped):
            break
        if re.match(r"^-\s*background:", stripped):
            bg_idx = j
            break

    if background:
        bg_line = f"- background: {background}\n"
        if bg_idx is not None:
            lines[bg_idx] = bg_line
        else:
            insert_at = heading_line_idx + 1
            while insert_at < len(lines) and lines[insert_at].strip() == "":
                insert_at += 1
            lines.insert(insert_at, bg_line)
    elif bg_idx is not None:
        del lines[bg_idx]
    return "".join(lines)


def delete_task(text: str, task_id: str) -> str:
    """Delete a task block, preserving siblings and item."""
    v, start, end = _find_task_heading(text, task_id)
    block_end = _find_next_block_boundary(text, end)
    # Remove preceding blank line if present
    before = text[:start].rstrip()
    after = text[block_end:]
    return before + "\n" + after.lstrip("\n")
