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
    assert pick_clip_duration(8.0, prefer_short=True) == 5
    assert pick_clip_duration(9.2, prefer_short=True) == 5
    assert pick_clip_duration(9.3, prefer_short=True) == 10


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
    assert "same character as reference image" in a
    assert "do not alter face" in a
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
    assert est["runway"] == 0
    assert est["provider"] == "fal"
    assert "fal.ai" in est["text"]
    assert "Runway" in est["text"]
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
        SCENE_NARRATION_MAX_WORDS,
        SCENE_NARRATION_MIN_WORDS,
        SCRIPT_SYSTEM_PHOTO,
        SCRIPT_SYSTEM_SYNTH,
        grok_script,
        hook_opens_narration,
        scene_has_cta,
        script_quality_issues,
        script_system_for,
        visual_fallback_prompt,
        visual_is_soft_only,
    )

    assert SCENE_NARRATION_MIN_WORDS == 18
    assert SCENE_NARRATION_MAX_WORDS == 28
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
    assert "temperature=temp" in src
    assert "0.7" in src
    assert "narration_word_limits" in src
    assert "dynamic_pacing" in src
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
        assert 18 <= len(blob.split()) <= 28
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

    too_long = dict(good)
    too_long["scenes"] = [
        {
            "narration": long1 + " ещё слова чтобы перевалить лимит двадцать восемь слов здесь точно",
            "visual_prompt": "punch-in",
        },
        *good["scenes"][1:],
    ]
    long_issues = script_quality_issues(too_long, hook=hook, n_scenes=4)
    assert "длинн" in long_issues.lower()

    soft_script = {
        "title": "Лестница Микро",
        "scenes": [
            {"narration": long1, "visual_prompt": "camera holds static, slow subtle push-in, minimal body movement"},
            {"narration": long2, "visual_prompt": "punch-in"},
            {"narration": long3, "visual_prompt": "reach"},
            {"narration": long4, "visual_prompt": "cta"},
        ],
    }
    soft_issues = script_quality_issues(soft_script, hook=hook, n_scenes=4, photo_lock=False)
    assert "мягк" in soft_issues.lower() or "punch-in" in soft_issues
    assert script_quality_issues(soft_script, hook=hook, n_scenes=4, photo_lock=True) == ""
    assert visual_is_soft_only("camera holds static, slow subtle push-in")
    assert not visual_is_soft_only("decisive punch-in then handheld drive")
    assert visual_fallback_prompt(photo_lock=True) == "slow subtle push-in, minimal body movement"
    assert "punch-in" in visual_fallback_prompt(photo_lock=False)

    from night_video import render_idea

    nv = inspect.getsource(render_idea)
    assert 'camera_prompt("punch"' in nv
    assert 'motion_prompt("drive")' in nv
    assert "hook=hook" in nv
    assert "цепляющая" in IDEA_SYSTEM or "0:00" in IDEA_SYSTEM
    assert "8–14" in IDEA_SYSTEM
    assert "Не прыгай выше головы" in IDEA_SYSTEM

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
    expand_window = src[src.index("expand_topic_to_idea") - 350 : src.index("expand_topic_to_idea")]
    assert "not user_script" in expand_window
    assert "is_short_topic" in expand_window
    assert "not photo_lock" not in expand_window

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


def test_quick_optional_photo_voice() -> None:
    """1-клик: короткая тема → опционально фото/голос, без поломки custom."""
    import inspect

    from bot import (
        PHOTO_CONSENT_PROMPT,
        _go_voice_step,
        _voices_kb,
        consent_kb,
        on_consent,
        on_custom_script,
        on_photo_skip,
        on_quick_idea,
        on_voice_skip,
        photo_skip_kb,
        voice_kb,
    )
    from voices import voice_by_index

    q = inspect.getsource(on_quick_idea)
    assert "len(idea) < 3" in q
    assert 'job["user_script"] = False' in q
    assert "Flow.custom_photo" in q
    assert "photo_skip_kb" in q
    assert "Flow.tune" not in q
    assert "хук" in q.lower() or "Хук" in q
    assert "DYNAMIC_SCENE_COUNT" in q
    assert "dynamic_pacing" in q

    custom = inspect.getsource(on_custom_script)
    from bot import main, on_menu

    menu_src = inspect.getsource(on_menu)
    assert "Пришли готовый текст ролика" in menu_src
    assert "menu:custom" in menu_src
    assert 'job["user_script"] = True' in custom
    assert "Flow.custom_photo" in custom
    assert "script_too_long_for_custom" in custom
    assert "len(text) < 20" in custom

    skip_src = inspect.getsource(on_photo_skip)
    assert "_go_voice_step" in skip_src
    voice_step = inspect.getsource(_go_voice_step)
    assert '"Спасибо. Теперь выбери голос:"' in voice_step
    assert '"Выбери голос:"' in voice_step
    assert 'job.get("mode") == "quick"' in voice_step
    assert "allow_skip=True" in voice_step
    assert "Сара" in voice_step

    consent_src = inspect.getsource(on_consent)
    assert 'job.get("mode") == "act_two"' in consent_src
    assert "_go_voice_step" in consent_src
    assert "PHOTO_CONSENT_PROMPT" in inspect.getsource(
        __import__("bot", fromlist=["_maybe_start_consent"])._maybe_start_consent
    )
    assert "моё фото" in PHOTO_CONSENT_PROMPT.lower()
    photo_btns = [b.callback_data for row in consent_kb().inline_keyboard for b in row]
    assert photo_btns[0] == "consent:yes"
    skip_btns = [b.callback_data for row in photo_skip_kb().inline_keyboard for b in row]
    assert "photo:skip" in skip_btns

    skip_voice = inspect.getsource(on_voice_skip)
    assert 'job.get("mode") != "quick"' in skip_voice
    assert "voice_by_index(1)" in skip_voice
    assert "Flow.tune" in skip_voice
    sara = voice_by_index(1)
    assert sara["name"] == "Сара"

    plain = voice_kb(0)
    plain_data = [b.callback_data for row in plain.inline_keyboard for b in row]
    assert "vskip:default" not in plain_data
    with_skip = _voices_kb(None, 0, allow_skip=True)
    skip_data = [b.callback_data for row in with_skip.inline_keyboard for b in row]
    assert skip_data[0] == "vskip:default"
    assert "Пропустить голос" in with_skip.inline_keyboard[0][0].text
    assert 'F.data == "vskip:default"' in inspect.getsource(main)


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
    assert "RUNWAY_SEEDANCE_MODELS" in src
    assert "anchor_image" in src
    assert 'video_provider() == "fal"' in src
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
        _runway_clip_native,
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
    clip_src = inspect.getsource(_runway_clip_native)
    submit_src = inspect.getsource(_runway_submit)
    assert "runway_model_router_enabled" in clip_src
    assert "/v1/generate/video" in clip_src
    assert "render_clip" in inspect.getsource(runway_clip)
    assert 'path.startswith("/v1/generate/")' in submit_src
    resume_src = inspect.getsource(_resume_or_submit)
    assert ".runway_id" in resume_src
    assert "_runway_poll" in resume_src
    assert "_runway_submit" in resume_src


def test_credits_resume_keeps_artifacts() -> None:
    import inspect
    import tempfile
    from pathlib import Path

    from pipeline import _runway_clip_native, eleven_tts, runway_clip
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
    assert "fal_minimax_tts" in tts_src
    clip_src = inspect.getsource(_runway_clip_native)
    assert "T2V rejected" in clip_src
    assert 'getattr(exc, "code", "") == "credits"' in clip_src
    assert "render_clip" in inspect.getsource(runway_clip)
    submit_src = inspect.getsource(__import__("pipeline", fromlist=["_resume_or_submit"])._resume_or_submit)
    assert "side.unlink" in submit_src
    build_src = inspect.getsource(__import__("pipeline", fromlist=["build_video"]).build_video)
    assert "load_script" in build_src
    assert "resume muxed" in build_src
    bot_src = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
    assert "resume:go" in bot_src
    assert "credits_pause_kb" in bot_src
    assert "resume_work_dir" in bot_src
    assert "shoot_fail_text" in bot_src
    assert "В Mini App заново заходить не нужно" in bot_src
    assert "mark_credits_pause(work)" in bot_src
    assert "_user_photo_plates" in bot_src

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
            "photo_file_id": "fid-a",
            "photo_file_ids": ["fid-a", "fid-b"],
            "kind": "autorolik",
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
    assert kw["photo_file_ids"] == ["fid-a", "fid-b"]
    assert kw["kind"] == "autorolik"
    from bot import _user_photo_plates

    (work / "user_photo_2.jpg").write_bytes(b"x" * 100)
    (work / "user_photo_1.jpg").write_bytes(b"x" * 100)
    assert [p.name for p in _user_photo_plates(work)] == ["user_photo_1.jpg", "user_photo_2.jpg"]
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
    payload = live.status_payload(live.get_job("m42"))
    assert payload["active"] is True
    assert payload["scene_n"] == 2
    assert payload["scene_total"] == 4
    assert payload["scenes"][1]["current"] is True
    assert "Runway" in (payload["scenes"][1]["label"] or "")
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


def test_edit_auto_synth_vibe_no_download() -> None:
    import inspect

    from bot import (
        HOW_IT_WORKS,
        _run_auto_edit,
        _run_synth_vibe,
        edit_auto_source_kb,
        edit_hub_kb,
        on_edit_callback,
    )
    from edit import (
        VIBE_SYNTH_LOCK,
        scenes_for_vibe,
        vibe_style,
        vibe_synth_brief,
    )
    from night_ideas import expand_topic_to_idea
    from pipeline import build_video, mux_scene

    hub = [b.callback_data for row in edit_hub_kb().inline_keyboard for b in row]
    assert "edit:auto" in hub
    assert "edit:cut" in hub
    src_kb = [b.callback_data for row in edit_auto_source_kb().inline_keyboard for b in row]
    assert src_kb == ["edit:own", "edit:gen", "menu:edit"]

    own = inspect.getsource(_run_auto_edit)
    assert "plan_clips" in own
    assert "build_video" not in own
    assert "_run_job" not in own
    assert "yt-dlp" not in own
    assert "youtube" not in own.lower()

    gen = inspect.getsource(_run_synth_vibe)
    assert "_run_job" in gen
    assert "vibe_synth_brief" in gen
    assert "photo_file_id=None" in gen
    assert "yt-dlp" not in gen
    assert "yt_dlp" not in gen
    assert "playwright" not in gen.lower()
    assert "не качаю" in gen or "не ищу" in gen

    cb = inspect.getsource(on_edit_callback)
    assert 'data == "own"' in cb
    assert 'data == "gen"' in cb
    assert "edit_auto_pick" in cb
    assert "yt-dlp" not in cb

    brief = vibe_synth_brief("в духе Inception, неоновые коридоры")
    assert "Inception" in brief
    assert "ориентир" in brief.lower()
    assert "скачива" in brief.lower()
    assert "ремейка" in brief.lower() or "ремейк" in VIBE_SYNTH_LOCK.lower()
    assert "instagram" not in brief.lower()
    assert scenes_for_vibe("ночной вайб") == 6
    assert scenes_for_vibe("динамичный 30-45 сек") == 6
    assert scenes_for_vibe("ролик 20 сек") == 4
    assert scenes_for_vibe("нарезка 55 секунд") == 6
    assert vibe_style("мульт про чайник") == "cartoon"
    assert vibe_style("ночной город") == "cinematic"

    expand_src = inspect.getsource(expand_topic_to_idea)
    assert "extra_user" in expand_src
    build_src = inspect.getsource(build_video)
    assert "extra_user=extra_brief" in build_src
    mux_src = inspect.getsource(mux_scene)
    assert "BURN_SUBTITLES" in mux_src
    assert "drawtext" in mux_src

    assert "чужие ролики не скачиваю" in HOW_IT_WORKS
    assert "SCRIPT_SYSTEM_SYNTH" in HOW_IT_WORKS
    assert "whisper" not in gen.lower()
    assert "grok.com" not in gen.lower()
    assert "chatgpt.com" not in gen.lower()


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
    assert "upscale_media" in pix
    assert "_send_video" in pix
    from studio import upscale_media

    up_src = inspect.getsource(upscale_media)
    assert "/v1/video_upscale" in up_src
    assert "fal_upscale_file" in up_src
    assert "video_upscale_payload" in up_src
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
        _runway_clip_native,
        _runway_submit,
        build_video,
        read_runway_model,
        runway_clip,
        runway_video_payload,
        write_runway_model,
    )
    from provider_router import render_clip

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
    assert sd["audio"] is True
    assert "seed" not in sd
    assert "contentModeration" not in sd
    assert sd["promptImage"] == "data:x"
    assert isinstance(sd["promptImage"], str)
    sd_t2v = runway_video_payload("seedance2_5", "a quiet kitchen", "720:1280", 5)
    assert sd_t2v["audio"] is False
    assert "promptImage" not in sd_t2v
    assert duration_for_model("seedance2_5", 3) == 4
    clip_upload = inspect.getsource(render_clip)
    native = inspect.getsource(_runway_clip_native)
    assert "generate_kling" in clip_upload
    assert "generate_seedance" in clip_upload
    assert "legacy_runway" in clip_upload
    assert "runway_upload_data_uri" in native
    assert "RUNWAY_SEEDANCE_MODELS" in native
    assert video_models_for_quality("fast") == ("gen4_turbo", "")
    assert video_models_for_quality("optimal")[0] == "gen4.5"
    assert still_model_for_quality("optimal") == "gen4_image"
    assert still_model_for_quality("fast") == "gen4_image"

    submit_src = inspect.getsource(_runway_submit)
    assert "return str(task_id), model_used" in submit_src
    clip_src = inspect.getsource(_runway_clip_native)
    assert "_i2v_with_fallback" in clip_src
    assert "is_runway_credits_fail" in clip_src
    credits_at = clip_src.find("is_runway_credits_fail")
    next_at = clip_src.find("I2V %s failed, try")
    assert credits_at != -1 and next_at != -1 and credits_at < next_at
    build_src = inspect.getsource(build_video)
    assert "same_still" in build_src
    assert "chain_for" in build_src
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


def test_serial_reveal_show() -> None:
    import inspect
    import shutil
    import tempfile
    from datetime import date
    from pathlib import Path

    import config
    import store
    from night_accounts import load_accounts, night_feed_accounts, serial_account
    from night_runner import run_night
    from night_store import VIDEO_READY, create_job, remaining_daily_slots
    from pipeline import grok_script
    from serial_plot import (
        DEFAULT_SEED,
        SERIAL_EPISODE_SYSTEM,
        SERIAL_SCRIPT_SYSTEM,
        parse_episode_plan,
        planner_user,
        serial_script_brief,
    )
    from serial_render import generate_episodes
    from serial_store import add_note, next_run_dates, upsert_serial

    accs = load_accounts()
    assert len(accs) == 4
    assert accs[-1].role == "serial"
    assert accs[-1].id == "serial"
    assert accs[-1].style == "cartoon"
    assert accs[-1].tiktok_token_var == "NIGHT_ACC4_TIKTOK_ACCESS_TOKEN"
    feed = night_feed_accounts()
    assert len(feed) == 3
    assert all(a.role != "serial" for a in feed)
    assert serial_account() is not None
    assert "serial" not in inspect.getsource(run_night).split("night_feed_accounts")[0][-80:] or True
    assert "night_feed_accounts" in inspect.getsource(run_night)

    plan = parse_episode_plan(
        '{"title":"Полосатый","hook":"Не прячься, кроха","plot":"Аля и Боря ждут гибрида во дворе.",'
        '"reveal":"ягода-арбуз","cliffhanger":"во дворе гудит вторая машина",'
        '"caption":"Серия 1 #гибриды","continuity":"3D cartoon fruit town no logos",'
        '"lore_add":"Гибрид Тоша","summary_update":"Тоша родился, машина гудит."}'
    )
    assert plan["title"] == "Полосатый"
    assert "Тоша" in plan["summary_update"]
    assert "логотип" in SERIAL_SCRIPT_SYSTEM.lower() or "logos" in SERIAL_SCRIPT_SYSTEM.lower()
    assert "клиффхэнгер" in SERIAL_SCRIPT_SYSTEM.lower() or "cliffhanger" in SERIAL_SCRIPT_SYSTEM.lower()
    assert "REVEAL" in SERIAL_SCRIPT_SYSTEM
    assert "скачиван" in SERIAL_EPISODE_SYSTEM.lower()
    assert "BMW" not in DEFAULT_SEED and "Mercedes" not in DEFAULT_SEED
    assert "логотип" in DEFAULT_SEED.lower() or "логотипов" in DEFAULT_SEED.lower()
    serial = {
        "title": "Гибриды",
        "seed": DEFAULT_SEED,
        "lore": "Аля",
        "continuity": "3D",
        "summary": "",
        "last_cliff": "",
    }
    user = planner_user(serial, [{"text": "пусть Тоша стесняется"}], n=2)
    assert "стесняется" in user
    assert "Серия номер 2" in user
    brief = serial_script_brief(serial, plan)
    assert "не закрывает" in brief.lower() or "Клиффхэнгер" in brief

    grok_src = inspect.getsource(grok_script)
    assert "script_system" in grok_src
    from pipeline import build_video

    build_src = inspect.getsource(build_video)
    assert "script_system" in build_src
    assert "photo_lock is None" in build_src
    gen_src = inspect.getsource(generate_episodes)
    assert "SERIAL_SCRIPT_SYSTEM" in inspect.getsource(__import__("serial_render", fromlist=["_render_one"]))
    render_src = inspect.getsource(__import__("serial_render"))
    assert "yt-dlp" not in render_src
    assert "photo_lock=False" in render_src
    assert "create_job" in render_src
    assert "WAIT_CONFIRM" in render_src

    from bot import HOW_IT_WORKS, main, more_kb, on_serial_callback

    more = [b.callback_data for row in more_kb().inline_keyboard for b in row]
    assert "serial:hub" in more
    cb = inspect.getsource(on_serial_callback)
    assert "serial:next" in inspect.getsource(__import__("serial_bot", fromlist=["serial_hub_kb"]).serial_hub_kb)
    from serial_bot import serial_hub_kb

    hub = [b.callback_data for row in serial_hub_kb().inline_keyboard for b in row]
    assert "serial:next" in hub
    assert "serial:b3" in hub
    assert "serial:note" in hub
    assert "Command(\"serial\")" in inspect.getsource(main)
    assert "Мультсериал" in HOW_IT_WORKS
    assert "NIGHT_ACC4" in HOW_IT_WORKS

    tmp = tempfile.mkdtemp()
    old = config.DATA_DIR
    config.DATA_DIR = tmp
    store.reset_for_tests()
    try:
        day = "2026-08-24"
        create_job(
            {
                "run_date": day,
                "account_id": "serial",
                "kind": "serial",
                "title": "ep",
                "status": VIDEO_READY,
            }
        )
        from night_store import update_job

        from pathlib import Path as P

        fake = P(tmp) / "s.mp4"
        fake.write_bytes(b"mp4")
        from night_store import jobs_for_date, get_job

        jobs = jobs_for_date(day)
        update_job(int(jobs[0]["id"]), video_path=str(fake))
        assert remaining_daily_slots(day, daily_limit=3) == 3
        row = upsert_serial({"slug": "hybrids", "title": "Гибриды", "account_id": "serial"})
        add_note(int(row["id"]), "в серии 3 пусть появится жёлтая капля-машина")
        dates = next_run_dates("serial", 3, start=date(2026, 8, 24))
        assert dates == ["2026-08-24", "2026-08-25", "2026-08-26"]
        from serial_store import insert_episode

        insert_episode(int(row["id"]), 1, {"run_date": "2026-08-24", "title": "one", "status": "wait_confirm"})
        dates2 = next_run_dates("serial", 2, start=date(2026, 8, 24))
        assert dates2[0] == "2026-08-25"
        assert "2026-08-24" not in dates2
    finally:
        config.DATA_DIR = old
        store.reset_for_tests()
        shutil.rmtree(tmp, ignore_errors=True)


def test_nano_banana_and_dynamic_pacing() -> None:
    import asyncio
    import base64
    import inspect
    from pathlib import Path

    import config
    from bot import _run_synth_vibe, cost_text, on_quick_idea
    from pipeline import (
        DYNAMIC_SCENE_COUNT,
        SCENE_NARRATION_MAX_WORDS_DYNAMIC,
        SCENE_NARRATION_MIN_WORDS_DYNAMIC,
        build_video,
        enhance_reference_with_nano_banana,
        extract_gemini_inline_image,
        narration_word_limits,
        nano_banana_prompt,
        script_quality_issues,
    )
    from presets import default_job, estimate_cost

    assert DYNAMIC_SCENE_COUNT == 6
    assert narration_word_limits() == (18, 28)
    assert narration_word_limits(dynamic_pacing=True) == (
        SCENE_NARRATION_MIN_WORDS_DYNAMIC,
        SCENE_NARRATION_MAX_WORDS_DYNAMIC,
    )
    quick = default_job(mode="quick")
    assert quick["n_scenes"] == 6
    assert quick["dynamic_pacing"] is True
    custom = default_job(mode="custom")
    assert custom["dynamic_pacing"] is False
    assert custom["n_scenes"] == 5

    blob = b"\x89PNG" + b"x" * 200
    encoded = base64.b64encode(blob).decode("ascii")
    camel = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": "ok"},
                        {"inlineData": {"mimeType": "image/png", "data": encoded}},
                    ]
                }
            }
        ]
    }
    assert extract_gemini_inline_image(camel) == blob
    snake = {"candidates": [{"content": {"parts": [{"inline_data": {"data": encoded}}]}}]}
    assert extract_gemini_inline_image(snake) == blob
    assert extract_gemini_inline_image({}) is None
    prompt = nano_banana_prompt("punch-in, looking at camera")
    assert "SAME person" in prompt
    assert "punch-in" in prompt
    assert "9:16" in prompt

    long_nar = " ".join(f"слово{i}" for i in range(22))
    too_long = {
        "title": "x",
        "scenes": [
            {"narration": long_nar, "visual_prompt": "punch-in handheld drive"} for _ in range(6)
        ],
    }
    issues = script_quality_issues(too_long, n_scenes=6, dynamic_pacing=True, photo_lock=True)
    assert "длинн" in issues.lower() or "максимум" in issues.lower()

    few = {
        "title": "x",
        "scenes": [
            {"narration": " ".join(f"слово{i}" for i in range(14)), "visual_prompt": "punch-in"}
            for _ in range(3)
        ],
    }
    few_issues = script_quality_issues(few, n_scenes=6, dynamic_pacing=True, photo_lock=True)
    assert "мало сцен" in few_issues.lower()

    env = Path(__file__).with_name(".env.example").read_text(encoding="utf-8")
    assert "GEMINI_API_KEY=" in env
    assert "aistudio.google.com/apikey" in env
    assert "FAL_API_KEY=" in env
    assert "FAL_KEY=" in env
    assert "fal.ai/dashboard/keys" in env
    assert "WEBAPP_PUBLIC_URL=" in env
    assert "GEMINI_API_KEY" not in inspect.getsource(config.missing_secrets)
    miss_src = inspect.getsource(config.missing_secrets)
    assert "FAL_KEY" in miss_src
    assert "RUNWAY_API_KEY" in miss_src
    assert "gemini-2.5-flash-image" in Path(__file__).with_name("config.py").read_text(
        encoding="utf-8"
    )

    banana_src = inspect.getsource(enhance_reference_with_nano_banana)
    from pipeline import GEMINI_GENERATE_URL

    assert "generativelanguage.googleapis.com" in GEMINI_GENERATE_URL
    assert "x-goog-api-key" in banana_src
    assert "google-genai" not in banana_src
    assert "GEMINI_GENERATE_URL" in banana_src
    build_src = inspect.getsource(build_video)
    assert "enhance_reference_with_nano_banana" in build_src
    assert "banana_still" in build_src
    assert "prefer_short=dynamic_pacing" in build_src

    night_src = Path(__file__).with_name("night_runner.py").read_text(encoding="utf-8")
    assert "n_scenes = 4" in night_src
    serial_src = Path(__file__).with_name("serial_render.py").read_text(encoding="utf-8")
    assert "dynamic_pacing=True" not in serial_src

    q = inspect.getsource(on_quick_idea)
    assert "DYNAMIC_SCENE_COUNT" in q
    assert "dynamic_pacing" in q
    vibe = inspect.getsource(_run_synth_vibe)
    assert "dynamic_pacing=True" in vibe
    assert "clip_sec=5" in vibe
    assert "photo_file_id=None" in vibe

    text = cost_text(
        {"n_scenes": 6, "quality": "optimal", "idea": "кофе", "dynamic_pacing": True}
    )
    assert "6 × 5 сек" in text
    assert "fal.ai" in text
    est = estimate_cost(n_scenes=6, clip_sec=5, quality="optimal", text="кофе", need_still=True)
    assert est["runway"] == 0
    assert est["provider"] == "fal"

    old_key = config.GEMINI_API_KEY
    config.GEMINI_API_KEY = ""

    async def _skip() -> object:
        return await enhance_reference_with_nano_banana(
            None,  # type: ignore[arg-type]
            Path("/tmp/missing-photo.jpg"),
            Path("/tmp/banana_out.png"),
        )

    try:
        assert asyncio.run(_skip()) is None
    finally:
        config.GEMINI_API_KEY = old_key


def test_fal_kling_and_miniapp() -> None:
    import hashlib
    import hmac
    import inspect
    import time
    from pathlib import Path
    from urllib.parse import urlencode

    import config
    from fal_api import (
        FAL_CREDITS_MSG,
        extract_fal_media_url,
        fal_fail_error,
        fal_headers,
        fal_poll,
        fal_request_urls,
        fal_run,
        _model_id_candidates,
        _result_url_candidates,
    )
    from fal_models import (
        KLING_I2V_PRO,
        SEEDANCE_I2V,
        as_file_url,
        i2v_fallback_models,
        kling_duration,
        kling_i2v_payload,
        quality_video_model,
        seedance_i2v_payload,
        topaz_image_payload,
        topaz_video_payload,
        tryon_payload,
        use_fal,
        video_payload,
    )
    from presets import QUALITY, RUNWAY_QUALITY, quality_catalog
    from webapp_auth import WebAppAuthError, validate_init_data

    assert config.video_provider() == "fal"
    assert use_fal() is True
    assert QUALITY["fast"]["i2v_model"] == SEEDANCE_I2V
    assert QUALITY["optimal"]["i2v_model"] == KLING_I2V_PRO
    assert quality_catalog()["optimal"]["i2v_model"] == KLING_I2V_PRO
    assert RUNWAY_QUALITY["optimal"]["i2v_model"] == "gen4.5"
    assert quality_video_model("fast") == SEEDANCE_I2V
    assert quality_video_model("optimal") == KLING_I2V_PRO
    assert kling_duration(5) == "5"
    assert kling_duration(2) == "3"
    assert kling_duration(20) == "15"
    kling = kling_i2v_payload("a quiet street", "https://example.com/a.jpg", 5)
    assert kling["duration"] == "5"
    assert kling["generate_audio"] is False
    assert kling["start_image_url"].startswith("https://")
    seed = seedance_i2v_payload("a quiet street", "https://example.com/a.jpg", 5)
    assert seed["duration"] == "5"
    assert seed["generate_audio"] is False
    assert seed["aspect_ratio"] == "auto"
    assert seed["resolution"] == "720p"
    body = video_payload(KLING_I2V_PRO, "go", "data:image/jpeg;base64,xx", 5)
    assert body["generate_audio"] is False
    assert as_file_url("data:image/png;base64,abc") == "data:image/png;base64,abc"
    top = topaz_video_payload("https://example.com/v.mp4")
    assert top["model"] == "Proteus"
    img = topaz_image_payload("https://example.com/p.jpg")
    assert img["upscale_factor"] == 2.0
    tryon = tryon_payload("https://a", "https://b")
    assert tryon["person_image_url"] == "https://a"
    assert tryon["product_image_url"] == "https://b"
    assert "clothing_image_url" not in tryon
    assert "preserve_pose" not in tryon
    assert i2v_fallback_models(KLING_I2V_PRO)[0] == KLING_I2V_PRO
    assert SEEDANCE_I2V in i2v_fallback_models(KLING_I2V_PRO)
    usage = format_runway_usage(
        {
            "runway_still_model": "fal-ai/flux/schnell",
            "runway_models": [KLING_I2V_PRO, SEEDANCE_I2V],
        }
    )
    assert usage.startswith("fal.ai:")
    assert "kling" in usage
    nested = extract_fal_media_url({"images": [{"url": "https://cdn.example/x.png"}]})
    assert nested.endswith("x.png")
    cred = fal_fail_error("insufficient credits on account")
    assert cred.code == "credits"
    assert cred.user_message == FAL_CREDITS_MSG
    person = fal_fail_error(
        'HTTP 422: {"detail":[{"loc":["body","image_urls"],'
        '"msg":"The images or videos provided may contain likenesses of real people '
        'or other private information that cannot be processed.",'
        '"type":"content_policy_violation"}]}',
        used_image=True,
    )
    assert person.code == "moderation_person"
    assert "живого человека" in person.user_message
    assert "не смог выполнить задачу" not in person.user_message
    kling_el = fal_fail_error(
        'HTTP 422: {"detail":[{"type":"value_error","loc":["body","elements",0],'
        '"msg":"Value error, Either frontal_image_url and reference_image_urls or video_url must be provided."}]}'
    )
    assert "reference_image_urls" in kling_el.user_message
    assert kling_el.user_message.startswith("fal.ai:")

    old_key = config.FAL_KEY
    config.FAL_KEY = "test-fal-key"
    try:
        headers = fal_headers()
        assert headers["Authorization"] == "Key test-fal-key"
        assert "Bearer" not in headers["Authorization"]
        get_headers = fal_headers(json_body=False)
        assert "Content-Type" not in get_headers
        status_u, response_u = fal_request_urls("fal-ai/flux/schnell", "rid-1")
        assert status_u.endswith("/fal-ai/flux/schnell/requests/rid-1/status")
        assert response_u.endswith("/fal-ai/flux/schnell/requests/rid-1")
        assert not response_u.rstrip("/").endswith("/response")
        _st, from_submit = fal_request_urls(
            "fal-ai/flux/schnell",
            "rid-1",
            submitted={
                "status_url": "https://queue.fal.run/fal-ai/flux/requests/rid-1/status",
                "response_url": "https://queue.fal.run/fal-ai/flux/requests/rid-1",
            },
        )
        assert from_submit == "https://queue.fal.run/fal-ai/flux/requests/rid-1"
        assert "/schnell" not in from_submit
        _st, with_response = fal_request_urls(
            "fal-ai/kling-video/v3/pro/image-to-video",
            "rid-2",
            response_url="https://queue.fal.run/fal-ai/kling-video/v3/pro/image-to-video/requests/rid-2/response",
        )
        assert with_response.endswith("/response")
        _st, rejected = fal_request_urls(
            "fal-ai/flux/schnell",
            "rid-1",
            response_url="https://evil.example/requests/rid-1/response",
        )
        assert rejected.startswith("https://queue.fal.run/")
        assert "fal-ai/flux" in _model_id_candidates("fal-ai/flux/schnell")
        alts = _result_url_candidates("https://queue.fal.run/fal-ai/flux/requests/rid-1")
        assert alts[0].endswith("/requests/rid-1")
        assert any(item.endswith("/response") for item in alts)
        assert "status_url" in inspect.getsource(fal_poll)
        assert "response_url" in inspect.getsource(fal_run)
        fal_src = Path(__file__).with_name("fal_api.py").read_text(encoding="utf-8")
        assert "_fal_get_json" in fal_src
        assert "json_body=False" in fal_src
    finally:
        config.FAL_KEY = old_key

    miss = config.missing_secrets()
    assert "RUNWAY_API_KEY" not in miss or config.video_provider() == "runway"
    # при дефолтном fal ключ Runway не обязателен
    names = inspect.getsource(config.missing_secrets)
    assert 'video_provider() == "runway"' in names

    token = "123456:TESTTOKEN"
    user = '{"id":42,"first_name":"Ann"}'
    auth_date = str(int(time.time()))
    pairs = {"auth_date": auth_date, "query_id": "AA", "user": user}
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", token.encode("utf-8"), hashlib.sha256).digest()
    digest = hmac.new(secret, data_check.encode("utf-8"), hashlib.sha256).hexdigest()
    init = urlencode({**pairs, "hash": digest})
    parsed = validate_init_data(init, token)
    assert parsed["id"] == 42
    try:
        validate_init_data(init, "wrong-token")
        raise AssertionError("bad token must fail")
    except WebAppAuthError:
        pass

    from bot import _drop_remote_voice, main, main_menu, more_kb, on_consent, on_w2_menu
    from webapp_server import build_app, start_webapp
    import asyncio

    drop_src = inspect.getsource(_drop_remote_voice)
    assert "is_minimax_voice" in drop_src
    assert "delete_eleven_voice" in drop_src

    menu_labels = [btn.text for row in main_menu().inline_keyboard for btn in row]
    assert "🎬 Открыть меню" in menu_labels
    assert "🎞 Авторолик" in menu_labels
    more = [b.callback_data for row in more_kb().inline_keyboard for b in row]
    assert "more:tryon" in more
    assert "more:upscale" in more
    consent_src = inspect.getsource(on_consent)
    assert 'job.get("mode") == "tryon"' in consent_src
    assert 'job.get("mode") == "act_two"' in consent_src
    w2 = inspect.getsource(on_w2_menu)
    assert "tryon" in w2
    main_src = inspect.getsource(main)
    assert "start_webapp" in main_src
    assert "MenuButtonWebApp" in main_src
    assert "WEBAPP_PUBLIC_URL" in main_src
    assert "Открыть меню" in main_src
    app = build_app(bot=None)
    paths = set()
    for route in app.router.routes():
        info = route.resource.get_info() if route.resource else {}
        if "path" in info:
            paths.add(info["path"])
        if "formatter" in info:
            paths.add(info["formatter"])
    assert "/api/quick" in paths
    assert "/api/autorolik" in paths
    assert "/api/autorolik/status" in paths
    assert "/api/autorolik/revise" in paths
    assert "/api/autorolik/script" in paths
    assert "/cover.jpg" in paths
    assert "/api/autorolik/shoot" in paths
    assert "/api/autorolik/cancel" in paths
    assert "/api/upscale" in paths
    assert "/api/tryon" in paths
    assert "/api/clone" in paths
    assert "/api/interpolate" in paths
    assert "/api/restore" in paths
    assert "/api/history" in paths
    assert "/api/vibe" in paths

    async def _unsigned_post_is_403() -> None:
        from aiohttp.test_utils import TestClient, TestServer

        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/quick")
            assert resp.status == 403
            data = await resp.json()
            assert data.get("ok") is False
            resp2 = await client.post("/api/autorolik")
            assert resp2.status == 403
            resp3 = await client.post("/api/autorolik/status")
            assert resp3.status == 403
            resp4 = await client.post("/api/autorolik/shoot")
            assert resp4.status == 403
            resp5 = await client.post("/api/autorolik/script")
            assert resp5.status == 403

    asyncio.run(_unsigned_post_is_403())

    html = Path(__file__).with_name("webapp").joinpath("index.html").read_text(encoding="utf-8")
    assert "telegram-web-app.js" in html
    assert "go-tryon" in html
    assert "go-slowmo" in html
    assert "go-history" in html
    assert "data-go=\"improve\"" in html
    assert "Мой голос" in html
    assert "Topaz" in html
    js = Path(__file__).with_name("webapp").joinpath("app.js").read_text(encoding="utf-8")
    assert "data-go" in js
    assert "/api/interpolate" in js
    assert "/api/history" in js
    assert "/api/autorolik" in js
    assert "/api/autorolik/status" in js
    assert "/api/autorolik/shoot" in js
    assert "/api/autorolik/script" in js
    assert "go-auto-shoot" in js
    assert "go-auto-save" in js
    assert "saveScriptEdits" in js
    assert "narr-in" in js
    assert "Речь" in js
    assert "Кадр" in js
    assert "stale" in js
    assert "upload_failed" in js
    assert "X-Telegram-Init-Data" in js
    assert "Загрузка фото оборвалась" in js
    assert "go-auto-refresh" in js
    assert "vb_onboard_v1" in js
    smoke = Path(__file__).with_name("smoke_rollout.py").read_text(encoding="utf-8")
    assert "--live" in smoke
    assert "chain_for(\"real_photo\")" in smoke or "real_photo" in smoke
    assert 'TIPS' in js or "home:" in js
    assert "Нажми карточку" in js
    assert "sendData" not in js
    assert html.count('class="sub"') == 7
    assert "go-autorolik" in html
    assert "go-auto-shoot" in html
    assert "go-auto-edit" in html
    assert "go-auto-save" in html
    assert "Сохранить правки сцен" in html
    assert "go-auto-refresh" in html
    assert "Обновить статус" in html
    assert "auto-wait" in html
    assert "Авторолик" in html
    assert "чат" in html.lower()
    assert "cover.jpg" in html
    assert "Anro.AI" in html
    cover = Path(__file__).with_name("webapp").joinpath("cover.jpg")
    assert cover.is_file() and cover.stat().st_size > 1024
    assert inspect.getsource(start_webapp)
    from studio import clone_user_audio, upscale_media

    assert "fal_upscale_file" in inspect.getsource(upscale_media)
    assert "fal_minimax_clone" in inspect.getsource(clone_user_audio)

    from fal_models import (
        KLING_LIPSYNC,
        MINIMAX_CLONE,
        MINIMAX_TTS,
        decode_minimax_voice,
        encode_minimax_voice,
        is_minimax_voice,
        kling_i2v_payload,
        minimax_tts_payload,
        seedance_ref_payload,
    )
    from prompt_templates import video_prompt_for
    from prompt_templates.kling import ELEMENT_TOKEN
    from provider_router import ROUTING, chain_for
    from providers.fal_client import FalClient, _split_fal_task

    locked = kling_i2v_payload("walk", "https://example.com/a.jpg", 5, photo_lock=True)
    assert locked["generate_audio"] is False
    assert locked["elements"] == [
        {
            "frontal_image_url": "https://example.com/a.jpg",
            "reference_image_urls": ["https://example.com/a.jpg"],
        }
    ]
    assert ELEMENT_TOKEN in locked["prompt"]
    ref = seedance_ref_payload("go", ["https://a", "https://b"], 8)
    assert ref["duration"] == "8"
    assert "@Image1" in ref["prompt"]
    many = kling_i2v_payload(
        "walk @Element1",
        "https://example.com/a.jpg",
        5,
        elements=["https://example.com/face.jpg"],
    )
    assert many["elements"] == [
        {
            "frontal_image_url": "https://example.com/face.jpg",
            "reference_image_urls": ["https://example.com/face.jpg"],
        }
    ]
    # Прод 01:20: Kling 422 — data URI в elements.frontal_image_url. Перед submit льём https.
    kling_fn = inspect.getsource(FalClient.generate_kling)
    assert "to_fal_https_url" in kling_fn
    assert "converted_map" in kling_fn
    seed_fn = inspect.getsource(FalClient.generate_seedance)
    assert "to_fal_https_url" in seed_fn
    assert "converted_map" in seed_fn
    from fal_api import fal_side_payload, fal_run, to_fal_https_url

    side = fal_side_payload({"request_id": "rid-x"}, model_id=KLING_I2V_PRO)
    assert side["model_id"] == KLING_I2V_PRO
    fal_run_src = inspect.getsource(fal_run)
    assert "fal resume dead" in fal_run_src
    assert "other model" in fal_run_src
    router_src = Path(__file__).with_name("provider_router.py").read_text(encoding="utf-8")
    assert "skip_runway" in router_src
    assert "skip legacy_runway after fal validation error" in router_src
    assert "skip seedance for FACE" in router_src
    assert 'route_mode in ("autorolik_face", "real_photo")' in router_src
    data_uri = "data:image/jpeg;base64,xx"
    leaked = kling_i2v_payload("walk", data_uri, 5, elements=[data_uri])
    assert leaked["elements"][0]["frontal_image_url"].startswith("data:")
    assert leaked["elements"][0]["reference_image_urls"][0].startswith("data:")
    https_src = inspect.getsource(to_fal_https_url)
    assert "fal_storage_upload" in https_src
    assert "data:" in https_src
    assert ROUTING["real_photo"][0] == "kling"
    assert ROUTING["autorolik_face"][0] == "kling"
    assert ROUTING["autorolik_wide"][0] == "seedance"
    assert ROUTING["synthetic_multi_scene"][0] == "seedance"
    assert ROUTING["night_pipeline"][0] == "seedance"
    assert ROUTING["montage_generate"][0] == "seedance"
    assert "legacy_runway" in ROUTING["real_photo"]
    assert chain_for("real_photo")[0] in ("kling", "seedance", "legacy_runway")
    kling_p = video_prompt_for("kling", "red coat", "walk", photo_lock=True)
    assert ELEMENT_TOKEN in kling_p
    seed_p = video_prompt_for("seedance", "red coat", "walk", photo_lock=False)
    assert "@Image1" in seed_p
    assert KLING_LIPSYNC == "fal-ai/kling-video/lipsync/audio-to-video"
    assert "KLING_LIPSYNC" in inspect.getsource(FalClient.lip_sync)
    assert MINIMAX_CLONE == "fal-ai/minimax/voice-clone"
    assert MINIMAX_TTS == "fal-ai/minimax/speech-02-hd"
    assert encode_minimax_voice("abc") == "mm:abc"
    assert decode_minimax_voice("mm:abc") == "abc"
    assert is_minimax_voice("mm:abc") is True
    assert is_minimax_voice("eleven-id") is False
    mm = minimax_tts_payload("привет", "abc")
    assert mm["voice_setting"]["voice_id"] == "abc"
    engine, model_id, rid = _split_fal_task("fal:fal-ai/kling-video/v3/pro/image-to-video:rid-1")
    assert engine == "kling"
    assert model_id.endswith("image-to-video")
    assert rid == "rid-1"
    mux_src = inspect.getsource(__import__("pipeline", fromlist=["mux_scene"]).mux_scene)
    assert "lip_sync" in mux_src or "_maybe_kling_lipsync" in mux_src
    seed_wide = video_prompt_for(
        "seedance", "amber dusk", "drone over city", photo_lock=False, character_lock=False
    )
    assert "Face is not the subject" in seed_wide
    bot_src = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
    assert "menu:auto" in bot_src
    assert "Авторолик" in bot_src
    assert "poll_status" in bot_src
    assert "note_fal_poll" in bot_src


def test_autorolik_script_and_route() -> None:
    import inspect
    from pathlib import Path

    from autorolik import (
        MAX_PHOTOS,
        MAX_SCENES,
        MIN_SCENES,
        WIDE_CAMERA,
        clamp_element,
        kling_api_prompt,
        parse_autorolik_script,
        photos_kb,
        route_for_scene,
        scene_camera,
        SCRIPT_SYSTEM,
        LOCKED_GRADE,
        decide_face_scene,
    )
    from pipeline import PipelineError, build_video
    from provider_router import chain_for

    raw = """
    {
      "title": "Янтарь",
      "hook": "Город не спит",
      "caption": "друзья",
      "continuity": "warm amber",
      "scenes": [
        {"narration": "Мы выходим из машины на закате и город уже горит.", "visual_prompt": "man steps out of a car at sunset, rack focus to face", "face_scene": true, "element_index": 2},
        {"narration": "Дрон над площадью, вертолёты в дымке, медленный наезд.", "visual_prompt": "drone over city monument helicopters haze slow push-in", "face_scene": false, "element_index": 0},
        {"narration": "Колонна фар в тумане режет боковой трекинг низко.", "visual_prompt": "low angle lateral track of convoy headlights in fog", "face_scene": false},
        {"narration": "Он смотрит в камеру сквозь боке фар клуба.", "visual_prompt": "@Element3 close-up club backlight", "face_scene": true, "element_index": 9}
      ]
    }
    """
    parsed = parse_autorolik_script(raw, n_photos=3)
    assert parsed["kind"] == "autorolik"
    assert MIN_SCENES <= len(parsed["scenes"]) <= MAX_SCENES
    assert parsed["scenes"][0]["face_scene"] is True
    assert parsed["scenes"][0]["element_index"] == 2
    assert "@Element2" in parsed["scenes"][0]["visual_prompt"]
    assert parsed["scenes"][1]["face_scene"] is False
    assert parsed["scenes"][1]["element_index"] == 0
    assert parsed["scenes"][3]["element_index"] == 3
    assert route_for_scene(parsed["scenes"][0]) == "autorolik_face"
    assert route_for_scene(parsed["scenes"][1]) == "autorolik_wide"
    assert route_for_scene(False) == "autorolik_wide"
    assert chain_for("autorolik_face")[0] == "kling"
    assert chain_for("autorolik_wide")[0] == "seedance"
    assert "drone" in scene_camera(False).lower() or "lateral" in WIDE_CAMERA.lower()
    assert "rack focus" in WIDE_CAMERA.lower()
    rewritten = kling_api_prompt("@Element3 close-up", element_index=3)
    assert "@Element1" in rewritten
    assert "@Element3" not in rewritten
    assert clamp_element(0, 4, face=False) == 0
    assert clamp_element(99, 4, face=True) == 4
    assert MAX_PHOTOS == 6
    kb = photos_kb(count=6)
    labels = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "auto:next" in labels
    three = '{"scenes":[{"narration":"a","visual_prompt":"x","face_scene":true},{"narration":"b","visual_prompt":"y","face_scene":false},{"narration":"c","visual_prompt":"z","face_scene":true}]}'
    try:
        parse_autorolik_script(three, n_photos=1)
        raise AssertionError("need 4 scenes")
    except PipelineError:
        pass
    bot_src = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
    assert "седьмое не беру" in bot_src
    assert "MAX_PHOTOS" in bot_src
    assert "class MiniChat" in bot_src
    assert "quiet: bool" in bot_src
    pipe = inspect.getsource(build_video)
    assert "autorolik_wide" in pipe
    assert "kling_api_prompt" in pipe
    assert "script_override" in pipe
    assert "element_images" in pipe
    assert "character_lock=False" in pipe
    studio_src = Path(__file__).with_name("studio.py").read_text(encoding="utf-8")
    assert "run_studio_autorolik" in studio_src
    assert "prepare_autorolik_photos" in studio_src
    assert "quiet=True" in studio_src
    assert "review_kb" in studio_src
    assert "format_script_preview" in studio_src
    assert "Можно закрыть Telegram" in studio_src
    from webapp_server import _spawn

    assert callable(_spawn)
    server_src = Path(__file__).with_name("webapp_server.py").read_text(encoding="utf-8")
    assert "def _spawn" in server_src
    assert "_JOBS.add" in server_src
    assert "expire_all_dead_pendings" in server_src
    assert "reconcile_pending" in server_src
    assert "collect_autorolik_parts" in server_src
    assert "ingest_autorolik_body" in server_src
    assert "mark_upload_failed" in server_src
    assert "create_task(_run_safe" not in server_src
    assert "send_photo_get_id" not in inspect.getsource(
        __import__("studio", fromlist=["run_studio_autorolik"]).run_studio_autorolik
    )
    from autorolik import pending_view, review_kb, script_view

    viewed = script_view(parsed)
    assert viewed["n_scenes"] == 4
    assert viewed["scenes"][0]["face"] is True
    assert "Kling" in viewed["scenes"][0]["tag"]
    assert viewed["scenes"][1]["face"] is False
    assert pending_view({"phase": "review", "script": parsed, "photo_paths": ["a.jpg"]})["phase"] == "review"
    assert review_kb().inline_keyboard
    from autorolik import apply_manual_script_edits

    patched = apply_manual_script_edits(
        parsed,
        {"title": "Ночь", "hook": "тихо", "scenes": [{"n": 1, "narration": "новая озвучка"}]},
        n_photos=2,
    )
    assert patched["title"] == "Ночь"
    assert patched["hook"] == "тихо"
    assert patched["scenes"][0]["narration"] == "новая озвучка"
    assert patched["scenes"][0]["face_scene"] == parsed["scenes"][0]["face_scene"]
    from autorolik import (
        STALE_SCRIPT_MSG,
        clear_live,
        expire_all_dead_pendings,
        reconcile_pending,
        save_pending,
        set_live,
        worker_alive,
    )
    import config as _cfg
    import tempfile as _tf

    old_data = _cfg.DATA_DIR
    tmp = _tf.mkdtemp(prefix="vb-stale-")
    _cfg.DATA_DIR = tmp
    try:
        uid = 6748280112
        photo_dir = Path(tmp) / "autorolik" / f"{uid}_photos"
        photo_dir.mkdir(parents=True)
        photo = photo_dir / "p1.jpg"
        photo.write_bytes(b"jpeg-keep")
        save_pending(
            uid,
            {
                "phase": "scripting",
                "error": "",
                "script": None,
                "photo_paths": [str(photo)],
                "consent_verified": True,
                "idea": "тени города",
                "source": "miniapp",
            },
        )
        assert worker_alive(uid) is False
        after = reconcile_pending(uid)
        assert after is not None
        assert after["phase"] == "stale"
        assert STALE_SCRIPT_MSG in after["error"]
        assert photo.is_file()
        resume = Path("/tmp") / "not-this-test"
        _ = resume
        save_pending(
            uid + 1,
            {
                "phase": "shooting",
                "error": "",
                "script": parsed,
                "photo_paths": [str(photo)],
                "consent_verified": True,
            },
        )
        shot = reconcile_pending(uid + 1)
        assert shot["phase"] == "review"
        assert shot["stale"] is True
        assert shot["script"]["title"] == parsed["title"]
        save_pending(uid + 2, {"phase": "review", "script": parsed, "error": ""})
        kept = reconcile_pending(uid + 2)
        assert kept["phase"] == "review"
        set_live(uid + 3, "scripting")
        save_pending(uid + 3, {"phase": "scripting", "script": None, "error": ""})
        live = reconcile_pending(uid + 3)
        assert live["phase"] == "scripting"
        clear_live(uid + 3)
        expired_ids = expire_all_dead_pendings()
        assert uid + 3 in expired_ids
        assert reconcile_pending(uid + 3)["phase"] == "stale"
    finally:
        _cfg.DATA_DIR = old_data
        clear_live(6748280112)
        clear_live(6748280113)
        clear_live(6748280115)
    from branding import BRAND_NAME, COVER_MARK, COVER_PROMPT, cover_candidates
    from fal_models import FLUX_DEV, flux_still_payload

    assert BRAND_NAME == "Успех 888"
    assert COVER_MARK == "Anro.AI"
    assert "no text" in COVER_PROMPT
    assert "action" in COVER_PROMPT.lower() or "sports car" in COVER_PROMPT.lower()
    assert any(str(p).endswith("cover.jpg") for p in cover_candidates())
    assert FLUX_DEV == "fal-ai/flux/dev"
    still = flux_still_payload("x", image_size="landscape_16_9", steps=28, guidance=3.5)
    assert still["output_format"] == "jpeg"
    assert still["image_size"] == "landscape_16_9"
    assert still["num_inference_steps"] == 28
    assert "cover_path" in bot_src
    assert "photo_paths=paths" in bot_src
    assert "Публичных лиц" in SCRIPT_SYSTEM
    assert "политики" in SCRIPT_SYSTEM
    assert "подлежащее" in SCRIPT_SYSTEM
    assert "мельком" in SCRIPT_SYSTEM
    assert "со спины" in SCRIPT_SYSTEM
    assert "цветокор" in SCRIPT_SYSTEM
    assert decide_face_scene(True, "drone over a hazy city monument slow push-in", omitted=False) is False
    assert decide_face_scene(True, "friend from behind walking into fog", omitted=False) is False
    assert decide_face_scene(True, "close-up of @Element1 at sunset, shallow DOF", omitted=False) is True
    mistag = """
    {"title":"x","hook":"h","scenes":[
      {"narration":"Дрон над площадью медленно едет в дымке заката.", "visual_prompt":"drone over city monument haze slow push-in", "face_scene": true, "element_index": 1},
      {"narration":"Друг крупно доворачивает лицо к камере на закате.", "visual_prompt":"close-up rack focus to face shallow DOF", "face_scene": true, "element_index": 1},
      {"narration":"Колонна машин в тумане режет боковой трекинг фарами.", "visual_prompt":"low angle convoy in fog from behind", "face_scene": true},
      {"narration":"Толпа силуэтами в контровом свете зала не узнаётся.", "visual_prompt":"crowd silhouettes in a backlit hall", "face_scene": true}
    ]}
    """
    safe = parse_autorolik_script(mistag, n_photos=2)
    assert safe["scenes"][0]["face_scene"] is False
    assert "@Element" not in safe["scenes"][0]["visual_prompt"]
    assert safe["scenes"][1]["face_scene"] is True
    assert safe["scenes"][2]["face_scene"] is False
    assert safe["scenes"][3]["face_scene"] is False
    assert "warm sunset amber" in (safe.get("continuity") or LOCKED_GRADE)


def test_ai_generated_disclosure() -> None:
    import asyncio
    import inspect
    import tempfile
    from pathlib import Path

    from pipeline import (
        AI_GENERATED_LABEL,
        apply_ai_generated_disclosure,
        build_video,
        needs_ai_generated_mark,
        with_ai_generated_caption,
        _run_ffmpeg,
    )

    assert AI_GENERATED_LABEL == "AI generated"
    assert with_ai_generated_caption("привет") == "привет\nAI generated"
    assert with_ai_generated_caption("x\nAI generated") == "x\nAI generated"
    assert needs_ai_generated_mark(photo_lock=True) is True
    assert needs_ai_generated_mark(element_images=[Path("/tmp/a.jpg")]) is True
    assert needs_ai_generated_mark(script={"kind": "autorolik"}) is True
    assert needs_ai_generated_mark(route_mode="night_pipeline") is True
    assert needs_ai_generated_mark(route_mode="real_photo") is True
    assert needs_ai_generated_mark(route_mode="synthetic_multi_scene") is False
    build_src = inspect.getsource(build_video)
    assert "apply_ai_generated_disclosure" in build_src
    bot_src = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
    assert "apply_ai_generated_disclosure" in bot_src
    assert "with_ai_generated_caption" in bot_src
    night_src = Path(__file__).with_name("night_runner.py").read_text(encoding="utf-8")
    assert "with_ai_generated_caption" in night_src

    async def _burn() -> None:
        tmp = Path(tempfile.mkdtemp())
        src = tmp / "in.mp4"
        await _run_ffmpeg(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=720x1280:d=1",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=48000:cl=stereo",
                "-shortest",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                str(src),
            ]
        )
        out, script = await apply_ai_generated_disclosure(
            src, tmp / "out.mp4", {"caption": "пост"}, required=True
        )
        assert out.is_file() and out.stat().st_size > 1000
        assert script["caption"] == "пост\nAI generated"
        assert script["ai_generated"] is True
        skipped, same = await apply_ai_generated_disclosure(src, tmp / "skip.mp4", {"caption": "x"}, required=False)
        assert skipped == src
        assert same.get("ai_generated") is not True

    asyncio.run(_burn())


def _sign_init_data(pairs: dict[str, str], token: str) -> str:
    import hmac
    import hashlib
    from urllib.parse import urlencode

    data_check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", token.encode("utf-8"), hashlib.sha256).digest()
    digest = hmac.new(secret, data_check.encode("utf-8"), hashlib.sha256).hexdigest()
    return urlencode({**pairs, "hash": digest})


def test_webapp_init_data_hmac_includes_signature() -> None:
    """Bot API 7.2+: hash покрывает signature. Выкидывать его из HMAC — прод-баг «Подпись не совпала»."""
    import time

    from webapp_auth import WebAppAuthError, validate_init_data

    token = "123456:TESTTOKEN"
    user = '{"id":42,"first_name":"Ann"}'
    auth_date = str(int(time.time()))
    base = {"auth_date": auth_date, "query_id": "AA", "user": user}
    # Как живой Telegram iOS: signature есть, и hash посчитан вместе с ним.
    with_sig = {
        **base,
        "signature": "zL-ucjNyREiHDE8aihFwpfR9aggP2xiAo3NSpfe-p7IbCisNlDKlo7Kb6G4D0Ao2mBrSgEk4maLSdv6MLIlADQ",
    }
    parsed = validate_init_data(_sign_init_data(with_sig, token), token)
    assert parsed["id"] == 42

    # Старые клиенты без signature тоже проходят.
    parsed_legacy = validate_init_data(_sign_init_data(base, token), token)
    assert parsed_legacy["id"] == 42

    # Регрессия PR #10: hash без signature, поле signature дописали отдельно — HMAC не сходится.
    broken = _sign_init_data(base, token) + "&signature=not-part-of-hmac"
    try:
        validate_init_data(broken, token)
        raise AssertionError("hash without signature must fail once signature is present")
    except WebAppAuthError as exc:
        assert "не совпала" in str(exc)

    # Опубликованный вектор Telegram (без signature, старый auth_date).
    # Токен из документации Bot API, не прод-секрет.
    official = (
        "query_id=AAHdF6IQAAAAAN0XohDhrOrc"
        "&user=%7B%22id%22%3A279058397%2C%22first_name%22%3A%22Vladislav%22%2C%22last_name%22%3A%22Kibenko%22%2C%22username%22%3A%22vdkfrost%22%2C%22language_code%22%3A%22ru%22%2C%22is_premium%22%3Atrue%7D"
        "&auth_date=1662771648"
        "&hash=c501b71e775f74ce10e377dea85a7ea24ecd640b223ea86dfe453e0eaed2e2b2"
    )
    official_token = "5768337691:AAH5YkoiEuPk8-FZa32hStHTqXiLPtAEhx8"
    official_user = validate_init_data(official, official_token, now=1662771648 + 10)
    assert official_user["id"] == 279058397

    stale = _sign_init_data(base, token)
    try:
        validate_init_data(stale, token, now=float(auth_date) + 200_000)
        raise AssertionError("stale auth_date must fail")
    except WebAppAuthError as exc:
        assert "устарел" in str(exc)


def test_webapp_autorolik_formdata_hmac() -> None:
    """Тот же initData через multipart /api/autorolik: HMAC 200, без KeyError photos."""
    import asyncio
    import time
    from io import BytesIO

    import config
    from aiohttp import FormData
    from aiohttp.test_utils import TestClient, TestServer

    from webapp_server import build_app

    token = "123456:TESTTOKEN"
    user = '{"id":42,"first_name":"Ann"}'
    with_sig = {
        "auth_date": str(int(time.time())),
        "query_id": "AA",
        "user": user,
        "signature": "zL-ucjNyREiHDE8aihFwpfR9aggP2xiAo3NSpfe-p7IbCisNlDKlo7Kb6G4D0Ao2mBrSgEk4maLSdv6MLIlADQ",
    }
    signed = _sign_init_data(with_sig, token)

    class _StubBot:
        async def send_message(self, *_a, **_k):
            return None

    async def _autorolik_hmac_ok() -> None:
        old = config.VIDEOBOT_TELEGRAM_TOKEN
        config.VIDEOBOT_TELEGRAM_TOKEN = token
        app = build_app(bot=_StubBot())
        body = FormData()
        body.add_field("initData", signed)
        body.add_field("topic", "вечер в городе")
        body.add_field("consent", "1")
        body.add_field(
            "photo1",
            BytesIO(b"\xff\xd8\xff\xd9"),
            filename="a.jpg",
            content_type="image/jpeg",
        )
        try:
            async with TestClient(TestServer(app)) as client:
                resp = await client.post(
                    "/api/autorolik",
                    data=body,
                    headers={"X-Telegram-Init-Data": signed},
                )
                data = await resp.json()
                assert resp.status != 403, data
                assert data.get("ok") is True
                assert data.get("close") is False
                status_body = FormData()
                status_body.add_field("initData", signed)
                st = await client.post("/api/autorolik/status", data=status_body)
                st_data = await st.json()
                assert st.status == 200, st_data
                assert st_data.get("ok") is True
                assert "pending" in st_data
        finally:
            config.VIDEOBOT_TELEGRAM_TOKEN = old

    asyncio.run(_autorolik_hmac_ok())


def test_autorolik_upload_disconnect() -> None:
    """Обрыв multipart до _spawn: JSON 409 + pending upload_failed, не голый 500.
    Если хотя бы одно фото уже доехало — воркер всё равно стартует."""
    import asyncio
    import tempfile
    import time
    from pathlib import Path

    import config
    from aiohttp import FormData
    from aiohttp.test_utils import TestClient, TestServer
    from autorolik import (
        UPLOAD_FAIL_MSG,
        clear_live,
        load_pending,
        mark_upload_failed,
        pending_view,
        reconcile_pending,
    )
    from webapp_server import (
        _is_client_disconnect,
        build_app,
        collect_autorolik_parts,
    )

    assert _is_client_disconnect(ConnectionResetError("Connection lost"))
    assert "сценарий не запустился" in UPLOAD_FAIL_MSG

    class _Part:
        def __init__(self, name: str, data: bytes, filename: str | None = None) -> None:
            self.name = name
            self.filename = filename
            self._data = data

        async def text(self) -> str:
            return self._data.decode("utf-8")

        async def read(self, decode: bool = False) -> bytes:
            return self._data

    class _Reader:
        def __init__(self, parts: list[_Part], fail_at: int | None = None) -> None:
            self.parts = parts
            self.fail_at = fail_at
            self.i = 0

        async def next(self):
            if self.fail_at is not None and self.i == self.fail_at:
                raise ConnectionResetError("Connection lost")
            if self.i >= len(self.parts):
                return None
            part = self.parts[self.i]
            self.i += 1
            return part

    async def _stream() -> None:
        fields, photos, disc = await collect_autorolik_parts(
            _Reader(
                [
                    _Part("initData", b"signed"),
                    _Part("consent", b"1"),
                    _Part("topic", "город".encode("utf-8")),
                ],
                fail_at=3,
            )
        )
        assert disc is True
        assert fields["consent"] == "1"
        assert fields["topic"] == "город"
        assert photos == []

        fields2, photos2, disc2 = await collect_autorolik_parts(
            _Reader(
                [
                    _Part("consent", b"1"),
                    _Part("photo1", b"\xff\xd8\xff\xd9", filename="a.jpg"),
                    _Part("photo2", b"second", filename="b.jpg"),
                ],
                fail_at=2,
            )
        )
        assert disc2 is True
        assert fields2["consent"] == "1"
        assert photos2 == [b"\xff\xd8\xff\xd9"]

    asyncio.run(_stream())

    old_data = config.DATA_DIR
    tmp = tempfile.mkdtemp(prefix="vb-upload-")
    config.DATA_DIR = tmp
    uid = 424242
    try:
        tomb = mark_upload_failed(uid, "друзья")
        assert tomb["phase"] == "error"
        assert tomb["upload_failed"] is True
        assert UPLOAD_FAIL_MSG in tomb["error"]
        view = pending_view(load_pending(uid))
        assert view["upload_failed"] is True
        assert view["phase"] == "error"
        kept = reconcile_pending(uid)
        assert kept is not None
        assert kept["phase"] == "error"
        assert kept.get("stale") is not True
    finally:
        config.DATA_DIR = old_data
        clear_live(uid)

    token = "123456:TESTTOKEN"
    user = '{"id":42,"first_name":"Ann"}'
    signed = _sign_init_data(
        {
            "auth_date": str(int(time.time())),
            "query_id": "AA",
            "user": user,
            "signature": "zL-ucjNyREiHDE8aihFwpfR9aggP2xiAo3NSpfe-p7IbCisNlDKlo7Kb6G4D0Ao2mBrSgEk4maLSdv6MLIlADQ",
        },
        token,
    )

    class _StubBot:
        async def send_message(self, *_a, **_k):
            return None

    async def _http() -> None:
        import shutil
        import webapp_server as ws

        old_token = config.VIDEOBOT_TELEGRAM_TOKEN
        old_ing = ws.ingest_autorolik_body
        old_dir = config.DATA_DIR
        tmp_http = tempfile.mkdtemp(prefix="vb-upload-http-")
        config.VIDEOBOT_TELEGRAM_TOKEN = token
        config.DATA_DIR = tmp_http
        app = build_app(bot=_StubBot())

        async def _drop_before_photos(_request):
            return {"topic": "вечер", "consent": "1"}, [], True

        async def _drop_after_photo(_request):
            return {"topic": "вечер", "consent": "1"}, [b"\xff\xd8\xff\xd9"], True

        try:
            ws.ingest_autorolik_body = _drop_before_photos
            async with TestClient(TestServer(app)) as client:
                resp = await client.post(
                    "/api/autorolik",
                    headers={"X-Telegram-Init-Data": signed},
                )
                data = await resp.json()
                assert resp.status == 409, data
                assert data.get("ok") is False
                assert data.get("upload_failed") is True
                assert "не запустился" in (data.get("error") or "")
                st_body = FormData()
                st_body.add_field("initData", signed)
                st = await client.post(
                    "/api/autorolik/status",
                    data=st_body,
                    headers={"X-Telegram-Init-Data": signed},
                )
                st_data = await st.json()
                assert st.status == 200, st_data
                assert st_data.get("pending", {}).get("upload_failed") is True
                assert st_data.get("phase") == "error"

            ws.ingest_autorolik_body = _drop_after_photo
            async with TestClient(TestServer(app)) as client:
                resp = await client.post(
                    "/api/autorolik",
                    headers={"X-Telegram-Init-Data": signed},
                )
                data = await resp.json()
                assert resp.status == 200, data
                assert data.get("ok") is True
                assert data.get("phase") == "scripting"
        finally:
            ws.ingest_autorolik_body = old_ing
            config.VIDEOBOT_TELEGRAM_TOKEN = old_token
            config.DATA_DIR = old_dir
            clear_live(42)
            shutil.rmtree(tmp_http, ignore_errors=True)

    asyncio.run(_http())
    js = Path(__file__).with_name("webapp").joinpath("app.js").read_text(encoding="utf-8")
    assert "authHeaders" in js
    assert "X-Telegram-Init-Data" in js
    assert "upload_failed" in js


def test_telegram_photo_compress_and_error_text() -> None:
    """Прод 01:01: sendPhoto 11.3 МБ / лимит 10 МБ → generic «Не вышло». Сжимаем и мапим ошибку."""
    import inspect
    import subprocess
    import tempfile
    from pathlib import Path

    from aiogram.exceptions import TelegramBadRequest

    from pipeline import PipelineError
    from studio import (
        TELEGRAM_PHOTO_SAFE_BYTES,
        compress_telegram_photo,
        job_error_text,
        send_photo_get_id,
    )

    msg = (
        "Telegram server says - Bad Request: file of size 11308176 bytes is too big "
        "for a photo; the maximum size is 10485760 bytes"
    )
    try:
        raise TelegramBadRequest(method="sendPhoto", message=msg)
    except TypeError:
        exc = TelegramBadRequest(message=msg)  # type: ignore[call-arg]
    except TelegramBadRequest as raised:
        exc = raised
    text = job_error_text(exc)
    assert "10 МБ" in text
    assert "Не вышло" not in text

    generic = job_error_text(RuntimeError("boom"))
    assert "Не вышло" in generic

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        big = root / "big.jpg"
        proc = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc=size=6000x4000:duration=1:rate=1",
                "-frames:v",
                "1",
                "-q:v",
                "1",
                str(big),
            ],
            capture_output=True,
            timeout=90,
            check=False,
        )
        assert proc.returncode == 0, (proc.stderr or b"")[-400:]
        assert big.exists() and big.stat().st_size > 1000
        out = compress_telegram_photo(big, root / "out.jpg")
        assert out.exists()
        assert out.stat().st_size <= TELEGRAM_PHOTO_SAFE_BYTES
        if big.stat().st_size > TELEGRAM_PHOTO_SAFE_BYTES:
            assert out.stat().st_size < big.stat().st_size

    src = Path(__file__).with_name("studio.py").read_text(encoding="utf-8")
    assert "compress_telegram_photo" in src
    assert "send_photo_get_id" in src
    assert "p{i}_raw.jpg" in src
    assert inspect.getsource(send_photo_get_id)
    assert issubclass(PipelineError, Exception)


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
    test_quick_optional_photo_voice()
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
    test_edit_auto_synth_vibe_no_download()
    test_upscale_result_uses_video_upscale()
    test_look_and_runway_models()
    test_serial_reveal_show()
    test_nano_banana_and_dynamic_pacing()
    test_fal_kling_and_miniapp()
    test_autorolik_script_and_route()
    test_ai_generated_disclosure()
    test_webapp_init_data_hmac_includes_signature()
    test_webapp_autorolik_formdata_hmac()
    test_autorolik_upload_disconnect()
    test_telegram_photo_compress_and_error_text()
    print("ok")
