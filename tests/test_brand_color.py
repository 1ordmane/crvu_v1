"""Фирменный оранжевый ЦРВУ — #F15A2B (как на логотипе): общий токен сайта и админки.

Кнопки и мелкий текст — тот же тон темнее, ради контраста. Ошибки остаются красными (свои токены),
логотип — в прежних цветах (свой токен), чтобы смена акцента его не перекрашивала.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / "app/static/css/style.css").read_text(encoding="utf-8")
ADMIN = (ROOT / "app/static/css/admin.css").read_text(encoding="utf-8")
LOGO = (ROOT / "app/templates/partials/logo.html").read_text(encoding="utf-8")
OLD_ACCENTS = ("#F04624", "#D93A1B", "#C2321A", "#C9361A", "#FF7552", "#FF0000", "240 70 36", "255 0 0")


def luminance(hex_color):
    channels = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a, b):
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def token(css, name):
    return re.search(rf"--{name}: (#[0-9A-F]{{6}})", css).group(1)


def test_brand_token_is_the_logo_orange_and_shades_keep_contrast():
    assert token(CSS, "brand") == "#F15A2B"
    for css in (CSS, ADMIN):
        assert contrast(token(css, "brand-strong"), "#FFFFFF") >= 4.5          # белый текст кнопок
        assert contrast(token(css, "brand-strong-hover"), "#FFFFFF") >= 4.5
        assert contrast(token(css, "brand-text"), "#F6F4F1") >= 4.5            # ссылки на «бумаге»
    assert [token(ADMIN, n) for n in ("brand-strong", "brand-strong-hover", "brand-text")] == \
        [token(CSS, n) for n in ("brand-strong", "brand-strong-hover", "brand-text")]
    assert "--filter-accent: var(--brand);" in CSS and "rgb(241 90 43 / 0.14)" in CSS


def test_old_red_accents_remain_only_for_errors_and_the_logo():
    allowed = {"--logo-accent: #F04624;", "--error: #F04624;", "--error-text: #C9361A;"}
    for css in (CSS, ADMIN):
        for line in css.splitlines():
            if any(old in line for old in OLD_ACCENTS):
                assert line.strip().split("  /*")[0].strip() in allowed or line.strip().startswith(("/*", "*")), line


def test_errors_stay_red_and_logo_keeps_its_colors():
    for rule in (".field-error {", ".field input[aria-invalid=\"true\"] {", ".form-status {", ".cfg-hint:not(:empty) {"):
        body = CSS[CSS.index("\n" + rule):].split("}", 1)[0]
        assert "var(--error" in body and "var(--brand" not in body, rule
    assert 'fill="var(--brand)"' not in LOGO and LOGO.count('fill="var(--logo-accent, #F04624)"') == 8
    # Ошибки админки — свои токены, как прежде
    assert "--error-ink: #9A2A14;" in ADMIN
