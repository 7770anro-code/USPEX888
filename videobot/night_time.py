from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore
    ZoneInfoNotFoundError = Exception  # type: ignore


def today_msk() -> date:
    try:
        tz = ZoneInfo("Europe/Moscow") if ZoneInfo else timezone(timedelta(hours=3))
    except ZoneInfoNotFoundError:
        tz = timezone(timedelta(hours=3))
    return datetime.now(tz).date()
