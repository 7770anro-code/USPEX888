"""Проверка Telegram Mini App initData (HMAC-SHA256). Токен в лог не пишем."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any
from urllib.parse import parse_qsl, unquote


class WebAppAuthError(ValueError):
    """initData не прошёл проверку."""


def validate_init_data(
    init_data: str,
    bot_token: str,
    *,
    max_age_sec: int = 86_400,
    now: float | None = None,
) -> dict[str, Any]:
    """Вернуть объект user из initData. Без сети, только HMAC.

    Алгоритм Bot API: secret = HMAC_SHA256(key=b'WebAppData', msg=bot_token),
    hash = HMAC_SHA256(secret, data_check_string) в hex.
    """
    raw = (init_data or "").strip()
    token = (bot_token or "").strip()
    if not raw or not token:
        raise WebAppAuthError("Нет initData или токена бота.")
    pairs = dict(parse_qsl(raw, keep_blank_values=True, strict_parsing=False))
    got_hash = str(pairs.pop("hash", "") or "")
    if not got_hash:
        raise WebAppAuthError("В initData нет hash.")
    # signature — отдельное поле Telegram; в HMAC-строку не входит (как и hash).
    pairs.pop("signature", None)
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", token.encode("utf-8"), hashlib.sha256).digest()
    calc = hmac.new(secret, data_check.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, got_hash):
        raise WebAppAuthError("Подпись Mini App не совпала.")
    try:
        auth_date = int(pairs.get("auth_date") or 0)
    except ValueError as exc:
        raise WebAppAuthError("Некорректный auth_date.") from exc
    stamp = float(now if now is not None else time.time())
    if auth_date <= 0 or stamp - auth_date > int(max_age_sec):
        raise WebAppAuthError("initData устарел. Открой меню заново.")
    user_raw = pairs.get("user") or "{}"
    try:
        user = json.loads(unquote(user_raw) if "%" in user_raw else user_raw)
    except json.JSONDecodeError as exc:
        raise WebAppAuthError("В initData нет user.") from exc
    if not isinstance(user, dict) or not user.get("id"):
        raise WebAppAuthError("В initData нет user.id.")
    user["id"] = int(user["id"])
    return user
