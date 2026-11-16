"""«Реализовано»: в карточке главное — выставка, компания, площадь и планировка — второстепенные."""

import re
from pathlib import Path

from app import create_app

ROOT = Path(__file__).resolve().parent.parent
STYLE = ROOT / "app" / "static" / "css" / "style.css"


def css_rule(css, selector):
    match = re.search(rf"^{re.escape(selector)} \{{(.*?)^\}}", css, re.S | re.M)
    return match.group(1) if match else ""


def card_bodies(content_dir):
    app = create_app({"TESTING": True, "CONTENT_DIR": str(content_dir)})
    html = app.test_client().get("/").get_data(as_text=True)
    block = re.search(r'<section class="section projects[^"]*".*?</section>', html, re.S).group(0)
    return re.findall(r'<span class="project-body">(.*?)<span class="project-more">', block, re.S)


def test_exhibition_comes_first_and_company_joins_the_facts(make_content):
    bodies = card_bodies(make_content())

    assert bodies
    for body in bodies:
        event = re.search(r'^\s*<span class="project-event">([^<]+)</span>', body)
        assert event, body
        facts = re.search(r'<span class="project-facts">(.*?)</span>\s*$', body, re.S).group(1)
        # Первым в строке фактов — компания, выставка там больше не повторяется
        assert re.match(r'\s*<span class="project-title">[^<]+</span>', facts), facts
        assert event.group(1) not in facts


def test_exhibition_is_the_largest_text_and_company_is_secondary():
    css = STYLE.read_text(encoding="utf-8")

    event = css_rule(css, ".project-event")
    assert "font-size: var(--fs-project-event);" in event and "font-weight: var(--fw-display);" in event
    # Длинное название на узком экране переносится по словам и не распирает карточку
    assert "text-wrap: balance;" in event and "overflow-wrap: anywhere;" in event
    title = css_rule(css, ".project-title")
    assert "font-size: var(--fs-sm);" in title and "font-weight: var(--fw-text);" in title
    assert "font-size: var(--fs-sm);" in css_rule(css, ".project-facts")
