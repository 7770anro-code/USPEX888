"""Нарезка и склейка пользовательских роликов. Только ffmpeg, без Grok/Runway/ElevenLabs."""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
from pathlib import Path

from pipeline import PipelineError

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
