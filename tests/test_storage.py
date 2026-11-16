"""История content/.history: сколько копий JSON хранится и что ограничение не трогает фото."""

import json

from app import storage


def test_history_keeps_500_copies_of_each_json():
    assert storage.HISTORY_KEEP == 500


def test_old_json_copies_are_pruned_to_limit_and_nothing_else_is_touched(tmp_path):
    history = tmp_path / ".history"
    history.mkdir()
    # 505 старых копий portfolio.json (по имени — раньше любой новой) и копии других файлов
    old = [history / f"portfolio-20200101-000000-{n:06d}.json" for n in range(505)]
    for path in old:
        path.write_text("{}", encoding="utf-8")
    home = history / "home-20200101-000000-000000.json"
    home.write_text("{}", encoding="utf-8")
    photo = history / "media" / "20200101-000000-000000" / "portfolio" / "stand" / "01.webp"
    original = history / "originals" / "20200101-000000-000000" / "portfolio" / "stand" / "01.jpg"
    for path in (photo, original):
        path.parent.mkdir(parents=True)
        path.write_bytes(b"x")
    (tmp_path / "portfolio.json").write_text('{"projects": []}', encoding="utf-8")

    storage.write_json(tmp_path, "portfolio", {"projects": [{"id": "new"}]})

    copies = sorted(history.glob("portfolio-*.json"))
    assert len(copies) == 500
    # Остались самые новые: свежая копия прежнего файла и 499 последних старых
    assert json.loads(copies[-1].read_text(encoding="utf-8")) == {"projects": []}
    assert copies[0] == old[6] and not old[5].exists()
    # Ограничение — для каждого файла отдельно; удалённые фото и оригиналы не удаляются никогда
    assert home.exists() and photo.exists() and original.exists()


def test_custom_keep_still_works(tmp_path):
    (tmp_path / "home.json").write_text("{}", encoding="utf-8")
    for n in range(3):
        storage.write_json(tmp_path, "home", {"n": n}, keep=2)

    assert len(list((tmp_path / ".history").glob("home-*.json"))) == 2
