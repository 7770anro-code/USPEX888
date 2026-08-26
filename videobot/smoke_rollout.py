#!/usr/bin/env python3
"""Короткий smoke перед деплоем. Не рестартит бота, секреты в лог не пишет.

  python3 smoke_rollout.py           # маршруты, Grok, ffmpeg-монтаж, Mini App
  python3 smoke_rollout.py --live    # + 1 still Flux и по одному короткому Kling/Seedance
  python3 smoke_rollout.py --live-only

Live жжёт кредиты fal.ai. Нужен FAL_KEY или FAL_API_KEY.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
import sys
import tempfile
from pathlib import Path

log = logging.getLogger("videobot.smoke")


def _ok(name: str, detail: str = "") -> None:
    extra = f" — {detail}" if detail else ""
    print(f"OK  {name}{extra}")


def _fail(name: str, detail: str) -> None:
    print(f"FAIL  {name} — {detail}", file=sys.stderr)


def smoke_routing() -> None:
    import config
    from fal_api import fal_headers
    from fal_models import kling_i2v_payload, seedance_i2v_payload
    from provider_router import ROUTING, chain_for

    assert ROUTING["real_photo"][0] == "kling"
    assert ROUTING["synthetic_multi_scene"][0] == "seedance"
    assert ROUTING["night_pipeline"][0] == "seedance"
    assert ROUTING["montage_generate"][0] == "seedance"
    assert ROUTING["autorolik_face"][0] == "kling"
    assert ROUTING["autorolik_wide"][0] == "seedance"
    for mode in (
        "real_photo",
        "synthetic_multi_scene",
        "night_pipeline",
        "montage_generate",
        "autorolik_face",
        "autorolik_wide",
    ):
        assert "legacy_runway" not in ROUTING[mode], mode
        assert "kling" in ROUTING[mode] and "seedance" in ROUTING[mode]
        assert "legacy_runway" not in chain_for(mode), mode
    assert chain_for("real_photo")[0] == "kling"
    assert chain_for("night_pipeline")[0] == "seedance"
    assert chain_for("autorolik_face")[0] == "kling"
    assert chain_for("autorolik_wide")[0] == "seedance"
    assert chain_for("autorolik_wide")[1] == "kling"
    router_src = Path(__file__).with_name("provider_router.py").read_text(encoding="utf-8")
    assert "seedance likeness — retry this clip with Kling" in router_src
    night_src = Path(__file__).with_name("night_video.py").read_text(encoding="utf-8")
    assert 'route_mode="night_pipeline"' in night_src
    kling = kling_i2v_payload("walk", "https://example.com/a.jpg", 5, photo_lock=True)
    assert kling["generate_audio"] is False
    assert kling["elements"]
    assert kling["elements"][0]["frontal_image_url"] == "https://example.com/a.jpg"
    assert kling["elements"][0]["reference_image_urls"] == ["https://example.com/a.jpg"]
    from fal_api import fal_try_resume, keep_fal_sidecar
    from pipeline import PipelineError

    assert keep_fal_sidecar(PipelineError("x", code="fal_keep_sidecar")) is True
    assert "fal_try_resume" in Path(__file__).with_name("fal_api.py").read_text(encoding="utf-8")
    seed = seedance_i2v_payload("walk", "https://example.com/a.jpg", 5)
    assert seed["duration"] == "5"
    assert seed["generate_audio"] is False
    if config.FAL_KEY:
        auth = fal_headers()["Authorization"]
        assert auth.startswith("Key ")
        assert "Bearer" not in auth
    _ok(
        "routing",
        "1клик/фото/ночь/вайб/авторолик=Kling+Seedance без Runway",
    )


def smoke_miniapp() -> None:
    from webapp_server import build_app
    from test_parse import test_webapp_init_data_hmac_includes_signature

    app = build_app(bot=None)
    paths = set()
    for route in app.router.routes():
        info = route.resource.get_info() if route.resource else {}
        paths.add(info.get("path") or info.get("formatter") or "")
    for need in (
        "/api/quick",
        "/api/vibe",
        "/api/tryon",
        "/api/clone",
        "/api/autorolik",
        "/api/autorolik/status",
        "/api/autorolik/shoot",
        "/api/autorolik/script",
        "/cover.jpg",
        "/health",
    ):
        assert need in paths, need
    html = Path(__file__).with_name("webapp").joinpath("index.html").read_text(encoding="utf-8")
    assert html.count('class="sub"') == 7
    assert "go-auto-shoot" in html
    assert "go-auto-save" in html
    assert "go-auto-refresh" in html
    assert "Обновить статус" in html
    assert "cover.jpg" in html
    assert "Anro.AI" in html
    cover = Path(__file__).with_name("webapp").joinpath("cover.jpg")
    assert cover.is_file() and cover.stat().st_size > 1024
    js = Path(__file__).with_name("webapp").joinpath("app.js").read_text(encoding="utf-8")
    assert "go-auto-refresh" in js
    assert "phase === \"stale\"" in js or "phase === 'stale'" in js
    assert "/api/autorolik/script" in js
    assert "authHeaders" in js
    assert "X-Telegram-Init-Data" in js
    assert "upload_failed" in js
    assert "Загрузка фото оборвалась" in js
    test_webapp_init_data_hmac_includes_signature()
    _ok("miniapp", "7 карточек, HMAC, правки сцен, обложка, Обновить статус, обрыв загрузки")


async def smoke_idea_script() -> None:
    import aiohttp
    from pipeline import grok_script

    timeout = aiohttp.ClientTimeout(total=120)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        script = await grok_script(
            session,
            "лестница микро",
            style="cinematic",
            n_scenes=4,
            dynamic_pacing=True,
        )
    scenes = script.get("scenes") or []
    assert len(scenes) >= 4, f"scenes={len(scenes)}"
    _ok("idea→script", f"Grok {len(scenes)} сцен, title={script.get('title')!r}"[:120])


async def smoke_voice() -> None:
    import aiohttp
    from pipeline import eleven_tts
    from voices import voice_by_index

    voice = voice_by_index(1)
    timeout = aiohttp.ClientTimeout(total=90)
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "n0.mp3"
        async with aiohttp.ClientSession(timeout=timeout) as session:
            out = await eleven_tts(session, "Короткий тест озвучки.", dest, voice_id=voice["id"])
        size = out.stat().st_size
        assert size >= 200, size
    _ok("voice", f"ElevenLabs {size} байт, пресет {voice['name']}")


async def smoke_montage() -> None:
    from edit import concat_videos, cut_video, media_duration

    if shutil.which("ffmpeg") is None:
        raise RuntimeError("нет ffmpeg")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        src = root / "src.mp4"
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=12:size=720x1280:rate=24",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=12",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(src),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _out, err = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(err.decode("utf-8", "replace")[-300:])
        a = await cut_video(src, root / "a.mp4", 1.0, 5.0)
        b = await cut_video(src, root / "b.mp4", 6.0, 10.0)
        out = await concat_videos([a, b], root / "montage.mp4")
        dur = await media_duration(out)
        assert out.stat().st_size > 1000
        assert dur and 6.0 <= dur <= 10.0, dur
    _ok("montage", f"ffmpeg cut+concat {dur:.1f}с, fal не вызывали")


async def smoke_live_fal() -> None:
    import aiohttp
    import config
    from fal_api import extract_fal_media_url, fal_run
    from fal_models import FLUX_STILL, flux_still_payload
    from pipeline import PipelineError
    from providers.fal_client import FalClient

    if not config.FAL_KEY:
        raise RuntimeError("нет FAL_KEY/FAL_API_KEY — live Kling/Seedance пропущен")
    work = Path(tempfile.mkdtemp(prefix="fal_smoke_"))
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=180)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        try:
            data = await fal_run(session, FLUX_STILL, flux_still_payload(
                "cinematic still, empty rainy street at night, no people, vertical 9:16, photoreal"
            ))
        except PipelineError as exc:
            raise PipelineError(
                exc.user_message,
                f"flux: {exc.detail}",
                code=getattr(exc, "code", "") or "",
            ) from exc
        still = extract_fal_media_url(data)
        if not still.startswith("http"):
            keys = ",".join(sorted(data.keys())) if isinstance(data, dict) else type(data).__name__
            raise RuntimeError(f"Flux не вернул URL still, keys={keys}")
        _ok("live still", "Flux Schnell")
        kling_dest = work / "kling.mp4"
        seed_dest = work / "seedance.mp4"
        client_k = FalClient(session, engine="kling")
        client_s = FalClient(session, engine="seedance")
        await client_k.generate_kling(
            session,
            "slow push-in along an empty rainy street, photoreal, no people",
            still,
            3,
            kling_dest,
            photo_lock=False,
        )
        ksz = kling_dest.stat().st_size
        assert ksz > 8000, ksz
        _ok("live kling", f"I2V 3с, {ksz} байт (маршрут real_photo)")
        await client_s.generate_seedance(
            session,
            "gentle camera drift on an empty rainy street, photoreal, no people",
            still,
            4,
            seed_dest,
        )
        ssz = seed_dest.stat().st_size
        assert ssz > 8000, ssz
        _ok("live seedance", f"I2V 4с, {ssz} байт (маршрут synth/night/montage)")
    shutil.rmtree(work, ignore_errors=True)


async def async_main(*, live: bool, live_only: bool = False) -> int:
    failed: list[str] = []
    if not live_only:
        smoke_routing()
        smoke_miniapp()
        try:
            await smoke_idea_script()
        except Exception as extra:
            _fail("idea→script", f"{type(extra).__name__}: {extra}")
            failed.append("idea")
        try:
            await smoke_voice()
        except Exception as extra:
            _fail("voice", f"{type(extra).__name__}: {extra}")
            failed.append("voice")
        try:
            await smoke_montage()
        except Exception as extra:
            _fail("montage", f"{type(extra).__name__}: {extra}")
            failed.append("montage")
    if live or live_only:
        try:
            await smoke_live_fal()
        except Exception as extra:
            detail = getattr(extra, "detail", "") or ""
            code = getattr(extra, "code", "")
            status = getattr(extra, "status", "")
            _fail(
                "live fal",
                f"{type(extra).__name__}: {extra} | status={status} code={code} {str(detail)[:280]}",
            )
            failed.append("live")
    elif not live_only:
        print("SKIP  live fal — нет --live (нужен FAL_API_KEY, жжёт кредиты)")
    if failed:
        print("SMOKE FAIL: " + ", ".join(failed))
        return 1
    print("SMOKE OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description="VideoBot rollout smoke")
    parser.add_argument("--live", action="store_true", help="Kling+Seedance на fal.ai")
    parser.add_argument("--live-only", action="store_true", help="только live fal, без Grok/монтажа")
    args = parser.parse_args(argv)
    return asyncio.run(async_main(live=bool(args.live), live_only=bool(args.live_only)))


if __name__ == "__main__":
    raise SystemExit(main())
