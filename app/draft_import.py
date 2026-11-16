"""Импорт подборок фото стендов с диска в черновики портфолио: flask --app app import-drafts manifest.json.

manifest.json — {"stands": [{"source", "title", "year", "month"?, "area"?, "review"?, "photos": [путь…]}]}:
source — откуда подборка (путь в облаке), review — что проверить перед публикацией; остальные поля
проекта не заполняются. Каждый стенд — черновик тем же путём, что ZIP в админке (_store_photos):
оригинал байт в байт в content/originals → WebP и версии → portfolio.json. Посетители черновик не
видят; без планировки он не публикуется (admin.set_status).

Защита от дублей: стенд с тем же source не создаётся повторно (прерванный — дозагружается: пометка
import_pending), совпадение фото (SHA-256) с оригиналами проекта сайта или названия и года — возможный
дубль, такой стенд пропускается. Фото добавляются пачками по ADMIN_MAX_FILES, как из формы, — каждая
пачка под content_lock, админка между пачками не ждёт.
"""

from pathlib import Path
from types import SimpleNamespace

from .storage import HISTORY_DIR, content_lock


def _title_key(title, year):
    return " ".join(str(title).casefold().split()), year


def _known_digests(cdir, file_digest):
    """SHA-256 оригиналов проектов → id проекта; оригиналы удалённых проектов (в истории) — отдельно."""
    projects, history = {}, {}
    for path in (cdir / "originals" / "portfolio").glob("*/*"):
        if path.is_file() and not path.name.startswith("."):
            projects.setdefault(file_digest(path), path.parent.name)
    for path in (cdir / HISTORY_DIR / "originals").glob("*/portfolio/*/*"):
        if path.is_file():
            history.setdefault(file_digest(path), path.parent.name)
    return projects, history


def _items(paths, limits):
    """Пути на диске → [(имя, чтение)] с теми же проверками, что у файла из формы (read_upload)."""
    from .uploads import read_upload

    def read(path):
        with open(path, "rb") as stream:
            return read_upload(SimpleNamespace(filename=path.name, stream=stream), *limits)

    return [(path.name, lambda path=path: read(path)) for path in paths]


def import_drafts(stands, dry_run=False, echo=print):
    """Создать черновики по списку стендов. Возвращает [(source, итог, подробности)]."""
    from flask import current_app

    from . import admin
    from .uploads import skip_reason

    cdir = admin.content_dir()
    cfg = current_app.config
    limits = (cfg["ADMIN_MAX_IMAGE_BYTES"], cfg["ADMIN_MAX_PIXELS"], cfg["ADMIN_MAX_PIXELS_FULL_DECODE"])
    batch = cfg["ADMIN_MAX_FILES"]
    results = []

    def done(source, status, detail):
        results.append((source, status, detail))
        echo(f"{status[0].upper()}{status[1:]}: {source} — {detail}")

    by_project, by_history = _known_digests(cdir, admin.file_digest)

    for stand in stands:
        source = stand["source"]
        paths, skipped = [], []
        for raw in stand.get("photos", []):
            path = Path(raw)
            reason = skip_reason(path.name)
            if reason is None and path.is_file():
                paths.append(path)
            elif reason:
                skipped.append(reason)
            elif reason is None:
                skipped.append(f"«{path.name}»: файл не найден.")
        data = admin.load_portfolio()
        project = next((p for p in data["projects"] if p.get("source") == source), None)
        if project is not None and not project.get("import_pending"):
            done(source, "уже импортирован", project["id"])
            continue
        if not paths:
            done(source, "нет фото", "; ".join(skipped) or "список пуст")
            continue
        digests = {p: admin.file_digest(p) for p in paths}
        if project is None:
            # Возможный дубль: те же фото у проекта сайта или то же название в том же году
            same = sorted({by_project[d] for d in digests.values() if d in by_project})
            key = _title_key(stand["title"], stand["year"])
            named = [p["id"] for p in data["projects"] if _title_key(p.get("title", ""), p.get("year")) == key]
            if same or named:
                why = (f"те же фото, что у проекта {', '.join(same)}" if same
                       else f"то же название и год, что у проекта {', '.join(named)}")
                done(source, "возможный дубль", why)
                continue
        review = list(stand.get("review", []))
        deleted = sorted({by_history[d] for d in digests.values() if d in by_history})
        if deleted and project is None:
            review.append(f"Часть фото уже была в удалённом проекте ({', '.join(deleted)}), копия — в content/.history.")
        if dry_run:
            done(source, "будет создан" if project is None else "будет дозагружен", f"{stand['title']}: фото {len(paths)}")
            continue

        saved_total, failed = 0, list(skipped)
        for start in range(0, len(paths), batch):
            part = paths[start:start + batch]
            with content_lock(cdir):
                data = admin.load_portfolio()
                project = next((p for p in data["projects"] if p.get("source") == source), None)
                if project is None:
                    fields = {"title": stand["title"][:admin.TITLE_MAX], "year": stand["year"]}
                    for name in ("month", "area"):
                        if stand.get(name) is not None:
                            fields[name] = stand[name]
                    project = {"id": admin.unique_id(data, fields["title"], fields["year"]), "status": "draft",
                               **fields, "source": source, "review": review, "import_pending": True,
                               "cover": "", "photos": []}
                    data["projects"].append(project)
                    existing = []
                else:
                    existing = admin.photos_of(project)
                saved, errors = admin._store_photos(project["id"], project, data, existing, _items(part, limits))
            saved_total += len(saved)
            failed += errors
        with content_lock(cdir):
            data = admin.load_portfolio()
            project = next((p for p in data["projects"] if p.get("source") == source), None)
            if project is not None and project.pop("import_pending", None):
                errors = admin.save_portfolio(data)
                failed += errors
        if project is None:
            done(source, "не создан", "; ".join(failed) or "ни одно фото не загружено")
        else:
            for d, p in digests.items():
                by_project.setdefault(d, project["id"])
            done(source, "создан черновик", f"{project['id']}: загружено фото {saved_total}"
                 + (f"; пропущено: {'; '.join(failed)}" if failed else ""))
    return results
