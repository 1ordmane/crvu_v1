"use strict";

/* Фильтры портфолио: планировка, площадь, этажность.

   Своих правил здесь нет: сервер (app/portfolio.py) уже записал у каждой карточки data-layout,
   data-area (ключ диапазона) и data-floors; пустое значение — неизвестно. Скрипт сравнивает их
   с выбранным, прячет лишнее, пересчитывает счётчики и пишет выбор в адрес (?layout=…&area=…&floors=…).
   Порядок карточек не меняется. Чистые функции проверяются в Node (tests/test_portfolio_filters.py). */

(function (root) {
  const KEYS = ["layout", "area", "floors"];

  function empty() {
    return { layout: "", area: "", floors: "" };
  }

  // Выбор из адреса: только значения, которые есть среди кнопок (allowed), остальное отбрасывается
  function readState(search, allowed) {
    const params = new URLSearchParams(search || "");
    const state = empty();
    KEYS.forEach((key) => {
      const value = params.get(key) || "";
      if ((allowed[key] || []).includes(value)) state[key] = value;
    });
    return state;
  }

  function toSearch(state) {
    const params = new URLSearchParams();
    KEYS.forEach((key) => {
      if (state[key]) params.set(key, state[key]);
    });
    const query = params.toString();
    return query ? "?" + query : "";
  }

  // Нажатие кнопки: остальные фильтры не меняются. Планировку и площадь повторное нажатие снимает,
  // у этажности «Любая» — своя кнопка
  function toggle(state, key, value) {
    const next = Object.assign(empty(), state);
    next[key] = key === "floors" || state[key] !== value ? value : "";
    return next;
  }

  function isActive(state) {
    return KEYS.some((key) => Boolean(state && state[key]));
  }

  // Сколько фильтров выбрано — для значка на кнопке «Фильтры»
  function activeCount(state) {
    return KEYS.filter((key) => Boolean(state && state[key])).length;
  }

  // Карточка подходит, если совпадает каждый выбранный фильтр; неизвестное ("") не совпадает ни с чем
  function matches(card, state) {
    return KEYS.every((key) => !state[key] || card[key] === state[key]);
  }

  function init(doc, win) {
    const form = doc.querySelector("[data-pf-filters]");
    if (!form) return;
    const buttons = Array.from(form.querySelectorAll("[data-pf-key]"));
    const cards = Array.from(doc.querySelectorAll("[data-stand]"));
    const summary = form.querySelector("[data-pf-summary]");
    const bar = doc.querySelector("[data-pf-bar]");
    const toggleButton = doc.querySelector("[data-pf-toggle]");
    const panel = doc.querySelector("[data-pf-panel]");
    const badge = doc.querySelector("[data-pf-badge]");
    const footerReset = form.querySelector("[data-pf-reset]");
    let texts = {};
    try {
      texts = JSON.parse(form.dataset.pfTexts || "{}");
    } catch (err) {
      // Повреждённые подписи — текст по умолчанию ниже
    }
    const allowed = {};
    buttons.forEach((button) => {
      (allowed[button.dataset.pfKey] = allowed[button.dataset.pfKey] || []).push(button.dataset.pfValue);
    });
    let state = readState(win.location.search, allowed);

    function render() {
      const active = isActive(state);
      buttons.forEach((button) => {
        button.setAttribute("aria-pressed", String(state[button.dataset.pfKey] === button.dataset.pfValue));
      });
      cards.forEach((card) => {
        card.hidden = !matches({ layout: card.dataset.layout, area: card.dataset.area, floors: card.dataset.floors }, state);
      });
      let found = 0;
      doc.querySelectorAll(".year-panel").forEach((panel) => {
        let inYear = 0;
        panel.querySelectorAll(".exhibition").forEach((section) => {
          const shown = section.querySelectorAll("[data-stand]:not([hidden])").length;
          section.hidden = shown === 0;
          const count = section.querySelector(".exhibition-count");
          if (count) count.textContent = String(shown);
          inYear += shown;
        });
        found += inYear;
        const tabCount = doc.querySelector("#" + panel.getAttribute("aria-labelledby") + " .year-count");
        if (tabCount) tabCount.textContent = String(inYear);
        const message = panel.querySelector("[data-pf-empty]");
        if (message) message.hidden = !(active && inYear === 0);
        // «За год проекты скоро появятся» — только без фильтров
        panel.querySelectorAll(":scope > .empty").forEach((el) => { el.hidden = active; });
      });
      if (summary) {
        summary.textContent = active
          ? String(texts.found || "Найдено стендов: {n}").replace("{n}", String(found))
          : "";
      }
      if (footerReset) footerReset.hidden = !active;
      if (badge) {
        badge.textContent = String(activeCount(state));
        badge.hidden = !active;
      }
    }

    // Панель: открытие — убрать hidden, на следующем кадре класс is-open запускает переход высоты
    // и прозрачности; закрытие — снять is-open и после перехода вернуть hidden (места не занимает).
    // Во время закрытия панель inert: по ней уже нельзя кликнуть или перейти Tab.
    const reduceMotion = win.matchMedia && win.matchMedia("(prefers-reduced-motion: reduce)");
    let closeTimer = null;

    function setOpen(open) {
      if (!panel || !toggleButton) return;
      toggleButton.setAttribute("aria-expanded", String(open));
      win.clearTimeout(closeTimer);
      if (open) {
        panel.inert = false;
        panel.hidden = false;
        void panel.offsetHeight;                 // зафиксировать свёрнутое состояние перед переходом
        panel.classList.add("is-open");
        return;
      }
      panel.classList.remove("is-open");
      panel.inert = true;
      const finish = () => {
        if (toggleButton.getAttribute("aria-expanded") === "true") return;
        panel.hidden = true;
        panel.inert = false;
      };
      if (reduceMotion && reduceMotion.matches) finish();
      else closeTimer = win.setTimeout(finish, 320);  // длительность перехода в style.css (.pf-collapse)
    }

    function apply(next) {
      state = next;
      render();
      // Выбор — в адресе: ссылкой можно поделиться, после обновления страницы он сохранится
      const url = win.location.pathname + toSearch(state) + win.location.hash;
      win.history.replaceState(win.history.state, "", url);
    }

    form.addEventListener("click", (event) => {
      const button = event.target.closest("[data-pf-key]");
      if (button) apply(toggle(state, button.dataset.pfKey, button.dataset.pfValue));
    });
    doc.addEventListener("click", (event) => {
      if (event.target.closest("[data-pf-reset]")) apply(empty());
    });
    form.addEventListener("submit", (event) => event.preventDefault());
    if (toggleButton) {
      toggleButton.addEventListener("click", () => {
        setOpen(toggleButton.getAttribute("aria-expanded") !== "true");
      });
    }

    if (bar) bar.hidden = false;
    render();
  }

  const api = { readState, toSearch, toggle, isActive, activeCount, matches };
  if (typeof module === "object" && module.exports) module.exports = api;
  else if (root.document) {
    if (root.document.readyState === "loading") {
      root.document.addEventListener("DOMContentLoaded", () => init(root.document, root));
    } else {
      init(root.document, root);
    }
  }
})(typeof window === "undefined" ? globalThis : window);
