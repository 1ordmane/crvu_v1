"""Админ-панель /admin: проекты портфолио, их фотографии и тексты сайта.

Работает с теми же файлами content/, что и сайт. Перед записью изменения
проверяются validate_data, поэтому через админку нельзя сохранить то, что
сломает публичную страницу. Инструкция по входу — content/README.md.
"""

import copy
import hashlib
import os
import re
from datetime import date, datetime
from pathlib import Path

from flask import (Blueprint, abort, current_app, flash, get_template_attribute, jsonify, make_response, redirect,
                   render_template, request, send_file, url_for)
from PIL import Image, ImageOps
from werkzeug.exceptions import RequestEntityTooLarge

from . import auth, drafts, leads, texts
from . import exhibitions as expo
from . import videos as video_lib
from .content import (FEATURED_MAX, FLOORS, IMAGE_EXTENSIONS, LAYOUTS, ContentError, load_content, read_json, validate_data,
                      validate_exhibitions, video_player_url)
from .portfolio import MONTHS, chronological_key, existing_photos, project_photos
from .routes import render_page
from .storage import content_lock, history_stamp, move_to_history, write_json
from .uploads import (ORIGINAL_EXTENSIONS, UploadError, check_upload, next_photo_path, original_paths, process_image, read_upload,
                      read_zip, read_zip_photo, save_image, save_original, skip_reason, variant_paths)

bp = Blueprint("admin", __name__, url_prefix="/admin")

TITLE_MAX = 200
FIELD_MAX = 200
TEXT_MAX = 5000
FIRST_YEAR = 1990
AREA_MAX = 100_000
# Площадь в м²: целое или с одним-двумя знаками после точки или запятой («40», «40,5», «12.25»)
AREA_RE = re.compile(r"[0-9]{1,6}(?:[.,][0-9]{1,2})?")
TRANSLIT = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z",
    "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
})


def content_dir():
    return Path(current_app.config["CONTENT_DIR"])


def limiter():
    return current_app.extensions.setdefault("admin_login_limiter", auth.LoginLimiter())


@bp.before_request
def guard():
    if not auth.admin_enabled():
        abort(404)
    if request.method == "POST":
        cfg = current_app.config
        if request.endpoint in ("admin.new_project", "admin.upload_photos", "admin.import_project"):
            request.max_content_length = cfg["ADMIN_MAX_UPLOAD_BYTES"]
        elif request.endpoint in ("admin.video_add", "admin.video_cover"):
            # Видеофайл и обложка одним запросом
            request.max_content_length = cfg["ADMIN_MAX_VIDEO_BYTES"] + cfg["ADMIN_VIDEO_COVER_MARGIN"]
        else:
            request.max_content_length = cfg["ADMIN_MAX_FORM_BYTES"]
        if not auth.csrf_valid():
            abort(400, "Форма устарела. Обновите страницу и повторите действие.")
    if request.endpoint != "admin.login" and not auth.is_admin():
        return redirect(url_for("admin.login"))
    return None


@bp.after_request
def private_headers(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@bp.errorhandler(RequestEntityTooLarge)
def too_large(_err):
    if request.endpoint in ("admin.video_add", "admin.video_cover"):
        mb = current_app.config["ADMIN_MAX_VIDEO_BYTES"] // (1024 * 1024)
        return jsonify(ok=False, field="file", message=f"Файл слишком большой: видео — не больше {mb} МБ."), 413
    mb = current_app.config["ADMIN_MAX_UPLOAD_BYTES"] // (1024 * 1024)
    flash(f"Слишком большой объём за один раз (больше {mb} МБ). Загрузите фото частями.", "error")
    project_id = (request.view_args or {}).get("project_id")
    if project_id:
        return redirect(url_for("admin.edit_project", project_id=project_id))
    return redirect(url_for("admin.projects"))


@bp.context_processor
def admin_context():
    cfg = current_app.config
    return {
        "csrf_token": auth.csrf_token,
        "is_admin": auth.is_admin,
        "limits": {
            "image_mb": cfg["ADMIN_MAX_IMAGE_BYTES"] // (1024 * 1024),
            "upload_mb": cfg["ADMIN_MAX_UPLOAD_BYTES"] // (1024 * 1024),
            "files": cfg["ADMIN_MAX_FILES"],
            "side": cfg["ADMIN_IMAGE_MAX_SIDE"],
        },
    }


# --- Вход ---

@bp.route("/login", methods=["GET", "POST"])
def login():
    if auth.is_admin():
        return redirect(url_for("admin.projects"))
    error, status = None, 200
    if request.method == "POST":
        key = request.remote_addr or "unknown"
        wait = limiter().blocked_for(key)
        if wait:
            error, status = f"Слишком много неудачных попыток. Повторите через {wait // 60 + 1} мин.", 429
        elif auth.check_credentials(request.form.get("username", ""), request.form.get("password", "")):
            limiter().reset(key)
            auth.log_in()
            return redirect(url_for("admin.projects"))
        else:
            limiter().fail(key)
            error, status = "Неверный логин или пароль.", 401
    return render_template("admin/login.html", error=error), status


@bp.route("/logout", methods=["POST"])
def logout():
    auth.log_out()
    return redirect(url_for("admin.login"))


# --- Данные ---

def load_portfolio():
    data = read_json(content_dir() / "portfolio.json")
    data.setdefault("projects", [])
    return data


def find_project(data, project_id):
    for project in data["projects"]:
        if project.get("id") == project_id:
            return project
    abort(404)


def save_portfolio(data):
    """Проверить и записать portfolio.json. Возвращает ошибки; пустой список — сохранено.

    Блокируют запись только новые ошибки портфолио: старые проблемы в других
    файлах не мешают работать с проектами.
    """
    cdir, static = content_dir(), current_app.static_folder
    content = load_content(cdir)
    before = set(validate_data(content, cdir, static))
    content["portfolio"] = data
    errors = [e for e in validate_data(content, cdir, static)
              if e.startswith("portfolio.json") and e not in before]
    if not errors:
        write_json(cdir, "portfolio", data)
    return errors


def report(errors, success):
    for error in errors:
        flash(error, "error")
    if not errors:
        flash(success, "ok")


def photos_of(project):
    return project_photos(project, content_dir() / "media")


def originals_dir(project_id):
    """Исходные файлы фото проекта: вне media/, поэтому сайт их не отдаёт."""
    return content_dir() / "originals" / "portfolio" / project_id


def set_photos(project, photos):
    # Первое фото — обложка: так порядок в админке совпадает с порядком на сайте.
    project["photos"] = photos
    project["cover"] = photos[0] if photos else ""


def slugify(text):
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower().translate(TRANSLIT)).strip("-")
    return slug[:50].strip("-") or "project"


def unique_id(data, title, year):
    base = f"{slugify(title)}-{year}"
    taken = {p.get("id") for p in data["projects"]}
    taken |= {p.name for p in (content_dir() / "media" / "portfolio").glob("*")}
    candidate, n = base, 2
    while candidate in taken:
        candidate, n = f"{base}-{n}", n + 1
    return candidate


def parse_area(raw):
    """«40», «40,5», «12.25» → 40, 40.5, 12.25; иначе None. Больше нуля и не больше AREA_MAX."""
    raw = (raw or "").strip()
    if not AREA_RE.fullmatch(raw):
        return None
    value = float(raw.replace(",", "."))
    if not 0 < value <= AREA_MAX:
        return None
    return int(value) if value.is_integer() else value


def read_form(layout_required=True, title_required=True):
    """Поля проекта из формы → (значения, ошибки). Только поля формы: старые (город, категория,
    характеристики, «Пример») в форме не показываются и при сохранении не меняются.

    layout_required=False — только для старого проекта, у которого планировки ещё нет: пустой выбор
    тогда не ошибка и поле layout не записывается. title_required=False — для ZIP (название из папки)."""
    form = request.form
    errors = []
    values = {}
    max_year = date.today().year + 1

    values["title"] = form.get("title", "").strip()
    if not values["title"] and title_required:
        errors.append("Укажите название компании или стенда.")
    elif len(values["title"]) > TITLE_MAX:
        errors.append(f"Название — не длиннее {TITLE_MAX} символов.")

    values["exhibition"] = form.get("exhibition", "").strip()
    if not values["exhibition"]:
        errors.append("Укажите выставку.")
    elif len(values["exhibition"]) > FIELD_MAX:
        errors.append(f"Выставка — не длиннее {FIELD_MAX} символов.")

    raw_year = form.get("year", "").strip()
    values["year"] = int(raw_year) if raw_year.isdigit() else raw_year
    if not isinstance(values["year"], int) or not FIRST_YEAR <= values["year"] <= max_year:
        errors.append(f"Год — число от {FIRST_YEAR} до {max_year}.")

    raw_month = form.get("month", "").strip()
    values["month"] = int(raw_month) if raw_month.isdigit() else raw_month
    if not isinstance(values["month"], int) or not 1 <= values["month"] <= 12:
        errors.append("Месяц — выберите от января до декабря.")

    raw_area = form.get("area", "")
    values["area"] = parse_area(raw_area)
    if values["area"] is None:
        values["area_text"] = raw_area.strip()       # вернуть в форму как ввели
        errors.append(f"Площадь — число в м² больше нуля, например 40 или 40,5 (не больше {AREA_MAX:,}).".replace(",", " "))

    # Планировка — только одно из LAYOUTS, слово в слово; HTML-список проверку не заменяет
    layout = form.get("layout", "")
    if layout in LAYOUTS:
        values["layout"] = layout
    elif layout or layout_required:
        values["layout_text"] = layout
        errors.append(f"Планировка стенда — выберите одну из: {', '.join(LAYOUTS)}.")

    # Этажность — необязательна: пусто — не указана (поле floors не записывается)
    raw_floors = form.get("floors", "")
    if raw_floors in {str(n) for n in FLOORS}:
        values["floors"] = int(raw_floors)
    elif raw_floors:
        errors.append("Этажность — одноэтажный, двухэтажный или «не указана».")

    values["description"] = form.get("description", "").replace("\r\n", "\n").strip()
    if len(values["description"]) > TEXT_MAX:
        errors.append(f"Описание — не длиннее {TEXT_MAX} символов.")

    values["video"] = form.get("video", "").strip()
    # Видео — ссылка Vimeo или Rutube; принимается только то, из чего собирается адрес плеера
    if values["video"] and not video_player_url(values["video"]):
        errors.append("Ссылка на видео — только Vimeo или Rutube, например https://vimeo.com/123456789 или "
                      "https://rutube.ru/video/<код видео>/ (закрытое видео — со вторым кодом или ключом ?p=…).")
    return values, errors


def area_field(project):
    """Значение поля «Площадь»: area проекта или старая характеристика «Площадь …», если это число."""
    if "area_text" in project:
        return project["area_text"]
    area = project.get("area")
    if isinstance(area, (int, float)) and not isinstance(area, bool):
        return f"{area:g}".replace(".", ",")
    for spec in project.get("specs", []):
        if (spec.get("label") or "").casefold().startswith("площад"):
            value = re.sub(r"\s*(м²|м2|кв\.?\s*м\.?)\s*$", "", (spec.get("value") or "").strip())
            if parse_area(value) is not None:
                return value
    return ""


def render_form(project, errors=(), status=200, is_new=False):
    photos = [] if is_new else photos_of(project)
    # Планировку можно не выбирать только у сохранённого старого проекта, где её ещё нет
    stored = {} if is_new else next((p for p in load_portfolio()["projects"] if p.get("id") == project.get("id")), {})
    layout_optional = not is_new and stored.get("layout") not in LAYOUTS
    years = list(range(date.today().year + 1, FIRST_YEAR - 1, -1))
    return render_template("admin/project_form.html", project=project, photos=photos, errors=errors, is_new=is_new,
                           years=years, months=MONTHS, layouts=LAYOUTS, floors=FLOORS, layout_optional=layout_optional,
                           area=area_field(project),
                           exhibition_options=exhibition_options()), status


def exhibition_options():
    """Подсказки для поля «Выставка»: название и годы, в которые она уже была."""
    return expo.name_options(expo.directory(load_portfolio()["projects"], load_exhibitions()))


# --- Проекты ---

@bp.route("")
def projects():
    """Проекты деревом «год → месяц → выставка», с поиском и фильтрами (GET, без скриптов)."""
    items = load_portfolio()["projects"]
    events = expo.directory(items, load_exhibitions())
    filters = read_filters(events)
    shown = [p for p in items if expo.matches(p, filters["q"], filters["year"], filters["month"], filters["event"],
                                              filters["layout"])]
    filtering = any(filters[k] not in ("", None) for k in ("q", "year", "month", "event", "layout"))
    rows = {p.get("id"): photos_of(p) for p in shown}
    years = sorted({e["year"] for e in events} | {y for y in (expo.project_year(p) for p in items) if y}, reverse=True)
    return render_template("admin/projects.html", tree=expo.tree(shown), photos=rows, total=len(items),
                           shown=len(shown), filters=filters, filtering=filtering,
                           open_all=filtering or request.args.get("open") == "all", years=years, months=MONTHS,
                           events=events, published=sum(p.get("status") == "published" for p in items),
                           current_year=date.today().year, layouts=LAYOUTS, exhibition_options=exhibition_options(),
                           form_years=list(range(date.today().year + 1, FIRST_YEAR - 1, -1)))


def read_filters(events):
    """Поиск и фильтры из адреса; неизвестные значения просто не применяются."""
    args = request.args
    year, month = args.get("year", ""), args.get("month", "")
    event = args.get("event", "")
    return {
        "q": args.get("q", "").strip()[:200],
        "year": int(year) if year.isdigit() and len(year) == 4 else None,
        "month": int(month) if month.isdigit() and 1 <= int(month) <= 12 else None,
        "event": event if event in {e["key"] for e in events} else "",
        "layout": args.get("layout", "") if args.get("layout", "") in LAYOUTS else "",
    }


# --- Заявки с сайта ---

def describe_request(item):
    contact = str(item.get("contact") or "")
    # Ссылка для звонка или письма: в tel: — только «+» и цифры
    item["href"] = ("mailto:" + contact if item.get("contact_type") == "email"
                    else "tel:" + re.sub(r"[^\d+]", "", contact))
    try:
        item["when"] = datetime.fromisoformat(item.get("created", "")).astimezone().strftime("%d.%m.%Y %H:%M")
    except (TypeError, ValueError):
        item["when"] = ""
    return item


@bp.route("/requests")
def requests_list():
    """Заявки «Заказать дизайн-проект» (leads.py): контакт и параметры стенда, новые первыми."""
    items = [describe_request(item) for item in leads.list_requests(current_app.config["REQUESTS_DIR"])]
    return render_template("admin/requests.html", items=items)


@bp.route("/requests/<name>/delete", methods=["GET", "POST"])
def delete_request(name):
    """GET — подтверждение, POST (с CSRF, как все формы админки) — удаление файла заявки безвозвратно."""
    folder = current_app.config["REQUESTS_DIR"]
    if leads.request_path(folder, name) is None:
        abort(404)
    missing = "Заявка не найдена — возможно, её уже удалили."
    if request.method == "GET":
        item = leads.read_request(folder, name)
        if item is None:
            flash(missing, "error")
            return redirect(url_for("admin.requests_list"))
        return render_template("admin/request_delete.html", item=describe_request(item))
    try:
        deleted = leads.delete_request(folder, name)
    except OSError:
        flash("Не удалось удалить заявку: нет доступа к файлу. Заявка осталась в списке.", "error")
    else:
        flash("Заявка удалена." if deleted else missing, "ok" if deleted else "error")
    return redirect(url_for("admin.requests_list"))


# --- Видеоотзывы ---
# Библиотека роликов (videos.json) правится только в Редакторе, группа «Видеоотзывы»: как и тексты,
# правки уходят в черновик и попадают на сайт по «Опубликовать». Ролик — ссылка RuTube (сайт хранит код
# и ссылку, адрес плеера собирает заново из проверенного кода) или загруженный файл MP4/WebM
# (content/media/videos/files/). Своя обложка — content/media/videos/covers/ (app/videos.py).

VIDEO_LINK_ERROR = ("нужна ссылка RuTube на ролик — например https://rutube.ru/video/<код>/ "
                    "или https://rutube.ru/play/embed/<код>. Код встраивания (iframe) не подходит.")
THUMB_FAILED = ("Обложку с RuTube получить не удалось — вместо неё значок воспроизведения. "
                "Попробуйте позже кнопкой «Получить обложку» в группе «Видеоотзывы».")
# Кнопки группы «Видеоотзывы»: name="video_action", value — действие и id ролика
VIDEO_ACTION_RE = re.compile(r"(add)|(delete|up|down|thumb):([0-9a-f]{8})")
VIDEO_FIELDS = ("url", "title", "description")
TEXT_FIELDS = ("title", "description")
# Обложка из формы: не больше этого по длинной стороне до перевода в WebP (videos.COVER_SIZE)
COVER_MAX_SIDE = 1920


def player_of(link):
    return video_lib.card({**link, "active": True})["player"] if link else ""


def media_dir():
    return content_dir() / "media"


def video_thumb_url(code):
    return url_for("main.media", filename=video_lib.thumb_path(code)) if video_lib.has_thumb(media_dir(), code) else ""


def cover_url(video):
    """Адрес обложки ролика для админки: своя обложка или копия картинки RuTube; нет — ""."""
    path = video_lib.cover_of(video, media_dir())
    return url_for("main.media", filename=path) if path else ""


def file_url(video):
    """Адрес загруженного видео (/media/videos/files/…) или "" — у роликов RuTube."""
    path = video.get("file") if video_lib.is_file(video) else None
    return url_for("main.media", filename=path) if isinstance(path, str) and video_lib.FILE_PATH_RE.fullmatch(path) else ""


def source_of(video):
    return "Файл" if video_lib.is_file(video) else "RuTube"


def ensure_thumbnail(video):
    """Скачать обложку RuTube, если её ещё нет; False — не получилось (или скачивание выключено).
    Загруженному видео и ролику со своей обложкой скачивать нечего."""
    if video_lib.is_file(video) or video.get("cover"):
        return True
    if video_lib.has_thumb(media_dir(), video.get("rutube")):
        return True
    if not current_app.config.get("VIDEO_THUMBNAILS"):
        return False
    return video_lib.fetch_thumbnail(media_dir(), video)


def video_name(video):
    return video.get("title") or "без названия"


def read_video_texts(form, prefix, where):
    """Название и описание ролика из формы → (подписи, ошибки)."""
    title = video_lib.clean(form.get(f"{prefix}.title"), video_lib.TITLE_MAX)
    description = video_lib.clean(form.get(f"{prefix}.description"), video_lib.DESCRIPTION_MAX)
    errors = []
    if title is None:
        errors.append(f"{where}: название — не длиннее {video_lib.TITLE_MAX} символов.")
    if description is None:
        errors.append(f"{where}: описание — не длиннее {video_lib.DESCRIPTION_MAX} символов.")
    return {"title": title, "description": description}, errors


def read_video_fields(form, prefix, where):
    """Поля ролика RuTube из формы (prefix — video.<id> или video.new) → (подписи, {"rutube", "url"} или None, ошибки)."""
    link = video_lib.parse_link(str(form.get(f"{prefix}.url") or ""))
    errors = [] if link else [f"{where}: {VIDEO_LINK_ERROR}"]
    texts_, text_errors = read_video_texts(form, prefix, where)
    return texts_, link, errors + text_errors


def apply_video_library(data, form):
    """Правки библиотеки из группы «Видеоотзывы» (data — копия videos.json, меняется на месте).

    Поля каждого показанного ролика (у загруженного — без ссылки), затем действие кнопки: добавить,
    удалить, выше/ниже, обложка. Заполненная форма «+ Добавить видео» (ссылка RuTube) добавляет ролик
    и при обычном «Сохранить черновик». → (ошибки, сообщения, ролики, которым нужна обложка RuTube)."""
    videos = data["videos"]
    errors, notes, covers = [], [], []
    for video in videos:
        if not isinstance(video, dict):
            continue
        prefix = f"video.{video.get('id')}"
        if video_lib.is_file(video):
            if f"{prefix}.title" not in form:
                continue
            texts_, row_errors = read_video_texts(form, prefix, f"Видео «{video_name(video)}»")
            errors += row_errors
            if not row_errors:
                video.update(**texts_, active=form.get(f"{prefix}.active") == "1")
            continue
        if f"{prefix}.url" not in form:
            continue
        texts_, link, row_errors = read_video_fields(form, prefix, f"Видео «{video_name(video)}»")
        errors += row_errors
        if row_errors:
            continue
        if link["rutube"] != video.get("rutube"):
            if any(isinstance(v, dict) and v is not video and v.get("rutube") == link["rutube"] for v in videos):
                errors.append(f"Видео «{video_name(video)}»: этот ролик уже есть в библиотеке.")
                continue
            covers.append(video)
        video.update(rutube=link["rutube"], url=link["url"], **texts_,
                     active=form.get(f"{prefix}.active") == "1")

    match = VIDEO_ACTION_RE.fullmatch(form.get("video_action", ""))
    action, vid = (match.group(1) or match.group(2), match.group(3)) if match else ("", None)
    target = next((v for v in videos if isinstance(v, dict) and v.get("id") == vid), None) if vid else None
    if vid and target is None:
        errors.append("Этого видео уже нет в библиотеке — обновите страницу.")
    elif action == "delete":
        videos.remove(target)
        kept = "Файл видео сохранится в истории версий." if video_lib.is_file(target) else "Ролик на RuTube остаётся."
        notes.append(f"Видео «{video_name(target)}» удалено из библиотеки (в черновике). {kept}")
    elif action in ("up", "down"):
        i = videos.index(target)
        j = i + (1 if action == "down" else -1)
        if 0 <= j < len(videos):
            videos[i], videos[j] = videos[j], videos[i]
    elif action == "thumb":
        covers.append(target)

    new = {key: str(form.get(f"video.new.{key}") or "").strip() for key in VIDEO_FIELDS}
    if action == "add" and not new["url"]:
        errors.append("Новое видео: вставьте ссылку RuTube.")
    elif new["url"]:
        texts_, link, new_errors = read_video_fields(form, "video.new", "Новое видео")
        if link and any(isinstance(v, dict) and v.get("rutube") == link["rutube"] for v in videos):
            new_errors.append("Новое видео: этот ролик уже есть в библиотеке.")
        errors += new_errors
        if not new_errors:
            video = {"id": video_lib.new_id(videos), "rutube": link["rutube"], "url": link["url"], **texts_,
                     "active": True}
            videos.append(video)
            covers.append(video)
            notes.append(f"Видео «{video_name(video)}» добавлено. Поставить его на главную — «Выбрать видео…».")
    return errors, notes, covers


def video_catalog(data):
    """Ролики для окна выбора: подписи, источник, включён ли, обложка (по черновику библиотеки)."""
    return [{"id": v.get("id"), "title": v.get("title") or "Видео без названия", "description": v.get("description") or "",
             "active": v.get("active", True) is True, "thumb": cover_url(v), "source": source_of(v)}
            for v in data.get("videos", []) if isinstance(v, dict) and v.get("id")]


def video_group_key(home):
    """Ключ группы «Видеоотзывы» в Редакторе (texts.section_group) или None, если блока нет."""
    return next((f"section-{s.get('id')}" for s in home.get("sections", [])
                 if isinstance(s, dict) and s.get("type") == "reviews" and s.get("id")), None)


def video_library(current, shown):
    """Данные группы «Видеоотзывы»: карточки библиотеки (с введёнными значениями после ошибки) и форма добавления."""
    section = next((s for s in current["home"].get("sections", []) if isinstance(s, dict) and s.get("type") == "reviews"),
                   None)
    # Номер — место на главной с учётом пустых мест (заглушек)
    items = section.get("items") if section and isinstance(section.get("items"), list) else []
    featured = [it.get("video") if isinstance(it, dict) else None for it in items[:video_lib.FEATURED_MAX]]
    rows = []
    for v in current["videos"].get("videos", []):
        if not (isinstance(v, dict) and v.get("id")):
            continue
        prefix = f"video.{v['id']}"
        is_file = video_lib.is_file(v)
        fields = TEXT_FIELDS if is_file else VIDEO_FIELDS
        values = {key: shown.get(f"{prefix}.{key}", v.get(key) or "") for key in VIDEO_FIELDS}
        marker = f"{prefix}.title" if is_file else f"{prefix}.url"
        rows.append({**values, "id": v["id"], "name": v.get("title") or "Видео без названия",
                     "active": shown.get(f"{prefix}.active") == "1" if marker in shown else v.get("active", True),
                     "position": featured.index(v["id"]) + 1 if v["id"] in featured else 0,
                     "kind": "file" if is_file else "rutube", "source": source_of(v),
                     "thumb": cover_url(v), "custom_cover": bool(v.get("cover")),
                     "rutube_thumb": not is_file and bool(video_thumb_url(v.get("rutube"))),
                     "file": file_url(v),
                     "page": "" if is_file else video_lib.video_page_url(v.get("url")),
                     "player": "" if is_file else player_of(video_lib.parse_link(v.get("url"))),
                     "changed": any(values[key] != (v.get(key) or "") for key in fields)})
    new = {key: shown.get(f"video.new.{key}", "") for key in VIDEO_FIELDS}
    return {"rows": rows, "new": new, "add_open": any(new.values()) or shown.get("video_action") == "add",
            "thumbs": bool(current_app.config.get("VIDEO_THUMBNAILS")),
            "title_max": video_lib.TITLE_MAX, "description_max": video_lib.DESCRIPTION_MAX, "limits": video_limits()}


def video_limits():
    """Пределы файлов для подсказок и проверки в браузере (макросы импортируются без контекста шаблона)."""
    cfg, mb = current_app.config, 1024 * 1024
    return {"video_bytes": cfg["ADMIN_MAX_VIDEO_BYTES"], "video_mb": cfg["ADMIN_MAX_VIDEO_BYTES"] // mb,
            "image_bytes": cfg["ADMIN_MAX_IMAGE_BYTES"], "image_mb": cfg["ADMIN_MAX_IMAGE_BYTES"] // mb}


def library_card(current, vid):
    """HTML карточки библиотеки (тот же макрос, что на странице) — для вставки без перезагрузки."""
    lib = video_library(current, {})
    row = next((r for r in lib["rows"] if r["id"] == vid), None)
    if row is None:
        return ""
    return get_template_attribute("admin/_video_library.html", "video_row")(row, lib, len(lib["rows"]) - 1, len(lib["rows"]))


def apply_video_selection(home, form, known):
    """Выбор роликов из окна → позиции блока «Нам доверяют свои проекты»: {"video": id} по порядку,
    свободные позиции до трёх — {} (заглушка). Выбор не менялся — items не трогаются.

    known — включённые ролики библиотеки после правок: удалённый или выключенный ролик с главной уходит,
    его место занимают следующие. → (ошибки, id убранных с главной)."""
    errors, dropped = [], []
    for i, section in enumerate(home.get("sections", [])):
        if not (isinstance(section, dict) and section.get("type") == "reviews"):
            continue
        raw = form.get(f"sections.{i}.items")
        if raw is None:
            continue
        ids = []
        for part in raw.split(","):
            vid = part.strip()
            if vid and vid not in ids:
                ids.append(vid)
        dropped += [vid for vid in ids if vid not in known]
        ids = [vid for vid in ids if vid in known]
        items = section.get("items") if isinstance(section.get("items"), list) else []
        if ids == video_lib.featured_ids(items):
            continue
        if len(ids) > video_lib.FEATURED_MAX:
            errors.append(f"На главной — не больше {video_lib.FEATURED_MAX} видео.")
            continue
        section["items"] = [{"video": vid} for vid in ids] + \
            [{} for _ in range(video_lib.FEATURED_MAX - len(ids))]
    return errors, dropped


@bp.route("/editor/video-check")
def video_check():
    """Проверка ссылки в форме «+ Добавить видео»: адрес плеера собирает сервер из кода ролика."""
    link = video_lib.parse_link(request.args.get("url", "")[:500])
    if link is None:
        return jsonify(ok=False, message=VIDEO_LINK_ERROR[0].upper() + VIDEO_LINK_ERROR[1:])
    return jsonify(ok=True, player=player_of(link), page=link["url"])


def receive_cover_upload(field):
    """Картинка обложки из запроса (поле field) → videos.Upload, None (не прислали) или UploadError."""
    file = request.files.get(field)
    if not file or not file.filename:
        return None
    cfg = current_app.config
    img = check_upload(file, cfg["ADMIN_MAX_IMAGE_BYTES"], COVER_MAX_SIDE, cfg["ADMIN_MAX_PIXELS"],
                       cfg["ADMIN_MAX_PIXELS_FULL_DECODE"])
    return video_lib.receive_cover(media_dir(), img)


def video_fail(message, field=None, status=400):
    return jsonify(ok=False, field=field, message=message), status


def stale_library():
    return video_fail("Библиотеку видео изменили, пока был открыт редактор. "
                      "Сохраните остальные правки и обновите страницу.", status=409)


@bp.route("/editor/video-add", methods=["POST"])
def video_add():
    """«+ Добавить видео» (в группе «Видеоотзывы» и в окне выбора): ролик сразу в черновик библиотеки, ответ — JSON.

    source=rutube — ссылка url; source=file — видеофайл file (MP4 или WebM). Обложка cover — по желанию
    (картинка или кадр из видео, снятый в браузере). Страница не перезагружается, поэтому выбор в окне
    и остальные несохранённые поля Редактора остаются как были. Как и «Сохранить черновик», сверяет
    отпечаток videos.json со страницы (version_videos); в ответе — новый отпечаток, данные карточки
    для окна выбора и HTML карточки библиотеки."""
    cdir, cfg = content_dir(), current_app.config
    source = request.form.get("source", "rutube")
    if source not in ("rutube", "file"):
        return video_fail("Выберите, откуда видео: ссылка RuTube или файл.")
    form = {f"video.new.{key}": str(request.form.get(key) or "") for key in VIDEO_FIELDS}
    texts_, errors = read_video_texts(form, "video.new", "Новое видео")
    if errors:
        return video_fail(" ".join(errors), "title")
    link = None
    if source == "rutube":
        if not form["video.new.url"].strip():
            return video_fail("Вставьте ссылку на видео RuTube.", "url")
        link = video_lib.parse_link(form["video.new.url"])
        if link is None:
            return video_fail(f"Новое видео: {VIDEO_LINK_ERROR}", "url")
    upload = cover = None
    try:
        if source == "file":
            file = request.files.get("file")
            if not file or not file.filename:
                return video_fail("Выберите видеофайл MP4 или WebM.", "file")
            try:
                upload = video_lib.receive_video(media_dir(), file.stream, cfg["ADMIN_MAX_VIDEO_BYTES"])
            except video_lib.VideoError as err:
                return video_fail(f"Видео не принято: {err}.", "file")
        try:
            cover = receive_cover_upload("cover")
        except UploadError as err:
            return video_fail(f"Обложка не принята: {err}.", "cover")
        with content_lock(cdir):
            try:
                published, current, _ = load_editor()
            except ContentError as err:
                return video_fail(f"Видео не добавлено: {err}", status=409)
            library = copy.deepcopy(current[video_lib.FILE])
            if request.form.get("version_videos", "") != texts.version_of(library):
                return stale_library()
            if link and any(isinstance(v, dict) and v.get("rutube") == link["rutube"] for v in library["videos"]):
                return video_fail("Этот ролик уже есть в библиотеке — найдите его в списке.", "url")
            video = {"id": video_lib.new_id(library["videos"])}
            if link:
                video.update(rutube=link["rutube"], url=link["url"])
            else:
                video["file"] = upload.place()
            if cover:
                video["cover"] = cover.place()
            video.update(**texts_, active=True)
            library["videos"].append(video)
            # Не прошло проверку — поставленные файлы ни на что не ссылаются, их уберёт sweep()
            errors = check_candidate({video_lib.FILE: library}, [video_lib.FILE])
            if errors:
                return video_fail(" ".join(errors))
            draft = drafts.read(cdir, video_lib.FILE)
            drafts.write(cdir, video_lib.FILE, library,
                         draft["base"] if draft else texts.version_of(published[video_lib.FILE]))
            current = {**current, video_lib.FILE: library}
    finally:
        for item in (upload, cover):
            if item:
                item.discard()
    # Обложка RuTube — после записи черновика, вне блокировки: без неё карточка со значком воспроизведения
    covered = ensure_thumbnail(video)
    message = f"Видео «{video_name(video)}» добавлено в библиотеку (черновик). Нажмите на карточку, чтобы поставить его на главную."
    if not covered and cfg.get("VIDEO_THUMBNAILS"):
        message += " Обложку с RuTube получить не удалось — вместо неё значок воспроизведения."
    return jsonify(ok=True, message=message, version=texts.version_of(library),
                   video={"id": video["id"], "title": video["title"] or "Видео без названия",
                          "description": video["description"], "thumb": cover_url(video), "source": source_of(video)},
                   card=library_card(current, video["id"]))


@bp.route("/editor/video-cover", methods=["POST"])
def video_cover():
    """Обложка ролика из библиотеки (JSON): action=upload — картинка или кадр cover; action=reset — убрать
    свою обложку (у RuTube — снова картинка RuTube). В черновик; прежняя обложка остаётся в истории."""
    cdir = content_dir()
    vid, action = request.form.get("id", ""), request.form.get("action", "upload")
    if not video_lib.ID_RE.fullmatch(vid) or action not in ("upload", "reset"):
        return video_fail("Неверный запрос — обновите страницу.")
    cover = None
    try:
        if action == "upload":
            try:
                cover = receive_cover_upload("cover")
            except UploadError as err:
                return video_fail(f"Обложка не принята: {err}.", "cover")
            if cover is None:
                return video_fail("Выберите картинку или кадр для обложки.", "cover")
        with content_lock(cdir):
            try:
                published, current, _ = load_editor()
            except ContentError as err:
                return video_fail(f"Обложка не сохранена: {err}", status=409)
            library = copy.deepcopy(current[video_lib.FILE])
            if request.form.get("version_videos", "") != texts.version_of(library):
                return stale_library()
            video = next((v for v in library["videos"] if isinstance(v, dict) and v.get("id") == vid), None)
            if video is None:
                return video_fail("Этого видео уже нет в библиотеке — обновите страницу.", status=409)
            if cover:
                video["cover"] = cover.place()
            elif "cover" in video:
                del video["cover"]
            else:
                return jsonify(ok=True, message="Своей обложки и так нет.", version=texts.version_of(library),
                               id=vid, thumb=cover_url(video), custom=False)
            errors = check_candidate({video_lib.FILE: library}, [video_lib.FILE])
            if errors:
                return video_fail(" ".join(errors))
            draft = drafts.read(cdir, video_lib.FILE)
            drafts.write(cdir, video_lib.FILE, library,
                         draft["base"] if draft else texts.version_of(published[video_lib.FILE]))
    finally:
        if cover:
            cover.discard()
    if action == "reset":
        ensure_thumbnail(video)
    message = (f"Обложка видео «{video_name(video)}» сохранена в черновик." if action == "upload" else
               f"У видео «{video_name(video)}» снова обложка по умолчанию (в черновике).")
    return jsonify(ok=True, message=message, version=texts.version_of(library), id=vid, thumb=cover_url(video),
                   custom=bool(video.get("cover")))


# --- Справочник выставок ---

def load_exhibitions():
    return expo.load_stored(content_dir())


def save_exhibitions(items):
    """Проверить и записать content/exhibitions.json (с копией прежней версии в .history/)."""
    data = {"exhibitions": items}
    errors = validate_exhibitions(data)
    if not errors:
        write_json(content_dir(), expo.FILE, data)
    return errors


@bp.route("/exhibitions", methods=["GET", "POST"])
def exhibitions():
    """Выставки по годам: какие проекты к ним относятся; новая выставка — название и год."""
    errors, form = [], {"name": "", "year": date.today().year}
    if request.method == "POST":
        name = expo.clean(request.form.get("name"))
        raw_year = request.form.get("year", "").strip()
        year = int(raw_year) if raw_year.isdigit() else None
        form = {"name": name, "year": year or raw_year}
        max_year = date.today().year + 1
        if not name:
            errors.append("Укажите название выставки.")
        elif len(name) > expo.NAME_MAX:
            errors.append(f"Название — не длиннее {expo.NAME_MAX} символов.")
        if year is None or not FIRST_YEAR <= year <= max_year:
            errors.append(f"Год — число от {FIRST_YEAR} до {max_year}.")
        if not errors:
            with content_lock(content_dir()):
                stored = load_exhibitions()
                existing = expo.directory(load_portfolio()["projects"], stored)
                found, is_new = expo.resolve(name, year, existing)
                if not is_new:
                    errors.append(f"Выставка «{found}» {year} года уже есть — выберите её при создании проекта.")
                else:
                    errors = save_exhibitions(stored + [{"name": name, "year": year}])
            if not errors:
                flash(f"Выставка «{name}» ({year}) добавлена.", "ok")
                return redirect(url_for("admin.exhibitions") + f"#e-{year}")
    return render_exhibitions(form, errors)


def render_exhibitions(form, errors=()):
    events = expo.directory(load_portfolio()["projects"], load_exhibitions())
    by_year = {}
    for event in events:
        by_year.setdefault(event["year"], []).append(event)
    return render_template("admin/exhibitions.html", by_year=sorted(by_year.items(), reverse=True), form=form,
                           errors=errors, years=list(range(date.today().year + 1, FIRST_YEAR - 1, -1))), \
        400 if errors else 200


@bp.route("/exhibitions/delete", methods=["POST"])
def delete_exhibition():
    """Убрать из справочника выставку без проектов; выставку с проектами — нельзя."""
    key = request.form.get("key", "")
    with content_lock(content_dir()):
        stored = load_exhibitions()
        event = next((e for e in expo.directory(load_portfolio()["projects"], stored) if e["key"] == key), None)
        if event is None:
            abort(404)
        if event["projects"]:
            return render_exhibitions({"name": "", "year": date.today().year},
                                      [f"У выставки «{event['name']}» ({event['year']}) есть проекты — "
                                       "её нельзя убрать из справочника."])
        if not event["stored"]:
            abort(404)
        errors = save_exhibitions([e for e in stored if expo.event_key(e.get("name"), e.get("year")) != key])
    report(errors, f"Выставка «{event['name']}» ({event['year']}) убрана из справочника.")
    return redirect(url_for("admin.exhibitions"))


def apply_exhibition(values, data, project_id=None):
    """Название выставки из формы → написание события того же года из справочника (без дублей).
    Возвращает True, если это новое событие. Вызывается под content_lock."""
    others = [p for p in data["projects"] if p.get("id") != project_id]
    values["exhibition"], is_new = expo.resolve(values["exhibition"], values["year"],
                                                expo.directory(others, load_exhibitions()))
    return is_new


def announce_exhibition(values):
    flash(f"Новая выставка «{values['exhibition']}» ({values['year']}) — добавлена в справочник.", "ok")


def upload_items(files):
    """Файлы из формы → [(имя, чтение → (байты, формат))] для _store_photos."""
    cfg = current_app.config
    limits = (cfg["ADMIN_MAX_IMAGE_BYTES"], cfg["ADMIN_MAX_PIXELS"], cfg["ADMIN_MAX_PIXELS_FULL_DECODE"])
    return [(Path(f.filename).name, lambda f=f: read_upload(f, *limits)) for f in files]


@bp.route("/projects/import-folder")
def import_folder():
    """Выбор папки проекта на компьютере: миниатюры, порядок и обложка — в браузере (admin-import.js),
    отправка — обычной формой создания проекта (new_project), тем же путём обработки фото."""
    response = make_response(render_template("admin/import_folder.html", current_year=date.today().year,
                                             years=list(range(date.today().year + 1, FIRST_YEAR - 1, -1)),
                                             months=MONTHS, layouts=LAYOUTS,
                                             exhibition_options=exhibition_options()))
    # Миниатюры выбранных фото показываются до загрузки из памяти браузера (blob:) — только на этой странице
    from . import CONTENT_SECURITY_POLICY
    response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY.replace(
        "img-src 'self'", "img-src 'self' blob:")
    return response


@bp.route("/projects/new", methods=["GET", "POST"])
def new_project():
    """Проект за один шаг: поля, несколько фото и «Опубликовать сразу» — одной формой."""
    if request.method == "GET":
        return render_form({"year": date.today().year, "publish": True}, is_new=True)
    values, errors = read_form()
    # Из выбранной папки — только фото: служебные файлы (Thumbs.db, .DS_Store) молча, прочие — с причиной
    files, skipped = [], []
    for f in request.files.getlist("photos"):
        if f and f.filename:
            reason = skip_reason(f.filename)
            if reason is None:
                files.append(f)
            elif reason:
                skipped.append(reason)
    publish = request.form.get("publish") == "on"
    if len(files) > current_app.config["ADMIN_MAX_FILES"]:
        errors.append(f"За один раз — не больше {current_app.config['ADMIN_MAX_FILES']} файлов.")
    if errors:
        if files:
            errors.append("Фото не загружены — выберите их снова.")
        return render_form({**values, "publish": publish}, errors, 400, is_new=True)
    with content_lock(content_dir()):
        data = load_portfolio()
        new_event = apply_exhibition(values, data)
        # Порядок на сайте и в админке — по дате (год, месяц), поле order больше не нужно
        project = {"id": unique_id(data, values["title"], values["year"]), "status": "draft", **values,
                   "cover": "", "photos": []}
        data["projects"].append(project)
        # Фото — тем же путём, что и на странице проекта; с фото проект записывается вместе с ними
        saved, failed = _store_photos(project["id"], project, data, [], upload_items(files)) if files else ([], [])
        if not saved:
            set_photos(project, [])
            errors = save_portfolio(data)
        elif publish:
            project["status"] = "published"
            errors = save_portfolio(data)
    if errors:
        return render_form({**values, "publish": publish}, errors, 400, is_new=True)
    for message in skipped + failed:
        flash(message, "error")
    if new_event:
        announce_exhibition(values)
    title = project["title"]
    if saved and publish:
        flash(f"Проект «{title}» опубликован. Загружено фото: {len(saved)}.", "ok")
    elif saved:
        flash(f"Проект «{title}» сохранён черновиком. Загружено фото: {len(saved)}.", "ok")
    else:
        flash(f"Проект «{title}» сохранён черновиком: без фото проект не публикуется — добавьте фото ниже.", "ok")
    return redirect(url_for("admin.edit_project", project_id=project["id"]))


PHOTO_ORDER_CHANGED = ("Фотографии проекта изменились (загружены или удалены в другой вкладке) — порядок не "
                       "сохранён. Остальные поля ниже не потеряны: проверьте фото и сохраните ещё раз.")


def read_photo_order(project):
    """Порядок фото из формы проекта (скрытые поля photo_order, первое — обложка) → (порядок, ошибки).

    Принимается только перестановка текущих фото проекта: ни добавить, ни удалить фото так нельзя.
    Без полей (страница без фото) — (None, []), фото не меняются."""
    order = request.form.getlist("photo_order")
    if not order:
        return None, []
    if len(order) != len(set(order)) or set(order) != set(photos_of(project)):
        return None, [PHOTO_ORDER_CHANGED]
    return order, []


@bp.route("/projects/<project_id>", methods=["GET", "POST"])
def edit_project(project_id):
    if request.method == "GET":
        return render_form(find_project(load_portfolio(), project_id))
    stored = find_project(load_portfolio(), project_id)
    # Старый проект без планировки можно сохранить без неё; выбранную планировку убрать нельзя
    values, errors = read_form(layout_required=stored.get("layout") in LAYOUTS)
    order, order_errors = read_photo_order(stored)
    errors += order_errors
    if errors:
        shown = {**stored, **values}
        if order:
            shown.update(photos=order, cover=order[0])   # форма с ошибкой показывает выбранный порядок
        return render_form(shown, errors, 400)
    with content_lock(content_dir()):
        data = load_portfolio()
        project = find_project(data, project_id)
        new_event = apply_exhibition(values, data, project_id)
        project.update(values)
        if "floors" not in values:
            project.pop("floors", None)              # «Не указана» в форме правки
        # Порядок фото — вместе с полями; под блокировкой проверяется ещё раз (фото могли поменять в другой вкладке)
        if order and set(order) != set(photos_of(project)):
            errors = [PHOTO_ORDER_CHANGED]
        elif order and order != photos_of(project):
            set_photos(project, order)
        if not errors:
            errors = save_portfolio(data)
    if errors:
        return render_form(project, errors, 400)
    flash("Изменения сохранены.", "ok")
    if new_event:
        announce_exhibition(values)
    return redirect(url_for("admin.edit_project", project_id=project_id))


@bp.route("/projects/<project_id>/status", methods=["POST"])
def set_status(project_id):
    status = request.form.get("status")
    if status not in ("published", "draft"):
        abort(400)
    with content_lock(content_dir()):
        data = load_portfolio()
        project = find_project(data, project_id)
        if status == "published" and not photos_of(project):
            errors = ["Сначала загрузите хотя бы одно фото — проект без фото на сайте не показывается."]
        elif status == "published" and project.get("layout") not in LAYOUTS:
            errors = ["Сначала выберите планировку стенда в данных проекта и сохраните — без неё проект не публикуется."]
        else:
            project["status"] = status
            errors = save_portfolio(data)
    report(errors, "Проект опубликован." if status == "published" else "Проект снят с публикации и сохранён как черновик.")
    if request.form.get("next") == "list":
        return redirect(url_for("admin.projects") + f"#project-{project_id}")
    return redirect(url_for("admin.edit_project", project_id=project_id))


@bp.route("/projects/<project_id>/delete", methods=["GET", "POST"])
def delete_project(project_id):
    if request.method == "GET":
        return render_template("admin/project_delete.html", project=find_project(load_portfolio(), project_id))
    with content_lock(content_dir()):
        data = load_portfolio()
        project = find_project(data, project_id)
        data["projects"].remove(project)
        errors = save_portfolio(data)
        if not errors:
            stamp = history_stamp()
            move_to_history(content_dir(), content_dir() / "media" / "portfolio" / project_id, stamp)
            move_to_history(content_dir(), originals_dir(project_id), stamp)
    report(errors, f"Проект «{project.get('title')}» удалён. Копия данных и фото сохранена в content/.history/.")
    return redirect(url_for("admin.projects"))


# --- Фотографии ---

def file_digest(path):
    """SHA-256 содержимого файла (читается частями: оригиналы бывают по 40 МБ)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _store_photos(project_id, project, data, existing, items):
    """Сохранить фото в проект (под content_lock): items — [(имя для сообщений, чтение → (байты, формат))].

    Общий путь обычной загрузки и импорта из ZIP: оригинал байт в байт → WebP и версии →
    один раз portfolio.json. Ошибка одного фото не мешает остальным; прерывание до записи
    portfolio.json убирает фото пачки (оригиналы — в историю). Возвращает (сохранённые, ошибки).
    """
    cfg = current_app.config
    folder = content_dir() / "media" / "portfolio" / project_id
    originals = originals_dir(project_id)
    limits = (cfg["ADMIN_MAX_PIXELS"], cfg["ADMIN_MAX_PIXELS_FULL_DECODE"])
    saved, failed = [], []
    # Те же файлы второй раз не добавляются: сравнение по содержимому (SHA-256) с оригиналами
    # фото проекта и с уже принятыми в этой пачке. Оригинал удалённого фото — в истории, его можно вернуть.
    known = {file_digest(path) for path in originals.glob("*") if re.fullmatch(r"\d+\.(jpg|png|webp)", path.name)}

    def keep_original(original):
        # Исходный файл не теряется: фото не добавлено, а оригинал — в истории, не в рабочей папке
        kept = move_to_history(content_dir(), original)
        return f" Исходный файл сохранён в content/{kept.relative_to(content_dir()).as_posix()}." if kept else ""

    def discard(batch):
        # Без записи в portfolio.json фото на сайте нет: версии удаляются, оригиналы — в историю
        stamp = history_stamp()
        for path, original in batch:
            for file in [path, *variant_paths(path)]:
                file.unlink(missing_ok=True)
            move_to_history(content_dir(), original, stamp)

    current = []        # фото, которое обрабатывается сейчас: его оригинал уже на диске
    committed = False   # portfolio.json записан — файлы пачки больше не убираются
    try:
        for name, read in items:
            try:
                upload, fmt = read()
            except UploadError as err:
                failed.append(f"«{name}»: {err}.")
                continue
            digest = hashlib.sha256(upload).hexdigest()
            if digest in known:
                failed.append(f"«{name}»: уже есть в проекте — пропущено.")
                continue
            # Номер свободен и среди фото, и среди оригиналов: существующий файл не перезаписывается
            path = next_photo_path(folder, originals)
            try:
                original = save_original(upload, originals / f"{path.stem}{ORIGINAL_EXTENSIONS[fmt]}")
            except OSError:
                failed.append(f"«{name}»: не удалось сохранить исходный файл на диск.")
                continue
            current = [(path, original)]
            try:
                save_image(process_image(upload, cfg["ADMIN_IMAGE_MAX_SIDE"], *limits), path)
            except UploadError as err:
                failed.append(f"«{name}»: {err}.{keep_original(original)}")
                continue
            except (OSError, MemoryError):
                failed.append(f"«{name}»: не удалось сохранить фото на диск.{keep_original(original)}")
                continue
            except Exception:
                # Непредвиденная ошибка одного фото не обрывает загрузку остальных
                current_app.logger.exception("Не удалось обработать фото «%s»", name)
                failed.append(f"«{name}»: не удалось обработать фото.{keep_original(original)}")
                continue
            saved.append((path, original))
            known.add(digest)
        if saved:
            set_photos(project, existing + [f"portfolio/{project_id}/{p.name}" for p, _ in saved])
            errors = save_portfolio(data)
            committed = not errors
            if errors:
                discard(saved)
                failed += errors
                saved = []
    except BaseException:
        # Обработку прервали (таймаут воркера, остановка сервера) до записи portfolio.json:
        # фото этой пачки не остаются в media/ и originals/ без записи в портфолио.
        # discard повторяем безопасно: отсутствующие файлы пропускаются.
        if not committed:
            discard(saved + current)
        raise
    return saved, failed


@bp.route("/projects/<project_id>/photos", methods=["POST"])
def upload_photos(project_id):
    cfg = current_app.config
    files = [f for f in request.files.getlist("photos") if f and f.filename]
    if not files:
        flash("Выберите хотя бы один файл.", "error")
        return redirect(url_for("admin.edit_project", project_id=project_id))
    if len(files) > cfg["ADMIN_MAX_FILES"]:
        flash(f"За один раз — не больше {cfg['ADMIN_MAX_FILES']} файлов.", "error")
        return redirect(url_for("admin.edit_project", project_id=project_id))

    with content_lock(content_dir()):
        data = load_portfolio()
        project = find_project(data, project_id)
        # До сохранения новых файлов: иначе фото из папки попадут в список дважды.
        saved, failed = _store_photos(project_id, project, data, photos_of(project), upload_items(files))
    for message in failed:
        flash(message, "error")
    if saved:
        flash(f"Загружено фото: {len(saved)}.", "ok")
    return redirect(url_for("admin.edit_project", project_id=project_id) + "#photos")


@bp.route("/projects/import", methods=["POST"])
def import_project():
    """ZIP с фото одного стенда → новый черновик. Архив выбирают вручную; на диск он не распаковывается."""
    cfg = current_app.config
    back = redirect(url_for("admin.projects"))
    archive = request.files.get("archive")
    if not archive or not archive.filename:
        flash("Выберите ZIP-архив с фото стенда.", "error")
        return back
    archive_name = Path(archive.filename).name
    # Те же обязательные поля и проверки, что у формы проекта; название можно не писать — из папки
    values, errors = read_form(title_required=False)
    if errors:
        for error in errors:
            flash(error, "error")
        return back
    title, year = values.pop("title"), values["year"]

    try:
        zip_file, folder, photos, skipped = read_zip(archive.stream, cfg["ADMIN_MAX_FILES"],
                                                     cfg["ADMIN_MAX_IMAGE_BYTES"], cfg["ADMIN_ZIP_MAX_RATIO"])
    except UploadError as err:
        flash(f"«{archive_name}»: {err}.", "error")
        return back
    # Название: из формы, иначе имя папки в архиве, иначе имя архива
    title = (title or folder or Path(archive_name).stem).strip()[:TITLE_MAX] or "Стенд"
    limits = (cfg["ADMIN_MAX_IMAGE_BYTES"], cfg["ADMIN_MAX_PIXELS"], cfg["ADMIN_MAX_PIXELS_FULL_DECODE"])
    items = [(name, lambda info=info: read_zip_photo(zip_file, info, *limits)) for name, info in photos]
    with zip_file, content_lock(content_dir()):
        data = load_portfolio()
        new_event = apply_exhibition(values, data)
        project = {"id": unique_id(data, title, year), "status": "draft", "title": title, **values,
                   "cover": "", "photos": []}
        data["projects"].append(project)
        # Проект попадает в portfolio.json вместе с фото — одной записью; без фото его нет
        saved, failed = _store_photos(project["id"], project, data, [], items)
    for message in skipped + failed:
        flash(message, "error")
    if not saved:
        flash(f"Проект не создан: из «{archive_name}» не загружено ни одного фото.", "error")
        return back
    if new_event:
        announce_exhibition(values)
    flash(f"Создан черновик «{title}». Загружено фото: {len(saved)}. Проверьте данные и опубликуйте.", "ok")
    return redirect(url_for("admin.edit_project", project_id=project["id"]))


@bp.route("/projects/<project_id>/photo", methods=["POST"])
def photo_action(project_id):
    """Действие с одним фото. Из формы без скрипта — сразу сохраняется, ответ — переход на страницу проекта.
    Из admin-photos.js (заголовок X-Requested-With: fetch; так приходит только удаление) — ответ JSON:
    страница не перезагружается, и несохранённые поля проекта в браузере остаются."""
    action, photo = request.form.get("action"), request.form.get("photo", "")
    as_json = request.headers.get("X-Requested-With") == "fetch"
    if action not in ("up", "down", "cover", "delete"):
        abort(400)
    removed = None
    with content_lock(content_dir()):
        data = load_portfolio()
        project = find_project(data, project_id)
        photos = photos_of(project)
        if photo not in photos:
            if as_json:
                # Уже удалено (повторное нажатие, другая вкладка) — второй раз ничего не трогаем
                return jsonify(ok=False, message="Этого фото уже нет в проекте — обновите страницу."), 404
            abort(404)
        i = photos.index(photo)
        if action == "cover":
            photos.insert(0, photos.pop(i))
        elif action in ("up", "down"):
            j = i - 1 if action == "up" else i + 1
            if 0 <= j < len(photos):
                photos[i], photos[j] = photos[j], photos[i]
        else:
            photos.pop(i)
        if action == "delete" and not photos and project.get("status") == "published":
            errors = ["Нельзя удалить последнее фото опубликованного проекта. Сначала снимите проект с публикации."]
        else:
            set_photos(project, photos)
            errors = save_portfolio(data)
            # Файл из папки проекта убираем в историю; кадры из static/ — часть приложения, их не трогаем.
            if not errors and action == "delete" and photo.startswith(f"portfolio/{project_id}/"):
                # Фото, его версии и оригинал — в одну папку истории
                stamp = history_stamp()
                path = content_dir() / "media" / photo
                removed = move_to_history(content_dir(), path, stamp)
                for file in [*variant_paths(path), *original_paths(originals_dir(project_id), path.stem)]:
                    move_to_history(content_dir(), file, stamp)
    messages = {"cover": "Обложка выбрана.", "up": "Порядок фото изменён.", "down": "Порядок фото изменён.",
                "delete": "Фото удалено" + (" (копия в content/.history/)." if removed else " из проекта.")}
    if as_json:
        if errors:
            return jsonify(ok=False, message=" ".join(errors)), 409
        return jsonify(ok=True, message=messages[action], photos=photos_of(project))
    report(errors, messages[action])
    return redirect(url_for("admin.edit_project", project_id=project_id) + "#photos")


# --- Тексты сайта (реестр полей — texts.py) ---

def load_texts():
    """Файлы с текстами и группы редактора. Ошибка чтения или структуры — ContentError."""
    # videos.json — необязательный: пока его нет, библиотека пустая
    data = {name: video_lib.load(content_dir()) if name == video_lib.FILE else read_json(content_dir() / f"{name}.json")
            for name in drafts.EDITOR_FILES}
    for name, value in data.items():
        if not isinstance(value, dict):
            raise ContentError(f"{name}.json: ожидался объект {{ … }} на верхнем уровне")
    try:
        return data, texts.groups(data)
    except (AttributeError, KeyError, TypeError, IndexError):
        raise ContentError("Структура JSON-файлов отличается от ожидаемой. Запустите flask --app app check-content.")


# --- Визуальный редактор ---
# Правки сохраняются в черновик (drafts.py) и попадают на сайт только по «Опубликовать».
# Поля — реестр texts.py: общие тексты, главная, разделы и список контактов;
# HTML, CSS, JS и структура страницы через редактор не меняются.

FILE_TITLES = {"site": "Общие тексты", "home": "Главная", "pages": "Разделы", "contacts": "Контакты",
               "videos": "Видеоотзывы"}


def site_content(cdir):
    """Контент сайта вместе с библиотекой видеоотзывов (её файл необязателен). Ошибка — ContentError."""
    return {**load_content(cdir), video_lib.FILE: video_lib.load(cdir)}


def load_editor():
    """(опубликованные тексты, они же с черновиками поверх, группы редактора). Ошибка — ContentError."""
    published, _ = load_texts()
    current = dict(published)
    for name in drafts.EDITOR_FILES:
        draft = drafts.read(content_dir(), name)
        if draft is not None:
            current[name] = draft["data"]
    try:
        groups = [g for g in texts.groups(current) if g.file in drafts.EDITOR_FILES]
    except (AttributeError, KeyError, TypeError, IndexError):
        raise ContentError("Структура черновика отличается от ожидаемой. Отмените черновик.")
    return published, current, groups


def check_candidate(candidate, names):
    """Новые ошибки проверки контента в файлах names, если на сайте окажутся candidate-тексты."""
    cdir, static = content_dir(), current_app.static_folder
    try:
        content = drafts.overlay(site_content(cdir), cdir)
        before = set(validate_data(content, cdir, static))
        after = validate_data({**content, **candidate}, cdir, static)
    except ContentError as err:
        return [f"Не удалось проверить контент: {err}"]
    except (AttributeError, KeyError, TypeError, IndexError):
        return ["Не удалось проверить контент: структура JSON-файлов отличается от ожидаемой."]
    prefixes = tuple(f"{name}.json" for name in names)
    return [e for e in after if e.startswith(prefixes) and e not in before]


def render_editor(current, groups, shown=None, errors=(), versions=None, status=200):
    cdir = content_dir()
    shown = shown or {}
    values = {}
    for group in groups:
        values.update(texts.values_of(current[group.file], group))
    values.update(shown)
    pending = drafts.existing(cdir)
    catalog, pickers = picker_data(current, shown)
    # Группа, которую держать открытой: после действия с видео (?group=…) или после ошибки в её полях
    video_group = video_group_key(current["home"])
    open_group = request.args.get("group", "")
    if any(key.startswith("video") for key in shown):
        open_group = video_group
    response = make_response(render_template(
        "admin/editor.html", groups=groups, values=values, errors=errors,
        versions=versions or {name: texts.version_of(current[name]) for name in drafts.EDITOR_FILES},
        pending=[FILE_TITLES[name] for name in pending], field_name=texts.field_name,
        catalog=catalog, pickers=pickers, open_group=open_group,
        video_lib=video_library(current, shown) if video_group else None,
    ), status)
    # Предпросмотр — рамка со своего сайта (/admin/editor/preview); без 'self' в frame-src браузер её
    # блокирует. Исключение только для этой страницы: остальные встраивают лишь плееры Vimeo и Rutube.
    # blob: — предпросмотр выбранного видеофайла и обложки (кадра) до загрузки, только на этой странице
    from . import CONTENT_SECURITY_POLICY
    response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY.replace("frame-src ", "frame-src 'self' ") \
        .replace("img-src 'self'", "img-src 'self' blob:") + "; media-src 'self' blob:"
    return response


def picker_data(current, shown):
    """Окно выбора проектов: все проекты портфолио (обложка — миниатюра) и выбранные id блоков «Проекты».

    На главной хранятся только id выбранных проектов в home.json; название и фото берутся из портфолио.
    """
    media, static = content_dir() / "media", current_app.static_folder
    try:
        projects = [p for p in load_portfolio()["projects"] if isinstance(p, dict) and p.get("id")]
    except ContentError:
        projects = []
    catalog = []
    for p in sorted(projects, key=chronological_key):
        photos = existing_photos(project_photos(p, media), media, static)
        catalog.append({"id": p["id"], "title": p.get("title") or p["id"], "year": p.get("year"),
                        "event": p.get("exhibition") or "",
                        "published": p.get("status") == "published",
                        "thumb": url_for("admin.thumb", src=photos[0]) if photos else ""})
    pickers = {}
    for i, section in enumerate(current["home"].get("sections", [])):
        if isinstance(section, dict) and section.get("type") == "projects":
            field = f"sections.{i}.items"
            ids = [it.get("project") for it in section.get("items", []) if isinstance(it, dict) and it.get("project")]
            pickers[f"section-{section.get('id')}"] = {
                "field": field, "value": shown.get(field, ",".join(ids)), "max": FEATURED_MAX}
        elif isinstance(section, dict) and section.get("type") == "reviews":
            # Видеоотзывы — то же окно выбора, каталог — библиотека роликов (черновик, если он есть)
            field = f"sections.{i}.items"
            ids = video_lib.featured_ids(section.get("items"))
            pickers[f"section-{section.get('id')}"] = {
                "field": field, "value": shown.get(field, ",".join(ids)), "max": video_lib.FEATURED_MAX,
                "kind": "videos", "catalog": video_catalog(current[video_lib.FILE]),
                # Форма «+ Добавить видео» в окне — та же, что в группе (без полей с name)
                "lib": {"title_max": video_lib.TITLE_MAX, "description_max": video_lib.DESCRIPTION_MAX,
                        "limits": video_limits(), "new": {}}}
    return catalog, pickers


def apply_project_selection(home, form, known):
    """Выбор проектов из окна → items блоков «Проекты»: только {"project": id} в выбранном порядке.

    Уже выбранная карточка сохраняется вместе со своими полями (если они заданы), новая — только с id.
    Выбор не менялся — items не трогаются (в том числе карточки без проекта).
    """
    errors = []
    for i, section in enumerate(home.get("sections", [])):
        if not (isinstance(section, dict) and section.get("type") == "projects"):
            continue
        raw = form.get(f"sections.{i}.items")
        if raw is None:
            continue
        # Прежний автоотбор «последние N по дате» не действует: на главной — только этот выбор
        section.pop("latest", None)
        section.pop("_latest", None)
        ids = []
        for part in raw.split(","):
            pid = part.strip()
            if pid and pid not in ids:
                ids.append(pid)
        items = section.get("items") if isinstance(section.get("items"), list) else []
        linked = {it.get("project"): it for it in items if isinstance(it, dict) and it.get("project")}
        if ids == list(linked):
            continue
        if len(ids) > FEATURED_MAX:
            errors.append(f"На главной — не больше {FEATURED_MAX} проектов.")
            continue
        # Удалённый из портфолио проект можно оставить или убрать, но не добавить заново
        unknown = [pid for pid in ids if pid not in known and pid not in linked]
        if unknown:
            errors.append(f"Проекта «{unknown[0]}» нет в портфолио.")
            continue
        section["items"] = [linked.get(pid, {"project": pid}) for pid in ids]
    return errors


THUMB_SIZE = (480, 360)


@bp.route("/thumb")
def thumb():
    """Миниатюра фото для окна выбора проектов: до 480 px, WebP, кэш в content/.cache/thumbs/."""
    src = request.args.get("src", "")
    rel = Path(src)
    if rel.suffix.lower() not in IMAGE_EXTENSIONS or rel.is_absolute() or ".." in rel.parts:
        abort(404)
    if src.startswith("static/"):
        base, file = Path(current_app.static_folder), Path(current_app.static_folder) / src.removeprefix("static/")
    else:
        base, file = content_dir() / "media", content_dir() / "media" / src
    try:
        file = file.resolve(strict=True)
        file.relative_to(base.resolve())
    except (OSError, ValueError):
        abort(404)
    stat = file.stat()
    key = hashlib.sha1(f"{file}:{stat.st_mtime_ns}:{stat.st_size}".encode()).hexdigest()
    cache = content_dir() / ".cache" / "thumbs"
    target = cache / f"{key}.webp"
    if not target.exists():
        cache.mkdir(parents=True, exist_ok=True)
        tmp = cache / f".{key}.tmp"
        try:
            with Image.open(file) as img:
                img = ImageOps.exif_transpose(img)
                img.thumbnail(THUMB_SIZE)
                img.convert("RGB").save(tmp, "WEBP", quality=78)
            os.replace(tmp, target)
        except (OSError, ValueError, Image.DecompressionBombError):
            tmp.unlink(missing_ok=True)
            abort(404)
    return send_file(target, mimetype="image/webp")


@bp.route("/editor")
def editor():
    try:
        _, current, groups = load_editor()
    except ContentError as err:
        flash(f"Редактор не открылся: {err}", "error")
        return redirect(url_for("admin.projects"))
    return render_editor(current, groups)


@bp.route("/editor/draft", methods=["POST"])
def editor_draft():
    try:
        published, current, groups = load_editor()
    except ContentError as err:
        flash(f"Черновик не сохранён: {err}", "error")
        return redirect(url_for("admin.editor"))
    changes = {name: {} for name in drafts.EDITOR_FILES}
    shown, errors, updated = {}, [], {}
    for group in groups:
        group_changes, group_shown, group_errors = texts.read_form(request.form, group)
        changes[group.file].update(group_changes)
        shown.update(group_shown)
        errors += group_errors
    sent = {name: request.form.get(f"version_{name}", "") for name in drafts.EDITOR_FILES}
    shown.update({key: value for key, value in request.form.items()
                  if re.fullmatch(r"sections\.\d+\.items", key) or key.startswith("video")})
    notes, covers = [], []
    if not errors:
        cdir = content_dir()
        with content_lock(cdir):
            try:
                published, current, groups = load_editor()
            except ContentError as err:
                errors = [str(err)]
            if not errors and any(sent[name] != texts.version_of(current[name]) for name in drafts.EDITOR_FILES):
                # Кто-то сохранил черновик или тексты, пока была открыта эта страница
                errors = ["Тексты изменились, пока был открыт редактор. Обновите страницу и повторите правку."]
            if not errors:
                try:
                    known = {p.get("id") for p in load_portfolio()["projects"] if isinstance(p, dict)}
                except ContentError as err:
                    known, errors = set(), [str(err)]
                # Библиотека видео — раньше главной: выбор на главную сверяется с ней после правок
                library = copy.deepcopy(current[video_lib.FILE])
                video_errors, notes, covers = apply_video_library(library, request.form)
                errors += video_errors
                known_videos = {v.get("id") for v in library["videos"] if isinstance(v, dict) and v.get("active", True)}
                names = {v.get("id"): video_name(v) for v in current[video_lib.FILE]["videos"] if isinstance(v, dict)}
                for name in drafts.EDITOR_FILES:
                    data = library if name == video_lib.FILE else copy.deepcopy(current[name])
                    if changes[name]:
                        errors += texts.apply(data, changes[name], name)
                    if name == "home":
                        # Сначала тексты (их пути — по прежним номерам карточек), потом новый порядок карточек
                        errors += apply_project_selection(data, request.form, known)
                        selection_errors, dropped = apply_video_selection(data, request.form, known_videos)
                        errors += selection_errors
                        notes += [f"Видео «{names[vid]}» убрано с главной: оно выключено или удалено."
                                  for vid in dropped if vid in names]
                    if data != current[name]:
                        updated[name] = data
            if not errors and updated:
                errors = check_candidate(updated, updated)
            if not errors:
                for name, data in updated.items():
                    draft = drafts.read(cdir, name)
                    base = draft["base"] if draft else texts.version_of(published[name])
                    drafts.write(cdir, name, data, base)
    if errors:
        return render_editor(current, groups, shown, errors, sent, 400)
    if updated:
        flash("Черновик сохранён. На сайте пока прежняя версия — нажмите «Опубликовать», когда будете готовы.", "ok")
    elif not covers:
        flash("Изменений нет — черновик не менялся.", "ok")
    for note in notes:
        flash(note, "ok")
    # Обложки новых роликов и роликов с другой ссылкой — после записи черновика, вне блокировки контента
    asked = request.form.get("video_action", "").startswith("thumb:")
    for video in covers:
        if ensure_thumbnail(video):
            if asked:
                flash(f"Обложка видео «{video_name(video)}» получена.", "ok")
        elif current_app.config.get("VIDEO_THUMBNAILS"):
            flash(f"Видео «{video_name(video)}»: {THUMB_FAILED}", "error")
        elif asked:
            flash("Скачивание обложек выключено (CRVU_VIDEO_THUMBNAILS=0) — на карточке значок воспроизведения.", "error")
    # После правок видео группа «Видеоотзывы» остаётся открытой
    featured = lambda home: [video_lib.featured_ids(s.get("items")) for s in home.get("sections", [])
                             if isinstance(s, dict) and s.get("type") == "reviews"]
    if "video_action" in request.form or video_lib.FILE in updated or \
            ("home" in updated and featured(updated["home"]) != featured(current["home"])):
        return redirect(url_for("admin.editor", group=video_group_key(current["home"])) + "#video-library")
    return redirect(url_for("admin.editor"))


@bp.route("/editor/publish", methods=["POST"])
def editor_publish():
    cdir, static = content_dir(), current_app.static_folder
    errors = []
    with content_lock(cdir):
        pending = {name: draft for name in drafts.EDITOR_FILES if (draft := drafts.read(cdir, name))}
        if not pending:
            flash("Публиковать нечего: черновика нет.", "error")
            return redirect(url_for("admin.editor"))
        try:
            content = site_content(cdir)
        except ContentError as err:
            errors = [f"Не удалось прочитать контент: {err}"]
        if not errors:
            for name, draft in pending.items():
                if texts.version_of(content[name]) != draft["base"]:
                    errors.append(f"«{FILE_TITLES[name]}» ({name}.json) изменили в другом месте после создания "
                                  "черновика. Чтобы не стереть ту правку, черновик "
                                  "не опубликован. Отмените черновик и внесите изменения заново.")
        if not errors:
            try:
                before = set(validate_data(content, cdir, static))
                after = validate_data({**content, **{n: d["data"] for n, d in pending.items()}}, cdir, static)
            except (AttributeError, KeyError, TypeError, IndexError):
                after, before = ["Структура черновика отличается от ожидаемой."], set()
            errors = [e for e in after if e not in before]
        if not errors:
            for name, draft in pending.items():
                write_json(cdir, name, draft["data"])
                drafts.discard(cdir, name)
            # Видео и обложки, не нужные ни сайту, ни истории версий, — в .history/media (не удаляются)
            video_lib.sweep(cdir)
    if errors:
        for error in errors:
            flash(f"Не опубликовано: {error}", "error")
    else:
        flash("Опубликовано. Прежняя версия сохранена в истории.", "ok")
    return redirect(url_for("admin.editor"))


@bp.route("/editor/discard", methods=["POST"])
def editor_discard():
    cdir = content_dir()
    with content_lock(cdir):
        pending = drafts.existing(cdir)
        for name in pending:
            drafts.discard(cdir, name)
        # Файлы, загруженные только в отменённый черновик, — в .history/media (не удаляются)
        video_lib.sweep(cdir)
    flash("Черновик отменён — в редакторе снова опубликованная версия." if pending else "Черновика нет.", "ok")
    return redirect(url_for("admin.editor"))


@bp.route("/editor/history")
def editor_history():
    items = [{"name": name, "file": FILE_TITLES[file], "time": time} for name, file, time in drafts.history(content_dir())]
    return render_template("admin/editor_history.html", items=items, pending=drafts.existing(content_dir()))


@bp.route("/editor/restore", methods=["POST"])
def editor_restore():
    cdir = content_dir()
    try:
        name, data = drafts.read_history(cdir, request.form.get("version", ""))
    except ValueError:
        abort(404)
    with content_lock(cdir):
        try:
            published, _ = load_texts()
            texts.groups({**published, name: data})
        except (ContentError, AttributeError, KeyError, TypeError, IndexError):
            flash("Эту версию нельзя открыть в редакторе: её структура отличается от текущей.", "error")
            return redirect(url_for("admin.editor_history"))
        drafts.write(cdir, name, data, texts.version_of(published[name]))
    flash(f"Версия «{FILE_TITLES[name]}» восстановлена в черновик. Проверьте предпросмотр и опубликуйте, "
          "когда будете готовы — до этого сайт не меняется.", "ok")
    return redirect(url_for("admin.editor"))


@bp.route("/editor/preview")
def editor_preview():
    """Главная из черновика (или выбранной версии из истории) с метками полей для выбора кликом."""
    from . import CONTENT_SECURITY_POLICY

    cdir = content_dir()
    try:
        content = site_content(cdir)
        version = request.args.get("version")
        if version:
            name, data = drafts.read_history(cdir, version)
            content[name] = data
        else:
            content = drafts.overlay(content, cdir)
        response = make_response(render_page(content=content, editing=True))
    except ValueError:
        abort(404)
    except (ContentError, AttributeError, KeyError, TypeError, IndexError) as err:
        return f"Предпросмотр недоступен: {err}", 500
    # Предпросмотр показывается в рамке страницы редактора — разрешаем это только своему сайту
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY.replace(
        "frame-ancestors 'none'", "frame-ancestors 'self'")
    return response
