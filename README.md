# thaumapolis.github.io

Source of the organisation front page at **<https://thaumapolis.github.io/>**.

An organisation's root Pages site publishes from a repository named exactly `<org>.github.io` — here `thaumapolis.github.io`. This repo was created as `.github` instead, on the mistaken belief that the name also selects the front page. It does not: `.github` is the special repository for community health content, and as a Pages source it publishes to `thaumapolis.github.io/.github/` — a project site, not the root. Renaming the repository moved it to the root; no workflow or generator change was needed. The forfeit is the `.github` role, since the two names are mutually exclusive.

What gets published is generated, so this repo holds a generator rather than a gallery: **no project description, screenshot or URL is written down here.**

## What the page is

A list of the organisation's projects in the order they were started, oldest first, each with the date range its commits span, its commit count, and a link to open it. The thumbnails are generated inline SVG in each project's colour, drawn from what the project is — a waterline for the pond, pipes for the organ, a ridge for the tower — rather than screenshots, so nothing here is a frozen picture of a thing that moves, and nothing needs to be committed and kept current.

## How it works

`tools/generate.py` reads each project's own README and derives its title, description and preview link from it, then emits `index.html`. `--repos` points at a directory of checkouts; `--out` says where the page goes. `--skip-preview-checks` is for offline development and prints a warning, because a page built with it may advertise a dead link.

Deriving rather than hand-writing is about drift. A hand-kept index restates facts that live in other repositories, and this workspace has already spent a session chasing an organisation name copied into a README, a test and a shell script. Here there is one copy of each fact: the project's README. Edit that and the front page follows at the next build.

The build **fails loudly** rather than publishing something plausible with a hole in it: a missing project, a README with no title or descriptive paragraph, an advertised preview link that stops serving HTML, a shallow clone, or an embedded font that is not a font.

### Ordering

Order comes from each project's first commit, read from its clone. The GitHub API's `created_at` is not used: it is repo creation, not when the work started, and it disagrees with the first commit on several projects — enough to reorder the page, which it does to `nebulabrot`.

This makes full history load-bearing, so the workflow clones without `--depth`. A shallow clone reports one commit and one date, so every project looks a day old and the chronology silently becomes a tie — and the page still publishes, because nothing else notices. `tools/generate.py` therefore refuses to build against a shallow clone. Verified against real one-commit clones, which it rejects with a message naming the repos and the fix.

`ORDER` used to be a hand-maintained list that fixed the display order. It is now `REPOS`, which says only what must exist; position follows from the history.

### Fonts

The page embeds its own two faces as base64 WOFF2, generated from DejaVu Sans by a subsetter and committed as `faces/*.base64`.

This is not decoration. A `system-ui` stack resolves to whatever the visitor's machine provides, so the layout being designed is not the layout anyone sees; and this container ships one font, so an unembedded page renders differently locally, in CI and on a phone. `tools/generate.py` checks the characters the page can emit against the faces that are actually embedded, and says which glyph is missing — see the note in `tests/` about what that check does not cover.

## Checking it

`./check.sh --repos ..` runs lint, the build and the golden screenshots. Run it inside the pinned toolchain:

```console
$ nix develop --command ./check.sh --repos ..
```

Every phase runs or fails the build; a phase that cannot run is a red result, never a skip. Concretely verified to fail on: a missing baseline, a missing `PLAYWRIGHT_ENTRY`, a missing artifact, a shallow clone, and a visual change (measured 100% of pixels differing for a colour change, and card heights changing for a clipped control).

The goldens exist because the generator's own assertions were not enough. Those all passed while the page was clipping buttons off the bottom of cards and stacking a layout written to be horizontal — only pixels catch that class. Each golden also asserts observable behaviour on the live page: no horizontal overflow, no card clipping its own controls, the embedded face actually in use, the expected number of cards and links.

Baselines live per engine (`tests/golden/firefox/`) with no cross-engine fallback. **Firefox is the only engine that starts in this container**; Chromium and WebKit fail to launch. `--update` rewrites baselines and prints them for review — nothing here should accept a baseline that nobody has looked at.

## Adding a project

Append its repository name to `REPOS` in `tools/generate.py` and to the clone loop in `.github/workflows/pages.yml`. These lists are deliberately separate rather than shared: the workflow list controls what gets cloned, the script list controls what must exist, and the script failing on a missing repo is the check that catches the two lists drifting apart. Position on the page needs no edit — it comes from the project's own history.

A project needs a `# Title` line and a descriptive first paragraph in its README. A browser preview must be advertised as a `raw.githack.com/.../index.html` URL, and is fetched at build time to confirm it serves. Projects without a browser artifact — the CLI tools — are listed with a Source link and no preview, which is stated rather than hidden.

To give a new project a colour and a thumbnail motif, add entries to `ACCENTS` and `_KIND`. An unlisted project still renders, with a neutral colour and a generic wave: a missing label degrades the page, it does not break the build.

## Deploying

`.github/workflows/pages.yml` builds and publishes to Pages. It runs on push, on `workflow_dispatch`, and nightly, because the inputs live in other repositories: a project can change its README without touching this one, and the page should follow without anyone remembering.

Credentials come from GitHub's own per-run `GITHUB_TOKEN`, scoped to `contents: read`, `pages: write`, `id-token: write` — the minimum `actions/deploy-pages` needs. No personal access token is read, stored or logged by anything in this repository.

## Verified, and what is not

- Built and rendered locally at 1280x900, 390x844 and 844x390; viewed in all three. Desktop, portrait and landscape baselines are committed.
- Chronology verified against real git history for all eight projects, including the `nebulabrot` case where the API would have ordered it wrong.
- Preview link fetching, and therefore the whole CI-only path, cannot run in this container: it has no DNS. It has only ever been exercised by CI.
- The glyph-coverage assertion is skipped when `fonttools` is unavailable, which includes the CI runner. Only CI would notice a regression in what CI itself builds; the always-on check is that each embedded face is genuine WOFF2.
