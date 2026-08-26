"""Обложка «Успех 888»: Flux Schnell + лёгкий titling. Дешёвый still, без Kling."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import aiohttp

import config
from fal_api import fal_download_media, fal_run
from fal_models import FLUX_STILL, flux_still_payload
from pipeline import PipelineError

log = logging.getLogger("videobot")

BRAND_NAME = "Успех 888"
COVER_PROMPT = (
    "Cinematic luxury still, no text, no letters, no watermark, no logo. "
    "Dark graphite studio, warm amber volumetric light, gold dust in the air, "
    "a single anamorphic lens flare, vertical light beams, premium AI video lab, "
    "photoreal 8k film still, shallow depth, modern, expensive, midnight black "
    "and molten gold, abstract cinematic machinery softly out of focus, 16:9 widescreen."
)

_FONTS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
)


def cover_candidates() -> list[Path]:
    root = Path(__file__).resolve().parent
    return [
        root / "webapp" / "cover.jpg",
        Path(config.DATA_DIR) / "brand" / "cover.jpg",
    ]


def cover_path() -> Path | None:
    for path in cover_candidates():
        if path.is_file() and path.stat().st_size > 1024:
            return path
    return None


def _font() -> str:
    for path in _FONTS:
        if Path(path).is_file():
            return path
    return ""


def overlay_brand(src: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    font = _font()
    vf = (
        "scale=1920:1080:force_original_aspect_ratio=increase,"
        "crop=1920:1080,"
        "eq=contrast=1.04:saturation=0.92"
    )
    if font:
        escaped = font.replace(":", "\\:").replace("'", "\\'")
        title = BRAND_NAME.replace("'", "")
        vf += (
            f",drawtext=fontfile='{escaped}':text='{title}':"
            "fontsize=86:fontcolor=0xF3EADC:borderw=2:bordercolor=0x1A1208:"
            "x=(w-text_w)/2:y=h*0.78"
        )
        vf += (
            f",drawtext=fontfile='{escaped}':text='AI VIDEO':"
            "fontsize=28:fontcolor=0xE8A04A:"
            "x=(w-text_w)/2:y=h*0.78+96"
        )
    proc = subprocess.run(
        ["ffmpeg", "-y", "-i", str(src), "-vf", vf, "-frames:v", "1", "-q:v", "3", str(dest)],
        capture_output=True,
        timeout=90,
        check=False,
    )
    if proc.returncode != 0 or not dest.is_file() or dest.stat().st_size < 1024:
        detail = (proc.stderr or b"").decode("utf-8", "replace")[-400]
        raise PipelineError("Не собрал обложку ffmpeg.", detail)
    return dest


async def generate_cover(dest: Path | None = None) -> Path:
    """Flux Schnell → JPEG. Копейки, без Kling/Seedance."""
    out = dest or (Path(config.DATA_DIR) / "brand" / "cover.jpg")
    out.parent.mkdir(parents=True, exist_ok=True)
    raw = out.with_name("cover_raw.jpg")
    timeout = aiohttp.ClientTimeout(total=120)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        data = await fal_run(
            session,
            FLUX_STILL,
            flux_still_payload(COVER_PROMPT, image_size="landscape_16_9"),
            dest_id=raw,
        )
        await fal_download_media(session, data, raw)
    if not raw.is_file() or raw.stat().st_size < 1024:
        raise PipelineError("fal.ai не сохранил still обложки.")
    branded = overlay_brand(raw, out)
    try:
        raw.unlink(missing_ok=True)
    except OSError:
        pass
    log.info("brand cover %s bytes=%s", branded, branded.stat().st_size)
    web = Path(__file__).resolve().parent / "webapp" / "cover.jpg"
    if web.parent.is_dir() and branded.resolve() != web.resolve():
        try:
            web.write_bytes(branded.read_bytes())
        except OSError as exc:
            log.warning("cover copy to webapp: %s", exc)
    return branded


if __name__ == "__main__":
    import asyncio

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    out = asyncio.run(generate_cover())
    print(f"cover {out} bytes={out.stat().st_size}")
