from pathlib import Path

import pytest

from app.portfolio import build_years, project_photos, published_projects, years_word


@pytest.mark.parametrize(
    ("n", "expected"),
    [(1, "1 год"), (2, "2 года"), (4, "4 года"), (5, "5 лет"), (11, "11 лет"),
     (14, "14 лет"), (21, "21 год"), (22, "22 года"), (27, "27 лет"), (112, "112 лет")],
)
def test_years_word(n, expected):
    assert years_word(n) == expected


def project(**fields):
    base = {"id": "p", "status": "published", "title": "P", "year": 2026, "photos": ["static/img/a.webp"]}
    return {**base, **fields}


def test_project_photos_puts_cover_first(tmp_path):
    p = project(photos=["a.jpg", "b.jpg", "c.jpg"], cover="b.jpg")

    assert project_photos(p, tmp_path) == ["b.jpg", "a.jpg", "c.jpg"]


def test_project_photos_reads_media_folder_when_list_is_empty(tmp_path):
    folder = tmp_path / "portfolio" / "stand-1"
    folder.mkdir(parents=True)
    for name in ["02.webp", "01.jpg", "notes.txt"]:
        (folder / name).write_bytes(b"x")

    photos = project_photos(project(id="stand-1", photos=[]), tmp_path)

    assert photos == ["portfolio/stand-1/01.jpg", "portfolio/stand-1/02.webp"]


def test_published_projects_skip_drafts_and_sort_by_date_then_title():
    # Старое поле order больше не влияет: при равной дате — по названию
    items = [
        project(id="b", title="B", order=20),
        project(id="draft", status="draft", order=0),
        project(id="a", title="A", order=10),
        project(id="c", title="C", order=-5),
    ]

    assert [p["id"] for p in published_projects(items)] == ["a", "b", "c"]


def test_build_years_groups_by_exhibition(tmp_path):
    items = [
        project(id="a", title="A", exhibition="Выставка 1", order=1),
        project(id="b", title="B", exhibition="", order=2),
        project(id="c", title="C", exhibition="Выставка 1", order=3),
        project(id="d", title="D", year=2024, exhibition="Выставка 2"),
        project(id="x", status="draft"),
    ]

    years = build_years(items, current_year=2026, media_dir=tmp_path, first_year=2023)

    assert [y["year"] for y in years] == [2026, 2024]
    assert [y["count"] for y in years] == [3, 1]
    groups = years[0]["groups"]
    assert [g["name"] for g in groups] == ["Выставка 1", "Другие проекты"]
    assert [s["id"] for s in groups[0]["stands"]] == ["a", "c"]


def test_build_years_includes_projects_outside_default_range(tmp_path):
    years = build_years([project(year=2018)], current_year=2026, media_dir=tmp_path, first_year=2020)

    assert years[-1]["year"] == 2018
    assert years[-1]["count"] == 1


def test_build_years_drops_unsafe_video_and_empty_specs(tmp_path):
    items = [project(video="javascript:alert(1)", specs=[{"label": "Площадь", "value": "54 м²"}, {"label": "", "value": "x"}])]

    stand = build_years(items, current_year=2026, media_dir=tmp_path, first_year=2026)[0]["groups"][0]["stands"][0]

    assert stand["video"] == ""
    assert stand["specs"] == [{"label": "Площадь", "value": "54 м²"}]


def test_build_years_shows_only_years_with_published_stands(tmp_path):
    # 2026 и 2025 — опубликованные, 2024 — только черновики, 2023 — проект, снятый
    # с публикации (админка возвращает его в status "draft"), 2022 — проектов нет
    items = [
        project(id="live", year=2026),
        project(id="live-2025", year=2025),
        project(id="draft", year=2024, status="draft"),
        project(id="draft-2", year=2024, status="draft"),
        project(id="unpublished", year=2023, status="draft", example=False),
    ]

    years = build_years(items, current_year=2026, media_dir=tmp_path, first_year=2022)

    assert [y["year"] for y in years] == [2026, 2025]
    assert [y["count"] for y in years] == [1, 1]
    assert [s["id"] for s in years[0]["groups"][0]["stands"]] == ["live"]


def test_build_years_returns_nothing_when_only_drafts(tmp_path):
    items = [project(id="old", year=2018, status="draft"), project(id="d", year=2026, status="draft")]

    assert build_years(items, current_year=2026, media_dir=tmp_path, first_year=2020) == []


def test_build_years_hides_year_whose_stands_lost_all_photos(tmp_path):
    items = [project(id="live", year=2026, photos=["static/img/stand/frame-159.webp"]),
             project(id="no-files", year=2025, photos=["portfolio/no-files/01.webp"])]
    static_dir = Path(__file__).resolve().parent.parent / "app" / "static"

    years = build_years(items, current_year=2026, media_dir=tmp_path, first_year=2020, static_dir=static_dir)

    assert [y["year"] for y in years] == [2026]


# --- Реализованные проекты на главной ---

from app.portfolio import featured_cards  # noqa: E402

STAND = {"id": "stand-1", "status": "published", "title": "Стенд", "year": 2025, "city": "Казань",
         "exhibition": "Иннопром", "specs": [{"label": "Площадь стенда", "value": "54 м²"}],
         "photos": ["static/img/stand/frame-159.webp"]}
STATIC = Path(__file__).resolve().parent.parent / "app" / "static"


def test_card_without_photo_is_placeholder_only_when_allowed(tmp_path):
    empty = [{"title": "", "image": ""}]

    assert featured_cards(empty, [], tmp_path, STATIC) == []
    [card] = featured_cards(empty, [], tmp_path, STATIC, show_placeholders=True)
    assert card["placeholder"] and card["photos"] == [] and card["project"] is None


def test_card_takes_data_from_published_project_and_own_fields_win(tmp_path):
    [linked] = featured_cards([{"project": "stand-1"}], [STAND], tmp_path, STATIC)
    assert (linked["title"], linked["event"], linked["area"], linked["place"]) == ("Стенд", "Иннопром", "54 м²", "Казань")
    assert linked["photos"] == ["static/img/stand/frame-159.webp"] and linked["project"]["title"] == "Стенд"

    [own] = featured_cards([{"project": "stand-1", "title": "Своё", "area": "60 м²"}], [STAND], tmp_path, STATIC)
    assert own["title"] == "Своё" and own["area"] == "60 м²" and own["place"] == "Казань"


def test_card_never_uses_draft_projects_or_missing_files(tmp_path):
    draft = {**STAND, "status": "draft"}

    assert featured_cards([{"project": "stand-1"}], [draft], tmp_path, STATIC) == []
    assert featured_cards([{"title": "Без файла", "image": "home/missing.webp"}], [], tmp_path, STATIC) == []


def test_manual_card_with_image_is_shown_without_inventing_fields(tmp_path):
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "p1.webp").write_bytes(b"x")

    [card] = featured_cards([{"image": "home/p1.webp", "title": "Стенд на выставке"}], [], tmp_path, STATIC)

    assert card["photos"] == ["home/p1.webp"] and not card["placeholder"]
    assert card["event"] == card["area"] == card["place"] == "" and card["project"] is None


def test_card_carries_layout_year_photo_count_and_video_flag_from_project(tmp_path):
    # Подпись карточки на главной: выставка, год, площадь, планировка — только из данных проекта
    stand = {**STAND, "layout": "Угловой", "video": "https://vimeo.com/76979871"}
    [card] = featured_cards([{"project": "stand-1"}], [stand], tmp_path, STATIC)
    assert (card["year"], card["layout"], card["photo_count"], card["has_video"]) == (2025, "Угловой", 1, True)

    [plain] = featured_cards([{"project": "stand-1"}], [{**STAND, "layout": "Другое"}], tmp_path, STATIC)
    assert plain["layout"] == "" and plain["has_video"] is False       # неизвестная планировка не выводится

    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "p1.webp").write_bytes(b"x")
    [own] = featured_cards([{"image": "home/p1.webp", "title": "Свой"}], [], tmp_path, STATIC)
    assert own["year"] == "" and own["layout"] == "" and own["has_video"] is False
