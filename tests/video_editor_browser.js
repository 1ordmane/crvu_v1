"use strict";

/* Окно «Видео для „Нам доверяют свои проекты“» в настоящем Chrome — для tests/test_video_editor_browser.py.

   Chrome без окна управляется по DevTools Protocol через встроенный в Node WebSocket (Node 22+);
   зависимостей нет. Запуск: node video_editor_browser.js <адрес сайта> <порт DevTools> <пароль>.
   Результат — одна строка JSON в stdout: значения шагов сценария, проверяет их pytest. */

const [base, port, password] = process.argv.slice(2);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const page = await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: "PUT" })).json();
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((r) => ws.addEventListener("open", r));
  let id = 0;
  const pending = {};
  const errors = [];
  ws.addEventListener("message", (e) => {
    const m = JSON.parse(e.data);
    if (m.id && pending[m.id]) { pending[m.id](m); delete pending[m.id]; }
    if (m.method === "Runtime.exceptionThrown") {
      const d = m.params.exceptionDetails;
      errors.push(d.exception ? d.exception.description : d.text);
    }
    if (m.method === "Page.javascriptDialogOpening") send("Page.handleJavaScriptDialog", { accept: true });
  });
  function send(method, params = {}) {
    return new Promise((r) => { pending[++id] = r; ws.send(JSON.stringify({ id, method, params })); });
  }
  async function js(expr) {
    const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.result.exceptionDetails) throw new Error(expr.slice(0, 100) + " :: " + JSON.stringify(r.result.exceptionDetails).slice(0, 300));
    return r.result.result.value;
  }
  async function until(expr, ms = 20000) {
    for (let t = 0; t < ms; t += 200) {
      if (await js(expr)) return true;
      await sleep(200);
    }
    return false;
  }
  async function go(url) {
    await send("Page.navigate", { url });
    await sleep(500);
    await until("document.readyState === 'complete'");
  }

  const out = {};
  for (const domain of ["Page", "Runtime"]) await send(domain + ".enable");
  await send("Emulation.setFocusEmulationEnabled", { enabled: true });
  await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });

  await go(base + "/admin/login");
  await js(`document.querySelector('[name=username]').value = 'admin';
            document.querySelector('[name=password]').value = ${JSON.stringify(password)};
            document.querySelector('form').submit(); true`);
  await sleep(800);
  await go(base + "/admin/editor?group=section-reviews");

  // Окно видео — то, где есть форма «+ Добавить видео»; окно проектов («Реализовано») — другое
  const D = "document.querySelector('[data-picker-dialog] [data-video-adder]').closest('[data-picker-dialog]')";
  const P = `${D}.closest('[data-picker]')`;
  const input = `${P}.querySelector('[data-picker-input]')`;
  const chosen = () => js(`Array.from(${D}.querySelectorAll('[data-picker-chosen] .picker-row strong')).map(r => r.textContent)`);
  const card = (title) => `Array.from(${D}.querySelectorAll('[data-picker-card]')).find(c => c.dataset.title === ${JSON.stringify(title)})`;
  const row = (title, act) => `Array.from(${D}.querySelectorAll('[data-picker-chosen] .picker-row')).find(r => r.querySelector('strong').textContent === ${JSON.stringify(title)}).querySelector('[data-act=${act}]')`;
  const ids = () => js(`${input}.value`);

  // Предпросмотр: щелчок по заглушке и по кнопке «смотреть» в шторке открывает окно выбора, плеер не создаётся
  const frame = "document.querySelector('[data-editor-frame]').contentDocument";
  await until(`!!${frame} && ${frame}.readyState === 'complete' && !!${frame}.querySelector('.reviews .review-card')`);
  await js(`${frame}.querySelector('.reviews .review-card .review-soon').click(); true`);
  out.preview_placeholder_opens_picker = await until(`${D}.open`, 3000);
  await js(`${D}.querySelector('.picker-foot [data-picker-close]').click(); true`);
  await js(`${frame}.querySelector('[data-video-sheet] .review-play-link').click(); true`);
  out.preview_play_opens_picker = await until(`${D}.open`, 3000);
  out.preview_no_player = await js(`!${frame}.querySelector('.review-player')`);
  await js(`${D}.querySelector('.picker-foot [data-picker-close]').click(); true`);

  await js(`${P}.closest('details').open = true; ${P}.querySelector('[data-picker-open]').click(); true`);
  out.dialog_open = await js(`${D}.open`);
  out.raw_input_hidden = await js(`${P}.querySelector('[data-picker-raw]').hidden`);
  out.catalog = await js(`Array.from(${D}.querySelectorAll('[data-picker-card]')).filter(c => !c.closest('li').hidden).map(c => c.dataset.title)`);
  out.foot = await js(`Array.from(${D}.querySelectorAll('.picker-foot button')).map(b => b.textContent.trim())`);

  // Поиск
  await js(`{ const s = ${D}.querySelector('[data-picker-search]'); s.value = 'Отзыв B'; s.dispatchEvent(new Event('input')); } true`);
  out.search = await js(`Array.from(${D}.querySelectorAll('[data-picker-card]')).filter(c => !c.closest('li').hidden).map(c => c.dataset.title)`);
  await js(`{ const s = ${D}.querySelector('[data-picker-search]'); s.value = ''; s.dispatchEvent(new Event('input')); } true`);

  // Выбрать A, B, C; D — уже нет места
  for (const t of ["Отзыв A", "Отзыв B", "Отзыв C", "Отзыв D"]) await js(`${card(t)}.click(); true`);
  out.after_select = await chosen();
  out.count = await js(`${D}.querySelector('[data-picker-count]').textContent`);
  out.full_card = await js(`[${card("Отзыв D")}.querySelector('[data-picker-action]').textContent, ${card("Отзыв D")}.getAttribute('aria-pressed')]`);

  // Порядок и «убрать»
  await js(`${row("Отзыв C", "up")}.click(); true`);
  out.after_up = await chosen();
  await js(`${row("Отзыв A", "down")}.click(); true`);
  out.after_down = await chosen();
  await js(`${row("Отзыв A", "remove")}.click(); true`);
  out.after_remove = await chosen();
  await js(`${D}.querySelector('[data-picker-done]').click(); true`);
  out.after_done = { open: await js(`${D}.open`), ids: await ids() };

  // «Отмена» отбрасывает изменения в окне
  await js(`${P}.querySelector('[data-picker-open]').click(); true`);
  await js(`${card("Отзыв D")}.click(); true`);
  await js(`${D}.querySelector('.picker-foot [data-picker-close]').click(); true`);
  out.after_cancel = await ids();
  await js(`${P}.querySelector('[data-picker-open]').click(); true`);
  out.reopened = await chosen();

  // Добавить видеофайл, не закрывая окно: видео делает сам Chrome (canvas → MediaRecorder → WebM)
  await js(`window.makeVideo = () => new Promise((resolve) => {
    const c = document.createElement('canvas'); c.width = 320; c.height = 180;
    const g = c.getContext('2d'); let n = 0;
    const rec = new MediaRecorder(c.captureStream(25), { mimeType: 'video/webm' }); const parts = [];
    rec.ondataavailable = (e) => parts.push(e.data);
    rec.onstop = () => resolve(new File(parts, 'review.webm', { type: 'video/webm' }));
    const t = setInterval(() => { g.fillStyle = 'hsl(' + (n * 9) + ',70%,45%)'; g.fillRect(0, 0, 320, 180); n++; }, 40);
    rec.start(); setTimeout(() => { clearInterval(t); rec.stop(); }, 1500);
  }); true`);
  const A = `${D}.querySelector('[data-video-adder]')`;
  await js(`${D}.querySelector('[data-picker-add-open]').click(); ${A}.querySelector('[data-adder-source="file"]').click(); true`);
  await js(`makeVideo().then((f) => { const dt = new DataTransfer(); dt.items.add(f); const i = ${A}.querySelector('[data-adder-file]'); i.files = dt.files; i.dispatchEvent(new Event('change', { bubbles: true })); return true })`);
  out.local_preview = await until(`(() => { const v = ${A}.querySelector('[data-adder-preview] video'); return !!v && v.readyState >= 2 })()`);
  out.frame_cover = await until(`(${A}.querySelector('[data-cover-img]').getAttribute('src') || '').startsWith('blob:')`);
  await js(`${A}.querySelector('[data-adder-field="title"]').value = 'Новый файл'; ${A}.querySelector('[data-adder-save]').click(); true`);
  out.added = await until(`!!${card("Новый файл")}`);
  out.added_card = await js(`(() => { const c = ${card("Новый файл")}; return [c.querySelector('[data-picker-badge]').textContent, /\\/media\\/videos\\/covers\\//.test((c.querySelector('img') || {}).src || '')] })()`);
  out.dialog_still_open = await js(`${D}.open`);
  out.after_add = await chosen();
  await js(`${card("Новый файл")}.click(); true`);
  out.after_pick_new = await chosen();
  await js(`${D}.querySelector('[data-picker-done]').click(); true`);

  // Сохранить черновик → предпросмотр; сайт не меняется до «Опубликовать»
  const captions = (html) => `(() => { const d = new DOMParser().parseFromString(${html}, 'text/html');
    return Array.from(d.querySelectorAll('.reviews .review-play-link')).map(a => a.dataset.videoTitle) })()`;
  out.public_before = await js(`fetch('/').then(r => r.text()).then(h => ${captions("h")})`);
  await js(`document.querySelector('.editor-save button').click(); true`);
  await sleep(1000);
  await until("document.readyState === 'complete' && !!document.querySelector('[data-editor-frame]')");
  out.save_error = await js(`(document.querySelector('.message-error') || {}).textContent || ''`);
  out.preview = await js(`fetch('/admin/editor/preview').then(r => r.text()).then(h => ${captions("h")})`);
  out.public_after_save = await js(`fetch('/').then(r => r.text()).then(h => ${captions("h")})`);
  await js(`document.querySelector('[data-editor-publish]').click(); true`);
  await sleep(1000);
  await until("document.readyState === 'complete'");
  out.public_after_publish = await js(`fetch('/').then(r => r.text()).then(h => ${captions("h")})`);

  // На сайте щелчок по ролику по-прежнему запускает плеер
  await go(base + "/");
  await js(`document.querySelector('.reviews .review-play-link').click(); true`);
  out.public_plays = await until("!!document.querySelector('.reviews .review-player')", 3000);

  // Телефон: окно во весь экран, без горизонтальной прокрутки
  await send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
  await go(base + "/admin/editor?group=section-reviews");
  await js(`${P}.closest('details').open = true; ${P}.querySelector('[data-picker-open]').click(); true`);
  await sleep(300);
  out.phone_fits = await js(`${D}.scrollWidth <= ${D}.clientWidth + 1 && document.documentElement.scrollWidth <= innerWidth`);

  out.errors = errors;
  process.stdout.write(JSON.stringify(out) + "\n");
  ws.close();
  process.exit(0);
})().catch((err) => {
  process.stderr.write(String(err && err.stack || err) + "\n");
  process.exit(1);
});
