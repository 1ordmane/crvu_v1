"""project_photos/ — подготовленные подборки фото стендов (одна папка = один стенд).

Хранилище исходных подборок рядом с content/: сайт его не раздаёт и не читает, архив делает
scripts/backup-project-photos.sh. Тесты работают только с временными папками.
"""

import hashlib
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

from app import create_app

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "backup-project-photos.sh"
needs_shell = pytest.mark.skipif(shutil.which("bash") is None or shutil.which("tar") is None, reason="нужны bash и tar")


@pytest.fixture
def photos(tmp_path):
    root = tmp_path / "site" / "project_photos"
    files = {
        "Expo Moscow 2025/01.jpg": b"jpeg with exif and gps",
        "Expo Moscow 2025/02.jpg": b"jpeg 2",
        "Haval 2025/01.jpg": b"jpeg 3",
        "Suzuki 2024/01.png": b"png",
    }
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return root


def snapshot(root):
    return {str(p.relative_to(root)): (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
            for p in sorted(root.rglob("*")) if p.is_file()}


def run(photos_dir, dest=None, home=None):
    env = {**os.environ, "CRVU_PHOTOS_DIR": str(photos_dir)}
    env.pop("CRVU_BACKUP_DIR", None)
    if dest is not None:
        env["CRVU_BACKUP_DIR"] = str(dest)
    if home is not None:
        env["HOME"] = str(home)
    return subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=env, timeout=60)


def archives(dest):
    return sorted(Path(dest).glob("crvu-project-photos-*.tar.gz"))


@needs_shell
def test_archive_has_all_folders_and_sources_stay_untouched(photos, tmp_path):
    before = snapshot(photos)
    dest = tmp_path / "backups"

    result = run(photos, dest)

    assert result.returncode == 0, result.stderr
    [archive] = archives(dest)
    assert archive.stat().st_mode & 0o777 == 0o600                    # в снимках EXIF и геометка
    with tarfile.open(archive) as tar:
        names = {m.name for m in tar.getmembers() if m.isfile()}
    assert names == {f"project_photos/{n}" for n in before}
    assert "папок стендов: 3" in result.stdout and "файлов: 4" in result.stdout
    # Исходные папки и фото не удаляются и не меняются
    assert snapshot(photos) == before
    assert [p.name for p in dest.iterdir()] == [archive.name]


@needs_shell
def test_restore_gives_same_files(photos, tmp_path):
    dest = tmp_path / "backups"
    assert run(photos, dest).returncode == 0
    restore = tmp_path / "restore"
    restore.mkdir()

    subprocess.run(["tar", "-xzf", str(archives(dest)[0]), "-C", str(restore)], check=True)

    assert {k: v[1] for k, v in snapshot(restore / "project_photos").items()} == {
        k: v[1] for k, v in snapshot(photos).items()}


@needs_shell
def test_default_destination_and_no_overwrite(photos, tmp_path):
    home = tmp_path / "home"
    home.mkdir()

    assert run(photos, home=home).returncode == 0
    first = archives(home / "CRVU-backups")[0]
    first_bytes = first.read_bytes()
    assert run(photos, home=home).returncode == 0                     # та же секунда — другое имя

    assert len(archives(home / "CRVU-backups")) == 2
    assert first.read_bytes() == first_bytes


@needs_shell
def test_missing_folder_and_folder_inside_photos_are_refused(photos, tmp_path):
    missing = run(tmp_path / "nope", tmp_path / "backups")
    assert missing.returncode != 0 and "нет папки с подборками" in missing.stderr

    before = snapshot(photos)
    for dest in (photos / "backups", photos / "Haval 2025" / "nested" / "backups"):
        inside = run(photos, dest)
        assert inside.returncode != 0 and "внутри подборок" in inside.stderr
    assert snapshot(photos) == before
    # Отказ — до создания папки: внутри подборок ничего не появилось
    assert not (photos / "backups").exists() and not (photos / "Haval 2025" / "nested").exists()


@needs_shell
def test_unreadable_file_fails_without_leftovers(photos, tmp_path):
    secret = photos / "Haval 2025" / "01.jpg"
    secret.chmod(0)
    dest = tmp_path / "backups"
    try:
        result = run(photos, dest)
    finally:
        secret.chmod(0o644)

    assert result.returncode != 0 and "архивация не удалась" in result.stderr
    assert list(dest.iterdir()) == []


def test_project_photos_next_to_content_are_not_public(tmp_path, make_content):
    # Та же раскладка, что на сайте: project_photos/ — рядом с content/
    content = make_content()
    folder = content.parent / "project_photos" / "Expo Moscow 2025"
    folder.mkdir(parents=True)
    (folder / "01.jpg").write_bytes(b"jpeg with gps")
    client = create_app({"TESTING": True, "CONTENT_DIR": str(content)}).test_client()

    for path in ("/project_photos/Expo%20Moscow%202025/01.jpg",
                 "/media/../project_photos/Expo%20Moscow%202025/01.jpg",
                 "/media/%2e%2e/project_photos/Expo%20Moscow%202025/01.jpg",
                 "/static/../../project_photos/Expo%20Moscow%202025/01.jpg",
                 "/media/portfolio/../../project_photos/Expo%20Moscow%202025/01.jpg"):
        assert client.get(path).status_code == 404, path
    # Сайт подборки не читает: на страницах и в проверке контента их нет
    assert "project_photos" not in client.get("/portfolio").get_data(as_text=True)
