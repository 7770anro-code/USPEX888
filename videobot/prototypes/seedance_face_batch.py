#!/usr/bin/env python3
"""Одноразовый пакет Seedance 2.0 Fast Ref: варианты 9, 8, 11, 13.

Не прод. Ключ не печатает. Не деплоить в videobot.service.

  PYTHONPATH=. python3 prototypes/seedance_face_batch.py \\
      --photos-dir /path/photos --still /path/cinematic_still.jpg --out /tmp/seedance-batch

Порядок: 9 → 8 → 11 → 13a/b/c. 11 = p3 (не p1). 13 = p1, p3, p4.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("seedance_face_batch")

SEEDANCE = "bytedance/seedance-2.0/fast/reference-to-video"
PROMPT_P1 = (
    "@Image1 is the real person, same face. "
    "Medium close-up of that man in a dim executive lounge at dusk, "
    "black wool overcoat, slight handheld push-in, warm amber key light, "
    "cool rim light, photoreal live-action, vertical 9:16, no text overlay."
)
PROMPT_STILL_PLUS = (
    "@Image1 is the cinematic look, wardrobe and lighting of the shot. "
    "@Image2 is the real face of the same man. "
    "Medium close-up of that man in a dim executive lounge at dusk, "
    "black wool overcoat, slight handheld push-in, warm amber key light, "
    "cool rim light, photoreal live-action, vertical 9:16, no text overlay."
)
PROMPT_OTHER = (
    "@Image1 is the real person in the photo, keep the same face. "
    "Medium close-up of that person in a dim executive lounge at dusk, "
    "dark tailored coat, slight handheld push-in, warm amber key light, "
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
        return u.split("?", 1)[0][:88]
    return f"other len={len(u)}"


def jobs_spec() -> list[dict]:
    """Порядок как выбрал владелец. 13 — три отдельных FACE."""
    return [
        {"id": "9", "photo_keys": ["still", "p1"], "prompt": PROMPT_STILL_PLUS},
        {"id": "8", "photo_keys": ["p1"], "prompt": PROMPT_P1},
        {"id": "11", "photo_keys": ["p3"], "prompt": PROMPT_OTHER},
        {"id": "13a", "photo_keys": ["p1"], "prompt": PROMPT_P1},
        {"id": "13b", "photo_keys": ["p3"], "prompt": PROMPT_OTHER},
        {"id": "13c", "photo_keys": ["p4"], "prompt": PROMPT_OTHER},
    ]


async def _https(session, path: Path) -> str:
    from fal_api import path_to_fal_url, to_fal_https_url

    blob = await path_to_fal_url(session, path)
    return await to_fal_https_url(session, blob)


def _extract_frames(video: Path, prefix: Path) -> tuple[Path, Path, Path]:
    t0, mid, end = (
        prefix.with_name(prefix.name + "_t0.jpg"),
        prefix.with_name(prefix.name + "_mid.jpg"),
        prefix.with_name(prefix.name + "_end.jpg"),
    )
    probes = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            str(video),
        ],
        text=True,
    ).strip()
    dur = float(probes or "4")
    mid_t = max(0.2, dur / 2)
    cmds = [
        ["ffmpeg", "-y", "-ss", "0.05", "-i", str(video), "-frames:v", "1", "-q:v", "2", str(t0)],
        ["ffmpeg", "-y", "-ss", f"{mid_t:.2f}", "-i", str(video), "-frames:v", "1", "-q:v", "2", str(mid)],
        ["ffmpeg", "-y", "-sseof", "-0.08", "-i", str(video), "-frames:v", "1", "-q:v", "2", str(end)],
    ]
    for cmd in cmds:
        subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return t0, mid, end


async def _run_one(session, job: dict, urls: dict[str, str], out: Path) -> dict:
    from fal_api import extract_fal_media_url, fal_download_media, fal_run, path_to_fal_url, to_fal_https_url
    from pipeline import PipelineError

    jid = job["id"]
    image_urls = [urls[k] for k in job["photo_keys"]]
    payload = {
        "prompt": job["prompt"],
        "image_urls": image_urls,
        "duration": "4",
        "resolution": "720p",
        "aspect_ratio": "9:16",
        "generate_audio": False,
    }
    dest = out / f"{jid}.mp4"
    row: dict = {
        "id": jid,
        "photos": job["photo_keys"],
        "image_kinds": [_url_kind(u) for u in image_urls],
        "status": "ok",
        "video": "",
        "t0": "",
        "mid": "",
        "end": "",
    }
    try:
        data = await fal_run(session, SEEDANCE, payload, used_image=True, dest_id=dest)
        video_url = extract_fal_media_url(data)
        await fal_download_media(session, data, dest)
        row["video"] = video_url
        log.info("VIDEO %s %s bytes=%s", jid, video_url, dest.stat().st_size)
        t0, mid, end = _extract_frames(dest, out / jid)
        for key, path in (("t0", t0), ("mid", mid), ("end", end)):
            blob = await path_to_fal_url(session, path)
            cdn = await to_fal_https_url(session, blob)
            row[key] = cdn
            log.info("FRAME %s %s %s", jid, key, cdn)
    except PipelineError as exc:
        row["status"] = getattr(exc, "code", "") or "error"
        row["detail"] = (exc.detail or str(exc))[:240]
        log.warning("FAIL %s code=%s detail=%s", jid, row["status"], row["detail"])
    return row


async def main_async(photos_dir: Path, still: Path, out: Path) -> int:
    import aiohttp

    out.mkdir(parents=True, exist_ok=True)
    needed = {
        "p1": photos_dir / "p1.jpg",
        "p3": photos_dir / "p3.jpg",
        "p4": photos_dir / "p4.jpg",
        "still": still,
    }
    for key, path in needed.items():
        if not path.is_file():
            log.error("нет файла %s %s", key, path)
            return 2
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=180)
    report = {"jobs": []}
    async with aiohttp.ClientSession(timeout=timeout) as session:
        urls = {key: await _https(session, path) for key, path in needed.items()}
        log.info("uploads ready keys=%s", ",".join(urls))
        for job in jobs_spec():
            row = await _run_one(session, job, urls, out)
            report["jobs"].append(row)
            (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    log.info("done jobs=%s", len(report["jobs"]))
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    _setup_path()
    parser = argparse.ArgumentParser()
    parser.add_argument("--photos-dir", required=True, type=Path)
    parser.add_argument("--still", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    return asyncio.run(main_async(args.photos_dir, args.still, args.out))


if __name__ == "__main__":
    raise SystemExit(main())
