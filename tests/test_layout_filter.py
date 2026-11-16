"""Фильтр «Планировка» в списке проектов админки.

Работает той же функцией matches, что и поиск, год, месяц и выставка; значения — общий LAYOUTS.
Смотрит только на сохранённое поле layout. Проекты без планировки видны при «Все» и не попадают
ни в одну конкретную планировку. Фильтры применяются обычной GET-формой по кнопке «Показать».
"""

import re

import pytest

from app.content import LAYOUTS
from app.exhibitions import event_key, matches
from tests.test_exhibitions import admin, content_dir  # noqa: F401  (фикстуры)
from tests.test_project_dates import create, projects

CREATED = {"Альфа", "Бета", "Гамма", "Дельта", "Ёж", "Жук"}

# название, выставка, год, месяц, планировка
STANDS = [("Альфа", "Мебель", "2026", "10", "Остров"), ("Бета", "Мебель", "2026", "10", "Угловой"),
          ("Гамма", "Связь", "2026", "9", "Остров"), ("Дельта", "Мебель", "2025", "12", "Остров"),
          ("Ёж", "Мебель", "2026", "1", "Линейный"), ("Жук", "Связь", "2026", "10", "Полуостров")]


def titles(client, query=""):
    html = client.get("/admin" + query).get_data(as_text=True)
    return [t for t in re.findall(r'class="project-title"[^>]*>([^<]+)</a>', html) if t in CREATED]


@pytest.fixture
def filled(admin, content_dir):  # noqa: F811
    for title, exhibition, year, month, layout in STANDS:
        create(admin, title=title, exhibition=exhibition, year=year, month=month, layout=layout)
    return admin


def test_layout_select_in_filter_panel(filled):
    html = filled.get("/admin").get_data(as_text=True)
    form = html.split('<form class="filters', 1)[1].split("</form>", 1)[0]

    select = form.split('<select name="layout">', 1)[1].split("</select>", 1)[0]
    assert re.findall(r'<option value="([^"]*)"', select) == ["", *LAYOUTS]
    assert '<option value="">Все</option>' in select
    assert form.index('name="event"') < form.index('name="layout"') < form.index("Показать")


@pytest.mark.parametrize("layout, expected", [("Угловой", {"Бета"}), ("Остров", {"Альфа", "Гамма", "Дельта"}),
                                              ("Линейный", {"Ёж"}), ("Полуостров", {"Жук"})])
def test_each_layout_filters(filled, layout, expected):
    assert set(titles(filled, f"?layout={layout}")) == expected


def test_all_does_not_limit(filled):
    assert set(titles(filled, "?layout=")) == CREATED == set(titles(filled))


def test_layout_with_search(filled):
    assert set(titles(filled, "?layout=Остров&q=мебель")) == {"Альфа", "Дельта"}
    assert set(titles(filled, "?layout=Угловой&q=альфа")) == set()


def test_layout_with_year_month_and_exhibition(filled):
    assert set(titles(filled, "?layout=Остров&year=2026")) == {"Альфа", "Гамма"}
    assert set(titles(filled, "?layout=Угловой&event=2026:%D0%BC%D0%B5%D0%B1%D0%B5%D0%BB%D1%8C")) == {"Бета"}
    assert set(titles(filled, "?layout=Остров&year=2026&month=10")) == {"Альфа"}
    assert set(titles(filled, "?layout=Остров&year=2026&month=10&q=альфа"
                              "&event=2026:%D0%BC%D0%B5%D0%B1%D0%B5%D0%BB%D1%8C")) == {"Альфа"}


def test_old_projects_without_layout(filled, content_dir):  # noqa: F811
    old = [p["title"] for p in projects(content_dir) if "layout" not in p]
    assert old                                                  # в тестовом контенте есть старые проекты
    html_all = filled.get("/admin").get_data(as_text=True)
    for layout in LAYOUTS:
        html = filled.get(f"/admin?layout={layout}").get_data(as_text=True)
        assert not any(f">{t}</a>" in html for t in old)
    assert all(f">{t}</a>" in html_all for t in old)
    assert all("layout" not in p for p in projects(content_dir) if p["title"] in old)   # не назначена


def test_unknown_layout_value_is_ignored(filled):
    for value in ("угловой", "Круглый", "<script>", "Остров "):
        response = filled.get(f"/admin?layout={value}")
        assert response.status_code == 200 and set(titles(filled, f"?layout={value}")) == CREATED
        assert 'selected>Остров' not in response.get_data(as_text=True)


def test_selected_layout_stays_selected_and_counts(filled):
    html = filled.get("/admin?layout=Остров&year=2026").get_data(as_text=True)

    assert '<option value="Остров" selected>Остров</option>' in html
    assert re.search(r"Найдено: 2 из \d+", html) and "Сбросить" in html


def test_sorting_is_kept_with_layout_filter(filled):
    # Год ↓, месяц ↓ (декабрь… январь), внутри — по выставке и названию
    assert titles(filled, "?layout=Остров") == ["Альфа", "Гамма", "Дельта"]
    assert titles(filled) == ["Альфа", "Бета", "Жук", "Гамма", "Ёж", "Дельта"]


def test_filters_apply_only_on_submit(filled):
    html = filled.get("/admin").get_data(as_text=True)
    form = html.split('<form class="filters', 1)[1].split("</form>", 1)[0]

    assert 'method="get"' in html.split('<form class="filters', 1)[1][:200]
    assert '<button class="button button-primary" type="submit">Показать</button>' in form
    assert "onchange" not in form and "<script" not in html            # ничего не срабатывает само


def test_matches_uses_only_stored_layout():
    p = {"title": "Островной стенд", "exhibition": "Остров", "description": "остров", "year": 2026, "month": 3}

    assert matches(p) and not matches(p, layout="Остров")            # название и описание не учитываются
    assert matches({**p, "layout": "Угловой"}, layout="Угловой")
    assert matches({**p, "layout": "Угловой"}, "островной", 2026, 3, event_key("Остров", 2026), "Угловой")
