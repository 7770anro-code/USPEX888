#!/usr/bin/env python3
"""№16 почти боевой Авторолик, не прод.

FACE = PuLID cinematic still + Seedance 2.0 Fast Ref (рецепт №9).
WIDE = Flux still + Seedance 2.5 I2V; при 422 likeness → Kling I2V (как прод).
Пишет только в --out. .env/data/videobot.service не меняет.

  PYTHONPATH=/opt/videobot python3 prototypes/autorolik16.py \\
    --script /opt/videobot/data/autorolik/6748280112.json \\
    --photos-dir /opt/videobot/data/autorolik/6748280112_photos \\
    --p1-still /tmp/kling-ab2/cinematic_still.jpg \\
    --orig-video /opt/videobot/data/last/6748280112.mp4 \\
    --out /tmp/autorolik-16
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("autorolik16")

PULID = "fal-ai/flux-pulid"
SEED_REF = "bytedance/seedance-2.0/fast/reference-to-video"
SEED_I2V = "bytedance/seedance-2.5/image-to-video"
KLING = "fal-ai/kling-video/v3/pro/image-to-video"
FLUX = "fal-ai/flux/schnell"
FACE_SEC = "4"
WIDE_SEC = "4"
KLING_SEC = "4"
ELEMENT_RE = re.compile(r"@Element\s*\d+", re.I)

PULID_PROMPT = (
    "photoreal cinematic medium close-up of the same person as the reference, "
    "wearing a dark tailored wool coat, seated in a dim executive lounge at dusk, "
    "warm amber key light, cool rim light, night city window bokeh, "
    "vertical portrait 9:16, natural skin pores, photoreal live-action, no text overlay"
)
COST = {
    "pulid": 0.035,
    "flux": 0.003,
    "seed_ref_s": 0.2419,
    "seed_i2v_s": 0.4730,
    "kling_s": 0.112,
}


def _setup_path() -> None:
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


def _strip_el(text: str) -> str:
    return re.sub(r"\s+", " ", ELEMENT_RE.sub("the person", text or "")).strip()


def _is_credits(exc: BaseException) -> bool:
    from pipeline import PipelineError, is_runway_credits_fail

    if not isinstance(exc, PipelineError):
        return False
    if getattr(exc, "code", "") == "credits":
        return True
    blob = f"{exc.detail or ''} {exc}".lower()
    return is_runway_credits_fail(exc.detail) or any(
        w in blob for w in ("exhausted balance", "user is locked", "insufficient", "out of credit")
    )


async def _https(session, path: Path) -> str:
    from fal_api import path_to_fal_url, to_fal_https_url

    return await to_fal_https_url(session, await path_to_fal_url(session, path))


async def _run(session, model: str, payload: dict, dest: Path | None = None) -> tuple[str, dict]:
    from fal_api import extract_fal_media_url, fal_download_media, fal_run

    data = await fal_run(session, model, payload, used_image=True, dest_id=dest)
    url = extract_fal_media_url(data)
    if dest is not None and url:
        await fal_download_media(session, data, dest)
    return url, data


def _slate(dest: Path, label: str) -> None:
    # Без drawtext: на VPS может не быть шрифта. Подпись только в логе/report.
    log.warning("slate %s %s", dest.name, label)
    subprocess.check_call(
        [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"color=c=black:s=720x1280:d={FACE_SEC}:r=24",
            "-pix_fmt", "yuv420p", "-c:v", "libx264", "-t", FACE_SEC, str(dest),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _normalize(src: Path, dest: Path) -> None:
    subprocess.check_call(
        [
            "ffmpeg", "-y", "-i", str(src),
            "-vf", "scale=720:1280:force_original_aspect_ratio=decrease,"
            "pad=720:1280:(ow-iw)/2:(oh-ih)/2,fps=24",
            "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", FACE_SEC, str(dest),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _concat(clips: list[Path], dest: Path) -> None:
    lst = dest.with_suffix(".txt")
    lst.write_text("".join(f"file '{c}'\n" for c in clips), encoding="utf-8")
    try:
        subprocess.check_call(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(dest)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if dest.is_file() and dest.stat().st_size > 1000:
            return
    except subprocess.CalledProcessError:
        log.warning("concat copy fail, re-encode")
    subprocess.check_call(
        [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(dest),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _extract_frames(video: Path, prefix: Path) -> tuple[Path, Path, Path]:
    t0 = prefix.with_name(prefix.name + "_t0.jpg")
    mid = prefix.with_name(prefix.name + "_mid.jpg")
    end = prefix.with_name(prefix.name + "_end.jpg")
    try:
        probes = subprocess.check_output(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=nw=1:nk=1", str(video),
            ],
            text=True,
        ).strip()
        dur = float(probes or FACE_SEC)
    except (subprocess.CalledProcessError, ValueError):
        dur = float(FACE_SEC)
    mid_t = max(0.2, dur / 2)
    cmds = [
        ["ffmpeg", "-y", "-ss", "0.05", "-i", str(video), "-frames:v", "1", "-q:v", "2", str(t0)],
        ["ffmpeg", "-y", "-ss", f"{mid_t:.2f}", "-i", str(video), "-frames:v", "1", "-q:v", "2", str(mid)],
        ["ffmpeg", "-y", "-sseof", "-0.08", "-i", str(video), "-frames:v", "1", "-q:v", "2", str(end)],
    ]
    for cmd in cmds:
        subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return t0, mid, end


def _mux_audio(video: Path, audio_src: Path, dest: Path) -> None:
    subprocess.check_call(
        [
            "ffmpeg", "-y", "-i", str(video), "-i", str(audio_src),
            "-map", "0:v:0", "-map", "1:a:0?", "-c:v", "copy", "-c:a", "aac",
            "-shortest", "-movflags", "+faststart", str(dest),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


async def _pulid(session, ref: str, dest: Path) -> str:
    from fal_api import extract_fal_media_url, fal_run
    from pipeline import _download

    data = await fal_run(
        session,
        PULID,
        {
            "prompt": PULID_PROMPT,
            "reference_image_url": ref,
            "image_size": "portrait_16_9",
            "id_weight": 1.0,
            "num_inference_steps": 20,
            "enable_safety_checker": True,
        },
    )
    url = extract_fal_media_url(data)
    if not url:
        raise RuntimeError("pulid empty")
    await _download(session, url, dest)
    return url


def _face_prompt(visual: str, n_images: int) -> str:
    scene = _strip_el(visual) or "medium close-up in a dim executive lounge at dusk"
    if n_images >= 4:
        return (
            "@Image1 is cinematic look and wardrobe of person A. @Image2 is the real face of person A. "
            "@Image3 is cinematic look of person B. @Image4 is the real face of person B. "
            f"{scene} photoreal live-action, vertical 9:16, no text overlay."
        )
    return (
        "@Image1 is the cinematic look, wardrobe and lighting. "
        "@Image2 is the real face of the same person. "
        f"{scene} slight handheld push-in, photoreal live-action, vertical 9:16, no text overlay."
    )


async def main_async(args: argparse.Namespace) -> int:
    import aiohttp
    from fal_api import path_to_fal_url, to_fal_https_url
    from pipeline import PipelineError

    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    raw = json.loads(Path(args.script).read_text(encoding="utf-8"))
    script = raw.get("script") or raw
    scenes = list(script.get("scenes") or [])
    photos_dir: Path = args.photos_dir
    photos = [photos_dir / f"p{i}.jpg" for i in range(1, 7)]
    report: dict = {"title": script.get("title"), "scenes": [], "est_usd": 0.0, "notes": []}
    est = 0.0

    timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=180)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        https: dict[int, str] = {}
        stills: dict[int, str] = {}
        needed = set()
        for sc in scenes:
            if sc.get("face_scene"):
                ei = int(sc.get("element_index") or 1)
                needed.add(ei)
                vis = str(sc.get("visual_prompt") or "")
                if "@Element6" in vis or "@element6" in vis.lower():
                    needed.add(6)
        for idx in sorted(needed):
            path = photos[idx - 1]
            if not path.is_file():
                report["notes"].append(f"нет фото p{idx}")
                continue
            try:
                https[idx] = await _https(session, path)
            except Exception as exc:
                if _is_credits(exc):
                    report["notes"].append(f"credits on upload p{idx}: {(getattr(exc,'detail',None) or str(exc))[:180]}")
                    report["est_usd"] = round(est, 2)
                    (out / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
                    log.error("credits locked on upload p%s", idx)
                    print("CREDITS_LOCKED")
                    print("EST_USD 0.0")
                    return 3
                raise
            reuse = args.p1_still if idx == 1 and args.p1_still and Path(args.p1_still).is_file() else None
            dest = out / f"still_p{idx}.jpg"
            try:
                if reuse:
                    stills[idx] = await _https(session, Path(reuse))
                    log.info("reuse p1 still")
                else:
                    stills[idx] = await _pulid(session, https[idx], dest)
                    est += COST["pulid"]
                    log.info("pulid p%s %s", idx, dest.name)
            except Exception as exc:
                if _is_credits(exc):
                    report["notes"].append(f"credits on PuLID p{idx}")
                    report["est_usd"] = round(est, 2)
                    (out / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
                    log.error("credits locked on pulid p%s", idx)
                    print("CREDITS_LOCKED")
                    print("EST_USD 0.0")
                    return 3
                report["notes"].append(f"PuLID p{idx} fail: {type(exc).__name__}")
                log.warning("pulid p%s fail %s", idx, exc)

        clips: list[Path] = []
        for i, sc in enumerate(scenes):
            n = i + 1
            face = bool(sc.get("face_scene"))
            vis = str(sc.get("visual_prompt") or "")
            row: dict = {"n": n, "face": face, "status": "ok", "model": "", "video": "", "detail": ""}
            dest = out / f"c{i}.mp4"
            try:
                if face:
                    ei = int(sc.get("element_index") or 1)
                    urls = []
                    if stills.get(ei):
                        urls.append(stills[ei])
                    if https.get(ei):
                        urls.append(https[ei])
                    if n == 8 and stills.get(6) and https.get(6):
                        urls.extend([stills[6], https[6]])
                    elif n == 8 and https.get(6):
                        urls.append(https[6])
                    if len(urls) < 1:
                        raise RuntimeError("no face refs")
                    payload = {
                        "prompt": _face_prompt(vis, len(urls)),
                        "image_urls": urls[:9],
                        "duration": FACE_SEC,
                        "resolution": "720p",
                        "aspect_ratio": "9:16",
                        "generate_audio": False,
                    }
                    try:
                        url, _ = await _run(session, SEED_REF, payload, dest)
                        row["model"] = SEED_REF
                        row["video"] = url
                        est += COST["seed_ref_s"] * int(FACE_SEC)
                    except PipelineError as exc:
                        row["detail"] = f"{getattr(exc,'code','')}:{(exc.detail or '')[:180]}"
                        if _is_credits(exc):
                            row["status"] = "credits"
                            report["scenes"].append(row)
                            report["est_usd"] = round(est, 2)
                            report["notes"].append(f"credits stop at FACE scene {n}")
                            (out / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
                            log.error("credits stop at scene %s", n)
                            break
                        if https.get(ei) and (not stills.get(ei) or len(urls) > 1):
                            payload["image_urls"] = [https[ei]]
                            payload["prompt"] = (
                                "@Image1 is the real person, same face. "
                                + _strip_el(vis)
                                + " photoreal 9:16, no text."
                            )
                            url, _ = await _run(session, SEED_REF, payload, dest)
                            row["model"] = SEED_REF + "+selfie_only"
                            row["video"] = url
                            row["status"] = "fallback_selfie"
                            est += COST["seed_ref_s"] * int(FACE_SEC)
                            log.warning("scene %s FACE fail → selfie-only ok code=%s", n, getattr(exc, "code", ""))
                        else:
                            raise
                else:
                    from fal_models import flux_still_payload, kling_i2v_payload, seedance_i2v_payload

                    wide_prompt = (
                        "cinematic photoreal establishing shot, no recognizable faces, no portraits, "
                        f"{_strip_el(vis)}, warm sunset amber and cool club backlight, vertical 9:16, no text"
                    )
                    still_path = out / f"wide_{i}.jpg"
                    still_url, _ = await _run(session, FLUX, flux_still_payload(wide_prompt), still_path)
                    est += COST["flux"]
                    from pipeline import _download

                    if still_url:
                        await _download(session, still_url, still_path)
                        still_https = still_url if still_url.startswith("http") else await _https(session, still_path)
                    else:
                        still_https = await _https(session, still_path)
                    try:
                        url, _ = await _run(
                            session,
                            SEED_I2V,
                            seedance_i2v_payload(_strip_el(vis), still_https, int(WIDE_SEC)),
                            dest,
                        )
                        row["model"] = SEED_I2V
                        row["video"] = url
                        est += COST["seed_i2v_s"] * int(WIDE_SEC)
                    except PipelineError as exc:
                        row["detail"] = f"{getattr(exc,'code','')}:{(exc.detail or '')[:180]}"
                        if _is_credits(exc):
                            row["status"] = "credits"
                            report["scenes"].append(row)
                            report["est_usd"] = round(est, 2)
                            report["notes"].append(f"credits stop at WIDE scene {n}")
                            (out / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
                            log.error("credits stop at scene %s", n)
                            break
                        kpayload = kling_i2v_payload(_strip_el(vis), still_https, int(KLING_SEC), photo_lock=False)
                        url, _ = await _run(session, KLING, kpayload, dest)
                        row["model"] = KLING + "+wide_fallback"
                        row["video"] = url
                        row["status"] = "wide_kling_fallback"
                        est += COST["kling_s"] * int(KLING_SEC)
                        log.warning("scene %s WIDE fail → Kling code=%s", n, getattr(exc, "code", ""))
            except Exception as exc:
                from pipeline import PipelineError as PE

                code = getattr(exc, "code", "") if isinstance(exc, PE) else ""
                row["status"] = code or "error"
                row["detail"] = (getattr(exc, "detail", None) or str(exc))[:240]
                log.warning("scene %s fail %s %s", n, row["status"], row["detail"])
                _slate(dest, f"scene {n} fail")
            if dest.is_file() and dest.stat().st_size > 1000:
                norm = out / f"n{i}.mp4"
                try:
                    _normalize(dest, norm)
                    clips.append(norm if norm.is_file() else dest)
                except subprocess.CalledProcessError:
                    clips.append(dest)
                try:
                    t0, mid, end = _extract_frames(dest, out / f"s{n}")
                    for key, path in (("t0", t0), ("mid", mid), ("end", end)):
                        if path.is_file():
                            row[key] = await to_fal_https_url(session, await path_to_fal_url(session, path))
                except Exception as frame_exc:
                    row["frame_err"] = type(frame_exc).__name__
            report["scenes"].append(row)
            report["est_usd"] = round(est, 2)
            (out / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
            log.info("scene %s %s %s", n, row["status"], row["model"])

    if not clips:
        log.error("нет клипов")
        return 2
    silent = out / "silent.mp4"
    _concat(clips, silent)
    final = out / "final.mp4"
    orig = Path(args.orig_video) if args.orig_video else None
    if orig and orig.is_file():
        try:
            _mux_audio(silent, orig, final)
        except subprocess.CalledProcessError:
            silent.replace(final)
            report["notes"].append("не смог наложить исходную озвучку")
    else:
        silent.replace(final)
        report["notes"].append("исходного mp4 нет — без озвучки")

    from fal_api import path_to_fal_url, to_fal_https_url
    import aiohttp

    async with aiohttp.ClientSession() as session:
        blob = await path_to_fal_url(session, final)
        cdn = await to_fal_https_url(session, blob)
        try:
            ft0, fmid, fend = _extract_frames(final, out / "final")
            report["final_t0"] = await to_fal_https_url(session, await path_to_fal_url(session, ft0))
            report["final_mid"] = await to_fal_https_url(session, await path_to_fal_url(session, fmid))
            report["final_end"] = await to_fal_https_url(session, await path_to_fal_url(session, fend))
        except Exception as frame_exc:
            report["notes"].append(f"final frames fail: {type(frame_exc).__name__}")
    report["final"] = cdn
    report["final_bytes"] = final.stat().st_size
    report["est_usd"] = round(est, 2)
    (out / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("FINAL %s est=$%s", cdn, report["est_usd"])
    print(f"FINAL {cdn}")
    print(f"EST_USD {report['est_usd']}")
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    _setup_path()
    p = argparse.ArgumentParser()
    p.add_argument("--script", type=Path, required=True)
    p.add_argument("--photos-dir", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--p1-still", type=Path, default=None)
    p.add_argument("--orig-video", type=Path, default=None)
    args = p.parse_args()
    if not args.script.is_file():
        log.error("нет script")
        return 2
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
