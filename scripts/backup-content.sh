#!/usr/bin/env bash
# Резервная копия контента сайта CRVU: весь content/ (JSON, media/, originals/, .history/)
# в архив tar.gz — без content/.cache/ и content/.lock. Секретов (.env) в content/ нет, и он
# не архивируется. Рабочий контент только читается.
#
# Запуск из любой папки:
#   scripts/backup-content.sh
#   CRVU_BACKUP_DIR=/Volumes/Диск/CRVU-backups scripts/backup-content.sh
#
# Куда: $CRVU_BACKUP_DIR, иначе ~/CRVU-backups (создаётся). Что: $CRVU_CONTENT_DIR, иначе
# content/ рядом со скриптом. Имя — crvu-content-ГГГГММДД-ЧЧММСС.tar.gz; существующие архивы
# не перезаписываются и не удаляются. Архив создаётся с правами 0600: в оригиналах EXIF и геометка.
#
# На время архивации берётся та же блокировка content/.lock, что у админ-панели (fcntl.flock):
# сохранения в админке подождут, а архив не застанет наполовину записанные файлы.
#
# Проверить архив и восстановить его во временную папку (не поверх рабочего content/!):
#   tar -tzf ~/CRVU-backups/crvu-content-….tar.gz | less
#   mkdir -p /tmp/crvu-restore && tar -xzf ~/CRVU-backups/crvu-content-….tar.gz -C /tmp/crvu-restore
#   CRVU_CONTENT_DIR=/tmp/crvu-restore/content CRVU_ENV_FILE= .venv/bin/flask --app app check-content

set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONTENT="${CRVU_CONTENT_DIR:-$ROOT/content}"
DEST="${CRVU_BACKUP_DIR:-$HOME/CRVU-backups}"

fail() {
  echo "Ошибка: $*" >&2
  exit 1
}

# Python — из .venv проекта, иначе системный python3 (нужна только стандартная библиотека)
if [[ -n "${CRVU_PYTHON:-}" ]]; then
  PYTHON="$CRVU_PYTHON"
elif [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON="$ROOT/.venv/bin/python"
else
  PYTHON="$(command -v python3 || true)"
fi
[[ -n "$PYTHON" ]] || fail "не найден python3"

[[ -d "$CONTENT" ]] || fail "нет папки контента: $CONTENT"
[[ -f "$CONTENT/portfolio.json" ]] || fail "в $CONTENT нет portfolio.json — это точно папка контента?"
CONTENT="$(cd "$CONTENT" && pwd -P)"

mkdir -p "$DEST" || fail "не удалось создать $DEST"
DEST="$(cd "$DEST" && pwd -P)"
# Архив внутри content/ попал бы в следующие копии и рос бы сам из себя
case "$DEST/" in
  "$CONTENT"/*) fail "папка для архивов ($DEST) внутри контента ($CONTENT) — укажите другую в CRVU_BACKUP_DIR" ;;
esac

NAME="$(basename "$CONTENT")"
PARENT="$(dirname "$CONTENT")"
TMP="$(mktemp "$DEST/.crvu-content-XXXXXX")"
trap 'rm -f "$TMP"' EXIT

# Блокировка и tar — в одном процессе Python: lock держится, пока tar не закончит.
# .lock открывается только на чтение (создаётся, лишь если его нет, — как это сделала бы админка).
"$PYTHON" - "$CONTENT" "$PARENT" "$NAME" "$TMP" <<'PY' || fail "архивация не удалась (см. сообщения выше)"
import fcntl, os, subprocess, sys
content, parent, name, archive = sys.argv[1:5]
fd = os.open(os.path.join(content, ".lock"), os.O_RDONLY | os.O_CREAT, 0o644)
try:
    fcntl.flock(fd, fcntl.LOCK_EX)
    code = subprocess.call(["tar", "-czf", archive,
                            "--exclude", f"{name}/.cache", "--exclude", f"{name}/.lock",
                            "-C", parent, name])
finally:
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)
sys.exit(code)
PY

# Проверка: файл не пустой, архив читается целиком, в нём есть portfolio.json
[[ -s "$TMP" ]] || fail "архив пустой"
LIST="$(tar -tzf "$TMP")" || fail "архив не читается"
grep -qx "$NAME/portfolio.json" <<<"$LIST" || fail "в архиве нет $NAME/portfolio.json"
if grep -qE "^$NAME/(\.cache/|\.lock$)" <<<"$LIST"; then
  fail "в архив попали .cache или .lock"
fi

# Итоговое имя: не перезаписываем — жёсткая ссылка не создаётся поверх существующего файла
STAMP="$(date +%Y%m%d-%H%M%S)"
FINAL="$DEST/crvu-content-$STAMP.tar.gz"
n=1
while ! ln "$TMP" "$FINAL" 2>/dev/null; do
  if [[ ! -e "$FINAL" ]]; then
    # Файловая система без жёстких ссылок (например, exFAT): переименование без перезаписи
    mv -n "$TMP" "$FINAL"
    [[ -e "$TMP" ]] && fail "не удалось сохранить архив как $FINAL"
    break
  fi
  n=$((n + 1))
  FINAL="$DEST/crvu-content-$STAMP-$n.tar.gz"
done
rm -f "$TMP"

count() { grep -cE "$1" <<<"$LIST" || true; }
echo "Архив: $FINAL ($(du -h "$FINAL" | cut -f1))"
echo "  JSON в корне:   $(count "^$NAME/[^/]+\.json$")"
echo "  media/:         $(count "^$NAME/media/.+[^/]$") файлов"
echo "  originals/:     $(count "^$NAME/originals/.+[^/]$") файлов"
echo "  .history/:      $(count "^$NAME/\.history/.+[^/]$") файлов"
