"""Запись контента из админ-панели.

Каждое изменение: блокировка папки (одна запись за раз, даже при нескольких
процессах сервера) → копия прежнего файла в content/.history/ → запись во
временный файл → атомарная замена. Сайт никогда не увидит наполовину
записанный JSON.
"""

import fcntl
import json
import os
import shutil
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

HISTORY_DIR = ".history"
LOCK_FILE = ".lock"
HISTORY_KEEP = 500  # копий каждого JSON в content/.history (фото и оригиналы там не удаляются)


def _stamp():
    return datetime.now().strftime("%Y%m%d-%H%M%S-%f")


@contextmanager
def content_lock(content_dir):
    with open(Path(content_dir) / LOCK_FILE, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _prune(history, name, keep):
    copies = sorted(history.glob(f"{name}-*.json"))
    for old in copies[:-keep] if keep else copies:
        old.unlink()


def write_json(content_dir, name, data, keep=HISTORY_KEEP):
    """Сохранить content/<name>.json; прежняя версия уходит в .history/."""
    content_dir = Path(content_dir)
    target = content_dir / f"{name}.json"
    history = content_dir / HISTORY_DIR
    history.mkdir(exist_ok=True)
    if target.exists():
        shutil.copy2(target, history / f"{name}-{_stamp()}.json")
        _prune(history, name, keep)

    fd, tmp = tempfile.mkstemp(dir=content_dir, prefix=f".{name}-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def history_stamp():
    """Метка времени для папки истории: одна на несколько файлов — они окажутся рядом."""
    return _stamp()


def move_to_history(content_dir, path, stamp=None):
    """Убрать файл или папку из content/media (или content/originals) в .history/<раздел>/<время>/…, не удаляя.

    Путь внутри раздела сохраняется: файл возвращается на место простым переносом обратно.
    """
    content_dir = Path(content_dir)
    path = Path(path)
    if not path.exists():
        return None
    section, *rest = path.relative_to(content_dir).parts
    if section not in ("media", "originals") or not rest:
        raise ValueError(f"в историю переносятся только файлы из media/ и originals/: {path}")
    target = content_dir / HISTORY_DIR / section / (stamp or _stamp()) / Path(*rest)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), target)
    return target
