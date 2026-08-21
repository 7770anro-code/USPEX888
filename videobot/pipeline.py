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

SCRIPT_SYSTEM = """Ты режиссёр коротких вертикальных/горизонтальных роликов 20–30 секунд высокого качества.
Пользователь даёт идею текстом. Верни ТОЛЬКО JSON без markdown и без комментариев:

{
  "title": "короткий заголовок",
  "scenes": [
    {
      "narration": "озвучка на языке идеи, 16–24 слова, один законченный кадр мысли",
      "visual_prompt": "English cinematic prompt: camera, lens, lighting, motion, mood, continuity with previous shot; no on-screen text"
    }
  ]
}

Правила:
- Ровно 3 сцены (если идея крошечная — 2). Каждая сцена рассчитана на ~10 секунд видео.
- narration: живая речь диктора, без кавычек, без нумерации, без «в этом видео».
- visual_prompt: один конкретный shot на английском, непрерывное движение камеры, тот же персонаж/локация если история одна.
- Стиль задан пользователем — встрой его в каждый visual_prompt.
- Без логотипов, без текста на экране, без знаменитостей, без NSFW, без watermark.
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


class PipelineError(Exception):
    def __init__(self, user_message: str, detail: str = "") -> None:
        super().__init__(user_message)
        self.user_message = user_message
        self.detail = detail or user_message
        self.status: int | None = None


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
        raise PipelineError("В сценарии нет сцен.")
    cleaned = []
    for scene in scenes[:3]:
        if not isinstance(scene, dict):
            continue
        narration = str(scene.get("narration") or "").strip()
        visual = str(scene.get("visual_prompt") or scene.get("visualPrompt") or "").strip()
        if not narration or not visual:
            continue
        cleaned.append(
            {"narration": narration[:500], "visual_prompt": visual[:RUNWAY_PROMPT_MAX]}
        )
    if not cleaned:
        raise PipelineError("Сцены пустые: нет текста озвучки или visual-промпта.")
    title = str(data.get("title") or "Ролик").strip()[:80] or "Ролик"
    return {"title": title, "scenes": cleaned}


def scene_durations(count: int) -> list[int]:
    n = max(1, min(int(count), 3))
    return [10] * n


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
    lines = [f"🎬 {script.get('title') or 'Ролик'}", ""]
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
) -> dict[str, Any]:
    if config.XAI_API_KEY_ERROR:
        raise PipelineError("Ключ Grok в неправильном формате.", config.XAI_API_KEY_ERROR)
    if not config.XAI_API_KEY_NEW:
        raise PipelineError("Нет XAI_API_KEY_NEW — сценарий собрать не могу.")
    style_key = style if style in STYLES else "cinematic"
    messages = [
        {"role": "system", "content": SCRIPT_SYSTEM},
        {
            "role": "user",
            "content": (
                f"Стиль: {style_key} — {STYLES[style_key]}\n"
                f"Идея ролика:\n{idea.strip()[:2000]}"
            ),
        },
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
                            return parse_script(content)
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
                    return parse_script(content)
                last_err = f"{model}: пустой responses"
                break
            except PipelineError:
                raise
            except Exception as exc:
                last_err = f"{model} responses: {type(exc).__name__}: {exc}"
                if attempt < tries - 1:
                    await sleep_backoff(attempt)
                    continue
    raise PipelineError("Grok не смог написать сценарий.", last_err)


async def eleven_tts(session: aiohttp.ClientSession, text: str, dest: Path) -> Path:
    if not config.ELEVENLABS_API_KEY:
        raise PipelineError("Нет ELEVENLABS_API_KEY — озвучку сделать не могу.")
    voice_id = config.ELEVENLABS_VOICE_ID
    if not voice_id:
        raise PipelineError("Не задан ELEVENLABS_VOICE_ID — для MVP нужен дефолтный голос.")
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


async def _runway_poll(session: aiohttp.ClientSession, task_id: str) -> str:
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
                    raise PipelineError("Runway не отдал статус задачи.", _clip(f"HTTP {resp.status}: {raw}"))
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
            reason = data.get("failure") or data.get("failureCode") or data.get("error") or raw
            raise PipelineError("Runway не смог сгенерировать клип.", _clip(f"{status_u}: {reason}", 300))
        await asyncio.sleep(runway_poll_delay())
    raise PipelineError(
        "Runway слишком долго генерирует клип, остановил ожидание.",
        f"status={last_status or 'unknown'} timeout={int(config.RUNWAY_TIMEOUT_SEC)}s",
    )


async def _runway_submit(
    session: aiohttp.ClientSession,
    path: str,
    payload: dict[str, Any],
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
                    err = PipelineError(
                        "Runway отклонил запрос на видео.",
                        _clip(f"HTTP {resp.status}: {raw}"),
                    )
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
    """Запасной путь: кадр для gen4_turbo (только image-to-video)."""
    payload = {
        "model": "gen4_image_turbo",
        "promptText": runway_prompt_text(prompt),
        "ratio": {"720:1280": "1080:1920", "960:960": "1080:1080"}.get(ratio, "1920:1080"),
    }
    task_id = await _runway_submit(session, "/v1/text_to_image", payload)
    return await _runway_poll(session, task_id)


async def runway_clip(
    session: aiohttp.ClientSession,
    prompt: str,
    seconds: int,
    dest: Path,
    ratio: str | None = None,
) -> Path:
    if not config.RUNWAY_API_KEY:
        raise PipelineError("Нет RUNWAY_API_KEY — видео не собрать.")
    seconds = runway_duration(seconds)
    visual = runway_prompt_text(prompt)
    ratio = ratio or config.RUNWAY_RATIO or "720:1280"
    model = config.RUNWAY_MODEL or "gen4.5"
    t2v_ok = model in RUNWAY_T2V_MODELS
    last_fail: PipelineError | None = None
    for round_i in range(2):
        try:
            if t2v_ok:
                t2v_payload = {
                    "model": model,
                    "promptText": visual,
                    "ratio": ratio,
                    "duration": seconds,
                }
                try:
                    task_id = await _runway_submit(session, "/v1/text_to_video", t2v_payload)
                    video_url = await _runway_poll(session, task_id)
                    return await _download(session, video_url, dest)
                except PipelineError as exc:
                    status = getattr(exc, "status", None)
                    if status not in (400, 404, 422):
                        raise
                    log.warning("T2V rejected (%s), fallback image+gen4_turbo: %s", status, exc.detail)
            else:
                log.info("Модель %s не T2V — сразу кадр+I2V", model)
            image_url = await _text_to_image_url(session, visual, ratio)
            i2v_payload = {
                "model": "gen4_turbo",
                "promptText": visual,
                "promptImage": image_url,
                "ratio": ratio,
                "duration": seconds,
            }
            task_id = await _runway_submit(session, "/v1/image_to_video", i2v_payload)
            video_url = await _runway_poll(session, task_id)
            return await _download(session, video_url, dest)
        except PipelineError as exc:
            last_fail = exc
            detail = (exc.detail or "").upper()
            retryable = "INTERNAL" in detail or "BAD_OUTPUT" in detail or "THROTTLED" in detail
            if round_i == 0 and retryable:
                log.warning("Runway clip retry: %s", exc.detail)
                await sleep_backoff(1)
                continue
            raise
    raise last_fail or PipelineError("Runway не смог сгенерировать клип.")


async def _run_ffmpeg(args: list[str]) -> None:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _out, err = await proc.communicate()
    if proc.returncode != 0:
        raise PipelineError("ffmpeg не смог склеить ролик.", _clip(err.decode("utf-8", "replace"), 400))


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
        raise PipelineError("На сервере нет ffmpeg. Поставь: sudo apt-get install -y ffmpeg")


async def build_video(
    idea: str,
    work_dir: Path,
    progress: ProgressCb | None = None,
    *,
    ratio: str | None = None,
    style: str | None = None,
) -> tuple[Path, dict[str, Any]]:
    ensure_ffmpeg()
    work_dir.mkdir(parents=True, exist_ok=True)
    ratio = ratio or config.RUNWAY_RATIO or "720:1280"
    style = style or config.DEFAULT_STYLE or "cinematic"
    width, height = ratio_wh(ratio)
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=180)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        await _notify(progress, "Пишу сценарий через Grok…")
        script = await grok_script(session, idea, style=style)
        scenes = script["scenes"]
        script["ratio"] = ratio
        script["style"] = style
        await _notify(
            progress,
            f"Сценарий «{script['title']}»: {len(scenes)} сцен. Делаю озвучку…",
        )
        muxed: list[Path] = []
        for i, scene in enumerate(scenes):
            await _notify(progress, f"Озвучка сцены {i + 1}/{len(scenes)}…")
            audio = await eleven_tts(session, scene["narration"], work_dir / f"n{i}.mp3")
            audio_sec = await media_duration(audio)
            seconds = pick_clip_duration(audio_sec) if audio_sec else 10
            seconds = runway_duration(seconds)
            await _notify(
                progress,
                f"Runway: клип {i + 1}/{len(scenes)} (~{seconds} сек, {ratio})…",
            )
            clip = await runway_clip(
                session,
                scene["visual_prompt"],
                seconds,
                work_dir / f"c{i}.mp4",
                ratio=ratio,
            )
            mixed = await mux_scene(
                clip,
                audio,
                work_dir / f"m{i}.mp4",
                caption=scene["narration"],
                width=width,
                height=height,
            )
            muxed.append(mixed)
        await _notify(progress, "Склеиваю клипы в один ролик…")
        out = await concat_mp4(muxed, work_dir / "final.mp4", width=width, height=height)
        return out, script
