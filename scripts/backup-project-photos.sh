#!/usr/bin/env bash
# Резервная копия подготовленных подборок фото стендов: project_photos/ (одна папка = один стенд)
# в архив tar.gz. Подборки — отдельное хранилище: сайт их не раздаёт и не читает, импорт в
# портфолио — только вручную через ZIP в админке. Исходные папки и файлы скрипт только читает.
#
# Запуск из любой папки:
#   scripts/backup-project-photos.sh
#   CRVU_BACKUP_DIR=/Volumes/Диск/CRVU-backups scripts/backup-project-photos.sh
#
# Что: $CRVU_PHOTOS_DIR, иначе project_photos/ в корне проекта. Куда: $CRVU_BACKUP_DIR, иначе
# ~/CRVU-backups (создаётся) — рядом с архивами контента (scripts/backup-content.sh).
# Имя — crvu-project-photos-ГГГГММДД-ЧЧММСС.tar.gz; существующие архивы не перезаписываются и не
# удаляются. Права 0600: в снимках EXIF и геометка. Блокировка не нужна — админка сюда не пишет.
#
# Проверка и восстановление — только во временную папку:
#   tar -tzf ~/CRVU-backups/crvu-project-photos-….tar.gz | less
#   mkdir -p /tmp/crvu-photos-restore && tar -xzf ~/CRVU-backups/crvu-project-photos-….tar.gz -C /tmp/crvu-photos-restore

set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PHOTOS="${CRVU_PHOTOS_DIR:-$ROOT/project_photos}"
DEST="${CRVU_BACKUP_DIR:-$HOME/CRVU-backups}"

fail() {
  echo "Ошибка: $*" >&2
  exit 1
}

[[ -d "$PHOTOS" ]] || fail "нет папки с подборками: $PHOTOS"
PHOTOS="$(cd "$PHOTOS" && pwd -P)"

# Python — из .venv проекта, иначе системный python3 (только стандартная библиотека)
if [[ -x "$ROOT/.venv/bin/python" ]]; then PYTHON="$ROOT/.venv/bin/python"; else PYTHON="$(command -v python3 || true)"; fi
[[ -n "$PYTHON" ]] || fail "не найден python3"

# Папку для архивов проверяем до её создания: внутри подборок не должно появиться ничего
DEST_ABS="$("$PYTHON" -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$DEST")" || fail "неверный путь: $DEST"
case "$DEST_ABS/" in
  "$PHOTOS"/*) fail "папка для архивов ($DEST) внутри подборок ($PHOTOS) — укажите другую в CRVU_BACKUP_DIR" ;;
esac
mkdir -p "$DEST" || fail "не удалось создать $DEST"
DEST="$(cd "$DEST" && pwd -P)"
case "$DEST/" in
  "$PHOTOS"/*) fail "папка для архивов ($DEST) внутри подборок ($PHOTOS) — укажите другую в CRVU_BACKUP_DIR" ;;
esac

NAME="$(basename "$PHOTOS")"
TMP="$(mktemp "$DEST/.crvu-project-photos-XXXXXX")"
trap 'rm -f "$TMP"' EXIT

tar -czf "$TMP" -C "$(dirname "$PHOTOS")" "$NAME" || fail "архивация не удалась (см. сообщения выше)"

# Проверка: файл не пустой, архив читается целиком
[[ -s "$TMP" ]] || fail "архив пустой"
LIST="$(tar -tzf "$TMP")" || fail "архив не читается"

# Итоговое имя без перезаписи: жёсткая ссылка не создаётся поверх существующего файла
STAMP="$(date +%Y%m%d-%H%M%S)"
FINAL="$DEST/crvu-project-photos-$STAMP.tar.gz"
n=1
while ! ln "$TMP" "$FINAL" 2>/dev/null; do
  if [[ ! -e "$FINAL" ]]; then
    # Файловая система без жёстких ссылок (например, exFAT): переименование без перезаписи
    mv -n "$TMP" "$FINAL"
    [[ -e "$TMP" ]] && fail "не удалось сохранить архив как $FINAL"
    break
  fi
  n=$((n + 1))
  FINAL="$DEST/crvu-project-photos-$STAMP-$n.tar.gz"
done
rm -f "$TMP"

count() { grep -cE "$1" <<<"$LIST" || true; }
echo "Архив: $FINAL ($(du -h "$FINAL" | cut -f1))"
echo "  папок стендов: $(count "^$NAME/[^/]+/$")"
echo "  файлов: $(count "^$NAME/.+[^/]$")"
