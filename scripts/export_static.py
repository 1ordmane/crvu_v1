"""Статическая сборка публичного сайта CRVU для GitHub Pages (инструкция — GITHUB_PAGES.md).

Страницы рендерит само Flask-приложение (как анонимный посетитель), файлы скачиваются через его же
маршруты: /media отдаёт только опубликованное, поэтому черновики, оригиналы фото, история, заявки и
админка в сборку не попадают. Берутся только файлы, на которые ссылаются страницы (и CSS), плюс
кадры сцены, имена которых собирает main.js. В конце — проверка сборки (audit).

  python scripts/export_static.py --base-path /имя-репозитория         # сборка в build/pages
  python scripts/export_static.py --base-path /имя-репозитория --serve # и просмотр на 127.0.0.1:8000

Базовый путь — CRVU_BASE_PATH или --base-path ("" — сайт в корне домена); адрес приёма заявок
конфигуратора — CRVU_FORM_ENDPOINT или --form-endpoint (только https; не задан — форма сообщает,
что онлайн-отправки нет). Папку вывода сборка перезаписывает, только если там её прежний результат
(файл-метка .crvu-static-export); чужую непустую папку не трогает.

Фото и видео для Pages. content/media/ в Git не хранится (там и черновики), поэтому публичные файлы
лежат отдельно — в pages-media/ (хранится в Git) со списком pages-media/MANIFEST.tsv: путь, размер,
SHA-256 каждого файла. Это ровно файлы из build/pages/media/ последней сборки по content/.

  python scripts/export_static.py --sync-media           # сборка по content/ → обновить pages-media/
  python scripts/export_static.py --media-dir pages-media # сборка только из pages-media/ (как в Actions)

Сборка из pages-media/ останавливается, если файлы не совпадают со списком, если опубликованный
проект или ролик ссылается на файл не из списка, или если в списке есть файлы, которые сайт больше
не использует. --sync-media лишние файлы не удаляет, пока не добавлен --prune.
"""

import argparse
import hashlib
import http.server
import os
import posixpath
import re
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import CONTENT_SECURITY_POLICY, create_app  # noqa: E402
from app import videos  # noqa: E402
from app.content import ContentError, load_content, read_json, visible  # noqa: E402

DEFAULT_OUT = ROOT / "build" / "pages"
MARKER = ".crvu-static-export"
# Публичные фото и видео для сборки в Actions и их список (см. начало файла)
PAGES_MEDIA = ROOT / "pages-media"
MANIFEST = "MANIFEST.tsv"
MEDIA_TOP = ("portfolio", "videos")
# Служебные файлы ОС в pages-media/ не считаются (.gitignore их и так не пускает в Git)
OS_FILES = (".DS_Store", "Thumbs.db")
# Папки проекта, куда сборка не пишет ни при каких аргументах
PROTECTED = ("app", "content", "dist", "backup", "requests", "tests", "scripts", ".venv", ".github", "pages-media")
MAX_FILE_BYTES = 95 * 1024 * 1024       # GitHub не принимает файлы больше 100 МБ
MAX_SITE_BYTES = 1024 * 1024 * 1024     # предел опубликованного сайта GitHub Pages — 1 ГБ
SLUG = re.compile(r"[a-z0-9][a-z0-9-]*")
FRAMES_BASE = re.compile(r'data-frames-base(?:-mobile)?="([^"]+)"')
CSS_URL = re.compile(r"url\(\s*['\"]?([^'\")]+)['\"]?\s*\)")
# В пути — признаки закрытых данных: им не место в сборке
FORBIDDEN_PARTS = (".env", "originals", ".history", ".drafts", ".cache", "requests", ".lock", "admin", "backup")
FORBIDDEN_SUFFIXES = (".json", ".py", ".md", ".sh", ".txt", ".jpg.tmp", ".tmp")
FORBIDDEN_TEXT = ("data-edit=", "/admin/", "csrf_token", "pbkdf2:", "scrypt:", "ADMIN_PASSWORD", "SECRET_KEY")


class ExportError(Exception):
    pass


def normalize_base(value):
    """"/repo" или "" (корень домена): без завершающей косой, только безопасные символы."""
    value = (value or "").strip().strip("/")
    if not value:
        return ""
    if not re.fullmatch(r"[A-Za-z0-9._~-]+(?:/[A-Za-z0-9._~-]+)*", value) or ".." in value.split("/"):
        raise ExportError(f"Базовый путь {value!r}: только латиница, цифры, «.», «_», «~», «-» и «/»")
    return "/" + value


def check_endpoint(value):
    """Адрес приёма заявок: https://… без пробелов и кавычек, или None."""
    value = (value or "").strip()
    if not value:
        return None
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.netloc or re.search(r"[\s\"'<>]", value):
        raise ExportError("Адрес приёма заявок — только https://…")
    return value


def prepare_out(out):
    """Папка вывода: новая, пустая или прежний результат сборки (с меткой). Иначе — отказ."""
    out = Path(out).resolve()
    if out == ROOT or any(out == ROOT / p or out.is_relative_to(ROOT / p) for p in PROTECTED):
        raise ExportError(f"В {out} сборка не пишет: это папка проекта")
    if out.exists():
        if not out.is_dir():
            raise ExportError(f"{out} — файл, а не папка")
        if any(out.iterdir()):
            if not (out / MARKER).is_file():
                raise ExportError(f"{out} не пуста и это не прежняя сборка (нет {MARKER}) — выберите другую папку")
            shutil.rmtree(out)                       # только собственный прежний результат
    out.mkdir(parents=True, exist_ok=True)
    (out / MARKER).write_text("Статическая сборка CRVU (scripts/export_static.py). Папку можно пересобрать.\n",
                              encoding="utf-8")
    return out


def csp_meta(endpoint):
    """Политика безопасности как у Flask-сервера, но в <meta> (на Pages заголовков нет). frame-ancestors
    в <meta> не действует — не включаем; адрес приёма заявок добавляется в connect-src."""
    rules = [r for r in CONTENT_SECURITY_POLICY.split("; ") if not r.startswith("frame-ancestors")]
    if endpoint:
        origin = "{0.scheme}://{0.netloc}".format(urlsplit(endpoint))
        rules = [r + " " + origin if r.startswith("connect-src") else r for r in rules]
    return '<meta http-equiv="Content-Security-Policy" content="{}">'.format("; ".join(rules))


def add_csp(html, meta):
    marker = '<meta charset="utf-8">'
    if marker not in html:
        raise ExportError("В странице нет <meta charset=\"utf-8\"> — некуда добавить политику безопасности")
    return html.replace(marker, marker + "\n  " + meta, 1)


def redirect_page(location):
    """Страница-переход для адресов, которые на сервере отвечают редиректом (например, /about)."""
    href = location.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
    return ('<!doctype html>\n<html lang="ru">\n<head>\n  <meta charset="utf-8">\n'
            f'  <meta http-equiv="refresh" content="0; url={href}">\n  <link rel="canonical" href="{href}">\n'
            f'  <title>Переход</title>\n</head>\n<body>\n  <p><a href="{href}">Перейти</a></p>\n</body>\n</html>\n')


def not_found_page(base, title):
    home = (base or "") + "/"
    title = title.replace("&", "&amp;").replace("<", "&lt;")
    return ('<!doctype html>\n<html lang="ru">\n<head>\n  <meta charset="utf-8">\n'
            '  <meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f'  <title>Страница не найдена — {title}</title>\n'
            f'  <link rel="stylesheet" href="{base}/static/css/style.css">\n</head>\n<body>\n'
            f'  <main class="section"><div class="container"><h1>Страница не найдена</h1>\n'
            f'    <p><a href="{home}">На главную</a></p></div></main>\n</body>\n</html>\n')


def refs_in(text, base):
    """Адреса /static/… и /media/… (с базовым путём) из HTML: атрибуты, srcset, JSON в data-*."""
    pattern = re.compile(r"(?<=[\"'\s,(=])(" + re.escape(base) + r"/(?:static|media)/[^\"'\s<>),\\]+)")
    return set(pattern.findall(text))


def build(out=DEFAULT_OUT, base_path="", form_endpoint=None, content_dir=None, media_dir=None):
    """Собирает сайт в out; возвращает сводку. Ошибка — ExportError (сборка не считается готовой).

    media_dir — папка разрешённых фото и видео (pages-media/ со списком): тогда тексты берутся из
    content/, а файлы — только оттуда; без него — из content/media/ (локальная сборка)."""
    if media_dir is None:
        return render_site(out, base_path, form_endpoint, content_dir)
    media_dir = Path(media_dir).resolve()
    content_root = Path(content_dir or os.environ.get("CRVU_CONTENT_DIR") or ROOT / "content").resolve()
    problems = check_allowlist(media_dir)
    entries = read_manifest(media_dir)
    for rel, owner in sorted(expected_media(content_root).items()):
        if rel not in entries:
            problems.append(f"{owner} ссылается на {rel} — его нет в {media_dir.name}/{MANIFEST}")
    if problems:
        raise ExportError("Разрешённые файлы не готовы (обновите их: python scripts/export_static.py "
                          "--sync-media):\n  " + "\n  ".join(problems))
    # Тексты — из content/, файлы — только из списка: временная папка контента с media → media_dir
    with tempfile.TemporaryDirectory(prefix="crvu-pages-") as tmp:
        staged = Path(tmp) / "content"
        staged.mkdir()
        # Все JSON контента: тексты, разделы, портфолио и библиотека видео (videos.json читается отдельно)
        for path in sorted(content_root.glob("*.json")):
            shutil.copy2(path, staged / path.name)
        (staged / "media").symlink_to(media_dir, target_is_directory=True)
        summary = render_site(out, base_path, form_endpoint, staged)
    used = media_files(summary["out"])
    unused = sorted(set(entries) - set(used))
    if unused:
        raise ExportError(f"В {media_dir.name}/{MANIFEST} есть файлы, которые сайт больше не использует "
                          "(проект снят с публикации или фото заменено). Обновите список: "
                          "python scripts/export_static.py --sync-media --prune\n  " + "\n  ".join(unused))
    summary.update(media_files=len(used), media_bytes=sum(size for size, _ in used.values()))
    return summary


def render_site(out, base_path, form_endpoint, content_dir):
    """Сборка страниц и файлов (см. build)."""
    base = normalize_base(base_path)
    endpoint = check_endpoint(form_endpoint)
    # .env с секретами админки сборке не нужен: create_app его не читает, вход в админку выключен
    saved = os.environ.get("CRVU_ENV_FILE")
    os.environ["CRVU_ENV_FILE"] = ""
    try:
        config = {"STATIC_EXPORT": True, "DESIGN_REQUEST_ENDPOINT": endpoint,
                  "SECRET_KEY": None, "ADMIN_USERNAME": None, "ADMIN_PASSWORD_HASH": None,
                  "SHOW_PLACEHOLDERS": False, "VIDEO_THUMBNAILS": False}
        if content_dir is not None:
            config["CONTENT_DIR"] = str(content_dir)
        app = create_app(config)
    finally:
        if saved is None:
            os.environ.pop("CRVU_ENV_FILE", None)
        else:
            os.environ["CRVU_ENV_FILE"] = saved

    content_root = Path(app.config["CONTENT_DIR"])
    static_root = Path(app.static_folder)
    out = prepare_out(out)
    client = app.test_client()
    base_url = "http://localhost" + base
    meta = csp_meta(endpoint)
    content = load_content(content_root)

    # Страницы: главная, разделы-шторки из pages.json и шторка «Другие видео-отзывы»
    slugs = [p.get("slug") for p in visible(content["pages"].get("pages", []))]
    slugs = [s for s in slugs + [videos.SHEET_SLUG] if isinstance(s, str) and SLUG.fullmatch(s)]
    pages, wanted = [], set()
    for slug in [""] + list(dict.fromkeys(slugs)):
        response = client.get("/" + slug, base_url=base_url)
        name = (slug or "index") + ".html"
        if response.status_code == 200:
            html = add_csp(response.get_data(as_text=True), meta)
            (out / name).write_text(html, encoding="utf-8")
            wanted |= refs_in(html, base)
            for frames in FRAMES_BASE.findall(html):          # имена кадров собирает main.js
                rel = frames.removeprefix(base + "/static/")
                for frame in sorted((static_root / rel).glob("frame-*.webp")):
                    wanted.add(frames + frame.name)
            pages.append(name)
        elif response.status_code in (301, 302, 303, 307, 308):
            (out / name).write_text(redirect_page(response.headers["Location"]), encoding="utf-8")
            pages.append(name)
        elif response.status_code != 404:
            raise ExportError(f"/{slug}: ответ {response.status_code}")
    if "index.html" not in pages:
        raise ExportError("Главная страница не собрана")
    (out / "404.html").write_text(not_found_page(base, content["site"].get("name") or "CRVU"), encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")       # Pages отдаёт файлы как есть, без Jekyll

    # Файлы — через маршруты приложения (публичные проверки /media); CSS тянет свои url(...)
    missing, done, queue = [], set(), sorted(wanted)
    while queue:
        url = queue.pop()
        path = urlsplit(url).path
        if path in done or path.endswith("/"):                # папка кадров — сами кадры добавлены выше
            continue
        done.add(path)
        response = client.get(path.removeprefix(base), base_url=base_url)
        if response.status_code != 200:
            missing.append(f"{path} ({response.status_code})")
            response.close()
            continue
        target = (out / unquote(path.removeprefix(base)).lstrip("/")).resolve()
        if not target.is_relative_to(out):
            raise ExportError(f"{path}: путь вне папки сборки")
        target.parent.mkdir(parents=True, exist_ok=True)
        data = response.get_data()
        response.close()
        target.write_bytes(data)
        if path.endswith(".css"):
            for ref in CSS_URL.findall(data.decode("utf-8")):
                if ref.startswith(("data:", "http:", "https:", "#")):
                    continue
                queue.append(posixpath.normpath(posixpath.join(posixpath.dirname(path), urlsplit(ref).path)))
    if missing:
        raise ExportError("Страницы ссылаются на файлы, которых нет или которые не опубликованы:\n  "
                          + "\n  ".join(sorted(missing)))

    problems = audit(out, content_root)
    if problems:
        raise ExportError("Проверка сборки не пройдена:\n  " + "\n  ".join(problems))
    files = [p for p in out.rglob("*") if p.is_file()]
    used = media_files(out)
    return {"out": out, "base": base, "pages": pages, "files": len(files),
            "bytes": sum(p.stat().st_size for p in files), "form_endpoint": endpoint,
            "media_files": len(used), "media_bytes": sum(size for size, _ in used.values())}


def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def media_files(out):
    """Фото и видео сборки: {путь от media/: (размер, sha256)}."""
    media = Path(out) / "media"
    return {p.relative_to(media).as_posix(): (p.stat().st_size, digest(p))
            for p in sorted(media.rglob("*")) if p.is_file()} if media.is_dir() else {}


def safe_media_path(rel):
    parts = rel.split("/")
    return (len(parts) >= 2 and parts[0] in MEDIA_TOP and all(part and part not in (".", "..") for part in parts)
            and not rel.startswith("/") and "\\" not in rel)


def read_manifest(media_dir):
    """Список разрешённых файлов: {путь: (размер, sha256)}."""
    path = Path(media_dir) / MANIFEST
    if not path.is_file():
        raise ExportError(f"Нет списка {path} — создайте его: python scripts/export_static.py --sync-media")
    entries = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) != 3 or not re.fullmatch(r"[0-9a-f]{64}", fields[0]) or not fields[1].isdigit() \
                or not safe_media_path(fields[2]) or fields[2] in entries:
            raise ExportError(f"{path.name}, строка {number}: ожидается «sha256<Tab>байты<Tab>путь»")
        entries[fields[2]] = (int(fields[1]), fields[0])
    return entries


def write_manifest(media_dir, entries):
    total = sum(size for size, _ in entries.values())
    lines = ["# Фото и видео, которые публикуются на GitHub Pages (scripts/export_static.py, GITHUB_PAGES.md).",
             "# Ровно файлы, на которые ссылается опубликованный сайт; обновлять только командой",
             "# python scripts/export_static.py --sync-media. Вручную не править.",
             f"# Файлов: {len(entries)}, байт: {total}",
             "# sha256\tбайты\tпуть от content/media/"]
    lines += [f"{h}\t{size}\t{rel}" for rel, (size, h) in sorted(entries.items())]
    (Path(media_dir) / MANIFEST).write_text("\n".join(lines) + "\n", encoding="utf-8")


def allowlist_files(media_dir):
    media_dir = Path(media_dir)
    return {p.relative_to(media_dir).as_posix(): p for p in sorted(media_dir.rglob("*"))
            if p.is_file() and p.name != MANIFEST and p.name not in OS_FILES}


def check_allowlist(media_dir):
    """Файлы папки совпадают со списком: ни лишних, ни недостающих, ни изменённых."""
    entries = read_manifest(media_dir)
    actual = allowlist_files(media_dir)
    problems = [f"{rel}: файла нет в списке" for rel in sorted(set(actual) - set(entries))]
    problems += [f"{rel}: файл из списка отсутствует" for rel in sorted(set(entries) - set(actual))]
    for rel in sorted(set(actual) & set(entries)):
        size, h = entries[rel]
        if actual[rel].stat().st_size != size or digest(actual[rel]) != h:
            problems.append(f"{rel}: файл отличается от списка")
    return problems


def expected_media(content_root):
    """Файлы, без которых опубликованный контент неполон: обложки и фото опубликованных проектов,
    видео и свои обложки включённых роликов. {путь от media/: чей}."""
    expected = {}
    for project in read_json(Path(content_root) / "portfolio.json").get("projects", []):
        if project.get("status") != "published":
            continue
        photos = [project.get("cover")] + list(project.get("photos") or [])
        for rel in photos:
            if isinstance(rel, str) and rel and not rel.startswith("static/"):
                expected[rel] = f"Проект «{project.get('title') or project.get('id')}»"
    try:
        library = videos.load(content_root)
    except ContentError:
        library = {}
    for v in library.get("videos", []) if isinstance(library.get("videos"), list) else []:
        if isinstance(v, dict) and v.get("active", True) is True:
            for rel in videos.media_paths({"videos": [v]}):
                expected[rel] = f"Видеоотзыв «{v.get('title') or v.get('id')}»"
    return expected


def sync_media(out, media_dir=PAGES_MEDIA, prune=False):
    """Разрешённые файлы := фото и видео сборки out (сборка по content/). Лишние файлы удаляются только
    с prune — это копии, исходники остаются в content/media/."""
    media_dir = Path(media_dir).resolve()
    if media_dir == (ROOT / "content" / "media").resolve() or media_dir.is_relative_to((ROOT / "content").resolve()):
        raise ExportError("Разрешённые файлы не хранятся в content/")
    used = media_files(out)
    if not used:
        raise ExportError("В сборке нет фото и видео — сначала соберите сайт по content/")
    existing = allowlist_files(media_dir) if media_dir.is_dir() else {}
    stale = sorted(set(existing) - set(used))
    if stale and not prune:
        raise ExportError("В pages-media/ есть файлы, которые сайт больше не использует. Проверьте и "
                          "запустите с --prune, чтобы убрать их из списка и папки:\n  " + "\n  ".join(stale))
    copied = 0
    for rel, (size, h) in used.items():
        target = media_dir / rel
        if target.is_file() and target.stat().st_size == size and digest(target) == h:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path(out) / "media" / rel, target)
        copied += 1
    for rel in stale:
        existing[rel].unlink()
        parent = existing[rel].parent
        while parent != media_dir and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent
    write_manifest(media_dir, used)
    return {"files": len(used), "bytes": sum(size for size, _ in used.values()), "copied": copied,
            "removed": len(stale)}


def audit(out, content_root):
    """Что не должно попасть на Pages: закрытые папки и файлы, черновики, метки редактора, секреты,
    слишком большие файлы. Возвращает список нарушений (пустой — всё в порядке)."""
    out = Path(out)
    problems, total = [], 0
    portfolio = read_json(content_root / "portfolio.json").get("projects", [])
    published = {p.get("id") for p in portfolio if p.get("status") == "published"}
    # Видео: файлы и обложки включённых роликов опубликованной библиотеки, копии картинок RuTube у них же
    try:
        library = videos.load(content_root)
    except ContentError:
        library = {}
    public_videos = videos.public_media(library)
    for v in library.get("videos", []) if isinstance(library.get("videos"), list) else []:
        if isinstance(v, dict) and v.get("active", True) is True:
            public_videos.add(videos.cover_of(v, content_root / "media"))
    for path in sorted(out.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(out).as_posix()
        parts = rel.split("/")
        size = path.stat().st_size
        total += size
        lowered = rel.casefold()
        if len(parts) == 1:
            if not (rel.endswith(".html") or rel in (".nojekyll", MARKER)):
                problems.append(f"{rel}: лишний файл в корне сборки")
        elif parts[0] not in ("static", "media"):
            problems.append(f"{rel}: файл вне static/ и media/")
        if any(f in part for part in lowered.split("/") for f in FORBIDDEN_PARTS):
            problems.append(f"{rel}: закрытые данные")
        if lowered.endswith(FORBIDDEN_SUFFIXES):
            problems.append(f"{rel}: такие файлы не публикуются")
        if parts[0] == "media" and len(parts) > 2 and parts[1] == "portfolio" and parts[2] not in published:
            problems.append(f"{rel}: фото неопубликованного проекта")
        if parts[0] == "media" and len(parts) > 1 and parts[1] == "videos" and "/".join(parts[1:]) not in public_videos:
            problems.append(f"{rel}: видео вне опубликованной библиотеки")
        if size > MAX_FILE_BYTES:
            problems.append(f"{rel}: {size // (1024 * 1024)} МБ — больше предела GitHub")
        if rel.endswith(".html"):
            text = path.read_text(encoding="utf-8")
            problems += [f"{rel}: в тексте «{t}»" for t in FORBIDDEN_TEXT if t in text]
    if total > MAX_SITE_BYTES:
        problems.append(f"Сборка {total // (1024 * 1024)} МБ — больше предела GitHub Pages (1 ГБ)")
    return problems


class PreviewHandler(http.server.SimpleHTTPRequestHandler):
    """Просмотр сборки как на Pages: базовый путь, /portfolio → portfolio.html, 404.html."""

    base = ""

    def translate_path(self, path):
        path = urlsplit(path).path
        if self.base:
            if path != self.base and not path.startswith(self.base + "/"):
                return str(Path(self.directory) / "404-outside-base-path")
            path = path[len(self.base):] or "/"
        full = Path(super().translate_path(path))
        if not full.exists() and full.with_name(full.name + ".html").is_file():
            return str(full.with_name(full.name + ".html"))
        return str(full)

    def list_directory(self, path):
        self.send_error(404)                         # Pages списков файлов не показывает
        return None

    def send_error(self, code, message=None, explain=None):
        page = Path(self.directory) / "404.html"
        if code != 404 or not page.is_file():
            return super().send_error(code, message, explain)
        body = page.read_bytes()
        self.send_response(404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve(out, base, port):
    handler = type("Handler", (PreviewHandler,), {"base": base})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port),
                                             lambda *a, **kw: handler(*a, directory=str(out), **kw))
    print(f"Просмотр: http://127.0.0.1:{port}{base}/  (Ctrl+C — остановить)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


def main(argv=None):
    parser = argparse.ArgumentParser(description="Статическая сборка публичного сайта CRVU для GitHub Pages")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="папка сборки (по умолчанию build/pages)")
    parser.add_argument("--base-path", default=os.environ.get("CRVU_BASE_PATH", ""),
                        help="адрес сайта от корня домена: /имя-репозитория; пусто — корень домена")
    parser.add_argument("--form-endpoint", default=os.environ.get("CRVU_FORM_ENDPOINT", ""),
                        help="https-адрес приёма заявок конфигуратора (необязательно)")
    parser.add_argument("--content-dir", default=None, help="папка контента (по умолчанию как у сайта)")
    parser.add_argument("--media-dir", default=None,
                        help="собрать с фото и видео только из этой папки со списком (pages-media — как в Actions)")
    parser.add_argument("--sync-media", action="store_true",
                        help="собрать по content/ и обновить pages-media/ и её список")
    parser.add_argument("--prune", action="store_true",
                        help="с --sync-media: убрать из pages-media/ файлы, которые сайт больше не использует")
    parser.add_argument("--serve", action="store_true", help="после сборки открыть просмотр")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    if args.sync_media and args.media_dir:
        parser.error("--sync-media собирает по content/ — без --media-dir")
    try:
        summary = build(args.out, args.base_path, args.form_endpoint, args.content_dir, args.media_dir)
        synced = sync_media(summary["out"], PAGES_MEDIA, args.prune) if args.sync_media else None
    except (ExportError, ContentError) as err:
        print(f"Сборка не готова: {err}", file=sys.stderr)
        return 1
    print(f"Готово: {summary['out']} — страниц {len(summary['pages'])}, файлов {summary['files']}, "
          f"{summary['bytes'] / (1024 * 1024):.1f} МБ; базовый путь «{summary['base'] or '/'}»; "
          f"приём заявок: {summary['form_endpoint'] or 'не настроен — форма отсылает к контактам'}")
    print(f"Фото и видео: {summary['media_files']} файлов, {summary['media_bytes'] / (1024 * 1024):.1f} МБ"
          + (f" — только из {args.media_dir}" if args.media_dir else " — из content/media/"))
    if synced:
        print(f"pages-media/: {synced['files']} файлов, {synced['bytes'] / (1024 * 1024):.1f} МБ; "
              f"скопировано {synced['copied']}, убрано {synced['removed']}")
    elif not args.media_dir and not args.content_dir and (PAGES_MEDIA / MANIFEST).is_file():
        listed = read_manifest(PAGES_MEDIA)
        if set(listed) != set(media_files(summary["out"])) or any(
                listed[rel] != v for rel, v in media_files(summary["out"]).items() if rel in listed):
            print("Внимание: фото и видео сайта изменились, а pages-media/ — нет. Перед публикацией: "
                  "python scripts/export_static.py --sync-media", file=sys.stderr)
    if args.serve:
        serve(summary["out"], summary["base"], args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
