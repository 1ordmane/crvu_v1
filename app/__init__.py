import os
from datetime import timedelta
from functools import lru_cache
from pathlib import Path

import click
from flask import Flask, url_for
from PIL import Image

from .portfolio import format_area
from .uploads import VARIANTS, is_variant, variant_path

CONTENT_SECURITY_POLICY = "; ".join([
    "default-src 'self'",
    "img-src 'self'",
    "script-src 'self'",
    "style-src 'self'",
    "font-src 'self'",
    "connect-src 'self'",
    # Плееры Vimeo и Rutube в галерее стенда — единственные разрешённые источники iframe
    "frame-src https://player.vimeo.com https://rutube.ru",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])


# Поворот по EXIF, при котором ширина на экране — это высота в файле
ROTATED_ORIENTATIONS = {5, 6, 7, 8}


@lru_cache(maxsize=4096)
def _display_width(path, mtime_ns, size):
    """(ширина на экране, есть ли геометка) из заголовка картинки; не прочитать — (None, False).

    Ключ кэша — путь, время изменения и размер файла: заменённый файл перечитывается.
    """
    try:
        with Image.open(path) as img:
            width, height = img.size
            exif = img.getexif()
            rotated = exif.get(0x0112) in ROTATED_ORIENTATIONS
            return (height if rotated else width), bool(exif.get_ifd(0x8825))
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        return None, False


def display_width(path):
    """Ширина картинки на экране и наличие геометки; файла нет или он не читается — (None, False)."""
    try:
        stat = Path(path).stat()
    except OSError:
        return None, False
    return _display_width(str(path), stat.st_mtime_ns, stat.st_size)


def read_env_file(path):
    """Строки KEY=value из локального .env: python-dotenv в проекте не используется.

    Кавычки вокруг значения снимаются, `$` внутри не раскрывается.
    """
    values = {}
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return values
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.removeprefix("export ").strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key] = value
    return values


def create_app(test_config=None):
    app = Flask(__name__)
    # Настройки — из окружения, а если там нет — из .env в корне проекта
    # (файл не коммитится). CRVU_ENV_FILE="" отключает чтение файла.
    env_file = os.environ.get("CRVU_ENV_FILE", str(Path(app.root_path).parent / ".env"))
    local_env = read_env_file(env_file) if env_file else {}

    def setting(name):
        return os.environ.get(name) or local_env.get(name)

    app.config.from_mapping(
        # Секрет только из окружения или .env. Сессии сейчас не используются, но без ключа
        # Flask не даст их подписать — значение по умолчанию в коде не нужно.
        SECRET_KEY=setting("SECRET_KEY"),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        # Включать только при работе по HTTPS, иначе cookie не дойдут.
        SESSION_COOKIE_SECURE=setting("SESSION_COOKIE_SECURE") == "1",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        # Публичная часть не принимает загрузки и формы; админ-панель повышает
        # лимит только для своих запросов (ADMIN_MAX_*).
        MAX_CONTENT_LENGTH=64 * 1024,
        # Админ-панель включается, только когда заданы оба значения и SECRET_KEY.
        ADMIN_USERNAME=setting("ADMIN_USERNAME"),
        ADMIN_PASSWORD_HASH=setting("ADMIN_PASSWORD_HASH"),
        ADMIN_MAX_FORM_BYTES=512 * 1024,
        # Оригиналы с камеры (до 40 МБ, до 100 Мп) загружаются как есть — сайт сам готовит версии.
        # Размер файла и разрешение — разные пределы: больший файл не повышает ADMIN_MAX_PIXELS.
        # За один запрос — до 200 МБ: 4 фото по 40 МБ (5 — если каждое чуть меньше 40 МБ).
        # У прокси на боевом сервере предел чуть выше (nginx: client_max_body_size 210m) —
        # тогда о превышении сообщает админка, а не страница 413 прокси.
        ADMIN_MAX_IMAGE_BYTES=40 * 1024 * 1024,
        ADMIN_MAX_UPLOAD_BYTES=200 * 1024 * 1024,
        ADMIN_MAX_FILES=20,
        ADMIN_MAX_PIXELS=100_000_000,
        # PNG и WebP декодируются целиком (около 11 байт памяти на пиксель): свой, меньший предел.
        # JPEG декодируется сразу уменьшенным, для него действует ADMIN_MAX_PIXELS.
        ADMIN_MAX_PIXELS_FULL_DECODE=50_000_000,
        # Импорт стенда из ZIP: фото в архиве почти не сжимаются; файл от 1 МБ, сжатый сильнее
        # чем в 10 раз, — не фото (защита от «ZIP-бомб»). Остальные пределы — те же, что у загрузки.
        ADMIN_ZIP_MAX_RATIO=10,
        ADMIN_IMAGE_MAX_SIDE=2560,
        CONTENT_DIR=setting("CRVU_CONTENT_DIR") or str(Path(app.root_path).parent / "content"),
        # Заявки с сайта (leads.py): вне content/ и static/ — сайт их не отдаёт, в архив контента не попадают
        REQUESTS_DIR=setting("CRVU_REQUESTS_DIR") or str(Path(app.root_path).parent / "requests"),
        # Временные заглушки вместо фото проектов видны только при CRVU_SHOW_PLACEHOLDERS=1
        # (просмотр макета). По умолчанию карточки без фото на сайт не попадают.
        SHOW_PLACEHOLDERS=setting("CRVU_SHOW_PLACEHOLDERS") == "1",
        # Обложки видеоотзывов админка скачивает с RuTube при добавлении ролика (app/videos.py);
        # CRVU_VIDEO_THUMBNAILS=0 — без обращений к RuTube, карточки со значком воспроизведения
        VIDEO_THUMBNAILS=setting("CRVU_VIDEO_THUMBNAILS") != "0",
        # Видеофайл видеоотзыва (MP4 или WebM, загружается как есть — сервер его не перекодирует).
        # CRVU_MAX_VIDEO_MB — предел файла в МБ (по умолчанию 180). Запрос загрузки — видео плюс
        # обложка до ADMIN_VIDEO_COVER_MARGIN; у прокси предел должен быть выше их суммы
        # (по умолчанию 200 МБ — то же, что у фото: nginx client_max_body_size 210m)
        ADMIN_MAX_VIDEO_BYTES=int(setting("CRVU_MAX_VIDEO_MB") or 180) * 1024 * 1024,
        ADMIN_VIDEO_COVER_MARGIN=20 * 1024 * 1024,
        # Статическая сборка для GitHub Pages (scripts/export_static.py): заявку конфигуратора принимает
        # не этот сервер, а внешний адрес DESIGN_REQUEST_ENDPOINT; без него форма честно сообщает,
        # что онлайн-отправки нет, и отсылает к контактам. На Flask-сервере обе настройки не нужны
        STATIC_EXPORT=False,
        DESIGN_REQUEST_ENDPOINT=None,
    )
    if test_config:
        app.config.update(test_config)
    # Тесты не ходят в сеть, если явно не попросили
    if app.config.get("TESTING") and "VIDEO_THUMBNAILS" not in (test_config or {}):
        app.config["VIDEO_THUMBNAILS"] = False

    @app.template_filter("media_url")
    def media_url(path):
        """static/… — файл приложения, иначе путь от content/media/."""
        if path.startswith("static/"):
            return url_for("static", filename=path.removeprefix("static/"))
        return url_for("main.media", filename=path)

    def existing_variants(path):
        """{длинная сторона: путь} облегчённых версий фото из content/media, которые есть на диске."""
        if path.startswith("static/"):
            return {}
        media = Path(app.config["CONTENT_DIR"]) / "media"
        result = {}
        for size in VARIANTS:
            variant = variant_path(path, size)
            if (media / variant).is_file():
                result[size] = variant.as_posix()
        return result

    @app.template_filter("area_text")
    def area_text(area):
        return format_area(area)

    @app.template_filter("media_src")
    def media_src(path, size):
        """Наименьшая версия не меньше size (или сам файл, если версий нет) — для src без srcset."""
        variants = existing_variants(path)
        fitting = [s for s in sorted(variants) if s >= size]
        return media_url(variants[fitting[0]] if fitting else path)

    @app.template_filter("media_srcset")
    def media_srcset(path):
        """srcset: облегчённые версии и основной файл, если он шире самой крупной из них.

        «/media/…-640.webp 640w, …, /media/….webp 2560w». Ширина — настоящая, из заголовка файла
        (у вертикального снимка версия «640» — 427w); файл, который не читается, не попадает.
        Основной файл — публичное фото из content/media; исходники из originals/ сюда не попадают,
        как и файл с геометкой в EXIF. Нет версий — пустая строка: браузер берёт src.
        """
        if path.startswith("static/"):
            return ""
        media = Path(app.config["CONTENT_DIR"]) / "media"
        entries = {}
        for size in sorted(VARIANTS):
            # Нет файла версии — display_width вернёт None (отдельная проверка is_file не нужна)
            variant = variant_path(path, size).as_posix()
            width, _ = display_width(media / variant)
            if width and width not in entries:
                entries[width] = media_url(variant)
        if not entries:
            return ""
        width, has_gps = display_width(media / path)
        if width and not has_gps and width > max(entries, default=0):
            entries[width] = media_url(path)
        return ", ".join(f"{url} {width}w" for width, url in sorted(entries.items()))

    @app.context_processor
    def editor_defaults():
        # Метки визуального редактора выводит только предпросмотр (routes.render_page с editing=True)
        from .routes import no_edit
        return {"editing": False, "edit": no_edit}

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return response

    @app.cli.command("check-content")
    def check_content():
        """Проверить JSON-файлы и картинки в папке контента."""
        from .content import load_content, validate_content
        from .portfolio import placeholder_notes

        errors = validate_content(app.config["CONTENT_DIR"], app.static_folder)
        for error in errors:
            click.echo(f"• {error}", err=True)
        if errors:
            raise SystemExit(f"Найдено ошибок: {len(errors)}")
        click.echo(f"Контент в порядке: {app.config['CONTENT_DIR']}")
        # Не ошибка, а напоминание перед публикацией: карточки без фото на сайте не видны
        notes = placeholder_notes(load_content(app.config["CONTENT_DIR"]), app.config["CONTENT_DIR"], app.static_folder)
        if notes:
            click.echo("Перед публикацией добавьте фото (сейчас эти карточки на сайте не показываются):")
            for note in notes:
                click.echo(f"• {note}")

    @app.cli.command("make-image-sizes")
    def make_image_sizes():
        """Создать облегчённые версии (640/1280/1920 px) для уже загруженных фото в content/media."""
        from .uploads import make_variants

        media = Path(app.config["CONTENT_DIR"]) / "media"
        created = 0
        for path in sorted(media.rglob("*")):
            if path.suffix.lower() in {".webp", ".jpg", ".jpeg", ".png"} and not is_variant(path.name):
                try:
                    created += len(make_variants(path))
                except OSError as err:
                    click.echo(f"• {path.relative_to(media)}: {err}", err=True)
        click.echo(f"Создано версий: {created}")

    @app.cli.command("import-drafts")
    @click.argument("manifest", type=click.Path(exists=True, dir_okay=False))
    @click.option("--dry-run", is_flag=True, help="Только показать, что будет создано и что пропущено.")
    @click.option("--report", type=click.Path(dir_okay=False), help="Записать итог в JSON-файл.")
    def import_drafts_command(manifest, dry_run, report):
        """Создать черновики портфолио из подборок фото на диске (только черновики, без публикации)."""
        import json

        from .draft_import import import_drafts

        with open(manifest, encoding="utf-8") as f:
            stands = json.load(f)["stands"]
        results = import_drafts(stands, dry_run=dry_run, echo=click.echo)
        if report:
            with open(report, "w", encoding="utf-8") as f:
                json.dump([{"source": s, "result": r, "detail": d} for s, r, d in results], f,
                          ensure_ascii=False, indent=1)
        counts = {}
        for _, result, _ in results:
            counts[result] = counts.get(result, 0) + 1
        click.echo("Итог: " + ", ".join(f"{k} — {v}" for k, v in counts.items()))

    @app.cli.command("hash-password")
    def hash_password():
        """Создать хэш пароля администратора для ADMIN_PASSWORD_HASH."""
        from werkzeug.security import generate_password_hash

        password = click.prompt("Пароль", hide_input=True, confirmation_prompt="Повторите пароль")
        if len(password) < 12:
            raise SystemExit("Пароль должен быть не короче 12 символов")
        click.echo(generate_password_hash(password))

    from .admin import bp as admin_bp
    from .routes import bp
    app.register_blueprint(bp)
    app.register_blueprint(admin_bp)
    return app
