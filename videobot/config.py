"""Конфиг VideoBot. Секреты только из окружения / .env, в лог не пишем."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")


def _clean(name: str) -> str:
    raw = os.getenv(name, "") or ""
    return "".join(ch for ch in raw if 32 < ord(ch) < 127).strip().strip('"').strip("'")


def _require(name: str) -> str:
    value = _clean(name)
    if not value:
        raise RuntimeError(f"Нет переменной окружения {name}")
    return value


XAI_API_KEY_NEW = _clean("XAI_API_KEY_NEW")
ELEVENLABS_API_KEY = _clean("ELEVENLABS_API_KEY")
RUNWAY_API_KEY = _clean("RUNWAY_API_KEY")
VIDEOBOT_TELEGRAM_TOKEN = _clean("VIDEOBOT_TELEGRAM_TOKEN")

XAI_MODEL = _clean("XAI_MODEL") or "grok-4-1-fast-non-reasoning"
XAI_FALLBACK_MODEL = _clean("XAI_FALLBACK_MODEL") or "grok-4.5"

ELEVENLABS_VOICE_ID = _clean("ELEVENLABS_VOICE_ID") or "EXAVITQu4vr4xnSDxMaL"
ELEVENLABS_MODEL_ID = _clean("ELEVENLABS_MODEL_ID") or "eleven_flash_v2_5"

# gen4.5 — самый дешёвый нативный text-to-video в Runway API.
# gen4_turbo дешевле, но это image-to-video (нужен кадр).
RUNWAY_MODEL = _clean("RUNWAY_MODEL") or "gen4.5"
RUNWAY_RATIO = _clean("RUNWAY_RATIO") or "1280:720"
RUNWAY_VERSION = _clean("RUNWAY_VERSION") or "2024-11-06"
RUNWAY_POLL_SEC = float(_clean("RUNWAY_POLL_SEC") or "5")
RUNWAY_TIMEOUT_SEC = float(_clean("RUNWAY_TIMEOUT_SEC") or "720")

WORK_DIR = _clean("WORK_DIR") or "/tmp/videobot"


def missing_secrets() -> list[str]:
    names = [
        "XAI_API_KEY_NEW",
        "ELEVENLABS_API_KEY",
        "RUNWAY_API_KEY",
        "VIDEOBOT_TELEGRAM_TOKEN",
    ]
    values = [
        XAI_API_KEY_NEW,
        ELEVENLABS_API_KEY,
        RUNWAY_API_KEY,
        VIDEOBOT_TELEGRAM_TOKEN,
    ]
    return [name for name, value in zip(names, values) if not value]


def require_all() -> None:
    missing = missing_secrets()
    if missing:
        raise RuntimeError("Нет секретов: " + ", ".join(missing))
