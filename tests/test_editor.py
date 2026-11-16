import json
import re

import pytest
from werkzeug.security import generate_password_hash

from app import create_app

PASSWORD = "correct horse battery staple"
TOKEN_RE = re.compile(r'name="csrf_token" value="([^"]+)"')
VERSION_RE = re.compile(r'name="version_(site|home|pages|contacts|videos)" value="([^"]+)"')
TITLE = "hero.title"


@pytest.fixture
def content_dir(make_content):
    # Демонстрационные проекты опубликованы — их можно выбирать для главной
    return make_content(publish_examples=True)


@pytest.fixture
def app(content_dir):
    return create_app({
        "TESTING": True, "CONTENT_DIR": str(content_dir), "SECRET_KEY": "test-secret",
        "ADMIN_USERNAME": "admin", "ADMIN_PASSWORD_HASH": generate_password_hash(PASSWORD),
    })


@pytest.fixture
def admin(app):
    client = app.test_client()
    token = TOKEN_RE.search(client.get("/admin/login").get_data(as_text=True)).group(1)
    assert client.post("/admin/login", data={"csrf_token": token, "username": "admin", "password": PASSWORD}).status_code == 302
    return client


def read(content_dir, name):
    return json.loads((content_dir / f"{name}.json").read_text(encoding="utf-8"))


def editor_form(client):
    html = client.get("/admin/editor").get_data(as_text=True)
    return {"csrf_token": TOKEN_RE.search(html).group(1),
            **{f"version_{name}": value for name, value in VERSION_RE.findall(html)}}


def save_draft(client, **fields):
    return client.post("/admin/editor/draft", data={**editor_form(client), **fields})


def post(client, path, **data):
    return client.post(path, data={"csrf_token": editor_form(client)["csrf_token"], **data})


def public_title(app):
    html = app.test_client().get("/").get_data(as_text=True)
    # Тире в заголовке склеено с предыдущим словом неразрывным пробелом — в данных пробел обычный
    return re.search(r'<h1 class="hero-title" id="hero-title">([^<]*)</h1>', html).group(1).replace("\xa0", " ")


def test_editor_is_private_and_lists_all_site_texts(app, admin):
    assert app.test_client().get("/admin/editor").headers["Location"].endswith("/admin/login")
    assert app.test_client().get("/admin/editor/preview").headers["Location"].endswith("/admin/login")

    html = admin.get("/admin/editor").get_data(as_text=True)
    assert 'name="hero.title"' in html and 'name="header_button"' in html and 'name="sections.1.title"' in html
    # Тексты разделов (шторки) и список контактов — тоже здесь: отдельного раздела «Тексты» больше нет
    assert 'name="pages.0.title"' in html and 'name="contacts.0.value"' in html
    assert admin.get("/admin/editor").headers["Cache-Control"] == "no-store"


def test_draft_changes_preview_but_not_the_public_site(app, admin, content_dir):
    before = (content_dir / "home.json").read_bytes()
    original = public_title(app)

    response = save_draft(admin, **{TITLE: "Новый <b>заголовок</b>"})

    assert response.status_code == 302
    assert (content_dir / "home.json").read_bytes() == before        # опубликованный файл не тронут
    assert public_title(app) == original
    preview = admin.get("/admin/editor/preview").get_data(as_text=True)
    assert "Новый &lt;b&gt;заголовок&lt;/b&gt;" in preview and "<b>заголовок" not in preview
    # Поле в редакторе показывает черновик
    assert 'value="Новый &lt;b&gt;заголовок&lt;/b&gt;"' in admin.get("/admin/editor").get_data(as_text=True)


def test_preview_has_field_markers_and_may_be_framed_only_by_the_site(app, admin):
    preview = admin.get("/admin/editor/preview")
    html = preview.get_data(as_text=True)

    assert 'data-edit="hero.title"' in html and 'data-edit="sections.1.title"' in html
    assert 'data-edit="header_button"' in html and "editor-preview.js" in html
    assert preview.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert "frame-ancestors 'self'" in preview.headers["Content-Security-Policy"]

    public = app.test_client().get("/")
    assert "data-edit" not in public.get_data(as_text=True) and "editor-preview" not in public.get_data(as_text=True)
    assert public.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in public.headers["Content-Security-Policy"]


def csp(response):
    return dict(part.strip().split(" ", 1) for part in response.headers["Content-Security-Policy"].split(";"))


def test_editor_page_may_frame_its_own_preview(app, admin):
    # Без 'self' в frame-src браузер блокирует рамку предпросмотра («Этот контент заблокирован»)
    editor = admin.get("/admin/editor")

    assert editor.status_code == 200 and 'src="/admin/editor/preview"' in editor.get_data(as_text=True)
    assert csp(editor)["frame-src"] == "'self' https://player.vimeo.com https://rutube.ru"
    assert csp(editor)["frame-ancestors"] == "'none'" and editor.headers["X-Frame-Options"] == "DENY"
    # Остальные страницы по-прежнему встраивают только плееры Vimeo и Rutube
    for client, url in ((app.test_client(), "/"), (app.test_client(), "/portfolio"), (admin, "/admin"),
                        (admin, "/admin/requests")):
        assert csp(client.get(url))["frame-src"] == "https://player.vimeo.com https://rutube.ru", url


def test_publish_updates_site_keeps_history_and_clears_draft(app, admin, content_dir):
    save_draft(admin, **{TITLE: "Опубликованный заголовок"})

    response = post(admin, "/admin/editor/publish")

    assert response.status_code == 302
    assert public_title(app) == "Опубликованный заголовок"
    assert read(content_dir, "home")["hero"]["title"] == "Опубликованный заголовок"
    assert list((content_dir / ".history").glob("home-*.json"))
    assert not (content_dir / ".drafts" / "home.json").exists()


def test_discard_removes_draft_and_keeps_site(app, admin, content_dir):
    original = public_title(app)
    save_draft(admin, **{TITLE: "Временный заголовок"})

    post(admin, "/admin/editor/discard")

    assert not (content_dir / ".drafts" / "home.json").exists()
    assert public_title(app) == original
    assert "Временный заголовок" not in admin.get("/admin/editor/preview").get_data(as_text=True)


def test_invalid_value_is_not_saved_to_draft(admin, content_dir):
    response = save_draft(admin, **{TITLE: "   "})

    assert response.status_code == 400
    assert "Заполните поле «Заголовок»" in response.get_data(as_text=True)
    assert not (content_dir / ".drafts" / "home.json").exists()


def test_stale_editor_page_does_not_overwrite_newer_draft(admin, content_dir):
    stale = editor_form(admin)
    save_draft(admin, **{TITLE: "Первая правка"})

    response = admin.post("/admin/editor/draft", data={**stale, TITLE: "Правка из старой вкладки"})

    assert response.status_code == 400
    assert "изменились, пока был открыт редактор" in response.get_data(as_text=True)
    draft = json.loads((content_dir / ".drafts" / "home.json").read_text(encoding="utf-8"))
    assert draft["data"]["hero"]["title"] == "Первая правка"


def test_publish_refuses_when_site_texts_changed_elsewhere(app, admin, content_dir):
    save_draft(admin, **{TITLE: "Заголовок из черновика"})
    # Пока черновик ждёт, тот же файл изменили в другом месте (например, вручную)
    home = read(content_dir, "home")
    home["hero"]["lead"] = "Правка в другом разделе"
    (content_dir / "home.json").write_text(json.dumps(home, ensure_ascii=False), encoding="utf-8")

    post(admin, "/admin/editor/publish")

    assert read(content_dir, "home")["hero"]["lead"] == "Правка в другом разделе"
    assert read(content_dir, "home")["hero"]["title"] != "Заголовок из черновика"
    assert (content_dir / ".drafts" / "home.json").exists()
    assert "черновик не опубликован" in admin.get("/admin/editor").get_data(as_text=True)


def test_history_version_is_restored_into_draft_only(app, admin, content_dir):
    original = public_title(app)
    save_draft(admin, **{TITLE: "Вторая версия"})
    post(admin, "/admin/editor/publish")
    [old] = [p.name for p in (content_dir / ".history").glob("home-*.json")]

    page = admin.get("/admin/editor/history").get_data(as_text=True)
    assert old in page
    assert original in admin.get(f"/admin/editor/preview?version={old}").get_data(as_text=True).replace("\xa0", " ")

    post(admin, "/admin/editor/restore", version=old)

    assert public_title(app) == "Вторая версия"                    # до публикации сайт прежний
    draft = json.loads((content_dir / ".drafts" / "home.json").read_text(encoding="utf-8"))
    assert draft["data"]["hero"]["title"] == original
    post(admin, "/admin/editor/publish")
    assert public_title(app) == original


@pytest.mark.parametrize("version", ["../home.json", "home.json", "pages-20260101-000000-000000.json", "x"])
def test_restore_and_preview_accept_only_known_history_files(admin, version):
    assert post(admin, "/admin/editor/restore", version=version).status_code == 404
    assert admin.get(f"/admin/editor/preview?version={version}").status_code == 404


@pytest.mark.parametrize("path", ["/admin/editor/draft", "/admin/editor/publish", "/admin/editor/discard",
                                  "/admin/editor/restore"])
def test_editor_actions_require_csrf(admin, content_dir, path):
    assert admin.post(path, data={TITLE: "Без токена"}).status_code == 400
    assert not (content_dir / ".drafts").exists()


def test_page_and_contact_texts_are_published_only_from_the_draft(app, admin, content_dir):
    before = {name: (content_dir / f"{name}.json").read_bytes() for name in ("pages", "contacts")}

    assert save_draft(admin, **{"pages.1.title": "Кто мы", "contacts.0.label": "Звоните"}).status_code == 302

    # Черновик: файлы и сайт прежние, предпросмотр — с правкой
    assert {name: (content_dir / f"{name}.json").read_bytes() for name in before} == before
    assert "Кто мы" not in app.test_client().get("/about").get_data(as_text=True)
    assert "Кто мы" in admin.get("/admin/editor/preview").get_data(as_text=True)
    assert "Разделы, Контакты" in admin.get("/admin/editor").get_data(as_text=True)

    assert post(admin, "/admin/editor/publish").status_code == 302

    assert read(content_dir, "pages")["pages"][1]["title"] == "Кто мы"
    assert read(content_dir, "contacts")["contacts"][0]["label"] == "Звоните"
    assert "Кто мы" in app.test_client().get("/about").get_data(as_text=True)
    assert not (content_dir / ".drafts" / "pages.json").exists()
    # Прежние версии — в истории редактора
    history = admin.get("/admin/editor/history").get_data(as_text=True)
    for name, title in (("pages", "Разделы"), ("contacts", "Контакты")):
        [old] = [p.name for p in (content_dir / ".history").glob(f"{name}-*.json")]
        assert old in history and title in history


# --- Выбор проектов для главной ---

FIELD = "sections.0.items"


def projects_items(data):
    return next(sec for sec in data["sections"] if sec["type"] == "projects")["items"]


def draft_home(content_dir):
    return json.loads((content_dir / ".drafts" / "home.json").read_text(encoding="utf-8"))["data"]


def card_titles(html):
    block = re.search(r'<section class="section projects[^"]*".*?</section>', html, re.S)
    # Название карточки: <span> внутри кнопки галереи или <h3> у заглушки
    return re.findall(r'<(?:h3|span) class="project-title[^"]*">([^<]+)</(?:h3|span)>', block.group(0)) if block else []


def portfolio_title(content_dir, pid):
    return next(p for p in read(content_dir, "portfolio")["projects"] if p["id"] == pid)["title"]


def test_picker_lists_portfolio_with_thumbnails(admin):
    html = admin.get("/admin/editor").get_data(as_text=True)

    assert 'data-picker-field="sections.0.items"' in html and f'name="{FIELD}"' in html
    assert 'data-id="example-2020-1"' in html and "/admin/thumb?src=" in html
    assert 'loading="lazy"' in html


def test_selection_saves_only_ids_in_order_without_duplicates(app, admin, content_dir):
    published_before = (content_dir / "home.json").read_bytes()

    response = save_draft(admin, **{FIELD: "example-2021-2, example-2020-1, example-2021-2"})

    assert response.status_code == 302
    assert projects_items(draft_home(content_dir)) == [{"project": "example-2021-2"}, {"project": "example-2020-1"}]
    assert (content_dir / "home.json").read_bytes() == published_before
    # Предпросмотр — выбранные проекты в заданном порядке, с названиями из портфолио
    preview = admin.get("/admin/editor/preview").get_data(as_text=True)
    assert card_titles(preview) == [portfolio_title(content_dir, "example-2021-2"), portfolio_title(content_dir, "example-2020-1")]
    assert 'data-edit="sections.0.items"' in preview
    # Публичная главная не изменилась
    assert portfolio_title(content_dir, "example-2021-2") not in card_titles(app.test_client().get("/").get_data(as_text=True))


def test_selection_is_published_and_follows_portfolio_changes(app, admin, content_dir):
    save_draft(admin, **{FIELD: "example-2020-1"})
    post(admin, "/admin/editor/publish")

    assert card_titles(app.test_client().get("/").get_data(as_text=True)) == [portfolio_title(content_dir, "example-2020-1")]
    # Название поменяли в портфолио — на главной сразу новое, копий данных в home.json нет
    portfolio = read(content_dir, "portfolio")
    next(p for p in portfolio["projects"] if p["id"] == "example-2020-1")["title"] = "Новое имя проекта"
    (content_dir / "portfolio.json").write_text(json.dumps(portfolio, ensure_ascii=False), encoding="utf-8")
    assert card_titles(app.test_client().get("/").get_data(as_text=True)) == ["Новое имя проекта"]
    assert projects_items(read(content_dir, "home")) == [{"project": "example-2020-1"}]


def test_deleted_project_does_not_break_home_and_can_be_removed(app, admin, content_dir):
    save_draft(admin, **{FIELD: "example-2020-1,example-2021-2"})
    post(admin, "/admin/editor/publish")
    portfolio = read(content_dir, "portfolio")
    portfolio["projects"] = [p for p in portfolio["projects"] if p["id"] != "example-2020-1"]
    (content_dir / "portfolio.json").write_text(json.dumps(portfolio, ensure_ascii=False), encoding="utf-8")

    home = app.test_client().get("/")
    assert home.status_code == 200
    assert card_titles(home.get_data(as_text=True)) == [portfolio_title(content_dir, "example-2021-2")]
    # В редакторе удалённый проект остаётся в списке, и его можно убрать
    assert f'value="example-2020-1,example-2021-2"' in admin.get("/admin/editor").get_data(as_text=True)
    assert save_draft(admin, **{FIELD: "example-2021-2"}).status_code == 302
    assert projects_items(draft_home(content_dir)) == [{"project": "example-2021-2"}]


def test_unknown_or_too_many_projects_are_rejected(admin, content_dir):
    assert save_draft(admin, **{FIELD: "no-such-project"}).status_code == 400
    many = ",".join(f"example-202{y}-{n}" for y in range(0, 3) for n in range(1, 4))
    response = save_draft(admin, **{FIELD: many})
    assert response.status_code == 400 and "не больше 6" in response.get_data(as_text=True)
    assert not (content_dir / ".drafts" / "home.json").exists()


def test_unchanged_selection_keeps_cards_and_discard_restores_published(admin, content_dir):
    before = projects_items(read(content_dir, "home"))
    save_draft(admin, **{"hero.lead": "Другой подзаголовок"})
    assert projects_items(draft_home(content_dir)) == before          # выбор не трогали — карточки те же

    save_draft(admin, **{FIELD: "example-2020-1"})
    post(admin, "/admin/editor/discard")
    assert projects_items(read(content_dir, "home")) == before
    assert not (content_dir / ".drafts" / "home.json").exists()


def test_thumbnail_is_small_webp_and_private(app, admin):
    response = admin.get("/admin/thumb?src=static/img/stand/frame-159.webp")

    assert response.status_code == 200 and response.mimetype == "image/webp"
    from io import BytesIO
    from PIL import Image
    assert max(Image.open(BytesIO(response.data)).size) <= 480
    assert app.test_client().get("/admin/thumb?src=static/img/stand/frame-159.webp").status_code == 302
    for bad in ["../app/__init__.py", "static/../../.env", "/etc/hosts.jpg", "portfolio/nope.jpg"]:
        assert admin.get(f"/admin/thumb?src={bad}").status_code == 404


# --- Витрина «Реализовано»: только ручной выбор и порядок, без отбора по дате ---

def test_projects_of_any_year_are_published_in_the_exact_chosen_order(app, admin, content_dir):
    # Устаревшее «последние N по дате» в home.json не мешает выбору и убирается при сохранении
    home = read(content_dir, "home")
    section = next(s for s in home["sections"] if s["type"] == "projects")
    section.update(latest=2, _latest="последние проекты по дате")
    (content_dir / "home.json").write_text(json.dumps(home, ensure_ascii=False), encoding="utf-8")
    order = ["example-2020-1", "example-2026-1", "example-2023-2", "example-2021-3", "example-2026-3"]

    assert save_draft(admin, **{FIELD: ",".join(order)}).status_code == 302
    assert post(admin, "/admin/editor/publish").status_code == 302

    saved = next(s for s in read(content_dir, "home")["sections"] if s["type"] == "projects")
    assert saved["items"] == [{"project": pid} for pid in order]
    assert "latest" not in saved and "_latest" not in saved
    titles = [portfolio_title(content_dir, pid) for pid in order]
    assert card_titles(app.test_client().get("/").get_data(as_text=True)) == titles
    # Перезапуск Flask: новое приложение читает тот же порядок из home.json
    restarted = create_app({"TESTING": True, "CONTENT_DIR": str(content_dir)})
    assert card_titles(restarted.test_client().get("/").get_data(as_text=True)) == titles
    # В редакторе после обновления страницы — тот же выбор и порядок
    assert f'value="{",".join(order)}"' in admin.get("/admin/editor").get_data(as_text=True)


def test_reordering_keeps_projects_and_removing_does_not_touch_the_portfolio(app, admin, content_dir):
    portfolio_before = (content_dir / "portfolio.json").read_bytes()
    photos_before = sorted(p.name for p in (content_dir / "media").rglob("*") if p.is_file())

    save_draft(admin, **{FIELD: "example-2026-1,example-2020-1,example-2024-1"})
    post(admin, "/admin/editor/publish")
    save_draft(admin, **{FIELD: "example-2024-1,example-2026-1"})
    post(admin, "/admin/editor/publish")

    assert projects_items(read(content_dir, "home")) == [{"project": "example-2024-1"}, {"project": "example-2026-1"}]
    assert (content_dir / "portfolio.json").read_bytes() == portfolio_before
    assert sorted(p.name for p in (content_dir / "media").rglob("*") if p.is_file()) == photos_before
    # Убранный с главной проект остаётся в портфолио
    assert portfolio_title(content_dir, "example-2020-1") in app.test_client().get("/portfolio").get_data(as_text=True)


def test_picker_offers_every_project_with_exhibition_for_search(admin, content_dir):
    html = admin.get("/admin/editor").get_data(as_text=True)
    projects = read(content_dir, "portfolio")["projects"]

    for p in projects:
        assert f'data-id="{p["id"]}"' in html
    card = re.search(r'<button class="picker-card"[^>]*data-id="example-2026-1"[^>]*>', html).group(0)
    assert 'data-event="Застройщики"' in card
    assert 'aria-label="Поиск по компании или выставке"' in html


def picker_dialog(html):
    return re.search(r'<dialog class="picker-dialog".*?</dialog>', html, re.S).group(0)


def test_picker_has_selected_list_on_top_and_catalog_with_search_and_year(admin, content_dir):
    dialog = picker_dialog(admin.get("/admin/editor").get_data(as_text=True))

    # Сверху — выбранные с числом, ниже — каталог с поиском и необязательным фильтром года
    assert dialog.index("Проекты на главной") < dialog.index("data-picker-chosen") < dialog.index("Добавить проекты")
    assert "data-picker-count" in dialog and "data-picker-changed" in dialog
    published = [p for p in read(content_dir, "portfolio")["projects"] if p["status"] == "published"]
    years = sorted({p["year"] for p in published}, reverse=True)
    assert re.findall(r'<option value="(\d+)">', dialog) == [str(y) for y in years]
    # Поиск и год — без name: в форму редактора не уходят
    assert re.search(r'<input type="search"(?![^>]*\sname=)[^>]*data-picker-search>', dialog)
    assert re.search(r'<select(?![^>]*\sname=)[^>]*data-picker-year>', dialog)
    # «Готово» применяет, «Отмена» и «Закрыть» отбрасывают; CSP — без inline-стилей и обработчиков
    assert "data-picker-done" in dialog and dialog.count("data-picker-close") == 2
    assert " style=" not in dialog and not re.search(r"\son[a-z]+=", dialog)


def test_picker_cards_show_add_action_and_drafts_are_kept_out_of_the_catalog(admin, content_dir):
    dialog = picker_dialog(admin.get("/admin/editor").get_data(as_text=True))
    projects = read(content_dir, "portfolio")["projects"]

    for p in projects:
        item = re.search(rf'<li( hidden data-picker-draft)?>\s*<button class="picker-card"[^>]*data-id="{p["id"]}"[^>]*>.*?</li>',
                         dialog, re.S)
        assert item, p["id"]
        # Черновик не выбрать: в каталоге его нет, данные — только для подписи уже выбранного
        assert bool(item.group(1)) == (p["status"] != "published"), p["id"]
        assert f'data-year="{p["year"]}"' in item.group(0) and "data-picker-action>Добавить<" in item.group(0)
    drafts = sum(p["status"] != "published" for p in projects)
    assert f"Черновики портфолио ({drafts}) здесь не показаны" in dialog
