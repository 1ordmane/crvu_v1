/* Предпросмотр в визуальном редакторе: подключается только в рамке редактора
   (routes.render_page с editing=True). Щелчок по элементу с data-edit выбирает его поле
   в панели; ссылки и отправка форм в предпросмотре не срабатывают — страница не уходит. */
(function () {
  "use strict";

  const inFrame = window.parent !== window;
  const SCROLL_KEY = "crvu-editor-scroll";

  function select(field) {
    document.querySelectorAll(".is-edit-selected").forEach((el) => el.classList.remove("is-edit-selected"));
    const items = document.querySelectorAll('[data-edit="' + CSS.escape(field) + '"]');
    items.forEach((el) => el.classList.add("is-edit-selected"));
    return items[0] || null;
  }

  document.addEventListener("click", (event) => {
    const target = event.target.closest("[data-edit]");
    if (target) {
      event.preventDefault();
      event.stopPropagation();
      select(target.dataset.edit);
      if (inFrame) window.parent.postMessage({ type: "crvu-edit", field: target.dataset.edit }, window.location.origin);
      return;
    }
    if (inFrame && event.target.closest("a[href], button[type='submit']")) event.preventDefault();
  }, true);

  if (!inFrame) return;

  document.addEventListener("submit", (event) => event.preventDefault(), true);

  // Поле выбрано в панели — показать его элемент.
  window.addEventListener("message", (event) => {
    if (event.origin !== window.location.origin || event.source !== window.parent) return;
    const data = event.data;
    if (!data || data.type !== "crvu-focus" || typeof data.field !== "string") return;
    const el = select(data.field);
    if (el && el.getClientRects().length) el.scrollIntoView({ block: "center" });
  });

  // После сохранения черновика страница перезагружается — вернуть прежнее место прокрутки.
  try {
    const y = Number(sessionStorage.getItem(SCROLL_KEY));
    if (y > 0) window.addEventListener("load", () => window.scrollTo(0, y));
  } catch (err) {
    // Хранилище недоступно — предпросмотр откроется сверху.
  }
  window.addEventListener("pagehide", () => {
    try {
      sessionStorage.setItem(SCROLL_KEY, String(Math.round(window.scrollY)));
    } catch (err) {
      // Не запомним — не страшно.
    }
  });
})();
