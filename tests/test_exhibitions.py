"""Справочник выставок (событие = название + год), дерево проектов админки, поиск и фильтры.

«Мебель» 2025 и «Мебель» 2026 — разные события; « мебель » и «Мебель» в одном году — одно.
Проект по-прежнему хранит название выставки и год — сайт и старые данные работают без миграции.
"""

import hashlib
import json
import re

import pytest

from app.exhibitions import directory, event_key, matches, name_options, normalize, resolve, tree
from app.portfolio import build_years, published_projects
from tests.test_image_sizes import PASSWORD, TOKEN_RE, make_app, photo_bytes
from tests.test_project_dates import REQUIRED, create, edit, projects


@pytest.fixture
def content_dir(make_content):
    return make_content()


@pytest.fixture
def admin(content_dir):
    client = make_app(content_dir).test_client()
    token = TOKEN_RE.search(client.get("/admin/login").get_data(as_text=True)).group(1)
    client.post("/admin/login", data={"csrf_token": token, "username": "admin", "password": PASSWORD})
    return client


def by_title(content_dir, title):
    return next(p for p in projects(content_dir) if p["title"] == title)


def stored(content_dir):
    path = content_dir / "exhibitions.json"
    return json.loads(path.read_text(encoding="utf-8"))["exhibitions"] if path.exists() else []


def token(client, path="/admin/exhibitions"):
    return TOKEN_RE.search(client.get(path).get_data(as_text=True)).group(1)


def add_exhibition(client, name, year):
    return client.post("/admin/exhibitions", data={"csrf_token": token(client), "name": name, "year": year})


def state(content_dir):
    return {str(p.relative_to(content_dir)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(content_dir.rglob("*")) if p.is_file() and ".cache" not in p.parts and p.name != ".lock"}


def proj(pid, year=None, month=None, exhibition="", title=None, status="published"):
    p = {"id": pid, "status": status, "title": title or pid, "exhibition": exhibition,
         "photos": ["static/img/stand/frame-159.webp"]}
    if year is not None:
        p["year"] = year
    if month is not None:
        p["month"] = month
    return p


# --- Логика справочника ---

def test_same_name_same_year_is_one_event_regardless_of_case_and_spaces():
    assert normalize("  Мебель   Экспо ") == normalize("мебель экспо") == normalize("МЕБЕЛЬ ЭКСПО")
    assert event_key(" мебель ", 2026) == event_key("Мебель", 2026)
    assert event_key("Мебель", 2025) != event_key("Мебель", 2026)
    assert event_key("", 2026) == "" and event_key("Мебель", None) == ""


def test_directory_merges_stored_and_project_exhibitions_by_name_and_year():
    events = directory([proj("a", 2026, 10, "мебель "), proj("b", 2025, 3, "Мебель"), proj("c", 2026, 9, "МЕБЕЛЬ")],
                       [{"name": "Мебель", "year": 2026}, {"name": "Связь", "year": 2026}])

    assert [(e["name"], e["year"], [p["id"] for p in e["projects"]]) for e in events] == [
        ("Мебель", 2026, ["a", "c"]), ("Связь", 2026, []), ("Мебель", 2025, ["b"])]


def test_resolve_reuses_existing_event_only_in_the_same_year():
    events = directory([proj("a", 2025, 3, "Мебель")], [{"name": "Связь", "year": 2026}])

    assert resolve("  мебель ", 2025, events) == ("Мебель", False)        # та же выставка — без повторного ввода
    assert resolve("мебель", 2026, events) == ("мебель", True)           # другой год — новое событие
    assert resolve("СВЯЗЬ", 2026, events) == ("Связь", False)
    assert name_options(events) == [("Мебель", "2025"), ("Связь", "2026")]


def test_tree_goes_year_month_exhibition_project():
    items = [proj("p1", 2026, 10, "Выставка A", "Проект 2"), proj("p2", 2026, 10, "Выставка A", "Проект 1"),
             proj("p3", 2026, 10, "Выставка B", "Проект 3"), proj("p4", 2026, 9, "Выставка C", "Проект 4"),
             proj("p5", 2025, 12, "Выставка A", "Проект 5"), proj("p6", 2026, None, "", "Старый"),
             proj("p7", None, None, "", "Без года")]

    shape = [(y["label"], [(m["label"], [(e["name"], [p["title"] for p in e["projects"]]) for e in m["events"]])
                           for m in y["months"]]) for y in tree(items)]

    assert shape == [
        ("2026", [("Октябрь", [("Выставка A", ["Проект 1", "Проект 2"]), ("Выставка B", ["Проект 3"])]),
                  ("Сентябрь", [("Выставка C", ["Проект 4"])]),
                  ("Месяц не указан", [("Без выставки", ["Старый"])])]),
        ("2025", [("Декабрь", [("Выставка A", ["Проект 5"])])]),
        ("Без даты", [("Месяц не указан", [("Без выставки", ["Без года"])])]),
    ]
    # «Выставка A» 2026 и 2025 — разные группы
    assert tree(items)[0]["months"][0]["events"][0]["key"] != tree(items)[1]["months"][0]["events"][0]["key"]


def test_december_of_last_year_goes_after_january_of_this_year():
    items = [proj("dec", 2025, 12), proj("jan", 2026, 1), proj("mar", 2026, 3), proj("nov", 2025, 11)]

    assert [p["id"] for p in published_projects(items)] == ["mar", "jan", "dec", "nov"]
    assert [(y["year"], [m["month"] for m in y["months"]]) for y in tree(items)] == [(2026, [3, 1]), (2025, [12, 11])]


def test_public_order_does_not_depend_on_exhibition_names():
    items = [proj("a", 2026, 3, "Ааа"), proj("b", 2026, 10, "Яяя"), proj("c", 2025, 12, "Ааа")]

    year = build_years(items, 2026, "/nonexistent", first_year=2025)[0]
    assert [g["name"] for g in year["groups"]] == ["Яяя", "Ааа"]           # октябрь выше марта


def test_matches_search_and_filters():
    p = proj("x", 2026, 10, "Мебель Экспо", "Агрос. Трансфаир")

    assert matches(p, "трансфаир") and matches(p, "МЕБЕЛЬ") and not matches(p, "кухни")
    assert matches(p, year=2026, month=10, event=event_key("мебель экспо", 2026))
    assert not matches(p, year=2025) and not matches(p, month=9) and not matches(p, event=event_key("Мебель Экспо", 2025))


# --- Справочник в админке ---

def test_new_exhibition_is_created_and_shown(admin, content_dir):
    response = add_exhibition(admin, "  Мебель   2026 ", "2026")

    assert response.status_code == 302
    assert stored(content_dir) == [{"name": "Мебель 2026", "year": 2026}]
    html = admin.get("/admin/exhibitions").get_data(as_text=True)
    assert "Мебель 2026" in html


def test_duplicate_exhibition_in_same_year_is_refused(admin, content_dir):
    add_exhibition(admin, "Мебель", "2026")

    response = add_exhibition(admin, "  МЕБЕЛЬ ", "2026")

    assert response.status_code == 400 and "уже есть" in response.get_data(as_text=True)
    assert stored(content_dir) == [{"name": "Мебель", "year": 2026}]


def test_same_exhibition_name_in_another_year_is_a_separate_event(admin, content_dir):
    add_exhibition(admin, "Мебель", "2025")
    add_exhibition(admin, "Мебель", "2026")

    assert stored(content_dir) == [{"name": "Мебель", "year": 2025}, {"name": "Мебель", "year": 2026}]


@pytest.mark.parametrize("name, year", [("", "2026"), ("x" * 201, "2026"), ("Мебель", "1800"), ("Мебель", "abc")])
def test_bad_exhibition_is_refused(admin, content_dir, name, year):
    before = state(content_dir)

    assert add_exhibition(admin, name, year).status_code == 400
    assert state(content_dir) == before


def test_project_reuses_existing_exhibition_of_the_same_year(admin, content_dir):
    add_exhibition(admin, "Мебель", "2026")

    create(admin, title="Первый", exhibition=" мебель ", year="2026")
    create(admin, title="Второй", exhibition="МЕБЕЛЬ", year="2026")

    assert by_title(content_dir, "Первый")["exhibition"] == by_title(content_dir, "Второй")["exhibition"] == "Мебель"
    assert stored(content_dir) == [{"name": "Мебель", "year": 2026}]         # дубля нет


def test_project_with_same_exhibition_name_in_other_year_is_separate(admin, content_dir):
    create(admin, title="Прошлый", exhibition="Мебель", year="2025")
    create(admin, title="Нынешний", exhibition="мебель", year="2026")

    events = {(e["name"], e["year"]): [p["title"] for p in e["projects"]]
              for e in directory(projects(content_dir), stored(content_dir))}
    assert events[("Мебель", 2025)] == ["Прошлый"] and events[("мебель", 2026)] == ["Нынешний"]
    html = admin.get("/admin/exhibitions").get_data(as_text=True)
    assert html.count(">Мебель<") + html.count(">мебель<") == 2


def test_new_exhibition_from_project_form_is_announced(admin, content_dir):
    response = create(admin, title="Стенд", exhibition="Связь", year="2026")

    page = admin.get(response.headers["Location"]).get_data(as_text=True)
    assert "Новая выставка «Связь» (2026)" in page
    assert any(e["name"] == "Связь" and e["year"] == 2026
               for e in directory(projects(content_dir), stored(content_dir)))


def test_project_form_suggests_existing_exhibitions(admin, content_dir):
    add_exhibition(admin, "Мебель", "2025")
    add_exhibition(admin, "Мебель", "2026")

    html = admin.get("/admin/projects/new").get_data(as_text=True)

    assert re.search(r'<input name="exhibition"[^>]*list="exhibition-list"', html)
    assert '<option value="Мебель" label="2026, 2025"></option>' in html


def test_exhibitions_page_lists_projects_and_links_to_filter(admin, content_dir):
    create(admin, title="Стенд А", exhibition="Связь", year="2026")

    html = admin.get("/admin/exhibitions").get_data(as_text=True)

    assert "Стенд А" in html and 'href="/admin?event=2026:%D1%81%D0%B2%D1%8F%D0%B7%D1%8C"' in html


def test_empty_stored_exhibition_can_be_removed_but_used_one_cannot(admin, content_dir):
    add_exhibition(admin, "Пустая", "2026")
    create(admin, title="Стенд", exhibition="Занятая", year="2026")
    add_exhibition(admin, "Занятая", "2025")

    admin.post("/admin/exhibitions/delete", data={"csrf_token": token(admin), "key": "2026:пустая"})
    assert stored(content_dir) == [{"name": "Занятая", "year": 2025}]

    response = admin.post("/admin/exhibitions/delete", data={"csrf_token": token(admin), "key": "2026:занятая"})
    assert response.status_code == 400 and by_title(content_dir, "Стенд")["exhibition"] == "Занятая"


def test_exhibitions_need_login_and_csrf(content_dir, admin):
    anonymous = make_app(content_dir).test_client()
    assert anonymous.get("/admin/exhibitions").status_code == 302
    before = state(content_dir)

    assert admin.post("/admin/exhibitions", data={"csrf_token": "forged", "name": "X", "year": "2026"}).status_code == 400
    assert admin.post("/admin/exhibitions/delete", data={"csrf_token": "forged", "key": "x"}).status_code == 400
    assert state(content_dir) == before


# --- Список проектов: дерево, поиск, фильтры ---

def tree_html(client, query=""):
    return client.get("/admin" + query).get_data(as_text=True)


CREATED = {"Альфа", "Бета", "Гамма", "Дельта", "Ёж"}


def titles(html):
    """Названия проектов в списке — из созданных в тесте (в тестовом контенте есть и свои проекты)."""
    return [t for t in re.findall(r'class="project-title"[^>]*>([^<]+)</a>', html) if t in CREATED]


@pytest.fixture
def filled(admin, content_dir):
    for title, exhibition, year, month in [("Альфа", "Мебель", "2026", "10"), ("Бета", "Связь", "2026", "10"),
                                           ("Гамма", "Мебель", "2026", "9"), ("Дельта", "Мебель", "2025", "12"),
                                           ("Ёж", "Мебель", "2026", "1")]:
        create(admin, title=title, exhibition=exhibition, year=year, month=month)
    return admin


def test_project_list_nests_year_month_exhibition(filled):
    html = tree_html(filled)

    y26, y25 = html.index('id="y-2026"'), html.index('id="y-2025"')
    oct26, sep26, jan26 = html.index('id="m-2026-10"'), html.index('id="m-2026-09"'), html.index('id="m-2026-01"')
    dec25 = html.index('id="m-2025-12"')
    assert y26 < oct26 < sep26 < jan26 < y25 < dec25                       # декабрь 2025 — ниже января 2026
    october = html[oct26:sep26]
    assert october.index(">Мебель<") < october.index("Альфа") < october.index(">Связь<") < october.index("Бета")
    assert re.search(r'<details class="year-group" open id="y-2026"', html)
    assert re.search(r'<details class="month-group" open id="m-2026-10"', html)


def test_search_by_title_and_exhibition(filled):
    assert titles(tree_html(filled, "?q=альф")) == ["Альфа"]
    assert set(titles(tree_html(filled, "?q=  МЕБЕЛЬ "))) == {"Альфа", "Гамма", "Дельта", "Ёж"}
    assert "Ничего не найдено" in tree_html(filled, "?q=кухни")


def test_filters_by_year_month_and_exhibition(filled):
    assert set(titles(tree_html(filled, "?year=2026"))) == {"Альфа", "Бета", "Гамма", "Ёж"}
    assert set(titles(tree_html(filled, "?year=2026&month=10"))) == {"Альфа", "Бета"}
    assert set(titles(tree_html(filled, "?month=12"))) == {"Дельта"}
    # Одноимённая выставка другого года не попадает в фильтр
    assert set(titles(tree_html(filled, "?event=2025%3A%D0%BC%D0%B5%D0%B1%D0%B5%D0%BB%D1%8C"))) == {"Дельта"}


def test_bad_filter_values_do_not_break_the_list(filled):
    for query in ("?year=abc", "?month=13", "?month=-1", "?event=<script>", "?q=" + "x" * 500):
        response = filled.get("/admin" + query)
        assert response.status_code == 200 and "<script>" not in response.get_data(as_text=True).split("<main", 1)[1]


def test_older_years_start_collapsed_unless_filtering(filled):
    assert re.search(r'<details class="year-group" id="y-2025"', tree_html(filled))
    assert re.search(r'<details class="year-group" open id="y-2025"', tree_html(filled, "?q=дельта"))
    assert re.search(r'<details class="year-group" open id="y-2025"', tree_html(filled, "?open=all"))


def test_filter_form_offers_events_grouped_by_year(filled):
    html = tree_html(filled)

    for year in ("2026", "2025"):
        group = html.split(f'<optgroup label="{year}">', 1)[1].split("</optgroup>", 1)[0]
        assert f'<option value="{year}:мебель">Мебель</option>' in group
    assert html.index('<optgroup label="2026">') < html.index('<optgroup label="2025">')


# --- Сохранность ---

def test_editing_keeps_photos_and_exhibitions_file(admin, content_dir):
    add_exhibition(admin, "Мебель", "2026")
    create(admin, title="Стенд", exhibition="Мебель", year="2026",
           files=[("1.jpg", photo_bytes((900, 600))), ("2.jpg", photo_bytes((1000, 700)))])
    p = by_title(content_dir, "Стенд")
    files_before = {k: v for k, v in state(content_dir).items() if f"portfolio/{p['id']}/" in k}
    expo_before = (content_dir / "exhibitions.json").read_bytes()

    edit(admin, p["id"], title="Стенд", exhibition="мебель", year="2026", month="11", area="30")

    after = by_title(content_dir, "Стенд")
    assert after["photos"] == p["photos"] and after["cover"] == p["cover"] and after["exhibition"] == "Мебель"
    assert {k: v for k, v in state(content_dir).items() if f"portfolio/{p['id']}/" in k} == files_before
    assert (content_dir / "exhibitions.json").read_bytes() == expo_before


def test_deleting_project_keeps_photos_of_similar_ids(admin, content_dir):
    for _ in range(2):
        create(admin, title="Стенд", exhibition="Мебель", year="2026", files=[("1.jpg", photo_bytes((900, 600)))])
    ids = sorted(p["id"] for p in projects(content_dir) if p["title"] == "Стенд")
    keep = {k: v for k, v in state(content_dir).items() if f"portfolio/{ids[1]}/" in k}
    assert keep

    admin.post(f"/admin/projects/{ids[0]}/delete", data={"csrf_token": token(admin, f"/admin/projects/{ids[0]}")})

    assert [p["id"] for p in projects(content_dir) if p["title"] == "Стенд"] == [ids[1]]
    assert {k: v for k, v in state(content_dir).items() if f"portfolio/{ids[1]}/" in k} == keep
    assert not (content_dir / "media" / "portfolio" / ids[0]).exists()


def test_check_content_accepts_and_validates_exhibitions_file(content_dir):
    from app.content import validate_content

    static = str(make_app(content_dir).static_folder)
    (content_dir / "exhibitions.json").write_text(json.dumps({"exhibitions": [{"name": "Мебель", "year": 2026}]}),
                                                  encoding="utf-8")
    assert validate_content(content_dir, static) == []

    (content_dir / "exhibitions.json").write_text(json.dumps({"exhibitions": [
        {"name": "Мебель", "year": 2026}, {"name": " мебель", "year": 2026}, {"name": "", "year": "x"}]}),
        encoding="utf-8")
    errors = validate_content(content_dir, static)
    assert any("повторяется" in e for e in errors) and any("name" in e for e in errors)


def test_public_portfolio_has_no_month_or_admin_blocks(admin, content_dir):
    create(admin, title="Стенд", exhibition="Мебель", year="2026", month="10",
           files=[("1.jpg", photo_bytes((900, 600)))], publish="on")

    html = make_app(content_dir).test_client().get("/portfolio").get_data(as_text=True)

    assert "Стенд" in html
    for admin_only in ("month-group", "year-group", "Октябрь", "/admin/exhibitions", "Без выставки"):
        assert admin_only not in html


def test_required_fields_stay_required(admin, content_dir):
    before = state(content_dir)

    response = create(admin, exhibition="   ")

    assert response.status_code == 400 and "Укажите выставку" in response.get_data(as_text=True)
    assert state(content_dir) == before
    assert {k for k in REQUIRED} == {"title", "exhibition", "year", "month", "area", "layout"}
