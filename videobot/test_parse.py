"""Офлайн-проверка разбора сценария. Без сети и секретов."""

from pipeline import parse_script, scene_durations


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
    assert scene_durations(3) == [5, 5, 5]


def test_visual_prompt_alias() -> None:
    raw = '{"scenes": [{"narration": "hello there friend", "visualPrompt": "A red bicycle in the sun"}]}'
    data = parse_script(raw)
    assert data["scenes"][0]["visual_prompt"].startswith("A red")


if __name__ == "__main__":
    test_plain_json()
    test_fenced_and_extra()
    test_visual_prompt_alias()
    print("ok")
