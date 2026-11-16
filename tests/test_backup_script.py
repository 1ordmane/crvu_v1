"""scripts/backup-content.sh на временном контенте: состав архива, блокировка, ошибки, восстановление."""

import fcntl
import hashlib
import os
import shutil
import subprocess
import tarfile
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "backup-content.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None or shutil.which("tar") is None,
                                reason="нужны bash и tar")


@pytest.fixture
def content(tmp_path):
    root = tmp_path / "site" / "content"
    files = {
        "portfolio.json": b'{"projects": []}',
        "home.json": b"{}",
        "media/portfolio/stand/01.webp": b"webp",
        "media/portfolio/stand/01-640.webp": b"small",
        "originals/portfolio/stand/01.jpg": b"camera original with exif",
        ".history/portfolio-20260101-000000-000000.json": b"{}",
        ".history/media/20260101-000000-000000/portfolio/old/01.webp": b"deleted photo",
        ".history/originals/20260101-000000-000000/portfolio/old/01.jpg": b"deleted original",
        ".drafts/home.json": b"{}",
        ".cache/thumbs/abc.webp": b"cache",
        ".lock": b"",
    }
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return root


def snapshot(root):
    return {str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
            for p in sorted(root.rglob("*")) if p.is_file()}


def run(content, dest=None, home=None, **env):
    environ = {**os.environ, "CRVU_CONTENT_DIR": str(content), **env}
    environ.pop("CRVU_BACKUP_DIR", None)
    if dest is not None:
        environ["CRVU_BACKUP_DIR"] = str(dest)
    if home is not None:
        environ["HOME"] = str(home)
    return subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=environ, timeout=60)


def archives(dest):
    return sorted(p for p in Path(dest).glob("crvu-content-*.tar.gz"))


def test_archive_has_everything_except_cache_and_lock(content, tmp_path):
    before = snapshot(content)
    dest = tmp_path / "backups"

    result = run(content, dest)

    assert result.returncode == 0, result.stderr
    [archive] = archives(dest)
    assert archive.stat().st_size > 0
    assert archive.stat().st_mode & 0o777 == 0o600                 # в оригиналах EXIF и геометка
    with tarfile.open(archive) as tar:
        names = {m.name for m in tar.getmembers() if m.isfile()}
    assert names == {f"content/{n}" for n in before if not n.startswith(".cache/") and n != ".lock"}
    assert "media/:         2 файлов" in result.stdout and "originals/:     1 файлов" in result.stdout
    # Рабочий контент не изменился, временных файлов не осталось
    assert snapshot(content) == before
    assert [p.name for p in dest.iterdir()] == [archive.name]


def test_restore_into_temporary_folder_gives_same_files(content, tmp_path):
    dest = tmp_path / "backups"
    assert run(content, dest).returncode == 0
    restore = tmp_path / "restore"
    restore.mkdir()

    subprocess.run(["tar", "-xzf", str(archives(dest)[0]), "-C", str(restore)], check=True)

    restored = snapshot(restore / "content")
    original = snapshot(content)
    assert {k: v[2] for k, v in restored.items()} == {
        k: v[2] for k, v in original.items() if not k.startswith(".cache/") and k != ".lock"}


def test_default_destination_is_home_backups_folder(content, tmp_path):
    home = tmp_path / "home"
    home.mkdir()

    result = run(content, home=home)

    assert result.returncode == 0, result.stderr
    assert len(archives(home / "CRVU-backups")) == 1


def test_existing_archives_are_never_overwritten(content, tmp_path):
    dest = tmp_path / "backups"
    assert run(content, dest).returncode == 0
    first = archives(dest)[0]
    first_bytes = first.read_bytes()

    assert run(content, dest).returncode == 0                      # та же секунда — другое имя
    assert run(content, dest).returncode == 0

    assert len(archives(dest)) == 3
    assert first.read_bytes() == first_bytes


def test_backup_folder_inside_content_is_refused(content):
    before = snapshot(content)

    result = run(content, content / "backups")

    assert result.returncode != 0 and "внутри контента" in result.stderr
    assert not list((content / "backups").glob("*"))
    assert {k: v for k, v in snapshot(content).items()} == before


def test_missing_content_folder_fails(tmp_path):
    result = run(tmp_path / "nope", tmp_path / "backups")

    assert result.returncode != 0 and "нет папки контента" in result.stderr


def test_tar_error_fails_and_leaves_no_archive(content, tmp_path):
    secret = content / "media" / "portfolio" / "stand" / "01.webp"
    secret.chmod(0)                                                 # tar не сможет прочитать файл
    dest = tmp_path / "backups"
    try:
        result = run(content, dest)
    finally:
        secret.chmod(0o644)

    assert result.returncode != 0 and "архивация не удалась" in result.stderr
    assert list(dest.iterdir()) == []                               # ни архива, ни временного файла


def test_waits_for_admin_lock(content, tmp_path):
    dest = tmp_path / "backups"
    environ = {**os.environ, "CRVU_CONTENT_DIR": str(content), "CRVU_BACKUP_DIR": str(dest)}
    with open(content / ".lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)                            # админка сейчас записывает контент
        process = subprocess.Popen(["bash", str(SCRIPT)], env=environ, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        time.sleep(1.5)
        assert process.poll() is None and archives(dest) == []
        fcntl.flock(lock, fcntl.LOCK_UN)
    assert process.wait(timeout=30) == 0
    assert len(archives(dest)) == 1
