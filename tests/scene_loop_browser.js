"use strict";

/* Петля pre-video.mp4 первого экрана в настоящем Chrome — для tests/test_scene_loop.py.

   Chrome без окна управляется по DevTools Protocol через встроенный в Node WebSocket (Node 22+);
   зависимостей нет. Запуск: node scene_loop_browser.js <адрес сайта> <порт DevTools>.
   Результат — одна строка JSON в stdout: значения шагов сценария, проверяет их pytest. */

const [base, port] = process.argv.slice(2);
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
  });
  function send(method, params = {}) {
    return new Promise((r) => { pending[++id] = r; ws.send(JSON.stringify({ id, method, params })); });
  }
  async function js(expr) {
    const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.result.exceptionDetails) throw new Error(expr.slice(0, 100) + " :: " + JSON.stringify(r.result.exceptionDetails).slice(0, 300));
    return r.result.result.value;
  }
  async function until(expr, ms = 15000) {
    for (let t = 0; t < ms; t += 100) {
      if (await js(expr)) return true;
      await sleep(100);
    }
    return false;
  }
  async function open(width, height, mobile, motion = "no-preference") {
    await send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile });
    await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-motion", value: motion }] });
    await send("Page.navigate", { url: base + "/" });
    await sleep(300);
    await until("document.readyState === 'complete'");
  }

  const V = "document.querySelector('[data-scene-loop]')";
  const opacity = () => js(`Number(getComputedStyle(${V}).opacity)`);
  const playing = () => until(`!${V}.paused && ${V}.currentTime > 0.2`, 10000);
  // Кадр холста — по нескольким точкам: меняется ли сцена сборки под петлёй
  const canvasPixels = () => js(`(() => { const c = document.querySelector('[data-scene-canvas]'); const g = c.getContext('2d');
    return [0.3, 0.5, 0.7].map((x) => Array.from(g.getImageData(Math.floor(c.width * x), Math.floor(c.height * 0.5), 1, 1).data).join()).join('|') })()`);
  async function scrollTo(screens) {
    await js(`scrollTo(0, innerHeight * ${screens}); true`);
    await sleep(900);                                         // плавность прокрутки кадров догоняет
  }

  const out = {};
  for (const domain of ["Page", "Runtime"]) await send(domain + ".enable");

  // Компьютер: вверху петля видна и играет, без звука и по кругу
  await open(1440, 900, false);
  out.top_plays = await playing();
  out.top_opacity = await opacity();
  out.flags = await js(`[${V}.muted, ${V}.loop, ${V}.autoplay, ${V}.playsInline, ${V}.controls]`);
  // Сравнивать кадры — когда загружены все: до того холст рисует ближайший загруженный
  out.frames_loaded = await until(`new Set(performance.getEntriesByType('resource').map((e) => e.name).filter((n) => /\\/frame-\\d+\\.webp$/.test(n))).size >= Number(document.querySelector('[data-scene]').dataset.frameCount)`, 30000);
  await sleep(500);
  const topPixels = await canvasPixels();

  // Прокрутка: петля растворяется в кадры, скрытая — на паузе; кадры под ней сменились (разборка)
  await scrollTo(0.12);
  out.mid_opacity = await opacity();
  await scrollTo(0.5);
  out.past_opacity = await opacity();
  out.past_paused = await js(`${V}.paused`);
  out.frames_change = (await canvasPixels()) !== topPixels;

  // Прокрутка управляет кадрами в обе стороны: на тех же местах вниз и вверх — те же кадры;
  // внизу страницы — снова готовый стенд, как вверху
  const range = await js("document.documentElement.scrollHeight - innerHeight");
  const stops = [0.75 * 900, range * 0.4, range * 0.7, range];
  const down = [];
  for (const y of stops) { await js(`scrollTo(0, ${y}); true`); await sleep(900); down.push(await canvasPixels()); }
  const up = [];
  for (const y of stops.slice().reverse()) { await js(`scrollTo(0, ${y}); true`); await sleep(900); up.unshift(await canvasPixels()); }
  out.scrub_reversible = down.every((h, i) => h === up[i]);
  out.scrub_distinct = new Set(down).size;
  out.bottom_is_top = down[down.length - 1] === topPixels;

  // Низ страницы: кадры растворяются в ту же петлю, она играет; выше — снова кадры, петля на паузе
  await js(`scrollTo(0, ${range}); true`);
  await until(`Number(getComputedStyle(${V}).opacity) === 1`, 4000);
  out.bottom_opacity = await opacity();
  out.bottom_y = await js("[Math.round(scrollY), document.documentElement.scrollHeight - innerHeight]");
  const b0 = await js(`${V}.currentTime`);
  await sleep(600);
  out.bottom_plays = !(await js(`${V}.paused`)) && (await js(`${V}.currentTime`)) !== b0;
  await js(`scrollTo(0, ${range} - innerHeight * 0.12); true`); await sleep(900);
  out.bottom_mid_opacity = await opacity();
  await js(`scrollTo(0, ${range} * 0.7); true`); await sleep(900);
  out.above_bottom = [await opacity(), await js(`${V}.paused`)];
  out.single_video = await js("document.querySelectorAll('video').length");

  // Снова наверх — петля видна и играет дальше (путь от низа долгий: ждём, пока кадры догонят прокрутку)
  await scrollTo(0);
  await until(`Number(getComputedStyle(${V}).opacity) === 1`, 4000);
  out.back_opacity = await opacity();
  const t0 = await js(`${V}.currentTime`);
  await sleep(600);
  out.back_plays = !(await js(`${V}.paused`)) && (await js(`${V}.currentTime`)) !== t0;

  // Телефон: петля под шапкой, стенд на экране, без горизонтальной прокрутки
  await open(390, 844, true);
  out.phone_plays = await playing();
  out.phone_box = await js(`(() => { const r = ${V}.getBoundingClientRect(); const h = document.querySelector('[data-header]').getBoundingClientRect();
    return { top: Math.round(r.top), header: Math.round(h.bottom), standLeft: Math.round(r.left + r.width * 0.46), standRight: Math.round(r.right - r.width * 0.06) } })()`);
  out.phone_fits = await js("document.documentElement.scrollWidth <= innerWidth");
  await scrollTo(0.5);
  out.phone_past_opacity = await opacity();
  await js("scrollTo(0, document.documentElement.scrollHeight); true"); await sleep(1200);
  out.phone_bottom = [await opacity(), await js(`!${V}.paused`)];

  // Уменьшение движения: видео не грузится и не видно — прежняя неподвижная сцена
  await open(1440, 900, false, "reduce");
  await sleep(800);
  out.reduced = await js(`({ src: ${V}.hasAttribute('src'), display: getComputedStyle(${V}).display,
    requested: performance.getEntriesByType('resource').some((e) => e.name.includes('pre-video.mp4')) })`);

  out.errors = errors;
  process.stdout.write(JSON.stringify(out) + "\n");
  ws.close();
  process.exit(0);
})().catch((err) => {
  process.stderr.write(String(err && err.stack || err) + "\n");
  process.exit(1);
});
