"""Офлайн-тесты ночного пайплайна «Успех 888». Без сети и без ffmpeg."""

from __future__ import annotations

import asyncio
import inspect
import json
import shutil
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

import config
import night
import nightcal
import nightpack
import store
from joblock import JobLock
from nightcal import CalendarError
from presets import PRESETS


def _tmp_env() -> tuple[str, str]:
    tmp = tempfile.mkdtemp()
    old = config.DATA_DIR
    config.DATA_DIR = tmp
    store.reset_for_tests()
    return tmp, old


def _restore(tmp: str, old: str) -> None:
    config.DATA_DIR = old
    store.reset_for_tests()
    shutil.rmtree(tmp, ignore_errors=True)


def _sample() -> dict:
    return {
        "name": "Успех 888",
        "timezone": "Europe/Moscow",
        "owner_chat_id": 0,
        "daily_budget_runway": 800,
        "max_jobs": 2,
        "quality_default": "fast",
        "watermark": False,
        "slots": [
            {
                "id": "mon-viral-hour",
                "weekdays": ["mon"],
                "preset": "viral",
                "topic": "3 привычки, которые возвращают тебе час каждый день",
                "platforms": ["tiktok", "instagram"],
            },
            {
                "id": "mon-brand-two",
                "weekdays": ["mon"],
                "preset": "brand",
                "topic": "Почему много задач — это не продуктивность, а шум",
                "platforms": ["tiktok"],
            },
            {
                "id": "mon-meme-three",
                "weekdays": ["mon"],
                "preset": "meme",
                "topic": "Когда открыл заметки и понял, что это список желаний",
                "platforms": ["tiktok"],
            },
            {
                "id": "tue-only",
                "weekdays": ["tue"],
                "preset": "ad",
                "topic": "Как упаковать оффер в 20 секунд без крика",
                "platforms": ["instagram"],
            },
        ],
    }


def test_example_calendar_loads() -> None:
    path = Path(__file__).resolve().parent / "calendar.example.json"
    cal = nightcal.load_calendar(path)
    assert cal.name == "Успех 888"
    assert len(cal.slots) == 7
    ids = [s.id for s in cal.slots]
    assert len(ids) == len(set(ids))
    days = {d for s in cal.slots for d in s.weekdays}
    assert days == set(nightcal.WEEKDAYS)


def test_calendar_rejects_photo_and_bad_preset() -> None:
    raw = _sample()
    raw["slots"][0]["photo_path"] = "/tmp/face.jpg"
    try:
        nightcal.parse_calendar(raw)
        raise AssertionError("photo must fail")
    except CalendarError as exc:
        assert "фото" in str(exc)
    raw = _sample()
    raw["slots"][0]["preset"] = "unknown"
    try:
        nightcal.parse_calendar(raw)
        raise AssertionError("preset must fail")
    except CalendarError as exc:
        assert "пресета" in str(exc)
    raw = _sample()
    raw["slots"].append(dict(raw["slots"][0]))
    try:
        nightcal.parse_calendar(raw)
        raise AssertionError("dupes must fail")
    except CalendarError as exc:
        assert "повторяющиеся" in str(exc)


def test_weekday_selection() -> None:
    cal = nightcal.parse_calendar(_sample())
    monday = date(2026, 8, 24)  # Monday
    tuesday = date(2026, 8, 25)
    assert nightcal.weekday_key(monday) == "mon"
    mon = nightcal.slots_for_day(cal, monday)
    assert [s.id for s in mon] == ["mon-viral-hour", "mon-brand-two", "mon-meme-three"]
    tue = nightcal.slots_for_day(cal, tuesday)
    assert [s.id for s in tue] == ["tue-only"]
    moscow = nightcal.today_in_tz(
        "Europe/Moscow",
        datetime(2026, 8, 24, 23, 30, tzinfo=timezone.utc),
    )
    assert moscow == date(2026, 8, 25)


def test_budget_and_cap() -> None:
    cal = nightcal.parse_calendar(_sample())
    monday = date(2026, 8, 24)
    rows = night.plan_slots(cal, monday)
    queued = [r for r in rows if r["status"] == "queued"]
    skipped_cap = [r for r in rows if r["status"] == "skipped_cap"]
    assert len(queued) == 2
    assert len(skipped_cap) == 1
    assert skipped_cap[0]["slot_id"] == "mon-meme-three"
    tight = nightcal.parse_calendar({**_sample(), "daily_budget_runway": 10, "max_jobs": 5})
    rows = night.plan_slots(tight, monday)
    assert all(r["status"] == "skipped_budget" for r in rows)
    assert all(r["runway_credits"] > 10 for r in rows)
    already = night.plan_slots(
        cal,
        monday,
        done_ids={"mon-viral-hour", "mon-brand-two"},
        used_runway=510,
    )
    assert [r["status"] for r in already] == ["skipped_done", "skipped_done", "skipped_cap"]


def test_packager_captions() -> None:
    cal = nightcal.parse_calendar(_sample())
    slot = cal.slots[0]
    tmp = tempfile.mkdtemp()
    try:
        dest = Path(tmp) / "pack"
        meta = nightpack.write_package(
            dest,
            slot,
            day=date(2026, 8, 24),
            mode="shadow",
            runway_credits=255,
            quality="fast",
        )
        assert meta["auto_publish"] is False
        tt = (dest / "tiktok_caption.txt").read_text(encoding="utf-8")
        ig = (dest / "instagram_caption.txt").read_text(encoding="utf-8")
        assert "#успех888" in tt and "#успех888" in ig
        assert "#tiktok" in tt
        assert "#reels" in ig
        assert "привычки" in tt.lower() or "час" in tt.lower()
        assert (dest / "README.txt").is_file()
        assert json.loads((dest / "meta.json").read_text(encoding="utf-8"))["auto_publish"] is False
        tags_ig = nightpack.hashtags("brand", "личный фокус на одной задаче", "instagram")
        assert len(tags_ig) <= 5
        tags_tt = nightpack.hashtags("viral", "личный фокус на одной задаче", "tiktok")
        assert len(tags_tt) <= 6
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_shadow_does_not_render() -> None:
    tmp, old = _tmp_env()
    called = {"n": 0}

    async def boom(**kwargs):
        called["n"] += 1
        raise AssertionError("shadow must not render")

    try:
        cal = nightcal.parse_calendar(_sample())
        report = asyncio.run(
            night.run_night(
                calendar=cal,
                day=date(2026, 8, 24),
                render=False,
                notify=False,
                build_fn=boom,
                outbox=Path(tmp) / "outbox",
            )
        )
        assert called["n"] == 0
        assert report["mode"] == "shadow"
        assert report["auto_publish"] is False
        assert report["planned"] == 2
        assert report["rendered"] == 0
        plan = Path(tmp) / "outbox" / "2026-08-24" / "_plan.json"
        assert plan.is_file()
        pack = Path(tmp) / "outbox" / "2026-08-24" / "mon-viral-hour"
        assert (pack / "tiktok_caption.txt").is_file()
        assert not (pack / "final.mp4").exists()
        jobs = store.list_night_jobs("2026-08-24")
        assert {j["status"] for j in jobs} >= {"planned", "skipped_cap"}
    finally:
        _restore(tmp, old)


def test_render_idempotent_and_credits_stop() -> None:
    tmp, old = _tmp_env()

    async def fake_ok(**kwargs):
        dest = Path(kwargs["work_dir"])
        dest.mkdir(parents=True, exist_ok=True)
        video = dest / "final.mp4"
        video.write_bytes(b"mp4")
        return video, {"title": "Тест", "scenes": []}

    try:
        cal = nightcal.parse_calendar(_sample())
        first = asyncio.run(
            night.run_night(
                calendar=cal,
                day=date(2026, 8, 24),
                render=True,
                notify=False,
                build_fn=fake_ok,
                outbox=Path(tmp) / "outbox",
            )
        )
        assert first["rendered"] == 2
        assert first["failed"] == 0
        video = Path(tmp) / "outbox" / "2026-08-24" / "mon-viral-hour" / "final.mp4"
        assert video.is_file()
        packed = store.packed_night_slot_ids("2026-08-24")
        assert "mon-viral-hour" in packed

        async def should_not_run(**kwargs):
            raise AssertionError("packed slot must be skipped")

        second = asyncio.run(
            night.run_night(
                calendar=cal,
                day=date(2026, 8, 24),
                render=True,
                notify=False,
                build_fn=should_not_run,
                outbox=Path(tmp) / "outbox",
            )
        )
        assert second["rendered"] == 0
        assert any(j["status"] == "skipped_done" for j in second["jobs"])
    finally:
        _restore(tmp, old)


def test_credits_fail_closed() -> None:
    from pipeline import credits_error

    tmp, old = _tmp_env()
    seen: list[str] = []

    async def fail_first(**kwargs):
        idea = str(kwargs.get("idea") or "")
        seen.append(idea)
        if len(seen) == 1:
            raise credits_error("not enough credits")
        dest = Path(kwargs["work_dir"])
        dest.mkdir(parents=True, exist_ok=True)
        video = dest / "final.mp4"
        video.write_bytes(b"mp4")
        return video, {"title": "X", "scenes": []}

    try:
        cal = nightcal.parse_calendar(_sample())
        report = asyncio.run(
            night.run_night(
                calendar=cal,
                day=date(2026, 8, 24),
                render=True,
                notify=False,
                build_fn=fail_first,
                outbox=Path(tmp) / "outbox",
            )
        )
        assert report["failed"] == 1
        assert any(j["status"] == "skipped_budget" for j in report["jobs"] if j["slot_id"] == "mon-brand-two")
        assert len(seen) == 1
    finally:
        _restore(tmp, old)


def test_lock_blocks_second_process() -> None:
    tmp, old = _tmp_env()
    try:
        path = Path(tmp) / "videobot.lock"
        a = JobLock(path)
        b = JobLock(path)
        assert a.acquire() is True
        assert b.acquire() is False
        a.release()
        assert b.acquire() is True
        b.release()
    finally:
        _restore(tmp, old)


def test_night_skips_when_lock_held() -> None:
    tmp, old = _tmp_env()
    try:
        path = Path(tmp) / "held.lock"
        held = JobLock(path)
        assert held.acquire()
        other = JobLock(path)
        cal = nightcal.parse_calendar(_sample())

        async def boom(**kwargs):
            raise AssertionError("locked run must not render")

        report = asyncio.run(
            night.run_night(
                calendar=cal,
                day=date(2026, 8, 24),
                render=True,
                notify=False,
                build_fn=boom,
                lock=other,
                outbox=Path(tmp) / "outbox",
            )
        )
        assert any(j["status"] == "skipped_lock" for j in report["jobs"])
        assert all(j["status"] != "packed" for j in report["jobs"])
        held.release()
    finally:
        _restore(tmp, old)


def test_no_social_publish_code() -> None:
    root = Path(__file__).resolve().parent
    text = (root / "night.py").read_text(encoding="utf-8")
    text += (root / "nightpack.py").read_text(encoding="utf-8")
    text += (root / "night_run.py").read_text(encoding="utf-8")
    banned = (
        "graph.facebook.com",
        "open.tiktokapis.com",
        "api.tiktok.com",
        "instagram.com/oauth",
        "content/upload",
    )
    for token in banned:
        assert token not in text, token
    assert "auto_publish" in text


def test_bot_uses_joblock_and_night_command() -> None:
    from bot import cmd_night, _run_job, HOW_IT_WORKS

    src = inspect.getsource(_run_job)
    assert "JobLock" in src
    assert "file_lock" in src
    night_src = inspect.getsource(cmd_night)
    assert "status_text" in night_src
    assert "Успех 888" in HOW_IT_WORKS
    main_src = inspect.getsource(__import__("bot").main)
    assert 'Command("night")' in main_src


def test_presets_still_intact() -> None:
    assert set(PRESETS) >= {"viral", "ad", "meme", "brand", "cine"}


if __name__ == "__main__":
    test_example_calendar_loads()
    test_calendar_rejects_photo_and_bad_preset()
    test_weekday_selection()
    test_budget_and_cap()
    test_packager_captions()
    test_shadow_does_not_render()
    test_render_idempotent_and_credits_stop()
    test_credits_fail_closed()
    test_lock_blocks_second_process()
    test_night_skips_when_lock_held()
    test_no_social_publish_code()
    test_bot_uses_joblock_and_night_command()
    test_presets_still_intact()
    print("ok")
