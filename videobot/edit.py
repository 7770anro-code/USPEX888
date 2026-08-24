"""Нарезка и склейка пользовательских роликов.

Ручной режим — только ffmpeg.
Авто-режим — план клипов через официальный xAI API (тот же ключ, что сценарии), затем ffmpeg.
Веб-кабинеты подписок и браузерную автоматизацию не используем.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
from pathlib import Path
from typing import Any

import aiohttp

import config
from pipeline import (
    RETRY_STATUSES,
    XAI_CHAT_URL,
    XAI_RESPONSES_URL,
    PipelineError,
    _clip,
    _read_error,
    sleep_backoff,
)

log = logging.getLogger("videobot.edit")

# Telegram Bot API: getFile ≤ 20 МБ, sendVideo/sendDocument ≤ 50 МБ.
MAX_INPUT_BYTES = 20 * 1024 * 1024
MAX_OUTPUT_BYTES = 49 * 1024 * 1024
MAX_INPUT_SEC = 180
MAX_OUTPUT_SEC = 180
MAX_CLIPS = 8
MIN_CLIP_SEC = 0.2

_CLOCK = re.compile(
    r"^(?:(\d{1,2}):)?(\d{1,2}):(\d{1,2}(?:\.\d+)?)$|^(\d+(?:\.\d+)?)$"
)


def parse_clock(token: str) -> float:
    raw = (token or "").strip().lower().replace(",", ".")
    raw = re.sub(r"^[^\d]+", "", raw)
    raw = re.sub(r"[^\d:.]+$", "", raw)
    match = _CLOCK.match(raw)
    if not match:
        raise PipelineError("Не понял таймкод. Пример: 0:05 или 12.5")
    if match.group(4) is not None:
        return float(match.group(4))
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = float(match.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds


def parse_timecodes(text: str) -> tuple[float, float]:
    blob = (text or "").strip().replace("–", "-").replace("—", "-")
    if not blob:
        raise PipelineError("Напиши начало и конец куска, например 0:05-0:18")
    parts = re.split(r"\s*(?:-|по|до)\s*", blob, maxsplit=1, flags=re.I)
    if len(parts) != 2:
        parts = blob.split()
    if len(parts) != 2:
        raise PipelineError("Нужны два таймкода: начало и конец. Пример: 0:05-0:18 или 12 40")
    start = parse_clock(parts[0])
    end = parse_clock(parts[1])
    if start < 0 or end < 0:
        raise PipelineError("Таймкоды не могут быть отрицательными.")
    if end <= start:
        raise PipelineError("Конец должен быть позже начала.")
    if end - start < MIN_CLIP_SEC:
        raise PipelineError("Кусок слишком короткий — хотя бы 0.2 секунды.")
    if end - start > MAX_OUTPUT_SEC + 0.05:
        raise PipelineError(f"Готовый кусок длиннее {MAX_OUTPUT_SEC} сек — так Telegram может не принять файл.")
    return start, end


Clip = tuple[float, float]

EDIT_SYSTEM = """Ты монтажёр коротких вертикальных роликов.
Верни ТОЛЬКО JSON без markdown:

{"clips": [{"start": 1.2, "end": 5.0}, {"start": 12.0, "end": 18.5}], "note": "кратко"}

Жёстко:
- start и end — секунды, числа. 0 ≤ start < end ≤ duration.
- 1–8 клипов, порядок clips = порядок склейки.
- Суммарная длина не больше max_out секунд и не больше duration.
- Не выдумывай таймкоды за пределами duration. Исходник ты не видишь — опирайся на запрос и длительность.
"""


def parse_target_range(brief: str) -> tuple[float, float]:
    text = (brief or "").lower().replace("–", "-").replace("—", "-")
    pair = re.search(r"(\d{1,3})\s*-\s*(\d{1,3})\s*(?:сек|s\b)?", text)
    if pair:
        lo, hi = float(pair.group(1)), float(pair.group(2))
        if hi < lo:
            lo, hi = hi, lo
        return max(5.0, lo), min(float(MAX_OUTPUT_SEC), hi)
    one = re.search(r"(\d{1,3})\s*(?:сек|секунд)", text)
    if one:
        sec = float(one.group(1))
        return max(5.0, sec * 0.8), min(float(MAX_OUTPUT_SEC), max(sec, sec * 1.15))
    return 30.0, 45.0


def parse_edit_plan(raw: str) -> list[dict[str, Any]]:
    text = (raw or "").strip()
    if not text:
        raise PipelineError("Пустой план монтажа.")
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S)
    data = json.loads(text)
    items = data.get("clips") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise PipelineError("В плане монтажа нет списка clips.")
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            start = float(item.get("start"))
            end = float(item.get("end"))
        except (TypeError, ValueError):
            continue
        out.append({"start": start, "end": end})
    if not out:
        raise PipelineError("В плане нет ни одного клипа с start/end.")
    return out


def validate_clips(clips: list[dict[str, Any]] | list[Clip], duration: float) -> list[Clip]:
    dur = max(0.0, float(duration or 0))
    out: list[Clip] = []
    total = 0.0
    for item in clips:
        if isinstance(item, dict):
            start, end = float(item["start"]), float(item["end"])
        else:
            start, end = float(item[0]), float(item[1])
        if start > end:
            start, end = end, start
        start = max(0.0, start)
        if dur:
            end = min(end, dur)
            start = min(start, dur)
        if end - start < MIN_CLIP_SEC:
            continue
        remain = MAX_OUTPUT_SEC - total
        if remain < MIN_CLIP_SEC:
            break
        if end - start > remain:
            end = start + remain
        if end - start < MIN_CLIP_SEC:
            continue
        out.append((round(start, 3), round(end, 3)))
        total += end - start
        if len(out) >= MAX_CLIPS:
            break
    return out


def heuristic_plan(duration: float, brief: str = "") -> list[Clip]:
    """Простой монтаж без LLM: куски по длине ролика и запросу «N сек»."""
    dur = max(0.0, float(duration or 0))
    if dur < MIN_CLIP_SEC * 2:
        return []
    lo, hi = parse_target_range(brief)
    want = min(dur, max(lo, min(hi, dur if dur <= hi else (lo + hi) / 2)))
    if dur <= want + 0.5:
        return [(0.0, round(dur, 3))]
    n = min(MAX_CLIPS, max(2, int(round(want / 7.0))))
    clip_len = want / n
    margin = min(dur * 0.06, 4.0)
    usable = max(clip_len, dur - 2 * margin)
    step = usable / n if n else usable
    clips: list[Clip] = []
    for i in range(n):
        start = margin + i * step
        end = min(dur, start + clip_len)
        if end - start >= MIN_CLIP_SEC:
            clips.append((round(start, 3), round(end, 3)))
    return validate_clips(clips, dur)


def format_clips(clips: list[Clip]) -> str:
    parts = [f"{s:.1f}–{e:.1f}с" for s, e in clips]
    total = sum(e - s for s, e in clips)
    return f"{len(clips)} куск.: {', '.join(parts)} (≈{total:.0f} сек)"


async def _run_ffmpeg(args: list[str]) -> None:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _out, err = await proc.communicate()
    if proc.returncode != 0:
        log.warning("ffmpeg failed: %s", (err or b"")[:400])
        raise PipelineError("ffmpeg не смог обработать это видео. Другой файл или другие таймкоды.")


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


async def has_audio(path: Path) -> bool:
    if shutil.which("ffprobe") is None:
        return False
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=codec_type",
        "-of",
        "csv=p=0",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, _err = await proc.communicate()
    return b"audio" in (out or b"")


async def video_wh(path: Path) -> tuple[int, int]:
    if shutil.which("ffprobe") is None:
        return 720, 1280
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "csv=p=0:s=x",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, _err = await proc.communicate()
    raw = (out.decode("utf-8", "replace") or "").strip()
    try:
        w_s, h_s = raw.split("x", 1)
        w, h = int(w_s), int(h_s)
        w -= w % 2
        h -= h % 2
        if w >= 16 and h >= 16:
            return w, h
    except (TypeError, ValueError):
        pass
    return 720, 1280


def _check_output(path: Path) -> None:
    if not path.is_file() or path.stat().st_size < 1000:
        raise PipelineError("Готовый файл пустой — ffmpeg не собрал ролик.")
    if path.stat().st_size > MAX_OUTPUT_BYTES:
        raise PipelineError(
            "Готовый файл больше 49 МБ — Telegram его не отправит. Короткий кусок или меньше клипов."
        )


def _encode_args(dest: Path) -> list[str]:
    return [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-ar",
        "44100",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        str(dest),
    ]


async def cut_video(src: Path, dest: Path, start: float, end: float) -> Path:
    if shutil.which("ffmpeg") is None:
        raise PipelineError("На сервере нет ffmpeg — нарезка недоступна.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    duration = await media_duration(src)
    if duration and start >= duration:
        raise PipelineError(f"Начало {start:.1f}с за пределами ролика ({duration:.1f}с).")
    if duration:
        end = min(end, duration)
    if end - start < MIN_CLIP_SEC:
        raise PipelineError("После обрезки по длине файла кусок слишком короткий.")
    args = ["ffmpeg", "-y", "-i", str(src), "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-map", "0:v:0"]
    if await has_audio(src):
        args += ["-map", "0:a:0?"]
        await _run_ffmpeg(args + _encode_args(dest))
    else:
        await _run_ffmpeg(
            args
            + [
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "23",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(dest),
            ]
        )
    _check_output(dest)
    return dest


async def _normalize(src: Path, dest: Path, width: int, height: int) -> Path:
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps=30,setsar=1,format=yuv420p"
    )
    args = ["ffmpeg", "-y", "-i", str(src)]
    if await has_audio(src):
        args += ["-vf", vf]
        await _run_ffmpeg(args + _encode_args(dest))
    else:
        args += [
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=stereo",
            "-vf",
            vf,
            "-shortest",
        ]
        await _run_ffmpeg(args + _encode_args(dest))
    _check_output(dest)
    return dest


async def concat_videos(paths: list[Path], dest: Path) -> Path:
    if shutil.which("ffmpeg") is None:
        raise PipelineError("На сервере нет ffmpeg — склейка недоступна.")
    if len(paths) < 2:
        raise PipelineError("Для склейки нужно хотя бы два видео.")
    if len(paths) > MAX_CLIPS:
        raise PipelineError(f"Максимум {MAX_CLIPS} клипов за раз.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    total = 0.0
    for path in paths:
        total += await media_duration(path) or 0.0
    if total > MAX_OUTPUT_SEC + 0.5:
        raise PipelineError(
            f"Суммарная длина {total:.0f} сек, лимит {MAX_OUTPUT_SEC} сек (ограничение Telegram)."
        )
    width, height = await video_wh(paths[0])
    work = dest.parent
    normalized: list[Path] = []
    for i, src in enumerate(paths):
        npath = work / f"norm_{i:02d}.mp4"
        await _normalize(src, npath, width, height)
        normalized.append(npath)
    listing = work / "concat.txt"
    listing.write_text(
        "".join(f"file '{p.resolve()}'\n" for p in normalized),
        encoding="utf-8",
    )
    await _run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(listing),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(dest),
        ]
    )
    _check_output(dest)
    return dest


def check_incoming(*, size: int | None, duration: float | None) -> None:
    if size is not None and size > MAX_INPUT_BYTES:
        raise PipelineError("Файл больше 20 МБ — Telegram боту столько не отдаёт. Короткий ролик.")
    if duration is not None and duration > MAX_INPUT_SEC + 0.5:
        raise PipelineError(f"Ролик длиннее {MAX_INPUT_SEC} сек. Нарежь короче и пришли снова.")


async def render_clips(src: Path, dest: Path, clips: list[Clip]) -> Path:
    if not clips:
        raise PipelineError("Нет валидных кусков для склейки.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if len(clips) == 1:
        return await cut_video(src, dest, clips[0][0], clips[0][1])
    parts: list[Path] = []
    for i, (start, end) in enumerate(clips):
        piece = dest.parent / f"auto_{i:02d}.mp4"
        await cut_video(src, piece, start, end)
        parts.append(piece)
    return await concat_videos(parts, dest)


async def grok_edit_plan(session: aiohttp.ClientSession, *, duration: float, brief: str) -> str:
    if config.XAI_API_KEY_ERROR:
        raise PipelineError("Ключ Grok в неправильном формате.", config.XAI_API_KEY_ERROR)
    if not config.XAI_API_KEY_NEW:
        raise PipelineError("Нет XAI_API_KEY_NEW — авто-монтаж без плана не собрать.")
    user_content = (
        f"duration={duration:.2f}\n"
        f"max_out={MAX_OUTPUT_SEC}\n"
        f"max_clips={MAX_CLIPS}\n"
        f"Запрос пользователя:\n{(brief or '').strip()[:800]}"
    )
    messages = [
        {"role": "system", "content": EDIT_SYSTEM},
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
        payload = {"model": model, "messages": messages, "temperature": 0.2}
        for attempt in range(tries):
            try:
                async with session.post(
                    XAI_CHAT_URL,
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=90),
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
                            log.info("Grok edit plan ok model=%s", model)
                            return str(content)
                        last_err = f"{model}: пустой chat"
                    else:
                        last_err = f"{model} chat: {await _read_error(resp)}"
            except Exception as exc:
                last_err = f"{model} chat: {type(exc).__name__}"
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
                    timeout=aiohttp.ClientTimeout(total=90),
                ) as resp:
                    raw = await resp.text()
                    if resp.status in RETRY_STATUSES and attempt < tries - 1:
                        await sleep_backoff(attempt)
                        continue
                    if resp.status >= 400:
                        last_err = f"{model} responses: {_clip(f'HTTP {resp.status}', 200)}"
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
                    log.info("Grok edit plan responses ok model=%s", model)
                    return content
            except Exception as exc:
                last_err = f"{model} responses: {type(exc).__name__}"
                if attempt < tries - 1:
                    await sleep_backoff(attempt)
                    continue
    raise PipelineError("Не получил план монтажа от Grok.", last_err)


async def plan_clips(
    *, duration: float, brief: str, session: aiohttp.ClientSession | None = None
) -> tuple[list[Clip], str]:
    """Вернуть (клипы, источник: grok | heuristic). Без браузера."""
    fallback = heuristic_plan(duration, brief)
    try:
        own = session is None
        sess = session or aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=90))
        try:
            raw = await grok_edit_plan(sess, duration=duration, brief=brief)
        finally:
            if own:
                await sess.close()
        clips = validate_clips(parse_edit_plan(raw), duration)
        if clips:
            return clips, "grok"
    except Exception as exc:
        log.warning("edit auto grok fallback: %s", type(exc).__name__)
    if fallback:
        return fallback, "heuristic"
    raise PipelineError(
        "Не смог собрать план монтажа. Напиши таймкоды вручную или другое описание."
    )
