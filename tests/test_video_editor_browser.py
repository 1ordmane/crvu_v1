"""Окно выбора видеоотзывов в настоящем браузере: выбор до трёх, порядок, «Готово»/«Отмена», поиск,
добавление видеофайла с обложкой-кадром не закрывая окно, черновик → предпросмотр → «Опубликовать».

Сайт поднимается на копии тестового контента, Chrome — без окна, сценарий — tests/video_editor_browser.js
(Node 22+, без зависимостей). Нет Chrome или подходящего Node — тест пропускается: остальные тесты
видеоотзывов (test_videos.py, test_video_uploads.py) проверяют сервер без браузера.
Путь к Chrome можно задать переменной CRVU_CHROME.
"""

import json
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from urllib.request import urlopen

import pytest
from werkzeug.serving import make_server

from app import create_app
from tests.test_admin import PASSWORD, PASSWORD_HASH
from tests.test_videos import IDS, LIBRARY, positions, read, reviews_index, write_videos
from tests.test_reviews import with_reviews

SCRIPT = Path(__file__).resolve().parent / "video_editor_browser.js"
CHROME_PATHS = ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "/Applications/Chromium.app/Contents/MacOS/Chromium")


def find_chrome():
    if os.environ.get("CRVU_CHROME"):
        return os.environ["CRVU_CHROME"]
    for path in CHROME_PATHS:
        if Path(path).exists():
            return path
    return next((shutil.which(n) for n in ("google-chrome", "chromium", "chromium-browser") if shutil.which(n)), None)


def node_has_websocket():
    node = shutil.which("node")
    if not node:
        return False
    return subprocess.run([node, "-e", "process.exit(typeof WebSocket === 'function' ? 0 : 1)"],
                          capture_output=True).returncode == 0


CHROME = find_chrome()
pytestmark = pytest.mark.skipif(not (CHROME and node_has_websocket()),
                                reason="нужны Chrome и Node 22+ (встроенный WebSocket)")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def site_and_chrome(make_content, tmp_path):
    content = make_content()
    with_reviews(content)
    write_videos(content)                      # 6 включённых роликов RuTube (A–F) и выключенный G
    app = create_app({"TESTING": True, "CONTENT_DIR": str(content), "SECRET_KEY": "test-secret",
                      "ADMIN_USERNAME": "admin", "ADMIN_PASSWORD_HASH": PASSWORD_HASH, "VIDEO_THUMBNAILS": False})
    server = make_server("127.0.0.1", free_port(), app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    devtools = free_port()
    chrome = subprocess.Popen([CHROME, "--headless=new", f"--remote-debugging-port={devtools}",
                               f"--user-data-dir={tmp_path / 'chrome'}", "--no-first-run", "--no-default-browser-check",
                               "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            try:
                urlopen(f"http://127.0.0.1:{devtools}/json/version", timeout=1)
                break
            except OSError:
                time.sleep(0.25)
        yield content, f"http://127.0.0.1:{server.server_port}", devtools
    finally:
        chrome.terminate()
        chrome.wait(timeout=10)
        server.shutdown()


def test_video_picker_in_a_real_browser(site_and_chrome):
    content, base, devtools = site_and_chrome
    run = subprocess.run(["node", str(SCRIPT), base, str(devtools), PASSWORD],
                         capture_output=True, text=True, timeout=180)
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout.strip().splitlines()[-1])

    # В предпросмотре щелчок по видео (заглушка, «смотреть») открывает окно выбора, как стенд в «Реализовано»
    assert out["preview_placeholder_opens_picker"] and out["preview_play_opens_picker"] and out["preview_no_player"]
    # Окно — как «Реализовано»: сверху выбранные, ниже карточки включённых видео, внизу «Готово» и «Отмена»
    assert out["dialog_open"] and out["raw_input_hidden"]
    assert out["catalog"] == [f"Отзыв {k}" for k in "ABCDEF"]                  # выключенный G не предлагается
    assert out["foot"] == ["Готово", "Отмена"]
    assert out["search"] == ["Отзыв B"]

    # Не больше трёх; порядок — ↑/↓; «убрать»
    assert out["after_select"] == ["Отзыв A", "Отзыв B", "Отзыв C"]
    assert out["count"] == "3 из 3" and out["full_card"] == ["Мест нет", "false"]
    assert out["after_up"] == ["Отзыв A", "Отзыв C", "Отзыв B"]
    assert out["after_down"] == ["Отзыв C", "Отзыв A", "Отзыв B"]
    assert out["after_remove"] == ["Отзыв C", "Отзыв B"]
    assert out["after_done"] == {"open": False, "ids": f"{IDS['c']},{IDS['b']}"}
    # «Отмена» — без изменений
    assert out["after_cancel"] == f"{IDS['c']},{IDS['b']}" and out["reopened"] == ["Отзыв C", "Отзыв B"]

    # Видеофайл добавлен из окна: предпросмотр и кадр-обложка до сохранения, окно открыто, выбор не сбит,
    # новое видео выбирается сразу
    assert out["local_preview"] and out["frame_cover"] and out["added"]
    assert out["added_card"] == ["Файл", True] and out["dialog_still_open"]
    assert out["after_add"] == ["Отзыв C", "Отзыв B"]
    assert out["after_pick_new"] == ["Отзыв C", "Отзыв B", "Новый файл"]

    # Черновик → предпросмотр; сайт меняется только после «Опубликовать»
    assert out["save_error"] == ""
    assert out["public_before"] == [] and out["public_after_save"] == []
    assert out["preview"] == ["Отзыв C", "Отзыв B", "Новый файл"]
    assert out["public_after_publish"] == ["Отзыв C", "Отзыв B", "Новый файл"]
    library = read(content, "videos")["videos"]
    new = library[-1]
    assert positions(content) == [IDS["c"], IDS["b"], new["id"]]
    assert new["file"].startswith("videos/files/") and new["cover"].startswith("videos/covers/")
    assert library[:-1] == LIBRARY["videos"]                                  # записи RuTube не тронуты
    assert (content / "media" / new["file"]).is_file()

    assert out["public_plays"]                                               # на сайте видео играет как раньше
    assert out["phone_fits"]
    assert out["errors"] == []
    assert reviews_index(content) >= 0
