"""Пайплайн: идея -> сценарий (Grok) -> TTS -> Runway T2V -> ffmpeg -> mp4."""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import shutil
import time
from pathlib import Path
from typing import Any, Callable

import aiohttp

import config

log = logging.getLogger("videobot")

ProgressCb = Callable[[str], Any]

XAI_CHAT_URL = "https://api.x.ai/v1/chat/completions"
XAI_RESPONSES_URL = "https://api.x.ai/v1/responses"

# Runway API (docs 2026-08-21): host + /v1/... ; X-Runway-Version обязателен.
RUNWAY_HOST = "https://api.dev.runwayml.com"
RUNWAY_VERSION = "2024-11-06"
RUNWAY_PROMPT_MAX = 1000
RUNWAY_DURATION_MIN = 2
RUNWAY_DURATION_MAX = 10
RUNWAY_T2V_MODELS = frozenset({"gen4.5", "veo3", "veo3.1", "veo3.1_fast", "seedance2", "seedance2_5"})
RUNWAY_I2V_MODELS = frozenset(
    {"gen4.5", "gen4_turbo", "seedance2", "seedance2_5", "veo3", "veo3.1", "veo3.1_fast"}
)
RUNWAY_VEO_MODELS = frozenset({"veo3", "veo3.1", "veo3.1_fast"})
RUNWAY_SEEDANCE_MODELS = frozenset({"seedance2", "seedance2_5", "seedance2_fast", "seedance2_mini"})
# Seedance 2.5 — ByteDance через тот же ключ. Photoreal-лицо в still: SAFETY.THIRD_PARTY.
# I2V first-frame + audio=false → INPUT_VALIDATION. Не в UI, не дефолт.
RUNWAY_GEMINI_IMAGE = frozenset({"gemini_image3_pro", "gemini_image3.1_flash"})
GEMINI_IMAGE_RATIO = {
    "720:1280": "768:1344",
    "1280:720": "1344:768",
    "960:960": "1024:1024",
}
GEMINI_PROMPT_MAX = 5500
RUNWAY_DONE_FAIL = frozenset({"FAILED", "CANCELED", "CANCELLED"})

# ElevenLabs: POST /v1/text-to-speech/{voice_id} → сырой audio/mpeg, не JSON.
ELEVEN_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"

SCRIPT_LOCK = (
    "continuity — ОДИН locked English look на ВСЕ клипы: clothes, location, lighting, color grade, "
    "visual style. No face, age, hair, eyes or likeness. No camera motion in continuity."
)

SCRIPT_SYSTEM_PHOTO = f"""Ты режиссёр вертикальных TikTok-роликов 30–60 секунд (кадр 9:16) по РЕАЛЬНОМУ фото человека.
Верни ТОЛЬКО JSON без markdown:

{{
  "title": "короткий заголовок",
  "continuity": "ONE locked English description for EVERY shot: clothes, location, lighting, color grade, visual style. No face, age, hair, eyes or likeness. No camera motion here. Must stay identical across shots.",
  "scenes": [
    {{
      "narration": "озвучка на языке пользователя",
      "visual_prompt": "English CAMERA AND ACTION ONLY, one short sentence. Soft only: subtle head turn, camera holds static, slow push-in, minimal body movement. Do NOT re-describe face, clothes, or location. No spin, dramatic, extreme close-up, energetic."
    }}
  ]
}}

Правила:
- {SCRIPT_LOCK} Это фото человека: мягкая камера обязательна, иначе плывёт лицо. continuity visual style — photoreal live-action (handheld phone / cinema camera), не CGI-пластик.
- visual_prompt сцены — только мягкое движение камеры и тела, без нового лица и локации.
- Сцен от 4 до 6. Каждая narration 18–28 слов (конкретная ситуация, конфликт или вопрос зрителю). Итого 40–60 секунд.
- Если дан готовый текст пользователя — режь ЕГО слова на сцены, не выдумывай новую речь.
- Без текста на экране, логотипов, знаменитостей, NSFW, watermark.
"""

SCRIPT_SYSTEM_SYNTH = f"""Ты режиссёр вертикальных TikTok-роликов 30–60 секунд (кадр 9:16). Только синтетика: выдуманный персонаж, графика, абстракция. Не фото реального человека.
Верни ТОЛЬКО JSON без markdown:

{{
  "title": "короткий заголовок",
  "continuity": "ONE locked English description for EVERY shot: clothes, location, lighting, color grade, visual style. No face, age, hair, eyes or likeness. No camera motion here. Must stay identical across shots.",
  "scenes": [
    {{
      "narration": "озвучка на языке пользователя, 18–28 слов",
      "visual_prompt": "English CAMERA AND ACTION, 1–2 sentences. Energy allowed: punch-in, whip pan, crash zoom, decisive blocking. Keep the SAME character/clothes/location from continuity."
    }}
  ]
}}

Правила:
- {SCRIPT_LOCK} Консистентность персонажа важнее трюка камеры. Новое лицо/локацию не вводить.
- Если стиль photoreal/cinematic/ad — это live-action пластина «снято камерой», не AI-smooth. Если cartoon/abstract — 3D/графика, без «shot on iPhone/ARRI».
- Камера и действие МОГУТ быть энергичными. Запрет soft-only / static / «только push-in» здесь НЕ действует (он только для режима с реальным фото).
- Сцен от 4 до 6. Каждая narration СТРОГО 18–28 слов. Короче 18 — брак, перепиши.
- Каждая сцена: конкретная ситуация, конфликт или прямой вопрос зрителю. Не голая метафора («лестница = прогресс») без действия.
- Призыв к действию не только в последней сцене: минимум ещё в одной ранней (1–3).
- Если в брифе есть ХУК — narration сцены 1 буквально начинается с этой фразы или её прямым усилением. Первая секунда = цепляющая фраза, не нейтральное описание кадра.
- Если дан готовый текст пользователя — режь ЕГО слова на сцены, не выдумывай новую речь.
- Без текста на экране, логотипов, знаменитостей, NSFW, watermark.
"""

# По умолчанию — синтетика (автоконтур). Фото-режим берёт SCRIPT_SYSTEM_PHOTO.
SCRIPT_SYSTEM = SCRIPT_SYSTEM_SYNTH

STYLES = {
    "cinematic": (
        "photoreal live-action, shot on ARRI Alexa Mini with 35mm anamorphic lens, "
        "handheld micro-shake, organic film grain, creamy bokeh, natural motivated light, "
        "natural exposure no blown highlights, 24fps cadence, not CGI, not plastic skin"
    ),
    "ad": (
        "photoreal live-action product, shot on iPhone 15 Pro handheld, natural motion blur, "
        "real sensor noise, practical light, natural exposure, premium commercial, not CGI plastic"
    ),
    "cartoon": (
        "stylized 3D animation render, appealing shapes, painterly lighting, graphic look, "
        "not live-action footage, not iPhone, not ARRI, not documentary"
    ),
}

# Короткие LOOK-пакеты в visual (Grok может выкинуть стиль из continuity).
LOOK_ARRI = (
    "shot on ARRI Alexa Mini 35mm anamorphic, handheld micro-shake, organic grain, "
    "creamy bokeh, natural exposure, 24fps, not CGI plastic"
)
LOOK_PHONE = (
    "shot on iPhone 15 Pro handheld, natural motion blur, real sensor noise, "
    "natural exposure, not CGI plastic, not oversharpened"
)
LOOK_CARTOON = (
    "stylized 3D render, appealing shapes, painterly light, not live-action, "
    "not iPhone, not ARRI, not documentary footage"
)
# Жёсткая фиксация персонажа: still/фото уходит как promptImage (first),
# gen4.5 не принимает второй reference. Seedance I2V тоже не смешивает
# first-frame и reference в одном массиве — поэтому lock в тексте на каждую сцену.
CHARACTER_LOCK = (
    "same character as reference image, do not alter face, outfit, or visual style"
)

RATIO_PRESETS = {
    "9:16": "720:1280",
    "16:9": "1280:720",
    "1:1": "960:960",
}
RATIO_TO_ASPECT = {value: key for key, value in RATIO_PRESETS.items()}

RETRY_STATUSES = frozenset({429, 502, 503, 504})
CANCEL_ON_TIMEOUT = True


# Явные тексты при модерации Runway (docs 21.08.2026: FAILED + failureCode SAFETY.*).
RUNWAY_PERSON_MSG = (
    "Runway отклонил это фото (политика по реальным людям), "
    "попробуйте другое фото или текстовый режим."
)
RUNWAY_SAFETY_MSG = (
    "Runway не пропустил этот запрос по правилам контента. "
    "Измени текст или фото и попробуй ещё раз."
)
RUNWAY_CREDITS_MSG = (
    "На Runway закончились кредиты, пополните баланс и попробуйте снова. "
    "Прогресс сохранён: сценарий, озвучка и уже снятые сцены на месте. "
    "После пополнения нажмите «Продолжить съёмку» — Grok и ElevenLabs заново не спишем."
)

_PERSON_MOD_RE = re.compile(
    r"PUBLIC[_\s-]?FIGURE|LIKENESS|CELEBRITY|REAL PEOPLE|ANOTHER PERSON|"
    r"WITHOUT THEIR PERMISSION|SAFETY\.(INPUT|OUTPUT)\.(IMAGE|VIDEO|AUDIO)|"
    r"INPUT_PREPROCESSING\.SAFETY\.(IMAGE|VIDEO|AUDIO)|"
    r"\bFACES?\b|\bPEOPLE\b|\bPERSON\b(?!AL)",
    re.I,
)


class PipelineError(Exception):
    def __init__(self, user_message: str, detail: str = "", code: str = "") -> None:
        super().__init__(user_message)
        self.user_message = user_message
        self.detail = detail or user_message
        self.status: int | None = None
        self.code = code
        self.failure_code = ""


def is_runway_safety_fail(failure_code: str = "", detail: str = "") -> bool:
    blob = f"{failure_code} {detail}".upper()
    return "SAFETY" in blob or "CONTENT_MODERAT" in blob or "CONTENT MODERAT" in blob or "MODERATED" in blob


def is_runway_person_moderation(failure_code: str = "", detail: str = "") -> bool:
    blob = f"{failure_code} {detail}"
    return bool(_PERSON_MOD_RE.search(blob))


def is_runway_credits_fail(detail: str = "") -> bool:
    blob = (detail or "").lower()
    return (
        "enough credits" in blob
        or "insufficient credits" in blob
        or "out of credits" in blob
    )


def is_runway_user_facing(err: PipelineError) -> bool:
    code = getattr(err, "code", "") or ""
    return code.startswith("moderation") or code == "credits" or is_runway_credits_fail(err.detail)


def credits_error(detail: str, *, status: int | None = None) -> PipelineError:
    err = PipelineError(RUNWAY_CREDITS_MSG, detail, code="credits")
    err.status = status
    return err


def runway_fail_error(
    failure_code: str,
    detail: str,
    *,
    used_image: bool = False,
) -> PipelineError:
    """Понятный текст в чат вместо generic «ошибка» при FAILED модерации.

    У Runway третий сегмент failureCode часто врёт: SAFETY.INPUT.TEXT бывает
    и на картинке. Если в запрос уходил promptImage — любой SAFETY/moderation
    показываем как отказ по реальным людям (пункт 4 фактчека).
    """
    if is_runway_credits_fail(failure_code) or is_runway_credits_fail(detail):
        err = credits_error(detail)
    elif used_image and (
        is_runway_safety_fail(failure_code, detail) or is_runway_person_moderation(failure_code, detail)
    ):
        err = PipelineError(RUNWAY_PERSON_MSG, detail, code="moderation_person")
    elif is_runway_person_moderation(failure_code, detail):
        err = PipelineError(RUNWAY_PERSON_MSG, detail, code="moderation_person")
    elif is_runway_safety_fail(failure_code, detail):
        err = PipelineError(RUNWAY_SAFETY_MSG, detail, code="moderation")
    else:
        err = PipelineError("Runway не смог сгенерировать клип.", detail)
    err.failure_code = failure_code
    return err


def runway_content_moderation() -> dict[str, str]:
    # auto — дефолт API; low ослабляет фильтр знаменитостей, нам это не нужно.
    return {"publicFigureThreshold": "auto"}


def _clip(text: str, n: int = 400) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    return text if len(text) <= n else text[: n - 1] + "…"


async def _notify(progress: ProgressCb | None, text: str) -> None:
    if progress is None:
        return
    result = progress(text)
    if asyncio.iscoroutine(result):
        await result


def parse_script(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        raise PipelineError("Grok вернул пустой сценарий.")
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S | re.I)
    if fence:
        text = fence.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise PipelineError("Grok не вернул JSON-сценарий.", _clip(raw, 240))
        text = text[start : end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PipelineError("Не получилось разобрать сценарий от Grok.", str(exc)) from exc
    scenes = data.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise PipelineError("Не получилось понять сцены. Напиши текст чуть подробнее.")
    cleaned = []
    for scene in scenes[:MAX_SCENES]:
        if not isinstance(scene, dict):
            continue
        narration = str(scene.get("narration") or "").strip()
        visual = str(scene.get("visual_prompt") or scene.get("visualPrompt") or "").strip()
        if not narration:
            continue
        if not visual:
            visual = "slow cinematic camera move, keep identity unchanged"
        cleaned.append(
            {"narration": narration[:500], "visual_prompt": visual[:RUNWAY_PROMPT_MAX]}
        )
    if not cleaned:
        raise PipelineError("В тексте нет слов для озвучки. Напиши сценарий своими словами.")
    title = str(data.get("title") or "Ролик").strip()[:80] or "Ролик"
    continuity = str(
        data.get("continuity") or data.get("bible") or data.get("lock") or ""
    ).strip()
    if not continuity:
        continuity = cleaned[0]["visual_prompt"][:500]
    return {"title": title, "continuity": continuity, "scenes": cleaned}


def scene_durations(count: int) -> list[int]:
    n = max(1, min(int(count or 1), 6))
    return [10] * n


def target_scene_count(text: str) -> int:
    words = len(re.findall(r"\w+", text or "", flags=re.U))
    if words < 50:
        return 4
    if words < 110:
        return 5
    return 6


# 10с клип + atempo ≤ ~1.8, чтобы -shortest не резал хвост речи (лимит ffmpeg 2.0).
CLIP_SPEECH_BUDGET_SEC = 18.0
MAX_SCENES = 6
SPEECH_WORDS_PER_SEC = 2.2
SPEECH_CHARS_PER_SEC = 13.0
SCENE_NARRATION_MIN_WORDS = 18
SCENE_NARRATION_MAX_WORDS = 32
SCRIPT_QUALITY_RETRIES = 2
TOPIC_EXPAND_MAX_WORDS = 16
SCRIPT_TOO_LONG_MSG = (
    "Текст слишком длинный: озвучка не влезет в 6 клипов по 10 секунд "
    "даже с ускорением, последние слова обрежутся. "
    "Сократи сценарий примерно до 230–250 слов и пришли снова."
)


def estimate_speech_sec(text: str) -> float:
    words = len(re.findall(r"\w+", text or "", flags=re.U))
    chars = len(re.sub(r"\s+", "", text or ""))
    return max(words / SPEECH_WORDS_PER_SEC, chars / SPEECH_CHARS_PER_SEC)


def count_narration_words(text: str) -> int:
    return len(re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9]+", text or "", flags=re.U))


def is_short_topic(text: str) -> bool:
    """2–3 слова или короткая фраза — ещё не сюжет, нужен этап IDEA_SYSTEM."""
    blob = (text or "").strip()
    if not blob:
        return False
    return count_narration_words(blob) <= TOPIC_EXPAND_MAX_WORDS


def _norm_phrase(text: str) -> str:
    blob = (text or "").lower().replace("ё", "е")
    blob = re.sub(r"[^\w\s]", " ", blob, flags=re.U)
    return re.sub(r"\s+", " ", blob).strip()


def hook_opens_narration(hook: str, narration: str) -> bool:
    """Первая реплика начинается с хука или содержит его в первых словах."""
    h = _norm_phrase(hook)
    n = _norm_phrase(narration)
    if not h:
        return True
    if not n:
        return False
    if n.startswith(h):
        return True
    h_words = h.split()
    n_words = n.split()
    take = min(4, len(h_words))
    if take >= 3 and n_words[:take] == h_words[:take]:
        return True
    head = " ".join(n_words[:12])
    return bool(h) and h in head


def script_system_for(*, photo_lock: bool) -> str:
    return SCRIPT_SYSTEM_PHOTO if photo_lock else SCRIPT_SYSTEM_SYNTH


_CTA_RE = re.compile(
    r"(подпишись|сохрани(сь|те)?|попробуй(те)?|начни(те)?|"
    r"поставь(те)?|напиши(те)?|включи(те)?|не листай|досмотри|"
    r"повтори(те)?|возьми(те)?|открой(те)?|прямо сейчас|"
    r"спроси себя|скажи себе|хватит листать|сделай(те)? (это|сейчас|шаг)|"
    r"поставь(те)? (таймер|себе|пять))",
    re.I,
)


def scene_has_cta(text: str) -> bool:
    return bool(_CTA_RE.search(text or ""))


def script_quality_issues(script: dict[str, Any], *, hook: str = "", n_scenes: int = 4) -> str:
    """Пусто = ок. Иначе текст для переспроса Grok. Кастомный user_script сюда не пускаем."""
    scenes = list(script.get("scenes") or [])
    issues: list[str] = []
    need = min(4, int(n_scenes or 4))
    if len(scenes) < need:
        issues.append(f"Мало сцен: {len(scenes)}, нужно минимум {need}.")
    short: list[str] = []
    for i, scene in enumerate(scenes, 1):
        nar = str(scene.get("narration") or "")
        n = count_narration_words(nar)
        if n < SCENE_NARRATION_MIN_WORDS:
            short.append(f"сцена {i}: {n} слов")
    if short:
        issues.append(
            "Narration слишком короткая (минимум "
            f"{SCENE_NARRATION_MIN_WORDS} слов в КАЖДОЙ сцене): " + "; ".join(short)
        )
    if hook and scenes:
        first = str(scenes[0].get("narration") or "")
        if not hook_opens_narration(hook, first):
            issues.append(
                f"Сцена 1 должна начинаться с хука «{hook.strip()[:120]}» "
                "или его прямым усилением. Сейчас первая фраза нейтральная."
            )
    if len(scenes) >= 2:
        early = scenes[:-1]
        if not any(scene_has_cta(str(s.get("narration") or "")) for s in early):
            issues.append(
                "Призыв к действию не только в финале: минимум ещё в одной из сцен 1–3 "
                "(глагол зрителю: поставь таймер, попробуй, начни, не листай, спроси себя…)."
            )
    return " ".join(issues)


def max_speech_sec_for_clip(clip_sec: int = 10) -> float:
    return min(CLIP_SPEECH_BUDGET_SEC, float(clip_sec) * 1.8)


def script_too_long_for_custom(text: str) -> bool:
    return estimate_speech_sec(text) > MAX_SCENES * max_speech_sec_for_clip(10)


def split_text_to_speech_budget(text: str, budget_sec: float) -> list[str]:
    words = re.findall(r"\S+", text or "")
    if not words:
        return []
    chunks: list[str] = []
    cur: list[str] = []
    for word in words:
        trial = " ".join(cur + [word])
        if cur and estimate_speech_sec(trial) > budget_sec:
            chunks.append(" ".join(cur))
            cur = [word]
        else:
            cur.append(word)
    if cur:
        chunks.append(" ".join(cur))
    return chunks


def enforce_speech_budget(script: dict[str, Any], *, user_script: bool) -> dict[str, Any]:
    """Режет длинные сцены на доп. клипы; в кастомном режиме не молча обрезает речь."""
    budget = max_speech_sec_for_clip(10)
    visual_fallback = "slow subtle push-in, minimal body movement"
    out: list[dict[str, str]] = []
    for scene in script.get("scenes") or []:
        nar = str(scene.get("narration") or "").strip()
        vis = str(scene.get("visual_prompt") or visual_fallback)
        parts = split_text_to_speech_budget(nar, budget)
        if not parts and nar:
            parts = [nar]
        for i, part in enumerate(parts):
            out.append(
                {
                    "narration": part,
                    "visual_prompt": vis if i == 0 else visual_fallback,
                }
            )
    if user_script and len(out) > MAX_SCENES:
        raise PipelineError(
            SCRIPT_TOO_LONG_MSG,
            f"scenes={len(out)} speech_sec={estimate_speech_sec(' '.join(s['narration'] for s in out)):.1f}",
            code="speech_too_long",
        )
    script["scenes"] = out[:MAX_SCENES]
    return script


def visual_look_lock(style: str = "", *, photo_lock: bool = False) -> str:
    """Камерный LOOK для live-action; для cartoon — рендер, без iPhone/ARRI."""
    key = style if style in STYLES else "cinematic"
    if key == "cartoon":
        return LOOK_CARTOON
    if photo_lock or key == "ad":
        return LOOK_PHONE
    return LOOK_ARRI


def compose_runway_prompt(
    continuity: str,
    scene_visual: str,
    camera: str = "",
    motion: str = "",
    *,
    style: str = "cinematic",
    photo_lock: bool = False,
    character_lock: bool = True,
) -> str:
    """Один lock на все клипы + действие сцены + камера/динамика (текстом, не API-параметр)."""
    look = visual_look_lock(style, photo_lock=photo_lock)
    lock = re.sub(r"\s+", " ", (continuity or "").strip())
    if look and look.lower() not in lock.lower():
        lock = f"{look}, {lock}".strip(", ")
    if character_lock and CHARACTER_LOCK.lower() not in lock.lower():
        lock = f"{CHARACTER_LOCK}. {lock}".strip()
    bits = [
        re.sub(r"\s+", " ", (scene_visual or "").strip()),
        re.sub(r"\s+", " ", (camera or "").strip()),
        re.sub(r"\s+", " ", (motion or "").strip()),
    ]
    action = ", ".join(b for b in bits if b)
    header = "LOCKED LOOK (same clothes, location, lighting, style): "
    glue = " | CAMERA/ACTION: "
    budget = RUNWAY_PROMPT_MAX - len(header) - len(glue)
    lock_max = min(len(lock), max(280, budget - 120))
    lock_part = lock[:lock_max]
    motion_part = action[: max(40, budget - len(lock_part))]
    return (header + lock_part + glue + motion_part)[:RUNWAY_PROMPT_MAX]


def fallback_split_script(text: str, n: int = 5) -> dict[str, Any]:
    words = re.findall(r"\S+", text or "")
    if not words:
        raise PipelineError("Пустой сценарий. Напиши текст ролика.")
    n = max(4, min(MAX_SCENES, n))
    chunk = max(1, (len(words) + n - 1) // n)
    scenes = []
    for i in range(n):
        part = " ".join(words[i * chunk : (i + 1) * chunk]).strip()
        if not part:
            continue
        scenes.append(
            {
                "narration": part,
                "visual_prompt": "slow subtle push-in, minimal body movement",
            }
        )
    if not scenes:
        raise PipelineError("Не смог разрезать сценарий на сцены.")
    return {
        "title": "Мой ролик",
        "continuity": "same clothes and location throughout, consistent lighting, photoreal",
        "scenes": scenes,
    }


def pick_clip_duration(audio_sec: float) -> int:
    if audio_sec <= 6.5:
        return 5
    return 10


def ratio_wh(ratio: str) -> tuple[int, int]:
    raw = (ratio or "720:1280").replace("x", ":")
    parts = raw.split(":")
    try:
        w, h = int(parts[0]), int(parts[1])
        if w > 0 and h > 0:
            return w, h
    except (TypeError, ValueError, IndexError):
        pass
    return 720, 1280


def format_script(script: dict[str, Any]) -> str:
    lines = [f"🎬 {script.get('title') or 'Ролик'}"]
    hook = (script.get("hook") or "").strip()
    if hook:
        lines.append(f"🪝 Хук: {hook}")
    lock = (script.get("continuity") or "").strip()
    if lock:
        lines.append("")
        lines.append(f"🔒 Один образ на весь ролик: {lock[:280]}")
    lines.append("")
    for i, scene in enumerate(script.get("scenes") or [], 1):
        lines.append(f"{i}. {scene.get('narration') or ''}")
    cap = (script.get("caption") or "").strip()
    if cap:
        lines.append("")
        lines.append(cap[:400])
    usage = format_runway_usage(script)
    if usage:
        lines.append("")
        lines.append(usage)
    return "\n".join(lines).strip()


def format_runway_usage(script: dict[str, Any] | None) -> str:
    """Фактические модели Runway по кадрам — не тариф, pay-as-you-go кредиты."""
    data = script or {}
    still = str(data.get("runway_still_model") or "").strip()
    raw = data.get("runway_models") or []
    models = [str(m).strip() for m in raw if str(m).strip()] if isinstance(raw, list) else []
    if not still and not models:
        return ""
    bits: list[str] = []
    if still:
        bits.append(f"первый кадр {still}")
    for i, name in enumerate(models, 1):
        bits.append(f"сцена {i} {name}")
    line = "Runway: " + "; ".join(bits) + "."
    cheap = [m for m in models if "turbo" in m.lower()]
    has_45 = any(m.replace("_", "") in ("gen45", "gen4.5") or "gen4.5" in m for m in models)
    if cheap and has_45:
        line += " gen4_turbo здесь — дешёвый запас при нехватке кредитов, не «другой тариф»."
    elif cheap and len(cheap) == len(models):
        line += " Все клипы gen4_turbo (режим «Быстро» или запас по кредитам)."
    return line


def compact_runway_models(models: list[str] | None) -> str:
    names = [str(m).strip() for m in (models or []) if str(m).strip()]
    if not names:
        return ""
    return "сцены: " + ", ".join(names)


def wrap_caption(text: str, width: int = 24) -> str:
    words = re.sub(r"\s+", " ", (text or "").strip()).split(" ")
    lines: list[str] = []
    cur = ""
    for word in words:
        trial = f"{cur} {word}".strip()
        if len(trial) <= width:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return "\n".join(lines[:4])


def find_font() -> str:
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    ):
        if Path(path).is_file():
            return path
    return ""


def _drawtext_escape(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace("'", r"\'")
        .replace(":", r"\:")
        .replace("%", r"\%")
    )


async def sleep_backoff(attempt: int) -> None:
    await asyncio.sleep(min(20.0, 1.5 * (2**attempt)) + random.uniform(0.0, 0.8))


def runway_prompt_text(text: str) -> str:
    visual = re.sub(r"\s+", " ", (text or "").strip())[:RUNWAY_PROMPT_MAX]
    if not visual:
        raise PipelineError("Пустой visual-промпт для Runway.")
    return visual


def runway_duration(seconds: int) -> int:
    try:
        value = int(seconds)
    except (TypeError, ValueError):
        value = 5
    return max(RUNWAY_DURATION_MIN, min(RUNWAY_DURATION_MAX, value))


def runway_poll_delay() -> float:
    base = max(5.0, float(config.RUNWAY_POLL_SEC or 5))
    return base + random.uniform(0.0, 1.5)


async def _read_error(resp: aiohttp.ClientResponse) -> str:
    raw = await resp.text()
    return _clip(f"HTTP {resp.status}: {raw}", 350)


async def _grok_once(
    session: aiohttp.ClientSession,
    messages: list[dict[str, str]],
    model: str,
) -> tuple[str, str]:
    """Один проход chat, затем responses. Возвращает (текст, ошибка)."""
    headers = {
        "Authorization": f"Bearer {config.XAI_API_KEY_NEW}",
        "Content-Type": "application/json",
    }
    tries = max(1, int(config.HTTP_RETRIES))
    last_err = ""
    payload = {"model": model, "messages": messages, "temperature": 0.55}
    for attempt in range(tries):
        try:
            async with session.post(
                XAI_CHAT_URL,
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                if resp.status in RETRY_STATUSES and attempt < tries - 1:
                    last_err = f"{model} chat HTTP {resp.status}"
                    await sleep_backoff(attempt)
                    continue
                if resp.status < 400:
                    data = await resp.json()
                    content = (
                        (((data.get("choices") or [{}])[0].get("message") or {}).get("content"))
                        or ""
                    )
                    if str(content).strip():
                        log.info("Grok chat ok model=%s", model)
                        return str(content), ""
                    last_err = f"{model}: пустой chat/completions"
                else:
                    last_err = f"{model} chat: {await _read_error(resp)}"
        except Exception as exc:
            last_err = f"{model} chat: {type(exc).__name__}: {exc}"
            if attempt < tries - 1:
                await sleep_backoff(attempt)
                continue

    payload_r = {"model": model, "input": messages}
    for attempt in range(tries):
        try:
            async with session.post(
                XAI_RESPONSES_URL,
                headers=headers,
                json=payload_r,
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                raw = await resp.text()
                if resp.status in RETRY_STATUSES and attempt < tries - 1:
                    last_err = f"{model} responses HTTP {resp.status}"
                    await sleep_backoff(attempt)
                    continue
                if resp.status >= 400:
                    last_err = f"{model} responses: {_clip(f'HTTP {resp.status}: {raw}', 350)}"
                    break
                data = json.loads(raw)
            chunks: list[str] = []
            if isinstance(data.get("output_text"), str):
                chunks.append(data["output_text"])
            for item in data.get("output") or []:
                if not isinstance(item, dict):
                    continue
                for part in item.get("content") or []:
                    if isinstance(part, dict) and part.get("text"):
                        chunks.append(str(part["text"]))
            content = "\n".join(chunks).strip()
            if content:
                log.info("Grok responses ok model=%s", model)
                return content, ""
            last_err = f"{model}: пустой responses"
            break
        except Exception as exc:
            last_err = f"{model} responses: {type(exc).__name__}: {exc}"
            if attempt < tries - 1:
                await sleep_backoff(attempt)
                continue
    return "", last_err


async def grok_script(
    session: aiohttp.ClientSession,
    idea: str,
    style: str = "cinematic",
    *,
    n_scenes: int = 5,
    user_script: bool = False,
    extra_brief: str = "",
    photo_lock: bool = False,
    hook: str = "",
) -> dict[str, Any]:
    if config.XAI_API_KEY_ERROR:
        raise PipelineError("Ключ Grok в неправильном формате.", config.XAI_API_KEY_ERROR)
    if not config.XAI_API_KEY_NEW:
        raise PipelineError("Нет XAI_API_KEY_NEW — сценарий собрать не могу.")
    style_key = style if style in STYLES else "cinematic"
    n_scenes = max(4, min(MAX_SCENES, int(n_scenes or 5)))
    hook = (hook or "").strip()
    quality_rules = (
        f"Каждая narration СТРОГО {SCENE_NARRATION_MIN_WORDS}–{SCENE_NARRATION_MAX_WORDS} русских слов. "
        "Каждая сцена — конкретная ситуация, конфликт или прямой вопрос зрителю, не голая метафора. "
        "Призыв к действию не только в последней сцене: минимум ещё в одной из сцен 1–3."
    )
    hook_block = ""
    if hook and not user_script:
        hook_block = (
            f"\nХУК ПЕРВОЙ СЕКУНДЫ — narration сцены 1 обязана буквально начинаться с этой фразы "
            f"или её прямого усиления (те же слова + один удар), не с нейтрального описания кадра: «{hook}»."
        )

    def build_user(quality_note: str = "") -> str:
        if user_script:
            body = (
                f"Стиль: {style_key} — {STYLES[style_key]}\n"
                f"Готовый текст ролика (нарежь на {n_scenes} сцен, речь почти дословно):\n"
                f"{idea.strip()[:4000]}"
            )
        else:
            body = (
                f"Стиль: {style_key} — {STYLES[style_key]}\n"
                f"Сделай {n_scenes} сцен. continuity — одежда/локация/стиль, без лица.\n"
                f"{quality_rules}\n"
                f"Идея:\n{idea.strip()[:2000]}"
                f"{hook_block}"
            )
        if extra_brief.strip():
            body += "\n\nДоп. режиссура пресета:\n" + extra_brief.strip()[:4000]
        if quality_note.strip():
            body += (
                "\n\nПОВТОР. Предыдущий JSON отклонён. Исправь ровно эти ошибки, "
                "верни полный JSON заново:\n" + quality_note.strip()[:1200]
            )
        return body

    system = script_system_for(photo_lock=photo_lock)
    last_err = ""
    quality_retries = 0 if user_script else SCRIPT_QUALITY_RETRIES
    for model in config.xai_creative_models():
        if not model:
            continue
        quality_note = ""
        for q_attempt in range(1 + quality_retries):
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": build_user(quality_note)},
            ]
            content, last_err = await _grok_once(session, messages, model)
            if not content.strip():
                break
            try:
                script = parse_script(content)
            except PipelineError as exc:
                if user_script:
                    return fallback_split_script(idea, n_scenes)
                quality_note = f"JSON не разобрался: {exc.user_message}. Верни ТОЛЬКО валидный JSON по схеме."
                last_err = quality_note
                log.warning("script parse attempt %s/%s model=%s: %s", q_attempt + 1, 1 + quality_retries, model, exc)
                continue
            if not user_script:
                issues = script_quality_issues(script, hook=hook, n_scenes=n_scenes)
                if issues:
                    quality_note = issues
                    last_err = issues
                    log.warning(
                        "script quality attempt %s/%s model=%s: %s",
                        q_attempt + 1,
                        1 + quality_retries,
                        model,
                        issues,
                    )
                    continue
            return script
    if user_script:
        log.warning("Grok failed, split script locally: %s", last_err)
        return fallback_split_script(idea, n_scenes)
    raise PipelineError("Не получилось сочинить сценарий. Напиши идею другими словами.", last_err)


async def eleven_tts(
    session: aiohttp.ClientSession,
    text: str,
    dest: Path,
    voice_id: str | None = None,
    voice_settings: dict[str, Any] | None = None,
) -> Path:
    if dest.is_file() and dest.stat().st_size >= 200:
        log.info("ElevenLabs skip existing %s bytes=%s", dest.name, dest.stat().st_size)
        return dest
    if not config.ELEVENLABS_API_KEY:
        raise PipelineError("Голос сейчас недоступен. Попробуй ещё раз чуть позже.")
    voice_id = voice_id or config.ELEVENLABS_VOICE_ID
    if not voice_id:
        raise PipelineError("Не выбран голос. Нажми /start и выбери голос кнопкой.")
    url = ELEVEN_TTS_URL.format(voice_id=voice_id)
    params = {"output_format": "mp3_44100_128"}
    headers = {
        "xi-api-key": config.ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    body: dict[str, Any] = {
        "text": text.strip()[:900],
        "model_id": config.ELEVENLABS_MODEL_ID or "eleven_multilingual_v2",
    }
    if voice_settings:
        body["voice_settings"] = voice_settings
    last_err = ""
    tries = max(1, int(config.HTTP_RETRIES))
    raw = b""
    for attempt in range(tries):
        try:
            async with session.post(
                url,
                params=params,
                headers=headers,
                json=body,
                timeout=aiohttp.ClientTimeout(total=90),
            ) as resp:
                raw = await resp.read()
                ctype = (resp.headers.get("Content-Type") or "").lower()
                if resp.status in RETRY_STATUSES and attempt < tries - 1:
                    last_err = f"HTTP {resp.status}"
                    await sleep_backoff(attempt)
                    continue
                if resp.status >= 400:
                    raise PipelineError(
                        "ElevenLabs не озвучил сцену.",
                        _clip(f"HTTP {resp.status}: {raw.decode('utf-8', 'replace')}", 350),
                    )
                if "json" in ctype or raw[:1] in (b"{", b"["):
                    raise PipelineError(
                        "ElevenLabs вернул JSON вместо аудио.",
                        _clip(raw.decode("utf-8", "replace"), 300),
                    )
                break
        except PipelineError:
            raise
        except Exception as exc:
            last_err = f"{type(exc).__name__}: {exc}"
            if attempt >= tries - 1:
                raise PipelineError("ElevenLabs недоступен.", last_err) from exc
            await sleep_backoff(attempt)
    if len(raw) < 200:
        raise PipelineError("ElevenLabs вернул пустой аудиофайл.", last_err)
    dest.write_bytes(raw)
    log.info("ElevenLabs mp3 voice=%s bytes=%s", voice_id, len(raw))
    return dest


def _runway_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {config.RUNWAY_API_KEY}",
        "X-Runway-Version": config.RUNWAY_VERSION or RUNWAY_VERSION,
        "Content-Type": "application/json",
    }


async def runway_upload_file(session: aiohttp.ClientSession, path: Path) -> str:
    """POST /v1/uploads → ephemeral runway:// URI. Кредиты не тратит."""
    filename = path.name or "media.bin"
    async with session.post(
        f"{RUNWAY_HOST}/v1/uploads",
        headers=_runway_headers(),
        json={"filename": filename, "type": "ephemeral"},
        timeout=aiohttp.ClientTimeout(total=30),
    ) as resp:
        raw = await resp.text()
        if resp.status >= 400:
            raise PipelineError("Не загрузился файл на Runway.", _clip(f"HTTP {resp.status}: {raw}", 350))
        data = json.loads(raw)
    upload_url = str(data.get("uploadUrl") or "")
    fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}
    runway_uri = str(data.get("runwayUri") or "")
    if not upload_url or not runway_uri:
        raise PipelineError("Runway не дал ссылку для загрузки файла.", _clip(raw, 240))
    form = aiohttp.FormData()
    for key, value in fields.items():
        form.add_field(str(key), str(value))
    form.add_field("file", path.read_bytes(), filename=filename, content_type="application/octet-stream")
    async with session.post(upload_url, data=form, timeout=aiohttp.ClientTimeout(total=180)) as up:
        if up.status >= 400:
            body = await up.text()
            raise PipelineError("Не доехал файл до Runway.", _clip(f"HTTP {up.status}: {body}", 350))
    return runway_uri


async def runway_upload_data_uri(session: aiohttp.ClientSession, data_uri: str) -> str:
    """data:image/...;base64 → runway://. Нужно Seedance I2V, если data URI режется."""
    import base64
    import tempfile

    if not data_uri.startswith("data:"):
        return data_uri
    _header, sep, b64 = data_uri.partition(",")
    if not sep or not b64:
        return data_uri
    raw = base64.b64decode(b64)
    suffix = ".png" if "image/png" in _header.lower() else ".jpg"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as fh:
        fh.write(raw)
        tmp = Path(fh.name)
    try:
        return await runway_upload_file(session, tmp)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def runway_model_side(dest: Path) -> Path:
    return dest.with_suffix(dest.suffix + ".runway_model")


def write_runway_model(dest: Path, model: str) -> None:
    name = (model or "").strip()
    if not name:
        return
    try:
        runway_model_side(dest).write_text(name, encoding="utf-8")
    except OSError:
        log.warning("не записал Runway model рядом с %s", dest.name)


def read_runway_model(dest: Path) -> str:
    path = runway_model_side(dest)
    try:
        return path.read_text(encoding="utf-8").strip() if path.is_file() else ""
    except OSError:
        return ""


def _model_from_submit(payload: dict[str, Any], data: dict[str, Any]) -> str:
    routing = data.get("routing") if isinstance(data.get("routing"), dict) else {}
    name = str(routing.get("model") or payload.get("model") or "").strip()
    return name


async def _runway_poll(
    session: aiohttp.ClientSession,
    task_id: str,
    *,
    used_image: bool = False,
) -> str:
    url = f"{RUNWAY_HOST}/v1/tasks/{task_id}"
    deadline = time.monotonic() + config.RUNWAY_TIMEOUT_SEC
    last_status = ""
    raw = ""
    while time.monotonic() < deadline:
        try:
            async with session.get(
                url,
                headers=_runway_headers(),
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                raw = await resp.text()
                if resp.status in RETRY_STATUSES:
                    log.warning(
                        "Runway poll HTTP %s task_id=%s — повтор до дедлайна",
                        resp.status,
                        task_id,
                    )
                    await asyncio.sleep(runway_poll_delay())
                    continue
                if resp.status >= 400:
                    detail = _clip(f"HTTP {resp.status}: {raw}")
                    failure_code = _failure_code_from_http_body(raw)
                    mapped = runway_fail_error(failure_code, detail, used_image=used_image)
                    mapped.status = resp.status
                    if is_runway_user_facing(mapped):
                        raise mapped
                    raise PipelineError("Runway не отдал статус задачи.", detail)
                data = json.loads(raw)
        except PipelineError:
            raise
        except Exception as exc:
            log.warning("Runway poll error task_id=%s: %s", task_id, exc)
            await asyncio.sleep(runway_poll_delay())
            continue
        last_status = str(data.get("status") or "")
        status_u = last_status.upper()
        try:
            from live_status import note_runway_poll

            note_runway_poll(task_id, data)
        except Exception:
            pass
        if status_u == "SUCCEEDED":
            output = data.get("output") or []
            if isinstance(output, list) and output:
                first = output[0]
                if isinstance(first, str) and first.startswith("http"):
                    return first
                if isinstance(first, dict):
                    for key in ("url", "uri", "href"):
                        if isinstance(first.get(key), str) and first[key].startswith("http"):
                            return first[key]
            if isinstance(output, str) and output.startswith("http"):
                return output
            raise PipelineError("Runway SUCCEEDED без URL в output[0].", _clip(raw, 240))
        if status_u in RUNWAY_DONE_FAIL:
            failure_code = str(data.get("failureCode") or "")
            nested = data.get("error")
            if not failure_code and isinstance(nested, dict):
                failure_code = str(nested.get("code") or nested.get("failureCode") or "")
            reason = data.get("failure") or failure_code or nested or raw
            raise runway_fail_error(
                failure_code,
                _clip(f"{status_u}: {failure_code} {reason}", 300),
                used_image=used_image,
            )
        await asyncio.sleep(runway_poll_delay())
    log.error(
        "Runway poll timeout task_id=%s last_status=%s timeout=%ss — "
        "задача на стороне Runway могла продолжать расходовать кредиты, пробую cancel",
        task_id,
        last_status or "unknown",
        int(config.RUNWAY_TIMEOUT_SEC),
    )
    cancelled = False
    if CANCEL_ON_TIMEOUT:
        cancelled = await _runway_cancel(session, task_id)
    if not cancelled:
        log.error(
            "Runway task still running after local timeout task_id=%s last_status=%s "
            "(cancel/delete не удался, кредиты могут списываться дальше)",
            task_id,
            last_status or "unknown",
        )
    raise PipelineError(
        "Runway слишком долго генерирует клип, остановил ожидание.",
        f"task_id={task_id} status={last_status or 'unknown'} "
        f"timeout={int(config.RUNWAY_TIMEOUT_SEC)}s cancel={'ok' if cancelled else 'failed-still-running'}",
    )


def _failure_code_from_http_body(raw: str) -> str:
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        return ""
    if not isinstance(body, dict):
        return ""
    code = body.get("failureCode") or body.get("errorCode") or ""
    nested = body.get("error")
    if not code and isinstance(nested, dict):
        code = nested.get("code") or nested.get("failureCode") or ""
    return str(code or "")


async def _runway_cancel(session: aiohttp.ClientSession, task_id: str) -> bool:
    """Best-effort DELETE /v1/tasks/{id}. True если 200/204/404."""
    headers = {
        "Authorization": f"Bearer {config.RUNWAY_API_KEY}",
        "X-Runway-Version": config.RUNWAY_VERSION or RUNWAY_VERSION,
    }
    try:
        async with session.delete(
            f"{RUNWAY_HOST}/v1/tasks/{task_id}",
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            raw = await resp.text()
            if resp.status in (200, 204, 404):
                log.info("Runway cancel task_id=%s http=%s", task_id, resp.status)
                return True
            log.warning(
                "Runway cancel failed task_id=%s http=%s body=%s — задача могла остаться висеть",
                task_id,
                resp.status,
                _clip(raw, 200),
            )
            return False
    except Exception as exc:
        log.warning(
            "Runway cancel exception task_id=%s: %s — задача могла остаться висеть",
            task_id,
            exc,
        )
        return False


async def _runway_submit(
    session: aiohttp.ClientSession,
    path: str,
    payload: dict[str, Any],
    *,
    used_image: bool = False,
) -> tuple[str, str]:
    tries = max(1, int(config.HTTP_RETRIES))
    last_err = ""
    raw = ""
    for attempt in range(tries):
        try:
            async with session.post(
                f"{RUNWAY_HOST}{path}",
                headers=_runway_headers(),
                json=payload,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                raw = await resp.text()
                if resp.status in RETRY_STATUSES and attempt < tries - 1:
                    last_err = f"HTTP {resp.status}"
                    await sleep_backoff(attempt)
                    continue
                if resp.status >= 400:
                    detail = _clip(f"HTTP {resp.status}: {raw}")
                    failure_code = _failure_code_from_http_body(raw)
                    mapped = runway_fail_error(failure_code, detail, used_image=used_image)
                    if is_runway_user_facing(mapped):
                        err = mapped
                    elif resp.status == 404 and path.startswith("/v1/generate/"):
                        err = PipelineError(
                            "Model Router: нет такого конфига RUNWAY_ROUTER_CONFIG_ID. "
                            "Проверьте slug на dev.runwayml.com/model-routers или выключите флаг.",
                            detail,
                        )
                    else:
                        err = PipelineError("Runway отклонил запрос на видео.", detail)
                    err.status = resp.status
                    raise err
                data = json.loads(raw)
                task_id = data.get("id")
                if not task_id:
                    raise PipelineError("Runway не вернул id задачи.", _clip(raw, 240))
                routing = data.get("routing") if isinstance(data.get("routing"), dict) else {}
                model_used = _model_from_submit(payload, data)
                log.info(
                    "Runway submitted %s id=%s cost=%s model=%s router_provider=%s",
                    path,
                    task_id,
                    data.get("estimatedCost"),
                    model_used or (routing.get("model") or "-"),
                    routing.get("provider") or "-",
                )
                return str(task_id), model_used
        except PipelineError:
            raise
        except Exception as exc:
            last_err = f"{type(exc).__name__}: {exc}"
            if attempt >= tries - 1:
                raise PipelineError("Runway недоступен.", last_err) from exc
            await sleep_backoff(attempt)
    raise PipelineError("Runway недоступен.", last_err or _clip(raw, 240))


async def _resume_or_submit(
    session: aiohttp.ClientSession,
    path: str,
    payload: dict[str, Any],
    dest: Path,
    *,
    used_image: bool = False,
) -> str:
    """После timeout не создаём новую задачу — сначала poll сохранённого task id."""
    side = dest.with_suffix(dest.suffix + ".runway_id")
    if side.is_file():
        tid = side.read_text(encoding="utf-8").strip()
        if tid:
            log.info("Runway resume poll task_id=%s file=%s", tid, dest.name)
            _remember_runway_task(tid, path, used_image=used_image)
            try:
                return await _runway_poll(session, tid, used_image=used_image)
            except PipelineError as exc:
                timeout = "слишком долго" in (exc.user_message or "").lower()
                if not timeout:
                    try:
                        side.unlink()
                    except OSError:
                        pass
                raise
    tid, model_used = await _runway_submit(session, path, payload, used_image=used_image)
    try:
        side.write_text(tid, encoding="utf-8")
    except OSError:
        log.warning("не записал Runway task id рядом с %s", dest.name)
    write_runway_model(dest, model_used)
    _remember_runway_task(tid, path, used_image=used_image)
    return await _runway_poll(session, tid, used_image=used_image)


def _remember_runway_task(task_id: str, path: str, *, used_image: bool = False) -> None:
    kind = "image_to_video"
    if "text_to_image" in path:
        kind = "text_to_image"
    elif "generate/video" in path:
        kind = "image_to_video" if used_image else "text_to_video"
    elif "text_to_video" in path:
        kind = "text_to_video"
    try:
        from live_status import note_runway_task

        note_runway_task(task_id, kind=kind)
    except Exception:
        pass


async def fetch_runway_task(session: aiohttp.ClientSession, task_id: str) -> dict[str, Any]:
    """Только GET /v1/tasks/{id}. Не submit, кредиты не тратит."""
    tid = (task_id or "").strip()
    if not tid:
        raise PipelineError("Нет сохранённого Runway task_id — опрашивать нечего.")
    async with session.get(
        f"{RUNWAY_HOST}/v1/tasks/{tid}",
        headers=_runway_headers(),
        timeout=aiohttp.ClientTimeout(total=30),
    ) as resp:
        raw = await resp.text()
        if resp.status >= 400:
            raise PipelineError(
                "Runway не отдал статус сохранённой задачи.",
                _clip(f"HTTP {resp.status}: {raw}", 240),
            )
        data = json.loads(raw)
    if not isinstance(data, dict):
        raise PipelineError("Runway вернул не JSON-объект статуса.")
    try:
        from live_status import note_runway_poll

        note_runway_poll(tid, data)
    except Exception:
        pass
    return data


async def _download(session: aiohttp.ClientSession, url: str, dest: Path) -> Path:
    tries = max(1, int(config.HTTP_RETRIES))
    last_err = ""
    for attempt in range(tries):
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=180)) as resp:
                if resp.status in RETRY_STATUSES and attempt < tries - 1:
                    last_err = f"HTTP {resp.status}"
                    await sleep_backoff(attempt)
                    continue
                if resp.status >= 400:
                    raise PipelineError("Не скачался клип Runway.", f"HTTP {resp.status}")
                dest.write_bytes(await resp.read())
                break
        except PipelineError:
            raise
        except Exception as exc:
            last_err = f"{type(exc).__name__}: {exc}"
            if attempt >= tries - 1:
                raise PipelineError("Не скачался клип Runway.", last_err) from exc
            await sleep_backoff(attempt)
    if not dest.exists() or dest.stat().st_size < 1000:
        raise PipelineError("Скачанный клип Runway пустой.", last_err)
    return dest


def still_ratio_for_model(model: str, ratio: str) -> str:
    if model in RUNWAY_GEMINI_IMAGE:
        return GEMINI_IMAGE_RATIO.get(ratio, "768:1344")
    return {"720:1280": "1080:1920", "960:960": "1080:1080"}.get(ratio, "1920:1080")


def duration_for_model(model: str, seconds: int) -> int:
    """Veo — 4/6/8; Seedance — 4–30 (у нас клип 4–10); gen4.5/turbo — 5 или 10."""
    try:
        raw = int(seconds)
    except (TypeError, ValueError):
        raw = 5
    if model in RUNWAY_VEO_MODELS:
        if raw <= 5:
            return 4
        if raw <= 7:
            return 6
        return 8
    if model in RUNWAY_SEEDANCE_MODELS:
        return max(4, min(30, raw if raw >= 4 else 4))
    return 10 if raw >= 8 else 5


def video_ratio_for_model(model: str, ratio: str) -> str:
    ratio = ratio or "720:1280"
    if ratio not in RATIO_PRESETS.values():
        ratio = "720:1280"
    if model in RUNWAY_VEO_MODELS:
        if ratio in ("720:1280", "1280:720", "1080:1920", "1920:1080"):
            return ratio
        width, height = ratio_wh(ratio)
        return "720:1280" if height >= width else "1280:720"
    return ratio


def i2v_fallback_chain(primary: str) -> list[str]:
    chain: list[str] = []
    extra = ""
    if primary in RUNWAY_VEO_MODELS or primary in RUNWAY_SEEDANCE_MODELS:
        extra = "gen4.5"
    for name in (primary, extra, "gen4_turbo"):
        if name and name not in chain:
            chain.append(name)
    return chain or ["gen4_turbo"]


def runway_quality_spec(quality: str) -> dict[str, Any]:
    from presets import QUALITY

    return QUALITY.get(quality) or QUALITY["optimal"]


def still_model_for_quality(quality: str) -> str:
    override = (config.RUNWAY_STILL_MODEL or "").strip()
    if override:
        return override
    spec = runway_quality_spec(quality)
    name = str(spec.get("still_model") or "gen4_image").strip()
    return name or "gen4_image"


def video_models_for_quality(quality: str) -> tuple[str, str]:
    """(i2v_model, t2v_model). t2v пустой, если этот режим только I2V."""
    spec = runway_quality_spec(quality)
    if quality == "fast":
        return "gen4_turbo", ""
    env = (config.RUNWAY_MODEL or "gen4.5").strip() or "gen4.5"
    i2v = env if env in RUNWAY_I2V_MODELS else "gen4.5"
    t2v = env if env in RUNWAY_T2V_MODELS else ("gen4.5" if spec.get("prefer_t2v") else "")
    return i2v, t2v


def text_to_image_payload(prompt: str, ratio: str, model: str | None = None) -> dict[str, Any]:
    """POST /v1/text_to_image без фото пользователя.

    docs.dev.runwayml.com (2024-11-06): у gen4_image_turbo поле referenceImages
    обязательно, min 1 / max 3, элемент {uri, tag?}. Пустой массив не принимают.
    У gen4_image referenceImages необязателен — его не шлём, если референса нет.
    gemini_image3_pro / gemini_image3.1_flash — Nano Banana, свои ratio, без contentModeration.
    """
    name = (model or "gen4_image").strip() or "gen4_image"
    if name in RUNWAY_GEMINI_IMAGE:
        text = re.sub(r"\s+", " ", (prompt or "").strip())[:GEMINI_PROMPT_MAX]
        if not text:
            raise PipelineError("Пустой visual-промпт для Runway.")
        return {
            "model": name,
            "promptText": text,
            "ratio": still_ratio_for_model(name, ratio),
        }
    return {
        "model": "gen4_image",
        "promptText": runway_prompt_text(prompt),
        "ratio": still_ratio_for_model("gen4_image", ratio),
        "contentModeration": runway_content_moderation(),
    }


async def _text_to_image_url(
    session: aiohttp.ClientSession,
    prompt: str,
    ratio: str,
    dest_hint: Path | None = None,
    model: str | None = None,
) -> str:
    """Общий still для цепочки I2V, если пользователь не прислал фото."""
    wanted = (model or "gen4_image").strip() or "gen4_image"
    chain = [wanted]
    if wanted != "gen4_image":
        chain.append("gen4_image")
    last_exc: PipelineError | None = None
    for name in chain:
        payload = text_to_image_payload(prompt, ratio, model=name)
        try:
            if dest_hint is not None:
                return await _resume_or_submit(session, "/v1/text_to_image", payload, dest_hint)
            task_id, _model = await _runway_submit(session, "/v1/text_to_image", payload)
            return await _runway_poll(session, task_id)
        except PipelineError as exc:
            last_exc = exc
            if getattr(exc, "code", "") == "credits" or is_runway_credits_fail(exc.detail):
                raise credits_error(exc.detail, status=getattr(exc, "status", None)) from exc
            if is_runway_user_facing(exc):
                raise
            status = getattr(exc, "status", None)
            if status in (400, 404, 422) and name != chain[-1]:
                log.warning("still %s failed, try gen4_image: %s", name, exc.detail)
                continue
            raise
    if last_exc:
        raise last_exc
    raise PipelineError("Runway не вернул still.")


def runway_video_payload(
    model: str,
    visual: str,
    ratio: str,
    seconds: int,
    *,
    seed: int | None = None,
    prompt_image: str | None = None,
) -> dict[str, Any]:
    """Поля строго по модели: Veo/Seedance не принимают seed/contentModeration; Veo duration 4/6/8.

    Seedance I2V: promptImage — строка (first frame), audio=true — иначе
    third-party INPUT_VALIDATION. Last-frame JPEG chaining тоже падает;
    на сцены 2+ заново шлём исходный still. Photoreal-лицо: SAFETY.THIRD_PARTY.
    Массив [{uri, position:first}] документирован, но часто INVALID.
    First-frame и unpositioned reference смешивать нельзя.
    """
    payload: dict[str, Any] = {
        "model": model,
        "promptText": visual[:15000] if model in RUNWAY_SEEDANCE_MODELS else visual,
        "ratio": video_ratio_for_model(model, ratio),
        "duration": duration_for_model(model, seconds),
    }
    if model in RUNWAY_VEO_MODELS or model in RUNWAY_SEEDANCE_MODELS:
        # Seedance I2V first-frame проходит только с audio=true; Veo и T2V — false.
        payload["audio"] = bool(model in RUNWAY_SEEDANCE_MODELS and prompt_image)
        if prompt_image:
            payload["promptImage"] = prompt_image
        return payload
    payload["contentModeration"] = runway_content_moderation()
    if seed is not None:
        payload["seed"] = int(seed) & 0xFFFFFFFF
    if prompt_image:
        payload["promptImage"] = prompt_image
    return payload


def _clip_payload_base(model: str, visual: str, ratio: str, seconds: int, seed: int | None) -> dict[str, Any]:
    return runway_video_payload(model, visual, ratio, seconds, seed=seed)


def runway_router_video_payload(
    visual: str,
    ratio: str,
    seconds: int,
    *,
    prompt_image: str | None = None,
    seed: int | None = None,
    config_id: str | None = None,
) -> dict[str, Any]:
    """POST /v1/generate/video: configId + model-agnostic input, без поля model.

    Асинхронно: ответ 200 содержит task id — тот же GET /v1/tasks/{id}, что и прямой вызов.
    audio=false: TTS клеим сами, нативный звук модели не нужен.
    """
    slug = (config_id or config.RUNWAY_ROUTER_CONFIG_ID or "").strip()
    aspect = RATIO_TO_ASPECT.get(ratio) or "9:16"
    inp: dict[str, Any] = {
        "promptText": visual,
        "aspectRatio": aspect,
        "duration": int(seconds),
        "audio": False,
        "contentModeration": runway_content_moderation(),
    }
    if prompt_image:
        inp["referenceImages"] = [{"uri": prompt_image, "role": "first"}]
    if seed is not None:
        inp["seed"] = int(seed) & 0xFFFFFFFF
    return {"configId": slug, "input": inp}


async def runway_clip(
    session: aiohttp.ClientSession,
    prompt: str,
    seconds: int,
    dest: Path,
    ratio: str | None = None,
    prompt_image: str | None = None,
    clip_index: int = 1,
    clip_total: int = 1,
    seed: int | None = None,
    quality: str = "optimal",
) -> Path:
    if not config.RUNWAY_API_KEY:
        raise PipelineError("Камера сейчас недоступна. Попробуй ещё раз чуть позже.")
    requested = int(seconds)
    visual = runway_prompt_text(prompt)
    ratio = ratio or "720:1280"
    if ratio not in RATIO_PRESETS.values():
        ratio = "720:1280"
    if config.RUNWAY_USE_MODEL_ROUTER and not config.RUNWAY_ROUTER_CONFIG_ID:
        log.warning(
            "RUNWAY_USE_MODEL_ROUTER=1, но RUNWAY_ROUTER_CONFIG_ID пуст — прямой вызов модели"
        )
    if config.runway_model_router_enabled():
        router_sec = 10 if requested >= 8 else 5
        payload = runway_router_video_payload(
            visual,
            ratio,
            router_sec,
            prompt_image=prompt_image,
            seed=seed,
        )
        video_url = await _resume_or_submit(
            session,
            "/v1/generate/video",
            payload,
            dest,
            used_image=bool(prompt_image),
        )
        path_out = await _download(session, video_url, dest)
        if not read_runway_model(dest):
            write_runway_model(dest, "router")
        return path_out
    i2v_model, t2v_model = video_models_for_quality(quality)
    still_model = still_model_for_quality(quality)
    last_fail: PipelineError | None = None
    label = f"клип {clip_index} из {clip_total}"

    async def _i2v(image: str, mdl: str) -> Path:
        image_for = image
        # Seedance 2.5 (third-party) часто режет data-URI promptImage
        # INPUT_VALIDATION; hosted runway:// URI проходит.
        if (
            mdl in RUNWAY_SEEDANCE_MODELS
            and isinstance(image, str)
            and image.startswith("data:")
        ):
            try:
                image_for = await runway_upload_data_uri(session, image)
            except PipelineError as up_err:
                log.warning("Seedance still upload failed, trying data URI: %s", up_err.detail)
        payload = runway_video_payload(
            mdl, visual, ratio, requested, seed=seed, prompt_image=image_for
        )
        video_url = await _resume_or_submit(
            session, "/v1/image_to_video", payload, dest, used_image=True
        )
        out = await _download(session, video_url, dest)
        write_runway_model(dest, read_runway_model(dest) or mdl)
        return out

    async def _i2v_with_fallback(image: str, primary: str) -> Path:
        chain = i2v_fallback_chain(primary)
        last_exc: PipelineError | None = None
        for idx, mdl in enumerate(chain):
            try:
                return await _i2v(image, mdl)
            except PipelineError as exc:
                last_exc = exc
                if getattr(exc, "code", "") == "credits" or is_runway_credits_fail(exc.detail):
                    raise credits_error(exc.detail, status=getattr(exc, "status", None)) from exc
                if is_runway_user_facing(exc):
                    raise
                status = getattr(exc, "status", None)
                if status in (400, 404, 422) and idx < len(chain) - 1:
                    log.warning("I2V %s failed, try %s: %s", mdl, chain[idx + 1], exc.detail)
                    continue
                raise
        if last_exc:
            raise last_exc
        raise PipelineError("Runway не вернул клип.")

    for round_i in range(2):
        try:
            if prompt_image:
                return await _i2v_with_fallback(prompt_image, i2v_model)
            if quality == "fast":
                still = await _text_to_image_url(
                    session,
                    visual,
                    ratio,
                    dest.with_name(dest.stem + "_still.hint"),
                    model=still_model,
                )
                return await _i2v_with_fallback(still, "gen4_turbo")
            if t2v_model in RUNWAY_T2V_MODELS:
                t2v_payload = runway_video_payload(t2v_model, visual, ratio, requested, seed=seed)
                try:
                    video_url = await _resume_or_submit(session, "/v1/text_to_video", t2v_payload, dest)
                    return await _download(session, video_url, dest)
                except PipelineError as exc:
                    if getattr(exc, "code", "") == "credits" or is_runway_credits_fail(exc.detail):
                        raise credits_error(exc.detail, status=getattr(exc, "status", None)) from exc
                    if is_runway_user_facing(exc):
                        raise
                    status = getattr(exc, "status", None)
                    if status not in (400, 404, 422):
                        raise
                    log.warning("T2V rejected (%s), fallback still+I2V: %s", status, exc.detail)
            still = await _text_to_image_url(
                session,
                visual,
                ratio,
                dest.with_name(dest.stem + "_still.hint"),
                model=still_model,
            )
            return await _i2v_with_fallback(still, i2v_model)
        except PipelineError as exc:
            last_fail = exc
            if is_runway_user_facing(exc):
                if getattr(exc, "code", "") == "credits" or is_runway_credits_fail(exc.detail):
                    raise credits_error(exc.detail, status=getattr(exc, "status", None)) from exc
                raise
            detail = (exc.detail or "").upper()
            retryable = "INTERNAL" in detail or "BAD_OUTPUT" in detail or "THROTTLED" in detail
            if round_i == 0 and retryable:
                log.warning("Runway %s retry: %s", label, exc.detail)
                await sleep_backoff(1)
                continue
            raise PipelineError(
                f"🎥 Не получился {label}. Я остановился, чтобы не склеить кривой ролик. "
                "Попробуй ещё раз или другое фото.",
                exc.detail,
                code=getattr(exc, "code", ""),
            ) from exc
    raise PipelineError(
        f"🎥 Не получился {label}. Попробуй ещё раз.",
        (last_fail.detail if last_fail else ""),
    )


async def file_to_data_uri(path: Path, dest_jpeg: Path | None = None) -> str:
    """JPEG data URI для Runway promptImage (лимит ~5 МБ)."""
    import base64

    jpeg = dest_jpeg or path.with_suffix(".ref.jpg")
    await _run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(path),
            "-vf",
            "scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2",
            "-frames:v",
            "1",
            "-q:v",
            "4",
            str(jpeg),
        ]
    )
    raw = jpeg.read_bytes()
    if len(raw) > 4_500_000:
        await _run_ffmpeg(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(jpeg),
                "-q:v",
                "8",
                str(jpeg),
            ]
        )
        raw = jpeg.read_bytes()
    if len(raw) < 80:
        raise PipelineError("Фото не прочиталось. Пришли другое изображение.")
    if len(raw) > 5_000_000:
        raise PipelineError("Фото слишком тяжёлое. Пришли файл поменьше.")
    return "data:image/jpeg;base64," + base64.b64encode(raw).decode("ascii")


async def last_frame_data_uri(video: Path, dest_jpeg: Path) -> str:
    """Последний кадр клипа → promptImage следующего (last-frame chaining)."""
    dest_jpeg.parent.mkdir(parents=True, exist_ok=True)
    await _run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-sseof",
            "-0.2",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-q:v",
            "4",
            str(dest_jpeg),
        ]
    )
    if not dest_jpeg.exists() or dest_jpeg.stat().st_size < 80:
        raise PipelineError("Не снялся последний кадр клипа.")
    return await file_to_data_uri(dest_jpeg, dest_jpeg.with_name(dest_jpeg.stem + "_ref.jpg"))


async def _run_ffmpeg(args: list[str]) -> None:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _out, err = await proc.communicate()
    if proc.returncode != 0:
        raise PipelineError("Не получилось склеить ролик. Попробуй ещё раз.", _clip(err.decode("utf-8", "replace"), 400))


async def media_duration(path: Path) -> float:
    if shutil.which("ffprobe") is None:
        return 0.0
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, _err = await proc.communicate()
    try:
        return float((out.decode("utf-8", "replace") or "0").strip() or 0)
    except ValueError:
        return 0.0


async def mux_scene(
    video: Path,
    audio: Path,
    dest: Path,
    caption: str = "",
    width: int = 720,
    height: int = 1280,
) -> Path:
    vdur = await media_duration(video) or 10.0
    adur = await media_duration(audio) or vdur
    if vdur > 0.2 and adur > 0.2 and (adur / vdur) > 2.0 + 1e-3:
        raise PipelineError(
            "Озвучка этой сцены длиннее клипа даже на максимальном ускорении. "
            "Сократи текст и попробуй снова — иначе последние слова обрежутся.",
            f"audio={adur:.1f}s video={vdur:.1f}s",
            code="speech_too_long",
        )
    tempo = adur / vdur if vdur > 0.2 and adur > 0.2 else 1.0
    tempo = max(0.5, min(2.0, tempo))
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps=24,setsar=1,format=yuv420p"
    )
    font = find_font()
    if config.BURN_SUBTITLES and caption and font:
        wrapped = wrap_caption(caption)
        escaped = _drawtext_escape(wrapped)
        vf += (
            f",drawtext=fontfile={font}:text='{escaped}':fontsize=32:"
            "fontcolor=white:borderw=3:bordercolor=black:"
            "x=(w-text_w)/2:y=h-th-96"
        )
    await _run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video),
            "-i",
            str(audio),
            "-filter_complex",
            f"[0:v]{vf}[v];[1:a]atempo={tempo:.3f},aformat=sample_rates=44100:channel_layouts=stereo[a]",
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(dest),
        ]
    )
    return dest


async def concat_mp4(clips: list[Path], dest: Path, width: int = 720, height: int = 1280) -> Path:
    if len(clips) == 1:
        shutil.copyfile(clips[0], dest)
        return dest
    n = len(clips)
    args = ["ffmpeg", "-y"]
    for clip in clips:
        args += ["-i", str(clip)]
    filters = []
    for i in range(n):
        filters.append(
            f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps=24,setsar=1,format=yuv420p[v{i}]"
        )
        filters.append(f"[{i}:a]aformat=sample_rates=44100:channel_layouts=stereo[a{i}]")
    concat_in = "".join(f"[v{i}][a{i}]" for i in range(n))
    filters.append(f"{concat_in}concat=n={n}:v=1:a=1[v][a]")
    args += [
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-movflags",
        "+faststart",
        str(dest),
    ]
    await _run_ffmpeg(args)
    return dest


def watermark_drawtext(text: str, font: str) -> str:
    escaped = _drawtext_escape((text or "VideoBot").strip() or "VideoBot")
    return (
        f"drawtext=fontfile={font}:text='{escaped}':fontsize=28:"
        "fontcolor=white@0.6:borderw=2:bordercolor=black@0.45:"
        "x=w-tw-24:y=h-th-24"
    )


async def apply_watermark(
    src: Path,
    dest: Path,
    *,
    text: str = "",
    logo_path: str = "",
) -> Path:
    """Простой оверлей лого/текста. Без Brand Kit — только вкл/выкл."""
    text = (text or config.WATERMARK_TEXT or "VideoBot").strip() or "VideoBot"
    logo = Path(logo_path or config.WATERMARK_LOGO or "")
    font = find_font()
    has_logo = bool(str(logo) and logo.is_file())
    if not has_logo and not font:
        log.warning("нет шрифта и лого для водяного знака, оставляю ролик как есть")
        if src.resolve() != dest.resolve():
            shutil.copyfile(src, dest)
        return dest
    args = ["ffmpeg", "-y", "-i", str(src)]
    if has_logo:
        args += ["-i", str(logo)]
        fc = "[1:v]format=rgba,scale=120:-1[wm];[0:v][wm]overlay=W-w-24:H-h-48[base]"
        if font:
            fc += f";[base]{watermark_drawtext(text, font)}[v]"
            args += ["-filter_complex", fc, "-map", "[v]", "-map", "0:a"]
        else:
            args += ["-filter_complex", fc, "-map", "[base]", "-map", "0:a"]
    else:
        args += ["-vf", watermark_drawtext(text, font), "-map", "0:v", "-map", "0:a"]
    args += [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-movflags",
        "+faststart",
        str(dest),
    ]
    await _run_ffmpeg(args)
    return dest


def ensure_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        raise PipelineError("На сервере нет программы склейки видео (ffmpeg).")


async def build_video(
    idea: str,
    work_dir: Path,
    progress: ProgressCb | None = None,
    *,
    ratio: str | None = None,
    style: str | None = None,
    voice_id: str | None = None,
    reference_image: Path | str | None = None,
    user_script: bool = False,
    n_scenes: int | None = None,
    extra_brief: str = "",
    voice_settings: dict[str, Any] | None = None,
    camera: str = "",
    motion: str = "",
    quality: str = "optimal",
    watermark: bool = False,
    hook: str = "",
) -> tuple[Path, dict[str, Any]]:
    from presets import StageProgress
    import live_status as live

    ensure_ffmpeg()
    work_dir.mkdir(parents=True, exist_ok=True)
    ratio = ratio or "720:1280"
    style = style or config.DEFAULT_STYLE or "cinematic"
    width, height = ratio_wh(ratio)
    planned = int(n_scenes or target_scene_count(idea))
    tracker = StageProgress(planned)

    async def report(label: str, *, stage: str, scene: int = 0) -> None:
        live.update_job(
            stage=stage,
            label=label,
            scene_n=int(scene or 0),
            scene_total=int(tracker.n or 0),
        )
        key = live.current_key()
        snap = live.get_job(key) if key else None
        text = live.format_status(snap) if snap else tracker.render(label)
        await _notify(progress, text)

    from resume_job import (
        MP4_MIN_BYTES,
        file_ready,
        load_checkpoint,
        load_script,
        save_checkpoint,
        save_script,
    )

    timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=180)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        await report("Пишу сценарий…", stage=live.STAGE_SCRIPT)
        photo_lock = (
            isinstance(reference_image, Path) and reference_image.exists()
        ) or (
            isinstance(reference_image, str) and reference_image.startswith("data:")
        )
        packed: dict[str, Any] | None = None
        script = load_script(work_dir)
        resumed = script is not None
        if resumed:
            log.info("resume script.json scenes=%s dir=%s", len(script["scenes"]), work_dir)
            await report("Сценарий уже есть — не пишу заново", stage=live.STAGE_SCRIPT)
        else:
            if (
                not user_script
                and not photo_lock
                and not (hook or "").strip()
                and is_short_topic(idea)
            ):
                from night_ideas import expand_topic_to_idea, script_brief_from_idea

                await report("Разворачиваю тему в идею…", stage=live.STAGE_SCRIPT)
                packed = await expand_topic_to_idea(session, idea)
                hook = str(packed.get("hook") or packed.get("title") or "").strip()
                extra_brief = script_brief_from_idea(packed, extra=extra_brief)
                idea = str(packed.get("plot") or packed.get("title") or idea)
                planned = max(planned, 4)
            script = await grok_script(
                session,
                idea,
                style=style,
                n_scenes=planned,
                user_script=user_script,
                extra_brief=extra_brief,
                photo_lock=photo_lock,
                hook=hook,
            )
            script = enforce_speech_budget(script, user_script=user_script)
            if packed:
                if packed.get("title"):
                    script["title"] = packed["title"]
                script["hook"] = hook
                if packed.get("caption"):
                    script["caption"] = packed["caption"]
            script["plot"] = idea
            if hook:
                script["hook"] = hook
            script["ratio"] = ratio
            script["style"] = style
            save_script(work_dir, script)
        scenes = script["scenes"]
        continuity = script.get("continuity") or ""
        script["ratio"] = ratio
        script["style"] = style
        total = len(scenes)
        tracker.n = max(1, total)
        live.update_job(scene_total=total)
        tracker.script_done = True
        await report("Сценарий готов", stage=live.STAGE_SCRIPT)

        ckpt = load_checkpoint(work_dir) or {}
        try:
            job_seed = int(ckpt.get("job_seed"))
        except (TypeError, ValueError):
            job_seed = random.randint(0, 2_147_483_647)
        save_checkpoint(work_dir, job_seed=job_seed, n_scenes=total, credits_paused=False)
        still_png = work_dir / "bible_still.png"
        anchor_image: str | None = None
        if isinstance(reference_image, Path) and reference_image.exists():
            await report("Готовлю фото как первый кадр…", stage=live.STAGE_STILL)
            anchor_image = await file_to_data_uri(reference_image, work_dir / "user_ref.jpg")
        elif isinstance(reference_image, str) and reference_image.startswith(("data:", "http")):
            anchor_image = reference_image
        elif file_ready(still_png, min_bytes=1000):
            log.info("resume still %s", still_png.name)
            await report("Первый кадр уже есть — Runway не дергаю", stage=live.STAGE_STILL)
            anchor_image = await file_to_data_uri(still_png, work_dir / "bible_ref.jpg")
        else:
            await report("Общий первый кадр в Runway…", stage=live.STAGE_STILL)
            try:
                still_url = await _text_to_image_url(
                    session,
                    compose_runway_prompt(
                        continuity,
                        "medium shot, looking into camera, still",
                        camera,
                        motion,
                        style=style,
                        photo_lock=photo_lock,
                    ),
                    ratio,
                    work_dir / "bible_still.hint",
                    model=still_model_for_quality(quality),
                )
                await _download(session, still_url, still_png)
                anchor_image = await file_to_data_uri(still_png, work_dir / "bible_ref.jpg")
            except PipelineError as exc:
                if is_runway_user_facing(exc):
                    if getattr(exc, "code", "") == "credits" or is_runway_credits_fail(exc.detail):
                        raise credits_error(exc.detail, status=getattr(exc, "status", None)) from exc
                    raise
                log.warning("shared still failed, T2V with lock: %s", exc.detail)
                anchor_image = None
        prompt_image = anchor_image
        tracker.still_done = True
        still_model = read_runway_model(work_dir / "bible_still.hint")
        if still_model:
            script["runway_still_model"] = still_model
        elif still_png.is_file() and not photo_lock:
            script["runway_still_model"] = still_model or "gen4_image"
        await report("Первый кадр готов", stage=live.STAGE_STILL)

        muxed: list[Path] = []
        clip_models: list[str] = []
        i2v_model, _t2v_model = video_models_for_quality(quality)
        for i, scene in enumerate(scenes):
            n = i + 1
            mixed_path = work_dir / f"m{i}.mp4"
            clip_path = work_dir / f"c{i}.mp4"
            audio_path = work_dir / f"n{i}.mp3"
            if file_ready(mixed_path, min_bytes=MP4_MIN_BYTES):
                log.info("resume muxed scene %s/%s", n, total)
                muxed.append(mixed_path)
                tracker.tts_done = max(tracker.tts_done, n)
                tracker.video_done = n
                if prompt_image and n < total and file_ready(clip_path, min_bytes=MP4_MIN_BYTES):
                    if i2v_model in RUNWAY_SEEDANCE_MODELS and anchor_image:
                        prompt_image = anchor_image
                    else:
                        try:
                            prompt_image = await last_frame_data_uri(clip_path, work_dir / f"tail{i}.jpg")
                        except PipelineError:
                            prompt_image = anchor_image
                await report(f"Клип {n} из {total} уже смонтирован", stage=live.STAGE_RUNWAY, scene=n)
                clip_models.append(read_runway_model(clip_path) or "?")
                continue
            await report(f"Озвучка ElevenLabs · сцена {n} из {total}", stage=live.STAGE_TTS, scene=n)
            audio = await eleven_tts(
                session,
                scene["narration"],
                audio_path,
                voice_id=voice_id,
                voice_settings=voice_settings,
            )
            tracker.tts_done = n
            await report(
                f"Озвучка готова ({n} из {total})",
                stage=live.STAGE_TTS,
                scene=n,
            )
            prompt = compose_runway_prompt(
                continuity,
                scene["visual_prompt"],
                camera,
                motion,
                style=style,
                photo_lock=photo_lock,
            )
            if file_ready(clip_path, min_bytes=MP4_MIN_BYTES):
                log.info("resume clip %s/%s", n, total)
                clip = clip_path
            else:
                audio_sec = await media_duration(audio)
                clip_sec = pick_clip_duration(audio_sec or 10.0)
                await report(
                    f"Сцена {n} из {total} рендерится в Runway",
                    stage=live.STAGE_RUNWAY,
                    scene=n,
                )
                try:
                    clip = await runway_clip(
                        session,
                        prompt,
                        clip_sec,
                        clip_path,
                        ratio=ratio,
                        prompt_image=prompt_image,
                        clip_index=n,
                        clip_total=total,
                        seed=job_seed,
                        quality=quality,
                    )
                except PipelineError:
                    raise
            # Клип 1: якорь (фото или still). gen4.5/Veo: last-frame chaining.
            # Seedance I2V last-frame JPEG → INPUT_VALIDATION; тот же still
            # на каждую сцену лучше держит персонажа (и это единственный слот).
            if prompt_image and n < total:
                if i2v_model in RUNWAY_SEEDANCE_MODELS and anchor_image:
                    prompt_image = anchor_image
                else:
                    try:
                        prompt_image = await last_frame_data_uri(clip, work_dir / f"tail{i}.jpg")
                    except PipelineError as exc:
                        log.warning("last-frame chain fallback to anchor: %s", exc.detail)
                        prompt_image = anchor_image
            mixed = await mux_scene(
                clip,
                audio,
                mixed_path,
                caption=scene["narration"],
                width=width,
                height=height,
            )
            muxed.append(mixed)
            tracker.video_done = n
            clip_models.append(read_runway_model(clip_path) or "?")
            save_checkpoint(work_dir, scene_done=n, credits_paused=False, runway_models=clip_models)
            await report(f"Клип {n} из {total} смонтирован", stage=live.STAGE_RUNWAY, scene=n)
        script["runway_models"] = clip_models
        save_script(work_dir, script)
        await report("Сборка финального файла ffmpeg…", stage=live.STAGE_MUX)
        out = await concat_mp4(muxed, work_dir / "final.mp4", width=width, height=height)
        if watermark:
            await report("Водяной знак", stage=live.STAGE_MUX)
            out = await apply_watermark(out, work_dir / "final_wm.mp4")
        tracker.mux_done = True
        await report("Файл собран", stage=live.STAGE_MUX)
        return out, script
