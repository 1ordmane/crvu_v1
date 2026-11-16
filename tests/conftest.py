import json
import shutil
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
# Тестовые данные: демонстрационные проекты (22 примера, test-arara — черновик) и тексты сайта.
# Тесты не зависят от рабочего content/: его состав меняется в админке.
CONTENT = Path(__file__).resolve().parent / "fixtures" / "content"
# Рабочий контент сайта — тесты его только читают (проверки репозитория в test_content.py)
WORKING_CONTENT = ROOT / "content"

# Фото демо-проектов из портфолио тестовых данных: синтетические картинки тех же размеров,
# что были на сайте (без настоящих снимков), версии — как у старых фото после make-image-sizes
DEMO_PHOTOS = {
    "portfolio/test-arara/01.jpg": (1000, 562),
    "portfolio/example-2026-1/01.webp": (1672, 941),
}


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    # Тесты не должны зависеть от локального .env и настроек терминала разработчика.
    monkeypatch.setenv("CRVU_ENV_FILE", "")
    for name in ("SECRET_KEY", "ADMIN_USERNAME", "ADMIN_PASSWORD_HASH", "SESSION_COOKIE_SECURE", "CRVU_CONTENT_DIR",
                 "CRVU_SHOW_PLACEHOLDERS", "CRVU_VIDEO_THUMBNAILS"):
        monkeypatch.delenv(name, raising=False)


def _working_content_state():
    """Список файлов рабочего content/ с размером и временем изменения (без .cache и .lock)."""
    return {str(p.relative_to(WORKING_CONTENT)): (p.stat().st_size, p.stat().st_mtime_ns)
            for p in sorted(WORKING_CONTENT.rglob("*"))
            if p.is_file() and p.name != ".lock" and ".cache" not in p.relative_to(WORKING_CONTENT).parts}


@pytest.fixture(scope="session", autouse=True)
def working_content_is_not_touched():
    # Если во время прогона кто-то правит сайт в админке, это тоже сработает — тогда перезапустите тесты.
    before = _working_content_state()
    yield
    assert _working_content_state() == before, "тесты изменили рабочий content/"


@pytest.fixture(scope="session")
def demo_media(tmp_path_factory):
    """Фото демо-проектов — один раз на прогон: основной файл с деталями и облегчённые версии."""
    from app.uploads import make_variants

    media = tmp_path_factory.mktemp("demo") / "media"
    for rel, size in DEMO_PHOTOS.items():
        path = media / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        base = Image.linear_gradient("L").resize(size).convert("RGB")
        noise = Image.merge("RGB", [Image.effect_noise(size, 30 + 8 * i) for i in range(3)])
        Image.blend(base, noise, 0.35).save(path, "JPEG" if path.suffix == ".jpg" else "WEBP", quality=90)
        make_variants(path)
    return media


@pytest.fixture
def make_content(tmp_path, demo_media):
    """Копия тестового контента для теста: JSON из tests/fixtures/content и фото демо-проектов.

    С publish_examples=True демонстрационные примеры (example: true) публикуются — только в копии,
    для сценариев, которым нужны опубликованные проекты. Остальные проекты, в том числе
    черновик test-arara, сохраняют свой статус.
    """
    def make(publish_examples=False):
        target = tmp_path / "content"
        shutil.copytree(CONTENT, target)
        shutil.copytree(demo_media, target / "media", dirs_exist_ok=True)
        if publish_examples:
            path = target / "portfolio.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            for project in data["projects"]:
                if project.get("example"):
                    project["status"] = "published"
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return target
    return make
