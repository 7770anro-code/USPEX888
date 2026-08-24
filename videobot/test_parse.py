"""Офлайн-проверка разбора сценария и unwrap Grok-ключа. Без сети."""

from bot import CONSENT_REQUIRED_MSG, photo_start_blocked, tiktok_upload_filename
from config import unwrap_xai_api_key
from pipeline import (
    CLIP_SPEECH_BUDGET_SEC,
    MAX_SCENES,
    PipelineError,
    RUNWAY_CREDITS_MSG,
    RUNWAY_PERSON_MSG,
    RUNWAY_PROMPT_MAX,
    SCRIPT_SYSTEM,
    SCRIPT_TOO_LONG_MSG,
    compose_runway_prompt,
    enforce_speech_budget,
    estimate_speech_sec,
    fallback_split_script,
    format_script,
    is_runway_credits_fail,
    is_runway_person_moderation,
    is_runway_safety_fail,
    is_runway_user_facing,
    parse_script,
    pick_clip_duration,
    ratio_wh,
    runway_content_moderation,
    runway_duration,
    runway_fail_error,
    runway_poll_delay,
    runway_prompt_text,
    scene_durations,
    script_too_long_for_custom,
    split_text_to_speech_budget,
    target_scene_count,
    text_to_image_payload,
    wrap_caption,
)
from voices import VOICES, catalog_for, voice_by_index


def test_plain_json() -> None:
    raw = '{"title": "Дождь", "scenes": [{"narration": "Капли стучат по стеклу", "visual_prompt": "Rain on a window, night city bokeh"}]}'
    data = parse_script(raw)
    assert data["title"] == "Дождь"
    assert len(data["scenes"]) == 1
    assert "Rain" in data["scenes"][0]["visual_prompt"]
    assert data["continuity"]


def test_fenced_and_extra() -> None:
    raw = """вот:\n```json\n{"title": "X", "scenes": [
      {"narration": "один два три", "visual_prompt": "wide shot of a forest"},
      {"narration": "четыре пять", "visual_prompt": "close-up of moss"}
    ]}\n```\nконец"""
    data = parse_script(raw)
    assert len(data["scenes"]) == 2
    assert scene_durations(2) == [10, 10]
    assert scene_durations(3) == [10, 10, 10]


def test_visual_prompt_alias() -> None:
    raw = '{"scenes": [{"narration": "hello there friend", "visualPrompt": "A red bicycle in the sun"}]}'
    data = parse_script(raw)
    assert data["scenes"][0]["visual_prompt"].startswith("A red")


def test_unwrap_wrapped_key() -> None:
    inner = "xai-" + ("a" * 80)
    key, err = unwrap_xai_api_key("XAI_API_KEY=" + inner)
    assert err == ""
    assert key == inner
    assert len(key) == 84
    assert key.startswith("xai-")


def test_unwrap_plain_key() -> None:
    inner = "xai-" + ("b" * 80)
    key, err = unwrap_xai_api_key(inner)
    assert err == ""
    assert key == inner


def test_unwrap_rejects_bad_shape() -> None:
    key, err = unwrap_xai_api_key("XAI_API_KEY=not-a-real-key")
    assert key == ""
    assert "длина" in err
    assert "xai-" in err


def test_unwrap_empty() -> None:
    key, err = unwrap_xai_api_key("   ")
    assert key == ""
    assert "пустой" in err


def test_runway_duration_clamp() -> None:
    assert runway_duration(5) == 5
    assert runway_duration(10) == 10
    assert runway_duration(1) == 2
    assert runway_duration(99) == 10


def test_runway_prompt_limit() -> None:
    text = runway_prompt_text("  hello   world  " + ("x" * 2000))
    assert text.startswith("hello world")
    assert len(text) == 1000


def test_runway_poll_delay_min() -> None:
    for _ in range(8):
        delay = runway_poll_delay()
        assert delay >= 5.0
        assert delay < 7.0


def test_pick_clip_duration() -> None:
    assert pick_clip_duration(3.0) == 5
    assert pick_clip_duration(8.0) == 10


def test_ratio_wh() -> None:
    assert ratio_wh("720:1280") == (720, 1280)
    assert ratio_wh("1280:720") == (1280, 720)


def test_wrap_and_format() -> None:
    wrapped = wrap_caption("один два три четыре пять шесть семь восемь девять десять", 12)
    assert "\n" in wrapped
    text = format_script(
        {
            "title": "Тест",
            "continuity": "same woman, red coat, rainy street",
            "scenes": [{"narration": "Привет мир", "visual_prompt": "x"}],
        }
    )
    assert "Тест" in text
    assert "Привет мир" in text
    assert "red coat" in text


def test_compose_runway_prompt_lock() -> None:
    lock = "red coat, rainy street, cinematic grain"
    a = compose_runway_prompt(lock, "slow subtle push-in, subtle head turn")
    b = compose_runway_prompt(lock, "gentle pan, camera stays level")
    assert lock in a and lock in b
    assert "LOCKED LOOK" in a
    assert "clothes, location" in a
    assert "same person" not in a
    assert len(a) <= RUNWAY_PROMPT_MAX
    assert "push-in" in a
    assert "gentle pan" in b


def test_fallback_and_scene_count() -> None:
    words = " ".join(f"слово{i}" for i in range(80))
    data = fallback_split_script(words, 5)
    assert 4 <= len(data["scenes"]) <= 6
    assert data["continuity"]
    assert target_scene_count("коротко") == 4
    assert target_scene_count(" ".join(["w"] * 120)) == 6


def test_voices_catalog() -> None:
    assert len(VOICES) == 21
    ids = [v["id"] for v in VOICES]
    assert len(set(ids)) == 21
    assert voice_by_index(1)["name"] == "Сара"
    extra = [{"id": "custom-1", "name": "Мой", "tag": "клон"}]
    assert catalog_for(extra)[0]["id"] == "custom-1"
    assert voice_by_index(0, extra)["name"] == "Мой"
    assert voice_by_index(1, extra)["name"] == "Ария"
    assert voice_by_index(99)["id"] == VOICES[1]["id"]


def test_runway_moderation_person() -> None:
    assert is_runway_safety_fail("SAFETY.INPUT.IMAGE", "blocked")
    assert is_runway_person_moderation("SAFETY.INPUT.IMAGE", "face rejected")
    assert is_runway_person_moderation("SAFETY.OUTPUT.VIDEO", "public_figure")
    assert not is_runway_person_moderation("INTERNAL", "timeout")
    err = runway_fail_error("SAFETY.INPUT.IMAGE", "FAILED: likeness of a person")
    assert err.code == "moderation_person"
    assert err.user_message == RUNWAY_PERSON_MSG
    assert "политика по реальным людям" in err.user_message
    assert "текстовый режим" in err.user_message
    policy = runway_fail_error(
        "SAFETY.INPUT.IMAGE",
        "use of an image, video or audio of another person without their permission",
    )
    assert policy.code == "moderation_person"
    text_err = runway_fail_error("SAFETY.INPUT.TEXT", "prompt blocked")
    assert text_err.code == "moderation"
    # Реальный I2V: Runway часто ставит TEXT даже на фото. С картинкой — текст про людей.
    photo_text = runway_fail_error(
        "SAFETY.INPUT.TEXT",
        "The input was flagged by our content moderation system.",
        used_image=True,
    )
    assert photo_text.user_message == RUNWAY_PERSON_MSG
    vague = runway_fail_error(
        "",
        "FAILED: the input image was flagged by our content moderation system.",
        used_image=True,
    )
    assert vague.user_message == RUNWAY_PERSON_MSG
    assert runway_content_moderation()["publicFigureThreshold"] == "auto"


def test_runway_credits_message() -> None:
    live = '{"error":"You do not have enough credits to run this task.","docUrl":"https://docs.dev.runwayml.com/api"}'
    detail = f"HTTP 400: {live}"
    assert is_runway_credits_fail(detail)
    err = runway_fail_error("", detail)
    assert err.code == "credits"
    assert err.user_message == RUNWAY_CREDITS_MSG
    assert "закончились кредиты" in err.user_message
    assert is_runway_user_facing(err)
    generic = PipelineError(
        "🎥 Не получился клип 1 из 6. Я остановился, чтобы не склеить кривой ролик. "
        "Попробуй ещё раз или другое фото.",
        detail,
    )
    assert is_runway_credits_fail(generic.detail)
    assert is_runway_user_facing(generic)
    assert not is_runway_credits_fail("HTTP 400: Validation of body failed")


def test_text_to_image_payload_no_refs() -> None:
    payload = text_to_image_payload("woman in red coat, still", "720:1280")
    assert payload["model"] == "gen4_image"
    assert "referenceImages" not in payload
    assert payload["promptText"]
    assert payload["ratio"] == "1080:1920"
    assert payload["contentModeration"]["publicFigureThreshold"] == "auto"
    assert payload["model"] != "gen4_image_turbo"


def test_consent_gate() -> None:
    assert photo_start_blocked("file-1", False) == CONSENT_REQUIRED_MSG
    assert photo_start_blocked("file-1", True) == ""
    assert photo_start_blocked(None, False) == ""
    assert photo_start_blocked(None, True) == ""


def test_speech_budget_custom() -> None:
    assert estimate_speech_sec("один два три") < CLIP_SPEECH_BUDGET_SEC
    parts = split_text_to_speech_budget(" ".join(f"слово{i}" for i in range(80)), CLIP_SPEECH_BUDGET_SEC)
    assert len(parts) >= 2
    for part in parts:
        assert estimate_speech_sec(part) <= CLIP_SPEECH_BUDGET_SEC + 1.5
    long_text = " ".join(f"слово{i}" for i in range(400))
    assert script_too_long_for_custom(long_text)
    script = {
        "title": "x",
        "continuity": "lock",
        "scenes": [{"narration": long_text, "visual_prompt": "cam"}],
    }
    try:
        enforce_speech_budget(script, user_script=True)
        raise AssertionError("expected too-long")
    except PipelineError as exc:
        assert exc.code == "speech_too_long"
        assert SCRIPT_TOO_LONG_MSG in str(exc.user_message)
    ok = enforce_speech_budget(
        {
            "title": "x",
            "continuity": "lock",
            "scenes": [{"narration": " ".join(f"w{i}" for i in range(30)), "visual_prompt": "cam"}],
        },
        user_script=True,
    )
    assert 1 <= len(ok["scenes"]) <= MAX_SCENES


def test_presets_and_cost() -> None:
    from presets import (
        PRESETS,
        apply_preset,
        default_job,
        estimate_cost,
        voice_settings_payload,
    )

    assert len(PRESETS) == 5
    job = apply_preset(default_job(mode="preset"), "viral")
    assert job["n_scenes"] == 5
    assert "Подпишись" in job["brief"]
    vs = voice_settings_payload("energy", "xfst")
    assert vs["stability"] == 0.30
    assert vs["speed"] == 1.2
    assert "use_speaker_boost" in vs
    est = estimate_cost(n_scenes=5, clip_sec=10, quality="optimal", text="привет мир", need_still=True)
    assert est["runway"] == 5 * 10 * 12 + 5
    assert est["eleven_chars"] == len("привет мир")
    lock = "red coat rainy street"
    prompt = compose_runway_prompt(lock, "subtle head turn", "slow subtle push-in", "minimal body movement")
    assert lock in prompt
    assert "push-in" in prompt
    assert "minimal body movement" in prompt
    assert "energetic" not in prompt
    assert len(prompt) <= RUNWAY_PROMPT_MAX


def test_progress_weights() -> None:
    from presets import StageProgress

    t = StageProgress(5)
    assert t.percent() == 0
    t.script_done = True
    assert t.percent() == 12
    t.still_done = True
    assert t.percent() == 20
    t.tts_done = 5
    t.video_done = 3
    assert t.percent() == 20 + 20 + int(round(50 * 3 / 5))
    t.video_done = 5
    t.mux_done = True
    assert t.percent() == 100
    assert "100%" in t.render("Готово")


def test_camera_motion_soft() -> None:
    from presets import CAMERA, MOTION

    blob = " ".join(v["prompt"] for v in list(CAMERA.values()) + list(MOTION.values())).lower()
    for banned in ("spin", "dramatic", "energetic", "extreme close-up"):
        assert banned not in blob
    assert CAMERA["lock"]["prompt"] == "camera holds static"
    assert "subtle head turn" in MOTION["nat"]["prompt"]
    assert MOTION["min"]["prompt"] == "minimal body movement"


def test_tiktok_upload_filename() -> None:
    assert tiktok_upload_filename("Мой ролик") == "Мой_ролик_tiktok.mp4"
    assert tiktok_upload_filename("  ") == "video_tiktok.mp4"
    assert tiktok_upload_filename("Hello / World??").endswith("_tiktok.mp4")
    assert "/" not in tiktok_upload_filename("a/b")


def test_last_frame_chains_with_user_photo() -> None:
    import inspect

    from pipeline import build_video

    src = inspect.getsource(build_video)
    assert "last_frame_data_uri" in src
    assert "not user_supplied_photo" not in src
    assert "No face, age, hair" in SCRIPT_SYSTEM or "без деталей лица" in SCRIPT_SYSTEM.lower() or "No face" in SCRIPT_SYSTEM


def test_wave2_thin_api() -> None:
    from bot import clone_consent_kb, consent_kb, main_menu, result_kb
    from wave2 import (
        CLONE_CONSENT_MSG,
        act_two_payload,
        extend_video_payload,
        image_upscale_payload,
        video_upscale_payload,
        voice_design_payload,
    )

    labels = [btn.text for row in main_menu().inline_keyboard for btn in row]
    assert labels[0] == "⚡️ Видео за 1 клик"
    assert labels[1] == "🎬 Своё фото + текст + голос"
    assert "🧟 Оживить фото" in labels
    assert "🎙 Клонировать мой голос" in labels
    assert "🗑 Удалить мой голос" in labels
    assert "🎯 Пресеты" in labels
    assert "✂️ Нарезка и монтаж" in labels
    payload = voice_design_payload("спокойный низкий мужской голос, тёплый, спокойный темп")
    assert payload["auto_generate_text"] is True
    assert payload["model_id"] == "eleven_ttv_v3"
    img = image_upscale_payload("data:image/jpeg;base64,xx")
    assert img["model"] == "magnific_precision_upscaler_v2"
    vid = video_upscale_payload("runway://clip")
    assert vid["model"] == "magnific_video_upscaler_creative"
    assert vid["resolution"] == "2k"
    act = act_two_payload("data:image/jpeg;base64,a", "runway://perf")
    assert act["model"] == "act_two"
    assert act["character"]["type"] == "image"
    ext = extend_video_payload("runway://v", "")
    assert ext["mode"] == "extend"
    assert ext["promptText"]
    clone_btns = [b.callback_data for row in clone_consent_kb().inline_keyboard for b in row]
    photo_btns = [b.callback_data for row in consent_kb().inline_keyboard for b in row]
    assert "w2c:yes" in clone_btns
    assert "consent:yes" in photo_btns
    assert "consent:yes" not in clone_btns
    assert "Разрешаю клонировать голос" in clone_consent_kb().inline_keyboard[0][0].text
    assert "моё фото" in consent_kb().inline_keyboard[0][0].text.lower()
    assert "отдельно от согласия на фото" in CLONE_CONSENT_MSG.lower()
    result_btns = [b.callback_data for row in result_kb().inline_keyboard for b in row]
    assert "upscale:last" in result_btns


def test_store_sqlite_voices_and_prefs() -> None:
    import json
    import shutil
    import tempfile
    from pathlib import Path

    import config
    import store

    tmp = tempfile.mkdtemp()
    old = config.DATA_DIR
    config.DATA_DIR = tmp
    store.reset_for_tests()
    try:
        legacy = Path(tmp) / "user_42.json"
        legacy.write_text(
            json.dumps({"voices": [{"id": "old-id", "name": "Старый", "tag": "клон", "kind": "clone"}]}),
            encoding="utf-8",
        )
        migrated = store.load_user_voices(42)
        assert migrated[0]["id"] == "old-id"
        store.set_cloned_voice(7, "abc123", "Мой голос")
        cloned = store.get_cloned_voice(7)
        assert cloned is not None
        assert cloned["id"] == "abc123"
        store.set_cloned_voice(7, "new-id", "Мой голос")
        assert store.get_cloned_voice(7)["id"] == "new-id"
        assert len([v for v in store.load_user_voices(7) if v["kind"] == "clone"]) == 1
        deleted = store.delete_cloned_voice(7)
        assert deleted == "new-id"
        assert store.get_cloned_voice(7) is None
        store.set_watermark(7, True)
        assert store.get_watermark(7) is True
        store.set_watermark(7, False)
        assert store.get_watermark(7) is False
        src = Path(tmp) / "clip.mp4"
        src.write_bytes(b"mp4-bytes")
        keep = store.save_last_video(7, src, "Ролик")
        assert keep.is_file()
        assert store.get_last_video(7) == keep
        assert store.get_last_title(7) == "Ролик"
        store.save_user_voice(7, {"id": "design-1", "name": "Дизайн", "tag": "по описанию", "kind": "design"})
        ids = store.clear_user_voices(7)
        assert "design-1" in ids
        assert store.load_user_voices(7) == []
    finally:
        config.DATA_DIR = old
        store.reset_for_tests()
        shutil.rmtree(tmp, ignore_errors=True)


def test_act_two_uses_photo_consent() -> None:
    import inspect

    from bot import PHOTO_CONSENT_PROMPT, on_act_photo, on_consent, on_w2_act_video

    assert PHOTO_CONSENT_PROMPT
    assert "consent_kb" in inspect.getsource(on_act_photo)
    assert "PHOTO_CONSENT_PROMPT" in inspect.getsource(on_act_photo)
    consent_src = inspect.getsource(on_consent)
    assert 'job.get("mode") == "act_two"' in consent_src
    video_src = inspect.getsource(on_w2_act_video)
    assert "photo_start_blocked" in video_src
    assert "CONSENT_REQUIRED_MSG" in video_src or "blocked" in video_src


def test_preset_topic_goes_to_cost() -> None:
    import inspect

    from bot import confirm_kb, cost_text, on_preset_topic
    from presets import PRESETS

    src = inspect.getsource(on_preset_topic)
    assert "Flow.confirm" in src
    assert "cost_text" in src
    assert "Flow.tune" not in src
    labels = {p["label"] for p in PRESETS.values()}
    assert {"Вирусный TikTok", "Реклама товара", "Мем", "Личный бренд"} <= labels
    kb = [b.text for row in confirm_kb().inline_keyboard for b in row]
    assert "✅ Создать" in kb
    assert "❌ Отмена" in kb
    text = cost_text({"n_scenes": 5, "quality": "optimal", "idea": "тема ролика про кофе"})
    assert "кредит" in text.lower()
    assert "Создать" in text or "кредит" in text.lower()


def test_watermark_ffmpeg_overlay() -> None:
    from pipeline import watermark_drawtext

    vf = watermark_drawtext("VideoBot", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    assert "drawtext" in vf
    assert "VideoBot" in vf
    import inspect

    from pipeline import apply_watermark, build_video

    assert "watermark_drawtext" in inspect.getsource(apply_watermark)
    assert "if watermark:" in inspect.getsource(build_video)


def test_clone_posts_voices_add() -> None:
    import inspect

    from wave2 import ELEVEN_IVC_URL, clone_voice

    assert ELEVEN_IVC_URL.endswith("/v1/voices/add")
    src = inspect.getsource(clone_voice)
    assert "ELEVEN_IVC_URL" in src


def test_night_policy_defaults() -> None:
    import inspect
    import shutil
    import tempfile
    from datetime import datetime, timedelta, timezone
    from pathlib import Path

    import config
    import store
    from night_ideas import DENY_RE, assign_to_accounts, parse_ideas
    from night_post import PostResult, classify_job_status, is_manual_error, is_retryable_http, post_tiktok
    from night_store import (
        MANUAL_REVIEW,
        POSTING,
        PUBLISH_UNKNOWN,
        VIDEO_READY,
        consecutive_moderation,
        create_job,
        ready_video_count,
        recover_stale,
        remaining_daily_slots,
        require_confirm,
        set_publish_mode,
        update_job,
        video_belongs_to_account,
    )

    assert is_retryable_http(429)
    assert is_retryable_http(503)
    assert not is_retryable_http(400)
    assert not is_retryable_http(401)
    assert is_manual_error("oauth scope unaudited")
    assert is_manual_error("App Review permission denied")
    assert is_manual_error("moderation rejected")
    assert is_manual_error("unsupported format")
    assert not is_manual_error("connection reset")
    assert classify_job_status([PostResult("tiktok", False, mode="publish_unknown", error="timeout")]) == PUBLISH_UNKNOWN
    assert classify_job_status([PostResult("tiktok", False, error="oauth forbidden", blocker=True)]) == MANUAL_REVIEW

    src = inspect.getsource(post_tiktok)
    assert "is_aigc" in src
    assert "TIKTOK_INBOX_INIT" in src
    assert "existing_publish_id" in src
    assert "publish_unknown" in src

    from night_post import post_instagram

    ig_src = inspect.getsource(post_instagram)
    assert "existing_container_id" in ig_src
    assert "PUBLISH_UNKNOWN" in ig_src

    denied = parse_ideas(
        '{"ideas":[{"kind":"motivational","title":"Про Путина","plot":"Политик говорит речь на площади долго.",'
        '"caption":"#news","score":9},{"kind":"absurd","title":"Синий чайник","plot":"Чайник спорит с будильником утром.",'
        '"caption":"#абсурд","score":8}]}'
    )
    assert DENY_RE.search("знаменитость и celebrity")
    assert all("путин" not in i["title"].lower() for i in denied)
    assert any(i["title"] == "Синий чайник" for i in denied)

    class _Acc:
        def __init__(self, aid: str, theme: str) -> None:
            self.id = aid
            self.theme = theme

    ideas = [
        {"kind": "motivational", "tokens": ["таймер", "утро"], "hashtags": ["фокус"], "caption": "#фокус", "title": "A"},
        {"kind": "absurd", "tokens": ["чайник", "спор"], "hashtags": ["абсурд"], "caption": "#абсурд", "title": "B"},
        {"kind": "motivational", "tokens": ["блокнот", "план"], "hashtags": ["план"], "caption": "#план", "title": "C"},
    ]
    picked = assign_to_accounts(
        ideas,
        [_Acc("motiv", "motivational"), _Acc("absurd", "absurd"), _Acc("brand", "mixed")],
        limit=3,
    )
    assert len(picked) == 3
    assert len({id(idea) for _, idea in picked}) == 3
    assert {acc.id for acc, _ in picked} == {"motiv", "absurd", "brand"}

    tmp = tempfile.mkdtemp()
    old = config.DATA_DIR
    old_out = config.NIGHT_OUTBOX
    config.DATA_DIR = tmp
    config.NIGHT_OUTBOX = str(Path(tmp) / "outbox")
    store.reset_for_tests()
    try:
        assert require_confirm() is True
        set_publish_mode(confirm=False, autopost=True)
        from night_store import autopost_enabled

        assert require_confirm() is False
        assert autopost_enabled() is True
        set_publish_mode(confirm=True, autopost=False)
        assert require_confirm() is True
        day = "2026-08-23"
        acc_dir = Path(config.NIGHT_OUTBOX) / day / "motiv"
        acc_dir.mkdir(parents=True)
        video = acc_dir / "final.mp4"
        video.write_bytes(b"mp4")
        assert video_belongs_to_account(str(video), "motiv", day)
        assert not video_belongs_to_account(str(video), "absurd", day)
        jid = create_job(
            {
                "run_date": day,
                "account_id": "motiv",
                "kind": "motivational",
                "title": "x",
                "status": POSTING,
            }
        )
        stale = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec="seconds")
        update_job(
            jid,
            video_path=str(video),
            tiktok_publish_id="pub-1",
            locked_at=stale,
            worker_id="old",
        )
        # recover_stale смотрит locked_at в SQL; update_job пишет locked_at
        n = recover_stale(minutes=40)
        from night_store import get_job

        job = get_job(jid)
        assert job is not None
        assert job["status"] == PUBLISH_UNKNOWN
        assert n >= 1
        update_job(jid, status=MANUAL_REVIEW, last_error="moderation")
        assert consecutive_moderation(day) >= 1
        assert ready_video_count(day) >= 1
        assert remaining_daily_slots(day, daily_limit=3) <= 2
    finally:
        config.DATA_DIR = old
        config.NIGHT_OUTBOX = old_out
        store.reset_for_tests()
        shutil.rmtree(tmp, ignore_errors=True)

    from night_accounts import accounts_round_robin
    from types import SimpleNamespace

    accs = [
        SimpleNamespace(id="motiv", index=1),
        SimpleNamespace(id="absurd", index=2),
        SimpleNamespace(id="brand", index=3),
    ]
    order = [a.id for a in accounts_round_robin(accs, [], 4)]
    assert order == ["motiv", "absurd", "brand", "motiv"]

    from bot import cmd_night_mode, main, night_confirm_kb
    from night_loop import auto_pipeline_loop
    from night_runner import _render_job, run_night

    kb = night_confirm_kb([11, 12])
    data = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "night:ok:11" in data
    assert "night:okall" in data
    assert "night_mode" in inspect.getsource(cmd_night_mode)
    assert "start_auto_pipeline" in inspect.getsource(main)
    assert "NIGHT_INTERVAL_MINUTES" in inspect.getsource(auto_pipeline_loop)
    assert "busy" in inspect.getsource(run_night)
    assert "{job['id']}.mp4" in inspect.getsource(_render_job)
    assert 15 <= config.NIGHT_INTERVAL_MINUTES <= 24 * 60
    assert config.NIGHT_BATCH_PER_TICK >= 1
    assert 1 <= config.VIDEOS_PER_NIGHT <= 48


def test_legacy_night_schema_migrates() -> None:
    """Прототипные night_jobs/night_runs без account_id/status не должны ломать ensure()."""
    import shutil
    import sqlite3
    import tempfile
    from pathlib import Path

    import config
    import store
    from night_store import create_job, ensure, jobs_for_date, save_run

    tmp = tempfile.mkdtemp()
    old = config.DATA_DIR
    config.DATA_DIR = tmp
    store.reset_for_tests()
    try:
        db = Path(tmp) / "videobot.sqlite3"
        with sqlite3.connect(str(db)) as conn:
            conn.executescript(
                """
                CREATE TABLE night_jobs (
                    run_date TEXT NOT NULL,
                    slot_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    preset TEXT NOT NULL DEFAULT '',
                    topic TEXT NOT NULL DEFAULT '',
                    platforms TEXT NOT NULL DEFAULT '',
                    quality TEXT NOT NULL DEFAULT '',
                    runway_credits INTEGER NOT NULL DEFAULT 0,
                    outbox TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE night_runs (
                    run_id TEXT PRIMARY KEY,
                    run_date TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    planned INTEGER NOT NULL DEFAULT 0,
                    rendered INTEGER NOT NULL DEFAULT 0,
                    failed INTEGER NOT NULL DEFAULT 0,
                    skipped INTEGER NOT NULL DEFAULT 0,
                    runway_used INTEGER NOT NULL DEFAULT 0,
                    report TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                INSERT INTO night_jobs VALUES (
                    '2026-08-24','mon-viral-hour','planned','viral','old',
                    'tiktok','fast',255,'/tmp/x','','2026-08-23T18:52:47+00:00',
                    '2026-08-23T18:52:47+00:00'
                );
                """
            )
            conn.commit()
        ensure()
        jid = create_job(
            {
                "run_date": "2026-08-24",
                "account_id": "motiv",
                "kind": "motivational",
                "title": "after-migrate",
            }
        )
        assert jid >= 1
        rows = jobs_for_date("2026-08-24")
        assert any(r.get("title") == "after-migrate" for r in rows)
        save_run("abc123", "2026-08-24", "done", "ok")
        with sqlite3.connect(str(db)) as conn:
            job_tables = [
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'night_jobs%'"
                )
            ]
            assert "night_jobs" in job_tables
            assert any(n.startswith("night_jobs_legacy_") for n in job_tables)
            cols = {r[1] for r in conn.execute("PRAGMA table_info(night_jobs)")}
            assert "account_id" in cols and "id" in cols
            run_cols = {r[1] for r in conn.execute("PRAGMA table_info(night_runs)")}
            assert "status" in run_cols
    finally:
        config.DATA_DIR = old
        store.reset_for_tests()
        shutil.rmtree(tmp, ignore_errors=True)


def test_edit_timecodes_and_limits() -> None:
    import inspect
    import shutil

    from pipeline import PipelineError
    from edit import (
        MAX_CLIPS,
        MAX_INPUT_BYTES,
        MAX_OUTPUT_BYTES,
        check_incoming,
        concat_videos,
        cut_video,
        parse_clock,
        parse_timecodes,
        render_clips,
    )

    assert parse_clock("0:05") == 5.0
    assert parse_clock("1:02") == 62.0
    assert parse_clock("1:02:03") == 3723.0
    assert parse_timecodes("0:05-0:18") == (5.0, 18.0)
    assert parse_timecodes("12 40") == (12.0, 40.0)
    assert parse_timecodes("с 1:00 по 1:12") == (60.0, 72.0)
    try:
        parse_timecodes("20-5")
        raise AssertionError("expected inverted range")
    except PipelineError:
        pass
    check_incoming(size=1000, duration=10)
    try:
        check_incoming(size=MAX_INPUT_BYTES + 1, duration=None)
        raise AssertionError("expected size reject")
    except PipelineError:
        pass
    assert MAX_OUTPUT_BYTES < 50 * 1024 * 1024
    assert MAX_CLIPS == 8
    blob = inspect.getsource(__import__("edit", fromlist=["cut_video"]))
    assert "grok.com" not in blob.lower()
    assert "chatgpt.com" not in blob.lower()
    assert "playwright" not in blob.lower()
    assert "from wave2" not in blob
    assert "XAI_CHAT_URL" in blob
    from bot import edit_hub_kb, main

    data = [b.callback_data for row in edit_hub_kb().inline_keyboard for b in row]
    assert "edit:cut" in data
    assert "edit:auto" in data
    assert "Command(\"edit\")" in inspect.getsource(main)

    from edit import heuristic_plan, parse_edit_plan, parse_target_range, validate_clips

    assert parse_target_range("динамичный ролик 30-45 сек") == (30.0, 45.0)
    plan = parse_edit_plan('{"clips":[{"start":1,"end":4},{"start":10,"end":16}]}')
    assert plan[0]["start"] == 1
    fenced = parse_edit_plan('```json\n{"clips":[{"start":0,"end":2}]}\n```')
    assert fenced[0]["end"] == 2
    ok = validate_clips([{"start": -1, "end": 5}, {"start": 90, "end": 92}], 20)
    assert ok[0] == (0.0, 5.0)
    assert all(e <= 20 for _, e in ok)
    bad = validate_clips([{"start": 50, "end": 60}], 10)
    assert bad == []
    swapped = validate_clips([{"start": 8, "end": 3}], 10)
    assert swapped and swapped[0][0] < swapped[0][1]
    hz = heuristic_plan(120, "яркие моменты 30-45 сек")
    assert 2 <= len(hz) <= 8
    total = sum(e - s for s, e in hz)
    assert 25 <= total <= 50
    short = heuristic_plan(20, "весь ролик")
    assert short and short[0][0] == 0.0

    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        import asyncio
        import tempfile
        from pathlib import Path

        async def _roundtrip() -> None:
            tmp = Path(tempfile.mkdtemp())
            try:
                a = tmp / "a.mp4"
                b = tmp / "b.mp4"
                for path, color in ((a, "red"), (b, "blue")):
                    proc = await asyncio.create_subprocess_exec(
                        "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={color}:s=320x240:d=2",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                        "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                        str(path),
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    await proc.communicate()
                    assert proc.returncode == 0
                cut = tmp / "cut.mp4"
                await cut_video(a, cut, 0.3, 1.2)
                assert cut.is_file() and cut.stat().st_size > 1000
                out = tmp / "cat.mp4"
                await concat_videos([a, b], out)
                assert out.is_file() and out.stat().st_size > 1000
                auto = tmp / "auto.mp4"
                await render_clips(a, auto, [(0.2, 0.8), (1.0, 1.6)])
                assert auto.is_file() and auto.stat().st_size > 1000
            finally:
                shutil.rmtree(tmp, ignore_errors=True)

        asyncio.run(_roundtrip())


def test_upscale_result_uses_video_upscale() -> None:
    import inspect

    from bot import on_upscale_last
    from wave2 import video_upscale_payload

    src = inspect.getsource(on_upscale_last)
    assert "/v1/video_upscale" in src
    assert "video_upscale_payload" in src
    assert "_send_video" in src
    payload = video_upscale_payload("runway://final")
    assert payload["model"] == "magnific_video_upscaler_creative"


if __name__ == "__main__":
    test_plain_json()
    test_fenced_and_extra()
    test_visual_prompt_alias()
    test_unwrap_wrapped_key()
    test_unwrap_plain_key()
    test_unwrap_rejects_bad_shape()
    test_unwrap_empty()
    test_runway_duration_clamp()
    test_runway_prompt_limit()
    test_runway_poll_delay_min()
    test_pick_clip_duration()
    test_ratio_wh()
    test_wrap_and_format()
    test_compose_runway_prompt_lock()
    test_fallback_and_scene_count()
    test_voices_catalog()
    test_runway_moderation_person()
    test_runway_credits_message()
    test_text_to_image_payload_no_refs()
    test_consent_gate()
    test_speech_budget_custom()
    test_presets_and_cost()
    test_progress_weights()
    test_camera_motion_soft()
    test_tiktok_upload_filename()
    test_last_frame_chains_with_user_photo()
    test_wave2_thin_api()
    test_store_sqlite_voices_and_prefs()
    test_act_two_uses_photo_consent()
    test_preset_topic_goes_to_cost()
    test_watermark_ffmpeg_overlay()
    test_clone_posts_voices_add()
    test_night_policy_defaults()
    test_legacy_night_schema_migrates()
    test_edit_timecodes_and_limits()
    test_upscale_result_uses_video_upscale()
    print("ok")
