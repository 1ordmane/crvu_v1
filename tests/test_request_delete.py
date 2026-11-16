"""Удаление заявки в админке: ссылка у каждой заявки → страница подтверждения → POST с CSRF."""

import pathlib
import re

import pytest

from app import create_app
from app.leads import list_requests
from tests.test_admin import PASSWORD_HASH, TOKEN_RE, log_in, token_from
from tests.test_design_request import send


@pytest.fixture
def requests_dir(tmp_path):
    return tmp_path / "requests"


def make_site(content, requests_dir, **config):
    return create_app({"TESTING": True, "CONTENT_DIR": str(content), "REQUESTS_DIR": str(requests_dir),
                       "SECRET_KEY": "test-secret", "ADMIN_USERNAME": "admin",
                       "ADMIN_PASSWORD_HASH": PASSWORD_HASH, **config})


@pytest.fixture
def site(make_content, requests_dir):
    return make_site(make_content(), requests_dir)


@pytest.fixture
def two_requests(site, requests_dir):
    send(site.test_client())
    send(site.test_client(), contact="name@company.ru")
    # Новые первыми: [почта, телефон]
    return [item["file"] for item in list_requests(requests_dir)]


@pytest.fixture
def admin(site):
    client = site.test_client()
    log_in(client)
    return client


def files(requests_dir):
    return sorted(p.name for p in requests_dir.glob("*.json"))


def confirm(client, name):
    return client.post(f"/admin/requests/{name}/delete", data={"csrf_token": token_from(client, "/admin/requests")})


def test_every_request_has_a_delete_link_to_confirmation(admin, two_requests):
    html = admin.get("/admin/requests").get_data(as_text=True)

    for name in two_requests:
        assert f'href="/admin/requests/{name}/delete"' in html
    assert 'aria-label="Удалить заявку от name@company.ru…"' in html


def test_confirmation_page_explains_and_offers_cancel_and_delete(admin, requests_dir, two_requests):
    name = two_requests[0]
    response = admin.get(f"/admin/requests/{name}/delete")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "name@company.ru" in html and "безвозвратно" in html
    form = re.search(rf'<form method="post" action="/admin/requests/{re.escape(name)}/delete">(.*?)</form>', html, re.S)
    assert form and 'name="csrf_token"' in form.group(1)
    assert re.search(r'<button class="button button-danger" type="submit">Удалить</button>', form.group(1))
    assert '<a class="button" href="/admin/requests">Отмена</a>' in html
    assert " style=" not in html and "<script" not in html.split("</head>")[1]
    # Открыть подтверждение — ещё не удалить
    assert files(requests_dir) == sorted(two_requests)


def test_confirmed_delete_removes_only_that_request(admin, requests_dir, two_requests):
    removed, kept = two_requests

    response = confirm(admin, removed)

    assert response.status_code == 302 and response.headers["Location"].endswith("/admin/requests")
    assert files(requests_dir) == [kept]
    html = admin.get("/admin/requests").get_data(as_text=True)
    assert "Заявка удалена." in html
    assert "name@company.ru" not in html and "+7 (999) 123-45-67" in html


@pytest.mark.parametrize("token", [None, "", "forged-token"])
def test_delete_without_valid_csrf_token_changes_nothing(admin, requests_dir, two_requests, token):
    data = {} if token is None else {"csrf_token": token}

    assert admin.post(f"/admin/requests/{two_requests[0]}/delete", data=data).status_code == 400
    assert files(requests_dir) == sorted(two_requests)


def test_guest_cannot_open_or_delete(site, requests_dir, two_requests):
    name = two_requests[0]
    guest = site.test_client()
    assert guest.get(f"/admin/requests/{name}/delete").headers["Location"].endswith("/admin/login")
    # Даже с настоящим токеном сессии гостя — только на вход
    token = TOKEN_RE.search(guest.get("/admin/login").get_data(as_text=True)).group(1)
    response = guest.post(f"/admin/requests/{name}/delete", data={"csrf_token": token})
    assert response.status_code == 302 and response.headers["Location"].endswith("/admin/login")
    assert files(requests_dir) == sorted(two_requests)


def test_delete_is_off_when_admin_is_not_configured(requests_dir, site, two_requests):
    off = make_site(site.config["CONTENT_DIR"], requests_dir, SECRET_KEY=None).test_client()
    assert off.get(f"/admin/requests/{two_requests[0]}/delete").status_code == 404
    assert off.post(f"/admin/requests/{two_requests[0]}/delete").status_code == 404
    assert files(requests_dir) == sorted(two_requests)


@pytest.mark.parametrize("name", ["..%2Fsite.json", "..", "x.json", ".request-abc.tmp",
                                  "20261022-124707-184140-06c5d7a8.txt", "20261022-124707-184140-06C5D7A8.json"])
def test_foreign_names_are_not_found(admin, requests_dir, two_requests, name):
    assert admin.get(f"/admin/requests/{name}/delete").status_code == 404
    assert confirm(admin, name).status_code == 404
    assert files(requests_dir) == sorted(two_requests)


def test_already_deleted_request_is_reported(admin, requests_dir, two_requests):
    name = two_requests[0]
    assert confirm(admin, name).status_code == 302

    for again in (lambda: admin.get(f"/admin/requests/{name}/delete"), lambda: confirm(admin, name)):
        response = again()
        assert response.status_code == 302 and response.headers["Location"].endswith("/admin/requests")
        assert "Заявка не найдена" in admin.get("/admin/requests").get_data(as_text=True)
    assert files(requests_dir) == [two_requests[1]]


def test_failed_delete_keeps_the_request_and_says_so(admin, requests_dir, two_requests, monkeypatch):
    def refuse(self, missing_ok=False):
        raise PermissionError("read-only")
    monkeypatch.setattr(pathlib.Path, "unlink", refuse)

    response = confirm(admin, two_requests[0])

    monkeypatch.undo()
    assert response.status_code == 302
    assert "Не удалось удалить заявку" in admin.get("/admin/requests").get_data(as_text=True)
    assert files(requests_dir) == sorted(two_requests)
