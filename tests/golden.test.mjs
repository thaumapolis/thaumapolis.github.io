#!/usr/bin/env node
/**
 * Golden screenshot tests for the generated front page.
 *
 * These pin the page as a rendered thing, because nothing else here does. The
 * generator's own assertions cover facts -- a project resolves, a preview
 * serves, the embedded faces cover the page's characters -- and every one of
 * them passed while the page was clipping buttons off the bottom of cards and
 * stacking a layout written to be horizontal. Only pixels catch that class.
 *
 * A baseline is accepted only by a human looking at it: --update writes
 * candidates and prints them, and never overwrites a baseline in place.
 *
 * Baselines live per engine (tests/golden/<engine>/<name>.png) with no
 * cross-engine fallback: engines disagree about sub-pixel text layout, so
 * comparing Firefox pixels against Chromium pixels reports a difference that
 * means nothing.
 *
 *   node tests/golden.test.mjs --engine firefox
 *   node tests/golden.test.mjs --engine firefox --update
 */
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import zlib from 'node:zlib';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const PAGE = resolve(ROOT, '.tmp', 'site', 'index.html');
const ACTUAL_DIR = join(ROOT, '.tmp', 'golden-actual');

/**
 * Fraction of pixels allowed to differ before a case fails. Small because the
 * page is static with no animation; not zero because text antialiasing is not
 * byte-exact across driver revisions.
 */
const TOLERANCE_RATIO = 0.001;

/** What the page must contain, derived from the repos it advertises. */
const EXPECTED_CARDS = 8;
const EXPECTED_OPEN_LINKS = 6;

/** The three shapes the page is designed against. Both phone orientations
 *  matter: the grid changes column count between them. */
const SCENES = [
  { name: 'desktop', width: 1280, height: 900 },
  { name: 'phone-portrait', width: 390, height: 844 },
  { name: 'phone-landscape', width: 844, height: 390 },
];

function die(message) {
  console.error(message);
  process.exit(1);
}

const REQUIRED_ENV = ['PLAYWRIGHT_ENTRY', 'PLAYWRIGHT_BROWSERS_PATH'];
const missingEnv = REQUIRED_ENV.filter((name) => !process.env[name]);
if (missingEnv.length > 0) {
  // Loud, never skipped: a suite that prints "passed" after painting nothing is
  // worse than a red build, because the red one describes the code.
  die(
    `golden checks need ${missingEnv.join(' and ')}; run inside the pinned shell:\n` +
      '  nix develop --command ./check.sh',
  );
}
if (!existsSync(process.env.PLAYWRIGHT_ENTRY)) {
  die(`PLAYWRIGHT_ENTRY does not exist: ${process.env.PLAYWRIGHT_ENTRY}`);
}
if (!existsSync(PAGE)) {
  die(`nothing to test: ${PAGE} is missing. Generate it first (./check.sh does).`);
}

const argv = process.argv.slice(2);
const UPDATE = argv.includes('--update');
const engineIndex = argv.indexOf('--engine');
const ENGINE = engineIndex < 0 ? 'firefox' : argv[engineIndex + 1];
if (!['firefox', 'chromium', 'webkit'].includes(ENGINE)) {
  console.error(`--engine expects firefox, chromium or webkit, got "${ENGINE ?? ''}"`);
  process.exit(2);
}

/** Minimal PNG reader for the 8-bit RGB/RGBA output Playwright writes. */
function decodePng(path) {
  const data = readFileSync(path);
  const signature = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  if (data.subarray(0, 8).compare(signature) !== 0) throw new Error(`${path} is not a PNG`);
  let pos = 8;
  let idat = Buffer.alloc(0);
  let width = 0;
  let height = 0;
  let colorType = 0;
  let bitDepth = 0;
  while (pos < data.length) {
    const length = data.readUInt32BE(pos);
    const type = data.subarray(pos + 4, pos + 8).toString('latin1');
    const chunk = data.subarray(pos + 8, pos + 8 + length);
    pos += 12 + length;
    if (type === 'IHDR') {
      width = chunk.readUInt32BE(0);
      height = chunk.readUInt32BE(4);
      bitDepth = chunk[8];
      colorType = chunk[9];
    } else if (type === 'IDAT') {
      idat = Buffer.concat([idat, chunk]);
    } else if (type === 'IEND') {
      break;
    }
  }
  if (bitDepth !== 8) throw new Error(`${path}: unsupported bit depth ${bitDepth}`);
  const channels = { 0: 1, 2: 3, 4: 2, 6: 4 }[colorType];
  if (channels === undefined) throw new Error(`${path}: unsupported colour type ${colorType}`);
  const stride = width * channels;
  const raw = zlib.inflateSync(idat);
  const out = Buffer.alloc(height * stride);
  let previous = Buffer.alloc(stride);
  let offset = 0;
  for (let y = 0; y < height; y += 1) {
    const filter = raw[offset];
    offset += 1;
    const line = Buffer.from(raw.subarray(offset, offset + stride));
    offset += stride;
    for (let x = 0; x < stride; x += 1) {
      const a = x >= channels ? line[x - channels] : 0;
      const b = previous[x];
      const c = x >= channels ? previous[x - channels] : 0;
      let value = line[x];
      if (filter === 1) value += a;
      else if (filter === 2) value += b;
      else if (filter === 3) value += (a + b) >> 1;
      else if (filter === 4) {
        const p = a + b - c;
        const pa = Math.abs(p - a);
        const pb = Math.abs(p - b);
        const pc = Math.abs(p - c);
        value += pa <= pb && pa <= pc ? a : pb <= pc ? b : c;
      } else if (filter !== 0) {
        throw new Error(`${path}: unsupported PNG filter ${filter}`);
      }
      line[x] = value & 0xff;
    }
    line.copy(out, y * stride);
    previous = line;
  }
  return { width, height, channels, data: out };
}

/** Fraction of pixels differing beyond a rounding-level threshold. */
function diffRatio(a, b) {
  if (a.width !== b.width || a.height !== b.height) {
    return { ratio: 1, note: `size ${a.width}x${a.height} vs ${b.width}x${b.height}` };
  }
  let different = 0;
  const total = a.width * a.height;
  for (let i = 0; i < total; i += 1) {
    const ao = i * a.channels;
    const bo = i * b.channels;
    if (
      Math.abs(a.data[ao] - b.data[bo]) > 2 ||
      Math.abs(a.data[ao + 1] - b.data[bo + 1]) > 2 ||
      Math.abs(a.data[ao + 2] - b.data[bo + 2]) > 2
    ) {
      different += 1;
    }
  }
  return { ratio: different / total, note: '' };
}

/** Checks on the live DOM, so a broken page cannot pass on unchanged pixels. */
const PAGE_HEALTH = `(() => {
  const overflowX = document.documentElement.scrollWidth - document.documentElement.clientWidth;
  const clipped = [];
  for (const card of document.querySelectorAll('.card')) {
    const box = card.getBoundingClientRect();
    const links = card.querySelector('.links').getBoundingClientRect();
    if (links.height === 0 || links.bottom > box.bottom + 1) {
      clipped.push(card.querySelector('h2').textContent);
    }
  }
  const font = getComputedStyle(document.body).fontFamily;
  return {
    overflowX,
    clipped,
    cards: document.querySelectorAll('.card').length,
    opens: [...document.querySelectorAll('a.open')].map((a) => a.href),
    wide: document.querySelectorAll('.card.wide').length,
    font,
  };
})()`;

async function main() {
  const mod = await import(pathToFileURL(process.env.PLAYWRIGHT_ENTRY).href);
  const playwright = mod.default && mod.firefox === undefined ? mod.default : mod;
  const launcher = playwright[ENGINE];
  if (!launcher) die(`the driver at ${process.env.PLAYWRIGHT_ENTRY} has no "${ENGINE}" engine`);

  let browser;
  try {
    browser = await launcher.launch();
  } catch (error) {
    die(
      `${ENGINE} failed to launch: ${error.message}\n` +
        'This container only starts Firefox; re-run with --engine firefox.',
    );
  }

  mkdirSync(ACTUAL_DIR, { recursive: true });
  mkdirSync(join(ROOT, 'tests', 'golden', ENGINE), { recursive: true });
  let failures = 0;
  const toLookAt = [];

  try {
    for (const scene of SCENES) {
      const context = await browser.newContext({
        viewport: { width: scene.width, height: scene.height },
        reducedMotion: 'reduce',
      });
      const page = await context.newPage();
      await page.goto(pathToFileURL(PAGE).href, { waitUntil: 'load' });

      const health = await page.evaluate(PAGE_HEALTH);
      const problems = [];
      if (health.overflowX > 0) problems.push(`horizontal overflow ${health.overflowX}px`);
      if (health.clipped.length > 0) problems.push(`clipped controls: ${health.clipped.join(', ')}`);
      if (health.cards !== EXPECTED_CARDS) problems.push(`expected ${EXPECTED_CARDS} cards, rendered ${health.cards}`);
      if (health.opens.length !== EXPECTED_OPEN_LINKS) {
        problems.push(`expected ${EXPECTED_OPEN_LINKS} "Open it" links, rendered ${health.opens.length}`);
      }
      if (health.wide !== 2) problems.push(`expected 2 full-width cards, rendered ${health.wide}`);
      if (!health.font.includes('Thaumapolis Sans')) {
        problems.push(`body font is "${health.font}"; the embedded face is not in use`);
      }
      if (problems.length > 0) {
        console.error(`FAIL ${scene.name}: ${problems.join('; ')}`);
        failures += 1;
      }

      const shot = join(ACTUAL_DIR, `${scene.name}.png`);
      await page.screenshot({ path: shot, fullPage: true });
      await context.close();

      const baseline = join(ROOT, 'tests', 'golden', ENGINE, `${scene.name}.png`);
      const digest = createHash('sha256').update(decodePng(shot).data).digest('hex').slice(0, 12);

      if (UPDATE) {
        writeFileSync(baseline, readFileSync(shot));
        console.log(`WROTE ${scene.name} baseline (sha256 ${digest})`);
        toLookAt.push(baseline);
        continue;
      }
      if (!existsSync(baseline)) {
        const candidate = join(ACTUAL_DIR, `${scene.name}.png`);
        console.error(`FAIL ${scene.name}: no baseline. Inspect the candidate, then accept with --update.`);
        toLookAt.push(candidate);
        failures += 1;
        continue;
      }
      const { ratio, note } = diffRatio(decodePng(baseline), decodePng(shot));
      if (ratio > TOLERANCE_RATIO) {
        const suffix = note ? ` (${note})` : '';
        console.error(
          `FAIL ${scene.name}: differs from baseline in ${(ratio * 100).toFixed(3)}% of pixels` +
            `${suffix} (limit ${(TOLERANCE_RATIO * 100).toFixed(3)}%)`,
        );
        toLookAt.push(shot);
        failures += 1;
      } else {
        console.log(`ok ${scene.name}: matches baseline (${(ratio * 100).toFixed(4)}% differ)`);
      }
    }
  } finally {
    await browser.close();
  }

  if (toLookAt.length > 0) {
    console.log('\nView every image listed here before trusting it:');
    for (const path of toLookAt) console.log(`  ${path}`);
  }
  if (failures > 0) {
    console.error(`\ngolden checks failed: ${failures} problem(s)`);
    process.exit(1);
  }
  console.log(`\ngolden checks passed (${ENGINE}, ${SCENES.length} scenes)`);
}

await main();
