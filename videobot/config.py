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
# Google AI Studio — Nano Banana (gemini-2.5-flash-image). Не Runway GEMINI.
# Без ключа своё фото идёт в I2V как есть. Не входит в missing_secrets().
GEMINI_API_KEY = _clean("GEMINI_API_KEY")
GEMINI_IMAGE_MODEL = _clean("GEMINI_IMAGE_MODEL") or "gemini-2.5-flash-image"

XAI_MODEL = _clean("XAI_MODEL") or "grok-4.5"
XAI_FALLBACK_MODEL = _clean("XAI_FALLBACK_MODEL") or "grok-4-1-fast-non-reasoning"
# Идеи и сценарии — сильная модель. XAI_MODEL на проде может быть дешёвым fast; это её не подменяет.
XAI_CREATIVE_MODEL = _clean("XAI_CREATIVE_MODEL") or "grok-4.5"


def xai_creative_models() -> list[str]:
    """grok-4.5 первым для идей/сценариев, даже если XAI_MODEL = fast."""
    out: list[str] = []
    for name in (XAI_CREATIVE_MODEL, "grok-4.5", XAI_MODEL, XAI_FALLBACK_MODEL):
        clean = (name or "").strip()
        if clean and clean not in out:
            out.append(clean)
    return out

ELEVENLABS_VOICE_ID = _clean("ELEVENLABS_VOICE_ID") or "EXAVITQu4vr4xnSDxMaL"  # Sarah
ELEVENLABS_MODEL_ID = _clean("ELEVENLABS_MODEL_ID") or "eleven_multilingual_v2"

# gen4.5 — дефолт видео. veo3.1 / seedance2_5 / gemini_image3_pro — тот же ключ,
# только через RUNWAY_MODEL / RUNWAY_STILL_MODEL (A/B не вывел их в UI по умолчанию).
RUNWAY_MODEL = _clean("RUNWAY_MODEL") or "gen4.5"
# Пусто = still из пресета качества (обычно gen4_image).
RUNWAY_STILL_MODEL = _clean("RUNWAY_STILL_MODEL")
RUNWAY_RATIO = _clean("RUNWAY_RATIO") or "720:1280"
RUNWAY_VERSION = _clean("RUNWAY_VERSION") or "2024-11-06"
RUNWAY_POLL_SEC = float(_clean("RUNWAY_POLL_SEC") or "5")
RUNWAY_TIMEOUT_SEC = float(_clean("RUNWAY_TIMEOUT_SEC") or "720")
# Опционально: POST /v1/generate/video с configId вместо захардкоженной модели.
# По умолчанию выкл — прямой gen4.5 / gen4_turbo. Конфиг создаёт владелец на
# https://dev.runwayml.com/model-routers (slug в RUNWAY_ROUTER_CONFIG_ID).
RUNWAY_USE_MODEL_ROUTER = (_clean("RUNWAY_USE_MODEL_ROUTER") or "0").lower() in (
    "1",
    "true",
    "yes",
    "on",
)
RUNWAY_ROUTER_CONFIG_ID = _clean("RUNWAY_ROUTER_CONFIG_ID")
HTTP_RETRIES = int(_clean("HTTP_RETRIES") or "4")
KEEP_FAILED_DIR = (_clean("KEEP_FAILED_DIR") or "1").lower() in ("1", "true", "yes", "on")
BURN_SUBTITLES = (_clean("BURN_SUBTITLES") or "1").lower() in ("1", "true", "yes", "on")
DEFAULT_STYLE = _clean("DEFAULT_STYLE") or "cinematic"
WATERMARK_TEXT = _clean("WATERMARK_TEXT") or "VideoBot"
WATERMARK_LOGO = _clean("WATERMARK_LOGO")

WORK_DIR = _clean("WORK_DIR") or "/tmp/videobot"
DATA_DIR = _clean("VIDEOBOT_DATA_DIR") or str(Path(__file__).resolve().parent / "data")

VIDEOS_PER_NIGHT = max(1, min(48, int(_clean("VIDEOS_PER_NIGHT") or "3")))
NIGHT_IDEAS_PER_NIGHT = max(5, min(10, int(_clean("NIGHT_IDEAS_PER_NIGHT") or "8")))
NIGHT_INTERVAL_MINUTES = max(15, min(24 * 60, int(_clean("NIGHT_INTERVAL_MINUTES") or "90")))
NIGHT_BATCH_PER_TICK = max(1, min(VIDEOS_PER_NIGHT, int(_clean("NIGHT_BATCH_PER_TICK") or "1")))
NIGHT_BACKGROUND = (_clean("NIGHT_BACKGROUND") or "1").lower() in ("1", "true", "yes", "on")
NIGHT_STARTUP_DELAY_SEC = max(5, min(300, int(_clean("NIGHT_STARTUP_DELAY_SEC") or "45")))
NIGHT_RUNWAY_DAILY_BUDGET = max(0, int(_clean("NIGHT_RUNWAY_DAILY_BUDGET") or "0"))
NIGHT_AUTOPOST = (_clean("NIGHT_AUTOPOST") or "0").lower() in ("1", "true", "yes", "on")
NIGHT_REQUIRE_CONFIRM = (_clean("NIGHT_REQUIRE_CONFIRM") or "1").lower() in ("1", "true", "yes", "on")
NIGHT_MODERATION_STOP = max(1, int(_clean("NIGHT_MODERATION_STOP") or "3"))
NIGHT_OWNER_CHAT_ID = int(_clean("NIGHT_OWNER_CHAT_ID") or "0")
NIGHT_STALE_MINUTES = max(10, int(_clean("NIGHT_STALE_MINUTES") or "40"))
NIGHT_POST_PAUSE_MIN = max(0, int(_clean("NIGHT_POST_PAUSE_MIN") or "90"))
NIGHT_POST_PAUSE_MAX = max(NIGHT_POST_PAUSE_MIN, int(_clean("NIGHT_POST_PAUSE_MAX") or "240"))
NIGHT_TIKTOK_MODE = (_clean("NIGHT_TIKTOK_MODE") or "inbox").strip().lower()
NIGHT_PUBLIC_VIDEO_BASE_URL = _clean("NIGHT_PUBLIC_VIDEO_BASE_URL")
NIGHT_GRAPH_VERSION = _clean("NIGHT_GRAPH_VERSION") or "v21.0"
NIGHT_OUTBOX = _clean("NIGHT_OUTBOX") or str(Path(DATA_DIR) / "outbox")
NIGHT_DEDUP_DAYS = max(7, min(30, int(_clean("NIGHT_DEDUP_DAYS") or "21")))


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


def runway_model_router_enabled() -> bool:
    """Роутер только если флаг и slug конфига оба заданы. Иначе прямой вызов модели."""
    return bool(RUNWAY_USE_MODEL_ROUTER and RUNWAY_ROUTER_CONFIG_ID)


def missing_render_secrets() -> list[str]:
    names = ["XAI_API_KEY_NEW", "ELEVENLABS_API_KEY", "RUNWAY_API_KEY"]
    values = [XAI_API_KEY_NEW, ELEVENLABS_API_KEY, RUNWAY_API_KEY]
    missing = [name for name, value in zip(names, values) if not value]
    if XAI_API_KEY_ERROR and "XAI_API_KEY_NEW" not in missing:
        missing.insert(0, "XAI_API_KEY_NEW")
    return missing
