"""Заявки с сайта («Заказать дизайн-проект» в конфигураторе): проверка, сохранение и чтение.

Заявка — отдельный JSON-файл в REQUESTS_DIR (по умолчанию requests/ в корне проекта): вне content/
и static/, поэтому сайт его не отдаёт; права — только у владельца, в архив контента он не попадает.
Менеджер видит заявки в админке (/admin/requests). Данным от браузера не доверяем: контакт и все
параметры стенда проверяются здесь заново по тем же правилам и пределам, что у конфигуратора.
"""

import json
import os
import re
import secrets
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

CONTACT_MAX = 100
BUDGET_DIGITS = 12
# Телефон в любом формате: цифры, пробелы, скобки, дефисы, точки и «+» только в начале.
# Цифр — от 7 (короткие городские) до 15 (предел международного номера E.164)
PHONE_RE = re.compile(r"\+?[0-9 ()\-.]+")
PHONE_DIGITS = (7, 15)
# Email: без пробелов и служебных символов, в домене — точка и зона из двух и более букв.
# Буквы любого алфавита: почта@сайт.рф тоже адрес
_ATOM = r"[^\s@<>()\[\],;:\"\\.]+"
EMAIL_RE = re.compile(rf"{_ATOM}(?:\.{_ATOM})*@(?:{_ATOM}\.)+[^\W\d_]{{2,}}")
# Сторона — как в конфигураторе (stand-plan.js): от 1 м, до сотых, через запятую или точку
SIDE_RE = re.compile(r"[0-9]{1,4}(?:[.,][0-9]{1,2})?")
MIN_SIDE = 1
DEFAULT_AREA = (9, 300)      # как у шаблона конфигуратора, если в home.json пределов нет

CONTACT_ERRORS = {
    "empty": "Укажите телефон или электронную почту.",
    "long": f"Слишком длинно — не больше {CONTACT_MAX} символов.",
    "email": "Проверьте адрес почты — например, name@company.ru.",
    "phone": "Проверьте номер: от 7 до 15 цифр, можно с «+», пробелами, скобками и дефисами.",
}
STAND_ERRORS = {
    "length": "Длина стенда — число от 1 м, например 6 или 4,5.",
    "width": "Ширина стенда — число от 1 м, например 6 или 4,5.",
    "area": "Площадь стенда вне допустимых пределов.",
    "type": "Выберите тип стенда.",
    "budget": f"Бюджет — сумма в рублях цифрами, не больше {BUDGET_DIGITS} цифр.",
    "level": "Выберите уровень сложности.",
    "extras": "Неизвестная дополнительная опция.",
    "estimate": "Ориентир бюджета — суммы в рублях цифрами, «до» не меньше «от».",
}


def check_contact(raw):
    """Телефон или email → (значение, "phone" | "email", None) или (None, None, код ошибки)."""
    value = " ".join(str(raw or "").split())
    if not value:
        return None, None, "empty"
    if len(value) > CONTACT_MAX:
        return None, None, "long"
    if "@" in value:
        ok = EMAIL_RE.fullmatch(value) and len(value.split("@")[0]) <= 64
        return (value, "email", None) if ok else (None, None, "email")
    digits = sum(ch.isdigit() for ch in value)
    ok = (PHONE_RE.fullmatch(value) and PHONE_DIGITS[0] <= digits <= PHONE_DIGITS[1]
          and value.count("(") == value.count(")") <= 1 and value.find("(") <= value.find(")"))
    return (value, "phone", None) if ok else (None, None, "phone")


def _side(raw):
    text = str(raw or "").strip()
    if not SIDE_RE.fullmatch(text):
        return None
    n = float(text.replace(",", "."))
    return n if n >= MIN_SIDE else None


def _number(n):
    """6.0 → 6, 4.5 → 4.5: в заявке и админке без лишнего «.0»."""
    n = round(n, 2)
    return int(n) if n == int(n) else n


def check_stand(form, section):
    """Параметры стенда из формы → (stand, ошибки {поле: текст}).

    Пределы площади и типы — из блока конфигуратора в home.json, как на странице: тип с deck
    (второй этаж) на сайте не показывается и в заявке не принимается. Открытые стороны считаются
    по закрытым стенам типа, а не берутся из запроса."""
    errors = {}
    area_cfg = section.get("area") or {}
    area_min = area_cfg.get("min") or DEFAULT_AREA[0]
    area_max = area_cfg.get("max") or DEFAULT_AREA[1]
    length, width = _side(form.get("length")), _side(form.get("width"))
    if length is None:
        errors["length"] = STAND_ERRORS["length"]
    if width is None:
        errors["width"] = STAND_ERRORS["width"]
    area = round(length * width, 2) if length and width else None
    if area is not None and not area_min - 1e-9 <= area <= area_max + 1e-9:
        errors["area"] = STAND_ERRORS["area"]

    types = {t.get("name"): t for t in section.get("types", []) if isinstance(t, dict) and not t.get("deck")}
    stand_type = types.get(form.get("type", ""))
    if stand_type is None:
        errors["type"] = STAND_ERRORS["type"]

    budget_raw = "".join(str(form.get("budget") or "").split())
    budget = None
    if budget_raw:
        if budget_raw.isdigit() and len(budget_raw) <= BUDGET_DIGITS and int(budget_raw) > 0:
            budget = int(budget_raw)
        else:
            errors["budget"] = STAND_ERRORS["budget"]
    calculator = _check_calculator(form, section.get("pricing") or {}, errors)
    if errors:
        return None, errors
    return {"length": _number(length), "width": _number(width), "area": _number(area),
            "type": stand_type["name"], "open_sides": 4 - len(stand_type.get("walls") or []),
            "budget": budget, **calculator}, {}


def _rubles(raw):
    """«1 450 000» → 1450000; пусто → None; не сумма → False."""
    text = "".join(str(raw or "").split())
    if not text:
        return None
    return int(text) if text.isdigit() and len(text) <= BUDGET_DIGITS and int(text) > 0 else False


def _check_calculator(form, pricing, errors):
    """Уровень, опции и ориентир бюджета из калькулятора (static/js/stand-price.js).

    Уровень и опции приходят по id и сохраняются названиями из home.json → pricing — неизвестные
    отклоняются. Ориентир посчитан в браузере клиента: сохраняется как пометка для менеджера
    («ориентир с сайта»), а не как цена. Старые заявки без этих полей остаются верными."""
    level_id = str(form.get("level") or "").strip()
    if not level_id:
        return {}
    result = {}
    levels = {lv.get("id"): lv for lv in pricing.get("levels", []) if isinstance(lv, dict)}
    if level_id not in levels:
        errors["level"] = STAND_ERRORS["level"]
    else:
        result["level"] = levels[level_id].get("name") or level_id
    extras = {e.get("id"): e for e in pricing.get("extras", []) if isinstance(e, dict)}
    wanted = [x for x in dict.fromkeys(str(form.get("extras") or "").split(",")) if x.strip()]
    if any(x not in extras for x in wanted):
        errors["extras"] = STAND_ERRORS["extras"]
    else:
        result["extras"] = [extras[x].get("name") or x for x in wanted]
    low, high = _rubles(form.get("estimate_min")), _rubles(form.get("estimate_max"))
    if low is False or high is False or (high is not None and (low is None or high < low)):
        errors["estimate"] = STAND_ERRORS["estimate"]
    elif low is not None:
        result["estimate"] = {"min": low, "max": high}
    return result


def save_request(folder, record):
    """Записать заявку в отдельный файл (атомарно, права 0600). Возвращает путь; при ошибке — OSError."""
    folder = Path(folder)
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Имя по времени до микросекунд: сортировка имён — порядок заявок, даже двух в одну секунду
    name = f"{datetime.now(timezone.utc):%Y%m%d-%H%M%S-%f}-{secrets.token_hex(4)}.json"
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".request-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, folder / name)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return folder / name


def list_requests(folder):
    """Заявки, новые первыми; повреждённый файл пропускается, а не роняет страницу админки."""
    items = []
    for path in sorted(Path(folder).glob("*.json"), reverse=True) if Path(folder).is_dir() else []:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(record, dict):
            items.append({**record, "file": path.name})
    return items


# Имя файла заявки — как его создаёт save_request; любое другое (путь, служебный .tmp) — чужое
REQUEST_NAME_RE = re.compile(r"\d{8}-\d{6}-\d{6}-[0-9a-f]{8}\.json")


def request_path(folder, name):
    """Путь к файлу заявки по имени из списка или None, если имя не похоже на имя заявки."""
    return Path(folder) / name if REQUEST_NAME_RE.fullmatch(name or "") else None


def read_request(folder, name):
    """Заявка по имени файла или None: имя чужое, файла нет или он повреждён (в списке его тоже нет)."""
    path = request_path(folder, name)
    try:
        record = json.loads(path.read_text(encoding="utf-8")) if path else None
    except (OSError, ValueError):
        return None
    return {**record, "file": name} if isinstance(record, dict) else None


def delete_request(folder, name):
    """Удалить файл заявки безвозвратно. False — заявки уже нет; ошибка доступа — OSError."""
    path = request_path(folder, name)
    if path is None:
        return False
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


class RequestLimiter:
    """Не больше limit заявок за window секунд с одного адреса — от случайных повторов и спама.

    Хранится в памяти процесса, как ограничение попыток входа в админку (auth.LoginLimiter)."""

    def __init__(self, limit=5, window=10 * 60):
        self.limit, self.window = limit, window
        self._lock = threading.Lock()
        self._sent = {}

    def allow(self, key, now=None):
        now = time.monotonic() if now is None else now
        with self._lock:
            recent = [t for t in self._sent.get(key, []) if now - t < self.window]
            if len(recent) >= self.limit:
                self._sent[key] = recent
                return False
            self._sent[key] = recent + [now]
            return True
