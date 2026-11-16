/* Видеоотзывы в Редакторе (группа «Видеоотзывы»): форма «+ Добавить видео» (в группе и в окне выбора),
   библиотека — поиск, «Смотреть», обложка у каждого видео.

   Добавление и обложки сохраняются сразу в черновик запросом к серверу (страница не перезагружается,
   несохранённые поля Редактора и выбор в окне остаются как были): /admin/editor/video-add и
   /admin/editor/video-cover отвечают JSON с новым отпечатком videos.json — он заменяет version_videos
   в форме Редактора, иначе следующее «Сохранить черновик» сочло бы библиотеку изменённой другим.

   Предпросмотр до сохранения — в браузере: выбранный файл и обложка показываются через blob:-адреса
   (CSP страницы Редактора их разрешает), кадр из видео снимается на canvas. Плеер RuTube — только
   https://rutube.ru/play/embed/… (адрес собирает сервер). Файлы проверяет и сервер: тип — по содержимому. */
(function () {
  "use strict";

  const PLAYER = /^https:\/\/rutube\.ru\/play\/embed\/[0-9a-f]{32}\/?(\?p=[A-Za-z0-9_-]+)?$/;
  const VIDEO_TYPES = ["video/mp4", "video/webm"];
  const VIDEO_EXT = /\.(mp4|m4v|webm)$/i;
  const IMAGE_TYPES = ["image/jpeg", "image/png", "image/webp"];
  const IMAGE_EXT = /\.(jpe?g|png|webp)$/i;
  const FRAME_MAX = 1920;
  const MB = 1024 * 1024;

  const megabytes = (bytes) => (bytes / MB).toLocaleString("ru-RU", { maximumFractionDigits: 1 }) + " МБ";

  function rutubePlayer(src, title) {
    const iframe = document.createElement("iframe");
    iframe.className = "video-preview";
    iframe.src = src;
    iframe.title = title || "Предпросмотр видео";
    iframe.allow = "encrypted-media; fullscreen; picture-in-picture";
    iframe.referrerPolicy = "strict-origin-when-cross-origin";
    return iframe;
  }

  function filePlayer(src, title) {
    const video = document.createElement("video");
    video.className = "video-preview";
    video.controls = true;
    video.playsInline = true;
    video.preload = "metadata";
    video.src = src;
    video.setAttribute("aria-label", title || "Предпросмотр видео");
    return video;
  }

  /* Кадр <video> → JPEG (Blob). Видео — свой файл или blob:, холст не «заражён». Не вышло — null. */
  function captureFrame(video) {
    return new Promise((resolve) => {
      if (!video.videoWidth || !video.videoHeight) {
        resolve(null);
        return;
      }
      const scale = Math.min(1, FRAME_MAX / Math.max(video.videoWidth, video.videoHeight));
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(video.videoWidth * scale);
      canvas.height = Math.round(video.videoHeight * scale);
      try {
        canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
        canvas.toBlob((blob) => resolve(blob), "image/jpeg", 0.9);
      } catch (err) {
        resolve(null);
      }
    });
  }

  /* Отправка FormData с прогрессом загрузки (XMLHttpRequest: у fetch его нет).
     → Promise {ok, message, field, …}; ответ не JSON — понятное сообщение по коду. */
  function send(url, body, onProgress) {
    const xhr = new XMLHttpRequest();
    const done = new Promise((resolve) => {
      xhr.open("POST", url);
      xhr.setRequestHeader("X-Requested-With", "fetch");
      xhr.setRequestHeader("Accept", "application/json");
      if (onProgress) {
        xhr.upload.addEventListener("progress", (event) => {
          if (event.lengthComputable) onProgress(event.loaded, event.total);
        });
      }
      xhr.addEventListener("load", () => {
        let json = null;
        if ((xhr.getResponseHeader("Content-Type") || "").includes("application/json")) {
          try {
            json = JSON.parse(xhr.responseText);
          } catch (err) {
            json = null;
          }
        }
        resolve(json || { ok: false, message: xhr.status === 413
          ? "Файл слишком большой для сервера." : xhr.status === 400
          ? "Форма устарела. Сохраните остальные правки и обновите страницу." : "Сервер не принял файл (ошибка " + xhr.status + ")." });
      });
      xhr.addEventListener("error", () => resolve({ ok: false, message: "Нет связи с сервером — ничего не сохранено." }));
      xhr.addEventListener("abort", () => resolve({ ok: false, aborted: true, message: "Загрузка остановлена — видео не добавлено." }));
      xhr.send(body);
    });
    return { xhr, done };
  }

  // Токен и отпечаток библиотеки — из формы Редактора; ответ сервера приносит новый отпечаток
  function formData(form) {
    const body = new FormData();
    body.set("csrf_token", form.querySelector("[name='csrf_token']").value);
    const version = form.querySelector("[name='version_videos']");
    body.set("version_videos", version ? version.value : "");
    return body;
  }

  function setVersion(form, value) {
    const version = form.querySelector("[name='version_videos']");
    if (version && value) version.value = value;
  }

  function say(el, text, isError) {
    el.textContent = text;
    el.hidden = !text;
    el.classList.toggle("is-error", !!isError);
  }

  /* Выбор обложки: превью, «Загрузить картинку…», у видеофайла — «Взять текущий кадр», «Убрать».
     getVideo() — <video>, из которого снимать кадр (или null). onChange(blob) — выбор изменился. */
  function coverPicker(box, opts) {
    const img = box.querySelector("[data-cover-img]");
    const empty = box.querySelector("[data-cover-empty]");
    const upload = box.querySelector("[data-cover-upload]");
    const input = box.querySelector("[data-cover-file]");
    const frame = box.querySelector("[data-cover-frame]");
    const clear = box.querySelector("[data-cover-clear]");
    const maxImage = Number(opts.maxImage) || 40 * MB;
    let blob = null;
    let url = "";
    let initial = img && img.getAttribute("src");      // сохранённая обложка (панель библиотеки)

    function show(src) {
      if (src) {
        img.src = src;
        img.hidden = false;
        empty.hidden = true;
      } else {
        img.removeAttribute("src");
        img.hidden = true;
        empty.hidden = false;
      }
    }

    function set(next, name) {
      if (url) URL.revokeObjectURL(url);
      url = "";
      blob = next;
      if (blob) {
        blob.coverName = name;
        url = URL.createObjectURL(blob);
      }
      show(url || (opts.keepInitial ? initial : ""));
      if (clear) clear.hidden = !blob;
      if (opts.onChange) opts.onChange(blob);
    }

    upload.addEventListener("click", () => input.click());
    input.addEventListener("change", () => {
      const file = input.files[0];
      input.value = "";
      if (!file) return;
      if (!IMAGE_TYPES.includes(file.type) && !IMAGE_EXT.test(file.name)) {
        opts.error("Обложка — картинка JPG, PNG или WebP.");
        return;
      }
      if (file.size > maxImage) {
        opts.error("Картинка больше " + megabytes(maxImage) + " — уменьшите её.");
        return;
      }
      opts.error("");
      set(file, file.name);
    });
    if (frame) {
      frame.addEventListener("click", async () => {
        const video = opts.getVideo();
        const shot = video && await captureFrame(video);
        if (!shot) {
          opts.error("Кадр не получился — дождитесь загрузки видео и попробуйте ещё раз.");
          return;
        }
        opts.error("");
        set(shot, "frame.jpg");
      });
    }
    if (clear) clear.addEventListener("click", () => set(null));

    return {
      get blob() { return blob; },
      set,
      reset() { set(null); },
      // Обложка сохранена на сервере — теперь она «исходная» для этой панели
      setInitial(src) { initial = src || ""; },
      // «Вернуть обложку по умолчанию»: показать пустое превью до сохранения
      blank() {
        set(null);
        show("");
      },
      frameButton: frame,
    };
  }

  /* Форма «+ Добавить видео». opts: form — форма Редактора; onAdded(result) — видео в черновике;
     onEscape() — Esc внутри формы. → {reset, leave, isBusy, focus}. */
  function adder(root, opts) {
    const form = opts.form;
    const sources = root.querySelector("[data-adder-sources]");
    const panes = {};
    root.querySelectorAll("[data-adder-pane]").forEach((pane) => { panes[pane.dataset.adderPane] = pane; });
    const fields = {};
    root.querySelectorAll("[data-adder-field]").forEach((el) => { fields[el.dataset.adderField] = el; });
    const fileButton = root.querySelector("[data-adder-file-button]");
    const fileInput = root.querySelector("[data-adder-file]");
    const fileName = root.querySelector("[data-adder-file-name]");
    const preview = root.querySelector("[data-adder-preview]");
    const coverBox = root.querySelector("[data-cover-pick]");
    const coverNote = root.querySelector("[data-cover-note]");
    const progress = root.querySelector("[data-adder-progress]");
    const bar = progress.querySelector("progress");
    const progressText = root.querySelector("[data-adder-progress-text]");
    const message = root.querySelector("[data-adder-message]");
    const save = root.querySelector("[data-adder-save]");
    const check = root.querySelector("[data-adder-check]");
    const cancel = root.querySelector("[data-adder-cancel]");
    const abort = root.querySelector("[data-adder-abort]");
    const maxVideo = Number(root.dataset.maxVideo) || 180 * MB;
    let source = "rutube";
    let file = null;
    let fileUrl = "";
    let unplayable = false;
    let coverChosen = false;            // обложку выбрал человек (а не автоматический первый кадр)
    let request = null;

    const error = (text) => say(message, text, true);
    const cover = coverPicker(coverBox, {
      maxImage: root.dataset.maxImage,
      error,
      getVideo: () => preview.querySelector("video"),
    });

    sources.hidden = false;
    coverBox.hidden = false;
    check.hidden = false;
    cancel.hidden = false;
    if (save.type === "submit") save.type = "button";      // с JS — сохранение запросом, без перезагрузки

    function invalid(name) {
      Object.values(fields).forEach((el) => el.removeAttribute("aria-invalid"));
      fileButton.removeAttribute("aria-invalid");
      const el = name === "file" ? fileButton : fields[name];
      if (el) el.setAttribute("aria-invalid", "true");
      return el;
    }

    function clearPreview() {
      preview.replaceChildren();
      preview.hidden = true;
    }

    function setSource(next) {
      source = next;
      sources.querySelectorAll("[data-adder-source]").forEach((b) => {
        b.setAttribute("aria-pressed", String(b.dataset.adderSource === source));
      });
      Object.entries(panes).forEach(([name, pane]) => { pane.hidden = name !== source; });
      check.hidden = source !== "rutube";
      cover.frameButton.hidden = !(source === "file" && file && !unplayable);
      coverNote.textContent = source === "file"
        ? "По умолчанию — кадр в начале видео. Поставьте видео на нужный момент и нажмите «Взять текущий кадр» или загрузите картинку."
        : "По умолчанию — картинка с RuTube. Можно загрузить свою.";
      clearPreview();
      if (source === "file" && fileUrl) showFile();
      say(message, "");
    }

    // Выбранный файл в плеере; первый кадр (около 1-й секунды) — обложка, пока человек не выбрал свою
    function showFile() {
      const video = filePlayer(fileUrl, "Предпросмотр выбранного видео");
      video.muted = true;
      video.addEventListener("loadedmetadata", () => {
        if (!coverChosen) video.currentTime = Math.min(1, (video.duration || 0) / 10);
      });
      video.addEventListener("seeked", async () => {
        if (coverChosen || cover.blob) return;
        const shot = await captureFrame(video);
        if (shot && !coverChosen) cover.set(shot, "frame.jpg");
      }, { once: true });
      video.addEventListener("error", () => {
        unplayable = true;
        cover.frameButton.hidden = true;
        error("Браузер не может воспроизвести этот файл — скорее всего, видео сжато не в H.264. Сохраните его в MP4 (H.264) или WebM.");
      });
      preview.replaceChildren(video);
      preview.hidden = false;
    }

    function chooseFile(next) {
      if (fileUrl) URL.revokeObjectURL(fileUrl);
      file = null;
      fileUrl = "";
      unplayable = false;
      if (!coverChosen) cover.reset();
      clearPreview();
      fileName.textContent = "Файл не выбран";
      if (!next) return;
      if (!VIDEO_TYPES.includes(next.type) && !VIDEO_EXT.test(next.name)) {
        invalid("file");
        error("Нужен видеофайл MP4 или WebM. Видео MOV с iPhone сначала сохраните в MP4 (H.264).");
        return;
      }
      if (next.size > maxVideo) {
        invalid("file");
        error("Файл " + megabytes(next.size) + " — больше предела " + megabytes(maxVideo) + ". Сожмите видео или укоротите его.");
        return;
      }
      invalid(null);
      say(message, "");
      file = next;
      fileUrl = URL.createObjectURL(file);
      fileName.textContent = file.name + " — " + megabytes(file.size);
      cover.frameButton.hidden = false;
      showFile();
    }

    function filled() {
      return !!(fields.url.value.trim() || fields.title.value.trim() || fields.description.value.trim() || file || coverChosen);
    }

    function setBusy(on) {
      save.disabled = on;
      sources.querySelectorAll("button").forEach((b) => { b.disabled = on; });
      abort.hidden = !on || source !== "file";
      progress.hidden = !on;
      if (on) {
        bar.value = 0;
        progressText.textContent = source === "file" ? "Загрузка: 0 %" : "Сохраняем…";
      }
    }

    function reset() {
      if (request) return;
      Object.values(fields).forEach((el) => { el.value = ""; });
      invalid(null);
      coverChosen = false;
      chooseFile(null);
      cover.reset();
      setSource("rutube");
      say(message, "");
      progress.hidden = true;
    }

    async function submit() {
      if (request) return;
      if (source === "rutube" && !fields.url.value.trim()) {
        invalid("url").focus();
        error("Вставьте ссылку на видео RuTube.");
        return;
      }
      if (source === "file" && !file) {
        invalid("file").focus();
        error("Выберите видеофайл MP4 или WebM.");
        return;
      }
      if (source === "file" && unplayable) {
        invalid("file").focus();
        error("Этот файл браузер не воспроизводит — сохраните видео в MP4 (H.264) или WebM.");
        return;
      }
      invalid(null);
      const body = formData(form);
      body.set("source", source);
      body.set("title", fields.title.value);
      body.set("description", fields.description.value);
      if (source === "rutube") body.set("url", fields.url.value.trim());
      else body.set("file", file, file.name);
      if (cover.blob) body.set("cover", cover.blob, cover.blob.coverName || "cover.jpg");
      setBusy(true);
      say(message, "");
      request = send(root.dataset.addUrl, body, (loaded, total) => {
        const percent = Math.floor((loaded / total) * 100);
        bar.value = percent;
        progressText.textContent = percent < 100
          ? "Загрузка: " + percent + " % (" + megabytes(loaded) + " из " + megabytes(total) + ")"
          : "Файл загружен, сервер проверяет его…";
      });
      const result = await request.done;
      request = null;
      setBusy(false);
      if (!result.ok) {
        const el = invalid(result.field);
        error(result.message);
        if (!result.aborted) (el || save).focus();
        return;
      }
      setVersion(form, result.version);
      reset();
      say(message, result.message);
      if (opts.onAdded) opts.onAdded(result);
    }

    sources.addEventListener("click", (event) => {
      const button = event.target.closest("[data-adder-source]");
      if (button && !request) setSource(button.dataset.adderSource);
    });
    fileButton.addEventListener("click", () => fileInput.click());
    fileInput.addEventListener("change", () => {
      chooseFile(fileInput.files[0] || null);
      fileInput.value = "";
    });
    // Выбор обложки человеком: автоматический кадр её больше не заменит
    coverBox.addEventListener("click", (event) => {
      if (event.target.closest("[data-cover-frame], [data-cover-upload]")) coverChosen = true;
      if (event.target.closest("[data-cover-clear]")) coverChosen = true;
    });
    save.addEventListener("click", (event) => {
      event.preventDefault();
      submit();
    });
    abort.addEventListener("click", () => {
      if (request) request.xhr.abort();
    });
    cancel.addEventListener("click", () => {
      if (!leave()) return;
      reset();
      if (opts.onEscape) opts.onEscape();
    });

    check.addEventListener("click", () => {
      const value = fields.url.value.trim();
      if (!value) {
        invalid("url").focus();
        error("Вставьте ссылку на видео RuTube.");
        return;
      }
      say(message, "Проверяем ссылку…");
      fetch(root.dataset.checkUrl + "?url=" + encodeURIComponent(value), {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      })
        .then((response) => response.json())
        .then((data) => {
          if (data.ok && PLAYER.test(data.player)) {
            invalid(null);
            say(message, "");
            preview.replaceChildren(rutubePlayer(data.player));
            preview.hidden = false;
          } else {
            invalid("url");
            error(data.message || "Ссылка не подходит.");
          }
        })
        .catch(() => error("Не удалось проверить ссылку — проверьте соединение."));
    });
    fields.url.addEventListener("input", () => {
      fields.url.removeAttribute("aria-invalid");
      if (preview.querySelector("iframe")) clearPreview();
    });

    // Enter в однострочном поле — «Сохранить видео», а не отправка всей формы Редактора;
    // Esc — закрыть только форму (а не окно выбора): перехват на keydown, отмену события cancel
    // у <dialog> Chrome без недавнего действия пользователя не принимает
    root.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && opts.onEscape) {
        event.preventDefault();
        event.stopPropagation();
        if (!request && leave()) {
          reset();
          opts.onEscape();
        }
      } else if (event.key === "Enter" && event.target.tagName === "INPUT" && event.target.type !== "file") {
        event.preventDefault();
        submit();
      }
    });

    // Уйти из формы: посреди загрузки — остановить её; с несохранённым новым видео — подтвердить
    function leave() {
      if (request) {
        if (!window.confirm("Видео ещё загружается. Остановить загрузку?")) return false;
        request.xhr.abort();
        return true;
      }
      return !filled() || window.confirm("Новое видео не сохранено. Закрыть без него?");
    }

    setSource("rutube");
    return {
      reset,
      leave,
      isBusy: () => !!request,
      focus() {
        const target = source === "file" ? fileButton : fields.url;
        target.focus();
      },
    };
  }

  /* Библиотека: поиск по карточкам, «Смотреть» (плеер под карточкой), «Обложка…» — панель с выбором
     обложки и сохранением в черновик; insertCard(html) — карточка нового видео (HTML от сервера,
     тот же шаблон, что у страницы: значения в нём экранированы). onCover(id, thumb) — обложка изменилась. */
  function library(root, opts) {
    const form = opts.form;
    const list = root.querySelector("[data-video-list]");
    const search = root.querySelector("[data-vlib-search]");
    const count = root.querySelector("[data-vlib-count]");
    const empty = root.querySelector("[data-vlib-empty]");
    const nothing = root.querySelector("[data-vlib-nothing]");
    root.querySelector("[data-vlib-tools]").hidden = false;

    const cards = () => Array.from(list.querySelectorAll("[data-video-card]"));

    function filter() {
      const q = search.value.trim().toLowerCase();
      let shown = 0;
      cards().forEach((card) => {
        const match = !q || (card.dataset.search || "").toLowerCase().includes(q);
        card.hidden = !match;
        if (match) shown += 1;
      });
      const total = cards().length;
      count.textContent = "(" + (q ? shown + " из " + total : total) + ")";
      nothing.hidden = !q || shown > 0;
      empty.hidden = total > 0;
    }

    search.addEventListener("input", filter);
    search.addEventListener("keydown", (event) => {
      if (event.key === "Enter") event.preventDefault();
    });

    function watch(card) {
      const box = card.querySelector("[data-video-watch-box]");
      card.querySelectorAll("[data-video-watch], [data-video-file]").forEach((link) => {
        const src = link.dataset.videoWatch || link.dataset.videoFile;
        if (link.dataset.videoWatch && !PLAYER.test(src)) return;
        link.addEventListener("click", (event) => {
          event.preventDefault();
          const open = box.hidden;
          box.replaceChildren();
          if (open) box.append(link.dataset.videoWatch ? rutubePlayer(src) : filePlayer(src));
          box.hidden = !open;
          link.setAttribute("aria-expanded", String(open));
          link.textContent = open ? "Скрыть видео" : "Смотреть";
        });
      });
    }

    function coverPanel(card) {
      const panel = card.querySelector("[data-cover-panel]");
      const toggle = card.querySelector("[data-cover-open]");
      if (!panel || !toggle) return;
      const save = panel.querySelector("[data-cover-save]");
      const cancel = panel.querySelector("[data-cover-cancel]");
      const message = panel.querySelector("[data-cover-message]");
      const sourceVideo = panel.querySelector("[data-cover-video]");
      let busy = false;
      let action = "";
      const error = (text) => say(message, text, true);
      const picker = coverPicker(panel, {
        maxImage: panel.dataset.maxImage,
        keepInitial: true,
        error,
        getVideo: () => sourceVideo,
        onChange: (blob) => {
          action = blob ? "upload" : "";
          save.disabled = !action;
        },
      });
      toggle.hidden = false;

      function show(on) {
        panel.hidden = !on;
        toggle.setAttribute("aria-expanded", String(on));
        if (!on) {
          picker.reset();
          say(message, "");
          if (sourceVideo) sourceVideo.pause();
        }
      }

      toggle.addEventListener("click", () => {
        show(panel.hidden);
        if (!panel.hidden) panel.querySelector("button:not([hidden])").focus();
      });
      cancel.addEventListener("click", () => {
        show(false);
        toggle.focus();
      });
      panel.addEventListener("click", (event) => {
        if (!event.target.closest("[data-cover-reset]")) return;
        picker.blank();
        action = "reset";
        save.disabled = false;
        say(message, "Будет обложка по умолчанию — нажмите «Сохранить обложку».");
      });
      panel.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
          event.preventDefault();
          show(false);
          toggle.focus();
        }
      });

      save.addEventListener("click", async () => {
        if (busy || !action) return;
        const body = formData(form);
        body.set("id", card.dataset.id);
        body.set("action", action);
        if (action === "upload") body.set("cover", picker.blob, picker.blob.coverName || "cover.jpg");
        busy = true;
        save.disabled = true;
        say(message, "Сохраняем…");
        const result = await send(panel.dataset.coverUrl, body).done;
        busy = false;
        if (!result.ok) {
          save.disabled = false;
          error(result.message);
          return;
        }
        setVersion(form, result.version);
        picker.setInitial(result.thumb);
        updateCard(card, result);
        show(false);
        toggle.focus();
        say(card.querySelector("[data-vcard-status]"), result.message);
        if (opts.onCover) opts.onCover(card.dataset.id, result.thumb);
      });
    }

    // Обложка в карточке, в панели и пометка «Своя обложка»; кнопка «Вернуть…» — только при своей
    function updateCard(card, result) {
      const media = card.querySelector(".vcard-media");
      let img = media.querySelector("[data-vcard-img]");
      const icon = media.querySelector(".picker-video-play");
      if (result.thumb) {
        if (!img) {
          img = document.createElement("img");
          img.alt = "";
          img.width = 320;
          img.height = 180;
          img.setAttribute("data-vcard-img", "");
          media.prepend(img);
        }
        img.src = result.thumb;
        if (icon) icon.remove();
      }
      const panelImg = card.querySelector("[data-cover-panel] [data-cover-img]");
      const panelEmpty = card.querySelector("[data-cover-panel] [data-cover-empty]");
      if (result.thumb) {
        panelImg.src = result.thumb;
        panelImg.hidden = false;
        panelEmpty.hidden = true;
      }
      const tags = card.querySelector(".video-tags");
      let tag = tags.querySelector("[data-vcard-cover-tag]");
      if (!tag) {
        tag = document.createElement("span");
        tag.className = "tag";
        tag.setAttribute("data-vcard-cover-tag", "");
        tags.append(tag);
      }
      tag.textContent = result.custom ? "Своя обложка" : result.thumb ? "" : "Без обложки";
      tag.hidden = !tag.textContent;
      const actions = card.querySelector("[data-cover-panel] .cover-pick-actions");
      let reset = actions.querySelector("[data-cover-reset]");
      if (result.custom && !reset) {
        reset = document.createElement("button");
        reset.className = "button button-small";
        reset.type = "button";
        reset.setAttribute("data-cover-reset", "");
        reset.textContent = card.dataset.kind === "rutube" ? "Вернуть обложку RuTube" : "Убрать свою обложку";
        actions.append(reset);
      } else if (!result.custom && reset) {
        reset.remove();
      }
    }

    function setup(card) {
      watch(card);
      coverPanel(card);
    }

    cards().forEach(setup);

    function insertCard(html) {
      if (!html) return;
      const template = document.createElement("template");
      template.innerHTML = html.trim();
      const card = template.content.querySelector("[data-video-card]");
      if (!card) return;
      // Новая карточка — последняя: у прежней последней снова работает «↓»
      const last = cards().pop();
      if (last) {
        const down = last.querySelector("[name='video_action'][value^='down:']");
        if (down) down.disabled = false;
      }
      list.append(card);
      setup(card);
      search.value = "";
      filter();
    }

    filter();
    return { insertCard };
  }

  window.CRVUVideos = { adder, library };
})();
