"""Ориентир бюджета конфигуратора (app/static/js/stand-price.js).

Модуль — чистые функции без DOM, проверяется в Node без браузера на настоящих параметрах
из content/home.json (блок configurator → pricing). Главное правило: цена за м² не растёт
с площадью; 10 м² — от ~70 000 ₽/м², 100 м² — ~35 000–45 000 ₽/м².
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MODULE = ROOT / "app" / "static" / "js" / "stand-price.js"
HOME = json.loads((ROOT / "content" / "home.json").read_text(encoding="utf-8"))
PRICING = next(s for s in HOME["sections"] if s.get("type") == "configurator")["pricing"]
NBSP = " "

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="нужен Node.js")


def run(expression, pricing=PRICING):
    script = (f"const price = require({json.dumps(str(MODULE))}); const P = {json.dumps(pricing)};"
              f"process.stdout.write(JSON.stringify({expression}));")
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=20, check=True)
    return json.loads(out.stdout)


def est(area, sides=1, level="standard", extras=()):
    return run(f"price.estimate({{area: {area}, openSides: {sides}, level: {json.dumps(level)}, "
               f"extras: {json.dumps(list(extras))}}}, P)")


def test_ten_square_metres_start_at_seventy_thousand():
    r = est(10)
    assert r["rateMin"] == 70000
    assert r["totalMin"] == 700000
    assert r["rateMax"] > r["rateMin"] and r["totalMax"] > r["totalMin"]


def test_hundred_square_metres_cost_35_to_45_thousand_per_metre():
    r = est(100)
    assert 35000 <= r["rateMin"] <= r["rateMax"] <= 45000
    assert r["totalMin"] == 3500000 and r["totalMax"] == 4500000


def test_rate_never_grows_with_area_and_total_grows():
    areas = [a / 4 for a in range(4, 1201)]          # 1 … 300 м² с шагом 0,25
    rates = run(f"{json.dumps(areas)}.map((a) => price.rateAt(a, P))")
    assert all(b <= a for a, b in zip(rates, rates[1:]))
    assert rates[0] > rates[-1]
    totals = run(f"{json.dumps(areas)}.map((a) => price.rateAt(a, P) * a)")
    assert all(b > a for a, b in zip(totals, totals[1:]))


def test_intermediate_areas_are_on_one_smooth_curve():
    table = {a: est(a) for a in (10, 20, 40, 60, 100)}
    rates = [table[a]["rateMin"] for a in (10, 20, 40, 60, 100)]
    assert rates == sorted(rates, reverse=True) and len(set(rates)) == 5
    # Степенная кривая: удвоение площади снижает ставку в одно и то же число раз
    r = run("[10, 20, 40, 80].map((a) => price.rateAt(a, P))")
    ratios = [b / a for a, b in zip(r, r[1:])]
    assert max(ratios) - min(ratios) < 1e-9


def test_fractional_area_uses_exact_value():
    r = est(6.25)
    raw = run("price.rateAt(6.25, P)")
    assert r["area"] == 6.25 and raw > 70000
    assert r["totalMin"] == round(raw * 6.25 / 10000) * 10000


def test_more_open_sides_cost_more():
    totals = [est(36, sides)["totalMin"] for sides in (1, 2, 3, 4)]
    assert totals == sorted(totals) and len(set(totals)) == 4


def test_levels_raise_estimate_and_premium_has_no_upper_bound():
    std, custom, premium = (est(36, level=lv) for lv in ("standard", "custom", "premium"))
    assert std["totalMin"] < custom["totalMin"] < premium["totalMin"]
    assert std["totalMax"] and custom["totalMax"]
    assert premium["totalMax"] is None and premium["rateMax"] is None and premium["openEnded"]


def test_engineering_extras_hide_upper_bound_and_are_named():
    for extra in ("second_level", "suspended"):
        r = est(36, extras=[extra])
        assert r["totalMax"] is None and r["openEnded"]
        assert r["engineering"] == [next(e["name"] for e in PRICING["extras"] if e["id"] == extra)]
        assert r["totalMin"] > est(36)["totalMin"]


def test_led_adds_fixed_sum_and_urgency_adds_percent_once():
    base = est(36)
    led = est(36, extras=["led"])
    fixed = next(e for e in PRICING["extras"] if e["id"] == "led")
    assert led["totalMin"] - base["totalMin"] == pytest.approx(fixed["min"], abs=10000)
    assert led["totalMax"] - base["totalMax"] == pytest.approx(fixed["max"], abs=10000)
    assert not led["engineering"] and not led["openEnded"]

    urgent = next(e for e in PRICING["extras"] if e["id"] == "urgent")
    both = est(36, extras=["led", "urgent"])
    # Срочность — к производству, а не к прокату экрана: экран не наценяется дважды
    raw = run("price.rateAt(36, P) * 36")
    expected = raw * (1 + urgent["min"]) + fixed["min"]
    assert both["totalMin"] == round(expected / 10000) * 10000


def test_duplicate_or_unknown_extras_count_once():
    assert est(36, extras=["urgent", "urgent", "nope"]) == est(36, extras=["urgent"])


@pytest.mark.parametrize("area", ["0", "-5", "NaN", "Infinity", "null", "'36'"])
def test_invalid_area_gives_no_estimate(area):
    assert run(f"price.estimate({{area: {area}, openSides: 1, level: 'standard', extras: []}}, P)") is None


def test_unknown_level_and_sides_fall_back_to_defaults():
    assert est(36, sides=9, level="gold") == est(36, sides=1, level="standard")


def test_totals_are_rounded_and_rate_matches_total():
    for area in (7.5, 18, 36, 99.75, 300):
        r = est(area)
        assert r["totalMin"] % 10000 == 0 and r["totalMax"] % 10000 == 0
        assert r["rateMin"] % 100 == 0
        assert abs(r["rateMin"] * area - r["totalMin"]) <= 100 * area + 10000


def test_rub_format_uses_russian_grouping():
    assert run("price.formatRub(1450000)") == f"1{NBSP}450{NBSP}000{NBSP}₽"
    assert run("price.formatRange(1450000, 1870000)") == f"1{NBSP}450{NBSP}000 – 1{NBSP}870{NBSP}000{NBSP}₽"
    assert run("price.formatRange(1450000, null)") == f"от 1{NBSP}450{NBSP}000{NBSP}₽"


def test_broken_pricing_gives_no_estimate():
    for broken in ({}, {"rate": {}}, {**PRICING, "levels": []}):
        assert run("price.estimate({area: 36, openSides: 1, level: 'standard', extras: []}, P)", broken) is None
