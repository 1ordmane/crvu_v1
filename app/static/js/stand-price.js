"use strict";

/* Ориентир бюджета для конфигуратора — единственное место, где считается цена.
   Только чистые функции без DOM: их использует main.js и проверяет tests/test_stand_price.py в Node.
   Числа — не здесь, а в content/home.json → блок configurator → pricing (описание — content/README.md).

   Формула (S — площадь, м²):
     ставка «от» за м²   rate(S) = small.rate_min × (S / small.area) ^ (−k),
                         k = ln(small.rate_min / large.rate_min) / ln(large.area / small.area)
                         — степенная кривая через две точки калибровки: плавно убывает с площадью,
                         а сумма rate(S) × S при этом растёт (k < 1);
     ставка «до»         rate(S) × large.rate_max / large.rate_min — та же ширина диапазона на любой площади;
     база                ставка × S × open_sides[число открытых сторон] × уровень.factor;
     производство        база × (1 + Σ надбавок kind "percent": второй уровень, подвесные конструкции);
     срочность           производство × (1 + kind "urgent") — только производство, не экраны;
     итог                + kind "fixed" (LED-экраны: сумма за позицию), округление до pricing.round ₽.
   Верхней границы нет («от …»), если у уровня open_ended или выбрана опция с engineering:
   такие работы требуют инженерного расчёта, точный «потолок» здесь был бы выдумкой. */

(function (root) {
  const RATE_STEP = 100;

  function positive(n) {
    return typeof n === "number" && isFinite(n) && n > 0;
  }

  // Ставка «от» за м² на площади area; null — неверные параметры
  function rateAt(area, pricing) {
    const rate = (pricing || {}).rate || {};
    const small = rate.small || {};
    const large = rate.large || {};
    if (!positive(area) || !positive(small.area) || !positive(small.rate_min) ||
        !positive(large.area) || !positive(large.rate_min) || large.area <= small.area ||
        large.rate_min > small.rate_min) return null;
    const k = Math.log(small.rate_min / large.rate_min) / Math.log(large.area / small.area);
    return small.rate_min * Math.pow(area / small.area, -k);
  }

  function roundTo(n, step) {
    return Math.round(n / step) * step;
  }

  // Ориентир по выбору в конфигураторе: input = {area, openSides, level (id), extras: [id…]}.
  // Неизвестный уровень — первый из списка, неизвестное число сторон — 1, неизвестные
  // и повторные опции не считаются. null — площадь или параметры цены неверны.
  function estimate(input, pricing) {
    const p = pricing || {};
    const s = input || {};
    const rate = rateAt(s.area, p);
    const levels = Array.isArray(p.levels) ? p.levels.filter((l) => l && positive(l.factor)) : [];
    const large = (p.rate || {}).large || {};
    if (rate === null || !levels.length || !(large.rate_max >= large.rate_min)) return null;

    const level = levels.find((l) => l.id === s.level) || levels[0];
    const sides = p.open_sides || {};
    const sidesFactor = positive(sides[String(s.openSides)]) ? sides[String(s.openSides)]
      : positive(sides["1"]) ? sides["1"] : 1;
    const wanted = Array.isArray(s.extras) ? s.extras : [];
    const extras = (Array.isArray(p.extras) ? p.extras : [])
      .filter((e) => e && wanted.includes(e.id) && typeof e.min === "number" && e.min >= 0 && e.max >= e.min);

    const spread = large.rate_max / large.rate_min;
    const base = rate * s.area * sidesFactor * level.factor;
    let low = base;
    let high = base * spread;
    let percentLow = 0;
    let percentHigh = 0;
    extras.filter((e) => e.kind === "percent").forEach((e) => { percentLow += e.min; percentHigh += e.max; });
    low *= 1 + percentLow;
    high *= 1 + percentHigh;
    extras.filter((e) => e.kind === "urgent").forEach((e) => { low *= 1 + e.min; high *= 1 + e.max; });
    extras.filter((e) => e.kind === "fixed").forEach((e) => { low += e.min; high += e.max; });

    const step = positive(p.round) ? p.round : 10000;
    const engineering = extras.filter((e) => e.engineering).map((e) => e.name);
    const openEnded = Boolean(level.open_ended) || engineering.length > 0;
    const totalMin = roundTo(low, step);
    const totalMax = openEnded ? null : Math.max(totalMin, roundTo(high, step));
    return {
      area: s.area,
      level: level.name,
      extras: extras.map((e) => e.name),
      rateMin: roundTo(low / s.area, RATE_STEP),
      rateMax: openEnded ? null : roundTo(high / s.area, RATE_STEP),
      totalMin,
      totalMax,
      openEnded,
      engineering,
    };
  }

  // 1450000 → «1 450 000 ₽» с неразрывными пробелами, как принято в русском тексте
  function formatRub(n) {
    return String(Math.round(n)).replace(/\B(?=(\d{3})+(?!\d))/g, " ") + " ₽";
  }

  // Диапазон «1 450 000 – 1 870 000 ₽»; без верхней границы — «от 1 450 000 ₽»
  function formatRange(min, max) {
    if (max === null || max === undefined) return "от " + formatRub(min);
    return formatRub(min).replace(" ₽", "") + " – " + formatRub(max);
  }

  const api = { rateAt, estimate, formatRub, formatRange };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.CRVUStandPrice = api;
})(typeof window === "undefined" ? globalThis : window);
