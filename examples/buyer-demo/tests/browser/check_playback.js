// Headless browser check for step playback and the mobile layout.
//
// Starts tests/browser/fake_ui_server.py (fake in-process seller, no network,
// no real keys) and drives the page with Playwright's Chromium:
//   - a 2 s step timer advances one beat every ~2 s (3D and flat views);
//   - in Manual mode a background click/tap advances, while clicks on
//     controls, panels, JSON expanders and camera drags do not;
//   - Space / Right Arrow advance, P pauses;
//   - phones (390x844 and 844x390, touch): no horizontal scroll, 44 px
//     playback buttons, readable 3D bubbles, the bottom sheet and
//     presenter mode.
//
//   cd tests/browser && npm install && npx playwright install chromium
//   node check_playback.js            # SHOTS_DIR=../../docs to refresh screenshots
"use strict";
const { spawn } = require("child_process");
const path = require("path");
const { chromium } = require("playwright");

const PORT = Number(process.env.PORT || 8799);
const URL = `http://127.0.0.1:${PORT}/`;
const SHOTS = process.env.SHOTS_DIR || "";
const ARGS = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"];
const DEMO_DIR = path.resolve(__dirname, "..", "..");

let failures = 0;
function check(ok, message) {
  console.log(`${ok ? "ok  " : "FAIL"} ${message}`);
  if (!ok) failures++;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function startServer() {
  const proc = spawn("uv", ["run", "python", "tests/browser/fake_ui_server.py", "--port", String(PORT)], {
    cwd: DEMO_DIR, stdio: ["ignore", "pipe", "inherit"],
  });
  for (let i = 0; i < 120; i++) {
    try { const r = await fetch(URL + "health"); if (r.ok) return proc; } catch (e) { /* not up yet */ }
    await sleep(250);
  }
  proc.kill();
  throw new Error("fake UI server did not start");
}

const step = (page) => page.evaluate(() => {
  const m = /Step (\d+)/.exec(document.getElementById("step-count").textContent);
  return m ? Number(m[1]) : 0;
});

async function openPage(browser, { viewport, touch = false, view = "3d", timer = "4", reducedMotion } = {}) {
  const ctx = await browser.newContext({
    viewport, hasTouch: touch, isMobile: touch, deviceScaleFactor: touch ? 3 : 1, reducedMotion,
  });
  await ctx.addInitScript(([v, t]) => {
    localStorage.setItem("buyerDemoView", v);
    localStorage.setItem("buyerDemoStepTimer", t);
  }, [view, timer]);
  const page = await ctx.newPage();
  page.errors = [];
  page.on("pageerror", (e) => page.errors.push(e.message));
  await page.goto(URL);
  await page.waitForSelector("#scenario option", { state: "attached" });
  if (view === "3d") await page.waitForSelector("#scene3d canvas");
  return { ctx, page };
}

async function startRun(page, scenario = "booking") {
  await page.selectOption("#scenario", scenario);
  await page.click("#run");
  await page.waitForFunction(() => /Step 1 of/.test(document.getElementById("step-count").textContent), null, { timeout: 20000 });
  // Wait until the (fast) server run is fully buffered.
  await page.waitForFunction(() => /of \d+$/.test(document.getElementById("step-count").textContent), null, { timeout: 20000 });
}

async function timedRun(browser, view) {
  const { ctx, page } = await openPage(browser, { viewport: { width: 1280, height: 800 }, view, timer: "2" });
  check((await page.inputValue("#step-timer")) === "2", `${view}: step timer restored from localStorage`);
  await startRun(page);
  const t0 = Date.now();
  const seen = [];
  for (const at of [800, 2900, 4900]) {
    await sleep(Math.max(0, at - (Date.now() - t0)));
    seen.push(await step(page));
  }
  check(seen[0] === 1 && seen[1] === 2 && seen[2] === 3, `${view}: 2 s timer plays one beat every ~2 s (steps ${seen.join(", ")})`);
  if (view === "2d") {
    const rows = await page.locator("#chat .row").count();
    check(rows >= 2 && rows <= 4, `2d: chat bubbles appear in step with the beats (${rows} rows after 3 beats)`);
  }
  await page.keyboard.press("p");
  const paused = await step(page);
  await sleep(2600);
  check((await step(page)) === paused, `${view}: P pauses the timer`);
  await page.keyboard.press("p");
  await page.click("#skip");
  await page.waitForFunction(() => document.getElementById("status").textContent === "Done", null, { timeout: 10000 });
  check(true, `${view}: Skip to end finishes the run`);
  check(page.errors.length === 0, `${view}: no page errors ${page.errors.join(" | ")}`);
  await ctx.close();
}

async function manualDesktop(browser) {
  const { ctx, page } = await openPage(browser, { viewport: { width: 1280, height: 800 }, timer: "manual" });
  await startRun(page);
  check(await page.isVisible("#advance-hint"), "manual: 'Click anywhere to continue' hint is shown");
  await sleep(2300);
  check((await step(page)) === 1, "manual: does not advance by itself");
  await page.click("#status");
  await page.click("aside .panel h2 >> nth=0");
  await page.click("#step-count");
  await page.click("#scenario-help", { force: true });
  check((await step(page)) === 1, "manual: clicks on controls and the side panel do not advance");
  const canvas = await page.$("#scene3d canvas");
  const box = await canvas.boundingBox();
  const cx = box.x + box.width * 0.35, cy = box.y + box.height * 0.6;
  await page.mouse.move(cx, cy); await page.mouse.down();
  for (let i = 1; i <= 10; i++) await page.mouse.move(cx + i * 8, cy - i * 2);
  await page.mouse.up();
  check((await step(page)) === 1, "manual: drag-to-rotate does not advance");
  await page.mouse.click(cx, cy);
  check((await step(page)) === 2, "manual: a background click advances");
  const fresh = await page.evaluate(() => {
    const b = document.querySelector(".label3d.bubble3d");
    if (!b) return false;
    const top = b.getBoundingClientRect().top;
    return getComputedStyle(b).visibility === "hidden" || top > document.getElementById("topbar").offsetHeight;
  });
  check(fresh, "manual: a new 3D bubble appears at its anchor, not in the corner");
  await page.mouse.click(cx, cy);
  const summary = page.locator("#calls details summary").first();
  await summary.click();
  check((await step(page)) === 3, "manual: opening a JSON expander does not advance");
  await page.evaluate(() => document.activeElement && document.activeElement.blur());
  await page.keyboard.press("Space");
  check((await step(page)) === 4, "manual: Space advances");
  await page.keyboard.press("ArrowRight");
  check((await step(page)) === 5, "manual: Right Arrow advances");
  check(page.errors.length === 0, `manual: no page errors ${page.errors.join(" | ")}`);
  await ctx.close();
}

async function touchDrag(page, x, y, dx, dy) {
  const cdp = await page.context().newCDPSession(page);
  const pt = (px, py) => [{ x: px, y: py, id: 1 }];
  await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: pt(x, y) });
  for (let i = 1; i <= 8; i++) {
    await cdp.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: pt(x + (dx * i) / 8, y + (dy * i) / 8) });
  }
  await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
}

async function phone(browser, name, viewport) {
  const { ctx, page } = await openPage(browser, { viewport, touch: true, timer: "manual" });
  const noHScroll = () => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth
    && document.body.scrollWidth <= document.documentElement.clientWidth);
  check(await noHScroll(), `${name}: no horizontal scroll on load`);
  check(!(await page.isVisible("aside")), `${name}: side panel sheet is closed by default`);
  await startRun(page);
  const canvas = await page.$("#scene3d canvas");
  const box = await canvas.boundingBox();
  const cx = box.x + box.width / 2, cy = box.y + box.height * 0.55;
  await page.touchscreen.tap(cx, cy);
  check((await step(page)) === 2, `${name}: tap on the scene advances`);
  await touchDrag(page, cx - 60, cy, 120, 10);
  await sleep(200);
  check((await step(page)) === 2, `${name}: one-finger rotate does not advance`);
  const sizes = await page.$$eval("#playbar button", (bs) => bs.filter((b) => b.offsetParent).map((b) => [b.offsetWidth, b.offsetHeight]));
  check(sizes.length >= 4 && sizes.every(([w, h]) => w >= 44 && h >= 44), `${name}: playback buttons are >= 44 px (${JSON.stringify(sizes)})`);
  await page.touchscreen.tap(cx, cy);
  await sleep(300);
  const bubble = await page.evaluate(() => {
    const b = document.querySelector(".label3d.bubble3d");
    if (!b) return null;
    const r = b.getBoundingClientRect();
    return { font: parseFloat(getComputedStyle(b).fontSize), left: r.left, right: r.right, top: r.top, w: innerWidth };
  });
  check(bubble && bubble.font >= 14, `${name}: 3D bubble text is >= 14 px (${bubble && bubble.font})`);
  check(bubble && bubble.left >= 0 && bubble.right <= bubble.w && bubble.top >= 0, `${name}: 3D bubble stays on screen`);
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, `ui-mobile-${name}.png`) });

  const before = await step(page);
  await page.tap("#sheet-open");
  await sleep(350);
  check(await page.isVisible("#cards"), `${name}: the panel button opens the bottom sheet`);
  check((await step(page)) === before, `${name}: opening the sheet does not advance`);
  if (SHOTS && name === "portrait") await page.screenshot({ path: path.join(SHOTS, "ui-mobile-sheet.png") });
  await page.tap("#sheet-close");
  await sleep(350);
  check((await step(page)) === before, `${name}: closing the sheet does not advance`);

  await page.tap("#more-toggle");
  await page.tap("#presenter-toggle");
  await sleep(300);
  check(!(await page.isVisible("#topbar")) && (await page.isVisible("#playbar")), `${name}: presenter mode hides everything but the playback bar`);
  await page.touchscreen.tap(cx, cy);
  check((await step(page)) === before + 1, `${name}: tap advances in presenter mode`);
  if (SHOTS && name === "portrait") await page.screenshot({ path: path.join(SHOTS, "ui-mobile-presenter.png") });
  await page.tap("#presenter-exit");
  await sleep(300);
  check(await page.isVisible("#topbar"), `${name}: presenter mode exits`);
  check(await noHScroll(), `${name}: no horizontal scroll after playback`);
  check(page.errors.length === 0, `${name}: no page errors ${page.errors.join(" | ")}`);
  await ctx.close();
}

async function reducedMotion(browser) {
  const { ctx, page } = await openPage(browser, { viewport: { width: 1280, height: 800 }, timer: "2", reducedMotion: "reduce" });
  await startRun(page);
  await sleep(2400);
  check((await step(page)) >= 2, "reduced motion: playback still advances");
  check(page.errors.length === 0, `reduced motion: no page errors ${page.errors.join(" | ")}`);
  await ctx.close();
}

(async () => {
  const server = await startServer();
  const browser = await chromium.launch({ args: ARGS });
  try {
    await timedRun(browser, "3d");
    await timedRun(browser, "2d");
    await manualDesktop(browser);
    await phone(browser, "portrait", { width: 390, height: 844 });
    await phone(browser, "landscape", { width: 844, height: 390 });
    await reducedMotion(browser);
  } catch (e) {
    failures++;
    console.log("FAIL", e.stack || e);
  } finally {
    await browser.close();
    server.kill();
  }
  console.log(failures ? `\n${failures} check(s) failed` : "\nall browser checks passed");
  process.exit(failures ? 1 : 0);
})();
