"""Три ночных аккаунта из .env. Разный голос/темп/стиль — меньше риск копипаста."""

from __future__ import annotations

from dataclasses import dataclass

import config
from voices import voice_by_index

THEMES = ("motivational", "absurd", "mixed")

_DEFAULTS = (
    {
        "id": "motiv",
        "theme": "motivational",
        "voice_idx": 14,  # Уилл
        "style": "cinematic",
        "delivery": "sure",
        "speed": "norm",
        "quality": "fast",
        "label": "мотивация",
    },
    {
        "id": "absurd",
        "theme": "absurd",
        "voice_idx": 10,  # Чарли
        "style": "cartoon",
        "delivery": "humor",
        "speed": "fast",
        "quality": "fast",
        "label": "абсурд",
    },
    {
        "id": "brand",
        "theme": "mixed",
        "voice_idx": 1,  # Сара
        "style": "ad",
        "delivery": "calm",
        "speed": "slow",
        "quality": "fast",
        "label": "бренд/смесь",
    },
)


@dataclass(frozen=True)
class Account:
    index: int
    id: str
    theme: str
    label: str
    voice_id: str
    voice_name: str
    style: str
    delivery: str
    speed: str
    quality: str
    tiktok_token_var: str
    ig_user_var: str
    ig_token_var: str
    has_tiktok: bool
    has_instagram: bool

    def token_blockers(self) -> list[str]:
        missing: list[str] = []
        if not self.has_tiktok:
            missing.append(self.tiktok_token_var)
        if not self.has_instagram:
            missing.append(self.ig_token_var)
            if not config._clean(self.ig_user_var):
                missing.append(self.ig_user_var)
        return missing


def _idx(n: int, key: str, default: str | int) -> str:
    name = f"NIGHT_ACC{n}_{key}"
    raw = config._clean(name)
    return raw if raw else str(default)


def accounts_round_robin(accounts: list[Account], jobs: list[dict], n: int) -> list[Account]:
    """Следующие n съёмок: меньше готовых роликов за день — раньше в очереди."""
    from collections import Counter
    from pathlib import Path

    if n <= 0 or not accounts:
        return []
    counts: Counter[str] = Counter()
    for job in jobs:
        if Path(str(job.get("video_path") or "")).is_file():
            counts[str(job.get("account_id") or "")] += 1
    work = Counter(counts)
    out: list[Account] = []
    for _ in range(int(n)):
        acc = min(accounts, key=lambda a: (work[a.id], a.index))
        out.append(acc)
        work[acc.id] += 1
    return out


def load_accounts() -> list[Account]:
    accounts: list[Account] = []
    for i, base in enumerate(_DEFAULTS, start=1):
        voice = voice_by_index(int(_idx(i, "VOICE_IDX", base["voice_idx"])))
        tt_var = f"NIGHT_ACC{i}_TIKTOK_ACCESS_TOKEN"
        ig_user_var = f"NIGHT_ACC{i}_IG_USER_ID"
        ig_token_var = f"NIGHT_ACC{i}_IG_ACCESS_TOKEN"
        theme = _idx(i, "THEME", base["theme"]).lower()
        if theme not in THEMES:
            theme = base["theme"]
        accounts.append(
            Account(
                index=i,
                id=_idx(i, "ID", base["id"]),
                theme=theme,
                label=_idx(i, "LABEL", base["label"]),
                voice_id=config._clean(f"NIGHT_ACC{i}_VOICE_ID") or voice["id"],
                voice_name=voice["name"],
                style=_idx(i, "STYLE", base["style"]),
                delivery=_idx(i, "DELIVERY", base["delivery"]),
                speed=_idx(i, "SPEED", base["speed"]),
                quality=_idx(i, "QUALITY", base["quality"]),
                tiktok_token_var=tt_var,
                ig_user_var=ig_user_var,
                ig_token_var=ig_token_var,
                has_tiktok=bool(config._clean(tt_var)),
                has_instagram=bool(config._clean(ig_token_var) and config._clean(ig_user_var)),
            )
        )
    return accounts


def tiktok_token(account: Account) -> str:
    return config._clean(account.tiktok_token_var)


def ig_creds(account: Account) -> tuple[str, str]:
    return config._clean(account.ig_user_var), config._clean(account.ig_token_var)
