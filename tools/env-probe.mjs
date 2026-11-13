/**
 * env-probe.mjs — record the rendering environment, and refuse to record a
 * meaningless one.
 *
 * WHY THIS EXISTS
 * Goldens passed in this org for a while by luck: a pinned nixpkgs revision that
 * happened to match the machine that painted them. Without a record of what did
 * the painting, a future disagreement is a guess rather than a lookup. So print
 * the nixpkgs rev, the browser build, the fontconfig state, and text metrics.
 *
 * WHAT THIS PROBE ACTUALLY ESTABLISHES (observed 2026-11-13)
 * The only environment variable that moved page layout here was the font set,
 * NOT the browser build. All three playwright-browsers store paths in this
 * container report Firefox/157.0 and all produced identical metrics; installing
 * a real font set via FONTCONFIG_FILE changed the page from 3487px to 3510px and
 * made it agree with GitHub's runner to the pixel.
 *
 * WHAT AN EARLIER VERSION OF THIS FILE GOT WRONG, TWICE
 * 1. It registered no face, so every family measured the same and the identical
 *    numbers read as "fonts agree across machines" when they meant "nothing
 *    loaded anywhere."
 * 2. It then asserted that the embedded face must measure DIFFERENTLY from
 *    sans-serif, and failed the run otherwise. That assertion is unsound: a face
 *    can legitimately share the fallback's advance widths. Measured over four
 *    strings, the committed face equals sans-serif everywhere (A 342.75,
 *    B 308.25, C 173.55, D 538.2) in BOTH a one-font container and a
 *    properly-fonted one, while monospace correctly varies -- because
 *    fontconfig routes `sans-serif` to the same DejaVu face the browser
 *    rasterises, and this page is typeset in a derivative of it. The face WAS
 *    in use; the assertion was wrong, and it was redlining a working build.
 *
 * So: the probe measures and reports, and gates ONLY on conditions that genuinely
 * invalidate a comparison -- a face that failed to load, and a machine whose
 * generic families are indistinguishable (which means fontconfig sees one face
 * and line wrapping is fiction; see tests/golden.test.mjs for that gate, which
 * is the one with teeth).
 *
 *   node tools/env-probe.mjs
 */
const entry = process.env.PLAYWRIGHT_ENTRY;
if (!entry) {
  console.error('PLAYWRIGHT_ENTRY is not set');
  process.exit(2);
}
const { firefox } = await import(entry);

// Measure the real generated page, not a stub that merely embeds the same faces.
// Page height and the resolved font stack are properties of the artifact a
// visitor gets; a synthetic page could report healthy numbers while the shipped
// one was broken. Generated if absent, the same way check.sh does.
const { existsSync } = await import('node:fs');
const siteFile = new URL('../.tmp/site/index.html', import.meta.url);
if (!existsSync(siteFile)) {
  console.error('.tmp/site/index.html is missing; run ./check.sh first');
  process.exit(2);
}

const browser = await firefox.launch();
const page = await (await browser.newContext({ viewport: { width: 1280, height: 800 } })).newPage();
await page.goto(siteFile.href);
await page.waitForTimeout(500);

const report = await page.evaluate(async () => {
  // Ask for each weight explicitly and require a match, so "loaded" means the
  // face the page asks for, not merely that some face exists on the machine.
  const loaded = {};
  for (const weight of [400, 700]) {
    const matches = await document.fonts.load(`${weight} 16px "Thaumapolis Sans"`);
    loaded[weight] = { count: matches.length, status: matches.map((f) => f.status).join(",") };
  }
  await document.fonts.ready;

  const probe = 'thaumapolis machines with real arithmetic';
  const widths = {};
  const c = document.createElement('canvas').getContext('2d');
  for (const family of ['"Thaumapolis Sans"', 'system-ui', 'sans-serif', 'monospace']) {
    c.font = `16px ${family}`;
    widths[family] = Number(c.measureText(probe).width.toFixed(3));
  }

  // What the layout engine actually resolves for body text, and the resulting
  // page height -- the number that changed when the font set was fixed.
  const body = getComputedStyle(document.body);
  return {
    userAgent: navigator.userAgent,
    dpr: window.devicePixelRatio,
    facesRegistered: [...document.fonts].map((f) => `${f.family}/${f.weight}:${f.status}`),
    faceLoad: loaded,
    textWidths16px: widths,
    bodyFontFamily: body.fontFamily,
    pageHeightPx: document.body.scrollHeight,
  };
});

await browser.close().catch(() => {});

// Gate only on conditions that invalidate the record. Reporting a number is
// fine; reporting a number as a fingerprint when nothing loaded is not.
const unloadable = Object.entries(report.faceLoad).filter(([, v]) => v.count === 0);
if (unloadable.length > 0) {
  console.error(
    `FAIL: the face the page asks for did not load (weights: ${unloadable.map(([k]) => k).join(", ")})`,
  );
  console.error(JSON.stringify(report, null, 2));
  process.exit(1);
}
const fam = '"Thaumapolis Sans"';
if (report.textWidths16px[fam] === undefined) {
  console.error('FAIL: the probe did not measure the page\'s own face');
  console.error(JSON.stringify(report, null, 2));
  process.exit(1);
}
// fontconfig sees one face => every generic family resolves to the same thing
// and wrapping is fiction. Observed: one-font sans-serif 342.75 == monospace
// 342.75; a real font set gives monospace 394.967. This is the precondition
// that actually invalidates a pixel comparison, so it fails the run here too
// rather than leaving it to the golden tests alone.
const { sans, mono } = {
  sans: report.textWidths16px['sans-serif'],
  mono: report.textWidths16px['monospace'],
};
if (sans === mono) {
  console.error(
    `FAIL: sans-serif and monospace both measure ${sans}px, so this machine has one ` +
      `usable face. Set FONTCONFIG_FILE to a config naming real font directories ` +
      `before painting or comparing goldens.`,
  );
  console.error(JSON.stringify(report, null, 2));
  process.exit(1);
}

console.log(JSON.stringify({
  ...report,
  driver: entry,
  browsersPath: process.env.PLAYWRIGHT_BROWSERS_PATH ?? null,
  // Which font set produced this render. FONTCONFIG_FILE is the variable that
  // changed page layout here, so a fingerprint without it is incomplete.
  fontconfigFile: process.env.FONTCONFIG_FILE ?? null,
}, null, 2));
process.exit(0);
