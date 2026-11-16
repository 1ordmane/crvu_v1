"""«О нас» на главной: три главные цифры крупно, год основания и остальные — мелкой строкой.

Данные — раздел type "about" в pages.json (их правит «Редактор»); блок type "about" в home.json
задаёт только место. Пока блок есть, у раздела нет своей шторки, меню и /about ведут к блоку.
"""

import json
import re

import pytest

from tests.test_app import edit, make_client


def add_about_block(d, visible=True):
    at = next(i for i, s in enumerate(d["sections"]) if s["type"] == "projects") + 1
    d["sections"].insert(at, {"type": "about", "id": "company", "visible": visible})


@pytest.fixture
def content_dir(make_content):
    content_dir = make_content()
    edit(content_dir, "home", add_about_block)
    return content_dir


def about_page(content_dir):
    pages = json.loads((content_dir / "pages.json").read_text(encoding="utf-8"))["pages"]
    return next(p for p in pages if p["type"] == "about")


def block(html):
    return re.search(r'<section class="section about is-paper" id="company".*?</section>', html, re.S).group(0)


def test_stats_are_on_home_right_after_projects(content_dir):
    html = make_client(content_dir).get("/").get_data(as_text=True)

    ids = re.findall(r'<section class="section [^"]*" id="([\w-]+)"', html.split("</main>")[0])
    assert ids[ids.index("projects") + 1] == "company"
    # Без заголовка: имя блока для экранных дикторов — название раздела
    assert re.search(r'<section class="section about is-paper" id="company" aria-label="О нас">', html)
    assert 'company-title' not in html and "<h2" not in block(html)


def test_three_primary_numbers_and_small_secondary_line(content_dir):
    page = about_page(content_dir)
    about = block(make_client(content_dir).get("/").get_data(as_text=True))

    primary = re.findall(r'<li class="stat"[^>]*>.*?</li>', about, re.S)
    assert len(primary) == 3
    for stat, item in zip(page["stats"][:3], primary):
        number = "{:,}".format(stat["value"]).replace(",", " ")
        # Число — фирменным цветом, «+» — отдельно, цветом текста
        assert f'<span class="stat-number" data-count="{stat["value"]}">{number}</span><span class="stat-suffix">+</span>' in item
        assert stat["label"] in item
    # Год основания и четвёртая цифра — не крупные цифры, а строка под ними
    more = re.search(r'<div class="stats-more"[^>]*>.*?</div>', about, re.S).group(0)
    assert f"Работаем с {page['founded_year']} года" in more
    assert page["stats"][3]["label"] in more and 'class="stat"' not in more
    assert "since-year" not in about and "data-count" not in more


def test_about_has_no_sheet_and_menu_and_old_address_lead_to_the_block(content_dir):
    client = make_client(content_dir)
    html = client.get("/").get_data(as_text=True)

    assert html.count('class="stats"') == 1      # цифры — один раз
    assert not re.search(r'<section class="sheet[^"]*" id="about"', html)
    nav = re.search(r'<nav class="site-nav".*?</nav>', html, re.S).group(0)
    # «О нас» в меню нет: блок на главной, отдельного пункта не нужно
    assert "О нас" not in nav and "#company" not in nav and 'data-route="about"' not in nav
    assert re.findall(r'data-route="(\w+)"', nav) == ["home", "portfolio", "contacts"]
    response = client.get("/about")
    assert response.status_code == 302 and response.headers["Location"] == "/#company"
    # Остальные разделы — по-прежнему шторки
    assert client.get("/portfolio").status_code == 200 and 'data-route="portfolio"' in nav


def test_hidden_block_brings_the_sheet_back(content_dir):
    edit(content_dir, "home", lambda d: next(s for s in d["sections"] if s["type"] == "about").update(visible=False))
    client = make_client(content_dir)
    html = client.get("/").get_data(as_text=True)

    assert 'class="section about' not in html
    assert re.search(r'<section class="sheet[^"]*" id="about"', html) and 'data-route="about"' in html
    sheet = client.get("/about")
    assert sheet.status_code == 200 and sheet.get_data(as_text=True).count('class="stat"') == 3


def test_hidden_about_page_hides_the_block(content_dir):
    def hide(d):
        next(p for p in d["pages"] if p["type"] == "about")["visible"] = False
    edit(content_dir, "pages", hide)
    client = make_client(content_dir)

    assert 'class="section about' not in client.get("/").get_data(as_text=True)
    assert client.get("/about").status_code == 404


def test_check_content_accepts_the_about_block(content_dir):
    from app import create_app
    from app.content import validate_content
    static = create_app({"TESTING": True, "CONTENT_DIR": str(content_dir)}).static_folder
    assert validate_content(content_dir, static) == []


def test_numbers_are_edited_in_the_editor_and_marked_in_its_preview(content_dir):
    from werkzeug.security import generate_password_hash
    from app import create_app
    from tests.test_editor import PASSWORD, TOKEN_RE, post, save_draft

    app = create_app({"TESTING": True, "CONTENT_DIR": str(content_dir), "SECRET_KEY": "test-secret",
                      "ADMIN_USERNAME": "admin", "ADMIN_PASSWORD_HASH": generate_password_hash(PASSWORD)})
    admin = app.test_client()
    token = TOKEN_RE.search(admin.get("/admin/login").get_data(as_text=True)).group(1)
    admin.post("/admin/login", data={"csrf_token": token, "username": "admin", "password": PASSWORD})
    pages = json.loads((content_dir / "pages.json").read_text(encoding="utf-8"))["pages"]
    at = next(i for i, p in enumerate(pages) if p["type"] == "about")

    preview = admin.get("/admin/editor/preview").get_data(as_text=True)
    for name in (f"pages.{at}.stats.0.value", f"pages.{at}.stats.2.label", f"pages.{at}.since_text"):
        assert f'data-edit="{name}"' in preview, name

    assert save_draft(admin, **{f"pages.{at}.stats.0.value": "1450"}).status_code == 302
    assert post(admin, "/admin/editor/publish").status_code == 302
    assert 'data-count="1450">1 450</span>' in block(app.test_client().get("/").get_data(as_text=True))
