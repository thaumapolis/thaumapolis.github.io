/**
 * env-probe.mjs — identify the rendering environment that produced a golden.
 *
 * A golden can only be interpreted if you know which browser painted it, and
 * "Firefox 157" is not enough: Playwright pins a *build* (1555), and two
 * nixpkgs revisions can ship different builds that both report 157. Recorded
 * here alongside a text-measurement fingerprint, so a golden difference can be
 * attributed to a machine instead of argued about.
 *
 *   node tools/env-probe.mjs
 */
const entry = process.env.PLAYWRIGHT_ENTRY;
if (!entry) {
  console.error('PLAYWRIGHT_ENTRY is not set');
  process.exit(2);
}
const { firefox } = await import(entry);
const browser = await firefox.launch();
const page = await (await browser.newContext({ viewport: { width: 1280, height: 800 } })).newPage();
await page.setContent(`<!doctype html><meta charset="utf-8"><body style="font-family:'Thaumapolis Sans'">x`);
const report = await page.evaluate(() => {
  const c = document.createElement('canvas').getContext('2d');
  const probe = 'thaumapolis machines with real arithmetic';
  const widths = {};
  for (const family of ['"Thaumapolis Sans"', 'system-ui', 'sans-serif']) {
    c.font = `16px ${family}`;
    widths[family] = Number(c.measureText(probe).width.toFixed(3));
  }
  return {
    userAgent: navigator.userAgent,
    dpr: window.devicePixelRatio,
    languages: navigator.languages.join(','),
    textWidths16px: widths,
  };
});
await browser.close().catch(() => {});
console.log(JSON.stringify({
  ...report,
  driver: entry,
  browsersPath: process.env.PLAYWRIGHT_BROWSERS_PATH ?? null,
}, null, 2));
process.exit(0);
