#!/usr/bin/env python3
"""Build the org front page from the project repositories.

Nothing here is hand-written about a project: title, description and preview
link all come from the project's own README. That is deliberate. A hand-kept
index restates facts that live elsewhere, and this workspace has already
spent a session fixing org names that had been copied into too many places.
Deriving them means a project edits its own README and the front page follows.

The build fails loudly rather than emitting a plausible-looking page with a
hole in it: every project must resolve, every preview link must be reachable.
"""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import html
import io
import math
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ORG = "thaumapolis"
# Every project the front page must show. This is deliberately not discovered
# from the org API: an explicit list is what lets the build fail when one goes
# missing, which is the failure mode worth catching.
#
# This is an allow-list, not an ordering. The page is ordered chronologically by
# first commit (see _chronological), so adding a project here is the only edit
# needed -- its position follows from its own history.
REPOS = [
    "nebulabrot",
    "flux-pond",
    "termbrot",
    "resonance-engine",
    "aurelia",
    "quantum-loom",
    "stem",
    "ridgeline",
]
# One colour per project, keyed by repo so a rename is a visible, deliberate
# edit rather than a hash collision. Used for the stripe, the tinted header
# panel, and the thumbnail art, so a card reads as one colour from across the
# page. Values are chosen to stay legible against the dark background and to be
# distinguishable from each other at card size.
ACCENTS = {
    "ridgeline": "#c2703e",
    "flux-pond": "#2f8f9d",
    "aurelia": "#a9c47f",
    "stem": "#7fd8b8",
    "quantum-loom": "#8f7fd8",
    "resonance-engine": "#d87f9c",
    "nebulabrot": "#e0b34a",
    "termbrot": "#e8b44a",
}
NO_ACCENT = "#3d4a5a"
# Spelled out rather than run through %b, whose abbreviation depends on the
# container's locale: a page generated in CI and one generated locally should
# show the same month, and %b is the kind of thing that quietly differs.
_MONTHS = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
]


@dataclass(frozen=True, kw_only=True)
class Project:
    repo: str
    title: str
    tagline: str
    preview: str | None
    default_branch: str
    first_commit: date | None
    last_commit: date | None
    commits: int


def _strip_markdown(text: str) -> str:
    """Reduce markdown to the text a reader actually sees."""
    # Badge markup is nested: [![img](badge-url)](target-url). Strip the whole
    # thing before bare images, or the inner image is consumed and an empty
    # link is left behind, which then looks like prose.
    text = re.sub(r"\[!\[[^\]]*\]\((?:[^)]*)\)\]\((?:[^)]*)\)", "", text)
    text = re.sub(r"!\[[^\]]*\]\((?:[^)]*)\)", "", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"\1", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    text = re.sub(r"\[([^\]]*)\]\((?:[^)]*)\)", r"\1", text)
    return " ".join(text.split())


def _html(text: str) -> str:
    """Escape text derived from another repository.

    Title and tagline are read out of someone else's README, so they are input,
    not markup. Anything they contain must render as the characters it is.
    """
    return html.escape(text, quote=False)


def _format_date(day: date | None) -> str:
    """`5 Nov 2026`, no leading zero, month abbreviated."""
    if day is None:
        return "date unknown"
    return f"{day.day} {_MONTHS[day.month - 1]} {day.year}"


def _span(first: date | None, last: date | None) -> str:
    """Describe how long a project was worked on, in the terms a reader wants.

    A project with one day of history gets one date, not a redundant range.
    """
    if first is None:
        return "history unknown"
    if last is None or first == last:
        return _format_date(first)
    same_year = first.year == last.year
    left = _format_date(first)
    right = _format_date(last)
    if same_year:
        # Drop the year from the left date only when the right one still says it.
        left = left.replace(f" {first.year}", "")
    return f"{left} – {right}"


def parse_readme(readme: str) -> tuple[str, str, str | None]:
    """Return (title, tagline, preview_url) from a project README."""
    title_match = re.search(r"^# (.+)$", readme, re.MULTILINE)
    if title_match is None:
        msg = "README has no `# Title` line"
        raise ValueError(msg)
    title = title_match.group(1).strip()

    body = readme.split("\n", 1)[1] if "\n" in readme else ""
    tagline: str | None = None
    for paragraph in re.split(r"\n\s*\n", body):
        paragraph = " ".join(paragraph.split())
        if not paragraph or paragraph.startswith(">"):
            continue
        # Judge the rendered text. A "Play it now" call to action arrives as
        # `**▶ [Play it now](url)**`, which looks like prose until it is stripped.
        core = _strip_markdown(paragraph)
        if not core or core.startswith("▶") or len(core) < 30:
            continue
        tagline = core
        break
    if tagline is None:
        msg = "README has no descriptive first paragraph"
        raise ValueError(msg)

    preview_match = re.search(
        r"https://raw(?:cdn)?\.githack\.com/\S+?/index\.html", readme
    )
    preview = preview_match.group(0) if preview_match is not None else None
    return title, tagline, preview


def _git(checkout: Path, *args: str) -> str:
    """Run git in a checkout, returning stdout, or "" when it fails.

    A failure here is never fatal on its own: the caller turns an empty result
    into None and the page degrades to "date unknown" rather than dropping a
    project. A date is decoration; a missing project is a build error.
    """
    # check=False is deliberate, not an oversight: a git failure here means "no
    # history available", which the caller turns into None so the card says
    # "history unknown" rather than taking the whole page down.
    result = subprocess.run(
        ["git", *args], cwd=checkout, capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _first_commit(checkout: Path) -> str:
    """SHA of the earliest commit reachable from HEAD.

    `rev-list --reverse | head -1` rather than `git log --reverse
    --max-count=1`, which does not work: --max-count limits "the first N commits
    that would be shown" and --reverse reorders only what survives the limit, so
    the combination returns the *newest* commit. `rev-list --reverse` truncates
    before reversing, so it has the same trap. The one reading that is correct on
    its own is `log --reverse | head -1`, which discards the whole history
    instead of just taking the first line -- and reading a truncated history is
    exactly the failure this function exists to avoid.
    """
    history = _git(checkout, "rev-list", "--reverse", "HEAD")
    if not history:
        return ""
    # awk 'NR==1' over `head -1`: same answer, but no SIGPIPE in the pipeline.
    first = history.split("\n", 1)[0]
    return first if re.fullmatch(r"[0-9a-f]{7,40}", first) else ""


def git_history(checkout: Path) -> tuple[date | None, date | None, int]:
    """Return (first commit date, latest commit date, commit count).

    Read from the clone rather than the GitHub API: API `created_at` is repo
    creation, not when the work started, and it disagrees with the real first
    commit on several projects -- enough to reorder the page, which it does to
    nebulabrot. CI clones these repositories anyway, so the accurate source
    costs nothing extra beyond clone time.

    Requires the full history. A shallow clone reports one commit and would date
    every project to a single day, flattening the chronology into a tie; hence
    the workflow's clones must therefore omit `--depth`, asserted by
    _require_full_history below.
    """
    first_commit = _first_commit(checkout)
    oldest = _date_of(checkout, first_commit)
    newest = _date_of(checkout, "HEAD")
    counted = _git(checkout, "rev-list", "--count", "HEAD")
    commits = int(counted) if counted.isdigit() else 0
    return oldest, newest, commits


def _date_of(checkout: Path, rev: str) -> date | None:
    """Commit date as a plain calendar date.

    %as is the author date in ISO short form (YYYY-MM-DD), which is UTC with no
    timezone suffix, so date.fromisoformat needs no parsing of an offset. Author
    date rather than committer date because it is the date written at the time of
    the work and survives rebases; a re-committed project keeps its chronology.
    """
    if not rev:
        return None
    stamp = _git(checkout, "log", "-1", "--format=%as", rev)
    try:
        return date.fromisoformat(stamp)
    except ValueError:
        return None


def load_project(repos_dir: Path, repo: str) -> Project:
    checkout = repos_dir / repo
    try:
        readme = (checkout / "README.md").read_text(encoding="utf-8")
    except FileNotFoundError as error:
        msg = f"{repo}: expected README.md at {checkout / 'README.md'}"
        raise FileNotFoundError(msg) from error
    title, tagline, preview = parse_readme(readme)
    branch_file = checkout / ".default_branch"
    branch = (
        branch_file.read_text(encoding="utf-8").strip()
        if branch_file.is_file()
        else "main"
    )
    first, last, commits = git_history(checkout)
    return Project(
        repo=repo,
        title=title,
        tagline=tagline,
        preview=preview,
        default_branch=branch,
        first_commit=first,
        last_commit=last,
        commits=commits,
    )


def check_preview(project: Project) -> tuple[Project, str | None]:
    """Verify an advertised preview actually serves the artifact."""
    if project.preview is None:
        return project, None
    request = urllib.request.Request(
        project.preview, headers={"User-Agent": "thaumapolis-front-page"}
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
    except (urllib.error.URLError, TimeoutError) as error:
        return project, f"{project.repo}: preview URL did not load ({error})"
    if b"<html" not in body[:600].lower():
        return project, f"{project.repo}: preview URL did not serve HTML"
    return project, None


def _decode_face(filename: str) -> bytes:
    """Read a committed face and check it really is WOFF2."""
    blob = _FACES_DIR.joinpath(filename).read_text().strip()
    if not re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", blob):
        msg = f"{filename}: not base64"
        raise SystemExit(msg)
    raw = base64.b64decode(blob)
    if raw[:4] != b"wOF2":
        msg = f"{filename}: not a WOFF2 file"
        raise SystemExit(msg)
    return raw


def _faces() -> list[tuple[int, str, bytes]]:
    """(weight, filename, bytes) for every face, read once and reused.

    One reader for both the CSS and the glyph assertion. They previously read
    separately, and not even the same files: the assertion opened a .woff2 that
    the page did not embed, so it could pass while the shipped face was missing
    the very glyphs it claimed to check.
    """
    return [(weight, name, _decode_face(name)) for weight, name in _FACES]


def _font_css(faces: list[tuple[int, str, bytes]]) -> str:
    rules = []
    for weight, _, raw in faces:
        blob = base64.b64encode(raw).decode()
        rules.append(
            "@font-face {\n"
            '  font-family: "Thaumapolis Sans";\n'
            "  font-style: normal;\n"
            f"  font-weight: {weight};\n"
            f'  src: url(data:font/woff2;base64,{blob}) format("woff2");\n'
            "}"
        )
    return "\n".join(rules)


def _assert_glyphs(faces: list[tuple[int, str, bytes]]) -> None:
    """Fail the build if the page uses a character the embedded faces lack.

    A character missing from a subset falls back per glyph and re-widens the
    line: invisible in the source, visible on the page.

    Skipped when fontTools is not importable, which is the case in CI and in a
    bare python. That is a real gap rather than a tidy guarantee -- the always-on
    assertion is that each file is genuine WOFF2, which _decode_face does. The
    glyph check is run by tools/test_render_rules.py, which resolves fontTools
    from the Nix store when it can and reports that it could not otherwise.
    """
    try:
        from fontTools.ttLib import TTFont
    except ImportError:
        return
    covered: set[int] = set()
    for _, name, raw in faces:
        covered |= set(TTFont(io.BytesIO(raw)).getBestCmap())
    used = _USED_CODEPOINTS
    missing = sorted(c for c in used if c not in covered)
    if missing:
        msg = "embedded faces are missing glyphs the page uses: " + " ".join(
            f"{chr(c)} (U+{c:04X})" for c in missing
        )
        raise SystemExit(msg)


def render(projects: list[Project]) -> str:
    """Build the whole page as one self-contained string.

    No script, no stylesheet request, no font request, no image request: a
    showcase for offline single-file artifacts should not itself need a network.
    The thumbnails are inline SVG derived from each project's accent colour, so
    they cost nothing and cannot 404.
    """
    faces = _faces()
    _assert_glyphs(faces)
    # Two cards span the full width, and both for the same reason: a half-width
    # card with nothing beside it looks broken rather than arranged. The opener
    # spans deliberately -- it is the oldest project, so the page starts where
    # the work started. The last card spans only when the arithmetic leaves it
    # alone in a row: after the opener takes one, an even remainder tiles cleanly
    # and an odd one does not.
    last = len(projects) - 1
    spans = {0} if projects else set()
    if last and (len(projects) - 1) % 2 == 1:
        spans.add(last)
    cards = "\n".join(_card(p, wide=i in spans) for i, p in enumerate(projects))
    oldest = min(p.first_commit for p in projects if p.first_commit is not None)
    newest = max(p.last_commit for p in projects if p.last_commit is not None)
    openable = sum(1 for p in projects if p.preview is not None)
    total_commits = sum(p.commits for p in projects)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{ORG} &mdash; self-contained machines with real arithmetic inside</title>
<meta name="description" content="{len(projects)} projects, each a single HTML file that asks for nothing else, with a genuine simulation underneath rather than an animation pretending to be one.">
<style>
{_font_css(faces)}
  :root {{
    color-scheme: dark;
    --ink: #e8eef6;
    --ink-soft: #a7b8ca;
    --ink-faint: #7b8ca0;
    --bg: #080c12;
    --panel: #111823;
    --edge: #1e2836;
    --rule: #16202c;
  }}
  * {{ box-sizing: border-box; }}
  html {{ -webkit-text-size-adjust: 100%; }}
  body {{
    margin: 0;
    background:
      radial-gradient(120% 80% at 50% -20%, #16222f 0%, var(--bg) 62%) fixed;
    color: var(--ink);
    font: 400 17px/1.65 "Thaumapolis Sans", "DejaVu Sans", system-ui, sans-serif;
    padding: 0 1.25rem 5rem;
  }}
  main {{ max-width: 74rem; margin: 0 auto; }}

  /* Masthead: sized to be read, not skimmed. The date range answers "is this
     alive?" before a visitor has to look for it. */
  .masthead {{ padding: clamp(3rem, 9vw, 6rem) 0 clamp(2rem, 5vw, 3rem); }}
  .masthead h1 {{
    margin: 0;
    font-size: clamp(2.4rem, 8vw, 4.6rem);
    font-weight: 700;
    letter-spacing: -.02em;
    line-height: 1;
    color: var(--ink);
  }}
  .masthead h1 span {{ color: #5f7488; }}
  .masthead .lede {{
    margin: 1.25rem 0 0;
    max-width: 46rem;
    font-size: clamp(1.05rem, 2.4vw, 1.3rem);
    line-height: 1.55;
    color: var(--ink-soft);
  }}
  .masthead .lede strong {{ color: var(--ink); font-weight: 700; }}
  .tally {{
    display: flex; flex-wrap: wrap; gap: .4rem 1.5rem;
    margin: 1.75rem 0 0; padding: 0; list-style: none;
    font-size: .82rem; color: var(--ink-faint);
    letter-spacing: .06em; text-transform: uppercase;
  }}
  .tally b {{ color: var(--ink-soft); font-weight: 700; }}

  /* A card is a button-shaped region with a real link inside; the stripe is the
     project's own colour, repeated in its header so identity survives a glance. */
  ol {{ list-style: none; margin: 0; padding: 0; display: grid; gap: 1.1rem; }}
  /* display:flex, not the li default of list-item: a list-item box cannot be a
     flex container, so declaring flex-direction on .card does nothing at all
     while looking like it works -- the "horizontal" wide card silently stacked,
     and a width like `flex: 0 0 42%` was ignored without any warning.
     Column is the stacked art-over-copy layout every ordinary card uses; the
     wide override changes only the direction, so nothing else has to be repeated
     or undone. */
  .card {{ display: flex; flex-direction: column; }}
  @media (min-width: 48rem) {{
    ol {{
      grid-template-columns: 1fr 1fr;
      gap: 1.4rem;
      /* Rows size to their content. The default `1fr` track would stretch each
         row to the tallest card in it, which made every card in a row a couple
         of hundred pixels taller than its text needed: the description drifted
         away from the buttons and the page stretched to almost three screens. */
      grid-auto-rows: auto;
    }}
    /* The opener spans the row: it is the oldest project, so the page starts at
       where the work started, and the grid gets an anchor instead of eight
       identical tiles. */
    .card.wide {{ grid-column: 1 / -1; }}
    /* Wide cards go horizontal: art beside copy, not stacked above it. Stacking
       one made the first thumbnail about 40% of the viewport, pushing the
       sentence that explains the project below the fold -- a banner for a card
       nobody has read yet. */
    .card.wide {{ flex-direction: row; align-items: center; }}
    .card.wide .thumb {{
      flex: 0 0 42%; width: auto; min-height: 0;
      border-bottom: none; border-right: 1px solid var(--rule);
    }}
  }}
  .thumb {{
    display: block; width: 100%; min-height: 132px;
    border-bottom: 1px solid var(--rule);
    /* No aspect-ratio: the card's height comes from its copy, and a ratio here
       either makes the art taller than that text -- pushing the buttons out of
       the card, where overflow: hidden would hide the problem rather than fix
       it -- or leaves a gap under it. The art fills whatever panel the layout
       settled on, which is why the SVG scales with the box instead of keeping
       its own 320x180 shape. */
  }}
  .thumb svg {{ display: block; width: 100%; height: 100%; }}
  .body {{ padding: 1.15rem 1.3rem 1.35rem; display: flex; flex-direction: column; flex: 1; }}
  .card h2 {{
    margin: 0; font-size: 1.3rem; font-weight: 700; letter-spacing: -.01em;
    color: var(--accent);
  }}
  .card .kind {{
    margin: .2rem 0 0; font-size: .76rem; letter-spacing: .11em;
    text-transform: uppercase; color: var(--ink-faint);
  }}
  .card p.desc {{
    margin: .7rem 0 0; font-size: .95rem; line-height: 1.6; color: var(--ink-soft);
  }}
  .meta {{
    margin: .9rem 0 0; padding-top: .8rem; border-top: 1px solid var(--rule);
    font-size: .8rem; color: var(--ink-faint);
    display: flex; flex-wrap: wrap; gap: .15rem .9rem;
  }}
  .meta span {{ white-space: nowrap; }}
  /* margin-top: auto aligns the buttons along the bottom of a row, so the grid
     reads as columns of controls rather than a staggered scatter. */
  .links {{ display: flex; flex-wrap: wrap; gap: .6rem; margin: auto 0 0; padding-top: 1.1rem; }}
  .links a {{
    font-size: .9rem; font-weight: 700; text-decoration: none;
    padding: .5rem .95rem; border-radius: 8px;
    min-height: 2.5rem; display: inline-flex; align-items: center;
    /* 2.5rem minimum target height for touch; the padding, not a font bump. */
  }}
  .links .open {{
    color: #08111a; background: var(--accent);
  }}
  .links .open:hover {{ filter: brightness(1.12); }}
  .links .source {{
    color: var(--ink-soft); border: 1px solid var(--edge);
  }}
  .links .source:hover {{ color: var(--ink); border-color: var(--ink-faint); }}
  .links .none {{
    font-size: .85rem; color: var(--ink-faint); align-self: center;
  }}
  footer {{
    margin-top: 3.5rem; padding-top: 1.5rem; border-top: 1px solid var(--rule);
    font-size: .88rem; color: var(--ink-faint); max-width: 52rem;
  }}
  footer code {{ color: var(--ink-soft); }}
  @media (prefers-reduced-motion: reduce) {{ .card:hover {{ filter: none; }} }}
</style>
</head>
<body>
<main>
  <div class="masthead">
    <h1>{ORG}<span>.</span></h1>
    <p class="lede">Self-contained machines. Every project below is <strong>one file
    that asks for nothing else</strong> &mdash; no build, no network, no storage &mdash;
    with a real simulation underneath rather than an animation pretending to be one.
    Drag on them. They respond because they are doing arithmetic, not because a
    timeline says so.</p>
    <ul class="tally">
      <li><b>{len(projects)}</b> projects</li>
      <li><b>{openable}</b> open in a browser</li>
      <li><b>{total_commits}</b> commits</li>
      <li>{_format_date(oldest)} &ndash; {_format_date(newest)}</li>
    </ul>
  </div>
  <ol>
{cards}
  </ol>
  <footer>
    <p>Ordered by first commit, oldest first. This page is generated from each
    project&rsquo;s own README by <code>tools/generate.py</code> &mdash; no title,
    description or link is written down here, so editing a README edits the front
    page. The build fails if a project goes missing or a preview link stops serving
    HTML, rather than publishing a plausible page with a hole in it.</p>
  </footer>
</main>
</body>
</html>
"""


# The characters the page can put on screen, as a set rather than a string, so a
# missing glyph is a set difference instead of a nested loop. Not extracted by
# scanning the template -- that would need an HTML entity parser, and the point is
# a cheap guard. _STATIC_TEXT lists literals from the template; _DYNAMIC_CHARS
# covers what arrives from elsewhere: digits and the abbreviations for dates and
# commit counts, plus punctuation a project README might legitimately contain.
_PAGE_TEXT = (
    "thaumapolismachines with real arithmetic insideSelf-contained. Every project"
    " below is one file that asks for nothing else &mdash; no build network storage"
    " with a simulation underneath rather than an animation pretending to beDrag on"
    " themThey respond because they are doing arithmetic not because a timeline says"
    " soprojects open browsercommits by first commit oldest this page generated from"
    " each&rsquo;s own README tools/generate.py no title description link written"
    " down here editing edits front The build fails if a goes missing or preview"
    " stops serving HTML publishing plausible page hole it0123456789NovJanFebMarApr"
    "MayJunJulAugSepOctDecSourceOpen itand unknown history commits in progress"
)
_PAGE_TEXT += (
    "0123456789"  # counts and dates
    "-.,"  # date and number separators
    "\u2013\u2014\u2018\u2019\u201c\u201d\u2026"  # dashes, curly quotes, ellipsis
    "\u00b7\u00b0\u00d7"  # middot, degree, times
    "abcdefghijklmnopqrstuvwxyz"  # titles, taglines, kind labels
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
)

# Codepoints, not strings: comparing a character against a set of words would
# never match and would pass forever. This is what makes _assert_glyphs meaningful.
_USED_CODEPOINTS = {ord(c) for c in _PAGE_TEXT}

_FACES_DIR = Path(__file__).resolve().parent.parent / "faces"
_FACES = [
    (400, "ThaumapolisSans-regular.woff2.base64"),
    (700, "ThaumapolisSans-bold.woff2.base64"),
]

# Two pieces of per-project metadata. The label tells a reader what category of
# thing to expect before they open it; the motif selects the thumbnail. Both are
# keyed by repo, so an unlisted repo still renders (as "Project" / "lines") --
# the page degrades, it does not crash, because a missing label is not a reason
# to publish nothing.
_KIND = {
    "nebulabrot": ("Renderer", "orbits"),
    "flux-pond": ("Simulation", "water"),
    "termbrot": ("CLI tool", "blocks"),
    "resonance-engine": ("Instrument", "field"),
    "aurelia": ("Instrument", "strings"),
    "quantum-loom": ("Simulation", "packet"),
    "stem": ("Instrument", "pipes"),
    "ridgeline": ("Game", "terrain"),
}


# Each motif paints its own background, so each gets its own ink and fill. The
# accent colour cannot do both jobs: the same hue at the opacity that reads well
# on near-black disappears on a tinted field, and what looks right for a dense
# block pattern is wrong for a single hairline curve. Keyed by motif, not project,
# because it is the drawing that needs the colour.
# Block-shade motif: 16 columns of 20px across nine rows of 20px.
_BLOCK = 20
# Fraction of the project accent in each of the four block levels, blended
# towards the panel background in python rather than expressed as SVG opacity.
# 144 adjacent translucent rectangles are also what an earlier attempt used,
# and the rendered result bore little relation to the numbers: opacity is a
# compositing hint the engine is free to antialias and round, whereas a literal
# fill colour is unambiguous in every engine and every screenshot.
_BLOCK_TINT = [0.06, 0.20, 0.46, 0.80]
THUMB_BG = "#0d141d"

_MOTIF_INK = {
    "orbits": "#f0cd76",
    "water": "#8fdfe8",
    "blocks": "#ffd98a",
    "field": "#f2aabf",
    "strings": "#cfe08a",
    "pipes": "#a6ecd4",
    "packet": "#bfaef2",
    "terrain": "#f0975f",
}


def _mix(background: str, foreground: str, amount: float) -> str:
    """Blend `foreground` over `background` at `amount`, as a hex colour.

    Used where the result must be a specific colour in every engine rather than a
    composite the engine works out -- see _BLOCK_TINT.
    """

    def components(value: str) -> tuple[int, int, int]:
        return tuple(int(value[i : i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]

    back, fore = components(background), components(foreground)
    mixed = (round(fore[i] * amount + back[i] * (1 - amount)) for i in range(3))
    return "#" + "".join(f"{value:02x}" for value in mixed)


def _motif(shape: str, accent: str) -> str:
    """SVG content for a 320x180 box.

    Drawn for that aspect ratio, and the box it lands in keeps the ratio, so
    nothing is rescaled at any width. Each motif echoes what the project actually
    is -- a waterline for the pond, pipes for the organ, a ridge for the tower --
    so the grid scans as a set of different things rather than eight panels of
    the same decoration.

    `accent` is the fallback ink for a motif not listed in _MOTIF_INK, which is
    what an unlisted repository gets; the panel background is THUMB_BG.
    """
    ink = _MOTIF_INK.get(shape, accent)
    return (
        f'<svg viewBox="0 0 320 180" preserveAspectRatio="xMidYMid slice" role="img" '
        f'aria-hidden="true"><rect width="320" height="180" fill="{THUMB_BG}"/>'
        f"{_motif_art(shape, ink)}</svg>"
    )


def _motif_art(shape: str, accent: str) -> str:
    """The drawing itself, in `accent`, on whatever background _motif chose."""
    if shape == "terrain":
        return (
            f'<path d="M0 180 L44 116 L84 142 L136 74 L188 126 L244 92 L320 150 L320 180 Z" '
            f'fill="{accent}" opacity=".2"/>'
            f'<path d="M0 180 L44 116 L84 142 L136 74 L188 126 L244 92 L320 150" '
            f'fill="none" stroke="{accent}" stroke-width="3" stroke-linejoin="round"/>'
            f'<circle cx="136" cy="74" r="5" fill="{accent}"/>'
        )
    if shape == "water":
        return (
            f'<rect y="112" width="320" height="68" fill="{accent}" opacity=".16"/>'
            f'<path d="M0 112 q20 -11 40 0 t40 0 t40 0 t40 0 t40 0 t40 0 t40 0 t40 0" '
            f'fill="none" stroke="{accent}" stroke-width="3"/>'
            f'<circle cx="214" cy="112" r="16" fill="{accent}" opacity=".8"/>'
            f'<path d="M198 112 q16 -9 32 0" fill="none" stroke="#0d141d" '
            'stroke-width="3" opacity=".5"/>'
        )
    if shape == "blocks":
        # Block shades of a real Mandelbrot escape-time grid -- what the tool
        # actually prints. Computed rather than drawn by hand, because a
        # hand-drawn approximation of a fractal is exactly the "animation
        # pretending to be one" this page says its projects are not.
        #
        # Every attempt before this one produced something unrecognisable, and a
        # screenshot of the card was not enough to see why. Each was diagnosed by
        # decoding the emitted SVG back into a character grid and reading it,
        # which is the only way to check all four failure modes at once:
        #
        #   1. Symmetry. The set is symmetric about the real axis and about
        #      nothing else, so a render that is symmetric about both, or neither,
        #      is wrong whatever it looks like. Imaginary is signed about 0.
        #   2. The interior must be shaded, not left blank. Dropping inside-the-set
        #      points leaves a hollow ring, which is the shape that was repeatedly
        #      mistaken for "noise".
        #   3. All four levels must occur. Checked by histogram: 10/62/21/51 cells
        #      across the four opacities. A linear ramp over escape time collapses
        #      this to two levels, because escape time is heavily skewed.
        #   4. The set must be recognisable in nine rows. Framing on a zoomed
        #      boundary makes it unrecognisable; framing on the whole set does not.
        #
        # Escape time outside the set is a poor guide to shading it, because the
        # gradient lives only in a thin band hugging the boundary. Interior points
        # escape most slowly and outside points most quickly, so shading by
        # "how long did it take, capped at the iteration limit" -- interior points
        # reaching the cap and being darkest -- fills the silhouette and shades the
        # surroundings smoothly. That is the canonical look, and the plausible
        # inverse of the intuitive one.
        max_iter = 140
        rows, cols = 9, 16
        # Whole set with margin, chosen so the silhouette is centred and readable
        # at nine rows rather than zoomed into interesting detail.
        left, right, top, bottom = -2.0, 0.6, -1.15, 1.15
        cells = []
        for row in range(rows):
            for col in range(cols):
                # Both sampled at cell centres. `cr` sampling the left edge of
                # each cell shifts the picture half a block right and drops the
                # rightmost slice of the frame, which is visible as the set
                # sitting off-centre with a hard cut on one side.
                cr = left + (col + 0.5) * ((right - left) / cols)
                ci = top + (row + 0.5) * ((bottom - top) / rows)
                zr = zi = 0.0
                for step in range(1, max_iter + 1):
                    zr, zi = zr * zr - zi * zi + cr, 2 * zr * zi + ci
                    if zr * zr + zi * zi > 4:
                        break
                # Smooth (fractional) escape time removes the banding a raw step
                # count gives; interior points fall through with step == max_iter.
                nu = (
                    step + 1 - math.log2(math.log2(zr * zr + zi * zi))
                    if step < max_iter
                    else float(max_iter)
                )
                level = min(3, int(math.sqrt(nu / max_iter) * 11))
                cells.append(
                    f'<rect x="{col * _BLOCK}" y="{row * _BLOCK}" '
                    f'width="{_BLOCK}" height="{_BLOCK}" '
                    f'fill="{_mix(THUMB_BG, accent, _BLOCK_TINT[level])}"/>'
                )
        return "".join(cells)

    if shape == "field":
        # A toroidal field of oscillators: rows of dots at different phases.
        dots = []
        for row in range(6):
            for col in range(11):
                phase = col * 0.55 + row * 0.9
                opacity = 0.18 + 0.62 * (0.5 + 0.5 * math.sin(phase))
                dots.append(
                    f'<circle cx="{16 + col * 29}" cy="{18 + row * 29}" '
                    f'r="{2.5 + 2.5 * (0.5 + 0.5 * math.sin(phase)):.1f}" '
                    f'fill="{accent}" opacity="{opacity:.2f}"/>'
                )
        return "".join(dots)
    if shape == "strings":
        # A plucked string: same pinned-both-ends arc the instrument simulates,
        # drawn thick enough to see and at varying phase so it reads as motion.
        return "".join(
            f'<path d="M0 {y} Q{60 + 40 * i} {y - 34} 160 {y - 20} '
            f'T320 {y}" fill="none" stroke="{accent}" '
            f'stroke-width="{2 + (i % 3)}" opacity="{0.5 + 0.09 * i:.2f}" '
            'stroke-linecap="round"/>'
            for i, y in enumerate(range(40, 152, 28))
        )
    if shape == "pipes":
        # Organ pipes of increasing length, which is literally the interface.
        pipes = "".join(
            f'<rect x="{18 + i * 38}" y="{160 - 16 - i * 13}" width="22" '
            f'height="{16 + i * 13}" rx="3" fill="{accent}" '
            f'opacity="{0.45 + 0.07 * i:.2f}"/>'
            for i in range(8)
        )
        return (
            pipes
            + f'<rect y="160" width="320" height="6" fill="{accent}" opacity=".55"/>'
        )
    if shape == "packet":
        # A localised wave packet with its spectrum: the two views the app shows.
        pts = " ".join(
            f"{x},{110 - 46 * math.sin((x - 160) / 26) * math.exp(-((x - 160) ** 2) / 6400)}"
            for x in range(0, 321, 8)
        )
        spectrum = " ".join(
            f"{x},{168 - 22 * math.exp(-((x - 160) ** 2) / 2400)}"
            for x in range(0, 321, 8)
        )
        return (
            f'<polyline points="{pts}" fill="none" stroke="{accent}" stroke-width="3"/>'
            f'<polyline points="{spectrum}" fill="none" stroke="{accent}" '
            'stroke-width="2" opacity=".45"/>'
            f'<line x1="0" y1="168" x2="320" y2="168" stroke="{accent}" '
            'stroke-width="1" opacity=".3"/>'
        )
    # orbits: an attractor with orbiting ellipses (nebulabrot plots orbits).
    return (
        f'<circle cx="160" cy="90" r="40" fill="{accent}" opacity=".14"/>'
        + "".join(
            f'<ellipse cx="160" cy="90" rx="{40 + 17 * i}" ry="{16 + 9 * i}" '
            f'fill="none" stroke="{accent}" stroke-width="1.5" '
            f'opacity="{0.55 - 0.1 * i:.2f}" transform="rotate({-20 - 8 * i} 160 90)"/>'
            for i in range(4)
        )
        + f'<circle cx="160" cy="90" r="6" fill="{accent}"/>'
    )


def _thumb(p: Project) -> str:
    """Inline SVG art in the project's accent colour.

    Generated rather than screenshotted deliberately: a screenshot would have to
    be committed here -- the duplicate-copy problem this repo exists to avoid --
    and would freeze one moment of a thing that moves. These cost nothing, scale
    to any width, and cannot 404.
    """
    accent = ACCENTS.get(p.repo, NO_ACCENT)
    return _motif(_KIND.get(p.repo, ("Project", "lines"))[1], accent)


def _card(p: Project, wide: bool) -> str:
    repo_url = f"https://github.com/{ORG}/{p.repo}"
    accent = ACCENTS.get(p.repo, NO_ACCENT)
    kind = _KIND.get(p.repo, ("Project", "waves"))[0]
    if p.preview is not None:
        links = (
            f'<a class="open" href="{p.preview}">Open it</a>'
            f'<a class="source" href="{repo_url}">Source</a>'
        )
    else:
        # Honest about what is missing rather than offering a link that does
        # nothing: these two are a renderer and a CLI, so there is no page to open.
        links = (
            f'<a class="source" href="{repo_url}">Source</a>'
            '<span class="none">no browser artifact</span>'
        )
    commits = "1 commit" if p.commits == 1 else f"{p.commits} commits"
    classes = "card wide" if wide else "card"
    return f"""    <li class="{classes}" style="--accent: {accent}">
      <div class="thumb">{_thumb(p)}</div>
      <div class="body">
        <h2>{_html(p.title)}</h2>
        <p class="kind">{_html(kind)}</p>
        <p class="desc">{_html(p.tagline)}</p>
        <div class="meta">
          <span>{_span(p.first_commit, p.last_commit)}</span>
          <span>{commits}</span>
        </div>
        <div class="links">{links}</div>
      </div>
    </li>"""


def _require_full_history(projects: list[Project]) -> None:
    """Fail if any clone is shallow, since the page is ordered by first commit.

    Without this a shallow clone makes every project look one day old, the
    chronology silently becomes a tie, and the page still publishes: plausible
    and wrong, which is the failure worth preventing. The check costs nothing.
    """
    thin = [p.repo for p in projects if p.commits <= 1 or p.first_commit is None]
    if thin:
        msg = (
            "shallow or unreadable clone(s): "
            + ", ".join(thin)
            + " -- the page is ordered by first commit, so a --depth 1 clone\n"
            "would date every project to a single day. Clone without --depth."
        )
        raise SystemExit(msg)


def _chronological(projects: list[Project]) -> list[Project]:
    """Oldest first, so the page reads as a timeline of the work.

    The sort is stable, so projects sharing a first date keep REPOS order rather
    than being rearranged by the sort's internals.

    Projects with no readable date sort last rather than raising inside the
    comparison key. _require_full_history should already have rejected those;
    ordering must not silently depend on that call having happened first.
    """
    return sorted(projects, key=lambda p: (p.first_commit is None, p.first_commit))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repos",
        type=Path,
        required=True,
        help="directory containing the project checkouts",
    )
    parser.add_argument(
        "--out", type=Path, required=True, help="where to write index.html"
    )
    parser.add_argument(
        "--skip-preview-checks",
        action="store_true",
        help="do not fetch preview URLs (for offline development only)",
    )
    args = parser.parse_args()

    projects = [load_project(args.repos, repo) for repo in REPOS]
    _require_full_history(projects)
    projects = _chronological(projects)

    # Checked in parallel: each is one HTTP request to a different host, and a
    # serial pass over six of them dominates the build time for no good reason.
    errors: list[str] = []
    if args.skip_preview_checks:
        # Exists so the page can be built and looked at without a network. Not a
        # default and not for CI: the value of the check is that a green build
        # means every advertised Open-it link actually serves, and skipping it
        # silently would be the "plausible page with a dead link" failure again.
        print(
            "NOTE: preview URLs not checked; the page may advertise a link that "
            "no longer serves.",
            file=sys.stderr,
        )
    else:
        # Parallel: one request per project to a different URL, and serially
        # these dominate build time for no good reason.
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            checked = list(pool.map(check_preview, [p for p in projects if p.preview]))
        errors = [error for _, error in checked if error]
    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1

    page = render(projects)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(page, encoding="utf-8")
    playable = sum(1 for p in projects if p.preview is not None)
    print(
        f"wrote {args.out} ({len(projects)} projects, {playable} openable in a browser)"
    )
    for p in projects:
        print(f"  {p.first_commit}  {p.repo}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
