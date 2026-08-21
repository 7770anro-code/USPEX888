"""Конфиг VideoBot. Секреты только из окружения / .env, в лог не пишем."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

log = logging.getLogger("videobot")

# Cursor-секрет USPEX/VideoBot иногда лежит как "XAI_API_KEY=xai-...":
# снаружи 96 символов без префикса xai-, внутри после "=" — нормальный ключ длиной 84.
XAI_WRAP_PREFIX = "XAI_API_KEY="
XAI_KEY_PREFIX = "xai-"
XAI_KEY_LEN = 84


def _ascii_secret(raw: str) -> str:
    raw = (raw or "").strip().strip('"').strip("'")
    return "".join(ch for ch in raw if 33 <= ord(ch) <= 126)


def _clean(name: str) -> str:
    return _ascii_secret(os.getenv(name, "") or "")


def unwrap_xai_api_key(raw: str) -> tuple[str, str]:
    """Вернуть (чистый ключ, текст ошибки). Ошибка пустая, если ключ годный."""
    cleaned = _ascii_secret(raw)
    if not cleaned:
        return "", "XAI_API_KEY_NEW пустой"
    if cleaned.startswith(XAI_WRAP_PREFIX):
        cleaned = _ascii_secret(cleaned.split("=", 1)[1])
    prefix = cleaned[:4] if cleaned else ""
    if not cleaned.startswith(XAI_KEY_PREFIX) or len(cleaned) != XAI_KEY_LEN:
        return "", (
            "XAI_API_KEY_NEW после unwrap не похож на inference-ключ: "
            f"длина {len(cleaned)} (нужно {XAI_KEY_LEN}), "
            f"префикс {prefix!r} (нужно {XAI_KEY_PREFIX!r}). "
            "В секрете Cursor должно быть либо xai-..., либо обёртка XAI_API_KEY=xai-..."
        )
    return cleaned, ""


def _load_xai_api_key() -> tuple[str, str]:
    raw = os.getenv("XAI_API_KEY_NEW", "") or ""
    if not _ascii_secret(raw):
        return "", ""
    key, err = unwrap_xai_api_key(raw)
    if err:
        log.error("%s", err)
        return "", err
    log.info("XAI_API_KEY_NEW ok: len=%s prefix=%s", len(key), key[:4])
    return key, ""


def _require(name: str) -> str:
    value = _clean(name)
    if not value:
        raise RuntimeError(f"Нет переменной окружения {name}")
    return value


XAI_API_KEY_NEW, XAI_API_KEY_ERROR = _load_xai_api_key()
ELEVENLABS_API_KEY = _clean("ELEVENLABS_API_KEY")
RUNWAY_API_KEY = _clean("RUNWAY_API_KEY")
VIDEOBOT_TELEGRAM_TOKEN = _clean("VIDEOBOT_TELEGRAM_TOKEN")

XAI_MODEL = _clean("XAI_MODEL") or "grok-4-1-fast-non-reasoning"
XAI_FALLBACK_MODEL = _clean("XAI_FALLBACK_MODEL") or "grok-4.5"

ELEVENLABS_VOICE_ID = _clean("ELEVENLABS_VOICE_ID") or "EXAVITQu4vr4xnSDxMaL"  # Sarah, MVP без выбора голоса
ELEVENLABS_MODEL_ID = _clean("ELEVENLABS_MODEL_ID") or "eleven_multilingual_v2"

# gen4.5 — text-to-video. gen4_turbo — только image-to-video, на T2V не отправляем.
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
    missing = [name for name, value in zip(names, values) if not value]
    if XAI_API_KEY_ERROR:
        missing = [n for n in missing if n != "XAI_API_KEY_NEW"]
    return missing


def require_all() -> None:
    if XAI_API_KEY_ERROR:
        raise RuntimeError(XAI_API_KEY_ERROR)
    missing = missing_secrets()
    if missing:
        raise RuntimeError("Нет секретов: " + ", ".join(missing))
