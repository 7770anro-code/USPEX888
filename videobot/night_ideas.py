"""Модуль 1: ночные идеи через тот же Grok chat/completions, что сценарии бота."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import aiohttp

import config
from night_circuit import GROK, with_breaker
from night_store import recent_idea_tokens
from pipeline import (
    RETRY_STATUSES,
    XAI_CHAT_URL,
    XAI_RESPONSES_URL,
    PipelineError,
    _clip,
    _read_error,
    sleep_backoff,
)

log = logging.getLogger("videobot.night")

DENY_RE = re.compile(
    r"путин|зеленск|трамп|байден|навальн|макрон|меркел|"
    r"киев|москва\s+атак|обстрел|"
    r"суицид|самоубий|наркот|героин|кокаин|метамфетамин|"
    r"знаменитост|селебрити|celebrity|real person|узнаваемо(е|го)\s+лиц|"
    r"нацист|теракт|расстрел|massacre|school shooting|"
    r"взрывчат|onlyfans|nsfw|porn|порн|эротик",
    re.I,
)

STOPWORDS = {
    "и", "в", "во", "на", "что", "как", "для", "это", "не", "ни", "с", "со", "по",
    "из", "к", "у", "о", "же", "бы", "а", "но", "или", "если", "чтобы", "то",
    "день", "ночь", "видео", "ролик", "tiktok", "reels", "просто", "очень",
}

IDEA_SYSTEM = """Ты редактор вертикальных роликов «Успех 888» (TikTok + Instagram Reels, 9:16).
Верни ТОЛЬКО JSON без markdown:

{
  "ideas": [
    {
      "kind": "motivational" | "absurd",
      "title": "короткий заголовок",
      "plot": "2–3 предложения сюжета, только синтетические сцены",
      "caption": "текст публикации + 3–5 хештегов",
      "hook": "первая фраза на 1 секунде",
      "score": 8
    }
  ]
}

Жёстко:
- Только синтетика: выдуманные персонажи, графика, абстракция. Без реальных фото, лиц, знаменитостей, брендов, логотипов, NSFW.
- kind: половина motivational (привычки, фокус, решение), половина absurd (тренд-абсурд, ирония без оскорблений).
- Речь на русском. score 1–10 — вирусный потенциал.
- Не повторяй темы из блока «недавно было».
"""


def tokenize(*parts: str) -> list[str]:
    blob = " ".join(parts).lower()
    words = re.findall(r"[a-zа-яё0-9]{3,}", blob, flags=re.I)
    seen: list[str] = []
    for w in words:
        w = w.lower()
        if w in STOPWORDS or w in seen:
            continue
        seen.append(w)
    return seen


def idea_hash(tokens: list[str]) -> str:
    return "-".join(sorted(tokens)[:12])


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def too_similar(tokens: list[str], history: list[set[str]], *, threshold: float = 0.55) -> bool:
    now = set(tokens)
    return any(jaccard(now, old) >= threshold for old in history)


def extract_hashtags(caption: str) -> list[str]:
    return [m.group(1).lower() for m in re.finditer(r"#([A-Za-zА-Яа-яЁё0-9_]+)", caption or "")]


def same_day_conflict(
    idea: dict[str, Any],
    *,
    account_id: str,
    existing_jobs: list[dict[str, Any]] | None = None,
    used_today: list[set[str]] | None = None,
) -> bool:
    now = set(idea.get("tokens") or [])
    tags = set(idea.get("hashtags") or extract_hashtags(str(idea.get("caption") or "")))
    if any(jaccard(now, old) >= 0.4 for old in (used_today or [])):
        return True
    for job in existing_jobs or []:
        if str(job.get("account_id") or "") != str(account_id):
            continue
        old_tokens = {t for t in str(job.get("tokens") or "").split() if t}
        old_tags = extract_hashtags(str(job.get("caption") or ""))
        if tags and set(old_tags) & tags:
            return True
        if old_tokens and jaccard(now, old_tokens) >= 0.4:
            return True
    return False


def parse_ideas(raw: str) -> list[dict[str, Any]]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S)
    data = json.loads(text)
    items = data.get("ideas") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise PipelineError("Grok вернул идеи не списком.")
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip().lower()
        if kind not in ("motivational", "absurd"):
            continue
        title = " ".join(str(item.get("title") or "").split())[:80]
        plot = " ".join(str(item.get("plot") or "").split())[:500]
        caption = str(item.get("caption") or "").strip()[:2200]
        if len(title) < 4 or len(plot) < 12:
            continue
        blob = f"{title} {plot} {caption}"
        if DENY_RE.search(blob):
            continue
        tokens = tokenize(title, plot, caption)
        try:
            score = float(item.get("score") or 5)
        except (TypeError, ValueError):
            score = 5.0
        out.append(
            {
                "kind": kind,
                "title": title,
                "plot": plot,
                "caption": caption or title,
                "hook": str(item.get("hook") or title)[:120],
                "score": max(1.0, min(10.0, score)),
                "tokens": tokens,
                "hashtags": extract_hashtags(caption),
                "idea_hash": idea_hash(tokens),
            }
        )
    if not out:
        raise PipelineError("После разбора не осталось годных идей.")
    return out


async def _grok_raw(session: aiohttp.ClientSession, user_content: str) -> str:
    if config.XAI_API_KEY_ERROR:
        raise PipelineError("Ключ Grok в неправильном формате.", config.XAI_API_KEY_ERROR)
    if not config.XAI_API_KEY_NEW:
        raise PipelineError("Нет XAI_API_KEY_NEW — идеи собрать не могу.")
    messages = [
        {"role": "system", "content": IDEA_SYSTEM},
        {"role": "user", "content": user_content},
    ]
    headers = {
        "Authorization": f"Bearer {config.XAI_API_KEY_NEW}",
        "Content-Type": "application/json",
    }
    last_err = ""
    tries = max(1, int(config.HTTP_RETRIES))
    for model in (config.XAI_MODEL, config.XAI_FALLBACK_MODEL):
        if not model:
            continue
        payload = {"model": model, "messages": messages, "temperature": 0.8}
        for attempt in range(tries):
            try:
                async with session.post(
                    XAI_CHAT_URL,
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=120),
                ) as resp:
                    if resp.status in RETRY_STATUSES and attempt < tries - 1:
                        last_err = f"{model} chat HTTP {resp.status}"
                        await sleep_backoff(attempt)
                        continue
                    if resp.status < 400:
                        data = await resp.json()
                        content = (
                            (((data.get("choices") or [{}])[0].get("message") or {}).get("content"))
                            or ""
                        )
                        if content.strip():
                            log.info("Grok ideas ok model=%s", model)
                            return str(content)
                        last_err = f"{model}: пустой chat"
                    else:
                        last_err = f"{model} chat: {await _read_error(resp)}"
            except Exception as exc:
                last_err = f"{model} chat: {type(exc).__name__}"
                if attempt < tries - 1:
                    await sleep_backoff(attempt)
                    continue
        payload_r = {"model": model, "input": messages}
        for attempt in range(tries):
            try:
                async with session.post(
                    XAI_RESPONSES_URL,
                    headers=headers,
                    json=payload_r,
                    timeout=aiohttp.ClientTimeout(total=120),
                ) as resp:
                    raw = await resp.text()
                    if resp.status in RETRY_STATUSES and attempt < tries - 1:
                        await sleep_backoff(attempt)
                        continue
                    if resp.status >= 400:
                        last_err = f"{model} responses: {_clip(f'HTTP {resp.status}', 200)}"
                        break
                    data = json.loads(raw)
                chunks: list[str] = []
                if isinstance(data.get("output_text"), str):
                    chunks.append(data["output_text"])
                for item in data.get("output") or []:
                    if not isinstance(item, dict):
                        continue
                    for part in item.get("content") or []:
                        if isinstance(part, dict) and part.get("text"):
                            chunks.append(str(part["text"]))
                content = "\n".join(chunks).strip()
                if content:
                    log.info("Grok ideas responses ok model=%s", model)
                    return content
            except Exception as exc:
                last_err = f"{model} responses: {type(exc).__name__}"
                if attempt < tries - 1:
                    await sleep_backoff(attempt)
                    continue
    raise PipelineError("Не получилось получить идеи от Grok.", last_err)


async def generate_ideas(
    session: aiohttp.ClientSession,
    *,
    n: int,
    recent: list[set[str]] | None = None,
) -> list[dict[str, Any]]:
    n = max(5, min(10, int(n)))
    history = recent if recent is not None else recent_idea_tokens(days=config.NIGHT_DEDUP_DAYS)
    ban = ", ".join(sorted({next(iter(s)) for s in history if s})[:20])
    user = (
        f"Сделай ровно {n} идей: поровну motivational и absurd.\n"
        f"Недавно было (не повторять): {ban or 'пусто, это первый прогон'}."
    )

    async def _call() -> str:
        return await _grok_raw(session, user)

    raw = await with_breaker(GROK, _call, retries=3)
    ideas = parse_ideas(raw)
    kept: list[dict[str, Any]] = []
    used: list[set[str]] = list(history)
    for idea in sorted(ideas, key=lambda x: -float(x["score"])):
        toks = list(idea["tokens"])
        if too_similar(toks, used):
            log.info("drop similar idea: %s", idea["title"])
            continue
        used.append(set(toks))
        kept.append(idea)
    if len(kept) < 2:
        raise PipelineError("Слишком мало уникальных идей после дедупа.")
    return kept[:n]


def assign_to_accounts(
    ideas: list[dict[str, Any]],
    accounts: list[Any],
    *,
    limit: int,
    existing_jobs: list[dict[str, Any]] | None = None,
) -> list[tuple[Any, dict[str, Any]]]:
    """По одной идее на аккаунт. Не повторяем тему/хештеги этого дня на том же аккаунте."""
    leftover = list(ideas)
    picked: list[tuple[Any, dict[str, Any]]] = []
    today_used: list[set[str]] = []
    for acc in accounts:
        if len(picked) >= limit:
            break
        match: dict[str, Any] | None = None
        for idea in leftover:
            if acc.theme != "mixed" and idea["kind"] != (
                "motivational" if acc.theme == "motivational" else "absurd"
            ):
                continue
            if same_day_conflict(
                idea, account_id=acc.id, existing_jobs=existing_jobs, used_today=today_used
            ):
                continue
            match = idea
            break
        if match is None:
            for idea in leftover:
                if same_day_conflict(
                    idea, account_id=acc.id, existing_jobs=existing_jobs, used_today=today_used
                ):
                    continue
                match = idea
                break
        if match is None:
            continue
        leftover.remove(match)
        today_used.append(set(match["tokens"]))
        picked.append((acc, match))
    return picked
