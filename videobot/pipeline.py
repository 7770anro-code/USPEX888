"""Пайплайн: идея -> сценарий (Grok) -> TTS -> Runway T2V -> ffmpeg -> mp4."""

from __future__ import annotations

import asyncio
import json
import logging
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
RUNWAY_BASE = "https://api.dev.runwayml.com/v1"
ELEVEN_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"

SCRIPT_SYSTEM = """Ты режиссёр коротких вертикально-нейтральных роликов 15–20 секунд.
Пользователь даёт идею текстом. Верни ТОЛЬКО JSON без markdown и без комментариев:

{
  "title": "короткий заголовок",
  "scenes": [
    {
      "narration": "озвучка на языке идеи, 8–14 слов",
      "visual_prompt": "English cinematic prompt, one shot, no on-screen text, photoreal or matching style"
    }
  ]
}

Правила:
- Ровно 2 или 3 сцены. Для 3 сцен суммарно ~15 сек, для 2 сцен ~20 сек.
- narration: живая речь диктора, без кавычек и без нумерации.
- visual_prompt: конкретный кадр (камера, свет, действие, стиль). На английском.
- Без логотипов, без текста на экране, без знаменитостей, без NSFW.
"""


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
        cleaned.append({"narration": narration[:500], "visual_prompt": visual[:900]})
    if not cleaned:
        raise PipelineError("Сцены пустые: нет текста озвучки или visual-промпта.")
    title = str(data.get("title") or "Ролик").strip()[:80] or "Ролик"
    return {"title": title, "scenes": cleaned}


def scene_durations(count: int) -> list[int]:
    if count <= 1:
        return [10]
    if count == 2:
        return [10, 10]
    return [5, 5, 5]


async def _read_error(resp: aiohttp.ClientResponse) -> str:
    raw = await resp.text()
    return _clip(f"HTTP {resp.status}: {raw}", 350)


async def grok_script(session: aiohttp.ClientSession, idea: str) -> dict[str, Any]:
    if config.XAI_API_KEY_ERROR:
        raise PipelineError("Ключ Grok в неправильном формате.", config.XAI_API_KEY_ERROR)
    if not config.XAI_API_KEY_NEW:
        raise PipelineError("Нет XAI_API_KEY_NEW — сценарий собрать не могу.")
    messages = [
        {"role": "system", "content": SCRIPT_SYSTEM},
        {"role": "user", "content": f"Идея ролика:\n{idea.strip()[:2000]}"},
    ]
    headers = {
        "Authorization": f"Bearer {config.XAI_API_KEY_NEW}",
        "Content-Type": "application/json",
    }
    last_err = ""
    for model in (config.XAI_MODEL, config.XAI_FALLBACK_MODEL):
        if not model:
            continue
        payload = {"model": model, "messages": messages, "temperature": 0.6}
        try:
            async with session.post(
                XAI_CHAT_URL,
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=90),
            ) as resp:
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

        payload_r = {"model": model, "input": messages}
        try:
            async with session.post(
                XAI_RESPONSES_URL,
                headers=headers,
                json=payload_r,
                timeout=aiohttp.ClientTimeout(total=90),
            ) as resp:
                raw = await resp.text()
                if resp.status >= 400:
                    last_err = f"{model} responses: {_clip(f'HTTP {resp.status}: {raw}', 350)}"
                    continue
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
        except PipelineError:
            raise
        except Exception as exc:
            last_err = f"{model} responses: {type(exc).__name__}: {exc}"
    raise PipelineError("Grok не смог написать сценарий.", last_err)


async def eleven_tts(session: aiohttp.ClientSession, text: str, dest: Path) -> Path:
    if not config.ELEVENLABS_API_KEY:
        raise PipelineError("Нет ELEVENLABS_API_KEY — озвучку сделать не могу.")
    url = ELEVEN_TTS_URL.format(voice_id=config.ELEVENLABS_VOICE_ID)
    params = {"output_format": "mp3_44100_128"}
    headers = {
        "xi-api-key": config.ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    body = {
        "text": text.strip()[:900],
        "model_id": config.ELEVENLABS_MODEL_ID,
        "voice_settings": {"stability": 0.45, "similarity_boost": 0.75},
    }
    try:
        async with session.post(
            url,
            params=params,
            headers=headers,
            json=body,
            timeout=aiohttp.ClientTimeout(total=90),
        ) as resp:
            if resp.status >= 400:
                raise PipelineError(
                    "ElevenLabs не озвучил сцену.",
                    await _read_error(resp),
                )
            data = await resp.read()
    except PipelineError:
        raise
    except Exception as exc:
        raise PipelineError("ElevenLabs недоступен.", f"{type(exc).__name__}: {exc}") from exc
    if len(data) < 200:
        raise PipelineError("ElevenLabs вернул пустой аудиофайл.")
    dest.write_bytes(data)
    return dest


def _runway_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {config.RUNWAY_API_KEY}",
        "X-Runway-Version": config.RUNWAY_VERSION,
        "Content-Type": "application/json",
    }


async def _runway_poll(session: aiohttp.ClientSession, task_id: str) -> str:
    url = f"{RUNWAY_BASE}/tasks/{task_id}"
    deadline = time.monotonic() + config.RUNWAY_TIMEOUT_SEC
    last_status = ""
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
            await asyncio.sleep(config.RUNWAY_POLL_SEC)
            continue
        last_status = str(data.get("status") or "")
        if last_status.upper() == "SUCCEEDED":
            output = data.get("output") or []
            if isinstance(output, str) and output.startswith("http"):
                return output
            if isinstance(output, list) and output:
                first = output[0]
                if isinstance(first, str) and first.startswith("http"):
                    return first
                if isinstance(first, dict):
                    for key in ("url", "uri", "href"):
                        if isinstance(first.get(key), str) and first[key].startswith("http"):
                            return first[key]
            raise PipelineError("Runway завершился без ссылки на видео.", _clip(raw, 240))
        if last_status.upper() == "FAILED":
            reason = data.get("failure") or data.get("failureCode") or data.get("error") or raw
            raise PipelineError("Runway не смог сгенерировать клип.", _clip(str(reason), 300))
        await asyncio.sleep(config.RUNWAY_POLL_SEC)
    raise PipelineError(
        "Runway слишком долго генерирует клип, остановил ожидание.",
        f"status={last_status or 'unknown'} timeout={int(config.RUNWAY_TIMEOUT_SEC)}s",
    )


async def _runway_submit(
    session: aiohttp.ClientSession,
    path: str,
    payload: dict[str, Any],
) -> str:
    try:
        async with session.post(
            f"{RUNWAY_BASE}{path}",
            headers=_runway_headers(),
            json=payload,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            raw = await resp.text()
            if resp.status >= 400:
                err = PipelineError("Runway отклонил запрос на видео.", _clip(f"HTTP {resp.status}: {raw}"))
                err.status = resp.status
                raise err
            data = json.loads(raw)
    except PipelineError:
        raise
    except Exception as exc:
        raise PipelineError("Runway недоступен.", f"{type(exc).__name__}: {exc}") from exc
    task_id = data.get("id") or data.get("taskId") or data.get("task_id")
    if not task_id:
        raise PipelineError("Runway не вернул id задачи.", _clip(raw, 240))
    return str(task_id)


async def _download(session: aiohttp.ClientSession, url: str, dest: Path) -> Path:
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=180)) as resp:
            if resp.status >= 400:
                raise PipelineError("Не скачался клип Runway.", f"HTTP {resp.status}")
            dest.write_bytes(await resp.read())
    except PipelineError:
        raise
    except Exception as exc:
        raise PipelineError("Не скачался клип Runway.", f"{type(exc).__name__}: {exc}") from exc
    if dest.stat().st_size < 1000:
        raise PipelineError("Скачанный клип Runway пустой.")
    return dest


async def _text_to_image_url(session: aiohttp.ClientSession, prompt: str) -> str:
    """Запасной путь: дешёвый кадр для gen4_turbo (image-to-video)."""
    payload = {
        "model": "gen4_image_turbo",
        "promptText": prompt[:900],
        "ratio": "1280:720",
    }
    task_id = await _runway_submit(session, "/text_to_image", payload)
    return await _runway_poll(session, task_id)


async def runway_clip(
    session: aiohttp.ClientSession,
    prompt: str,
    seconds: int,
    dest: Path,
) -> Path:
    if not config.RUNWAY_API_KEY:
        raise PipelineError("Нет RUNWAY_API_KEY — видео не собрать.")
    seconds = 10 if seconds >= 8 else 5
    visual = prompt.strip()[:900]
    t2v_payload = {
        "model": config.RUNWAY_MODEL,
        "promptText": visual,
        "ratio": config.RUNWAY_RATIO,
        "duration": seconds,
    }
    try:
        task_id = await _runway_submit(session, "/text_to_video", t2v_payload)
        video_url = await _runway_poll(session, task_id)
        return await _download(session, video_url, dest)
    except PipelineError as exc:
        status = getattr(exc, "status", None)
        if status not in (400, 404, 422):
            raise
        log.warning("T2V rejected (%s), fallback image+turbo: %s", status, exc.detail)
        image_url = await _text_to_image_url(session, visual)
        i2v_payload = {
            "model": "gen4_turbo",
            "promptImage": image_url,
            "promptText": visual,
            "ratio": config.RUNWAY_RATIO,
            "duration": seconds,
        }
        task_id = await _runway_submit(session, "/image_to_video", i2v_payload)
        video_url = await _runway_poll(session, task_id)
        return await _download(session, video_url, dest)


async def _run_ffmpeg(args: list[str]) -> None:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _out, err = await proc.communicate()
    if proc.returncode != 0:
        raise PipelineError("ffmpeg не смог склеить ролик.", _clip(err.decode("utf-8", "replace"), 400))


async def mux_scene(video: Path, audio: Path, dest: Path) -> Path:
    await _run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video),
            "-i",
            str(audio),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(dest),
        ]
    )
    return dest


async def concat_mp4(clips: list[Path], dest: Path) -> Path:
    if len(clips) == 1:
        shutil.copyfile(clips[0], dest)
        return dest
    n = len(clips)
    args = ["ffmpeg", "-y"]
    for clip in clips:
        args += ["-i", str(clip)]
    filters = []
    for i in range(n):
        filters.append(f"[{i}:v]scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,fps=24,setsar=1,format=yuv420p[v{i}]")
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
        "23",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        str(dest),
    ]
    await _run_ffmpeg(args)
    return dest


def ensure_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        raise PipelineError("На сервере нет ffmpeg. Поставь: sudo apt-get install -y ffmpeg")


async def build_video(idea: str, work_dir: Path, progress: ProgressCb | None = None) -> tuple[Path, dict[str, Any]]:
    ensure_ffmpeg()
    work_dir.mkdir(parents=True, exist_ok=True)
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=180)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        await _notify(progress, "Пишу сценарий через Grok…")
        script = await grok_script(session, idea)
        scenes = script["scenes"]
        durs = scene_durations(len(scenes))
        await _notify(
            progress,
            f"Сценарий «{script['title']}»: {len(scenes)} сцен. Делаю озвучку…",
        )
        muxed: list[Path] = []
        for i, scene in enumerate(scenes):
            await _notify(progress, f"Озвучка сцены {i + 1}/{len(scenes)}…")
            audio = await eleven_tts(session, scene["narration"], work_dir / f"n{i}.mp3")
            await _notify(
                progress,
                f"Runway: клип {i + 1}/{len(scenes)} (~{durs[i]} сек, может занять несколько минут)…",
            )
            clip = await runway_clip(session, scene["visual_prompt"], durs[i], work_dir / f"c{i}.mp4")
            mixed = await mux_scene(clip, audio, work_dir / f"m{i}.mp4")
            muxed.append(mixed)
        await _notify(progress, "Склеиваю клипы в один ролик…")
        out = await concat_mp4(muxed, work_dir / "final.mp4")
        return out, script
