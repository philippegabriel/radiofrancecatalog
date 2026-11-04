"""Inclusive Radio France catalogue dates interpreted in Europe/Paris."""

from datetime import date, datetime, time, timedelta
import re
from typing import TypedDict
from zoneinfo import ZoneInfo


class DateRange(TypedDict):
    from_date: str | None
    to_date: str | None
    start_timestamp: int | None
    end_timestamp: int | None


def date_range(from_date: str | None = None, to_date: str | None = None) -> DateRange:
    """Validate ISO calendar dates and return inclusive-start/exclusive-end UTC seconds.

    Blank inputs mean unbounded. Build the next local midnight before converting
    the inclusive end date, so daylight-saving days need not be 24 hours long.
    """
    days: list[date | None] = []
    for value in (from_date, to_date):
        if not value:
            days.append(None)
            continue
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise ValueError('Dates must use YYYY-MM-DD')
        days.append(date.fromisoformat(value))
    first, last = days
    if first is not None and last is not None and first > last:
        raise ValueError('From date must be on or before to date')
    paris = ZoneInfo('Europe/Paris')
    start = int(datetime.combine(first, time.min, paris).timestamp()) if first else None
    end = int(datetime.combine(last + timedelta(days=1), time.min, paris).timestamp()) if last else None
    return {'from_date': from_date or None, 'to_date': to_date or None,
        'start_timestamp': start, 'end_timestamp': end}
