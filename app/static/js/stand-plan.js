"use strict";

/* Расчёт плана стенда для конфигуратора: одна клетка = 1 × 1 м.
   Только чистые функции без DOM — их использует main.js и проверяет
   tests/test_stand_plan.py в Node. Размеры — от 1 м с точностью до сотых,
   кнопки меняют сторону на 0,5 м; сетка всегда идёт через 1 м. */

(function (root) {
  const DEFAULT_LIMITS = { areaMin: 9, areaMax: 300 };

  // Пределы площади из content/home.json (area.min / area.max)
  function limitsOf(area) {
    const a = area || {};
    const areaMin = Number(a.min) > 0 ? Number(a.min) : DEFAULT_LIMITS.areaMin;
    const areaMax = Number(a.max) >= areaMin ? Number(a.max) : Math.max(areaMin, DEFAULT_LIMITS.areaMax);
    return { areaMin, areaMax };
  }

  const MIN_SIDE = 1;
  const STEP = 0.5;
  // Погрешность двоичной дроби: 2,3 × 100 = 229,99999…
  const EPS = 1e-9;

  function round2(n) {
    return Math.round(n * 100) / 100;
  }

  // Значение поля «Длина»/«Ширина» и причина отказа: empty — пусто, format — не число
  // или больше двух знаков после запятой, min — меньше 1 м (в том числе 0 и минус).
  function checkSide(raw) {
    if (typeof raw === "number") raw = String(raw);
    if (typeof raw !== "string" || !raw.trim()) return { value: null, error: "empty" };
    const text = raw.trim();
    if (!/^-?\d+([.,]\d{1,2})?$/.test(text)) return { value: null, error: "format" };
    const n = Number(text.replace(",", "."));
    if (n < MIN_SIDE) return { value: null, error: "min" };
    return { value: n, error: null };
  }

  // Допустимое значение стороны или null; принимает и «2,5», и «2.5»
  function parseSide(raw) {
    return checkSide(raw).value;
  }

  // Допустимая сторона при известной другой: площадь остаётся в пределах areaMin…areaMax,
  // границы — до сотых метра внутрь диапазона
  function sideRange(other, limits) {
    const max = Math.max(MIN_SIDE, Math.floor(limits.areaMax / other * 100 + EPS) / 100);
    const min = Math.min(max, Math.max(MIN_SIDE, Math.ceil(limits.areaMin / other * 100 - EPS) / 100));
    return { min, max };
  }

  function areaOf(size) {
    return round2(size.length * size.width);
  }

  // 2.5 → «2,5», 6 → «6»: для поля, подписей плана, итога и заявки
  function formatSize(n) {
    return String(round2(n)).replace(".", ",");
  }

  function clamp(n, range) {
    return Math.min(range.max, Math.max(range.min, n));
  }

  // Новый размер после ввода в одно поле. Пустое или неверное значение не меняет размер,
  // число вне пределов прижимается к ближайшему допустимому.
  function resize(size, side, raw, limits) {
    const other = side === "length" ? size.width : size.length;
    const n = parseSide(raw);
    const next = { length: size.length, width: size.width };
    if (n !== null) next[side] = clamp(n, sideRange(other, limits));
    return next;
  }

  // Кнопки «−»/«+»: шаг 0,5 м; значение не на сетке шага (6,25) встаёт на ближайшее кратное
  // в сторону нажатия (6 или 6,5). У пределов размер прижимается к ним.
  function step(size, side, direction, limits) {
    const other = side === "length" ? size.width : size.length;
    const halves = size[side] / STEP;
    const target = direction < 0
      ? (Math.ceil(halves - EPS) - 1) * STEP
      : (Math.floor(halves + EPS) + 1) * STEP;
    const next = { length: size.length, width: size.width };
    next[side] = clamp(target, sideRange(other, limits));
    return next;
  }

  // Число в пределах, но без прижатия: для промежуточного ввода («1» на пути к «12»)
  function accepts(size, side, raw, limits) {
    const n = parseSide(raw);
    if (n === null) return false;
    const range = sideRange(side === "length" ? size.width : size.length, limits);
    return n >= range.min && n <= range.max;
  }

  // Стартовый размер — квадрат по площади по умолчанию
  function initialSize(area) {
    const limits = limitsOf(area);
    const wanted = Number((area || {}).default) || limits.areaMin;
    const side = Math.max(1, Math.round(Math.sqrt(wanted)));
    const length = clamp(side, { min: 1, max: limits.areaMax });
    return { length, width: clamp(side, sideRange(length, limits)) };
  }

  // Геометрия в координатах SVG: одна величина клетки на обе оси, поэтому клетки
  // всегда квадратные, а пропорции стенда — настоящие. Большой стенд уменьшает все клетки.
  function layout(length, width, box) {
    const cell = Math.min((box.width - box.pad * 2) / length, (box.height - box.pad * 2) / width);
    const w = length * cell;
    const h = width * cell;
    return {
      columns: length,
      rows: width,
      area: length * width,
      cell,
      x: (box.width - w) / 2,
      y: (box.height - h) / 2,
      w,
      h,
    };
  }

  function r(n) {
    return String(Math.round(n * 100) / 100);
  }

  // Внутренние линии сетки: по одной на каждый целый метр; у дробной стороны последняя клетка неполная
  function gridPath(l) {
    const d = [];
    for (let i = 1; i < l.columns; i += 1) d.push("M" + r(l.x + i * l.cell) + " " + r(l.y) + "V" + r(l.y + l.h));
    for (let j = 1; j < l.rows; j += 1) d.push("M" + r(l.x) + " " + r(l.y + j * l.cell) + "H" + r(l.x + l.w));
    return d.join("");
  }

  // Закрытые стороны по краям пола: back — верхний край, left/right — боковые
  function wallPath(l, walls) {
    const right = l.x + l.w;
    const bottom = l.y + l.h;
    const d = [];
    if (walls.includes("back")) d.push("M" + r(l.x) + " " + r(l.y) + "H" + r(right));
    if (walls.includes("left")) d.push("M" + r(l.x) + " " + r(l.y) + "V" + r(bottom));
    if (walls.includes("right")) d.push("M" + r(right) + " " + r(l.y) + "V" + r(bottom));
    return d.join("");
  }

  // Подстановка {метка} в текст из контента; неизвестные метки остаются как есть
  function fill(template, values) {
    return String(template).replace(/\{(\w+)\}/g, (match, key) => (key in values ? String(values[key]) : match));
  }

  const api = {
    limitsOf, checkSide, parseSide, sideRange, areaOf, formatSize, resize, step, accepts, initialSize,
    layout, gridPath, wallPath, fill,
  };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.CRVUStandPlan = api;
})(typeof window === "undefined" ? globalThis : window);
