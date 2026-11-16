"""«Частые вопросы» на главной: последний блок перед подвалом «Обсудим ваш следующий проект».

Вопросы и ответы — в home.json (блок type "faq"), правятся в «Редакторе». Аккордеон: целая строка
вопроса — кнопка с aria-expanded; открыт не больше одного ответа (main.js, initFaq). Без скрипта
ответы видны (скрываются только классом is-ready, который ставит скрипт).
"""

import json
import re
from pathlib import Path

import pytest

from tests.test_app import edit, make_client

ROOT = Path(__file__).parents[1]
WORKING_HOME = ROOT / "content" / "home.json"

QUESTIONS = [
    "Сколько стоит строительство выставочного стенда?",
    "За сколько времени нужно начинать подготовку стенда?",
    "Вы работаете под ключ?",
    "Можно ли заказать стенд по собственному дизайну?",
    "Вы работаете с небольшими стендами?",
    "В каких городах вы работаете?",
    "Что нужно для расчёта стоимости?",
    "Как оставить заявку на разработку стенда?",
]


def working_faq():
    return next(s for s in json.loads(WORKING_HOME.read_text(encoding="utf-8"))["sections"] if s["type"] == "faq")


@pytest.fixture
def content_dir(make_content):
    content_dir = make_content()
    edit(content_dir, "home", lambda d: d["sections"].append(working_faq()))
    return content_dir


def faq_html(html):
    return re.search(r'<section class="section faq is-paper" id="faq".*?</section>', html, re.S).group(0)


def test_working_home_ends_with_faq_of_eight_questions():
    sections = json.loads(WORKING_HOME.read_text(encoding="utf-8"))["sections"]
    assert [s["type"] for s in sections][-1] == "faq" and [s["type"] for s in sections].count("faq") == 1
    faq = working_faq()
    assert faq["title"] == "Частые вопросы" and faq.get("visible", True)
    assert [item["question"] for item in faq["items"]] == QUESTIONS
    assert all(item["answer"].strip() for item in faq["items"])
    assert faq["items"][0]["answer"].startswith("Стоимость зависит от размеров стенда")
    assert "«Обсудить мой проект»" in faq["items"][7]["answer"]


def test_faq_is_the_last_block_right_before_the_footer(content_dir):
    html = make_client(content_dir).get("/").get_data(as_text=True)

    main = html.split("</main>")[0]
    assert re.findall(r'<section class="section [^"]*" id="([\w-]+)"', main)[-1] == "faq"
    # После FAQ до подвала — только скрытые шторки разделов, ни одного блока страницы
    between = html[html.index('id="faq"'):html.index('<footer class="site-footer">')]
    assert 'class="section ' not in between.split("</section>", 1)[1]
    # Подвал прежний: заголовок, текст и кнопка заявки
    footer = html.split('<footer class="site-footer">')[1]
    assert "Обсудим ваш следующий проект" in footer and "data-open-request" in footer
    assert html.count('id="faq"') == 1


def test_questions_are_buttons_controlling_answer_regions(content_dir):
    block = faq_html(make_client(content_dir).get("/").get_data(as_text=True))
    faq = working_faq()

    assert '<h2 class="section-title" id="faq-title" data-reveal>Частые вопросы</h2>' in block
    items = re.findall(r'<li class="faq-item">.*?</li>', block, re.S)
    assert len(items) == 8
    for n, (item, data) in enumerate(zip(items, faq["items"]), 1):
        button = re.search(r'<h3 class="faq-q"><button class="faq-toggle" type="button" id="faq-q-(\d+)"'
                           r' aria-expanded="false" aria-controls="faq-a-(\d+)" data-faq-toggle>', item)
        assert button and button.groups() == (str(n), str(n))
        assert f'<span class="faq-question">{data["question"]}</span>' in item
        assert re.search(rf'<div class="faq-a" id="faq-a-{n}" role="region" aria-labelledby="faq-q-{n}" data-faq-answer>', item)
        assert f"<p>{data['answer']}</p>" in item
    # Без скрипта ответы видны: скрывает их только класс is-ready от main.js
    assert re.search(r'<ul class="faq-list" data-faq>', block)
    assert " style=" not in block


def test_faq_script_keeps_one_answer_open_and_respects_reduced_motion():
    js = (ROOT / "app" / "static" / "js" / "main.js").read_text(encoding="utf-8")
    faq = re.search(r"function initFaq\(\) \{.*?\n  \}\n", js, re.S).group(0)
    assert 'classList.add("is-ready")' in faq and "aria-expanded" in faq and "initFaq();" in js
    css = (ROOT / "app" / "static" / "css" / "style.css").read_text(encoding="utf-8")
    assert re.search(r"\.faq-list\.is-ready \.faq-a \{[^}]*grid-template-rows: 0fr", css)
    assert re.search(r"@media \(prefers-reduced-motion: reduce\) \{\s*\.faq-a,", css)


def test_hidden_faq_is_not_shown(content_dir):
    edit(content_dir, "home", lambda d: next(s for s in d["sections"] if s["type"] == "faq").update(visible=False))
    assert 'id="faq"' not in make_client(content_dir).get("/").get_data(as_text=True)


@pytest.mark.parametrize("item, message", [
    ({"question": "", "answer": "Ответ"}, "вопрос"),
    ({"question": "Вопрос?", "answer": " "}, "ответ"),
])
def test_check_content_requires_question_and_answer(content_dir, item, message):
    from app import create_app
    from app.content import validate_content
    edit(content_dir, "home", lambda d: next(s for s in d["sections"] if s["type"] == "faq")["items"].append(item))
    static = create_app({"TESTING": True, "CONTENT_DIR": str(content_dir)}).static_folder
    errors = validate_content(content_dir, static)
    assert any("faq" in e and message in e for e in errors), errors


def test_questions_are_edited_in_the_editor(content_dir):
    from werkzeug.security import generate_password_hash
    from app import create_app
    from tests.test_editor import PASSWORD, TOKEN_RE, post, save_draft

    app = create_app({"TESTING": True, "CONTENT_DIR": str(content_dir), "SECRET_KEY": "test-secret",
                      "ADMIN_USERNAME": "admin", "ADMIN_PASSWORD_HASH": generate_password_hash(PASSWORD)})
    admin = app.test_client()
    token = TOKEN_RE.search(admin.get("/admin/login").get_data(as_text=True)).group(1)
    admin.post("/admin/login", data={"csrf_token": token, "username": "admin", "password": PASSWORD})
    at = len(json.loads((content_dir / "home.json").read_text(encoding="utf-8"))["sections"]) - 1

    editor = admin.get("/admin/editor").get_data(as_text=True)
    assert "Вопросы: Частые вопросы" in editor
    assert f'name="sections.{at}.items.0.question"' in editor and f'name="sections.{at}.items.7.answer"' in editor
    preview = admin.get("/admin/editor/preview").get_data(as_text=True)
    assert f'data-edit="sections.{at}.items.2.question"' in preview

    assert save_draft(admin, **{f"sections.{at}.items.2.question": "Вы работаете под ключ и с монтажом?"}).status_code == 302
    assert post(admin, "/admin/editor/publish").status_code == 302
    assert "Вы работаете под ключ и с монтажом?" in faq_html(app.test_client().get("/").get_data(as_text=True))
