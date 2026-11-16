/* Визуальный редактор главной (админка): ширина предпросмотра, выбор поля кликом
   в предпросмотре, предупреждение о несохранённых правках. Без JS форма и предпросмотр
   работают — нет только выбора кликом и переключателя ширины. */
(function () {
  "use strict";

  const stage = document.querySelector("[data-editor-stage]");
  const frame = document.querySelector("[data-editor-frame]");
  const form = document.querySelector("[data-editor-form]");
  if (!stage || !frame || !form) return;

  const widths = document.querySelector("[data-editor-widths]");
  const note = document.querySelector("[data-editor-note]");
  const dirtyNote = document.querySelector("[data-editor-dirty]");
  const publish = document.querySelector("[data-editor-publish]");
  const WIDTH_KEY = "crvu-editor-width";
  // Редактор открыт на телефоне — по умолчанию предпросмотр телефона, а не уменьшенный десктоп
  let width = stage.clientWidth < 700 ? 390 : 1440;
  let dirty = false;

  try {
    const saved = Number(localStorage.getItem(WIDTH_KEY));
    if ([390, 768, 1440].includes(saved)) width = saved;
  } catch (err) {
    // Хранилище недоступно — ширина по умолчанию.
  }

  /* Ширина: рамка получает настоящую ширину устройства и уменьшается масштабом,
     если не помещается, — сайт внутри видит именно 390, 768 или 1440 px. */
  function fit() {
    const scale = Math.min(1, stage.clientWidth / width);
    frame.style.width = width + "px";
    frame.style.height = Math.round(stage.clientHeight / scale) + "px";
    frame.style.transform = "scale(" + scale + ")";
    // Узкий предпросмотр (телефон, планшет) — по центру области
    frame.style.marginLeft = Math.max(0, Math.round((stage.clientWidth - width * scale) / 2)) + "px";
  }

  function setWidth(value) {
    width = value;
    widths.querySelectorAll("[data-preview-width]").forEach((button) => {
      button.setAttribute("aria-pressed", String(Number(button.dataset.previewWidth) === width));
    });
    try {
      localStorage.setItem(WIDTH_KEY, String(width));
    } catch (err) {
      // Не запомним — не страшно.
    }
    fit();
  }

  if (widths) {
    widths.hidden = false;
    widths.addEventListener("click", (event) => {
      const button = event.target.closest("[data-preview-width]");
      if (button) setWidth(Number(button.dataset.previewWidth));
    });
  }
  stage.classList.add("is-scaled");
  window.addEventListener("resize", fit);
  setWidth(width);

  function showNote(text) {
    if (!note) return;
    note.textContent = text;
    note.hidden = !text;
  }

  // Сообщить форме о правке (предупреждение о несохранённом и блокировка публикации).
  function markDirty() {
    form.dispatchEvent(new Event("input", { bubbles: true }));
  }

  /* Окно выбора проектов — общий модуль admin-picker.js (им же выбирают видеоотзывы).
     Выбор сохраняет сама форма редактора: «Готово» лишь переносит его в скрытое поле. */
  /* Видео или обложка уже в черновике библиотеки (сохранены запросом): предпросмотр показывает
     черновик, «Опубликовать» доступна (если нет других несохранённых правок). */
  const status = document.querySelector(".editor-status");
  function libraryChanged() {
    if (status) {
      const title = document.createElement("strong");
      title.textContent = "Черновик не опубликован";
      const rest = document.createElement("span");
      rest.className = "muted";
      rest.textContent = " — изменения видео уже в черновике. На сайте пока прежняя версия.";
      status.replaceChildren(title, rest, dirtyNote || "");
    }
    if (publish && !dirty) publish.disabled = false;
    frame.src = frame.getAttribute("src");
  }

  /* Видеоотзывы (admin-videos.js): форма «+ Добавить видео» в группе и в окне выбора, библиотека.
     Новое видео сразу встаёт и в окно выбора, и в библиотеку; новая обложка — в обе. */
  const videos = window.CRVUVideos;
  const pickers = {};
  let videoPicker = null;
  let library = null;

  function videoAdded(result, fromPicker) {
    if (videoPicker) videoPicker.addCard(result.video, fromPicker ? result.message : "");
    if (library) library.insertCard(result.card);
    libraryChanged();
  }

  const adders = {};
  if (videos) {
    form.querySelectorAll("[data-video-adder]").forEach((root) => {
      const inPicker = !!root.closest("[data-picker-dialog]");
      adders[inPicker ? "picker" : "library"] = videos.adder(root, {
        form,
        onAdded: (result) => videoAdded(result, inPicker),
        // Esc в форме окна — закрыть только форму; в группе — свернуть «+ Добавить видео»
        onEscape: inPicker ? () => videoPicker && videoPicker.escapeAdd() : () => {
          const details = root.closest("details");
          if (details) {
            details.open = false;
            details.querySelector("summary").focus();
          }
        },
      });
    });
    const libRoot = form.querySelector(".video-library");
    if (libRoot) {
      library = videos.library(libRoot, {
        form,
        onCover: (id, thumb) => {
          if (videoPicker) videoPicker.updateCard(id, { thumb });
          libraryChanged();
        },
      });
    }
  }

  document.querySelectorAll("[data-picker]").forEach((root) => {
    const isVideos = !!root.querySelector("[data-video-adder]");
    const picker = window.CRVUAdminPicker &&
      window.CRVUAdminPicker.init(root, markDirty, isVideos ? { adder: adders.picker } : null);
    if (picker) pickers[root.dataset.pickerField] = picker;
    if (picker && isVideos) videoPicker = picker;
  });

  // Поле в панели по имени: раскрыть группу, выделить, прокрутить к нему.
  function selectField(name) {
    if (pickers[name]) {
      const group = pickers[name].root.closest("details");
      if (group) group.open = true;
      showNote("");
      pickers[name].open();
      return;
    }
    const control = form.elements.namedItem(name);
    const field = form.querySelector('[data-field="' + CSS.escape(name) + '"]');
    form.querySelectorAll(".field.is-selected").forEach((el) => el.classList.remove("is-selected"));
    if (!control || !field) {
      showNote("Этот элемент пока нельзя изменить в редакторе.");
      return;
    }
    showNote("");
    const group = field.closest("details");
    if (group) group.open = true;
    field.classList.add("is-selected");
    field.scrollIntoView({ block: "center", behavior: "smooth" });
    control.focus({ preventScroll: true });
  }

  // Сообщения только от своей рамки предпросмотра и только своего сайта.
  window.addEventListener("message", (event) => {
    if (event.origin !== window.location.origin || event.source !== frame.contentWindow) return;
    const data = event.data;
    if (data && data.type === "crvu-edit" && typeof data.field === "string") selectField(data.field);
  });

  // Фокус в поле — подсветить его элемент в предпросмотре.
  form.addEventListener("focusin", (event) => {
    if (event.target.closest("[data-picker-dialog], [data-video-adder], [data-cover-panel], [data-vlib-tools]")) return;
    const name = event.target.name;
    if (!name || !frame.contentWindow) return;
    frame.contentWindow.postMessage({ type: "crvu-focus", field: name }, window.location.origin);
  });

  // Несохранённые правки: публикация опубликовала бы прежний черновик — блокируем и предупреждаем.
  form.addEventListener("input", (event) => {
    // Поиск в окне выбора, поиск по видео, формы добавления видео и обложки (сохраняются сами) — не правка
    if (event.target.closest && event.target.closest("[data-picker-dialog], [data-video-adder], [data-cover-panel], [data-vlib-tools]")) return;
    if (dirty) return;
    dirty = true;
    if (dirtyNote) dirtyNote.hidden = false;
    if (publish) publish.disabled = true;
  });
  form.addEventListener("submit", () => {
    dirty = false;
  });
  window.addEventListener("beforeunload", (event) => {
    // Несохранённые правки или ещё идёт загрузка видео
    if (!dirty && !Object.values(adders).some((a) => a.isBusy())) return;
    event.preventDefault();
    event.returnValue = "";
  });

  // Удаление из библиотеки — только после подтверждения.
  form.addEventListener("click", (event) => {
    const button = event.target.closest("[data-confirm]");
    if (button && !window.confirm(button.dataset.confirm)) event.preventDefault();
  });

})();
