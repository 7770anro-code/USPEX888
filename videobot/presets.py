"""Пресеты, подача, камера, качество и оценка кредитов. Без названий моделей в UI."""

from __future__ import annotations

from typing import Any

# Runway: по факту gen4.5 ~12 кр/с (10с = 120). turbo I2V дешевле — оценка 5 кр/с.
RUNWAY_CREDITS_PER_SEC = {"fast": 5, "optimal": 12}
STILL_CREDITS = 5
ELEVEN_CREDITS_PER_CHAR = 1
DEFAULT_CLIP_SEC = 10

DELIVERY: dict[str, dict[str, Any]] = {
    "energy": {
        "label": "Энергично",
        "stability": 0.30,
        "similarity_boost": 0.75,
        "style": 0.55,
        "use_speaker_boost": True,
    },
    "calm": {
        "label": "Спокойно",
        "stability": 0.70,
        "similarity_boost": 0.75,
        "style": 0.10,
        "use_speaker_boost": True,
    },
    "drama": {
        "label": "Драматично",
        "stability": 0.35,
        "similarity_boost": 0.78,
        "style": 0.75,
        "use_speaker_boost": True,
    },
    "sure": {
        "label": "Уверенно",
        "stability": 0.55,
        "similarity_boost": 0.82,
        "style": 0.30,
        "use_speaker_boost": True,
    },
    "humor": {
        "label": "С юмором",
        "stability": 0.25,
        "similarity_boost": 0.65,
        "style": 0.80,
        "use_speaker_boost": True,
    },
    "whisper": {
        "label": "Шёпотом",
        "stability": 0.82,
        "similarity_boost": 0.80,
        "style": 0.08,
        "use_speaker_boost": True,
    },
}

SPEED: dict[str, dict[str, Any]] = {
    "slow": {"label": "Медленно", "value": 0.9},
    "norm": {"label": "Обычно", "value": 1.0},
    "fast": {"label": "Быстро", "value": 1.1},
    "xfst": {"label": "Очень быстро", "value": 1.2},
}

QUALITY: dict[str, dict[str, Any]] = {
    "fast": {
        "label": "Быстро",
        "hint": "дешевле, чуть проще картинка",
        "i2v_model": "gen4_turbo",
        "prefer_t2v": False,
    },
    "optimal": {
        "label": "Оптимально",
        "hint": "дороже и лучше",
        "i2v_model": "gen4.5",
        "prefer_t2v": True,
    },
}

CAMERA: dict[str, dict[str, str]] = {
    "lock": {"label": "Статично", "prompt": "camera holds static"},
    "push": {"label": "Плавное приближение", "prompt": "slow subtle push-in"},
    "pull": {"label": "Плавное отдаление", "prompt": "slow subtle pull-back"},
    "pan": {"label": "Слева-направо", "prompt": "gentle pan, camera stays level"},
    "orbit": {"label": "Облёт", "prompt": "very slow small-arc orbit around the subject"},
}

MOTION: dict[str, dict[str, str]] = {
    "min": {"label": "Минимальное", "prompt": "minimal body movement"},
    "nat": {"label": "Естественное", "prompt": "subtle head turn, small natural gestures"},
    "dyn": {"label": "Динамичное", "prompt": "a bit more body movement, still gentle"},
}

# voice_idx — индекс в voices.VOICES, в UI не показываем id.
PRESETS: dict[str, dict[str, Any]] = {
    "viral": {
        "label": "Вирусный TikTok",
        "n_scenes": 5,
        "style": "ad",
        "voice_idx": 5,  # Джессика
        "delivery": "energy",
        "speed": "fast",
        "quality": "optimal",
        "camera": "push",
        "motion": "dyn",
        "brief": (
            "Формат: вирусный вертикальный TikTok. "
            "Первая сцена — хук в первых 8 словах, pattern interrupt. "
            "Короткие фразы, быстрый темп. "
            "Последняя сцена заканчивается CTA: «Подпишись, если хочешь ещё таких»."
        ),
    },
    "ad": {
        "label": "Реклама товара",
        "n_scenes": 4,
        "style": "ad",
        "voice_idx": 1,  # Сара
        "delivery": "sure",
        "speed": "norm",
        "quality": "optimal",
        "camera": "push",
        "motion": "nat",
        "brief": (
            "Формат: реклама товара. Первая сцена — выгода для зрителя в одном предложении. "
            "Показать продукт в кадре, без логотипов на экране. "
            "Последняя сцена CTA: «Жми, пока не разобрали»."
        ),
    },
    "meme": {
        "label": "Мем",
        "n_scenes": 4,
        "style": "cartoon",
        "voice_idx": 10,  # Чарли
        "delivery": "humor",
        "speed": "fast",
        "quality": "fast",
        "camera": "lock",
        "motion": "dyn",
        "brief": (
            "Формат: мем. Первая сцена — абсурдный хук. Ирония, без оскорблений. "
            "Последняя сцена CTA: «Дуэт, если узнала себя»."
        ),
    },
    "brand": {
        "label": "Личный бренд",
        "n_scenes": 5,
        "style": "cinematic",
        "voice_idx": 14,  # Уилл
        "delivery": "sure",
        "speed": "norm",
        "quality": "optimal",
        "camera": "push",
        "motion": "nat",
        "brief": (
            "Формат: личный бренд. Первая сцена — личный инсайт, как будто говоришь в камеру. "
            "Последняя сцена CTA: «Подпишись, чтобы не пропустить следующее»."
        ),
    },
    "cine": {
        "label": "Кино-история",
        "n_scenes": 6,
        "style": "cinematic",
        "voice_idx": 11,  # Джордж
        "delivery": "drama",
        "speed": "slow",
        "quality": "optimal",
        "camera": "orbit",
        "motion": "min",
        "brief": (
            "Формат: короткая кино-история. Первая сцена — атмосфера, не реклама. "
            "Медленный ритм, визуал важнее текста. "
            "Финал без жёсткого CTA, мягкое закрытие кадра."
        ),
    },
}


def voice_settings_payload(delivery_key: str, speed_key: str) -> dict[str, Any]:
    d = DELIVERY.get(delivery_key) or DELIVERY["sure"]
    s = SPEED.get(speed_key) or SPEED["norm"]
    speed = float(s["value"])
    speed = max(0.7, min(1.2, speed))
    return {
        "stability": float(d["stability"]),
        "similarity_boost": float(d["similarity_boost"]),
        "style": float(d["style"]),
        "use_speaker_boost": bool(d.get("use_speaker_boost", True)),
        "speed": speed,
    }


def camera_prompt(key: str) -> str:
    return (CAMERA.get(key) or CAMERA["push"])["prompt"]


def motion_prompt(key: str) -> str:
    return (MOTION.get(key) or MOTION["nat"])["prompt"]


def apply_preset(job: dict[str, Any], preset_id: str) -> dict[str, Any]:
    p = PRESETS[preset_id]
    job["preset_id"] = preset_id
    job["n_scenes"] = int(p["n_scenes"])
    job["style"] = p["style"]
    job["voice_idx"] = int(p["voice_idx"])
    job["delivery"] = p["delivery"]
    job["speed"] = p["speed"]
    job["quality"] = p["quality"]
    job["camera"] = p["camera"]
    job["motion"] = p["motion"]
    job["brief"] = p["brief"]
    return job


def default_job(*, mode: str) -> dict[str, Any]:
    return {
        "mode": mode,
        "preset_id": "",
        "idea": "",
        "user_script": mode == "custom",
        "n_scenes": 5,
        "style": "cinematic",
        "voice_idx": 1,
        "delivery": "sure",
        "speed": "norm",
        "quality": "optimal",
        "camera": "push",
        "motion": "nat",
        "brief": "",
        "photo_file_id": None,
        "consent_verified": False,
        "watermark": False,
    }


def estimate_cost(
    *,
    n_scenes: int,
    clip_sec: int = DEFAULT_CLIP_SEC,
    quality: str = "optimal",
    text: str = "",
    need_still: bool = False,
) -> dict[str, Any]:
    n_scenes = max(1, min(6, int(n_scenes or 1)))
    clip_sec = 10 if int(clip_sec) >= 8 else 5
    per_sec = RUNWAY_CREDITS_PER_SEC.get(quality, 12)
    runway = n_scenes * clip_sec * per_sec
    if need_still:
        runway += STILL_CREDITS
    chars = len((text or "").strip())
    eleven = chars * ELEVEN_CREDITS_PER_CHAR
    q_label = (QUALITY.get(quality) or QUALITY["optimal"])["label"]
    lines = [
        f"Клипы: {n_scenes} × {clip_sec} сек × {q_label} ≈ {n_scenes * clip_sec * per_sec} кр. Runway",
    ]
    if need_still:
        lines.append(f"Общий первый кадр ≈ {STILL_CREDITS} кр.")
    lines.append(f"Озвучка ≈ {chars} символов ElevenLabs")
    lines.append(f"Итого Runway ≈ {runway} кредитов + озвучка {eleven} кр.")
    return {
        "runway": runway,
        "eleven_chars": chars,
        "eleven": eleven,
        "total_runway": runway,
        "text": "\n".join(lines),
    }


def progress_bar(pct: int) -> str:
    pct = max(0, min(100, int(pct)))
    filled = round(pct / 10)
    return "▓" * filled + "░" * (10 - filled)


class StageProgress:
    """Процент только от завершённых весов этапов, не «нарисованный»."""

    W_SCRIPT = 12
    W_STILL = 8
    W_TTS = 20
    W_VIDEO = 50
    W_MUX = 10

    def __init__(self, n_scenes: int) -> None:
        self.n = max(1, int(n_scenes or 1))
        self.script_done = False
        self.still_done = False
        self.tts_done = 0
        self.video_done = 0
        self.mux_done = False

    def percent(self) -> int:
        p = 0.0
        if self.script_done:
            p += self.W_SCRIPT
        if self.still_done:
            p += self.W_STILL
        p += self.W_TTS * (self.tts_done / self.n)
        p += self.W_VIDEO * (self.video_done / self.n)
        if self.mux_done:
            p += self.W_MUX
        return min(100, int(round(p)))

    def render(self, label: str) -> str:
        pct = self.percent()
        return f"{progress_bar(pct)} {pct}%\n{label}"
