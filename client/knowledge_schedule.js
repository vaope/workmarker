'use strict';

function localDateString(value) {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, '0');
  const day = String(value.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function localMidnight(value) {
  return new Date(value.getFullYear(), value.getMonth(), value.getDate());
}

function addDays(value, count) {
  const result = localMidnight(value);
  result.setDate(result.getDate() + count);
  return result;
}

function mondayOfLocalWeek(value) {
  const result = localMidnight(value);
  const day = result.getDay() || 7;
  result.setDate(result.getDate() - day + 1);
  return result;
}

function scheduledAt(day, hhmm) {
  const [hour, minute] = String(hhmm || '').split(':').map(Number);
  if (!Number.isInteger(hour) || !Number.isInteger(minute)
      || hour < 0 || hour > 23 || minute < 0 || minute > 59) {
    return null;
  }
  return new Date(day.getFullYear(), day.getMonth(), day.getDate(), hour, minute, 0, 0);
}

function dueRuns(now, startedAt, schedule) {
  if (!(now instanceof Date) || Number.isNaN(now.getTime())) return [];
  if (!(startedAt instanceof Date) || Number.isNaN(startedAt.getTime())) return [];
  const config = schedule || {};
  const due = [];
  const today = localDateString(now);
  const todayStart = localMidnight(now);
  const tomorrowStart = addDays(todayStart, 1);
  const dailyAt = scheduledAt(now, config.dailyTime || '23:30');
  if (config.dailyEnabled && dailyAt && now >= dailyAt
      && config.lastDailySuccessDate !== today) {
    due.push({
      cadence: 'daily', scheduleKey: today, dateFrom: today, dateTo: today,
      rangeStartUtc: todayStart.toISOString(), rangeEndUtc: tomorrowStart.toISOString(),
    });
  }

  const weekStart = mondayOfLocalWeek(now);
  const weekEnd = addDays(weekStart, 6);
  const weekKey = `${localDateString(weekStart)}_to_${localDateString(weekEnd)}`;
  const weekday = Number(config.weeklyDay == null ? 5 : config.weeklyDay);
  const offset = weekday === 0 ? 6 : weekday - 1;
  const weeklyAt = scheduledAt(addDays(weekStart, offset), config.weeklyTime || '18:00');
  if (config.weeklyEnabled && weeklyAt && now >= weeklyAt
      && config.lastWeeklySuccessKey !== weekKey) {
    due.push({
      cadence: 'weekly', scheduleKey: weekKey,
      dateFrom: localDateString(weekStart), dateTo: localDateString(weekEnd),
      rangeStartUtc: weekStart.toISOString(), rangeEndUtc: addDays(weekEnd, 1).toISOString(),
    });
  }
  return due;
}

function markSuccessful(schedule, run) {
  const result = { ...(schedule || {}) };
  if (!run || run.state !== 'completed') return result;
  if (run.cadence === 'daily') result.lastDailySuccessDate = run.scheduleKey;
  if (run.cadence === 'weekly') result.lastWeeklySuccessKey = run.scheduleKey;
  result.lastRunStatus = JSON.stringify({
    cadence: run.cadence, scheduleKey: run.scheduleKey, state: run.state,
  });
  return result;
}

module.exports = { dueRuns, markSuccessful };
