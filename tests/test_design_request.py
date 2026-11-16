"""Заявка «Заказать дизайн-проект» из конфигуратора: контакт (телефон или email) и параметры стенда
проверяются на сервере, заявка сохраняется в REQUESTS_DIR и видна менеджеру в админке."""

import json
import re

import pytest

from app import create_app
from app.leads import RequestLimiter, check_contact, check_stand, list_requests
from tests.test_admin import PASSWORD_HASH, log_in, token_from

FETCH = {"X-Requested-With": "fetch"}
SECTION = {"area": {"min": 1, "max": 300}, "types": [
    {"name": "Линейный", "walls": ["back", "left", "right"]}, {"name": "Угловой", "walls": ["back", "left"]},
    {"name": "Полуостров", "walls": ["back"]}, {"name": "Остров", "walls": []},
    {"name": "Двухэтажный", "walls": [], "deck": True}]}
STAND = {"length": "6", "width": "4,5", "type": "Угловой", "budget": "1 500 000"}


@pytest.fixture
def requests_dir(tmp_path):
    return tmp_path / "requests"


@pytest.fixture
def site(make_content, requests_dir):
    content = make_content()
    return create_app({"TESTING": True, "CONTENT_DIR": str(content), "REQUESTS_DIR": str(requests_dir),
                       "SECRET_KEY": "test-secret", "ADMIN_USERNAME": "admin",
                       "ADMIN_PASSWORD_HASH": PASSWORD_HASH})


def send(client, headers=FETCH, **fields):
    return client.post("/request/design", data={"contact": "+7 (999) 123-45-67", **STAND, **fields}, headers=headers)


# --- Контакт ---

@pytest.mark.parametrize("raw,kind,value", [
    ("+7 (999) 123-45-67", "phone", "+7 (999) 123-45-67"),
    ("  89991234567 ", "phone", "89991234567"),
    ("+44 20 7946 0958", "phone", "+44 20 7946 0958"),
    ("+1-202-555-0143", "phone", "+1-202-555-0143"),
    ("(495) 123 45 67", "phone", "(495) 123 45 67"),
    ("name@company.ru", "email", "name@company.ru"),
    ("Ivan.Petrov+expo@mail.example.com", "email", "Ivan.Petrov+expo@mail.example.com"),
    ("почта@сайт.рф", "email", "почта@сайт.рф"),
])
def test_contact_accepts_phone_or_email(raw, kind, value):
    assert check_contact(raw) == (value, kind, None)


@pytest.mark.parametrize("raw,error", [
    ("", "empty"), ("   ", "empty"),
    ("123", "phone"), ("+7 999", "phone"), ("1234567890123456", "phone"), ("+7 999 12+3 45 67", "phone"),
    ("8 (999)) 123-45-67", "phone"), ("абвгд", "phone"), ("телефон 89991234567", "phone"),
    ("name@", "email"), ("@company.ru", "email"), ("name@company", "email"), ("name@@company.ru", "email"),
    ("name@company..ru", "email"), ("na me@company.ru", "email"), ("name@company.r", "email"),
    ("<script>@x.ru", "email"), ("a" * 101, "long"),
])
def test_contact_rejects_wrong_values(raw, error):
    value, kind, err = check_contact(raw)
    assert err == error and value is None


# --- Параметры стенда ---

def test_stand_is_normalised_from_the_configurator_settings():
    stand, errors = check_stand({"length": "6", "width": "4,5", "type": "Угловой", "budget": "1 500 000"}, SECTION)

    assert errors == {}
    assert stand == {"length": 6, "width": 4.5, "area": 27, "type": "Угловой", "open_sides": 2, "budget": 1500000}


@pytest.mark.parametrize("fields,field", [
    ({"length": "0,5"}, "length"), ({"length": "abc"}, "length"), ({"width": ""}, "width"),
    ({"length": "6.123"}, "length"), ({"length": "20", "width": "20"}, "area"),
    ({"type": "Двухэтажный"}, "type"), ({"type": "Купол"}, "type"),
    ({"budget": "сто тысяч"}, "budget"), ({"budget": "1" * 13}, "budget"),
])
def test_stand_rejects_values_the_configurator_never_sends(fields, field):
    stand, errors = check_stand({**STAND, **fields}, SECTION)

    assert stand is None and field in errors


def test_budget_is_optional():
    stand, errors = check_stand({**STAND, "budget": ""}, SECTION)

    assert errors == {} and stand["budget"] is None


# --- Приём заявки ---

def test_phone_only_request_is_saved_with_all_stand_parameters(site, requests_dir):
    response = send(site.test_client())

    assert response.status_code == 200 and response.get_json() == {"ok": True}
    [saved] = list_requests(requests_dir)
    assert saved["contact"] == "+7 (999) 123-45-67" and saved["contact_type"] == "phone"
    assert saved["stand"] == {"length": 6, "width": 4.5, "area": 27, "type": "Угловой", "open_sides": 2,
                              "budget": 1500000}
    assert saved["source"] == "configurator" and re.match(r"\d{4}-\d\d-\d\dT", saved["created"])


def test_email_only_request_without_budget_is_saved(site, requests_dir):
    response = send(site.test_client(), contact="name@company.ru", budget="")

    assert response.get_json() == {"ok": True}
    [saved] = list_requests(requests_dir)
    assert saved["contact_type"] == "email" and saved["stand"]["budget"] is None


def test_request_file_is_private_and_outside_the_site(site, requests_dir):
    send(site.test_client())

    [path] = list(requests_dir.glob("*.json"))
    assert path.stat().st_mode & 0o077 == 0
    assert site.test_client().get(f"/static/../requests/{path.name}").status_code == 404
    assert site.test_client().get(f"/media/../../requests/{path.name}").status_code == 404


@pytest.mark.parametrize("fields,field", [
    ({"contact": ""}, "contact"), ({"contact": "12"}, "contact"), ({"contact": "name@company"}, "contact"),
    ({"length": "0"}, "stand"), ({"type": "Купол"}, "stand"),
])
def test_invalid_request_is_refused_with_a_message_and_not_saved(site, requests_dir, fields, field):
    response = send(site.test_client(), **fields)

    assert response.status_code == 400
    body = response.get_json()
    assert body["ok"] is False and field in body["errors"] and body["errors"][field]
    assert list_requests(requests_dir) == []


def test_only_requests_from_the_site_script_are_accepted(site, requests_dir):
    client = site.test_client()

    # Обычная форма с чужого сайта не может добавить свой заголовок — такой запрос не принимается
    assert send(client, headers={}).status_code == 400
    assert send(client, headers={**FETCH, "Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert send(client, headers={**FETCH, "Origin": "https://evil.example"}).status_code == 403
    assert send(client, headers={**FETCH, "Origin": "http://localhost", "Sec-Fetch-Site": "same-origin"}).status_code == 200
    assert len(list_requests(requests_dir)) == 1


def test_honeypot_field_is_answered_but_not_saved(site, requests_dir):
    response = send(site.test_client(), website="http://spam.example")

    assert response.status_code == 200
    assert list_requests(requests_dir) == []


def test_too_many_requests_from_one_address_are_limited(site, requests_dir):
    client = site.test_client()
    codes = [send(client, contact=f"+7 999 000 00 {n:02d}").status_code for n in range(7)]

    assert codes[:5] == [200] * 5 and codes[5:] == [429, 429]
    assert len(list_requests(requests_dir)) == 5


def test_limiter_forgets_old_requests():
    limiter = RequestLimiter(limit=2, window=60)

    assert limiter.allow("a", now=0) and limiter.allow("a", now=1) and not limiter.allow("a", now=2)
    assert limiter.allow("a", now=62) and limiter.allow("b", now=2)


def test_storage_failure_is_reported_and_nothing_pretends_to_succeed(site, requests_dir):
    requests_dir.parent.mkdir(parents=True, exist_ok=True)
    requests_dir.write_text("не папка")                      # записать заявку некуда

    response = send(site.test_client())

    assert response.status_code == 500 and response.get_json()["ok"] is False
    assert "не отправлена" in response.get_json()["message"]


# --- Блок на главной и админка ---

def test_configurator_block_has_new_texts_and_the_design_request_dialog(site):
    html = site.test_client().get("/").get_data(as_text=True)
    block = re.search(r'<section class="section configurator.*?</section>', html, re.S).group(0)

    assert "Ваш стенд начинается здесь" in block
    assert "Задайте размеры и конфигурацию стенда." in block
    assert "Обсудить проект</button>" in block
    assert "Обсудим вашу задачу и подготовим проект с учётом ваших пожеланий и бюджета." in block
    dialog = re.search(r'<dialog class="modal design-request".*?</dialog>', html, re.S).group(0)
    assert "Обсудим ваш проект" in dialog and "Оставьте контакты, и наш менеджер свяжется с вами." in dialog
    assert 'placeholder="Введите телефон или email"' in dialog and "Укажите любой удобный способ связи." in dialog
    assert "Отправить заявку" in dialog and "Спасибо за заявку!" in dialog
    # Одно обязательное поле — контакт; поле-ловушка скрыто от людей
    assert dialog.count(" required") == 1
    assert re.search(r'<label for="dr-contact">Телефон или электронная почта</label>', dialog)
    assert 'name="website"' in dialog and 'tabindex="-1"' in dialog
    assert 'data-action="/request/design"' in dialog


def test_requests_page_in_admin_lists_contacts_and_stand(site, requests_dir):
    send(site.test_client())
    send(site.test_client(), contact="name@company.ru", budget="", type="Остров")
    client = site.test_client()
    assert site.test_client().get("/admin/requests").status_code == 302    # без входа — на страницу входа
    log_in(client)

    html = client.get("/admin/requests").get_data(as_text=True)

    assert 'href="/admin/requests"' in html
    assert 'href="tel:+79991234567"' in html and 'href="mailto:name@company.ru"' in html
    assert "6 × 4,5 м" in html and "27 м²" in html and "Угловой" in html and "1 500 000 ₽" in html
    assert html.index("name@company.ru") < html.index("+7 (999) 123-45-67")   # новые — первыми
    assert "<script" not in html


def test_requests_dir_setting_comes_from_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("CRVU_REQUESTS_DIR", str(tmp_path / "leads"))

    app = create_app({"TESTING": True})

    assert app.config["REQUESTS_DIR"] == str(tmp_path / "leads")


def test_content_json_has_new_texts_in_working_content():
    from pathlib import Path
    home = json.loads((Path(__file__).resolve().parent.parent / "content" / "home.json").read_text(encoding="utf-8"))
    section = next(s for s in home["sections"] if s["type"] == "configurator")

    assert section["title"] == "Ваш стенд начинается здесь"
    assert section["submit"] == "Обсудить проект"
    assert section["request"]["title"] == "Обсудим ваш проект"


# --- Уровень, опции и ориентир бюджета из калькулятора ---

PRICED = {**SECTION, "pricing": {
    "levels": [{"id": "standard", "name": "Стандарт", "factor": 1}, {"id": "premium", "name": "Премиум", "factor": 1.7}],
    "extras": [{"id": "led", "name": "LED-экраны", "kind": "fixed", "min": 1, "max": 2},
               {"id": "urgent", "name": "Срочное производство", "kind": "urgent", "min": 0.1, "max": 0.2}]}}
ESTIMATE = {"level": "premium", "extras": "led,urgent", "estimate_min": "1 450 000", "estimate_max": "1 870 000"}


def test_level_extras_and_estimate_are_saved_by_their_names():
    stand, errors = check_stand({**STAND, **ESTIMATE}, PRICED)

    assert errors == {}
    assert stand["level"] == "Премиум" and stand["extras"] == ["LED-экраны", "Срочное производство"]
    assert stand["estimate"] == {"min": 1450000, "max": 1870000}


def test_estimate_without_upper_bound_and_no_extras():
    stand, errors = check_stand({**STAND, **ESTIMATE, "extras": "", "estimate_max": ""}, PRICED)

    assert errors == {} and stand["extras"] == [] and stand["estimate"] == {"min": 1450000, "max": None}


@pytest.mark.parametrize("fields", [
    {"level": "gold"}, {"extras": "led,helipad"}, {"estimate_min": "дорого"}, {"estimate_min": "0"},
    {"estimate_min": "1" * 13}, {"estimate_max": "1000"},
])
def test_wrong_calculator_values_are_refused(fields):
    stand, errors = check_stand({**STAND, **ESTIMATE, **fields}, PRICED)

    assert stand is None and errors


def test_old_requests_without_calculator_fields_stay_valid():
    stand, errors = check_stand(STAND, PRICED)

    assert errors == {} and "level" not in stand and "estimate" not in stand


def test_calculator_request_is_saved_and_shown_in_admin(site, requests_dir):
    response = send(site.test_client(), budget="", level="custom", extras="led",
                    estimate_min="1 920 000", estimate_max="2 830 000")
    assert response.get_json() == {"ok": True}
    [saved] = list_requests(requests_dir)
    assert saved["stand"]["level"] == "Индивидуальный" and saved["stand"]["extras"] == ["LED-экраны"]
    assert saved["stand"]["estimate"] == {"min": 1920000, "max": 2830000}

    client = site.test_client()
    log_in(client)
    html = client.get("/admin/requests").get_data(as_text=True)
    assert "Индивидуальный" in html and "LED-экраны" in html
    assert "1 920 000 – 2 830 000 ₽" in html and "ориентир с сайта" in html
    assert "Бюджет" not in html.split("request-stand", 1)[1]       # клиент свой бюджет не указал
