"""Локальный HTTP для Telegram Mini App. Снаружи — nginx + WEBAPP_PUBLIC_URL."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from aiohttp import web
from aiohttp.web_request import FileField

import config
from pipeline import PipelineError
from webapp_auth import WebAppAuthError, validate_init_data

log = logging.getLogger("videobot")

WEBAPP_DIR = Path(__file__).resolve().parent / "webapp"
MAX_BODY = 42 * 1024 * 1024


def _user_from_request(request: web.Request, form: Any) -> dict[str, Any]:
    init_data = ""
    if form is not None:
        init_data = str(form.get("initData") or form.get("init_data") or "")
    if not init_data:
        init_data = request.headers.get("X-Telegram-Init-Data") or ""
    return validate_init_data(init_data, config.VIDEOBOT_TELEGRAM_TOKEN)


def json_error(message: str, status: int = 400) -> web.Response:
    return web.json_response({"ok": False, "error": message}, status=status)


async def handle_index(_request: web.Request) -> web.FileResponse:
    return web.FileResponse(WEBAPP_DIR / "index.html")


async def handle_health(_request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "app": "videobot-studio"})


async def _read_file_field(form: Any, name: str) -> tuple[bytes, str, str]:
    field = form.get(name)
    if field is None:
        return b"", "", ""
    if isinstance(field, FileField):
        data = field.file.read() if field.file else b""
        return data or b"", str(field.filename or name), str(field.content_type or "")
    if isinstance(field, bytes):
        return field, name, ""
    return b"", "", ""


async def handle_quick(request: web.Request) -> web.Response:
    form = await request.post()
    try:
        user = _user_from_request(request, form)
    except WebAppAuthError as exc:
        return json_error(str(exc), 403)
    idea = str(form.get("idea") or "").strip()
    quality = str(form.get("quality") or "optimal")
    consent = str(form.get("consent") or "") in ("1", "true", "yes", "on")
    photo, _name, _mime = await _read_file_field(form, "photo")
    bot = request.app["bot"]
    asyncio.create_task(_run_safe(bot, user["id"], "quick", idea, quality, consent, photo))
    return web.json_response(
        {
            "ok": True,
            "message": "Снимаю ролик. Результат придёт в чат с ботом.",
            "close": True,
        }
    )


async def handle_upscale(request: web.Request) -> web.Response:
    form = await request.post()
    try:
        user = _user_from_request(request, form)
    except WebAppAuthError as exc:
        return json_error(str(exc), 403)
    data, filename, mime = await _read_file_field(form, "file")
    if not data:
        return json_error("Приложи фото или видео.")
    bot = request.app["bot"]
    asyncio.create_task(_run_safe(bot, user["id"], "upscale", data, filename, mime))
    return web.json_response(
        {"ok": True, "message": "Topaz в работе. Файл придёт в чат с ботом.", "close": True}
    )


async def handle_tryon(request: web.Request) -> web.Response:
    form = await request.post()
    try:
        user = _user_from_request(request, form)
    except WebAppAuthError as exc:
        return json_error(str(exc), 403)
    consent = str(form.get("consent") or "") in ("1", "true", "yes", "on")
    person, _, _ = await _read_file_field(form, "person")
    clothes, _, _ = await _read_file_field(form, "clothes")
    if not person or not clothes:
        return json_error("Нужны оба фото: человек и одежда.")
    bot = request.app["bot"]
    asyncio.create_task(_run_safe(bot, user["id"], "tryon", person, clothes, consent))
    return web.json_response(
        {"ok": True, "message": "Примерка пошла. Картинка придёт в чат.", "close": True}
    )


async def handle_clone(request: web.Request) -> web.Response:
    form = await request.post()
    try:
        user = _user_from_request(request, form)
    except WebAppAuthError as exc:
        return json_error(str(exc), 403)
    consent = str(form.get("consent") or "") in ("1", "true", "yes", "on")
    audio, filename, _mime = await _read_file_field(form, "audio")
    if not audio:
        return json_error("Приложи голосовое или аудиофайл.")
    bot = request.app["bot"]
    asyncio.create_task(_run_safe(bot, user["id"], "clone", audio, filename, consent))
    return web.json_response(
        {"ok": True, "message": "Клонирую голос. Напишу в чат, когда будет готово.", "close": True}
    )


async def _run_safe(bot: Any, user_id: int, kind: str, *args: Any) -> None:
    from studio import (
        job_error_text,
        run_studio_clone,
        run_studio_quick,
        run_studio_tryon,
        run_studio_upscale,
        send_chat_text,
    )

    try:
        if kind == "quick":
            idea, quality, consent, photo = args
            await run_studio_quick(
                bot,
                user_id,
                str(idea),
                quality=str(quality),
                photo_bytes=photo or None,
                consent=bool(consent),
            )
        elif kind == "upscale":
            data, filename, mime = args
            await run_studio_upscale(bot, user_id, data, str(filename), str(mime))
        elif kind == "tryon":
            person, clothes, consent = args
            await run_studio_tryon(bot, user_id, person, clothes, consent=bool(consent))
        elif kind == "clone":
            audio, filename, consent = args
            await run_studio_clone(bot, user_id, audio, str(filename), consent=bool(consent))
    except (PipelineError, Exception) as exc:
        try:
            await send_chat_text(bot, user_id, job_error_text(exc))
        except Exception:
            log.exception("studio notify failed kind=%s user=%s", kind, user_id)


def build_app(bot: Any) -> web.Application:
    app = web.Application(client_max_size=MAX_BODY)
    app["bot"] = bot
    app.router.add_get("/", handle_index)
    app.router.add_get("/index.html", handle_index)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/app.css", lambda _r: web.FileResponse(WEBAPP_DIR / "app.css"))
    app.router.add_get("/app.js", lambda _r: web.FileResponse(WEBAPP_DIR / "app.js"))
    app.router.add_post("/api/quick", handle_quick)
    app.router.add_post("/api/upscale", handle_upscale)
    app.router.add_post("/api/tryon", handle_tryon)
    app.router.add_post("/api/clone", handle_clone)
    return app


async def start_webapp(bot: Any) -> web.AppRunner | None:
    host = config.WEBAPP_HOST
    port = int(config.WEBAPP_PORT or 8088)
    app = build_app(bot)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    try:
        await site.start()
    except OSError as exc:
        log.warning("Mini App HTTP не слушает %s:%s (%s) — бот без webapp", host, port, exc)
        await runner.cleanup()
        return None
    public = config.WEBAPP_PUBLIC_URL or f"http://{host}:{port}/"
    log.info("Mini App HTTP %s:%s public=%s", host, port, public)
    return runner
