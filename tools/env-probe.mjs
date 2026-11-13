/**
 * env-probe.mjs — identify the renderer that produced a golden.
 *
 * "Firefox 157" is not enough to compare two runs. Playwright pins a build
 * (1555), and two nixpkgs revisions can ship different builds that both report
 * Firefox/157.0 and both satisfy that build number -- observed 2026-11-12, where
 * the runner's /nix/store/l3avk0gf...-playwright-browsers and this container's
 * /nix/store/4z4armxi...-playwright-browsers disagree, and text metrics differ
 * accordingly.
 *
 * The fingerprint therefore measures the page's OWN embedded face, and asserts
 * that the face actually loaded. A probe that never registered it measures the
 * single fallback font available in this container, which returns the same width
 * for every family including monospace -- an earlier version of this file did
 * exactly that, and its identical numbers looked like "fonts match across
 * machines" when they meant "nothing loaded anywhere".
 *
 *   node tools/env-probe.mjs
 */
const entry = process.env.PLAYWRIGHT_ENTRY;
if (!entry) {
  console.error('PLAYWRIGHT_ENTRY is not set');
  process.exit(2);
}
const { firefox } = await import(entry);

// Embed the same committed faces the page embeds, so the measurement is of the
// real face rather than of a fallback.
const { readFileSync } = await import('node:fs');
const faces = ['regular', 'bold'].map((w) => {
  const b64 = readFileSync(
    new URL(`../faces/ThaumapolisSans-${w}.woff2.base64`, import.meta.url),
    'utf8',
  ).trim();
  return `@font-face{font-family:"Thaumapolis Sans";font-style:normal;font-weight:${w === 'bold' ? 700 : 400};src:url(data:font/woff2;base64,${b64}) format("woff2");}`;
}).join('\n');

const browser = await firefox.launch();
const page = await (await browser.newContext({ viewport: { width: 1280, height: 800 } })).newPage();
await page.setContent(
  `<!doctype html><meta charset="utf-8"><style>${faces}</style><body>probe`,
);

const report = await page.evaluate(async () => {
  await document.fonts.ready;
  const probe = 'thaumapolis machines with real arithmetic';
  const widths = {};
  const c = document.createElement('canvas').getContext('2d');
  for (const family of ['"Thaumapolis Sans"', 'system-ui', 'sans-serif', 'monospace']) {
    c.font = `16px ${family}`;
    widths[family] = Number(c.measureText(probe).width.toFixed(3));
  }
  return {
    userAgent: navigator.userAgent,
    dpr: window.devicePixelRatio,
    facesRegistered: [...document.fonts].map((f) => `${f.family}/${f.weight}:${f.status}`),
    faceLoadedCount: (await document.fonts.load('16px "Thaumapolis Sans"')).length,
    textWidths16px: widths,
  };
});

await browser.close().catch(() => {});

// Fail loudly rather than emit a fingerprint that would be misread.
if (report.facesRegistered.length === 0 || report.faceLoadedCount === 0) {
  console.error('FAIL: the embedded face did not load, so every width below is the fallback');
  console.error(JSON.stringify(report, null, 2));
  process.exit(1);
}
const fam = '"Thaumapolis Sans"';
const others = Object.entries(report.textWidths16px).filter(([k]) => k !== fam).map(([, v]) => v);
if (others.includes(report.textWidths16px[fam])) {
  console.error('FAIL: the embedded face measures the same as a generic family, so it is not really in use');
  console.error(JSON.stringify(report, null, 2));
  process.exit(1);
}

console.log(JSON.stringify({
  ...report,
  driver: entry,
  browsersPath: process.env.PLAYWRIGHT_BROWSERS_PATH ?? null,
}, null, 2));
process.exit(0);
