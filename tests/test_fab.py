"""Плавающая кнопка «Связаться» справа внизу и её меню: позвонить, Telegram, WhatsApp, MAX.

Пункты — content/contacts.json → quick (network, label, url): у «Позвонить» — tel:+цифры, у мессенджеров —
https; пункт без ссылки не показывается (MAX ждёт ссылку на профиль). Кнопка одна (.fab); без пунктов
она по-прежнему открывает форму заявки. Правки — через черновик Редактора.
"""

import json
import re
from pathlib import Path

import pytest

from app import create_app
from app.content import published_quick_contacts, validate_content

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "app" / "static"
CSS = (STATIC / "css" / "style.css").read_text(encoding="utf-8")
JS = (STATIC / "js" / "main.js").read_text(encoding="utf-8")
WORKING = json.loads((ROOT / "content" / "contacts.json").read_text(encoding="utf-8"))

TEL = "tel:+79267341178"
TG = "https://t.me/+79267341178"
WA = "https://wa.me/79267341178"
MAX = "https://max.ru/u/example-profile-link"            # вымышленная ссылка — только для теста
QUICK = [
    {"network": "phone", "label": "Позвонить", "url": TEL},
    {"network": "telegram", "label": "Telegram", "url": TG},
    {"network": "whatsapp", "label": "WhatsApp", "url": WA},
    {"network": "max", "label": "MAX", "url": ""},
]


@pytest.fixture
def content_dir(make_content):
    return make_content()


def edit(content_dir, name, change):
    path = content_dir / f"{name}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def with_quick(content_dir, quick=QUICK, label="Связаться"):
    edit(content_dir, "contacts", lambda d: d.update(quick=quick))
    edit(content_dir, "site", lambda d: d.update(floating_button=label))


def html_of(content_dir, path="/"):
    return create_app({"TESTING": True, "CONTENT_DIR": str(content_dir)}).test_client().get(path).get_data(as_text=True)


def fab(html):
    return re.search(r'<details class="fab" data-fab>.*?</details>', html, re.S).group(0)


def test_working_content_has_the_requested_destinations():
    quick = {q["network"]: q for q in WORKING["quick"]}
    assert [q["network"] for q in WORKING["quick"]] == ["phone", "telegram", "whatsapp", "max"]
    assert quick["phone"]["url"] == TEL and quick["telegram"]["url"] == TG and quick["whatsapp"]["url"] == WA
    # Ссылку на профиль MAX из номера не выдумываем: пока её нет, поле пустое
    assert quick["max"]["url"] == "" and quick["max"]["label"] == "MAX"


@pytest.mark.parametrize("path", ["/", "/contacts", "/portfolio"])
def test_one_floating_button_with_a_contact_menu(content_dir, path):
    with_quick(content_dir)
    html = html_of(content_dir, path)
    assert html.count('class="fab"') == 1 and html.count("data-open-request") >= 1     # заявка — в шапке и подвале
    part = fab(html)
    assert re.search(r'<summary class="btn btn-primary fab-button" aria-controls="fab-menu" data-fab-toggle>'
                     r'<svg class="fab-icon"[^>]*aria-hidden="true"[^>]*>.*?</svg><span>Связаться</span></summary>', part)
    items = re.findall(r'<a class="fab-item[^"]*"[^>]*>', part)
    assert [re.search(r'href="([^"]+)"', a).group(1) for a in items] == [TEL, TG, WA]   # MAX без ссылки — не показан
    assert "target" not in items[0]                                                   # звонок — без новой вкладки
    for a, label in zip(items[1:], ("Telegram", "WhatsApp")):
        assert 'target="_blank" rel="noopener noreferrer"' in a and f'aria-label="{label} — откроется в новой вкладке"' in a
    assert re.findall(r"</svg><span>([^<]+)</span></a>", part) == ["Позвонить", "Telegram", "WhatsApp"]
    assert "social-link--phone" in part and "social-link--whatsapp" in part


def test_max_appears_once_its_profile_link_is_set(content_dir):
    with_quick(content_dir, [*QUICK[:3], {**QUICK[3], "url": MAX}])
    part = fab(html_of(content_dir))
    assert f'href="{MAX}"' in part and "<span>MAX</span>" in part
    assert 'id="fab-max-gradient"' in part and 'fill="url(#fab-max-gradient)"' in part


@pytest.mark.parametrize("url", ["tel:+79267341178", "http://max.ru/x", "javascript:alert(1)", "max.ru/u/abc"])
def test_wrong_max_links_are_not_published(url):
    assert published_quick_contacts([{**QUICK[3], "url": url}]) == []


def test_phone_needs_tel_and_messengers_need_https():
    assert published_quick_contacts([{**QUICK[0], "url": "+79267341178"}]) == []
    assert published_quick_contacts([{**QUICK[1], "url": "http://t.me/x"}]) == []
    assert published_quick_contacts([{"network": "icq", "label": "ICQ", "url": "https://icq.com"}]) == []
    assert [q["network"] for q in published_quick_contacts(QUICK)] == ["phone", "telegram", "whatsapp"]


def test_without_menu_items_the_button_still_opens_the_request_form(content_dir):
    with_quick(content_dir, [])
    html = html_of(content_dir)
    assert "data-fab" not in html
    assert re.search(r'<div class="fab">\s*<button class="btn btn-primary fab-button" type="button" data-open-request>'
                     r'<svg class="fab-icon".*?</svg><span>Связаться</span></button>', html, re.S)


@pytest.mark.parametrize("item, message", [
    ({"network": "max", "label": "MAX", "url": "tel:+79267341178"}, "https"),
    ({"network": "phone", "label": "Позвонить", "url": "+7 926 734-11-78"}, "tel:"),
    ({"network": "skype", "label": "Skype", "url": "https://skype.com"}, "network"),
    ({"network": "telegram", "label": "", "url": TG}, "label"),
])
def test_check_content_explains_mistakes(content_dir, item, message):
    with_quick(content_dir, [item])
    errors = validate_content(content_dir, STATIC)
    assert any("quick" in e and message in e for e in errors), errors


def test_empty_max_link_passes_check(content_dir):
    with_quick(content_dir)
    assert not [e for e in validate_content(content_dir, STATIC) if "quick" in e]


def test_max_link_is_set_through_the_editor_draft(content_dir):
    from tests.test_admin import PASSWORD_HASH, editor_versions, log_in, token_from
    with_quick(content_dir)
    app = create_app({"TESTING": True, "CONTENT_DIR": str(content_dir), "SECRET_KEY": "test-secret",
                      "ADMIN_USERNAME": "admin", "ADMIN_PASSWORD_HASH": PASSWORD_HASH})
    admin = app.test_client()
    assert log_in(admin).status_code == 302
    editor = admin.get("/admin/editor").get_data(as_text=True)
    assert 'name="quick.3.url"' in editor and "Ссылка из профиля MAX" in editor and 'name="floating_button"' in editor
    token = token_from(admin, "/admin/editor")
    bad = admin.post("/admin/editor/draft", data={"csrf_token": token, **editor_versions(admin), "quick.3.url": "tel:+79267341178"})
    assert bad.status_code == 400 and not (content_dir / ".drafts").exists()
    saved = admin.post("/admin/editor/draft", data={"csrf_token": token, **editor_versions(admin), "quick.3.url": MAX})
    assert saved.status_code == 302
    assert f'href="{MAX}"' in fab(admin.get("/admin/editor/preview").get_data(as_text=True))
    assert f'href="{MAX}"' not in html_of(content_dir)                        # до «Опубликовать» — без MAX
    assert admin.post("/admin/editor/publish", data={"csrf_token": token}).status_code == 302
    assert f'href="{MAX}"' in fab(html_of(content_dir))


def test_menu_script_closes_on_outside_click_escape_and_choice():
    block = re.search(r"function initFabMenu\(\) \{.*?\n  \}\n", JS, re.S).group(0)
    assert '"Escape"' in block and "toggle.focus()" in block and '"focusout"' in block
    assert "!fab.contains(document.activeElement)" in block
    assert "!fab.contains(event.target)" in block and "[data-fab-item]" in block and "aria-expanded" in block
    assert "initFabMenu();" in JS
    # Кнопка видна всегда: её не прячут у первого экрана, конфигуратора и подвала и не выключают под шторкой
    assert "function initFab()" not in JS and "is-hidden" not in JS
    assert 'document.querySelectorAll("main, .site-footer");' in JS


def test_button_is_always_bottom_right_large_and_leaves_room_below_content():
    block = CSS[CSS.index("/* Плавающая кнопка «Связаться» — всегда"):CSS.index("/* Шторки: портфолио, о нас, контакты */")]
    fab_rule = re.search(r"\n\.fab \{(.*?)\}", block, re.S).group(1)
    assert "position: fixed;" in fab_rule and "bottom: var(--fab-offset);" in fab_rule
    assert "right: max(var(--gutter), calc(env(safe-area-inset-right, 0px) + var(--space-3)));" in fab_rule
    assert "z-index: 46;" in fab_rule                                        # над шторкой (45), под шапкой (50)
    assert "is-hidden" not in CSS and ".has-sheet .fab" not in CSS
    # Крупнее: 56 px; отступ снизу учитывает safe-area
    assert "--fab-size: 3.5rem;" in CSS and "min-height: var(--fab-size);" in block
    assert "--fab-offset: max(var(--space-4), calc(env(safe-area-inset-bottom, 0px) + var(--space-2)));" in CSS
    # Подвал, шторки и фокус оставляют под кнопкой место — ничего не остаётся под ней насовсем
    assert "scroll-padding-bottom: var(--fab-clearance);" in CSS
    assert CSS.count("var(--fab-clearance));") >= 2                           # .site-footer и .sheet-body
    # Меню над кнопкой, у правого края и не шире экрана
    assert "bottom: calc(100% + var(--space-2));" in block and "max-width: calc(100vw - 2 * var(--gutter));" in block
    assert "min-height: var(--tap);" in block and ".fab-item:focus-visible" in block
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgb\(255", block), "цвета — только через custom properties"
