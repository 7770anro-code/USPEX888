#!/usr/bin/env python3
"""CLI ночного пайплайна «Успех 888».

  python night_run.py              # shadow: план + пакеты без съёмки
  python night_run.py --render     # съёмка (нужны ключи и NIGHT_RENDER=1 или флаг)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import date, datetime
from pathlib import Path

import config
from night import calendar_path, run_night
from nightcal import CalendarError, load_calendar

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("videobot.night")


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    return datetime.strptime(raw, "%Y-%m-%d").date()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Успех 888 — ночной пайплайн VideoBot")
    parser.add_argument("--calendar", default="", help="путь к calendar.json")
    parser.add_argument("--date", default="", help="YYYY-MM-DD, иначе сегодня в TZ календаря")
    parser.add_argument(
        "--render",
        action="store_true",
        help="снимать ролики (платные API). По умолчанию только план.",
    )
    parser.add_argument("--force", action="store_true", help="переснять уже готовые слоты дня")
    parser.add_argument("--no-telegram", action="store_true", help="не слать утренний отчёт")
    parser.add_argument("--outbox", default="", help="куда класть пакеты")
    return parser


async def _amain(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cal_path = Path(args.calendar) if args.calendar else calendar_path()
    try:
        calendar = load_calendar(cal_path)
    except CalendarError as exc:
        log.error("%s", exc)
        print(f"календарь: {exc}", file=sys.stderr)
        return 2
    # Снимаем только по явному --render или NIGHT_RENDER=1. Иначе shadow.
    want_render = bool(args.render or config.NIGHT_RENDER)
    report = await run_night(
        calendar=calendar,
        day=_parse_date(args.date or None),
        render=want_render,
        force=bool(args.force),
        notify=not bool(args.no_telegram),
        outbox=Path(args.outbox) if args.outbox else None,
    )
    print(report["text"], end="")
    return 0 if int(report.get("failed") or 0) == 0 else 1


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_amain(argv))


if __name__ == "__main__":
    raise SystemExit(main())
