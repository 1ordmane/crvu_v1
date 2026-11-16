"""Загруженные видеоотзывы (MP4/WebM) и свои обложки — в Редакторе, группа «Видеоотзывы».

Файлы — content/media/videos/files/<32 hex>.mp4|webm и videos/covers/<32 hex>.webp (app/videos.py):
тип по содержимому, предел размера, без перезаписи; пока ролик не опубликован, /media отдаёт их только
администратору. Всё — через черновик → предпросмотр → «Опубликовать»; ненужные файлы не удаляются,
а переносятся в .history/media (sweep).
"""

import json
import re
import stat
from io import BytesIO

import pytest
from PIL import Image

from app.content import validate_content
from app.videos import VideoError, receive_video, sniff_video, validate
from tests.test_admin import PASSWORD_HASH, editor_versions
from tests.test_videos import (CODES, IDS, LIBRARY, STATIC, admin, content_dir, draft, drafted, make_thumb, player_codes,  # noqa: F401
                               positions, post, publish, read, reviews, reviews_index, row_fields, sheet, site, video,
                               write_videos)

# Начало настоящих файлов: MP4 (ftyp, бренд isom) и WebM (EBML, DocType webm)
MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + b"\x00\x00\x00\x08free" + b"\x00" * 4000
WEBM = b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81\x01\x42\xf7\x81\x01\x42\x82\x84webm" + b"\x00" * 4000
MOV = b"\x00\x00\x00\x14ftypqt  \x00\x00\x00\x00qt  " + b"\x00" * 100
MKV = b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81\x01\x42\x82\x88matroska" + b"\x00" * 100
FILE_RE = re.compile(r"videos/files/[0-9a-f]{32}\.(mp4|webm)")
COVER_RE = re.compile(r"videos/covers/[0-9a-f]{32}\.webp")


def image(fmt="PNG", size=(64, 36), color="blue"):
    buf = BytesIO()
    Image.new("RGB", size, color).save(buf, fmt)
    return buf.getvalue()


def add_file(client, versions=None, data=MP4, name="clip.mp4", cover=None, **fields):
    """Как форма «+ Добавить видео» с файлом: multipart на /admin/editor/video-add."""
    versions = versions or editor_versions(client)
    form = {"source": "file", "version_videos": versions["version_videos"], "file": (BytesIO(data), name), **fields}
    if cover is not None:
        form["cover"] = (BytesIO(cover[0]), cover[1])
    return post(client, "/admin/editor/video-add", **form)


def set_cover(client, vid, action="upload", cover=None):
    form = {"id": vid, "action": action, "version_videos": editor_versions(client)["version_videos"]}
    if cover is not None:
        form["cover"] = (BytesIO(cover[0]), cover[1])
    return post(client, "/admin/editor/video-cover", **form)


def guest(content_dir):
    return site(content_dir, SECRET_KEY="test-secret", ADMIN_USERNAME="admin",
                ADMIN_PASSWORD_HASH=PASSWORD_HASH).test_client()


def media_files(content_dir, folder="files"):
    path = content_dir / "media" / "videos" / folder
    return sorted(p.name for p in path.iterdir()) if path.is_dir() else []


# --- Тип и размер файла ---

def test_video_type_is_taken_from_the_content():
    assert sniff_video(MP4) == "mp4" and sniff_video(WEBM) == "webm"
    for data, reason in ((MOV, "MOV"), (MKV, "MKV"), (b"<html><script>alert(1)</script>", "не видео"),
                         (b"MZ\x90\x00" + b"\x00" * 64, "не видео"), (b"\x00\x00\x00\x18ftypXXXX" + b"\x00" * 16, "MP4")):
        with pytest.raises(VideoError, match=reason):
            sniff_video(data)


def test_too_big_or_empty_video_leaves_nothing(tmp_path):
    with pytest.raises(VideoError, match="больше 0 МБ"):
        receive_video(tmp_path, BytesIO(MP4), 100)
    with pytest.raises(VideoError, match="пустой"):
        receive_video(tmp_path, BytesIO(b""), 10_000)
    assert list((tmp_path / "videos" / "files").iterdir()) == []


def test_placed_files_get_new_names_and_never_overwrite(tmp_path):
    first = receive_video(tmp_path, BytesIO(MP4), 10**6).place()
    second = receive_video(tmp_path, BytesIO(WEBM), 10**6).place()
    assert FILE_RE.fullmatch(first) and FILE_RE.fullmatch(second) and first != second
    assert second.endswith(".webm") and (tmp_path / first).read_bytes() == MP4
    assert stat.S_IMODE((tmp_path / first).stat().st_mode) == 0o644          # nginx другого пользователя прочтёт
    assert not [p for p in (tmp_path / "videos" / "files").iterdir() if p.name.startswith(".")]


# --- Добавление файла в Редакторе ---

def test_upload_goes_to_the_draft_and_keeps_existing_records(content_dir, admin):
    write_videos(content_dir)
    before = read(content_dir, "videos")
    answer = add_file(admin, title=" Отзыв  с выставки ", description="Крокус")
    assert answer.status_code == 200, answer.get_json()
    result = answer.get_json()
    vid = result["video"]["id"]
    assert result["video"]["source"] == "Файл" and result["video"]["title"] == "Отзыв с выставки"

    library = drafted(content_dir)["videos"]
    assert library[:-1] == before["videos"]                                  # записи RuTube — байт в байт те же
    new = library[-1]
    assert FILE_RE.fullmatch(new["file"]) and "rutube" not in new and "url" not in new
    assert new == {"id": vid, "file": new["file"], "title": "Отзыв с выставки", "description": "Крокус", "active": True}
    assert (content_dir / "media" / new["file"]).read_bytes() == MP4
    assert read(content_dir, "videos") == before                             # сайт не тронут
    assert f'id="v-{vid}"' in result["card"] and '<span class="vcard-badge">Файл</span>' in result["card"]
    assert f'data-video-file="/media/{new["file"]}"' in result["card"]


@pytest.mark.parametrize("data, name, reason", [
    (MOV, "IMG_0001.mov", "MOV"),
    (MKV, "clip.mkv", "MKV"),
    (b"<html><script>alert(1)</script></html>", "clip.mp4", "не видео"),
    (b"#!/bin/sh\nrm -rf /\n", "../../run.sh", "не видео"),
])
def test_wrong_files_are_refused(content_dir, admin, data, name, reason):
    answer = add_file(admin, data=data, name=name)
    result = answer.get_json()
    assert answer.status_code == 400 and result["field"] == "file" and reason in result["message"]
    assert not (content_dir / ".drafts").exists() and media_files(content_dir) == []


def test_size_limits_are_explained(content_dir, admin):
    admin.application.config["ADMIN_MAX_VIDEO_BYTES"] = 1000
    answer = add_file(admin)                                                 # 4 КБ при пределе 1000 байт
    assert answer.status_code == 400 and "больше" in answer.get_json()["message"]
    admin.application.config["ADMIN_VIDEO_COVER_MARGIN"] = 0
    big = add_file(admin, data=MP4 * 3)                                      # весь запрос больше предела
    assert big.status_code == 413 and big.get_json()["field"] == "file" and "не больше" in big.get_json()["message"]
    assert media_files(content_dir) == [] and not (content_dir / ".drafts").exists()


def test_missing_file_wrong_source_and_csrf(content_dir, admin):
    versions = editor_versions(admin)
    no_file = post(admin, "/admin/editor/video-add", source="file", version_videos=versions["version_videos"])
    assert no_file.status_code == 400 and no_file.get_json()["field"] == "file"
    odd = post(admin, "/admin/editor/video-add", source="ftp", version_videos=versions["version_videos"])
    assert odd.status_code == 400
    no_token = admin.post("/admin/editor/video-add", data={"source": "file", "file": (BytesIO(MP4), "a.mp4"), **versions})
    assert no_token.status_code == 400
    stale = add_file(admin, versions={"version_videos": "0" * 16})
    assert stale.status_code == 409
    assert media_files(content_dir) == [] and not (content_dir / ".drafts").exists()


# --- Обложки ---

def test_cover_uploaded_with_the_video_or_taken_from_a_frame(content_dir, admin):
    result = add_file(admin, cover=(image("PNG", (3000, 1200)), "cover.png")).get_json()
    cover = drafted(content_dir)["videos"][-1]["cover"]
    assert COVER_RE.fullmatch(cover) and result["video"]["thumb"] == f"/media/{cover}"
    with Image.open(content_dir / "media" / cover) as img:
        assert img.format == "WEBP" and img.size == (1920, 768)              # не больше 1920 × 1080, пропорции те же
    # Кадр из видео браузер присылает как JPEG
    frame = add_file(admin, data=WEBM, name="b.webm", cover=(image("JPEG"), "frame.jpg")).get_json()
    assert COVER_RE.fullmatch(drafted(content_dir)["videos"][-1]["cover"]) and frame["video"]["thumb"]


def test_bad_cover_is_refused_and_nothing_is_kept(content_dir, admin):
    answer = add_file(admin, cover=(b"<svg onload=alert(1)>", "cover.svg"))
    assert answer.status_code == 400 and answer.get_json()["field"] == "cover"
    fake = add_file(admin, cover=(b"not an image at all", "cover.jpg"))
    assert fake.status_code == 400 and "Обложка не принята" in fake.get_json()["message"]
    assert media_files(content_dir) == [] and media_files(content_dir, "covers") == []


def test_custom_cover_for_rutube_and_back(content_dir, admin):
    write_videos(content_dir)
    make_thumb(content_dir, "a")
    answer = set_cover(admin, IDS["a"], cover=(image(), "my.png"))
    result = answer.get_json()
    assert answer.status_code == 200 and result["custom"] is True
    entry = drafted(content_dir)["videos"][0]
    assert COVER_RE.fullmatch(entry["cover"]) and result["thumb"] == f"/media/{entry['cover']}"
    assert {k: v for k, v in entry.items() if k != "cover"} == LIBRARY["videos"][0]   # ссылка и код RuTube те же
    assert (content_dir / "media" / "videos" / f"{CODES['a']}.webp").exists()          # копия картинки RuTube на месте

    preview = admin.get("/admin/editor/preview").get_data(as_text=True)
    assert f'src="/media/{entry["cover"]}"' in reviews(preview) or f'src="/media/{entry["cover"]}"' in sheet(preview)

    back = set_cover(admin, IDS["a"], action="reset").get_json()
    assert back["custom"] is False and back["thumb"] == f"/media/videos/{CODES['a']}.webp"
    assert "cover" not in drafted(content_dir)["videos"][0]


def test_cover_endpoint_checks_its_input(content_dir, admin):
    write_videos(content_dir)
    assert set_cover(admin, "../../x", cover=(image(), "a.png")).status_code == 400
    assert set_cover(admin, "99990000", cover=(image(), "a.png")).status_code == 409
    assert set_cover(admin, IDS["a"]).status_code == 400                     # нет картинки
    stale = post(admin, "/admin/editor/video-cover", id=IDS["a"], action="upload", version_videos="0" * 16,
                 cover=(BytesIO(image()), "a.png"))
    assert stale.status_code == 409 and not (content_dir / ".drafts").exists()


# --- Доступ к файлам и сайт ---

def test_files_are_private_until_published(content_dir, admin):
    add_file(admin, cover=(image(), "c.png"))
    entry = drafted(content_dir)["videos"][-1]
    visitor = guest(content_dir)
    for path in (entry["file"], entry["cover"]):
        assert admin.get(f"/media/{path}").status_code == 200
        assert visitor.get(f"/media/{path}").status_code == 404
        assert visitor.get(f"/media/{path.replace('videos/', 'Videos/').replace('files/', 'Files/')}").status_code == 404

    assert publish(admin).status_code == 302
    answer = visitor.get(f"/media/{entry['file']}")
    assert answer.status_code == 200 and answer.mimetype == "video/mp4" and answer.data == MP4
    part = visitor.get(f"/media/{entry['file']}", headers={"Range": "bytes=0-99"})   # перемотка
    assert part.status_code == 206 and part.data == MP4[:100]
    assert visitor.get(f"/media/{entry['cover']}").status_code == 200

    # Выключенный ролик — файлы снова не отдаются посетителям
    vid = entry["id"]
    draft(admin, **{f"video.{vid}.title": "x"})                              # без галочки active — выключить
    publish(admin)
    assert visitor.get(f"/media/{entry['file']}").status_code == 404


def test_media_route_serves_video_only_from_its_folder(content_dir, admin):
    (content_dir / "media" / "portfolio").mkdir(parents=True, exist_ok=True)
    (content_dir / "media" / "portfolio" / "x.mp4").write_bytes(MP4)
    visitor = guest(content_dir)
    assert visitor.get("/media/portfolio/x.mp4").status_code == 404
    assert admin.get("/media/portfolio/x.mp4").status_code == 404
    assert visitor.get("/media/videos/files/../../videos.json").status_code == 404
    assert visitor.get("/media/videos/files/%2e%2e/%2e%2e/home.json").status_code == 404
    assert visitor.get("/media/videos/files/x.exe").status_code == 404


def test_homepage_plays_uploaded_video_with_its_cover(content_dir, admin):
    write_videos(content_dir)
    result = add_file(admin, title="Наш стенд", cover=(image(), "c.png")).get_json()
    entry = drafted(content_dir)["videos"][-1]
    field = f"sections.{reviews_index(content_dir)}.items"
    assert draft(admin, **{field: f"{result['video']['id']},{IDS['a']}"}).status_code == 302
    assert publish(admin).status_code == 302
    block = reviews(site(content_dir).test_client().get("/").get_data(as_text=True))
    link = re.search(r'<a class="review-play-link"[^>]*>', block, re.S).group(0)
    assert f'href="/media/{entry["file"]}"' in link and f'data-video-file="/media/{entry["file"]}"' in link
    assert f'data-video-poster="/media/{entry["cover"]}"' in link and "data-video-player" not in link
    assert f'src="/media/{entry["cover"]}"' in block and "Наш стенд" in block
    assert player_codes(block) == [CODES["a"]]                               # RuTube рядом работает как раньше


def test_three_videos_of_both_kinds_in_order(content_dir, admin):
    write_videos(content_dir)
    first = add_file(admin).get_json()["video"]["id"]
    second = add_file(admin, data=WEBM, name="b.webm").get_json()["video"]["id"]
    field = f"sections.{reviews_index(content_dir)}.items"
    assert draft(admin, **{field: f"{second},{IDS['b']},{first}"}).status_code == 302
    assert publish(admin).status_code == 302
    assert positions(content_dir) == [second, IDS["b"], first]
    block = reviews(site(content_dir).test_client().get("/").get_data(as_text=True))
    assert re.findall(r'data-video-file="/media/videos/files/[0-9a-f]{32}\.(mp4|webm)"', block) == ["webm", "mp4"]
    too_many = draft(admin, **{field: f"{first},{second},{IDS['a']},{IDS['b']}"})
    assert too_many.status_code == 400 and "не больше 3" in too_many.get_data(as_text=True)


# --- Библиотека: правка, удаление, черновик и история ---

def test_uploaded_video_row_edits_texts_and_switch(content_dir, admin):
    write_videos(content_dir)
    vid = add_file(admin).get_json()["video"]["id"]
    group = admin.get("/admin/editor").get_data(as_text=True)
    row = group.split(f'id="v-{vid}"', 1)[1].split("</li>", 1)[0]
    assert f'name="video.{vid}.url"' not in row and f'name="video.{vid}.title"' in row   # у файла нет ссылки
    assert "Взять текущий кадр" in row and "<video" in row
    fields = {f"video.{vid}.title": "Новое", f"video.{vid}.description": "Описание", **row_fields("a")}
    assert draft(admin, **fields).status_code == 302                         # без галочки — выключено
    entry = next(v for v in drafted(content_dir)["videos"] if v["id"] == vid)
    assert entry["title"] == "Новое" and entry["active"] is False and FILE_RE.fullmatch(entry["file"])


def test_discarded_draft_moves_its_files_to_history(content_dir, admin):
    write_videos(content_dir)
    make_thumb(content_dir, "a")
    add_file(admin, cover=(image(), "c.png"))
    entry = drafted(content_dir)["videos"][-1]
    assert post(admin, "/admin/editor/discard").status_code == 302
    assert media_files(content_dir) == [] and media_files(content_dir, "covers") == []
    kept = list((content_dir / ".history" / "media").glob("*/videos/*/*"))
    assert sorted(p.relative_to(p.parents[2]).as_posix() for p in kept) == sorted([entry["file"], entry["cover"]])
    assert (content_dir / "media" / "videos" / f"{CODES['a']}.webp").exists()          # обложки RuTube не трогаются


def test_removed_video_keeps_its_file_for_history_and_restore(content_dir, admin):
    write_videos(content_dir)
    vid = add_file(admin).get_json()["video"]["id"]
    publish(admin)
    path = read(content_dir, "videos")["videos"][-1]["file"]
    assert draft(admin, video_action=f"delete:{vid}").status_code == 302
    assert publish(admin).status_code == 302
    assert vid not in {v["id"] for v in read(content_dir, "videos")["videos"]}
    assert (content_dir / "media" / path).read_bytes() == MP4                # на файл ссылается история версий
    assert guest(content_dir).get(f"/media/{path}").status_code == 404       # но посетителям он больше не виден

    # Прежняя версия из истории возвращает ролик в черновик — файл на месте
    version = next(p.name for p in sorted((content_dir / ".history").glob("videos-*.json"), reverse=True)
                   if any(v.get("id") == vid for v in json.loads(p.read_text(encoding="utf-8"))["videos"]))
    assert post(admin, "/admin/editor/restore", version=version).status_code == 302
    assert any(v["id"] == vid for v in drafted(content_dir)["videos"])
    assert admin.get(f"/media/{path}").status_code == 200


def test_publishing_keeps_media_of_published_and_history(content_dir, admin):
    add_file(admin)
    publish(admin)
    published = read(content_dir, "videos")["videos"][0]["file"]
    add_file(admin, data=WEBM, name="b.webm")
    publish(admin)                                                           # sweep после публикации
    assert (content_dir / "media" / published).exists() and len(media_files(content_dir)) == 2
    assert not (content_dir / ".history" / "media").exists()


# --- Проверка контента ---

def test_check_content_knows_uploaded_videos(content_dir):
    entry = {"id": "abcd0000", "file": "videos/files/" + "a" * 32 + ".mp4", "title": "", "description": "", "active": True}
    write_videos(content_dir, {"videos": [entry]})
    assert any("не найден" in e for e in validate_content(content_dir, STATIC))
    folder = content_dir / "media" / "videos" / "files"
    folder.mkdir(parents=True)
    (folder / ("a" * 32 + ".mp4")).write_bytes(MP4)
    assert not [e for e in validate_content(content_dir, STATIC) if "videos.json" in e]
    for broken, message in (({"file": "../../etc/passwd"}, "file —"), ({"file": "videos/files/x.exe"}, "file —"),
                            ({"rutube": CODES["a"]}, "только file"), ({"cover": "../x.webp"}, "cover —")):
        assert any(message in e for e in validate({"videos": [{**entry, **broken}]})), broken


def test_rutube_thumbnail_files_are_readable_by_the_web_server(tmp_path):
    from app.videos import fetch_thumbnail
    jpeg = image("JPEG", (320, 180))

    def get(url, limit):
        return json.dumps({"thumbnail_url": "https://pic.rtbcdn.ru/x.jpg"}).encode() if "api" in url else jpeg

    assert fetch_thumbnail(tmp_path, video("a"), get)
    assert stat.S_IMODE((tmp_path / "videos" / f"{CODES['a']}.webp").stat().st_mode) == 0o644
