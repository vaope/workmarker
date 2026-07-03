# Report Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build scheduled and persisted WorkEventAgent reports with local-time windows, explicit manual date ranges, and AI synthesis where the product requires it.

**Architecture:** Python backend owns Timeline aggregation, local-date filtering, report rendering, AI synthesis, and atomic Markdown writes. Electron main process owns the schedule clock and config because it already owns the tray lifecycle and workspace config. The renderer keeps the Reports tab as the preview and settings surface.

**Tech Stack:** Python 3.13 standard library, existing `workeventagent.gui`, existing opencode runner contract, Electron main/preload/renderer JavaScript, existing unittest test suite.

## Global Constraints

- No generate-on-open behavior.
- Scheduled generation runs only while Electron app or tray process is alive.
- Scheduled daily reports write only when the target computer-local day has Timeline events.
- All report date filtering uses the computer local timezone at runtime.
- Manual long-period reports use explicit `date_from` and `date_to`.
- Reports persist under `<workspace>/reports/` as Markdown files.
- Daily and weekly reports are deterministic plus an optional AI highlight.
- Project summaries require a selected project and require AI narrative generation.
- Existing `generate_report` callers remain compatible during the migration.
- Every task ends with tests and a commit.

---

## File Structure

- Modify `workeventagent/gui.py`: local-time range helpers, report aggregation, report persistence, extended `handle_generate_report`.
- Modify `workeventagent/opencode_runner.py`: add `run_reporter`.
- Create `.opencode/agent/workevent-reporter.md`: read-only reporter prompt.
- Modify `tests/test_gui.py`: report range, persistence, scheduler-mode skip, and project-summary tests.
- Modify `tests/test_opencode_runner.py`: reporter runner invocation tests.
- Modify `client/main.js`: scheduler config, minute tick, `wea:generateReport` request shape, report status IPC.
- Modify `client/preload.js`: expose extended report APIs.
- Modify `client/windows/main.html`: report schedule controls and date range controls.
- Modify `client/windows/main.js`: report tab state, manual date range request, written path display.
- Modify `client/windows/main.css`: report schedule card, output path, and range-control styles.
- Modify `client/README.md`: report scheduling and persistence usage.
- Modify `docs/designs/F001-client-architecture.md` only if needed to link to `docs/designs/F002-report-generation.md`.

---

### Task 1: Local-Time Report Window and Aggregation

**Files:**
- Modify: `workeventagent/gui.py`
- Test: `tests/test_gui.py`

**Interfaces:**
- Produces: `_parse_local_date_range(date_from: str, date_to: str) -> tuple[datetime, datetime]`
- Produces: `_filter_events_by_local_range(events: list[dict], date_from: datetime, date_to: datetime) -> list[dict]`
- Produces: extended `handle_generate_report(request: dict) -> dict`

- [ ] **Step 1: Add failing tests for local day boundaries**

Add these tests to `tests/test_gui.py` near the current `generate_report` tests:

```python
def test_generate_report_filters_by_explicit_local_date_range(self):
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        project = _write_project_with_timeline(
            workspace,
            "local-boundary.md",
            [
                ("2026-07-02T15:30:00+00:00", "event-evening", "task-a", "Evening local event"),
                ("2026-07-03T16:30:00+00:00", "event-next-day", "task-a", "Next local day event"),
            ],
        )
        _ = project

        result = handle_generate_report({
            "workspace": str(workspace),
            "type": "daily",
            "date_from": "2026-07-02",
            "date_to": "2026-07-02",
            "persist": False,
            "include_ai": False,
        })

        assert result["ok"] is True
        assert result["event_count"] == 1
        assert "Evening local event" in result["report"]
        assert "Next local day event" not in result["report"]
```

If `_write_project_with_timeline` does not exist, add a helper in `tests/test_gui.py`:

```python
def _write_project_with_timeline(workspace: Path, filename: str, events: list[tuple[str, str, str, str]]) -> Path:
    lines = [
        "---",
        "project_id: report-project",
        "title: Report Project",
        "doc_kind: work_project",
        "created: 2026-07-01",
        "updated: 2026-07-01",
        "---",
        "",
        "## Current Snapshot",
        "",
        "## Work Map",
        "### Item: Report Item <!-- item:item-a -->",
        "#### Task: Report Task <!-- task:task-a -->",
        "- status: in_progress",
        "- next_action:",
        "- last_event_id:",
        "",
        "## Decisions",
        "",
        "## Attachments",
        "",
        "## Timeline",
    ]
    for timestamp, event_id, task_id, summary in events:
        lines.extend([
            f"- {timestamp} <!-- event:{event_id} -->",
            f"  - task_id: {task_id}",
            f"  - input: {summary}",
            f"  - summary: {summary}",
            "  - status: in_progress",
            "  - next_action:",
        ])
    lines.extend(["", "## Daily / Weekly Rollups", ""])
    path = workspace / filename
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
```

- [ ] **Step 2: Run the failing test**

Run: `python -m pytest tests/test_gui.py::GuiReportTests::test_generate_report_filters_by_explicit_local_date_range -q`

Expected before implementation: FAIL because `handle_generate_report` does not consume `date_from` and `date_to`.

- [ ] **Step 3: Implement local range helpers**

In `workeventagent/gui.py`, replace the fixed UTC date parsing in `handle_generate_report` with helpers:

```python
def _parse_local_date_range(date_from: str, date_to: str) -> tuple[datetime, datetime]:
    start_day = datetime.strptime(date_from, "%Y-%m-%d").date()
    end_day = datetime.strptime(date_to, "%Y-%m-%d").date()
    if end_day < start_day:
        raise ValueError("date_to must be on or after date_from")
    start = datetime.combine(start_day, datetime.min.time()).astimezone()
    end = datetime.combine(end_day, datetime.max.time()).astimezone()
    return start, end


def _filter_events_by_local_range(
    events: list[dict], date_from: datetime, date_to: datetime
) -> list[dict]:
    result: list[dict] = []
    for ev in events:
        ts = ev.get("timestamp", "")
        if not ts:
            continue
        try:
            ev_dt = datetime.fromisoformat(ts).astimezone()
        except ValueError:
            continue
        if date_from <= ev_dt <= date_to:
            result.append(ev)
    return result
```

Update `handle_generate_report` to accept `date_from` and `date_to`:

```python
date_from_str = request.get("date_from") or request.get("date") or datetime.now().astimezone().strftime("%Y-%m-%d")
date_to_str = request.get("date_to") or date_from_str
try:
    date_from, date_to = _parse_local_date_range(date_from_str, date_to_str)
except ValueError as exc:
    return {"ok": False, "kind": "invalid_input", "error": str(exc)}
```

- [ ] **Step 4: Update report type validation**

Keep current `daily`, `weekly`, and `project_summary`, and add `range`. Do not calculate `quarterly` or `semi_annual` as fixed 90/180 days inside backend. Treat old `quarterly` and `semi_annual` requests as `range` when `date_from` and `date_to` are supplied:

```python
raw_type = request.get("type", "daily")
if raw_type in {"quarterly", "semi_annual"}:
    report_type = "range"
    range_label = raw_type
else:
    report_type = raw_type
    range_label = str(request.get("range_label", "custom"))
```

- [ ] **Step 5: Run targeted tests**

Run: `python -m pytest tests/test_gui.py -q`

Expected: all `test_gui.py` tests pass.

- [ ] **Step 6: Commit**

Run:

```bash
git add workeventagent/gui.py tests/test_gui.py
git commit -m "feat: add local-time report ranges"
```

Expected: commit succeeds.

---

### Task 2: Persist Reports Atomically

**Files:**
- Modify: `workeventagent/gui.py`
- Test: `tests/test_gui.py`

**Interfaces:**
- Produces: `_report_output_path(workspace: Path, report_type: str, date_from: str, date_to: str, project_id: str | None, range_label: str) -> Path`
- Produces: `_write_report_atomically(path: Path, content: str) -> None`
- Produces: `handle_generate_report(... persist=True ...)` response with `written_path`, `skipped`, and `skip_reason`

- [ ] **Step 1: Add failing persistence tests**

Add tests:

```python
def test_generate_report_persists_markdown_with_frontmatter(self):
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        _write_project_with_timeline(
            workspace,
            "report-project.md",
            [("2026-07-03T10:00:00+00:00", "event-one", "task-a", "Persist me")],
        )

        result = handle_generate_report({
            "workspace": str(workspace),
            "type": "daily",
            "date_from": "2026-07-03",
            "date_to": "2026-07-03",
            "persist": True,
            "include_ai": False,
        })

        assert result["ok"] is True
        assert result["skipped"] is False
        report_path = Path(result["written_path"])
        assert report_path.exists()
        text = report_path.read_text(encoding="utf-8")
        assert "doc_kind: work_report" in text
        assert "report_type: daily" in text
        assert "Persist me" in text


def test_scheduled_daily_skips_when_no_events(self):
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        _write_project_with_timeline(workspace, "empty-day.md", [])

        result = handle_generate_report({
            "workspace": str(workspace),
            "type": "daily",
            "date_from": "2026-07-03",
            "date_to": "2026-07-03",
            "persist": True,
            "mode": "scheduled",
            "include_ai": False,
        })

        assert result["ok"] is True
        assert result["skipped"] is True
        assert result["skip_reason"] == "no_events"
        assert not (workspace / "reports").exists()
```

- [ ] **Step 2: Run the failing tests**

Run: `python -m pytest tests/test_gui.py::GuiReportTests::test_generate_report_persists_markdown_with_frontmatter tests/test_gui.py::GuiReportTests::test_scheduled_daily_skips_when_no_events -q`

Expected before implementation: FAIL because report files are not written and skip metadata is absent.

- [ ] **Step 3: Implement report paths and atomic writes**

Add helpers in `workeventagent/gui.py`:

```python
def _report_output_path(
    workspace: Path,
    report_type: str,
    date_from: str,
    date_to: str,
    project_id: str | None,
    range_label: str,
) -> Path:
    reports_dir = workspace / "reports"
    if report_type == "daily":
        return reports_dir / "daily" / f"{date_from}.md"
    if report_type == "weekly":
        return reports_dir / "weekly" / f"{date_from}_to_{date_to}.md"
    if report_type == "project_summary":
        safe_project = project_id or "project"
        return reports_dir / "project" / f"{safe_project}-summary-{date_to}.md"
    safe_label = range_label or "custom"
    return reports_dir / "range" / f"{date_from}_to_{date_to}-{safe_label}.md"


def _write_report_atomically(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)
```

Add `import os` at the top if it is not present.

- [ ] **Step 4: Add frontmatter rendering**

Build report content as:

```python
frontmatter = [
    "---",
    "doc_kind: work_report",
    f"report_type: {report_type}",
    f"date_from: {date_from_str}",
    f"date_to: {date_to_str}",
    f"generated_at: {datetime.now().astimezone().isoformat(timespec='seconds')}",
    "timezone: local",
    f"source_project_ids: [{', '.join(project_ids)}]",
    f"event_count: {total_events}",
    "generator_version: F002",
    "---",
    "",
]
report = "\n".join(frontmatter + report_lines)
```

Use `project_ids = [p.get("project_id", "") for p in included_projects if p.get("project_id")]`.

- [ ] **Step 5: Implement scheduled no-event skip**

Before writing, add:

```python
mode = request.get("mode", "manual")
if mode == "scheduled" and report_type in {"daily", "weekly"} and total_events == 0:
    return {
        "ok": True,
        "report": "",
        "written_path": "",
        "date_range": {"from": date_from_str, "to": date_to_str},
        "project_count": 0,
        "event_count": 0,
        "skipped": True,
        "skip_reason": "no_events",
    }
```

This exact condition covers daily and weekly scheduled reports.

- [ ] **Step 6: Run tests**

Run: `python -m pytest tests/test_gui.py -q`

Expected: all `test_gui.py` tests pass.

- [ ] **Step 7: Commit**

Run:

```bash
git add workeventagent/gui.py tests/test_gui.py
git commit -m "feat: persist generated reports"
```

Expected: commit succeeds.

---

### Task 3: Reporter Agent and AI Synthesis

**Files:**
- Create: `.opencode/agent/workevent-reporter.md`
- Modify: `workeventagent/opencode_runner.py`
- Modify: `workeventagent/gui.py`
- Test: `tests/test_opencode_runner.py`
- Test: `tests/test_gui.py`

**Interfaces:**
- Produces: `run_reporter(prompt: str, report_doc: Path, opencode_bin: str = "opencode") -> str`
- Produces: `parse_reporter_output(raw: str) -> dict`
- Consumes: existing `_run_opencode_agent`

- [ ] **Step 1: Add opencode runner test**

Add to `tests/test_opencode_runner.py`:

```python
@patch("workeventagent.opencode_runner.subprocess.run")
def test_run_reporter_calls_opencode_reporter_agent_with_file(self, run):
    run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout='{"type":"text","part":{"text":"{}"}}\n', stderr="")

    output = run_reporter("summarize", Path("report-context.md"), opencode_bin="opencode")

    assert output
    cmd = run.call_args.args[0]
    assert "--agent" in cmd
    assert "workevent-reporter" in cmd
    assert "--file" in cmd
    assert "report-context.md" in cmd
```

Import `run_reporter` in that file.

- [ ] **Step 2: Run failing runner test**

Run: `python -m pytest tests/test_opencode_runner.py::OpencodeRunnerTests::test_run_reporter_calls_opencode_reporter_agent_with_file -q`

Expected before implementation: FAIL because `run_reporter` is missing.

- [ ] **Step 3: Implement `run_reporter`**

In `workeventagent/opencode_runner.py`:

```python
def run_reporter(
    prompt: str, report_doc: Path, opencode_bin: str = "opencode"
) -> str:
    return _run_opencode_agent(
        prompt=prompt,
        input_doc=report_doc,
        agent_name="workevent-reporter",
        opencode_bin=opencode_bin,
    )
```

- [ ] **Step 4: Create reporter agent prompt**

Create `.opencode/agent/workevent-reporter.md`:

```markdown
---
description: Summarize WorkEventAgent timeline events into report highlights.
tools:
  read: true
  write: false
  edit: false
  bash: false
---

You summarize WorkEventAgent report context. Return only JSON, wrapped in no prose.

Schema:
{
  "highlight": "short paragraph for daily or weekly reports",
  "narrative": "longer project-summary narrative",
  "risks": ["risk, blocker, or follow-up"],
  "next_actions": ["recommended next action"]
}

Rules:
- Do not invent events not present in the context.
- Keep daily and weekly highlights concise.
- For project summaries, explain progress, current state, blockers, and recommended next steps.
- If the context has no events, return empty strings and empty arrays.
```

- [ ] **Step 5: Add reporter parse tests**

In `tests/test_gui.py`, add:

```python
def test_project_summary_requires_reporter_success(self):
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        _write_project_with_timeline(
            workspace,
            "report-project.md",
            [("2026-07-03T10:00:00+00:00", "event-one", "task-a", "Summarize me")],
        )

        with patch("workeventagent.gui.run_reporter", side_effect=OpencodeRunnerError("reporter failed")):
            result = handle_generate_report({
                "workspace": str(workspace),
                "type": "project_summary",
                "project_id": "report-project",
                "date_from": "2026-07-03",
                "date_to": "2026-07-03",
                "persist": True,
                "include_ai": True,
            })

        assert result["ok"] is False
        assert result["kind"] == "opencode_error"
        assert not (workspace / "reports" / "project").exists()
```

Add imports for `patch` and `OpencodeRunnerError` if missing.

- [ ] **Step 6: Wire AI synthesis into reports**

In `workeventagent/gui.py`, import `run_reporter` and `OpencodeRunnerError`. Add a deterministic context builder:

```python
def _reporter_context(report_type: str, date_from: str, date_to: str, report_body: str) -> str:
    return "\n".join([
        f"report_type: {report_type}",
        f"date_from: {date_from}",
        f"date_to: {date_to}",
        "",
        report_body,
    ])
```

Reject project summaries without AI synthesis before writing:

```python
if report_type == "project_summary" and request.get("include_ai") is False:
    return {
        "ok": False,
        "kind": "invalid_input",
        "error": "project_summary requires include_ai=true",
    }
```

Use an OS temp file and delete it after opencode returns:

```python
with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as fh:
    context_path = Path(fh.name)
    fh.write(_reporter_context(report_type, date_from_str, date_to_str, deterministic_body))
try:
    raw = run_reporter(
        "Summarize this report context as JSON.",
        context_path,
        opencode_bin=request.get("opencode_bin", "opencode"),
    )
finally:
    context_path.unlink(missing_ok=True)
```

Add `import tempfile` at the top of `workeventagent/gui.py`.

For daily and weekly, catch `OpencodeRunnerError` and insert `AI highlight unavailable.`. For `project_summary`, return:

```python
return {"ok": False, "kind": "opencode_error", "error": str(exc)}
```

- [ ] **Step 7: Run tests**

Run:

```bash
python -m pytest tests/test_opencode_runner.py tests/test_gui.py -q
```

Expected: all targeted tests pass.

- [ ] **Step 8: Commit**

Run:

```bash
git add .opencode/agent/workevent-reporter.md workeventagent/opencode_runner.py workeventagent/gui.py tests/test_opencode_runner.py tests/test_gui.py
git commit -m "feat: add reporter synthesis"
```

Expected: commit succeeds.

---

### Task 4: Electron Scheduler and Config

**Files:**
- Modify: `client/config.js`
- Modify: `client/main.js`
- Modify: `client/preload.js`

**Interfaces:**
- Produces: `scheduleReports()` in `client/main.js`
- Produces: `wea:getReportScheduleStatus` IPC
- Produces: `wea:updateConfig` accepts `reportSchedule`

- [ ] **Step 1: Add report schedule defaults**

In `client/config.js`, extend the default config object:

```javascript
reportSchedule: {
  dailyEnabled: false,
  dailyTime: '23:30',
  weeklyEnabled: false,
  weeklyDay: 5,
  weeklyTime: '18:00',
  lastDailyRunDate: '',
  lastWeeklyRunKey: '',
  lastRunStatus: '',
}
```

- [ ] **Step 2: Add scheduler helpers in `client/main.js`**

Add near existing config helpers:

```javascript
function localDateString(d = new Date()) {
  const year = d.getFullYear();
  const month = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function mondayOfLocalWeek(d = new Date()) {
  const copy = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const day = copy.getDay() || 7;
  copy.setDate(copy.getDate() - day + 1);
  return copy;
}

function addDays(d, days) {
  const copy = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  copy.setDate(copy.getDate() + days);
  return copy;
}
```

- [ ] **Step 3: Implement minute scheduler**

Add:

```javascript
let reportScheduleTimer = null;
let reportSchedulerStartedAt = null;

function startReportScheduler() {
  if (reportScheduleTimer) clearInterval(reportScheduleTimer);
  reportSchedulerStartedAt = new Date();
  reportScheduleTimer = setInterval(() => {
    runScheduledReports().catch((err) => {
      console.error('scheduled report failed', err);
    });
  }, 60 * 1000);
}

function scheduledTimeForLocalDate(date, hhmm) {
  const [hour, minute] = hhmm.split(':').map((x) => Number(x));
  return new Date(date.getFullYear(), date.getMonth(), date.getDate(), hour, minute, 0, 0);
}

function canRunScheduledAt(now, scheduledAt) {
  return reportSchedulerStartedAt && reportSchedulerStartedAt <= scheduledAt && now >= scheduledAt;
}

async function runScheduledReports(now = new Date()) {
  const c = cfg();
  if (!c.workspace) return;
  const schedule = c.reportSchedule || {};
  const today = localDateString(now);

  const dailyAt = scheduledTimeForLocalDate(now, schedule.dailyTime || '23:30');
  if (schedule.dailyEnabled && canRunScheduledAt(now, dailyAt) && schedule.lastDailyRunDate !== today) {
    const res = await callBackend('generate_report', {
      workspace: c.workspace,
      type: 'daily',
      date_from: today,
      date_to: today,
      persist: true,
      mode: 'scheduled',
      include_ai: true,
    }, c.pythonCmd);
    saveConfig({reportSchedule: {...schedule, lastDailyRunDate: today, lastRunStatus: JSON.stringify(res)}});
  }

  const weekStart = mondayOfLocalWeek(now);
  const weekEnd = addDays(weekStart, 6);
  const weekKey = `${localDateString(weekStart)}_to_${localDateString(weekEnd)}`;
  const weeklyOffset = schedule.weeklyDay === 0 ? 6 : Number(schedule.weeklyDay || 5) - 1;
  const weeklyDate = addDays(weekStart, weeklyOffset);
  const weeklyAt = scheduledTimeForLocalDate(weeklyDate, schedule.weeklyTime || '18:00');
  if (schedule.weeklyEnabled && canRunScheduledAt(now, weeklyAt) && schedule.lastWeeklyRunKey !== weekKey) {
    const res = await callBackend('generate_report', {
      workspace: c.workspace,
      type: 'weekly',
      date_from: localDateString(weekStart),
      date_to: localDateString(weekEnd),
      persist: true,
      mode: 'scheduled',
      include_ai: true,
    }, c.pythonCmd);
    saveConfig({reportSchedule: {...schedule, lastWeeklyRunKey: weekKey, lastRunStatus: JSON.stringify(res)}});
  }
}
```

Call `startReportScheduler()` after app readiness and config initialization.

- [ ] **Step 4: Add schedule status IPC**

Add:

```javascript
ipcMain.handle('wea:getReportScheduleStatus', async () => {
  const c = cfg();
  return {ok: true, reportSchedule: c.reportSchedule || {}};
});
```

In `client/preload.js` expose:

```javascript
getReportScheduleStatus: () => ipcRenderer.invoke('wea:getReportScheduleStatus'),
```

- [ ] **Step 5: Run JavaScript syntax checks**

Run:

```bash
node --check client/main.js
node --check client/preload.js
```

Expected: both commands exit 0.

- [ ] **Step 6: Commit**

Run:

```bash
git add client/config.js client/main.js client/preload.js
git commit -m "feat: schedule report generation"
```

Expected: commit succeeds.

---

### Task 5: Reports Tab UI

**Files:**
- Modify: `client/windows/main.html`
- Modify: `client/windows/main.js`
- Modify: `client/windows/main.css`
- Modify: `client/preload.js`
- Modify: `client/main.js`

**Interfaces:**
- Consumes: `wea.generateReport(request)` shape from Task 1 and Task 2
- Consumes: `wea.getReportScheduleStatus()`
- Produces: UI controls for schedule settings and manual range generation

- [ ] **Step 1: Extend preload report API**

Change `client/preload.js` report function from positional args to object form:

```javascript
generateReport: (request) => ipcRenderer.invoke('wea:generateReport', request),
```

Change `client/main.js` IPC to pass through `date_from`, `date_to`, `persist`, `mode`, `include_ai`, and `range_label`:

```javascript
ipcMain.handle('wea:generateReport', async (_e, request) => {
  const c = cfg();
  return callBackend('generate_report', {
    workspace: c.workspace,
    type: request.type || 'daily',
    project_id: request.projectId || request.project_id || null,
    date_from: request.dateFrom || request.date_from || request.date || null,
    date_to: request.dateTo || request.date_to || request.date || null,
    range_label: request.rangeLabel || request.range_label || '',
    persist: request.persist !== false,
    mode: request.mode || 'manual',
    include_ai: request.includeAi !== false,
  }, c.pythonCmd);
});
```

- [ ] **Step 2: Update HTML controls**

In `client/windows/main.html`, replace the single date input with:

```html
<select id="report-type">
  <option value="daily">日报</option>
  <option value="weekly">周报</option>
  <option value="range">指定日期范围</option>
  <option value="project_summary">项目总结</option>
</select>
<input id="report-date-from" type="date" />
<input id="report-date-to" type="date" />
<button id="report-generate" class="primary small">生成并保存</button>
<span id="report-status" class="report-status"></span>
```

Add a schedule card:

```html
<div class="report-schedule-card">
  <label><input id="report-daily-enabled" type="checkbox" /> 定时日报</label>
  <input id="report-daily-time" type="time" value="23:30" />
  <label><input id="report-weekly-enabled" type="checkbox" /> 定时周报</label>
  <select id="report-weekly-day">
    <option value="1">周一</option>
    <option value="2">周二</option>
    <option value="3">周三</option>
    <option value="4">周四</option>
    <option value="5">周五</option>
    <option value="6">周六</option>
    <option value="0">周日</option>
  </select>
  <input id="report-weekly-time" type="time" value="18:00" />
  <button id="report-save-schedule" class="small">保存定时设置</button>
</div>
```

- [ ] **Step 3: Update renderer request**

In `client/windows/main.js`, replace `generateReport()` with:

```javascript
async function generateReport() {
  const type = $('#report-type').value;
  const dateFrom = $('#report-date-from').value || todayStr();
  const dateTo = $('#report-date-to').value || dateFrom;
  const projectId = (type === 'project_summary' && state.currentProject)
    ? state.currentProject.project_id : null;
  const statusEl = $('#report-status');
  statusEl.textContent = '生成中...';
  try {
    const res = await wea.generateReport({
      type,
      projectId,
      dateFrom,
      dateTo,
      persist: true,
      mode: 'manual',
      includeAi: true,
    });
    if (!res || !res.ok) {
      $('#reports-body').innerHTML = `<div class="empty">生成失败：${esc((res && res.error) || '未知错误')}</div>`;
      statusEl.textContent = '';
      return;
    }
    statusEl.textContent = res.skipped ? '无记录，已跳过' : `${res.event_count || 0} 条记录 · ${res.project_count || 0} 个项目`;
    const pathHtml = res.written_path ? `<div class="report-path">${esc(res.written_path)}</div>` : '';
    $('#reports-body').innerHTML = `${pathHtml}<pre class="report-md">${esc(res.report || '')}</pre>`;
  } catch (e) {
    $('#reports-body').innerHTML = `<div class="empty">生成失败：${esc(e.message || String(e))}</div>`;
    statusEl.textContent = '';
  }
}
```

- [ ] **Step 4: Save schedule settings**

Add:

```javascript
async function saveReportSchedule() {
  const reportSchedule = {
    dailyEnabled: $('#report-daily-enabled').checked,
    dailyTime: $('#report-daily-time').value || '23:30',
    weeklyEnabled: $('#report-weekly-enabled').checked,
    weeklyDay: Number($('#report-weekly-day').value),
    weeklyTime: $('#report-weekly-time').value || '18:00',
  };
  const res = await wea.updateConfig({reportSchedule});
  $('#report-status').textContent = res && res.ok ? '定时设置已保存' : '定时设置保存失败';
}
```

Bind it in `bindStaticHandlers()`:

```javascript
$('#report-save-schedule').addEventListener('click', saveReportSchedule);
```

- [ ] **Step 5: Add styles**

In `client/windows/main.css`:

```css
.report-schedule-card {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--border);
}

.report-path {
  margin: 0 0 12px;
  color: var(--text-faint);
  font-size: 12px;
  word-break: break-all;
}
```

- [ ] **Step 6: Run syntax checks**

Run:

```bash
node --check client/windows/main.js
node --check client/main.js
node --check client/preload.js
```

Expected: all commands exit 0.

- [ ] **Step 7: Commit**

Run:

```bash
git add client/windows/main.html client/windows/main.js client/windows/main.css client/preload.js client/main.js
git commit -m "feat: add report schedule controls"
```

Expected: commit succeeds.

---

### Task 6: Documentation and Verification

**Files:**
- Modify: `client/README.md`
- Modify: `docs/designs/F001-client-architecture.md`
- Test: full Python and JavaScript verification

**Interfaces:**
- Consumes: all prior tasks
- Produces: acceptance-ready F002 documentation

- [ ] **Step 1: Update client README**

Add a section to `client/README.md`:

```markdown
## Reports

The Reports tab can generate and save Markdown reports under `<workspace>/reports/`.

Scheduled reports run while the Electron app or tray process is alive:

- Daily reports use the computer-local date and skip days with no Timeline events.
- Weekly reports use the selected local weekday and skip weeks with no Timeline events.
- Opening the app does not generate missed reports automatically.

Manual reports support explicit `date_from` and `date_to` values.
```

- [ ] **Step 2: Link F002 from F001 architecture**

Add this sentence near the report-related IPC list in `docs/designs/F001-client-architecture.md`:

```markdown
Report generation is specified by `docs/designs/F002-report-generation.md`.
```

- [ ] **Step 3: Run full verification**

Run:

```bash
python -m pytest
node --check client/main.js
node --check client/preload.js
node --check client/windows/main.js
node --check client/windows/capture.js
```

Expected: all Python tests pass and all JavaScript syntax checks exit 0.

- [ ] **Step 4: Inspect generated diff**

Run:

```bash
git diff --check
git status --short
```

Expected: `git diff --check` prints no whitespace errors. `git status --short` shows only F002 implementation files.

- [ ] **Step 5: Commit**

Run:

```bash
git add client/README.md docs/designs/F001-client-architecture.md
git commit -m "docs: document report generation"
```

Expected: commit succeeds.

---

## Self-Review

Spec coverage:

- Scheduled daily and weekly generation: Task 4.
- No generate-on-open: Global Constraints and Task 4 scheduler rules.
- Skip scheduled daily when there are no local-day events: Task 2.
- AI highlight and project summary narrative: Task 3.
- Persist `reports/*.md`: Task 2.
- Explicit date ranges: Task 1 and Task 5.
- Computer local time: Task 1.
- UI preview and settings: Task 5.
- Documentation and verification: Task 6.

Type consistency:

- Backend request fields use `date_from` and `date_to`.
- Renderer object fields use `dateFrom` and `dateTo`, translated in IPC.
- `project_id` is passed to backend as `project_id`.
- `persist`, `mode`, and `include_ai` are backend fields.

Placeholder scan:

- This plan contains no placeholder markers.
- Each implementation task names concrete files, functions, tests, commands, and expected outcomes.
