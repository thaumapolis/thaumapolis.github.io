/**
 * offline-proof.mjs — demonstrate the front page's central claim.
 *
 * The page says every project below it is "one file that asks for nothing
 * else — no build, no network, no storage". That is a claim about six browser
 * artifacts, and until now it was asserted rather than shown. This runs each
 * artifact in a sandboxed frame with every non-file: request aborted, and makes
 * the frame report what it actually observed, so the claim is evidence.
 *
 * WHY THE FRAME HAS TO ANNOUNCE ITSELF
 *
 * "No page errors" is what a frame that never executed the artifact also looks
 * like. Every earlier attempt at this passed vacuously. So each artifact gets a
 * self-report appended, and SILENCE IS A FAILURE, not a pass.
 *
 * WHY EVERYTHING IS SERVED OVER http: BY A ROUTE
 *
 * Every artifact here is one inline <script>, and three things had to be true at
 * once: the inline script must run, the frame must deny storage, and the parent
 * must be able to tell a loaded artifact from a silent one. Writing to disk and
 * loading a file: URL fails the third silently -- measured 2026-11-12, an http:
 * parent that sets f.src to a file: URL makes NO request at all, so every project
 * reads as "silent" and nothing explains why. A srcdoc frame under a file: parent
 * fails the first (Firefox skips inline scripts there), and page.setContent fails
 * the second by leaving the parent on a null origin where storage is denied for
 * everyone. Serving both parent and artifact from a routed http: origin is the
 * only combination that satisfies all three, and it is also the shape of the
 * advertised preview link. See knowledge/environment.md.
 *
 * WHY THE CONTROL IS NOT A FORMALITY
 *
 * Two controls gate the whole run, and the summary refuses to call anything
 * proven if either is missing or wrong:
 *   - the sandboxed frame must report storage denied, and
 *   - the same frame WITHOUT the sandbox attribute must report storage
 *     accessible, so a denial is attributable to the sandbox rather than to the
 *     origin doing it by itself.
 *
 * Usage:  node tools/offline-proof.mjs --repos DIR [--engine firefox] [--out f.json]
 */
import { writeFileSync } from 'node:fs';

const args = process.argv.slice(2);
const flag = (name) => {
  const i = args.indexOf(name);
  return i === -1 ? null : args[i + 1];
};
const REPOS = flag('--repos');
const ENGINE = flag('--engine') || 'firefox';
const OUT = flag('--out');
if (!REPOS) {
  process.stdout.write(JSON.stringify({ error: '--repos DIR is required' }) + '\n');
  process.exit(2);
}

const entry = process.env.PLAYWRIGHT_ENTRY;
if (!entry) {
  process.stdout.write(JSON.stringify({ error: 'PLAYWRIGHT_ENTRY is not set' }) + '\n');
  process.exit(2);
}
const mod = await import(entry);
const browserType = { firefox: mod.firefox, chromium: mod.chromium, webkit: mod.webkit }[ENGINE];
if (!browserType) {
  process.stdout.write(JSON.stringify({ error: `unknown engine ${ENGINE}` }) + '\n');
  process.exit(2);
}

/** Projects that advertise a browser preview are the ones the claim covers. */
const PREVIEW_RE = /https:\/\/raw(?:cdn)?\.githack\.com\/\S+?\/index\.html/;
const { readFileSync, existsSync } = await import('node:fs');
const allRepos = readFileSync(new URL('./generate.py', import.meta.url), 'utf8');
const listed = [...allRepos.matchAll(/^\s{4}"([a-z0-9-]+)",$/gm)].map((m) => m[1]);
if (listed.length === 0) {
  process.stdout.write(JSON.stringify({ error: 'could not read REPOS out of generate.py' }) + '\n');
  process.exit(1);
}
const covered = listed.filter((repo) => {
  const readme = `${REPOS}/${repo}/README.md`;
  return existsSync(readme) && PREVIEW_RE.test(readFileSync(readme, 'utf8'));
});
if (covered.length === 0) {
  process.stdout.write(JSON.stringify({ error: 'no project README advertises a preview', listed }) + '\n');
  process.exit(1);
}

// REPO is substituted per project: the harness matches a report to the project
// it came from, so a report with no identity can never match and every project
// would read as silent. That is a harness bug that looks like six app failures.
const selfReport = (repo) => [
  '  <script>',
  '  window.addEventListener("load", function () {',
  '    setTimeout(function () {',
  `      var o = { probe: "artifact", repo: ${JSON.stringify(repo)} };`,
  '      var cs = document.querySelectorAll("canvas");',
  '      o.canvases = cs.length;',
  '      o.sizes = [].map.call(cs, function (c) { return c.width + "x" + c.height; }).join(",");',
  '      var painted = 0;',
  '      [].forEach.call(cs, function (c) {',
  '        try {',
  '          var g = c.getContext("2d");',
  '          if (!g) { painted += 1; return; }',
  '          var d = g.getImageData(0, 0, Math.min(c.width, 80), Math.min(c.height, 80)).data;',
  '          for (var j = 3; j < d.length; j += 4) { if (d[j] !== 0) { painted += 1; break; } }',
  '        } catch (e) { painted += 1; }',
  '      });',
  '      o.painted = painted;',
  '      o.textLen = (document.body.innerText || "").length;',
  '      try { void window.localStorage; o.localStorage = "accessible"; }',
  '      catch (e) { o.localStorage = "denied:" + e.name; }',
  '      try { void window.indexedDB; o.indexedDB = "accessible"; }',
  '      catch (e) { o.indexedDB = "denied:" + e.name; }',
  '      o.origin = String(window.location.origin);',
  '      parent.postMessage(o, "*");',
  '    }, 3000);',
  '  });',
  '  </script>',
  '',
].join('\n');

// An onload attribute rather than an inline script, so the control reports
// truthfully even in contexts where inline scripts are skipped.
const controlSrc = (sandboxed) =>
  '<!doctype html><html><body onload="(function(){var o={probe:\'control\'};' +
  'var ks=[\'localStorage\',\'sessionStorage\',\'indexedDB\'];' +
  'for(var i=0;i<3;i++){try{void window[ks[i]];o[ks[i]]=\'accessible\'}' +
  'catch(e){o[ks[i]]=\'denied:\'+e.name}}' +
  'o.origin=String(window.location.origin);parent.postMessage(o,\'*\')})()">' +
  `control${sandboxed ? '' : ' (no sandbox attribute)'}</body></html>`;

const parentHtml = (sandboxed) => [
  '<!doctype html><html><body>',
  `<iframe id="f" ${sandboxed ? 'sandbox="allow-scripts"' : ''} style="width:940px;height:700px;border:0"></iframe>`,
  '<script>',
  'window.__messages = [];',
  'window.addEventListener("message", function (ev) { window.__messages.push(ev.data); });',
  `document.getElementById("f").srcdoc = ${JSON.stringify(controlSrc(sandboxed))};`,
  'window.loadArtifact = function (url) {',
  '  var f = document.getElementById("f");',
  '  f.removeAttribute("srcdoc");',
  '  f.src = url;',
  '};',
  '</script></body></html>',
  '',
].join('\n');

/**
 * The parent must live on a real http: origin. page.setContent leaves it on the
 * opaque `null` origin, where storage is denied no matter what the frame asks
 * for, and a control there cannot distinguish the sandbox from the origin. A
 * routed origin also matches how these artifacts are actually served.
 */
const PARENT_ORIGIN = 'http://offline-proof.invalid/';

async function openParent(browser, sandboxed) {
  const ctx = await browser.newContext({ viewport: { width: 980, height: 760 } });
  await ctx.route(`${PARENT_ORIGIN}*`, (route) =>
    route.fulfill({ status: 200, contentType: 'text/html', body: parentHtml(sandboxed) }));
  const page = await ctx.newPage();
  await page.goto(PARENT_ORIGIN, { waitUntil: 'load' });
  return { ctx, page };
}

async function awaitMessage(page, probeName, match, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const found = await page.evaluate(([name, m]) => {
      const idx = window.__messages.findIndex(
        (x) => x && x.probe === name && (!m || x[m.key] === m.value),
      );
      if (idx === -1) { return null; }
      const [msg] = window.__messages.splice(idx, 1);
      return msg;
    }, [probeName, match || null]);
    if (found) { return found; }
    await new Promise((r) => setTimeout(r, 200));
  }
  return null;
}

let browser;
try {
  browser = await browserType.launch();
} catch (error) {
  process.stdout.write(JSON.stringify({ error: `${ENGINE} failed to launch: ${error.message.split('\n')[0]}` }) + '\n');
  process.exit(1);
}

const result = { engine: ENGINE, controls: {}, projects: [], verdict: 'unknown' };
let failed = 0;

// ---- control 1: the sandboxed frame really denies Web Storage ----
{
  const { ctx, page } = await openParent(browser, true);
  const c = await awaitMessage(page, 'control', null, 12000);
  c.parentOrigin = await page.evaluate(() => String(window.location.origin));
  result.controls.sandboxed = c;
  const denied = c ? ['localStorage', 'sessionStorage'].filter((k) => String(c[k]).startsWith('denied')) : [];
  if (denied.length !== 2) {
    failed += 1;
    result.controls.sandboxed_ok = false;
  } else {
    result.controls.sandboxed_ok = true;
  }
  await ctx.close();
}

// ---- control 2: without the attribute, storage is reachable ----
// This is what makes a denial attributable to the sandbox and not to the origin.
{
  const { ctx, page } = await openParent(browser, false);
  const c = await awaitMessage(page, 'control', null, 12000);
  c.parentOrigin = await page.evaluate(() => String(window.location.origin));
  result.controls.unsandboxed = c;
  const open = c ? ['localStorage', 'sessionStorage'].filter((k) => c[k] === 'accessible') : [];
  if (open.length !== 2) {
    failed += 1;
    result.controls.unsandboxed_ok = false;
  } else {
    result.controls.unsandboxed_ok = true;
  }
  await ctx.close();
}

if (result.controls.sandboxed_ok && result.controls.unsandboxed_ok) {
  // ---- the artifacts, one hostile context each ----
  for (const repo of covered) {
    const ctx = await browser.newContext({ viewport: { width: 980, height: 760 } });
    const blocked = [];
    await ctx.route('**/*', (route) => {
      const u = route.request().url();
      // The routed parent/artifact origin is served from memory, not fetched.
      if (u.startsWith(PARENT_ORIGIN)) { return route.continue(); }
      blocked.push(u);
      return route.abort();
    });
    await ctx.route(`${PARENT_ORIGIN}*`, (route) =>
      route.fulfill({ status: 200, contentType: 'text/html', body: parentHtml(true) }));
    const page = await ctx.newPage();
    const errors = [];
    page.on('pageerror', (e) => errors.push(String(e).slice(0, 200)));

    const raw = readFileSync(`${REPOS}/${repo}/index.html`, 'utf8');
    const url = `${PARENT_ORIGIN}${repo}/index.html`;
    await ctx.route(url, (route) =>
      route.fulfill({
        status: 200,
        contentType: 'text/html',
        body: raw.replace('</body>', selfReport(repo) + '</body>'),
      }));

    await page.goto(PARENT_ORIGIN, { waitUntil: 'load' });
    const control = await awaitMessage(page, 'control', null, 12000);
    if (!control) {
      result.projects.push({ repo, ok: false, reason: 'control silent' });
      failed += 1;
      await ctx.close();
      continue;
    }
    await page.evaluate((u) => window.loadArtifact(u), url);
    const r = await awaitMessage(page, 'artifact', { key: 'repo', value: repo }, 30000);
    if (!r) {
      const diag = await page.evaluate(() => ({
        buffered: window.__messages.map((m) => String((m && m.probe) || '?')),
        src: document.getElementById('f').getAttribute('src'),
        hasSrcdoc: document.getElementById('f').hasAttribute('srcdoc'),
      })).catch((e) => ({ diagError: String(e) }));
      result.projects.push({ repo, ok: false, reason: 'artifact silent (it may not have run at all)', diag, pageErrors: errors });
      failed += 1;
      await ctx.close();
      continue;
    }
    const ok = r.canvases >= 1 && r.painted >= 1 && errors.length === 0 && blocked.length === 0;
    if (!ok) { failed += 1; }
    result.projects.push({
      repo, ok,
      canvases: r.canvases, painted: r.painted, sizes: r.sizes, textLen: r.textLen,
      origin: r.origin,
      storage: { localStorage: r.localStorage, indexedDB: r.indexedDB },
      blockedRequests: blocked.length,
      pageErrors: errors,
    });
    await ctx.close();
  }
}

result.failed = failed;
result.verdict = failed === 0 ? 'demonstrated' : 'NOT demonstrated';
if (OUT) { writeFileSync(OUT, JSON.stringify(result, null, 2)); }
process.stdout.write(JSON.stringify(result) + '\n');
await browser.close().catch(() => {});
process.exit(failed === 0 ? 0 : 1);
