"""Основной сценарий: «Добавить проект» → название, год, несколько фото → «Сохранить проект».

Фото идут через тот же путь, что и загрузка на странице проекта (_store_photos): проверки,
оригинал байт в байт в content/originals/, WebP без EXIF и GPS, откат при сбоях. Повторно
выбранные те же файлы (те же байты) в проект не добавляются.
"""

import hashlib
import io
import json
import re

import pytest
from PIL import Image

from tests.test_image_sizes import PASSWORD, TOKEN_RE, gps_photo_bytes, make_app, photo_bytes

MB = 1024 * 1024


@pytest.fixture
def content_dir(make_content):
    return make_content()


def logged_in(content_dir, **config):
    client = make_app(content_dir, **config).test_client()
    token = TOKEN_RE.search(client.get("/admin/login").get_data(as_text=True)).group(1)
    client.post("/admin/login", data={"csrf_token": token, "username": "admin", "password": PASSWORD})
    return client


@pytest.fixture
def admin(content_dir):
    return logged_in(content_dir)


def create(client, files, title="Стенд Haval", year="2025", publish=True, **fields):
    token = TOKEN_RE.search(client.get("/admin/projects/new").get_data(as_text=True)).group(1)
    form = {"csrf_token": token, "title": title, "year": year, "exhibition": "Выставка", "month": "5", "area": "40",
            "layout": "Линейный",
            **fields,
            "photos": [(io.BytesIO(data), name) for name, data in files]}
    if publish:
        form["publish"] = "on"
    return client.post("/admin/projects/new", content_type="multipart/form-data", data=form)


def add_photos(client, project_id, files):
    token = TOKEN_RE.search(client.get(f"/admin/projects/{project_id}").get_data(as_text=True)).group(1)
    return client.post(f"/admin/projects/{project_id}/photos", content_type="multipart/form-data",
                       data={"csrf_token": token, "photos": [(io.BytesIO(data), name) for name, data in files]})


def projects(content_dir):
    return json.loads((content_dir / "portfolio.json").read_text(encoding="utf-8"))["projects"]


def new_project(content_dir, title="Стенд Haval"):
    return next(p for p in projects(content_dir) if p["title"] == title)


def page_messages(client, path):
    return " ".join(re.findall(r'class="message[^"]*">(.*?)<', client.get(path).get_data(as_text=True)))


def state(content_dir):
    return {str(p.relative_to(content_dir)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(content_dir.rglob("*")) if p.is_file() and ".cache" not in p.parts and p.name != ".lock"}


# --- Создание проекта сразу с фото ---

def test_new_project_form_accepts_photos_and_publish_choice(admin):
    html = admin.get("/admin/projects/new").get_data(as_text=True)

    assert 'enctype="multipart/form-data"' in html
    assert re.search(r'<input type="file" name="photos" multiple[^>]*accept="\.jpg,\.jpeg,\.png,\.webp', html)
    assert 'name="publish" checked' in html                      # по умолчанию — сразу на сайт
    assert "Сохранить проект" in html


def test_project_with_several_photos_is_created_and_published_in_one_step(admin, content_dir):
    first, second, third = gps_photo_bytes((1600, 1200)), photo_bytes((1200, 900)), photo_bytes((900, 600))

    response = create(admin, [("IMG_1.JPG", first), ("IMG_2.jpg", second), ("IMG_3.jpg", third)])

    project = new_project(content_dir)
    pid = project["id"]
    assert response.status_code == 302 and response.headers["Location"].endswith(f"/admin/projects/{pid}")
    assert project["status"] == "published" and project["year"] == 2025
    # Порядок — как выбраны; первое фото — обложка
    assert project["photos"] == [f"portfolio/{pid}/0{n}.webp" for n in (1, 2, 3)]
    assert project["cover"] == project["photos"][0]
    # Оригиналы байт в байт (с EXIF и GPS) — закрыты; на сайт — WebP без метаданных
    originals = content_dir / "originals" / "portfolio" / pid
    assert [(originals / f"0{n}.jpg").read_bytes() for n in (1, 2, 3)] == [first, second, third]
    assert (originals / "01.jpg").stat().st_mode & 0o777 == 0o600
    assert Image.open(io.BytesIO(first)).getexif().get_ifd(0x8825)
    with Image.open(content_dir / "media" / "portfolio" / pid / "01.webp") as img:
        assert img.format == "WEBP" and not img.getexif()
    # Опубликован — виден на сайте
    html = make_app(content_dir).test_client().get("/portfolio").get_data(as_text=True)
    assert "Стенд Haval" in html and f"/media/portfolio/{pid}/01-640.webp" in html
    assert "Проект «Стенд Haval» опубликован. Загружено фото: 3" in page_messages(admin, f"/admin/projects/{pid}")


def test_without_publish_checkbox_project_stays_draft(admin, content_dir):
    create(admin, [("1.jpg", photo_bytes((900, 600)))], publish=False)

    project = new_project(content_dir)
    assert project["status"] == "draft" and len(project["photos"]) == 1


def test_project_without_photos_is_saved_as_draft_even_if_publish_checked(admin, content_dir):
    create(admin, [])

    project = new_project(content_dir)
    assert project["status"] == "draft" and project["photos"] == []
    assert "без фото проект не публикуется" in page_messages(admin, f"/admin/projects/{project['id']}")


def test_partial_error_keeps_good_photos_and_original_of_broken_one(admin, content_dir):
    good1, good2 = photo_bytes((900, 600)), photo_bytes((1000, 700))
    broken = photo_bytes((2000, 1500))
    broken = broken[: len(broken) // 2]

    create(admin, [("a.jpg", good1), ("b.jpg", broken), ("c.jpg", good2)])

    project = new_project(content_dir)
    assert project["status"] == "published" and len(project["photos"]) == 2
    originals = content_dir / "originals" / "portfolio" / project["id"]
    assert sorted(p.read_bytes() for p in originals.iterdir()) == sorted([good1, good2])
    assert [p.read_bytes() for p in (content_dir / ".history" / "originals").rglob("*.jpg")] == [broken]
    assert "«b.jpg»: файл изображения повреждён" in page_messages(admin, f"/admin/projects/{project['id']}")


def test_all_photos_broken_leaves_draft_with_typed_data(admin, content_dir):
    broken = photo_bytes((2000, 1500))[:4000]

    create(admin, [("a.jpg", broken)], description="Москва")

    project = new_project(content_dir)
    assert project["status"] == "draft" and project["photos"] == [] and project["description"] == "Москва"


def test_invalid_form_saves_nothing_and_asks_to_reselect_photos(admin, content_dir):
    before = state(content_dir)

    response = create(admin, [("a.jpg", photo_bytes((900, 600)))], title="")

    html = response.get_data(as_text=True)
    assert response.status_code == 400 and "Укажите название" in html
    assert "Фото не загружены — выберите их снова" in html
    assert state(content_dir) == before


def test_size_limit_rejects_only_the_big_file(content_dir):
    client = logged_in(content_dir, ADMIN_MAX_IMAGE_BYTES=MB)
    small = photo_bytes((900, 600))

    create(client, [("small.jpg", small), ("big.jpg", small + b"\0" * (MB + 10))])

    project = new_project(content_dir)
    assert len(project["photos"]) == 1
    assert "«big.jpg»: файл больше 1 МБ" in page_messages(client, f"/admin/projects/{project['id']}")


def test_too_many_files_or_too_big_request_create_nothing(content_dir):
    before = state(content_dir)
    few = logged_in(content_dir, ADMIN_MAX_FILES=2)
    response = create(few, [(f"{n}.jpg", photo_bytes((900, 600))) for n in range(3)])
    assert response.status_code == 400 and "не больше 2 файлов" in response.get_data(as_text=True)

    small = logged_in(content_dir, ADMIN_MAX_UPLOAD_BYTES=100 * 1024)
    response = create(small, [("a.jpg", photo_bytes((2000, 1500)) + b"\0" * 200 * 1024)])
    assert response.status_code == 302
    assert "Слишком большой объём за один раз" in page_messages(small, "/admin")
    assert state(content_dir) == before


def test_new_project_with_photos_needs_login_and_csrf(content_dir):
    before = state(content_dir)
    anonymous = make_app(content_dir).test_client()
    data = {"title": "X", "year": "2025", "photos": [(io.BytesIO(photo_bytes((900, 600))), "a.jpg")]}
    assert anonymous.post("/admin/projects/new", content_type="multipart/form-data", data=data).status_code in (302, 400)

    admin = logged_in(content_dir)
    forged = admin.post("/admin/projects/new", content_type="multipart/form-data",
                        data={**data, "csrf_token": "forged",
                              "photos": [(io.BytesIO(photo_bytes((900, 600))), "a.jpg")]})
    assert forged.status_code == 400
    assert state(content_dir) == before


# --- Добавление в существующий проект и повторы ---

def test_more_photos_can_be_added_to_existing_project(admin, content_dir):
    create(admin, [("1.jpg", photo_bytes((900, 600)))])
    pid = new_project(content_dir)["id"]
    extra = photo_bytes((1000, 700))

    add_photos(admin, pid, [("2.jpg", extra)])

    project = new_project(content_dir)
    assert project["photos"] == [f"portfolio/{pid}/01.webp", f"portfolio/{pid}/02.webp"]
    assert (content_dir / "originals" / "portfolio" / pid / "02.jpg").read_bytes() == extra


def test_same_files_are_not_added_twice(admin, content_dir):
    one, two = photo_bytes((900, 600)), photo_bytes((1000, 700))
    # В одном выборе один и тот же файл дважды
    create(admin, [("1.jpg", one), ("1 копия.jpg", one), ("2.jpg", two)])
    project = new_project(content_dir)
    pid = project["id"]
    assert len(project["photos"]) == 2
    assert "«1 копия.jpg»: уже есть в проекте — пропущено" in page_messages(admin, f"/admin/projects/{pid}")

    # Тот же файл ещё раз — в существующий проект: пропускается, новый — добавляется
    three = photo_bytes((1100, 800))
    add_photos(admin, pid, [("2 again.jpg", two), ("3.jpg", three)])
    project = new_project(content_dir)
    assert len(project["photos"]) == 3
    assert "«2 again.jpg»: уже есть в проекте — пропущено" in page_messages(admin, f"/admin/projects/{pid}")
    assert len(list((content_dir / "originals" / "portfolio" / pid).iterdir())) == 3


def test_deleted_photo_can_be_added_again(admin, content_dir):
    one = photo_bytes((900, 600))
    create(admin, [("1.jpg", one), ("2.jpg", photo_bytes((1000, 700)))])
    pid = new_project(content_dir)["id"]
    token = TOKEN_RE.search(admin.get(f"/admin/projects/{pid}").get_data(as_text=True)).group(1)
    admin.post(f"/admin/projects/{pid}/photo", data={"csrf_token": token, "action": "delete",
                                                     "photo": f"portfolio/{pid}/01.webp"})

    add_photos(admin, pid, [("1.jpg", one)])                     # оригинал удалённого — в истории, не в проекте

    assert len(new_project(content_dir)["photos"]) == 2


def test_other_projects_and_source_files_stay_untouched(admin, content_dir, tmp_path):
    # Фото «на компьютере» — обычные файлы во временной папке; загрузка их только читает
    source = tmp_path / "Мои фото"
    source.mkdir()
    for n in (1, 2):
        (source / f"{n}.jpg").write_bytes(photo_bytes((900 + n, 600)))
    source_state = {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in source.iterdir()}
    others = {k: v for k, v in state(content_dir).items() if not k.endswith("portfolio.json")}

    create(admin, [(p.name, p.read_bytes()) for p in sorted(source.iterdir())])

    assert {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in source.iterdir()} == source_state
    after = state(content_dir)
    assert all(after.get(k) == v for k, v in others.items())   # ничего прежнего не изменилось и не исчезло


def test_zip_import_is_optional_and_folded_away(admin):
    html = admin.get("/admin").get_data(as_text=True)

    assert re.search(r"<details[^>]*>\s*<summary>[^<]*ZIP", html)
    assert html.index("Добавить проект") < html.index("<details")
