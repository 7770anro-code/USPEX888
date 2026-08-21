"""21 голос ElevenLabs (premade), выбор кнопками в Telegram."""

from __future__ import annotations

# id — публичные premade-голоса ElevenLabs. Подписи по-русски, коротко.
VOICES: list[dict[str, str]] = [
    {"id": "9BWtsMINqrJLrRacOk9x", "name": "Ария", "tag": "спокойная женская"},
    {"id": "EXAVITQu4vr4xnSDxMaL", "name": "Сара", "tag": "мягкая женская"},
    {"id": "FGY2WhTYpPnrIDTdsKH5", "name": "Лаура", "tag": "тёплая женская"},
    {"id": "Xb7hH8MSUJpSbSDYk0k2", "name": "Алиса", "tag": "уверенная женская"},
    {"id": "XrExE9yKIg1WjnnlVkGX", "name": "Матильда", "tag": "добрая женская"},
    {"id": "cgSgspJ2msm6clMCkdW9", "name": "Джессика", "tag": "яркая женская"},
    {"id": "pFZP5JQG7iQjIQuC4Bku", "name": "Лили", "tag": "нежная женская"},
    {"id": "XB0fDUnXU5powFXDhCwa", "name": "Шарлотта", "tag": "чёткая женская"},
    {"id": "21m00Tcm4TlvDq8ikWAM", "name": "Рэйчел", "tag": "классика женская"},
    {"id": "CwhRBWXzGAHq8TQ4Fs17", "name": "Роджер", "tag": "уверенный мужской"},
    {"id": "IKne3meq5aSn9XLyUdCD", "name": "Чарли", "tag": "живой мужской"},
    {"id": "JBFqnCBsd6RMkjVDRZzb", "name": "Джордж", "tag": "рассказчик"},
    {"id": "N2lVS1w4EtoT3dr4eOWO", "name": "Каллум", "tag": "хриплый мужской"},
    {"id": "TX3LPaxmHKxFdv7VOQHJ", "name": "Лиам", "tag": "молодой мужской"},
    {"id": "bIHbv24MWmeRgasZH58o", "name": "Уилл", "tag": "дружелюбный мужской"},
    {"id": "cjVigY5qzO86Huf0OWal", "name": "Эрик", "tag": "спокойный мужской"},
    {"id": "iP95p4xoKVk53GoZ742B", "name": "Крис", "tag": "ровный мужской"},
    {"id": "nPczCjzI2devNBz1zQrb", "name": "Брайан", "tag": "глубокий мужской"},
    {"id": "onwK4e9ZLuTAKqWW03F9", "name": "Дэниел", "tag": "серьёзный мужской"},
    {"id": "pqHfZKP75CvOlQylNhV4", "name": "Билл", "tag": "доверительный мужской"},
    {"id": "SAz9YHcvj6GT2YYXdXww", "name": "Ривер", "tag": "нейтральный мягкий"},
]

assert len(VOICES) == 21


def voice_by_index(idx: int) -> dict[str, str]:
    if 0 <= idx < len(VOICES):
        return VOICES[idx]
    return VOICES[1]  # Сара


def voice_label(v: dict[str, str]) -> str:
    return f"{v['name']} — {v['tag']}"
