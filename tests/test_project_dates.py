"""Форма проекта из семи полей, группировка по месяцам в админке, порядок портфолио по дате.

Обязательные: название, выставка, год, месяц, площадь (м², дробная); необязательные: описание,
видео (Vimeo). Старые поля (город, категория, характеристики, «Пример», order) форма не показывает
и не стирает. Админка группирует по «месяц + год» от новых к старым; сайт — без групп по месяцам,
но в том же хронологическом порядке.
"""

import hashlib
import io
import json
import re
from datetime import date

import pytest

from app.portfolio import build_years, chronological_key, format_area, published_projects
from tests.test_image_sizes import PASSWORD, TOKEN_RE, make_app, photo_bytes

THIS_YEAR = date.today().year


@pytest.fixture
def content_dir(make_content):
    return make_content()


def logged_in(content_dir):
    client = make_app(content_dir).test_client()
    token = TOKEN_RE.search(client.get("/admin/login").get_data(as_text=True)).group(1)
    client.post("/admin/login", data={"csrf_token": token, "username": "admin", "password": PASSWORD})
    return client


@pytest.fixture
def admin(content_dir):
    return logged_in(content_dir)


REQUIRED = {"title": "Агрос. Трансфаир", "exhibition": "Трансфаир", "year": "2026", "month": "10", "area": "40,5",
            "layout": "Угловой"}


def token(client, path="/admin/projects/new"):
    return TOKEN_RE.search(client.get(path).get_data(as_text=True)).group(1)


def create(client, files=(), **fields):
    form = {"csrf_token": token(client), **REQUIRED, **fields,
            "photos": [(io.BytesIO(data), name) for name, data in files]}
    form = {k: v for k, v in form.items() if v is not None}
    return client.post("/admin/projects/new", content_type="multipart/form-data", data=form)


def edit(client, project_id, **fields):
    data = {"csrf_token": token(client, f"/admin/projects/{project_id}"), **REQUIRED, **fields}
    return client.post(f"/admin/projects/{project_id}", data={k: v for k, v in data.items() if v is not None})


def projects(content_dir):
    return json.loads((content_dir / "portfolio.json").read_text(encoding="utf-8"))["projects"]


def by_title(content_dir, title):
    return next(p for p in projects(content_dir) if p["title"] == title)


def state(content_dir):
    return {str(p.relative_to(content_dir)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(content_dir.rglob("*")) if p.is_file() and ".cache" not in p.parts and p.name != ".lock"}


# --- Форма ---

def test_form_has_only_the_seven_fields_and_photos(admin):
    html = admin.get("/admin/projects/new").get_data(as_text=True)
    names = set(re.findall(r'<(?:input|select|textarea)[^>]* name="([a-z_]+)"', html)) - {"csrf_token"}

    assert names == {"title", "exhibition", "year", "month", "area", "layout", "floors", "description", "video",
                     "photos", "publish"}
    assert "Название компании / стенда" in html and "Площадь, м²" in html
    assert re.search(r'<select name="year"[^>]*required', html) and re.search(r'<select name="month"[^>]*required', html)
    assert '<option value="10">Октябрь</option>' in html
    for gone in ("city", "category", "spec_label", "example"):
        assert f'name="{gone}"' not in html


def test_project_with_five_required_fields_is_created(admin, content_dir):
    response = create(admin)

    p = by_title(content_dir, "Агрос. Трансфаир")
    assert response.status_code == 302
    assert (p["exhibition"], p["year"], p["month"], p["area"]) == ("Трансфаир", 2026, 10, 40.5)
    assert p["description"] == "" and p["video"] == ""
    assert p["status"] == "draft"                                   # без фото не публикуется


def test_optional_fields_are_saved(admin, content_dir):
    create(admin, description="Двухэтажный стенд\nс подиумом", video="https://vimeo.com/76979871")

    p = by_title(content_dir, "Агрос. Трансфаир")
    assert p["description"] == "Двухэтажный стенд\nс подиумом" and p["video"] == "https://vimeo.com/76979871"


@pytest.mark.parametrize("field, message", [
    ("title", "Укажите название"), ("exhibition", "Укажите выставку"), ("year", "Год"),
    ("month", "Месяц"), ("area", "Площадь"),
])
def test_each_required_field_is_checked(admin, content_dir, field, message):
    before = state(content_dir)

    response = create(admin, **{field: ""})

    assert response.status_code == 400 and message in response.get_data(as_text=True)
    assert state(content_dir) == before


@pytest.mark.parametrize("raw, stored", [("40", 40), ("40.5", 40.5), ("40,5", 40.5), (" 12,25 ", 12.25), ("0,5", 0.5),
                                         ("1500", 1500)])
def test_area_accepts_whole_and_fractional_numbers(admin, content_dir, raw, stored):
    create(admin, area=raw)

    area = by_title(content_dir, "Агрос. Трансфаир")["area"]
    assert area == stored and isinstance(area, int) == isinstance(stored, int)


@pytest.mark.parametrize("raw", ["-5", "0", "abc", "40 м2", "1e3", "40.555", "40,5,1", "100001"])
def test_bad_area_is_rejected(admin, content_dir, raw):
    response = create(admin, area=raw)

    assert response.status_code == 400 and "Площадь" in response.get_data(as_text=True)


@pytest.mark.parametrize("year, month", [("1800", "10"), (str(THIS_YEAR + 2), "10"), ("abc", "10"), ("2026", "0"),
                                         ("2026", "13"), ("2026", "x"), ("2026", "10.5")])
def test_bad_year_or_month_is_rejected(admin, content_dir, year, month):
    response = create(admin, year=year, month=month)

    assert response.status_code == 400


def test_photos_in_the_same_form_still_work(admin, content_dir):
    create(admin, files=[("1.jpg", photo_bytes((900, 600))), ("2.jpg", photo_bytes((1000, 700)))], publish="on")

    p = by_title(content_dir, "Агрос. Трансфаир")
    assert p["status"] == "published" and len(p["photos"]) == 2
    assert len(list((content_dir / "originals" / "portfolio" / p["id"]).iterdir())) == 2


# --- Редактирование и старые данные ---

def test_editing_keeps_hidden_old_fields_and_photos(admin, content_dir):
    pid = "test-arara"                                              # старый проект: город, категория, фото
    before = next(p for p in projects(content_dir) if p["id"] == pid)
    files_before = {k: v for k, v in state(content_dir).items() if f"portfolio/{pid}/" in k}

    response = edit(admin, pid, title="Арара", exhibition="Ярмарка", year="2025", month="3", area="54")

    after = next(p for p in projects(content_dir) if p["id"] == pid)
    assert response.status_code == 302
    assert (after["title"], after["exhibition"], after["year"], after["month"], after["area"]) == \
        ("Арара", "Ярмарка", 2025, 3, 54)
    for old in ("city", "category", "specs", "example", "photos", "cover", "status"):
        assert after.get(old) == before.get(old), old
    assert {k: v for k, v in state(content_dir).items() if f"portfolio/{pid}/" in k} == files_before


def test_edit_form_prefills_area_from_old_spec(admin, content_dir):
    path = content_dir / "portfolio.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    p = next(p for p in data["projects"] if p["id"] == "test-arara")
    p["specs"] = [{"label": "Площадь", "value": "19,5"}]
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    html = admin.get("/admin/projects/test-arara").get_data(as_text=True)

    assert re.search(r'name="area"[^>]*value="19,5"', html)


def test_other_projects_are_not_touched(admin, content_dir):
    others = {p["id"]: p for p in projects(content_dir)}
    files = state(content_dir)

    create(admin)
    edit(admin, "test-arara", title="Арара", exhibition="Ярмарка", year="2025", month="3", area="54")

    after = {p["id"]: p for p in projects(content_dir)}
    for pid, p in others.items():
        if pid != "test-arara":
            assert after[pid] == p
    assert all(state(content_dir).get(k) == v for k, v in files.items() if not k.endswith("portfolio.json"))


# --- Группировка в админке ---

def admin_groups(client):
    """[(id группы месяца, название, число)] и названия проектов в каждой группе — по разметке списка."""
    html = client.get("/admin").get_data(as_text=True)
    groups = re.findall(r'<details class="month-group"[^>]*id="(m-[^"]+)"[^>]*>\s*<summary>\s*'
                        r'<span class="group-name">([^<]+)</span>\s*<span class="group-count"[^>]*>(\d+)</span>', html)
    titles = {gid: re.findall(r'class="project-title"[^>]*>([^<]+)</a>',
                              html.split(f'id="{gid}"', 1)[1].split('<details class="month-group"', 1)[0]
                              .split('<details class="year-group"', 1)[0]) for gid, _, _ in groups}
    return html, groups, titles


def test_admin_groups_by_month_newest_first_sorted_by_title(admin, content_dir):
    for title, year, month in [("Бета", "2026", "9"), ("Альфа", "2026", "10"), ("Гамма", "2026", "10"),
                               ("Дельта", "2026", "8"), ("Ёлка", "2025", "12")]:
        create(admin, title=title, year=year, month=month)

    html, groups, titles = admin_groups(admin)
    dated = [(gid, name, int(n)) for gid, name, n in groups if gid in ("m-2026-10", "m-2026-09", "m-2026-08", "m-2025-12")]
    assert dated == [("m-2026-10", "Октябрь", 2), ("m-2026-09", "Сентябрь", 1),
                     ("m-2026-08", "Август", 1), ("m-2025-12", "Декабрь", 1)]
    assert titles["m-2026-10"] == ["Альфа", "Гамма"]                 # одна выставка — по названию
    # Проекты без месяца (старые) — своей группой после месяцев своего года
    ids = [gid for gid, _, _ in groups]
    assert ids.index("m-2026-08") < ids.index("m-2026-none") < ids.index("m-2025-12")
    assert re.search(r'<details class="month-group" open id="m-2026-10"', html)


def test_changing_month_moves_project_to_another_group(admin, content_dir):
    create(admin, title="Альфа", month="10")
    pid = by_title(content_dir, "Альфа")["id"]

    edit(admin, pid, title="Альфа", month="7")

    _, groups, titles = admin_groups(admin)
    assert "Альфа" in titles["m-2026-07"]
    assert "m-2026-10" not in titles or "Альфа" not in titles["m-2026-10"]


def test_admin_list_survives_projects_without_year(admin, content_dir):
    path = content_dir / "portfolio.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["projects"].append({"id": "no-date", "status": "draft", "title": "Без даты", "photos": [], "cover": ""})
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    html = admin.get("/admin").get_data(as_text=True)
    tail = html.split('id="y-none"', 1)[1]
    assert 'id="y-none"' in html and html.rindex('<details class="year-group"') < html.index('id="y-none"') + 1
    assert re.findall(r'class="project-title"[^>]*>([^<]+)</a>', tail) == ["Без даты"]
    assert admin.get("/admin/projects/no-date").status_code == 200        # открывается и редактируется


def test_move_buttons_are_gone(admin):
    html = admin.get("/admin").get_data(as_text=True)

    assert 'name="direction"' not in html
    assert admin.post("/admin/projects/test-arara/move", data={"csrf_token": token(admin, "/admin"),
                                                               "direction": "up"}).status_code == 404


def test_admin_list_and_forms_need_login_and_csrf(content_dir):
    anonymous = make_app(content_dir).test_client()
    assert anonymous.get("/admin").status_code == 302
    assert anonymous.post("/admin/projects/new", data=REQUIRED).status_code in (302, 400)

    admin = logged_in(content_dir)
    before = state(content_dir)
    assert admin.post("/admin/projects/new", data={**REQUIRED, "csrf_token": "forged"}).status_code == 400
    assert admin.post("/admin/projects/test-arara", data={**REQUIRED, "csrf_token": "forged"}).status_code == 400
    assert state(content_dir) == before


# --- Публичный порядок ---

def proj(pid, year=None, month=None, title=None, **extra):
    p = {"id": pid, "status": "published", "title": title or pid, "photos": ["static/img/stand/frame-159.webp"], **extra}
    if year is not None:
        p["year"] = year
    if month is not None:
        p["month"] = month
    return p


def test_published_projects_go_newest_first_then_by_title():
    items = [proj("jul", 2026, 7), proj("oct-b", 2026, 10, "Бета"), proj("oct-a", 2026, 10, "Альфа"),
             proj("sep", 2026, 9), proj("old", 2025, 12), proj("no-month", 2026), proj("aug", 2026, 8, order=-100)]

    assert [p["id"] for p in published_projects(items)] == ["oct-a", "oct-b", "sep", "aug", "jul", "no-month", "old"]


def test_bad_dates_do_not_break_sorting():
    items = [proj("bad-month", 2026, "октябрь"), proj("month-13", 2026, 13), proj("ok", 2026, 5),
             proj("bool", 2026, True), proj("no-year")]

    keys = [chronological_key(p) for p in items]                   # не падает
    assert [p["id"] for p in published_projects(items)][0] == "ok"
    assert "no-year" not in [p["id"] for p in published_projects(items)]     # без года на сайт не попадает
    assert len(keys) == 5


def test_portfolio_page_lists_stands_in_date_order(tmp_path):
    items = [proj("a", 2026, 7, "Июльский", exhibition="Выставка А"),
             proj("b", 2026, 10, "Октябрьский", exhibition="Выставка Б"),
             proj("c", 2026, 9, "Сентябрьский", exhibition="Выставка А")]

    year = build_years(items, 2026, tmp_path, first_year=2026)[0]
    order = [s["title"] for g in year["groups"] for s in g["stands"]]
    assert order == ["Октябрьский", "Июльский", "Сентябрьский"] or order == ["Октябрьский", "Сентябрьский", "Июльский"]
    # Группа выставки стоит по самому новому стенду; внутри — от новых к старым
    assert [g["name"] for g in year["groups"]] == ["Выставка Б", "Выставка А"]
    assert [s["title"] for s in year["groups"][1]["stands"]] == ["Сентябрьский", "Июльский"]


def test_public_html_follows_date_order(admin, content_dir):
    for title, month in [("Июль-стенд", "7"), ("Октябрь-стенд", "10"), ("Сентябрь-стенд", "9")]:
        create(admin, title=title, month=month, exhibition="Одна выставка",
               files=[(f"{title}.jpg", photo_bytes((900 + int(month), 600)))], publish="on")

    html = make_app(content_dir).test_client().get("/portfolio").get_data(as_text=True)
    positions = [html.index(f'<span class="stand-title">{t}</span>') for t in ("Октябрь-стенд", "Сентябрь-стенд",
                                                                                 "Июль-стенд")]
    assert positions == sorted(positions)


def test_area_is_shown_on_site_instead_of_old_spec():
    p = proj("x", 2026, 10, exhibition="E", area=40.5, specs=[{"label": "Площадь", "value": "40"},
                                                             {"label": "Этажей", "value": "2"}])
    stand = build_years([p], 2026, "/nonexistent", first_year=2026)[0]["groups"][0]["stands"][0]

    assert stand["specs"] == [{"label": "Площадь", "value": "40,5 м²"}, {"label": "Этажей", "value": "2"}]
    assert format_area(40) == "40 м²" and format_area(12.25) == "12,25 м²" and format_area(None) == ""


def test_old_projects_without_month_or_area_still_render(content_dir):
    # Тестовые демо-проекты — без месяца и без area: страницы открываются как раньше
    client = make_app(content_dir).test_client()

    for url in ("/", "/portfolio"):
        assert client.get(url).status_code == 200
