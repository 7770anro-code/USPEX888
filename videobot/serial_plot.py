"""План серии reveal-мультсериала: running summary + сид арки, без скачивания чужого видео."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import aiohttp

from night_circuit import GROK, with_breaker
from night_ideas import _grok_raw
from pipeline import PipelineError, SCRIPT_LOCK

log = logging.getLogger("videobot.serial")

SERIAL_SLUG = "hybrids"
MAX_BATCH = 7
N_SCENES = 4

DEFAULT_TITLE = "Гибриды"
DEFAULT_SEED = (
    "Вертикальный мультсериал-скетч в трендовом reveal-формате, одна непрерывная история "
    "минимум на 30 серий. Стилизованный 3D-мульт, не live-action и не реальные бренды. "
    "Завязка: в городке Плодск живут пары фруктов и овощей (клубника + арбуз, петрушка + малина "
    "и другие выдуманные пары без торговых марок). Каждая пара в свой момент «рожает» гибрид-ребёнка "
    "— смешное существо с чертами обоих родителей (ягода-арбуз, лист-малина). Параллельная линия: "
    "по улице выезжает стилизованная машина-капля без логотипов и эмблем, следом появляется машина "
    "поменьше как «ребёнок». Семьи гибридов растут, дружат, ссорятся, строят общий двор, "
    "скрывают секреты, готовят большой праздник урожая. Каждая серия — новый reveal и клиффхэнгер "
    "на следующую. Не отдельные вирусные скетчи, а мыльная опера: имена, обиды и обещания копятся."
)
DEFAULT_LORE = (
    "Клубника Аля — маленькая, быстрая, в точечном платье. Арбуз Боря — большой, добродушный, "
    "в полосатой жилетке. Их ещё нет гибрида в серии 1 — он родится по ходу арки. "
    "Петрушка Петя — худой зелёный, Малина Маша — круглая и яркая. "
    "Капля-машина Синий Нос — округлый транспорт без решётки и логотипа, говорит гудком. "
    "Городок Плодск: рынок, двор, гараж-сарай, холм на закате. Визуальный стиль один на все серии."
)
DEFAULT_CONTINUITY = (
    "stylized 3D cartoon, appealing round shapes, painterly light, saturated fruit colors, "
    "no live-action, no iPhone, no ARRI, no logos, no brand emblems, no photoreal people, "
    "same town Płodsk market and yard, same character silhouettes and outfits every shot"
)

SERIAL_EPISODE_SYSTEM = """Ты шоураннер вертикального мультсериала 9:16 (TikTok). Одна непрерывная история.
Верни ТОЛЬКО JSON без markdown:

{
  "title": "название этой серии",
  "hook": "цепляющая фраза 1-й секунды, речь персонажа, не описание кадра",
  "plot": "2–4 предложения ТОЛЬКО этой серии, прямое продолжение предыдущей",
  "reveal": "что именно «рождается»/выезжает в этой серии",
  "cliffhanger": "крючок на следующую серию, конкретный незакрытый факт",
  "caption": "текст поста + 3–5 хештегов без брендов",
  "continuity": "ONE English locked look: shapes, colors, outfits, town, lighting. No logos. No photoreal face.",
  "lore_add": "новые персонажи/факты одной строкой, или пусто",
  "summary_update": "сжатая память всего сериала после этой серии, до 900 символов: кто есть, что случилось, что не закрыто"
}

Жёстко:
- Это ПРОДОЛЖЕНИЕ, не пилот с нуля (кроме серии 1). Не пересказывай всю арку в plot.
- Формат reveal: пара фруктов/овощей «рожает» гибрид-ребёнка ИЛИ стилизованная машина без логотипа «рожает» машину поменьше. Чередуй линии, если в сиде обе.
- Без реальных брендов, логотипов авто/еды, знаменитостей, NSFW, скачивания чужих роликов.
- Учитывай блок «правки владельца» дословно.
- summary_update короче сида+прошлого summary: имена, отношения, открытый клиффхэнгер.
- hook — разговорная фраза, её потом ставят в narration сцены 1.
"""

SERIAL_SCRIPT_SYSTEM = f"""Ты режиссёр ОДНОЙ серии вертикального мультсериала 30–40 секунд (кадр 9:16). Стилизованный 3D, не фото человека.
Верни ТОЛЬКО JSON без markdown:

{{
  "title": "короткий заголовок серии",
  "continuity": "ONE locked English look for EVERY shot: character shapes/colors/outfits, town, lighting. No logos, no brand emblems, no photoreal face. No camera motion here.",
  "scenes": [
    {{
      "narration": "озвучка на русском, 18–28 слов",
      "visual_prompt": "English CAMERA AND ACTION, 1–2 sentences. Cartoon 3D. Keep the SAME characters from continuity."
    }}
  ]
}}

Правила:
- {SCRIPT_LOCK} Консистентность персонажей важнее трюка. Новых героев не вводить, кроме reveal этой серии.
- Стиль cartoon: 3D/графика, без shot on iPhone/ARRI, без логотипов и эмблем.
- Камера может быть энергичной (punch-in, whip pan). 4 сцены.
- Каждая narration СТРОГО 18–28 слов.
- Сцена 1 начинается с ХУКА из брифа.
- Одна из средних сцен — REVEAL (гибрид-ребёнок или машина-малыш появляется).
- Последняя сцена — клиффхэнгер на следующую серию, не закрывай сюжет.
- Призыв к действию не только в финале: в сценах 1–3 хотя бы раз «не листай / досмотри / подпишись».
- Без текста на экране, брендов, знаменитостей, NSFW, watermark.
"""


def parse_episode_plan(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        raise PipelineError("Пустой план серии.")
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S)
    data = json.loads(text)
    if not isinstance(data, dict):
        raise PipelineError("План серии не JSON-объект.")
    title = str(data.get("title") or "").strip()[:180]
    plot = str(data.get("plot") or "").strip()[:2000]
    if not plot:
        raise PipelineError("В плане серии нет plot.")
    hook = str(data.get("hook") or title or "").strip()[:240]
    cliff = str(data.get("cliffhanger") or "").strip()[:500]
    caption = str(data.get("caption") or title).strip()[:2200]
    continuity = str(data.get("continuity") or "").strip()[:900]
    lore_add = str(data.get("lore_add") or "").strip()[:500]
    summary = str(data.get("summary_update") or "").strip()[:1200]
    reveal = str(data.get("reveal") or "").strip()[:400]
    return {
        "title": title or "Серия",
        "plot": plot,
        "hook": hook,
        "cliffhanger": cliff,
        "caption": caption,
        "continuity": continuity,
        "lore_add": lore_add,
        "summary_update": summary or plot[:900],
        "reveal": reveal,
    }


def planner_user(serial: dict[str, Any], notes: list[dict[str, Any]], *, n: int) -> str:
    last_plot = ""
    last_cliff = str(serial.get("last_cliff") or "")
    note_lines = [str(item.get("text") or "") for item in notes if item.get("text")]
    notes_blob = "\n".join(f"- {t}" for t in note_lines[:8]) or "нет"
    return (
        f"Серия номер {n} из длинной арки (цель 30+).\n"
        f"Название сериала: {serial.get('title') or DEFAULT_TITLE}\n"
        f"Сид арки:\n{(serial.get('seed') or DEFAULT_SEED)[:1800]}\n\n"
        f"Лор/персонажи:\n{(serial.get('lore') or DEFAULT_LORE)[:1200]}\n\n"
        f"Визуальный continuity (сохрани, дополни только если новый герой):\n"
        f"{(serial.get('continuity') or DEFAULT_CONTINUITY)[:900]}\n\n"
        f"Сжатая память сюжета:\n{(serial.get('summary') or 'пока пусто, это серия 1')[:1200]}\n\n"
        f"Клиффхэнгер прошлой серии: {last_cliff or 'нет'}\n"
        f"Прошлая серия (если есть): {last_plot}\n\n"
        f"Правки владельца (обязательны):\n{notes_blob}\n"
    )


def attach_last_plot(user: str, last: dict[str, Any] | None) -> str:
    if not last:
        return user
    extra = (
        f"Прошлая серия #{last.get('n')}: {last.get('title')}. "
        f"Сюжет: {(last.get('plot') or '')[:700]}. "
        f"Клиффхэнгер: {(last.get('cliffhanger') or '')[:300]}."
    )
    return user.replace("Прошлая серия (если есть): ", extra + "\nПрошлая серия (если есть): ")


def serial_script_brief(serial: dict[str, Any], plan: dict[str, Any]) -> str:
    return (
        "Это ОДНА серия мультсериала, не отдельный ролик. "
        "Только синтетика, cartoon 3D, без логотипов и брендов.\n"
        f"Continuity lock (не менять без нужды): "
        f"{plan.get('continuity') or serial.get('continuity') or DEFAULT_CONTINUITY}\n"
        f"Reveal этой серии: {plan.get('reveal') or 'гибрид или машина-малыш'}\n"
        f"Клиффхэнгер в последней сцене: {plan.get('cliffhanger') or ''}\n"
        f"Сюжет серии: {plan.get('plot') or ''}\n"
        "Последняя сцена не закрывает историю. Ранний CTA: не листай / досмотри / подпишись."
    )


async def plan_episode(
    session: aiohttp.ClientSession,
    serial: dict[str, Any],
    notes: list[dict[str, Any]],
    *,
    n: int,
    last: dict[str, Any] | None,
) -> dict[str, Any]:
    user = attach_last_plot(planner_user(serial, notes, n=n), last)

    async def _call() -> str:
        return await _grok_raw(
            session,
            user,
            system=SERIAL_EPISODE_SYSTEM,
            temperature=0.7,
        )

    raw = await with_breaker(GROK, _call, retries=3)
    try:
        return parse_episode_plan(raw)
    except Exception:
        log.warning("serial plan parse retry")
        raw2 = await with_breaker(
            GROK,
            lambda: _grok_raw(
                session,
                user + "\nПОВТОР: верни ТОЛЬКО валидный JSON по схеме.",
                system=SERIAL_EPISODE_SYSTEM,
                temperature=0.5,
            ),
            retries=2,
        )
        return parse_episode_plan(raw2)
