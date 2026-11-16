"""Проверка и обработка загружаемых фотографий.

Файл принимается, только если Pillow открывает его как JPEG, PNG или WebP.
Затем картинка перекодируется в новый WebP: метаданные (EXIF, геопозиция)
и любые «прицепленные» к файлу данные не сохраняются, имя файла задаёт сервер.

Рядом с основным файлом (NN.webp, до 2560 px) сохраняются облегчённые версии
NN-640.webp, NN-1280.webp, NN-1920.webp — для карточек, обычного показа и больших
экранов (srcset). Версия больше исходника не создаётся. Все файлы пишутся через
временный файл и замену: при ошибке не остаётся недописанных фото.

Исходный файл сохраняется байт в байт до перекодирования — в content/originals/
(NN.jpg / NN.png / NN.webp, тот же номер, что у фото). Эта папка вне media/,
поэтому сайт её не отдаёт: в оригинале остаются EXIF и геопозиция.
"""

import io
import os
import re
import stat
import tempfile
import zipfile
from pathlib import Path

from PIL import Image, ImageCms, ImageOps, UnidentifiedImageError

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
# JPEG с блоком MPF (основной кадр + превью — так снимают многие камеры и iPhone) Pillow
# называет MPO. Это тот же JPEG: берётся первый кадр, конвейер обработки прежний.
JPEG_FORMATS = {"JPEG", "MPO"}
ORIGINAL_EXTENSIONS = {"JPEG": ".jpg", "MPO": ".jpg", "PNG": ".png", "WEBP": ".webp"}
OUTPUT_EXTENSION = ".webp"
WEBP_QUALITY = 85
# Облегчённые версии по длинной стороне: качество чуть ниже у мелких — там артефакты не видны
VARIANTS = {640: 80, 1280: 82, 1920: 84}
VARIANT_RE = re.compile(r"^(?P<stem>.+)-(?P<size>640|1280|1920)\.webp$")
SRGB = ImageCms.createProfile("sRGB")


class UploadError(Exception):
    pass


def read_limited(stream, max_bytes):
    data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise UploadError(f"файл больше {max_bytes // (1024 * 1024)} МБ")
    if not data:
        raise UploadError("пустой файл")
    return data


def _to_srgb(img):
    """Фото с профилем Adobe RGB / Display P3 → sRGB: иначе без профиля в WebP цвета потускнеют."""
    icc = img.info.get("icc_profile")
    if not icc:
        return img
    try:
        source = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        if "srgb" in ImageCms.getProfileDescription(source).lower():
            return img
        mode = "RGBA" if img.mode in ("RGBA", "LA") else "RGB"
        return ImageCms.profileToProfile(img.convert(mode), source, SRGB, outputMode=mode)
    except (ImageCms.PyCMSError, OSError, ValueError):
        return img  # повреждённый профиль — оставляем цвета как есть


def _check_header(img, max_pixels, max_pixels_full):
    """Формат и разрешение — по заголовку, до декодирования картинки."""
    if img.format == "MPO":
        # Принимаем только настоящий JPEG: первый кадр MPO декодируется JPEG-декодером
        if not img.tile or img.tile[0][0] != "jpeg":
            raise UploadError("поддерживаются только JPG, PNG и WebP")
    elif img.format not in ALLOWED_FORMATS:
        raise UploadError("поддерживаются только JPG, PNG и WebP")
    width, height = img.size
    if width * height > max_pixels:
        raise UploadError(f"слишком большое разрешение ({width}×{height})")
    # PNG и WebP декодируются только целиком (JPEG — сразу уменьшенным): свой, меньший предел
    if max_pixels_full and img.format not in JPEG_FORMATS and width * height > max_pixels_full:
        raise UploadError(f"слишком большое разрешение для PNG и WebP ({width}×{height}, больше "
                          f"{max_pixels_full // 1_000_000} Мп) — сохраните фото в JPEG или уменьшите его")


def inspect_image(data, max_pixels, max_pixels_full=None):
    """Проверка без декодирования: формат и разрешение из заголовка. Возвращает формат Pillow."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            _check_header(img, max_pixels, max_pixels_full)
            return img.format
    except UploadError:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError):
        raise UploadError("файл не является изображением JPG, PNG или WebP") from None
    except (OSError, ValueError, SyntaxError):
        raise UploadError("файл изображения повреждён") from None


def jpeg_reduction(width, height, max_side):
    """Во сколько раз (8, 4, 2 или 1) JPEG декодируется сразу уменьшенным — без потери качества.

    Обычный кадр: короткая сторона после уменьшения не меньше max_side (как раньше). Вытянутый
    кадр (панорама): достаточно длинной стороны с запасом 2 × max_side (как reducing_gap
    у Image.thumbnail) — иначе панорама на 100 Мп декодировалась бы целиком, около 1 ГБ памяти.
    """
    for factor in (8, 4, 2):
        if min(width, height) // factor >= max_side or max(width, height) // factor >= max_side * 2:
            return factor
    return 1


def process_image(data, max_side, max_pixels, max_pixels_full=None):
    """Байты загруженного файла → готовая к сохранению картинка Pillow."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            _check_header(img, max_pixels, max_pixels_full)
            if img.format in JPEG_FORMATS:
                # Большой JPEG декодируется сразу уменьшенным (1/2, 1/4…), но с запасом к нужному размеру:
                # снимок с камеры на 40 МБ не занимает сотни мегабайт памяти
                factor = jpeg_reduction(*img.size, max_side)
                img.draft("RGB", (img.width // factor, img.height // factor))
            img.load()
            img = ImageOps.exif_transpose(img)
            img = _to_srgb(img)
            has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
            img = img.convert("RGBA" if has_alpha else "RGB")
    except UploadError:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError):
        raise UploadError("файл не является изображением JPG, PNG или WebP") from None
    except (OSError, ValueError, SyntaxError):
        raise UploadError("файл изображения повреждён") from None
    img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    return img


def read_upload(file, max_bytes, max_pixels, max_pixels_full=None):
    """Файл из формы → (байты, формат) после проверки расширения, размера и заголовка."""
    name = file.filename or ""
    if Path(name).suffix.lower() not in ALLOWED_EXTENSIONS:
        raise UploadError("поддерживаются только JPG, PNG и WebP")
    data = read_limited(file.stream, max_bytes)
    return data, inspect_image(data, max_pixels, max_pixels_full)


def check_upload(file, max_bytes, max_side, max_pixels, max_pixels_full=None):
    """Файл из формы → картинка Pillow или UploadError с понятной причиной."""
    data, _ = read_upload(file, max_bytes, max_pixels, max_pixels_full)
    return process_image(data, max_side, max_pixels, max_pixels_full)


# --- Файлы папки или ZIP-архива стенда: что из них фото ---

# Служебные файлы Finder и Проводника — пропускаются молча (как и скрытые файлы и папка __MACOSX)
JUNK_NAMES = {".ds_store", "thumbs.db", "desktop.ini"}


def skip_reason(filename):
    """Имя файла из папки или архива → None (это фото), "" (служебный — молча) или причина пропуска."""
    name = Path(filename).name
    if not name or name.startswith(".") or name.casefold() in JUNK_NAMES:
        return ""
    suffix = Path(name).suffix.lower()
    if suffix in ALLOWED_EXTENSIONS:
        return None
    reason = ("HEIC не поддерживается, сохраните в JPEG" if suffix in (".heic", ".heif")
              else "не фото (JPG, PNG или WebP)")
    return f"«{name}»: пропущен — {reason}."


# --- ZIP с фото одного стенда (импорт в админке) ---

# Степень сжатия проверяется у файлов от 1 МБ: фото почти не сжимаются, «бомба» — сжимается в тысячи раз
ZIP_RATIO_FROM = 1024 * 1024


def natural_key(name):
    """«2.jpg» раньше «10.jpg»: числа в имени сравниваются как числа."""
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", name.casefold())]


def read_zip(stream, max_files, max_bytes, max_ratio):
    """ZIP стенда → (архив, имя папки или "", [(имя файла, ZipInfo)] по порядку имён, [пропущенные]).

    Архив не распаковывается на диск: имена из него используются только для порядка и названия.
    Одна папка верхнего уровня (или файлы в корне); вложенные папки, пути с «..» и абсолютные,
    символические ссылки, зашифрованные файлы, подозрительно сжатые файлы — отказ всему архиву.
    Не фото (и HEIC) пропускаются с причиной. Каждое фото потом читается read_zip_photo.
    """
    try:
        archive = zipfile.ZipFile(stream)
    except (zipfile.BadZipFile, OSError, ValueError):
        raise UploadError("файл не является ZIP-архивом") from None
    folders, photos, skipped = set(), [], []
    try:
        for info in archive.infolist():
            name = info.filename
            parts = [part for part in name.split("/") if part]
            if not parts:
                continue
            if name.startswith("/") or "\\" in name or ".." in parts or ":" in parts[0]:
                raise UploadError("недопустимый путь в архиве")
            if parts[0] == "__MACOSX" or any(part.startswith(".") for part in parts) or skip_reason(name) == "":
                continue
            if len(parts) > 2 or (info.is_dir() and len(parts) > 1):
                raise UploadError("в архиве вложенные папки — один стенд: одна папка с фото")
            if info.is_dir():
                folders.add(parts[0])
                continue
            if stat.S_ISLNK(info.external_attr >> 16):
                raise UploadError("в архиве символическая ссылка — так нельзя")
            if info.flag_bits & 0x1:
                raise UploadError("архив зашифрован — сохраните его без пароля")
            if len(parts) == 2:
                folders.add(parts[0])
            reason = skip_reason(name)
            if reason:
                skipped.append(reason)
                continue
            if info.file_size >= ZIP_RATIO_FROM and info.file_size > max_ratio * max(info.compress_size, 1):
                raise UploadError(f"«{parts[-1]}» подозрительно сильно сжат — это не похоже на фото")
            photos.append((parts[-1], info))
        if len(folders) > 1:
            raise UploadError("в архиве несколько папок — один стенд на один архив")
        if not photos:
            raise UploadError("в архиве нет фото JPG, PNG или WebP")
        if len(photos) > max_files:
            raise UploadError(f"в архиве {len(photos)} фото — не больше {max_files} за раз")
    except UploadError:
        archive.close()
        raise
    photos.sort(key=lambda item: natural_key(item[0]))
    return archive, (folders.pop() if folders else ""), photos, skipped


def read_zip_photo(archive, info, max_bytes, max_pixels, max_pixels_full=None):
    """Одно фото из архива → (байты, формат): размер читается не больше max_bytes + 1, затем заголовок."""
    try:
        with archive.open(info) as member:
            data = read_limited(member, max_bytes)
    except UploadError:
        raise
    except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError, EOFError):
        raise UploadError("файл в архиве повреждён") from None
    return data, inspect_image(data, max_pixels, max_pixels_full)


def save_original(data, path):
    """Исходный файл как есть. Существующий файл не заменяется: FileExistsError.

    Сначала временный файл, затем жёсткая ссылка на итоговое имя — в отличие от
    os.replace, она не перезаписывает файл, а недописанный оригинал не появится.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.link(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)
    return path


def original_paths(folder, stem):
    """Оригиналы фото с номером stem (01 → 01.jpg / 01.png / 01.webp) в папке оригиналов проекта."""
    suffixes = set(ORIGINAL_EXTENSIONS.values())
    return sorted(p for p in Path(folder).glob(f"{stem}.*") if p.suffix in suffixes)


def is_variant(name):
    """Облегчённая версия (01-640.webp), а не самостоятельное фото."""
    return bool(VARIANT_RE.match(name))


def variant_path(path, size):
    path = Path(path)
    return path.with_name(f"{path.stem}-{size}{OUTPUT_EXTENSION}")


def variant_paths(path):
    """Все возможные облегчённые версии фото (существующие и нет)."""
    return [variant_path(path, size) for size in VARIANTS]


def next_photo_path(folder, *others):
    """Следующее свободное имя 01.webp, 02.webp… в папке проекта.

    others — другие папки с тем же нумерованием (оригиналы): номер не занят ни в одной.
    """
    numbers = [int(m.group(1)) for d in (folder, *others) for p in Path(d).glob("*")
               if (m := re.fullmatch(r"(\d+)\.\w+", p.name))]
    return Path(folder) / f"{max(numbers, default=0) + 1:02d}{OUTPUT_EXTENSION}"


def _write_atomic(img, path, quality):
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}-", suffix=".tmp")
    os.close(fd)
    try:
        img.save(tmp, "WEBP", quality=quality, method=6)
        os.chmod(tmp, 0o644)  # mkstemp создаёт 0600 — веб-сервер другого пользователя не смог бы отдать фото
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _variants(img, path, only_missing=False):
    """Облегчённые версии, меньшие исходника. Возвращает записанные пути."""
    written = []
    longest = max(img.size)
    for size, quality in VARIANTS.items():
        target = variant_path(path, size)
        if size >= longest or (only_missing and target.exists()):
            continue
        copy = img.copy()
        copy.thumbnail((size, size), Image.Resampling.LANCZOS)
        _write_atomic(copy, target, quality)
        written.append(target)
    return written


def save_image(img, path):
    """Основное фото и его облегчённые версии. При ошибке записанные файлы удаляются."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    written = []
    try:
        written += _variants(img, path)
        _write_atomic(img, path, WEBP_QUALITY)
        written.append(path)
    except BaseException:
        for file in written:
            file.unlink(missing_ok=True)
        raise
    return written


def make_variants(path):
    """Дополнить облегчёнными версиями уже загруженное фото (основной файл не перекодируется)."""
    path = Path(path)
    with Image.open(path) as img:
        img.load()
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGBA" if img.mode in ("RGBA", "LA", "P") and "A" in img.getbands() else "RGB")
        return _variants(img, path, only_missing=True)
