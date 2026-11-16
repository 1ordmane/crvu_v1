"""Контент сайта: JSON-файлы в папке content/ (путь — app.config["CONTENT_DIR"]).

Файлы читаются при каждом запросе, поэтому правки видны без перезапуска.
Проверка всей папки: `flask --app app check-content`.
"""

import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse, urlsplit

from . import texts
from .uploads import is_variant

# Планировка стенда: хранится и показывается одним и тем же словом ("layout": "Угловой")
LAYOUTS = ("Угловой", "Остров", "Линейный", "Полуостров")
# Этажность стенда: "floors": 1 или 2; поля нет — этажность не указана (не угадывается по тексту)
FLOORS = {1: "Одноэтажный", 2: "Двухэтажный"}
FILES = ("site", "home", "pages", "contacts", "portfolio")
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".avif"}
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
# Адреса и id элементов, которые уже заняты приложением.
# video-reviews — шторка «Другие видео-отзывы» (app/videos.py)
RESERVED_SLUGS = {"static", "media", "main", "request", "gallery", "admin", "api", "video-reviews"}
PAGE_TYPES = {"portfolio", "about", "contacts", "text"}
SECTION_TYPES = {"configurator", "projects", "reviews", "text", "about", "faq"}
CONTACT_TYPES = {"phone", "email", "address", "link", "text"}
# Соцсети и мессенджеры в разделе «Контакты»: network выбирает значок
SOCIAL_NETWORKS = {"telegram", "max", "vk"}
WALL_SIDES = {"back", "left", "right"}
STATUSES = {"published", "draft"}
# Карточек в блоке «Реализованные проекты» на главной — не больше
FEATURED_MAX = 6
# Карточки видеоотзывов на главной: не больше, чем помещается в два ряда по три
REVIEWS_MAX = 6
FAQ_MAX = 20


class ContentError(Exception):
    pass


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise ContentError(f"{path}: файл не найден") from None
    except json.JSONDecodeError as err:
        raise ContentError(f"{path}: ошибка JSON в строке {err.lineno}, столбце {err.colno}: {err.msg}") from None


def load_content(content_dir):
    content_dir = Path(content_dir)
    return {name: read_json(content_dir / f"{name}.json") for name in FILES}


def visible(items):
    return [item for item in items if item.get("visible", True)]


def safe_http_url(url):
    parsed = urlparse(url or "")
    if parsed.scheme in ("http", "https") and parsed.netloc:
        return url
    return ""


# Видео проекта — Vimeo или Rutube. Из ссылки берутся лишь код видео и ключ закрытого видео;
# адрес плеера и страницы собирается заново, поэтому ввод не попадает в HTML как есть.
VIMEO_ID = r"(?P<id>[0-9]{1,12})"
VIMEO_HASH = re.compile(r"[0-9a-f]{6,40}")
VIMEO_PAGE_PATHS = tuple(re.compile(p) for p in (
    rf"/{VIMEO_ID}(?:/(?P<hash>[0-9a-f]{{6,40}}))?/?",          # vimeo.com/123, vimeo.com/123/abcdef (закрытое)
    rf"/channels/[A-Za-z0-9_-]+/{VIMEO_ID}/?",
    rf"/groups/[A-Za-z0-9_-]+/videos/{VIMEO_ID}/?",
    rf"/(?:showcase|album)/[0-9]+/video/{VIMEO_ID}/?",
))
VIMEO_PLAYER_PATH = re.compile(rf"/video/{VIMEO_ID}/?")
VIMEO_HOSTS = {"vimeo.com", "www.vimeo.com"}


def _video_url_parts(url):
    """https/http-ссылка без логина и порта → (хост, разобранная ссылка); иначе (None, None)."""
    if not isinstance(url, str):
        return None, None
    try:
        parsed = urlsplit(url.strip())
        port = parsed.port
    except ValueError:
        return None, None
    host = parsed.hostname or ""
    if parsed.scheme not in ("http", "https") or port is not None or parsed.netloc.lower() != host:
        return None, None
    return host, parsed


def vimeo_video(url):
    """Ссылка Vimeo → (номер видео, hash или "") ; всё остальное → None.

    Принимаются https/http-ссылки vimeo.com (обычные, каналы, группы, подборки, закрытые
    со вторым кодом) и player.vimeo.com/video/…; без логина, порта и чужих доменов.
    """
    host, parsed = _video_url_parts(url)
    if parsed is None:
        return None
    paths = (VIMEO_PLAYER_PATH,) if host == "player.vimeo.com" else VIMEO_PAGE_PATHS if host in VIMEO_HOSTS else ()
    match = next((m for m in (pattern.fullmatch(parsed.path) for pattern in paths) if m), None)
    if not match:
        return None
    path_hash = match.groupdict().get("hash") or ""
    query_hash = parse_qs(parsed.query).get("h", [])
    if len(query_hash) > 1 or (query_hash and not VIMEO_HASH.fullmatch(query_hash[0])):
        return None
    if path_hash and query_hash and query_hash[0] != path_hash:
        return None
    return match.group("id"), path_hash or (query_hash[0] if query_hash else "")


def vimeo_player_url(url):
    """Адрес плеера для iframe галереи (без автозапуска, dnt=1 — без отслеживания) или ""."""
    video = vimeo_video(url)
    if not video:
        return ""
    video_id, private = video
    return f"https://player.vimeo.com/video/{video_id}?" + (f"h={private}&" if private else "") + "dnt=1"


def vimeo_page_url(url):
    """Страница видео на vimeo.com — для ссылки под карточкой портфолио — или ""."""
    video = vimeo_video(url)
    if not video:
        return ""
    video_id, private = video
    return f"https://vimeo.com/{video_id}" + (f"/{private}" if private else "")


# Rutube: код видео — 32 шестнадцатеричных символа; закрытое видео играет только с ключом ?p=…
# Плеер — https://rutube.ru/play/embed/<код>/[?p=<ключ>] (формат кода встраивания Rutube).
RUTUBE_ID = r"(?P<id>[0-9A-Fa-f]{32})"
RUTUBE_KEY = re.compile(r"[A-Za-z0-9_-]{6,64}")
RUTUBE_PATHS = tuple(re.compile(p) for p in (
    rf"/video/{RUTUBE_ID}/?",
    rf"/video/(?P<private>private)/{RUTUBE_ID}/?",
    rf"/shorts/{RUTUBE_ID}/?",
    rf"/play/embed/{RUTUBE_ID}/?",
))
RUTUBE_HOSTS = {"rutube.ru", "www.rutube.ru"}


def rutube_video(url):
    """Ссылка Rutube → (код видео, ключ закрытого видео или "", закрытое ли); всё остальное → None.

    Принимаются https/http-ссылки rutube.ru: страница видео, закрытое видео с ключом, shorts и
    код встраивания play/embed; без логина, порта и чужих доменов.
    """
    host, parsed = _video_url_parts(url)
    if parsed is None or host not in RUTUBE_HOSTS:
        return None
    match = next((m for m in (pattern.fullmatch(parsed.path) for pattern in RUTUBE_PATHS) if m), None)
    if not match:
        return None
    keys = parse_qs(parsed.query).get("p", [])
    if len(keys) > 1 or (keys and not RUTUBE_KEY.fullmatch(keys[0])):
        return None
    private = bool(match.groupdict().get("private"))
    if private and not keys:
        return None
    return match.group("id").lower(), keys[0] if keys else "", private


def video_player_url(url):
    """Адрес плеера для iframe галереи — Vimeo или Rutube, без автозапуска — или ""."""
    if vimeo_video(url):
        return vimeo_player_url(url)
    video = rutube_video(url)
    if not video:
        return ""
    video_id, key, _ = video
    return f"https://rutube.ru/play/embed/{video_id}/" + (f"?p={key}" if key else "")


def video_page_url(url):
    """Страница видео на Vimeo или Rutube — для ссылки под карточкой портфолио — или ""."""
    if vimeo_video(url):
        return vimeo_page_url(url)
    video = rutube_video(url)
    if not video:
        return ""
    video_id, key, private = video
    if private:
        return f"https://rutube.ru/video/private/{video_id}/?p={key}"
    return f"https://rutube.ru/video/{video_id}/" + (f"?p={key}" if key else "")


def contact_href(contact):
    """Ссылка для контакта или пустая строка, если значение не похоже на свой тип."""
    kind, value = contact.get("type"), (contact.get("value") or "").strip()
    if kind == "phone":
        digits = re.sub(r"[^\d+]", "", value)
        return f"tel:{digits}" if re.fullmatch(r"\+?\d{5,15}", digits) else ""
    if kind == "email":
        return f"mailto:{value}" if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value) else ""
    if kind == "link":
        return safe_http_url(value)
    return ""


def published_contacts(contacts):
    result = []
    for c in contacts:
        value = (c.get("value") or "").strip()
        if value:
            result.append({"type": c.get("type", ""), "label": c.get("label", ""), "value": value,
                           "href": contact_href(c)})
    return result


def published_socials(socials):
    """Ссылки на соцсети для сайта: только известная сеть, подпись и адрес http/https.
    short — короткая подпись для подвала: своя из contacts.json или по сети (texts.SOCIAL_SHORT)."""
    result = []
    for s in socials if isinstance(socials, list) else []:
        if not isinstance(s, dict):
            continue
        label, url = str(s.get("label") or "").strip(), safe_http_url(str(s.get("url") or "").strip())
        if s.get("network") in SOCIAL_NETWORKS and label and url:
            short = str(s.get("short") or "").strip() or texts.SOCIAL_SHORT[s["network"]]
            result.append({"network": s["network"], "label": label, "url": url, "short": short})
    return result


# Плавающая кнопка «Связаться» (contacts.json → quick): позвонить и мессенджеры
QUICK_NETWORKS = ("phone", "telegram", "whatsapp", "max")
QUICK_TEL = re.compile(r"tel:\+?[0-9]{5,15}")


def quick_url(network, url):
    """Проверенный адрес пункта меню «Связаться» или "": у «Позвонить» — tel:+цифры, у мессенджеров — https."""
    url = str(url or "").strip()
    if network == "phone":
        return url if QUICK_TEL.fullmatch(url) else ""
    return url if urlparse(url).scheme == "https" and safe_http_url(url) else ""


def published_quick_contacts(items):
    """Пункты меню «Связаться» для сайта по порядку; без подписи или с пустой/неверной ссылкой — не показываются
    (так пункт MAX ждёт ссылку на профиль, не ломая меню)."""
    result = []
    for q in items if isinstance(items, list) else []:
        if not isinstance(q, dict) or q.get("network") not in QUICK_NETWORKS:
            continue
        label, url = str(q.get("label") or "").strip(), quick_url(q.get("network"), q.get("url"))
        if label and url:
            result.append({"network": q["network"], "label": label, "url": url, "external": q["network"] != "phone"})
    return result


def media_files(media_dir, folder):
    """Картинки в папке media_dir/folder, по имени файла."""
    path = Path(media_dir) / folder
    if not path.is_dir():
        return []
    # 01-640.webp и т.п. — облегчённые версии 01.webp, а не отдельные фото
    files = sorted(p.name for p in path.iterdir() if p.suffix.lower() in IMAGE_EXTENSIONS and not is_variant(p.name))
    return [f"{folder}/{name}" for name in files]


def media_path_exists(content_dir, static_dir, path):
    if path.startswith("static/"):
        return (Path(static_dir) / path.removeprefix("static/")).is_file()
    return (Path(content_dir) / "media" / path).is_file()


def validate_content(content_dir, static_dir):
    """Список понятных ошибок в папке контента; пустой список — всё в порядке."""
    content_dir = Path(content_dir)
    errors = []
    data = {}
    for name in FILES:
        try:
            data[name] = read_json(content_dir / f"{name}.json")
        except ContentError as err:
            errors.append(str(err))
    # Справочник выставок и видеоотзывы — необязательные файлы (app/exhibitions.py, app/videos.py)
    for optional in ("exhibitions", "videos"):
        if (content_dir / f"{optional}.json").exists():
            try:
                data[optional] = read_json(content_dir / f"{optional}.json")
            except ContentError as err:
                errors.append(str(err))
    if errors:
        return errors
    return validate_data(data, content_dir, static_dir)


PRICE_KINDS = {"percent", "urgent", "fixed"}
PRICE_ID_RE = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def check_pricing(p, where):
    """Параметры ориентира бюджета (формула — static/js/stand-price.js) → список ошибок."""
    if not isinstance(p, dict):
        return [f"{where}: ожидается объект"]
    errors = []

    def need(cond, message):
        if not cond:
            errors.append(f"{where}: {message}")

    rate = p.get("rate") if isinstance(p.get("rate"), dict) else {}
    small = rate.get("small") if isinstance(rate.get("small"), dict) else {}
    large = rate.get("large") if isinstance(rate.get("large"), dict) else {}
    for name, part, keys in (("small", small, ("area", "rate_min")), ("large", large, ("area", "rate_min", "rate_max"))):
        for key in keys:
            need(_num(part.get(key)) and part[key] > 0, f"rate.{name}.{key} — положительное число")
    if not errors:
        need(large["area"] > small["area"], "rate.large.area должна быть больше rate.small.area")
        need(large["rate_min"] <= small["rate_min"],
             "ставка за м² не может расти с площадью: rate.large.rate_min ≤ rate.small.rate_min")
        need(large["rate_max"] >= large["rate_min"], "rate.large.rate_max не меньше rate_min")

    sides = p.get("open_sides", {})
    need(isinstance(sides, dict) and all(_num(sides.get(k)) and sides[k] > 0 for k in ("1", "2", "3", "4")),
         "open_sides — положительные множители для «1», «2», «3», «4»")

    levels = p.get("levels")
    need(isinstance(levels, list) and levels, "levels — непустой список уровней")
    ids = []
    for lv in levels if isinstance(levels, list) else []:
        lv = lv if isinstance(lv, dict) else {}
        ids.append(lv.get("id"))
        need(PRICE_ID_RE.match(str(lv.get("id") or "")) and str(lv.get("name") or "").strip(),
             f"levels: «{lv.get('id')}» — нужны id (латиница) и name")
        need(_num(lv.get("factor")) and lv["factor"] > 0, f"levels: «{lv.get('id')}» — factor больше 0")
    extras = p.get("extras", [])
    need(isinstance(extras, list), "extras — список")
    for e in extras if isinstance(extras, list) else []:
        e = e if isinstance(e, dict) else {}
        ids.append(e.get("id"))
        need(PRICE_ID_RE.match(str(e.get("id") or "")) and str(e.get("name") or "").strip(),
             f"extras: «{e.get('id')}» — нужны id (латиница) и name")
        need(e.get("kind") in PRICE_KINDS, f"extras: «{e.get('id')}» — kind: {', '.join(sorted(PRICE_KINDS))}")
        need(_num(e.get("min")) and _num(e.get("max")) and 0 <= e["min"] <= e["max"],
             f"extras: «{e.get('id')}» — 0 ≤ min ≤ max")
    dup = sorted({i for i in ids if i and ids.count(i) > 1})
    need(not dup, f"id повторяется: {', '.join(map(str, dup))}")
    need(isinstance(p.get("round", 10000), int) and p.get("round", 10000) > 0, "round — целое число рублей больше 0")
    return errors


def validate_data(data, content_dir, static_dir):
    """То же, что validate_content, но для уже загруженных данных — проверка до записи."""
    content_dir = Path(content_dir)
    errors = []

    def need(cond, message):
        if not cond:
            errors.append(message)

    site = data["site"]
    need(str(site.get("company", "")).strip(), "site.json: не заполнено поле company")
    email = (site.get("request") or {}).get("email", "")
    need(not email or contact_href({"type": "email", "value": email}),
         f"site.json: request.email «{email}» не похож на адрес почты")

    ids = set()
    sections = data["home"].get("sections", [])
    for i, s in enumerate(sections):
        where = f"home.json: блок {i + 1}"
        need(s.get("type") in SECTION_TYPES, f"{where}: неизвестный type «{s.get('type')}», допустимо: {', '.join(sorted(SECTION_TYPES))}")
        sid = s.get("id", "")
        need(SLUG_RE.match(sid or ""), f"{where}: id «{sid}» — только латиница в нижнем регистре, цифры и дефис")
        need(sid not in ids, f"{where}: id «{sid}» повторяется")
        ids.add(sid)
        if s.get("type") == "projects":
            items = s.get("items", [])
            need(isinstance(items, list) and len(items) <= FEATURED_MAX,
                 f"{where}: items — список не длиннее {FEATURED_MAX} карточек")
            known = {p.get("id") for p in data["portfolio"].get("projects", [])}
            for j, item in enumerate(items if isinstance(items, list) else []):
                card = f"{where}: карточка {j + 1}"
                need(not item.get("project") or item["project"] in known,
                     f"{card}: проекта «{item.get('project')}» нет в portfolio.json")
                image = item.get("image") or ""
                if image:
                    need(Path(image).suffix.lower() in IMAGE_EXTENSIONS, f"{card}: «{image}» — не картинка")
                    need(".." not in Path(image).parts and not Path(image).is_absolute(),
                         f"{card}: «{image}» — путь должен быть относительным и без ..")
                    need(media_path_exists(content_dir, static_dir, image), f"{card}: файл «{image}» не найден")
        if s.get("type") == "faq":
            items = s.get("items", [])
            need(isinstance(items, list) and 0 < len(items) <= FAQ_MAX,
                 f"{where}: items — список вопросов, от 1 до {FAQ_MAX}")
            for j, item in enumerate(items if isinstance(items, list) else []):
                ok = isinstance(item, dict)
                need(ok and isinstance(item.get("question"), str) and item["question"].strip(),
                     f"{where} (faq): вопрос {j + 1} — пустой вопрос")
                need(ok and isinstance(item.get("answer"), str) and item["answer"].strip(),
                     f"{where} (faq): вопрос {j + 1} — пустой ответ")
        if s.get("type") == "reviews":
            items = s.get("items", [])
            need(isinstance(items, list) and len(items) <= REVIEWS_MAX,
                 f"{where}: items — список не больше {REVIEWS_MAX} карточек")
            for j, item in enumerate(items if isinstance(items, list) else []):
                need(isinstance(item, dict), f"{where}: карточка {j + 1} — объект {{}}, а не «{item}»")
                # Ролик на позиции — id записи из videos.json (выбирается в Редакторе); {} — заглушка
                if isinstance(item, dict) and "video" in item:
                    need(isinstance(item["video"], str) and re.fullmatch(r"[0-9a-f]{8}", item["video"]),
                         f"{where}: карточка {j + 1} — video: id видео из videos.json (8 символов 0–9, a–f)")
            linked = [it.get("video") for it in items if isinstance(it, dict) and it.get("video")] \
                if isinstance(items, list) else []
            need(len(linked) <= 3 and len(set(linked)) == len(linked),
                 f"{where}: на главной — до 3 разных видео")
        if s.get("type") == "configurator":
            area = s.get("area", {})
            lo, hi, default = area.get("min"), area.get("max"), area.get("default")
            need(all(isinstance(v, int) for v in (lo, hi, default)) and 0 < lo < hi and lo <= default <= hi,
                 f"{where}: area должна быть целыми числами min < max, min ≤ default ≤ max")
            types = s.get("types", [])
            need(types, f"{where}: нет ни одного типа стенда")
            names = [t.get("name") for t in types]
            need(all(names) and len(set(names)) == len(names), f"{where}: у типов стенда пустые или повторяющиеся name")
            for t in types:
                need(set(t.get("walls", [])) <= WALL_SIDES,
                     f"{where}: walls у «{t.get('name')}» — только back, left, right")
            if "pricing" in s:
                errors.extend(check_pricing(s["pricing"], f"{where}: pricing"))

    # Тексты, которые правит админка: строки остаются строками, списки — списками строк
    errors.extend(texts.check(data))

    pages = data["pages"].get("pages", [])
    slugs = set()
    for i, p in enumerate(pages):
        slug = p.get("slug", "")
        where = f"pages.json: раздел «{slug or i + 1}»"
        need(SLUG_RE.match(slug or ""), f"{where}: slug — только латиница в нижнем регистре, цифры и дефис")
        need(slug not in RESERVED_SLUGS, f"{where}: slug занят приложением")
        need(slug not in ids, f"{where}: slug совпадает с id блока главной")
        need(slug not in slugs, f"{where}: slug повторяется")
        slugs.add(slug)
        need(p.get("type") in PAGE_TYPES, f"{where}: неизвестный type «{p.get('type')}», допустимо: {', '.join(sorted(PAGE_TYPES))}")
        need(p.get("title"), f"{where}: не заполнен title")
        if p.get("type") == "about":
            need(isinstance(p.get("founded_year"), int), f"{where}: founded_year должен быть числом")
            for stat in p.get("stats", []):
                need(isinstance(stat.get("value"), int), f"{where}: value в stats должен быть целым числом")
    page_types = [p.get("type") for p in pages]
    need(page_types.count("portfolio") <= 1, "pages.json: раздел portfolio может быть только один")

    hero = data["home"].get("hero", {})
    need(not hero.get("secondary_page") or hero["secondary_page"] in slugs,
         f"home.json: hero.secondary_page «{hero.get('secondary_page')}» нет в pages.json")
    need(not hero.get("primary_target") or hero["primary_target"] in ids,
         f"home.json: hero.primary_target «{hero.get('primary_target')}» нет среди id блоков")
    need(hero.get("secondary_action") in (None, "", "request"),
         f"home.json: hero.secondary_action «{hero.get('secondary_action')}» — допустимо только request или пусто")
    need(not hero.get("secondary_target") or hero["secondary_target"] in ids,
         f"home.json: hero.secondary_target «{hero.get('secondary_target')}» нет среди id блоков")
    for s in sections:
        need(not s.get("button_page") or s["button_page"] in slugs,
             f"home.json: button_page «{s.get('button_page')}» нет в pages.json")

    for c in data["contacts"].get("contacts", []):
        need(c.get("type") in CONTACT_TYPES, f"contacts.json: «{c.get('label')}»: неизвестный type «{c.get('type')}»")
        if (c.get("value") or "").strip() and c.get("type") in ("phone", "email", "link"):
            need(contact_href(c), f"contacts.json: «{c.get('label')}»: значение «{c['value']}» не подходит для type {c['type']}")

    socials = data["contacts"].get("socials", [])
    need(isinstance(socials, list), "contacts.json: socials — список")
    for s in socials if isinstance(socials, list) else []:
        where = f"contacts.json: socials «{s.get('label') if isinstance(s, dict) else s}»"
        if not isinstance(s, dict):
            need(False, f"{where}: ожидается объект с network, label, url")
            continue
        need(s.get("network") in SOCIAL_NETWORKS,
             f"{where}: неизвестный network «{s.get('network')}» (можно: {', '.join(sorted(SOCIAL_NETWORKS))})")
        need(str(s.get("label") or "").strip(), f"{where}: пустой label")
        url = str(s.get("url") or "").strip()
        # Пустой url — ссылка не показывается (как пустое значение контакта)
        need(not url or safe_http_url(url), f"{where}: url «{url}» — нужен адрес http:// или https://")
        # short — короткая подпись в подвале, необязательна (пусто — по сети: ТГ, МАХ, ВК)
        short = s.get("short", "")
        need(isinstance(short, str) and len(short) <= texts.LINE_MAX,
             f"{where}: short — строка до {texts.LINE_MAX} символов")

    quick = data["contacts"].get("quick", [])
    need(isinstance(quick, list), "contacts.json: quick — список")
    for q in quick if isinstance(quick, list) else []:
        where = f"contacts.json: quick «{q.get('label') if isinstance(q, dict) else q}»"
        if not isinstance(q, dict):
            need(False, f"{where}: ожидается объект с network, label, url")
            continue
        need(q.get("network") in QUICK_NETWORKS,
             f"{where}: неизвестный network «{q.get('network')}» (можно: {', '.join(QUICK_NETWORKS)})")
        need(str(q.get("label") or "").strip(), f"{where}: пустой label")
        url = str(q.get("url") or "").strip()
        # Пустой url — пункт не показывается (например, MAX до ссылки на профиль)
        need(not url or quick_url(q.get("network"), url),
             f"{where}: url «{url}» — " + ("нужно tel:+79991234567" if q.get("network") == "phone" else "нужен адрес https://"))

    project_ids = set()
    for i, p in enumerate(data["portfolio"].get("projects", [])):
        pid = p.get("id", "")
        where = f"portfolio.json: проект «{pid or i + 1}»"
        need(SLUG_RE.match(pid or ""), f"{where}: id — только латиница в нижнем регистре, цифры и дефис")
        need(pid not in project_ids, f"{where}: id повторяется")
        project_ids.add(pid)
        need(p.get("status") in STATUSES, f"{where}: status должен быть published или draft")
        need(p.get("title"), f"{where}: не заполнен title")
        need(isinstance(p.get("year"), int), f"{where}: year должен быть числом")
        need(isinstance(p.get("order", 0), (int, float)), f"{where}: order должен быть числом")
        month = p.get("month")
        need(month is None or (type(month) is int and 1 <= month <= 12), f"{where}: month — число от 1 до 12")
        need(p.get("layout") is None or p.get("layout") in LAYOUTS,
             f"{where}: layout — одно из: {', '.join(LAYOUTS)}")
        need(p.get("floors") is None or (type(p.get("floors")) is int and p["floors"] in FLOORS),
             f"{where}: floors — 1 (одноэтажный) или 2 (двухэтажный)")
        area = p.get("area")
        need(area is None or (type(area) in (int, float) and area > 0), f"{where}: area — площадь в м², число больше нуля")
        need(not p.get("video") or video_player_url(p["video"]) or p.get("status") == "draft",
             f"{where}: video — ссылка на видео Vimeo или Rutube, например https://vimeo.com/123456789")
        for spec in p.get("specs", []):
            need(spec.get("label") and spec.get("value"), f"{where}: у характеристики пустые label или value")
        for photo in [p.get("cover")] + list(p.get("photos", [])):
            if photo:
                need(Path(photo).suffix.lower() in IMAGE_EXTENSIONS, f"{where}: «{photo}» — не картинка")
                need(".." not in Path(photo).parts and not Path(photo).is_absolute(),
                     f"{where}: «{photo}» — путь должен быть относительным и без ..")
                need(media_path_exists(content_dir, static_dir, photo), f"{where}: файл «{photo}» не найден")
        if p.get("status") == "published":
            has_photos = p.get("photos") or media_files(content_dir / "media", f"portfolio/{pid}")
            need(has_photos, f"{where}: нет фотографий (photos пуст и папка media/portfolio/{pid} пуста)")
    errors.extend(validate_exhibitions(data.get("exhibitions")))
    from .videos import validate as validate_videos      # videos импортирует content
    errors.extend(validate_videos(data.get("videos"), Path(content_dir) / "media"))
    return errors


def validate_exhibitions(data):
    """exhibitions.json: {"exhibitions": [{"name", "year"}]}; одно событие (название + год) — один раз."""
    if data is None:
        return []
    from .exhibitions import NAME_MAX, event_key       # exhibitions импортирует content
    items = data.get("exhibitions") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return ["exhibitions.json: нужен список exhibitions"]
    errors, keys = [], set()
    for i, item in enumerate(items):
        where = f"exhibitions.json: выставка {i + 1}"
        name, year = (item.get("name"), item.get("year")) if isinstance(item, dict) else (None, None)
        if not isinstance(name, str) or not name.strip() or len(name) > NAME_MAX:
            errors.append(f"{where}: name — название до {NAME_MAX} символов")
            continue
        if type(year) is not int:
            errors.append(f"{where}: year должен быть числом")
            continue
        key = event_key(name, year)
        if key in keys:
            errors.append(f"{where}: «{name}» {year} повторяется (регистр и пробелы не различаются)")
        keys.add(key)
    return errors
