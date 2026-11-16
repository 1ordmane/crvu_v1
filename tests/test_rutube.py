"""Видео Rutube в галерее стенда — наравне с Vimeo, в том же поле video проекта.

Из ссылки берутся только код видео (32 шестнадцатеричных символа) и ключ закрытого видео (?p=);
адрес плеера собирается заново: https://rutube.ru/play/embed/<код>/[?p=<ключ>]. Плеер — только
после «Смотреть видео», CSP разрешает рамки лишь от player.vimeo.com и rutube.ru.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app import create_app
from app.content import rutube_video, validate_content, video_page_url, video_player_url
from app.portfolio import build_years
from tests.test_admin import admin, app, content_dir, create, projects  # noqa: F401  (фикстуры и помощники)

HARNESS = Path(__file__).resolve().parent / "gallery_harness.js"
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="нужен Node.js")
ID = "c6cc4d620b1d4338901770a44b3e82f4"
PLAYER = f"https://rutube.ru/play/embed/{ID}/"
PRIVATE = f"https://rutube.ru/play/embed/{ID}/?p=f2Ar8UZNsfWnI5cS2BbVZQ"


@pytest.mark.parametrize("url, player", [
    (f"https://rutube.ru/video/{ID}/", PLAYER),
    (f"https://rutube.ru/video/{ID}", PLAYER),
    (f"http://rutube.ru/video/{ID}/", PLAYER),
    (f"https://www.rutube.ru/video/{ID}/", PLAYER),
    (f"  https://rutube.ru/video/{ID}/  ", PLAYER),
    (f"https://rutube.ru/video/{ID}/?r=wd", PLAYER),                   # метки «Поделиться» отбрасываются
    (f"https://rutube.ru/video/{ID.upper()}/", PLAYER),
    (f"https://rutube.ru/shorts/{ID}/", PLAYER),
    (f"https://rutube.ru/play/embed/{ID}", PLAYER),
    (f"https://rutube.ru/play/embed/{ID}/?autoplay=1", PLAYER),        # автозапуска нет
    # Закрытое видео: ключ доступа p обязателен для воспроизведения и сохраняется
    (f"https://rutube.ru/video/private/{ID}/?p=f2Ar8UZNsfWnI5cS2BbVZQ", PRIVATE),
    (f"https://rutube.ru/video/{ID}/?p=f2Ar8UZNsfWnI5cS2BbVZQ", PRIVATE),
    (f"https://rutube.ru/play/embed/{ID}/?p=f2Ar8UZNsfWnI5cS2BbVZQ", PRIVATE),
])
def test_rutube_links_become_player_url(url, player):
    assert video_player_url(url) == player


def test_vimeo_still_supported_by_common_helpers():
    assert video_player_url("https://vimeo.com/76979871") == "https://player.vimeo.com/video/76979871?dnt=1"
    assert video_page_url("https://vimeo.com/76979871/8272103f6e") == "https://vimeo.com/76979871/8272103f6e"


def test_page_url_is_rebuilt():
    assert video_page_url(f"https://rutube.ru/play/embed/{ID}/?r=x") == f"https://rutube.ru/video/{ID}/"
    assert video_page_url(f"https://rutube.ru/video/private/{ID}/?p=f2Ar8UZNsfWnI5cS2BbVZQ") == \
        f"https://rutube.ru/video/private/{ID}/?p=f2Ar8UZNsfWnI5cS2BbVZQ"


@pytest.mark.parametrize("url", [
    None, "", 12345,
    "javascript:alert(1)",
    f'<iframe src="https://rutube.ru/play/embed/{ID}"></iframe>',
    f"https://rutube.ru/video/{ID}/\"><script>alert(1)</script>",
    f"https://rutube.ru/video/{ID}/?p=\"onload=alert(1)",
    f"https://rutube.ru/video/{ID}/?p=abcdef1&p=ghijkl2",               # два ключа
    f"https://rutube.ru/video/private/{ID}/",                           # закрытое без ключа не играет
    f"https://rutube.ru/video/{ID[:-1]}/",                              # не 32 символа
    f"https://rutube.ru/video/{ID}x/",
    "https://rutube.ru/video/12345/",
    "https://rutube.ru/channel/123456/",
    "https://rutube.ru/",
    f"https://rutube.ru.evil.example/video/{ID}/",
    f"https://evilrutube.ru/video/{ID}/",
    f"https://rutube.evil.example/video/{ID}/",
    f"https://user:pass@rutube.ru/video/{ID}/",
    f"https://rutube.ru@evil.example/video/{ID}/",
    f"https://rutube.ru:8443/video/{ID}/",
    f"ftp://rutube.ru/video/{ID}/",
    f"//rutube.ru/video/{ID}/",
    f"rutube.ru/video/{ID}/",
    f"https://rutube.ru/video/{ID}/extra/",
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
])
def test_unsafe_or_unsupported_links_are_rejected(url):
    assert rutube_video(url) is None
    assert video_player_url(url) == "" or "vimeo" in video_player_url(url)
    assert "rutube" not in video_player_url(url) and "rutube" not in video_page_url(url)


def stand(**fields):
    base = {"id": "p", "status": "published", "title": "P", "year": 2026, "photos": ["static/img/a.webp"]}
    return build_years([{**base, **fields}], 2026, Path("/nonexistent"))[0]["groups"][0]["stands"][0]


def test_stand_gets_rutube_player_and_page():
    s = stand(video=f"https://rutube.ru/video/{ID}/?r=wd")
    assert s["vimeo"] == PLAYER and s["video"] == f"https://rutube.ru/video/{ID}/"
    assert stand()["vimeo"] == "" and stand()["video"] == ""


def test_csp_allows_frames_only_from_vimeo_and_rutube(app):
    csp = app.test_client().get("/").headers["Content-Security-Policy"]
    rules = dict(part.strip().split(" ", 1) for part in csp.split(";"))
    assert rules["frame-src"] == "https://player.vimeo.com https://rutube.ru"
    assert rules["default-src"] == "'self'" and rules["script-src"] == "'self'" and rules["connect-src"] == "'self'"


def test_admin_saves_rutube_link_as_entered_and_keeps_draft(admin, content_dir):
    url = f"https://rutube.ru/video/{ID}/"
    response = create(admin, title="Стенд Rutube", video=url)

    assert response.status_code == 302
    saved = next(p for p in projects(content_dir) if p["title"] == "Стенд Rutube")
    assert saved["video"] == url and saved["status"] == "draft"


def test_admin_saves_project_without_video(admin, content_dir):
    assert create(admin, title="Без видео", video="").status_code == 302
    saved = next(p for p in projects(content_dir) if p["title"] == "Без видео")
    assert saved["video"] == "" and saved["status"] == "draft"


@pytest.mark.parametrize("url", ["https://www.youtube.com/watch?v=x", f"https://rutube.ru.evil.example/video/{ID}/",
                                 f'<iframe src="https://rutube.ru/play/embed/{ID}">', "https://rutube.ru/channel/1/"])
def test_admin_rejects_foreign_or_broken_links(admin, content_dir, url):
    before = projects(content_dir)
    response = create(admin, title="Стенд с видео", video=url)

    assert response.status_code == 400
    assert "только Vimeo или Rutube" in response.get_data(as_text=True)
    assert projects(content_dir) == before


def test_admin_form_explains_both_services(admin):
    html = admin.get("/admin/projects/new").get_data(as_text=True)
    assert "Видео проекта (Vimeo или Rutube)" in html
    assert "https://rutube.ru/video/" in html and "https://vimeo.com/" in html


def test_check_content_accepts_rutube_in_published_project(content_dir):
    path = content_dir / "portfolio.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    project = next(p for p in data["projects"] if p["status"] == "published")
    project["video"] = f"https://rutube.ru/video/{ID}/"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    errors = validate_content(content_dir, create_app({"TESTING": True}).static_folder)
    assert not [e for e in errors if project["id"] in e]


def gallery(scenario):
    out = subprocess.run(["node", str(HARNESS), scenario], capture_output=True, text=True, timeout=20, check=True)
    return json.loads(out.stdout)


@needs_node
def test_rutube_player_is_created_only_on_click():
    s = gallery("rutube_play")
    assert s["before"] == {"buttonHidden": False, "iframes": 0}
    assert s["after"]["iframes"] == 1 and s["after"]["src"] == "https://rutube.ru/play/embed/" \
        "c6cc4d620b1d4338901770a44b3e82f4/?p=f2Ar8UZNsfWnI5cS2BbVZQ"


@needs_node
def test_forged_rutube_addresses_never_become_iframe():
    for result in gallery("unsafe_rutube").values():
        assert result == {"buttonHidden": True, "iframes": 0}


# --- Видео внутри сайта: элемент галереи и кнопка на карточке, без перехода на Rutube/Vimeo ---

VIMEO_PLAYER = "https://player.vimeo.com/video/76979871?h=8272103f6e&dnt=1"


@needs_node
def test_video_is_a_tile_in_gallery_and_loads_only_on_click():
    s = gallery("video_tile")
    assert [t["label"] for t in s["opened"]["tiles"]] == ["Фото 1", "Фото 2", "Фото 3", "Видео"]
    assert s["opened"]["tiles"][-1]["video"] and s["opened"]["iframes"] == 0      # превью без плеера
    assert s["playing"]["state"]["iframes"] == 1 and s["playing"]["state"]["src"].startswith("https://rutube.ru/play/embed/")
    assert [t["current"] for t in s["playing"]["tiles"]] == ["false", "false", "false", "true"]
    assert s["back"]["iframes"] == 0 and not s["back"]["imageHidden"]               # плеер закрыт, снова фото


@needs_node
def test_single_photo_with_video_still_offers_video_tile():
    s = gallery("one_photo_with_video")
    assert not s["hidden"] and [t["video"] for t in s["tiles"]] == [False, True]


@needs_node
def test_no_video_no_tile():
    s = gallery("photos_without_video")
    assert not any(t["video"] for t in s["tiles"]) and len(s["tiles"]) == 3


@needs_node
def test_card_button_opens_same_gallery_with_player():
    s = gallery("card_play")
    assert s["playing"]["open"] and s["playing"]["iframes"] == 1 and s["playing"]["imageHidden"]
    assert s["back"]["iframes"] == 0 and not s["back"]["imageHidden"] and s["back"]["open"]
    # стрелки фото не перекрывают плеер и возвращаются вместе с фото
    assert s["playing"]["stepsHidden"] == [True, True] and s["back"]["stepsHidden"] == [False, False]


def test_only_one_close_button_visible_over_portfolio():
    css = (Path(__file__).resolve().parent.parent / "app" / "static" / "css" / "style.css").read_text(encoding="utf-8")
    assert "body:has(#gallery[open]) .sheet-close {\n  visibility: hidden;" in css


@needs_node
def test_card_button_with_forged_address_creates_no_player():
    s = gallery("card_play_unsafe")
    assert s["open"] and s["iframes"] == 0 and s["buttonHidden"]


def set_videos(content_dir, videos):
    path = content_dir / "portfolio.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for project in data["projects"]:
        if project["id"] in videos:
            project["video"] = videos[project["id"]]
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def test_portfolio_card_plays_inside_site_not_external_link(content_dir):
    data = json.loads((content_dir / "portfolio.json").read_text(encoding="utf-8"))
    published = [p["id"] for p in data["projects"] if p["status"] == "published"][:2]
    set_videos(content_dir, {published[0]: f"https://rutube.ru/video/{ID}/", published[1]: "https://vimeo.com/76979871"})
    html = create_app({"TESTING": True, "CONTENT_DIR": str(content_dir)}).test_client().get("/portfolio") \
        .get_data(as_text=True)

    assert html.count("data-play-video") == 2
    assert 'href="https://rutube.ru' not in html and 'href="https://vimeo.com' not in html     # без ухода с сайта
    assert "<iframe" not in html                                                             # плеер — только по нажатию


def test_card_without_video_has_no_play_button(content_dir):
    html = create_app({"TESTING": True, "CONTENT_DIR": str(content_dir)}).test_client().get("/portfolio") \
        .get_data(as_text=True)
    assert "data-play-video" not in html


def test_draft_with_video_stays_hidden(content_dir):
    data = json.loads((content_dir / "portfolio.json").read_text(encoding="utf-8"))
    draft = next(p for p in data["projects"] if p["status"] == "draft")
    set_videos(content_dir, {draft["id"]: f"https://rutube.ru/video/{ID}/"})
    html = create_app({"TESTING": True, "CONTENT_DIR": str(content_dir)}).test_client().get("/portfolio") \
        .get_data(as_text=True)
    assert ID not in html and draft["title"] not in html
