#!/usr/bin/env python3
"""A/B Kling v3 Pro I2V: одно фото vs frontal + доп. ракурсы (один Element).

Не прод. Жжёт кредиты fal.ai. Ключ не печатает. Не деплоить в videobot.service.

  PYTHONPATH=. python3 prototypes/kling_multiref_ab.py --photo /path/p1.jpg --out /tmp/kling-ab

Доп. ракурсы: fal-ai/flux-pulid (identity-preserving, не Flux schnell).
Kling: duration=3, generate_audio=false (~$0.34 за клип).
Оба клипа стартуют с исходного фото (start_image) — это не restage как Higgsfield Soul ID.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

log = logging.getLogger("kling_multiref_ab")

KLING = "fal-ai/kling-video/v3/pro/image-to-video"
PULID = "fal-ai/flux-pulid"
PROMPT = (
    "@Element1 is the same person, same face and clothes. "
    "Medium close-up of @Element1 in a dim executive lounge at dusk, "
    "slight handheld push-in, warm amber key light, cool rim light, "
    "photoreal live-action, vertical 9:16, no text overlay."
)
ANGLE_PROMPTS = (
    (
        "photoreal three-quarter portrait of a young man with dark bowl-cut bangs "
        "and a full dark beard, looking slightly to the left, indoor warm light, "
        "sharp face, natural skin, no text"
    ),
    (
        "photoreal close portrait of a young man with dark bowl-cut bangs "
        "and a full dark beard, looking slightly over his right shoulder, "
        "indoor light, sharp face, natural skin, no text"
    ),
)


def _setup_path() -> None:
    here = Path(__file__).resolve()
    root = here.parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


async def _download(session, url: str, dest: Path) -> Path:
    from pipeline import _download as pipe_download

    return await pipe_download(session, url, dest)


async def _pulid_angle(session, ref_url: str, prompt: str, dest: Path) -> str:
    from fal_api import extract_fal_media_url, fal_run

    data = await fal_run(
        session,
        PULID,
        {
            "prompt": prompt,
            "reference_image_url": ref_url,
            "image_size": "portrait_4_3",
            "id_weight": 1.0,
            "num_inference_steps": 20,
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
    log.info("kling saved %s bytes=%s", dest.name, dest.stat().st_size)


def _url_kind(url: str) -> str:
    u = (url or "").strip()
    if u.startswith("data:"):
        return f"data-uri len={len(u)}"
    if "://" in u:
        return u.split("?", 1)[0][:72]
    return f"other len={len(u)}"


def _payload_shape(body: dict) -> dict:
    """Без data-URI/лиц на диск — только форма elements."""
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
    return {"duration": body.get("duration"), "elements": els}


def _element(frontal_url: str, extra_angle_urls: list[str] | None = None) -> dict:
    """Один персонаж. На VPS импортируем из ветки; иначе дублируем хелпер, прод не трогаем."""
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


async def main_async(photo: Path, out: Path) -> int:
    import aiohttp

    from fal_api import path_to_fal_url

    out.mkdir(parents=True, exist_ok=True)
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=180)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        src_url = await path_to_fal_url(session, photo)
        log.info("source uploaded")
        extras: list[str] = []
        extra_files: list[str] = []
        for i, prompt in enumerate(ANGLE_PROMPTS, start=1):
            dest = out / f"extra_{i}.jpg"
            url = await _pulid_angle(session, src_url, prompt, dest)
            extras.append(url)
            extra_files.append(dest.name)

        base = {
            "prompt": PROMPT,
            "start_image_url": src_url,
            "duration": "3",
            "generate_audio": False,
            "negative_prompt": "blur, distort, low quality, extra people, watermark, text overlay, generic face",
        }
        a = dict(base)
        a["elements"] = [_element(src_url)]
        b = dict(base)
        b["elements"] = [_element(src_url, extras)]
        (out / "payload_a.json").write_text(
            json.dumps(_payload_shape(a), indent=2),
            encoding="utf-8",
        )
        (out / "payload_b.json").write_text(
            json.dumps({**_payload_shape(b), "extra_files": extra_files}, indent=2),
            encoding="utf-8",
        )
        await _kling(session, a, out / "a_single.mp4")
        await _kling(session, b, out / "b_multiref.mp4")
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
