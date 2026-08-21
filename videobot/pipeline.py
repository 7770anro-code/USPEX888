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
RUNWAY_T2V_MODELS = frozenset({"gen4.5", "veo3", "veo3.1", "veo3.1_fast", "seedance2"})
RUNWAY_DONE_FAIL = frozenset({"FAILED", "CANCELED", "CANCELLED"})

# ElevenLabs: POST /v1/text-to-speech/{voice_id} → сырой audio/mpeg, не JSON.
ELEVEN_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"

SCRIPT_SYSTEM = """Ты режиссёр вертикальных TikTok-роликов 30–60 секунд (кадр 9:16).
Верни ТОЛЬКО JSON без markdown:

{
  "title": "короткий заголовок",
  "continuity": "ONE locked English description for EVERY shot: character (age, face, hair, clothes), location, lighting, color grade, visual style. No camera motion here. Must stay identical across shots.",
  "scenes": [
    {
      "narration": "озвучка на языке пользователя",
      "visual_prompt": "English CAMERA AND ACTION ONLY: move, gesture, framing. Do NOT re-describe face, clothes, or location."
    }
  ]
}

Правила:
- continuity пишется ОДИН раз — единственное описание персонажа и места.
- visual_prompt сцены — только действие камеры, без нового лица и локации.
- Сцен от 4 до 6. Каждая ~10 секунд речи (примерно 18–28 слов). Итого 40–60 секунд.
- Если дан готовый текст пользователя — режь ЕГО слова на сцены, не выдумывай новую речь.
- Без текста на экране, логотипов, знаменитостей, NSFW, watermark.
"""

STYLES = {
    "cinematic": "photoreal cinematic, shallow depth of field, natural motivated light, subtle film grain, 24fps motion",
    "ad": "premium commercial, clean high-end lighting, polished product look, slow elegant camera",
    "cartoon": "stylized 3D animation, vibrant, appealing shapes, not a celebrity likeness",
}

RATIO_PRESETS = {
    "9:16": "720:1280",
    "16:9": "1280:720",
    "1:1": "960:960",
}

RETRY_STATUSES = frozenset({429, 502, 503, 504})


# Явные тексты при модерации Runway (docs 21.08.2026: FAILED + failureCode SAFETY.*).
RUNWAY_PERSON_MSG = (
    "Runway отклонил это фото (политика по реальным людям), "
    "попробуйте другое фото или текстовый режим."
)
RUNWAY_SAFETY_MSG = (
    "Runway не пропустил этот запрос по правилам контента. "
    "Измени текст или фото и попробуй ещё раз."
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
    if used_image and (
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
    for scene in scenes[:6]:
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


def compose_runway_prompt(continuity: str, scene_visual: str) -> str:
    """Один lock на все клипы + действие сцены. continuity не переписывается."""
    lock = re.sub(r"\s+", " ", (continuity or "").strip())
    motion = re.sub(r"\s+", " ", (scene_visual or "").strip())
    header = "LOCKED LOOK (same person, clothes, location, style): "
    glue = " | CAMERA/ACTION: "
    budget = RUNWAY_PROMPT_MAX - len(header) - len(glue)
    lock_max = min(len(lock), max(280, budget - 120))
    lock_part = lock[:lock_max]
    motion_part = motion[: max(40, budget - len(lock_part))]
    return (header + lock_part + glue + motion_part)[:RUNWAY_PROMPT_MAX]


def fallback_split_script(text: str, n: int = 5) -> dict[str, Any]:
    words = re.findall(r"\S+", text or "")
    if not words:
        raise PipelineError("Пустой сценарий. Напиши текст ролика.")
    n = max(4, min(6, n))
    chunk = max(1, (len(words) + n - 1) // n)
    scenes = []
    for i in range(n):
        part = " ".join(words[i * chunk : (i + 1) * chunk]).strip()
        if not part:
            continue
        scenes.append(
            {
                "narration": part,
                "visual_prompt": "slow push-in, natural motion, keep locked look",
            }
        )
    if not scenes:
        raise PipelineError("Не смог разрезать сценарий на сцены.")
    return {
        "title": "Мой ролик",
        "continuity": "same protagonist and location throughout, consistent lighting and clothes, photoreal",
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
    lock = (script.get("continuity") or "").strip()
    if lock:
        lines.append("")
        lines.append(f"🔒 Один образ на весь ролик: {lock[:280]}")
    lines.append("")
    for i, scene in enumerate(script.get("scenes") or [], 1):
        lines.append(f"{i}. {scene.get('narration') or ''}")
    return "\n".join(lines).strip()


def wrap_caption(text: str, width: int = 28) -> str:
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


async def grok_script(
    session: aiohttp.ClientSession,
    idea: str,
    style: str = "cinematic",
    *,
    n_scenes: int = 5,
    user_script: bool = False,
) -> dict[str, Any]:
    if config.XAI_API_KEY_ERROR:
        raise PipelineError("Ключ Grok в неправильном формате.", config.XAI_API_KEY_ERROR)
    if not config.XAI_API_KEY_NEW:
        raise PipelineError("Нет XAI_API_KEY_NEW — сценарий собрать не могу.")
    style_key = style if style in STYLES else "cinematic"
    n_scenes = max(4, min(6, int(n_scenes or 5)))
    if user_script:
        user_content = (
            f"Стиль: {style_key} — {STYLES[style_key]}\n"
            f"Готовый текст ролика (нарежь на {n_scenes} сцен, речь почти дословно):\n"
            f"{idea.strip()[:4000]}"
        )
    else:
        user_content = (
            f"Стиль: {style_key} — {STYLES[style_key]}\n"
            f"Сделай {n_scenes} сцен. continuity — один lock на весь ролик.\n"
            f"Идея:\n{idea.strip()[:2000]}"
        )
    messages = [
        {"role": "system", "content": SCRIPT_SYSTEM},
        {"role": "user", "content": user_content},
    ]
    headers = {
        "Authorization": f"Bearer {config.XAI_API_KEY_NEW}",
        "Content-Type": "application/json",
    }
    last_err = ""
    tries = max(1, int(config.HTTP_RETRIES))
    for model in (config.XAI_MODEL, config.XAI_FALLBACK_MODEL):
        if not model:
            continue
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
                        if content.strip():
                            log.info("Grok chat ok model=%s", model)
                            try:
                                return parse_script(content)
                            except PipelineError:
                                if user_script:
                                    return fallback_split_script(idea, n_scenes)
                                raise
                        last_err = f"{model}: пустой chat/completions"
                    else:
                        last_err = f"{model} chat: {await _read_error(resp)}"
            except PipelineError:
                raise
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
                    try:
                        return parse_script(content)
                    except PipelineError:
                        if user_script:
                            return fallback_split_script(idea, n_scenes)
                        raise
                last_err = f"{model}: пустой responses"
                break
            except PipelineError:
                raise
            except Exception as exc:
                last_err = f"{model} responses: {type(exc).__name__}: {exc}"
                if attempt < tries - 1:
                    await sleep_backoff(attempt)
                    continue
    if user_script:
        log.warning("Grok failed, split script locally: %s", last_err)
        return fallback_split_script(idea, n_scenes)
    raise PipelineError("Не получилось сочинить сценарий. Напиши идею другими словами.", last_err)


async def eleven_tts(
    session: aiohttp.ClientSession,
    text: str,
    dest: Path,
    voice_id: str | None = None,
) -> Path:
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
    body = {
        "text": text.strip()[:900],
        "model_id": config.ELEVENLABS_MODEL_ID or "eleven_multilingual_v2",
    }
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
                if resp.status >= 400:
                    detail = _clip(f"HTTP {resp.status}: {raw}")
                    failure_code = _failure_code_from_http_body(raw)
                    mod_err = runway_fail_error(failure_code, detail, used_image=used_image)
                    if getattr(mod_err, "code", "").startswith("moderation"):
                        raise mod_err
                    raise PipelineError("Runway не отдал статус задачи.", detail)
                data = json.loads(raw)
        except PipelineError:
            raise
        except Exception as exc:
            log.warning("Runway poll error: %s", exc)
            await asyncio.sleep(runway_poll_delay())
            continue
        last_status = str(data.get("status") or "")
        status_u = last_status.upper()
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
    raise PipelineError(
        "Runway слишком долго генерирует клип, остановил ожидание.",
        f"status={last_status or 'unknown'} timeout={int(config.RUNWAY_TIMEOUT_SEC)}s",
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


async def _runway_submit(
    session: aiohttp.ClientSession,
    path: str,
    payload: dict[str, Any],
    *,
    used_image: bool = False,
) -> str:
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
                    mod_err = runway_fail_error(failure_code, detail, used_image=used_image)
                    if getattr(mod_err, "code", "").startswith("moderation"):
                        err = mod_err
                    else:
                        err = PipelineError("Runway отклонил запрос на видео.", detail)
                    err.status = resp.status
                    raise err
                data = json.loads(raw)
                task_id = data.get("id")
                if not task_id:
                    raise PipelineError("Runway не вернул id задачи.", _clip(raw, 240))
                log.info("Runway submitted %s id=%s cost=%s", path, task_id, data.get("estimatedCost"))
                return str(task_id)
        except PipelineError:
            raise
        except Exception as exc:
            last_err = f"{type(exc).__name__}: {exc}"
            if attempt >= tries - 1:
                raise PipelineError("Runway недоступен.", last_err) from exc
            await sleep_backoff(attempt)
    raise PipelineError("Runway недоступен.", last_err or _clip(raw, 240))


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


async def _text_to_image_url(session: aiohttp.ClientSession, prompt: str, ratio: str) -> str:
    """Общий still для цепочки I2V, если пользователь не прислал фото."""
    payload = {
        "model": "gen4_image_turbo",
        "promptText": runway_prompt_text(prompt),
        "ratio": {"720:1280": "1080:1920", "960:960": "1080:1080"}.get(ratio, "1920:1080"),
        "contentModeration": runway_content_moderation(),
    }
    task_id = await _runway_submit(session, "/v1/text_to_image", payload)
    return await _runway_poll(session, task_id)


def _clip_payload_base(model: str, visual: str, ratio: str, seconds: int, seed: int | None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "promptText": visual,
        "ratio": ratio,
        "duration": seconds,
        "contentModeration": runway_content_moderation(),
    }
    if seed is not None:
        payload["seed"] = int(seed) & 0xFFFFFFFF
    return payload


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
) -> Path:
    if not config.RUNWAY_API_KEY:
        raise PipelineError("Камера сейчас недоступна. Попробуй ещё раз чуть позже.")
    # gen4.5 image_to_video: на практике 5 или 10; API допускает integer 2–10.
    seconds = 10 if int(seconds) >= 8 else 5
    visual = runway_prompt_text(prompt)
    ratio = ratio or "720:1280"
    if ratio not in RATIO_PRESETS.values():
        ratio = "720:1280"
    model = config.RUNWAY_MODEL or "gen4.5"
    i2v_model = model if model in ("gen4.5", "gen4_turbo", "seedance2", "veo3.1", "veo3.1_fast") else "gen4.5"
    last_fail: PipelineError | None = None
    label = f"клип {clip_index} из {clip_total}"

    async def _i2v(image: str, mdl: str) -> Path:
        payload = _clip_payload_base(mdl, visual, ratio, seconds, seed)
        payload["promptImage"] = image
        task_id = await _runway_submit(session, "/v1/image_to_video", payload, used_image=True)
        video_url = await _runway_poll(session, task_id, used_image=True)
        return await _download(session, video_url, dest)

    for round_i in range(2):
        try:
            if prompt_image:
                try:
                    return await _i2v(prompt_image, i2v_model)
                except PipelineError as exc:
                    if getattr(exc, "code", "").startswith("moderation"):
                        raise
                    status = getattr(exc, "status", None)
                    if status in (400, 404, 422) and i2v_model != "gen4_turbo":
                        log.warning("I2V %s failed, try gen4_turbo: %s", i2v_model, exc.detail)
                        return await _i2v(prompt_image, "gen4_turbo")
                    raise
            if model in RUNWAY_T2V_MODELS:
                t2v_payload = _clip_payload_base(model, visual, ratio, seconds, seed)
                try:
                    task_id = await _runway_submit(session, "/v1/text_to_video", t2v_payload)
                    video_url = await _runway_poll(session, task_id)
                    return await _download(session, video_url, dest)
                except PipelineError as exc:
                    if getattr(exc, "code", "").startswith("moderation"):
                        raise
                    status = getattr(exc, "status", None)
                    if status not in (400, 404, 422):
                        raise
                    log.warning("T2V rejected (%s), fallback still+I2V: %s", status, exc.detail)
            still = await _text_to_image_url(session, visual, ratio)
            return await _i2v(still, "gen4_turbo")
        except PipelineError as exc:
            last_fail = exc
            if getattr(exc, "code", "").startswith("moderation"):
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
            f",drawtext=fontfile={font}:text='{escaped}':fontsize=28:"
            "fontcolor=white:borderw=2:bordercolor=black:"
            "x=(w-text_w)/2:y=h-th-80"
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
) -> tuple[Path, dict[str, Any]]:
    ensure_ffmpeg()
    work_dir.mkdir(parents=True, exist_ok=True)
    ratio = ratio or "720:1280"
    style = style or config.DEFAULT_STYLE or "cinematic"
    width, height = ratio_wh(ratio)
    n_scenes = target_scene_count(idea)
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=180)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        await _notify(progress, "✍️ Пишу сценарий…")
        script = await grok_script(
            session,
            idea,
            style=style,
            n_scenes=n_scenes,
            user_script=user_script,
        )
        scenes = script["scenes"]
        if len(scenes) > 6:
            scenes = scenes[:6]
            script["scenes"] = scenes
        continuity = script.get("continuity") or ""
        script["ratio"] = ratio
        script["style"] = style
        total = len(scenes)

        # Якорь — одно исходное фото на КАЖДЫЙ клип (штатного character-consistency нет).
        # Last-frame chaining только если фото пользователя нет: иначе клипы 2+ теряют лицо
        # и лишний раз бьются о модерацию чужого кадра.
        job_seed = random.randint(0, 2_147_483_647)
        user_supplied_photo = bool(
            (isinstance(reference_image, Path) and reference_image.exists())
            or (isinstance(reference_image, str) and reference_image.startswith(("data:", "http")))
        )
        anchor_image: str | None = None
        if isinstance(reference_image, Path) and reference_image.exists():
            await _notify(progress, "🖼️ Готовлю твоё фото как первый кадр для всех клипов…")
            anchor_image = await file_to_data_uri(reference_image, work_dir / "user_ref.jpg")
        elif isinstance(reference_image, str) and reference_image.startswith(("data:", "http")):
            anchor_image = reference_image
        else:
            await _notify(progress, "🖼️ Рисую общий первый кадр, чтобы лицо и место не прыгали…")
            try:
                still_url = await _text_to_image_url(
                    session,
                    compose_runway_prompt(continuity, "medium shot, looking into camera, still"),
                    ratio,
                )
                still_path = work_dir / "bible_still.png"
                await _download(session, still_url, still_path)
                anchor_image = await file_to_data_uri(still_path, work_dir / "bible_ref.jpg")
            except PipelineError as exc:
                if getattr(exc, "code", "").startswith("moderation"):
                    raise
                log.warning("shared still failed, T2V with lock: %s", exc.detail)
                anchor_image = None
        prompt_image = anchor_image

        muxed: list[Path] = []
        for i, scene in enumerate(scenes):
            n = i + 1
            await _notify(progress, f"🎤 Записываю голос ({n} из {total})…")
            audio = await eleven_tts(
                session,
                scene["narration"],
                work_dir / f"n{i}.mp3",
                voice_id=voice_id,
            )
            prompt = compose_runway_prompt(continuity, scene["visual_prompt"])
            audio_sec = await media_duration(audio)
            clip_sec = pick_clip_duration(audio_sec or 10.0)
            await _notify(progress, f"🎥 Снимаю клип {n} из {total}…")
            try:
                clip = await runway_clip(
                    session,
                    prompt,
                    clip_sec,
                    work_dir / f"c{i}.mp4",
                    ratio=ratio,
                    prompt_image=prompt_image,
                    clip_index=n,
                    clip_total=total,
                    seed=job_seed,
                )
            except PipelineError:
                raise
            if prompt_image and n < total and not user_supplied_photo:
                try:
                    prompt_image = await last_frame_data_uri(clip, work_dir / f"tail{i}.jpg")
                except PipelineError as exc:
                    log.warning("last-frame chain fallback to anchor: %s", exc.detail)
                    prompt_image = anchor_image
            mixed = await mux_scene(
                clip,
                audio,
                work_dir / f"m{i}.mp4",
                caption=scene["narration"],
                width=width,
                height=height,
            )
            muxed.append(mixed)
        await _notify(progress, "✂️ Монтирую ролик…")
        out = await concat_mp4(muxed, work_dir / "final.mp4", width=width, height=height)
        await _notify(progress, "✅ Готово!")
        return out, script
