#!/usr/bin/env python3
"""A/B2: cinematic-still-first + cfg/negative + Seedance 2.0 Fast Ref.

Не прод. Жжёт кредиты fal.ai. Ключ не печатает. Не деплоить в videobot.service.

  PYTHONPATH=. python3 prototypes/kling_restage_ab.py --photo /path/p1.jpg --out /tmp/kling-ab2

Бюджет ~$1.7:
  flux-pulid cinematic still (~$0.03)
  2× Kling v3 Pro I2V 3s audio off (~$0.67)
  1× Seedance 2.0 Fast Ref 4s 720p (~$0.97)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

log = logging.getLogger("kling_restage_ab")

KLING = "fal-ai/kling-video/v3/pro/image-to-video"
PULID = "fal-ai/flux-pulid"
SEEDANCE_REF_FAST = "bytedance/seedance-2.0/fast/reference-to-video"

NEG_DEFAULT = "blur, distort, and low quality"
NEG_FACE = (
    "blur, distort, and low quality, distorted face, warped features, uncanny, "
    "plastic skin, extra fingers, asymmetric eyes, extra people, watermark, "
    "text overlay, generic face"
)

CINEMATIC_STILL = (
    "photoreal cinematic medium close-up of the same young man, "
    "dark bowl-cut bangs covering the forehead, full dark groomed beard, "
    "wearing a black wool overcoat, seated in a dim executive lounge at dusk, "
    "leather chairs, warm amber key light, cool rim light, shallow depth of field, "
    "vertical portrait 9:16, natural skin pores, photoreal live-action, no text overlay"
)
KLING_MOTION = (
    "@Element1 is the same man as the start frame, keep his face. "
    "Slight handheld push-in, he breathes and blinks, subtle head turn. "
    "Photoreal live-action, no text overlay."
)
SEEDANCE_PROMPT = (
    "@Image1 is the real person, same face. "
    "Medium close-up of that man in a dim executive lounge at dusk, "
    "black wool overcoat, slight handheld push-in, warm amber key light, "
    "cool rim light, photoreal live-action, vertical 9:16, no text overlay."
)


def _setup_path() -> None:
    here = Path(__file__).resolve()
    root = here.parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


def _url_kind(url: str) -> str:
    u = (url or "").strip()
    if u.startswith("data:"):
        return f"data-uri len={len(u)}"
    if "://" in u:
        return u.split("?", 1)[0][:72]
    return f"other len={len(u)}"


def _payload_shape(body: dict) -> dict:
    els = []
    for el in body.get("elements") or []:
        refs = el.get("reference_image_urls") or []
        frontal = el.get("frontal_image_url") or ""
        els.append(
            {
                "frontal": _url_kind(frontal),
                "n_refs": len(refs),
                "refs": [_url_kind(u) for u in refs],
                "frontal_in_refs": frontal in refs,
            }
        )
    return {
        "duration": body.get("duration"),
        "cfg_scale": body.get("cfg_scale"),
        "negative": body.get("negative_prompt"),
        "start": _url_kind(str(body.get("start_image_url") or "")),
        "elements": els,
        "image_urls": [_url_kind(u) for u in (body.get("image_urls") or [])],
        "resolution": body.get("resolution"),
        "aspect_ratio": body.get("aspect_ratio"),
    }


def _element(frontal_url: str, extra_angle_urls: list[str] | None = None) -> dict:
    try:
        from fal_models import kling_same_person_element

        return kling_same_person_element(frontal_url, extra_angle_urls)
    except ImportError:
        frontal = (frontal_url or "").strip()
        extras: list[str] = []
        for raw in extra_angle_urls or []:
            url = (raw or "").strip()
            if not url or url == frontal or url in extras:
                continue
            extras.append(url)
            if len(extras) >= 3:
                break
        refs = extras[:3] if extras else ([frontal] if frontal else [])
        return {"frontal_image_url": frontal, "reference_image_urls": refs}


async def _download(session, url: str, dest: Path) -> Path:
    from pipeline import _download as pipe_download

    return await pipe_download(session, url, dest)


async def _https(session, photo: Path) -> str:
    from fal_api import path_to_fal_url, to_fal_https_url

    blob = await path_to_fal_url(session, photo)
    return await to_fal_https_url(session, blob)


async def _pulid(session, ref_url: str, dest: Path) -> str:
    from fal_api import extract_fal_media_url, fal_run

    data = await fal_run(
        session,
        PULID,
        {
            "prompt": CINEMATIC_STILL,
            "reference_image_url": ref_url,
            "image_size": "portrait_16_9",
            "id_weight": 1.0,
            "num_inference_steps": 20,
            "guidance_scale": 4,
            "negative_prompt": (
                "restaurant, selfie, dirty plate, wine rack, plastic skin, "
                "distorted face, extra fingers, text, watermark, cartoon"
            ),
            "enable_safety_checker": True,
        },
    )
    url = extract_fal_media_url(data)
    if not url:
        raise RuntimeError("flux-pulid не вернул картинку")
    await _download(session, url, dest)
    log.info("pulid saved %s bytes=%s", dest.name, dest.stat().st_size)
    return url


async def _kling(session, payload: dict, dest: Path) -> None:
    from fal_api import fal_download_media, fal_run

    data = await fal_run(session, KLING, payload, used_image=True, dest_id=dest)
    await fal_download_media(session, data, dest)
    log.info("kling saved %s bytes=%s cfg=%s", dest.name, dest.stat().st_size, payload.get("cfg_scale"))


async def _seedance(session, payload: dict, dest: Path) -> str:
    from fal_api import fal_download_media, fal_run
    from pipeline import PipelineError

    try:
        data = await fal_run(session, SEEDANCE_REF_FAST, payload, used_image=True, dest_id=dest)
        await fal_download_media(session, data, dest)
        log.info("seedance saved %s bytes=%s", dest.name, dest.stat().st_size)
        return "ok"
    except PipelineError as exc:
        code = getattr(exc, "code", "") or ""
        log.warning("seedance fail code=%s detail=%s", code, (exc.detail or str(exc))[:240])
        return code or "error"


async def main_async(photo: Path, out: Path) -> int:
    import aiohttp

    out.mkdir(parents=True, exist_ok=True)
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=180)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        selfie = await _https(session, photo)
        log.info("selfie https ready")
        still_url = await _pulid(session, selfie, out / "cinematic_still.jpg")

        kling_base = {
            "prompt": KLING_MOTION,
            "start_image_url": still_url,
            "duration": "3",
            "generate_audio": False,
            "elements": [_element(still_url, [selfie])],
        }
        k1 = dict(kling_base)
        k1["cfg_scale"] = 0.5
        k1["negative_prompt"] = NEG_DEFAULT
        k2 = dict(kling_base)
        k2["cfg_scale"] = 0.8
        k2["negative_prompt"] = NEG_FACE
        seed = {
            "prompt": SEEDANCE_PROMPT,
            "image_urls": [selfie],
            "duration": "4",
            "resolution": "720p",
            "aspect_ratio": "9:16",
            "generate_audio": False,
        }
        shapes = {
            "k1_cinematic_cfg05": _payload_shape(k1),
            "k2_cinematic_cfg08": _payload_shape(k2),
            "seedance_fast_ref": _payload_shape(seed),
        }
        (out / "payload_shapes.json").write_text(json.dumps(shapes, indent=2), encoding="utf-8")

        await _kling(session, k1, out / "k1_cfg05.mp4")
        await _kling(session, k2, out / "k2_cfg08.mp4")
        seed_status = await _seedance(session, seed, out / "s_ref.mp4")
        (out / "seedance_status.txt").write_text(seed_status + "\n", encoding="utf-8")
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    _setup_path()
    parser = argparse.ArgumentParser()
    parser.add_argument("--photo", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if not args.photo.is_file():
        log.error("нет фото %s", args.photo)
        return 2
    return asyncio.run(main_async(args.photo, args.out))


if __name__ == "__main__":
    raise SystemExit(main())
