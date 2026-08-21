"""Офлайн-проверка разбора сценария и unwrap Grok-ключа. Без сети."""

from config import unwrap_xai_api_key
from pipeline import (
    format_script,
    parse_script,
    pick_clip_duration,
    ratio_wh,
    runway_duration,
    runway_poll_delay,
    runway_prompt_text,
    scene_durations,
    wrap_caption,
)


def test_plain_json() -> None:
    raw = '{"title": "Дождь", "scenes": [{"narration": "Капли стучат по стеклу", "visual_prompt": "Rain on a window, night city bokeh"}]}'
    data = parse_script(raw)
    assert data["title"] == "Дождь"
    assert len(data["scenes"]) == 1
    assert "Rain" in data["scenes"][0]["visual_prompt"]


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
        {"title": "Тест", "scenes": [{"narration": "Привет мир", "visual_prompt": "x"}]}
    )
    assert "Тест" in text
    assert "Привет мир" in text


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
    print("ok")
