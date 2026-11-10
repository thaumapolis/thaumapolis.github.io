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
import concurrent.futures
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ORG = "thaumapolis"
# The front page lists projects in this order. Repos are not discovered from
# the org API: an explicit list is what lets CI fail when one goes missing,
# which is the failure mode worth catching.
ORDER = [
    "ridgeline",
    "flux-pond",
    "aurelia",
    "stem",
    "quantum-loom",
    "resonance-engine",
    "nebulabrot",
    "termbrot",
]


@dataclass(frozen=True, kw_only=True)
class Project:
    repo: str
    title: str
    tagline: str
    preview: str | None
    default_branch: str


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


def load_project(repos_dir: Path, repo: str) -> Project:
    readme_path = repos_dir / repo / "README.md"
    try:
        readme = readme_path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        msg = f"{repo}: expected README.md at {readme_path}"
        raise FileNotFoundError(msg) from error
    title, tagline, preview = parse_readme(readme)
    branch_path = repos_dir / repo / ".default_branch"
    branch = branch_path.read_text(encoding="utf-8").strip() if branch_path.is_file() else "main"
    return Project(
        repo=repo, title=title, tagline=tagline, preview=preview, default_branch=branch
    )


def check_preview(project: Project) -> tuple[Project, str | None]:
    """Verify an advertised preview actually serves the artifact."""
    if project.preview is None:
        return project, None
    request = urllib.request.Request(project.preview, headers={"User-Agent": "thaumapolis-front-page"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
    except (urllib.error.URLError, TimeoutError) as error:
        return project, f"{project.repo}: preview URL did not load ({error})"
    if b"<html" not in body[:600].lower():
        return project, f"{project.repo}: preview URL did not serve HTML"
    return project, None


def render(projects: list[Project]) -> str:
    cards = "\n".join(_card(p) for p in projects)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{ORG} &mdash; small machines that do real arithmetic</title>
<meta name="description" content="Self-contained browser toys and tools: each one a single HTML file with a genuine simulation inside.">
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 2rem 1.25rem 4rem; background: #0c1118; color: #d7e0ea;
    font: 400 16px/1.6 system-ui, sans-serif;
  }}
  main {{ max-width: 68rem; margin: 0 auto; }}
  header h1 {{
    font-size: clamp(1.8rem, 5vw, 3rem); margin: 0 0 .25rem; color: #7fd8b8;
    letter-spacing: .14em; text-transform: lowercase;
  }}
  header p {{ margin: 0 0 2.5rem; color: #8fa0b3; max-width: 42rem; }}
  ul {{ list-style: none; margin: 0; padding: 0; display: grid; gap: 1rem; }}
  li {{
    background: #131a24; border: 1px solid #202b38; border-radius: .6rem;
    padding: 1.1rem 1.25rem;
  }}
  li h2 {{ margin: 0 0 .3rem; font-size: 1.15rem; color: #e8eef5; }}
  li p {{ margin: 0 0 .7rem; color: #9fb0c2; font-size: .95rem; }}
  .links a {{
    display: inline-block; margin-right: 1.1rem; font-size: .9rem;
    color: #7fd8b8; text-decoration: none; border-bottom: 1px solid #24413a;
  }}
  .links a:hover {{ color: #b6f0d6; }}
  .links span {{ font-size: .85rem; color: #6c7c8e; }}
  footer {{ margin-top: 3rem; font-size: .85rem; color: #66768a; }}
  footer code {{ color: #9fb0c2; }}
</style>
</head>
<body>
<main>
  <header>
    <h1>{ORG}</h1>
    <p>Small self-contained machines. Every project below is one file that asks
    for nothing else &mdash; no build, no network, no storage &mdash; with a real
    simulation underneath rather than an animation pretending to be one.</p>
  </header>
  <ul>
{cards}
  </ul>
  <footer>
    <p>This page is generated from each project&rsquo;s own README by
    <code>tools/generate.py</code>; edit a README and the page follows. The build
    fails if a project is missing or a preview link stops serving.</p>
  </footer>
</main>
</body>
</html>
"""


def _card(p: Project) -> str:
    repo_url = f"https://github.com/{ORG}/{p.repo}"
    if p.preview is not None:
        links = (
            f'<a href="{p.preview}">Open it</a>'
            f'<a href="{repo_url}">Source</a>'
        )
    else:
        links = f'<a href="{repo_url}">Source</a> <span>no browser artifact</span>'
    return f"""    <li>
      <h2>{p.title}</h2>
      <p>{p.tagline}</p>
      <div class="links">{links}</div>
    </li>"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repos", type=Path, required=True, help="directory containing the project checkouts")
    parser.add_argument("--out", type=Path, required=True, help="where to write index.html")
    args = parser.parse_args()

    projects = [load_project(args.repos, repo) for repo in ORDER]

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        checked = list(pool.map(check_preview, [p for p in projects if p.preview]))
    errors = [error for _, error in checked if error]
    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(projects), encoding="utf-8")
    playable = sum(1 for p in projects if p.preview is not None)
    print(f"wrote {args.out} ({len(projects)} projects, {playable} openable in a browser)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
