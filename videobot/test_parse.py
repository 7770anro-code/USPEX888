"""Офлайн-проверка разбора сценария и unwrap Grok-ключа. Без сети."""

from config import unwrap_xai_api_key
from pipeline import (
    RUNWAY_PERSON_MSG,
    RUNWAY_PROMPT_MAX,
    compose_runway_prompt,
    fallback_split_script,
    format_script,
    is_runway_person_moderation,
    is_runway_safety_fail,
    parse_script,
    pick_clip_duration,
    ratio_wh,
    runway_content_moderation,
    runway_duration,
    runway_fail_error,
    runway_poll_delay,
    runway_prompt_text,
    scene_durations,
    target_scene_count,
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
    lock = "25yo woman, short black hair, green parka, neon alley, cinematic grain"
    a = compose_runway_prompt(lock, "slow push-in, she looks at camera")
    b = compose_runway_prompt(lock, "handheld pan left, she walks")
    assert lock in a and lock in b
    assert "LOCKED LOOK" in a
    assert len(a) <= RUNWAY_PROMPT_MAX
    assert "push-in" in a
    assert "pan left" in b


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
    text_err = runway_fail_error("SAFETY.INPUT.TEXT", "prompt blocked")
    assert text_err.code == "moderation"
    assert runway_content_moderation()["publicFigureThreshold"] == "auto"


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
    print("ok")
