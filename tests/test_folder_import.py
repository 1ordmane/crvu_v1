"""«Импортировать папку»: выбор папки проекта в браузере → черновик с фото.

Страница отправляет форму на обычное создание проекта (new_project → _store_photos), второго
конвейера нет. Браузер присылает все файлы папки; сервер пропускает служебные молча, прочие не-фото —
с сообщением. Чистые функции скрипта (отбор, порядок, название, лимиты) проверяются в Node.
"""

import hashlib
import io
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_image_sizes import PASSWORD, TOKEN_RE, gps_photo_bytes, make_app, photo_bytes

SCRIPT = Path(__file__).resolve().parent.parent / "app" / "static" / "js" / "admin-import.js"
FOLDER = "Агрос. Трансфаир 40м2"


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


def send_folder(client, files, title=FOLDER, year="2026", publish=False):
    """Как браузер после выбора папки: имена файлов — с папкой («Папка/01.jpg»)."""
    token = TOKEN_RE.search(client.get("/admin/projects/import-folder").get_data(as_text=True)).group(1)
    form = {"csrf_token": token, "title": title, "year": year, "exhibition": "Трансфаир", "month": "1", "area": "40", "layout": "Угловой",
            "photos": [(io.BytesIO(data), f"{FOLDER}/{name}") for name, data in files]}
    if publish:
        form["publish"] = "on"
    return client.post("/admin/projects/new", content_type="multipart/form-data", data=form)


def project(content_dir, title=FOLDER):
    return next(p for p in json.loads((content_dir / "portfolio.json").read_text(encoding="utf-8"))["projects"]
                if p["title"] == title)


def page_text(client, path):
    return " ".join(re.findall(r'class="message[^"]*">(.*?)<', client.get(path).get_data(as_text=True)))


def state(content_dir):
    return {str(p.relative_to(content_dir)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(content_dir.rglob("*")) if p.is_file() and ".cache" not in p.parts and p.name != ".lock"}


# --- Страница ---

def test_projects_page_links_to_folder_import(admin):
    html = admin.get("/admin").get_data(as_text=True)

    assert 'href="/admin/projects/import-folder"' in html and "Импортировать папку" in html


def test_folder_page_posts_to_regular_project_creation(admin):
    response = admin.get("/admin/projects/import-folder")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert re.search(r'<form[^>]*action="/admin/projects/new"[^>]*enctype="multipart/form-data"', html) \
        or re.search(r'<form[^>]*enctype="multipart/form-data"[^>]*action="/admin/projects/new"', html)
    assert re.search(r'<input type="file" name="photos" multiple webkitdirectory', html)
    assert 'name="title"' in html and 'name="year"' in html
    assert re.search(r'<input type="checkbox" name="publish">', html)          # по умолчанию — черновик
    assert "/static/js/admin-import.js" in html
    assert "<script>" not in html and " style=" not in html and " onclick=" not in html


def test_only_folder_page_allows_blob_previews(admin):
    folder_csp = admin.get("/admin/projects/import-folder").headers["Content-Security-Policy"]
    other_csp = admin.get("/admin").headers["Content-Security-Policy"]

    assert "img-src 'self' blob:" in folder_csp and "script-src 'self'" in folder_csp
    assert "blob:" not in other_csp


def test_folder_page_needs_login(content_dir):
    response = make_app(content_dir).test_client().get("/admin/projects/import-folder")

    assert response.status_code == 302 and response.headers["Location"].endswith("/admin/login")


# --- Отправка папки ---

def test_folder_files_become_draft_and_junk_is_skipped(admin, content_dir):
    one, two, extra = gps_photo_bytes((1600, 1200)), photo_bytes((1200, 900)), photo_bytes((900, 600))
    files = [("01.jpg", one), ("02.jpg", two), ("2026.01.21 Transfaire 051.jpg", extra),
             ("Трансфаир (2).pdf", b"%PDF-1.4 brochure"), ("Thumbs.db", b"thumbs"), (".DS_Store", b"finder")]

    response = send_folder(admin, files)

    p = project(content_dir)
    pid = p["id"]
    assert response.status_code == 302 and response.headers["Location"].endswith(f"/admin/projects/{pid}")
    assert p["status"] == "draft"
    assert p["photos"] == [f"portfolio/{pid}/0{n}.webp" for n in (1, 2, 3)] and p["cover"] == p["photos"][0]
    originals = content_dir / "originals" / "portfolio" / pid
    assert [(originals / f"0{n}.jpg").read_bytes() for n in (1, 2, 3)] == [one, two, extra]
    text = page_text(admin, f"/admin/projects/{pid}")
    assert "«Трансфаир (2).pdf»: пропущен — не фото" in text
    assert "Thumbs.db" not in text and "DS_Store" not in text
    assert "сохранён черновиком. Загружено фото: 3" in text


def test_order_and_cover_come_from_the_page(admin, content_dir):
    # Скрипт страницы отправляет фото в выбранном порядке (обложка — первым)
    first, second, third = photo_bytes((900, 600)), photo_bytes((1000, 700)), photo_bytes((1100, 800))

    send_folder(admin, [("03.jpg", third), ("01.jpg", first), ("02.jpg", second)])

    originals = content_dir / "originals" / "portfolio" / project(content_dir)["id"]
    assert [(originals / f"0{n}.jpg").read_bytes() for n in (1, 2, 3)] == [third, first, second]


def test_file_limit_counts_only_photos(content_dir):
    client = logged_in(content_dir, ADMIN_MAX_FILES=2)
    photos = [("01.jpg", photo_bytes((900, 600))), ("02.jpg", photo_bytes((1000, 700)))]

    send_folder(client, photos + [("a.pdf", b"%PDF"), ("Thumbs.db", b"x"), (".DS_Store", b"y")])
    assert len(project(content_dir)["photos"]) == 2

    response = send_folder(client, photos + [("03.jpg", photo_bytes((1100, 800)))], title="Ещё стенд")
    assert response.status_code == 400 and "не больше 2 файлов" in response.get_data(as_text=True)


def test_folder_without_photos_gives_draft_and_explains(admin, content_dir):
    send_folder(admin, [("Трансфаир (2).pdf", b"%PDF"), ("Thumbs.db", b"x")])

    p = project(content_dir)
    assert p["status"] == "draft" and p["photos"] == []
    assert "без фото проект не публикуется" in page_text(admin, f"/admin/projects/{p['id']}")


def test_heic_is_reported(admin, content_dir):
    send_folder(admin, [("01.jpg", photo_bytes((900, 600))), ("IMG_0001.HEIC", b"heic")])

    assert "«IMG_0001.HEIC»: пропущен — HEIC не поддерживается" in page_text(
        admin, f"/admin/projects/{project(content_dir)['id']}")


def test_existing_projects_and_source_folder_stay_untouched(admin, content_dir, tmp_path):
    source = tmp_path / "Photo Stend" / FOLDER
    source.mkdir(parents=True)
    for name, data in [("01.jpg", photo_bytes((900, 600))), ("02.jpg", photo_bytes((1000, 700))),
                       ("Трансфаир (2).pdf", b"%PDF"), ("Thumbs.db", b"x")]:
        (source / name).write_bytes(data)
    before_source = {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in source.iterdir()}
    others = {k: v for k, v in state(content_dir).items() if not k.endswith("portfolio.json")}

    send_folder(admin, [(p.name, p.read_bytes()) for p in sorted(source.iterdir())])

    assert {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in source.iterdir()} == before_source
    after = state(content_dir)
    assert all(after.get(k) == v for k, v in others.items())


# --- Чистые функции скрипта (Node) ---

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="нужен Node.js")


def js(expression):
    code = f"const m = require({json.dumps(str(SCRIPT))}); process.stdout.write(JSON.stringify({expression}));"
    return json.loads(subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=20,
                                     check=True).stdout)


@needs_node
def test_js_picks_photos_and_ignores_junk():
    names = ["Агрос/01.jpg", "Агрос/02.JPG", "Агрос/03.jpeg", "Агрос/04.png", "Агрос/05.webp",
             "Агрос/Трансфаир (2).pdf", "Агрос/Thumbs.db", "Агрос/.DS_Store", "Агрос/._01.jpg", "Агрос/IMG.heic"]

    assert js(f"{json.dumps(names)}.map(m.kind)") == ["photo"] * 5 + ["other", "junk", "junk", "junk", "heic"]


@needs_node
def test_js_natural_order_and_folder_title():
    names = ["P/10.jpg", "P/2.jpg", "P/01.jpg", "P/2026.01.21 Transfaire 052.jpg", "P/2026.01.21 Transfaire 051.jpg"]

    assert js(f"{json.dumps(names)}.slice().sort(m.naturalCompare)") == [
        "P/01.jpg", "P/2.jpg", "P/10.jpg", "P/2026.01.21 Transfaire 051.jpg", "P/2026.01.21 Transfaire 052.jpg"]
    assert js(f"m.folderName({json.dumps('Агрос. Трансфаир 40м2/01.jpg')})") == "Агрос. Трансфаир 40м2"
    assert js("m.folderName('01.jpg')") == ""


@needs_node
def test_js_reorder_cover_and_remove():
    assert js("m.move(['a','b','c'], 2, -1)") == ["a", "c", "b"]
    assert js("m.move(['a','b','c'], 0, -1)") == ["a", "b", "c"]                # дальше некуда
    assert js("m.toFront(['a','b','c'], 2)") == ["c", "a", "b"]                 # «Обложка»
    assert js("m.without(['a','b','c'], 1)") == ["a", "c"]


@needs_node
def test_js_limits_match_server():
    mb = 1024 * 1024
    ok = js(f"m.check([{10 * mb}, {10 * mb}], {{files: 20, fileBytes: {40 * mb}, totalBytes: {200 * mb}}})")
    assert ok == []
    problems = js(f"m.check([{41 * mb}, ...Array(20).fill({10 * mb})], "
                  f"{{files: 20, fileBytes: {40 * mb}, totalBytes: {200 * mb}}})")
    assert any("не больше 20" in p for p in problems)
    assert any("больше 40 МБ" in p for p in problems)
    assert any("200 МБ" in p for p in problems)
