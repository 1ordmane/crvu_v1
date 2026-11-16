"""Импорт стенда из ZIP в админке: одна папка с фото → черновик проекта.

Фото из архива проходят тот же путь, что и обычная загрузка (проверки, оригинал байт в байт,
WebP без EXIF, откат при ошибках). Архив на диск не распаковывается; исходные папки на
компьютере (project_photos/) импорт не трогает.
"""

import hashlib
import io
import json
import re
import shutil
import zipfile
from datetime import date

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


def make_zip(entries, compression=zipfile.ZIP_DEFLATED):
    """entries: [(имя, байты)] или [(ZipInfo, байты)] → байты ZIP."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression) as zf:
        for name, data in entries:
            if isinstance(name, zipfile.ZipInfo):
                zf.writestr(name, data)
            elif name.endswith("/"):
                zf.writestr(zipfile.ZipInfo(name), b"")
            else:
                zf.writestr(name, data)
    return buf.getvalue()


def import_zip(client, data, filename="stand.zip", year=None, title=""):
    token = TOKEN_RE.search(client.get("/admin").get_data(as_text=True)).group(1)
    form = {"csrf_token": token, "year": str(year if year is not None else date.today().year), "title": title,
            "exhibition": "Выставка", "month": "3", "area": "24.5", "layout": "Полуостров",
            "archive": (io.BytesIO(data), filename)}
    return client.post("/admin/projects/import", content_type="multipart/form-data", data=form)


def projects(content_dir):
    return json.loads((content_dir / "portfolio.json").read_text(encoding="utf-8"))["projects"]


def messages(client, path="/admin"):
    return re.findall(r'class="message[^"]*">(.*?)<', client.get(path).get_data(as_text=True))


def state(content_dir):
    """Всё, что импорт может изменить: портфолио, фото, оригиналы, история (без .cache и .lock)."""
    return {str(p.relative_to(content_dir)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(content_dir.rglob("*")) if p.is_file() and ".cache" not in p.parts and p.name != ".lock"}


# --- Удачный импорт ---

def test_zip_creates_draft_with_photos_in_natural_order(admin, content_dir):
    first, second, tenth = gps_photo_bytes((1600, 1200)), photo_bytes((1200, 900)), photo_bytes((900, 600))
    data = make_zip([
        ("Expo Moscow 2025/", b""),
        ("Expo Moscow 2025/10.jpg", tenth),
        ("Expo Moscow 2025/2.jpg", second),
        ("Expo Moscow 2025/01.jpg", first),
        ("__MACOSX/Expo Moscow 2025/._01.jpg", b"apple double"),        # мусор macOS — молча пропускается
        ("Expo Moscow 2025/.DS_Store", b"finder"),
    ])
    before = {p["id"] for p in projects(content_dir)}

    response = import_zip(admin, data, year=2025)

    [project] = [p for p in projects(content_dir) if p["id"] not in before]
    assert response.status_code == 302 and response.headers["Location"].endswith(f"/admin/projects/{project['id']}")
    assert project["status"] == "draft" and project["title"] == "Expo Moscow 2025" and project["year"] == 2025
    pid = project["id"]
    assert project["photos"] == [f"portfolio/{pid}/01.webp", f"portfolio/{pid}/02.webp", f"portfolio/{pid}/03.webp"]
    assert project["cover"] == project["photos"][0]
    # Порядок — по именам: 01, 2, 10; оригиналы байт в байт, закрыты (0600)
    originals = content_dir / "originals" / "portfolio" / pid
    assert [(originals / f"0{n}.jpg").read_bytes() for n in (1, 2, 3)] == [first, second, tenth]
    assert all((originals / f"0{n}.jpg").stat().st_mode & 0o777 == 0o600 for n in (1, 2, 3))
    # На сайт — WebP без EXIF и геометки, с версиями
    with Image.open(content_dir / "media" / "portfolio" / pid / "01.webp") as img:
        assert img.format == "WEBP" and not img.getexif()
    assert (content_dir / "media" / "portfolio" / pid / "01-640.webp").is_file()
    page = " ".join(messages(admin, f"/admin/projects/{pid}"))
    assert "Создан черновик «Expo Moscow 2025»" in page and "Загружено фото: 3" in page
    assert "DS_Store" not in page and "__MACOSX" not in page


def test_title_field_wins_and_root_files_take_title_from_archive_name(admin, content_dir):
    import_zip(admin, make_zip([("01.jpg", photo_bytes((900, 600)))]), filename="Haval 2025.zip")
    import_zip(admin, make_zip([("Suzuki/01.jpg", photo_bytes((900, 600)))]), title="Suzuki — стенд 2024", year=2024)

    titles = {p["title"]: p for p in projects(content_dir) if p["status"] == "draft"}
    assert "Haval 2025" in titles
    assert titles["Suzuki — стенд 2024"]["year"] == 2024


def test_other_files_are_skipped_with_reason_and_photos_still_imported(admin, content_dir):
    data = make_zip([("Stand/01.jpg", photo_bytes((900, 600))), ("Stand/notes.txt", b"text"),
                     ("Stand/02.heic", b"heic")])

    import_zip(admin, data)

    project = projects(content_dir)[-1]
    assert len(project["photos"]) == 1
    page = " ".join(messages(admin, f"/admin/projects/{project['id']}"))
    assert "«notes.txt»: пропущен — не фото" in page
    assert "«02.heic»: пропущен — HEIC не поддерживается" in page


def test_broken_photo_keeps_its_original_in_history(admin, content_dir):
    good = photo_bytes((900, 600))
    broken = photo_bytes((2000, 1500))
    broken = broken[: len(broken) // 2]
    data = make_zip([("Stand/01.jpg", good), ("Stand/02.jpg", broken)])

    import_zip(admin, data)

    project = projects(content_dir)[-1]
    assert len(project["photos"]) == 1
    kept = list((content_dir / ".history" / "originals").rglob("*.jpg"))
    assert [p.read_bytes() for p in kept] == [broken]
    assert "«02.jpg»: файл изображения повреждён" in " ".join(messages(admin, f"/admin/projects/{project['id']}"))


def test_too_big_member_is_rejected_others_imported(content_dir):
    client = logged_in(content_dir, ADMIN_MAX_IMAGE_BYTES=MB)
    small = photo_bytes((900, 600))
    big = small + b"\0" * (MB + 10)                                 # JPEG с «хвостом» — больше 1 МБ
    data = make_zip([("Stand/01.jpg", small), ("Stand/02.jpg", big)], compression=zipfile.ZIP_STORED)

    import_zip(client, data)

    project = projects(content_dir)[-1]
    assert len(project["photos"]) == 1
    assert "«02.jpg»: файл больше 1 МБ" in " ".join(messages(client, f"/admin/projects/{project['id']}"))


# --- Архив отклоняется целиком, ничего не создаётся ---

def symlink_entry():
    info = zipfile.ZipInfo("Stand/link.jpg")
    info.external_attr = (0o120777 << 16)
    return info, b"/etc/passwd"


def encrypted_zip():
    """Архив с флагом шифрования: zipfile при записи его сбрасывает, поэтому бит ставится в заголовках."""
    data = bytearray(make_zip([("Stand/01.jpg", photo_bytes((900, 600)))]))
    for signature, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):   # локальный и центральный заголовки
        at = data.index(signature) + offset
        data[at] |= 0x1
    assert zipfile.ZipFile(io.BytesIO(bytes(data))).infolist()[0].flag_bits & 0x1
    return bytes(data)


BAD_ARCHIVES = [
    ("not a zip", lambda: b"PK\x03\x04 not really", "не является ZIP-архивом"),
    ("path traversal", lambda: make_zip([("../evil.jpg", photo_bytes((900, 600)))]), "недопустимый путь"),
    ("absolute path", lambda: make_zip([("/tmp/evil.jpg", photo_bytes((900, 600)))]), "недопустимый путь"),
    ("symlink", lambda: make_zip([symlink_entry()]), "символическая ссылка"),
    ("encrypted", encrypted_zip, "зашифрован"),
    ("nested folders", lambda: make_zip([("Stand/sub/01.jpg", photo_bytes((900, 600)))]), "вложенные папки"),
    ("two stands", lambda: make_zip([("A/01.jpg", photo_bytes((900, 600))), ("B/01.jpg", photo_bytes((900, 600)))]),
     "несколько папок"),
    ("too many photos", lambda: make_zip([(f"Stand/{n:02d}.jpg", b"x") for n in range(21)]), "не больше 20"),
    ("zip bomb", lambda: make_zip([("Stand/01.jpg", b"\0" * (5 * MB))]), "подозрительно сильно сжат"),
    ("no photos", lambda: make_zip([("Stand/notes.txt", b"text")]), "нет фото"),
    ("empty", lambda: make_zip([]), "нет фото"),
]


@pytest.mark.parametrize("name, build, message", BAD_ARCHIVES, ids=[b[0] for b in BAD_ARCHIVES])
def test_bad_archive_is_rejected_without_changes(admin, content_dir, name, build, message):
    before = state(content_dir)

    response = import_zip(admin, build())

    assert response.status_code == 302 and response.headers["Location"].endswith("/admin")
    assert message in " ".join(messages(admin))
    assert state(content_dir) == before


def test_all_photos_broken_creates_nothing(admin, content_dir):
    before = {p["id"] for p in projects(content_dir)}
    broken = photo_bytes((2000, 1500))[:5000]

    import_zip(admin, make_zip([("Stand/01.jpg", broken)]))

    assert {p["id"] for p in projects(content_dir)} == before
    assert "Проект не создан" in " ".join(messages(admin))
    assert not list((content_dir / "media" / "portfolio").glob("stand*"))


@pytest.mark.parametrize("year", ["", "двадцать", "1800"])
def test_year_is_required(admin, content_dir, year):
    before = state(content_dir)
    token = TOKEN_RE.search(admin.get("/admin").get_data(as_text=True)).group(1)

    admin.post("/admin/projects/import", content_type="multipart/form-data",
               data={"csrf_token": token, "year": year, "title": "",
                     "archive": (io.BytesIO(make_zip([("S/01.jpg", photo_bytes((900, 600)))])), "s.zip")})

    assert "Год — число" in " ".join(messages(admin))
    assert state(content_dir) == before


def test_missing_archive_is_reported(admin, content_dir):
    token = TOKEN_RE.search(admin.get("/admin").get_data(as_text=True)).group(1)
    admin.post("/admin/projects/import", data={"csrf_token": token, "year": "2025"})

    assert "Выберите ZIP-архив" in " ".join(messages(admin))


def test_request_over_limit_shows_message_and_creates_nothing(content_dir):
    client = logged_in(content_dir, ADMIN_MAX_UPLOAD_BYTES=200 * 1024)
    before = state(content_dir)
    data = make_zip([("Stand/01.jpg", photo_bytes((2000, 1500)) + b"\0" * 300 * 1024)], compression=zipfile.ZIP_STORED)

    response = import_zip(client, data)

    assert response.status_code == 302
    assert "Слишком большой объём за один раз" in " ".join(messages(client))
    assert state(content_dir) == before


def test_import_needs_login_and_csrf(content_dir):
    client = make_app(content_dir).test_client()
    data = make_zip([("Stand/01.jpg", photo_bytes((900, 600)))])
    before = state(content_dir)

    anonymous = client.post("/admin/projects/import", content_type="multipart/form-data",
                            data={"year": "2025", "archive": (io.BytesIO(data), "s.zip")})
    assert anonymous.status_code in (302, 400)

    admin = logged_in(content_dir)
    forged = admin.post("/admin/projects/import", content_type="multipart/form-data",
                        data={"csrf_token": "forged", "year": "2025", "archive": (io.BytesIO(data), "s.zip")})
    assert forged.status_code == 400
    assert state(content_dir) == before


def test_projects_page_has_import_form(admin):
    html = admin.get("/admin").get_data(as_text=True)

    assert 'action="/admin/projects/import"' in html and 'enctype="multipart/form-data"' in html
    assert 'name="archive"' in html and 'accept=".zip' in html and 'name="year"' in html


# --- Исходные подборки на компьютере не меняются ---

def test_source_folder_stays_untouched(admin, content_dir, tmp_path):
    # Как у пользователя: project_photos/<стенд>/01.jpg… рядом с content/ → ZIP → импорт
    stand = content_dir.parent / "project_photos" / "Expo Moscow 2025"
    stand.mkdir(parents=True)
    (stand / "01.jpg").write_bytes(gps_photo_bytes((1600, 1200)))
    (stand / "02.jpg").write_bytes(photo_bytes((900, 600)))
    source_state = {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in stand.iterdir()}
    archive = shutil.make_archive(str(tmp_path / "Expo Moscow 2025"), "zip", stand.parent, stand.name)

    with open(archive, "rb") as f:
        import_zip(admin, f.read(), filename="Expo Moscow 2025.zip")

    assert {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in stand.iterdir()} == source_state
    assert projects(content_dir)[-1]["title"] == "Expo Moscow 2025" and len(projects(content_dir)[-1]["photos"]) == 2
