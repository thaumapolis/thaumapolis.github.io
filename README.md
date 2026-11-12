# thaumapolis.github.io

Source of the organisation front page at **<https://thaumapolis.github.io/>**.

An organisation's root Pages site publishes from a repository named exactly `<org>.github.io` — here `thaumapolis.github.io`. This repo was created as `.github` instead, on the mistaken belief that the name also selects the front page. It does not: `.github` is the special repository for community health content, and as a Pages source it publishes to `thaumapolis.github.io/.github/` — a project site, not the root. Renaming the repository moved it to the root; no workflow or generator change was needed. The forfeit is the `.github` role, since the two names are mutually exclusive.

What gets published is generated, so this repo holds a generator rather than a gallery: **no project description or URL is written down here**, and the only pictures are cut from screenshots each project nominates for itself.

## What the page is

A list of the organisation's projects in the order they were started, oldest first, each with the date range its commits span, its commit count, and a link to open it. Above the grid, a contents bar names every project with its kind, so the page can be navigated and summarised without scrolling it — the eight cards run to three viewports on a desktop and six on a phone, and until 2026-11-12 no link on the page went anywhere on it. The kind prefixes are dropped below 640px, where they duplicated the card label instead of summarising the set.

Each card shows the project's own screenshot where it has one, and a generated SVG motif where it does not. The motif is drawn in the project's colour from what the project is — a waterline for the pond, pipes for the organ, a ridge for the tower — so a card with no screenshot still reads as a thing rather than an empty box. Three of the eight cards use a motif: two projects publish no image at all, and a third whose README shows only dark, sparse frames is better served by a motif than by a black rectangle.

A screenshot is a freeze of something that moves, so the choice matters, and it is not this repo's to make. See [Card images](#card-images).

## How it works

`tools/generate.py` reads each project's own README and derives its title, description and preview link from it, then emits `index.html`. `--repos` points at a directory of checkouts; `--out` says where the page goes. `--skip-preview-checks` is for offline development and prints a warning, because a page built with it may advertise a dead link.

Deriving rather than hand-writing is about drift. A hand-kept index restates facts that live in other repositories, and this workspace has already spent a session chasing an organisation name copied into a README, a test and a shell script. Here there is one copy of each fact: the project's README. Edit that and the front page follows at the next build.

The build **fails loudly** rather than publishing something plausible with a hole in it: a missing project, a README with no title or descriptive paragraph, an advertised preview link that stops serving HTML, a shallow clone, or an embedded font that is not a font.

### The offline claim is demonstrated, not asserted

The lede says every project is "one file that asks for nothing else — no build, no network, no storage". `tools/offline-proof.mjs` makes that an observation: each artifact that advertises a preview is loaded in a `sandbox="allow-scripts"` frame with every non-routed request aborted, and must paint a canvas, produce DOM, and report **zero** external request attempts. Which projects are covered is read from the READMEs by the same `_PREVIEW_RE` the generator uses, so the list cannot drift from what the page actually links to.

The hard part was not the assertion, it was making the test capable of failing. Three things had to be true at once — the inline script must run, the frame must deny storage, and the harness must distinguish a loaded artifact from a silent one — and the first two arrangements got each of those wrong in a way that still reported success:

- `page.setContent` leaves the parent on an opaque `null` origin where storage is denied **for every frame regardless of the sandbox**, so a "hostile" control there proves nothing. Both parent and artifact are therefore served from a routed `http:` origin.
- A frame holding both `srcdoc` and `src` keeps `srcdoc` authoritative and fetches nothing from `src`.
- An `http:` parent that sets `f.src` to a `file:` URL issues **no request at all** and reports no error, so all six projects read as "silent" with an empty explanation. This cost the most time, because it looked like the artifacts were broken.

Each frame now reports its own state over `postMessage` and **silence fails the run**, since "no page errors" is also what a frame that never executed the artifact looks like. Two controls gate the verdict: the sandboxed frame must deny Web Storage, and the same frame *without* the `sandbox` attribute must allow it — without the second, a denial is not attributable to the sandbox. Verified to go red for each reason it claims to: removing the `sandbox` attribute (control collapses), and dropping the self-report (all six fail as silent rather than passing).

One honest limit: the frame reports `indexedDB` as **accessible**, which matches what a `sandbox="allow-scripts"` frame really exposes. No artifact here touches it, but "no storage" means Web Storage and cookies, not literally every persistence API.

### Ordering

Order comes from each project's first commit, read from its clone. The GitHub API's `created_at` is not used: it is repo creation, not when the work started, and it disagrees with the first commit on several projects — enough to reorder the page, which it does to `nebulabrot`.

This makes full history load-bearing, so the workflow clones without `--depth`. A shallow clone reports one commit and one date, so every project looks a day old and the chronology silently becomes a tie — and the page still publishes, because nothing else notices. `tools/generate.py` therefore refuses to build against a shallow clone. Verified against real one-commit clones, which it rejects with a message naming the repos and the fix.

`ORDER` used to be a hand-maintained list that fixed the display order. It is now `REPOS`, which says only what must exist; position follows from the history.

### Fonts

The page embeds its own two faces as base64 WOFF2, generated from DejaVu Sans by a subsetter and committed as `faces/*.base64`.

This is not decoration. A `system-ui` stack resolves to whatever the visitor's machine provides, so the layout being designed is not the layout anyone sees; and this container ships one font, so an unembedded page renders differently locally, in CI and on a phone. `tools/generate.py` checks the characters the page can emit against the faces that are actually embedded, and says which glyph is missing — see the note in `tests/` about what that check does not cover.

## Checking it

`./check.sh --repos ..` runs lint, the card thumbnails, the build and the golden screenshots. Run it inside the pinned toolchain:

```console
$ nix develop --command ./check.sh --repos ..
```

Every phase runs or fails the build; a phase that cannot run is a red result, never a skip. Concretely verified to fail on: a missing baseline, a missing `PLAYWRIGHT_ENTRY`, a missing artifact, a shallow clone, and a visual change (measured 100% of pixels differing for a colour change, and card heights changing for a clipped control). The offline-claim phase is verified to fail when the `sandbox` attribute is removed and when a frame stops reporting.

The goldens exist because the generator's own assertions were not enough. Those all passed while the page was clipping buttons off the bottom of cards and stacking a layout written to be horizontal — only pixels catch that class. Each golden also asserts observable behaviour on the live page: no horizontal overflow, no card clipping its own controls, the embedded face actually in use, and that every project the page claims to list is present with a date range, a commit count and a card image that loaded.

Those content checks matter because the pixels alone are the wrong tool for facts. Commit counts and date ranges come from the projects' history, so a baseline goes stale the moment any project commits — the failure looks like a rendering regression and invites someone to re-accept a baseline that has stopped asserting anything. The page publishes a manifest of what it rendered; the suite reads it and verifies every claim against the project's own README, so a stale or lying manifest fails rather than being rubber-stamped. Verified to fail by mutation on: a dropped card, a blanked commit count, a corrupted embedded image, and a manifest that disagrees with a README.

Baselines live per engine (`tests/golden/firefox/`) with no cross-engine fallback. **Firefox is the only engine that launches in-sandbox**; Chromium renders only under an escalation and WebKit fails either way. Cross-engine baselines are therefore possible here, at the cost of one approval per session rather than never. `--update` rewrites baselines and prints them for review — nothing here should accept a baseline that nobody has looked at.

## Card images

A project nominates the picture its card shows with one inert line in its README:

```markdown
<!-- Front page image: docs/images/artifact.png -->
```

The path is relative to the project's own root. It is an HTML comment because the line has to be invisible to someone reading the README and still machine-readable here; `tools/generate.py` owns the pattern and `tools/make_thumbs.py` imports it, so there is one definition of the convention rather than two that can disagree.

The declared file must also be embedded in that README as a markdown image. That rule is the whole reason the cards can be trusted: the declaration then points at a picture the project already shows its own readers, rather than at any file in the tree. It replaced a heuristic that took the first image in the README, which failed twice in ways that only showed up after looking at the rendered page — it put a near-empty mid-bow frame on Aurelia's card, and it silently demoted Ridgeline, whose README links no images at all despite having committed golden frames. Choosing from the filesystem is a guess; a file the README shows is a statement.

Thumbnails are cut to 560x280 WebP and committed to `thumbs/`. They are committed rather than fetched at build time so the published page remains one file with no external requests, and so building it needs no imaging library. Regenerate deliberately:

```console
$ nix develop --command ./tools/make_thumbs.py --repos ..
```

A declared file that is missing, not embedded, absurdly large, or given as a URL fails the build. It does not fall back to the previous thumbnail, because a card quietly showing a stale frame is how an index starts lying. Sources are never upscaled, and the size budget is asserted rather than hoped for.

The framing override in `tools/make_thumbs.py` (`FRAMING`) exists for sources a centre crop reads badly on, and each entry carries its reason. It is deliberately not a smart crop: with three exceptions in eight projects, per-project judgement with a stated reason is less machinery and more honest than an algorithm that decides silently.

## Adding a project

Append its repository name to `REPOS` in `tools/generate.py` and to the clone loop in `.github/workflows/pages.yml`. These lists are deliberately separate rather than shared: the workflow list controls what gets cloned, the script list controls what must exist, and the script failing on a missing repo is the check that catches the two lists drifting apart. Position on the page needs no edit — it comes from the project's own history.

A project needs a `# Title` line and a descriptive first paragraph in its README. A browser preview must be advertised as a `raw.githack.com/.../index.html` URL, and is fetched at build time to confirm it serves. Projects without a browser artifact — the CLI tools — are listed with a Source link and no preview, which is stated rather than hidden.

To give a new project a colour and a motif, add entries to `ACCENTS` and `_KIND`; to give it a screenshot instead, add the `Front page image:` line described under [Card images](#card-images). An unlisted project still renders, with a neutral colour and a generic wave: a missing label degrades the page, it does not break the build.

Give it a one-word kind in `_KIND` as well — it appears on the card, in the contents bar, and selects the wording used when the project has no browser preview, so it is worth choosing from the project's own vocabulary rather than inventing a taxonomy (`stem` is a "Bench", not an "Instrument", because its README calls it a bench of stopped flue pipes).

## Deploying

`.github/workflows/pages.yml` builds and publishes to Pages. It runs on push, on `workflow_dispatch`, and nightly, because the inputs live in other repositories: a project can change its README without touching this one, and the page should follow without anyone remembering.

Credentials come from GitHub's own per-run `GITHUB_TOKEN`, scoped to `contents: read`, `pages: write`, `id-token: write` — the minimum `actions/deploy-pages` needs. No personal access token is read, stored or logged by anything in this repository.

## Verified, and what is not

- Built and rendered locally at 1280x900, 390x844 and 844x390; viewed in all three. Desktop, portrait and landscape baselines are committed.
- Chronology verified against real git history for all eight projects, including the `nebulabrot` case where the API would have ordered it wrong.
- Preview link fetching, and therefore the whole CI-only path, cannot run in this container: it has no DNS. It has only ever been exercised by CI.
- The glyph-coverage assertion is skipped when `fonttools` is unavailable, which includes the CI runner. Only CI would notice a regression in what CI itself builds; the always-on check is that each embedded face is genuine WOFF2.
