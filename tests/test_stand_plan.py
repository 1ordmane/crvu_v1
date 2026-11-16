"""Расчёт плана конфигуратора (app/static/js/stand-plan.js): 1 клетка = 1 × 1 м.

Модуль — чистые функции без DOM, поэтому проверяется в Node без браузера.
Размеры — от 1 м, до сотых метра; кнопки меняют сторону на 0,5 м.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parent.parent / "app" / "static" / "js" / "stand-plan.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="нужен Node.js")


def run(expression):
    """Вычислить выражение с объектом plan = модуль и вернуть результат как JSON."""
    script = f"const plan = require({json.dumps(str(MODULE))}); process.stdout.write(JSON.stringify({expression}));"
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=20, check=True)
    return json.loads(out.stdout)


BOX = "{width: 320, height: 240, pad: 30}"
LIMITS = "{areaMin: 9, areaMax: 300}"


@pytest.mark.parametrize("length, width", [
    (6, 4), (8, 5), (3, 3), (12, 2), (1, 300), (100, 3), (1, 1), (1, 2.5), (2.5, 2.5), (7.25, 3.5),
])
def test_layout_has_one_square_cell_per_metre(length, width):
    p = run(f"plan.layout({length}, {width}, {BOX})")

    assert p["area"] == pytest.approx(length * width)
    assert p["columns"] == length and p["rows"] == width
    # Клетки квадратные: одна величина на обе оси, и пол ровно length × width клеток
    assert p["w"] == pytest.approx(length * p["cell"])
    assert p["h"] == pytest.approx(width * p["cell"])
    # План помещается в поле и стоит по центру
    assert 30 - 1e-9 <= p["x"] and p["x"] + p["w"] <= 290 + 1e-9
    assert 30 - 1e-9 <= p["y"] and p["y"] + p["h"] <= 210 + 1e-9
    assert p["x"] * 2 + p["w"] == pytest.approx(320)
    assert p["y"] * 2 + p["h"] == pytest.approx(240)


def test_grid_has_lines_between_every_metre():
    p = run(f"(() => {{ const l = plan.layout(6, 4, {BOX}); return {{l, d: plan.gridPath(l)}}; }})()")
    d, l = p["d"], p["l"]

    # Внутренние линии: 5 вертикальных и 3 горизонтальные → 6 × 4 клеток
    assert d.count("V") == 5 and d.count("H") == 3
    xs = sorted(float(part.split("M")[1].split(" ")[0]) for part in d.split("V")[:-1])
    steps = [b - a for a, b in zip([l["x"]] + xs, xs + [l["x"] + l["w"]])]
    # Координаты в пути округлены до сотых
    assert steps == pytest.approx([l["cell"]] * 6, abs=0.02)


@pytest.mark.parametrize("length, width, vertical, horizontal", [(1, 1, 0, 0), (1, 2.5, 0, 2), (2.5, 2.5, 2, 2)])
def test_grid_keeps_one_metre_step_for_fractional_sizes(length, width, vertical, horizontal):
    p = run(f"(() => {{ const l = plan.layout({length}, {width}, {BOX}); return {{l, d: plan.gridPath(l)}}; }})()")
    d, l = p["d"], p["l"]

    # Линии только на целых метрах: у 2,5 м — на 1 и 2 м, последняя клетка неполная (0,5 м)
    assert d.count("V") == vertical and d.count("H") == horizontal
    xs = [float(part.split(" ")[0]) for part in d.split("M")[1:] if "V" in part]
    assert xs == pytest.approx([l["x"] + i * l["cell"] for i in range(1, vertical + 1)], abs=0.01)
    assert l["w"] == pytest.approx(length * l["cell"]) and l["h"] == pytest.approx(width * l["cell"])
    assert "NaN" not in d and "Infinity" not in d


def test_bigger_stand_gets_more_cells_and_smaller_cells():
    small = run(f"plan.layout(6, 4, {BOX})")
    longer = run(f"plan.layout(12, 4, {BOX})")
    wider = run(f"plan.layout(6, 6, {BOX})")

    assert longer["columns"] == 12 and longer["rows"] == 4
    assert wider["columns"] == 6 and wider["rows"] == 6
    assert longer["cell"] < small["cell"]
    assert longer["w"] / longer["h"] == pytest.approx(3)


@pytest.mark.parametrize("raw, expected", [
    ("6", 6), (" 12 ", 12), (7, 7), ("1", 1),
    ("4.5", 4.5), ("4,5", 4.5), ("2,25", 2.25), ("2,50", 2.5), (2.5, 2.5),
    ("", None), ("  ", None), ("0", None), ("0,5", None), ("-3", None), ("-1", None), ("abc", None),
    ("2,555", None), ("1,", None), ("1e3", None), (None, None), ("NaN", None),
])
def test_parse_side_accepts_metres_from_one_with_two_decimals(raw, expected):
    assert run(f"plan.parseSide({json.dumps(raw)})") == expected


@pytest.mark.parametrize("raw, error", [
    ("0,5", "min"), ("0.5", "min"), ("0", "min"), ("-2", "min"), ("-0,5", "min"),
    ("abc", "format"), ("2,555", "format"), ("1e3", "format"),
    ("", "empty"), ("  ", "empty"),
    ("1", None), ("2,5", None),
])
def test_check_side_tells_why_value_is_rejected(raw, error):
    # «min» — повод показать «Минимальный размер — 1 м», а не исправлять значение молча
    assert run(f"plan.checkSide({json.dumps(raw)}).error") == error


def test_side_range_keeps_area_within_project_limits():
    # Правило проекта — area.min…area.max в content/home.json; сторона подстраивается под другую
    # Стороны — с точностью до сотых: 9 м² / 4 м = 2,25 м
    assert run(f"plan.sideRange(4, {LIMITS})") == {"min": 2.25, "max": 75}
    assert run(f"plan.sideRange(6, {LIMITS})") == {"min": 1.5, "max": 50}
    assert run(f"plan.sideRange(300, {LIMITS})") == {"min": 1, "max": 1}
    assert run(f"plan.sideRange(7, {LIMITS})") == {"min": 1.29, "max": 42.85}


def test_side_is_never_below_one_metre():
    # area.min = 1 м²: даже при длинной второй стороне минимум — 1 м
    one = "{areaMin: 1, areaMax: 300}"
    assert run(f"plan.sideRange(1, {one})") == {"min": 1, "max": 300}
    assert run(f"plan.sideRange(2.5, {one})") == {"min": 1, "max": 120}
    assert run(f"plan.sideRange(300, {one})") == {"min": 1, "max": 1}


@pytest.mark.parametrize("value, expected", [
    ("8", 8), ("500", 50), ("1", 1.5), ("", 6), ("-2", 6), ("x", 6), ("2,5", 2.5), ("0,5", 6), ("0", 6),
])
def test_resize_clamps_or_keeps_last_valid_value(value, expected):
    size = run(f"plan.resize({{length: 6, width: 6}}, 'length', {json.dumps(value)}, {LIMITS})")

    assert size == {"length": expected, "width": 6}


@pytest.mark.parametrize("length, width, area", [(1, 1, 1), (1, 2.5, 2.5), (2.5, 2.5, 6.25), (6, 6, 36), (0.1 + 0.2, 10, 3)])
def test_area_is_rounded_to_hundredths(length, width, area):
    assert run(f"plan.areaOf({{length: {length}, width: {width}}})") == area


STEP_LIMITS = "{areaMin: 1, areaMax: 300}"


@pytest.mark.parametrize("start, direction, expected", [
    (1, 1, 1.5), (1, -1, 1), (1.5, -1, 1), (2, 1, 2.5), (2.5, 1, 3),
    # Значение не на сетке 0,5 м сначала встаёт на ближайшее кратное в сторону шага
    (6.25, 1, 6.5), (6.25, -1, 6), (1.2, -1, 1),
])
def test_step_changes_side_by_half_metre(start, direction, expected):
    size = run(f"plan.step({{length: {start}, width: 2}}, 'length', {direction}, {STEP_LIMITS})")

    assert size == {"length": expected, "width": 2}


def test_step_stops_at_maximum_area():
    # 2,5 × 120 = 300 м² — больше нельзя
    assert run(f"plan.step({{length: 2.5, width: 120}}, 'width', 1, {STEP_LIMITS})") == {"length": 2.5, "width": 120}
    assert run(f"plan.step({{length: 2.5, width: 119.75}}, 'width', 1, {STEP_LIMITS})") == {"length": 2.5, "width": 120}


@pytest.mark.parametrize("n, text", [(1, "1"), (2.5, "2,5"), (6.25, "6,25"), (120, "120"), (0.1 + 0.2, "0,3")])
def test_format_size_uses_comma_and_drops_trailing_zeros(n, text):
    assert run(f"plan.formatSize({n})") == text


def test_initial_size_comes_from_default_area():
    assert run(f"plan.initialSize({{min: 9, max: 300, default: 36}})") == {"length": 6, "width": 6}
    assert run(f"plan.initialSize({{min: 9, max: 300, default: 40}})") == {"length": 6, "width": 6}
    assert run(f"plan.initialSize({{}})") == {"length": 3, "width": 3}


def test_walls_follow_floor_edges():
    p = run(f"(() => {{ const l = plan.layout(8, 5, {BOX}); return {{l, d: plan.wallPath(l, ['back', 'left', 'right'])}}; }})()")
    l, d = p["l"], p["d"]
    r = lambda v: ("%.2f" % v).rstrip("0").rstrip(".")

    assert d == (f"M{r(l['x'])} {r(l['y'])}H{r(l['x'] + l['w'])}"
                 f"M{r(l['x'])} {r(l['y'])}V{r(l['y'] + l['h'])}"
                 f"M{r(l['x'] + l['w'])} {r(l['y'])}V{r(l['y'] + l['h'])}")
    assert run(f"plan.wallPath(plan.layout(8, 5, {BOX}), [])") == ""


def test_fill_replaces_placeholders():
    assert run("plan.fill('{type} стенд, {length} × {width} м', {type: 'Угловой', length: 6, width: 4})") \
        == "Угловой стенд, 6 × 4 м"
    assert run("plan.fill('Без меток', {a: 1})") == "Без меток"
