"""Validated PC-local calendar rules; delivery remains owned by WorkQueue."""
from __future__ import annotations

import calendar
from datetime import datetime, timedelta
import math
import re

KINDS = {'once', 'daily', 'weekdays', 'weekly', 'monthly', 'interval'}


def _wall(value, label='실행 시간'):
    if not isinstance(value, str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value):
        raise ValueError(f'{label}은 HH:MM 형식으로 선택해 주세요.')
    return value


def _minutes(value):
    hour, minute = map(int, value.split(':'))
    return hour * 60 + minute


def timing(kind, wall_time, weekdays=None, *, day_of_month=None,
           interval_minutes=None, start_time=None, end_time=None):
    if not isinstance(kind, str) or kind not in KINDS:
        raise ValueError('한 번, 평일, 매일, 매주, 매월 또는 간격 반복을 선택해 주세요.')
    days, month_day, interval, start, end = [], None, None, None, None
    if kind == 'interval':
        if type(interval_minutes) is not int or not 1 <= interval_minutes <= 1440:
            raise ValueError('반복 간격은 합계 1분~24시간의 정수로 입력해 주세요.')
        start, end = _wall(start_time, '시작 시간'), _wall(end_time, '종료 시간')
        if start >= end:
            raise ValueError('종료 시간은 시작 시간보다 늦어야 합니다. 자정을 넘는 구간은 지원하지 않습니다.')
        interval, wall_time = interval_minutes, start
    else:
        wall_time = _wall(wall_time)
        if kind == 'weekly':
            if (not isinstance(weekdays, list) or not weekdays or len(weekdays) > 7
                    or any(type(day) is not int or day not in range(7) for day in weekdays)):
                raise ValueError('예약할 요일을 선택해 주세요.')
            days = sorted(set(weekdays))
        elif kind == 'weekdays':
            days = list(range(5))
        elif kind == 'monthly':
            month_day = 1 if day_of_month is None else day_of_month
            if type(month_day) is not int or not 1 <= month_day <= 31:
                raise ValueError('매월 실행할 날짜는 1~31일 중 선택해 주세요.')
    return {'kind': kind, 'time': wall_time, 'weekdays': days, 'dayOfMonth': month_day,
            'intervalMinutes': interval, 'startTime': start, 'endTime': end}


def next_after(rule, now):
    """Find one future slot without iterating through missed occurrences."""
    if type(now) not in (int, float) or not math.isfinite(now):
        raise ValueError('예약 시각을 확인해 주세요.')
    if rule['kind'] == 'once':
        return None
    local = datetime.fromtimestamp(now)
    kind = rule['kind']
    hour, minute = map(int, rule['time'].split(':'))
    if kind == 'monthly':
        for offset in range(13):
            year, month = divmod(local.year * 12 + local.month - 1 + offset, 12)
            month += 1
            day = rule.get('dayOfMonth') or 1
            if day > calendar.monthrange(year, month)[1]:
                continue  # A missing 29th/30th/31st is skipped, never shifted.
            candidate = datetime(year, month, day, hour, minute)
            stamp = candidate.timestamp()
            if stamp > now:
                return stamp
    elif kind == 'interval':
        start_minutes, end_minutes = _minutes(rule['startTime']), _minutes(rule['endTime'])
        interval = rule['intervalMinutes']
        for offset in range(2):
            midnight = (local + timedelta(days=offset)).replace(hour=0, minute=0, second=0, microsecond=0)
            start = midnight + timedelta(minutes=start_minutes)
            elapsed = (local - start).total_seconds()
            step = max(0, math.floor(elapsed / (interval * 60)) + 1) if offset == 0 else 0
            # Normally one candidate; only a PC-local clock transition can make
            # an otherwise future wall-clock slot map into the past.
            while start_minutes + step * interval < end_minutes:
                candidate = start + timedelta(minutes=step * interval)
                stamp = candidate.timestamp()
                if stamp > now:
                    return stamp
                step += 1
    else:
        for offset in range(8):
            candidate = (local + timedelta(days=offset)).replace(hour=hour, minute=minute, second=0, microsecond=0)
            if kind in {'weekly', 'weekdays'} and candidate.weekday() not in rule['weekdays']:
                continue
            stamp = candidate.timestamp()
            if stamp > now:
                return stamp
    raise ValueError('다음 예약 시각을 확인하지 못했습니다.')


def first_run(schedule, now):
    if schedule.get('kind') == 'once':
        return schedule.get('runAt')
    rule = timing(schedule.get('kind'), schedule.get('time'), schedule.get('weekdays'),
                  day_of_month=schedule.get('dayOfMonth'), interval_minutes=schedule.get('intervalMinutes'),
                  start_time=schedule.get('startTime'), end_time=schedule.get('endTime'))
    return next_after(rule, now)
