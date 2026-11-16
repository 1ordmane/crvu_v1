"""Поле «Планировка стенда»: Угловой, Остров, Линейный, Полуостров.

Обязательно для новых проектов (форма, импорт папки, ZIP) и для публикации; сервер принимает только
эти четыре значения. Старый проект без планировки открывается и сохраняется без неё, пока её не выберут
вручную; выбранную планировку убрать нельзя. Значение хранится как показывается: "layout": "Угловой".
"""

import hashlib
import io
import json
import re
import zipfile

import pytest

from app.portfolio import LAYOUTS
from tests.test_image_sizes import PASSWORD, TOKEN_RE, make_app, photo_bytes
from tests.test_project_dates import REQUIRED, create, edit, projects


@pytest.fixture
def content_dir(make_content):
    return make_content()


@pytest.fixture
def admin(content_dir):
    client = make_app(content_dir).test_client()
    token = TOKEN_RE.search(client.get("/admin/login").get_data(as_text=True)).group(1)
    client.post("/admin/login", data={"csrf_token": token, "username": "admin", "password": PASSWORD})
    return client


def state(content_dir):
    return {str(p.relative_to(content_dir)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(content_dir.rglob("*")) if p.is_file() and ".cache" not in p.parts and p.name != ".lock"}


def by_title(content_dir, title):
    return next(p for p in projects(content_dir) if p["title"] == title)


def old_project(content_dir, pid="test-arara"):
    """Тестовый «старый» проект: опубликован, без планировки."""
    p = next(p for p in projects(content_dir) if p["id"] == pid)
    assert "layout" not in p
    return p


def token(client, path):
    return TOKEN_RE.search(client.get(path).get_data(as_text=True)).group(1)


def test_four_layouts_in_fixed_order():
    assert LAYOUTS == ("Угловой", "Остров", "Линейный", "Полуостров")


# --- Новые проекты ---

def test_form_offers_layout_select(admin):
    html = admin.get("/admin/projects/new").get_data(as_text=True)

    assert re.search(r'<select name="layout"[^>]*required', html)
    for name in LAYOUTS:
        assert f'<option value="{name}">{name}</option>' in html
    assert "Планировка стенда" in html


@pytest.mark.parametrize("layout", LAYOUTS)
def test_each_layout_is_saved_as_is(admin, content_dir, layout):
    create(admin, layout=layout)

    assert by_title(content_dir, REQUIRED["title"])["layout"] == layout


@pytest.mark.parametrize("layout", ["", "   ", "угловой", "Круглый", "<script>", "Угловой ", None])
def test_new_project_without_valid_layout_is_not_created(admin, content_dir, layout):
    before = state(content_dir)

    response = create(admin, layout=layout,
                      files=[("1.jpg", photo_bytes((900, 600)))], publish="on")

    assert response.status_code == 400
    assert "Планировка стенда — выберите" in response.get_data(as_text=True)
    assert state(content_dir) == before                              # ни проекта, ни фото


@pytest.mark.parametrize("field", ["area", "layout"])
def test_area_and_layout_are_both_required(admin, content_dir, field):
    before = state(content_dir)

    assert create(admin, **{field: ""}).status_code == 400
    assert state(content_dir) == before


def test_folder_import_needs_layout(admin, content_dir):
    before = state(content_dir)
    form = {"csrf_token": token(admin, "/admin/projects/import-folder"), **REQUIRED, "layout": "",
            "photos": [(io.BytesIO(photo_bytes((900, 600))), "Папка/01.jpg")]}

    response = admin.post("/admin/projects/new", content_type="multipart/form-data", data=form)

    assert response.status_code == 400 and state(content_dir) == before
    assert re.search(r'<select name="layout"[^>]*required',
                     admin.get("/admin/projects/import-folder").get_data(as_text=True))


def zip_bytes():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Стенд/01.jpg", photo_bytes((900, 600)))
    return buf.getvalue()


def test_zip_import_needs_all_required_fields(admin, content_dir):
    before = state(content_dir)
    form = {"csrf_token": token(admin, "/admin"), **{k: v for k, v in REQUIRED.items() if k != "layout"},
            "archive": (io.BytesIO(zip_bytes()), "stand.zip")}

    admin.post("/admin/projects/import", content_type="multipart/form-data", data=form)

    assert state(content_dir) == before
    assert "Планировка стенда — выберите" in admin.get("/admin").get_data(as_text=True)

    form = {"csrf_token": token(admin, "/admin"), **REQUIRED, "title": "",
            "archive": (io.BytesIO(zip_bytes()), "stand.zip")}
    admin.post("/admin/projects/import", content_type="multipart/form-data", data=form)
    p = by_title(content_dir, "Стенд")                               # название — из папки архива
    assert (p["layout"], p["area"], p["month"], p["exhibition"]) == \
        (REQUIRED["layout"], 40.5, 10, REQUIRED["exhibition"])


# --- Редактирование ---

def test_edit_form_shows_saved_layout(admin, content_dir):
    create(admin, layout="Остров")
    pid = by_title(content_dir, REQUIRED["title"])["id"]

    html = admin.get(f"/admin/projects/{pid}").get_data(as_text=True)

    assert '<option value="Остров" selected>Остров</option>' in html


def test_layout_can_be_changed_but_not_removed(admin, content_dir):
    create(admin, layout="Остров")
    pid = by_title(content_dir, REQUIRED["title"])["id"]

    assert edit(admin, pid, layout="Линейный").status_code == 302
    assert by_title(content_dir, REQUIRED["title"])["layout"] == "Линейный"

    response = edit(admin, pid, layout="")
    assert response.status_code == 400 and "Планировка стенда — выберите" in response.get_data(as_text=True)
    assert by_title(content_dir, REQUIRED["title"])["layout"] == "Линейный"


def test_old_project_without_layout_opens_and_saves_without_it(admin, content_dir):
    before = old_project(content_dir)
    html = admin.get(f"/admin/projects/{before['id']}").get_data(as_text=True)
    assert re.search(r'<select name="layout"(?![^>]*required)', html)       # без required
    assert "планировка ещё не выбрана" in html and "можно позже" in html

    response = edit(admin, before["id"], layout="", title="Арара")

    after = old_project(content_dir)                                # ключ layout не появился
    assert response.status_code == 302 and after["title"] == "Арара"
    assert after["photos"] == before["photos"] and after["status"] == before["status"]


def test_old_project_rejects_invalid_layout_and_accepts_a_real_one(admin, content_dir):
    pid = old_project(content_dir)["id"]

    assert edit(admin, pid, layout="Круглый").status_code == 400
    assert "layout" not in next(p for p in projects(content_dir) if p["id"] == pid)

    edit(admin, pid, layout="Полуостров")
    assert next(p for p in projects(content_dir) if p["id"] == pid)["layout"] == "Полуостров"
    assert edit(admin, pid, layout="").status_code == 400            # исключение больше не действует


# --- Публикация ---

def test_draft_without_layout_cannot_be_published(admin, content_dir):
    path = content_dir / "portfolio.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    p = next(p for p in data["projects"] if p["id"] == "test-arara")
    p["status"] = "draft"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    admin.post("/admin/projects/test-arara/status",
               data={"csrf_token": token(admin, "/admin/projects/test-arara"), "status": "published"})

    assert old_project(content_dir)["status"] == "draft"
    assert "Сначала выберите планировку стенда" in admin.get("/admin/projects/test-arara").get_data(as_text=True)

    edit(admin, "test-arara", layout="Угловой")
    admin.post("/admin/projects/test-arara/status",
               data={"csrf_token": token(admin, "/admin/projects/test-arara"), "status": "published"})
    assert next(p for p in projects(content_dir) if p["id"] == "test-arara")["status"] == "published"


def test_already_published_old_projects_stay_published(admin, content_dir):
    before = {p["id"]: p["status"] for p in projects(content_dir)}

    admin.get("/admin")
    edit(admin, "test-arara", title="Арара")

    assert {p["id"]: p["status"] for p in projects(content_dir)} == before


# --- Данные ---

def test_check_content_validates_layout(content_dir):
    from app.content import validate_content

    static = str(make_app(content_dir).static_folder)
    path = content_dir / "portfolio.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["projects"][0]["layout"] = "Угловой"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert validate_content(content_dir, static) == []

    data["projects"][0]["layout"] = "Круглый"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert any("layout" in e for e in validate_content(content_dir, static))


def test_layout_shown_in_admin_list(admin, content_dir):
    create(admin, layout="Остров")

    assert re.search(r"Остров</span>", admin.get("/admin").get_data(as_text=True))


def test_layout_needs_login_and_csrf(content_dir, admin):
    before = state(content_dir)
    anonymous = make_app(content_dir).test_client()

    assert anonymous.post("/admin/projects/test-arara", data={**REQUIRED}).status_code in (302, 400)
    assert admin.post("/admin/projects/test-arara", data={**REQUIRED, "csrf_token": "forged"}).status_code == 400
    assert state(content_dir) == before
