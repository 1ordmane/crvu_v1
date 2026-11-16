"""Конфигуратор стенда с ориентиром бюджета, без старых «Опций» и без «Двухэтажного».

Размер (длина, ширина, «−»/«+», площадь, план-сетка), четыре типа (Линейный, Угловой, Полуостров,
Остров), уровень сложности, дополнительные опции калькулятора (pricing.extras) и блок ориентира.
Старые записи options и тип с deck в content/home.json не удаляются — код их не использует.
Расчёт цены — static/js/stand-price.js (tests/test_stand_price.py).
"""

import json
import re
import shutil
import subprocess
from html import unescape
from pathlib import Path

import pytest

from tests.test_app import client, content_dir, make_client  # noqa: F401  (фикстуры)

ROOT = Path(__file__).resolve().parent.parent
MAIN_JS = ROOT / "app" / "static" / "js" / "main.js"
PLAN_JS = ROOT / "app" / "static" / "js" / "stand-plan.js"
CSS = ROOT / "app" / "static" / "css" / "style.css"
TYPES = ["Линейный", "Угловой", "Полуостров", "Остров"]


def configurator(html):
    return html.split('id="configurator"', 1)[1].split("</section>", 1)[0]


def test_only_four_stand_types_one_radio_group(client):  # noqa: F811
    block = configurator(client.get("/").get_data(as_text=True))

    radios = re.findall(r'<input type="radio" name="type" value="([^"]+)"', block)
    assert radios == TYPES
    assert len(re.findall(r'name="type"[^>]*checked', block)) == 1      # выбран один — первый
    assert re.search(r'value="Линейный" data-walls="back left right" checked', block)
    assert "Двухэтажный" not in block and "data-deck" not in block


def test_old_options_are_gone_from_page(client):  # noqa: F811
    html = client.get("/").get_data(as_text=True)
    block = configurator(html)

    assert 'name="options"' not in block
    for word in ("Опции", "Видеоэкран", "Подсветка", "Переговорная", "Склад", "Стойка ресепшн"):
        assert word not in block, word
    texts = json.loads(unescape(re.search(r"data-texts='([^']*)'", block).group(1)))
    assert "options" not in texts and "deck" not in texts


def test_levels_and_extras_come_from_pricing(client):  # noqa: F811
    block = configurator(client.get("/").get_data(as_text=True))

    levels = re.findall(r'<input type="radio" name="level" value="([^"]+)"', block)
    assert levels == ["standard", "custom", "premium"]
    assert re.search(r'name="level" value="standard"[^>]*checked', block)
    assert len(re.findall(r'name="level"[^>]*checked', block)) == 1
    for name in ("Стандарт", "Индивидуальный", "Премиум"):
        assert f">{name}</span>" in block
    extras = re.findall(r'<input type="checkbox" name="extras" value="([^"]+)"', block)
    assert extras == ["second_level", "suspended", "led", "urgent"]
    assert not re.search(r'name="extras"[^>]*checked', block)             # по умолчанию — просто
    for name in ("Второй уровень", "Подвесные конструкции", "LED-экраны", "Срочное производство"):
        assert name in block
    # Дополнительные опции — в свёрнутом блоке, чтобы не перегружать панель
    assert re.search(r'<details class="cfg-more"[^>]*>\s*<summary', block)
    pricing = json.loads(unescape(re.search(r"data-pricing='([^']*)'", block).group(1)))
    assert pricing["rate"]["small"] == {"area": 10, "rate_min": 70000}


def test_estimate_block_button_and_honest_note(client):  # noqa: F811
    block = configurator(client.get("/").get_data(as_text=True))

    result = block[block.index("data-estimate"):block.index('class="cfg-submit"')]
    assert "hidden" in re.search(r"<div[^>]*data-estimate[^>]*>", block).group(0)   # без JS расчёта нет
    assert "Ориентировочный бюджет" in result
    for part in ("data-est-area", "data-est-config", "data-est-rate", "data-est-total", "data-est-warning"):
        assert part in result
    assert 'aria-live="polite"' in result
    assert "не коммерческое предложение" in result
    for word in ("аренда площади", "сборы организатора", "подключение", "подвес"):
        assert word in result.lower(), word
    assert re.search(r'<button class="btn btn-primary btn-lg" type="submit"[^>]*>Обсудить проект</button>', block)


def test_without_pricing_there_is_no_estimate(content_dir):  # noqa: F811
    path = content_dir / "home.json"
    home = json.loads(path.read_text(encoding="utf-8"))
    next(s for s in home["sections"] if s.get("type") == "configurator").pop("pricing")
    path.write_text(json.dumps(home, ensure_ascii=False), encoding="utf-8")

    block = configurator(make_client(content_dir).get("/").get_data(as_text=True))
    assert "data-estimate" not in block and 'name="level"' not in block and "data-pricing" not in block
    assert re.findall(r'name="type" value="([^"]+)"', block) == TYPES


def test_plan_has_no_second_floor_layer(client):  # noqa: F811
    block = configurator(client.get("/").get_data(as_text=True))

    assert "data-plan-deck" not in block and "plan-deck" not in block
    for part in ("data-plan-floor", "data-plan-grid", "data-plan-walls", "data-plan-length", "data-plan-width"):
        assert part in block


def test_size_stays_and_own_budget_field_is_replaced_by_estimate(client):  # noqa: F811
    block = configurator(client.get("/").get_data(as_text=True))

    for side in ("length", "width"):
        assert re.search(rf'id="cfg-{side}" name="{side}"[^>]*required', block)
        assert block.count(f'data-side="{side}"') == 2                   # «−» и «+»
    assert ">36 м²</output>" in block
    assert 'name="budget"' not in block and "data-cfg-summary" not in block


def test_content_json_keeps_old_entries_untouched(content_dir):  # noqa: F811
    # Тип с deck и options остаются в JSON, но на сайт не попадают
    home = json.loads((content_dir / "home.json").read_text(encoding="utf-8"))
    cfg = next(s for s in home["sections"] if s.get("type") == "configurator")
    assert any(t.get("deck") for t in cfg["types"]) and cfg["options"]

    block = configurator(make_client(content_dir).get("/").get_data(as_text=True))
    assert "Двухэтажный" not in block and "Видеоэкран" not in block


def test_new_deck_type_in_content_is_also_hidden(content_dir):  # noqa: F811
    path = content_dir / "home.json"
    home = json.loads(path.read_text(encoding="utf-8"))
    cfg = next(s for s in home["sections"] if s.get("type") == "configurator")
    cfg["types"].insert(0, {"name": "Мезонин", "walls": [], "deck": True})
    path.write_text(json.dumps(home, ensure_ascii=False), encoding="utf-8")

    block = configurator(make_client(content_dir).get("/").get_data(as_text=True))
    assert re.findall(r'name="type" value="([^"]+)"', block) == TYPES
    assert re.search(r'value="Линейный"[^>]*checked', block)


def test_scripts_and_styles_have_no_options_or_deck():
    main = MAIN_JS.read_text(encoding="utf-8")
    start = main.index("function initConfigurator()")
    cfg_js = main[start:main.index("\n  }\n", start)]
    for word in ("options", "deck"):
        assert word not in cfg_js, word
    assert "deckRect" not in PLAN_JS.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")
    assert ".plan-deck" not in css and 'type="checkbox"]:checked + span' not in css


needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="нужен Node.js")


@needs_node
def test_plan_api_without_deck():
    code = (f"const p = require({json.dumps(str(PLAN_JS))});"
            "process.stdout.write(JSON.stringify([Object.keys(p).sort(), p.areaOf({length: 6, width: 4.5}),"
            " p.step({length: 6, width: 6}, 'length', 1, p.limitsOf({min: 1, max: 300}))]));")
    keys, area, stepped = json.loads(subprocess.run(["node", "-e", code], capture_output=True, text=True,
                                                    timeout=20, check=True).stdout)
    assert "deckRect" not in keys and {"layout", "gridPath", "wallPath", "step", "resize", "areaOf"} <= set(keys)
    assert area == 27 and stepped == {"length": 6.5, "width": 6}
