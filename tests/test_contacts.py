"""Блок контактов: раздел «Контакты» и подвал берут данные из одного места — content/contacts.json.

Почта — обычный текст (его можно выделить и скопировать Cmd/Ctrl+C), рядом кнопка «скопировать»
и отдельная ссылка mailto. Кнопка без JS скрыта: копирование — дополнение, а не единственный способ.
"""

import json
import re
from pathlib import Path

import pytest

from app import create_app
from app.content import published_contacts

ROOT = Path(__file__).resolve().parent.parent
MAIN_JS = (ROOT / "app" / "static" / "js" / "main.js").read_text(encoding="utf-8")
CSS = (ROOT / "app" / "static" / "css" / "style.css").read_text(encoding="utf-8")
WORKING = json.loads((ROOT / "content" / "contacts.json").read_text(encoding="utf-8"))


@pytest.fixture
def content_dir(make_content):
    return make_content()


def set_contacts(content_dir, values, extra=()):
    path = content_dir / "contacts.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for c in data["contacts"]:
        c["value"] = values.get(c["type"], c["value"])
    data["contacts"].extend(extra)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def page(content_dir, path="/contacts"):
    return create_app({"TESTING": True, "CONTENT_DIR": str(content_dir)}).test_client().get(path)


def footer(html):
    return html.split('<footer class="site-footer">', 1)[1].split("</footer>", 1)[0]


def sheet(html):
    return html.split('id="contacts"', 1)[1].split("</section>", 1)[0]


FILLED = {"phone": "+7 (495) 106-70-22", "email": "info@crvu.ru",
          "address": "123007, г. Москва, 2-й Хорошёвский пр-д, дом 7, кор. 1"}
HOURS = ({"type": "text", "label": "Время работы", "value": "Ежедневно с 9:30 до 18:30"},)


def test_working_contacts_are_filled_and_email_is_set_once():
    by_type = {c["type"]: c["value"] for c in WORKING["contacts"] if c["value"]}
    assert by_type["phone"] == "+7 (495) 106-70-22"
    assert by_type["email"] == "info@crvu.ru"
    assert by_type["address"] == "123007, г. Москва, 2-й Хорошёвский пр-д, дом 7, кор. 1"
    assert any(c["value"] == "Ежедневно с 9:30 до 18:30" for c in WORKING["contacts"])
    # адрес почты задан ровно в одном месте рабочего контента
    assert [c["type"] for c in WORKING["contacts"]].count("email") == 1


def test_published_contacts_keep_type():
    result = published_contacts([{"type": "email", "label": "Email", "value": " a@b.ru "}])
    assert result == [{"type": "email", "label": "Email", "value": "a@b.ru", "href": "mailto:a@b.ru"}]


def test_contacts_shown_in_sheet_and_footer(content_dir):
    set_contacts(content_dir, FILLED, HOURS)
    html = page(content_dir).get_data(as_text=True)

    for part in (sheet(html), footer(html)):
        assert 'href="tel:+74951067022"' in part
        assert "123007, г. Москва, 2-й Хорошёвский пр-д, дом 7, кор. 1" in part
        assert "Ежедневно с 9:30 до 18:30" in part
        # почта — видимый текст, отдельный от ссылки, без лишних символов вокруг
        assert re.search(r'<span class="copy-value" data-copy-source>info@crvu\.ru</span>', part)
        assert 'href="mailto:info@crvu.ru"' in part
    assert "скоро появятся" not in sheet(html)


def test_copy_button_is_accessible_and_hidden_without_js(content_dir):
    set_contacts(content_dir, FILLED)
    html = sheet(page(content_dir).get_data(as_text=True))
    button = re.search(r"<button[^>]*data-copy[^>]*>", html).group(0)
    assert 'type="button"' in button and "hidden" in button
    assert 'aria-label="Скопировать адрес почты info@crvu.ru"' in button
    assert re.search(r'<span class="copy-status" role="status" aria-live="polite" data-copy-status></span>', html)


def test_changed_email_appears_everywhere(content_dir):
    set_contacts(content_dir, {**FILLED, "email": "sales@example.com"})
    html = page(content_dir, "/").get_data(as_text=True)
    assert "info@crvu.ru" not in html
    assert html.count('data-copy-source>sales@example.com</span>') == 2   # раздел и подвал
    # раздел, подвал и шапка (адрес и пункт «Написать письмо» в его меню)
    assert html.count('href="mailto:sales@example.com"') == 4


def test_empty_email_does_not_break_page(content_dir):
    set_contacts(content_dir, {**FILLED, "email": ""})
    response = page(content_dir)
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "data-copy-source" not in html and "mailto:" not in html
    assert 'href="tel:+74951067022"' in footer(html)


def test_no_contacts_keeps_empty_message_and_footer_without_block(content_dir):
    html = page(content_dir).get_data(as_text=True)
    assert "footer-contacts" not in footer(html)
    assert '<p class="empty">' in sheet(html)


def test_contact_values_are_escaped(content_dir):
    set_contacts(content_dir, {"address": "<script>alert(1)</script>"})
    html = page(content_dir).get_data(as_text=True)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_copy_script_reads_rendered_text_and_has_fallback():
    js = MAIN_JS[MAIN_JS.index("function initCopy()"):]
    js = js[:js.index("\n  }\n") + 4]
    assert "navigator.clipboard" in js and "writeText" in js
    assert "isSecureContext" in js
    # Запасной способ и выделение — общие помощники (ими пользуется и меню почты в шапке)
    helpers = MAIN_JS[MAIN_JS.index("function select(node)"):MAIN_JS.index("function initCopy()")]
    assert 'execCommand("copy")' in helpers and "addRange" in helpers
    assert "legacyCopy(source)" in js and "select(source)" in js
    assert "textContent" in js and "@" not in js               # адрес не зашит в JS
    assert "setTimeout" in js                                  # подтверждение исчезает само
    assert MAIN_JS.count("initCopy") >= 2                     # функция вызывается при загрузке


def test_copy_styles_keep_text_selectable_and_wrapping():
    value = re.search(r"\.copy-value \{([^}]*)\}", CSS).group(1)
    assert "user-select: all" in value and "overflow-wrap: anywhere" in value
    assert re.search(r"\.copy-btn\[hidden\] \{[^}]*display: none", CSS)
