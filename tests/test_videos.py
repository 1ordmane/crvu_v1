"""Видеоотзывы: библиотека роликов RuTube и выбор трёх для главной — только в Редакторе, группа
«Видеоотзывы» (черновик → предпросмотр → «Опубликовать»), — и шторка «Другие видео-отзывы».

Библиотека — content/videos.json (app/videos.py), позиции на главной — блок reviews в home.json
(items: {"video": id} или {} — заглушка). Видео на сайт не загружаются: хранится код ролика и ссылка
на RuTube; обложка — копия картинки RuTube в content/media/videos/.
"""

import json
import re
from io import BytesIO

import pytest
from PIL import Image

from app import create_app
from app.content import validate_content
from app.videos import clean, fetch_thumbnail, for_site, parse_link, validate
from tests.test_admin import PASSWORD_HASH, editor_versions, log_in, token_from
from tests.test_reviews import with_reviews

CODES = {k: k * 32 for k in "abcdef"} | {"g": "1" * 32}
IDS = {k: f"{k}{k}{k}{k}0000" if k in "abcdef" else "11110000" for k in CODES}
STATIC = __import__("pathlib").Path(__file__).resolve().parent.parent / "app" / "static"


def video(k, title=None, description="", active=True):
    return {"id": IDS[k], "rutube": CODES[k], "url": f"https://rutube.ru/video/{CODES[k]}/",
            "title": f"Отзыв {k.upper()}" if title is None else title, "description": description, "active": active}


LIBRARY = {"videos": [video("a", description="Стенд на выставке"), video("b"), video("c"), video("d"),
                      video("e"), video("f"), video("g", "Выключенный", active=False)]}


@pytest.fixture
def content_dir(make_content):
    content = make_content()
    with_reviews(content)
    return content


def write_videos(content_dir, data=LIBRARY):
    (content_dir / "videos.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def read(content_dir, name):
    return json.loads((content_dir / f"{name}.json").read_text(encoding="utf-8"))


def set_positions(content_dir, keys):
    """Позиции блока на главной: ключи роликов или None — заглушка."""
    home = read(content_dir, "home")
    section = next(s for s in home["sections"] if s["type"] == "reviews")
    section["items"] = [{"video": IDS[k]} if k else {} for k in keys]
    (content_dir / "home.json").write_text(json.dumps(home, ensure_ascii=False), encoding="utf-8")


def reviews_index(content_dir):
    return next(i for i, s in enumerate(read(content_dir, "home")["sections"]) if s["type"] == "reviews")


def positions(content_dir):
    section = read(content_dir, "home")["sections"][reviews_index(content_dir)]
    return [it.get("video") for it in section["items"]]


def site(content_dir, **config):
    return create_app({"TESTING": True, "CONTENT_DIR": str(content_dir), **config})


def reviews(html):
    return re.search(r'<section class="section reviews.*?</section>', html, re.S).group(0)


def sheet(html):
    return re.search(r'<section class="sheet[^"]*" id="video-reviews".*?</section>', html, re.S).group(0)


def player_codes(part):
    return re.findall(r'data-video-player="https://rutube.ru/play/embed/([0-9a-f]{32})/"', part)


def make_thumb(content_dir, k):
    folder = content_dir / "media" / "videos"
    folder.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 18), "red").save(folder / f"{CODES[k]}.webp", "WEBP")


@pytest.fixture
def admin(content_dir):
    app = site(content_dir, SECRET_KEY="test-secret", ADMIN_USERNAME="admin", ADMIN_PASSWORD_HASH=PASSWORD_HASH)
    client = app.test_client()
    assert log_in(client).status_code == 302
    client.token = token_from(client, "/admin/editor")
    return client


def post(client, path, **data):
    return client.post(path, data={"csrf_token": client.token, **data})


def draft(client, **fields):
    """«Сохранить черновик» (или кнопка группы «Видеоотзывы» — поле video_action) с отпечатками файлов."""
    return post(client, "/admin/editor/draft", **editor_versions(client), **fields)


def publish(client):
    return post(client, "/admin/editor/publish")


def row_fields(k, **change):
    """Поля строки ролика в группе «Видеоотзывы», как их отправляет форма (включён — галочка)."""
    v = {**next(v for v in LIBRARY["videos"] if v["id"] == IDS[k]), **change}
    fields = {f"video.{IDS[k]}.{key}": v[key] for key in ("url", "title", "description")}
    if v["active"]:
        fields[f"video.{IDS[k]}.active"] = "1"
    return fields


def drafted(content_dir, name="videos"):
    return json.loads((content_dir / ".drafts" / f"{name}.json").read_text(encoding="utf-8"))["data"]


def choose_in_editor(client, content_dir, keys, publish_it=True):
    """Как окно выбора в Редакторе: скрытое поле с id по порядку → «Сохранить черновик» → «Опубликовать»."""
    field = f"sections.{reviews_index(content_dir)}.items"
    response = draft(client, **{field: ",".join(IDS[k] for k in keys)})
    if response.status_code != 302 or not publish_it:
        return response
    return publish(client)


def video_group(html):
    """Группа «Видеоотзывы» Редактора — до следующей группы или кнопки сохранения."""
    part = html.split("<summary>Видеоотзывы</summary>", 1)[1]
    return re.split(r'<details class="editor-group"|<div class="editor-save">', part, maxsplit=1)[0]


# --- Ссылки и данные ---

@pytest.mark.parametrize("raw, url", [
    (f"https://rutube.ru/video/{CODES['a']}/", f"https://rutube.ru/video/{CODES['a']}/"),
    (f"https://rutube.ru/play/embed/{CODES['a']}", f"https://rutube.ru/video/{CODES['a']}/"),
    (f"http://www.rutube.ru/video/{CODES['a'].upper()}", f"https://rutube.ru/video/{CODES['a']}/"),
    (f"  https://rutube.ru/shorts/{CODES['a']}/ ", f"https://rutube.ru/video/{CODES['a']}/"),
    (f"https://rutube.ru/video/private/{CODES['a']}/?p=Key_12345",
     f"https://rutube.ru/video/private/{CODES['a']}/?p=Key_12345"),
])
def test_rutube_links_are_normalised_to_the_video_code(raw, url):
    assert parse_link(raw) == {"rutube": CODES["a"], "url": url}


@pytest.mark.parametrize("raw", [
    "", "rutube", f"https://youtube.com/watch?v={CODES['a']}", f"https://rutube.ru.evil.com/video/{CODES['a']}/",
    f"https://evil.com/rutube.ru/video/{CODES['a']}/", "https://rutube.ru/video/12345/", "javascript:alert(1)",
    f'<iframe src="https://rutube.ru/play/embed/{CODES["a"]}"></iframe>', f"https://user@rutube.ru/video/{CODES['a']}/",
    f"https://rutube.ru/video/private/{CODES['a']}/",
])
def test_anything_else_is_refused(raw):
    assert parse_link(raw) is None


def test_text_is_cleaned_and_limited():
    assert clean("  Отзыв​  клиента\n\tИвановой ", 50) == "Отзыв клиента Ивановой"
    assert clean("a" * 121, 120) is None
    assert clean(None, 10) == ""


def test_site_data_follows_positions_and_never_repeats():
    data = for_site(LIBRARY, [{"video": IDS["b"]}, {}, {"video": IDS["g"]}])
    assert [c and c["title"] for c in data["featured"]] == ["Отзыв B", None, None]   # выключенный — заглушка
    assert [c["title"] for c in data["more"]] == ["Отзыв A", "Отзыв C", "Отзыв D", "Отзыв E", "Отзыв F"]
    first = data["featured"][0]
    assert first["player"] == f"https://rutube.ru/play/embed/{CODES['b']}/"
    assert first["page"] == f"https://rutube.ru/video/{CODES['b']}/"
    # Позиций нет или все пустые — блок со своими заглушками, все включённые ролики — в «Другие»
    for empty in ([], [{}, {}, {}], None):
        assert for_site(LIBRARY, empty)["featured"] == []
        assert len(for_site(LIBRARY, empty)["more"]) == 6


def test_site_data_survives_broken_entries():
    data = for_site({"videos": [video("a"), "мусор", {"id": "bbbb0000", "url": "https://evil.com"}]},
                    [{"video": IDS["a"]}, {"video": "bbbb0000"}, "x"])
    assert [c and c["title"] for c in data["featured"]] == ["Отзыв A", None, None] and data["more"] == []
    assert for_site(None, None) == {"featured": [], "more": []}


@pytest.mark.parametrize("change, message", [
    (lambda d: d["videos"][0].update(url="https://youtube.com/x"), "url"),
    (lambda d: d["videos"][0].update(url=f"https://rutube.ru/video/{CODES['b']}/"), "url"),
    (lambda d: d["videos"][1].update(rutube=CODES["a"], url=f"https://rutube.ru/video/{CODES['a']}/"), "уже есть"),
    (lambda d: d["videos"][1].update(id=IDS["a"]), "повторяется"),
    (lambda d: d["videos"][0].update(id="Ролик"), "id"),
    (lambda d: d["videos"][0].update(title="<b>\n"), "title"),
    (lambda d: d["videos"][0].update(active="да"), "active"),
    (lambda d: d.update(videos="все"), "нужен список"),
])
def test_validation(change, message):
    data = json.loads(json.dumps(LIBRARY))
    change(data)
    assert any(message in e for e in validate(data)), validate(data)


def test_check_content_reads_the_optional_file_and_positions(content_dir):
    assert validate_content(content_dir, STATIC) == []          # файла нет — всё в порядке
    write_videos(content_dir)
    set_positions(content_dir, ["a", None, "b"])
    assert validate_content(content_dir, STATIC) == []
    set_positions(content_dir, ["a", "a"])
    assert any("до 3 разных видео" in e for e in validate_content(content_dir, STATIC))
    home = read(content_dir, "home")
    home["sections"][reviews_index(content_dir)]["items"] = [{"video": "<script>"}]
    (content_dir / "home.json").write_text(json.dumps(home, ensure_ascii=False), encoding="utf-8")
    assert any("video: id видео" in e for e in validate_content(content_dir, STATIC))
    (content_dir / "videos.json").write_text("{", encoding="utf-8")
    assert any("videos.json" in e for e in validate_content(content_dir, STATIC))


def test_sheet_address_is_reserved(content_dir):
    pages = read(content_dir, "pages")
    pages["pages"][0]["slug"] = "video-reviews"
    (content_dir / "pages.json").write_text(json.dumps(pages, ensure_ascii=False), encoding="utf-8")
    assert any("занят" in e for e in validate_content(content_dir, STATIC))


# --- Обложки ---

def jpeg():
    buf = BytesIO()
    Image.new("RGB", (1280, 720), "blue").save(buf, "JPEG")
    return buf.getvalue()


def test_thumbnail_is_copied_from_rutube_and_resized(tmp_path):
    asked = []

    def get(url, limit):
        asked.append(url)
        if url.startswith("https://rutube.ru/api/"):
            return json.dumps({"thumbnail_url": "https://pic.rtbcdn.ru/video/x/y.jpg"}).encode()
        return jpeg()

    assert fetch_thumbnail(tmp_path, video("a"), get) is True
    assert asked == [f"https://rutube.ru/api/video/{CODES['a']}/?format=json", "https://pic.rtbcdn.ru/video/x/y.jpg"]
    with Image.open(tmp_path / "videos" / f"{CODES['a']}.webp") as img:
        assert img.format == "WEBP" and img.size == (960, 540)


@pytest.mark.parametrize("thumb", ["https://evil.com/x.jpg", "http://pic.rtbcdn.ru/x.jpg", "javascript:x",
                                   "https://pic.rtbcdn.ru.evil.com/x.jpg", ""])
def test_thumbnail_only_from_rutube_hosts(tmp_path, thumb):
    def get(url, limit):
        return json.dumps({"thumbnail_url": thumb}).encode() if "/api/" in url else jpeg()

    assert fetch_thumbnail(tmp_path, video("a"), get) is False
    assert not (tmp_path / "videos").exists()


def test_thumbnail_failures_are_quiet(tmp_path):
    def offline(url, limit):
        raise OSError("нет сети")

    def not_image(url, limit):
        return json.dumps({"thumbnail_url": "https://pic.rtbcdn.ru/x.jpg"}).encode() if "/api/" in url else b"<html>"

    assert fetch_thumbnail(tmp_path, video("a"), offline) is False
    assert fetch_thumbnail(tmp_path, video("a"), not_image) is False
    assert fetch_thumbnail(tmp_path, {"rutube": "../../etc"}, offline) is False
    assert not list((tmp_path / "videos").glob("*")) if (tmp_path / "videos").exists() else True


# --- Главная ---

def test_without_videos_the_block_keeps_its_placeholders(content_dir):
    block = reviews(site(content_dir).test_client().get("/").get_data(as_text=True))
    assert block.count("Скоро здесь будет видео") == 3
    assert "<iframe" not in block and "data-video" not in block and "Другие видео-отзывы" not in block
    assert site(content_dir).test_client().get("/video-reviews").status_code == 404


def test_homepage_shows_chosen_videos_in_saved_order_with_thumbnails(content_dir):
    write_videos(content_dir)
    set_positions(content_dir, ["c", None, "a"])
    make_thumb(content_dir, "c")
    block = reviews(site(content_dir).test_client().get("/").get_data(as_text=True))

    assert re.findall(r'<p class="review-caption">([^<]+)</p>', block) == ["Отзыв C", "Видеоотзыв клиента", "Отзыв A"]
    assert block.count("Скоро здесь будет видео") == 1
    assert "Стенд на выставке" in block
    assert f'<img class="review-thumb" src="/media/videos/{CODES["c"]}.webp"' in block
    assert block.count('class="review-thumb"') == 1                 # у A обложки нет — значок
    # Плеер не грузится вместе со страницей: ссылка на RuTube, которую скрипт превращает в плеер
    assert "<iframe" not in block and player_codes(block) == [CODES["c"], CODES["a"]]
    assert 'target="_blank" rel="noopener noreferrer"' in block


def test_more_button_opens_sheet_with_all_other_active_videos(content_dir):
    write_videos(content_dir)
    set_positions(content_dir, ["a", "b", "c"])
    client = site(content_dir).test_client()
    html = client.get("/").get_data(as_text=True)
    assert re.search(r'<a class="btn btn-quiet reviews-more" href="/video-reviews" data-route="video-reviews"[^>]*>'
                     r'Другие видео-отзывы →</a>', reviews(html))
    assert player_codes(sheet(html)) == [CODES[k] for k in "def"]  # без трёх с главной и без выключенного
    assert not set(player_codes(reviews(html))) & set(player_codes(sheet(html)))
    nav = re.search(r'<nav class="site-nav".*?</nav>', html, re.S).group(0)
    assert "video-reviews" not in nav
    page = client.get("/video-reviews")
    assert page.status_code == 200
    assert re.search(r'<section class="sheet is-open" id="video-reviews"', page.get_data(as_text=True))


def test_no_extra_videos_no_button_and_no_sheet(content_dir):
    write_videos(content_dir, {"videos": [video("a")]})
    set_positions(content_dir, ["a", None, None])
    client = site(content_dir).test_client()
    html = client.get("/").get_data(as_text=True)
    assert "Другие видео-отзывы" not in html and 'id="video-reviews"' not in html
    assert client.get("/video-reviews").status_code == 404


def test_broken_file_does_not_break_the_homepage(content_dir):
    (content_dir / "videos.json").write_text("{ не json", encoding="utf-8")
    set_positions(content_dir, ["a", None, None])
    response = site(content_dir).test_client().get("/")
    assert response.status_code == 200
    assert reviews(response.get_data(as_text=True)).count("Скоро здесь будет видео") == 3


def test_titles_are_escaped(content_dir):
    write_videos(content_dir, {"videos": [video("a", "<script>x</script>")]})
    set_positions(content_dir, ["a"])
    html = site(content_dir).test_client().get("/").get_data(as_text=True)
    assert "<script>x</script>" not in html and "&lt;script&gt;x&lt;/script&gt;" in html


def test_csp_still_allows_only_known_players_and_own_images(content_dir):
    csp = site(content_dir).test_client().get("/").headers["Content-Security-Policy"]
    assert "frame-src https://player.vimeo.com https://rutube.ru;" in csp and "img-src 'self';" in csp


# --- Отдельного раздела нет: всё в Редакторе → «Видеоотзывы» ---

def test_separate_section_is_gone(content_dir, admin):
    write_videos(content_dir)
    for path in ("/admin/videos", f"/admin/videos/{IDS['a']}", f"/admin/videos/{IDS['a']}/delete"):
        assert admin.get(path).status_code == 404, path
    assert post(admin, "/admin/videos/new", url=f"https://rutube.ru/video/{CODES['b']}/").status_code == 404
    for page in ("/admin", "/admin/editor", "/admin/exhibitions"):
        html = admin.get(page).get_data(as_text=True)
        nav = re.search(r'<nav class="admin-nav".*?</nav>', html, re.S).group(0)
        assert "Видеоотзывы" not in nav and "/admin/videos" not in html, page
    assert "Видеоотзывы" in re.findall(r"<summary>([^<]+)</summary>", admin.get("/admin/editor").get_data(as_text=True))


def test_editor_group_lists_the_existing_library_without_changing_it(content_dir, admin):
    write_videos(content_dir)
    set_positions(content_dir, ["c", None, "a"])
    make_thumb(content_dir, "b")
    before = (content_dir / "videos.json").read_bytes()
    group = video_group(admin.get("/admin/editor").get_data(as_text=True))

    assert re.findall(r'<li class="vcard[^"]*" id="v-([0-9a-f]{8})"', group) == [IDS[k] for k in "abcdefg"]
    assert "+ Добавить видео" in group and "data-vlib-count>(7)</span>" in group
    row = lambda k: group.split(f'id="v-{IDS[k]}"', 1)[1].split("</li>", 1)[0]
    assert "На главной, № 1" in row("c") and "На главной, № 3" in row("a") and "На главной" not in row("b")
    assert "Выключено" in row("g") and f'name="video.{IDS["g"]}.active" value="1">' in row("g")   # без checked
    assert '<span class="vcard-badge">RuTube</span>' in row("a") and 'class="vcard is-off"' in group
    assert f'name="video.{IDS["a"]}.active" value="1" checked>' in row("a")
    assert f'src="/media/videos/{CODES["b"]}.webp"' in row("b")
    assert f'data-video-watch="https://rutube.ru/play/embed/{CODES["a"]}/"' in row("a")
    assert f'value="{CODES["a"].join(["https://rutube.ru/video/", "/"])}"' in row("a")
    assert 'data-confirm="Удалить видео «Отзыв C» из библиотеки? Оно стоит на главной' in row("c")
    assert (content_dir / "videos.json").read_bytes() == before and not (content_dir / ".drafts").exists()


def test_add_video_in_the_editor_goes_to_the_draft_and_becomes_selectable(content_dir, admin):
    link = f"https://rutube.ru/play/embed/{CODES['a']}"
    response = draft(admin, **{"video.new.url": link, "video.new.title": "  Отзыв  клиента ",
                               "video.new.description": "Москва", "video_action": "add"})
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/editor?group=section-reviews#video-library")
    assert not (content_dir / "videos.json").exists()                          # сайт не тронут
    [saved] = drafted(content_dir)["videos"]
    vid = saved.pop("id")
    assert re.fullmatch(r"[0-9a-f]{8}", vid)
    assert saved == {"rutube": CODES["a"], "url": f"https://rutube.ru/video/{CODES['a']}/", "title": "Отзыв клиента",
                     "description": "Москва", "active": True}

    html = admin.get("/admin/editor?group=section-reviews").get_data(as_text=True)
    assert "добавлено" in html and "Черновик не опубликован" in html and "Видеоотзывы" in html
    assert '<details class="editor-group" open>\n        <summary>Видеоотзывы</summary>' in html
    group = video_group(html)
    assert re.search(rf'data-picker-card\s+data-id="{vid}"', group)             # сразу в окне выбора
    assert f'id="v-{vid}"' in group

    # Выбрать на главную → опубликовать: на сайте ролик и библиотека
    assert draft(admin, **{f"sections.{reviews_index(content_dir)}.items": vid}).status_code == 302
    assert publish(admin).status_code == 302
    assert read(content_dir, "videos")["videos"][0]["id"] == vid and positions(content_dir) == [vid, None, None]
    assert player_codes(reviews(site(content_dir).test_client().get("/").get_data(as_text=True))) == [CODES["a"]]
    assert not (content_dir / ".drafts" / "videos.json").exists()


def test_filled_add_form_is_saved_with_the_plain_save_button_too(content_dir, admin):
    assert draft(admin, **{"video.new.url": f"https://rutube.ru/video/{CODES['b']}/"}).status_code == 302
    assert [v["rutube"] for v in drafted(content_dir)["videos"]] == [CODES["b"]]
    assert draft(admin, **{"video.new.url": "", "video.new.title": ""}).status_code == 302     # пустая форма — ничего
    assert len(drafted(content_dir)["videos"]) == 1


@pytest.mark.parametrize("url", ["https://youtube.com/watch?v=1",
                                 f'<iframe src="https://rutube.ru/play/embed/{"a" * 32}"></iframe>',
                                 "javascript:alert(1)", ""])
def test_wrong_links_are_refused(content_dir, admin, url):
    response = draft(admin, **{"video.new.url": url, "video.new.title": "Мой отзыв", "video_action": "add"})
    html = response.get_data(as_text=True)
    assert response.status_code == 400 and "ссылк" in html and "RuTube" in html
    assert not (content_dir / ".drafts").exists()
    # Введённое не пропадает, форма добавления раскрыта
    assert 'value="Мой отзыв"' in html and '<details class="video-add" data-video-add open>' in html


def test_duplicates_and_long_texts_are_refused(content_dir, admin):
    write_videos(content_dir)
    dup = draft(admin, **{"video.new.url": f"https://rutube.ru/video/{CODES['a']}/", "video_action": "add"})
    assert dup.status_code == 400 and "уже есть в библиотеке" in dup.get_data(as_text=True)
    long = draft(admin, **{"video.new.url": "https://rutube.ru/video/" + "9" * 32 + "/", "video.new.title": "a" * 121,
                           "video_action": "add"})
    assert long.status_code == 400 and "не длиннее 120" in long.get_data(as_text=True)
    same = draft(admin, **row_fields("b", url=f"https://rutube.ru/video/{CODES['a']}/"))
    assert same.status_code == 400 and "уже есть в библиотеке" in same.get_data(as_text=True)
    assert not (content_dir / ".drafts").exists()


def test_new_link_keeps_the_homepage_position(content_dir, admin):
    write_videos(content_dir)
    set_positions(content_dir, ["b", "a", None])
    new = "9" * 32
    assert draft(admin, **row_fields("a", url=f"https://rutube.ru/video/{new}/", title="Новое")).status_code == 302
    assert publish(admin).status_code == 302
    assert read(content_dir, "videos")["videos"][0] == {
        "id": IDS["a"], "rutube": new, "url": f"https://rutube.ru/video/{new}/", "title": "Новое",
        "description": "Стенд на выставке", "active": True}
    block = reviews(site(content_dir).test_client().get("/").get_data(as_text=True))
    assert player_codes(block) == [CODES["b"], new]


def test_toggle_move_and_delete_in_the_editor(content_dir, admin):
    write_videos(content_dir)
    set_positions(content_dir, ["b", "a", "c"])
    field = f"sections.{reviews_index(content_dir)}.items"
    selection = {field: ",".join(IDS[k] for k in "bac")}

    # Выключить C: остаётся в библиотеке, но уходит с главной (сообщение об этом)
    response = draft(admin, **selection, **row_fields("c", active=False))
    assert response.status_code == 302
    assert drafted(content_dir)["videos"][2]["active"] is False
    assert video_lib_positions(drafted(content_dir, "home"), content_dir) == [IDS["b"], IDS["a"], None]
    assert "Отзыв C» убрано с главной" in admin.get("/admin/editor").get_data(as_text=True)
    draft(admin, **row_fields("c"))                                               # снова включить
    assert drafted(content_dir)["videos"][2]["active"] is True

    assert draft(admin, video_action=f"up:{IDS['c']}").status_code == 302
    assert [v["id"] for v in drafted(content_dir)["videos"]][:3] == [IDS["a"], IDS["c"], IDS["b"]]
    draft(admin, video_action=f"up:{IDS['a']}")                                   # первый выше не поднимается
    assert drafted(content_dir)["videos"][0]["id"] == IDS["a"]

    response = draft(admin, video_action=f"delete:{IDS['b']}", **{field: ",".join(IDS[k] for k in "ba")})
    assert response.status_code == 302
    assert IDS["b"] not in [v["id"] for v in drafted(content_dir)["videos"]]
    assert len(read(content_dir, "videos")["videos"]) == 7                        # до публикации — на сайте прежнее
    assert publish(admin).status_code == 302
    assert len(read(content_dir, "videos")["videos"]) == 6
    block = reviews(site(content_dir).test_client().get("/").get_data(as_text=True))
    assert player_codes(block) == [CODES["a"]] and block.count("Скоро здесь будет видео") == 2
    assert draft(admin, video_action="delete:99990000").status_code == 400       # уже удалено


def video_lib_positions(home, content_dir):
    return [it.get("video") for it in home["sections"][reviews_index(content_dir)]["items"]]


def test_preview_shows_the_draft_and_the_site_waits_for_publishing(content_dir, admin):
    write_videos(content_dir)
    field = f"sections.{reviews_index(content_dir)}.items"
    new = "9" * 32
    draft(admin, **{"video.new.url": f"https://rutube.ru/video/{new}/", "video.new.title": "Новый",
                    "video_action": "add"})
    vid = drafted(content_dir)["videos"][-1]["id"]
    assert draft(admin, **{field: f"{vid},{IDS['a']}"}, **row_fields("f", active=False)).status_code == 302

    preview = admin.get("/admin/editor/preview").get_data(as_text=True)
    assert player_codes(reviews(preview)) == [new, CODES["a"]]
    assert player_codes(sheet(preview)) == [CODES[k] for k in "bcde"]             # F выключен в черновике
    public = site(content_dir).test_client().get("/").get_data(as_text=True)
    assert player_codes(reviews(public)) == [] and new not in public

    # «Отменить черновик» возвращает библиотеку и выбор
    post(admin, "/admin/editor/discard")
    assert not (content_dir / ".drafts" / "videos.json").exists()
    assert len(read(content_dir, "videos")["videos"]) == 7


def test_publishing_keeps_history_of_the_library(content_dir, admin):
    write_videos(content_dir)
    draft(admin, **row_fields("a", title="Новое название"))
    assert publish(admin).status_code == 302
    history = admin.get("/admin/editor/history").get_data(as_text=True)
    assert "Видеоотзывы" in history
    [old] = (content_dir / ".history").glob("videos-*.json")
    assert json.loads(old.read_text(encoding="utf-8"))["videos"][0]["title"] == "Отзыв A"
    # Прежнюю версию можно вернуть в черновик
    assert post(admin, "/admin/editor/restore", version=old.name).status_code == 302
    assert drafted(content_dir)["videos"][0]["title"] == "Отзыв A"


def test_link_check_builds_the_player_on_the_server(content_dir, admin):
    ok = admin.get("/admin/editor/video-check", query_string={"url": f"https://rutube.ru/shorts/{CODES['a']}/"}).get_json()
    assert ok == {"ok": True, "player": f"https://rutube.ru/play/embed/{CODES['a']}/",
                  "page": f"https://rutube.ru/video/{CODES['a']}/"}
    for bad in ("https://evil.com/x", f'<iframe src="https://rutube.ru/play/embed/{CODES["a"]}">', ""):
        answer = admin.get("/admin/editor/video-check", query_string={"url": bad}).get_json()
        assert answer["ok"] is False and "player" not in answer
    guest = site(content_dir, SECRET_KEY="test-secret", ADMIN_USERNAME="admin", ADMIN_PASSWORD_HASH=PASSWORD_HASH)
    assert guest.test_client().get("/admin/editor/video-check?url=x").status_code == 302


def test_thumbnail_button_downloads_missing_cover(content_dir, admin, monkeypatch):
    write_videos(content_dir)
    calls = []

    def fake(media, v, *args):
        calls.append(v["rutube"])
        make_thumb(content_dir, "a")
        return True

    monkeypatch.setattr("app.videos.fetch_thumbnail", fake)
    assert "Получить обложку" not in admin.get("/admin/editor").get_data(as_text=True)   # скачивание выключено
    admin.application.config["VIDEO_THUMBNAILS"] = True
    assert "Получить обложку" in admin.get("/admin/editor").get_data(as_text=True)
    assert draft(admin, video_action=f"thumb:{IDS['a']}").status_code == 302
    assert calls == [CODES["a"]]
    html = admin.get("/admin/editor").get_data(as_text=True)
    row = html.split(f'id="v-{IDS["a"]}"', 1)[1].split("</li>", 1)[0]
    assert f'src="/media/videos/{CODES["a"]}.webp"' in row and "Получить обложку" not in row
    assert "Обложка видео «Отзыв A» получена" in html


def test_new_video_gets_its_cover_after_saving(content_dir, admin, monkeypatch):
    calls = []
    monkeypatch.setattr("app.videos.fetch_thumbnail", lambda media, v, *a: calls.append(v["rutube"]) or False)
    admin.application.config["VIDEO_THUMBNAILS"] = True
    draft(admin, **{"video.new.url": f"https://rutube.ru/video/{CODES['c']}/", "video_action": "add"})
    assert calls == [CODES["c"]]
    assert "Обложку с RuTube получить не удалось" in admin.get("/admin/editor").get_data(as_text=True)
    assert len(drafted(content_dir)["videos"]) == 1                               # видео всё равно добавлено


def test_posts_need_csrf_token(content_dir, admin):
    write_videos(content_dir)
    response = admin.post("/admin/editor/draft", data={**editor_versions(admin), "video_action": f"delete:{IDS['c']}"})
    assert response.status_code == 400 and not (content_dir / ".drafts").exists()


def test_enter_in_a_field_saves_the_draft_not_a_video_button(content_dir, admin):
    write_videos(content_dir)
    html = admin.get("/admin/editor").get_data(as_text=True)
    form = html.split("data-editor-form", 1)[1]
    first = re.search(r"<button[^>]*>", form).group(0)
    assert 'type="submit"' in first and "name=" not in first


# --- Выбор в Редакторе (как «Реализовано») ---

def test_editor_has_the_same_picker_for_videos(content_dir, admin):
    write_videos(content_dir)
    make_thumb(content_dir, "b")
    i = reviews_index(content_dir)
    html = admin.get("/admin/editor").get_data(as_text=True)
    group = video_group(html)
    assert f'data-picker-field="sections.{i}.items" data-picker-max="3"' in group
    assert "<h3>Видео на главной</h3>" in group and ">Добавить видеоотзывы</h3>" in group
    assert 'placeholder="Название видео"' in group and "Выбрать видео…" in group
    cards = re.findall(r'data-picker-card\s+data-id="([0-9a-f]{8})"', group)
    assert cards == [IDS[k] for k in "abcdefg"]
    assert re.search(rf'<li hidden data-picker-draft>\s*<button[^>]*\s+data-id="{IDS["g"]}"', group)   # выключенный
    assert f'src="/media/videos/{CODES["b"]}.webp"' in group and group.split("<template", 1)[0].count('<span class="picker-video-badge" aria-hidden="true">RuTube</span>') == 7
    assert 'data-event="Стенд на выставке"' in group
    for key in ("more_button", "more_title", "play_label", "placeholder"):
        assert f'name="sections.{i}.{key}"' in group, key
    # «Реализовано» — прежнее окно проектов
    assert "<h3>Проекты на главной</h3>" in html and 'placeholder="Компания или выставка"' in html


def test_scenario_select_reorder_replace_and_publish(content_dir, admin):
    """6+ роликов → выбрать три → поменять порядок → убрать один → добавить другой → после обновления
    выбор и порядок те же → главная и «Другие видео-отзывы» без повторов."""
    write_videos(content_dir)
    field = f"sections.{reviews_index(content_dir)}.items"
    # После смены выбора группа «Видеоотзывы» открыта; правка без видео — обычный возврат
    assert draft(admin, **{field: IDS["a"]}).headers["Location"].endswith("?group=section-reviews#video-library")
    post(admin, "/admin/editor/discard")
    assert draft(admin, **{field: ""}).headers["Location"].endswith("/admin/editor")
    assert choose_in_editor(admin, content_dir, "abc").status_code == 302
    assert positions(content_dir) == [IDS[k] for k in "abc"]
    assert choose_in_editor(admin, content_dir, "cab").status_code == 302          # порядок
    assert choose_in_editor(admin, content_dir, "cad").status_code == 302          # B убран, D добавлен
    assert positions(content_dir) == [IDS[k] for k in "cad"]
    assert len(read(content_dir, "videos")["videos"]) == 7                        # библиотека не тронута

    html = admin.get("/admin/editor").get_data(as_text=True)
    assert f'value="{IDS["c"]},{IDS["a"]},{IDS["d"]}" data-picker-input' in html

    page = site(content_dir).test_client().get("/").get_data(as_text=True)
    assert player_codes(reviews(page)) == [CODES[k] for k in "cad"]
    assert player_codes(sheet(page)) == [CODES[k] for k in "bef"]                 # B вернулся, G выключен


def test_fewer_than_three_leaves_placeholders(content_dir, admin):
    write_videos(content_dir)
    assert choose_in_editor(admin, content_dir, "e").status_code == 302
    assert positions(content_dir) == [IDS["e"], None, None]
    block = reviews(site(content_dir).test_client().get("/").get_data(as_text=True))
    assert player_codes(block) == [CODES["e"]] and block.count("Скоро здесь будет видео") == 2
    assert choose_in_editor(admin, content_dir, "").status_code == 302
    assert positions(content_dir) == [None, None, None]


def test_selection_stays_a_draft_until_published(content_dir, admin):
    write_videos(content_dir)
    assert choose_in_editor(admin, content_dir, "ab", publish_it=False).status_code == 302
    assert positions(content_dir) == [None, None, None]                            # на сайте прежнее
    preview = admin.get("/admin/editor/preview").get_data(as_text=True)
    assert player_codes(reviews(preview)) == [CODES["a"], CODES["b"]]


def test_more_than_three_is_refused_and_unknown_ids_are_dropped(content_dir, admin):
    write_videos(content_dir)
    field = f"sections.{reviews_index(content_dir)}.items"
    response = draft(admin, **{field: ",".join(IDS[k] for k in "abcd")})
    assert response.status_code == 400 and "не больше 3" in response.get_data(as_text=True)
    assert not (content_dir / ".drafts").exists()
    # Неизвестный или выключенный ролик на главную не попадает
    assert draft(admin, **{field: f"99990000,{IDS['g']},{IDS['a']}"}).status_code == 302
    assert video_lib_positions(drafted(content_dir, "home"), content_dir) == [IDS["a"], None, None]


# --- «+ Добавить видео» в окне выбора: ролик в черновик библиотеки, окно не закрывается ---

def add_in_picker(client, versions, **fields):
    """Как окно выбора: POST /admin/editor/video-add с отпечатком videos.json со страницы Редактора."""
    return post(client, "/admin/editor/video-add", version_videos=versions["version_videos"], **fields)


def test_picker_dialog_has_its_own_add_form(content_dir, admin):
    write_videos(content_dir)
    group = video_group(admin.get("/admin/editor").get_data(as_text=True))
    dialog = group.split("<dialog", 1)[1].split("</dialog>", 1)[0]
    assert "+ Добавить видео" in dialog and 'data-add-url="/admin/editor/video-add"' in dialog
    assert "Ссылка на видео RuTube <em>обязательно</em>" in dialog and "Короткое описание" in dialog
    assert "Сохранить видео" in dialog and "data-adder-cancel>Отмена</button>" in dialog
    add_form = dialog.split("data-picker-add>", 1)[1].split("data-picker-add-done", 1)[0]
    assert "name=" not in add_form                         # форма Редактора эти поля не отправляет
    assert " required" not in add_form                     # пустое скрытое поле не блокирует «Сохранить черновик»
    # Источник — ссылка RuTube или файл; обложка — картинка или кадр
    assert 'data-adder-source="rutube"' in add_form and 'data-adder-source="file"' in add_form
    assert 'accept="video/mp4,video/webm,.mp4,.m4v,.webm"' in add_form and "Взять текущий кадр" in add_form
    assert "<template data-picker-card-template>" in dialog
    # Отдельной страницы или пункта меню для видео по-прежнему нет
    nav = re.search(r'<nav class="admin-nav".*?</nav>', admin.get("/admin/editor").get_data(as_text=True), re.S).group(0)
    assert "Видеоотзывы" not in nav and admin.get("/admin/videos").status_code == 404


def test_add_from_picker_keeps_selection_and_order_then_publishes(content_dir, admin):
    write_videos(content_dir)
    set_positions(content_dir, ["c", "a", None])
    versions = editor_versions(admin)
    new = "9" * 32
    answer = add_in_picker(admin, versions, url=f"https://rutube.ru/play/embed/{new}", title="  Отзыв  из окна ",
                           description="Экспоцентр")
    assert answer.status_code == 200
    result = answer.get_json()
    vid = result["video"]["id"]
    assert result["ok"] and re.fullmatch(r"[0-9a-f]{8}", vid)
    assert result["video"] == {"id": vid, "title": "Отзыв из окна", "description": "Экспоцентр", "thumb": "",
                               "source": "RuTube"}
    assert f'id="v-{vid}"' in result["card"] and "Отзыв из окна" in result["card"]   # карточка для библиотеки

    # Только черновик библиотеки: выбор на главной, home.json и сайт не тронуты
    assert drafted(content_dir)["videos"][-1] == {"id": vid, "rutube": new, "url": f"https://rutube.ru/video/{new}/",
                                                  "title": "Отзыв из окна", "description": "Экспоцентр", "active": True}
    assert not (content_dir / ".drafts" / "home.json").exists()
    assert len(read(content_dir, "videos")["videos"]) == 7 and positions(content_dir) == [IDS["c"], IDS["a"], None]
    page = admin.get("/admin/editor").get_data(as_text=True)
    assert re.search(rf'data-picker-card\s+data-id="{vid}"', video_group(page))
    assert result["version"] == editor_versions(admin)["version_videos"]

    # «Готово» с прежним порядком + новый ролик → «Сохранить черновик» с отпечатком из ответа
    field = f"sections.{reviews_index(content_dir)}.items"
    response = post(admin, "/admin/editor/draft", **{**versions, "version_videos": result["version"]},
                    **{field: f"{IDS['c']},{IDS['a']},{vid}"})
    assert response.status_code == 302
    assert video_lib_positions(drafted(content_dir, "home"), content_dir) == [IDS["c"], IDS["a"], vid]
    preview = admin.get("/admin/editor/preview").get_data(as_text=True)
    assert player_codes(reviews(preview)) == [CODES["c"], CODES["a"], new]
    public = site(content_dir).test_client().get("/").get_data(as_text=True)
    assert player_codes(reviews(public)) == [CODES["c"], CODES["a"]] and new not in public    # ждёт публикации

    assert publish(admin).status_code == 302
    assert positions(content_dir) == [IDS["c"], IDS["a"], vid] and read(content_dir, "videos")["videos"][-1]["id"] == vid
    assert player_codes(reviews(site(content_dir).test_client().get("/").get_data(as_text=True))) == \
        [CODES["c"], CODES["a"], new]
    # Прежняя библиотека — в истории версий
    assert any(len(json.loads(p.read_text(encoding="utf-8"))["videos"]) == 7
               for p in (content_dir / ".history").glob("videos-*.json"))


def test_add_from_picker_keeps_an_existing_draft(content_dir, admin):
    write_videos(content_dir)
    draft(admin, **row_fields("a", title="Правка в черновике"))
    versions = editor_versions(admin)
    assert add_in_picker(admin, versions, url=f"https://rutube.ru/video/{'8' * 32}/").status_code == 200
    library = drafted(content_dir)["videos"]
    assert library[0]["title"] == "Правка в черновике" and library[-1]["title"] == ""
    assert add_in_picker(admin, versions, url=f"https://rutube.ru/video/{'7' * 32}/").status_code == 409   # устаревший отпечаток
    # Опубликовать: основа черновика — прежняя опубликованная версия, публикация проходит
    assert publish(admin).status_code == 302 and len(read(content_dir, "videos")["videos"]) == 8


@pytest.mark.parametrize("url", ["https://youtube.com/watch?v=1", "https://vimeo.com/123456",
                                 f'<iframe src="https://rutube.ru/play/embed/{"a" * 32}"></iframe>',
                                 "javascript:alert(1)", "   "])
def test_add_from_picker_refuses_wrong_links(content_dir, admin, url):
    answer = add_in_picker(admin, editor_versions(admin), url=url, title="Отзыв")
    result = answer.get_json()
    assert answer.status_code == 400 and result["ok"] is False and result["field"] == "url" and "RuTube" in result["message"]
    assert not (content_dir / ".drafts").exists()


def test_add_from_picker_refuses_duplicates_long_titles_and_missing_token(content_dir, admin):
    write_videos(content_dir)
    versions = editor_versions(admin)
    dup = add_in_picker(admin, versions, url=f"https://rutube.ru/shorts/{CODES['b']}/")
    assert dup.status_code == 400 and "уже есть" in dup.get_json()["message"]
    long = add_in_picker(admin, versions, url=f"https://rutube.ru/video/{'9' * 32}/", title="x" * 121)
    assert long.status_code == 400 and long.get_json()["field"] == "title"
    no_token = admin.post("/admin/editor/video-add", data={**versions, "url": f"https://rutube.ru/video/{'9' * 32}/"})
    assert no_token.status_code == 400
    assert not (content_dir / ".drafts").exists()
    guest = site(content_dir, SECRET_KEY="test-secret", ADMIN_USERNAME="admin", ADMIN_PASSWORD_HASH=PASSWORD_HASH)
    assert guest.test_client().get("/admin/editor/video-add").status_code in (302, 405)


def test_new_video_from_picker_gets_its_cover(content_dir, admin, monkeypatch):
    calls = []

    def fake(media, v, *args):
        calls.append(v["rutube"])
        make_thumb(content_dir, "c")
        return True

    monkeypatch.setattr("app.videos.fetch_thumbnail", fake)
    admin.application.config["VIDEO_THUMBNAILS"] = True
    result = add_in_picker(admin, editor_versions(admin), url=f"https://rutube.ru/video/{CODES['c']}/").get_json()
    assert calls == [CODES["c"]] and result["video"]["thumb"] == f"/media/videos/{CODES['c']}.webp"
    assert result["video"]["title"] == "Видео без названия"


# --- Предпросмотр Редактора: щелчок по видео открывает окно выбора, а не плеер ---

def test_preview_video_cards_open_the_picker_like_projects(content_dir, admin):
    write_videos(content_dir)
    i = reviews_index(content_dir)
    mark = f'data-edit="sections.{i}.items"'
    # Позиции пусты — заглушки; карточка целиком с меткой окна выбора, без меток текстовых полей внутри
    block = reviews(admin.get("/admin/editor/preview").get_data(as_text=True))
    assert block.count(f'<li class="review-card" {mark}>') == 3
    assert f'data-edit="sections.{i}.placeholder"' not in block and f'data-edit="sections.{i}.caption"' not in block

    choose_in_editor(admin, content_dir, "ab", publish_it=False)
    preview = admin.get("/admin/editor/preview").get_data(as_text=True)
    cards = re.findall(r'<li class="review-card"[^>]*>', reviews(preview))
    assert cards == [f'<li class="review-card" {mark}>'] * 3                 # два ролика и заглушка
    assert re.findall(r'<li class="review-card"[^>]*>', sheet(preview)) == [f'<li class="review-card" {mark}>'] * 4

    # На сайте меток нет — карточки играют видео как раньше
    public = site(content_dir).test_client().get("/").get_data(as_text=True)
    assert "data-edit" not in public


def test_player_ignores_clicks_the_editor_already_handled():
    js = (STATIC / "js" / "main.js").read_text(encoding="utf-8")
    handler = re.search(r"function initVideoReviews\(\) \{.*?\n  \}\n", js, re.S).group(0)
    assert "event.defaultPrevented" in handler
