"""Видео Vimeo в галерее стенда: поле video проекта → плеер только после «Смотреть видео».

Ссылка проверяется на сервере (content.vimeo_video), адрес плеера собирается из id и hash,
а не берётся из ввода. Жизненный цикл iframe проверяется на настоящем main.js
(tests/gallery_harness.js, Node без браузера).
"""

import json
import re
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest

from app import create_app
from app.content import validate_content, vimeo_page_url, vimeo_player_url, vimeo_video
from app.portfolio import build_years
from tests.test_admin import admin, app, content_dir, create, projects  # noqa: F401  (фикстуры и помощники)

HARNESS = Path(__file__).resolve().parent / "gallery_harness.js"
PLAYER = "https://player.vimeo.com/video/76979871?dnt=1"
PRIVATE = "https://player.vimeo.com/video/76979871?h=8272103f6e&dnt=1"


# --- Разбор ссылок ---

@pytest.mark.parametrize("url, player", [
    ("https://vimeo.com/76979871", PLAYER),
    ("http://vimeo.com/76979871", PLAYER),
    ("https://www.vimeo.com/76979871/", PLAYER),
    ("  https://vimeo.com/76979871  ", PLAYER),
    ("https://vimeo.com/76979871?share=copy", PLAYER),
    ("https://VIMEO.com/76979871", PLAYER),
    ("https://vimeo.com/channels/staffpicks/76979871", PLAYER),
    ("https://vimeo.com/groups/stands/videos/76979871", PLAYER),
    ("https://vimeo.com/showcase/1234567/video/76979871", PLAYER),
    ("https://player.vimeo.com/video/76979871", PLAYER),
    ("https://player.vimeo.com/video/76979871?badge=0&autopause=0", PLAYER),
    # Закрытое видео: второй код (hash) обязателен для воспроизведения и сохраняется
    ("https://vimeo.com/76979871/8272103f6e", PRIVATE),
    ("https://vimeo.com/76979871/8272103f6e?share=copy", PRIVATE),
    ("https://vimeo.com/76979871?h=8272103f6e", PRIVATE),
    ("https://player.vimeo.com/video/76979871?h=8272103f6e", PRIVATE),
    ("https://player.vimeo.com/video/76979871?h=8272103f6e&autoplay=1", PRIVATE),   # autoplay отбрасывается
])
def test_vimeo_links_become_player_url(url, player):
    assert vimeo_player_url(url) == player


@pytest.mark.parametrize("url", [
    None, "", "   ", 12345,
    "javascript:alert(1)",
    "JaVaScRiPt:alert(document.cookie)//vimeo.com/76979871",
    "data:text/html,<script>alert(1)</script>",
    '<iframe src="https://player.vimeo.com/video/76979871"></iframe>',
    "https://vimeo.com/76979871\"><script>alert(1)</script>",
    "https://vimeo.com/76979871/<script>",
    "https://player.vimeo.com/video/76979871?h=\"onload=alert(1)",
    "https://player.vimeo.com/video/76979871?h=8272103f6e&h=0000000000",            # два разных hash
    "https://vimeo.com/76979871/8272103f6e?h=0000000000",
    "https://vimeo.com/76979871/NOTAHASH",
    "https://evil.example/76979871",
    "https://vimeo.com.evil.example/76979871",
    "https://evilvimeo.com/76979871",
    "https://vimeo.evil.example/video/76979871",
    "https://user:pass@vimeo.com/76979871",
    "https://vimeo.com@evil.example/76979871",
    "https://vimeo.com:8443/76979871",
    "ftp://vimeo.com/76979871",
    "//vimeo.com/76979871",
    "vimeo.com/76979871",
    "https://vimeo.com/",
    "https://vimeo.com/about",
    "https://vimeo.com/76979871/8272103f6e/extra",
    "https://vimeo.com/%37%36979871",
    "https://player.vimeo.com/76979871",
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://vimeo.com/" + "1" * 13,
])
def test_unsafe_or_unsupported_links_are_rejected(url):
    assert vimeo_video(url) is None
    assert vimeo_player_url(url) == "" and vimeo_page_url(url) == ""


def test_page_url_is_rebuilt_from_id_and_hash():
    # Ссылка под карточкой — тоже собрана сервером, а не взята из ввода
    assert vimeo_page_url("https://vimeo.com/channels/staffpicks/76979871?share=copy") == "https://vimeo.com/76979871"
    assert vimeo_page_url("https://player.vimeo.com/video/76979871?h=8272103f6e") == "https://vimeo.com/76979871/8272103f6e"


# --- Данные галереи и HTML ---

def stand(**fields):
    base = {"id": "p", "status": "published", "title": "P", "year": 2026, "photos": ["static/img/a.webp"]}
    years = build_years([{**base, **fields}], 2026, Path("/nonexistent"))
    return years[0]["groups"][0]["stands"][0]


def test_stand_gets_player_url_only_for_valid_vimeo():
    assert stand(video="https://vimeo.com/76979871/8272103f6e")["vimeo"] == PRIVATE
    assert stand(video="https://vimeo.com/76979871/8272103f6e")["video"] == "https://vimeo.com/76979871/8272103f6e"
    for bad in ("", "https://www.youtube.com/watch?v=x", "javascript:alert(1)"):
        assert stand(video=bad)["vimeo"] == "" and stand(video=bad)["video"] == ""


STAND = "vimeo-stand"


def with_video(content_dir, url):
    """Во временную копию контента — опубликованный стенд с видео; он же первый на главной.

    От рабочих проектов портфолио тест не зависит: их состав меняется в админке.
    """
    path = content_dir / "portfolio.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["projects"].append({"id": STAND, "status": "published", "title": "Стенд с видео", "year": date.today().year,
                             "exhibition": "Выставка", "order": 0, "cover": "", "specs": [], "description": "",
                             "photos": ["static/img/stand/frame-159.webp", "static/img/stand/frame-140.webp"],
                             "video": url})
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    home_path = content_dir / "home.json"
    home = json.loads(home_path.read_text(encoding="utf-8"))
    section = next(s for s in home["sections"] if s.get("type") == "projects")
    section["visible"] = True
    section["items"] = [{"project": STAND}] + [i for i in section.get("items", []) if i.get("project") != STAND]
    home_path.write_text(json.dumps(home, ensure_ascii=False), encoding="utf-8")


def galleries(html):
    return [json.loads(m.replace("&#39;", "'")) for m in re.findall(r"data-gallery='([^']+)'", html)]


def test_pages_have_player_url_only_in_gallery_data_and_no_iframe(app, content_dir):
    with_video(content_dir, "https://vimeo.com/76979871/8272103f6e")
    client = app.test_client()

    for url in ("/", "/portfolio"):
        html = client.get(url).get_data(as_text=True)
        # Ни iframe, ни скриптов или превью Vimeo: адрес плеера есть только внутри data-gallery
        assert "<iframe" not in html
        outside = re.sub(r"data-gallery='[^']*'", "", html)
        assert "player.vimeo.com" not in outside and "vimeocdn" not in outside
        withvideo = [g for g in galleries(html) if g["vimeo"]]
        assert withvideo and all(g["vimeo"] == PRIVATE for g in withvideo)
        # Кнопка в окне галереи изначально скрыта
        assert re.search(r'<button class="btn btn-quiet gallery-video" type="button" data-gallery-video hidden>', html)


def test_stand_without_video_gets_empty_player_url(app, content_dir):
    with_video(content_dir, "")
    for url in ("/", "/portfolio"):
        found = [g for g in galleries(app.test_client().get(url).get_data(as_text=True)) if g["title"] == "Стенд с видео"]
        assert found and all(g["vimeo"] == "" for g in found)


def test_card_video_button_plays_on_site_instead_of_external_link(app, content_dir):
    # Под карточкой — кнопка, которая открывает галерею с плеером; на vimeo.com посетителя не уводим
    with_video(content_dir, "https://vimeo.com/76979871/8272103f6e")
    html = app.test_client().get("/portfolio").get_data(as_text=True)

    assert 'class="stand-video" type="button" data-play-video' in html
    assert 'href="https://vimeo.com/76979871' not in html


def test_invalid_link_in_content_breaks_nothing(app, content_dir):
    with_video(content_dir, 'javascript:alert(1)"><script>alert(1)</script>')
    client = app.test_client()

    for url in ("/", "/portfolio"):
        response = client.get(url)
        html = response.get_data(as_text=True)
        assert response.status_code == 200
        found = [g for g in galleries(html) if g["title"] == "Стенд с видео"]
        assert found and "alert(1)" not in html and all(g["vimeo"] == "" for g in found)


def test_csp_allows_frames_only_from_video_players(app):
    csp = app.test_client().get("/").headers["Content-Security-Policy"]
    rules = dict(part.strip().split(" ", 1) for part in csp.split(";"))

    assert rules["frame-src"] == "https://player.vimeo.com https://rutube.ru"
    # Остальное не ослаблено
    assert rules["default-src"] == "'self'" and rules["script-src"] == "'self'" and rules["img-src"] == "'self'"
    assert rules["connect-src"] == "'self'" and rules["frame-ancestors"] == "'none'"


# --- Админка и проверка контента ---

def test_admin_accepts_vimeo_link_as_entered(admin, content_dir):
    response = create(admin, title="Стенд с видео", video="https://vimeo.com/76979871/8272103f6e")

    assert response.status_code == 302
    saved = next(p for p in projects(content_dir) if p["title"] == "Стенд с видео")
    assert saved["video"] == "https://vimeo.com/76979871/8272103f6e"


@pytest.mark.parametrize("url", ["https://www.youtube.com/watch?v=x", "https://evil.example/76979871",
                                 "javascript:alert(1)", '<iframe src="https://player.vimeo.com/video/1">'])
def test_admin_rejects_non_vimeo_link(admin, content_dir, url):
    before = projects(content_dir)
    response = create(admin, title="Стенд с видео", video=url)

    assert response.status_code == 400
    assert "Ссылка на видео — только Vimeo" in response.get_data(as_text=True)
    assert projects(content_dir) == before


def test_check_content_rejects_non_vimeo_video_in_published_project(content_dir):
    with_video(content_dir, "https://www.youtube.com/watch?v=x")
    errors = validate_content(content_dir, create_app({"TESTING": True}).static_folder)

    assert any(STAND in e and "Vimeo" in e for e in errors)


def test_check_content_accepts_vimeo_video(content_dir):
    with_video(content_dir, "https://vimeo.com/76979871/8272103f6e")
    errors = validate_content(content_dir, create_app({"TESTING": True}).static_folder)

    assert not [e for e in errors if STAND in e]


# --- Жизненный цикл iframe в галерее (настоящий main.js) ---

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="нужен Node.js")


def gallery(scenario):
    out = subprocess.run(["node", str(HARNESS), scenario], capture_output=True, text=True, timeout=20, check=True)
    return json.loads(out.stdout)


@needs_node
def test_gallery_without_video_works_as_before():
    s = gallery("no_video")

    for state in (s["opened"], s["after_step"]):
        assert state["iframes"] == 0 and state["buttonHidden"] and state["errorHidden"] and not state["imageHidden"]
    assert s["after_step"]["imageSrc"] == "/media/b-1920.webp"


@needs_node
def test_opening_gallery_does_not_create_iframe():
    s = gallery("open_without_click")

    assert s["iframes"] == 0
    assert not s["buttonHidden"] and s["buttonText"] == "Смотреть видео"


@needs_node
def test_iframe_is_created_only_on_click_with_safe_attributes():
    s = gallery("play")

    assert s["iframes"] == 1 and s["imageHidden"] and s["buttonText"] == "К фото"
    assert s["src"] == "https://player.vimeo.com/video/76979871?h=8272103f6e&dnt=1"   # без autoplay
    assert s["attrs"] == {"title": "Стенд — видео", "loading": "lazy", "allow": "encrypted-media; picture-in-picture",
                          "allowFullscreen": True, "referrerPolicy": "strict-origin-when-cross-origin",
                          "className": "gallery-player"}


@needs_node
@pytest.mark.parametrize("scenario, photo", [
    ("play_then_step", "/media/b-1920.webp"),
    ("play_then_thumb", "/media/c-1920.webp"),
    ("play_then_key", "/media/c-1920.webp"),
    ("play_then_back_button", "/media/a-1920.webp"),
])
def test_switching_to_photos_removes_iframe(scenario, photo):
    s = gallery(scenario)

    assert s["iframes"] == 0 and not s["imageHidden"] and s["imageSrc"] == photo
    assert s["buttonText"] == "Смотреть видео" and s["errorHidden"]


@needs_node
def test_closing_gallery_removes_iframe():
    s = gallery("play_then_close")

    assert s["iframes"] == 0 and not s["open"]


@needs_node
def test_other_stand_opens_without_old_player():
    s = gallery("play_then_other_stand")

    assert s["iframes"] == 0 and s["buttonHidden"] and s["open"]


@needs_node
def test_unsafe_player_url_in_card_data_never_creates_iframe():
    for result in gallery("unsafe_vimeo").values():
        assert result == {"buttonHidden": True, "iframes": 0}


@needs_node
def test_player_that_does_not_load_shows_message_and_way_back():
    s = gallery("load_timeout")

    assert s["failed"]["iframes"] == 0 and not s["failed"]["errorHidden"] and s["failed"]["buttonText"] == "К фото"
    assert s["back"]["errorHidden"] and not s["back"]["imageHidden"] and s["back"]["buttonText"] == "Смотреть видео"


@needs_node
def test_loaded_player_stays():
    s = gallery("load_ok")

    assert s["iframes"] == 1 and s["errorHidden"]
