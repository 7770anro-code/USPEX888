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
    compact_runway_models,
    duration_for_model,
    enforce_speech_budget,
    estimate_speech_sec,
    fallback_split_script,
    format_runway_usage,
    format_script,
    i2v_fallback_chain,
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
    still_model_for_quality,
    target_scene_count,
    text_to_image_payload,
    video_models_for_quality,
    visual_look_lock,
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
    assert "Прогресс сохранён" in err.user_message
    assert "Продолжить съёмку" in err.user_message
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
    assert "max" not in __import__("presets", fromlist=["QUALITY"]).QUALITY
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

    soft_keys = ("lock", "push", "pull", "pan", "min", "nat")
    blob = " ".join(
        v["prompt"]
        for d in (CAMERA, MOTION)
        for k, v in d.items()
        if k in soft_keys
    ).lower()
    for banned in ("spin", "dramatic", "energetic", "extreme close-up"):
        assert banned not in blob
    assert CAMERA["lock"]["prompt"] == "camera holds static"
    assert "subtle head turn" in MOTION["nat"]["prompt"]
    assert MOTION["min"]["prompt"] == "minimal body movement"
    assert "punch-in" in CAMERA["punch"]["prompt"]
    assert "steps" in MOTION["drive"]["prompt"]


def test_night_script_quality_and_hook() -> None:
    import inspect
    import shutil
    import tempfile
    from pathlib import Path

    import config
    import store
    from night_ideas import IDEA_SYSTEM, parse_ideas
    from night_store import create_job, get_job, insert_idea
    from pipeline import (
        SCRIPT_SYSTEM_PHOTO,
        SCRIPT_SYSTEM_SYNTH,
        grok_script,
        hook_opens_narration,
        scene_has_cta,
        script_quality_issues,
        script_system_for,
    )

    assert "Soft only" in SCRIPT_SYSTEM_PHOTO
    assert "No spin, dramatic, extreme close-up, energetic." in SCRIPT_SYSTEM_PHOTO
    assert "Soft only" not in SCRIPT_SYSTEM_SYNTH
    assert "Energy allowed" in SCRIPT_SYSTEM_SYNTH
    assert "18–28" in SCRIPT_SYSTEM_SYNTH
    assert "continuity" in SCRIPT_SYSTEM_SYNTH.lower()
    assert "No face" in SCRIPT_SYSTEM_SYNTH
    assert script_system_for(photo_lock=True) == SCRIPT_SYSTEM_PHOTO
    assert script_system_for(photo_lock=False) == SCRIPT_SYSTEM_SYNTH

    src = inspect.getsource(grok_script)
    assert "xai_creative_models" in src
    assert "script_quality_issues" in src
    assert "photo_lock" in src
    from night_ideas import _grok_raw

    assert "xai_creative_models" in inspect.getsource(_grok_raw)
    from config import xai_creative_models

    models = xai_creative_models()
    assert models[0] == "grok-4.5" or models[0].startswith("grok-4.5")
    assert "grok-4.5" in models

    hook = "Не прыгай выше головы — поставь таймер на пять минут."
    assert hook_opens_narration(hook, hook + " Сейчас, не завтра.")
    assert not hook_opens_narration(hook, "Одна ступень — пять минут фокуса.")
    assert scene_has_cta("Поставь таймер на пять минут и не листай дальше.")
    assert not scene_has_cta("Лестница светлеет с каждым шагом вверх.")

    old = {
        "title": "Лестница Микро",
        "scenes": [
            {"narration": "Одна ступень — пять минут фокуса.", "visual_prompt": "static"},
            {"narration": "Не прыгай выше головы, шагай.", "visual_prompt": "push-in"},
            {"narration": "Лестница светлеет вместе с тобой.", "visual_prompt": "soft"},
            {"narration": "Просто сделай первый шаг.", "visual_prompt": "hold"},
        ],
    }
    issues = script_quality_issues(old, hook=hook, n_scenes=4)
    assert "короткая" in issues.lower() or "слов" in issues
    assert "хука" in issues

    long1 = (
        "Не прыгай выше головы — поставь таймер на пять минут. "
        "Телефон снова тянет руку? Спроси себя: ты сейчас работаешь или просто листаешь?"
    )
    long2 = (
        "Коллега уже закрыл ноутбук, а ты всё ещё «ещё одну вкладку». "
        "Поставь таймер, закрой мессенджер и сделай один кусок задачи до сигнала."
    )
    long3 = (
        "Сигнал прозвенел — ты дописал абзац, не идеальный, но живой. "
        "Почему ждать понедельника, если пять минут уже сдвинули лестницу?"
    )
    long4 = (
        "Завтра снова будет шум. Сохрани этот ролик и завтра утром "
        "повтори тот же таймер — одна ступень, не марафон. Подпишись, если шагаешь с нами."
    )
    for blob in (long1, long2, long3, long4):
        assert 18 <= len(blob.split()) <= 32
    good = {
        "title": "Лестница Микро",
        "scenes": [
            {"narration": long1, "visual_prompt": "punch-in"},
            {"narration": long2, "visual_prompt": "reach"},
            {"narration": long3, "visual_prompt": "turn"},
            {"narration": long4, "visual_prompt": "cta"},
        ],
    }
    assert script_quality_issues(good, hook=hook, n_scenes=4) == ""

    from night_video import render_idea

    nv = inspect.getsource(render_idea)
    assert 'camera_prompt("punch"' in nv
    assert 'motion_prompt("drive")' in nv
    assert "hook=hook" in nv
    assert "цепляющая фраза" in IDEA_SYSTEM or "0:00" in IDEA_SYSTEM

    parsed = parse_ideas(
        '{"ideas":[{"kind":"motivational","title":"Лестница Микро",'
        '"plot":"Пиксельный герой поднимается по ступеням по пять минут фокуса.",'
        '"caption":"Пять минут — одна ступень. #Успех888","hook":"Не прыгай выше головы","score":9}]}'
    )
    assert parsed[0]["hook"] == "Не прыгай выше головы"

    tmp = tempfile.mkdtemp()
    old_dir = config.DATA_DIR
    config.DATA_DIR = tmp
    store.reset_for_tests()
    try:
        iid = insert_idea(
            {
                "kind": "motivational",
                "title": "Лестница Микро",
                "plot": "plot",
                "caption": "cap",
                "hook": "Не прыгай выше головы",
                "idea_hash": "h",
                "tokens": ["лестница"],
            },
            "2026-08-24",
        )
        assert iid >= 1
        jid = create_job(
            {
                "run_date": "2026-08-24",
                "account_id": "motiv",
                "kind": "motivational",
                "title": "Лестница Микро",
                "plot": "plot",
                "caption": "cap",
                "hook": "Не прыгай выше головы",
            }
        )
        job = get_job(jid)
        assert job is not None
        assert job["hook"] == "Не прыгай выше головы"
    finally:
        config.DATA_DIR = old_dir
        store.reset_for_tests()
        shutil.rmtree(tmp, ignore_errors=True)


def test_manual_short_topic_expands() -> None:
    import inspect

    from bot import on_preset_topic, on_quick_idea
    from night_ideas import script_brief_from_idea, topic_expand_user
    from pipeline import build_video, is_short_topic
    from presets import default_job

    assert is_short_topic("лестница микро")
    assert is_short_topic("фокус")
    assert is_short_topic("утренний кофе на балконе")
    long_plot = (
        "Пиксельный персонаж поднимается по ступеням: каждая ступень — пять минут фокуса, "
        "лестница светлеет с каждым шагом, герой не прыгает через пролёты."
    )
    assert not is_short_topic(long_plot)

    q = inspect.getsource(on_quick_idea)
    assert "len(idea) < 3" in q
    assert "len(idea) < 8" not in q
    p = inspect.getsource(on_preset_topic)
    assert "len(idea) < 3" in p

    src = inspect.getsource(build_video)
    assert "expand_topic_to_idea" in src
    assert "is_short_topic" in src
    assert "not user_script" in src
    assert "not photo_lock" in src

    user = topic_expand_user("лестница микро")
    assert "лестница микро" in user
    assert "hook" in user.lower()
    brief = script_brief_from_idea(
        {
            "kind": "motivational",
            "title": "Лестница",
            "hook": "Не прыгай выше головы.",
            "plot": "Герой шагает по пяти минутам.",
        }
    )
    assert "Не прыгай выше головы" in brief
    assert "Хук первой секунды" in brief

    quick = default_job(mode="quick")
    assert quick["camera"] == "punch"
    assert quick["motion"] == "drive"
    custom = default_job(mode="custom")
    assert custom["camera"] == "push"
    assert custom["motion"] == "nat"
    assert custom["user_script"] is True


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
    assert "revise:final" in result_btns
    assert "menu:home" in result_btns
    labels = [b.text for row in result_kb().inline_keyboard for b in row]
    assert any("Улучшить" in t for t in labels)
    assert any("финал" in t.lower() for t in labels)


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
        store.save_last_job(
            7,
            {"idea": "лестница микро", "hook": "Не прыгай", "revisions": []},
            status="draft",
        )
        job = store.get_last_job(7)
        assert job is not None
        assert job["hook"] == "Не прыгай"
        assert job["status"] == "draft"
        fin = store.mark_last_job_final(7)
        assert fin is not None and fin["status"] == "final"
        assert store.get_last_job(7)["status"] == "final"
        store.clear_last_job(7)
        assert store.get_last_job(7) is None
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

    from wave2 import (
        CLONE_PLAN_MSG,
        ELEVEN_IVC_URL,
        clone_fail_user_message,
        clone_voice,
        parse_elevenlabs_error,
        prepare_clone_audio,
    )

    assert ELEVEN_IVC_URL.endswith("/v1/voices/add")
    src = inspect.getsource(clone_voice)
    assert "ELEVEN_IVC_URL" in src
    assert "prepare_clone_audio" in src
    assert "clone_fail_user_message" in src
    assert "audio/wav" in inspect.getsource(prepare_clone_audio) or "_ivc.wav" in inspect.getsource(
        prepare_clone_audio
    )
    prod = (
        '{"detail":{"type":"payment_required","code":"paid_plan_required",'
        '"message":"Your subscription does not include instant voice cloning. '
        'Please upgrade your plan.","status":"can_not_use_instant_voice_cloning"}}'
    )
    parsed = parse_elevenlabs_error(prod)
    assert parsed["code"] == "paid_plan_required"
    assert parsed["status"] == "can_not_use_instant_voice_cloning"
    assert clone_fail_user_message(400, prod) == CLONE_PLAN_MSG
    assert "Starter" in clone_fail_user_message(400, prod)
    short = '{"detail":{"message":"Audio is too short for cloning"}}'
    from wave2 import CLONE_SHORT_MSG

    assert clone_fail_user_message(422, short) == CLONE_SHORT_MSG
    perm = (
        '{"detail":{"type":"authentication_error","code":"unauthorized",'
        '"message":"The API key you used is missing the permission voices_write",'
        '"status":"missing_permissions"}}'
    )
    from wave2 import CLONE_KEY_PERM_MSG

    assert clone_fail_user_message(401, perm) == CLONE_KEY_PERM_MSG


def test_runway_model_router_optional() -> None:
    import inspect

    import config
    from pipeline import (
        RATIO_TO_ASPECT,
        _resume_or_submit,
        _runway_submit,
        runway_clip,
        runway_router_video_payload,
    )

    assert RATIO_TO_ASPECT["720:1280"] == "9:16"
    assert RATIO_TO_ASPECT["1280:720"] == "16:9"
    assert config.RUNWAY_USE_MODEL_ROUTER is False
    assert config.runway_model_router_enabled() is False
    payload = runway_router_video_payload(
        "a quiet kitchen, no faces",
        "720:1280",
        10,
        prompt_image="runway://img",
        seed=7,
        config_id="quality-vertical",
    )
    assert payload["configId"] == "quality-vertical"
    assert "model" not in payload
    assert payload["input"]["promptText"].startswith("a quiet kitchen")
    assert payload["input"]["aspectRatio"] == "9:16"
    assert payload["input"]["duration"] == 10
    assert payload["input"]["audio"] is False
    assert payload["input"]["referenceImages"] == [{"uri": "runway://img", "role": "first"}]
    assert payload["input"]["seed"] == 7
    t2v = runway_router_video_payload("fog over a city, no faces", "720:1280", 5, config_id="x")
    assert "referenceImages" not in t2v["input"]
    clip_src = inspect.getsource(runway_clip)
    submit_src = inspect.getsource(_runway_submit)
    assert "runway_model_router_enabled" in clip_src
    assert "/v1/generate/video" in clip_src
    assert 'path.startswith("/v1/generate/")' in submit_src
    resume_src = inspect.getsource(_resume_or_submit)
    assert ".runway_id" in resume_src
    assert "_runway_poll" in resume_src
    assert "_runway_submit" in resume_src


def test_credits_resume_keeps_artifacts() -> None:
    import inspect
    import tempfile
    from pathlib import Path

    from pipeline import eleven_tts, runway_clip
    from resume_job import (
        format_resume_progress,
        load_script,
        mark_credits_pause,
        next_scene_to_render,
        resume_progress,
        resume_work_dir,
        run_kwargs_from_checkpoint,
        save_checkpoint,
        save_script,
        script_is_resumable,
        wipe_resume,
    )

    tts_src = inspect.getsource(eleven_tts)
    assert "skip existing" in tts_src
    clip_src = inspect.getsource(runway_clip)
    assert "T2V rejected" in clip_src
    assert 'getattr(exc, "code", "") == "credits"' in clip_src
    submit_src = inspect.getsource(__import__("pipeline", fromlist=["_resume_or_submit"])._resume_or_submit)
    assert "side.unlink" in submit_src
    build_src = inspect.getsource(__import__("pipeline", fromlist=["build_video"]).build_video)
    assert "load_script" in build_src
    assert "resume muxed" in build_src
    bot_src = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
    assert "resume:go" in bot_src
    assert "credits_pause_kb" in bot_src
    assert "resume_work_dir" in bot_src

    tmp = tempfile.mkdtemp()
    work = Path(tmp)
    script = {
        "title": "Тест",
        "plot": "лестница микро",
        "scenes": [
            {"narration": "один два три четыре пять шесть семь восемь девять десять", "visual_prompt": "kitchen still"},
            {"narration": "ещё фраза для второй сцены нужна длиннее", "visual_prompt": "street still"},
            {"narration": "третья сцена тоже с достаточным текстом здесь", "visual_prompt": "window still"},
            {"narration": "четвёртая сцена завершает ролик призывом подписаться", "visual_prompt": "door still"},
        ],
    }
    assert script_is_resumable(script)
    save_script(work, script)
    assert load_script(work) is not None
    (work / "n0.mp3").write_bytes(b"x" * 500)
    (work / "n1.mp3").write_bytes(b"x" * 500)
    (work / "n2.mp3").write_bytes(b"x" * 500)
    (work / "n3.mp3").write_bytes(b"x" * 500)
    (work / "c0.mp4").write_bytes(b"x" * 20_000)
    (work / "c1.mp4").write_bytes(b"x" * 20_000)
    (work / "c2.mp4").write_bytes(b"x" * 20_000)
    (work / "m0.mp4").write_bytes(b"x" * 20_000)
    (work / "m1.mp4").write_bytes(b"x" * 20_000)
    (work / "m2.mp4").write_bytes(b"x" * 20_000)
    (work / "bible_still.png").write_bytes(b"x" * 2000)
    mark_credits_pause(
        work,
        run={
            "idea": "лестница микро",
            "n_scenes": 4,
            "quality": "optimal",
            "voice_name": "Сара",
        },
    )
    prog = resume_progress(work, 4)
    assert prog["has_script"] is True
    assert prog["has_still"] is True
    assert prog["tts"] == 4
    assert prog["clips"] == 3
    assert prog["muxed"] == 3
    assert next_scene_to_render(work, 4) == 3
    text = format_resume_progress(work, 4)
    assert "4/4" in text
    assert "3/4" in text
    kw = run_kwargs_from_checkpoint(work)
    assert kw is not None
    assert kw["idea"] == "лестница микро"
    assert resume_work_dir(7).name == "7_resume"
    wipe_resume(7)

    import shutil

    shutil.rmtree(tmp, ignore_errors=True)


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
    assert "job_scope" in inspect.getsource(_render_job)
    assert "live_markup_dict" in inspect.getsource(_render_job)
    from bot import on_live_refresh, main as bot_main

    assert "live:" in inspect.getsource(bot_main)
    assert "fetch_runway_task" in inspect.getsource(on_live_refresh) or "compose_live_text" in inspect.getsource(on_live_refresh)
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


def test_live_status_runway_fields() -> None:
    import inspect

    import live_status as live
    from pipeline import fetch_runway_task

    live.reset_for_tests()
    assert live.parse_runway_progress(0.2) == 0.2
    assert live.parse_runway_progress(0) == 0.0
    assert live.parse_runway_progress(1) == 1.0
    assert live.parse_runway_progress(20) is None
    assert live.parse_runway_progress("nope") is None
    assert live.parse_runway_progress(None) is None
    assert live.parse_runway_frame({"status": "RUNNING", "progress": 0.2}) is None
    assert live.parse_runway_frame({"currentFrame": 17}) == 17

    live.start_job("m42", chat_id=42, title="Тест", scene_total=4)
    live.update_job(
        "m42",
        stage=live.STAGE_RUNWAY,
        scene_n=2,
        scene_total=4,
        label="Сцена 2 из 4 рендерится в Runway",
        runway_task_id="11111111-1111-4111-8111-111111111111",
        runway_status="RUNNING",
        runway_progress=0.2,
    )
    text = live.format_status(live.get_job("m42"))
    assert "сцена 2 из 4" in text
    assert "20%" in text
    assert "кадр 17" not in text
    assert "GET /v1/tasks" in text
    no_pct = dict(live.get_job("m42") or {})
    no_pct["runway_progress"] = None
    quiet = live.format_status(no_pct)
    assert "20%" not in quiet
    assert live.parse_callback_key("live:m42") == "m42"
    assert live.parse_callback_key("live:n7") == "n7"
    assert live.parse_callback_key("live:../x") is None
    src = inspect.getsource(fetch_runway_task)
    assert "session.get" in src
    assert "/v1/tasks/" in src
    assert "session.post" not in src
    poll_src = inspect.getsource(__import__("pipeline", fromlist=["_runway_poll"])._runway_poll)
    assert "session.get" in poll_src
    live.reset_for_tests()


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

    from bot import _pixel_upscale_last, on_revise_notes, on_upscale_last, result_kb
    from wave2 import video_upscale_payload

    ask = inspect.getsource(on_upscale_last)
    assert "REVISE_ASK" in ask
    assert "revise_notes" in ask
    assert "_job_is_final" in ask
    assert "/v1/video_upscale" not in ask
    notes = inspect.getsource(on_revise_notes)
    assert "_revision_extra_brief" in notes
    assert "revisions" in notes
    assert "_job_is_final" in notes
    assert "BUSY.locked()" in notes
    pix = inspect.getsource(_pixel_upscale_last)
    assert "/v1/video_upscale" in pix
    assert "video_upscale_payload" in pix
    assert "_send_video" in pix
    payload = video_upscale_payload("runway://final")
    assert payload["model"] == "magnific_video_upscaler_creative"
    kb = result_kb(can_finalize=True)
    data = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert data == ["upscale:last", "revise:final", "menu:home"]
    slim = [b.callback_data for row in result_kb(can_finalize=False).inline_keyboard for b in row]
    assert "revise:final" not in slim
    from bot import _revision_extra_brief

    blob = _revision_extra_brief(
        {
            "kind": "motivational",
            "hook": "Не прыгай выше головы.",
            "idea": "Пиксельный герой по ступеням.",
            "title": "Лестница",
            "preset_brief": "",
            "revisions": ["вторая сцена скучная", "хук слабый"],
        }
    )
    assert "Не прыгай выше головы" in blob
    assert "вторая сцена скучная" in blob
    assert "хук слабый" in blob
    assert blob.find("Правки зрителя") < blob.find("Сюжет:")
    from pipeline import grok_script

    gsrc = inspect.getsource(grok_script)
    assert "extra_brief.strip()[:4000]" in gsrc


def test_look_and_runway_models() -> None:
    from pathlib import Path
    import inspect
    import tempfile

    from pipeline import (
        _runway_submit,
        read_runway_model,
        runway_clip,
        runway_video_payload,
        write_runway_model,
    )

    cine = compose_runway_prompt("red coat, rainy street", "slow push-in", style="cinematic")
    assert "ARRI Alexa Mini" in cine
    assert "same character as reference image" in cine
    assert "do not alter face" in cine
    assert "shot on iPhone" not in cine
    assert visual_look_lock("cinematic").startswith("shot on ARRI")
    cartoon = compose_runway_prompt("pixel hero, neon stairs", "punch-in", style="cartoon")
    assert "shot on iPhone" not in cartoon
    assert "shot on ARRI" not in cartoon
    assert "3D render" in cartoon
    phone = compose_runway_prompt("product on table", "hold", style="cinematic", photo_lock=True)
    assert "iPhone 15 Pro" in phone
    assert "shot on ARRI" not in phone
    ad = compose_runway_prompt("bottle on marble", "push", style="ad")
    assert "iPhone 15 Pro" in ad

    usage = format_runway_usage(
        {
            "runway_still_model": "gen4_image",
            "runway_models": ["gen4.5", "gen4_turbo", "gen4.5"],
        }
    )
    assert "сцена 1 gen4.5" in usage
    assert "сцена 2 gen4_turbo" in usage
    assert "дешёвый запас" in usage
    assert compact_runway_models(["gen4.5", "gen4_turbo"]) == "сцены: gen4.5, gen4_turbo"
    preview = format_script(
        {
            "title": "X",
            "scenes": [{"narration": "hi", "visual_prompt": "x"}],
            "runway_still_model": "gemini_image3_pro",
            "runway_models": ["veo3.1", "veo3.1"],
        }
    )
    assert "gemini_image3_pro" in preview
    assert "veo3.1" in preview

    gem = text_to_image_payload("still", "720:1280", model="gemini_image3_pro")
    assert gem["model"] == "gemini_image3_pro"
    assert gem["ratio"] == "768:1344"
    assert "contentModeration" not in gem
    flash = text_to_image_payload("still", "720:1280", model="gemini_image3.1_flash")
    assert flash["model"] == "gemini_image3.1_flash"
    gen = text_to_image_payload("still", "720:1280")
    assert gen["model"] == "gen4_image"
    assert gen["contentModeration"]["publicFigureThreshold"] == "auto"

    veo = runway_video_payload(
        "veo3.1", "a quiet kitchen", "720:1280", 10, seed=7, prompt_image="data:x"
    )
    assert veo["duration"] == 8
    assert veo["audio"] is False
    assert "seed" not in veo
    assert "contentModeration" not in veo
    assert veo["promptImage"] == "data:x"
    fast_veo = runway_video_payload("veo3.1_fast", "a quiet kitchen", "720:1280", 5)
    assert fast_veo["duration"] == 4
    assert fast_veo["audio"] is False
    g45 = runway_video_payload("gen4.5", "a quiet kitchen", "720:1280", 10, seed=7)
    assert g45["duration"] == 10
    assert g45["seed"] == 7
    assert g45["contentModeration"]["publicFigureThreshold"] == "auto"
    assert duration_for_model("veo3.1_fast", 5) == 4
    assert duration_for_model("gen4_turbo", 5) == 5
    assert i2v_fallback_chain("veo3.1") == ["veo3.1", "gen4.5", "gen4_turbo"]
    assert i2v_fallback_chain("seedance2_5") == ["seedance2_5", "gen4.5", "gen4_turbo"]
    sd = runway_video_payload(
        "seedance2_5", "a quiet kitchen", "720:1280", 5, seed=7, prompt_image="data:x"
    )
    assert sd["duration"] == 5
    assert sd["audio"] is False
    assert "seed" not in sd
    assert "contentModeration" not in sd
    assert sd["promptImage"] == [{"uri": "data:x", "position": "first"}]
    assert duration_for_model("seedance2_5", 3) == 4
    assert video_models_for_quality("fast") == ("gen4_turbo", "")
    assert video_models_for_quality("optimal")[0] == "gen4.5"
    assert still_model_for_quality("optimal") == "gen4_image"
    assert still_model_for_quality("fast") == "gen4_image"

    submit_src = inspect.getsource(_runway_submit)
    assert "return str(task_id), model_used" in submit_src
    clip_src = inspect.getsource(runway_clip)
    assert "_i2v_with_fallback" in clip_src
    assert "is_runway_credits_fail" in clip_src
    credits_at = clip_src.find("is_runway_credits_fail")
    next_at = clip_src.find("I2V %s failed, try")
    assert credits_at != -1 and next_at != -1 and credits_at < next_at
    bot_src = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
    assert "compact_runway_models" in bot_src
    assert "format_runway_usage" in bot_src
    assert "первый кадр:" in bot_src

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "c0.mp4"
        dest.write_bytes(b"x" * 20)
        write_runway_model(dest, "gen4_turbo")
        assert read_runway_model(dest) == "gen4_turbo"
        assert dest.with_suffix(".mp4.runway_model").is_file() or dest.with_name(
            dest.name + ".runway_model"
        ).is_file() or dest.with_suffix(dest.suffix + ".runway_model").is_file()


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
    test_night_script_quality_and_hook()
    test_manual_short_topic_expands()
    test_last_frame_chains_with_user_photo()
    test_wave2_thin_api()
    test_store_sqlite_voices_and_prefs()
    test_act_two_uses_photo_consent()
    test_preset_topic_goes_to_cost()
    test_watermark_ffmpeg_overlay()
    test_clone_posts_voices_add()
    test_runway_model_router_optional()
    test_credits_resume_keeps_artifacts()
    test_night_policy_defaults()
    test_legacy_night_schema_migrates()
    test_live_status_runway_fields()
    test_edit_timecodes_and_limits()
    test_upscale_result_uses_video_upscale()
    test_look_and_runway_models()
    print("ok")
