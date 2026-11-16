import io
import json
from pathlib import Path
import re
import shutil

import pytest
from PIL import Image
from werkzeug.security import generate_password_hash

from app import create_app, uploads

PASSWORD = "correct horse battery staple"
TOKEN_RE = re.compile(r'name="csrf_token" value="([^"]+)"')
PROJECT = "example-2020-1"


@pytest.fixture
def content_dir(make_content):
    return make_content(publish_examples=True)


def make_app(content_dir, **config):
    return create_app({"TESTING": True, "CONTENT_DIR": str(content_dir), "SECRET_KEY": "s",
                       "ADMIN_USERNAME": "admin", "ADMIN_PASSWORD_HASH": generate_password_hash(PASSWORD), **config})


@pytest.fixture
def admin(content_dir):
    client = make_app(content_dir).test_client()
    token = TOKEN_RE.search(client.get("/admin/login").get_data(as_text=True)).group(1)
    client.post("/admin/login", data={"csrf_token": token, "username": "admin", "password": PASSWORD})
    return client


def photo_bytes(size, orientation=None, quality=92, fmt="JPEG"):
    """Фото с деталями (шум поверх градиента), как снимок стенда, а не однотонная заливка."""
    w, h = size
    base = Image.linear_gradient("L").resize(size).convert("RGB")
    noise = Image.merge("RGB", [Image.effect_noise(size, 30 + 8 * i) for i in range(3)])
    img = Image.blend(base, noise, 0.35)
    buf = io.BytesIO()
    kwargs = {"quality": quality} if fmt == "JPEG" else {}
    if orientation:
        exif = Image.Exif()
        exif[0x0112] = orientation
        exif[0x010F] = "TestCam"
        kwargs["exif"] = exif.tobytes()
    img.save(buf, fmt, **kwargs)
    return buf.getvalue()


def upload(client, data, name="photo.jpg", project_id=PROJECT):
    token = TOKEN_RE.search(client.get(f"/admin/projects/{project_id}").get_data(as_text=True)).group(1)
    return client.post(f"/admin/projects/{project_id}/photos", content_type="multipart/form-data",
                       data={"csrf_token": token, "photos": (io.BytesIO(data), name)})


def folder(content_dir, project_id=PROJECT):
    return content_dir / "media" / "portfolio" / project_id


def originals(content_dir, project_id=PROJECT):
    return content_dir / "originals" / "portfolio" / project_id


def messages(client, project_id=PROJECT):
    return client.get(f"/admin/projects/{project_id}").get_data(as_text=True)


def project(content_dir):
    data = json.loads((content_dir / "portfolio.json").read_text(encoding="utf-8"))
    return next(p for p in data["projects"] if p["id"] == PROJECT)


def test_large_camera_photo_gets_main_file_and_three_sizes(admin, content_dir):
    data = photo_bytes((6000, 4000), orientation=6, quality=97)
    assert len(data) > 10 * 1024 * 1024                    # больше прежнего предела 10 МБ

    assert upload(admin, data).status_code == 302

    files = sorted(p.name for p in folder(content_dir).iterdir())
    assert files == ["01-1280.webp", "01-1920.webp", "01-640.webp", "01.webp"]
    sizes = {}
    for name in files:
        with Image.open(folder(content_dir) / name) as img:
            sizes[name] = img.size
            assert img.format == "WEBP" and not img.getexif()    # без EXIF и геометки
    # Поворот по EXIF применён: снимок портретный; основной файл — до 2560 px
    assert sizes["01.webp"] == (1707, 2560)
    assert max(sizes["01-640.webp"]) == 640 and max(sizes["01-1280.webp"]) == 1280 and max(sizes["01-1920.webp"]) == 1920
    # В портфолио — только основной файл, версии отдельными фото не считаются
    assert project(content_dir)["photos"][-1] == f"portfolio/{PROJECT}/01.webp"
    assert all("-640" not in p for p in project(content_dir)["photos"])


def test_small_photo_is_not_upscaled(admin, content_dir):
    upload(admin, photo_bytes((900, 600)))

    assert sorted(p.name for p in folder(content_dir).iterdir()) == ["01-640.webp", "01.webp"]
    with Image.open(folder(content_dir) / "01.webp") as img:
        assert img.size == (900, 600)


def test_too_big_file_and_broken_file_are_rejected_without_leftovers(content_dir):
    client = make_app(content_dir, ADMIN_MAX_IMAGE_BYTES=1024 * 1024).test_client()
    token = TOKEN_RE.search(client.get("/admin/login").get_data(as_text=True)).group(1)
    client.post("/admin/login", data={"csrf_token": token, "username": "admin", "password": PASSWORD})

    # Сообщение показывается на странице сразу после загрузки (следующий запрос его уже не видит)
    upload(client, photo_bytes((3000, 2000), quality=98))
    assert "файл больше 1 МБ" in client.get(f"/admin/projects/{PROJECT}").get_data(as_text=True)
    upload(client, b"\xff\xd8\xff\xe0 not really a jpeg", "broken.jpg")
    assert "повреждён" in client.get(f"/admin/projects/{PROJECT}").get_data(as_text=True)
    assert not folder(content_dir).exists()


def test_failed_write_leaves_no_partial_files(admin, content_dir, monkeypatch):
    real = uploads._write_atomic

    def fail_on_main(img, path, quality):
        if not uploads.is_variant(path.name):
            raise OSError("диск заполнен")
        real(img, path, quality)
    monkeypatch.setattr(uploads, "_write_atomic", fail_on_main)

    upload(admin, photo_bytes((2000, 1500)))

    assert list(folder(content_dir).iterdir()) == []       # версии, записанные до сбоя, удалены
    assert "не удалось сохранить фото" in admin.get(f"/admin/projects/{PROJECT}").get_data(as_text=True)


def test_deleting_photo_moves_its_sizes_and_original_to_history_together(admin, content_dir):
    data = photo_bytes((2000, 1500))
    upload(admin, data)
    photo = f"portfolio/{PROJECT}/01.webp"
    token = TOKEN_RE.search(admin.get(f"/admin/projects/{PROJECT}").get_data(as_text=True)).group(1)

    admin.post(f"/admin/projects/{PROJECT}/photo", data={"csrf_token": token, "action": "delete", "photo": photo})

    assert not any(folder(content_dir).iterdir()) and not any(originals(content_dir).iterdir())
    assert photo not in project(content_dir)["photos"]
    # Все файлы фото — в одной папке истории (одно время удаления), с прежней структурой путей
    history = content_dir / ".history"
    stamps = {p.name for p in (history / "media").iterdir()} | {p.name for p in (history / "originals").iterdir()}
    assert len(stamps) == 1
    stamp = stamps.pop()
    kept = history / "media" / stamp / "portfolio" / PROJECT
    # 2000 px: версия 1920 меньше исходника, поэтому она тоже есть
    assert sorted(p.name for p in kept.iterdir()) == ["01-1280.webp", "01-1920.webp", "01-640.webp", "01.webp"]
    assert (history / "originals" / stamp / "portfolio" / PROJECT / "01.jpg").read_bytes() == data


def test_photo_restored_from_history_is_served_again(admin, content_dir):
    upload(admin, photo_bytes((2000, 1500)))
    photo = f"portfolio/{PROJECT}/01.webp"
    before = (content_dir / "media" / photo).read_bytes()
    token = TOKEN_RE.search(admin.get(f"/admin/projects/{PROJECT}").get_data(as_text=True)).group(1)
    admin.post(f"/admin/projects/{PROJECT}/photo", data={"csrf_token": token, "action": "delete", "photo": photo})

    # Восстановление вручную: файлы из .history/<раздел>/<время>/… возвращаются на прежние места
    history = content_dir / ".history"
    for section in ("media", "originals"):
        stamp_dir = next((history / section).iterdir())
        for file in stamp_dir.rglob("*"):
            if file.is_file():
                target = content_dir / section / file.relative_to(stamp_dir)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(file, target)
    data = json.loads((content_dir / "portfolio.json").read_text(encoding="utf-8"))
    next(p for p in data["projects"] if p["id"] == PROJECT)["photos"].append(photo)
    (content_dir / "portfolio.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    response = make_app(content_dir).test_client().get(f"/media/{photo}")
    assert response.status_code == 200 and response.data == before
    assert (originals(content_dir) / "01.jpg").is_file()


def test_deleting_project_moves_its_originals_to_history(admin, content_dir):
    upload(admin, photo_bytes((900, 600)))
    token = TOKEN_RE.search(admin.get(f"/admin/projects/{PROJECT}/delete").get_data(as_text=True)).group(1)

    admin.post(f"/admin/projects/{PROJECT}/delete", data={"csrf_token": token})

    assert not folder(content_dir).exists() and not originals(content_dir).exists()
    assert len(list((content_dir / ".history" / "originals").rglob(f"{PROJECT}/01.jpg"))) == 1
    assert len(list((content_dir / ".history" / "media").rglob(f"{PROJECT}/01.webp"))) == 1


# --- Оригиналы ---

def gps_photo_bytes(size):
    img = Image.open(io.BytesIO(photo_bytes(size)))
    exif = Image.Exif()
    exif[0x010F] = "TestCam"
    exif.get_ifd(0x8825).update({1: "N", 2: (55.0, 45.0, 0.0), 3: "E", 4: (37.0, 37.0, 0.0)})
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90, exif=exif.tobytes())
    return buf.getvalue()


def test_upload_keeps_original_file_as_is_outside_media(admin, content_dir):
    data = gps_photo_bytes((3000, 2000))

    upload(admin, data, "IMG_1234.JPG")

    # Исходный файл — байт в байт, с EXIF и геометкой, но в отдельной папке, не в media/
    assert sorted(p.name for p in originals(content_dir).iterdir()) == ["01.jpg"]
    assert (originals(content_dir) / "01.jpg").read_bytes() == data
    assert sorted(p.name for p in folder(content_dir).iterdir()) == ["01-1280.webp", "01-1920.webp", "01-640.webp", "01.webp"]
    # В портфолио — только опубликованная версия; на сайт идёт WebP без метаданных
    assert project(content_dir)["photos"][-1] == f"portfolio/{PROJECT}/01.webp"
    with Image.open(folder(content_dir) / "01.webp") as img:
        assert not img.getexif()


@pytest.mark.parametrize("path", [
    f"/media/../originals/portfolio/{PROJECT}/01.jpg",
    f"/media/%2e%2e/originals/portfolio/{PROJECT}/01.jpg",
    f"/media/portfolio/{PROJECT}/01.jpg",
    f"/media/originals/portfolio/{PROJECT}/01.jpg",
    f"/static/../../content/originals/portfolio/{PROJECT}/01.jpg",
    f"/originals/portfolio/{PROJECT}/01.jpg",
])
def test_original_is_not_served_by_public_routes(admin, content_dir, path):
    upload(admin, gps_photo_bytes((900, 600)))
    assert (originals(content_dir) / "01.jpg").is_file()

    # Ни посетителю, ни администратору (проект опубликован в копии контента)
    assert make_app(content_dir).test_client().get(path).status_code == 404
    assert admin.get(path).status_code == 404


def test_admin_thumbnails_do_not_read_originals(admin, content_dir):
    upload(admin, gps_photo_bytes((900, 600)))

    for src in (f"../originals/portfolio/{PROJECT}/01.jpg", f"originals/portfolio/{PROJECT}/01.jpg"):
        assert admin.get("/admin/thumb", query_string={"src": src}).status_code == 404


def test_original_is_kept_in_history_when_conversion_fails(admin, content_dir):
    data = photo_bytes((2000, 1500))
    broken = data[: len(data) // 2]             # заголовок JPEG цел, данные оборваны — декодирование падает

    upload(admin, broken)

    assert not folder(content_dir).exists() or not any(folder(content_dir).iterdir())
    assert f"portfolio/{PROJECT}/01.webp" not in project(content_dir)["photos"]
    kept = list((content_dir / ".history" / "originals").rglob("01.jpg"))
    assert len(kept) == 1 and kept[0].read_bytes() == broken
    page = messages(admin)
    assert "повреждён" in page and "Исходный файл сохранён" in page


def test_original_is_kept_in_history_when_writing_webp_fails(admin, content_dir, monkeypatch):
    def fail(img, path, quality):
        raise OSError("диск заполнен")
    monkeypatch.setattr(uploads, "_write_atomic", fail)
    data = photo_bytes((2000, 1500))

    upload(admin, data)

    assert not any(folder(content_dir).iterdir())
    assert not originals(content_dir).exists() or not any(originals(content_dir).iterdir())
    assert [p.read_bytes() for p in (content_dir / ".history" / "originals").rglob("01.jpg")] == [data]
    assert "Исходный файл сохранён" in messages(admin)


def test_existing_original_is_never_overwritten(admin, content_dir):
    # Осиротевший оригинал (например, после сбоя сервера) занимает номер 01
    originals(content_dir).mkdir(parents=True)
    (originals(content_dir) / "01.jpg").write_bytes(b"old original")
    data = photo_bytes((900, 600))

    upload(admin, data)

    assert (originals(content_dir) / "01.jpg").read_bytes() == b"old original"
    assert (originals(content_dir) / "02.jpg").read_bytes() == data
    assert project(content_dir)["photos"][-1] == f"portfolio/{PROJECT}/02.webp"


def test_published_sizes_are_readable_and_original_stays_private(admin, content_dir):
    # Версии для сайта читаемы всеми (их может отдавать веб-сервер), оригинал — только владельцу
    upload(admin, photo_bytes((2000, 1500)))

    for file in folder(content_dir).iterdir():
        assert file.stat().st_mode & 0o777 == 0o644, file.name
    assert (originals(content_dir) / "01.jpg").stat().st_mode & 0o777 == 0o600


def test_save_original_refuses_to_replace_a_file(tmp_path):
    target = tmp_path / "01.jpg"
    target.write_bytes(b"old")

    with pytest.raises(FileExistsError):
        uploads.save_original(b"new", target)

    assert target.read_bytes() == b"old"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["01.jpg"]     # временный файл не остался


def test_rejected_file_is_not_stored(admin, content_dir):
    upload(admin, b"GIF89a not a photo", "photo.jpg")

    assert not originals(content_dir).exists()
    assert not (content_dir / ".history" / "originals").exists()


# --- JPEG с MPF (Pillow: формат MPO) ---

def mpo_bytes(size, orientation=None):
    img = Image.open(io.BytesIO(photo_bytes(size)))
    kwargs = {}
    if orientation:
        exif = Image.Exif()
        exif[0x0112] = orientation
        kwargs["exif"] = exif.tobytes()
    buf = io.BytesIO()
    img.save(buf, "MPO", save_all=True, append_images=[img.resize((320, 240))], quality=90, **kwargs)
    return buf.getvalue()


def test_jpeg_with_mpf_block_is_accepted(admin, content_dir):
    data = mpo_bytes((3000, 2000), orientation=6)
    assert Image.open(io.BytesIO(data)).format == "MPO"           # так Pillow видит снимки многих камер и iPhone

    assert upload(admin, data, "DSC_0001.JPG").status_code == 302

    files = sorted(p.name for p in folder(content_dir).iterdir())
    assert files == ["01-1280.webp", "01-1920.webp", "01-640.webp", "01.webp"]
    with Image.open(folder(content_dir) / "01.webp") as img:
        assert img.format == "WEBP" and not img.getexif()
        assert img.size == (1707, 2560)                           # поворот по EXIF применён
    assert (originals(content_dir) / "01.jpg").read_bytes() == data


def test_other_formats_are_still_rejected_whatever_the_name(admin, content_dir):
    buf = io.BytesIO()
    Image.new("RGB", (800, 600), "red").save(buf, "GIF")
    upload(admin, buf.getvalue(), "photo.jpg")

    assert "поддерживаются только JPG, PNG и WebP" in messages(admin)
    assert not folder(content_dir).exists()


# --- Предел разрешения для PNG и WebP ---

def png_bytes(size, fmt="PNG"):
    buf = io.BytesIO()
    Image.linear_gradient("L").resize(size).convert("RGB").save(buf, fmt)
    return buf.getvalue()


@pytest.mark.parametrize("fmt, name", [("PNG", "big.png"), ("WEBP", "big.webp")])
def test_huge_png_and_webp_are_rejected_before_decoding(content_dir, monkeypatch, fmt, name):
    client = make_app(content_dir, ADMIN_MAX_PIXELS_FULL_DECODE=1_000_000).test_client()
    token = TOKEN_RE.search(client.get("/admin/login").get_data(as_text=True)).group(1)
    client.post("/admin/login", data={"csrf_token": token, "username": "admin", "password": PASSWORD})
    data = png_bytes((2000, 1000), fmt)

    # Отказ — по размеру из заголовка: декодировать картинку для этого не нужно
    from PIL import ImageFile
    def no_decoding(self):
        raise AssertionError("картинка не должна декодироваться")
    with monkeypatch.context() as patch:
        patch.setattr(ImageFile.ImageFile, "load", no_decoding)
        upload(client, data, name)

    page = messages(client)
    assert "слишком большое разрешение для PNG и WebP (2000×1000, больше 1 Мп)" in page
    assert not folder(content_dir).exists() and not originals(content_dir).exists()


def test_jpeg_of_same_resolution_passes_png_limit(content_dir):
    # JPEG декодируется сразу уменьшенным, поэтому для него действует общий предел ADMIN_MAX_PIXELS
    client = make_app(content_dir, ADMIN_MAX_PIXELS_FULL_DECODE=1_000_000).test_client()
    token = TOKEN_RE.search(client.get("/admin/login").get_data(as_text=True)).group(1)
    client.post("/admin/login", data={"csrf_token": token, "username": "admin", "password": PASSWORD})

    upload(client, photo_bytes((2000, 1000)))

    assert (folder(content_dir) / "01.webp").is_file()


def test_default_png_limit_is_lower_than_general_limit():
    app = create_app({"TESTING": True})
    assert app.config["ADMIN_MAX_PIXELS_FULL_DECODE"] < app.config["ADMIN_MAX_PIXELS"]
    assert app.config["ADMIN_MAX_IMAGE_BYTES"] == 40 * 1024 * 1024


# --- Галерея: srcset для большого фото ---

def test_gallery_gets_srcset_on_portfolio_and_home(admin, content_dir):
    featured = "example-2026-1"                                   # карточка блока «Реализованные проекты»
    upload(admin, photo_bytes((3000, 2000)), project_id=featured)
    data = json.loads((content_dir / "portfolio.json").read_text(encoding="utf-8"))
    for p in data["projects"]:
        if p["id"] == featured:
            p["photos"] = [f"portfolio/{featured}/02.webp"]
            p["cover"] = p["photos"][0]
    (content_dir / "portfolio.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    client = make_app(content_dir).test_client()
    base = f"/media/portfolio/{featured}/02"
    # Основной файл 2560 px шире версии 1920 — он в srcset для крупных экранов с высокой плотностью
    srcset = f"{base}-640.webp 640w, {base}-1280.webp 1280w, {base}-1920.webp 1920w, {base}.webp 2560w"

    for url in ("/", "/portfolio"):
        html = client.get(url).get_data(as_text=True)
        galleries = [json.loads(m.replace("&#39;", "'")) for m in re.findall(r"data-gallery='([^']+)'", html)]
        gallery = next(g for g in galleries if g["images"] == [f"{base}-1920.webp"])
        # src остаётся 1920 (для браузеров без srcset), миниатюры — 640; 2560 — только вариант в srcset
        assert gallery["srcsets"] == [srcset]
        assert gallery["thumbs"] == [f"{base}-640.webp"]
        assert f'"{base}.webp"' not in html


def test_gallery_srcset_is_empty_for_photos_without_sizes(make_content):
    # Старое фото без версий и кадры из static/: srcset пустой — браузер берёт src как раньше
    content_dir = make_content()
    html = make_app(content_dir).test_client().get("/portfolio").get_data(as_text=True)
    galleries = [json.loads(m) for m in re.findall(r"data-gallery='([^']+)'", html)]

    assert galleries and all(len(g["srcsets"]) == len(g["images"]) for g in galleries)
    assert any(g["images"][0].startswith("/static/") and g["srcsets"][0] == "" for g in galleries)


def test_site_uses_light_versions_and_srcset(admin, content_dir):
    upload(admin, photo_bytes((3000, 2000)))
    data = json.loads((content_dir / "portfolio.json").read_text(encoding="utf-8"))
    for p in data["projects"]:
        if p["id"] == PROJECT:
            p["photos"] = [f"portfolio/{PROJECT}/01.webp"]
            p["cover"] = p["photos"][0]
    (content_dir / "portfolio.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    html = make_app(content_dir).test_client().get("/portfolio").get_data(as_text=True)
    base = f"/media/portfolio/{PROJECT}/01"
    card = re.search(rf'<img src="{base}-640\.webp" srcset="([^"]+)" sizes="[^"]+"[^>]*loading="lazy"', html)
    assert card and card.group(1) == (f"{base}-640.webp 640w, {base}-1280.webp 1280w, {base}-1920.webp 1920w, "
                                      f"{base}.webp 2560w")
    # Галерея: src — 1920, лента миниатюр — 640; основной файл 2560 — только вариант в srcset
    assert f'"images": ["{base}-1920.webp"]' in html and f'"thumbs": ["{base}-640.webp"]' in html
    assert f'"{base}.webp"' not in html


def test_old_photos_get_sizes_by_command_once(content_dir):
    # Только своё фото: команда обходит всю папку media, а в копии контента могут быть и другие
    shutil.rmtree(content_dir / "media" / "portfolio")
    target = folder(content_dir)
    target.mkdir(parents=True)
    Image.open(io.BytesIO(photo_bytes((1600, 1200)))).save(target / "01.webp", "WEBP")
    runner = make_app(content_dir).test_cli_runner()

    first = runner.invoke(args=["make-image-sizes"])
    second = runner.invoke(args=["make-image-sizes"])

    assert "Создано версий: 2" in first.output and "Создано версий: 0" in second.output
    assert sorted(p.name for p in target.iterdir()) == ["01-1280.webp", "01-640.webp", "01.webp"]


# --- Оригиналы до 40 МБ ---

MB = 1024 * 1024


def padded_jpeg(total, size=(1600, 1200), orientation=6):
    """Настоящий JPEG ровно total байт: небольшое фото с EXIF (поворот, геометка) плюс блоки
    комментария COM по 64 КБ сразу после SOI — как «тяжёлый» файл камеры без огромной картинки.

    Размер файла и число пикселей — разные пределы: здесь файл большой, а пикселей немного.
    """
    img = Image.open(io.BytesIO(photo_bytes(size)))
    exif = Image.Exif()
    exif[0x0112] = orientation
    exif[0x010F] = "TestCam"
    exif.get_ifd(0x8825).update({1: "N", 2: (55.0, 45.0, 0.0), 3: "E", 4: (37.0, 37.0, 0.0)})
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90, exif=exif.tobytes())
    base = buf.getvalue()
    need = total - len(base)
    assert need >= 0

    def segments(need):
        while need:
            chunk = min(65535 + 2, need)          # маркер (2 байта) + длина (2) + данные; длина ≤ 65535
            if 0 < need - chunk < 4:              # остаток меньше пустого сегмента — делим иначе
                chunk -= 4
            yield b"\xff\xfe" + (chunk - 2).to_bytes(2, "big") + b"\x00" * (chunk - 4)
            need -= chunk
    return base[:2] + b"".join(segments(need)) + base[2:]


@pytest.mark.parametrize("total", [35 * MB, 39 * MB, 40 * MB])
def test_original_up_to_40_mb_is_accepted_kept_and_processed(admin, content_dir, total):
    data = padded_jpeg(total)
    assert len(data) == total and Image.open(io.BytesIO(data)).format == "JPEG"

    assert upload(admin, data, "DSC_0001.JPG").status_code == 302

    assert "Загружено фото: 1" in messages(admin)
    # Оригинал — байт в байт, вне media/
    assert (originals(content_dir) / "01.jpg").read_bytes() == data
    # Версии для сайта: WebP без EXIF и геометки, поворот по EXIF применён (1600×1200 → портрет)
    files = sorted(p.name for p in folder(content_dir).iterdir())
    assert files == ["01-1280.webp", "01-640.webp", "01.webp"]   # 1920 больше длинной стороны (1600) — не нужна
    with Image.open(folder(content_dir) / "01.webp") as img:
        assert img.format == "WEBP" and not img.getexif() and img.size == (1200, 1600)
    assert (folder(content_dir) / "01.webp").stat().st_size < MB
    # Публично оригинал не отдаётся ни по одному адресу
    public = make_app(content_dir).test_client()
    for path in (f"/media/portfolio/{PROJECT}/01.jpg", f"/media/../originals/portfolio/{PROJECT}/01.jpg",
                 f"/media/originals/portfolio/{PROJECT}/01.jpg"):
        assert public.get(path).status_code == 404


def test_file_over_40_mb_is_rejected_with_clear_message(admin, content_dir):
    upload(admin, padded_jpeg(40 * MB + 1), "DSC_0002.JPG")

    assert "«DSC_0002.JPG»: файл больше 40 МБ." in messages(admin)
    assert not folder(content_dir).exists() and not originals(content_dir).exists()
    assert project(content_dir)["photos"] == project_photos_before(content_dir)


def project_photos_before(content_dir):
    # Фото проекта в тестовых данных (tests/fixtures/content): отклонённая загрузка их не меняет
    from tests.conftest import CONTENT
    data = json.loads((CONTENT / "portfolio.json").read_text(encoding="utf-8"))
    return next(p for p in data["projects"] if p["id"] == PROJECT)["photos"]


def test_big_original_is_kept_when_processing_fails(admin, content_dir, monkeypatch):
    data = padded_jpeg(39 * MB)

    def broken(*args, **kwargs):
        raise uploads.UploadError("файл изображения повреждён")
    monkeypatch.setattr("app.admin.process_image", broken)
    upload(admin, data, "DSC_0003.JPG")

    page = messages(admin)
    assert "«DSC_0003.JPG»: файл изображения повреждён. Исходный файл сохранён в content/.history/originals/" in page
    kept = list((content_dir / ".history" / "originals").rglob("01.jpg"))
    assert [p.read_bytes() == data for p in kept] == [True]
    assert not folder(content_dir).exists() or not any(folder(content_dir).iterdir())


@pytest.mark.parametrize("config, data, message", [
    # Большой файл с малым числом пикселей: отказ только по размеру файла
    ({"ADMIN_MAX_IMAGE_BYTES": 2 * MB}, lambda: padded_jpeg(3 * MB), "файл больше 2 МБ"),
    # Маленький файл с большим числом пикселей: отказ только по разрешению
    ({"ADMIN_MAX_PIXELS": 1_000_000}, lambda: photo_bytes((2000, 1000)), "слишком большое разрешение (2000×1000)"),
])
def test_file_size_and_pixel_limits_are_independent(content_dir, config, data, message):
    client = make_app(content_dir, **config).test_client()
    token = TOKEN_RE.search(client.get("/admin/login").get_data(as_text=True)).group(1)
    client.post("/admin/login", data={"csrf_token": token, "username": "admin", "password": PASSWORD})

    upload(client, data())

    page = messages(client)
    assert message in page
    other = "слишком большое разрешение" if "файл больше" in message else "файл больше"
    assert other not in page
    assert not folder(content_dir).exists()


def test_big_file_is_still_checked_by_its_pixels(content_dir):
    # Тот же 39-мегабайтный файл, но разрешение выше общего предела: пиксели проверяются сами по себе
    client = make_app(content_dir, ADMIN_MAX_PIXELS=1_000_000).test_client()
    token = TOKEN_RE.search(client.get("/admin/login").get_data(as_text=True)).group(1)
    client.post("/admin/login", data={"csrf_token": token, "username": "admin", "password": PASSWORD})

    upload(client, padded_jpeg(39 * MB, size=(1600, 1200)))

    page = messages(client)
    # Размер — из заголовка файла, до поворота по EXIF
    assert "слишком большое разрешение (1600×1200)" in page and "файл больше" not in page
    assert not originals(content_dir).exists()


def multipart_size(n, file_bytes, token):
    """Размер тела multipart-запроса формы загрузки с n файлами по file_bytes байт.

    Накладные расходы (границы, заголовки частей, поле csrf_token) не зависят от размера
    файлов, поэтому считаются на пустых файлах.
    """
    from werkzeug.test import EnvironBuilder
    files = [(io.BytesIO(b""), f"DSC_{i:04d}.JPG", "image/jpeg") for i in range(n)]
    builder = EnvironBuilder(method="POST", data={"csrf_token": token, "photos": files})
    try:
        overhead = int(builder.get_environ()["CONTENT_LENGTH"])
    finally:
        builder.close()
    return overhead + n * file_bytes


def test_upload_request_fits_four_photos_of_40_mb(admin):
    token = TOKEN_RE.search(admin.get(f"/admin/projects/{PROJECT}").get_data(as_text=True)).group(1)
    app = create_app({"TESTING": True})
    limit, image = app.config["ADMIN_MAX_UPLOAD_BYTES"], app.config["ADMIN_MAX_IMAGE_BYTES"]

    # 200 МБ = 5 × 40 МБ ровно, но multipart добавляет заголовки: пять файлов по 40 МБ не влезают
    assert multipart_size(4, image, token) <= limit < multipart_size(5, image, token)
    # Пять файлов немного меньше 40 МБ (как обычно у камеры) — влезают
    assert multipart_size(5, 39 * MB, token) <= limit


def test_request_over_total_limit_shows_message_and_saves_nothing(content_dir):
    # Тот же сценарий в масштабе 1:40 (предел запроса = 5 × предел файла), чтобы не гонять 200 МБ в тесте
    client = make_app(content_dir, ADMIN_MAX_IMAGE_BYTES=MB, ADMIN_MAX_UPLOAD_BYTES=5 * MB).test_client()
    token = TOKEN_RE.search(client.get("/admin/login").get_data(as_text=True)).group(1)
    client.post("/admin/login", data={"csrf_token": token, "username": "admin", "password": PASSWORD})
    files = [padded_jpeg(MB, size=(900, 600)) for _ in range(5)]

    def send(batch):
        token = TOKEN_RE.search(client.get(f"/admin/projects/{PROJECT}").get_data(as_text=True)).group(1)
        return client.post(f"/admin/projects/{PROJECT}/photos", content_type="multipart/form-data",
                           data={"csrf_token": token,
                                 "photos": [(io.BytesIO(b), f"DSC_{i:04d}.JPG") for i, b in enumerate(batch)]})

    response = send(files)

    # Не ошибка сервера, а возврат в админку с понятным сообщением
    assert response.status_code == 302
    assert "Слишком большой объём за один раз (больше 5 МБ). Загрузите фото частями." in messages(client)
    assert not folder(content_dir).exists() and not originals(content_dir).exists()

    # Четыре файла того же размера проходят
    assert send(files[:4]).status_code == 302
    assert "Загружено фото: 4" in messages(client)
    assert sorted(p.name for p in originals(content_dir).iterdir()) == ["01.jpg", "02.jpg", "03.jpg", "04.jpg"]


# --- Уменьшенное декодирование JPEG: память без потери качества ---

@pytest.mark.parametrize("size, factor", [
    # Обычные кадры — как прежде: короткая сторона после уменьшения не меньше 2560
    ((6000, 4000), 1), ((7560, 5040), 1), ((9504, 6336), 2), ((12240, 8160), 2), ((4000, 3000), 1),
    ((8064, 6048), 2), ((2000, 1500), 1),
    # Панорамы: раньше декодировались целиком; теперь — с запасом 2 × 2560 по длинной стороне
    ((16000, 4000), 2), ((19600, 4900), 2), ((4900, 19600), 2), ((40000, 2500), 4), ((6000, 2000), 1),
])
def test_jpeg_reduction_keeps_margin_for_main_photo(size, factor):
    assert uploads.jpeg_reduction(*size, 2560) == factor


def test_jpeg_reduction_never_decodes_more_than_before():
    # Прежнее правило — draft до (2560, 2560): уменьшение, пока короткая сторона не меньше 2560
    for w in range(1000, 41000, 700):
        for h in range(1000, 12000, 650):
            before = next((f for f in (8, 4, 2) if min(w, h) // f >= 2560), 1)
            assert uploads.jpeg_reduction(w, h, 2560) >= before, (w, h)


def detail_jpeg(size, orientation=None):
    """JPEG с мелкими деталями по всему кадру (плитка шума), без огромной картинки в памяти теста."""
    tile = Image.merge("RGB", [Image.effect_noise((256, 256), 50 + 10 * i) for i in range(3)])
    img = Image.new("RGB", size)
    for x in range(0, size[0], 256):
        for y in range(0, size[1], 256):
            img.paste(tile, (x, y))
    kwargs = {}
    if orientation:
        exif = Image.Exif()
        exif[0x0112] = orientation
        kwargs["exif"] = exif.tobytes()
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92, **kwargs)
    return buf.getvalue()


def psnr(a, b):
    from PIL import ImageChops, ImageStat
    rms = (sum(v ** 2 for v in ImageStat.Stat(ImageChops.difference(a, b)).rms) / 3) ** 0.5
    return 99.0 if rms == 0 else 20 * __import__("math").log10(255 / rms)


def test_panorama_is_decoded_reduced_with_same_quality_and_orientation(monkeypatch):
    from PIL import ImageOps, JpegImagePlugin
    data = detail_jpeg((12000, 3000), orientation=6)          # 36 Мп, 4:1, снято «лёжа»
    scales = []
    real_draft = JpegImagePlugin.JpegImageFile.draft

    def spy(self, mode, size):
        result = real_draft(self, mode, size)
        scales.append(self.size)                                   # размер, до которого декодируется кадр
        return result
    monkeypatch.setattr(JpegImagePlugin.JpegImageFile, "draft", spy)

    img = uploads.process_image(data, 2560, 100_000_000, 50_000_000)

    # Декодирование вдвое меньше по каждой стороне (в 4 раза меньше памяти), а не целиком, как раньше
    assert scales == [(6000, 1500)]
    assert img.size == (640, 2560)                                # поворот по EXIF применён
    with Image.open(io.BytesIO(data)) as full:
        full.load()
        ref = ImageOps.exif_transpose(full).convert("RGB").resize(img.size, Image.Resampling.LANCZOS)
    assert psnr(ref, img) > 40


def test_ordinary_photo_result_is_unchanged(monkeypatch):
    # Кадр 3:2 на 24 Мп: уменьшения нет, как и раньше (короткая сторона 4000 < 2 × 2560)
    from PIL import JpegImagePlugin
    data = detail_jpeg((6000, 4000))
    calls = []
    real_draft = JpegImagePlugin.JpegImageFile.draft
    monkeypatch.setattr(JpegImagePlugin.JpegImageFile, "draft",
                        lambda self, mode, size: calls.append(size) or real_draft(self, mode, size))

    img = uploads.process_image(data, 2560, 100_000_000, 50_000_000)

    assert img.size == (2560, 1707)
    with Image.open(io.BytesIO(data)) as full:
        full.load()
        full = full.convert("RGB")
        full.thumbnail((2560, 2560), Image.Resampling.LANCZOS)
    assert psnr(full, img) == 99.0                                  # тот же результат, что без draft


# --- Непредвиденная ошибка и прерванная обработка пачки ---

def upload_many(client, items, project_id=PROJECT):
    token = TOKEN_RE.search(client.get(f"/admin/projects/{project_id}").get_data(as_text=True)).group(1)
    return client.post(f"/admin/projects/{project_id}/photos", content_type="multipart/form-data",
                       data={"csrf_token": token, "photos": [(io.BytesIO(d), n) for n, d in items]})


def failing_second_save(monkeypatch, error):
    from app import admin as admin_module
    real, calls = admin_module.save_image, []

    def save(img, path):
        calls.append(path.name)
        if len(calls) == 2:
            raise error
        return real(img, path)
    monkeypatch.setattr(admin_module, "save_image", save)


def test_unexpected_error_on_one_photo_does_not_break_the_others(admin, content_dir, monkeypatch):
    failing_second_save(monkeypatch, RuntimeError("непредвиденная ошибка Pillow"))
    first, second, third = photo_bytes((900, 600)), photo_bytes((900, 601)), photo_bytes((900, 602))

    response = upload_many(admin, [("a.jpg", first), ("b.jpg", second), ("c.jpg", third)])

    # Не ошибка 500: остальные фото загружены, о сбойном — понятное сообщение
    assert response.status_code == 302
    page = messages(admin)
    assert "«b.jpg»: не удалось обработать фото. Исходный файл сохранён в content/.history/originals/" in page
    assert "Загружено фото: 2." in page
    photos = [p for p in project(content_dir)["photos"] if p.startswith("portfolio/")]
    assert photos == [f"portfolio/{PROJECT}/01.webp", f"portfolio/{PROJECT}/02.webp"]
    # Только фото из portfolio.json: версий сбойного фото нет, его оригинал — в истории, байт в байт
    assert sorted(p.name for p in folder(content_dir).iterdir() if not uploads.is_variant(p.name)) == ["01.webp", "02.webp"]
    assert (originals(content_dir) / "01.jpg").read_bytes() == first
    assert (originals(content_dir) / "02.jpg").read_bytes() == third
    assert [p.read_bytes() for p in (content_dir / ".history" / "originals").rglob("*.jpg")] == [second]


def test_interrupted_batch_leaves_no_photos_outside_portfolio(admin, content_dir, monkeypatch):
    # Таймаут воркера gunicorn: SIGABRT → SystemExit посреди обработки пачки
    failing_second_save(monkeypatch, SystemExit(1))
    first, second = photo_bytes((900, 600)), photo_bytes((900, 601))
    before = project(content_dir)["photos"]

    with pytest.raises(SystemExit):
        upload_many(admin, [("a.jpg", first), ("b.jpg", second), ("c.jpg", photo_bytes((900, 602)))])

    # portfolio.json не тронут, в media/ и originals/ ничего от этой пачки; оригиналы не потеряны — в истории
    assert project(content_dir)["photos"] == before
    assert not folder(content_dir).exists() or not any(folder(content_dir).iterdir())
    assert not originals(content_dir).exists() or not any(originals(content_dir).iterdir())
    kept = sorted(p.read_bytes() for p in (content_dir / ".history" / "originals").rglob("*.jpg"))
    assert kept == sorted([first, second])
    # Блокировка освобождена: следующая загрузка проходит
    monkeypatch.undo()
    assert upload(admin, photo_bytes((900, 600))).status_code == 302
