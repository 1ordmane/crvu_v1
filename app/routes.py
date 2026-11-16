from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from flask import Blueprint, abort, current_app, jsonify, redirect, render_template, request, send_from_directory, url_for
from markupsafe import Markup, escape

from . import leads, videos
from .auth import is_admin
from .content import (IMAGE_EXTENSIONS, load_content, published_contacts, published_quick_contacts, published_socials,
                      read_json, visible)
from .content import FLOORS, ContentError
from .portfolio import AREA_RANGES, FIRST_YEAR, LAYOUT_FILTER, build_years, featured_cards, years_word

bp = Blueprint("main", __name__)

FRAME_COUNT = 160


def no_edit(*parts):
    """На публичном сайте меток редактора нет."""
    return ""


def edit_marker(home):
    """Метка поля для предпросмотра в редакторе: data-edit="sections.1.items.0.title".

    Путь совпадает с именем поля в реестре texts.py. Блок главной передаётся самим словарём —
    его номер в home.json ищется по тождеству (показанные блоки — подмножество всех).
    """
    sections = home.get("sections", []) if isinstance(home.get("sections"), list) else []

    def edit(*parts):
        path = []
        for part in parts:
            if isinstance(part, dict):
                index = next((i for i, s in enumerate(sections) if s is part), None)
                if index is None:
                    return ""
                path += ["sections", str(index)]
            else:
                path.append(str(part))
        return Markup(' data-edit="{}"').format(escape(".".join(path)))
    return edit


def render_page(slug=None, content=None, editing=False):
    """Главная со шторками. content — для предпросмотра черновика; editing — метки полей редактора."""
    content_dir = Path(current_app.config["CONTENT_DIR"])
    if content is None:
        content = load_content(content_dir)
    pages = visible(content["pages"].get("pages", []))
    # Видеоотзывы (content/videos.json): три ролика блока «Нам доверяют» и остальные — в шторке
    # «Другие видео-отзывы» (/video-reviews). Шторка есть, только если блок показан и роликов больше трёх
    reviews = next((s for s in visible(content["home"].get("sections", [])) if s.get("type") == "reviews"), None)
    # В предпросмотре редактора content["videos"] — черновик библиотеки; на сайте — content/videos.json
    video_cards = site_videos(content_dir, reviews.get("items") if reviews else [], content.get("videos"))
    video_sheet = reviews is not None and bool(video_cards["more"])
    if slug is not None and slug not in {p.get("slug") for p in pages} and \
            not (slug == videos.SHEET_SLUG and video_sheet):
        abort(404)

    # Блок «Реализованные проекты» без карточек (только заглушки, а они выключены) не показывается
    projects = content["portfolio"].get("projects", [])
    sections, featured = [], {}
    # Карточки — ровно по списку items в сохранённом порядке (выбор в «Редакторе»), без отбора по дате
    for section in visible(content["home"].get("sections", [])):
        if section.get("type") == "projects":
            cards = featured_cards(section.get("items", []), projects, content_dir / "media",
                                   current_app.static_folder, current_app.config["SHOW_PLACEHOLDERS"])
            if not cards:
                continue
            featured[section.get("id")] = cards
        sections.append(section)

    current_year = date.today().year
    portfolio = next((p for p in pages if p.get("type") == "portfolio"), None)
    about = next((p for p in pages if p.get("type") == "about"), None)
    # Блок type "about" на главной показывает цифры раздела «О нас»: тогда у раздела нет своей шторки,
    # а пункт меню ведёт к блоку. Блок скрыт или его нет — «О нас» остаётся шторкой, как прежде
    about_section = about_home_section(sections, about)
    if about_section is None:
        sections = [s for s in sections if s.get("type") != "about"]
    founded = about.get("founded_year") if about else None
    return render_template(
        "index.html",
        sheet=slug,
        site=content["site"],
        ui=content["site"].get("ui") or {},
        hero=content["home"].get("hero", {}),
        sections=sections,
        featured=featured,
        pages=pages,
        years=build_years(
            content["portfolio"].get("projects", []),
            current_year,
            content_dir / "media",
            portfolio.get("first_year", FIRST_YEAR),
            static_dir=current_app.static_folder,
        ) if portfolio else [],
        current_year=current_year,
        about_page=about,
        about_section=about_section,
        # Номер раздела в pages.json — для меток полей «О нас» в предпросмотре редактора
        about_index=next((i for i, p in enumerate(content["pages"].get("pages", [])) if p is about), None),
        portfolio_filters={"layouts": LAYOUT_FILTER, "areas": AREA_RANGES, "floors": FLOORS},
        years_since=years_word(current_year - founded) if isinstance(founded, int) else "",
        contacts=published_contacts(content["contacts"].get("contacts", [])),
        socials=published_socials(content["contacts"].get("socials", [])),
        quick_contacts=published_quick_contacts(content["contacts"].get("quick", [])),
        frame_count=FRAME_COUNT,
        video_cards=video_cards,
        video_sheet=video_sheet,
        video_slug=videos.SHEET_SLUG,
        editing=editing,
        edit=edit_marker(content["home"]) if editing else no_edit,
    )


def site_videos(content_dir, items, data=None):
    """Ролики для главной по позициям блока (items); испорченный videos.json не ломает страницу —
    блок остаётся с заглушками. data — уже прочитанная библиотека (черновик в предпросмотре)."""
    try:
        return videos.for_site(data if data is not None else videos.load(content_dir), items, content_dir / "media")
    except ContentError as err:
        current_app.logger.error("Видеоотзывы не показаны: %s", err)
        return videos.for_site(None, items)


@bp.route("/")
def index():
    return render_page()


def about_home_section(sections, about):
    """Показанный блок «О нас» на главной (id для ссылки меню) или None."""
    if about is None:
        return None
    return next((s for s in sections if s.get("type") == "about" and s.get("id")), None)


@bp.route("/<slug>")
def page(slug):
    # «О нас» живёт на главной — прежний адрес /about ведёт к блоку (временный переход: блок можно скрыть)
    content = load_content(Path(current_app.config["CONTENT_DIR"]))
    about = next((p for p in visible(content["pages"].get("pages", [])) if p.get("type") == "about"), None)
    if about is not None and about.get("slug") == slug:
        section = about_home_section(visible(content["home"].get("sections", [])), about)
        if section is not None:
            return redirect(url_for("main.index", _anchor=section["id"]))
    return render_page(slug, content)


@bp.route("/request/design", methods=["POST"])
def design_request():
    """Заявка «Заказать дизайн-проект» из конфигуратора: контакт и параметры стенда → файл в REQUESTS_DIR.

    Принимается только запрос скрипта сайта (заголовок X-Requested-With — обычная форма с чужого
    сайта его не добавит, а CORS не разрешён) с того же адреса. Ответ — JSON: ok или ошибки по полям.
    Успех — только после записи файла на диск."""
    if request.headers.get("X-Requested-With") != "fetch":
        abort(400)
    origin = request.headers.get("Origin")
    if request.headers.get("Sec-Fetch-Site") not in (None, "same-origin") or \
            (origin and urlparse(origin).netloc != request.host):
        abort(403)
    form = request.form
    # Поле-ловушка скрыто от людей; его заполняют только боты — ответ как обычно, но без записи
    if form.get("website"):
        return jsonify(ok=True)
    limiter = current_app.extensions.setdefault("request_limiter", leads.RequestLimiter())
    if not limiter.allow(request.remote_addr or "unknown"):
        return jsonify(ok=False, message="Слишком много заявок подряд. Попробуйте через несколько минут "
                                         "или свяжитесь с нами по контактам на сайте."), 429

    section = next((s for s in visible(load_content(Path(current_app.config["CONTENT_DIR"]))["home"]
                                       .get("sections", [])) if s.get("type") == "configurator"), None)
    if section is None:
        abort(404)
    errors = {}
    contact, kind, error = leads.check_contact(form.get("contact"))
    if error:
        errors["contact"] = leads.CONTACT_ERRORS[error]
    stand, stand_errors = leads.check_stand(form, section)
    if stand_errors:
        errors["stand"] = " ".join(stand_errors.values())
    if errors:
        return jsonify(ok=False, errors=errors, message=" ".join(errors.values())), 400

    record = {"created": datetime.now(timezone.utc).isoformat(timespec="seconds"), "source": "configurator",
              "contact": contact, "contact_type": kind, "stand": stand}
    try:
        leads.save_request(current_app.config["REQUESTS_DIR"], record)
    except OSError:
        current_app.logger.exception("Не удалось сохранить заявку")
        return jsonify(ok=False, message="Заявка не отправлена: не удалось её сохранить. Попробуйте ещё раз "
                                         "или свяжитесь с нами по контактам на сайте."), 500
    return jsonify(ok=True)


@bp.route("/media/<path:filename>")
def media(filename):
    # Отдаём только картинки и видеоотзывы (MP4, WebM — только из videos/files/);
    # send_from_directory сам не выпускает за пределы папки и поддерживает Range (перемотка видео).
    content_dir = Path(current_app.config["CONTENT_DIR"])
    parts = Path(filename).parts
    suffix = Path(filename).suffix.lower()
    in_videos = len(parts) == 3 and parts[0].casefold() == "videos" and parts[1].casefold() in ("files", "covers")
    if suffix not in IMAGE_EXTENSIONS and not (suffix in videos.VIDEO_EXTENSIONS and in_videos):
        abort(404)
    # Загруженные видео и свои обложки: посетителю — только включённые ролики опубликованной
    # библиотеки, черновик видит администратор. Путь сверяется точно, регистр — нет (как у портфолио)
    if in_videos and not is_admin():
        try:
            shown = videos.public_media(videos.load(content_dir))
        except ContentError:
            abort(404)
        if filename not in shown:
            abort(404)
    # Фото черновиков видит только администратор. Регистр не учитываем: файловая
    # система может его не различать, и Portfolio/… иначе обошёл бы проверку.
    # id проекта сверяем точно — любой другой регистр не опубликован и получит 404.
    if len(parts) > 2 and parts[0].casefold() == "portfolio" and not is_admin():
        projects = read_json(content_dir / "portfolio.json").get("projects", [])
        if parts[1] not in {p.get("id") for p in projects if p.get("status") == "published"}:
            abort(404)
    if suffix in videos.VIDEO_EXTENSIONS:
        return send_from_directory(content_dir / "media", filename, mimetype=f"video/{suffix[1:]}")
    return send_from_directory(content_dir / "media", filename)
