"""Модуль 3: TikTok Content Posting API + Instagram Graph API.

Публикация только если NIGHT_AUTOPOST=1 и токены аккаунта заданы.
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
from night_circuit import INSTAGRAM, POST_GATE, TIKTOK, CircuitOpen, with_breaker

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
    ) -> None:
        self.platform = platform
        self.ok = ok
        self.mode = mode
        self.url = url
        self.error = error
        self.blocker = blocker
        self.vars_needed = vars_needed or []

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


def _tiktok_err(data: dict[str, Any]) -> str:
    err = data.get("error") if isinstance(data.get("error"), dict) else {}
    code = str(err.get("code") or data.get("_status") or "")
    msg = str(err.get("message") or data.get("message") or "")
    return f"tiktok {code}: {msg}".strip(": ")


async def post_tiktok(
    session: aiohttp.ClientSession,
    account: Account,
    video: Path,
    caption: str,
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
    mode = config.NIGHT_TIKTOK_MODE if config.NIGHT_TIKTOK_MODE in ("inbox", "direct") else "inbox"
    size = video.stat().st_size
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=UTF-8",
    }

    async def _init() -> dict[str, Any]:
        await POST_GATE.wait()
        if mode == "direct":
            body: dict[str, Any] = {
                "post_info": {
                    "title": caption[:2200],
                    "privacy_level": "SELF_ONLY",
                    "disable_duet": False,
                    "disable_comment": False,
                    "disable_stitch": False,
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
            body = {
                "source_info": {
                    "source": "FILE_UPLOAD",
                    "video_size": size,
                    "chunk_size": size,
                    "total_chunk_count": 1,
                }
            }
            url = TIKTOK_INBOX_INIT
        async with session.post(url, headers=headers, json=body, timeout=aiohttp.ClientTimeout(total=60)) as resp:
            return await _json(resp)

    try:
        data = await with_breaker(TIKTOK, _init, retries=3)
    except CircuitOpen:
        return PostResult("tiktok", False, error="circuit open: tiktok")
    except Exception as exc:
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
        return PostResult("tiktok", False, mode=mode, error=(msg + hint)[:400], blocker=True)

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
        return PostResult("tiktok", False, mode=mode, error=f"upload {type(exc).__name__}")
    if status >= 400:
        TIKTOK.fail()
        return PostResult("tiktok", False, mode=mode, error=f"upload HTTP {status}")

    # inbox: SEND_TO_USER_INBOX; direct: PUBLISH_COMPLETE
    link = f"tiktok:publish_id={publish_id}"
    note = "черновик в inbox (нужно подтверждение в приложении TikTok)" if mode == "inbox" else "direct/SELF_ONLY"
    log.info("tiktok uploaded account=%s mode=%s", account.id, mode)
    return PostResult("tiktok", True, mode=f"{mode}/{note}", url=link)


async def post_instagram(
    session: aiohttp.ClientSession,
    account: Account,
    video: Path,
    caption: str,
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
            return data

    try:
        created = await with_breaker(INSTAGRAM, _create, retries=3)
    except CircuitOpen:
        return PostResult("instagram", False, error="circuit open: instagram")
    except Exception as exc:
        return PostResult("instagram", False, error=f"{type(exc).__name__}")

    if created.get("error") or created.get("_status", 200) >= 400:
        err = created.get("error") if isinstance(created.get("error"), dict) else {}
        msg = str(err.get("message") or created.get("_raw") or "instagram create failed")
        blocker = any(
            x in msg.lower()
            for x in ("permission", "review", "instagram_content_publish", "page", "business")
        )
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
                if resp.status >= 400:
                    return PostResult(
                        "instagram",
                        False,
                        mode="resumable",
                        error=f"rupload HTTP {resp.status}",
                    )
        except Exception as exc:
            INSTAGRAM.fail()
            return PostResult("instagram", False, error=f"rupload {type(exc).__name__}")

    # poll container
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
            break
        if code in {"ERROR", "EXPIRED"}:
            return PostResult(
                "instagram",
                False,
                error=str(st.get("status") or code),
            )
    else:
        return PostResult("instagram", False, error="container timeout")

    async def _publish() -> dict[str, Any]:
        async with session.post(
            f"{_graph_base()}/{user_id}/media_publish",
            data={"creation_id": container, "access_token": token},
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            return await _json(resp)

    try:
        pub = await with_breaker(INSTAGRAM, _publish, retries=3)
    except Exception as exc:
        return PostResult("instagram", False, error=f"publish {type(exc).__name__}")
    if pub.get("error") or pub.get("_status", 200) >= 400:
        err = pub.get("error") if isinstance(pub.get("error"), dict) else {}
        return PostResult("instagram", False, error=str(err.get("message") or "publish failed"))
    media_id = str(pub.get("id") or container)
    log.info("instagram published account=%s", account.id)
    return PostResult("instagram", True, mode="reels", url=f"instagram:media_id={media_id}")


async def publish_account(
    session: aiohttp.ClientSession,
    account: Account,
    video: Path,
    caption: str,
) -> list[PostResult]:
    results: list[PostResult] = []
    if not config.NIGHT_AUTOPOST:
        results.append(
            PostResult(
                "tiktok",
                False,
                blocker=True,
                error="NIGHT_AUTOPOST=0 — постинг выключен, файл сохранён локально",
                vars_needed=["NIGHT_AUTOPOST"],
            )
        )
        results.append(
            PostResult(
                "instagram",
                False,
                blocker=True,
                error="NIGHT_AUTOPOST=0 — постинг выключен, файл сохранён локально",
                vars_needed=["NIGHT_AUTOPOST"],
            )
        )
        return results
    results.append(await post_tiktok(session, account, video, caption))
    results.append(await post_instagram(session, account, video, caption))
    return results
