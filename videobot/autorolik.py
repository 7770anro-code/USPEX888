"""Авторолик: хайповый монтаж UKRAINIAN CORE, 4–8 сцен, face_scene → Kling / Seedance."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import aiohttp

import config
from pipeline import PipelineError, _clip, _grok_once

log = logging.getLogger("videobot")

MAX_PHOTOS = 6
MIN_SCENES = 4
MAX_SCENES = 8
ELEMENT_RE = re.compile(r"@Element\s*(\d+)|@element_(\d+)", re.I)

LOCKED_GRADE = (
    "LOCKED GRADE on every shot (Kling and Seedance must match): "
    "warm sunset amber + cool club backlight, photoreal 9:16 live-action, "
    "no on-screen text, no logos, no watermark"
)
FACE_CAMERA = (
    "slight handheld push-in or handheld shake, shallow depth of field, "
    "same locked warm amber / cool backlight grade, "
    "subject looks slightly away or turns toward camera"
)
WIDE_CAMERA = (
    "more camera movement than a portrait: drone over a hazy city monument with helicopters, "
    "slow push-in; or low-angle lateral tracking of a convoy in fog with headlights; "
    "or rack focus on a radiator grille and lamps; or crowd silhouettes in a backlit hall. "
    "Face is not the subject. Same locked warm amber / cool backlight grade as the portraits."
)
WIDE_SUBJECT_RE = re.compile(
    r"\b(drone|aerial|cityscape|monument|helicopter|convoy|fog|"
    r"radiator|grille|headlights?|crowd|silhouette|"
    r"from behind|back to (the )?camera|over[- ]the[- ]shoulder|"
    r"glimpsed?|partial(?:ly)?|passing by|traffic)\b",
    re.I,
)
FACE_CLOSE_RE = re.compile(
    r"\b(close-?up|medium (?:close-?up|shot)|portrait|face|"
    r"looking (?:at|into) camera|turns toward|shallow DOF|"
    r"rack(?:s)? focus (?:onto|to) (?:his|her|their )?face)\b",
    re.I,
)

SCRIPT_SYSTEM = """Ты режиссёр вертикального Reels 9:16 в стиле «хайповый монтаж / UKRAINIAN CORE».
Эталон: тёплый закат и контровый клубный свет. Между портретами друзей — масштаб страны/ночи/дорог.
Верни ТОЛЬКО JSON без markdown:

{
  "title": "короткий заголовок",
  "hook": "удар первой секунды, 4–8 слов",
  "caption": "подпись для Reels без водяного знака на кадре",
  "continuity": "English locked grade: warm sunset amber + cool club backlight, 9:16, photoreal live-action, no on-screen text, no logos, no watermark. Do not describe faces here. Same grade on EVERY shot so Kling/Seedance cut is invisible.",
  "scenes": [
    {
      "narration": "озвучка языком пользователя, 12–18 слов",
      "visual_prompt": "English, 1–2 sentences, camera + action + the same locked grade",
      "face_scene": true,
      "element_index": 1
    }
  ]
}

Правило face_scene (подлежащее кадра, не «есть ли человек где-то в кадре»):
- true (Kling @ElementN): подлежащее — конкретный друг крупно/узнаваемо, лицо читается.
  Камера тише: лёгкий наезд или хендхелд, неглубокая резкость, тёплый закат ИЛИ контровый клубный.
  Пример: "man steps out of a car at sunset, camera racks focus onto his face, headlight bokeh, warm amber grade, shallow DOF, slight handheld push-in".
  В visual_prompt — @ElementN (N = номер фото 1..P). Лицо не выдумывать.
- false (Seedance, безопаснее): подлежащее — город/машины/толпа/предмет; ИЛИ друг виден мельком / со спины / частично / силуэтом.
  Масштаб и движение: дрон над городом (памятник, вертолёты, дымка, медленный наезд); колонна в тумане (низкий ракурс, боковой трекинг, фары); решётка радиатора/фары (рек-фокус); толпа/зал, силуэты в контровом. Камеры больше, чем в FACE. element_index = 0. Узнаваемое лицо не в центре — Seedance такое не флагает.

Остальное:
- Ровно N сцен (N в запросе), 4–8. Чередуй FACE и WIDE: не две FACE подряд больше одного раза, не все WIDE подряд.
- continuity + каждый visual_prompt несут ОДИН тёплый/контровый цветокор. Смена Kling/Seedance не должна читаться по картинке.
- narration сцены 1 начинается с hook.
- Без текста на экране, логотипов, знаменитостей, NSFW. Живое кино, не CGI. UKRAINIAN CORE — янтарь заката, ночные фары, бетон, дымка; не надпись в кадре.
- Публичных лиц (политики, актёры, певцы) НЕ добавляй. Референс украинских публичных лиц — только про стиль камеры и свет, не про персонажей. В кадре только друзья с присланных фото.
"""


def pending_path(user_id: int) -> Path:
    folder = Path(config.DATA_DIR) / "autorolik"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{int(user_id)}.json"


def save_pending(user_id: int, payload: dict[str, Any]) -> Path:
    path = pending_path(user_id)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_pending(user_id: int) -> dict[str, Any] | None:
    path = pending_path(user_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def clear_pending(user_id: int) -> None:
    path = pending_path(user_id)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
    folder = photos_dir(user_id)
    try:
        import shutil

        shutil.rmtree(folder, ignore_errors=True)
    except OSError:
        pass


def photos_dir(user_id: int) -> Path:
    folder = Path(config.DATA_DIR) / "autorolik" / f"{int(user_id)}_photos"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def script_view(script: dict[str, Any] | None) -> dict[str, Any]:
    data = script if isinstance(script, dict) else {}
    scenes_out: list[dict[str, Any]] = []
    for i, scene in enumerate(data.get("scenes") or [], start=1):
        if not isinstance(scene, dict):
            continue
        face = parse_bool(scene.get("face_scene"))
        idx = int(scene.get("element_index") or 0)
        scenes_out.append(
            {
                "n": i,
                "face": face,
                "element_index": idx,
                "tag": (f"FACE · друг {idx} · Kling" if face else "WIDE · Seedance"),
                "narration": str(scene.get("narration") or "").strip(),
                "visual": str(scene.get("visual_prompt") or "").strip(),
            }
        )
    return {
        "title": str(data.get("title") or "Авторолик").strip() or "Авторолик",
        "hook": str(data.get("hook") or "").strip(),
        "caption": str(data.get("caption") or "").strip(),
        "n_scenes": len(scenes_out),
        "scenes": scenes_out,
    }


def apply_manual_script_edits(script: dict[str, Any] | None, edits: dict[str, Any] | None, *, n_photos: int = 1) -> dict[str, Any]:
    """Точечные правки title/hook/narration/visual, без смены FACE/WIDE."""
    if not isinstance(script, dict) or not isinstance(script.get("scenes"), list):
        raise PipelineError("Нет сценария, который можно править.")
    data = json.loads(json.dumps(script, ensure_ascii=False))
    blob = edits if isinstance(edits, dict) else {}
    title = str(blob.get("title") or "").strip()
    if title:
        data["title"] = title[:80]
    if "hook" in blob:
        data["hook"] = str(blob.get("hook") or "").strip()[:120]
    for item in blob.get("scenes") or []:
        if not isinstance(item, dict):
            continue
        try:
            n = int(item.get("n") or 0)
        except (TypeError, ValueError):
            continue
        if n < 1 or n > len(data["scenes"]):
            continue
        scene = dict(data["scenes"][n - 1] or {})
        if "narration" in item:
            scene["narration"] = str(item.get("narration") or "").strip()[:500]
        if "visual" in item or "visual_prompt" in item:
            scene["visual_prompt"] = str(item.get("visual") or item.get("visual_prompt") or "").strip()[:1500]
        data["scenes"][n - 1] = scene
    n = max(1, min(MAX_PHOTOS, int(n_photos or 1)))
    return parse_autorolik_script(json.dumps(data, ensure_ascii=False), n_photos=n)


def pending_view(pending: dict[str, Any] | None) -> dict[str, Any]:
    data = pending if isinstance(pending, dict) else {}
    photos = data.get("photo_paths") or data.get("photo_file_ids") or []
    n_photos = len(photos) if isinstance(photos, list) else 0
    return {
        "phase": str(data.get("phase") or ""),
        "error": str(data.get("error") or ""),
        "idea": str(data.get("idea") or ""),
        "n_photos": n_photos,
        "script": script_view(data.get("script") if isinstance(data.get("script"), dict) else None),
    }


def parse_bool(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    blob = str(raw or "").strip().lower()
    return blob in ("1", "true", "yes", "on", "face", "face_scene")


def decide_face_scene(raw: Any, visual: str, *, omitted: bool) -> bool:
    """Подлежащее: друг крупно → Kling; город/мельком/спина → Seedance (безопаснее)."""
    vis = visual or ""
    wide = bool(WIDE_SUBJECT_RE.search(vis))
    close = bool(FACE_CLOSE_RE.search(vis) or ELEMENT_RE.search(vis))
    if omitted:
        return bool(close and not wide)
    face = parse_bool(raw)
    if face and wide and not close:
        return False
    return face


def clamp_element(raw: Any, n_photos: int, *, face: bool) -> int:
    if not face:
        return 0
    n_photos = max(1, min(MAX_PHOTOS, int(n_photos or 1)))
    try:
        idx = int(raw)
    except (TypeError, ValueError):
        idx = 1
    if idx < 1:
        token = ELEMENT_RE.search(str(raw or ""))
        if token:
            idx = int(token.group(1) or token.group(2) or 1)
        else:
            idx = 1
    return max(1, min(n_photos, idx))


def parse_autorolik_script(raw: str, *, n_photos: int) -> dict[str, Any]:
    """JSON сцен с face_scene. 4–8 штук. n_photos 1..6."""
    text = (raw or "").strip()
    if not text:
        raise PipelineError("Сценарий Авторолика пустой.")
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S | re.I)
    if fence:
        text = fence.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise PipelineError("Сценарий Авторолика не JSON.", _clip(raw, 240))
        text = text[start : end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PipelineError("Не разобрал JSON Авторолика.", str(exc)) from exc
    n_photos = max(1, min(MAX_PHOTOS, int(n_photos or 1)))
    scenes_in = data.get("scenes")
    if not isinstance(scenes_in, list) or not scenes_in:
        raise PipelineError("В Авторолике нет сцен.")
    cleaned: list[dict[str, Any]] = []
    for i, scene in enumerate(scenes_in[:MAX_SCENES]):
        if not isinstance(scene, dict):
            continue
        narration = str(scene.get("narration") or "").strip()
        visual = str(scene.get("visual_prompt") or scene.get("visualPrompt") or "").strip()
        if not narration:
            continue
        omitted = "face_scene" not in scene and "faceScene" not in scene
        face = decide_face_scene(
            scene.get("face_scene", scene.get("faceScene")),
            visual,
            omitted=omitted,
        )
        idx = clamp_element(
            scene.get("element_index") or scene.get("elementIndex") or visual,
            n_photos,
            face=face,
        )
        if not visual:
            visual = (
                "close-up of @Element1 at sunset, shallow DOF, slight handheld push-in, warm amber grade"
                if face
                else "drone over a hazy city monument, slow push-in, helicopters, dusk smoke, locked warm amber grade, 9:16"
            )
        if face:
            token = f"@Element{idx}"
            if not ELEMENT_RE.search(visual):
                visual = f"{token} {visual}"
        else:
            visual = ELEMENT_RE.sub("", visual).strip()
            idx = 0
        cleaned.append(
            {
                "narration": narration[:500],
                "visual_prompt": visual[:1500],
                "face_scene": face,
                "element_index": idx if face else 0,
            }
        )
    if len(cleaned) < MIN_SCENES:
        raise PipelineError(f"Нужно {MIN_SCENES}–{MAX_SCENES} сцен, пришло {len(cleaned)}.")
    if n_photos >= 1 and not any(s["face_scene"] for s in cleaned):
        cleaned[0]["face_scene"] = True
        cleaned[0]["element_index"] = 1
        if not ELEMENT_RE.search(cleaned[0]["visual_prompt"]):
            cleaned[0]["visual_prompt"] = "@Element1 " + cleaned[0]["visual_prompt"]
    if not any(not s["face_scene"] for s in cleaned) and len(cleaned) >= 2:
        cleaned[1]["face_scene"] = False
        cleaned[1]["element_index"] = 0
        cleaned[1]["visual_prompt"] = ELEMENT_RE.sub("", cleaned[1]["visual_prompt"]).strip()
    title = str(data.get("title") or "Авторолик").strip()[:80] or "Авторолик"
    hook = str(data.get("hook") or "").strip()[:120]
    caption = str(data.get("caption") or "").strip()[:400]
    continuity = str(data.get("continuity") or "").strip()[:800]
    if not continuity:
        continuity = LOCKED_GRADE
    return {
        "title": title,
        "hook": hook,
        "caption": caption,
        "continuity": continuity,
        "scenes": cleaned,
        "kind": "autorolik",
    }


def route_for_scene(scene: dict[str, Any] | bool) -> str:
    face = parse_bool(scene.get("face_scene") if isinstance(scene, dict) else scene)
    return "autorolik_face" if face else "autorolik_wide"


def scene_camera(scene: dict[str, Any] | bool) -> str:
    face = parse_bool(scene.get("face_scene") if isinstance(scene, dict) else scene)
    return FACE_CAMERA if face else WIDE_CAMERA


def pick_element_uri(scene: dict[str, Any], uris: list[str]) -> str:
    if not uris:
        return ""
    idx = clamp_element(scene.get("element_index"), len(uris), face=True)
    return uris[idx - 1]


def kling_api_prompt(visual: str, *, element_index: int = 1) -> str:
    """В API одна картинка = всегда @Element1. В сценарии остаётся @ElementN."""
    text = ELEMENT_RE.sub("@Element1", visual or "")
    if "@Element1" not in text:
        text = f"@Element1 is the same person, same face and clothes. {text}"
    _ = element_index
    return text


def format_script_preview(script: dict[str, Any]) -> str:
    scenes = script.get("scenes") or []
    lines = [
        f"🎞  {script.get('title') or 'Авторолик'}",
        f"{len(scenes)} сцен · FACE → Kling · WIDE → Seedance",
    ]
    hook = str(script.get("hook") or "").strip()
    if hook:
        lines.append(f"Хук: {hook}")
    lines.append("────────")
    for i, scene in enumerate(scenes, start=1):
        face = parse_bool(scene.get("face_scene"))
        if face:
            tag = f"FACE · друг {int(scene.get('element_index') or 1)}"
        else:
            tag = "WIDE"
        narr = str(scene.get("narration") or "").strip()
        vis = str(scene.get("visual_prompt") or "").strip()
        lines.append(f"{i}  {tag}")
        if narr:
            lines.append(narr)
        if vis:
            lines.append(vis)
        lines.append("")
    lines.append("Можно закрыть Telegram — съёмка доварится на сервере, ролик придёт сюда.")
    return "\n".join(lines).strip()[:3500]


def photos_kb(*, count: int) -> Any:
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    rows: list[list[InlineKeyboardButton]] = []
    if count >= 1:
        rows.append(
            [InlineKeyboardButton(text=f"➡️ Дальше ({count}/{MAX_PHOTOS})", callback_data="auto:next")]
        )
    rows.append([InlineKeyboardButton(text="🗑 Сбросить фото", callback_data="auto:reset")])
    rows.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def review_kb() -> Any:
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎬 Снять ролик", callback_data="auto:go")],
            [InlineKeyboardButton(text="✏️ Описать правки", callback_data="auto:edit")],
            [InlineKeyboardButton(text="✕ Отмена", callback_data="auto:no")],
        ]
    )


def topic_kb() -> Any:
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Без темы — сам соберу", callback_data="auto:notopic")],
            [InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:home")],
        ]
    )


async def grok_autorolik(
    session: aiohttp.ClientSession,
    *,
    n_photos: int,
    idea: str = "",
    notes: str = "",
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if config.XAI_API_KEY_ERROR:
        raise PipelineError("Ключ Grok в неправильном формате.", config.XAI_API_KEY_ERROR)
    if not config.XAI_API_KEY_NEW:
        raise PipelineError("Нет XAI_API_KEY_NEW — сценарий Авторолика не собрать.")
    n_photos = max(1, min(MAX_PHOTOS, int(n_photos or 1)))
    n_scenes = max(MIN_SCENES, min(MAX_SCENES, 4 + min(4, n_photos)))
    idea = (idea or "").strip() or "хайповый монтаж друзей, закат, ночной город, UKRAINIAN CORE"
    body = (
        f"Фото друзей: {n_photos} шт. Нумерация @Element1…@Element{n_photos}.\n"
        f"Сделай ровно {n_scenes} сцен (можно {MIN_SCENES}–{MAX_SCENES}, но сейчас {n_scenes}).\n"
        "face_scene: подлежащее = конкретный друг крупно/узнаваемо → true (Kling); "
        "подлежащее = город/машины/толпа/предмет, или друг мельком/со спины/частично → false (Seedance).\n"
        f"Один цветокор на все сцены: {LOCKED_GRADE}\n"
        f"Тема / вайб:\n{idea[:1500]}\n"
    )
    if previous:
        body += "\nПредыдущий JSON (учти правки, верни полный JSON заново):\n"
        body += json.dumps(previous, ensure_ascii=False)[:3500]
    if notes.strip():
        body += "\n\nПравки владельца, обязательны:\n" + notes.strip()[:1500]
    last_err = ""
    for model in config.xai_creative_models():
        if not model:
            continue
        content, last_err = await _grok_once(
            session,
            [
                {"role": "system", "content": SCRIPT_SYSTEM},
                {"role": "user", "content": body},
            ],
            model,
            temperature=0.55,
        )
        if not content:
            continue
        try:
            return parse_autorolik_script(content, n_photos=n_photos)
        except PipelineError as exc:
            last_err = exc.user_message
            continue
    raise PipelineError("Не собрал сценарий Авторолика.", last_err or "Grok пустой")
