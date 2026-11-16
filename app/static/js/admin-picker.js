/* Окно выбора для админки: что показывать на главной и в каком порядке — проекты портфолио
   (Редактор → «Проекты на главной») и видеоотзывы (Видеоотзывы → «На главной»).
   В форме — скрытое поле с id через запятую в нужном порядке; окно работает с копией выбора
   (working): «Готово» переносит её в поле и вызывает onChange, «Отмена», «Закрыть» и Esc —
   отбрасывают. Данные строк (название, подпись, обложка) — из каталога в окне; id нет в
   каталоге — значит, запись удалили: показываем это и даём убрать. Без JS виден текстовый ввод id.
   В окне видеоотзывов есть «+ Добавить видео» ([data-picker-add]): саму форму ведёт admin-videos.js
   (hooks.adder — её reset/leave/isBusy); окно только показывает и прячет её. Новое видео встаёт
   в каталог через addCard(), выбор в окне и его порядок не меняются; updateCard() — новая обложка. */
(function () {
  "use strict";

  /* Выбор проектов из портфолио: в форме — скрытое поле с id через запятую в нужном порядке.
     Окно работает с копией выбора (working): «Готово» переносит её в поле редактора,
     «Отмена», «Закрыть» и Esc — отбрасывают. Сохраняет выбор сама форма редактора.
     Данные строк (название, выставка, обложка) — из каталога в окне; проекта нет в каталоге —
     значит, его удалили из портфолио: показываем это и даём убрать. */

  function init(root, onChange, hooks) {
    const adder = hooks && hooks.adder;
    const field = root.dataset.pickerField;
    const max = Number(root.dataset.pickerMax) || 6;
    // Подписи по умолчанию — для проектов портфолио; окно видеоотзывов задаёт свои data-атрибутами
    const say = (key, fallback) => root.dataset[key] || fallback;
    const textMissing = say("pickerMissing", "Удалён из портфолио — на главной не показывается");
    const textHidden = say("pickerHidden", "Черновик — на главной не показывается");
    const textFull = say("pickerFull", "на главной уже {n} проектов").replace("{n}", String(max));
    const input = root.querySelector("[data-picker-input]");
    const list = root.querySelector("[data-picker-list]");
    const empty = root.querySelector("[data-picker-empty]");
    const openButton = root.querySelector("[data-picker-open]");
    const dialog = root.querySelector("[data-picker-dialog]");
    if (!input || !list || !dialog || typeof dialog.showModal !== "function") return;
    const search = dialog.querySelector("[data-picker-search]");
    const year = dialog.querySelector("[data-picker-year]");
    const chosen = dialog.querySelector("[data-picker-chosen]");
    const chosenEmpty = dialog.querySelector("[data-picker-chosen-empty]");
    const count = dialog.querySelector("[data-picker-count]");
    const changed = dialog.querySelector("[data-picker-changed]");
    const nothing = dialog.querySelector("[data-picker-nothing]");
    const grid = dialog.querySelector("[data-picker-grid]");
    const cards = Array.from(dialog.querySelectorAll("[data-picker-card]"));
    const info = {};
    function remember(card) {
      const img = card.querySelector("img");
      // Видео без обложки — в строке тот же значок воспроизведения, что на карточке
      const icon = card.querySelector(".picker-video-play");
      info[card.dataset.id] = {
        title: card.dataset.title,
        meta: [card.dataset.event, card.dataset.year].filter(Boolean).join(", "),
        thumb: img ? img.getAttribute("src") : "",
        icon: !img && icon ? icon : null,
        published: card.dataset.published === "1",
      };
    }
    cards.forEach(remember);

    const parse = (value) => value.split(",").map((s) => s.trim()).filter((s, i, all) => s && all.indexOf(s) === i);
    let ids = parse(input.value);
    let working = ids.slice();
    let dragged = null;

    // Без JS виден текстовый ввод id; с JS — список и окно выбора.
    root.querySelector("[data-picker-raw]").hidden = true;
    openButton.hidden = false;

    function row(id, index, total, editable) {
      const li = document.createElement("li");
      const known = info[id];
      li.className = "picker-row" + (known && known.published ? "" : " is-missing");
      li.dataset.id = id;
      const number = document.createElement("span");
      number.className = "picker-row-number";
      number.textContent = String(index + 1);
      const thumb = document.createElement("span");
      thumb.className = "picker-row-thumb";
      if (known && known.thumb) {
        const img = document.createElement("img");
        img.src = known.thumb;
        img.alt = "";
        thumb.append(img);
      } else if (known && known.icon) {
        thumb.append(known.icon.cloneNode(true));
      }
      const text = document.createElement("span");
      text.className = "picker-row-text";
      const title = document.createElement("strong");
      title.textContent = known ? known.title : id;
      const meta = document.createElement("small");
      if (!known) meta.textContent = textMissing;
      else if (!known.published) meta.textContent = textHidden;
      else meta.textContent = known.meta;
      text.append(title, meta);
      li.append(number, thumb, text);
      const actions = document.createElement("span");
      actions.className = "picker-row-actions";
      const button = (label, act, disabled) => {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "picker-icon";
        b.textContent = label;
        b.dataset.act = act;
        b.dataset.id = id;
        b.disabled = !!disabled;
        b.setAttribute("aria-label", { up: "Выше", down: "Ниже", remove: "Убрать с главной" }[act] + ": " + title.textContent);
        actions.append(b);
      };
      if (editable) {
        // Перетаскивание мышью; с клавиатуры и на телефоне — кнопки ↑/↓
        li.draggable = true;
        button("↑", "up", index === 0);
        button("↓", "down", index === total - 1);
      }
      button("✕", "remove");
      li.append(actions);
      return li;
    }

    function renderPanel() {
      list.replaceChildren(...ids.map((id, i) => row(id, i, ids.length, false)));
      empty.hidden = ids.length > 0;
    }

    function renderDialog() {
      chosen.replaceChildren(...working.map((id, i) => row(id, i, working.length, true)));
      chosenEmpty.hidden = working.length > 0;
      count.textContent = working.length + " из " + max;
      changed.hidden = working.join(",") === ids.join(",");
      const full = working.length >= max;
      cards.forEach((card) => {
        const n = working.indexOf(card.dataset.id);
        const action = card.querySelector("[data-picker-action]");
        card.setAttribute("aria-pressed", String(n >= 0));
        card.classList.toggle("is-full", n < 0 && full);
        // Карточку можно нажать всегда: выбранная — убирает, при полном списке — подсказывает
        action.textContent = n >= 0 ? "№ " + (n + 1) + " · убрать" : full ? "Мест нет" : "Добавить";
        card.setAttribute("aria-label", card.dataset.title + (n >= 0 ? ": на главной, № " + (n + 1) + ", нажмите, чтобы убрать"
          : full ? ": " + textFull : ": добавить на главную"));
      });
    }

    function filter() {
      const q = search.value.trim().toLowerCase();
      const y = year ? year.value : "";
      let shown = 0;
      cards.forEach((card) => {
        const item = card.parentElement;
        if (item.hasAttribute("data-picker-draft")) return;
        const match = (!q || card.dataset.title.toLowerCase().includes(q) || (card.dataset.event || "").toLowerCase().includes(q) ||
          card.dataset.id.includes(q)) && (!y || card.dataset.year === y);
        item.hidden = !match;
        if (match) shown += 1;
      });
      nothing.hidden = shown > 0;
    }

    function move(arr, id, step) {
      const i = arr.indexOf(id);
      const j = i + step;
      if (i < 0 || j < 0 || j >= arr.length) return;
      [arr[i], arr[j]] = [arr[j], arr[i]];
    }

    // После перерисовки фокус остаётся на той же кнопке той же строки (или на соседней)
    function refocus(id, act) {
      const target = chosen.querySelector('[data-id="' + CSS.escape(id) + '"] [data-act="' + act + '"]:not(:disabled)') ||
        chosen.querySelector('[data-id="' + CSS.escape(id) + '"] [data-act="remove"]') || search;
      target.focus();
    }

    function open() {
      working = ids.slice();
      closeAdd();
      if (addDone) addDone.hidden = true;
      search.value = "";
      if (year) year.value = "";
      filter();
      renderDialog();
      dialog.showModal();
      // На телефоне клавиатура закрыла бы список — фокус в поиск только на широком экране
      if (window.matchMedia("(min-width: 641px)").matches) search.focus();
      else dialog.querySelector("[data-picker-close]").focus();
    }

    function apply() {
      ids = working.slice();
      input.value = ids.join(",");
      renderPanel();
      if (onChange) onChange();
    }

    openButton.addEventListener("click", open);
    search.addEventListener("input", filter);
    if (year) year.addEventListener("change", filter);
    // Enter в поиске не должен отправлять форму редактора
    search.addEventListener("keydown", (event) => {
      if (event.key === "Enter") event.preventDefault();
    });
    dialog.addEventListener("click", (event) => {
      const card = event.target.closest("[data-picker-card]");
      if (card) {
        const id = card.dataset.id;
        const i = working.indexOf(id);
        if (i >= 0) working.splice(i, 1);
        else if (working.length < max && card.dataset.published === "1") working.push(id);
        renderDialog();
        return;
      }
      const act = event.target.closest("[data-act]");
      if (act) {
        const id = act.dataset.id;
        if (act.dataset.act === "remove") {
          const i = working.indexOf(id);
          working = working.filter((x) => x !== id);
          renderDialog();
          const next = working[Math.min(i, working.length - 1)];
          if (next) refocus(next, "remove");
          else search.focus();
        } else {
          move(working, id, act.dataset.act === "up" ? -1 : 1);
          renderDialog();
          refocus(id, act.dataset.act);
        }
        return;
      }
      if (event.target.closest("[data-picker-done]")) {
        if (adder && !adder.leave()) return;
        apply();
        dialog.close();
      } else if (event.target.closest("[data-picker-close]")) {
        if (adder && !adder.leave()) return;
        dialog.close();
      }
    });

    // Esc: форма нового видео закрывается сама (admin-videos.js перехватывает Esc в ней); иначе — окно,
    // но не посреди загрузки и не с несохранённым новым видео без подтверждения
    dialog.addEventListener("cancel", (event) => {
      if (adder && !adder.leave()) event.preventDefault();
    });

    /* «+ Добавить видео» в окне: кнопка показывает и прячет форму (admin-videos.js). Новое видео —
       карточка из <template> в конце каталога (как в библиотеке); выбор working не меняется. */
    const addBox = dialog.querySelector("[data-picker-add]");
    const addToggle = dialog.querySelector("[data-picker-add-open]");
    const addDone = dialog.querySelector("[data-picker-add-done]");
    const template = dialog.querySelector("[data-picker-card-template]");

    function showAdd(on) {
      if (!addBox || !addToggle) return;
      addBox.hidden = !on;
      addToggle.setAttribute("aria-expanded", String(on));
    }

    function closeAdd() {
      if (adder) adder.reset();
      showAdd(false);
    }

    if (adder && addBox && addToggle) {
      addToggle.hidden = false;
      addToggle.addEventListener("click", () => {
        if (!addBox.hidden) {
          if (!adder.leave()) return;
          closeAdd();
          addToggle.focus();
          return;
        }
        if (addDone) addDone.hidden = true;
        showAdd(true);
        adder.focus();
      });
    }

    function setThumb(card, thumb) {
      const box = card.querySelector(".picker-thumb");
      let img = box.querySelector("img");
      if (!thumb) {
        if (img) img.remove();
      } else {
        if (!img) {
          img = document.createElement("img");
          img.alt = "";
          img.width = 240;
          img.height = 135;
          img.decoding = "async";
          box.prepend(img);
        }
        img.src = thumb;
      }
      remember(card);
    }

    /* Новое видео в каталоге. announce — добавлено из этого окна: фокус на карточку и сообщение;
       из библиотеки (окно закрыто) — карточка просто появляется к следующему открытию. */
    function addCard(video, announce) {
      if (!template || cards.some((card) => card.dataset.id === video.id)) return null;
      const li = template.content.firstElementChild.cloneNode(true);
      const card = li.querySelector("[data-picker-card]");
      card.dataset.id = video.id;
      card.dataset.title = video.title;
      card.dataset.event = video.description || "";
      li.querySelector(".picker-title").textContent = video.title;
      li.querySelector(".picker-meta").textContent = video.description || "";
      const badge = li.querySelector("[data-picker-badge]");
      if (badge) badge.textContent = video.source || "";
      dialog.querySelector("[data-picker-grid]").append(li);
      cards.push(card);
      setThumb(card, video.thumb);
      const none = dialog.querySelector("[data-picker-catalog-empty]");
      if (none) none.hidden = true;
      if (dialog.open) {
        // Новая карточка должна быть видна: поиск сбрасывается, выбор и порядок — нет
        search.value = "";
        if (year) year.value = "";
        filter();
        renderDialog();
      }
      if (announce) {
        showAdd(false);
        if (addDone) {
          addDone.textContent = announce;
          addDone.hidden = false;
        }
        card.scrollIntoView({ block: "nearest", behavior: "smooth" });
        card.focus({ preventScroll: true });
      }
      return card;
    }

    // Обложка изменилась (библиотека): карточка каталога и строки выбранных
    function updateCard(id, data) {
      const card = cards.find((c) => c.dataset.id === id);
      if (!card) return;
      setThumb(card, data.thumb);
      renderPanel();
      if (dialog.open) renderDialog();
    }

    // Перетаскивание строк выбранных: строка встаёт перед или после той, над которой отпущена
    chosen.addEventListener("dragstart", (event) => {
      const li = event.target.closest(".picker-row");
      if (!li) return;
      dragged = li.dataset.id;
      li.classList.add("is-dragging");
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", dragged);
    });
    chosen.addEventListener("dragover", (event) => {
      if (!dragged) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = "move";
    });
    chosen.addEventListener("drop", (event) => {
      const li = event.target.closest(".picker-row");
      if (!dragged || !li || li.dataset.id === dragged) return;
      event.preventDefault();
      const rect = li.getBoundingClientRect();
      const after = event.clientY > rect.top + rect.height / 2;
      working = working.filter((x) => x !== dragged);
      working.splice(working.indexOf(li.dataset.id) + (after ? 1 : 0), 0, dragged);
      renderDialog();
    });
    chosen.addEventListener("dragend", () => {
      dragged = null;
      chosen.querySelectorAll(".is-dragging").forEach((el) => el.classList.remove("is-dragging"));
    });

    // Убрать прямо из панели (например, удалённый из портфолио проект)
    list.addEventListener("click", (event) => {
      const act = event.target.closest("[data-act='remove']");
      if (!act) return;
      ids = ids.filter((id) => id !== act.dataset.id);
      working = ids.slice();
      input.value = ids.join(",");
      renderPanel();
      if (onChange) onChange();
    });

    renderPanel();
    // Esc в форме нового видео (admin-videos.js): закрыть только форму, фокус — на «+ Добавить видео»
    function escapeAdd() {
      closeAdd();
      if (addToggle) addToggle.focus();
    }

    return { open, root, field, addCard, updateCard, escapeAdd };
  }

  window.CRVUAdminPicker = { init };
})();
