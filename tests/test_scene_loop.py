"""Петля pre-video.mp4 на первом экране главной поверх сцены сборки стенда.

Вверху страницы — видео без звука по кругу; при прокрутке оно растворяется в прежние кадры (разборка,
дальше сборка — без изменений), при возврате наверх появляется снова. Без JavaScript и при уменьшении
движения видео не грузится — остаётся прежняя сцена; не дал браузер автозапуск — виден poster.
Сценарий в Chrome — tests/scene_loop_browser.js (нет Chrome или Node 22+ — пропускается).
"""

import json
import re
import subprocess
import threading
import time
from pathlib import Path
from urllib.request import urlopen

import pytest
from werkzeug.serving import make_server

from app import create_app
from tests.test_video_editor_browser import CHROME, free_port, node_has_websocket

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "app" / "static"
CSS = (STATIC / "css" / "style.css").read_text(encoding="utf-8")
JS = (STATIC / "js" / "main.js").read_text(encoding="utf-8")
SCRIPT = Path(__file__).resolve().parent / "scene_loop_browser.js"


@pytest.fixture
def home(make_content):
    app = create_app({"TESTING": True, "CONTENT_DIR": str(make_content())})
    return app.test_client().get("/").get_data(as_text=True)


def loop_tag(html):
    return re.search(r'<video class="scene-loop"[^>]*>', html).group(0)


def test_loop_sits_in_the_scene_above_the_canvas_and_is_decorative(home):
    scene = re.search(r'<div class="scene" aria-hidden="true"[^>]*>.*?</div>', home, re.S).group(0)
    assert scene.index("data-scene-canvas") < scene.index("data-scene-loop")       # над холстом
    tag = loop_tag(home)
    for attr in ("autoplay", "loop", "muted", "playsinline", 'preload="none"', 'tabindex="-1"',
                 "disablepictureinpicture", "disableremoteplayback"):
        assert re.search(rf"\s{attr}[\s>]", tag), attr
    assert "controls" not in tag
    # Файл подставляет main.js: без него и при уменьшении движения 10 МБ не грузятся
    assert " src=" not in tag and 'data-src="/static/video/pre-video.mp4"' in tag
    assert 'poster="/static/video/pre-video-poster.webp"' in tag


def test_video_and_poster_are_static_assets():
    video = STATIC / "video" / "pre-video.mp4"
    with video.open("rb") as f:
        assert f.read(12)[4:8] == b"ftyp"                                         # MP4
    assert video.stat().st_size < 15 * 1024 * 1024
    with (STATIC / "video" / "pre-video-poster.webp").open("rb") as f:
        head = f.read(12)
    assert head[:4] == b"RIFF" and head[8:] == b"WEBP"


def test_loop_is_hidden_until_the_script_shows_it():
    rule = re.search(r"^\.scene-loop \{(.*?)^\}", CSS, re.S | re.M).group(1)
    assert "opacity: 0;" in rule and "position: absolute;" in rule and "object-fit: cover;" in rule
    assert "@media (prefers-reduced-motion: reduce) {\n  .scene-loop {\n    display: none;" in CSS
    # Петля стоит там же, где холст с кадрами: стенд петли ложится на стенд кадров
    canvas = re.search(r"^body:has\(\[data-scene-light\]\) \.scene-canvas \{(.*?)^\}", CSS, re.S | re.M).group(1)
    for prop in ("top: 0;", "right: 0;", "width: 100%;", "height: min(100%, 91.5vw);", "object-position: 100% 50%;"):
        assert prop in rule and prop in canvas, prop


def test_script_fades_the_loop_with_the_smoothed_scroll_and_keeps_the_frames():
    block = re.search(r"\n    function updateLoop\(y\) \{.*?\n    \}\n", JS, re.S).group(0)
    assert "window.innerHeight * LOOP_SCREENS" in block and "loop.style.opacity" in block
    # Петля и у низа страницы: расстояние — до ближайшего края, верха или низа
    assert "const near = Math.max(0, Math.min(y, range - y) - 1);" in block
    assert "loop.pause()" in block and "loop.src = loop.dataset.src" in block and ".catch(" in block
    # Прозрачность — по тому же сглаженному положению, что и кадр; кадры и линия монтажа — как прежде
    assert "draw(frameAt(current));\n      updateLoop(current);\n      updateGauge(p);" in JS
    # При уменьшении движения выход раньше первого tick — петля не включается
    scene = JS[JS.index("function initScene()"):JS.index("function initHeader()")]
    assert scene.index("if (reduceMotion) {") < scene.index("loadAll();")
    assert "navigator.connection.saveData" in scene


@pytest.fixture
def site_and_chrome(make_content, tmp_path):
    app = create_app({"TESTING": True, "CONTENT_DIR": str(make_content())})
    server = make_server("127.0.0.1", free_port(), app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    devtools = free_port()
    chrome = subprocess.Popen([CHROME, "--headless=new", f"--remote-debugging-port={devtools}",
                               f"--user-data-dir={tmp_path / 'chrome'}", "--no-first-run", "--no-default-browser-check",
                               "--autoplay-policy=no-user-gesture-required", "about:blank"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            try:
                urlopen(f"http://127.0.0.1:{devtools}/json/version", timeout=1)
                break
            except OSError:
                time.sleep(0.25)
        yield f"http://127.0.0.1:{server.server_port}", devtools
    finally:
        chrome.terminate()
        chrome.wait(timeout=10)
        server.shutdown()


@pytest.mark.skipif(not (CHROME and node_has_websocket()), reason="нужны Chrome и Node 22+ (встроенный WebSocket)")
def test_loop_in_a_real_browser(site_and_chrome):
    base, devtools = site_and_chrome
    run = subprocess.run(["node", str(SCRIPT), base, str(devtools)], capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout.strip().splitlines()[-1])

    # Вверху — петля видна и играет: без звука, по кругу, без кнопок
    assert out["top_plays"] and out["top_opacity"] == 1
    assert out["flags"] == [True, True, True, True, False]
    # Прокрутка — растворяется в кадры; скрытая — на паузе; кадры сборки под ней идут как прежде
    assert 0 < out["mid_opacity"] < 1
    assert out["past_opacity"] == 0 and out["past_paused"] and out["frames_change"]
    # Кадры video.mp4 по прокрутке: разборка, затем сборка; вверх — в обратном порядке, внизу — готовый стенд
    assert out["frames_loaded"] and out["scrub_reversible"] and out["scrub_distinct"] == 4 and out["bottom_is_top"]
    # Низ страницы — та же петля (тот же <video>): растворяется из кадров, играет; выше — кадры, пауза
    assert out["bottom_opacity"] == 1 and out["bottom_plays"] and 0 < out["bottom_mid_opacity"] < 1
    assert out["above_bottom"] == [0, True] and out["single_video"] == 1
    # Наверх — снова видна и играет
    assert out["back_opacity"] == 1 and out["back_plays"]

    # Телефон: под шапкой, стенд целиком на экране, без горизонтальной прокрутки
    box = out["phone_box"]
    assert out["phone_plays"] and box["top"] >= box["header"] - 1
    assert 0 <= box["standLeft"] and box["standRight"] <= 390 and out["phone_fits"]
    assert out["phone_past_opacity"] == 0 and out["phone_bottom"] == [1, True]

    # Уменьшение движения — видео не грузится и не видно
    assert out["reduced"] == {"src": False, "display": "none", "requested": False}
    assert out["errors"] == []
