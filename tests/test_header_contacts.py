"""Телефон и email в шапке — из content/contacts.json (первые с корректным значением), ссылки tel: и mailto:."""

import json
import re

import pytest

from tests.test_app import edit, make_client


@pytest.fixture
def content_dir(make_content):
    content_dir = make_content()

    def fill(d):
        d["contacts"] = [{"type": "phone", "label": "Телефон", "value": "+7 (495) 000-00-00"},
                         {"type": "email", "label": "Email", "value": "hello@example.ru"},
                         {"type": "address", "label": "Офис", "value": "Москва"}]
    edit(content_dir, "contacts", fill)
    return content_dir


def header(html):
    return re.search(r'<header class="site-header".*?</header>', html, re.S).group(0)


def test_header_shows_phone_and_email_links_from_contacts(content_dir):
    head = header(make_client(content_dir).get("/").get_data(as_text=True))

    contacts = re.search(r'<div class="header-contacts">.*?</div>', head, re.S).group(0)
    # draggable="false": протянуть мышью по ссылке — выделить текст, а не перетащить ссылку
    assert '<a class="header-phone" href="tel:+74950000000" draggable="false">+7 (495) 000-00-00</a>' in contacts
    assert re.search(r'<a class="header-email" href="mailto:hello@example.ru" draggable="false"[^>]*>hello@example.ru</a>', contacts)
    assert "Москва" not in head
    # Кнопка заявки на месте и по-прежнему открывает форму
    assert re.search(r'<button class="btn btn-primary header-cta" type="button" data-open-request>', head)


def test_header_contacts_are_inside_the_menu_so_phone_users_reach_them(content_dir):
    head = header(make_client(content_dir).get("/").get_data(as_text=True))
    nav = re.search(r'<nav class="site-nav".*?</nav>', head, re.S).group(0)
    assert 'class="header-contacts"' in nav


@pytest.mark.parametrize("contacts, phone, email", [
    ([], False, False),
    ([{"type": "phone", "label": "Телефон", "value": ""}, {"type": "email", "label": "Email", "value": "x@y.ru"}], False, True),
    ([{"type": "phone", "label": "Телефон", "value": "не номер"}, {"type": "email", "label": "Email", "value": "не почта"}], False, False),
])
def test_missing_or_invalid_contacts_are_not_shown(content_dir, contacts, phone, email):
    edit(content_dir, "contacts", lambda d: d.update(contacts=contacts))
    head = header(make_client(content_dir).get("/").get_data(as_text=True))

    assert ('class="header-phone"' in head) is phone
    assert ('class="header-email"' in head) is email
    assert ('class="header-contacts"' in head) is (phone or email)


def test_working_contacts_feed_the_header():
    from pathlib import Path
    data = json.loads((Path(__file__).parents[1] / "content" / "contacts.json").read_text(encoding="utf-8"))
    kinds = {c["type"] for c in data["contacts"] if (c.get("value") or "").strip()}
    assert {"phone", "email"} <= kinds


def test_header_contacts_stay_selectable():
    from pathlib import Path
    css = (Path(__file__).parents[1] / "app" / "static" / "css" / "style.css").read_text(encoding="utf-8")
    assert "user-select: none" not in css
    rule = re.search(r"\.site-nav \.header-contacts a \{[^}]*\}", css).group(0)
    assert "-webkit-user-drag: none" in rule and "user-select: text" in rule
    # Отпускание кнопки после выделения не открывает звонок/почту — только при выделенном тексте в ссылке
    js = (Path(__file__).parents[1] / "app" / "static" / "js" / "main.js").read_text(encoding="utf-8")
    handler = re.search(r"function initHeaderContacts\(\) \{.*?\n  \}", js, re.S).group(0)
    assert "!selection.isCollapsed && link.contains(selection.anchorNode)" in handler and "initHeaderContacts();" in js


# --- Email в шапке: щелчок открывает меню «Написать письмо» / «Скопировать адрес», значок справа — копирует ---

def email_block(html):
    return re.search(r'<span class="header-email-wrap" data-copy-field>.*?</span>\s*</span>', header(html), re.S).group(0)


def test_email_opens_a_hidden_menu_with_write_and_copy(content_dir):
    block = email_block(make_client(content_dir).get("/").get_data(as_text=True))

    # Без JS — обычная ссылка mailto:; меню скрыто и связано со ссылкой (раскрывающийся блок)
    link = re.search(r'<a class="header-email"[^>]*>hello@example.ru</a>', block).group(0)
    assert 'href="mailto:hello@example.ru"' in link and 'draggable="false"' in link
    assert 'aria-expanded="false"' in link and 'aria-controls="header-email-menu"' in link
    assert "aria-haspopup" not in link                    # не меню со стрелками — обычные пункты по Tab
    menu = re.search(r'<div class="email-menu" id="header-email-menu" hidden data-email-menu>.*?</div>', block, re.S).group(0)
    write = '<a class="email-menu-item" href="mailto:hello@example.ru" data-email-write>Написать письмо</a>'
    copy = '<button class="email-menu-item" type="button" data-email-copy>Скопировать адрес</button>'
    assert write in menu and copy in menu and menu.index(write) < menu.index(copy)
    # Сообщение «Адрес скопирован» — для экранных дикторов, вне скрытого меню
    assert re.search(r'<span class="email-status" role="status" aria-live="polite" data-email-status></span>', block)
    # Значок копирования — отдельная кнопка сразу после ссылки (не внутри неё): mailto: и меню не срабатывают
    between = block.split("</a>", 1)[1].split('<div class="email-menu"', 1)[0]
    button = re.search(r'<button class="header-copy" type="button" data-copy hidden[^>]*>', between).group(0)
    assert 'aria-label="Скопировать адрес почты hello@example.ru"' in button and 'title="Скопировать адрес"' in button
    assert re.search(r'<span class="copy-status" role="status" aria-live="polite" data-copy-status></span>', between)
    assert "data-copy-source" in link


def test_phone_link_is_unchanged(content_dir):
    head = header(make_client(content_dir).get("/").get_data(as_text=True))
    assert '<a class="header-phone" href="tel:+74950000000" draggable="false">+7 (495) 000-00-00</a>' in head
    assert "aria-haspopup" not in re.search(r'<a class="header-phone"[^>]*>', head).group(0)


def test_email_menu_script_waits_for_a_choice():
    from pathlib import Path
    js = (Path(__file__).parents[1] / "app" / "static" / "js" / "main.js").read_text(encoding="utf-8")
    menu = re.search(r"function initEmailMenu\(\) \{.*?\n  \}\n", js, re.S).group(0)
    # Щелчок по адресу — только меню: переход по mailto: отменён, копирование — по кнопке
    click = menu.split('link.addEventListener("click"', 1)[1].split("});", 1)[0]
    assert "event.preventDefault()" in click and "clipboard" not in click
    assert "navigator.clipboard" in menu and "legacyCopy(" in menu and "Адрес скопирован" in menu
    # Закрывается по Esc, щелчку мимо и уходу фокуса
    assert '"Escape"' in menu and '"focusout"' in menu and "!wrap.contains(event.target)" in menu
    assert "initEmailMenu();" in js
    # Значок копирует общим initCopy и меню не открывает — только закрывает открытое
    assert 'button.closest(".copy-field, [data-copy-field]")' in js
    icon = menu.split('icon.addEventListener("click"', 1)[1].split("});", 1)[0]
    assert "setOpen(false)" in icon and "setOpen(true" not in icon and "!isOpen()" not in icon


def test_header_email_is_readable_on_desktop():
    from pathlib import Path
    css = (Path(__file__).parents[1] / "app" / "static" / "css" / "style.css").read_text(encoding="utf-8")
    rules = re.findall(r"\.site-nav \.header-contacts \.header-email \{([^}]*)\}", css)
    # Одно правило (без дублей): 17 px (--fs-base) и средняя насыщенность, цвет прежний
    assert len(rules) == 1
    assert "font-size: var(--fs-base)" in rules[0] and "font-weight: var(--fw-medium)" in rules[0]
    assert "color: var(--muted)" in rules[0]
    assert re.search(r"--fw-medium: 500;", css)
