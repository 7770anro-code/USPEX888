"""Telegram-кнопки мультсериала. Только владелец ночного контура."""

from __future__ import annotations

import logging
from typing import Any

from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from joblock import JobLock
from pipeline import PipelineError
from presets import estimate_cost
from serial_plot import MAX_BATCH, N_SCENES, SERIAL_SLUG
from serial_render import apply_owner_note, ensure_default_serial, generate_episodes, status_text
from serial_store import get_serial, list_episodes

log = logging.getLogger("videobot.serial")


def serial_hub_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="▶️ Следующая серия", callback_data="serial:next")],
            [
                InlineKeyboardButton(text="📦 3 серии", callback_data="serial:b3"),
                InlineKeyboardButton(text="5", callback_data="serial:b5"),
                InlineKeyboardButton(text="7", callback_data="serial:b7"),
            ],
            [InlineKeyboardButton(text="✏️ Правка сюжета", callback_data="serial:note")],
            [InlineKeyboardButton(text="📋 Статус", callback_data="serial:status")],
            [InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:home")],
        ]
    )


def hub_intro() -> str:
    serial = ensure_default_serial()
    return (
        "📺 Мультсериал «Гибриды» — отдельный TikTok-слот NIGHT_ACC4.\n\n"
        "Reveal-формат: пары фруктов «рожают» гибрид-ребёнка или стилизованная машина "
        "без логотипа — машину-малыша. Каждая серия ПРОДОЛЖАЕТ предыдущую "
        "(running summary + сид арки, не вся история в промпте).\n"
        "Чужие ролики не скачиваю — только синтетика через тот же Runway-пайплайн.\n\n"
        "▶️ Следующая серия — одно продолжение.\n"
        "📦 Пакет — сразу 3/5/7 серий, даты по одной в день, пост как обычно: да/нет в /night.\n"
        "✏️ Правка — свободный текст, учту со следующей серии.\n\n"
        + status_text(serial)
    )


def _cost_line(n: int) -> str:
    cost = estimate_cost(n_scenes=N_SCENES, quality="fast", text="сериал", need_still=n == 1)
    per = int(cost.get("runway") or 0)
    return f"Оценка Runway ≈{per} кр/серия × {n} ≈ {per * n} кр (качество слота «Быстро»)."


async def start_serial_hub(message: Message) -> None:
    try:
        text = hub_intro()
    except Exception as exc:
        text = f"Сериал: {type(exc).__name__}: {exc}"
    await message.answer(text[:3900], reply_markup=serial_hub_kb())


def _format_done(rows: list[dict[str, Any]]) -> str:
    lines = ["Готово:"]
    for row in rows:
        lines.append(
            f"#{row.get('n')} «{row.get('title')}» → {row.get('run_date')} "
            f"(job {row.get('job_id')})"
        )
        if row.get("cliffhanger"):
            lines.append(f"  дальше: {row['cliffhanger']}")
    lines.append("Публикация: да/нет в /night в день серии (не сегодняшним пакетом разом).")
    return "\n".join(lines)


async def run_serial_batch(message: Message, count: int, *, busy) -> None:
    count = max(1, min(MAX_BATCH, int(count)))
    if busy.locked():
        await message.answer("⏳ Сейчас идёт другая съёмка. Нажми ещё раз, когда освобожусь.")
        return
    file_lock = JobLock()
    if not file_lock.acquire():
        await message.answer("⏳ Съёмка уже идёт в другом процессе.")
        return
    await busy.acquire()
    try:
        await message.answer(
            f"Снимаю {count} сер. мультсериала. Интернет не ищу. {_cost_line(count)}\n"
            "Это несколько минут на серию."
        )

        async def progress(text: str) -> None:
            try:
                await message.answer(text[:500])
            except Exception:
                pass

        rows = await generate_episodes(count, progress=progress)
        await message.answer(_format_done(rows)[:3900], reply_markup=serial_hub_kb())
        serial = get_serial(slug=SERIAL_SLUG)
        last = (list_episodes(int(serial["id"]), limit=1) if serial else None) or []
        if last and last[0].get("video_path"):
            from pathlib import Path

            from aiogram.types import FSInputFile

            path = Path(str(last[0]["video_path"]))
            if path.is_file():
                try:
                    await message.answer_video(
                        FSInputFile(path, filename=path.name),
                        caption=f"Последняя из пакета: #{last[0].get('n')} {last[0].get('title')}"[:900],
                    )
                except Exception:
                    pass
    except PipelineError as exc:
        log.warning("serial batch: %s | %s", exc.user_message, exc.detail)
        await message.answer(exc.user_message, reply_markup=serial_hub_kb())
    except Exception:
        log.exception("serial batch failed")
        await message.answer("Серия не собралась. Попробуй ещё раз или напиши правку сюжета.", reply_markup=serial_hub_kb())
    finally:
        file_lock.release()
        busy.release()
