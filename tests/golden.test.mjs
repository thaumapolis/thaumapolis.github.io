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

/**
 * Content that must be present, as opposed to pixels that must be unchanged.
 *
 * Pixel goldens pin the page including facts that are true only today: commit
 * counts and date ranges come from the projects' history, so a baseline goes
 * stale the moment any project commits. That is intended for art and layout,
 * and actively misleading for facts -- the failure looks like a rendering
 * regression and invites someone to blindly re-accept a baseline that has
 * stopped asserting anything.
 *
 * So the pixel comparison is paired with checks insensitive to history: the
 * right number of cards, every advertised project present, and on each card a
 * date range, a commit count, and an embedded image that actually loaded. A
 * card that loses its dates or drops off the page fails here even when nobody
 * has looked at a baseline in weeks.
 *
 * The expectations are read from the same checkouts the page was generated
 * from, never written out here: this repo's rule is that project facts live in
 * the projects. Pass --repos when generating from somewhere other than `..`.
 */
/** Matches "24 Oct 2026", "29 Oct - 30 Oct 2026", and "history unknown". */
const META_DATE = /\d{1,2} \w{3} \d{4}|history unknown/;
/** Matches "1 commit" and "39 commits", but not an empty span. */
const META_COMMITS = /\b\d+ commits?\b/;

/**
 * Read the manifest the page was built with, then verify every claim in it
 * against the project's own README.
 *
 * The manifest is what the generator already knows, so the test does not
 * hard-code a card count in a second language. Verifying it against the
 * checkouts is what stops the manifest becoming a second source of truth: if it
 * says a project is openable and that project's README has no preview line, the
 * test fails even though the page and the manifest agree with each other.
 *
 * --repos is required for the same reason the generator requires it: without
 * the checkouts these checks would compare the page against itself.
 */
function expectations(reposDir) {
  const raw = readFileSync(PAGE, 'utf8');
  const match = raw.match(/<script type="application\/json" id="page-manifest">([\s\S]*?)<\/script>/);
  if (match === null) {
    die(`${PAGE} has no #page-manifest block; rebuild it with ./check.sh`);
  }
  const { projects } = JSON.parse(match[1]);
  if (!Array.isArray(projects) || projects.length === 0) {
    die('the page manifest lists no projects');
  }
  for (const entry of projects) {
    const readme = join(reposDir, entry.repo, 'README.md');
    if (!existsSync(readme)) {
      die(
        `--repos ${reposDir} has no ${entry.repo}/README.md; ` +
          'the checks cannot confirm what the page claims about it',
      );
    }
    const text = readFileSync(readme, 'utf8');
    const title = readmeTitle(text);
    if (title !== entry.title) {
      die(
        `manifest says ${entry.repo} is titled "${entry.title}" but its README says "${title}"`,
      );
    }
    if (readmeHasPreview(text) !== entry.preview) {
      die(
        `manifest says ${entry.repo} preview=${entry.preview} but its README ` +
          `says ${readmeHasPreview(text)}`,
      );
    }
  }
  return {
    titles: projects.map((p) => p.title),
    repos: projects.map((p) => p.repo),
    openable: projects.filter((p) => p.preview).length,
    thumbRepos: projects.filter((p) => p.thumb).map((p) => p.repo),
  };
}

/**
 * The two rules below must match tools/generate.py. They are deliberately
 * minimal, and the assertion in expectations() is what catches drift: a rule
 * that disagrees with the generator fails the run rather than silently
 * rubber-stamping the page.
 */
function readmeTitle(text) {
  const match = text.match(/^# (.+)$/m);
  if (match === null) die('README has no `# Title` line');
  return match[1].trim();
}
function readmeHasPreview(text) {
  return /https:\/\/raw(cdn)?\.githack\.com\/\S+?\/index\.html/.test(text);
}

/** Full-width cards when the grid has more than one column: the opener, and the
 *  last card when it would otherwise sit alone in a row. */
const EXPECTED_FULL_WIDTH_CARDS = 2;

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
const reposIndex = argv.indexOf('--repos');
if (reposIndex < 0) {
  die('--repos DIR is required: the content checks read the projects the page was built from.');
}
const REPOS_DIR = argv[reposIndex + 1];
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
    // Measured from layout, not from a class name, which can claim a span
    // falsely. Columns are inferred from the row the cards actually sit on, so
    // the same expectation reads correctly at every width: a single-column phone
    // grid legitimately has every card spanning, and a two-column desktop must
    // have exactly the wide ones doing it.
    columns: (() => {
      const cards = [...document.querySelectorAll('.card')];
      if (cards.length === 0) return 0;
      // Group into rows by top edge, then report the widest row. Reading only
      // the first row was the bug: it is the full-width opener, so the count
      // came back 1 on a desktop that clearly has two columns, and the
      // assertion built on it could never have failed.
      const rows = new Map();
      for (const card of cards) {
        const box = card.getBoundingClientRect();
        // 2px tolerance for sub-pixel layout differences within a row.
        const key = Math.round(box.top / 2) * 2;
        const found = [...rows.keys()].find((k) => Math.abs(k - key) < 2);
        const bucket = found === undefined ? [] : rows.get(found);
        bucket.push(Math.round(box.left));
        rows.set(found === undefined ? key : found, bucket);
      }
      return Math.max(...[...rows.values()].map((lefts) => new Set(lefts).size));
    })(),
    fullWidth: [...document.querySelectorAll('.card')].filter(
      (c) => c.closest('ol').getBoundingClientRect().width - c.getBoundingClientRect().width < 2,
    ).length,
    // Per card: its heading, whether both meta facts rendered, and whether its
    // image is a real embedded picture rather than a broken src.
    cardsInfo: [...document.querySelectorAll('.card')].map((c) => ({
      title: c.querySelector('h2').textContent.trim(),
      meta: [...c.querySelectorAll('.meta span')].map((s) => s.textContent.trim()),
      imgOk: (() => {
        const img = c.querySelector('img');
        if (img === null) return true; // motif card, nothing to load
        return img.complete && img.naturalWidth > 0;
      })(),
    })),
    font,
  };
})()`;

async function main() {
  const EXPECTED = expectations(REPOS_DIR);
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
      if (health.columns < 1) problems.push(`grid rendered no columns`);
      // In one column every card spans, so there is nothing to assert beyond
      // there being columns; in more, exactly the wide ones may span.
      if (health.columns > 1 && health.fullWidth !== EXPECTED_FULL_WIDTH_CARDS) {
        problems.push(
          `expected ${EXPECTED_FULL_WIDTH_CARDS} full-width cards in a ${health.columns}-column grid, measured ${health.fullWidth}`,
        );
      }
      if (health.cards !== EXPECTED.titles.length) {
        problems.push(
          `expected ${EXPECTED.titles.length} cards, rendered ${health.cards}`,
        );
      }
      if (health.opens.length !== EXPECTED.openable) {
        problems.push(
          `expected ${EXPECTED.openable} "Open it" links, rendered ${health.opens.length}`,
        );
      }
      for (const title of EXPECTED.titles) {
        if (!health.cardsInfo.some((c) => c.title === title)) {
          problems.push(`no card titled "${title}" (dropped off the page)`);
        }
      }
      for (const card of health.cardsInfo) {
        const [dates, commits] = card.meta;
        if (!META_DATE.test(dates ?? '')) {
          problems.push(`${card.title}: meta shows no date range (got ${JSON.stringify(dates)})`);
        }
        if (!META_COMMITS.test(commits ?? '')) {
          problems.push(`${card.title}: meta shows no commit count (got ${JSON.stringify(commits)})`);
        }
        if (!card.imgOk) problems.push(`${card.title}: embedded card image did not load`);
      }
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
