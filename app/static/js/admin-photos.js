"use strict";

/* Фото на странице проекта в админке (/admin/projects/<id>).

   Стрелки и «Сделать обложкой» переставляют карточки на месте: страница не перезагружается и не
   прыгает, введённые поля формы проекта остаются. Новый порядок (первое фото — обложка) записывается
   в скрытые поля photo_order формы проекта и сохраняется только кнопкой «Сохранить изменения».
   «Удалить» — после подтверждения отдельным запросом (фото, его версии и оригинал уходят в
   content/.history/); карточка убирается только после ответа сервера, при ошибке остаётся на месте.
   Без скрипта кнопки работают как раньше — каждое действие сохраняется сразу. */

(function () {
  const form = document.getElementById("project-form");
  const grid = document.querySelector("[data-photo-grid]");
  const order = document.querySelector("[data-photo-order]");
  if (!form || !grid || !order) return;

  const bar = document.querySelector("[data-photo-bar]");
  const status = document.querySelector("[data-photo-status]");
  const saveButton = document.querySelector("[data-photo-save]");
  let unsaved = false;     // в форме или порядке фото есть несохранённые изменения
  let saving = false;

  function cards() {
    return Array.from(grid.querySelectorAll("[data-photo]"));
  }

  function say(text, isError) {
    if (!status || !bar) return;
    status.textContent = text;
    status.classList.toggle("message-error", Boolean(isError));
    status.classList.toggle("message-note", !isError);
    status.setAttribute("role", isError ? "alert" : "status");
    bar.hidden = !text;
  }

  // Подписи, доступность стрелок и скрытые поля порядка — по текущему порядку карточек
  function refresh() {
    const list = cards();
    list.forEach((card, i) => {
      const n = i + 1;
      const label = card.querySelector(".photo-label");
      label.textContent = "";
      if (i === 0) {
        const tag = document.createElement("span");
        tag.className = "tag tag-published";
        tag.textContent = "Обложка";
        label.append(tag);
      } else {
        label.textContent = `Фото ${n}`;
      }
      card.querySelector("img").alt = `Фото ${n}`;
      const up = card.querySelector('[data-photo-action="up"]');
      const down = card.querySelector('[data-photo-action="down"]');
      up.disabled = i === 0;
      down.disabled = i === list.length - 1;
      up.setAttribute("aria-label", `Фото ${n} выше`);
      down.setAttribute("aria-label", `Фото ${n} ниже`);
      card.querySelector('[data-photo-action="cover"]').hidden = i === 0;
    });
    order.replaceChildren(...list.map((card) => {
      const input = document.createElement("input");
      input.type = "hidden";
      input.name = "photo_order";
      input.value = card.dataset.photo;
      return input;
    }));
  }

  function markUnsaved() {
    unsaved = true;
    if (saveButton) saveButton.hidden = false;
    say("Порядок фото изменён. Он сохранится вместе с остальными полями кнопкой «Сохранить изменения».");
  }

  // Фокус остаётся на нажатой кнопке, а если она стала недоступна — на соседней стрелке той же карточки
  function keepFocus(card, action) {
    const same = card.querySelector(`[data-photo-action="${action}"]`);
    const other = card.querySelector(`[data-photo-action="${action === "up" ? "down" : "up"}"]`);
    const target = same && !same.disabled && !same.hidden ? same : other;
    if (target && !target.disabled) target.focus({ preventScroll: true });
  }

  function move(card, action) {
    if (action === "up" && card.previousElementSibling) {
      grid.insertBefore(card, card.previousElementSibling);
    } else if (action === "down" && card.nextElementSibling) {
      grid.insertBefore(card.nextElementSibling, card);
    } else if (action === "cover" && card !== grid.firstElementChild) {
      grid.prepend(card);
    } else {
      return;                 // крайняя карточка: двойное нажатие ничего не ломает
    }
    refresh();
    markUnsaved();
    keepFocus(card, action === "cover" ? "down" : action);
  }

  async function remove(card, button) {
    const n = cards().indexOf(card) + 1;
    if (!window.confirm(`Удалить фото ${n}? Фото уберётся из проекта сразу; копия файла и оригинал останутся в content/.history/.`)) {
      return;
    }
    const actions = card.querySelector(".photo-actions");
    const buttons = Array.from(actions.querySelectorAll("button"));
    buttons.forEach((b) => { b.disabled = true; });          // повторное нажатие не отправит второй запрос
    const body = new FormData(actions);
    body.set("action", "delete");
    let result = null;
    try {
      // getAttribute: у формы есть кнопки name="action", и свойство form.action вернуло бы их, а не адрес
      const response = await fetch(actions.getAttribute("action"), {
        method: "POST",
        body,
        credentials: "same-origin",
        headers: { "X-Requested-With": "fetch", Accept: "application/json" },
      });
      const json = (response.headers.get("Content-Type") || "").includes("application/json")
        ? await response.json() : null;
      result = json || { ok: false, message: response.status === 400
        ? "Форма устарела. Сохраните поля проекта и обновите страницу." : "Сервер не удалил фото." };
    } catch (err) {
      result = { ok: false, message: "Нет связи с сервером — фото не удалено." };
    }
    if (!result.ok) {
      buttons.forEach((b) => { b.disabled = false; });
      refresh();                                             // стрелки — снова по положению карточки
      say(`${result.message} Остальные поля не изменились.`, true);
      button.focus({ preventScroll: true });
      return;
    }
    const next = card.nextElementSibling || card.previousElementSibling;
    card.remove();
    refresh();
    say(unsaved
      ? `${result.message} Порядок остальных фото сохранится кнопкой «Сохранить изменения».`
      : result.message);
    const focus = next && next.querySelector('[data-photo-action="delete"]');
    if (focus) focus.focus({ preventScroll: true });
  }

  grid.addEventListener("click", (event) => {
    const button = event.target.closest("[data-photo-action]");
    if (!button || !grid.contains(button)) return;
    event.preventDefault();                                  // без отправки формы и перезагрузки
    const card = button.closest("[data-photo]");
    const action = button.dataset.photoAction;
    // Второй щелчок двойного нажатия попал бы в соседнюю карточку, вставшую под курсор, и вернул бы
    // фото назад: двойное нажатие — один шаг. Удаление и так блокирует кнопки до ответа сервера
    if (event.detail > 1) return;
    if (action === "delete") remove(card, button);
    else move(card, action);
  });

  // Изменили поле проекта — уход со страницы (загрузка фото, публикация, ссылки) спросит подтверждение
  form.addEventListener("input", () => { unsaved = true; });
  form.addEventListener("change", () => { unsaved = true; });
  form.addEventListener("submit", () => { saving = true; });
  window.addEventListener("beforeunload", (event) => {
    if (unsaved && !saving) {
      event.preventDefault();
      event.returnValue = "";
    }
  });
})();
