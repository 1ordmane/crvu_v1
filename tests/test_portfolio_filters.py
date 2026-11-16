"""Фильтры публичного портфолио: планировка, площадь, этажность.

Классификация — один раз на сервере (app/portfolio.py): у карточки стенда data-layout (значение из
LAYOUTS), data-area (s, m, l, xl — по числу area) и data-floors (1, 2 — поле floors). Скрипт
static/js/portfolio-filter.js только сравнивает выбранное с этими атрибутами. Неизвестное значение —
пустой атрибут: такой стенд виден без фильтра и не попадает ни в одну конкретную категорию.
Порядок карточек — прежний (chronological_key), фильтр его не меняет.
"""

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.content import FLOORS, LAYOUTS
from app.portfolio import AREA_RANGES, area_range, build_years, project_floors
from tests.test_app import make_client

ROOT = Path(__file__).resolve().parent.parent
FILTER_JS = ROOT / "app" / "static" / "js" / "portfolio-filter.js"
PHOTO = "static/img/stand/frame-159.webp"


@pytest.fixture
def content_dir(make_content):
    return make_content()


def stand(pid, year=2026, month=None, **fields):
    p = {"id": pid, "status": "published", "title": fields.pop("title", pid), "year": year, "photos": [PHOTO],
         "exhibition": fields.pop("exhibition", "Выставка"), **fields}
    if month is not None:
        p["month"] = month
    return p


# Название, месяц, планировка, площадь, этажность
STANDS = [
    ("isl-big-2", 10, "Остров", 45, 2), ("cor-small-1", 10, "Угловой", 12, 1), ("pen-huge", 9, "Полуостров", 80, None),
    ("lin-mid-1", 9, "Линейный", 30, 1), ("isl-small-2", 8, "Остров", 9.5, 2), ("cor-60", 7, "Угловой", 60, 2),
    ("no-data", 6, None, None, None),
]


def with_stands(content_dir, stands=STANDS):
    path = content_dir / "portfolio.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for pid, month, layout, area, floors in stands:
        fields = {k: v for k, v in (("layout", layout), ("area", area), ("floors", floors)) if v is not None}
        data["projects"].append(stand(pid, 2026, month, title=pid, **fields))
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return content_dir


def cards(html):
    """[(название, layout, area, floors)] карточек портфолио в порядке на странице."""
    return [(t, layout, area, floors) for layout, area, floors, t in re.findall(
        r'<li class="stand-card" data-stand data-layout="([^"]*)" data-area="([^"]*)" data-floors="([^"]*)">'
        r'.*?<span class="stand-title">([^<]+)</span>', html, re.S)]


def portfolio_html(content_dir):
    return make_client(content_dir).get("/portfolio").get_data(as_text=True)


def state(content_dir):
    return {str(p.relative_to(content_dir)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(content_dir.rglob("*")) if p.is_file() and ".cache" not in p.parts and p.name != ".lock"}


# --- Классификация ---

@pytest.mark.parametrize("area, key", [
    (0.5, "s"), (9.5, "s"), (12, "s"), (12.01, "m"), (19.5, "m"), (30, "m"), (30.01, "l"), (45, "l"),
    (60, "l"), (60.01, "xl"), (61, "xl"), (1500, "xl"),
])
def test_area_ranges_and_boundaries(area, key):
    assert area_range(area) == key


@pytest.mark.parametrize("bad", [None, 0, -5, "40", "40 м²", True, False, float("nan"), [], {}])
def test_missing_or_bad_area_has_no_range(bad):
    assert area_range(bad) == ""


def test_area_ranges_are_four_with_labels():
    assert [(r[0], r[3], r[4]) for r in AREA_RANGES] == [
        ("s", "До 12 м²", "компактные стенды"), ("m", "12–30 м²", "средние стенды"),
        ("l", "30–60 м²", "просторные стенды"), ("xl", "Более 60 м²", "крупные проекты")]


@pytest.mark.parametrize("value, floors", [(1, 1), (2, 2), (None, None), (3, None), (0, None), ("2", None),
                                           (True, None), (2.0, None)])
def test_floors_only_from_stored_field(value, floors):
    p = {"title": "Двухэтажный стенд", "description": "два этажа"}             # текст не учитывается
    if value is not None:
        p["floors"] = value
    assert project_floors(p) == floors
    assert FLOORS == {1: "Одноэтажный", 2: "Двухэтажный"}


def test_stand_carries_classification_and_keeps_order(tmp_path):
    items = [stand(pid, 2026, month, layout=layout, area=area, floors=floors)
             if layout else stand(pid, 2026, month) for pid, month, layout, area, floors in STANDS]
    year = build_years(items, 2026, tmp_path, first_year=2026)[0]
    got = [(s["id"], s["layout"], s["area_range"], s["floors"]) for g in year["groups"] for s in g["stands"]]

    assert got == [("cor-small-1", "Угловой", "s", 1), ("isl-big-2", "Остров", "l", 2),
                   ("lin-mid-1", "Линейный", "m", 1), ("pen-huge", "Полуостров", "xl", ""),
                   ("isl-small-2", "Остров", "s", 2), ("cor-60", "Угловой", "l", 2), ("no-data", "", "", "")]


def test_unknown_layout_value_is_not_classified(tmp_path):
    s = build_years([stand("x", layout="Круглый", area=10)], 2026, tmp_path, first_year=2026)[0]["groups"][0]["stands"][0]
    assert s["layout"] == "" and s["area_range"] == "s"


# --- Страница ---

def test_cards_have_filter_data_in_chronological_order(content_dir):
    html = portfolio_html(with_stands(content_dir))

    got = [c for c in cards(html) if c[0] in {s[0] for s in STANDS}]
    # Месяцы 10 → 6; при равном месяце — по названию; данные — как классифицировал сервер
    assert got == [("cor-small-1", "Угловой", "s", "1"), ("isl-big-2", "Остров", "l", "2"),
                   ("lin-mid-1", "Линейный", "m", "1"), ("pen-huge", "Полуостров", "xl", ""),
                   ("isl-small-2", "Остров", "s", "2"), ("cor-60", "Угловой", "l", "2"), ("no-data", "", "", "")]


def test_filter_panel_markup(content_dir):
    html = portfolio_html(with_stands(content_dir))
    panel = html.split("data-pf-filters", 1)[1].split("</form>", 1)[0]

    # Без JS кнопка и панель скрыты — видны все стенды; панель свёрнута, пока не нажата «Фильтры»
    assert re.search(r'<div class="pf-bar" data-pf-bar hidden>', html)
    assert re.search(r'<div class="pf-collapse" id="pf-panel" data-pf-panel hidden>', html)
    # Первая кнопка группы — «не выбрано» (пустое значение)
    assert re.findall(r'data-pf-key="layout" data-pf-value="([^"]*)"', panel) == ["", "Линейный", "Угловой",
                                                                                  "Полуостров", "Остров"]
    assert set(LAYOUTS) == {"Линейный", "Угловой", "Полуостров", "Остров"}       # те же значения, что в админке
    assert re.findall(r'data-pf-key="area" data-pf-value="([^"]*)"', panel) == ["", "s", "m", "l", "xl"]
    assert re.findall(r'data-pf-key="floors" data-pf-value="([^"]*)"', panel) == ["", "1", "2"]
    for label in (">Все<", ">Любая площадь<", ">До 12 м²<", ">12–30 м²<", ">30–60 м²<", ">Более 60 м²<", ">Любая<",
                  ">Одноэтажный<", ">Двухэтажный<", "Сбросить фильтры"):
        assert label in panel, label
    for note in ("компактные стенды", "средние стенды", "просторные стенды", "крупные проекты"):
        assert note not in panel                                              # без пояснений у площади
    # По умолчанию ничего не выбрано: «Все», «Любая площадь», «Любая»
    assert re.findall(r'data-pf-key="(\w+)" data-pf-value="([^"]*)" aria-pressed="true"', panel) == [
        ("layout", ""), ("area", ""), ("floors", "")]
    assert panel.count("<svg") == 4                                           # маленькие схемы планировок


def test_filters_button_controls_collapsed_panel(content_dir):
    html = portfolio_html(with_stands(content_dir))
    bar = html.split('data-pf-bar hidden>', 1)[1].split("</div>", 1)[0]

    button = re.search(r'<button class="pf-toggle"[^>]*>', bar).group(0)
    assert 'type="button"' in button and 'aria-expanded="false"' in button and 'aria-controls="pf-panel"' in button
    assert "Фильтры" in bar and re.search(r'<span class="pf-badge" data-pf-badge hidden>', bar)
    # Панель — сразу за кнопкой и содержит прежнюю форму фильтров
    panel = html.split('id="pf-panel"', 1)[1]
    assert panel.index('data-pf-filters') < panel.index("</form>") < panel.index('class="year-tabs"')
    assert html.index('data-pf-bar') < html.index('id="pf-panel"') < html.index('data-year-tabs')


def test_each_year_has_filter_empty_message(content_dir):
    html = portfolio_html(with_stands(content_dir))

    panels = re.findall(r'<div class="year-panel".*?(?=<div class="year-panel"|</section>)', html, re.S)
    assert panels and all("По выбранным параметрам стенды не найдены" in p and "data-pf-empty" in p for p in panels)
    assert all(re.search(r'<div class="pf-empty" data-pf-empty hidden>', p) for p in panels)


def test_page_without_projects_data_still_renders(content_dir):
    # Тестовые проекты без месяца, площади, планировки и этажности
    html = portfolio_html(content_dir)
    assert cards(html) and all(c[1:] == ("", "", "") for c in cards(html))


def test_rendering_does_not_change_data(content_dir):
    with_stands(content_dir)
    before = state(content_dir)

    for url in ("/portfolio", "/portfolio?layout=Остров&area=l&floors=2", "/"):
        assert make_client(content_dir).get(url).status_code == 200
    assert state(content_dir) == before


def test_configurator_still_has_no_two_storey_type(content_dir):
    html = make_client(content_dir).get("/").get_data(as_text=True)
    cfg = html.split('id="configurator"', 1)[1].split("</section>", 1)[0]
    assert "Двухэтажный" not in cfg


def test_script_is_loaded_and_has_no_own_rules():
    base = (ROOT / "app" / "templates" / "base.html").read_text(encoding="utf-8")
    assert "js/portfolio-filter.js" in base
    js = FILTER_JS.read_text(encoding="utf-8")
    for rule in ("12", "30", "60", "Остров", "Двухэтаж"):                     # правила только на сервере
        assert rule not in js, rule


# --- Логика скрипта (Node) ---

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="нужен Node.js")


def js(expression):
    code = f"const m = require({json.dumps(str(FILTER_JS))}); process.stdout.write(JSON.stringify({expression}));"
    return json.loads(subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=20,
                                     check=True).stdout)


CARDS_JS = json.dumps([{"id": s[0], "layout": s[2] or "", "area": area_range(s[3]), "floors": str(s[4] or "")}
                       for s in STANDS])


def pick(state):
    return js(f"{CARDS_JS}.filter(c => m.matches(c, {json.dumps(state)})).map(c => c.id)")


@needs_node
@pytest.mark.parametrize("layout, ids", [("Остров", ["isl-big-2", "isl-small-2"]), ("Угловой", ["cor-small-1", "cor-60"]),
                                         ("Полуостров", ["pen-huge"]), ("Линейный", ["lin-mid-1"])])
def test_js_layout_filter(layout, ids):
    assert pick({"layout": layout}) == ids


@needs_node
@pytest.mark.parametrize("area, ids", [("s", ["cor-small-1", "isl-small-2"]), ("m", ["lin-mid-1"]),
                                       ("l", ["isl-big-2", "cor-60"]), ("xl", ["pen-huge"])])
def test_js_area_filter(area, ids):
    assert pick({"area": area}) == ids


@needs_node
def test_js_floors_filter_and_unknown():
    assert pick({"floors": "1"}) == ["cor-small-1", "lin-mid-1"]
    assert pick({"floors": "2"}) == ["isl-big-2", "isl-small-2", "cor-60"]
    assert "pen-huge" not in pick({"floors": "1"}) + pick({"floors": "2"})   # неизвестная — ни туда, ни туда
    assert "no-data" not in pick({"layout": "Остров"}) + pick({"area": "s"}) + pick({"floors": "1"})


@needs_node
def test_js_filters_combine():
    assert pick({"layout": "Остров", "area": "l", "floors": "2"}) == ["isl-big-2"]
    assert pick({"layout": "Угловой", "area": "s", "floors": "1"}) == ["cor-small-1"]
    assert pick({"layout": "Полуостров", "area": "xl", "floors": ""}) == ["pen-huge"]
    assert pick({"layout": "Линейный", "area": "xl"}) == []


@needs_node
def test_js_empty_state_shows_everything():
    everything = [s[0] for s in STANDS]
    assert pick({}) == everything and pick({"layout": "", "area": "", "floors": ""}) == everything
    assert js("m.isActive({})") is False and js("m.isActive({floors: '2'})") is True


@needs_node
def test_js_state_from_and_to_address():
    allowed = json.dumps({"layout": list(LAYOUTS), "area": ["s", "m", "l", "xl"], "floors": ["1", "2"]})
    got = js(f"m.readState('?layout=%D0%9E%D1%81%D1%82%D1%80%D0%BE%D0%B2&area=xl&floors=3&x=1', {allowed})")
    assert got == {"layout": "Остров", "area": "xl", "floors": ""}                # неизвестное — отброшено
    assert js(f"m.readState('?layout=<script>', {allowed})") == {"layout": "", "area": "", "floors": ""}
    assert js("m.toSearch({layout: 'Остров', area: '', floors: '2'})") == "?layout=%D0%9E%D1%81%D1%82%D1%80%D0%BE%D0%B2&floors=2"
    assert js("m.toSearch({layout: '', area: '', floors: ''})") == ""


@needs_node
def test_js_active_count_for_badge():
    assert js("m.activeCount({layout: '', area: '', floors: ''})") == 0
    assert js("m.activeCount({layout: 'Остров', area: '', floors: ''})") == 1
    assert js("m.activeCount({layout: 'Остров', area: 'l', floors: '2'})") == 3


@needs_node
def test_js_toggle_keeps_other_filters():
    assert js("m.toggle({layout: 'Остров', area: 'l', floors: '2'}, 'layout', 'Угловой')") == \
        {"layout": "Угловой", "area": "l", "floors": "2"}
    assert js("m.toggle({layout: 'Остров', area: 'l', floors: '2'}, 'layout', 'Остров')") == \
        {"layout": "", "area": "l", "floors": "2"}                             # повторное нажатие снимает
    assert js("m.toggle({layout: '', area: 'l', floors: '2'}, 'floors', '')") == {"layout": "", "area": "l", "floors": ""}


# --- Этажность в админке ---

def test_admin_floors_field(make_content):
    from tests.test_project_dates import create, edit, projects
    from tests.test_image_sizes import PASSWORD, TOKEN_RE, make_app

    content_dir = make_content()
    client = make_app(content_dir).test_client()
    token = TOKEN_RE.search(client.get("/admin/login").get_data(as_text=True)).group(1)
    client.post("/admin/login", data={"csrf_token": token, "username": "admin", "password": PASSWORD})

    html = client.get("/admin/projects/new").get_data(as_text=True)
    select = re.search(r'<select name="floors"[^>]*>(.*?)</select>', html, re.S)
    assert select and "required" not in select.group(0).split(">")[0]
    assert re.findall(r'<option value="([^"]*)"', select.group(1)) == ["", "1", "2"]

    create(client, title="Без этажности")
    create(client, title="Двушка", floors="2")
    by = {p["title"]: p for p in projects(content_dir)}
    assert "floors" not in by["Без этажности"] and by["Двушка"]["floors"] == 2

    assert create(client, title="Плохой", floors="3").status_code == 400
    edit(client, by["Двушка"]["id"], title="Двушка", floors="1")
    assert {p["title"]: p for p in projects(content_dir)}["Двушка"]["floors"] == 1
    edit(client, by["Двушка"]["id"], title="Двушка", floors="")
    assert "floors" not in {p["title"]: p for p in projects(content_dir)}["Двушка"]      # можно снова не указывать


def test_check_content_validates_floors(content_dir):
    from app.content import validate_content
    from tests.test_image_sizes import make_app

    static = str(make_app(content_dir).static_folder)
    path = content_dir / "portfolio.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["projects"][0]["floors"] = 2
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert validate_content(content_dir, static) == []

    data["projects"][0]["floors"] = "два"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert any("floors" in e for e in validate_content(content_dir, static))
