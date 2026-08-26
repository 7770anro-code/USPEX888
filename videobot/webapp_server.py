"""Локальный HTTP для Telegram Mini App. Снаружи — nginx + WEBAPP_PUBLIC_URL."""

from __future__ import annotations

import asyncio
import json
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
_JOBS: set[asyncio.Task[Any]] = set()


def _spawn(coro: Any) -> None:
    """Джоба живёт на event loop, а не на HTTP-запросе Mini App. Закрытие Telegram её не отменяет."""
    task = asyncio.create_task(coro)
    _JOBS.add(task)
    task.add_done_callback(_JOBS.discard)


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
    _spawn(_run_safe(bot, user["id"], "quick", idea, quality, consent, photo))
    return web.json_response(
        {
            "ok": True,
            "message": "Снимаю ролик. Результат придёт в чат с ботом.",
            "close": True,
        }
    )


async def handle_vibe(request: web.Request) -> web.Response:
    form = await request.post()
    try:
        user = _user_from_request(request, form)
    except WebAppAuthError as exc:
        return json_error(str(exc), 403)
    vibe = str(form.get("vibe") or form.get("idea") or "").strip()
    if len(vibe) < 3:
        return json_error("Напиши вайб или тему: хотя бы 2–3 слова.")
    bot = request.app["bot"]
    _spawn(_run_safe(bot, user["id"], "vibe", vibe))
    return web.json_response(
        {"ok": True, "message": "Снимаю вайб. Результат придёт в чат.", "close": True}
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
    _spawn(_run_safe(bot, user["id"], "upscale", data, filename, mime))
    return web.json_response(
        {"ok": True, "message": "Topaz в работе. Файл придёт в чат с ботом.", "close": True}
    )


async def handle_interpolate(request: web.Request) -> web.Response:
    form = await request.post()
    try:
        user = _user_from_request(request, form)
    except WebAppAuthError as exc:
        return json_error(str(exc), 403)
    data, filename, mime = await _read_file_field(form, "file")
    if not data:
        return json_error("Приложи видео для слоу-мо.")
    bot = request.app["bot"]
    _spawn(_run_safe(bot, user["id"], "interpolate", data, filename, mime))
    return web.json_response(
        {"ok": True, "message": "Делаю слоу-мо. Файл придёт в чат.", "close": True}
    )


async def handle_restore(request: web.Request) -> web.Response:
    form = await request.post()
    try:
        user = _user_from_request(request, form)
    except WebAppAuthError as exc:
        return json_error(str(exc), 403)
    data, filename, mime = await _read_file_field(form, "file")
    if not data:
        return json_error("Приложи фото для реставрации.")
    bot = request.app["bot"]
    _spawn(_run_safe(bot, user["id"], "restore", data, filename, mime))
    return web.json_response(
        {"ok": True, "message": "Реставрирую фото. Картинка придёт в чат.", "close": True}
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
    _spawn(_run_safe(bot, user["id"], "tryon", person, clothes, consent))
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
    _spawn(_run_safe(bot, user["id"], "clone", audio, filename, consent))
    return web.json_response(
        {"ok": True, "message": "Клонирую голос MiniMax. Напишу в чат, когда будет готово.", "close": True}
    )


async def _read_photos(form: Any) -> list[bytes]:
    blobs: list[bytes] = []
    seen: set[int] = set()

    def _add(data: bytes) -> None:
        if not data:
            return
        key = hash(data)
        if key in seen:
            return
        seen.add(key)
        blobs.append(data)

    for i in range(1, 7):
        data, _name, _mime = await _read_file_field(form, f"photo{i}")
        _add(data)
    data, _name, _mime = await _read_file_field(form, "photo")
    _add(data)
    if hasattr(form, "getall") and "photos" in form:
        for field in form.getall("photos") or []:
            if isinstance(field, FileField) and field.file:
                _add(field.file.read() or b"")
            elif isinstance(field, bytes):
                _add(field)
    return blobs


async def handle_autorolik(request: web.Request) -> web.Response:
    form = await request.post()
    try:
        user = _user_from_request(request, form)
    except WebAppAuthError as exc:
        return json_error(str(exc), 403)
    consent = str(form.get("consent") or "") in ("1", "true", "yes", "on")
    topic = str(form.get("topic") or form.get("idea") or "").strip()
    photos = await _read_photos(form)
    if len(photos) > 6:
        return json_error("Максимум 6 фото.")
    if not photos:
        return json_error("Нужно хотя бы одно фото.")
    if not consent:
        return json_error("Без согласия фото людей не использую.")
    bot = request.app["bot"]
    _spawn(_run_safe(bot, user["id"], "autorolik", photos, consent, topic))
    return web.json_response(
        {
            "ok": True,
            "phase": "scripting",
            "message": "Пишу сценарий. Можно закрыть Telegram — план и кнопки «Снять» придут в чат.",
            "close": False,
        }
    )


async def handle_autorolik_status(request: web.Request) -> web.Response:
    form = await request.post()
    try:
        user = _user_from_request(request, form)
    except WebAppAuthError as exc:
        return json_error(str(exc), 403)
    from autorolik import load_pending, pending_view
    from live_status import get_job, job_key_manual, status_payload

    pending = load_pending(int(user["id"]))
    snap = get_job(job_key_manual(int(user["id"])))
    view = pending_view(pending)
    return web.json_response(
        {
            "ok": True,
            "close": False,
            "phase": view.get("phase") or "",
            "pending": view,
            "shoot": status_payload(snap),
        }
    )


async def handle_cover(_request: web.Request) -> web.StreamResponse:
    from branding import cover_path

    path = cover_path()
    if not path:
        raise web.HTTPNotFound()
    return web.FileResponse(path)


async def handle_autorolik_save(request: web.Request) -> web.Response:
    form = await request.post()
    try:
        user = _user_from_request(request, form)
    except WebAppAuthError as exc:
        return json_error(str(exc), 403)
    raw = str(form.get("script") or form.get("edits") or "").strip()
    try:
        edits = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return json_error("Не разобрал правки сценария.")
    if not isinstance(edits, dict):
        return json_error("Не разобрал правки сценария.")
    from autorolik import apply_manual_script_edits, load_pending, pending_view, save_pending

    pending = load_pending(int(user["id"])) or {}
    if pending.get("phase") == "shooting":
        return json_error("Съёмка уже идёт. Правки после неё.")
    script = pending.get("script")
    n_photos = len(pending.get("photo_paths") or pending.get("photo_file_ids") or []) or 1
    try:
        updated = apply_manual_script_edits(script if isinstance(script, dict) else None, edits, n_photos=n_photos)
    except PipelineError as exc:
        return json_error(exc.user_message)
    pending["script"] = updated
    pending["phase"] = "review"
    pending["error"] = ""
    save_pending(int(user["id"]), pending)
    return web.json_response(
        {
            "ok": True,
            "phase": "review",
            "message": "Сохранил правки сцен.",
            "pending": pending_view(pending),
            "close": False,
        }
    )


async def handle_autorolik_revise(request: web.Request) -> web.Response:
    form = await request.post()
    try:
        user = _user_from_request(request, form)
    except WebAppAuthError as exc:
        return json_error(str(exc), 403)
    notes = str(form.get("notes") or form.get("text") or "").strip()
    if len(notes) < 3:
        return json_error("Напиши правку парой слов — что поменять в сценах.")
    from autorolik import load_pending

    pending = load_pending(int(user["id"])) or {}
    if pending.get("phase") == "shooting":
        return json_error("Съёмка уже идёт. Правки после неё.")
    bot = request.app["bot"]
    _spawn(_run_safe(bot, user["id"], "autorolik_revise", notes))
    return web.json_response(
        {
            "ok": True,
            "phase": "scripting",
            "message": "Переписываю сценарий…",
            "close": False,
        }
    )


async def handle_autorolik_shoot(request: web.Request) -> web.Response:
    form = await request.post()
    try:
        user = _user_from_request(request, form)
    except WebAppAuthError as exc:
        return json_error(str(exc), 403)
    from autorolik import load_pending

    pending = load_pending(int(user["id"])) or {}
    if pending.get("phase") == "shooting":
        return web.json_response(
            {
                "ok": True,
                "phase": "shooting",
                "message": "Съёмка уже идёт. Прогресс на этом экране.",
                "close": False,
            }
        )
    if pending.get("phase") not in ("review", "error"):
        return json_error("Сначала собери сценарий кнопкой «Собрать сценарий».")
    bot = request.app["bot"]
    _spawn(_run_safe(bot, user["id"], "autorolik_shoot"))
    return web.json_response(
        {
            "ok": True,
            "phase": "shooting",
            "message": "Снимаю. Прогресс здесь, готовое видео — в чат.",
            "close": False,
        }
    )


async def handle_autorolik_cancel(request: web.Request) -> web.Response:
    form = await request.post()
    try:
        user = _user_from_request(request, form)
    except WebAppAuthError as exc:
        return json_error(str(exc), 403)
    from autorolik import clear_pending, load_pending

    pending = load_pending(int(user["id"])) or {}
    if pending.get("phase") == "shooting":
        return json_error("Съёмку уже не остановить кнопкой. Дождись видео или ошибки.")
    clear_pending(int(user["id"]))
    return web.json_response({"ok": True, "phase": "", "message": "Отменил.", "close": False})


async def handle_history(request: web.Request) -> web.Response:
    form = await request.post()
    try:
        user = _user_from_request(request, form)
    except WebAppAuthError as exc:
        return json_error(str(exc), 403)
    bot = request.app["bot"]
    _spawn(_run_safe(bot, user["id"], "history"))
    return web.json_response(
        {"ok": True, "message": "Если есть готовый ролик — пришлю в чат.", "close": True}
    )


async def _run_safe(bot: Any, user_id: int, kind: str, *args: Any) -> None:
    from studio import (
        job_error_text,
        run_studio_autorolik,
        run_studio_autorolik_revise,
        run_studio_autorolik_shoot,
        run_studio_clone,
        run_studio_history,
        run_studio_interpolate,
        run_studio_quick,
        run_studio_restore,
        run_studio_tryon,
        run_studio_upscale,
        run_studio_vibe,
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
        elif kind == "vibe":
            (vibe,) = args
            await run_studio_vibe(bot, user_id, str(vibe))
        elif kind == "upscale":
            data, filename, mime = args
            await run_studio_upscale(bot, user_id, data, str(filename), str(mime))
        elif kind == "interpolate":
            data, filename, mime = args
            await run_studio_interpolate(bot, user_id, data, str(filename), str(mime))
        elif kind == "restore":
            data, filename, mime = args
            await run_studio_restore(bot, user_id, data, str(filename), str(mime))
        elif kind == "tryon":
            person, clothes, consent = args
            await run_studio_tryon(bot, user_id, person, clothes, consent=bool(consent))
        elif kind == "clone":
            audio, filename, consent = args
            await run_studio_clone(bot, user_id, audio, str(filename), consent=bool(consent))
        elif kind == "autorolik":
            photos, consent, topic = args
            await run_studio_autorolik(
                bot,
                user_id,
                list(photos or []),
                consent=bool(consent),
                topic=str(topic or ""),
            )
        elif kind == "autorolik_revise":
            (notes,) = args
            await run_studio_autorolik_revise(bot, user_id, str(notes or ""))
        elif kind == "autorolik_shoot":
            await run_studio_autorolik_shoot(bot, user_id)
        elif kind == "history":
            await run_studio_history(bot, user_id)
    except (PipelineError, Exception) as exc:
        log.warning("studio %s user=%s: %s", kind, user_id, job_error_text(exc))
        if kind == "autorolik_shoot":
            # MiniChat внутри _run_job уже пишет ошибку в чат.
            return
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
    app.router.add_get("/cover.jpg", handle_cover)
    app.router.add_post("/api/quick", handle_quick)
    app.router.add_post("/api/vibe", handle_vibe)
    app.router.add_post("/api/upscale", handle_upscale)
    app.router.add_post("/api/interpolate", handle_interpolate)
    app.router.add_post("/api/restore", handle_restore)
    app.router.add_post("/api/tryon", handle_tryon)
    app.router.add_post("/api/clone", handle_clone)
    app.router.add_post("/api/autorolik", handle_autorolik)
    app.router.add_post("/api/autorolik/status", handle_autorolik_status)
    app.router.add_post("/api/autorolik/script", handle_autorolik_save)
    app.router.add_post("/api/autorolik/revise", handle_autorolik_revise)
    app.router.add_post("/api/autorolik/shoot", handle_autorolik_shoot)
    app.router.add_post("/api/autorolik/cancel", handle_autorolik_cancel)
    app.router.add_post("/api/history", handle_history)
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
