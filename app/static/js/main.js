"use strict";

(function () {
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function formatNumber(n) {
    return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, " ");
  }

  // Подпись из content/ с метками {name}: значения вставляются как текст, не как HTML
  function fillText(template, values) {
    return String(template).replace(/\{(\w+)\}/g, (match, key) => (key in values ? String(values[key]) : match));
  }

  function closeOnOutsideClick(dialog) {
    dialog.addEventListener("click", (event) => {
      if (event.target !== dialog) return;
      const r = dialog.getBoundingClientRect();
      const inside = event.clientX >= r.left && event.clientX <= r.right &&
        event.clientY >= r.top && event.clientY <= r.bottom;
      if (!inside) dialog.close();
    });
  }

  /* Сцена: фон всей главной. Вверху страницы — собранный стенд (последний кадр).
     Первые полторы высоты экрана прокрутки кадры идут к пустой площадке, дальше до
     низа страницы — сборка; вверх — всё в обратном порядке. Только соседние кадры:
     ракурс камеры меняется вместе с ними, поэтому смешивать кадры нельзя. */

  /* Видео вместо кадров: прокрутка видео не управляет, кадры не грузятся. Остаётся только
     линия монтажа под шапкой — доля прокрутки страницы. Видео без звука, зациклено; скачивается
     и запускается здесь (preload="none" в разметке), поэтому при уменьшении движения
     остаётся poster и 40 МБ не грузятся. Не дал браузер автозапуск — тоже остаётся poster. */
  function initSceneVideo(video) {
    const track = document.querySelector("[data-gauge-track]");
    const root = document.documentElement;
    let queued = false;

    function update() {
      queued = false;
      const range = root.scrollHeight - window.innerHeight;
      const p = range <= 0 ? 1 : Math.min(1, Math.max(0, window.scrollY / range));
      if (track) track.style.setProperty("--progress", p.toFixed(4));
    }

    function request() {
      if (queued) return;
      queued = true;
      requestAnimationFrame(update);
    }

    window.addEventListener("scroll", request, { passive: true });
    window.addEventListener("resize", request);
    if ("ResizeObserver" in window) new ResizeObserver(request).observe(document.body);
    update();

    if (reduceMotion) return;
    video.muted = true;
    video.preload = "auto";
    const started = video.play();
    if (started && typeof started.catch === "function") started.catch(() => {});
  }

  function initScene() {
    const scene = document.querySelector("[data-scene]");
    const video = scene && scene.querySelector("[data-scene-video]");
    if (video) return initSceneVideo(video);
    const canvas = document.querySelector("[data-scene-canvas]");
    if (!scene || !canvas) return;

    const ctx = canvas.getContext("2d");
    // Узкий экран — облегчённые кадры, если они есть (выбор один раз, при загрузке)
    const narrow = window.matchMedia("(max-width: 859px)").matches;
    const base = (narrow && scene.dataset.framesBaseMobile) || scene.dataset.framesBase;
    const count = Number(scene.dataset.frameCount) || 0;
    const track = document.querySelector("[data-gauge-track]");
    const root = document.documentElement;
    const frames = new Array(count);
    // Плавность — по положению прокрутки (px), а кадр считается из него: так при
    // быстрой прокрутке путь по кадрам не срезает излом «последний → 0 → последний».
    // Разборка (сборка назад) — за первые 0,75 экрана; сразу за ней — сборка вперёд до низа страницы.
    const REWIND_SCREENS = 0.75;
    let current = 0;
    let target = 0;
    let drawn = -1;
    let running = false;
    if (!count) return;

    // Петля pre-video.mp4 над холстом: в самом верху видна целиком, за первую четверть экрана
    // прокрутки растворяется — под ней кадры идут как прежде; вернулись наверх — видна и снова играет.
    // То же у низа страницы: за последнюю четверть экрана кадры (там уже готовый стенд в ракурсе петли)
    // растворяются в ту же петлю, внизу она играет, выше — снова кадры. Один и тот же <video>.
    // Файл грузится, только когда петля впервые нужна; при «экономии трафика» её нет — прежняя сцена.
    // Не дал браузер автозапуск — остаётся poster, кадры под ним те же
    const LOOP_SCREENS = 0.25;
    const loop = scene.querySelector("[data-scene-loop]");
    const saveData = !!(navigator.connection && navigator.connection.saveData);
    const hasLoop = !!(loop && loop.dataset.src) && !saveData;
    let loopShown = -1;
    let loopVisible = false;

    function updateLoop(y) {
      if (!hasLoop) return;
      const fade = window.innerHeight * LOOP_SCREENS;
      const range = root.scrollHeight - window.innerHeight;
      // До ближайшего края страницы, верха или низа; допуск 1 px — прокрутка у края бывает дробной
      const near = Math.max(0, Math.min(y, range - y) - 1);
      const shown = Math.round(Math.min(1, Math.max(0, 1 - near / fade)) * 1000) / 1000;
      if (shown === loopShown) return;
      loopShown = shown;
      loop.style.opacity = String(shown);
      if (shown > 0 === loopVisible) return;
      loopVisible = shown > 0;
      if (!loopVisible) return loop.pause();
      if (!loop.hasAttribute("src")) loop.src = loop.dataset.src;
      const started = loop.play();
      if (started && typeof started.catch === "function") started.catch(() => {});
    }

    function src(i) {
      return base + "frame-" + String(i).padStart(3, "0") + ".webp";
    }

    // Кадр ещё не загружен — показываем ближайший загруженный.
    function nearestLoaded(i) {
      for (let d = 0; d < count; d++) {
        if (frames[i - d] && frames[i - d].ready) return i - d;
        if (frames[i + d] && frames[i + d].ready) return i + d;
      }
      return -1;
    }

    function draw(i) {
      const index = nearestLoaded(i);
      if (index < 0 || index === drawn) return;
      ctx.drawImage(frames[index].img, 0, 0, canvas.width, canvas.height);
      drawn = index;
    }

    function load(i) {
      return new Promise((resolve) => {
        if (frames[i]) return resolve();
        const img = new Image();
        frames[i] = { img, ready: false };
        img.decoding = "async";
        img.onload = () => {
          frames[i].ready = true;
          // Холст — размера кадра (у кадров для компьютера и телефона он разный)
          if (img.naturalWidth && canvas.width !== img.naturalWidth) {
            canvas.width = img.naturalWidth;
            canvas.height = img.naturalHeight;
            drawn = -1;
          }
          // Кадр ближе к нужному, чем уже нарисованный, — перерисовать.
          const want = frameAt(current);
          if (drawn < 0 || Math.abs(i - want) < Math.abs(drawn - want)) {
            drawn = -1;
            draw(want);
          }
          resolve();
        };
        img.onerror = resolve;
        img.src = src(i);
      });
    }

    // Линия — по реальной прокрутке (p): не отстаёт от страницы ни вниз, ни вверх.
    function updateGauge(p) {
      if (track) track.style.setProperty("--progress", p.toFixed(4));
    }

    if (reduceMotion) {
      load(count - 1).then(() => draw(count - 1));
      updateGauge(1);
      return;
    }

    async function loadAll() {
      // Первым — кадр для текущего положения (вверху страницы — собранный стенд).
      await load(frameAt(window.scrollY));
      // Затем каждый 8-й кадр — грубая сборка доступна быстро, потом промежуточные.
      const order = [];
      for (let i = 0; i < count; i += 8) order.push(i);
      order.push(count - 1);
      for (let i = 1; i < count; i++) if (i % 8) order.push(i);
      let next = 0;
      const worker = async () => {
        while (next < order.length) await load(order[next++]);
      };
      await Promise.all([worker(), worker(), worker(), worker()]);
    }

    // Доля прокрутки всей страницы: 0 — самый верх, 1 — самый низ.
    function progress() {
      const range = root.scrollHeight - window.innerHeight;
      if (range <= 0) return 1;
      return Math.min(1, Math.max(0, window.scrollY / range));
    }

    // Положение прокрутки → кадр. Разборка — первые 0,75 экрана (на короткой странице —
    // не больше четверти прокрутки): кадры «последний → 0»; остальная прокрутка — сборка
    // «0 → последний», без паузы между ними. В точке излома оба участка дают кадр 0 — переход непрерывный.
    function frameAt(y) {
      const last = count - 1;
      const range = root.scrollHeight - window.innerHeight;
      if (range <= 0) return last;
      const rewind = Math.min(window.innerHeight * REWIND_SCREENS, range / 4);
      const at = Math.min(range, Math.max(0, y));
      const f = at <= rewind ? (1 - at / rewind) * last : ((at - rewind) / (range - rewind)) * last;
      return Math.round(f);
    }

    // Кадр догоняет прокрутку плавно; цикл останавливается, когда догнал.
    function tick() {
      const p = progress();
      target = window.scrollY;
      current += (target - current) * 0.18;
      if (Math.abs(target - current) < 0.5) current = target;
      draw(frameAt(current));
      updateLoop(current);
      updateGauge(p);
      if (current !== target) {
        requestAnimationFrame(tick);
      } else {
        running = false;
      }
    }

    function schedule() {
      if (running) return;
      running = true;
      requestAnimationFrame(tick);
    }

    loadAll();
    window.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule);
    // Высота страницы меняется (шрифты, фото) — пересчитать прогресс.
    if ("ResizeObserver" in window) new ResizeObserver(schedule).observe(document.body);
    current = window.scrollY;
    schedule();
  }

  /* Шапка: прозрачная над первым экраном, светлая после него */

  function initHeader() {
    const header = document.querySelector("[data-header]");
    const hero = document.querySelector("[data-hero]");
    if (!header) return;
    if (!hero) {
      header.classList.add("is-solid");
      return;
    }

    const copy = hero.querySelector(".hero-copy") || hero;
    const gauge = document.querySelector("[data-gauge]");

    // Плотной шапка становится, как только текст первого экрана доходит до неё,
    // иначе заголовок проезжает сквозь прозрачную шапку и логотип
    function update() {
      const edge = header.offsetHeight + (gauge ? gauge.offsetHeight : 0);
      header.classList.toggle("is-solid", copy.getBoundingClientRect().top <= edge);
    }

    window.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update);
    update();
  }

  /* Телефон и email в шапке: текст можно выделить мышью и скопировать (user-select в style.css).
     Отпущенная после выделения кнопка — это ещё и щелчок по ссылке: без проверки он открыл бы
     звонок или почту. Переход отменяется только если внутри ссылки сейчас выделен текст;
     обычный щелчок, выделение и копирование не меняются. */

  function initHeaderContacts() {
    document.querySelectorAll(".header-contacts .header-phone").forEach((link) => {
      link.addEventListener("click", (event) => {
        const selection = window.getSelection();
        if (selection && !selection.isCollapsed && link.contains(selection.anchorNode)) event.preventDefault();
      });
    });
  }

  /* Email в шапке: щелчок (касание, Enter) по адресу открывает маленькое меню «Написать письмо» /
     «Скопировать адрес»; ни письмо, ни копирование до выбора пункта не происходят. Повторный щелчок,
     щелчок мимо, Esc и уход фокуса из меню (Tab) закрывают его; после копирования меню закрывается само.
     Адрес — видимый текст ссылки (из content/contacts.json). Копирование — Clipboard API,
     без него — legacyCopy, а если и это нельзя — адрес выделяется для Cmd/Ctrl+C.
     Выделили текст почты мышью — щелчок меню не открывает (как у телефона: выделение и копирование).
     Значок «скопировать» рядом с адресом копирует сам (initCopy) и меню не открывает; открытое — закрывает. */

  function initEmailMenu() {
    const wrap = document.querySelector(".header-email-wrap");
    const link = wrap && wrap.querySelector(".header-email");
    const menu = wrap && wrap.querySelector("[data-email-menu]");
    if (!link || !menu) return;
    const write = menu.querySelector("[data-email-write]");
    const copyButton = menu.querySelector("[data-email-copy]");
    const status = wrap.querySelector("[data-email-status]");
    const label = copyButton.textContent;
    let timer = 0;

    // Меню прячется классом, а не hidden: так работает плавное появление (style.css)
    menu.hidden = false;

    const isOpen = () => menu.classList.contains("is-open");

    function setOpen(open, focusFirst) {
      clearTimeout(timer);
      copyButton.textContent = label;
      if (open) status.textContent = "";
      // Фокус был в меню — возвращаем на адрес, чтобы он не потерялся в скрытом меню
      if (!open && menu.contains(document.activeElement)) link.focus();
      menu.classList.toggle("is-open", open);
      link.setAttribute("aria-expanded", String(open));
      if (open && focusFirst) write.focus();
    }

    function done(text) {
      status.textContent = text;
      copyButton.textContent = text;
      timer = setTimeout(() => setOpen(false), 1200);
    }

    link.addEventListener("click", (event) => {
      event.preventDefault();
      const selection = window.getSelection();
      if (selection && !selection.isCollapsed && link.contains(selection.anchorNode)) return;
      // С клавиатуры (detail 0) фокус сразу на первый пункт
      setOpen(!isOpen(), event.detail === 0);
    });

    copyButton.addEventListener("click", () => {
      const address = link.textContent.trim();
      const copied = () => done("Адрес скопирован");
      const fallback = () => {
        if (legacyCopy(link)) {
          window.getSelection().removeAllRanges();
          copied();
        } else {
          select(link);
          done("Адрес выделен — нажмите ⌘C или Ctrl+C");
        }
      };
      if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(address).then(copied, fallback);
      else fallback();
    });

    // «Написать письмо» — ссылка mailto: открывает почтовую программу сама; меню просто закрывается
    write.addEventListener("click", () => setOpen(false));

    const icon = wrap.querySelector("[data-copy]");
    if (icon) icon.addEventListener("click", () => { if (isOpen()) setOpen(false); });

    document.addEventListener("click", (event) => {
      if (isOpen() && !wrap.contains(event.target)) setOpen(false);
    });

    // Tab увёл фокус за пределы адреса и меню — меню закрывается
    wrap.addEventListener("focusout", (event) => {
      if (isOpen() && event.relatedTarget && !wrap.contains(event.relatedTarget)) setOpen(false);
    });

    // Esc закрывает сначала меню почты, а мобильное меню — только следующим нажатием
    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || !isOpen()) return;
      event.stopPropagation();
      setOpen(false);
      link.focus();
    }, true);
  }

  /* Меню плавающей кнопки «Связаться» — кнопка видна всегда (<details>: открывается и без JS, Enter и пробел — сами).
     Закрывается щелчком мимо, Esc (фокус — на кнопку), выбором пункта и уходом фокуса из меню (Tab). */

  function initFabMenu() {
    const fab = document.querySelector("[data-fab]");
    if (!fab) return;
    const toggle = fab.querySelector("[data-fab-toggle]");

    // Кнопка — раскрывающийся блок: читалкам — развёрнут ли он
    function sync() {
      toggle.setAttribute("aria-expanded", String(fab.open));
    }

    fab.addEventListener("toggle", sync);
    document.addEventListener("click", (event) => {
      if (fab.open && !fab.contains(event.target)) fab.open = false;
    });
    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || !fab.open) return;
      event.stopPropagation();
      fab.open = false;
      toggle.focus();
    }, true);
    // Фокус ушёл из меню — на другой элемент или за пределы страницы (тогда relatedTarget пуст):
    // проверяем, где он оказался, когда переход закончился. Переключение окна activeElement не меняет —
    // меню тогда остаётся открытым
    fab.addEventListener("focusout", () => {
      setTimeout(() => {
        if (fab.open && !fab.contains(document.activeElement)) fab.open = false;
      });
    });
    // Звонок или мессенджер выбран — меню больше не нужно (ссылки мессенджеров открываются в новой вкладке)
    fab.addEventListener("click", (event) => {
      if (event.target.closest("[data-fab-item]")) fab.open = false;
    });
    sync();
  }

  /* Мобильное меню */

  function initMenu() {
    const toggle = document.querySelector("[data-menu-toggle]");
    const nav = document.querySelector("[data-nav]");
    if (!toggle || !nav) return;

    function setOpen(open) {
      toggle.setAttribute("aria-expanded", String(open));
      nav.classList.toggle("is-open", open);
    }

    toggle.addEventListener("click", () => {
      setOpen(toggle.getAttribute("aria-expanded") !== "true");
    });

    // Переход отменён (выделили телефон или email в меню) — меню остаётся открытым, выделение видно
    nav.addEventListener("click", (event) => {
      if (event.target.closest("a") && !event.defaultPrevented) setOpen(false);
    });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && nav.classList.contains("is-open")) {
        setOpen(false);
        toggle.focus();
      }
    });

    document.addEventListener("click", (event) => {
      if (nav.classList.contains("is-open") && !event.target.closest("[data-nav], [data-menu-toggle]")) {
        setOpen(false);
      }
    });
  }

  /* Шторки с собственными адресами: /portfolio, /about и другие разделы из content/pages.json */

  function initSheets() {
    const sheets = document.querySelectorAll("[data-sheet]");
    const backdrop = document.querySelector("[data-backdrop]");
    // Плавающая кнопка «Связаться» — не фон: она остаётся доступной и над шторкой
    const background = document.querySelectorAll("main, .site-footer");
    const homeLink = document.querySelector('[data-nav] a[data-route="home"]');
    const homeUrl = homeLink ? homeLink.getAttribute("href") : "/";
    if (!sheets.length) return;

    const names = Array.from(sheets, (sheet) => sheet.id);
    let lastFocus = null;

    function sheetFromPath(pathname) {
      const last = pathname.replace(/\/+$/, "").split("/").pop();
      return names.includes(last) ? last : null;
    }

    function show(name, focus) {
      sheets.forEach((sheet) => sheet.classList.toggle("is-open", sheet.id === name));
      document.body.classList.toggle("has-sheet", Boolean(name));
      if (backdrop) backdrop.hidden = !name;
      background.forEach((el) => { el.inert = Boolean(name); });

      document.querySelectorAll("[data-nav] a[data-route]").forEach((link) => {
        const active = link.dataset.route === (name || "home");
        if (active) link.setAttribute("aria-current", "page");
        else link.removeAttribute("aria-current");
      });

      if (name && focus) {
        document.getElementById(name).focus({ preventScroll: true });
      } else if (!name && lastFocus && document.contains(lastFocus)) {
        lastFocus.focus({ preventScroll: true });
        lastFocus = null;
      }
    }

    function navigate(name, url) {
      const open = document.querySelector("[data-sheet].is-open");
      if (name && !open) lastFocus = document.activeElement;
      history.pushState({ sheet: name }, "", url);
      show(name, true);
    }

    document.addEventListener("click", (event) => {
      const link = event.target.closest("a[data-route]");
      if (!link || event.defaultPrevented || event.button !== 0 ||
          event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const route = link.dataset.route;
      const name = names.includes(route) ? route : null;
      const isOpen = document.querySelector("[data-sheet].is-open");
      if (!name && !isOpen) return; // «Главная» без открытой шторки — обычный переход
      event.preventDefault();
      navigate(name, link.getAttribute("href"));
    });

    if (backdrop) {
      backdrop.addEventListener("click", () => navigate(null, homeUrl));
    }

    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || document.querySelector("dialog[open]")) return;
      if (document.querySelector("[data-sheet].is-open")) navigate(null, homeUrl);
    });

    window.addEventListener("popstate", () => {
      show(sheetFromPath(location.pathname), true);
    });

    // Старые ссылки вида /#portfolio из прежней версии сайта
    const legacy = location.hash.slice(1);
    if (names.includes(legacy) && !sheetFromPath(location.pathname)) {
      const link = document.querySelector('[data-nav] a[data-route="' + legacy + '"]');
      if (link) {
        history.replaceState({ sheet: legacy }, "", link.getAttribute("href"));
        lastFocus = document.body;
        show(legacy, true);
      }
    } else {
      const initial = sheetFromPath(location.pathname);
      if (initial) show(initial, true);
    }
  }

  /* Портфолио: вкладки по годам */

  function initYearTabs() {
    const list = document.querySelector("[data-year-tabs]");
    if (!list) return;
    const tabs = Array.from(list.querySelectorAll('[role="tab"]'));

    function select(tab, focus) {
      tabs.forEach((t) => {
        const selected = t === tab;
        t.setAttribute("aria-selected", String(selected));
        t.tabIndex = selected ? 0 : -1;
        const panel = document.getElementById(t.getAttribute("aria-controls"));
        if (panel) panel.hidden = !selected;
      });
      if (focus) tab.focus();
      tab.scrollIntoView({ block: "nearest", inline: "nearest" });
    }

    list.addEventListener("click", (event) => {
      const tab = event.target.closest('[role="tab"]');
      if (tab) select(tab, false);
    });

    list.addEventListener("keydown", (event) => {
      const index = tabs.indexOf(document.activeElement);
      if (index < 0) return;
      let next = null;
      if (event.key === "ArrowRight") next = tabs[(index + 1) % tabs.length];
      else if (event.key === "ArrowLeft") next = tabs[(index - 1 + tabs.length) % tabs.length];
      else if (event.key === "Home") next = tabs[0];
      else if (event.key === "End") next = tabs[tabs.length - 1];
      if (next) {
        event.preventDefault();
        select(next, true);
      }
    });
  }

  /* Галерея проекта */

  function initGallery() {
    const dialog = document.getElementById("gallery");
    if (!dialog || typeof dialog.showModal !== "function") return;

    const title = dialog.querySelector("[data-gallery-title]");
    const meta = dialog.querySelector("[data-gallery-meta]");
    const description = dialog.querySelector("[data-gallery-description]");
    const specs = dialog.querySelector("[data-gallery-specs]");
    const image = dialog.querySelector("[data-gallery-image]");
    const backdrop = dialog.querySelector("[data-gallery-backdrop]");
    const stage = image.parentNode;
    const thumbs = dialog.querySelector("[data-gallery-thumbs]");
    const video = dialog.querySelector("[data-gallery-video]");
    const videoError = dialog.querySelector("[data-gallery-video-error]");
    const steps = dialog.querySelectorAll("[data-gallery-step]");
    // Плеер — только адрес, собранный сервером (content.video_player_url): Vimeo (номер и hash)
    // или Rutube (код видео и ключ закрытого видео)
    const PLAYER_URL = new RegExp("^(?:https://player\\.vimeo\\.com/video/[0-9]+\\?(?:h=[0-9a-f]+&)?dnt=1" +
      "|https://rutube\\.ru/play/embed/[0-9a-f]{32}/(?:\\?p=[A-Za-z0-9_-]{6,64})?)$");
    const PLAYER_TIMEOUT = 10000;
    let data = null;
    let index = 0;
    let player = null;
    let playerTimer = 0;

    /* Видео (Vimeo или Rutube): iframe создаётся только по нажатию «Смотреть видео» и удаляется при возврате
       к фото, переходе к другому стенду и закрытии окна — воспроизведение останавливается,
       до нажатия к сервису видео нет ни одного запроса. Автозапуска нет: видео включают в плеере. */
    function stopVideo() {
      clearTimeout(playerTimer);
      if (player) player.remove();
      player = null;
      image.hidden = false;
      // Стрелки фото возвращаются (если фото больше одного)
      steps.forEach((b) => { b.hidden = !(data && data.images.length > 1); });
      if (backdrop) backdrop.hidden = false;
      videoError.hidden = true;
      video.textContent = dialog.dataset.videoLabel || "Смотреть видео";
    }

    function playVideo() {
      const src = data && data.vimeo;
      if (!PLAYER_URL.test(src || "")) return;
      stopVideo();
      player = document.createElement("iframe");
      player.className = "gallery-player";
      player.title = fillText(dialog.dataset.videoTitle || "{title} — видео", { title: data.title || "" });
      player.loading = "lazy";
      // Как в коде встраивания Vimeo, но без autoplay; полноэкранный режим — через allowfullscreen
      player.allow = "encrypted-media; picture-in-picture";
      player.allowFullscreen = true;
      player.referrerPolicy = "strict-origin-when-cross-origin";
      player.addEventListener("load", () => clearTimeout(playerTimer));
      // Плеер не загрузился (нет сети, видео недоступно) — сообщение и путь назад к фото
      playerTimer = setTimeout(() => {
        if (player) player.remove();
        player = null;
        videoError.hidden = false;
      }, PLAYER_TIMEOUT);
      player.src = src;
      image.hidden = true;
      // Стрелки фото не лежат поверх плеера и его кнопок; к фото — «К фото», лента или ←/→
      steps.forEach((b) => { b.hidden = true; });
      if (backdrop) backdrop.hidden = true;
      image.after(player);
      // В ленте отмечена плитка видео
      thumbs.querySelectorAll("button").forEach((b) => {
        b.setAttribute("aria-current", String("videoTile" in b.dataset));
      });
      video.textContent = dialog.dataset.photosLabel || "К фото";
    }

    function watching() {
      return Boolean(player) || !videoError.hidden;
    }

    /* Без чёрных полос. Сцена одного размера для всех фото (style.css: 3 : 2, не выше 62svh), фото
       заполняет её (cover), если так срезается не больше MAX_CROP снимка; иначе показывается целиком
       (contain) на фоне своей же размытой копии. Решается по размерам после загрузки и при смене окна. */
    const MAX_CROP = 0.2;

    function fitFor(width, height, boxWidth, boxHeight) {
      if (!width || !height || !boxWidth || !boxHeight) return "contain";
      const photo = width / height;
      const box = boxWidth / boxHeight;
      return 1 - Math.min(photo, box) / Math.max(photo, box) <= MAX_CROP ? "cover" : "contain";
    }

    function applyFit() {
      if (!image.naturalWidth) return;
      const box = stage.getBoundingClientRect();
      const fit = fitFor(image.naturalWidth, image.naturalHeight, box.width, box.height);
      stage.dataset.fit = fit;
      if (!backdrop) return;
      if (fit === "contain") backdrop.src = image.currentSrc || image.src;   // уже загружено — из кеша
      else backdrop.removeAttribute("src");
    }

    function clearFit() {
      delete stage.dataset.fit;
      if (backdrop) backdrop.removeAttribute("src");
    }

    function show(i) {
      const total = data.images.length;
      if (!total) return;
      stopVideo();
      index = (i + total) % total;
      // Пока новое фото грузится, оно скрыто: иначе видно прежнее (другого стенда или предыдущее)
      // и оно подменяется на глазах. Место под фото зарезервировано, проявление — только opacity
      image.classList.add("is-loading");
      clearFit();                     // и подложка прежнего фото не остаётся, пока грузится новое
      // Версии 640/1280/1920: браузер выбирает по ширине окна и плотности экрана; нет версий — только src
      const srcset = Array.isArray(data.srcsets) ? data.srcsets[index] : "";
      if (srcset) image.srcset = srcset;
      else image.removeAttribute("srcset");
      image.src = data.images[index];
      if (image.complete && image.naturalWidth) {                                        // уже в кеше
        applyFit();
        image.classList.remove("is-loading");
      }
      image.alt = fillText(dialog.dataset.photoAlt || "{title}, фото {n} из {total}", { title: data.title, n: index + 1, total });
      thumbs.querySelectorAll("button").forEach((b, n) => {
        b.setAttribute("aria-current", String(n === index));
      });
    }

    function open(payload) {
      data = payload;
      title.textContent = data.title || "";
      meta.textContent = data.meta || "";
      if (description) {
        description.textContent = data.description || "";
        description.hidden = !data.description;
      }
      if (specs) {
        const list = Array.isArray(data.specs) ? data.specs : [];
        specs.replaceChildren(...list.map((spec) => {
          const row = document.createElement("div");
          const dt = document.createElement("dt");
          const dd = document.createElement("dd");
          dt.textContent = spec.label;
          dd.textContent = spec.value;
          row.append(dt, dd);
          return row;
        }));
        specs.hidden = !list.length;
      }
      thumbs.replaceChildren();
      const many = data.images.length > 1;
      // Видео — отдельный элемент ленты (плитка после фото), только у стенда с корректной ссылкой
      const hasVideo = PLAYER_URL.test(data.vimeo || "");
      steps.forEach((b) => { b.hidden = !many; });
      thumbs.hidden = !many && !hasVideo;
      if (many || hasVideo) {
        data.images.forEach((url, n) => {
          const button = document.createElement("button");
          button.type = "button";
          button.className = "gallery-thumb";
          button.setAttribute("aria-label", fillText(dialog.dataset.photoThumb || "Фото {n}", { n: n + 1 }));
          const img = document.createElement("img");
          // Лента миниатюр — облегчённые версии, если они есть
          img.src = (Array.isArray(data.thumbs) && data.thumbs[n]) || url;
          img.alt = "";
          img.loading = "lazy";
          button.append(img);
          button.addEventListener("click", () => show(n));
          thumbs.append(button);
        });
      }
      if (hasVideo) {
        // Лёгкое превью: первое фото стенда со значком воспроизведения; плеер — только по нажатию
        const tile = document.createElement("button");
        tile.type = "button";
        tile.className = "gallery-thumb gallery-thumb-video";
        tile.setAttribute("data-video-tile", "");
        tile.setAttribute("aria-label", dialog.dataset.videoThumb || "Видео");
        tile.setAttribute("aria-current", "false");
        const preview = (Array.isArray(data.thumbs) && data.thumbs[0]) || data.images[0];
        if (preview) {
          const img = document.createElement("img");
          img.src = preview;
          img.alt = "";
          img.loading = "lazy";
          tile.append(img);
        }
        const icon = document.createElement("span");
        icon.className = "gallery-thumb-play";
        icon.setAttribute("aria-hidden", "true");
        tile.append(icon);
        tile.addEventListener("click", playVideo);
        thumbs.append(tile);
      }
      // Кнопка видео — только у стенда с корректной ссылкой Vimeo или Rutube; без видео окно как раньше
      video.hidden = !hasVideo;
      show(0);
      dialog.showModal();
    }

    // Карточка открывает галерею; «Смотреть видео» под карточкой — ту же галерею сразу с плеером
    // (видео смотрят на сайте, без перехода на Rutube или Vimeo)
    document.addEventListener("click", (event) => {
      const play = event.target.closest("[data-play-video]");
      const card = play && play.closest("[data-stand]");
      const trigger = play ? card && card.querySelector("[data-gallery]") : event.target.closest("[data-gallery]");
      if (!trigger) return;
      try {
        open(JSON.parse(trigger.dataset.gallery));
      } catch (err) {
        // Повреждённые данные карточки — галерею не открываем.
        return;
      }
      if (play) playVideo();
    });

    steps.forEach((button) => {
      button.addEventListener("click", () => show(index + Number(button.dataset.galleryStep)));
    });

    // «Смотреть видео» ↔ «К фото»
    video.addEventListener("click", () => (watching() ? show(index) : playVideo()));

    dialog.addEventListener("keydown", (event) => {
      if (event.target.closest("input, textarea")) return;
      if (event.key === "ArrowRight") show(index + 1);
      if (event.key === "ArrowLeft") show(index - 1);
    });

    // Фото проявляется, когда загрузилось; при ошибке тоже снимаем скрытие — видна подпись alt
    image.addEventListener("load", () => {
      applyFit();
      image.classList.remove("is-loading");
    });
    // Окно меняет размер (поворот телефона) — пропорции сцены другие, решение пересчитывается
    window.addEventListener("resize", () => {
      if (dialog.open) applyFit();
    });
    image.addEventListener("error", () => image.classList.remove("is-loading"));

    dialog.addEventListener("close", () => {
      stopVideo();
      // Снять и srcset: с одним src картинка продолжала показывать фото закрытого стенда
      image.removeAttribute("srcset");
      image.removeAttribute("src");
      clearFit();
    });

    closeOnOutsideClick(dialog);
  }

  /* Заявка. Серверной отправки нет: если в content/site.json указан email,
     открывается почтовая программа посетителя, иначе форма сообщает, что
     онлайн-отправка не подключена. */

  function initRequest() {
    const dialog = document.getElementById("request");
    const form = document.querySelector("[data-request-form]");
    if (!dialog || !form || typeof dialog.showModal !== "function") return;

    const status = form.querySelector("[data-request-status]");
    const email = form.dataset.email || "";

    function setStatus(text) {
      if (!status) return;
      status.textContent = text;
      status.hidden = !text;
    }

    function open() {
      setStatus("");
      if (!dialog.open) dialog.showModal();
      const name = form.elements.name;
      if (name) name.focus();
    }

    document.addEventListener("click", (event) => {
      if (event.target.closest("[data-open-request]")) open();
    });

    document.querySelectorAll("[data-close-dialog]").forEach((button) => {
      button.addEventListener("click", () => button.closest("dialog").close());
    });

    form.addEventListener("submit", (event) => {
      event.preventDefault();
      if (!form.reportValidity()) return;
      const name = form.elements.name.value.trim();
      const contact = form.elements.contact.value.trim();
      const message = form.elements.message.value.trim();
      const body = [
        (form.dataset.bodyName || "Имя") + ": " + name,
        (form.dataset.bodyContact || "Контакт") + ": " + contact,
        message ? "\n" + message : "",
      ].join("\n").trim();
      if (!email) {
        setStatus(form.dataset.offlineMessage || "Онлайн-отправка заявок пока не подключена.");
        return;
      }
      const subject = form.dataset.subject || "Заявка";
      window.location.href = "mailto:" + encodeURIComponent(email).replace("%40", "@") +
        "?subject=" + encodeURIComponent(subject) + "&body=" + encodeURIComponent(body);
      dialog.close();
    });

    closeOnOutsideClick(dialog);

    window.CRVU = window.CRVU || {};
    window.CRVU.openRequest = function (message) {
      if (typeof message === "string") form.elements.message.value = message;
      open();
    };
  }

  /* Копирование почты. Адрес берётся из видимого текста рядом с кнопкой (его выводит сервер
     из content/contacts.json), поэтому в JS ничего не зашито. «Скопировано» — только после
     успешной записи в буфер; если копирование недоступно, адрес выделяется для Cmd/Ctrl+C. */

  function select(node) {
    const range = document.createRange();
    range.selectNodeContents(node);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
  }

  // Запасной путь без Clipboard API (не HTTPS, старый браузер): выделить и скопировать командой
  function legacyCopy(node) {
    select(node);
    try {
      return document.execCommand("copy");
    } catch (error) {
      return false;
    }
  }

  function initCopy() {
    const copied = "Скопировано";
    const manual = "Адрес выделен — нажмите Ctrl+C или ⌘C";

    document.querySelectorAll("[data-copy]").forEach((button) => {
      // Почта в «Контактах» и подвале (.copy-field) и значок у адреса в шапке (data-copy-field)
      const field = button.closest(".copy-field, [data-copy-field]");
      const source = field && field.querySelector("[data-copy-source]");
      const status = field && field.querySelector("[data-copy-status]");
      if (!source || !status) return;
      button.hidden = false;
      let timer = 0;

      function say(text) {
        status.textContent = text;
        clearTimeout(timer);
        timer = setTimeout(() => { status.textContent = ""; }, 2500);
      }

      function fallback() {
        if (legacyCopy(source)) say(copied);
        else {
          select(source);
          say(manual);
        }
      }

      button.addEventListener("click", () => {
        const text = source.textContent.trim();
        if (navigator.clipboard && window.isSecureContext) {
          navigator.clipboard.writeText(text).then(() => say(copied), fallback);
        } else {
          fallback();
        }
      });
    });
  }

  /* Заявка на дизайн-проект из конфигуратора (окно #design-request). Одно поле — телефон или
     email; параметры стенда берутся из конфигуратора в момент отправки (getStand), сервер
     проверяет всё заново (/request/design). «Спасибо» — только после ответа сервера ok;
     при ошибке введённое остаётся в форме, кнопка на время отправки заблокирована. */

  const CONTACT_MAX = 100;
  const PHONE_RE = /^\+?[0-9 ()\-.]+$/;
  const EMAIL_ATOM = "[^\\s@<>()\\[\\],;:\"\\\\.]+";
  const EMAIL_RE = new RegExp("^" + EMAIL_ATOM + "(?:\\." + EMAIL_ATOM + ")*@(?:" + EMAIL_ATOM + "\\.)+\\p{L}{2,}$", "u");

  // Те же правила, что на сервере (leads.check_contact): код ошибки или "" — всё верно
  function contactError(raw) {
    const value = String(raw || "").trim().replace(/\s+/g, " ");
    if (!value) return "empty";
    if (value.length > CONTACT_MAX) return "long";
    if (value.includes("@")) {
      return EMAIL_RE.test(value) && value.split("@")[0].length <= 64 ? "" : "email";
    }
    const digits = (value.match(/\d/g) || []).length;
    const open = value.split("(").length - 1;
    const close = value.split(")").length - 1;
    const ok = PHONE_RE.test(value) && digits >= 7 && digits <= 15 &&
      open === close && open <= 1 && value.indexOf("(") <= value.indexOf(")");
    return ok ? "" : "phone";
  }

  function initDesignRequest(getStand) {
    const dialog = document.getElementById("design-request");
    const form = dialog && dialog.querySelector("[data-design-request]");
    if (!form || typeof dialog.showModal !== "function" || !window.fetch) return null;

    let texts = {};
    try {
      texts = JSON.parse(form.dataset.texts || "{}");
    } catch (err) {
      // Повреждённые подписи — сообщения по умолчанию ниже.
    }
    const say = (key, fallback) => (typeof texts[key] === "string" && texts[key] ? texts[key] : fallback);
    const input = form.elements.namedItem("contact");
    const trap = form.elements.namedItem("website");
    const error = form.querySelector("[data-dr-error]");
    const status = form.querySelector("[data-dr-status]");
    const summary = form.querySelector("[data-dr-summary]");
    const submit = form.querySelector("[data-dr-submit]");
    const steps = { form: form.querySelector('[data-dr-step="form"]'), done: form.querySelector('[data-dr-step="done"]') };
    const doneTitle = steps.done.querySelector(".modal-title");
    let opener = null;
    let sending = false;

    function showError(text) {
      error.textContent = text;
      error.hidden = !text;
      if (text) input.setAttribute("aria-invalid", "true");
      else input.removeAttribute("aria-invalid");
    }

    function setStatus(text) {
      status.textContent = text;
      status.hidden = !text;
    }

    function showStep(name) {
      steps.form.hidden = name !== "form";
      steps.done.hidden = name !== "done";
      dialog.setAttribute("aria-labelledby", name === "done" ? doneTitle.id : "dr-title");
    }

    function open(trigger) {
      opener = trigger || document.activeElement;
      showStep("form");
      setStatus("");
      summary.textContent = getStand().summary;
      if (!dialog.open) dialog.showModal();
      input.focus();
    }

    // Ошибку под полем убираем, как только значение стало верным; неверное не стираем
    input.addEventListener("input", () => {
      if (!error.hidden && !contactError(input.value)) showError("");
    });

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (sending) return;                                   // повторное нажатие во время отправки
      const wrong = contactError(input.value);
      if (wrong) {
        showError(say(wrong, "Проверьте телефон или email."));
        input.focus();
        return;
      }
      showError("");
      setStatus("");
      // Статическая сборка без адреса приёма заявок (data-action нет): не отправляем и не показываем
      // «Спасибо» — только честное сообщение и отсылка к контактам
      if (!form.dataset.action) {
        setStatus(say("unavailable", "Заявка не отправлена: онлайн-отправка на этой версии сайта не работает. Свяжитесь с нами по контактам на сайте."));
        return;
      }
      // Параметры — на момент отправки, а не открытия окна
      const stand = getStand();
      summary.textContent = stand.summary;
      const body = new FormData();
      body.set("contact", input.value.trim());
      body.set("website", trap ? trap.value : "");
      Object.keys(stand.params).forEach((key) => body.set(key, stand.params[key]));

      sending = true;
      const label = submit.textContent;
      submit.disabled = true;
      submit.setAttribute("aria-busy", "true");
      submit.textContent = say("sending", "Отправляем…");
      let result;
      try {
        const response = await fetch(form.dataset.action, {
          method: "POST",
          body,
          credentials: "same-origin",
          headers: { "X-Requested-With": "fetch", Accept: "application/json" },
        });
        const json = (response.headers.get("Content-Type") || "").includes("application/json")
          ? await response.json() : null;
        result = json && typeof json.ok === "boolean" && (response.ok || !json.ok)
          ? json : { ok: false, message: say("failed", "Заявка не отправлена. Попробуйте ещё раз.") };
      } catch (err) {
        result = { ok: false, message: say("offline", "Нет связи с сервером. Попробуйте ещё раз.") };
      }
      sending = false;
      submit.disabled = false;
      submit.removeAttribute("aria-busy");
      submit.textContent = label;

      if (result.ok) {
        input.value = "";
        showStep("done");
        doneTitle.focus();
        return;
      }
      if (result.errors && result.errors.contact) {
        showError(result.errors.contact);
        input.focus();
      } else {
        setStatus(result.message || say("failed", "Заявка не отправлена. Попробуйте ещё раз."));
      }
    });

    // Пока заявка отправляется, окно не закрывается (Escape, фон): ответ сервера не потеряется
    dialog.addEventListener("cancel", (event) => {
      if (sending) event.preventDefault();
    });
    dialog.querySelectorAll("[data-close-dialog]").forEach((button) => {
      button.addEventListener("click", () => { if (!sending) dialog.close(); });
    });
    dialog.addEventListener("click", (event) => {
      if (event.target !== dialog || sending) return;
      const r = dialog.getBoundingClientRect();
      const inside = event.clientX >= r.left && event.clientX <= r.right &&
        event.clientY >= r.top && event.clientY <= r.bottom;
      if (!inside) dialog.close();                           // введённое в поле при этом сохраняется
    });
    dialog.addEventListener("close", () => {
      if (opener && document.contains(opener)) opener.focus();
    });

    return { open };
  }

  /* Конфигуратор стенда. Единое состояние size {length, width} и выбор в форме — из них считаются
     площадь, сетка плана (1 клетка = 1 × 1 м), стены, подписи, ориентир бюджета и текст заявки.
     Цена — только в CRVUStandPrice.estimate (stand-price.js), параметры — data-pricing из home.json. */

  function initConfigurator() {
    const form = document.querySelector("[data-configurator]");
    const plan = window.CRVUStandPlan;
    if (!form || !plan) return;
    const price = window.CRVUStandPrice;

    const box = { width: 320, height: 240, pad: 30 };
    const sizeField = form.querySelector("[data-area-min]");
    const limits = plan.limitsOf({ min: sizeField && sizeField.dataset.areaMin, max: sizeField && sizeField.dataset.areaMax });
    // namedItem: свойство length у коллекции полей — их число, а не поле name="length"
    const inputs = { length: form.elements.namedItem("length"), width: form.elements.namedItem("width") };
    const output = form.querySelector("[data-area-output]");
    const levelText = form.querySelector("[data-level-text]");
    const moreCount = form.querySelector("[data-more-count]");
    const moreHint = moreCount ? moreCount.textContent : "";
    const result = form.querySelector("[data-estimate]");
    const est = result && {
      area: result.querySelector("[data-est-area]"),
      config: result.querySelector("[data-est-config]"),
      rate: result.querySelector("[data-est-rate]"),
      total: result.querySelector("[data-est-total]"),
      warning: result.querySelector("[data-est-warning]"),
    };
    const hint = form.querySelector("[data-size-hint]");
    const floor = document.querySelector("[data-plan-floor]");
    const grid = document.querySelector("[data-plan-grid]");
    const walls = document.querySelector("[data-plan-walls]");
    const lengthLabel = document.querySelector("[data-plan-length]");
    const widthLabel = document.querySelector("[data-plan-width]");
    const caption = document.querySelector("[data-plan-caption]");
    let texts = {};
    try {
      texts = JSON.parse(form.dataset.texts || "{}");
    } catch (err) {
      // Повреждённые подписи — остаются тексты по умолчанию ниже.
    }
    const text = (key, fallback) => (typeof texts[key] === "string" && texts[key] ? texts[key] : fallback);
    let pricing = null;
    try {
      pricing = form.dataset.pricing ? JSON.parse(form.dataset.pricing) : null;
    } catch (err) {
      // Повреждённые параметры цены — ориентир не показывается, остальное работает.
    }

    // Стартовый размер — из полей, которые отрисовал сервер; если он вне пределов — минимальный квадрат
    const start = { length: plan.parseSide(inputs.length.value), width: plan.parseSide(inputs.width.value) };
    let size = start.length && start.width && plan.accepts(start, "length", String(start.length), limits)
      ? start
      : plan.initialSize({ min: limits.areaMin, max: limits.areaMax });

    function state() {
      const checked = form.querySelector('input[name="type"]:checked');
      const level = form.querySelector('input[name="level"]:checked');
      const walls = checked ? (checked.dataset.walls || "").split(" ").filter(Boolean) : [];
      const s = {
        length: size.length,
        width: size.width,
        area: plan.areaOf(size),
        type: checked ? checked.value : "",
        // Закрытые стороны из data-walls: back — задняя, left/right — боковые.
        walls,
        level: level ? level.value : "",
        extras: Array.from(form.querySelectorAll('input[name="extras"]:checked'), (box) => box.value),
      };
      s.estimate = pricing && price
        ? price.estimate({ area: s.area, openSides: 4 - walls.length, level: s.level, extras: s.extras }, pricing)
        : null;
      return s;
    }

    function describe(s) {
      const lines = [plan.fill(text("summary", "{type} стенд, {length} × {width} м, {area} м²"), {
        type: s.type, length: plan.formatSize(s.length), width: plan.formatSize(s.width), area: plan.formatSize(s.area),
      })];
      const e = s.estimate;
      if (e) {
        lines.push(plan.fill(text("level_line", "Уровень: {level}"), { level: e.level }));
        if (e.extras.length) lines.push(plan.fill(text("extras_line", "Опции: {extras}"), { extras: e.extras.join(", ") }));
        lines.push(plan.fill(text("estimate_line", "Ориентир бюджета: {range} (предварительно)"),
          { range: price.formatRange(e.totalMin, e.totalMax) }));
      }
      return lines;
    }

    // Блок ориентира: площадь, конфигурация, ставка за м² и итог — диапазоном; без верхней
    // границы — «от …» и пояснение, почему (инженерный расчёт или уровень «по проекту»)
    function showEstimate(s) {
      if (!result) return;
      const e = s.estimate;
      result.hidden = !e;
      if (!e) return;
      est.area.textContent = plan.fill(text("area_value", "{area} м² ({length} × {width} м)"), {
        area: plan.formatSize(s.area), length: plan.formatSize(s.length), width: plan.formatSize(s.width),
      });
      est.config.textContent = plan.fill(text("config", "{type}, {level}"), { type: s.type, level: e.level }) +
        (e.extras.length ? " + " + e.extras.join(", ") : "");
      est.rate.textContent = price.formatRange(e.rateMin, e.rateMax);
      const total = price.formatRange(e.totalMin, e.totalMax);
      if (est.total.textContent !== total) est.total.textContent = total;
      const warning = e.engineering.length
        ? plan.fill(text("engineering", "{names}: нужен инженерный расчёт, поэтому показана только нижняя граница."),
          { names: e.engineering.join(", ") })
        : e.openEnded ? text("open_ended", "Для этого уровня верхнюю границу определяет проект.") : "";
      est.warning.textContent = warning;
      est.warning.hidden = !warning;
    }

    function setRect(el, r) {
      el.setAttribute("x", r.x);
      el.setAttribute("y", r.y);
      el.setAttribute("width", r.w);
      el.setAttribute("height", r.h);
    }

    function drawPlan(s) {
      if (!floor || !walls) return;
      const l = plan.layout(s.length, s.width, box);
      setRect(floor, l);
      if (grid) grid.setAttribute("d", plan.gridPath(l));
      walls.setAttribute("d", plan.wallPath(l, s.walls));
      if (lengthLabel) {
        lengthLabel.setAttribute("x", l.x + l.w / 2);
        lengthLabel.setAttribute("y", l.y + l.h + 20);
        lengthLabel.textContent = plan.formatSize(s.length) + " м";
      }
      if (widthLabel) {
        const x = l.x + l.w + 20;
        const y = l.y + l.h / 2;
        widthLabel.setAttribute("x", x);
        widthLabel.setAttribute("y", y);
        widthLabel.setAttribute("transform", "rotate(90 " + x + " " + y + ")");
        widthLabel.textContent = plan.formatSize(s.width) + " м";
      }
      if (caption) {
        const note = plan.fill(text("open", "открытых сторон: {n}"), { n: 4 - s.walls.length });
        caption.textContent = plan.fill(text("caption", "Вид сверху: {type} стенд"), { type: s.type.toLowerCase() }) +
          ", " + note;
      }
    }

    // Пределы каждого поля зависят от другого: площадь остаётся в area.min…area.max.
    // На пределе кнопка неактивна: на 1 м «−» не уменьшает размер.
    function syncLimits() {
      ["length", "width"].forEach((side) => {
        const range = plan.sideRange(side === "length" ? size.width : size.length, limits);
        form.querySelectorAll('[data-side="' + side + '"]').forEach((button) => {
          const step = Number(button.dataset.step);
          button.disabled = step < 0 ? size[side] <= range.min : size[side] >= range.max;
        });
      });
    }

    function update() {
      const s = state();
      syncLimits();
      if (output) output.textContent = plan.formatSize(s.area) + " м²";
      const level = form.querySelector('input[name="level"]:checked');
      if (levelText && level) levelText.textContent = level.dataset.text || "";
      if (moreCount) moreCount.textContent = s.extras.length ? plan.fill(text("more_count", "выбрано: {n}"), { n: s.extras.length }) : moreHint;
      showEstimate(s);
      drawPlan(s);
    }

    // Неверные поля: side → причина (min — меньше 1 м, format — не число). Такое значение
    // не исправляется молча: оно остаётся в поле с подсказкой, а план, площадь и итог
    // считаются по последнему верному размеру.
    const invalid = {};

    function showHint() {
      const side = Object.keys(inputs).find((key) => invalid[key]);
      Object.keys(inputs).forEach((key) => {
        if (invalid[key]) inputs[key].setAttribute("aria-invalid", "true");
        else inputs[key].removeAttribute("aria-invalid");
      });
      if (!hint) return;
      const message = !side ? ""
        : invalid[side] === "min" ? text("min_hint", "Минимальный размер — 1 м")
        : text("format_hint", "Введите число, например 2,5");
      if (hint.textContent !== message) hint.textContent = message;
    }

    function setSide(side, next) {
      size = next;
      inputs[side].value = plan.formatSize(size[side]);
      delete invalid[side];
      showHint();
    }

    Object.keys(inputs).forEach((side) => {
      const input = inputs[side];
      // Во время набора принимаются только допустимые числа: пустое поле или «1» на пути к «12»
      // не ломают план — остаётся последнее верное значение.
      input.addEventListener("input", () => {
        if (!plan.accepts(size, side, input.value, limits)) return;
        size = plan.resize(size, side, input.value, limits);
        if (invalid[side]) {
          delete invalid[side];
          showHint();
        }
      });
      // После ввода: меньше 1 м или не число — подсказка; пустое поле возвращает последний
      // верный размер; число больше предела прижимается к нему, как и раньше.
      input.addEventListener("change", () => {
        const error = plan.checkSide(input.value).error;
        if (error === "min" || error === "format") {
          invalid[side] = error;
          showHint();
          return;
        }
        setSide(side, plan.resize(size, side, input.value, limits));
      });
    });

    form.querySelectorAll("[data-step]").forEach((button) => {
      button.addEventListener("click", () => {
        const side = button.dataset.side;
        setSide(side, plan.step(size, side, Number(button.dataset.step), limits));
        update();
      });
    });

    form.addEventListener("input", update);
    form.addEventListener("change", update);

    form.addEventListener("submit", (event) => {
      event.preventDefault();
      // С неверным размером заявка не уходит: фокус — на поле с подсказкой
      const wrong = Object.keys(inputs).find((side) => invalid[side]);
      if (wrong) {
        showHint();
        inputs[wrong].focus();
        return;
      }
      Object.keys(inputs).forEach((side) => { inputs[side].value = plan.formatSize(size[side]); });
      update();
      if (design) {
        design.open(event.submitter || form.querySelector('[type="submit"]'));
        return;
      }
      // Без окна заявки на дизайн-проект — общая форма заявки с готовым текстом, как раньше
      const lines = describe(state());
      const message = text("request", "Хочу дизайн-проект стенда:") + "\n" + lines.join("\n");
      if (window.CRVU && window.CRVU.openRequest) window.CRVU.openRequest(message);
    });

    // Заявка на дизайн-проект получает фактические параметры конфигуратора в момент отправки
    const design = initDesignRequest(() => {
      const s = state();
      return {
        params: Object.assign(
          { length: plan.formatSize(s.length), width: plan.formatSize(s.width), type: s.type },
          s.estimate ? {
            level: s.level,
            extras: s.extras.join(","),
            estimate_min: String(s.estimate.totalMin),
            estimate_max: s.estimate.totalMax === null ? "" : String(s.estimate.totalMax),
          } : {},
        ),
        summary: describe(s).join(". ") + ".",
      };
    });

    update();
  }

  /* Видеоотзывы: карточка до нажатия — ссылка на ролик (RuTube или загруженный файл); по нажатию на её
     месте появляется плеер RuTube (iframe) или <video> с файлом сайта. Адрес плеера собран сервером
     из кода ролика (app/videos.py) и ещё раз проверяется здесь: другие адреса не встраиваются; файл —
     только /media/videos/files/. Играет один ролик: следующий убирает прежний плеер; закрытие шторки
     «Другие видео-отзывы» убирает её плееры. Плеер не загрузился — сообщение (у RuTube — со ссылкой).
     До нажатия к RuTube нет ни одного запроса, а видеофайл не загружается. */

  function initVideoReviews() {
    const PLAYER = /^https:\/\/rutube\.ru\/play\/embed\/[0-9a-f]{32}\/(?:\?p=[A-Za-z0-9_-]{6,64})?$/;
    const FILE = /^\/media\/videos\/files\/[0-9a-f]{32}\.(?:mp4|webm)$/;
    const POSTER = /^\/media\/videos\/(?:covers\/)?[0-9a-f]{32}\.webp$/;
    const LINK = "[data-video-player], [data-video-file]";
    const TIMEOUT = 10000;
    let current = null;

    function reset(media) {
      if (!media) return;
      clearTimeout(media.videoTimer);
      const player = media.querySelector(".review-player");
      if (player) player.remove();
      media.classList.remove("is-playing");
      const link = media.querySelector(LINK);
      const error = media.querySelector("[data-video-error]");
      if (link) link.hidden = false;
      if (error) error.hidden = true;
      if (current === media) current = null;
    }

    function failed(media, player) {
      player.remove();
      media.classList.remove("is-playing");
      const error = media.querySelector("[data-video-error]");
      if (error) error.hidden = false;
    }

    // Загруженный файл: обычный <video> с элементами управления; обложка — постер
    function playFile(link, media) {
      const player = document.createElement("video");
      player.className = "review-player";
      player.controls = true;
      player.playsInline = true;
      player.preload = "auto";
      player.setAttribute("aria-label", (link.dataset.videoTitle || "") + " — видео");
      if (POSTER.test(link.dataset.videoPoster || "")) player.poster = link.dataset.videoPoster;
      player.addEventListener("error", () => failed(media, player));
      player.src = link.dataset.videoFile;
      return player;
    }

    function play(link) {
      const media = link.closest("[data-video]");
      const src = link.dataset.videoPlayer || "";
      const file = link.dataset.videoFile || "";
      if (!media || !(PLAYER.test(src) || FILE.test(file))) return false;
      if (current && current !== media) reset(current);
      reset(media);
      if (file) {
        const video = playFile(link, media);
        link.hidden = true;
        media.classList.add("is-playing");
        media.append(video);
        current = media;
        // Посетитель уже нажал «смотреть»; браузер запретил автозапуск — кнопка воспроизведения в самом плеере
        const started = video.play();
        if (started && started.catch) started.catch(() => {});
        video.focus({ preventScroll: true });
        return true;
      }
      const player = document.createElement("iframe");
      player.className = "review-player";
      player.title = (link.dataset.videoTitle || "") + " — видео";
      player.allow = "autoplay; encrypted-media; fullscreen; picture-in-picture";
      player.referrerPolicy = "strict-origin-when-cross-origin";
      player.addEventListener("load", () => {
        clearTimeout(media.videoTimer);
        // Посетитель уже нажал «смотреть» — просим плеер начать (команда API плеера RuTube;
        // не поддержана — ролик запускается кнопкой в самом плеере)
        try {
          player.contentWindow.postMessage(JSON.stringify({ type: "player:play", data: {} }), "https://rutube.ru");
        } catch (err) {
          // Плеер сам покажет кнопку воспроизведения.
        }
      });
      media.videoTimer = setTimeout(() => failed(media, player), TIMEOUT);
      player.src = src;
      link.hidden = true;
      media.classList.add("is-playing");
      // Нажатая ссылка скрыта — фокус на рамке ролика, а не в чужом iframe: там Esc не дошёл бы до шторки
      media.append(player);
      media.tabIndex = -1;
      media.focus({ preventScroll: true });
      current = media;
      return true;
    }

    document.addEventListener("click", (event) => {
      const link = event.target.closest(LINK);
      // Щелчок уже обработан (предпросмотр Редактора открывает окно выбора видео) — плеер не нужен
      if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      if (play(link)) event.preventDefault();          // иначе — обычная ссылка (RuTube или файл)
    });

    // Шторка открылась — ролик на главной за ней останавливается; закрылась — останавливаются её
    // ролики (плеер убирается, карточка снова кнопка)
    document.querySelectorAll("[data-video-sheet]").forEach((sheet) => {
      new MutationObserver(() => {
        if (sheet.classList.contains("is-open")) {
          if (current && !sheet.contains(current)) reset(current);
        } else {
          sheet.querySelectorAll("[data-video]").forEach(reset);
        }
      }).observe(sheet, { attributes: true, attributeFilter: ["class"] });
    });
  }

  /* Счётчики «Компания в цифрах» */

  function initCounters() {
    const counters = document.querySelectorAll("[data-count]");
    if (!counters.length || reduceMotion || !("IntersectionObserver" in window)) return;

    function run(el) {
      const value = Number(el.dataset.count);
      const suffix = el.dataset.suffix || "";
      const start = performance.now();
      const duration = 1200;
      function frame(now) {
        const t = Math.min(1, (now - start) / duration);
        const eased = 1 - Math.pow(1 - t, 3);
        el.textContent = formatNumber(Math.round(value * eased)) + suffix;
        if (t < 1) requestAnimationFrame(frame);
      }
      requestAnimationFrame(frame);
    }

    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        observer.unobserve(entry.target);
        run(entry.target);
      });
    }, { threshold: 0.6 });

    counters.forEach((el) => observer.observe(el));
  }

  /* Появление блоков при прокрутке. Текст скрывается только классом .reveal-on,
     который ставится здесь, — без скрипта и при reduce всё видно сразу. */

  function initReveal() {
    const items = Array.from(document.querySelectorAll("[data-reveal]"));
    if (!items.length || reduceMotion || !("IntersectionObserver" in window)) return;

    const step = 90;
    const maxDelay = 360;

    // Всё, что уже на экране или выше, показываем сразу — без вспышки при перезагрузке
    const rest = items.filter((el) => {
      if (el.getBoundingClientRect().top < window.innerHeight) {
        el.classList.add("is-in");
        return false;
      }
      return true;
    });

    const observer = new IntersectionObserver((entries) => {
      // Одновременно вошедшие блоки — по порядку в документе с небольшим шагом
      entries
        .filter((entry) => entry.isIntersecting)
        .map((entry) => entry.target)
        .sort((a, b) => (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1))
        .forEach((el, i) => {
          observer.unobserve(el);
          el.style.setProperty("--reveal-delay", Math.min(i * step, maxDelay) + "ms");
          el.classList.add("is-in");
        });
    }, { rootMargin: "0px 0px -12% 0px" });

    rest.forEach((el) => observer.observe(el));
    document.documentElement.classList.add("reveal-on");
  }

  /* Первый экран уходит вместе с прокруткой: значение — функция положения, а не события,
     поэтому вверх и вниз движение одинаковое. Кадр запрашивается только на прокрутку. */

  function initHeroExit() {
    // Значение ставится на сам первый экран: его читают и текст, и разметка зала за ним.
    const hero = document.querySelector("[data-hero]");
    if (!hero || reduceMotion) return;
    let queued = false;

    function update() {
      queued = false;
      const exit = Math.min(1, Math.max(0, window.scrollY / (window.innerHeight * 0.7)));
      hero.style.setProperty("--hero-exit", exit.toFixed(3));
    }

    function request() {
      if (queued) return;
      queued = true;
      requestAnimationFrame(update);
    }

    window.addEventListener("scroll", request, { passive: true });
    window.addEventListener("resize", request);
    update();
  }

  /* Частые вопросы: вся строка вопроса — кнопка; открыт не больше одного ответа, повторное
     нажатие закрывает. Ответы скрываются классом is-ready — без скрипта они видны (style.css) */

  function initFaq() {
    document.querySelectorAll("[data-faq]").forEach((list) => {
      const items = Array.from(list.querySelectorAll(".faq-item"));
      list.classList.add("is-ready");

      function setOpen(open) {
        items.forEach((item) => {
          const button = item.querySelector("[data-faq-toggle]");
          item.classList.toggle("is-open", item === open);
          if (button) button.setAttribute("aria-expanded", String(item === open));
        });
      }

      items.forEach((item) => {
        const button = item.querySelector("[data-faq-toggle]");
        if (!button) return;
        button.addEventListener("click", () => setOpen(item.classList.contains("is-open") ? null : item));
      });
    });
  }

  initScene();
  initHeader();
  initFabMenu();
  initMenu();
  initHeaderContacts();
  initEmailMenu();
  initFaq();
  initSheets();
  initYearTabs();
  initGallery();
  initRequest();
  initCopy();
  initConfigurator();
  initVideoReviews();
  initCounters();

  // Оформление не должно ломать остальное: ошибка здесь оставляет текст видимым
  try {
    initReveal();
    initHeroExit();
  } catch (error) {
    document.documentElement.classList.remove("reveal-on");
  }
})();
