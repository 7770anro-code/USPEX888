"""Живой статус съёмки для кнопки «Обновить статус».

Прогресс Runway берём только из GET /v1/tasks/{id}:
официальная схема 2024-11-06 — у RUNNING поле progress number 0…1.
Номера кадра в схеме нет — не выдумываем.
GET не создаёт задачу и не тратит кредиты. Не чаще раза в 5 с на task_id
(как просит Runway).
"""

from __future__ import annotations

import contextvars
import threading
import time
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

_CURRENT: contextvars.ContextVar[str | None] = contextvars.ContextVar("live_job", default=None)
_LOCK = threading.Lock()
_JOBS: dict[str, dict[str, Any]] = {}
_LAST_GET: dict[str, float] = {}
MIN_GET_SEC = 5.0

STAGE_SCRIPT = "script"
STAGE_STILL = "still"
STAGE_TTS = "tts"
STAGE_RUNWAY = "runway"
STAGE_MUX = "mux"
STAGE_DONE = "done"
STAGE_FAILED = "failed"

_STAGE_TITLE = {
    STAGE_SCRIPT: "Сценарий (Grok)",
    STAGE_STILL: "Первый кадр в Runway",
    STAGE_TTS: "Озвучка ElevenLabs",
    STAGE_RUNWAY: "Видео в Runway",
    STAGE_MUX: "Сборка финального файла ffmpeg",
    STAGE_DONE: "Готово",
    STAGE_FAILED: "Ошибка",
}


def job_key_manual(chat_id: int) -> str:
    return f"m{int(chat_id)}"


def job_key_night(job_id: int) -> str:
    return f"n{int(job_id)}"


def parse_callback_key(data: str) -> str | None:
    raw = (data or "").strip()
    if not raw.startswith("live:"):
        return None
    key = raw[5:]
    if not key or len(key) > 24:
        return None
    if key[0] not in ("m", "n"):
        return None
    rest = key[1:]
    if rest.startswith("-"):
        rest = rest[1:]
    if not rest.isdigit():
        return None
    return key


def live_kb(job_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить статус", callback_data=f"live:{job_key}")]
        ]
    )


def live_markup_dict(job_key: str) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": "🔄 Обновить статус", "callback_data": f"live:{job_key}"}]
        ]
    }


def current_key() -> str | None:
    return _CURRENT.get()


def job_scope(job_key: str):
    class _Scope:
        def __enter__(self) -> str:
            self._tok = _CURRENT.set(job_key)
            return job_key

        def __exit__(self, *exc: object) -> None:
            _CURRENT.reset(self._tok)

    return _Scope()


def start_job(
    job_key: str,
    *,
    chat_id: int,
    message_id: int = 0,
    title: str = "",
    scene_total: int = 0,
) -> None:
    snap = {
        "job_key": job_key,
        "chat_id": int(chat_id),
        "message_id": int(message_id or 0),
        "title": (title or "").strip()[:120],
        "stage": STAGE_SCRIPT,
        "label": "Пишу сценарий…",
        "scene_n": 0,
        "scene_total": int(scene_total or 0),
        "runway_task_id": "",
        "runway_kind": "",
        "runway_status": "",
        "runway_progress": None,
        "runway_frame": None,
        "done": False,
        "updated_at": time.time(),
    }
    with _LOCK:
        _JOBS[job_key] = snap


def set_message(job_key: str, message_id: int) -> None:
    with _LOCK:
        job = _JOBS.get(job_key)
        if job is not None:
            job["message_id"] = int(message_id)


def get_job(job_key: str) -> dict[str, Any] | None:
    with _LOCK:
        job = _JOBS.get(job_key)
        return dict(job) if job else None


def update_job(job_key: str | None = None, **fields: Any) -> None:
    key = job_key or current_key()
    if not key:
        return
    with _LOCK:
        job = _JOBS.get(key)
        if job is None:
            return
        for name, value in fields.items():
            if name not in job:
                continue
            if value is None and name not in ("runway_progress", "runway_frame"):
                continue
            job[name] = value
        job["updated_at"] = time.time()


def finish_job(job_key: str | None = None, *, failed: bool = False, label: str = "") -> None:
    key = job_key or current_key()
    if not key:
        return
    update_job(
        key,
        stage=STAGE_FAILED if failed else STAGE_DONE,
        label=label or ("Ошибка" if failed else "Готово"),
        done=True,
        runway_progress=None,
    )


def parse_runway_progress(raw: Any) -> float | None:
    """Только канон OpenAPI: number 0…1. Иначе None — не угадываем проценты."""
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if 0.0 <= value <= 1.0:
        return value
    return None


def parse_runway_frame(data: dict[str, Any] | None) -> int | None:
    """Кадра в официальной схеме GET /v1/tasks нет. Покажем, только если поле реально пришло."""
    if not isinstance(data, dict):
        return None
    for name in ("currentFrame", "frameIndex", "frame", "framesCompleted"):
        if name not in data:
            continue
        try:
            frame = int(data[name])
        except (TypeError, ValueError):
            continue
        if frame >= 0:
            return frame
    return None


def note_runway_task(task_id: str, *, kind: str = "") -> None:
    tid = (task_id or "").strip()
    if not tid:
        return
    update_job(
        runway_task_id=tid,
        runway_kind=(kind or "").strip(),
        runway_status="PENDING",
        runway_progress=None,
        runway_frame=None,
    )


def note_runway_poll(task_id: str, data: dict[str, Any]) -> None:
    if not isinstance(data, dict):
        return
    status = str(data.get("status") or "")
    progress = parse_runway_progress(data.get("progress")) if status.upper() == "RUNNING" else None
    update_job(
        runway_task_id=(task_id or "").strip() or None,
        runway_status=status,
        runway_progress=progress,
        runway_frame=parse_runway_frame(data),
    )


def allow_runway_get(task_id: str) -> bool:
    tid = (task_id or "").strip()
    if not tid:
        return False
    now = time.monotonic()
    with _LOCK:
        last = _LAST_GET.get(tid, 0.0)
        if now - last < MIN_GET_SEC:
            return False
        _LAST_GET[tid] = now
        return True


def format_status(snap: dict[str, Any] | None, *, stale_runway: bool = False) -> str:
    if not snap:
        return "Сейчас нет активной съёмки."
    title = snap.get("title") or ""
    stage = str(snap.get("stage") or "")
    scene_n = int(snap.get("scene_n") or 0)
    scene_total = int(snap.get("scene_total") or 0)
    lines = ["Съёмка" + (f": {title}" if title else "")]
    if scene_total:
        lines.append(f"Сцен в ролике: {scene_total}")
    lines.append("")
    order = (STAGE_SCRIPT, STAGE_STILL, STAGE_TTS, STAGE_RUNWAY, STAGE_MUX)
    reached = {
        STAGE_SCRIPT: 0,
        STAGE_STILL: 1,
        STAGE_TTS: 2,
        STAGE_RUNWAY: 3,
        STAGE_MUX: 4,
        STAGE_DONE: 5,
        STAGE_FAILED: 5,
    }.get(stage, 0)
    for i, name in enumerate(order):
        mark = "•"
        extra = ""
        if i < reached:
            mark = "✓"
        elif i == reached and stage not in (STAGE_DONE, STAGE_FAILED):
            mark = "→"
            extra = _stage_extra(snap, name)
        lines.append(f"{mark} {_STAGE_TITLE[name]}{extra}")
    if stage == STAGE_DONE:
        lines.append("✓ Готово")
    elif stage == STAGE_FAILED:
        err = str(snap.get("label") or "ошибка")
        lines.append(f"✗ {err}")
    else:
        label = str(snap.get("label") or "").strip()
        if label:
            lines.append("")
            lines.append(label)
    runway_line = _runway_line(snap, stale=stale_runway)
    if runway_line:
        lines.append("")
        lines.append(runway_line)
    if not snap.get("done"):
        lines.append("")
        lines.append(
            "Кнопка спрашивает GET /v1/tasks/{id} по уже сохранённому task_id. "
            "Новую задачу Runway не создаёт, кредиты не тратит."
        )
    return "\n".join(lines).strip()


def _stage_extra(snap: dict[str, Any], name: str) -> str:
    n = int(snap.get("scene_n") or 0)
    total = int(snap.get("scene_total") or 0)
    if name in (STAGE_TTS, STAGE_RUNWAY) and n and total:
        return f" · сцена {n} из {total}"
    return ""


def _runway_line(snap: dict[str, Any], *, stale: bool = False) -> str:
    status = str(snap.get("runway_status") or "").upper()
    if not status and not snap.get("runway_task_id"):
        return ""
    bits = ["Runway:"]
    if status:
        human = {
            "PENDING": "в очереди",
            "THROTTLED": "ждёт слот (THROTTLED, это не новая задача)",
            "RUNNING": "рендерит",
            "SUCCEEDED": "клип готов",
            "FAILED": "задача FAILED",
            "CANCELLED": "отменено",
            "CANCELED": "отменено",
        }.get(status, status)
        bits.append(human)
    progress = snap.get("runway_progress")
    if progress is not None and status == "RUNNING":
        bits.append(f"{int(round(float(progress) * 100))}% этого клипа")
    frame = snap.get("runway_frame")
    if frame is not None:
        bits.append(f"кадр {int(frame)}")
    if stale:
        bits.append("(повторный GET слишком частый — Runway просит ≥5 с, показал сохранённое)")
    return " ".join(bits)


def reset_for_tests() -> None:
    with _LOCK:
        _JOBS.clear()
        _LAST_GET.clear()
    try:
        _CURRENT.set(None)
    except Exception:
        pass
