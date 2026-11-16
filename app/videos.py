"""Видеоотзывы: библиотека роликов (RuTube и загруженные файлы) и их показ в блоке «Нам доверяют свои проекты».

Библиотека — content/videos.json (необязательный файл: пока его нет, роликов нет). Ролик — один из двух видов:

- RuTube: сайт хранит постоянный id записи, код ролика, нормализованную ссылку на его страницу,
  подписи и порядок. Адрес плеера собирается заново из проверенного кода (content.video_player_url),
  поэтому чужие iframe и адреса в HTML не попадают.
- Файл: MP4 или WebM, загруженный в админке, лежит в content/media/videos/files/<32 hex>.mp4|webm
  (имя случайное, файл не перекодируется — нужен формат, который браузеры играют сами).

    {"videos": [{"id": "1a2b3c4d", "rutube": "<код из 32 символов>", "url": "https://rutube.ru/video/<код>/",
                 "title": "", "description": "", "active": true},
                {"id": "5e6f7a8b", "file": "videos/files/<32 hex>.mp4", "cover": "videos/covers/<32 hex>.webp",
                 "title": "", "description": "", "active": true}, …]}   — порядок списка = порядок показа

Своя обложка (cover, необязательно у обоих видов) — content/media/videos/covers/<32 hex>.webp: картинка
или кадр из видео, выбранные в админке. У ролика RuTube без своей обложки — копия картинки RuTube.
Файлы видео и обложек не перезаписываются и не удаляются: что больше не нужно ни сайту, ни черновику,
ни истории версий, sweep() переносит в content/.history/media/<время>/. Пока ролик не опубликован,
/media отдаёт его файлы только администратору (routes.media, public_media).

Библиотеку и выбор на главную правят только в Редакторе, группа «Видеоотзывы»: videos.json — один из
файлов черновика (drafts.EDITOR_FILES), на сайт изменения попадают после «Опубликовать».
Какие ролики стоят на главной — выбор как у «Реализовано»: блок reviews в home.json
хранит позиции items: {"video": "<id>"} или {} (заглушка). Id записи не меняется при замене
ссылки, поэтому другая ссылка встаёт на ту же позицию.

Обложка ролика — копия картинки RuTube в content/media/videos/<код>.webp: сервер скачивает её
один раз при добавлении (fetch_thumbnail), страницы сайта к RuTube за картинками не ходят.
"""

import json
import os
import re
import secrets
import ssl
import tempfile
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from PIL import Image, ImageOps

from .content import ContentError, read_json, rutube_video, video_page_url, video_player_url
from .storage import HISTORY_DIR, history_stamp, move_to_history

FILE = "videos"
FEATURED_MAX = 3
TITLE_MAX = 120
DESCRIPTION_MAX = 300
# Адрес шторки «Другие видео-отзывы» (/video-reviews) — занят приложением, как /portfolio у раздела
SHEET_SLUG = "video-reviews"
ID_RE = re.compile(r"[0-9a-f]{8}")
CODE_RE = re.compile(r"[0-9a-f]{32}")
# Обложки: content/media/videos/<код>.webp, не шире 960 px (карточка до ~380 px на экране с плотностью 2)
THUMB_DIR = "videos"
THUMB_SIZE = (960, 540)
THUMB_MAX_BYTES = 5 * 1024 * 1024
THUMB_TIMEOUT = 8
# Откуда RuTube отдаёт картинки: только https и только свои домены
THUMB_HOSTS = ("rtbcdn.ru", "rutube.ru")
# Загруженные видео и свои обложки: пути от content/media/, имя — 32 случайных шестнадцатеричных символа
FILE_DIR = "videos/files"
COVER_DIR = "videos/covers"
FILE_PATH_RE = re.compile(r"videos/files/[0-9a-f]{32}\.(?:mp4|webm)")
COVER_PATH_RE = re.compile(r"videos/covers/[0-9a-f]{32}\.webp")
COVER_SIZE = (1920, 1080)
COVER_QUALITY = 82
# Контейнеры, которые браузеры играют сами. MP4 — по «брендам» в заголовке ftyp; QuickTime (MOV)
# и Matroska (MKV) не принимаются: их многие браузеры не воспроизводят, а перекодировать нечем
MP4_BRANDS = {b"isom", b"iso2", b"iso3", b"iso4", b"iso5", b"iso6", b"mp41", b"mp42", b"avc1",
              b"M4V ", b"M4VH", b"M4VP", b"mmp4", b"dash", b"MSNV", b"f4v "}
VIDEO_EXTENSIONS = (".mp4", ".webm")
VIDEO_CHUNK = 1024 * 1024
# Управляющие символы (кроме обычных пробелов) из подписей убираются
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f​-‏ -‮⁠-⁤﻿]")


def empty():
    return {"videos": []}


def clean(text, limit):
    """Подпись из формы: без управляющих символов и лишних пробелов; длиннее limit — None."""
    value = re.sub(r"\s+", " ", _CONTROL.sub(" ", str(text or ""))).strip()
    return value if len(value) <= limit else None


def parse_link(raw):
    """Ссылка RuTube из формы → {"rutube": код, "url": страница ролика} или None.

    Принимаются страница ролика (rutube.ru/video/<код>/), закрытый ролик с ключом, shorts и код
    встраивания (rutube.ru/play/embed/<код>); любые другие адреса и HTML — нет."""
    text = str(raw or "").strip()
    video = rutube_video(text)
    if not video:
        return None
    return {"rutube": video[0], "url": video_page_url(text)}


def new_id(videos):
    """Постоянный id новой записи: 8 случайных шестнадцатеричных символов, не занятый в списке."""
    taken = {v.get("id") for v in videos if isinstance(v, dict)}
    while True:
        vid = secrets.token_hex(4)
        if vid not in taken:
            return vid


def load(content_dir):
    """Содержимое videos.json; нет файла — пустая библиотека. Испорченный JSON — ContentError."""
    path = Path(content_dir) / f"{FILE}.json"
    if not path.exists():
        return empty()
    data = read_json(path)
    data.setdefault("videos", [])
    return data


def is_file(v):
    """Ролик — загруженный файл (а не RuTube)."""
    return isinstance(v, dict) and "file" in v


def validate(data, media_dir=None):
    """Ошибки videos.json (для check-content и перед каждой записью из админки); None — файла нет.

    media_dir — content/media: тогда проверяется и то, что файлы видео и обложек есть на диске."""
    if data is None:
        return []
    if not isinstance(data, dict) or not isinstance(data.get("videos"), list):
        return ["videos.json: нужен список videos"]
    errors, ids, codes, files = [], [], [], []
    for i, v in enumerate(data["videos"]):
        where = f"videos.json: видео {i + 1}"
        if not isinstance(v, dict):
            errors.append(f"{where} — объект {{}}")
            continue
        vid, code = v.get("id"), v.get("rutube")
        if not (isinstance(vid, str) and ID_RE.fullmatch(vid)):
            errors.append(f"{where}: id — 8 символов 0–9, a–f")
        elif vid in ids:
            errors.append(f"{where}: id {vid} повторяется")
        ids.append(vid)
        if is_file(v):
            path = v.get("file")
            if "rutube" in v or "url" in v:
                errors.append(f"{where}: у загруженного видео нет rutube и url — только file")
            elif not (isinstance(path, str) and FILE_PATH_RE.fullmatch(path)):
                errors.append(f"{where}: file — videos/files/<32 символа 0–9, a–f>.mp4 или .webm")
            elif path in files:
                errors.append(f"{where}: файл {path} уже есть в списке")
            elif media_dir is not None and not (Path(media_dir) / path).is_file():
                errors.append(f"{where}: файл «{path}» не найден в content/media")
            files.append(path)
        else:
            link = parse_link(v.get("url"))
            if not (isinstance(code, str) and CODE_RE.fullmatch(code)):
                errors.append(f"{where}: rutube — код ролика RuTube (32 символа 0–9, a–f)")
            elif link is None or link["rutube"] != code:
                errors.append(f"{where}: url — ссылка RuTube на этот же ролик, например https://rutube.ru/video/{code}/")
            elif code in codes:
                errors.append(f"{where}: ролик {code} уже есть в списке")
            codes.append(code)
        if "cover" in v:
            cover = v.get("cover")
            if not (isinstance(cover, str) and COVER_PATH_RE.fullmatch(cover)):
                errors.append(f"{where}: cover — videos/covers/<32 символа 0–9, a–f>.webp")
            elif media_dir is not None and not (Path(media_dir) / cover).is_file():
                errors.append(f"{where}: обложка «{cover}» не найдена в content/media")
        for key, limit in (("title", TITLE_MAX), ("description", DESCRIPTION_MAX)):
            value = v.get(key, "")
            if not isinstance(value, str) or clean(value, limit) != value:
                errors.append(f"{where}: {key} — строка до {limit} символов без переносов и служебных символов")
        if not isinstance(v.get("active", True), bool):
            errors.append(f"{where}: active — true или false")
    return errors


def thumb_path(code):
    """Обложка ролика — путь от content/media/."""
    return f"{THUMB_DIR}/{code}.webp"


def has_thumb(media_dir, code):
    return isinstance(code, str) and CODE_RE.fullmatch(code) is not None and \
        (Path(media_dir) / thumb_path(code)).is_file()


# Системный набор корневых сертификатов macOS: Python с python.org своих не подключает
SYSTEM_CA = Path("/etc/ssl/cert.pem")


def _tls():
    """HTTPS с проверкой сертификата. Если у Python нет корневых сертификатов (Python с python.org
    на macOS), берём системный набор; проверка не отключается никогда."""
    context = ssl.create_default_context()
    if not context.cert_store_stats().get("x509_ca") and SYSTEM_CA.is_file():
        context.load_verify_locations(cafile=str(SYSTEM_CA))
    return context


def _get(url, limit):
    with urlopen(Request(url, headers={"User-Agent": "CRVU-site/1.0"}), timeout=THUMB_TIMEOUT,
                 context=_tls()) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("слишком большой ответ")
    return data


def fetch_thumbnail(media_dir, video, get=_get):
    """Скачать обложку ролика с RuTube в media/videos/<код>.webp. True — обложка есть.

    Адрес картинки берётся из открытого API RuTube (данные о ролике) и принимается только с
    https-доменов RuTube; картинка проверяется Pillow и сохраняется заново в WebP. Нет сети,
    ролик закрыт или удалён — False: карточка остаётся со значком воспроизведения."""
    code = video.get("rutube") if isinstance(video, dict) else None
    if not (isinstance(code, str) and CODE_RE.fullmatch(code)):
        return False
    try:
        info = json.loads(get(f"https://rutube.ru/api/video/{code}/?format=json", 1024 * 1024))
        thumb = urlparse(str(info.get("thumbnail_url") or ""))
        host = (thumb.hostname or "").lower()
        if thumb.scheme != "https" or thumb.username or thumb.port or \
                not any(host == h or host.endswith("." + h) for h in THUMB_HOSTS):
            return False
        with Image.open(BytesIO(get(thumb.geturl(), THUMB_MAX_BYTES))) as img:
            img = ImageOps.exif_transpose(img)
            img.thumbnail(THUMB_SIZE)
            target = Path(media_dir) / thumb_path(code)
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".thumb-", suffix=".tmp")
            os.close(fd)
            try:
                img.convert("RGB").save(tmp, "WEBP", quality=80)
                os.chmod(tmp, 0o644)  # mkstemp создаёт 0600 — nginx другого пользователя не отдал бы обложку
                os.replace(tmp, target)
            finally:
                Path(tmp).unlink(missing_ok=True)
        return True
    except Exception:  # noqa: BLE001 — сеть, JSON, картинка: в любом случае просто без обложки
        return False


# --- Загруженные видео и свои обложки ---

class VideoError(Exception):
    """Понятная причина, по которой файл не принят."""


def sniff_video(head):
    """Тип видео по первым байтам файла: "mp4", "webm" или VideoError с причиной."""
    if len(head) >= 12 and head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in MP4_BRANDS:
            return "mp4"
        if brand == b"qt  ":
            raise VideoError("видео в формате MOV (QuickTime) — сохраните его в MP4 (H.264) и загрузите снова")
        raise VideoError("этот вариант MP4 браузеры могут не воспроизвести — сохраните видео в MP4 (H.264) или WebM")
    if head[:4] == b"\x1a\x45\xdf\xa3":
        # Заголовок EBML: WebM — частный случай Matroska, тип записан в поле DocType
        if b"webm" in head[:64]:
            return "webm"
        raise VideoError("видео в формате MKV — сохраните его в MP4 (H.264) или WebM")
    raise VideoError("это не видео MP4 или WebM")


def _free_name(folder, ext):
    """Новое имя файла, которого ещё нет в папке."""
    while True:
        path = folder / f"{secrets.token_hex(16)}{ext}"
        if not path.exists():
            return path


class Upload:
    """Принятый, но ещё не поставленный на место файл: временный «.upload-…» рядом с целевой папкой.

    Приём (долгий — копирование видео) идёт без блокировки контента; place() — под content_lock вместе
    с записью черновика, поэтому sweep() не застанет файл, на который ещё никто не ссылается.
    Временные файлы начинаются с точки — их не трогают ни sweep(), ни сайт. discard() — убрать."""

    def __init__(self, media_dir, folder, tmp, ext):
        self.media_dir, self.folder, self.tmp, self.ext = Path(media_dir), folder, Path(tmp), ext

    def place(self):
        """Постоянное случайное имя без перезаписи (os.link не заменяет существующий файл) → путь от media/."""
        os.chmod(self.tmp, 0o644)
        while True:
            target = _free_name(self.media_dir / self.folder, self.ext)
            try:
                os.link(self.tmp, target)
            except FileExistsError:
                continue
            self.tmp.unlink(missing_ok=True)
            return target.relative_to(self.media_dir).as_posix()

    def discard(self):
        self.tmp.unlink(missing_ok=True)


def _temp(media_dir, folder):
    path = Path(media_dir) / folder
    path.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path, prefix=".upload-", suffix=".tmp")
    return fd, tmp


def receive_video(media_dir, stream, max_bytes):
    """Загруженный файл → Upload (видео MP4 или WebM) или VideoError с понятной причиной.

    Тип — по содержимому (sniff_video), не по имени и не по типу из браузера; больше max_bytes —
    VideoError, недописанный файл не остаётся."""
    fd, tmp = _temp(media_dir, FILE_DIR)
    try:
        with os.fdopen(fd, "wb") as out:
            head = stream.read(VIDEO_CHUNK)
            if not head:
                raise VideoError("пустой файл")
            kind = sniff_video(head)
            size = len(head)
            out.write(head)
            while chunk := stream.read(VIDEO_CHUNK):
                size += len(chunk)
                if size > max_bytes:
                    raise VideoError(f"файл больше {max_bytes // (1024 * 1024)} МБ")
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        if size > max_bytes:
            raise VideoError(f"файл больше {max_bytes // (1024 * 1024)} МБ")
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return Upload(media_dir, FILE_DIR, tmp, f".{kind}")


def receive_cover(media_dir, img):
    """Картинка Pillow (уже проверенная uploads.check_upload) → Upload обложки WebP до 1920×1080."""
    img = img.copy()
    img.thumbnail(COVER_SIZE, Image.Resampling.LANCZOS)
    fd, tmp = _temp(media_dir, COVER_DIR)
    os.close(fd)
    try:
        img.convert("RGB").save(tmp, "WEBP", quality=COVER_QUALITY, method=6)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return Upload(media_dir, COVER_DIR, tmp, ".webp")


def media_paths(data):
    """Файлы видео и обложек, на которые ссылается библиотека (пути от content/media/)."""
    videos = data.get("videos") if isinstance(data, dict) and isinstance(data.get("videos"), list) else []
    paths = set()
    for v in videos:
        if not isinstance(v, dict):
            continue
        for key, pattern in (("file", FILE_PATH_RE), ("cover", COVER_PATH_RE)):
            if isinstance(v.get(key), str) and pattern.fullmatch(v[key]):
                paths.add(v[key])
    return paths


def public_media(data):
    """Файлы, которые /media отдаёт посетителям: только включённые ролики опубликованной библиотеки."""
    videos = data.get("videos") if isinstance(data, dict) and isinstance(data.get("videos"), list) else []
    return media_paths({"videos": [v for v in videos if isinstance(v, dict) and v.get("active", True) is True]})


def sweep(content_dir, draft=None):
    """Файлы видео и обложек, которые больше никому не нужны, → content/.history/media/<время>/ (не удаляются).

    Нужен файл, если на него ссылается опубликованный videos.json, черновик (draft — его data) или любая
    сохранённая версия в .history/ (её можно восстановить из «Истории версий»). Вызывать под content_lock.
    → список перенесённых путей (от content/media/)."""
    content_dir = Path(content_dir)
    media = content_dir / "media"
    keep = set()
    try:
        keep |= media_paths(load(content_dir))
    except ContentError:
        return []          # опубликованный файл не читается — ничего не трогаем
    keep |= media_paths(draft)
    history = content_dir / HISTORY_DIR
    for copy in history.glob(f"{FILE}-*.json") if history.is_dir() else []:
        try:
            keep |= media_paths(json.loads(copy.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return []      # испорченная копия — не знаем, что в ней было, ничего не трогаем
    moved, stamp = [], history_stamp()
    for folder in (FILE_DIR, COVER_DIR):
        for path in sorted((media / folder).glob("*")) if (media / folder).is_dir() else []:
            rel = path.relative_to(media).as_posix()
            if path.is_file() and not path.name.startswith(".") and rel not in keep:
                move_to_history(content_dir, path, stamp)
                moved.append(rel)
    return moved


def cover_of(v, media_dir=None):
    """Обложка ролика (путь от media/) или "": своя обложка, иначе копия картинки RuTube."""
    cover = v.get("cover") if isinstance(v, dict) else None
    if isinstance(cover, str) and COVER_PATH_RE.fullmatch(cover) and \
            (media_dir is None or (Path(media_dir) / cover).is_file()):
        return cover
    code = v.get("rutube") if isinstance(v, dict) else None
    return thumb_path(code) if media_dir and not is_file(v) and has_thumb(media_dir, code) else ""


def card(v, media_dir=None):
    """Ролик для страницы — или None (выключен, испорчен, нет файла).

    RuTube: kind "rutube", адреса плеера и страницы RuTube. Файл: kind "file", video — путь от media/
    (плеер <video> страница создаёт по нажатию). У обоих — подписи и обложка (thumb, путь от media/)."""
    if not isinstance(v, dict) or not v.get("active", True):
        return None
    base = {"id": v.get("id"), "title": v.get("title") or "", "description": v.get("description") or "",
            "thumb": cover_of(v, media_dir)}
    if is_file(v):
        path = v.get("file")
        if not (isinstance(path, str) and FILE_PATH_RE.fullmatch(path)) or \
                (media_dir is not None and not (Path(media_dir) / path).is_file()):
            return None
        return {**base, "kind": "file", "video": path, "player": "", "page": ""}
    player = video_player_url(v.get("url"))
    if not player.startswith("https://rutube.ru/play/embed/"):
        return None
    return {**base, "kind": "rutube", "player": player, "page": video_page_url(v.get("url"))}


def featured_ids(items):
    """Id роликов на позициях блока (items из home.json), по порядку; пустые позиции пропускаются."""
    items = items if isinstance(items, list) else []
    return [it["video"] for it in items if isinstance(it, dict) and isinstance(it.get("video"), str)]


def for_site(data, items, media_dir=None):
    """{"featured": [ролик или None по позициям items], "more": остальные включённые ролики}.

    Ролик на позиции выключен, удалён или испорчен — пустое место (заглушка). Позиций нет или
    ни на одной нет ролика — featured пуст: блок показывает свои заглушки, как раньше.
    В «Другие видео-отзывы» попадают включённые ролики, которых нет на главной, в порядке библиотеки."""
    videos = data.get("videos") if isinstance(data, dict) and isinstance(data.get("videos"), list) else []
    by_id = {v.get("id"): v for v in videos if isinstance(v, dict)}
    items = items if isinstance(items, list) else []
    featured = []
    if featured_ids(items):
        featured = [card(by_id.get(it.get("video")), media_dir) if isinstance(it, dict) and it.get("video") else None
                    for it in items[:FEATURED_MAX]]
    shown = {c["id"] for c in featured if c}
    more = [c for c in (card(v, media_dir) for v in videos) if c and c["id"] not in shown]
    return {"featured": featured, "more": more}
