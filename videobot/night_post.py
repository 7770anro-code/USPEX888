"""Модуль 3: TikTok Content Posting API + Instagram Graph API.

Автопост ночью — только если require_confirm=0 и autopost=1.
Подтверждение владельца в Telegram публикует даже при NIGHT_AUTOPOST=0.
Не обходим App Review: unaudited TikTok → inbox/draft; Instagram без
Business/Creator + instagram_content_publish → явный блокер в отчёте.
Секреты в логи не пишем — только имена переменных.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import aiohttp

import config
from night_accounts import Account, ig_creds, tiktok_token
from night_circuit import (
    INSTAGRAM,
    POST_GATE,
    TIKTOK,
    CircuitOpen,
    RetryableHttpError,
    with_breaker,
)

log = logging.getLogger("videobot.night")

TIKTOK_INBOX_INIT = "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/"
TIKTOK_DIRECT_INIT = "https://open.tiktokapis.com/v2/post/publish/video/init/"
TIKTOK_STATUS = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"
TIKTOK_CREATOR = "https://open.tiktokapis.com/v2/post/publish/creator_info/query/"


class PostBlocker(RuntimeError):
    def __init__(self, message: str, *, vars_needed: list[str] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.vars_needed = vars_needed or []


# Timeout после создания publish/container — не ретраим POST, только статус по ID.
RETRYABLE_EXC = (
    RetryableHttpError,
    aiohttp.ClientConnectionError,
    aiohttp.ClientOSError,
    ConnectionError,
)

TIKTOK_OK_STATUS = {"PUBLISH_COMPLETE", "SEND_TO_USER_INBOX"}
TIKTOK_WAIT_STATUS = {
    "PROCESSING",
    "PROCESSING_UPLOAD",
    "PROCESSING_DOWNLOAD",
    "UPLOADING",
    "DOWNLOADING",
}


class PostResult:
    def __init__(
        self,
        platform: str,
        ok: bool,
        *,
        mode: str = "",
        url: str = "",
        error: str = "",
        blocker: bool = False,
        vars_needed: list[str] | None = None,
        publish_id: str = "",
        container_id: str = "",
    ) -> None:
        self.platform = platform
        self.ok = ok
        self.mode = mode
        self.url = url
        self.error = error
        self.blocker = blocker
        self.vars_needed = vars_needed or []
        self.publish_id = publish_id
        self.container_id = container_id

    def as_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "ok": self.ok,
            "mode": self.mode,
            "url": self.url,
            "error": self.error,
            "blocker": self.blocker,
            "vars_needed": self.vars_needed,
        }


def _graph_base() -> str:
    return f"https://graph.facebook.com/{config.NIGHT_GRAPH_VERSION}"


def public_url_for(video: Path) -> str:
    base = (config.NIGHT_PUBLIC_VIDEO_BASE_URL or "").rstrip("/")
    if not base:
        return ""
    return f"{base}/{video.name}"


async def _json(resp: aiohttp.ClientResponse) -> dict[str, Any]:
    try:
        data = await resp.json(content_type=None)
    except Exception:
        text = await resp.text()
        return {"_raw": text[:300], "_status": resp.status}
    if not isinstance(data, dict):
        return {"_raw": str(data)[:300], "_status": resp.status}
    data["_status"] = resp.status
    return data


def is_manual_error(text: str) -> bool:
    low = (text or "").lower()
    keys = (
        "oauth", "unauthenticated", "unauthorized", "401", "403",
        "forbidden", "scope", "audit", "review", "permission",
        "moderation", "rejected", "rejection", "safety",
        "unsupported", "invalid video", "format", "spam",
        "unaudited", "app review",
    )
    return any(k in low for k in keys)


def is_retryable_http(status: int) -> bool:
    return status in (429, 500, 502, 503, 504)


def is_timeout_error(exc: BaseException) -> bool:
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return True
    return isinstance(exc, aiohttp.ServerTimeoutError) or "timeout" in type(exc).__name__.lower()


def classify_job_status(results: list[PostResult]) -> str:
    from night_store import MANUAL_REVIEW, POSTED, PUBLISH_UNKNOWN, VIDEO_READY

    if any(r.mode == "publish_unknown" for r in results):
        return PUBLISH_UNKNOWN
    if any(r.blocker and is_manual_error(r.error) for r in results):
        return MANUAL_REVIEW
    if any(is_manual_error(r.error) for r in results if r.error and not r.ok):
        return MANUAL_REVIEW
    if any(r.ok for r in results):
        return POSTED
    return VIDEO_READY


def _tiktok_err(data: dict[str, Any]) -> str:
    err = data.get("error") if isinstance(data.get("error"), dict) else {}
    code = str(err.get("code") or data.get("_status") or "")
    msg = str(err.get("message") or data.get("message") or "")
    return f"tiktok {code}: {msg}".strip(": ")


async def tiktok_status(session: aiohttp.ClientSession, token: str, publish_id: str) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"}
    async with session.post(
        TIKTOK_STATUS,
        headers=headers,
        json={"publish_id": publish_id},
        timeout=aiohttp.ClientTimeout(total=30),
    ) as resp:
        return await _json(resp)


async def post_tiktok(
    session: aiohttp.ClientSession,
    account: Account,
    video: Path,
    caption: str,
    *,
    existing_publish_id: str = "",
) -> PostResult:
    token = tiktok_token(account)
    if not token:
        return PostResult(
            "tiktok",
            False,
            blocker=True,
            vars_needed=[account.tiktok_token_var],
            error=f"нет {account.tiktok_token_var} — владелец должен вставить OAuth access token после логина в TikTok app",
        )
    if not TIKTOK.allow():
        return PostResult("tiktok", False, error="circuit open: tiktok", blocker=False)
    if existing_publish_id:
        return await _tiktok_resume(session, token, existing_publish_id)
    mode = config.NIGHT_TIKTOK_MODE if config.NIGHT_TIKTOK_MODE in ("inbox", "direct") else "inbox"
    size = video.stat().st_size
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=UTF-8",
    }

    async def _init() -> dict[str, Any]:
        await POST_GATE.wait()
        aigc_info = {"is_aigc": True}
        if mode == "direct":
            body: dict[str, Any] = {
                "post_info": {
                    "title": caption[:2200],
                    "privacy_level": "SELF_ONLY",
                    "disable_duet": False,
                    "disable_comment": False,
                    "disable_stitch": False,
                    **aigc_info,
                },
                "source_info": {
                    "source": "FILE_UPLOAD",
                    "video_size": size,
                    "chunk_size": size,
                    "total_chunk_count": 1,
                },
            }
            url = TIKTOK_DIRECT_INIT
        else:
            # inbox тоже помечаем AIGC — TikTok требует is_aigc для AI-видео
            body = {
                "post_info": aigc_info,
                "source_info": {
                    "source": "FILE_UPLOAD",
                    "video_size": size,
                    "chunk_size": size,
                    "total_chunk_count": 1,
                },
            }
            url = TIKTOK_INBOX_INIT
        async with session.post(url, headers=headers, json=body, timeout=aiohttp.ClientTimeout(total=60)) as resp:
            data = await _json(resp)
            status = int(data.get("_status") or resp.status or 0)
            if is_retryable_http(status):
                raise RetryableHttpError(status, _tiktok_err(data))
            return data

    try:
        data = await with_breaker(TIKTOK, _init, retries=3, retry_for=RETRYABLE_EXC)
    except CircuitOpen:
        return PostResult("tiktok", False, error="circuit open: tiktok")
    except Exception as exc:
        if is_timeout_error(exc):
            return PostResult(
                "tiktok",
                False,
                mode="publish_unknown",
                error="PUBLISH_UNKNOWN: timeout на init, повторный POST не делаю",
            )
        if is_manual_error(str(exc)):
            return PostResult("tiktok", False, error=f"{type(exc).__name__}", blocker=True)
        return PostResult("tiktok", False, error=f"{type(exc).__name__}")

    err = data.get("error") if isinstance(data.get("error"), dict) else {}
    if str(err.get("code") or "ok") != "ok" or data.get("_status", 200) >= 400:
        msg = _tiktok_err(data)
        hint = ""
        if "scope" in msg.lower() or "unaudited" in msg.lower() or "audit" in msg.lower():
            hint = (
                " Блокер: TikTok app без review. Нужен App Review (video.publish) "
                "или оставьте NIGHT_TIKTOK_MODE=inbox (черновик в inbox, scope video.upload)."
            )
        return PostResult(
            "tiktok",
            False,
            mode=mode,
            error=(msg + hint)[:400],
            blocker=True,
        )

    payload = data.get("data") if isinstance(data.get("data"), dict) else {}
    upload_url = str(payload.get("upload_url") or "")
    publish_id = str(payload.get("publish_id") or "")
    if not upload_url:
        return PostResult("tiktok", False, mode=mode, error="нет upload_url", blocker=True)

    async def _put() -> int:
        raw = video.read_bytes()
        put_headers = {
            "Content-Type": "video/mp4",
            "Content-Length": str(size),
            "Content-Range": f"bytes 0-{size - 1}/{size}",
        }
        async with session.put(
            upload_url,
            data=raw,
            headers=put_headers,
            timeout=aiohttp.ClientTimeout(total=300),
        ) as resp:
            return int(resp.status)

    try:
        status = await _put()
    except Exception as exc:
        TIKTOK.fail()
        return PostResult(
            "tiktok",
            False,
            mode="publish_unknown",
            url=f"tiktok:publish_id={publish_id}" if publish_id else "",
            error=f"PUBLISH_UNKNOWN: upload {type(exc).__name__}",
            publish_id=publish_id,
        )
    if is_retryable_http(status):
        TIKTOK.fail()
        return PostResult(
            "tiktok",
            False,
            mode="publish_unknown" if publish_id else mode,
            url=f"tiktok:publish_id={publish_id}" if publish_id else "",
            error=f"upload HTTP {status}",
            publish_id=publish_id,
        )
    if status >= 400:
        TIKTOK.fail()
        return PostResult(
            "tiktok",
            False,
            mode=mode,
            error=f"upload HTTP {status}",
            blocker=is_manual_error(f"HTTP {status}"),
            publish_id=publish_id,
        )

    # inbox: SEND_TO_USER_INBOX; direct: PUBLISH_COMPLETE
    link = f"tiktok:publish_id={publish_id}"
    note = "черновик в inbox (нужно подтверждение в приложении TikTok)" if mode == "inbox" else "direct/SELF_ONLY"
    log.info("tiktok uploaded account=%s mode=%s aigc=1", account.id, mode)
    return PostResult("tiktok", True, mode=f"{mode}/{note}", url=link, publish_id=publish_id)


async def _tiktok_resume(
    session: aiohttp.ClientSession, token: str, existing_publish_id: str
) -> PostResult:
    try:
        st = await tiktok_status(session, token, existing_publish_id)
        status = str(((st.get("data") or {}) if isinstance(st.get("data"), dict) else {}).get("status") or "")
        status_u = status.upper()
        if status_u in TIKTOK_OK_STATUS:
            return PostResult(
                "tiktok",
                True,
                mode="resume",
                url=f"tiktok:publish_id={existing_publish_id}",
                publish_id=existing_publish_id,
            )
        if status_u == "FAILED":
            msg = _tiktok_err(st) if st.get("error") else "publish failed after resume"
            return PostResult(
                "tiktok",
                False,
                error=msg,
                blocker=is_manual_error(msg + " " + status),
            )
        return PostResult(
            "tiktok",
            False,
            mode="publish_unknown",
            url=f"tiktok:publish_id={existing_publish_id}",
            error="PUBLISH_UNKNOWN: статус не подтверждён, повторный init не делаю",
            publish_id=existing_publish_id,
        )
    except Exception as exc:
        return PostResult(
            "tiktok",
            False,
            mode="publish_unknown",
            url=f"tiktok:publish_id={existing_publish_id}",
            error=f"PUBLISH_UNKNOWN: {type(exc).__name__}",
            publish_id=existing_publish_id,
        )


async def post_instagram(
    session: aiohttp.ClientSession,
    account: Account,
    video: Path,
    caption: str,
    *,
    existing_container_id: str = "",
) -> PostResult:
    user_id, token = ig_creds(account)
    if not user_id or not token:
        needed = [v for v in (account.ig_user_var, account.ig_token_var) if not config._clean(v)]
        return PostResult(
            "instagram",
            False,
            blocker=True,
            vars_needed=needed,
            error=(
                "нет Instagram creds. Нужен Business/Creator, привязка к Facebook Page, "
                "permission instagram_content_publish (часто App Review) и переменные "
                + ", ".join(needed)
            ),
        )
    if not INSTAGRAM.allow():
        return PostResult("instagram", False, error="circuit open: instagram")

    if existing_container_id:
        return await _instagram_finish(session, user_id, token, existing_container_id)

    public = public_url_for(video)

    async def _create() -> dict[str, Any]:
        await POST_GATE.wait()
        params: dict[str, Any] = {
            "media_type": "REELS",
            "caption": caption[:2200],
            "share_to_feed": "true",
            "access_token": token,
        }
        if public:
            params["video_url"] = public
            mode_used = "video_url"
        else:
            params["upload_type"] = "resumable"
            mode_used = "resumable"
        url = f"{_graph_base()}/{user_id}/media"
        async with session.post(url, data=params, timeout=aiohttp.ClientTimeout(total=60)) as resp:
            data = await _json(resp)
            data["_mode"] = mode_used
            status = int(data.get("_status") or resp.status or 0)
            if is_retryable_http(status):
                raise RetryableHttpError(status, str(data.get("error") or status))
            return data

    try:
        created = await with_breaker(INSTAGRAM, _create, retries=3, retry_for=RETRYABLE_EXC)
    except CircuitOpen:
        return PostResult("instagram", False, error="circuit open: instagram")
    except Exception as exc:
        if is_timeout_error(exc):
            return PostResult(
                "instagram",
                False,
                mode="publish_unknown",
                error="PUBLISH_UNKNOWN: timeout на create, повторный POST не делаю",
            )
        if is_manual_error(str(exc)):
            return PostResult("instagram", False, error=f"{type(exc).__name__}", blocker=True)
        return PostResult("instagram", False, error=f"{type(exc).__name__}")

    if created.get("error") or created.get("_status", 200) >= 400:
        err = created.get("error") if isinstance(created.get("error"), dict) else {}
        msg = str(err.get("message") or created.get("_raw") or "instagram create failed")
        blocker = any(
            x in msg.lower()
            for x in ("permission", "review", "instagram_content_publish", "page", "business")
        ) or is_manual_error(msg)
        extra = ""
        if blocker:
            extra = (
                " Блокер владельца: Meta App Review + instagram_content_publish, "
                "IG Business/Creator, связанный с Facebook Page. "
                f"Проверьте {account.ig_token_var} и {account.ig_user_var}."
            )
        return PostResult("instagram", False, error=(msg + extra)[:400], blocker=True)

    container = str(created.get("id") or "")
    if not container:
        return PostResult("instagram", False, error="нет container id", blocker=True)

    if created.get("_mode") == "resumable":
        try:
            raw = video.read_bytes()
            up_url = f"https://rupload.facebook.com/ig-api-upload/{config.NIGHT_GRAPH_VERSION}/{container}"
            headers = {
                "Authorization": f"OAuth {token}",
                "offset": "0",
                "file_size": str(len(raw)),
            }
            async with session.post(
                up_url,
                data=raw,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=300),
            ) as resp:
                if is_retryable_http(resp.status) or resp.status >= 400:
                    return PostResult(
                        "instagram",
                        False,
                        mode="publish_unknown" if is_retryable_http(resp.status) else "resumable",
                        error=f"rupload HTTP {resp.status}",
                        container_id=container,
                        blocker=not is_retryable_http(resp.status) and is_manual_error(f"HTTP {resp.status}"),
                    )
        except Exception as exc:
            INSTAGRAM.fail()
            return PostResult(
                "instagram",
                False,
                mode="publish_unknown",
                error=f"PUBLISH_UNKNOWN: rupload {type(exc).__name__}",
                container_id=container,
            )

    return await _instagram_finish(session, user_id, token, container)


async def _instagram_finish(
    session: aiohttp.ClientSession,
    user_id: str,
    token: str,
    container: str,
) -> PostResult:
    finished = False
    for _ in range(24):
        await asyncio.sleep(5)
        try:
            async with session.get(
                f"{_graph_base()}/{container}",
                params={"fields": "status_code,status", "access_token": token},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                st = await _json(resp)
        except Exception:
            continue
        code = str(st.get("status_code") or "").upper()
        if code == "FINISHED":
            finished = True
            break
        if code in {"ERROR", "EXPIRED"}:
            msg = str(st.get("status") or code)
            return PostResult(
                "instagram",
                False,
                error=msg,
                blocker=is_manual_error(msg),
            )
    if not finished:
        return PostResult(
            "instagram",
            False,
            mode="publish_unknown",
            error="PUBLISH_UNKNOWN: container timeout, повторный create не делаю",
            container_id=container,
        )

    async def _publish() -> dict[str, Any]:
        async with session.post(
            f"{_graph_base()}/{user_id}/media_publish",
            data={"creation_id": container, "access_token": token},
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            data = await _json(resp)
            status = int(data.get("_status") or resp.status or 0)
            if is_retryable_http(status):
                raise RetryableHttpError(status, str(data.get("error") or status))
            return data

    try:
        pub = await with_breaker(INSTAGRAM, _publish, retries=3, retry_for=RETRYABLE_EXC)
    except Exception as exc:
        if is_timeout_error(exc) or isinstance(exc, RetryableHttpError):
            return PostResult(
                "instagram",
                False,
                mode="publish_unknown",
                error=f"PUBLISH_UNKNOWN: publish {type(exc).__name__}",
                container_id=container,
            )
        return PostResult(
            "instagram",
            False,
            error=f"publish {type(exc).__name__}",
            container_id=container,
            blocker=is_manual_error(str(exc)),
        )
    if pub.get("error") or pub.get("_status", 200) >= 400:
        err = pub.get("error") if isinstance(pub.get("error"), dict) else {}
        msg = str(err.get("message") or "publish failed")
        return PostResult(
            "instagram",
            False,
            error=msg,
            container_id=container,
            blocker=is_manual_error(msg),
        )
    media_id = str(pub.get("id") or container)
    log.info("instagram published")
    return PostResult("instagram", True, mode="reels", url=f"instagram:media_id={media_id}", container_id=container)


async def publish_account(
    session: aiohttp.ClientSession,
    account: Account,
    video: Path,
    caption: str,
    *,
    tiktok_publish_id: str = "",
    ig_container_id: str = "",
    confirmed: bool = False,
) -> list[PostResult]:
    from night_store import autopost_enabled

    results: list[PostResult] = []
    if not confirmed and not autopost_enabled():
        results.append(
            PostResult(
                "tiktok",
                False,
                blocker=True,
                error="автопост выключен — публикация только после да/нет в Telegram",
                vars_needed=["NIGHT_AUTOPOST"],
            )
        )
        results.append(
            PostResult(
                "instagram",
                False,
                blocker=True,
                error="автопост выключен — публикация только после да/нет в Telegram",
                vars_needed=["NIGHT_AUTOPOST"],
            )
        )
        return results
    results.append(
        await post_tiktok(session, account, video, caption, existing_publish_id=tiktok_publish_id)
    )
    results.append(
        await post_instagram(
            session, account, video, caption, existing_container_id=ig_container_id
        )
    )
    return results


def persist_publish_results(job_id: int, job: dict[str, Any], results: list[PostResult]) -> str:
    from night_store import update_job

    tt = next((r for r in results if r.platform == "tiktok"), PostResult("tiktok", False))
    ig = next((r for r in results if r.platform == "instagram"), PostResult("instagram", False))
    errors = [r.error for r in results if r.error]
    status = classify_job_status(results)

    def _keep_id(result: PostResult, old: str, attr: str) -> str:
        current = getattr(result, attr) or ""
        if result.mode == "publish_unknown":
            return current or old
        if current:
            return current
        # FAILED / moderation — старый ID больше не используем, иначе вечный resume
        return ""

    update_job(
        job_id,
        status=status,
        tiktok_url=tt.url or str(job.get("tiktok_url") or ""),
        instagram_url=ig.url or str(job.get("instagram_url") or ""),
        tiktok_mode=tt.mode,
        instagram_mode=ig.mode,
        tiktok_publish_id=_keep_id(tt, str(job.get("tiktok_publish_id") or ""), "publish_id"),
        ig_container_id=_keep_id(ig, str(job.get("ig_container_id") or ""), "container_id"),
        last_error=(" | ".join(errors))[:400],
        locked_at=None,
        worker_id="",
    )
    return status


async def publish_job_id(job_id: int) -> str:
    """Подтверждение владельца из Telegram: публикуем уже готовый файл этого аккаунта."""
    from night_accounts import load_accounts
    from night_store import get_job, video_belongs_to_account

    job = get_job(job_id)
    if not job:
        return "Задача не найдена."
    video = Path(str(job.get("video_path") or ""))
    if not video.is_file():
        return "Файл видео не найден — публиковать нечего."
    acc_id = str(job.get("account_id") or "")
    if not video_belongs_to_account(str(video), acc_id, str(job.get("run_date") or "")):
        return "Этот файл не из папки аккаунта — один ролик на все три аккаунта не публикую."
    accounts = {a.id: a for a in load_accounts()}
    acc = accounts.get(acc_id)
    if not acc:
        return "Аккаунт задачи неизвестен."
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=180)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        results = await publish_account(
            session,
            acc,
            video,
            str(job.get("caption") or job.get("title") or ""),
            tiktok_publish_id=str(job.get("tiktok_publish_id") or ""),
            ig_container_id=str(job.get("ig_container_id") or ""),
            confirmed=True,
        )
    persist_publish_results(job_id, job, results)
    return format_job_result(job_id, results)


def format_job_result(job_id: int, results: list[PostResult]) -> str:
    lines = [f"Задача {job_id}:"]
    for r in results:
        mark = "ok" if r.ok else "нет"
        extra = f" — {r.error}" if r.error else ""
        url = f" {r.url}" if r.url else ""
        lines.append(f"{r.platform}: {mark}{url}{extra}")
    return "\n".join(lines)
