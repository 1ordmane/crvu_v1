"""Соцсети: блок «Мы в соцсетях и мессенджерах» в разделе «Контакты» и строка «Мы в соцсетях» в подвале.

Ссылки — в content/contacts.json (socials: network, label, url, short — подпись в подвале, по умолчанию
ТГ, МАХ, ВК), заголовки — в pages.json (раздел contacts, socials_title) и site.json (footer.socials_title).
Ссылка открывается в новой вкладке; значок выбирается по network. Правки — через черновик Редактора.
"""

import json
import re
from pathlib import Path

import pytest

from app import create_app
from app.content import published_socials, validate_content

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "app" / "static"
CSS = (STATIC / "css" / "style.css").read_text(encoding="utf-8")
WORKING = json.loads((ROOT / "content" / "contacts.json").read_text(encoding="utf-8"))
WORKING_PAGES = json.loads((ROOT / "content" / "pages.json").read_text(encoding="utf-8"))

TELEGRAM = "https://t.me/crvu_ru"
MAX = "https://max.ru/join/gS7x1LH_gv79ZiVED96E272hGNYkaW0REvIzXyJGoW8"
VK = "https://vk.ru/crvu_stends"
SOCIALS = [
    {"network": "telegram", "label": "Telegram", "url": TELEGRAM},
    {"network": "max", "label": "MAX", "url": MAX},
    {"network": "vk", "label": "ВКонтакте", "url": VK},
]


@pytest.fixture
def content_dir(make_content):
    return make_content()


def edit(content_dir, name, change):
    path = content_dir / f"{name}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def with_socials(content_dir, socials=SOCIALS):
    edit(content_dir, "contacts", lambda d: d.update(socials=socials))


def html_of(content_dir, path="/contacts"):
    return create_app({"TESTING": True, "CONTENT_DIR": str(content_dir)}).test_client().get(path).get_data(as_text=True)


def sheet(html):
    return html.split('id="contacts"', 1)[1].split("</section>", 1)[0]


def links(part):
    return re.findall(r'<a class="social-link[^"]*"[^>]*>', part)


def test_working_content_has_the_three_exact_links():
    assert [(s["network"], s["label"], s["url"]) for s in WORKING["socials"]] == \
           [(s["network"], s["label"], s["url"]) for s in SOCIALS]
    contacts_page = next(p for p in WORKING_PAGES["pages"] if p["type"] == "contacts")
    assert contacts_page["socials_title"] == "Мы в соцсетях и мессенджерах"


def test_block_follows_contact_list_in_contacts_sheet(content_dir):
    with_socials(content_dir)
    edit(content_dir, "contacts", lambda d: d["contacts"][0].update(value="+7 (495) 106-70-22"))
    part = sheet(html_of(content_dir))

    assert part.index('class="contact-list"') < part.index('class="socials"')
    assert re.search(r'<h3 class="socials-title" id="socials-title">Мы в соцсетях и мессенджерах</h3>', part)
    assert 'aria-labelledby="socials-title"' in part
    found = links(part)
    assert len(found) == 3
    for tag, social in zip(found, SOCIALS):
        assert f'href="{social["url"]}"' in tag
        assert 'target="_blank"' in tag and 'rel="noopener noreferrer"' in tag
        assert f'aria-label="{social["label"]} — откроется в новой вкладке"' in tag
        assert f"social-link--{social['network']}" in tag
    for social in SOCIALS:
        assert f'<span class="social-name">{social["label"]}</span>' in part
    # значок — декоративный: имя ссылки даёт подпись
    assert part.count('class="social-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"') == 3


def footer(html):
    return html.split('<footer class="site-footer">', 1)[1].split("</footer>", 1)[0]


@pytest.mark.parametrize("path", ["/", "/contacts", "/portfolio"])
def test_footer_has_the_three_links_with_short_labels_on_every_page(content_dir, path):
    with_socials(content_dir)
    html = html_of(content_dir, path)
    part = footer(html)
    nav = re.search(r'<nav class="footer-socials" aria-labelledby="footer-socials-title">.*?</nav>', part, re.S).group(0)
    assert '<p class="footer-socials-title" id="footer-socials-title">Мы в соцсетях</p>' in nav
    tags = re.findall(r'<a class="footer-social[^"]*"[^>]*>', nav)
    assert [re.search(r'href="([^"]+)"', t).group(1) for t in tags] == [TELEGRAM, MAX, VK]
    for tag, social in zip(tags, SOCIALS):
        assert 'target="_blank" rel="noopener noreferrer"' in tag
        assert f'aria-label="{social["label"]} — откроется в новой вкладке"' in tag
    assert re.findall(r"</svg><span>([^<]+)</span></a>", nav) == ["ТГ", "МАХ", "ВК"]
    # Адреса не дублируются в настройках: блок в «Контактах» — по одному разу, подвал — те же ссылки
    assert html.count('class="socials"') == 1 and html.count(f'href="{MAX}"') == 2
    # Градиент значка MAX — свой id на каждом месте страницы
    ids = re.findall(r'<linearGradient id="([^"]+)"', html)
    assert sorted(ids) == ["footer-max-gradient", "social-max-gradient"]
    assert 'fill="url(#footer-max-gradient)"' in nav


def test_no_socials_no_footer_block(content_dir):
    with_socials(content_dir, [])
    assert "footer-socials" not in footer(html_of(content_dir, "/"))


def test_footer_texts_go_through_the_editor_draft(content_dir):
    from tests.test_admin import PASSWORD_HASH, editor_versions, log_in, token_from
    with_socials(content_dir)
    app = create_app({"TESTING": True, "CONTENT_DIR": str(content_dir), "SECRET_KEY": "test-secret",
                      "ADMIN_USERNAME": "admin", "ADMIN_PASSWORD_HASH": PASSWORD_HASH})
    admin = app.test_client()
    assert log_in(admin).status_code == 302
    editor = admin.get("/admin/editor").get_data(as_text=True)
    assert 'name="socials.1.short"' in editor and 'name="footer.socials_title"' in editor
    assert "Пусто — «МАХ»" in editor
    token = token_from(admin, "/admin/editor")
    saved = admin.post("/admin/editor/draft", data={"csrf_token": token, **editor_versions(admin),
                                                     "socials.1.short": "MAX", "footer.socials_title": "Соцсети"})
    assert saved.status_code == 302
    preview = footer(admin.get("/admin/editor/preview").get_data(as_text=True))
    assert "<span>MAX</span>" in preview and ">Соцсети</p>" in preview
    public = footer(html_of(content_dir, "/"))
    assert "<span>МАХ</span>" in public and ">Мы в соцсетях</p>" in public       # до публикации — прежнее
    assert admin.post("/admin/editor/publish", data={"csrf_token": token}).status_code == 302
    public = footer(html_of(content_dir, "/"))
    assert "<span>MAX</span>" in public and ">Соцсети</p>" in public
    assert json.loads((content_dir / "contacts.json").read_text(encoding="utf-8"))["socials"][1]["url"] == MAX


def test_footer_styles_are_tokens_focus_and_tap_size():
    block = CSS[CSS.index("/* «Мы в соцсетях»"):CSS.index(".footer-copy {")]
    assert "flex-wrap: wrap" in block and ".footer-social:focus-visible" in block and "outline" in block
    assert "min-height: var(--tap-min)" in block
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgb\(", block), "цвета — только через custom properties"


def test_contact_button_stays_and_block_shows_even_without_contacts(content_dir):
    with_socials(content_dir)
    part = sheet(html_of(content_dir))
    assert "data-open-request>Связаться с менеджером</button>" in part
    assert part.index("data-open-request") < part.index('class="socials"')
    assert len(links(part)) == 3


def test_no_socials_no_block(content_dir):
    html = html_of(content_dir)
    assert 'class="socials"' not in html and "social-link" not in html


@pytest.mark.parametrize("item", [
    {"network": "telegram", "label": "Telegram", "url": ""},
    {"network": "telegram", "label": "Telegram", "url": "javascript:alert(1)"},
    {"network": "icq", "label": "ICQ", "url": "https://icq.com/x"},
    {"network": "vk", "label": "", "url": VK},
    "не объект",
])
def test_broken_items_are_not_published(item):
    assert published_socials([item]) == []


def test_published_socials_keep_order_and_trim():
    result = published_socials([{"network": "vk", "label": " ВКонтакте ", "url": f" {VK} "}, SOCIALS[0]])
    assert result == [{"network": "vk", "label": "ВКонтакте", "url": VK, "short": "ВК"},
                      {"network": "telegram", "label": "Telegram", "url": TELEGRAM, "short": "ТГ"}]
    own = published_socials([{**SOCIALS[1], "short": "  Макс  "}])
    assert own[0]["short"] == "Макс"


def test_labels_are_escaped(content_dir):
    with_socials(content_dir, [{"network": "vk", "label": "<b>VK</b>", "url": VK}])
    html = html_of(content_dir)
    assert "<b>VK</b>" not in html and "&lt;b&gt;VK&lt;/b&gt;" in html


@pytest.mark.parametrize("item, message", [
    ({"network": "icq", "label": "ICQ", "url": "https://icq.com/x"}, "network"),
    ({"network": "vk", "label": "ВКонтакте", "url": "vk.ru/crvu"}, "http"),
    ({"network": "vk", "label": "", "url": VK}, "label"),
])
def test_check_content_catches_mistakes(content_dir, item, message):
    with_socials(content_dir, [item])
    errors = validate_content(content_dir, STATIC)
    assert any("socials" in e and message in e for e in errors), errors


def test_valid_socials_pass_check(content_dir):
    with_socials(content_dir)
    assert not [e for e in validate_content(content_dir, STATIC) if "socials" in e]


def test_styles_use_tokens_focus_and_wrap():
    block = CSS[CSS.index("/* Соцсети и мессенджеры"):CSS.index("/* Диалоги */")]
    assert "flex-wrap: wrap" in block
    assert ".social-link:focus-visible" in block and "outline" in block
    assert "min-height: 2.75rem" in block          # удобно попасть пальцем
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgb\(", block), "цвета — только через custom properties"
