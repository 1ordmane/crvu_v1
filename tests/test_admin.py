import json
import re
from datetime import date
from html import escape, unescape
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from werkzeug.security import check_password_hash, generate_password_hash

from app import create_app

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "app" / "static"
PASSWORD = "correct horse battery staple"
PASSWORD_HASH = generate_password_hash(PASSWORD)
TOKEN_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


@pytest.fixture
def content_dir(make_content):
    # Сценарии правят и снимают с публикации опубликованные примеры.
    return make_content(publish_examples=True)


def make_app(content_dir, **config):
    return create_app({
        "TESTING": True,
        "CONTENT_DIR": str(content_dir),
        "SECRET_KEY": "test-secret",
        "ADMIN_USERNAME": "admin",
        "ADMIN_PASSWORD_HASH": PASSWORD_HASH,
        **config,
    })


@pytest.fixture
def app(content_dir):
    return make_app(content_dir)


def token_from(client, path):
    return TOKEN_RE.search(client.get(path).get_data(as_text=True)).group(1)


def log_in(client, username="admin", password=PASSWORD):
    token = token_from(client, "/admin/login")
    return client.post("/admin/login", data={"csrf_token": token, "username": username, "password": password})


@pytest.fixture
def admin(app):
    client = app.test_client()
    assert log_in(client).status_code == 302
    client.token = token_from(client, "/admin")
    return client


def post(client, path, data=None, **kwargs):
    return client.post(path, data={"csrf_token": client.token, **(data or {})}, **kwargs)


def projects(content_dir):
    return json.loads((content_dir / "portfolio.json").read_text(encoding="utf-8"))["projects"]


def project(content_dir, project_id):
    return next(p for p in projects(content_dir) if p["id"] == project_id)


def image(fmt="PNG", size=(800, 450), color=(200, 80, 40), **save):
    buf = BytesIO()
    Image.new("RGB", size, color).save(buf, fmt, **save)
    return buf.getvalue()


def upload(client, project_id, *files):
    data = {"photos": [(BytesIO(content), name) for name, content in files]}
    return post(client, f"/admin/projects/{project_id}/photos", data, content_type="multipart/form-data")


# Обязательные поля формы проекта, кроме названия и года
REQUIRED = {"exhibition": "Выставка", "month": "5", "area": "54", "layout": "Остров"}


def create(client, title="Стенд на выставке", year="2025", **fields):
    return post(client, "/admin/projects/new", {"title": title, "year": year, **REQUIRED, **fields})


def flashes(client, path):
    return client.get(path).get_data(as_text=True)


# --- Включение и вход ---

@pytest.mark.parametrize("missing", ["SECRET_KEY", "ADMIN_USERNAME", "ADMIN_PASSWORD_HASH"])
def test_admin_is_off_until_fully_configured(content_dir, missing):
    client = make_app(content_dir, **{missing: None}).test_client()

    for path in ["/admin", "/admin/login", "/admin/projects/new"]:
        assert client.get(path).status_code == 404


@pytest.mark.parametrize("path", ["/admin", "/admin/projects/new", "/admin/projects/example-2020-1",
                                  "/admin/projects/example-2020-1/delete"])
def test_pages_require_login(app, path):
    response = app.test_client().get(path)

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/login")


def test_login_and_logout(app):
    client = app.test_client()
    response = log_in(client)

    assert response.status_code == 302
    assert client.get("/admin").status_code == 200
    assert client.get("/admin/logout").status_code == 405

    client.post("/admin/logout", data={"csrf_token": token_from(client, "/admin")})
    assert client.get("/admin").status_code == 302


@pytest.mark.parametrize("username,password", [("admin", "wrong password"), ("root", PASSWORD), ("", "")])
def test_wrong_credentials_are_rejected(app, username, password):
    client = app.test_client()
    response = log_in(client, username, password)

    assert response.status_code == 401
    assert "Неверный логин или пароль" in response.get_data(as_text=True)
    assert client.get("/admin").status_code == 302


def test_login_is_blocked_after_repeated_failures(app):
    client = app.test_client()
    for _ in range(5):
        log_in(client, password="wrong password")

    response = log_in(client)

    assert response.status_code == 429
    assert client.get("/admin").status_code == 302


def test_login_form_requires_csrf_token(app):
    response = app.test_client().post("/admin/login", data={"username": "admin", "password": PASSWORD})

    assert response.status_code == 400


def test_session_is_renewed_on_login_and_cookie_is_protected(app):
    client = app.test_client()
    before = token_from(client, "/admin/login")
    response = log_in(client)

    cookie = response.headers["Set-Cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie
    assert token_from(client, "/admin") != before


def test_changing_password_ends_existing_sessions(app, admin):
    app.config["ADMIN_PASSWORD_HASH"] = generate_password_hash("another long password")

    assert admin.get("/admin").status_code == 302


@pytest.mark.parametrize("token", [None, "", "forged-token"])
def test_actions_without_valid_csrf_token_change_nothing(admin, content_dir, token):
    data = {"status": "draft"} if token is None else {"csrf_token": token, "status": "draft"}

    response = admin.post("/admin/projects/example-2020-1/status", data=data)

    assert response.status_code == 400
    assert project(content_dir, "example-2020-1")["status"] == "published"


def test_admin_pages_are_private_and_csp_compatible(admin):
    for path in ["/admin", "/admin/projects/new", "/admin/projects/example-2020-1",
                 "/admin/projects/example-2020-1/delete", "/admin/login"]:
        response = admin.get(path, follow_redirects=True)
        html = response.get_data(as_text=True)
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["X-Robots-Tag"] == "noindex, nofollow"
        assert "default-src 'self'" in response.headers["Content-Security-Policy"]
        assert " style=" not in html and "<style" not in html
        # Скрипты — только внешние файлы приложения (CSP script-src 'self'), без кода внутри тега
        scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, re.S)
        assert html.count("<script") == len(scripts) and all(body.strip() == "" for body in scripts)
        assert all(re.fullmatch(r'<script src="/static/js/[a-z-]+\.js" defer>', tag)
                   for tag in re.findall(r"<script[^>]*>", html))
        assert not re.search(r"<[^>]*\son[a-z]+=", html)


def test_too_large_form_is_rejected(content_dir):
    client = make_app(content_dir, ADMIN_MAX_FORM_BYTES=1024).test_client()
    log_in(client)
    client.token = token_from(client, "/admin")

    response = post(client, "/admin/projects/example-2020-1", {"title": "x" * 5000, "year": "2020"})

    assert response.status_code in (302, 413)
    assert project(content_dir, "example-2020-1")["title"] != "x" * 5000


# --- Проекты ---

def test_new_project_is_saved_as_hidden_draft(admin, content_dir):
    response = create(admin, exhibition="Выставка", description="Описание", area="54,5")

    assert response.headers["Location"].endswith("/admin/projects/stend-na-vystavke-2025")
    p = project(content_dir, "stend-na-vystavke-2025")
    assert p["status"] == "draft"
    assert (p["exhibition"], p["month"], p["area"], p["description"]) == ("Выставка", 5, 54.5, "Описание")
    assert p["photos"] == []
    assert "Стенд на выставке" not in admin.get("/portfolio").get_data(as_text=True)


def test_project_ids_stay_unique(admin, content_dir):
    create(admin)
    create(admin)

    ids = [p["id"] for p in projects(content_dir)]
    assert "stend-na-vystavke-2025" in ids and "stend-na-vystavke-2025-2" in ids


@pytest.mark.parametrize("fields,message", [
    ({"title": ""}, "Укажите название"),
    ({"year": "двадцать"}, "Год — число"),
    ({"year": "1800"}, "Год — число"),
    ({"video": "javascript:alert(1)"}, "Ссылка на видео"),
    ({"area": "-1"}, "Площадь"),
])
def test_invalid_project_fields_are_not_saved(admin, content_dir, fields, message):
    before = projects(content_dir)
    response = create(admin, **{"title": "Проект", "year": "2025", **fields})

    assert response.status_code == 400
    assert message in response.get_data(as_text=True)
    assert projects(content_dir) == before


def test_edit_saves_fields_and_public_page_escapes_them(admin, content_dir):
    before = project(content_dir, "example-2020-1")
    response = post(admin, "/admin/projects/example-2020-1", {
        "title": "Новое <b>название</b>", "year": "2020", "description": "<script>alert(1)</script>", **REQUIRED})

    assert response.status_code == 302
    p = project(content_dir, "example-2020-1")
    assert p["title"] == "Новое <b>название</b>"
    assert p["specs"] == before["specs"]  # старые поля форма не показывает и не стирает
    assert p["photos"]  # фото при правке полей не теряются
    html = admin.get("/portfolio").get_data(as_text=True)
    assert "Новое &lt;b&gt;название&lt;/b&gt;" in html
    assert "<script>alert(1)" not in html


def test_unknown_project_returns_404(admin):
    assert admin.get("/admin/projects/nope").status_code == 404
    assert post(admin, "/admin/projects/nope/status", {"status": "published"}).status_code == 404


def test_project_without_photos_cannot_be_published(admin, content_dir):
    create(admin)

    post(admin, "/admin/projects/stend-na-vystavke-2025/status", {"status": "published"})

    assert project(content_dir, "stend-na-vystavke-2025")["status"] == "draft"
    assert "Сначала загрузите хотя бы одно фото" in flashes(admin, "/admin/projects/stend-na-vystavke-2025")


def test_full_flow_create_upload_publish_unpublish(admin, app, content_dir):
    create(admin, title="Мой стенд")
    pid = "moy-stend-2025"
    upload(admin, pid, ("photo.png", image()))
    photo_url = f"/media/portfolio/{pid}/01.webp"

    assert (content_dir / "media" / "portfolio" / pid / "01.webp").is_file()
    assert project(content_dir, pid)["photos"] == [f"portfolio/{pid}/01.webp"]
    assert app.test_client().get(photo_url).status_code == 404  # черновик скрыт от посетителей
    assert admin.get(photo_url).status_code == 200  # а администратор видит превью

    post(admin, f"/admin/projects/{pid}/status", {"status": "published"})
    visitor = app.test_client()
    html = visitor.get("/portfolio").get_data(as_text=True)
    assert "Мой стенд" in html and photo_url in html
    assert visitor.get(photo_url).status_code == 200

    post(admin, f"/admin/projects/{pid}/status", {"status": "draft"})
    assert "Мой стенд" not in visitor.get("/portfolio").get_data(as_text=True)
    assert visitor.get(photo_url).status_code == 404


def test_unpublishing_last_stand_of_year_hides_the_year(admin, app, content_dir):
    # В копии контента 2025 год пуст — результат не зависит от реальных данных
    path = content_dir / "portfolio.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["projects"] = [p for p in data["projects"] if p.get("year") != 2025]
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    create(admin, title="Мой стенд")
    pid = "moy-stend-2025"
    upload(admin, pid, ("photo.png", image()))
    post(admin, f"/admin/projects/{pid}/status", {"status": "published"})
    visitor = app.test_client()
    assert re.search(r'id="tab-2025"[^>]*>\s*2025 <span class="year-count">1</span>', visitor.get("/portfolio").get_data(as_text=True))

    post(admin, f"/admin/projects/{pid}/status", {"status": "draft"})
    html = visitor.get("/portfolio").get_data(as_text=True)

    assert project(content_dir, pid)["status"] == "draft"
    assert 'id="tab-2025"' not in html and 'id="year-2025"' not in html
    assert "За 2025 год" not in html
    assert "Мой стенд" not in html


def test_delete_project_keeps_copy_in_history(admin, content_dir):
    create(admin)
    pid = "stend-na-vystavke-2025"
    upload(admin, pid, ("a.png", image()))
    assert admin.get(f"/admin/projects/{pid}/delete").status_code == 200

    post(admin, f"/admin/projects/{pid}/delete")

    assert pid not in [p["id"] for p in projects(content_dir)]
    assert not (content_dir / "media" / "portfolio" / pid).exists()
    assert list((content_dir / ".history" / "media").glob(f"*/portfolio/{pid}/01.webp"))


def test_every_save_keeps_previous_version(admin, content_dir):
    original = (content_dir / "portfolio.json").read_text(encoding="utf-8")

    post(admin, "/admin/projects/example-2020-1/status", {"status": "draft"})

    copies = list((content_dir / ".history").glob("portfolio-*.json"))
    assert len(copies) == 1
    assert copies[0].read_text(encoding="utf-8") == original


# --- Фотографии ---

def test_upload_converts_to_webp_strips_metadata_and_limits_size(admin, content_dir):
    create(admin)
    pid = "stend-na-vystavke-2025"
    exif = Image.Exif()
    exif[0x010F] = "SecretCameraMaker"
    exif[0x8825] = {2: (55.0, 45.0, 0.0)}  # GPS
    upload(admin, pid, ("big.jpg", image("JPEG", size=(4000, 1000), exif=exif.tobytes())))

    saved = content_dir / "media" / "portfolio" / pid / "01.webp"
    with Image.open(saved) as img:
        assert img.format == "WEBP"
        assert max(img.size) == 2560
        assert "exif" not in img.info
    assert b"SecretCameraMaker" not in saved.read_bytes()


def test_upload_several_photos_at_once(admin, content_dir):
    create(admin)
    pid = "stend-na-vystavke-2025"

    upload(admin, pid, ("a.jpg", image("JPEG")), ("b.webp", image("WEBP")), ("c.png", image()))

    assert project(content_dir, pid)["photos"] == [f"portfolio/{pid}/0{n}.webp" for n in (1, 2, 3)]
    assert project(content_dir, pid)["cover"] == f"portfolio/{pid}/01.webp"


GIF = image("GIF")
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
EXE = b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 200
PHP = b"<?php system($_GET['c']); ?>"


@pytest.mark.parametrize("name,content", [
    ("program.exe", EXE),
    ("photo.jpg", EXE),            # исполняемый файл с расширением картинки
    ("shell.php.jpg", PHP),
    ("shell.php", PHP),
    ("anim.gif", GIF),
    ("photo.png", GIF),            # GIF под видом PNG
    ("vector.svg", SVG),
    ("photo.jpg", SVG),
    ("broken.png", image()[:60]),  # обрезанный файл
    ("empty.jpg", b""),
    ("noextension", image()),
])
def test_bad_files_are_rejected(admin, content_dir, name, content):
    create(admin)
    pid = "stend-na-vystavke-2025"

    upload(admin, pid, (name, content))

    assert project(content_dir, pid)["photos"] == []
    assert not list((content_dir / "media" / "portfolio").glob(f"{pid}/*"))
    assert "message-error" in flashes(admin, f"/admin/projects/{pid}")


def test_valid_files_are_kept_when_some_are_rejected(admin, content_dir):
    create(admin)
    pid = "stend-na-vystavke-2025"

    upload(admin, pid, ("ok.png", image()), ("bad.jpg", EXE))

    assert project(content_dir, pid)["photos"] == [f"portfolio/{pid}/01.webp"]


@pytest.mark.parametrize("config,message", [
    ({"ADMIN_MAX_IMAGE_BYTES": 1000}, "файл больше"),
    ({"ADMIN_MAX_PIXELS": 1000}, "слишком большое разрешение"),
    ({"ADMIN_MAX_FILES": 1}, "не больше 1 файлов"),
    ({"ADMIN_MAX_UPLOAD_BYTES": 2000}, "Слишком большой объём"),
])
def test_upload_limits(content_dir, config, message):
    client = make_app(content_dir, **config).test_client()
    log_in(client)
    client.token = token_from(client, "/admin")
    create(client)
    pid = "stend-na-vystavke-2025"

    upload(client, pid, ("a.png", image(size=(1600, 900))), ("b.png", image(size=(1600, 900))))

    assert project(content_dir, pid)["photos"] == []
    assert message in flashes(client, f"/admin/projects/{pid}")


def test_photo_order_and_cover(admin, app, content_dir):
    create(admin)
    pid = "stend-na-vystavke-2025"
    upload(admin, pid, *[(f"{n}.png", image(color=(n * 40, 0, 0))) for n in range(1, 4)])
    p1, p2, p3 = (f"portfolio/{pid}/0{n}.webp" for n in (1, 2, 3))

    post(admin, f"/admin/projects/{pid}/photo", {"action": "cover", "photo": p3})
    assert project(content_dir, pid)["photos"] == [p3, p1, p2]
    assert project(content_dir, pid)["cover"] == p3

    post(admin, f"/admin/projects/{pid}/photo", {"action": "down", "photo": p1})
    assert project(content_dir, pid)["photos"] == [p3, p2, p1]

    post(admin, f"/admin/projects/{pid}/photo", {"action": "up", "photo": p2})
    assert project(content_dir, pid)["photos"] == [p2, p3, p1]

    post(admin, f"/admin/projects/{pid}/status", {"status": "published"})
    html = app.test_client().get("/portfolio").get_data(as_text=True)
    urls = [f"/media/{p}" for p in (p2, p3, p1)]
    assert re.search(r'"images": \["' + '", "'.join(map(re.escape, urls)) + r'"\]', html)


def test_delete_photo_moves_file_to_history(admin, content_dir):
    create(admin)
    pid = "stend-na-vystavke-2025"
    # Два разных фото: одинаковые по содержимому файлы в проект второй раз не добавляются
    upload(admin, pid, ("a.png", image()), ("b.png", image(color=(10, 120, 200))))

    post(admin, f"/admin/projects/{pid}/photo", {"action": "delete", "photo": f"portfolio/{pid}/01.webp"})

    assert project(content_dir, pid)["photos"] == [f"portfolio/{pid}/02.webp"]
    assert not (content_dir / "media" / "portfolio" / pid / "01.webp").exists()
    assert list((content_dir / ".history" / "media").glob(f"*/portfolio/{pid}/01.webp"))


def test_last_photo_of_published_project_cannot_be_deleted(admin, content_dir):
    create(admin)
    pid = "stend-na-vystavke-2025"
    upload(admin, pid, ("a.png", image()))
    post(admin, f"/admin/projects/{pid}/status", {"status": "published"})

    post(admin, f"/admin/projects/{pid}/photo", {"action": "delete", "photo": f"portfolio/{pid}/01.webp"})

    assert project(content_dir, pid)["photos"] == [f"portfolio/{pid}/01.webp"]
    assert (content_dir / "media" / "portfolio" / pid / "01.webp").is_file()


def test_removing_app_frame_keeps_static_file(admin, content_dir):
    photo = project(content_dir, "example-2020-1")["photos"][1]

    post(admin, "/admin/projects/example-2020-1/photo", {"action": "delete", "photo": photo})

    assert photo not in project(content_dir, "example-2020-1")["photos"]
    assert (STATIC / photo.removeprefix("static/")).is_file()


@pytest.mark.parametrize("photo", ["../site.json", "portfolio/other/01.webp", "/etc/passwd"])
def test_photo_actions_only_touch_photos_of_the_project(admin, photo):
    response = post(admin, "/admin/projects/example-2020-1/photo", {"action": "delete", "photo": photo})

    assert response.status_code == 404


# --- Команда создания пароля ---

def test_hash_password_command(app):
    runner = app.test_cli_runner()

    short = runner.invoke(args=["hash-password"], input="short\nshort\n")
    assert short.exit_code != 0

    result = runner.invoke(args=["hash-password"], input=f"{PASSWORD}\n{PASSWORD}\n")
    assert result.exit_code == 0
    assert check_password_hash(result.output.strip().splitlines()[-1], PASSWORD)


# --- Тексты: правятся в «Редакторе» (черновик → «Опубликовать»), отдельного раздела нет ---

SITE_FORM = {
    "company": "Компания",
    "title": "SEO заголовок",
    "description": "SEO описание",
    "nav_home": "Домой",
    "header_button": "Написать нам",
    "floating_button": "Чат",
    "footer.title": "Подвал",
    "footer.button": "Кнопка подвала",
    "request.title": "Новая заявка",
    "request.submit": "Отправить",
    "request.subject": "Тема",
    "request.note_email": "Откроется почта.",
    "request.note_offline": "Пока офлайн.",
    "request.offline_message": "Не отправлено.",
}
EDITOR_VERSION_RE = re.compile(r'name="(version_[a-z]+)" value="([^"]*)"')


def site(content_dir):
    return json.loads((content_dir / "site.json").read_text(encoding="utf-8"))


def editor_versions(client):
    return dict(EDITOR_VERSION_RE.findall(client.get("/admin/editor").get_data(as_text=True)))


def save_texts(client, fields, versions=None):
    """Как браузер: черновик с версиями файлов со страницы редактора, затем «Опубликовать».

    Черновик не принят — ответ черновика (400 с ошибками); принят — ответ публикации."""
    draft = post(client, "/admin/editor/draft", {**(versions or editor_versions(client)), **fields})
    if draft.status_code != 302:
        return draft
    return post(client, "/admin/editor/publish")


def test_texts_section_is_removed_and_editor_is_in_navigation(admin):
    nav = admin.get("/admin").get_data(as_text=True)
    assert 'href="/admin/editor"' in nav and "/admin/texts" not in nav and ">Тексты<" not in nav
    for path in ["/admin/texts", "/admin/texts/site", "/admin/texts/contacts"]:
        assert admin.get(path).status_code == 404
        assert post(admin, path, SITE_FORM).status_code == 404


def test_editor_is_csp_compatible(admin):
    response = admin.get("/admin/editor")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert " style=" not in html and "<style" not in html
    assert not re.search(r"<script(?![^>]*\ssrc=)", html)
    assert not re.search(r"<[^>]*\son[a-z]+=", html)


@pytest.mark.parametrize("token", [None, "", "forged-token"])
def test_site_texts_without_valid_csrf_token_change_nothing(admin, content_dir, token):
    before = (content_dir / "site.json").read_bytes()
    data = {**editor_versions(admin), **SITE_FORM}
    if token is not None:
        data["csrf_token"] = token

    assert admin.post("/admin/editor/draft", data=data).status_code == 400
    assert admin.post("/admin/editor/publish", data={} if token is None else {"csrf_token": token}).status_code == 400
    assert (content_dir / "site.json").read_bytes() == before
    assert not (content_dir / ".drafts").exists()


def test_site_texts_are_saved_and_shown_escaped(admin, app, content_dir):
    form = {**SITE_FORM, "header_button": "<b>Звоните</b>", "description": "  Строка\r\nвторая   строка "}
    lead = site(content_dir)["footer"].get("lead")

    response = save_texts(admin, form)

    assert response.status_code == 302
    saved = site(content_dir)
    assert saved["header_button"] == "<b>Звоните</b>"
    assert saved["description"] == "Строка вторая строка"
    # Поля, которых нет в форме (подзаголовок подвала), остаются как были
    assert saved["footer"] == {"title": "Подвал", "button": "Кнопка подвала", **({"lead": lead} if lead else {})}
    assert saved["request"]["offline_message"] == "Не отправлено."
    assert "Опубликовано" in flashes(admin, "/admin/editor")
    html = app.test_client().get("/").get_data(as_text=True)
    assert "&lt;b&gt;Звоните&lt;/b&gt;" in html and "<b>Звоните</b>" not in html
    assert "<title>SEO заголовок</title>" in html
    assert list((content_dir / ".history").glob("site-*.json"))


def test_site_texts_keep_unknown_and_service_fields(admin, content_dir):
    data = site(content_dir)
    data["extra"] = {"keep": [1, 2]}
    data["request"]["email"] = "sales@example.com"
    (content_dir / "site.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    form = {**SITE_FORM, "extra": "x", "_comment": "x", "request.email": "evil@example.com", "nav_home": ""}
    assert save_texts(admin, form).status_code == 302

    saved = site(content_dir)
    assert saved["extra"] == {"keep": [1, 2]}
    assert saved["_comment"] == data["_comment"]
    assert saved["request"]["_comment"] == data["request"]["_comment"]
    assert saved["request"]["email"] == "sales@example.com"
    assert saved["nav_home"] == ""
    assert list(saved) == list(data)


@pytest.mark.parametrize("field, value, message", [
    ("company", "   ", "Заполните поле «Название компании»"),
    ("header_button", "x" * 201, "не длиннее 200 символов"),
    ("description", "x" * 1001, "не длиннее 1000 символов"),
])
def test_invalid_site_texts_are_not_saved(admin, content_dir, field, value, message):
    before = (content_dir / "site.json").read_bytes()

    response = save_texts(admin, {**SITE_FORM, field: value})

    html = response.get_data(as_text=True)
    assert response.status_code == 400
    assert message in html
    assert 'value="Чат"' in html
    assert (content_dir / "site.json").read_bytes() == before


def test_site_texts_are_not_saved_when_content_cannot_be_checked(admin, content_dir):
    before = (content_dir / "site.json").read_bytes()
    versions = editor_versions(admin)
    (content_dir / "pages.json").write_text("{ broken", encoding="utf-8")

    response = save_texts(admin, SITE_FORM, versions)

    assert response.status_code == 302
    assert "Черновик не сохранён" in flashes(admin, "/admin")
    assert (content_dir / "site.json").read_bytes() == before
    assert not (content_dir / ".drafts").exists()


def test_site_field_with_unexpected_type_is_not_overwritten(admin, content_dir):
    data = site(content_dir)
    data["footer"] = "не объект"
    (content_dir / "site.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    before = (content_dir / "site.json").read_bytes()

    response = save_texts(admin, SITE_FORM)

    assert response.status_code == 400
    assert "исправьте файл вручную" in response.get_data(as_text=True)
    assert (content_dir / "site.json").read_bytes() == before


# --- Тексты: все группы ---

GROUP_TITLES = ["Общие тексты", "Первый экран",
                "Конфигуратор: Ваш стенд начинается здесь", "Раздел: Портфолио", "Раздел: О нас",
                "Раздел: Контакты", "Список контактов"]


def read(content_dir, name):
    return json.loads((content_dir / f"{name}.json").read_text(encoding="utf-8"))


def write(content_dir, name, data):
    (content_dir / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def save_group(client, key, fields):
    """Правка одной группы тем же редактором: поля других групп не отправляются и не меняются."""
    return save_texts(client, fields)


def test_every_text_group_is_in_the_editor(admin):
    html = admin.get("/admin/editor").get_data(as_text=True)
    summaries = re.findall(r"<summary>([^<]+)</summary>", html)
    for title in GROUP_TITLES:
        assert title in summaries, title
    # Версия каждого файла — защита от правки поверх чужой
    assert set(editor_versions(admin)) == {"version_site", "version_home", "version_pages", "version_contacts", "version_videos"}


@pytest.mark.parametrize("field", ["hero.title", "pages.1.title", "contacts.0.label"])
def test_text_saving_is_off_and_requires_login(content_dir, app, field):
    assert make_app(content_dir, SECRET_KEY=None).test_client().get("/admin/editor").status_code == 404
    client = app.test_client()
    assert client.get("/admin/editor").headers["Location"].endswith("/admin/login")
    before = {name: (content_dir / f"{name}.json").read_bytes() for name in ("home", "pages", "contacts")}
    assert client.post("/admin/editor/draft", data={field: "x"}).status_code == 400
    token = token_from(client, "/admin/login")
    response = client.post("/admin/editor/draft", data={"csrf_token": token, field: "x"})
    assert response.headers["Location"].endswith("/admin/login")
    assert {name: (content_dir / f"{name}.json").read_bytes() for name in before} == before
    assert not (content_dir / ".drafts").exists()


@pytest.mark.parametrize("token", [None, "forged-token"])
def test_text_group_without_valid_csrf_token_changes_nothing(admin, content_dir, token):
    before = (content_dir / "contacts.json").read_bytes()
    data = {"contacts.0.label": "Взлом"} if token is None else {"contacts.0.label": "Взлом", "csrf_token": token}

    assert admin.post("/admin/editor/draft", data=data).status_code == 400
    assert (content_dir / "contacts.json").read_bytes() == before


def test_form_shows_every_intended_field(admin):
    expected = {
        "site": ["company", "ui.menu", "ui.close", "ui.photo_alt", "footer.copy", "request.name_label",
                 "request.cancel", "request.body_name"],
        "hero": ["hero.title", "hero.lead", "hero.primary", "hero.secondary"],
        "section-configurator": ["sections.1.title", "sections.1.length_label", "sections.1.types.3.name",
                                 "sections.1.summary_text", "sections.1.submit"],
        "page-about": ["pages.1.title", "pages.1.nav", "pages.1.since_text", "pages.1.founded_year",
                       "pages.1.paragraphs", "pages.1.stats.3.value", "pages.1.stats.3.label"],
        "page-portfolio": ["pages.0.title", "pages.0.photo_count", "pages.0.empty"],
        "page-contacts": ["pages.2.button", "pages.2.empty"],
        "contacts": ["contacts.0.label", "contacts.3.value"],
    }
    html = admin.get("/admin/editor").get_data(as_text=True)
    for key, names in expected.items():
        for name in names:
            assert f'name="{name}"' in html, (key, name)


def test_technical_keys_are_not_in_forms(admin):
    html = admin.get("/admin/editor").get_data(as_text=True)
    # Как прежде в разделе «Тексты» — кроме карточек «Реализовано»: там area — подпись «54 м²», а не площадь конфигуратора
    groups = re.findall(r'<details class="editor-group"[^>]*>\s*<summary>([^<]+)</summary>(.*?)</details>', html, re.S)
    assert len(groups) >= len(GROUP_TITLES)
    form = "".join(body for title, body in groups if not title.startswith("Проекты:"))
    for name in re.findall(r'name="([^"]+)"', form):
        if name != "csrf_token" and not name.startswith("version_"):
            last = name.rsplit(".", 1)[-1]
            assert last not in {"id", "type", "slug", "icon", "walls", "deck", "area", "visible",
                                "primary_target", "secondary_page", "button_page", "first_year", "email"}, name
            assert not last.startswith("_"), name


def test_missing_key_shows_current_site_text_and_is_not_written(admin, content_dir):
    html = admin.get("/admin/editor").get_data(as_text=True)
    assert re.search(r'name="ui.menu" id="e-ui-menu" value="Меню"', html)

    before = read(content_dir, "site")
    assert save_group(admin, "site", {"ui.menu": "Меню"}).status_code == 302
    assert "ui" not in read(content_dir, "site")
    assert read(content_dir, "site") == before


def test_hero_texts_are_saved_and_links_stay(admin, app, content_dir):
    before = read(content_dir, "home")
    form = {"hero.title": "  Стенды\r\nпод ключ ", "hero.lead": "Подзаголовок", "hero.primary": "Собрать",
            "hero.secondary": "Работы", "hero.primary_target": "evil", "hero.secondary_page": "evil"}

    assert save_group(admin, "hero", form).status_code == 302

    saved = read(content_dir, "home")
    assert saved["hero"] == {**before["hero"], "title": "Стенды под ключ", "lead": "Подзаголовок",
                             "primary": "Собрать", "secondary": "Работы"}
    assert saved["sections"] == before["sections"] and saved["_comment"] == before["_comment"]
    html = app.test_client().get("/").get_data(as_text=True)
    assert "Стенды под ключ" in html and 'href="#configurator">Собрать</a>' in html


def test_configurator_texts_and_types(admin, app, content_dir):
    before = read(content_dir, "home")["sections"][1]
    form = {"sections.1.length_label": "Длина стенда", "sections.1.types.1.name": "Угловой плюс",
            "sections.1.options": "Бар", "sections.1.types.4.name": "Мезонин",
            "sections.1.summary_text": "{type}: {area} м²", "sections.1.area": "1"}
    html = admin.get("/admin/editor").get_data(as_text=True)
    # Опций и типа со вторым этажом в редакторе нет: на сайте они не используются
    assert 'name="sections.1.options"' not in html and 'name="sections.1.types.4.name"' not in html
    assert 'name="sections.1.options_label"' not in html and 'name="sections.1.deck_note"' not in html

    assert save_group(admin, "section-configurator", form).status_code == 302

    cfg = read(content_dir, "home")["sections"][1]
    assert cfg["length_label"] == "Длина стенда" and cfg["summary_text"] == "{type}: {area} м²"
    assert cfg["types"][1] == {"name": "Угловой плюс", "walls": before["types"][1]["walls"]}
    # Записи в JSON не трогаются: лишние поля формы игнорируются
    assert cfg["types"][4] == before["types"][4] and cfg["options"] == before["options"]
    assert cfg["area"] == before["area"] and cfg["_walls"] == before["_walls"]
    html = app.test_client().get("/").get_data(as_text=True)
    assert '<label for="cfg-length">Длина стенда</label>' in html
    assert 'value="Угловой плюс"' in html and 'value="Бар"' not in html and "Мезонин" not in html
    texts = json.loads(unescape(re.search(r"data-texts='([^']*)'", html).group(1)))
    assert texts["summary"] == "{type}: {area} м²"


@pytest.mark.parametrize("field, value, message", [
    ("sections.1.types.0.name", "  ", "Заполните поле «Тип 1»"),
    ("sections.1.types.0.name", "Угловой", "пустые или повторяющиеся name"),
    ("sections.1.title", "x" * 201, "не длиннее 200 символов"),
])
def test_invalid_configurator_texts_are_not_saved(admin, content_dir, field, value, message):
    before = (content_dir / "home.json").read_bytes()

    response = save_group(admin, "section-configurator", {field: value})

    assert response.status_code == 400
    assert message in response.get_data(as_text=True)
    assert (content_dir / "home.json").read_bytes() == before


def test_about_paragraphs_and_numbers(admin, app, content_dir):
    form = {"pages.1.paragraphs": "Первый\nабзац.\n\n\nВторой абзац.", "pages.1.founded_year": "2001",
            "pages.1.since_text": "С {year} года, {years}", "pages.1.stats.0.value": "1500",
            "pages.1.stats.0.label": "проектов"}

    assert save_group(admin, "page-about", form).status_code == 302

    about = read(content_dir, "pages")["pages"][1]
    assert about["paragraphs"] == ["Первый абзац.", "Второй абзац."]
    assert about["founded_year"] == 2001 and about["stats"][0]["value"] == 1500
    assert about["stats"][0]["suffix"] == "+"
    html = app.test_client().get("/about").get_data(as_text=True)
    assert "С 2001 года," in html and '<p class="prose">Второй абзац.</p>' in html


@pytest.mark.parametrize("field, value", [
    ("pages.1.founded_year", "двухтысячный"), ("pages.1.founded_year", "1500"),
    ("pages.1.stats.0.value", "-5"), ("pages.1.stats.0.value", ""), ("pages.1.title", ""),
])
def test_invalid_about_values_are_not_saved(admin, content_dir, field, value):
    before = (content_dir / "pages.json").read_bytes()

    assert save_group(admin, "page-about", {field: value}).status_code == 400
    assert (content_dir / "pages.json").read_bytes() == before


def test_contacts_values_are_checked_by_type(admin, app, content_dir):
    before = (content_dir / "contacts.json").read_bytes()
    response = save_group(admin, "contacts", {"contacts.1.value": "не почта"})
    assert response.status_code == 400 and "не подходит для type email" in response.get_data(as_text=True)
    assert (content_dir / "contacts.json").read_bytes() == before

    assert save_group(admin, "contacts", {"contacts.1.value": "sales@example.com",
                                          "contacts.1.type": "link"}).status_code == 302
    saved = read(content_dir, "contacts")["contacts"][1]
    assert saved == {"type": "email", "label": "Email", "value": "sales@example.com"}
    assert 'href="mailto:sales@example.com"' in app.test_client().get("/contacts").get_data(as_text=True)


def test_socials_are_edited_and_checked(admin, app, content_dir):
    contacts = read(content_dir, "contacts")
    contacts["socials"] = [{"network": "telegram", "label": "Telegram", "url": "https://t.me/crvu_ru"}]
    write(content_dir, "contacts", contacts)
    before = (content_dir / "contacts.json").read_bytes()

    response = save_group(admin, "contacts", {"socials.0.url": "t.me/crvu_ru"})
    assert response.status_code == 400 and "http:// или https://" in response.get_data(as_text=True)
    assert (content_dir / "contacts.json").read_bytes() == before

    assert save_group(admin, "contacts", {"socials.0.label": "Телеграм", "socials.0.network": "vk"}).status_code == 302
    assert read(content_dir, "contacts")["socials"] == [
        {"network": "telegram", "label": "Телеграм", "url": "https://t.me/crvu_ru"}]
    contacts_page = next(i for i, p in enumerate(read(content_dir, "pages")["pages"]) if p["type"] == "contacts")
    assert save_group(admin, f"page-contacts", {f"pages.{contacts_page}.socials_title": "Соцсети"}).status_code == 302
    html = app.test_client().get("/contacts").get_data(as_text=True)
    assert ">Соцсети</h3>" in html and '<span class="social-name">Телеграм</span>' in html


def test_pricing_names_are_edited_but_numbers_stay(admin, app, content_dir):
    before = read(content_dir, "home")["sections"][1]["pricing"]
    assert save_group(admin, "section-configurator", {
        "sections.1.pricing.levels.1.name": "Авторский",
        "sections.1.pricing.extras.2.name": "Светодиодные экраны",
        "sections.1.pricing.levels.1.factor": "99",
    }).status_code == 302
    after = read(content_dir, "home")["sections"][1]["pricing"]
    assert after["levels"][1]["name"] == "Авторский" and after["extras"][2]["name"] == "Светодиодные экраны"
    assert after["levels"][1]["factor"] == before["levels"][1]["factor"]
    assert after["rate"] == before["rate"] and after["open_sides"] == before["open_sides"]
    html = app.test_client().get("/").get_data(as_text=True)
    assert ">Авторский</span>" in html and ">Светодиодные экраны</span>" in html

    response = save_group(admin, "section-configurator", {"sections.1.pricing.levels.0.name": ""})
    assert response.status_code == 400


def test_stale_form_is_rejected_when_file_changed(admin, content_dir):
    versions = editor_versions(admin)
    home = read(content_dir, "home")
    home["sections"][1]["title"] = "Изменён в другом месте"
    write(content_dir, "home", home)
    before = (content_dir / "home.json").read_bytes()

    for sent in (versions, {**versions, "version_home": ""}):
        response = save_texts(admin, {"sections.1.title": "Конструктор 2"}, sent)
        assert response.status_code == 400
        assert "Обновите страницу" in response.get_data(as_text=True)
    assert (content_dir / "home.json").read_bytes() == before
    assert not (content_dir / ".drafts").exists()


def test_unknown_fields_survive_every_group(admin, content_dir):
    for name in ("home", "pages", "contacts"):
        data = read(content_dir, name)
        data["x_unknown"] = {"keep": [1, None, "a"]}
        write(content_dir, name, data)
    home = read(content_dir, "home")
    home["sections"][1]["x_note"] = "служебное"
    write(content_dir, "home", home)

    assert save_group(admin, "section-configurator", {"sections.1.title": "Конструктор!"}).status_code == 302
    assert save_group(admin, "page-about", {"pages.1.stats_title": "Цифры"}).status_code == 302
    assert save_group(admin, "contacts", {"contacts.0.label": "Тел."}).status_code == 302

    for name in ("home", "pages", "contacts"):
        assert read(content_dir, name)["x_unknown"] == {"keep": [1, None, "a"]}
    assert read(content_dir, "home")["sections"][1]["x_note"] == "служебное"
    assert list((content_dir / ".history").glob("home-*.json"))


@pytest.mark.parametrize("change", [
    lambda h: h["hero"].update(title=5),
    lambda h: h["sections"][1].update(summary_text=["Бар", "Свет"]),
])
def test_field_of_unexpected_type_is_not_overwritten(admin, content_dir, change):
    home = read(content_dir, "home")
    change(home)
    write(content_dir, "home", home)
    before = (content_dir / "home.json").read_bytes()

    key = "hero" if isinstance(home["hero"]["title"], int) else "section-configurator"
    field = "hero.title" if key == "hero" else "sections.1.summary_text"
    response = save_group(admin, key, {field: "Новое"})

    assert response.status_code == 400
    assert "исправьте файл вручную" in response.get_data(as_text=True)
    assert (content_dir / "home.json").read_bytes() == before


def test_new_ui_texts_reach_public_pages(admin, app, content_dir):
    form = {"ui.menu": "Навигация", "ui.close": "Скрыть", "ui.photo_alt": "{title} — {n}/{total}",
            "footer.copy": "{company}, {year}", "request.name_label": "Как вас зовут",
            "request.body_contact": "Связь", "ui.video": "Видео"}

    assert save_group(admin, "site", form).status_code == 302

    saved = read(content_dir, "site")
    assert saved["ui"] == {"menu": "Навигация", "close": "Скрыть", "photo_alt": "{title} — {n}/{total}",
                           "video": "Видео"}
    html = app.test_client().get("/portfolio").get_data(as_text=True)
    assert '<span class="menu-toggle-text">Навигация</span>' in html
    assert 'data-route="home">Скрыть</a>' in html and 'data-close-dialog>Скрыть</button>' in html
    assert 'data-photo-alt="{title} — {n}/{total}"' in html
    assert f'<p class="footer-copy">{escape(saved["company"])}, {date.today().year}</p>' in html
    assert '<label for="rq-name">Как вас зовут</label>' in html and 'data-body-contact="Связь"' in html
    assert "admin.css" not in html


def test_project_cards_are_edited_in_admin_and_photo_path_is_checked(admin, content_dir):
    html = admin.get("/admin/editor").get_data(as_text=True)
    assert 'name="sections.0.items.0.image"' in html and 'name="sections.0.items.2.area"' in html

    # Фото, которого нет в content/media, сохранить нельзя — карточка не станет «битой»
    before = (content_dir / "home.json").read_bytes()
    assert save_group(admin, "section-projects", {"sections.0.items.0.image": "home/none.webp"}).status_code == 400
    assert (content_dir / "home.json").read_bytes() == before

    (content_dir / "media" / "home").mkdir(exist_ok=True)
    (content_dir / "media" / "home" / "p1.webp").write_bytes(b"x")
    form = {"sections.0.items.0.image": "home/p1.webp", "sections.0.items.0.title": "Стенд <b>1</b>"}
    assert save_group(admin, "section-projects", form).status_code == 302
    card = read(content_dir, "home")["sections"][0]["items"][0]
    assert card["image"] == "home/p1.webp" and card["title"] == "Стенд <b>1</b>"
