"""Пакет под ручную загрузку в TikTok / Instagram. Автопостинга нет."""

from __future__ import annotations

import json
import re
import shutil
from datetime import date
from pathlib import Path
from typing import Any

from nightcal import Slot
from presets import PRESETS

BRAND_TAG = "#успех888"
STOPWORDS = {
    "и",
    "в",
    "во",
    "на",
    "что",
    "как",
    "для",
    "это",
    "не",
    "ни",
    "с",
    "со",
    "по",
    "из",
    "к",
    "ко",
    "у",
    "о",
    "об",
    "же",
    "бы",
    "а",
    "но",
    "или",
    "если",
    "чтобы",
    "то",
    "ты",
    "тебе",
    "мне",
    "мы",
    "вы",
    "они",
    "он",
    "она",
    "мне",
    "свой",
    "своя",
    "каждый",
    "день",
    "без",
    "до",
    "после",
    "когда",
    "где",
    "там",
    "тут",
    "уже",
    "ещё",
    "еще",
    "очень",
    "просто",
    "можно",
    "нужно",
    "которые",
    "который",
    "которая",
}

PRESET_TAGS: dict[str, tuple[str, ...]] = {
    "viral": ("#fyp", "#viral", "#мотивация", "#привычки"),
    "ad": ("#оффер", "#реклама", "#продажи", "#коротко"),
    "meme": ("#мем", "#юмор", "#узналасебя"),
    "brand": ("#личныйбренд", "#мысливслух", "#фокус"),
    "cine": ("#кино", "#атмосфера", "#история"),
}

HOOKS: dict[str, str] = {
    "viral": "Стоп. Это меняет день.",
    "ad": "Коротко, зачем это вам.",
    "meme": "Ну вот опять.",
    "brand": "Вслух, без обёртки.",
    "cine": "Один кадр — одна мысль.",
}


def slug_filename(title: str, platform: str) -> str:
    slug = re.sub(r"[^\w]+", "_", (title or "").strip(), flags=re.UNICODE)
    slug = re.sub(r"_+", "_", slug).strip("_")[:40]
    return f"{slug or 'video'}_{platform}.mp4"


def topic_tags(topic: str, limit: int = 2) -> list[str]:
    words = re.findall(r"[A-Za-zА-Яа-яЁё0-9]{4,}", topic or "")
    tags: list[str] = []
    for word in words:
        low = word.lower()
        if low in STOPWORDS:
            continue
        tag = "#" + re.sub(r"[^\w]", "", low, flags=re.UNICODE)
        if tag == "#" or tag in tags:
            continue
        tags.append(tag)
        if len(tags) >= limit:
            break
    return tags


def hashtags(preset: str, topic: str, platform: str) -> list[str]:
    cap = 5 if platform == "instagram" else 6
    extra = "#reels" if platform == "instagram" else "#tiktok"
    out = [BRAND_TAG]
    if extra not in out:
        out.append(extra)
    for tag in list(PRESET_TAGS.get(preset, ())) + topic_tags(topic):
        if tag in out:
            continue
        out.append(tag)
        if len(out) >= cap:
            break
    return out[:cap]


def hook_line(preset: str, topic: str) -> str:
    first = re.split(r"[.!?]", topic or "", maxsplit=1)[0].strip()
    if first and len(first) <= 80:
        return first
    return HOOKS.get(preset, HOOKS["viral"])


def caption(preset: str, topic: str, platform: str, *, title: str = "") -> str:
    hook = hook_line(preset, topic)
    tags = " ".join(hashtags(preset, topic, platform))
    head = title.strip() if title and title.strip() != hook else hook
    if platform == "instagram":
        body = f"{head}\n\n{topic.strip()}\n\n.\n.\n.\n\n{tags}"
    else:
        body = f"{head}\n\n{topic.strip()}\n\n{tags}"
    return body.strip()[:2200]


def outbox_dir(root: Path, day: date, slot_id: str) -> Path:
    return Path(root) / day.isoformat() / slot_id


def write_package(
    dest: Path,
    slot: Slot,
    *,
    day: date,
    mode: str,
    script: dict[str, Any] | None = None,
    video: Path | None = None,
    runway_credits: int = 0,
    quality: str = "",
) -> dict[str, Any]:
    dest.mkdir(parents=True, exist_ok=True)
    title = str((script or {}).get("title") or slot.topic)[:80]
    files: list[str] = []
    for platform in slot.platforms:
        name = f"{platform}_caption.txt"
        (dest / name).write_text(
            caption(slot.preset, slot.topic, platform, title=title),
            encoding="utf-8",
        )
        files.append(name)
    if script is not None:
        (dest / "script.json").write_text(
            json.dumps(script, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        files.append("script.json")
    video_name = ""
    if video and Path(video).is_file():
        final = dest / "final.mp4"
        src = Path(video)
        if src.resolve() != final.resolve():
            shutil.copyfile(src, final)
        files.append("final.mp4")
        video_name = "final.mp4"
        for platform in slot.platforms:
            named = dest / slug_filename(title, platform)
            if named.resolve() != final.resolve():
                shutil.copyfile(final, named)
            files.append(named.name)
    meta = {
        "brand": "Успех 888",
        "slot_id": slot.id,
        "date": day.isoformat(),
        "preset": slot.preset,
        "preset_label": (PRESETS.get(slot.preset) or {}).get("label", slot.preset),
        "topic": slot.topic,
        "platforms": list(slot.platforms),
        "quality": quality,
        "mode": mode,
        "title": title,
        "runway_credits": runway_credits,
        "video": video_name,
        "auto_publish": False,
        "publish_note": "Только ручная загрузка. Автопостинг в TikTok/Instagram выключен.",
    }
    (dest / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    files.append("meta.json")
    readme = [
        f"Успех 888 · {day.isoformat()} · {slot.id}",
        f"Пресет: {meta['preset_label']}",
        f"Тема: {slot.topic}",
        f"Режим: {mode}",
        "",
        "Как выложить:",
        "1) Открой final.mp4 или *_tiktok.mp4 / *_instagram.mp4",
        "2) Скопируй подпись из tiktok_caption.txt или instagram_caption.txt",
        "3) Залей вручную. Бот сам никуда не постит.",
    ]
    (dest / "README.txt").write_text("\n".join(readme) + "\n", encoding="utf-8")
    files.append("README.txt")
    meta["files"] = files
    meta["outbox"] = str(dest)
    return meta
