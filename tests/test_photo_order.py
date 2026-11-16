"""Фото в форме проекта: порядок и обложка — частью формы «Сохранить изменения», без перезагрузки
страницы; удаление — отдельным запросом с ответом JSON. Введённые поля не теряются."""

import re

import pytest

from tests.test_admin import (REQUIRED, admin, app, content_dir, create, image, post, project,  # noqa: F401
                              upload)

PID = "stend-na-vystavke-2025"
FORM = {"title": "Новое название", "exhibition": "Иннопром", "year": "2025", "month": "7", "area": "40,5",
        "layout": "Угловой", "floors": "2", "description": "Новое описание", "video": "https://vimeo.com/76979871"}
FETCH = {"X-Requested-With": "fetch"}


def three_photos(admin):
    create(admin)
    upload(admin, PID, *[(f"{n}.png", image(color=(n * 40, 0, 0))) for n in range(1, 4)])
    return [f"portfolio/{PID}/0{n}.webp" for n in (1, 2, 3)]


def save(admin, order, **fields):
    return post(admin, f"/admin/projects/{PID}", {**FORM, **fields, "photo_order": order})


@pytest.mark.parametrize("status", ["draft", "published"])
def test_save_keeps_fields_photo_order_and_cover_together(admin, content_dir, status):
    p1, p2, p3 = three_photos(admin)
    post(admin, f"/admin/projects/{PID}/status", {"status": status})

    assert save(admin, [p3, p1, p2]).status_code == 302

    saved = project(content_dir, PID)
    assert saved["photos"] == [p3, p1, p2] and saved["cover"] == p3
    assert (saved["title"], saved["exhibition"], saved["year"], saved["month"]) == ("Новое название", "Иннопром", 2025, 7)
    assert (saved["area"], saved["layout"], saved["floors"]) == (40.5, "Угловой", 2)
    assert saved["description"] == "Новое описание" and saved["video"] == "https://vimeo.com/76979871"
    assert saved["status"] == status                       # сохранение не публикует и не снимает с публикации
    # Повторно открытая форма показывает сохранённый порядок: обложка первой
    html = admin.get(f"/admin/projects/{PID}").get_data(as_text=True)
    assert re.findall(r'data-photo="([^"]+)"', html) == [p3, p1, p2]


def test_order_that_does_not_match_the_project_is_refused_without_losing_fields(admin, content_dir):
    p1, p2, p3 = three_photos(admin)
    before = project(content_dir, PID)

    for order in ([p1, p2], [p1, p2, p3, p3], [p1, p2, "portfolio/other/01.webp"], [p1, p2, "../site.json"]):
        response = save(admin, order)
        assert response.status_code == 400
        html = response.get_data(as_text=True)
        assert "Фотографии проекта изменились" in html
        assert 'value="Новое название"' in html and "Новое описание" in html     # введённое не потеряно

    assert project(content_dir, PID) == before


def test_failed_save_keeps_new_photo_order_on_the_page_and_writes_nothing(admin, content_dir):
    p1, p2, p3 = three_photos(admin)
    before = project(content_dir, PID)

    response = save(admin, [p2, p3, p1], area="много")

    assert response.status_code == 400
    html = response.get_data(as_text=True)
    assert re.findall(r'data-photo="([^"]+)"', html) == [p2, p3, p1]
    assert 'value="много"' in html and 'value="Новое название"' in html
    assert project(content_dir, PID) == before


def test_save_without_order_leaves_photos_as_they_are(admin, content_dir):
    p1, p2, p3 = three_photos(admin)

    post(admin, f"/admin/projects/{PID}", FORM)

    assert project(content_dir, PID)["photos"] == [p1, p2, p3]


def test_delete_by_fetch_answers_json_and_happens_once(admin, content_dir):
    p1, p2, p3 = three_photos(admin)

    response = post(admin, f"/admin/projects/{PID}/photo", {"action": "delete", "photo": p2}, headers=FETCH)
    assert response.status_code == 200 and response.is_json
    assert response.get_json()["ok"] is True and response.get_json()["photos"] == [p1, p3]
    assert project(content_dir, PID)["photos"] == [p1, p3]
    assert list((content_dir / ".history" / "media").glob(f"*/portfolio/{PID}/02.webp"))

    # Повторное нажатие: фото уже нет — понятная ошибка, ничего не меняется
    again = post(admin, f"/admin/projects/{PID}/photo", {"action": "delete", "photo": p2}, headers=FETCH)
    assert again.status_code == 404 and again.get_json()["ok"] is False
    assert project(content_dir, PID)["photos"] == [p1, p3]


def test_delete_keeps_unsaved_order_valid_for_the_next_save(admin, content_dir):
    p1, p2, p3 = three_photos(admin)
    # В браузере порядок уже изменён (p3, p1, p2), но не сохранён; удалили p1
    post(admin, f"/admin/projects/{PID}/photo", {"action": "delete", "photo": p1}, headers=FETCH)

    assert save(admin, [p3, p2]).status_code == 302
    assert project(content_dir, PID)["photos"] == [p3, p2] and project(content_dir, PID)["cover"] == p3


def test_delete_errors_answer_json_and_change_nothing(admin, content_dir):
    create(admin)
    upload(admin, PID, ("a.png", image()))
    post(admin, f"/admin/projects/{PID}/status", {"status": "published"})
    photo = f"portfolio/{PID}/01.webp"

    response = post(admin, f"/admin/projects/{PID}/photo", {"action": "delete", "photo": photo}, headers=FETCH)

    assert response.status_code == 409 and response.get_json()["ok"] is False
    assert "последнее фото" in response.get_json()["message"]
    assert project(content_dir, PID)["photos"] == [photo]
    assert (content_dir / "media" / photo).is_file()


def test_delete_by_fetch_still_requires_csrf(admin, content_dir):
    p1, _, _ = three_photos(admin)

    response = admin.post(f"/admin/projects/{PID}/photo", data={"action": "delete", "photo": p1, "csrf_token": "x"},
                          headers=FETCH)

    assert response.status_code == 400
    assert p1 in project(content_dir, PID)["photos"]


def test_photo_controls_are_part_of_the_form_and_edge_arrows_are_disabled(admin):
    p1, p2, p3 = three_photos(admin)
    html = admin.get(f"/admin/projects/{PID}").get_data(as_text=True)

    # Порядок уходит вместе с формой проекта — скрытыми полями в ней
    form = re.search(r'<form[^>]*id="project-form".*?</form>', html, re.S).group(0)
    assert re.findall(r'name="photo_order" value="([^"]+)"', form) == [p1, p2, p3]
    assert 'src="/static/js/admin-photos.js"' in html
    cards = re.findall(r'<li class="photo-card".*?</li>', html, re.S)
    assert len(cards) == 3
    assert re.search(r'data-photo-action="up"[^>]*disabled', cards[0])
    assert not re.search(r'data-photo-action="down"[^>]*disabled', cards[0])
    assert re.search(r'data-photo-action="down"[^>]*disabled', cards[2])
    # «Сделать обложкой» у обложки скрыта, удаление — с подтверждением в скрипте
    assert re.search(r'data-photo-action="cover"[^>]*hidden', cards[0])
    assert not re.search(r'data-photo-action="cover"[^>]*hidden', cards[1])
    assert all('data-photo-action="delete"' in c for c in cards)


def test_single_photo_has_both_arrows_disabled(admin):
    create(admin)
    upload(admin, PID, ("a.png", image()))
    html = admin.get(f"/admin/projects/{PID}").get_data(as_text=True)
    [card] = re.findall(r'<li class="photo-card".*?</li>', html, re.S)

    assert re.search(r'data-photo-action="up"[^>]*disabled', card)
    assert re.search(r'data-photo-action="down"[^>]*disabled', card)
