"use strict";

/* Импорт папки проекта в админке (страница /admin/projects/import-folder).

   После выбора папки в поле остаются только фото (JPG, PNG, WebP) в порядке имён; показываются
   миниатюры, их можно переставить, убрать и выбрать обложку (первое фото). Отправляет обычная форма
   создания проекта — обработка фото на сервере та же, что и при загрузке на странице проекта.
   Без этого скрипта форма тоже работает: сервер сам пропустит не-фото. Чистые функции проверяются
   в Node (tests/test_folder_import.py). */

(function (root) {
  const PHOTO = new Set([".jpg", ".jpeg", ".png", ".webp"]);
  const JUNK = new Set([".ds_store", "thumbs.db", "desktop.ini"]);
  const MB = 1024 * 1024;

  function baseName(path) {
    const parts = String(path).split("/");
    return parts[parts.length - 1];
  }

  // Что за файл: photo — загружаем, junk — служебный (молча), heic / other — пропускаем с пояснением
  function kind(path) {
    const name = baseName(path);
    if (!name || name.startsWith(".") || JUNK.has(name.toLowerCase())) return "junk";
    const dot = name.lastIndexOf(".");
    const ext = dot > 0 ? name.slice(dot).toLowerCase() : "";
    if (PHOTO.has(ext)) return "photo";
    if (ext === ".heic" || ext === ".heif") return "heic";
    return "other";
  }

  // «2.jpg» раньше «10.jpg» — числа в имени сравниваются как числа
  const collator = new Intl.Collator("ru", { numeric: true, sensitivity: "base" });
  function naturalCompare(a, b) {
    return collator.compare(String(a), String(b));
  }

  // «Агрос. Трансфаир 40м2/01.jpg» → «Агрос. Трансфаир 40м2»
  function folderName(path) {
    const parts = String(path).split("/");
    return parts.length > 1 ? parts[0] : "";
  }

  function move(list, index, step) {
    const target = index + step;
    const out = list.slice();
    if (target < 0 || target >= out.length) return out;
    [out[index], out[target]] = [out[target], out[index]];
    return out;
  }

  function toFront(list, index) {
    const out = list.slice();
    out.unshift(out.splice(index, 1)[0]);
    return out;
  }

  function without(list, index) {
    return list.filter((item, n) => n !== index);
  }

  // Те же пределы, что проверяет сервер: число фото, размер каждого и всего запроса
  function check(sizes, limits) {
    const problems = [];
    if (sizes.length > limits.files) {
      problems.push(`Выбрано ${sizes.length} фото — за раз не больше ${limits.files}. ` +
        "Уберите лишние: их можно добавить потом на странице проекта.");
    }
    const big = sizes.filter((size) => size > limits.fileBytes).length;
    if (big) problems.push(`${big} фото больше ${Math.round(limits.fileBytes / MB)} МБ — уберите их или уменьшите.`);
    const total = sizes.reduce((sum, size) => sum + size, 0);
    if (total > limits.totalBytes) {
      problems.push(`Всего ${Math.round(total / MB)} МБ — за раз не больше ${Math.round(limits.totalBytes / MB)} МБ. ` +
        "Уберите часть фото.");
    }
    return problems;
  }

  function init(doc) {
    const form = doc.querySelector("[data-folder-import]");
    if (!form) return;
    const input = form.querySelector("[data-folder-input]");
    const title = form.querySelector("[data-folder-title]");
    const preview = form.querySelector("[data-folder-preview]");
    const summary = form.querySelector("[data-folder-summary]");
    const problemsBox = form.querySelector("[data-folder-problems]");
    const submit = form.querySelector("[data-folder-submit]");
    const sending = form.querySelector("[data-folder-sending]");
    const limits = {
      files: Number(form.dataset.maxFiles),
      fileBytes: Number(form.dataset.maxFileBytes),
      totalBytes: Number(form.dataset.maxTotalBytes),
    };
    let photos = [];
    let others = [];
    let urls = [];
    let canReorder = true;

    const pathOf = (file) => file.webkitRelativePath || file.name;

    // В поле — только выбранные фото в выбранном порядке: их и отправит форма
    function sync() {
      try {
        const transfer = new DataTransfer();
        photos.forEach((file) => transfer.items.add(file));
        input.files = transfer.files;
      } catch (error) {
        canReorder = false;   // старый браузер: уйдут все файлы папки, сервер пропустит не-фото
      }
    }

    function button(label, act, index, aria, disabled) {
      const b = doc.createElement("button");
      b.type = "button";
      b.className = act === "front" || act === "remove" ? "button button-small" : "icon-button";
      b.textContent = label;
      b.dataset.act = act;
      b.dataset.index = String(index);
      b.setAttribute("aria-label", aria);
      b.disabled = Boolean(disabled);
      return b;
    }

    function render() {
      urls.forEach((url) => URL.revokeObjectURL(url));
      urls = [];
      preview.replaceChildren();
      photos.forEach((file, i) => {
        const name = baseName(pathOf(file));
        const item = doc.createElement("li");
        item.className = "photo-card";
        const img = doc.createElement("img");
        const url = URL.createObjectURL(file);
        urls.push(url);
        img.src = url;
        img.alt = "";
        img.width = 320;
        img.height = 180;
        img.decoding = "async";
        img.loading = "lazy";
        const label = doc.createElement("p");
        label.className = "photo-label";
        if (i === 0) {
          const tag = doc.createElement("span");
          tag.className = "tag tag-published";
          tag.textContent = "Обложка";
          label.append(tag, " ");
        }
        label.append(name);
        const actions = doc.createElement("div");
        actions.className = "photo-actions";
        if (canReorder) {
          if (i > 0) actions.append(button("Сделать обложкой", "front", i, `Сделать обложкой: ${name}`));
          actions.append(
            button("←", "left", i, `Раньше: ${name}`, i === 0),
            button("→", "right", i, `Позже: ${name}`, i === photos.length - 1),
            button("Убрать", "remove", i, `Не загружать: ${name}`),
          );
        }
        item.append(img, label, actions);
        preview.append(item);
      });
      preview.hidden = !photos.length;

      const parts = [`Фото к загрузке: ${photos.length}`];
      if (others.length) parts.push(`Не будут загружены: ${others.join(", ")}`);
      if (!canReorder) parts.push("Порядок — как в папке (этот браузер не даёт его менять)");
      summary.textContent = parts.join(". ") + ".";
      summary.hidden = false;

      const problems = check(photos.map((file) => file.size), limits);
      if (!photos.length) problems.push("В папке нет фото JPG, PNG или WebP.");
      problemsBox.replaceChildren(...problems.map((text) => {
        const p = doc.createElement("p");
        p.textContent = text;
        return p;
      }));
      problemsBox.hidden = !problems.length;
      submit.disabled = problems.length > 0;
    }

    input.addEventListener("change", () => {
      const all = Array.from(input.files);
      photos = all.filter((file) => kind(pathOf(file)) === "photo")
        .sort((a, b) => naturalCompare(pathOf(a), pathOf(b)));
      others = all.filter((file) => ["other", "heic"].includes(kind(pathOf(file)))).map((file) => baseName(pathOf(file)));
      const folder = all.length ? folderName(pathOf(all[0])) : "";
      if (folder && (!title.value || title.dataset.auto === "1")) {
        title.value = folder;
        title.dataset.auto = "1";   // название из папки — пока его не поправили вручную
      }
      canReorder = true;
      sync();
      render();
    });

    title.addEventListener("input", () => {
      delete title.dataset.auto;
    });

    preview.addEventListener("click", (event) => {
      const b = event.target.closest("[data-act]");
      if (!b) return;
      const i = Number(b.dataset.index);
      if (b.dataset.act === "front") photos = toFront(photos, i);
      if (b.dataset.act === "left") photos = move(photos, i, -1);
      if (b.dataset.act === "right") photos = move(photos, i, 1);
      if (b.dataset.act === "remove") photos = without(photos, i);
      sync();
      render();
    });

    form.addEventListener("submit", () => {
      submit.disabled = true;     // повторное нажатие не отправит фото второй раз
      sending.hidden = false;
    });
  }

  const api = { kind, naturalCompare, folderName, move, toFront, without, check };
  if (typeof module === "object" && module.exports) module.exports = api;
  else if (root.document) init(root.document);
})(typeof window === "undefined" ? globalThis : window);
