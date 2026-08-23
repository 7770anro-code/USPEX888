"""Календарь «Успех 888»: темы на ночь, без секретов и без фото людей."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from presets import PRESETS

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
PLATFORMS = ("tiktok", "instagram")
SLOT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
MSK = timezone(timedelta(hours=3))


class CalendarError(ValueError):
    """Календарь битый — пайплайн не стартует."""


@dataclass(frozen=True)
class Slot:
    id: str
    weekdays: tuple[str, ...]
    preset: str
    topic: str
    platforms: tuple[str, ...]
    quality: str = ""
    n_scenes: int | None = None
    enabled: bool = True


@dataclass(frozen=True)
class Calendar:
    name: str
    timezone: str
    owner_chat_id: int
    daily_budget_runway: int
    max_jobs: int
    quality_default: str
    watermark: bool
    slots: tuple[Slot, ...] = field(default_factory=tuple)
    path: str = ""


def resolve_tz(name: str):
    raw = (name or "Europe/Moscow").strip() or "Europe/Moscow"
    try:
        return ZoneInfo(raw)
    except ZoneInfoNotFoundError:
        return MSK


def today_in_tz(name: str, now: datetime | None = None) -> date:
    tz = resolve_tz(name)
    current = now.astimezone(tz) if now else datetime.now(tz)
    return current.date()


def weekday_key(day: date) -> str:
    return WEEKDAYS[day.weekday()]


def _as_int(value: Any, field: str, *, lo: int, hi: int, default: int | None = None) -> int:
    if value is None or value == "":
        if default is None:
            raise CalendarError(f"{field}: число обязательно")
        return default
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise CalendarError(f"{field}: нужно целое число") from exc
    if number < lo or number > hi:
        raise CalendarError(f"{field}: {number} вне диапазона {lo}…{hi}")
    return number


def _norm_weekdays(raw: Any) -> tuple[str, ...]:
    if raw in (None, "", "daily", ["daily"]):
        return WEEKDAYS
    if isinstance(raw, str):
        items = [p.strip().lower() for p in raw.split(",") if p.strip()]
    elif isinstance(raw, (list, tuple)):
        items = [str(p).strip().lower() for p in raw if str(p).strip()]
    else:
        raise CalendarError("weekdays: список или daily")
    if not items:
        raise CalendarError("weekdays: пусто")
    bad = [w for w in items if w not in WEEKDAYS]
    if bad:
        raise CalendarError(f"weekdays: неизвестно {bad}")
    seen: list[str] = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return tuple(seen)


def _norm_platforms(raw: Any) -> tuple[str, ...]:
    if raw in (None, "", "all"):
        return PLATFORMS
    if isinstance(raw, str):
        items = [p.strip().lower() for p in raw.split(",") if p.strip()]
    elif isinstance(raw, (list, tuple)):
        items = [str(p).strip().lower() for p in raw if str(p).strip()]
    else:
        raise CalendarError("platforms: список tiktok/instagram")
    bad = [p for p in items if p not in PLATFORMS]
    if bad:
        raise CalendarError(f"platforms: неизвестно {bad}")
    if not items:
        raise CalendarError("platforms: пусто")
    seen: list[str] = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return tuple(seen)


def parse_slot(raw: Any, index: int) -> Slot:
    if not isinstance(raw, dict):
        raise CalendarError(f"slots[{index}]: нужен объект")
    slot_id = str(raw.get("id") or "").strip().lower()
    if not SLOT_ID_RE.match(slot_id):
        raise CalendarError(f"slots[{index}].id: латиница/цифры/_/- , 2–64 символа")
    preset = str(raw.get("preset") or "").strip().lower()
    if preset not in PRESETS:
        raise CalendarError(f"slots[{index}].preset: нет пресета {preset!r}")
    topic = " ".join(str(raw.get("topic") or "").split())
    if len(topic) < 8 or len(topic) > 280:
        raise CalendarError(f"slots[{index}].topic: 8–280 символов")
    quality = str(raw.get("quality") or "").strip().lower()
    if quality and quality not in ("fast", "optimal"):
        raise CalendarError(f"slots[{index}].quality: fast или optimal")
    n_scenes = raw.get("n_scenes")
    scenes = None
    if n_scenes not in (None, ""):
        scenes = _as_int(n_scenes, f"slots[{index}].n_scenes", lo=1, hi=6)
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise CalendarError(f"slots[{index}].enabled: true/false")
    if raw.get("photo") or raw.get("photo_path") or raw.get("reference_image"):
        raise CalendarError(
            f"slots[{index}]: ночной пайплайн без фото людей — уберите photo/photo_path"
        )
    return Slot(
        id=slot_id,
        weekdays=_norm_weekdays(raw.get("weekdays", raw.get("weekday"))),
        preset=preset,
        topic=topic,
        platforms=_norm_platforms(raw.get("platforms")),
        quality=quality,
        n_scenes=scenes,
        enabled=enabled,
    )


def parse_calendar(raw: Any, *, path: str = "") -> Calendar:
    if not isinstance(raw, dict):
        raise CalendarError("корень календаря — объект JSON")
    slots_raw = raw.get("slots")
    if not isinstance(slots_raw, list) or not slots_raw:
        raise CalendarError("slots: нужен непустой список")
    if len(slots_raw) > 50:
        raise CalendarError("slots: максимум 50")
    slots = [parse_slot(item, i) for i, item in enumerate(slots_raw)]
    ids = [s.id for s in slots]
    dupes = sorted({sid for sid in ids if ids.count(sid) > 1})
    if dupes:
        raise CalendarError(f"повторяющиеся id: {dupes}")
    quality_default = str(raw.get("quality_default") or "fast").strip().lower()
    if quality_default not in ("fast", "optimal"):
        raise CalendarError("quality_default: fast или optimal")
    return Calendar(
        name=str(raw.get("name") or "Успех 888").strip() or "Успех 888",
        timezone=str(raw.get("timezone") or "Europe/Moscow").strip() or "Europe/Moscow",
        owner_chat_id=_as_int(raw.get("owner_chat_id"), "owner_chat_id", lo=0, hi=10**12, default=0),
        daily_budget_runway=_as_int(
            raw.get("daily_budget_runway"), "daily_budget_runway", lo=1, hi=50_000, default=400
        ),
        max_jobs=_as_int(raw.get("max_jobs"), "max_jobs", lo=1, hi=10, default=3),
        quality_default=quality_default,
        watermark=bool(raw.get("watermark", False)),
        slots=tuple(slots),
        path=path,
    )


def load_calendar(path: Path | str) -> Calendar:
    file = Path(path)
    try:
        text = file.read_text(encoding="utf-8")
    except OSError as exc:
        raise CalendarError(f"не читается календарь {file}: {exc}") from exc
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CalendarError(f"календарь не JSON: {exc}") from exc
    return parse_calendar(raw, path=str(file))


def slots_for_day(calendar: Calendar, day: date) -> list[Slot]:
    key = weekday_key(day)
    return [slot for slot in calendar.slots if slot.enabled and key in slot.weekdays]
