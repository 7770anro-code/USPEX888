"""Input validation for custom numeric menus."""
from __future__ import annotations

from typing import Any, Mapping, Tuple


def validate_mode_settings(d: Mapping[str, Any], *, max_lev: float = 100.0) -> Tuple[bool, str]:
    try:
        margin = float(d.get("margin", 0))
        lev = float(d.get("lev", 0))
        tp1 = float(d.get("tp1", 0))
        tp2 = float(d.get("tp2", 0))
        sl = float(d.get("sl", 0))
    except Exception:
        return False, "Параметры должны быть числами."
    if min(margin, lev, tp1, tp2, sl) <= 0:
        return False, "Все значения должны быть больше нуля."
    if lev > max_lev:
        return False, f"Плечо ограничено {max_lev:g}x."
    if tp1 >= tp2:
        return False, "TP1 должен быть меньше TP2."
    if tp2 / sl < 1.20:
        return False, "TP2/Stop минимум 1.20×."
    if tp1 > tp2 * 0.85:
        return False, "TP1 слишком близко к TP2 (нужен runner)."
    return True, ""


def validate_positive_number(raw: str, *, lo: float, hi: float, name: str = "value") -> Tuple[bool, float, str]:
    try:
        v = float(str(raw).replace(",", ".").strip())
    except Exception:
        return False, 0.0, f"{name}: нужно число"
    if not (lo < v <= hi):
        return False, 0.0, f"{name}: допустимо ({lo}; {hi}]"
    return True, v, ""
