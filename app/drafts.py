"""Черновики визуального редактора: правки текстов не попадают на сайт до публикации.

Черновик файла — content/.drafts/<имя>.json вида {"base": …, "data": {…}}:
data — содержимое будущего content/<имя>.json, base — отпечаток опубликованного файла
в момент создания черновика. Публикация (admin.editor_publish) сверяет base с текущим
файлом: если его успели изменить в другом месте, черновик не перезапишет чужую правку.

Сохранение черновика пишет только сюда; публичный сайт читает content/<имя>.json.
Публикация идёт через storage.write_json — с копией прежней версии в .history/.
"""

import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

from .storage import HISTORY_DIR

DRAFTS_DIR = ".drafts"
# Файлы редактора: общие тексты (шапка, подвал, форма), главная, разделы, список контактов
# и библиотека видеоотзывов (videos.json — необязательный файл, пока его нет, роликов нет).
EDITOR_FILES = ("site", "home", "pages", "contacts", "videos")
HISTORY_RE = re.compile(r"^(site|home|pages|contacts|videos)-(\d{8}-\d{6})-\d{6}\.json$")


def _path(content_dir, name):
    if name not in EDITOR_FILES:
        raise ValueError(f"черновик для {name}.json не поддерживается")
    return Path(content_dir) / DRAFTS_DIR / f"{name}.json"


def read(content_dir, name):
    """Черновик {"base", "data"} или None, если черновика нет или он повреждён."""
    path = _path(content_dir, name)
    try:
        draft = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    if not isinstance(draft, dict) or not isinstance(draft.get("data"), dict) or not isinstance(draft.get("base"), str):
        return None
    return draft


def write(content_dir, name, data, base):
    """Атомарно записать черновик: временный файл и замена — недописанного черновика не бывает."""
    path = _path(content_dir, name)
    path.parent.mkdir(exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{name}-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"base": base, "data": data}, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def discard(content_dir, name):
    _path(content_dir, name).unlink(missing_ok=True)


def existing(content_dir):
    """Имена файлов, для которых есть черновик."""
    return [name for name in EDITOR_FILES if read(content_dir, name) is not None]


def overlay(content, content_dir):
    """Контент сайта, где файлы с черновиком заменены черновиком (для предпросмотра и проверки)."""
    result = dict(content)
    for name in EDITOR_FILES:
        draft = read(content_dir, name)
        if draft is not None:
            result[name] = draft["data"]
    return result


def history(content_dir):
    """Сохранённые версии файлов редактора, от новых к старым: [(имя файла, файл, время)]."""
    folder = Path(content_dir) / HISTORY_DIR
    items = []
    for path in folder.glob("*.json") if folder.is_dir() else []:
        match = HISTORY_RE.match(path.name)
        if match:
            items.append((path.name, match.group(1), datetime.strptime(match.group(2), "%Y%m%d-%H%M%S")))
    return sorted(items, key=lambda item: item[0].split("-", 1)[1], reverse=True)


def read_history(content_dir, filename):
    """Версия из .history/ по точному имени файла → (имя, данные). Чужие имена и пути — ValueError."""
    match = HISTORY_RE.match(filename or "")
    if not match:
        raise ValueError("неизвестная версия")
    path = Path(content_dir) / HISTORY_DIR / filename
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        raise ValueError("версия не найдена или повреждена") from None
    if not isinstance(data, dict):
        raise ValueError("версия повреждена")
    return match.group(1), data
