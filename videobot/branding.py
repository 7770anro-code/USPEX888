"""Обложка бота: Flux Dev still + titling Anro.AI. Без Kling."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import aiohttp

import config
from fal_api import fal_download_media, fal_run
from fal_models import FLUX_DEV, FLUX_PRO, flux_still_payload
from pipeline import PipelineError

log = logging.getLogger("videobot")

BRAND_NAME = "Успех 888"
COVER_MARK = "Anro.AI"
COVER_SUB = "AI VIDEO"
COVER_PROMPT = (
    "Cinematic action still, no text, no letters, no watermark, no logo, no signage. "
    "Night rain on a wet megacity street, a dark sports car slicing through neon "
    "reflections at speed, low tracking camera, heavy motion in rain streaks, "
    "anamorphic lens flare, sparks off the asphalt, photoreal 8k IMAX, premium "
    "AI video commercial, midnight black cyan and molten gold, 16:9 widescreen."
)
COVER_PROMPTS: tuple[str, ...] = (
    COVER_PROMPT,
    "Cinematic action still, no text, no letters, no watermark, no logo. "
    "Motorcycle tearing through a tunnel of cyan and amber light, sparks, "
    "heavy motion blur, wet asphalt, anamorphic flares, photoreal 8k, "
    "premium AI video product key art, 16:9 widescreen.",
    "Cinematic action still, no text, no letters, no watermark, no logo. "
    "Camera crane and crew silhouettes racing along a rooftop at night, "
    "city skyline exploding with gold volumetric light behind them, kinetic "
    "composition, photoreal 8k film still, expensive, 16:9 widescreen.",
    "Cinematic action still, no text, no letters, no watermark, no logo. "
    "Low-angle tracking shot of headlights ripping through fog, convoy energy, "
    "rain, neon signage out of focus so it is unreadable, photoreal 8k, "
    "premium car commercial, 16:9 widescreen.",
)

_FONTS = (
    "/usr/share/fonts/truetype/macos/Inter-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
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
    """Крупное Anro.AI по центру — точная латиница, не генерация Flux."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    font = _font()
    vf = (
        "scale=1920:1080:force_original_aspect_ratio=increase,"
        "crop=1920:1080,"
        "eq=contrast=1.06:saturation=1.05:brightness=-0.04,"
        "vignette=PI/4"
    )
    if font:
        escaped = font.replace(":", "\\:").replace("'", "\\'")
        mark = COVER_MARK.replace("'", "")
        sub = COVER_SUB.replace("'", "")
        vf += (
            f",drawtext=fontfile='{escaped}':text='{mark}':"
            "fontsize=168:fontcolor=0xF7F1E6:borderw=3:bordercolor=0x0B0907:"
            "x=(w-text_w)/2:y=(h-text_h)/2-18"
        )
        vf += (
            f",drawtext=fontfile='{escaped}':text='{sub}':"
            "fontsize=36:fontcolor=0xE8A04A:"
            "x=(w-text_w)/2:y=(h-text_h)/2+110"
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


def _copy_web(branded: Path) -> None:
    web = Path(__file__).resolve().parent / "webapp" / "cover.jpg"
    if web.parent.is_dir() and branded.resolve() != web.resolve():
        try:
            web.write_bytes(branded.read_bytes())
        except OSError as exc:
            log.warning("cover copy to webapp: %s", exc)


async def generate_cover(
    dest: Path | None = None,
    *,
    prompt: str = "",
    model: str = "",
) -> Path:
    """Flux Dev (копейки, заметно лучше Schnell). Kling/Seedance не зовём."""
    out = dest or (Path(config.DATA_DIR) / "brand" / "cover.jpg")
    out.parent.mkdir(parents=True, exist_ok=True)
    raw = out.with_name("cover_raw.jpg")
    model_id = (model or FLUX_DEV).strip() or FLUX_DEV
    text = (prompt or COVER_PROMPT).strip()
    timeout = aiohttp.ClientTimeout(total=180)
    payload = flux_still_payload(
        text,
        image_size="landscape_16_9",
        steps=28 if "schnell" not in model_id else None,
        guidance=3.5 if "schnell" not in model_id else None,
    )
    async with aiohttp.ClientSession(timeout=timeout) as session:
        data = await fal_run(session, model_id, payload, dest_id=raw)
        await fal_download_media(session, data, raw)
    if not raw.is_file() or raw.stat().st_size < 1024:
        raise PipelineError("fal.ai не сохранил still обложки.")
    branded = overlay_brand(raw, out)
    try:
        raw.unlink(missing_ok=True)
    except OSError:
        pass
    log.info("brand cover %s bytes=%s model=%s", branded, branded.stat().st_size, model_id)
    _copy_web(branded)
    return branded


async def generate_cover_variants(folder: Path, *, use_pro: bool = True) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    jobs: list[tuple[str, str, str]] = [
        (f"dev{i}", FLUX_DEV, prompt) for i, prompt in enumerate(COVER_PROMPTS, start=1)
    ]
    if use_pro:
        jobs.append(("pro1", FLUX_PRO, COVER_PROMPT))
    for name, model, prompt in jobs:
        dest = folder / f"{name}.jpg"
        try:
            path = await generate_cover(dest, prompt=prompt, model=model)
            out.append(path)
        except Exception as exc:
            log.warning("cover variant %s failed: %s", name, exc)
    if not out:
        raise PipelineError("Не собрал ни одного варианта обложки.")
    return out


if __name__ == "__main__":
    import argparse
    import asyncio

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--variants", action="store_true")
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    if args.variants:
        folder = Path(args.out or "/tmp/anro-covers")
        paths = asyncio.run(generate_cover_variants(folder))
        for path in paths:
            print(f"cover {path} bytes={path.stat().st_size}")
    else:
        dest = Path(args.out) if args.out else None
        out = asyncio.run(generate_cover(dest))
        print(f"cover {out} bytes={out.stat().st_size}")
