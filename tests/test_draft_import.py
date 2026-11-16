"""Импорт подборок фото стендов с диска в черновики: flask --app app import-drafts manifest.json.

Черновик создаётся тем же путём, что ZIP в админке (_store_photos): оригинал байт в байт → WebP
и версии → portfolio.json. Посетители черновик не видят, без планировки он не публикуется.
Повторный запуск ничего не дублирует; совпадение фото или названия с проектом сайта — не импорт.
"""

import json
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from werkzeug.security import generate_password_hash

from app import create_app

PASSWORD = "correct horse battery staple"


@pytest.fixture
def content_dir(make_content):
    return make_content(publish_examples=True)


@pytest.fixture
def app(content_dir):
    return create_app({"TESTING": True, "CONTENT_DIR": str(content_dir), "SECRET_KEY": "test-secret",
                       "ADMIN_USERNAME": "admin", "ADMIN_PASSWORD_HASH": generate_password_hash(PASSWORD)})


def photo(path, seed, size=(1200, 800)):
    """Своя картинка на каждый seed — разное содержимое, разные SHA-256."""
    path.parent.mkdir(parents=True, exist_ok=True)
    base = Image.new("RGB", size, (seed * 37 % 255, seed * 91 % 255, seed * 53 % 255))
    noise = Image.effect_noise(size, 20 + seed).convert("RGB")
    Image.blend(base, noise, 0.3).save(path, "JPEG", quality=90)
    return str(path)


def manifest(tmp_path, stands, name="manifest.json"):
    path = tmp_path / name
    path.write_text(json.dumps({"stands": stands}, ensure_ascii=False), encoding="utf-8")
    return str(path)


def stand(tmp_path, title="Тайга", seeds=(1, 2, 3), **extra):
    folder = tmp_path / "src" / title
    return {"source": f"2026/ЦРВУ/02 Февраль/Продэкспо 26/{title} 21 м2", "title": title, "year": 2026,
            "month": 2, "area": 21, "review": ["Планировка не указана в источнике."],
            "photos": [photo(folder / f"{s:03}.jpg", s) for s in seeds], **extra}


def run(app, path, *args):
    result = app.test_cli_runner().invoke(args=["import-drafts", path, *args])
    assert result.exception is None or isinstance(result.exception, SystemExit), result.output
    return result


def portfolio(content_dir):
    return json.loads((content_dir / "portfolio.json").read_text(encoding="utf-8"))["projects"]


def imported(content_dir, source_part="Тайга"):
    return [p for p in portfolio(content_dir) if source_part in p.get("source", "")]


def files(content_dir):
    return sorted(str(p.relative_to(content_dir)) for p in (content_dir / "media").rglob("*") if p.is_file()) + \
        sorted(str(p.relative_to(content_dir)) for p in (content_dir / "originals").rglob("*") if p.is_file())


def log_in(client):
    import re
    token = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/admin/login").get_data(as_text=True)).group(1)
    assert client.post("/admin/login", data={"csrf_token": token, "username": "admin",
                                             "password": PASSWORD}).status_code == 302
    return re.search(r'name="csrf_token" value="([^"]+)"', client.get("/admin").get_data(as_text=True)).group(1)


def test_import_creates_draft_with_confirmed_fields_only(app, content_dir, tmp_path):
    item = stand(tmp_path)
    result = run(app, manifest(tmp_path, [item]))

    [project] = imported(content_dir)
    assert project["status"] == "draft"
    assert {k: project[k] for k in ("title", "year", "month", "area")} == {"title": "Тайга", "year": 2026,
                                                                           "month": 2, "area": 21}
    assert project["source"] == "2026/ЦРВУ/02 Февраль/Продэкспо 26/Тайга 21 м2"
    assert project["review"] == ["Планировка не указана в источнике."]
    for absent in ("layout", "floors", "exhibition", "description", "video", "import_pending"):
        assert not project.get(absent), absent
    # порядок фото — как в списке; первое — обложка
    assert project["photos"] == [f"portfolio/{project['id']}/{n:02d}.webp" for n in (1, 2, 3)]
    assert project["cover"] == project["photos"][0]
    media = content_dir / "media" / "portfolio" / project["id"]
    assert sorted(p.name for p in media.iterdir()) == sorted(
        f"{n:02d}{suffix}.webp" for n in (1, 2, 3) for suffix in ("", "-640"))   # 1200 px: версии не крупнее фото
    originals = content_dir / "originals" / "portfolio" / project["id"]
    assert sorted(p.name for p in originals.iterdir()) == ["01.jpg", "02.jpg", "03.jpg"]
    # оригинал — байт в байт
    assert (originals / "01.jpg").read_bytes() == Path(item["photos"][0]).read_bytes()
    assert "Создан черновик" in result.output


def test_imported_draft_is_invisible_to_visitors(app, content_dir, tmp_path):
    run(app, manifest(tmp_path, [stand(tmp_path, title="Невидимка")]))
    [project] = imported(content_dir, "Невидимка")
    client = app.test_client()

    for path in ("/", "/portfolio", "/about", "/contacts"):
        html = client.get(path).get_data(as_text=True)
        assert "Невидимка" not in html and project["id"] not in html
    assert client.get(f"/media/{project['photos'][0]}").status_code == 404
    assert client.get(f"/media/portfolio/{project['id']}/01-640.webp").status_code == 404
    assert client.get(f"/media/../originals/portfolio/{project['id']}/01.jpg").status_code == 404


def test_admin_sees_draft_photos_and_source_and_cannot_publish_without_layout(app, content_dir, tmp_path):
    run(app, manifest(tmp_path, [stand(tmp_path)]))
    [project] = imported(content_dir)
    client = app.test_client()
    token = log_in(client)

    page = client.get(f"/admin/projects/{project['id']}").get_data(as_text=True)
    assert "Черновик — посетители его не видят" in page
    assert "2026/ЦРВУ/02 Февраль/Продэкспо 26/Тайга 21 м2" in page
    assert "Планировка не указана в источнике." in page
    assert client.get(f"/media/{project['photos'][0]}").status_code == 200       # админ видит фото
    listing = client.get("/admin")
    assert listing.status_code == 200 and "Тайга" in listing.get_data(as_text=True)

    client.post(f"/admin/projects/{project['id']}/status", data={"csrf_token": token, "status": "published"})
    assert imported(content_dir)[0]["status"] == "draft"


def test_admin_can_fill_fields_and_keep_source(app, content_dir, tmp_path):
    run(app, manifest(tmp_path, [stand(tmp_path)]))
    [project] = imported(content_dir)
    client = app.test_client()
    token = log_in(client)

    response = client.post(f"/admin/projects/{project['id']}", data={
        "csrf_token": token, "title": "Тайга", "exhibition": "Продэкспо", "year": "2026", "month": "2",
        "area": "21", "layout": "Угловой", "floors": "", "description": "", "video": ""})
    assert response.status_code == 302
    saved = imported(content_dir)[0]
    assert saved["layout"] == "Угловой" and saved["status"] == "draft"
    assert saved["source"] == project["source"]


def test_rerun_creates_nothing_new(app, content_dir, tmp_path):
    path = manifest(tmp_path, [stand(tmp_path)])
    run(app, path)
    before = (portfolio(content_dir), files(content_dir))

    result = run(app, path)

    assert (portfolio(content_dir), files(content_dir)) == before
    assert "уже импортирован" in result.output


def test_interrupted_import_resumes_without_duplicates(app, content_dir, tmp_path):
    full = stand(tmp_path, seeds=(1, 2, 3, 4))
    run(app, manifest(tmp_path, [{**full, "photos": full["photos"][:2]}], "part.json"))
    # прерванный импорт: пометка осталась
    data = json.loads((content_dir / "portfolio.json").read_text(encoding="utf-8"))
    next(p for p in data["projects"] if p.get("source") == full["source"])["import_pending"] = True
    (content_dir / "portfolio.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    run(app, manifest(tmp_path, [full]))

    [project] = imported(content_dir)
    assert len(project["photos"]) == 4 and "import_pending" not in project


def test_photo_matching_existing_project_is_reported_not_imported(app, content_dir, tmp_path):
    existing = next(p for p in portfolio(content_dir) if p["status"] == "published")
    originals = content_dir / "originals" / "portfolio" / existing["id"]
    originals.mkdir(parents=True, exist_ok=True)
    copy = stand(tmp_path, title="Повтор")
    (originals / "99.jpg").write_bytes(Path(copy["photos"][1]).read_bytes())
    before = portfolio(content_dir)

    result = run(app, manifest(tmp_path, [copy]))

    assert portfolio(content_dir) == before
    assert "возможный дубль" in result.output and existing["id"] in result.output


def test_same_title_and_year_is_reported_not_imported(app, content_dir, tmp_path):
    existing = next(p for p in portfolio(content_dir) if p["status"] == "published")
    clash = stand(tmp_path, title=existing["title"].upper(), year=existing["year"])
    before = portfolio(content_dir)

    result = run(app, manifest(tmp_path, [clash]))

    assert portfolio(content_dir) == before
    assert "возможный дубль" in result.output


def test_identical_files_in_one_stand_are_stored_once(app, content_dir, tmp_path):
    item = stand(tmp_path, seeds=(1, 2))
    item["photos"].append(item["photos"][0])
    run(app, manifest(tmp_path, [item]))
    assert len(imported(content_dir)[0]["photos"]) == 2


def test_broken_file_is_reported_and_others_imported(app, content_dir, tmp_path):
    item = stand(tmp_path, seeds=(1, 2))
    bad = tmp_path / "src" / "битый.jpg"
    bad.write_bytes(b"not a jpeg")
    item["photos"].insert(1, str(bad))
    result = run(app, manifest(tmp_path, [item]))
    assert len(imported(content_dir)[0]["photos"]) == 2
    assert "битый.jpg" in result.output


def test_stand_without_photos_is_not_created(app, content_dir, tmp_path):
    result = run(app, manifest(tmp_path, [{**stand(tmp_path), "photos": []}]))
    assert imported(content_dir) == [] and "нет фото" in result.output


def test_dry_run_writes_nothing(app, content_dir, tmp_path):
    before = ((content_dir / "portfolio.json").read_bytes(), files(content_dir))
    result = run(app, manifest(tmp_path, [stand(tmp_path)]), "--dry-run")
    assert ((content_dir / "portfolio.json").read_bytes(), files(content_dir)) == before
    assert "будет создан" in result.output


def test_published_projects_are_untouched(app, content_dir, tmp_path):
    published = [p for p in portfolio(content_dir) if p["status"] == "published"]
    run(app, manifest(tmp_path, [stand(tmp_path)]))
    assert [p for p in portfolio(content_dir) if p["status"] == "published"] == published
