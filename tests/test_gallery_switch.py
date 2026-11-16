"""Галерея стенда при переходе от одной карточки к другой.

Причина «дёргания»: при закрытии у фото снимался только src, а srcset оставался — окно следующего
стенда открывалось с фото предыдущего и подменяло его, когда новое загрузится. Теперь при закрытии
снимаются и src, и srcset, а пока новое фото грузится, оно скрыто (is-loading) и плавно проявляется
на тёмной подложке; место под фото зарезервировано (фиксированная высота), сетка не двигается.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAIN_JS = (ROOT / "app" / "static" / "js" / "main.js").read_text(encoding="utf-8")
CSS = (ROOT / "app" / "static" / "css" / "style.css").read_text(encoding="utf-8")


def gallery_js():
    start = MAIN_JS.index("function initGallery()")
    return MAIN_JS[start:MAIN_JS.index("function initRequest()", start)]


def test_closing_clears_both_src_and_srcset():
    close = gallery_js().split('dialog.addEventListener("close"', 1)[1].split("});", 1)[0]
    assert 'removeAttribute("srcset")' in close and 'removeAttribute("src")' in close


def test_new_photo_is_hidden_until_loaded():
    js = gallery_js()
    show = js.split("function show(i)", 1)[1].split("\n    }\n", 1)[0]
    assert 'classList.add("is-loading")' in show
    assert re.search(r'image\.addEventListener\("load"', js) and re.search(r'image\.addEventListener\("error"', js)
    # Уже загруженное (из кеша) фото не мигает — класс снимается сразу
    assert "image.complete" in show


def test_loading_state_is_opacity_only_and_respects_reduced_motion():
    rule = re.search(r"\.gallery-image\.is-loading \{([^}]*)\}", CSS).group(1)
    assert "opacity: 0" in rule and "height" not in rule and "width" not in rule
    base = re.search(r"\.gallery-image \{([^}]*)\}", CSS).group(1)
    assert "transition: opacity" in base and "height: 100%" in base          # размер задаёт сцена
    assert re.search(r"prefers-reduced-motion: reduce\)[^@]*\.gallery-image[^}]*transition: none", CSS, re.S)


def test_card_image_space_is_reserved():
    media = re.search(r"\.stand-media \{([^}]*)\}", CSS).group(1)
    assert "aspect-ratio: 4 / 3" in media


# --- Без чёрных полос: cover или целиком на размытой копии ---

import json
import shutil
import subprocess

import pytest

HARNESS = ROOT / "tests" / "gallery_harness.js"
BASE = (ROOT / "app" / "templates" / "base.html").read_text(encoding="utf-8")
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="нужен Node.js")


@needs_node
def test_photo_fit_by_crop_amount():
    out = json.loads(subprocess.run(["node", str(HARNESS), "fit"], capture_output=True, text=True, timeout=20,
                                    check=True).stdout)

    assert out["wide"] == {"fit": "cover", "loading": False, "backdrop": ""}
    assert out["duringLoad"] == {"fit": "", "backdrop": ""}                  # пока грузится — ни старой копии
    assert out["narrow"]["fit"] == "contain" and out["narrow"]["backdrop"] == "/media/b-1920.webp"
    assert out["panorama"]["fit"] == "contain"
    assert out["video"] == {"backdropHidden": True, "imageHidden": True}     # видео — без подложки
    assert out["back"] == {"backdropHidden": False}
    assert out["afterClose"] == ""


def test_stage_has_fixed_size_independent_of_photo():
    stage = re.search(r"\.gallery-stage \{([^}]*)\}", CSS).group(1)
    assert "aspect-ratio: 3 / 2" in stage and "max-height: min(62svh, 36rem)" in stage
    image = re.search(r"\.gallery-image \{([^}]*)\}", CSS).group(1)
    assert "position: absolute" in image and "object-fit: cover" in image and "inset: 0" in image
    assert re.search(r'\.gallery-stage\[data-fit="contain"\] \.gallery-image \{[^}]*object-fit: contain', CSS)
    for rule in (".gallery-player", ".gallery-video-error"):
        assert "position: absolute" in re.search(re.escape(rule) + r" \{([^}]*)\}", CSS).group(1)


def test_backdrop_is_blurred_copy_and_decorative():
    tag = re.search(r'<img class="gallery-backdrop"[^>]*>', BASE).group(0)
    assert 'alt=""' in tag and 'aria-hidden="true"' in tag and "data-gallery-backdrop" in tag
    assert BASE.index("gallery-backdrop") < BASE.index('class="gallery-image"')     # под фото
    rule = re.search(r"\.gallery-backdrop \{([^}]*)\}", CSS).group(1)
    assert "blur(" in rule and "object-fit: cover" in rule and "display: none" in rule
