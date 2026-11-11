# thaumapolis.github.io

Source of the organisation front page at **<https://thaumapolis.github.io/>**.

An organisation's root Pages site is published from a repository named exactly `<org>.github.io` — here `thaumapolis.github.io`. This repo was created as `.github` instead, on the mistaken belief that the name also selects the org front page. It does not: `.github` is the special repository for community health content (issue and pull request templates, the org profile README), and as a Pages source it publishes to `thaumapolis.github.io/.github/` — a project site, not the root. Renaming the repository to `thaumapolis.github.io` moved it to the root; no workflow or generator change was needed. The forfeit is the `.github` role: those two names are mutually exclusive, so community health templates need a separate `.github` repo if they are wanted later.

What gets published is generated, so this repo holds a generator rather than a gallery: **no project description, screenshot or URL is written down here.**

## How it works

`tools/generate.py` clones each project's own README and derives its title, description and preview link from it, then emits `index.html`. `--repos` points at a directory of checkouts; `--out` says where the page goes.

The reason for deriving instead of hand-writing is drift. A hand-kept index restates facts that live in other repositories, and this workspace has already spent a session chasing an organisation name that had been copied into a README, a test and a shell script. Here there is one copy of each fact: the project's README. Edit that and the front page follows at the next build.

The build **fails loudly** instead of publishing something plausible with a hole in it: a missing project, a README with no title or descriptive paragraph, or an advertised preview link that stops serving HTML all stop the deploy.

`index.html` is not committed. Committing a generated page would create a second copy that can disagree with the projects it describes.

## Adding a project

Append its repository name to `ORDER` in `tools/generate.py` and to the loop in `.github/workflows/pages.yml`. These two lists are deliberately separate rather than shared: the workflow list controls what gets cloned, the script list controls what must exist, and the script failing on a missing repo is the check that catches the two lists drifting apart.

A project needs a `# Title` line and a descriptive first paragraph in its README. If it advertises a browser preview, the link must be a `raw.githack.com/.../index.html` URL, and it is fetched at build time to confirm it serves. Projects without a browser artifact — the CLI tools — are listed with a Source link and no preview.

## Deploying

`.github/workflows/pages.yml` builds and publishes to Pages. It runs on push, on `workflow_dispatch`, and nightly, because the inputs live in other repositories: a project can change its README without touching this one, and the page should follow without anyone remembering.

Credentials come from GitHub's own per-run `GITHUB_TOKEN`, scoped to `contents: read`, `pages: write`, `id-token: write` — the minimum `actions/deploy-pages` needs. No personal access token is read, stored, or logged by anything in this repository.
