import json
import os
import subprocess


def _run(script: str) -> dict:
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        text=True,
        encoding="utf-8",
        capture_output=True,
        env={**os.environ, "TZ": "Asia/Shanghai"},
    )
    return json.loads(result.stdout)


def _schedule(**overrides: object) -> dict:
    schedule = {
        "dailyEnabled": True,
        "dailyTime": "23:30",
        "weeklyEnabled": True,
        "weeklyDay": 5,
        "weeklyTime": "18:00",
        "lastDailySuccessDate": "",
        "lastWeeklySuccessKey": "",
        "lastRunStatus": "",
    }
    schedule.update(overrides)
    return schedule


def _due(now: str, started_at: str, schedule: dict) -> list[dict]:
    return _run(
        "const K=require('./client/knowledge_schedule');"
        f"const value=K.dueRuns(new Date({json.dumps(now)}),new Date({json.dumps(started_at)}),"
        f"{json.dumps(schedule)});console.log(JSON.stringify(value));"
    )


def test_daily_due_uses_local_time_and_waits_when_startup_is_before_due() -> None:
    schedule = _schedule(weeklyEnabled=False)
    assert _due("2026-07-20T23:29:00", "2026-07-20T10:00:00", schedule) == []
    due = _due("2026-07-20T23:31:00", "2026-07-20T10:00:00", schedule)
    assert due == [{
        "cadence": "daily",
        "scheduleKey": "2026-07-20",
        "dateFrom": "2026-07-20",
        "dateTo": "2026-07-20",
        "rangeStartUtc": "2026-07-19T16:00:00.000Z",
        "rangeEndUtc": "2026-07-20T16:00:00.000Z",
    }]


def test_daily_due_emits_explicit_utc_boundaries_for_the_client_local_day() -> None:
    result = _run(
        "process.env.TZ='Asia/Shanghai';"
        "const K=require('./client/knowledge_schedule');"
        "const due=K.dueRuns(new Date('2026-07-20T23:31:00+08:00'),"
        "new Date('2026-07-20T10:00:00+08:00'),"
        f"{json.dumps(_schedule(weeklyEnabled=False))});"
        "console.log(JSON.stringify(due[0]));"
    )

    assert result["rangeStartUtc"] == "2026-07-19T16:00:00.000Z"
    assert result["rangeEndUtc"] == "2026-07-20T16:00:00.000Z"


def test_startup_after_daily_due_recovers_current_run_once() -> None:
    schedule = _schedule(weeklyEnabled=False)
    due = _due("2026-07-20T23:45:00", "2026-07-20T23:44:00", schedule)
    assert [item["scheduleKey"] for item in due] == ["2026-07-20"]
    assert _due(
        "2026-07-20T23:46:00",
        "2026-07-20T23:44:00",
        _schedule(weeklyEnabled=False, lastDailySuccessDate="2026-07-20"),
    ) == []


def test_weekly_due_uses_local_week_and_missed_startup() -> None:
    schedule = _schedule(dailyEnabled=False, weeklyDay=5, weeklyTime="18:00")
    due = _due("2026-07-24T19:00:00", "2026-07-24T18:59:00", schedule)
    assert due == [{
        "cadence": "weekly",
        "scheduleKey": "2026-07-20_to_2026-07-26",
        "dateFrom": "2026-07-20",
        "dateTo": "2026-07-26",
        "rangeStartUtc": "2026-07-19T16:00:00.000Z",
        "rangeEndUtc": "2026-07-26T16:00:00.000Z",
    }]


def test_daily_and_weekly_can_be_due_together() -> None:
    due = _due(
        "2026-07-24T23:45:00",
        "2026-07-24T23:44:00",
        _schedule(weeklyDay=5, weeklyTime="18:00"),
    )
    assert [item["cadence"] for item in due] == ["daily", "weekly"]
    assert len({f"{item['cadence']}:{item['scheduleKey']}" for item in due}) == 2


def test_mark_successful_advances_only_the_matching_completed_manifest() -> None:
    schedule = _schedule()
    script = (
        "const K=require('./client/knowledge_schedule');"
        f"const s={json.dumps(schedule)};"
        "const failed=K.markSuccessful(s,{cadence:'daily',scheduleKey:'2026-07-20',state:'processing'});"
        "const daily=K.markSuccessful(failed,{cadence:'daily',scheduleKey:'2026-07-20',state:'completed'});"
        "const weekly=K.markSuccessful(daily,{cadence:'weekly',scheduleKey:'2026-07-20_to_2026-07-26',state:'completed'});"
        "console.log(JSON.stringify({failed,daily,weekly}));"
    )
    result = _run(script)
    assert result["failed"]["lastDailySuccessDate"] == ""
    assert result["daily"]["lastDailySuccessDate"] == "2026-07-20"
    assert result["daily"]["lastWeeklySuccessKey"] == ""
    assert result["weekly"]["lastDailySuccessDate"] == "2026-07-20"
    assert result["weekly"]["lastWeeklySuccessKey"] == "2026-07-20_to_2026-07-26"


def test_schedule_helper_does_not_mutate_config() -> None:
    schedule = _schedule()
    result = _run(
        "const K=require('./client/knowledge_schedule');"
        f"const s={json.dumps(schedule)};const before=JSON.stringify(s);"
        "K.dueRuns(new Date('2026-07-24T23:45:00'),new Date('2026-07-24T23:44:00'),s);"
        "K.markSuccessful(s,{cadence:'daily',scheduleKey:'2026-07-24',state:'completed'});"
        "console.log(JSON.stringify({unchanged:before===JSON.stringify(s)}));"
    )
    assert result["unchanged"] is True
