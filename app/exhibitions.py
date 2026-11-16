"""Справочник выставок и дерево проектов админки.

Выставка — это событие: нормализованное название + год. «Мебель» 2025 и «Мебель» 2026 — разные
события; « мебель » и «Мебель» в одном году — одно. Проект хранит название выставки (поле exhibition)
и год, как и раньше, поэтому сайт и старые данные работают без миграции.

Справочник = выставки из content/exhibitions.json (заведённые заранее, файл необязателен) +
выставки, которые встречаются в проектах. Месяц относится к проекту и в справочнике не хранится.
"""

import re
from pathlib import Path

from .content import read_json
from .portfolio import MONTHS, project_month, project_year

FILE = "exhibitions"
NAME_MAX = 200
NO_EXHIBITION = "Без выставки"


def clean(name):
    """Название для показа и записи: без лишних пробелов по краям и внутри."""
    return re.sub(r"\s+", " ", str(name or "")).strip()


def normalize(name):
    """Название для сравнения: регистр, пробелы и «ё» не различаются."""
    return clean(name).casefold().replace("ё", "е")


def event_key(name, year):
    """Ключ события «2026:мебель»; без названия или без года — ""."""
    if not normalize(name) or not isinstance(year, int) or isinstance(year, bool):
        return ""
    return f"{year}:{normalize(name)}"


def title_key(project):
    return (str(project.get("title") or "").casefold(), str(project.get("id") or ""))


def load_stored(content_dir):
    """Выставки из content/exhibitions.json; нет файла — пустой список."""
    path = Path(content_dir) / f"{FILE}.json"
    if not path.exists():
        return []
    return list(read_json(path).get("exhibitions", []))


def directory(projects, stored):
    """Все события: [{key, name, year, stored, projects}] — по году (новые выше), затем по названию.

    Написание названия — из справочника, иначе из первого проекта (по названию проекта).
    """
    events = {}
    for item in stored:
        key = event_key(item.get("name"), item.get("year"))
        if key and key not in events:
            events[key] = {"key": key, "name": clean(item["name"]), "year": item["year"], "stored": True,
                           "projects": []}
    for p in sorted(projects, key=title_key):
        year = project_year(p)
        key = event_key(p.get("exhibition"), year)
        if not key:
            continue
        event = events.setdefault(key, {"key": key, "name": clean(p["exhibition"]), "year": year, "stored": False,
                                        "projects": []})
        event["projects"].append(p)
    return sorted(events.values(), key=lambda e: (-e["year"], normalize(e["name"])))


def resolve(name, year, events):
    """Название из формы → (название события, новое ли оно). Тот же год и то же название без учёта
    регистра и пробелов → существующее событие с его написанием; другой год — всегда своё событие."""
    key = event_key(name, year)
    for event in events:
        if event["key"] == key:
            return event["name"], False
    return clean(name), True


def name_options(events):
    """Подсказки для поля «Выставка»: [(название, «2026, 2025»)] — каждое название один раз."""
    options = {}
    for event in events:
        name, years = options.setdefault(normalize(event["name"]), (event["name"], []))
        years.append(str(event["year"]))
    return sorted(((name, ", ".join(years)) for name, years in options.values()), key=lambda o: normalize(o[0]))


def matches(project, query="", year=None, month=None, event="", layout=""):
    """Подходит ли проект под поиск и фильтры админки. layout сравнивается с сохранённым полем
    проекта: у старого проекта без планировки она не определена — он не подходит ни под одну."""
    if year is not None and (project_year(project) or 0) != year:
        return False
    if month is not None and (project_month(project) or 0) != month:
        return False
    if event and event_key(project.get("exhibition"), project_year(project)) != event:
        return False
    if layout and project.get("layout") != layout:
        return False
    query = normalize(query)
    return not query or query in normalize(project.get("title")) or query in normalize(project.get("exhibition"))


def tree(projects):
    """Дерево админки: год (новые выше) → месяц (декабрь… январь, без месяца — в конце) → выставка
    (по названию, «Без выставки» — в конце) → проекты (по названию)."""
    years = {}
    for p in sorted(projects, key=title_key):
        year, month = project_year(p) or 0, project_month(p) or 0
        name = clean(p.get("exhibition"))
        key = event_key(name, year) or f"{year}:"
        events = years.setdefault(year, {}).setdefault(month, {})
        events.setdefault(key, {"key": key, "name": name or NO_EXHIBITION, "empty": not name, "projects": []})
        events[key]["projects"].append(p)
    result = []
    for year in sorted(years, reverse=True):
        months = []
        for month in sorted(years[year], reverse=True):
            events = sorted(years[year][month].values(), key=lambda e: (e["empty"], normalize(e["name"])))
            months.append({"month": month, "label": MONTHS[month - 1] if month else "Месяц не указан",
                           "id": f"m-{year}-{month:02d}" if month else f"m-{year}-none",
                           "count": sum(len(e["projects"]) for e in events), "events": events})
        result.append({"year": year, "label": str(year) if year else "Без даты", "id": f"y-{year}" if year else "y-none",
                       "count": sum(m["count"] for m in months), "months": months})
    return result
