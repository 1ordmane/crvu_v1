"use strict";

/* Галерея из app/static/js/main.js на маленькой поддельной DOM — для tests/test_vimeo.py.

   Браузера и зависимостей не нужно: main.js исполняется как есть, остальные его части
   не находят своих элементов и сразу выходят. Запуск: node gallery_harness.js <сценарий>;
   результат — JSON в stdout. */

const fs = require("fs");
const path = require("path");
const vm = require("vm");

class El {
  constructor(tag, attrs = {}) {
    this.tagName = tag.toUpperCase();
    this.attributes = {};
    this.dataset = {};
    this.children = [];
    this.parentNode = null;
    this.listeners = {};
    this.hidden = false;
    this.textContent = "";
    const classes = new Set();
    this.classList = {
      add: (...names) => names.forEach((n) => classes.add(n)),
      remove: (...names) => names.forEach((n) => classes.delete(n)),
      contains: (name) => classes.has(name),
      toggle: (name, force) => ((force ?? !classes.has(name)) ? classes.add(name) : classes.delete(name)),
    };
    for (const [k, v] of Object.entries(attrs)) this.setAttribute(k, v);
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name === "hidden") this.hidden = true;
    if (name.startsWith("data-")) {
      const key = name.slice(5).replace(/-([a-z])/g, (m, c) => c.toUpperCase());
      this.dataset[key] = String(value);
    }
  }

  getAttribute(name) {
    return name in this.attributes ? this.attributes[name] : null;
  }

  removeAttribute(name) {
    delete this.attributes[name];
    if (name === "src" || name === "srcset" || name === "href") this[name] = "";
  }

  addEventListener(type, fn) {
    (this.listeners[type] = this.listeners[type] || []).push(fn);
  }

  dispatch(type, extra = {}) {
    const event = { type, target: this, preventDefault() {}, ...extra };
    for (const fn of this.listeners[type] || []) fn(event);
  }

  click() {
    this.dispatch("click");
  }

  closest() {
    return null;
  }

  matches(selector) {
    const attr = selector.match(/^\[([\w-]+)\]$/);
    if (attr) return attr[1] in this.attributes;
    return this.tagName === selector.toUpperCase();
  }

  querySelectorAll(selector) {
    const found = [];
    const walk = (el) => {
      for (const child of el.children) {
        if (child.matches(selector)) found.push(child);
        walk(child);
      }
    };
    walk(this);
    return found;
  }

  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }

  append(...items) {
    for (const item of items) {
      item.parentNode = this;
      this.children.push(item);
    }
  }

  replaceChildren(...items) {
    this.children.forEach((child) => { child.parentNode = null; });
    this.children = [];
    this.append(...items);
  }

  after(item) {
    const siblings = this.parentNode.children;
    item.parentNode = this.parentNode;
    siblings.splice(siblings.indexOf(this) + 1, 0, item);
  }

  remove() {
    if (!this.parentNode) return;
    const siblings = this.parentNode.children;
    siblings.splice(siblings.indexOf(this), 1);
    this.parentNode = null;
  }

  getBoundingClientRect() {
    return { left: 0, right: 0, top: 0, bottom: 0 };
  }
}

// Окно галереи — как в app/templates/base.html (только то, что читает main.js)
function buildDialog() {
  const dialog = new El("dialog", {
    id: "gallery",
    "data-photo-alt": "{title}, фото {n} из {total}",
    "data-photo-thumb": "Фото {n}",
    "data-video-label": "Смотреть видео",
    "data-photos-label": "К фото",
    "data-video-title": "{title} — видео",
  });
  dialog.open = false;
  dialog.showModal = function () { this.open = true; };
  dialog.close = function () {
    if (!this.open) return;
    this.open = false;
    this.dispatch("close");
  };
  const head = new El("div");
  head.append(new El("h2", { "data-gallery-title": "" }), new El("p", { "data-gallery-meta": "" }),
    new El("p", { "data-gallery-description": "", hidden: "" }), new El("dl", { "data-gallery-specs": "", hidden: "" }));
  const stage = new El("div", { class: "gallery-stage" });
  const image = new El("img", { "data-gallery-image": "" });
  const backdrop = new El("img", { "data-gallery-backdrop": "" });
  stage.getBoundingClientRect = () => ({ width: 990, height: 558 });     // сцена 1440 px: ≈ 1,77 : 1
  stage.append(backdrop, image, new El("p", { "data-gallery-video-error": "", hidden: "" }),
    new El("button", { "data-gallery-step": "-1" }), new El("button", { "data-gallery-step": "1" }));
  const foot = new El("div");
  foot.append(new El("div", { "data-gallery-thumbs": "" }), new El("button", { "data-gallery-video": "", hidden: "" }));
  dialog.append(head, stage, foot);
  return { dialog, stage, image, backdrop };
}

function load() {
  const { dialog, stage, image, backdrop } = buildDialog();
  const docListeners = {};
  const timers = [];
  const document = {
    getElementById: (id) => (id === "gallery" ? dialog : null),
    querySelector: () => null,
    querySelectorAll: () => [],
    createElement: (tag) => new El(tag),
    addEventListener: (type, fn) => { (docListeners[type] = docListeners[type] || []).push(fn); },
    documentElement: { classList: { add() {}, remove() {} } },
  };
  const window = {
    matchMedia: () => ({ matches: false, addEventListener() {} }),
    addEventListener() {},
  };
  const context = {
    window, document, console,
    setTimeout: (fn, ms) => { timers.push({ fn, ms, done: false }); return timers.length; },
    clearTimeout: (id) => { if (timers[id - 1]) timers[id - 1].done = true; },
  };
  const code = fs.readFileSync(path.join(__dirname, "..", "app", "static", "js", "main.js"), "utf8");
  vm.runInNewContext(code, context);

  const g = (sel) => dialog.querySelector(sel);
  const api = {
    dialog, stage, image, backdrop,
    // Фото «загрузилось» с такими размерами
    loaded(width, height) {
      image.naturalWidth = width;
      image.naturalHeight = height;
      image.complete = true;
      image.dispatch("load");
    },
    open(data) {
      const trigger = new El("button", { "data-gallery": JSON.stringify(data) });
      trigger.closest = (sel) => (sel === "[data-gallery]" ? trigger : null);
      for (const fn of docListeners.click || []) fn({ target: trigger });
    },
    // Кнопка «Смотреть видео» под карточкой портфолио: та же галерея, сразу с плеером
    openFromCard(data) {
      const card = new El("li", { "data-stand": "" });
      const gallery = new El("button", { "data-gallery": JSON.stringify(data) });
      const play = new El("button", { "data-play-video": "" });
      card.append(gallery, play);
      play.closest = (sel) => (sel === "[data-play-video]" ? play : sel === "[data-stand]" ? card : null);
      for (const fn of docListeners.click || []) fn({ target: play });
    },
    tiles: () => g("[data-gallery-thumbs]").querySelectorAll("button").map((b) => ({
      label: b.getAttribute("aria-label"), current: b.getAttribute("aria-current"),
      video: "videoTile" in b.dataset })),
    thumbsHidden: () => g("[data-gallery-thumbs]").hidden,
    videoButton: () => g("[data-gallery-video]"),
    error: () => g("[data-gallery-video-error]"),
    step: (n) => dialog.querySelectorAll("[data-gallery-step]").find((b) => b.dataset.galleryStep === String(n)).click(),
    thumb: (n) => g("[data-gallery-thumbs]").querySelectorAll("button")[n].click(),
    key: (key) => dialog.dispatch("keydown", { key, target: { closest: () => null } }),
    runTimers() {
      for (const t of timers) if (!t.done) { t.done = true; t.fn(); }
    },
    iframes: () => dialog.querySelectorAll("iframe"),
    state() {
      const frames = api.iframes();
      const f = frames[0];
      return {
        iframes: frames.length,
        src: f ? f.src : null,
        attrs: f ? { title: f.title, loading: f.loading, allow: f.allow, allowFullscreen: f.allowFullscreen,
          referrerPolicy: f.referrerPolicy, className: f.className } : null,
        imageHidden: image.hidden,
        imageSrc: image.src || "",
        buttonHidden: api.videoButton().hidden,
        stepsHidden: dialog.querySelectorAll("[data-gallery-step]").map((b) => b.hidden),
        buttonText: api.videoButton().textContent,
        errorHidden: api.error().hidden,
        open: dialog.open,
      };
    },
  };
  return api;
}

const PHOTOS = {
  title: "Стенд",
  images: ["/media/a-1920.webp", "/media/b-1920.webp", "/media/c-1920.webp"],
  thumbs: ["/media/a-640.webp", "/media/b-640.webp", "/media/c-640.webp"],
  srcsets: ["", "", ""],
};
const VIMEO = "https://player.vimeo.com/video/76979871?h=8272103f6e&dnt=1";
const RUTUBE = "https://rutube.ru/play/embed/c6cc4d620b1d4338901770a44b3e82f4/?p=f2Ar8UZNsfWnI5cS2BbVZQ";

const scenarios = {
  // Как показывается фото: cover, если обрезка не больше 20 %, иначе целиком на размытой копии
  fit() {
    const g = load();
    const shot = (w, h) => {
      g.loaded(w, h);
      return { fit: g.stage.dataset.fit, loading: g.image.classList.contains("is-loading"),
        backdrop: g.backdrop.src || "" };
    };
    g.open({ ...PHOTOS, vimeo: VIMEO });
    const wide = shot(2560, 1707);           // 3 : 2 в рамке 1,77 : 1 — срез ≈ 15 %
    g.image.complete = false;                // как в браузере: новое фото ещё грузится
    g.step(1);
    const duringLoad = { fit: g.stage.dataset.fit || "", backdrop: g.backdrop.src || "" };
    const narrow = shot(2560, 2025);         // 1,26 : 1 — срез ≈ 29 %
    const panorama = shot(4000, 1200);       // 3,3 : 1 — тоже целиком
    g.videoButton().click();
    const video = { backdropHidden: g.backdrop.hidden, imageHidden: g.image.hidden };
    g.step(1);
    const back = { backdropHidden: g.backdrop.hidden };
    g.dialog.close();
    return { wide, duringLoad, narrow, panorama, video, back, afterClose: g.backdrop.src || "" };
  },
  no_video() {
    const g = load();
    g.open({ ...PHOTOS, vimeo: "" });
    const opened = g.state();
    g.step(1);
    return { opened, after_step: g.state() };
  },
  open_without_click() {
    const g = load();
    g.open({ ...PHOTOS, vimeo: VIMEO });
    return g.state();
  },
  play() {
    const g = load();
    g.open({ ...PHOTOS, vimeo: VIMEO });
    g.videoButton().click();
    return g.state();
  },
  play_then_step() {
    const g = load();
    g.open({ ...PHOTOS, vimeo: VIMEO });
    g.videoButton().click();
    g.step(1);
    return g.state();
  },
  play_then_thumb() {
    const g = load();
    g.open({ ...PHOTOS, vimeo: VIMEO });
    g.videoButton().click();
    g.thumb(2);
    return g.state();
  },
  play_then_key() {
    const g = load();
    g.open({ ...PHOTOS, vimeo: VIMEO });
    g.videoButton().click();
    g.key("ArrowLeft");
    return g.state();
  },
  play_then_back_button() {
    const g = load();
    g.open({ ...PHOTOS, vimeo: VIMEO });
    g.videoButton().click();
    g.videoButton().click();
    return g.state();
  },
  play_then_close() {
    const g = load();
    g.open({ ...PHOTOS, vimeo: VIMEO });
    g.videoButton().click();
    g.dialog.close();                      // Escape и «Закрыть» тоже закрывают окно — событие close
    return g.state();
  },
  play_then_other_stand() {
    const g = load();
    g.open({ ...PHOTOS, vimeo: VIMEO });
    g.videoButton().click();
    g.dialog.close();
    g.open({ ...PHOTOS, title: "Другой", vimeo: "" });
    return g.state();
  },
  rutube_play() {
    const g = load();
    g.open({ ...PHOTOS, vimeo: RUTUBE });
    const before = { buttonHidden: g.state().buttonHidden, iframes: g.iframes().length };
    g.videoButton().click();
    return { before, after: g.state() };
  },
  unsafe_rutube() {
    // Подменённые адреса Rutube: кнопки нет, iframe не создаётся
    const out = {};
    for (const bad of ["https://rutube.ru/video/c6cc4d620b1d4338901770a44b3e82f4/",
      "https://rutube.ru.evil.example/play/embed/c6cc4d620b1d4338901770a44b3e82f4/",
      "https://rutube.ru/play/embed/c6cc4d620b1d4338901770a44b3e82f4/?p=x\"><script>",
      "https://rutube.ru/play/embed/c6cc4d620b1d4338901770a44b3e82f4/?autoplay=1"]) {
      const g = load();
      g.open({ ...PHOTOS, vimeo: bad });
      const button = g.state().buttonHidden;
      g.videoButton().click();
      out[bad] = { buttonHidden: button, iframes: g.iframes().length };
    }
    return out;
  },
  video_tile() {
    // Видео — отдельный элемент ленты: плитка после фото; плеер — только по нажатию на неё
    const g = load();
    g.open({ ...PHOTOS, vimeo: RUTUBE });
    const opened = { tiles: g.tiles(), iframes: g.iframes().length };
    g.thumb(3);
    const playing = { tiles: g.tiles(), state: g.state() };
    g.thumb(0);
    return { opened, playing, back: g.state() };
  },
  one_photo_with_video() {
    const g = load();
    g.open({ ...PHOTOS, images: PHOTOS.images.slice(0, 1), thumbs: PHOTOS.thumbs.slice(0, 1), srcsets: [""],
      vimeo: VIMEO });
    return { hidden: g.thumbsHidden(), tiles: g.tiles() };
  },
  photos_without_video() {
    const g = load();
    g.open({ ...PHOTOS, vimeo: "" });
    return { hidden: g.thumbsHidden(), tiles: g.tiles() };
  },
  card_play() {
    const g = load();
    g.openFromCard({ ...PHOTOS, vimeo: RUTUBE });
    const playing = g.state();
    g.videoButton().click();                // «К фото»
    return { playing, back: g.state() };
  },
  card_play_unsafe() {
    const g = load();
    g.openFromCard({ ...PHOTOS, vimeo: "https://rutube.ru/video/c6cc4d620b1d4338901770a44b3e82f4/" });
    return g.state();
  },
  unsafe_vimeo() {
    // Данные карточки подменены: плеер всё равно не создаётся — адрес проверяется и в браузере
    const out = {};
    for (const bad of ["https://evil.example/video/1", "javascript:alert(1)",
      "https://player.vimeo.com/video/1?h=x\"><script>&dnt=1", "https://player.vimeo.com.evil.example/video/1?dnt=1"]) {
      const g = load();
      g.open({ ...PHOTOS, vimeo: bad });
      const button = g.state().buttonHidden;
      g.videoButton().click();
      out[bad] = { buttonHidden: button, iframes: g.iframes().length };
    }
    return out;
  },
  load_timeout() {
    const g = load();
    g.open({ ...PHOTOS, vimeo: VIMEO });
    g.videoButton().click();
    g.runTimers();                         // плеер так и не загрузился
    const failed = g.state();
    g.videoButton().click();               // «К фото»
    return { failed, back: g.state() };
  },
  load_ok() {
    const g = load();
    g.open({ ...PHOTOS, vimeo: VIMEO });
    g.videoButton().click();
    g.iframes()[0].dispatch("load");
    g.runTimers();
    return g.state();
  },
};

const name = process.argv[2];
if (!scenarios[name]) {
  console.error(`неизвестный сценарий: ${name}`);
  process.exit(2);
}
process.stdout.write(JSON.stringify(scenarios[name]()));
