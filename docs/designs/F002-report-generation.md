---
feature_ids: [F002]
topics: [reports, scheduler, local-time, opencode, markdown]
doc_kind: design
created: 2026-07-03
---

# F002 Report Generation Design

This document is the source of truth for report generation after the F001 client. It extends the current `generate_report` prototype into scheduled and persisted reports.

## Product Decisions

Co-creator decisions from 2026-07-02:

1. Daily and weekly reports should be scheduled. Do not generate reports just because the app is opened.
2. Scheduled daily generation writes a report only when the target local day has at least one Timeline event.
3. Daily and weekly reports use deterministic event grouping plus a short AI highlight. Project summaries use an AI narrative.
4. Reports are persisted as Markdown files under the workspace and can also be previewed in the client.
5. Manual long-period reports must support explicit date ranges. Quarterly and semi-annual are labels or presets, not fixed calendar-only calculations.
6. Date boundaries use the computer's local time at runtime.

## Goals

- Add scheduled daily and weekly report generation to the desktop client while the Electron app or tray process is running.
- Persist generated reports to `reports/*.md` inside the selected workspace.
- Keep deterministic report generation available even when AI synthesis fails for daily and weekly reports.
- Add AI synthesis for report highlights and project summaries through opencode.
- Support manual report generation for an explicit `date_from` and `date_to`.
- Preserve the current manual preview flow in the Reports tab.

## Non-Goals

- Do not add an OS background service that runs after the Electron app fully exits.
- Do not generate missed reports on app startup.
- Do not email, upload, or export reports outside the workspace.
- Do not edit project Markdown files when writing reports; reports are derived artifacts.

## Data Source

Timeline events are the report source. SQLite currently indexes project and task state, not Timeline event history, so F002 continues to parse each project's `## Timeline` section.

Each event is converted to local time at that event timestamp before date filtering:

```python
event_local = datetime.fromisoformat(event["timestamp"]).astimezone()
```

Report windows are inclusive local-date windows:

```text
date_from 00:00:00.000000 local <= event_local <= date_to 23:59:59.999999 local
```

## Report Files

Reports live under the workspace:

```text
<workspace>/reports/daily/YYYY-MM-DD.md
<workspace>/reports/weekly/YYYY-MM-DD_to_YYYY-MM-DD.md
<workspace>/reports/range/YYYY-MM-DD_to_YYYY-MM-DD-<range_label>.md
<workspace>/reports/project/<project_id>-summary-YYYY-MM-DD.md
```

Every report file starts with YAML frontmatter:

```yaml
doc_kind: work_report
report_type: daily
date_from: YYYY-MM-DD
date_to: YYYY-MM-DD
generated_at: YYYY-MM-DDTHH:MM:SS+HH:MM
timezone: local
source_project_ids: [project-id]
event_count: 3
generator_version: F002
```

Writes must be atomic: write to a temp file in the target directory, then `os.replace`.

## Backend Contract

The existing `python -m workeventagent.gui generate_report` command is extended rather than replaced.

Request:

```json
{
  "workspace": "D:/worklogs",
  "type": "daily",
  "project_id": null,
  "date_from": "2026-07-03",
  "date_to": "2026-07-03",
  "persist": true,
  "mode": "scheduled",
  "include_ai": true
}
```

Supported `type` values:

- `daily`
- `weekly`
- `range`
- `project_summary`

`range` carries a `range_label` value in the request when the UI wants to label a generated file as `quarterly` or `semi_annual`.

Response:

```json
{
  "ok": true,
  "report": "# Daily Report...",
  "written_path": "D:/worklogs/reports/daily/2026-07-03.md",
  "date_range": {"from": "2026-07-03", "to": "2026-07-03"},
  "project_count": 2,
  "event_count": 5,
  "skipped": false,
  "skip_reason": ""
}
```

Scheduled daily response when no events exist for that local day:

```json
{
  "ok": true,
  "report": "",
  "written_path": "",
  "date_range": {"from": "2026-07-03", "to": "2026-07-03"},
  "project_count": 0,
  "event_count": 0,
  "skipped": true,
  "skip_reason": "no_events"
}
```

Business failures still return `ok: false` with `kind` and `error`.

## AI Synthesis

Add `.opencode/agent/workevent-reporter.md` and a `run_reporter` wrapper in `workeventagent/opencode_runner.py`.

The reporter agent receives a deterministic context document containing:

- report type
- local date range
- source projects
- grouped Timeline events
- current task states when available

The reporter returns JSON:

```json
{
  "highlight": "One-paragraph daily or weekly highlight.",
  "narrative": "Longer project-summary narrative.",
  "risks": ["risk or blocker"],
  "next_actions": ["recommended next action"]
}
```

Daily and weekly reports are fail-open: if reporter synthesis fails, the deterministic event report is still written and the AI section says `AI highlight unavailable`.

Project summaries are AI-centered: if reporter synthesis fails, return `ok: false` and do not write a misleading summary document.

## Scheduler

The scheduler lives in Electron main process because the desktop app already owns config, tray lifetime, and IPC.

Config shape:

```json
{
  "reportSchedule": {
    "dailyEnabled": false,
    "dailyTime": "23:30",
    "weeklyEnabled": false,
    "weeklyDay": 5,
    "weeklyTime": "18:00",
    "lastDailyRunDate": "",
    "lastWeeklyRunKey": ""
  }
}
```

Rules:

- The scheduler runs only while the Electron app or tray process is alive.
- The scheduler checks once per minute.
- A scheduled run is eligible when the app or tray session was already alive before the scheduled local time and the current tick is at or after that scheduled time.
- Daily generation targets the current computer-local date.
- Daily generation skips and records no file when the target day has no Timeline events.
- Weekly generation targets the local Monday-Sunday week containing the current date and skips when the week has no Timeline events.
- A successful or skipped scheduled run records `lastDailyRunDate` or `lastWeeklyRunKey` so the same period does not run twice.
- Opening the app after a scheduled time does not backfill missed reports automatically.

## UI

The Reports tab gains:

- Scheduled report card: daily enabled/time, weekly enabled/day/time, last run status, next run estimate.
- Manual generator: report type, date range, optional project selector, generate button.
- Preview panel: rendered Markdown text and written path.

For manual long-period reports, the UI exposes `date_from` and `date_to`. It may provide quick-fill buttons for quarter-length and half-year-length ranges, but the final range remains user editable.

## Acceptance Criteria

- Daily scheduled report writes `reports/daily/YYYY-MM-DD.md` only when that computer-local date has at least one Timeline event.
- A day-boundary event at `23:30` local and an event at `00:30` local are assigned to their visible local dates even when stored as UTC timestamps.
- Weekly scheduled report writes one file per local week and does not run twice for the same week.
- Manual range report accepts arbitrary `date_from` and `date_to`.
- Project summary requires a selected project and writes a narrative Markdown report.
- Reports are written atomically.
- Daily and weekly AI failure does not block deterministic report persistence.
- Project-summary AI failure returns a visible error and writes no project-summary file.
- The Reports tab shows the written path and preview after generation.
