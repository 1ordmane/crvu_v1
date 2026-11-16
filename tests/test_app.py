import re
import subprocess

import pytest
from PIL import Image

from app import create_app


@pytest.fixture
def client(make_content):
    # Тестовые данные (tests/fixtures/content), а не рабочий content/
    app = create_app({"TESTING": True, "CONTENT_DIR": str(make_content())})
    return app.test_client()


def test_index_returns_200(client):
    response = client.get("/")

    assert response.status_code == 200
    assert "ЦЕНТР рекламно-выставочных услуг" in response.get_data(as_text=True)


@pytest.mark.parametrize("path", ["/portfolio", "/about", "/contacts"])
def test_sheet_pages_open_their_sheet(client, path):
    response = client.get(path)
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    sheet_id = path.strip("/")
    assert re.search(rf'<section class="sheet is-open"[^>]*id="{sheet_id}"', html)


def test_unknown_page_returns_404(client):
    assert client.get("/admin").status_code == 404


def test_home_has_no_open_sheet(client):
    html = client.get("/").get_data(as_text=True)

    assert "sheet is-open" not in html


def test_page_has_no_inline_base64_assets(client):
    html = client.get("/").get_data(as_text=True)

    assert "base64," not in html


def test_home_keeps_configurator_without_process_section(client):
    html = client.get("/").get_data(as_text=True)

    # Блока «Полный цикл застройки» больше нет — ни разметки, ни его стилей
    assert 'id="services"' not in html and "Полный цикл застройки" not in html and "cycle-step" not in html
    assert 'id="configurator"' in html
    for stand_type in ["Линейный", "Угловой", "Полуостров", "Остров"]:
        assert stand_type in html


def test_counters_do_not_target_service_steps(client):
    # Раньше счётчик выбирал все [data-n] и стирал текст карточек услуг.
    html = client.get("/").get_data(as_text=True)

    assert "data-n=" not in html
    # Счётчики — только у трёх главных цифр «О нас»; четвёртая — мелкой строкой, без анимации
    assert html.count("data-count=") == 3


def test_stand_frames_exist(client):
    response = client.get("/static/img/stand/frame-159.webp")

    assert response.status_code == 200
    assert response.mimetype == "image/webp"


def test_hidden_stand_is_not_rendered(client):
    html = client.get("/portfolio").get_data(as_text=True)

    assert "арара" not in html
    assert "project-00-0.jpg" not in html


def test_contact_buttons_open_request_dialog(client):
    html = client.get("/").get_data(as_text=True)

    assert html.count('data-open-request') >= 3
    assert re.search(r'<dialog[^>]*id="request"', html)
    assert re.search(r'<label[^>]*>\s*Ваше имя', html)


# Контент из временной копии папки content/

import html as html_lib
import json
import shutil
from datetime import date
from pathlib import Path


@pytest.fixture
def content_dir(make_content):
    return make_content()


def make_client(content_dir):
    return create_app({"TESTING": True, "CONTENT_DIR": str(content_dir)}).test_client()


def edit(content_dir, name, change):
    path = content_dir / f"{name}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_security_headers(client):
    response = client.get("/")

    csp = response.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"


def test_html_has_no_inline_styles_or_scripts(client):
    # Иначе их заблокирует Content-Security-Policy.
    for path in ["/", "/portfolio", "/about", "/contacts"]:
        html = client.get(path).get_data(as_text=True)
        assert " style=" not in html
        assert "<style" not in html
        assert not re.search(r"<script(?![^>]*\bsrc=)", html)
        assert not re.search(r"<[^>]*\son[a-z]+=", html)


def test_secret_key_comes_only_from_environment(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    assert create_app().config["SECRET_KEY"] is None

    monkeypatch.setenv("SECRET_KEY", "from-env")
    assert create_app().config["SECRET_KEY"] == "from-env"


def test_debug_is_off_by_default(monkeypatch):
    monkeypatch.delenv("FLASK_DEBUG", raising=False)

    app = create_app()

    assert not app.debug
    assert app.config["SESSION_COOKIE_HTTPONLY"]
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"


def test_media_hides_draft_photos_and_serves_published(content_dir):
    client = make_client(content_dir)
    assert client.get("/media/portfolio/test-arara/01.jpg").status_code == 404

    edit(content_dir, "portfolio", lambda d: d["projects"][0].update(status="published"))
    response = client.get("/media/portfolio/test-arara/01.jpg")

    assert response.status_code == 200
    assert response.mimetype == "image/jpeg"


@pytest.mark.parametrize("path", [
    "/media/portfolio/test-arara/01.jpg",
    "/media/Portfolio/test-arara/01.jpg",
    "/media/PORTFOLIO/test-arara/01.jpg",
    "/media/pOrTfOlIo/test-arara/01.jpg",
    "/media/portfolio/TEST-ARARA/01.jpg",
    "/media/portfolio/test-arara/01.JPG",
])
def test_draft_photos_are_hidden_in_any_letter_case(content_dir, path):
    # Файловая система macOS не различает регистр: Portfolio/… находит тот же файл,
    # поэтому проверка публикации не должна зависеть от регистра в адресе.
    assert make_client(content_dir).get(path).status_code == 404


def test_published_project_photos_are_served_to_visitors(content_dir):
    response = make_client(content_dir).get("/media/portfolio/example-2026-1/01.webp")

    assert response.status_code == 200
    assert response.mimetype == "image/webp"


def test_published_project_without_existing_photos_is_hidden(content_dir):
    project = {"id": "lost-photos", "status": "published", "title": "Фото потерялись", "year": 2025,
               "photos": ["portfolio/lost-photos/01.jpg", "static/img/stand/нет.webp"]}
    edit(content_dir, "portfolio", lambda d: d["projects"].append(project))

    html = make_client(content_dir).get("/portfolio").get_data(as_text=True)

    assert "Фото потерялись" not in html


@pytest.mark.parametrize(
    "path",
    ["/media/../site.json", "/media/%2e%2e/site.json", "/media/portfolio.json", "/media/portfolio/nope.jpg", "/media/..%2fapp/__init__.py"],
)
def test_media_does_not_leak_files(client, path):
    assert client.get(path).status_code == 404


def test_draft_project_is_hidden_until_published(content_dir):
    client = make_client(content_dir)
    assert "test-arara" not in client.get("/portfolio").get_data(as_text=True)

    edit(content_dir, "portfolio", lambda d: d["projects"][0].update(status="published", title="Новый проект"))
    html = client.get("/portfolio").get_data(as_text=True)

    assert "Новый проект" in html
    assert "/media/portfolio/test-arara/01.jpg" in html


def test_project_photos_from_media_folder(content_dir):
    folder = content_dir / "media" / "portfolio" / "new-stand"
    folder.mkdir(parents=True)
    shutil.copy(content_dir / "media" / "portfolio" / "test-arara" / "01.jpg", folder / "01.jpg")
    project = {"id": "new-stand", "status": "published", "title": "Стенд с фото", "year": 2025,
               "category": "Выставочный стенд", "description": "Описание <b>проекта</b>",
               "specs": [{"label": "Площадь", "value": "40 м²"}], "photos": []}
    edit(content_dir, "portfolio", lambda d: d["projects"].append(project))

    html = make_client(content_dir).get("/portfolio").get_data(as_text=True)

    assert "Стенд с фото" in html
    assert "/media/portfolio/new-stand/01.jpg" in html
    assert "Выставочный стенд, 2025" in html
    assert "<b>проекта</b>" not in html


def year_panels(html):
    """Разметка вкладки каждого года: {"2026": "…"} — от начала панели до следующей."""
    parts = html.split('<div class="year-panel"')[1:]
    return {re.search(r'id="year-(\d+)"', part).group(1): part for part in parts}


def test_portfolio_shows_only_years_with_published_stands(content_dir):
    photo = ["static/img/stand/frame-159.webp"]

    def scenario(data):
        data["projects"] = [
            {"id": "live", "status": "published", "title": "Опубликованный стенд", "year": 2026, "photos": photo},
            {"id": "live-2025", "status": "published", "title": "Стенд 2025", "year": 2025, "photos": photo},
            {"id": "draft", "status": "draft", "title": "Черновик стенда", "year": 2024, "photos": photo},
            {"id": "unpublished", "status": "draft", "title": "Снятый стенд", "year": 2023, "photos": photo},
        ]
    edit(content_dir, "portfolio", scenario)
    edit(content_dir, "pages", lambda d: next(p for p in d["pages"] if p["type"] == "portfolio").update(first_year=2022))
    html = make_client(content_dir).get("/portfolio").get_data(as_text=True)
    panels = year_panels(html)

    # 2024 — только черновик, 2023 — снятый с публикации, 2022 — пусто: ни вкладки, ни панели
    assert re.findall(r'id="tab-(\d+)"', html) == ["2026", "2025"]
    assert list(panels) == ["2026", "2025"]
    assert "Опубликованный стенд" in panels["2026"] and "Стенд 2025" in panels["2025"]
    assert "проекты скоро появятся" not in html
    assert "Черновик стенда" not in html and "Снятый стенд" not in html


def test_new_text_page_gets_its_own_url_and_menu_item(content_dir):
    page = {"slug": "faq", "type": "text", "title": "Вопросы", "nav": "FAQ", "paragraphs": ["Ответ <script>"]}
    edit(content_dir, "pages", lambda d: d["pages"].insert(1, page))
    client = make_client(content_dir)

    html = client.get("/faq").get_data(as_text=True)

    assert re.search(r'<section class="sheet is-open"[^>]*id="faq"', html)
    assert "Ответ &lt;script&gt;" in html
    nav = re.search(r'<nav class="site-nav".*?</nav>', html, re.S).group(0)
    assert re.findall(r'data-route="(\w+)"', nav) == ["home", "portfolio", "faq", "about", "contacts"]


def test_hidden_page_returns_404_and_leaves_menu(content_dir):
    edit(content_dir, "pages", lambda d: d["pages"][1].update(visible=False))
    client = make_client(content_dir)

    assert client.get("/about").status_code == 404
    assert 'data-route="about"' not in client.get("/").get_data(as_text=True)


def test_home_sections_follow_content_order_and_visibility(content_dir):
    def change(d):
        d["sections"].reverse()
        d["sections"].append({"type": "text", "id": "approach", "title": "Наш подход", "paragraphs": ["Текст"]})
    edit(content_dir, "home", change)
    html = make_client(content_dir).get("/").get_data(as_text=True)

    assert html.index('id="configurator"') < html.index('id="projects"') < html.index('id="approach"')

    edit(content_dir, "home", lambda d: d["sections"][0].update(visible=False))
    html = make_client(content_dir).get("/").get_data(as_text=True)
    assert 'id="configurator"' not in html
    assert 'href="#configurator"' not in html


def test_configurator_types_carry_plan_data(client):
    html = client.get("/").get_data(as_text=True)

    assert 'value="Угловой" data-walls="back left"' in html
    assert 'value="Остров" data-walls=""' in html and "data-deck" not in html


def test_configurator_has_length_and_width_instead_of_area_slider(client):
    html = client.get("/").get_data(as_text=True)

    assert 'type="range"' not in html and 'name="area"' not in html
    # Площадь по умолчанию 36 м² → 6 × 6 м; пределы площади (1…300 м²) проверяет stand-plan.js.
    # Текстовое поле с десятичной клавиатурой: type="number" не принимает «2,5» с запятой.
    for side in ("length", "width"):
        assert re.search(rf'id="cfg-{side}" name="{side}" inputmode="decimal"[^>]*value="6"', html)
    assert 'type="number" id="cfg-' not in html
    assert 'data-area-min="1" data-area-max="300"' in html
    assert ">36 м²</output>" in html
    for side in ("length", "width"):
        assert f'data-step="-1" data-side="{side}"' in html and f'data-step="1" data-side="{side}"' in html


def test_configurator_size_follows_area_rule_from_content(content_dir):
    edit(content_dir, "home", lambda d: d["sections"][1]["area"].update(min=20, max=120, default=64))
    html = make_client(content_dir).get("/").get_data(as_text=True)

    assert re.search(r'name="length"[^>]*value="8"', html)
    assert 'data-area-min="20" data-area-max="120"' in html
    assert ">64 м²</output>" in html


def test_configurator_has_hint_for_too_small_size(client):
    html = client.get("/").get_data(as_text=True)

    # Подсказку показывает main.js; поля ссылаются на неё через aria-describedby
    assert re.search(r'<p class="cfg-hint" id="cfg-size-hint" aria-live="polite" data-size-hint></p>', html)
    assert html.count('aria-describedby="cfg-size-hint"') == 2
    texts = json.loads(html_lib.unescape(re.search(r"data-texts='([^']*)'", html).group(1)))
    assert texts["min_hint"] == "Минимальный размер — 1 м"


def test_plan_shows_grid_scale(client):
    html = client.get("/").get_data(as_text=True)

    assert re.search(r'<text class="plan-scale"[^>]*>1 клетка = 1 × 1 м</text>', html)


def test_plan_module_loads_before_main_script(client):
    html = client.get("/").get_data(as_text=True)

    assert html.index("js/stand-plan.js") < html.index("js/main.js")
    assert client.get("/static/js/stand-plan.js").status_code == 200


def test_configurator_reads_length_field_by_name_not_collection_size():
    # form.elements.length — число элементов формы, а не поле name="length":
    # с ним конструктор падал при запуске и план не рисовался.
    js = (Path(__file__).resolve().parent.parent / "app" / "static" / "js" / "main.js").read_text(encoding="utf-8")

    assert "elements.length" not in js
    assert 'namedItem("length")' in js


def test_footer_without_copy_key_shows_default_line(content_dir):
    # В Jinja obj["copy"] без такого ключа даёт метод dict.copy, а не пустое значение
    edit(content_dir, "site", lambda d: d.setdefault("footer", {}).pop("copy", None))
    html = make_client(content_dir).get("/").get_data(as_text=True)
    line = re.search(r'<p class="footer-copy">(.*?)</p>', html).group(1)

    assert "built-in" not in line
    assert line.startswith(f"© {date.today().year} ")


def test_request_form_says_sending_is_not_connected(client):
    html = client.get("/").get_data(as_text=True)

    assert 'data-email=""' in html
    assert "Онлайн-отправка заявок пока не подключена." in html
    assert "почтовая программа" not in html


def test_request_form_uses_configured_email(content_dir):
    edit(content_dir, "site", lambda d: d["request"].update(email="info@example.com"))

    html = make_client(content_dir).get("/").get_data(as_text=True)

    assert 'data-email="info@example.com"' in html
    assert "После отправки откроется ваша почтовая программа" in html


def test_filled_contacts_become_links(content_dir):
    def change(d):
        d["contacts"][0]["value"] = "+7 900 000-00-00"
        d["contacts"][1]["value"] = "info@example.com"
        d["contacts"][2]["value"] = "Москва"
    edit(content_dir, "contacts", change)

    html = make_client(content_dir).get("/contacts").get_data(as_text=True)

    assert 'href="tel:+79000000000"' in html
    assert 'href="mailto:info@example.com"' in html
    assert "<dd>Москва</dd>" in html
    assert "Мессенджеры" not in html


def test_broken_content_gives_server_error_not_partial_page(content_dir):
    (content_dir / "home.json").write_text("{", encoding="utf-8")
    app = create_app({"TESTING": False, "CONTENT_DIR": str(content_dir)})

    assert app.test_client().get("/").status_code == 500


# Сцена сборки стенда — фон всей главной, а не отдельный прокручиваемый блок

STYLE = Path(__file__).resolve().parent.parent / "app" / "static" / "css" / "style.css"


def css_rule(css, selector):
    match = re.search(rf"^{re.escape(selector)} \{{(.*?)^\}}", css, re.S | re.M)
    return match.group(1) if match else ""


def test_stand_scene_is_background_layer_outside_hero(client):
    html = client.get("/").get_data(as_text=True)

    assert html.index("data-scene") < html.index("data-scene-canvas") < html.index('<main id="main"')
    hero = re.search(r'<section class="hero".*?</section>', html, re.S).group(0)
    assert "<canvas" not in hero and "<video" not in hero


def test_stand_scene_is_fixed_and_hero_is_not_a_scroll_spacer(client):
    css = STYLE.read_text(encoding="utf-8")
    html = client.get("/").get_data(as_text=True)

    scene = css_rule(css, ".scene")
    assert "position: fixed" in scene
    assert "z-index: 0" in scene
    # Первый экран — обычный блок: страница прокручивается сразу, сцену не нужно «проходить»
    assert "--hero-pin" not in css and "--scene-runway" not in css and ".hero-stage" not in css
    hero = css_rule(css, ".hero")
    assert "position: sticky" not in hero and "min-height: 82svh;" in hero
    assert "data-scene-runway" not in html and "hero-stage" not in html

def test_scene_uses_frames_cut_from_the_video_for_desktop_and_phone(client):
    html = client.get("/").get_data(as_text=True)
    static = STYLE.parents[1]
    scene = re.search(r'<div class="scene"[^>]*>', html).group(0)
    count = int(re.search(r'data-frame-count="(\d+)"', scene).group(1))
    desktop = re.search(r'data-frames-base="/static/([^"]+)"', scene).group(1)
    mobile = re.search(r'data-frames-base-mobile="/static/([^"]+)"', scene).group(1)
    # Кадры из video.mp4: от пустого зала до готового стенда, у телефона — свой облегчённый набор;
    # пропорция — как у петли pre-video.mp4 (2186 × 1080)
    assert (desktop, mobile) == ("img/stand-video/1280/", "img/stand-video/960/")
    for base, size in ((desktop, (1280, 632)), (mobile, (960, 474))):
        files = sorted((static / base).glob("frame-*.webp"))
        assert len(files) == count == 128, base
        assert files[0].name == "frame-000.webp" and files[-1].name == "frame-127.webp"
        with Image.open(files[0]) as first, Image.open(files[-1]) as last:
            assert first.size == last.size == size
    assert "data-scene-light" in scene and 'width="1280" height="632"' in html
    # Видео video.mp4 и 1020.mp4 целиком страница не загружает, прежние кадры остаются на диске для отката;
    # единственное видео — петля первого экрана (test_scene_loop.py)
    assert "1020.mp4" not in html and "/video.mp4" not in html
    assert html.count("<video") == 1 and "data-scene-loop" in html
    assert len(list((static / "img" / "stand").glob("frame-*.webp"))) == 160
    assert len(list((static / "img" / "stand-1020" / "1280").glob("frame-*.webp"))) == 128

    js = SCRIPT.read_text(encoding="utf-8")
    assert 'scene.dataset.framesBaseMobile' in js and '"(max-width: 859px)"' in js
    # Холст подстраивается под размер кадра (у наборов он разный)
    assert "canvas.width = img.naturalWidth;" in js

def test_assembly_gauge_is_thin_line_above_content_not_inside_scene(client):
    html = client.get("/").get_data(as_text=True)
    scene = re.search(r'<div class="scene".*?</video>\s*</div>', html, re.S)

    # Шкала внутри фонового слоя (z-index 0) оказывалась под контентом и текст проезжал сквозь неё
    assert scene and "data-gauge" not in scene.group(0)
    assert html.index("data-gauge") < html.index('<main id="main"')
    # Без подписи и подсказки: только линия прогресса
    assert "data-gauge-label" not in html and "data-gauge-hint" not in html
    gauge = re.search(r'<div class="gauge".*?</div>\s*</div>', html, re.S).group(0)
    assert not re.sub(r"<[^>]+>", "", gauge).strip()  # ни подписи «Монтаж», ни другого текста
    assert "Листайте" not in html

    css = STYLE.read_text(encoding="utf-8")
    gauge = css_rule(css, ".gauge")
    assert "position: fixed" in gauge
    assert "z-index: 40" in gauge
    assert "pointer-events: none" in gauge
    assert "height: var(--gauge-h)" in gauge
    assert re.search(r"--gauge-h: [23]px;", css)
    # Никаких капсул: ни стекла, ни теней, ни рамок
    for prop in ("backdrop-filter", "box-shadow", "border:", "border-radius"):
        assert prop not in gauge
        assert prop not in css_rule(css, ".gauge-fill")


SCRIPT = STYLE.parent.parent / "js" / "main.js"


def media_block(css, query):
    """Тело блока @media с точным условием query (без вложенных @media)."""
    start = css.index("@media " + query + " {")
    depth, i = 0, css.index("{", start)
    for j in range(i, len(css)):
        depth += {"{": 1, "}": -1}.get(css[j], 0)
        if depth == 0:
            return css[i + 1:j]
    return ""


def test_scene_keeps_natural_frames_without_global_veil(client):
    html = client.get("/").get_data(as_text=True)
    css = STYLE.read_text(encoding="utf-8")

    # Кадры показываются как есть: без осветления фильтром и без белой вуали на весь экран
    assert "filter" not in css_rule(css, ".scene-canvas")
    assert "scene-shade" not in html and ".scene-shade" not in css
    assert "var(--paper)" not in css_rule(css, ".scene")
    # Читаемость — локально: светлый текст на тёмном зале и затемнение только за первым экраном
    assert "color: var(--on-scene)" in css_rule(css, ".hero")
    assert "color: var(--on-scene)" in css_rule(css, ".section")
    # Форма конфигуратора — на дымчатом стекле (этапы теперь на белой «бумаге», см. test_steps.py)
    assert "color: var(--on-scene)" in css_rule(css, ".cfg-form")
    assert "var(--veil)" not in css_rule(css, ".site-footer")



def test_reveal_marks_headings_text_and_cards(client):
    html = client.get("/").get_data(as_text=True)
    main = re.search(r'<main id="main">.*?</main>', html, re.S).group(0)

    assert re.search(r'<h2 class="section-title"[^>]*data-reveal', main)
    assert re.search(r'<p class="section-lead"[^>]*data-reveal', main)
    assert re.search(r'<figure class="cfg-plan"[^>]*data-reveal', main)
    assert re.search(r'<form class="cfg-form"[^>]*data-reveal', main)
    # Первый экран виден сразу — его не прячет скрипт
    hero = re.search(r'<section class="hero".*?</section>', main, re.S).group(0)
    assert "data-reveal" not in hero


def test_reveal_never_hides_text_without_script_or_with_reduced_motion():
    css = STYLE.read_text(encoding="utf-8")
    motion = media_block(css, "(prefers-reduced-motion: no-preference)")

    # Скрытое состояние — только после того, как скрипт включил анимацию (класс на <html>),
    # и только если пользователь не просил уменьшить движение
    hidden = re.findall(r"^([^{}\n]*\[data-reveal\][^{}\n]*)\{", css, re.M)
    assert hidden, "нет правил для [data-reveal]"
    assert all(".reveal-on" in selector for selector in hidden)
    assert "opacity: 0" in motion and ".reveal-on [data-reveal]" in motion
    outside = css.replace(motion, "")
    assert not re.search(r"\[data-reveal\][^{]*\{[^}]*opacity: 0", outside)

    js = SCRIPT.read_text(encoding="utf-8")
    assert 'classList.add("reveal-on")' in js


def test_hero_and_portfolio_cards_animate_only_with_motion_allowed():
    css = STYLE.read_text(encoding="utf-8")
    motion = media_block(css, "(prefers-reduced-motion: no-preference)")

    # Первый экран проявляется чистым CSS: без JS и при ошибке скрипта текст всё равно виден
    assert "@keyframes rise" in css
    assert ".hero-title" in motion and "animation: rise" in motion
    # Карточки портфолио идут друг за другом, когда шторка открыта
    assert ".sheet.is-open .stand-card" in motion


def test_hero_is_a_clean_full_bleed_video_without_overlays(client):
    html = client.get("/").get_data(as_text=True)
    css = STYLE.read_text(encoding="utf-8")
    motion = media_block(css, "(prefers-reduced-motion: no-preference)")

    # Клетки на первом экране нет: ни псевдоэлемента, ни токенов, ни анимации её появления
    hero = re.search(r'<section class="hero".*?</section>', html, re.S).group(0)
    assert hero.count("<h1") == 1 and "hero-grid" not in hero
    assert ".hero::before" not in css and "--hero-grid" not in css and "grid-in" not in css
    # Ни графитовой подложки, ни других слоёв поверх видео
    assert ".hero::after" not in css and "--hero-scrim" not in css and "--graphite" not in css
    assert "background" not in css_rule(css, ".hero")
    # Видео и кадры — во весь экран, прижаты вправо (стенд справа, как в кадре), без растушёвки краёв;
    # высота не больше 91,5vw — на узком экране стенд не обрезается
    for rule in (css_rule(css, ".scene-loop"), css_rule(css, "body:has([data-scene-light]) .scene-canvas")):
        assert "top: 0;" in rule and "right: 0;" in rule and "width: 100%;" in rule
        assert "height: min(100%, 91.5vw);" in rule and "object-position: 100% 50%;" in rule
    assert "mask-image: none;" in css_rule(css, "body:has([data-scene-light]) .scene-canvas")
    wide = media_block(css, "(min-width: 1024px) and (min-aspect-ratio: 4/3)")
    assert "scene-loop" not in wide and "data-scene-light" not in wide
    # Портретный экран: полоса во всю ширину под шапкой, стенд целиком; текст под ней, на полу
    portrait = media_block(css, "(max-aspect-ratio: 4/5)")
    assert "padding-top: calc(var(--header-h) + 84vw);" in portrait
    assert "height: 91.5vw;" in portrait and "linear-gradient(#000 88%, transparent)" in portrait
    # Текст тёмный на светлом зале, держится светлым ореолом по контуру, а не затемнением
    for token in ("--on-scene: var(--ink);", "--on-scene-soft: var(--ink-soft);", "--hero-halo:"):
        assert token in css_rule(css, ".hero"), token
    assert "text-shadow: var(--hero-halo);" in css_rule(css, ".hero-copy")
    assert "background: rgb(255 255 255 / 0.7);" in css_rule(css, ".hero .btn-secondary")
    # Заголовок выровнен по строкам, тире не начинает строку
    assert "text-wrap: balance;" in css_rule(css, ".hero-title")
    assert "Выставочные стенды под ключ\u00a0— под ваш бюджет" in hero
    assert "var(--hero-dim-line)" in css_rule(css, ".hero-lead::before")
    # Появление — только если движение разрешено
    assert ".hero-lead::before" in motion and "animation: measure" in motion

    js = SCRIPT.read_text(encoding="utf-8")
    assert 'hero.style.setProperty("--hero-exit"' in js


def test_scroll_cue_is_decorative_desktop_only_and_stops_moving(client):
    html = client.get("/").get_data(as_text=True)
    css = STYLE.read_text(encoding="utf-8")
    motion = media_block(css, "(prefers-reduced-motion: no-preference)")

    hero = re.search(r'<section class="hero".*?</section>', html, re.S).group(0)
    cue = re.search(r'<span class="hero-scroll"[^>]*>', hero)
    # Только оформление: скрыто от экранного диктора и не перехватывает клики
    assert cue and 'aria-hidden="true"' in cue.group(0)
    assert "display: none" in css_rule(css, ".hero-scroll")
    desktop = nested_rule(css, ".hero-scroll")  # правило внутри @media (min-width: 860px)
    assert "display: block" in desktop
    assert "pointer-events: none" in desktop and "var(--hero-exit, 0)" in desktop
    # Покачивание конечное (не infinite) и только при разрешённом движении
    assert re.search(r"animation: cue-nudge [^;]* 3;", motion)
    assert "cue-nudge" not in css.replace(motion, "").replace("@keyframes cue-nudge", "")
    assert "infinite" not in nested_rule(motion, ".hero-scroll svg")


def nested_rule(block, selector):
    match = re.search(rf"^\s+{re.escape(selector)} \{{(.*?)^\s+\}}", block, re.S | re.M)
    return match.group(1) if match else ""


def test_portfolio_grid_has_two_columns_on_phones_and_keeps_desktop_grid():
    css = STYLE.read_text(encoding="utf-8")
    grid = css_rule(css, ".stand-grid")

    # До 860 px — ровно две равные колонки; minmax(0, …) не даёт длинному тексту раздвинуть колонку
    assert "grid-template-columns: repeat(2, minmax(0, 1fr));" in grid
    assert "gap: var(--space-4);" in grid
    # С 860 px — прежняя сетка десктопа
    desktop = [rule for query in ("(min-width: 640px)", "(min-width: 860px)")
               for rule in [nested_rule(block, ".stand-grid")
                            for block in re.findall(rf"@media {re.escape(query)} \{{\n(.*?)\n\}}", css, re.S)] if rule]
    assert any("grid-template-columns: repeat(auto-fill, minmax(min(100%, 17rem), 1fr));" in r for r in desktop)
    assert any("gap: var(--space-5);" in r for r in desktop)
    # В узкой карточке подписи переносятся, а бейджи с числом фото не вылезают за фото
    assert "overflow-wrap: break-word;" in css_rule(css, ".stand-open")
    badges = css_rule(css, ".stand-badges")
    assert "flex-wrap: wrap;" in badges and "right: var(--space-2);" in badges
    # Фото заполняет карточку без искажения и со скруглением
    assert "object-fit: cover;" in css_rule(css, ".stand-media img")
    # общий токен скругления фото проектов — тот же, что у карточек на главной
    assert "border-radius: var(--radius-media);" in css_rule(css, ".stand-media")


def test_portfolio_mobile_titles_and_badges_are_compact_and_desktop_keeps_sizes():
    css = STYLE.read_text(encoding="utf-8")
    root = css_rule(css, ":root")
    tablet = "\n".join(re.findall(r"@media \(min-width: 640px\) \{\n(.*?)\n\}", css, re.S))

    # Заголовок выставки на телефоне чуть меньше (18 px), счётчик идёт сразу за текстом
    assert "--fs-group-title: 1.125rem;" in root
    title = css_rule(css, ".exhibition-title")
    assert "font-size: var(--fs-group-title);" in title and "display: flex" not in title
    count = css_rule(css, ".exhibition-count")
    assert "margin-left: var(--space-2);" in count and "white-space: nowrap;" in count
    # С 640 px — прежние флекс-строка и размер var(--fs-md)
    wide_title = nested_rule(tablet, ".exhibition-title")
    assert "display: flex;" in wide_title and "font-size: var(--fs-md);" in wide_title
    assert "margin-left: 0;" in nested_rule(tablet, ".exhibition-count")

    # Бейджи: «Пример» слева, число фото (всегда последний бейдж) — справа, без выхода за фото
    assert "margin-left: auto;" in css_rule(css, ".stand-badges .badge:last-child")
    assert "max-width: 100%;" in css_rule(css, ".badge")
    assert "margin-left: 0;" in nested_rule(tablet, ".stand-badges .badge:last-child")

    # Название проекта на телефоне — 14 px, без обрезки многоточием; с 640 px — прежний var(--fs-base)
    assert "--fs-card-title: 0.875rem;" in root
    card_title = css_rule(css, ".stand-title")
    assert "font-size: var(--fs-card-title);" in card_title and "ellipsis" not in card_title
    assert "font-size: var(--fs-base);" in nested_rule(tablet, ".stand-title")


def test_gauge_fill_moves_by_transform_and_follows_scroll():
    css = STYLE.read_text(encoding="utf-8")
    fill = css_rule(css, ".gauge-fill")
    # Ширина пересчитывает раскладку, сдвиг — плавный и дешёвый
    assert "width: calc(var(--progress)" not in fill
    assert "translateX(calc((var(--progress) - 1) * 100%))" in fill
    assert "transition" not in fill
    assert "overflow: hidden" in css_rule(css, ".gauge-track")
    assert "--gauge-glass" not in css and ".gauge-fill::after" not in css
    js = SCRIPT.read_text(encoding="utf-8")
    # Линия — по реальной прокрутке, без отставания за догоняющим кадром
    assert "updateGauge(p)" in js
    assert 'track.style.setProperty("--progress", p' in js
    assert "data-gauge-label" not in js and "data-gauge-hint" not in js


def test_settings_are_read_from_local_env_file(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("# комментарий\nSECRET_KEY='file-secret'\nADMIN_USERNAME=admin\n"
                        "ADMIN_PASSWORD_HASH='scrypt:32768:8:1$salt$hash'\n", encoding="utf-8")
    monkeypatch.setenv("CRVU_ENV_FILE", str(env_file))

    app = create_app({"TESTING": True})

    assert app.config["SECRET_KEY"] == "file-secret"
    assert app.config["ADMIN_USERNAME"] == "admin"
    assert app.config["ADMIN_PASSWORD_HASH"] == "scrypt:32768:8:1$salt$hash"
    assert app.test_client().get("/admin/login").status_code == 200


def test_environment_variables_override_env_file(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("SECRET_KEY='file-secret'\n", encoding="utf-8")
    monkeypatch.setenv("CRVU_ENV_FILE", str(env_file))
    monkeypatch.setenv("SECRET_KEY", "from-env")

    assert create_app().config["SECRET_KEY"] == "from-env"


def test_missing_env_file_keeps_admin_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("CRVU_ENV_FILE", str(tmp_path / "absent.env"))

    app = create_app({"TESTING": True})

    assert app.config["SECRET_KEY"] is None
    assert app.test_client().get("/admin/login").status_code == 404


# --- Главная: блок «Реализованные проекты», первый экран, подвал ---

def projects_section(d):
    return next(s for s in d["sections"] if s["type"] == "projects")


def without_request_button(d):
    # Проверяется переход второй кнопки к блоку или разделу — вариант «открыть заявку» выключен
    d["hero"].pop("secondary_action", None)


def test_placeholder_project_cards_are_off_by_default_and_hero_falls_back_to_portfolio(content_dir):
    edit(content_dir, "home", lambda d: projects_section(d).update(items=[{"title": ""}] * 3) or without_request_button(d))

    html = make_client(content_dir).get("/").get_data(as_text=True)

    # Без фото карточек нет — блок не показан, и вторая кнопка ведёт в раздел портфолио, а не к пустому якорю
    assert 'id="projects"' not in html and "Временная заглушка" not in html
    assert 'href="#projects"' not in html
    assert re.search(r'<a class="btn btn-secondary btn-lg" href="/portfolio"', html)


def test_placeholder_cards_are_shown_only_in_preview_mode(content_dir):
    edit(content_dir, "home", lambda d: projects_section(d).update(items=[{"title": ""}] * 3) or without_request_button(d))
    client = create_app({"TESTING": True, "CONTENT_DIR": str(content_dir), "SHOW_PLACEHOLDERS": True}).test_client()

    html = client.get("/").get_data(as_text=True)
    block = re.search(r'<section class="section projects[^"]*".*?</section>', html, re.S).group(0)

    assert block.count('class="project-card is-placeholder"') == 3
    assert block.count("Временная заглушка") == 3 and "<img" not in block
    # «Подробнее» и «Смотреть все проекты» ведут в существующий раздел портфолио
    assert block.count('href="/portfolio"') == 4
    assert 'href="#projects"' in html


def test_home_shows_selected_projects_in_saved_order_whatever_their_dates(content_dir):
    # Прежнее поле latest («последние N по дате») больше не действует: только выбор администратора
    order = ["example-2024-1", "example-2026-3", "test-arara", "example-2025-1", "example-2026-1"]
    edit(content_dir, "home", lambda d: projects_section(d).update(latest=2, items=[{"project": pid} for pid in order]))
    portfolio = {p["id"]: p for p in json.loads((content_dir / "portfolio.json").read_text(encoding="utf-8"))["projects"]}

    html = make_client(content_dir).get("/").get_data(as_text=True)
    block = re.search(r'<section class="section projects[^"]*".*?</section>', html, re.S).group(0)

    titles = re.findall(r'<span class="project-title">([^<]*)</span>', block)
    # Старый проект 2024 года — первым, как выбрано; черновик test-arara на главной не виден
    assert titles == [portfolio[pid]["title"] for pid in order if pid != "test-arara"]
    assert block.count('class="project-card"') == 4


def test_linked_project_card_opens_its_gallery(content_dir):
    edit(content_dir, "home", lambda d: projects_section(d).update(items=[{"project": "example-2026-1"}]))

    html = make_client(content_dir).get("/").get_data(as_text=True)
    block = re.search(r'<section class="section projects[^"]*".*?</section>', html, re.S).group(0)

    assert block.count('class="project-card"') == 1
    assert 'loading="lazy"' in block and "data-gallery=" in block
    assert block.index("project-grid") < block.index("data-gallery")


def test_home_texts_and_final_call_to_action(content_dir):
    html = make_client(content_dir).get("/").get_data(as_text=True)

    assert '<ul class="hero-facts">' in html and html.count("<li>", html.index("hero-facts")) >= 3
    footer = re.search(r'<footer class="site-footer">.*?</footer>', html, re.S).group(0)
    assert '<p class="footer-lead"' in footer
    # В финальном блоке одна кнопка, и она открывает форму заявки
    assert footer.count("<button") == 1 and "data-open-request" in footer


def test_liquid_glass_only_on_header_and_menu_sheets_and_windows_are_solid():
    css = STYLE.read_text(encoding="utf-8")

    # Стекло — только у шапки и меню: они лежат поверх сцены первого экрана
    group = re.search(r"^\.site-header,\n\.site-nav \{(.*?)^\}", css, re.S | re.M)
    assert group, "нет общего правила жидкого стекла"
    for token in ("var(--lg-edge)", "var(--lg-sheen)", "var(--lg-shadow)", "var(--lg-blur)", "var(--lg-alpha"):
        assert token in group.group(1)
    # Шторки разделов и окна (галерея, заявка) — сплошная бумага, без размытия
    solid = css_rule(css, ".sheet,\n.modal")
    assert "background: var(--paper)" in solid and "backdrop-filter" not in solid
    assert "backdrop-filter" not in css_rule(css, ".sheet-bar")
    # Без backdrop-filter заливка шапки уплотняется
    fallback = re.search(r"@supports not \(\(backdrop-filter: blur\(1px\)\).*?\{(.*?)\n\}", css, re.S).group(1)
    assert "--lg-boost: 0.5" in fallback
    # Сцена сборки и первый экран без стекла: кадры видны как есть
    for selector in (".scene", ".scene-canvas", ".hero"):
        assert "backdrop-filter" not in css_rule(css, selector)
    assert "@keyframes sheen" not in css and "lg-sheen" not in media_block(css, "(prefers-reduced-motion: no-preference)")


def test_projects_paper_is_sheer_so_the_stand_shows_through_but_content_stays_opaque():
    css = STYLE.read_text(encoding="utf-8")

    # Полупрозрачна только подложка «Реализовано»: тон бумаги с альфой и лёгкое размытие сцены
    sheer = css_rule(css, ".section.projects.is-paper,\n.section.reviews.is-paper,\n.section.configurator.is-paper")
    assert "background: rgb(var(--paper-rgb) / var(--paper-sheer-alpha));" in sheer
    assert "backdrop-filter: blur(var(--paper-sheer-blur));" in sheer and "opacity" not in sheer
    assert "--paper-rgb: 246 244 241;" in css and "--paper: #F6F4F1;" in css
    # Сборка и разборка стенда хорошо видны: подложка на 15–30 % и размытие не больше 4 px;
    # конфигуратор чуть плотнее соседей (граница с «Реализовано»), но тоже полупрозрачный
    alpha = float(re.search(r"--paper-sheer-alpha: ([0-9.]+);", css).group(1))
    blur = int(re.search(r"--paper-sheer-blur: (\d+)px;", css).group(1))
    config = float(re.search(r"--paper-config-alpha: ([0-9.]+);", css).group(1))
    assert 0.15 <= alpha <= 0.3 and blur <= 4 and alpha < config <= 0.6
    # Текст прямо на подложке — со светлым ореолом (токен сцены переопределён), тихие кнопки — на своей бумаге
    assert "--text-on-scene: var(--text-on-sheer);" in sheer and "--text-on-sheer:" in css
    assert "background: rgb(var(--paper-rgb) / 0.88);" in css_rule(css, ".is-paper .btn-quiet")
    # Без размытия — плотнее, при «уменьшить прозрачность» — сплошная
    fallback = re.search(r"@supports not \(\(backdrop-filter: blur\(1px\)\).*?\{(.*?)\n\}", css, re.S).group(1)
    assert "--paper-sheer-alpha: 0.45" in fallback
    assert "--paper-sheer-alpha: 1;" in media_block(css, "(prefers-reduced-transparency: reduce)")
    # Конфигуратор — по тому же принципу: лист формы полупрозрачен на той же альфе, схема сплошная
    assert "background: rgb(255 255 255 / var(--paper-sheer-alpha));" in css_rule(css, ".is-paper .cfg-form")
    assert "background: var(--smoke);" in css_rule(css, ".cfg-plan svg") and "--smoke: var(--surface);" in css
    assert "--paper-sheer-alpha: 1;" in media_block(css, "(prefers-reduced-transparency: reduce)")
    assert "background: var(--paper)" in css_rule(css, ".section.is-paper")
    for selector in (".project-media", ".project-media img", ".scene", ".scene-canvas"):
        assert "opacity" not in css_rule(css, selector) and "backdrop-filter" not in css_rule(css, selector)


def test_home_alternates_paper_sections_and_scene_windows():
    css = STYLE.read_text(encoding="utf-8")
    projects = (STYLE.parents[2] / "templates" / "sections" / "projects.html").read_text(encoding="utf-8")
    configurator = (STYLE.parents[2] / "templates" / "sections" / "configurator.html").read_text(encoding="utf-8")

    # Проекты и конфигуратор — на «бумаге»: сплошной фон закрывает сцену, токены сцены переопределены
    assert 'class="section projects is-paper"' in projects
    assert 'class="section configurator is-paper"' in configurator
    paper = css_rule(css, ".section.is-paper")
    assert "background: var(--paper)" in paper
    for token in ("--on-scene: var(--ink)", "--text-on-scene: none", "--smoke: var(--surface)", "--smoke-blur: none"):
        assert token in paper, token
    assert "content: none" in css_rule(css, ".section.is-paper::before")
    # Блок «Полный цикл застройки» удалён вместе со стилями
    assert not (STYLE.parents[2] / "templates" / "sections" / "services.html").exists()
    assert ".cycle-step" not in css and ".section.services" not in css and ".steps-board" not in css
    # Остальные секции затемняются над сценой без растушёвки краёв: рядом «бумажные» секции,
    # и растворение давало на их границе грязную полосу; подвал — тем же затемнением
    veil = css_rule(css, ".section::before")
    assert "var(--scene-veil-narrow)" in veil and "mask-image" not in veil
    assert "--scene-veil-fade" not in css
    footer = re.search(r"\n\.site-footer \{\n  padding.*?\n\}", css, re.S).group(0)
    assert "background: var(--scene-veil-narrow);" in footer and "linear-gradient" not in footer
    assert "@media (min-width: 860px) {\n  .site-footer {\n    background: var(--scene-veil);" in css
    # Сцена и механизм сборки прежние: на широком экране холст лишь сдвинут вправо
    assert re.search(r"@media \(min-width: 1024px\) and \(min-aspect-ratio: 4/3\) \{\n  \.scene-canvas \{\n    transform: translateX\(", css)


def test_font_weights_are_tokens():
    # Насыщенность — только токены; --fw-medium (500) — для email в шапке, чтобы адрес читался
    css = STYLE.read_text(encoding="utf-8")
    weights = set(re.findall(r"font-weight: ([^;]+);", css))
    assert weights <= {"var(--fw-text)", "var(--fw-medium)", "var(--fw-strong)", "var(--fw-display)", "400 800"}, weights


def test_scene_starts_assembled_rewinds_then_assembles_on_scroll():
    js = SCRIPT.read_text(encoding="utf-8")
    css = STYLE.read_text(encoding="utf-8")

    # До загрузки кадров и в первом загруженном кадре — собранный стенд
    assert 'url("../img/stand-video/1280/frame-127.webp")' in css_rule(css, ".scene-canvas")
    assert "await load(frameAt(window.scrollY));" in js
    # Плавность по положению прокрутки, кадр — из него; индикатор считается как раньше
    assert "target = window.scrollY;" in js and "draw(frameAt(current));" in js
    assert "updateGauge(p);" in js and "return Math.min(1, Math.max(0, window.scrollY / range));" in js

    node = shutil.which("node")
    if not node:
        pytest.skip("node не установлен — расчёт кадров проверен только по исходному тексту")
    body = re.search(r"\n    function frameAt\(y\) \{.*?\n    \}\n", js, re.S).group(0)
    script = f"""
      const count = 128, REWIND_SCREENS = {re.search(r"REWIND_SCREENS = ([0-9.]+)", js).group(1)};
      const window = {{ innerHeight: 900 }}, root = {{ scrollHeight: 4287 }};
      {body}
      const range = root.scrollHeight - window.innerHeight;
      const all = []; for (let y = 0; y <= range; y++) all.push(frameAt(y));
      const steps = all.slice(1).map((f, i) => Math.abs(f - all[i]));
      // Сборка начинается сразу за разборкой (675 px = 0,75 экрана) и идёт до низа страницы
      const assembly = all.every((f, y) => y <= 675 || f === Math.round((y - 675) / (range - 675) * 127));
      const firstOne = all.indexOf(1, 675);
      const out = {{ top: frameAt(0), half: frameAt(337), turn: frameAt(675), firstOne,
                    bottom: frameAt(range), past: frameAt(range + 500),
                    maxStep: Math.max(...steps), zeros: all.filter((f) => f === 0).length, assembly }};
      root.scrollHeight = 1500;  // короткая страница: разборка — не больше четверти прокрутки
      out.shortTurn = frameAt(150); out.shortBottom = frameAt(600);
      console.log(JSON.stringify(out));
    """
    result = json.loads(subprocess.run([node, "-e", script], capture_output=True, text=True, check=True).stdout)

    assert result["top"] == 127                           # вверху — собранный стенд
    assert result["half"] == 64 and result["turn"] == 0   # первые 0,75 экрана: 127 → 0
    assert result["assembly"]                             # сразу за изломом — сборка до низа страницы
    assert result["firstOne"] - 675 < 20                  # кадр 1 — через несколько пикселей после кадра 0
    assert result["bottom"] == 127 and result["past"] == 127
    assert result["maxStep"] == 1                         # только соседние кадры, без скачков
    assert result["zeros"] < 20                           # пустая площадка — точка излома, без стоянки
    assert result["shortTurn"] == 0 and result["shortBottom"] == 127


def test_hero_secondary_button_can_open_the_request_form(content_dir):
    edit(content_dir, "home", lambda d: d["hero"].update(secondary="Оставить заявку", secondary_action="request"))

    html = make_client(content_dir).get("/").get_data(as_text=True)
    actions = re.search(r'<div class="hero-actions">.*?</div>', html, re.S).group(0)

    # Та же кнопка-стиль и тот же механизм открытия формы, что у кнопки в шапке
    assert '<button class="btn btn-secondary btn-lg" type="button" data-open-request>Оставить заявку</button>' in actions
    assert 'href="#projects"' not in actions and 'href="/portfolio"' not in actions
    assert '<dialog class="modal" id="request"' in html
