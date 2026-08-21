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
from voices import VOICES, voice_by_index


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
    print("ok")
