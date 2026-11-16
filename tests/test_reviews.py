"""Блок «Нам доверяют свои проекты»: три заглушки будущих видеоотзывов перед конфигуратором."""

import json
import re
from pathlib import Path

import pytest

from app import create_app
from app.content import validate_data

ROOT = Path(__file__).resolve().parent.parent
STYLE = ROOT / "app" / "static" / "css" / "style.css"
WORKING_HOME = ROOT / "content" / "home.json"

REVIEWS = {
    "type": "reviews", "id": "reviews", "visible": True,
    "title": "Нам доверяют свои проекты",
    "lead": "Рассказываем не мы — рассказывают наши клиенты.",
    "placeholder": "Скоро здесь будет видео",
    "caption": "Видеоотзыв клиента",
    "items": [{}, {}, {}],
    "cta_text": "Планируете выставку? Давайте обсудим ваш проект.",
    "cta": "Обсудить мой проект →",
    "cta_target": "configurator",
}


def with_reviews(content_dir, section=REVIEWS):
    """Вставить блок отзывов в копию тестового контента — сразу перед конфигуратором."""
    path = content_dir / "home.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    sections = data["sections"]
    at = next(i for i, s in enumerate(sections) if s["type"] == "configurator")
    sections.insert(at, dict(section))
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def home_html(content_dir):
    app = create_app({"TESTING": True, "CONTENT_DIR": str(content_dir)})
    return app.test_client().get("/").get_data(as_text=True)


def reviews_html(html):
    return re.search(r'<section class="section reviews.*?</section>', html, re.S).group(0)


def test_working_home_order_puts_configurator_right_after_projects():
    types = [s["type"] for s in json.loads(WORKING_HOME.read_text(encoding="utf-8"))["sections"]]

    # Сразу после работ — конструктор стенда; остальные блоки — в прежнем порядке, каждый по одному разу;
    # «Частые вопросы» — последним, перед подвалом
    # «Полный цикл застройки» удалён: после «Нам доверяют» — сразу «Частые вопросы»
    assert types == ["projects", "configurator", "about", "reviews", "faq"]


def test_working_reviews_have_only_placeholders_or_library_videos_without_invented_clients():
    section = next(s for s in json.loads(WORKING_HOME.read_text(encoding="utf-8"))["sections"]
                   if s["type"] == "reviews")
    library = WORKING_HOME.parent / "videos.json"
    ids = {v["id"] for v in json.loads(library.read_text(encoding="utf-8"))["videos"]} if library.exists() else set()

    assert section["title"] == "Нам доверяют свои проекты"
    assert section["lead"] == "Рассказываем не мы — рассказывают наши клиенты."
    assert section["placeholder"] == "Скоро здесь будет видео"
    assert len(section["items"]) == 3
    # Ни имён, ни компаний, ни цитат — на позиции заглушка или ролик из библиотеки (выбран в Редакторе)
    for item in section["items"]:
        assert set(item) <= {"_note", "video"}, item
        assert "video" not in item or item["video"] in ids, item


def test_reviews_render_three_static_placeholders(make_content):
    content_dir = make_content()
    with_reviews(content_dir)
    html = home_html(content_dir)
    block = reviews_html(html)

    assert html.index('id="projects"') < html.index('id="reviews"') < html.index('id="configurator"')
    assert 'class="section reviews is-paper"' in block
    assert "Нам доверяют свои проекты" in block and "Рассказываем не мы — рассказывают наши клиенты." in block
    assert block.count('class="review-card"') == 3
    assert block.count("Скоро здесь будет видео") == 3
    assert block.count("Видеоотзыв клиента") == 3
    # Заглушка не притворяется плеером: ни кнопок, ни видео, ни встроенных плееров, ни картинок людей
    for tag in ("<button", "<video", "<iframe", "<img", "data-gallery", "tabindex"):
        assert tag not in block, tag
    # Значок воспроизведения — только оформление
    assert re.search(r'<svg class="review-play"[^>]*aria-hidden="true"', block)


def test_reviews_cta_leads_to_existing_configurator(make_content):
    content_dir = make_content()
    with_reviews(content_dir)
    block = reviews_html(home_html(content_dir))

    assert "Планируете выставку? Давайте обсудим ваш проект." in block
    assert re.search(r'<a class="btn btn-quiet" href="#configurator"[^>]*>Обсудить мой проект →</a>', block)
    assert "<form" not in block


def test_reviews_cta_is_hidden_when_target_is_missing(make_content):
    content_dir = make_content()
    with_reviews(content_dir, {**REVIEWS, "cta_target": "nowhere"})
    block = reviews_html(home_html(content_dir))

    assert "Обсудить мой проект" not in block and "href=\"#" not in block


def test_hidden_reviews_are_not_rendered(make_content):
    content_dir = make_content()
    with_reviews(content_dir, {**REVIEWS, "visible": False})

    assert 'id="reviews"' not in home_html(content_dir)


@pytest.mark.parametrize("change, message", [
    (lambda s: s.update(items=[{}] * 7), "не больше 6"),
    (lambda s: s.update(items="три"), "не больше 6"),
    (lambda s: s.update(items=[{}, "видео"]), "карточка 2"),
])
def test_reviews_validation(make_content, change, message):
    content_dir = make_content()
    data = {"home": with_reviews(content_dir)}
    section = next(s for s in data["home"]["sections"] if s["type"] == "reviews")
    change(section)
    for name in ("site", "pages", "contacts", "portfolio"):
        data[name] = json.loads((content_dir / f"{name}.json").read_text(encoding="utf-8"))

    errors = validate_data(data, content_dir, ROOT / "app" / "static")
    assert any(message in e for e in errors), errors


def test_reviews_paper_reuses_sheer_rules_without_gradients():
    css = STYLE.read_text(encoding="utf-8")

    # Та же полупрозрачная бумага, что у «Реализовано» и конфигуратора — общим правилом, а не копией
    assert ".section.projects.is-paper,\n.section.reviews.is-paper,\n.section.configurator.is-paper {\n" \
           "  background: rgb(var(--paper-rgb) / var(--paper-sheer-alpha));" in css
    assert "  .section.projects.is-paper,\n  .section.reviews.is-paper,\n  .section.configurator.is-paper {\n" \
           "    --paper-sheer-alpha: 1;" in css
    assert css.count("rgb(var(--paper-rgb) / var(--paper-sheer-alpha))") == 1
    # Карточки — сплошные, без градиентов и теней
    rules = re.findall(r"^\.review[\w-]*[^{]*\{(.*?)^\}", css, re.S | re.M)
    assert rules
    for body in rules:
        assert "gradient" not in body and "box-shadow" not in body
    # Три в ряд на компьютере, две на планшете, одна на телефоне
    assert re.search(r"@media \(min-width: 640px\) \{\n  \.review-grid \{\n    grid-template-columns: repeat\(2,", css)
    assert re.search(r"@media \(min-width: 1024px\) \{\n  \.review-grid \{\n    grid-template-columns: repeat\(3,", css)
    assert "aspect-ratio: 16 / 9" in re.search(r"^\.review-media \{(.*?)^\}", css, re.S | re.M).group(1)
