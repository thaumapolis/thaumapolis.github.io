#!/usr/bin/env python3
"""Generate the committed card thumbnails from each project's own screenshot.

Regenerate deliberately, the same way faces come from a pinned subsetter:

    ./tools/make_thumbs.py --repos ..

Nothing about this runs at page-build time. The committed .webp files are the
input to tools/generate.py, exactly as faces/*.base64 are, so building the page
needs no imaging library, no network and no access to the project checkouts.

Why screenshots when hand-drawn motifs were cheaper: the motifs were honest
about being abstractions, but they were not honest about being the project. The
nebulabrot card showed a spiral diagram while the project's own README leads
with the rendered frame; flux-pond showed a wavy line while its golden frame
shows the creature, the lurker below it and the onboarding panel. A front page
that says "look at these things" should show the things.

Which screenshot: whatever the project declares in its README with

    <!-- Front page image: path/relative/to/repo/root.png -->

so the choice is the project's, not this script's. The line is an HTML comment
because it must be inert where it lives; tools/generate.py owns the pattern and
imports it. A project that declares nothing gets the generated motif -- termbrot
does -- rather than a frame hand-picked from a repo that never said anything
about being looked at.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import urllib.parse

# px. The widest a thumbnail renders is ~581 CSS px on desktop, so the old
# 560 was already a slight upscale there; on a 2x phone or Retina display the
# panel asks for ~1160 device px, and a 560 source is a 2x upscale of a lossy
# WebP. That double softening -- LANCZOS down to 560, then the browser up to
# 1160 -- was the visible pixelation/blur on the cards, not the projects.
#
# 840 was chosen to cover a 581 CSS px panel. That measurement no longer holds:
# rendered at 1440 CSS wide and dpr2, the panels are 581 CSS (1162 device px) for
# cover cards and 496 CSS (993 device px) for the two contain cards, so an 840 px
# source is upscaled 1.38x and 1.18x -- the double resample this constant exists
# to prevent.
THUMB_WIDTH = 840
QUALITY = 80
WEBP_METHOD = 6

# Resolution and composition used to be the same number, so sharpening a card
# necessarily reframed it. They are two now:
#
#   CROP_WIDTH   -- how much of the source a card shows, in source px.
#   ENCODE_WIDTH -- how many px that view is written out at.
#
# Pinning the window at the old 840 while encoding at the measured desktop device
# width makes the cards sharper and moves nothing. Checked per card: with the
# window pinned, stem/keel/flux-pond keep their exact source rectangle across
# 840 -> 1162; with the window following the encoder, stem widened from 65.6% to
# 90.8% of its source width and keel's hull visibly grew in the panel.
# The aspect ratio a thumb box settles at. The panel has no aspect-ratio of its
# own -- its height comes from the card's copy -- so this is a measured
# observation of what the layout produces, used to crop the source close to the
# shape it will be shown in. The page still uses object-fit: cover, because a
# real box is never exactly this; the crop is here so the browser is cropping a
# few percent rather than half of a 16:10 screenshot.
# Observed: 581x300 on desktop (1.94), 390x180 phone portrait (2.17), 42% of a
# wide card in phone landscape. 2.0 splits the difference.
THUMB_ASPECT = 2.0

# The crop window, in source px. Deliberately the historical value: this is the
# composition every card has always had, and changing it must be its own review.
CROP_WIDTH = THUMB_WIDTH
# Widest rendered panel (581 CSS) x dpr2, i.e. the 1162 device px measured on a
# 1440-wide desktop. Encoded 1:1 with that, narrower panels downsample in the
# browser instead of this one upscaling 1.38x.
ENCODE_WIDTH = 1162
ENCODE_HEIGHT = round(ENCODE_WIDTH / THUMB_ASPECT)

# repo -> vertical framing, for sources the centre crop reads badly on. Default
# is "center". One entry per exception, each with the reason next to it; a
# general "smart crop" would be more machinery than seven projects justify.
#
#   top    keep the top of the frame, at the thumbnail aspect, then scale down.
#          For tall screenshots whose subject is a header and one panel: a
#          centre crop of a 390x844 phone frame lands on the middle of the
#          page and cuts off everything a viewer would recognise. Unused today;
#          it exists because "center" is the wrong framing for a phone frame.
#   letter fill the thumbnail aspect with the whole image, padding the short
#          axis. For sparse frames -- Aurelia's strings run across the full
#          height, so any centre crop keeps two 2px lines out of 800 -- where
#          cropping is not a compromise but a deletion.
FRAMING: dict[str, str] = {
    "aurelia": "letter",
    # saltmarsh's card image is the 3D view alone, narration suppressed, captured
    # at 2:1 so the card is a downscale with nothing cut.
    # Captured at exactly 2:1 (see the capture recipe in that repo's README), so
    # letterboxing costs nothing and keeps the whole marsh; "center" would be
    # equivalent here and would silently cut it again the next time the source's
    # aspect drifts.
    "saltmarsh": "letter",
    # quantum-loom's frame is a two-column app: picture on the left, a full-height
    # column of controls and prose on the right. A centre crop at 2:1 keeps the
    # middle of both, which puts the right-hand column's paragraphs in the frame
    # and cuts each of them mid-word ("...the spectrum ne", "...readout b").
    # Letterboxing keeps the whole window legible instead of quoting prose badly.
    "quantum-loom": "letter",
    # ridgeline's card is captured taller than 2:1 on purpose (see the comment on
    # ASPECT_MAX in that repo's tools/capture-card.mjs): the floor is 56x44, so a
    # 2:1 canvas cannot both centre the player and keep the corridors that leave
    # the room. A centre crop keeps the room and drops every corridor, which is
    # the difference between "a lit box" and "a room you can leave". Letterboxing
    # keeps the whole sight disc, corridors included, for a small side gutter.
    "ridgeline": "letter",
    # pressure-well's whole subject is the grid *and* the scope trace under it. A
    # centre crop at 2:1 lands on the upper, empty half of the grid and drops the
    # scope entirely -- the one part that shows what the synth computed. Letterbox
    # so the card reads as the instrument it is, controls and trace included.
    "pressure-well": "letter",
}

# Sources bigger than this are refused rather than encoded. A front page is one
# file and these are embedded in it, so the source of a 10 KB thumbnail is
# allowed to be a purposeful capture, not an unrestrained render. Observed:
# the biggest accepted source here is a 2.2 MB nebulabrot frame, and every
# rejected candidate would have been a mistake about which file was meant.
MAX_SOURCE_BYTES = 8_000_000

# repo -> README image path, or None to mean "fall back to the generated motif".
# Derived from each README rather than asserted: see IMAGE_ALT_TEXT rationale.
# Imported, not redefined: see the note in tools/generate.py.
from generate import FRONT_PAGE_IMAGE_RE


def _load_imaging() -> object:
    """Import Pillow, or say exactly how to get it.

    A clear failure beats a fallback. If this silently drew a motif instead, a
    project that added a fine new screenshot would keep showing the old one and
    nothing would notice.
    """
    try:
        from PIL import Image
    except ImportError as error:
        msg = (
            "Pillow is required to regenerate thumbnails but is not importable.\n"
            "Run inside the pinned shell:  nix develop --command ./check.sh --repos ..\n"
            f"(import error: {error})"
        )
        raise SystemExit(msg) from error
    return Image


def _frame(Image: object, rgb: object, framing: str) -> object:
    """Return `rgb` at ENCODE_WIDTH x ENCODE_HEIGHT, without distortion.

    Composition and resolution are deliberately separate:

      * the crop window is chosen in *source* coordinates, from CROP_WIDTH, and
      * the output is always ENCODE_WIDTH x ENCODE_HEIGHT.

    They used to be the same number. `width = min(rgb.width, target_w)` sized the
    crop window from the output width, so raising the encoded resolution also
    widened every crop: at 840 -> 1162, stem went from showing 65.6% to 90.8% of
    its source width and keel's hull visibly grew in the panel. So "sharpen the
    cards" and "reframe every card" were one change, and neither could be reviewed
    without the other. Pinning the window to CROP_WIDTH makes the resolution a
    pure quality knob.

    Never upscales past the source, so a small screenshot cannot be softened into
    blurriness: if the selected window is smaller than the output, that window is
    resampled up, which is a genuine upscale of source detail and is reported.
    """
    target_w = ENCODE_WIDTH
    target_h = ENCODE_HEIGHT
    if framing == "letter":
        scale = min(target_w / rgb.width, target_h / rgb.height)
        scaled = rgb.resize(
            (max(1, round(rgb.width * scale)), max(1, round(rgb.height * scale))),
            Image.LANCZOS,
        )
        canvas = Image.new("RGB", (target_w, target_h), (18, 18, 22))
        canvas.paste(
            scaled,
            ((target_w - scaled.width) // 2, (target_h - scaled.height) // 2),
        )
        return canvas

    # "center" and "top" differ only in where the window starts vertically.
    # CROP_WIDTH, not target_w: the window is what is shown, and changing the
    # encoding must not change it.
    width = min(rgb.width, CROP_WIDTH)
    height = round(width / THUMB_ASPECT)
    if height > rgb.height:
        height = rgb.height
        width = round(height * THUMB_ASPECT)
    left = (rgb.width - width) // 2
    top = 0 if framing == "top" else (rgb.height - height) // 2
    cropped = rgb.crop((left, top, left + width, top + height))
    if (cropped.width, cropped.height) != (target_w, target_h):
        cropped = cropped.resize((target_w, target_h), Image.LANCZOS)
    return cropped


def framing_note(rgb: object, framing: str) -> str:
    """How much of the source is *kept* and how much of the *card* is filled.

    Not a gate. Cropping to the card's aspect is the design, and a 16:10
    screenshot legitimately loses 20% of its height -- refusing that would
    refuse every card the page has ever shown. Two numbers are reported because
    one cannot be read: kept-area is near 0 for any large screenshot even when
    the crop is ideal (nebulabrot's 1920x1080 keeps 17% while cutting nothing
    that matters), and fill says nothing at all about cropping. Read together
    they identify the one state that is always wrong -- a frame shown small and
    surrounded by bars -- which is why the build names it and nothing else.

    The defect this replaced: two cards were published with prose cut mid-word
    ("...the spectrum ne", "is an octave") and no output on any run said so,
    because nothing in the build had ever reported how much was being cut.
    """
    target_w = THUMB_WIDTH
    target_h = round(THUMB_WIDTH / THUMB_ASPECT)
    if framing == "letter":
        scale = min(target_w / rgb.width, target_h / rgb.height)
        fill = (rgb.width * scale * rgb.height * scale) / (target_w * target_h)
        if fill < 0.6:
            # Nothing to crop and almost nothing to show: the source should be
            # captured at or nearer the card's aspect instead.
            return f"letter, whole frame but only {fill * 100:.0f}% of the card (capture nearer {THUMB_ASPECT:.1f}:1)"
        return f"letter, whole frame at {fill * 100:.0f}% of the card"
    width = min(rgb.width, target_w)
    height = min(rgb.height, round(width / THUMB_ASPECT))
    kept = (width * height) / (rgb.width * rgb.height)
    return f"{framing} crop keeps {kept * 100:.0f}% of the source"


def readme_image(readme: str, repo: str) -> str | None:
    """The screenshot this project wants on the front page.

    Declared, not inferred. An earlier version took the first image in the
    README, which worked until it didn't: it put a near-empty Aurelia frame on
    a card, and it silently demoted Ridgeline, whose README links no images at
    all even though the repo has committed golden frames. Either outcome is
    this script deciding a project's portrait for it, which is not its call.

    No match means no declared image. It does not fall back to "first image" as
    a guess, because a guess that quietly changes when someone reorders a
    README is the drift this whole repository is built to avoid.

    A declared path must also be embedded in the README as a markdown image, so
    that the declaration is a pointer to a picture the project already shows
    its own readers. Without that rule this script happily cropped any file in
    the tree, and the cards that went wrong were the ones chosen that way:
    a golden frame of the onboarding modal for one project, a sparse mid-bow
    desktop frame for another, neither of which the author ever put forward as
    "this is what it looks like". Any file in the repo is a guess; a file the
    README shows is a statement.
    """
    match = FRONT_PAGE_IMAGE_RE.search(readme)
    if match is None:
        return None
    target = match.group(1).strip()
    split = urllib.parse.urlsplit(target)
    if split.scheme or split.netloc:
        # A card image is embedded into a page whose claim is that it needs
        # nothing external. A URL would break that claim or freeze a live thing.
        msg = f"a front page image must be a repo-relative path, got {target!r}"
        raise SystemExit(msg)
    if not split.path.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
        msg = f"unsupported front page image format: {target!r}"
        raise SystemExit(msg)
    path = urllib.parse.unquote(split.path)
    if not _embedded_in_readme(readme, path):
        msg = (
            f"{repo}: declares {path} as its front page image, but the README "
            "never embeds it as an image. Point the line at a picture the "
            "README actually shows, or add that image to the README."
        )
        raise SystemExit(msg)
    return path


def _embedded_in_readme(readme: str, path: str) -> bool:
    """True when the README shows `path` as a markdown image.

    Badge links are markdown links, not images, so they cannot satisfy this.
    """
    pattern = rf"!\[[^\]]*\]\({re.escape(path)}\s*\)"
    return re.search(pattern, readme) is not None


def make_one(repos: pathlib.Path, repo: str, out_dir: pathlib.Path) -> str:
    """Write out_dir/<repo>.webp. Returns a human-readable outcome line."""
    readme_path = repos / repo / "README.md"
    readme = readme_path.read_text(encoding="utf-8")
    source = readme_image(readme, repo)
    target = out_dir / f"{repo}.webp"
    if source is None:
        if target.exists():
            target.unlink()
            return f"{repo}: no README image; removed stale {target.name}"
        return f"{repo}: no README image; motif fallback"

    image_path = repos / repo / source
    if not image_path.is_file():
        msg = (
            f"{repo}: README advertises {source} but it is not at {image_path}.\n"
            "Add the file to that repository, or change the README line. The "
            "build does not fall back to the previous thumbnail, because a card "
            "showing a stale frame is how an index starts lying."
        )
        raise SystemExit(msg)
    source_bytes = image_path.stat().st_size
    if source_bytes > MAX_SOURCE_BYTES:
        msg = (
            f"{repo}: {source} is {source_bytes:,} bytes, over the "
            f"{MAX_SOURCE_BYTES:,} limit for a thumbnail source."
        )
        raise SystemExit(msg)

    Image = _load_imaging()
    with Image.open(image_path) as image:
        rgb = image.convert("RGB")
        if rgb.width <= 0 or rgb.height <= 0:
            msg = f"{repo}: {source} has degenerate size {rgb.size}"
            raise SystemExit(msg)
        framing = FRAMING.get(repo, "center")
        # Measured on the un-framed source, which is the frame the note is about.
        note = framing_note(rgb, framing)
        rgb = _frame(Image, rgb, framing)
        rgb.save(target, "WEBP", quality=QUALITY, method=WEBP_METHOD)

    size = target.stat().st_size
    # A card thumbnail is decoration for a page that should load instantly from
    # one file. The limit is a budget, not a measurement of what was needed.
    # Set when THUMB_WIDTH went 560 -> 840, and re-checked at ENCODE_WIDTH 1162:
    # all nine thumbs stay under it (largest 31,178) and total ~140 KB against a
    # ~190 KB page, so the headroom the budget was sized for still holds. The page
    # is roughly 24x smaller than the ~4.5 MB assumed when this was written, so
    # this is the stricter of the two readings.
    if size > 40_000:
        msg = (
            f"{repo}: thumbnail is {size:,} bytes, over the 40,000 budget. "
            f"Source was {source} at {image_path.stat().st_size:,} bytes. "
            "Either the source is enormous or the encoder settings are wrong."
        )
        raise SystemExit(msg)
    return f"{repo}: {source} -> {target.name} {size:,} B at {rgb.width}x{rgb.height} ({note})"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repos",
        type=pathlib.Path,
        required=True,
        help="directory containing the project checkouts",
    )
    parser.add_argument(
        "--out",
        type=pathlib.Path,
        default=pathlib.Path("thumbs"),
        help="where to write the .webp thumbnails",
    )
    args = parser.parse_args()

    # Import here so --help works without Pillow.
    import generate

    args.out.mkdir(parents=True, exist_ok=True)
    for repo in generate.REPOS:
        readme = args.repos / repo / "README.md"
        if not readme.is_file():
            msg = f"{repo}: expected README.md at {readme}"
            raise FileNotFoundError(msg)
        print(f"  {make_one(args.repos, repo, args.out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
