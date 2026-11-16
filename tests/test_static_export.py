"""Статическая сборка для GitHub Pages (scripts/export_static.py, инструкция — GITHUB_PAGES.md).

Сборка — только опубликованное: страницы глазами анонимного посетителя, файлы через публичные маршруты.
Базовый путь репозитория настраивается; заявка конфигуратора без внешнего адреса не притворяется
отправленной. Flask-сайт при этом не меняется.
"""

import importlib.util
import json
import re
from pathlib import Path

import pytest

from app import create_app

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("export_static", ROOT / "scripts" / "export_static.py")
export = importlib.util.module_from_spec(spec)
spec.loader.exec_module(export)
JS = (ROOT / "app" / "static" / "js" / "main.js").read_text(encoding="utf-8")
WORKFLOW = ROOT / ".github" / "workflows" / "pages.yml"
GITIGNORE = (ROOT / ".gitignore").read_text(encoding="utf-8")


@pytest.fixture
def built(make_content, tmp_path):
    content = make_content()
    summary = export.build(tmp_path / "site", "/demo-repo", None, content)
    return content, summary["out"], summary


def html(out, name="index.html"):
    return (out / name).read_text(encoding="utf-8")


def test_pages_are_built_with_the_repository_base_path(built):
    content, out, summary = built
    assert {"index.html", "portfolio.html", "contacts.html"} <= set(summary["pages"])
    assert (out / "404.html").is_file() and (out / ".nojekyll").is_file()
    page = html(out)
    # Все свои адреса — от базового пути; без него не остаётся ни одного
    assert not re.search(r'(?:href|src|data-[a-z-]+)="/(?!demo-repo/)', page)
    assert 'href="/demo-repo/portfolio"' in page and 'href="/demo-repo/static/css/style.css"' in page
    # Файлы, на которые ссылается страница, лежат в сборке по тем же путям
    for ref in set(re.findall(r'"/demo-repo/((?:static|media)/[^"?#\s]+)', page)) - {"static/img/stand-video/1280/",
                                                                                      "static/img/stand-video/960/"}:
        assert (out / ref).is_file(), ref
    assert len(list((out / "static/img/stand-video/1280").glob("frame-*.webp"))) == 128
    # Только нужное: видео 1020.mp4 страница не использует; из прежних кадров — лишь те, что служат
    # фото демо-проектов тестовых данных, а не весь набор
    assert not (out / "static/video/1020.mp4").exists() and not (out / "static/img/stand-1020").exists()
    assert len(list((out / "static/img/stand").glob("*.webp"))) < 160
    assert (out / "static/video/pre-video.mp4").is_file()


def test_sections_open_as_sheets_and_redirects_become_redirect_pages(built):
    _, out, _ = built
    # Раздел-шторка: своя страница, шторка уже открыта (как /portfolio на сервере)
    assert re.search(r'<section class="sheet is-open" id="portfolio"', html(out, "portfolio.html"))
    # Адрес, который на сервере — редирект (/about → блок «О нас» на главной), — страница-переход
    page = export.redirect_page("/demo-repo/#company")
    assert '<meta http-equiv="refresh" content="0; url=/demo-repo/#company">' in page
    assert '<a href="/demo-repo/#company">' in page


def test_only_published_content_and_no_private_files(built):
    content, out, _ = built
    published = {p["id"] for p in json.loads((content / "portfolio.json").read_text(encoding="utf-8"))["projects"]
                 if p.get("status") == "published"}
    exported = {p.name for p in (out / "media" / "portfolio").iterdir()}
    assert exported and exported <= published
    assert "test-arara" not in exported and (content / "media/portfolio/test-arara/01.jpg").is_file()
    names = [p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()]
    assert not [n for n in names if n.endswith((".json", ".py", ".md")) or "originals" in n or ".history" in n
                or ".drafts" in n or ".env" in n or "requests" in n]
    for page in out.glob("*.html"):
        text = page.read_text(encoding="utf-8")
        assert "/admin" not in text and "data-edit=" not in text and "csrf_token" not in text
    assert export.audit(out, content) == []


def test_security_policy_travels_as_meta_without_unsupported_directives(built):
    _, out, _ = built
    meta = re.search(r'<meta http-equiv="Content-Security-Policy" content="([^"]+)">', html(out)).group(1)
    assert "script-src 'self'" in meta and "connect-src 'self'" in meta and "frame-ancestors" not in meta


def test_design_request_without_endpoint_never_pretends_to_be_sent(built):
    _, out, _ = built
    form = re.search(r"<form class=\"request-form\" data-design-request[^>]*>", html(out)).group(0)
    assert "data-action" not in form
    assert "data-dr-unavailable" in html(out)
    # Скрипт без адреса не отправляет и не показывает «Спасибо»
    block = re.search(r"if \(!form\.dataset\.action\) \{.*?\n      \}", JS, re.S).group(0)
    assert 'setStatus(say("unavailable"' in block and "return;" in block and "showStep" not in block
    assert JS.index("if (!form.dataset.action)") < JS.index("const response = await fetch(form.dataset.action")


def test_design_request_uses_the_configured_https_endpoint(make_content, tmp_path):
    out = export.build(tmp_path / "site", "", "https://forms.example.com/crvu", make_content())["out"]
    page = html(out)
    assert 'data-action="https://forms.example.com/crvu"' in page and "data-dr-unavailable" not in page
    assert "connect-src 'self' https://forms.example.com" in page
    assert 'href="/static/css/style.css"' in page                       # без базового пути — корень домена
    with pytest.raises(export.ExportError):
        export.check_endpoint("http://forms.example.com/crvu")


def test_flask_site_keeps_its_own_request_endpoint(make_content):
    app = create_app({"TESTING": True, "CONTENT_DIR": str(make_content())})
    page = app.test_client().get("/").get_data(as_text=True)
    assert 'data-action="/request/design"' in page and "data-dr-unavailable" not in page


@pytest.mark.parametrize("value, expected", [("", ""), ("/", ""), ("crvu", "/crvu"), ("/crvu-site/", "/crvu-site")])
def test_base_path_is_normalized(value, expected):
    assert export.normalize_base(value) == expected


@pytest.mark.parametrize("value", ["../x", "/a b", "/a\"b", "/a/../b"])
def test_unsafe_base_paths_are_refused(value):
    with pytest.raises(export.ExportError):
        export.normalize_base(value)


def test_output_folder_is_never_a_foreign_or_project_folder(tmp_path):
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "keep.txt").write_text("user file", encoding="utf-8")
    with pytest.raises(export.ExportError):
        export.prepare_out(foreign)
    assert (foreign / "keep.txt").read_text(encoding="utf-8") == "user file"
    for protected in ("dist", "content", "app/static"):
        with pytest.raises(export.ExportError):
            export.prepare_out(ROOT / protected)
    # Прежнюю сборку (с меткой) можно пересобрать
    previous = export.prepare_out(tmp_path / "site")
    (previous / "old.html").write_text("old", encoding="utf-8")
    assert not (export.prepare_out(previous) / "old.html").exists()


def test_audit_catches_private_and_unpublished_files(built):
    content, out, _ = built
    (out / ".env").write_text("SECRET_KEY=x", encoding="utf-8")
    (out / "media" / "portfolio" / "test-arara").mkdir(parents=True)
    (out / "media" / "portfolio" / "test-arara" / "01.jpg").write_bytes(b"x")
    (out / "static" / "home.json").write_text("{}", encoding="utf-8")
    (out / "draft.html").write_text('<a href="/admin/">x</a> data-edit="hero.title"', encoding="utf-8")
    problems = "\n".join(export.audit(out, content))
    for expected in (".env", "test-arara", "home.json", "draft.html: в тексте «data-edit=»", "draft.html: в тексте «/admin/»"):
        assert expected in problems, expected


def test_workflow_builds_with_the_pages_base_path_and_publishes_only_the_export():
    flow = WORKFLOW.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in flow and "actions/configure-pages@v5" in flow
    assert '--base-path "${{ steps.pages.outputs.base_path }}"' in flow
    assert "CRVU_FORM_ENDPOINT: ${{ vars.CRVU_FORM_ENDPOINT }}" in flow
    assert "path: build/pages" in flow and "actions/deploy-pages@v4" in flow
    assert "pages: write" in flow and "id-token: write" in flow and "contents: read" in flow


def test_gitignore_keeps_private_and_generated_files_out():
    for line in (".env", ".env.*", "requests/", "content/originals/", "content/.drafts/", "content/.history/",
                 "content/.cache/", "backup/", "build/", "dist/", ".claude/settings.local.json"):
        assert line in GITIGNORE.splitlines(), line


# Фото и видео для Pages: pages-media/ со списком MANIFEST.tsv (сборка в Actions — только из него)

@pytest.fixture
def allowlist(built, tmp_path):
    content, out, _ = built
    media = tmp_path / "pages-media"
    export.sync_media(out, media)
    return content, out, media


def manifest_paths(media):
    return set(export.read_manifest(media))


def test_sync_takes_exactly_the_exported_media(allowlist):
    content, out, media = allowlist
    exported = {p.relative_to(out / "media").as_posix() for p in (out / "media").rglob("*") if p.is_file()}
    assert manifest_paths(media) == exported and exported
    assert not any(p.startswith("portfolio/test-arara/") for p in exported)          # черновик
    assert export.check_allowlist(media) == []
    line = next(l for l in (media / export.MANIFEST).read_text(encoding="utf-8").splitlines() if not l.startswith("#"))
    digest, size, rel = line.split("\t")
    assert len(digest) == 64 and int(size) == (media / rel).stat().st_size


def test_build_from_the_allowlist_matches_the_build_from_content(allowlist, tmp_path):
    content, out, media = allowlist
    second = export.build(tmp_path / "site2", "/demo-repo", None, content, media)
    first_files = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())
    assert first_files == sorted(p.relative_to(second["out"]).as_posix() for p in second["out"].rglob("*") if p.is_file())
    for rel in first_files:
        assert (out / rel).read_bytes() == (second["out"] / rel).read_bytes(), rel
    assert second["media_files"] == len(manifest_paths(media))


def test_files_outside_or_differing_from_the_list_stop_the_build(allowlist, tmp_path):
    content, _, media = allowlist
    (media / "portfolio" / "extra.webp").write_bytes(b"x")
    with pytest.raises(export.ExportError, match="portfolio/extra.webp: файла нет в списке"):
        export.build(tmp_path / "a", "", None, content, media)
    (media / "portfolio" / "extra.webp").unlink()
    victim = sorted(manifest_paths(media))[0]
    (media / victim).write_bytes(b"changed")
    with pytest.raises(export.ExportError, match="файл отличается от списка"):
        export.build(tmp_path / "b", "", None, content, media)


def test_published_photo_missing_from_the_list_stops_the_build_with_a_clear_message(allowlist, tmp_path):
    content, _, media = allowlist
    entries = export.read_manifest(media)
    cover = next(rel for rel in entries if rel.startswith("portfolio/") and "-" not in rel.rsplit("/", 1)[1])
    (media / cover).unlink()
    del entries[cover]
    export.write_manifest(media, entries)
    with pytest.raises(export.ExportError, match="ссылается на " + re.escape(cover) + " — его нет в"):
        export.build(tmp_path / "c", "", None, content, media)


def test_unpublished_project_left_in_the_list_stops_the_build(allowlist, tmp_path):
    content, _, media = allowlist
    path = content / "portfolio.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    shown = {rel.split("/")[1] for rel in manifest_paths(media) if rel.startswith("portfolio/")}
    for project in data["projects"]:
        if project["id"] in shown:
            project["status"] = "draft"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(export.ExportError, match="больше не использует"):
        export.build(tmp_path / "d", "", None, content, media)


def test_sync_never_drops_files_without_prune(allowlist, tmp_path):
    content, out, media = allowlist
    stale = media / "portfolio" / "old-project" / "01.webp"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"old")
    with pytest.raises(export.ExportError, match="old-project/01.webp"):
        export.sync_media(out, media)
    assert stale.is_file()
    result = export.sync_media(out, media, prune=True)
    assert result["removed"] == 1 and not stale.parent.exists() and export.check_allowlist(media) == []


def test_sync_refuses_to_write_into_content(built):
    _, out, _ = built
    with pytest.raises(export.ExportError):
        export.sync_media(out, ROOT / "content" / "media")


def test_repository_allowlist_matches_published_content():
    media = export.PAGES_MEDIA
    if not (media / export.MANIFEST).is_file():
        pytest.skip("pages-media/ ещё не создана")
    assert export.check_allowlist(media) == []
    entries = export.read_manifest(media)
    # Всё, без чего опубликованный контент неполон, — в списке; фото черновиков — нет
    assert set(export.expected_media(ROOT / "content")) <= set(entries)
    projects = json.loads((ROOT / "content" / "portfolio.json").read_text(encoding="utf-8"))["projects"]
    published = {p["id"] for p in projects if p.get("status") == "published"}
    assert {rel.split("/")[1] for rel in entries if rel.startswith("portfolio/")} <= published
    assert all(rel.endswith(".webp") or rel.endswith((".mp4", ".webm")) for rel in entries)


def test_workflow_builds_only_from_the_allowlist():
    flow = WORKFLOW.read_text(encoding="utf-8")
    assert "--media-dir pages-media" in flow
    # pages-media/ хранится в Git: ни одно правило .gitignore её не исключает
    rules = [l for l in GITIGNORE.splitlines() if l.strip() and not l.startswith("#")]
    assert not [r for r in rules if "pages-media" in r or r in ("*.webp", "*.mp4", "*.tsv")]
