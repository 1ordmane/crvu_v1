"""srcset фото: подготовленные версии с их настоящей шириной и основной файл, если он шире них.

Основной файл — публичное фото из content/media (NN.webp до 2560 px или старый файл). Исходники
из content/originals/ (с EXIF и геометкой) в srcset не попадают никогда. Ширина читается из
заголовка файла с учётом поворота по EXIF; не удалось прочитать — файл в srcset не добавляется.
"""

import io
import json
import re

import pytest
from PIL import Image

from app import create_app

PID = "stand"
BASE = f"/media/portfolio/{PID}/01"


@pytest.fixture
def media(tmp_path):
    folder = tmp_path / "media" / "portfolio" / PID
    folder.mkdir(parents=True)
    return folder


def picture(path, size, fmt=None, exif=None):
    img = Image.new("RGB", size, (120, 130, 140))
    kwargs = {"exif": exif.tobytes()} if exif is not None else {}
    img.save(path, fmt or {".jpg": "JPEG", ".png": "PNG"}.get(path.suffix, "WEBP"), **kwargs)


def srcset(tmp_path, photo=f"portfolio/{PID}/01.webp"):
    app = create_app({"TESTING": True, "CONTENT_DIR": str(tmp_path)})
    with app.test_request_context():
        return app.jinja_env.filters["media_srcset"](photo)


def test_source_1000_with_640_version(tmp_path, media):
    picture(media / "01.jpg", (1000, 562))
    picture(media / "01-640.webp", (640, 360))

    assert srcset(tmp_path, f"portfolio/{PID}/01.jpg") == f"{BASE}-640.webp 640w, {BASE}.jpg 1000w"


def test_source_1600_with_640_and_1280(tmp_path, media):
    picture(media / "01.webp", (1600, 900))
    picture(media / "01-640.webp", (640, 360))
    picture(media / "01-1280.webp", (1280, 720))

    assert srcset(tmp_path) == f"{BASE}-640.webp 640w, {BASE}-1280.webp 1280w, {BASE}.webp 1600w"


def test_source_2560_with_all_versions(tmp_path, media):
    picture(media / "01.webp", (2560, 1707))
    for side in (640, 1280, 1920):
        picture(media / f"01-{side}.webp", (side, round(side * 1707 / 2560)))

    assert srcset(tmp_path) == (f"{BASE}-640.webp 640w, {BASE}-1280.webp 1280w, {BASE}-1920.webp 1920w, "
                                f"{BASE}.webp 2560w")


def test_source_smaller_than_largest_version_is_not_added(tmp_path, media):
    # Нестандартный набор (версия больше файла — например, файл заменён вручную): только версии
    picture(media / "01.webp", (1000, 562))
    picture(media / "01-640.webp", (640, 360))
    picture(media / "01-1280.webp", (1280, 720))

    assert srcset(tmp_path) == f"{BASE}-640.webp 640w, {BASE}-1280.webp 1280w"


def test_source_with_same_width_as_a_version_is_not_duplicated(tmp_path, media):
    picture(media / "01.webp", (1280, 720))
    picture(media / "01-640.webp", (640, 360))
    picture(media / "01-1280.webp", (1280, 720))

    assert srcset(tmp_path) == f"{BASE}-640.webp 640w, {BASE}-1280.webp 1280w"


def test_missing_source_leaves_only_versions(tmp_path, media):
    picture(media / "01-640.webp", (640, 360))
    picture(media / "01-1280.webp", (1280, 720))

    assert srcset(tmp_path) == f"{BASE}-640.webp 640w, {BASE}-1280.webp 1280w"


def test_unreadable_source_is_not_added(tmp_path, media):
    (media / "01.webp").write_bytes(b"RIFF\x00\x00\x00\x00WEBPVP8 broken")
    picture(media / "01-640.webp", (640, 360))

    assert srcset(tmp_path) == f"{BASE}-640.webp 640w"


def test_unreadable_version_is_skipped_not_guessed(tmp_path, media):
    picture(media / "01.webp", (1600, 900))
    (media / "01-640.webp").write_bytes(b"not an image")
    picture(media / "01-1280.webp", (1280, 720))

    assert srcset(tmp_path) == f"{BASE}-1280.webp 1280w, {BASE}.webp 1600w"


def test_no_versions_means_no_srcset(tmp_path, media):
    # Маленькое фото без версий: как раньше — только src
    picture(media / "01.webp", (600, 400))

    assert srcset(tmp_path) == ""


def test_portrait_photo_gets_real_widths_not_long_side(tmp_path, media):
    # Версия «640» вертикального снимка — 427 px в ширину; раньше в srcset стояло фиктивное 640w
    picture(media / "01.webp", (1707, 2560))
    for side in (640, 1280, 1920):
        picture(media / f"01-{side}.webp", (round(side * 1707 / 2560), side))

    assert srcset(tmp_path) == (f"{BASE}-640.webp 427w, {BASE}-1280.webp 854w, {BASE}-1920.webp 1280w, "
                                f"{BASE}.webp 1707w")


def test_exif_rotated_legacy_jpeg_uses_displayed_width(tmp_path, media):
    # Старый JPEG, снятый «лёжа»: в файле 1000×562, на экране 562×1000; версии — уже повёрнутые
    exif = Image.Exif()
    exif[0x0112] = 6
    picture(media / "01.jpg", (1000, 562), exif=exif)
    picture(media / "01-640.webp", (360, 640))

    assert srcset(tmp_path, f"portfolio/{PID}/01.jpg") == f"{BASE}-640.webp 360w, {BASE}.jpg 562w"


def test_file_with_gps_is_not_added(tmp_path, media):
    # Старый файл с геометкой в media/ уже публичен как src, но шире его не продвигаем
    exif = Image.Exif()
    exif.get_ifd(0x8825).update({1: "N", 2: (55.0, 45.0, 0.0), 3: "E", 4: (37.0, 37.0, 0.0)})
    picture(media / "01.jpg", (1000, 562), exif=exif)
    picture(media / "01-640.webp", (640, 360))

    assert srcset(tmp_path, f"portfolio/{PID}/01.jpg") == f"{BASE}-640.webp 640w"


def test_width_follows_file_changes(tmp_path, media):
    picture(media / "01.webp", (1600, 900))
    picture(media / "01-640.webp", (640, 360))
    assert srcset(tmp_path).endswith("01.webp 1600w")

    picture(media / "01.webp", (1800, 1012))                     # файл заменён — ширина перечитывается
    assert srcset(tmp_path).endswith("01.webp 1800w")


def test_static_frames_keep_no_srcset(tmp_path):
    assert srcset(tmp_path, "static/img/stand/frame-159.webp") == ""


def test_originals_never_appear(tmp_path, media):
    # Исходник с камеры лежит в originals/ и больше всех — в srcset его нет, адресов на originals/ тоже
    originals = tmp_path / "originals" / "portfolio" / PID
    originals.mkdir(parents=True)
    picture(originals / "01.jpg", (6000, 4000))
    picture(media / "01.webp", (2560, 1707))
    picture(media / "01-1920.webp", (1920, 1280))

    result = srcset(tmp_path)
    assert "originals" not in result and "6000w" not in result
    assert result == f"{BASE}-1920.webp 1920w, {BASE}.webp 2560w"


# --- На страницах ---

def test_gallery_and_cards_get_source_in_srcset(tmp_path, media):
    import shutil
    from pathlib import Path
    from tests.conftest import CONTENT as src                    # тексты сайта из тестовых данных
    for name in ("site.json", "home.json", "pages.json", "contacts.json"):
        shutil.copy(src / name, tmp_path / name)
    picture(media / "01.webp", (1600, 900))
    picture(media / "01-640.webp", (640, 360))
    picture(media / "01-1280.webp", (1280, 720))
    (tmp_path / "portfolio.json").write_text(json.dumps({"projects": [{
        "id": PID, "status": "published", "title": "Стенд", "year": 2026, "order": 0, "cover": "",
        "photos": [f"portfolio/{PID}/01.webp"], "specs": [], "description": "", "video": "https://vimeo.com/76979871",
    }]}, ensure_ascii=False), encoding="utf-8")
    client = create_app({"TESTING": True, "CONTENT_DIR": str(tmp_path)}).test_client()
    expected = f"{BASE}-640.webp 640w, {BASE}-1280.webp 1280w, {BASE}.webp 1600w"

    html = client.get("/portfolio").get_data(as_text=True)
    galleries = [json.loads(m) for m in re.findall(r"data-gallery='([^']+)'", html)]
    gallery = next(g for g in galleries if g["title"] == "Стенд")
    assert gallery["srcsets"] == [expected]
    # src галереи (для браузеров без srcset) и лента миниатюр — как раньше
    assert gallery["images"] == [f"{BASE}.webp"] and gallery["thumbs"] == [f"{BASE}-640.webp"]
    assert f'srcset="{expected}"' in html
    # Vimeo по-прежнему только в данных галереи, без iframe
    assert gallery["vimeo"] == "https://player.vimeo.com/video/76979871?dnt=1" and "<iframe" not in html
    # Основной файл отдаётся сайтом, оригиналы — нет
    assert client.get(f"{BASE}.webp").status_code == 200
    assert client.get(f"/media/../originals/portfolio/{PID}/01.jpg").status_code == 404
