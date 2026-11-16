import json
from pathlib import Path

import pytest

from app import create_app
from app.content import contact_href, published_contacts, safe_http_url, validate_content

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "content"
STATIC = ROOT / "app" / "static"


@pytest.fixture
def content_dir(make_content):
    # Проверки фото и видео действуют только для опубликованных проектов.
    return make_content(publish_examples=True)


def edit(content_dir, name, change):
    path = content_dir / f"{name}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_repository_content_is_valid():
    assert validate_content(CONTENT, STATIC) == []


def test_repository_publishes_no_examples_or_test_uploads():
    # На сайте только реальные проекты: демонстрационные примеры и тестовая
    # загрузка test-arara остаются в портфолио черновиками.
    projects = json.loads((CONTENT / "portfolio.json").read_text(encoding="utf-8"))["projects"]

    assert [p["id"] for p in projects if p.get("example") and p["status"] == "published"] == []
    assert [p["status"] for p in projects if p["id"] == "test-arara"] in ([], ["draft"])


def test_cli_reports_ok():
    app = create_app({"TESTING": True})
    result = app.test_cli_runner().invoke(args=["check-content"])

    assert result.exit_code == 0
    assert "Контент в порядке" in result.output


def test_cli_fails_on_broken_json(content_dir):
    (content_dir / "site.json").write_text("{", encoding="utf-8")
    app = create_app({"TESTING": True, "CONTENT_DIR": str(content_dir)})

    result = app.test_cli_runner().invoke(args=["check-content"])

    assert result.exit_code != 0
    assert "site.json" in result.output


@pytest.mark.parametrize(
    ("name", "change", "message"),
    [
        ("portfolio", lambda d: d["projects"][2].update(id="example-2020-1"), "id повторяется"),
        ("portfolio", lambda d: d["projects"][1].update(status="hidden"), "status"),
        ("portfolio", lambda d: d["projects"][1].update(photos=["portfolio/missing.jpg"]), "не найден"),
        ("portfolio", lambda d: d["projects"][1].update(photos=["../site.json"]), "не картинка"),
        ("portfolio", lambda d: d["projects"][1].update(video="javascript:alert(1)"), "video"),
        ("pages", lambda d: d["pages"][0].update(slug="static"), "занят"),
        ("pages", lambda d: d["pages"][0].update(slug="Портфолио"), "slug"),
        ("pages", lambda d: d["pages"][0].update(type="gallery"), "type"),
        ("home", lambda d: d["sections"][1]["types"][0].update(walls=["front"]), "walls"),
        ("home", lambda d: d["sections"][1]["area"].update(default=1000), "area"),
        ("contacts", lambda d: d["contacts"][0].update(value="звоните"), "не подходит"),
        ("site", lambda d: d["request"].update(email="not-an-email"), "email"),
    ],
)
def test_validation_catches_mistakes(content_dir, name, change, message):
    edit(content_dir, name, change)

    errors = validate_content(content_dir, STATIC)

    assert any(message in e for e in errors), errors


def test_published_project_needs_photos(content_dir):
    edit(content_dir, "portfolio", lambda d: d["projects"][1].update(photos=[]))

    assert any("нет фотографий" in e for e in validate_content(content_dir, STATIC))


@pytest.mark.parametrize(
    ("contact", "href"),
    [
        ({"type": "phone", "value": "+7 (900) 000-00-00"}, "tel:+79000000000"),
        ({"type": "phone", "value": "звоните"}, ""),
        ({"type": "email", "value": "info@example.com"}, "mailto:info@example.com"),
        ({"type": "link", "value": "https://t.me/example"}, "https://t.me/example"),
        ({"type": "link", "value": "javascript:alert(1)"}, ""),
        ({"type": "address", "value": "Москва"}, ""),
    ],
)
def test_contact_href(contact, href):
    assert contact_href(contact) == href


def test_empty_contacts_are_hidden():
    contacts = [{"type": "phone", "label": "Телефон", "value": " "}, {"type": "text", "label": "Часы", "value": "9–18"}]

    assert published_contacts(contacts) == [{"type": "text", "label": "Часы", "value": "9–18", "href": ""}]


@pytest.mark.parametrize("url", ["javascript:alert(1)", "выаываыва", "", "https://"])
def test_safe_http_url_rejects(url):
    assert safe_http_url(url) == ""


@pytest.mark.parametrize("name, change, message", [
    ("site", lambda d: d.update(ui="Меню"), "site.json: «ui» должен быть объектом"),
    ("site", lambda d: d["request"].update(cancel=["Отмена"]), "site.json: «request.cancel» должен быть: текст"),
    ("home", lambda d: d["hero"].update(lead=1), "home.json: «hero.lead» должен быть: текст"),
    ("home", lambda d: d["sections"][1].update(summary_text=["Бар"]), "home.json: «sections.1.summary_text» должен быть: текст"),
    ("home", lambda d: d["sections"][0]["items"][1].update(title=7), "home.json: «sections.0.items.1.title» должен быть: текст"),
    ("pages", lambda d: d["pages"][1].update(paragraphs=["a", 2]), "pages.json: «pages.1.paragraphs» должен быть: список строк"),
    ("contacts", lambda d: d["contacts"][0].update(label=5), "contacts.json: «contacts.0.label» должен быть: текст"),
])
def test_text_fields_must_keep_their_type(content_dir, name, change, message):
    edit(content_dir, name, change)

    assert any(message in error for error in validate_content(content_dir, STATIC))


@pytest.mark.parametrize("item, message", [
    ({"project": "nope"}, "проекта «nope» нет"),
    ({"image": "home/missing.webp"}, "не найден"),
    ({"image": "../site.json"}, "не картинка"),
    ({"image": "/etc/x.jpg"}, "относительным"),
])
def test_project_cards_are_validated(content_dir, item, message):
    edit(content_dir, "home", lambda d: next(s for s in d["sections"] if s["type"] == "projects").update(items=[item]))

    assert any(message in e for e in validate_content(content_dir, STATIC))


def test_hero_secondary_target_must_be_a_block(content_dir):
    edit(content_dir, "home", lambda d: d["hero"].update(secondary_target="nope"))

    assert any("hero.secondary_target" in e for e in validate_content(content_dir, STATIC))


def test_check_content_reminds_about_placeholder_cards(content_dir, monkeypatch):
    edit(content_dir, "home", lambda d: next(s for s in d["sections"] if s["type"] == "projects").update(items=[{}, {}]))
    monkeypatch.setenv("CRVU_CONTENT_DIR", str(content_dir))

    result = create_app().test_cli_runner().invoke(args=["check-content"])

    assert result.exit_code == 0
    assert "карточка 1 без фото" in result.output and "карточка 2 без фото" in result.output


def test_hero_secondary_action_is_validated(content_dir):
    edit(content_dir, "home", lambda d: d["hero"].update(secondary_action="call"))

    assert any("hero.secondary_action" in e for e in validate_content(content_dir, STATIC))


def _pricing(d):
    return next(s for s in d["sections"] if s.get("type") == "configurator")["pricing"]


@pytest.mark.parametrize("change, message", [
    (lambda p: p["rate"]["large"].update(rate_min=80000), "не может расти"),
    (lambda p: p["rate"]["large"].update(rate_max=30000), "rate_max"),
    (lambda p: p["rate"]["large"].update(area=5), "large.area"),
    (lambda p: p["rate"]["small"].update(rate_min="много"), "rate.small.rate_min"),
    (lambda p: p.update(levels=[]), "levels"),
    (lambda p: p["levels"][1].update(id="standard"), "повторяется"),
    (lambda p: p["levels"][0].update(factor=0), "factor"),
    (lambda p: p["extras"][0].update(kind="magic"), "kind"),
    (lambda p: p["extras"][2].update(min=500000), "min"),
    (lambda p: p["open_sides"].update({"2": -1}), "open_sides"),
    (lambda p: p.update(round=0), "round"),
])
def test_pricing_mistakes_are_caught(content_dir, change, message):
    edit(content_dir, "home", lambda d: change(_pricing(d)))

    errors = validate_content(content_dir, STATIC)

    assert any("pricing" in e and message in e for e in errors), errors


def test_configurator_without_pricing_is_still_valid(content_dir):
    edit(content_dir, "home", lambda d: next(s for s in d["sections"] if s.get("type") == "configurator").pop("pricing"))

    assert validate_content(content_dir, STATIC) == []
