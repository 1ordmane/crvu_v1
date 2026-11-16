from pathlib import Path

from .content import FEATURED_MAX, FLOORS, LAYOUTS, media_files, video_page_url, video_player_url, visible

FIRST_YEAR = 2020
OTHER_GROUP = "Другие проекты"
MONTHS = ("Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь",
          "Ноябрь", "Декабрь")


def project_year(project):
    """Год проекта (целое число) или None — для старых и неполных записей."""
    year = project.get("year")
    return year if isinstance(year, int) and not isinstance(year, bool) else None


def project_month(project):
    """Месяц 1–12 или None: у старых проектов месяца нет, неверное значение не роняет страницу."""
    month = project.get("month")
    if isinstance(month, int) and not isinstance(month, bool) and 1 <= month <= 12:
        return month
    return None


def chronological_key(project):
    """Порядок «от новых к старым»: год, месяц (без месяца — после месяцев года), затем название.

    Сравниваются числа, а не строки дат; без года — в самом конце. Для sorted(..., key=...).
    """
    return (-(project_year(project) or 0), -(project_month(project) or 0),
            str(project.get("title") or "").casefold(), str(project.get("id") or ""))


# Планировки в фильтре портфолио — по числу открытых сторон, с закрытыми сторонами для значка-схемы
# (back — задняя, left, right). Значения — те же, что в админке (LAYOUTS)
LAYOUT_FILTER = (
    ("Линейный", ("back", "left", "right")),
    ("Угловой", ("back", "left")),
    ("Полуостров", ("back",)),
    ("Остров", ()),
)
assert {name for name, _ in LAYOUT_FILTER} == set(LAYOUTS)

# Диапазоны площади для фильтра портфолио: (ключ, больше, до включительно, подпись, пояснение).
# Граница — в нижнем диапазоне: 12 м² — «До 12», 30 — «12–30», 60 — «30–60»
AREA_RANGES = (
    ("s", 0, 12, "До 12 м²", "компактные стенды"),
    ("m", 12, 30, "12–30 м²", "средние стенды"),
    ("l", 30, 60, "30–60 м²", "просторные стенды"),
    ("xl", 60, None, "Более 60 м²", "крупные проекты"),
)


def area_range(area):
    """Ключ диапазона по числу area (м²); нет площади или она неверна — "" (ни в один диапазон)."""
    if isinstance(area, bool) or not isinstance(area, (int, float)) or not area > 0:
        return ""
    for key, above, upto, _, _ in AREA_RANGES:
        if area > above and (upto is None or area <= upto):
            return key
    return ""


def project_floors(project):
    """Этажность из поля floors (1 или 2) или None — не указана; текст проекта не учитывается."""
    floors = project.get("floors")
    return floors if type(floors) is int and floors in FLOORS else None


def format_area(area):
    """40 → «40 м²», 12.25 → «12,25 м²»; не число или не больше нуля — ""."""
    if isinstance(area, bool) or not isinstance(area, (int, float)) or area <= 0:
        return ""
    return f"{area:g}".replace(".", ",") + " м²"


def site_specs(project):
    """Характеристики для сайта: площадь из поля area (если есть) вместо старой строки «Площадь …»."""
    specs = [s for s in project.get("specs", []) if s.get("label") and s.get("value")]
    area = format_area(project.get("area"))
    if area:
        old_area = [s for s in specs if s["label"].casefold().startswith("площад")]
        specs = [{"label": "Площадь", "value": area}] + [s for s in specs if s not in old_area]
    return specs


def years_word(n):
    last, last_two = n % 10, n % 100
    if last == 1 and last_two != 11:
        word = "год"
    elif 2 <= last <= 4 and not 12 <= last_two <= 14:
        word = "года"
    else:
        word = "лет"
    return f"{n} {word}"


def project_photos(project, media_dir):
    """Фото проекта: обложка первой, затем photos или файлы из media/portfolio/<id>/.

    Пути начинаются с static/ (файлы приложения) или считаются от content/media/.
    """
    photos = list(project.get("photos") or media_files(media_dir, f"portfolio/{project.get('id', '')}"))
    cover = project.get("cover")
    if cover:
        photos = [cover] + [p for p in photos if p != cover]
    return photos


def published_projects(projects):
    """Опубликованные проекты с годом — от новых к старым (год и месяц), при равной дате — по названию."""
    items = [p for p in projects if p.get("status") == "published" and project_year(p) is not None]
    return sorted(items, key=chronological_key)


def existing_photos(photos, media_dir, static_dir):
    """Только фото, файлы которых на месте: удалённый файл не даст «битую» карточку."""
    def exists(path):
        if path.startswith("static/"):
            return (Path(static_dir) / path.removeprefix("static/")).is_file()
        return (Path(media_dir) / path).is_file()
    return [p for p in photos if exists(p)]


def build_years(projects, current_year, media_dir, first_year=FIRST_YEAR, static_dir=None):
    """Годы от новых к старым, в каждом — опубликованные проекты, сгруппированные по выставкам.

    Год без опубликованных стендов (пустой, только черновики) в список не попадает.
    С static_dir отсутствующие файлы отбрасываются, а проекты совсем без фото не показываются.
    """
    items = published_projects(projects)
    years_present = [p["year"] for p in items]
    newest = max([current_year] + years_present)
    oldest = min([first_year] + years_present)
    years = []
    for year in range(newest, oldest - 1, -1):
        groups = {}
        for p in items:
            if p["year"] != year:
                continue
            photos = project_photos(p, media_dir)
            if static_dir is not None:
                photos = existing_photos(photos, media_dir, static_dir)
                if not photos:
                    continue
            name = (p.get("exhibition") or "").strip() or OTHER_GROUP
            groups.setdefault(name, []).append({
                "id": p.get("id", ""),
                "title": p.get("title", ""),
                "category": p.get("category", ""),
                "city": p.get("city", ""),
                "year": year,
                "description": p.get("description", ""),
                "specs": site_specs(p),
                "example": bool(p.get("example")),
                # Для фильтров портфолио; неизвестное — "" (стенд виден только без этого фильтра)
                "layout": p.get("layout") if p.get("layout") in LAYOUTS else "",
                "area_range": area_range(p.get("area")),
                "floors": project_floors(p) or "",
                # Ссылка под карточкой и плеер галереи — Vimeo или Rutube, адреса собраны заново.
                # Ключ "vimeo" — исторический: в нём адрес плеера любого из двух сервисов
                "video": video_page_url(p.get("video")),
                "vimeo": video_player_url(p.get("video")),
                "photos": photos,
            })
        if not groups:
            continue
        names = sorted(groups, key=lambda name: name == OTHER_GROUP)
        years.append({
            "year": year,
            "count": sum(len(g) for g in groups.values()),
            "groups": [{"name": name, "stands": groups[name]} for name in names],
        })
    return years


def spec_value(project, prefix):
    """Значение характеристики проекта, название которой начинается с prefix («площад…»)."""
    for spec in project.get("specs", []):
        label, value = (spec.get("label") or "").casefold(), spec.get("value")
        if label.startswith(prefix) and value:
            return value
    return ""


def featured_cards(items, projects, media_dir, static_dir=None, show_placeholders=False):
    """Карточки блока «Реализованные проекты» на главной.

    Карточка берёт данные из опубликованного проекта портфолио (поле project), а свои
    поля title, event, area, place, image важнее. Чего нет — остаётся пустым, ничего не
    додумываем. Карточка без фото — временная заглушка: она попадает в список только
    при show_placeholders, иначе на сайте её нет.
    """
    # Любой опубликованный проект, какого бы года он ни был; черновик на главную не попадает
    published = {p.get("id"): p for p in projects if isinstance(p, dict) and p.get("status") == "published"}
    cards = []
    for index, item in enumerate(items[:FEATURED_MAX]):
        if not isinstance(item, dict):
            continue
        project = published.get(item.get("project") or "")
        if item.get("project") and project is None:
            # Проект удалили из портфолио или сняли с публикации — карточки нет, ошибки тоже
            continue
        photos = project_photos(project, media_dir) if project else []
        image = (item.get("image") or "").strip()
        if image:
            photos = [image] + [p for p in photos if p != image]
        if static_dir is not None:
            photos = existing_photos(photos, media_dir, static_dir)
        if not photos and not show_placeholders:
            continue
        source = project or {}
        cards.append({
            "index": index,
            "title": item.get("title") or source.get("title", ""),
            "event": item.get("event") or source.get("exhibition", ""),
            "area": item.get("area") or ((format_area(source.get("area")) or spec_value(source, "площад"))
                                         if project else ""),
            "place": item.get("place") or source.get("city", ""),
            # Подпись карточки: год и планировка — только из проекта, неизвестная планировка не выводится
            "year": source.get("year", "") if project else "",
            "layout": source.get("layout") if project and source.get("layout") in LAYOUTS else "",
            "photo_count": len(photos),
            "has_video": bool(project and video_player_url(source.get("video"))),
            "photos": photos,
            "placeholder": not photos,
            # Для «Подробнее»: у связанного проекта открывается его галерея
            "project": {
                "title": source.get("title", ""),
                "meta": ", ".join(str(v) for v in (source.get("category"), source.get("city"), source.get("year")) if v),
                "description": source.get("description", ""),
                "specs": site_specs(source),
                "video": video_page_url(source.get("video")),
                "vimeo": video_player_url(source.get("video")),
            } if project and photos else None,
        })
    return cards


def placeholder_notes(content, content_dir, static_dir):
    """Карточки главной, которые пока остаются заглушками (нет фото), — для check-content."""
    media_dir = Path(content_dir) / "media"
    projects = content["portfolio"].get("projects", [])
    notes = []
    for s in visible(content["home"].get("sections", [])):
        if s.get("type") != "projects":
            continue
        for n, card in enumerate(featured_cards(s.get("items", []), projects, media_dir, static_dir, True), 1):
            if card["placeholder"]:
                notes.append(f"home.json, блок «{s.get('id')}»: карточка {n} без фото")
    return notes
