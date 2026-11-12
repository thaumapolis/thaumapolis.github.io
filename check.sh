#!/usr/bin/env bash
# Acceptance checklist for the front page. Every phase runs or fails the build:
# a phase that cannot run is a red result, never a skip. Printing ALL CHECKS
# PASSED after executing nothing would describe the page as verified when
# nothing was.
#
# Run inside the pinned toolchain:  nix develop --command ./check.sh
#
#   ./check.sh                  all phases, goldens compared
#   ./check.sh --update-goldens rewrite goldens (developer action, never CI)
#   ./check.sh --repos DIR      where the project checkouts live
set -uo pipefail
cd "$(dirname "$0")"

# The projects this page describes are not vendored, so a local run has to be
# told where they are. CI clones them; nothing here guesses a path.
REPOS=""
ENGINE=firefox
UPDATE_GOLDENS=0
# Preview URLs are fetched by the generator, which is the point: a green build
# means every advertised "Open it" link serves. Offline that fetch cannot
# succeed, so there is a switch to say so out loud instead of leaving someone to
# conclude the generator is broken. It never defaults to on, and CI does not use it.
SKIP_LINK_CHECKS=0
while [[ $# -gt 0 ]]; do
  case $1 in
    --repos) REPOS="${2:-}"; shift 2 ;;
    --engine) ENGINE="${2:-}"; shift 2 ;;
    --update-goldens) UPDATE_GOLDENS=1; shift ;;
    --skip-link-checks) SKIP_LINK_CHECKS=1; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
if [[ -z $REPOS ]]; then
  echo "--repos DIR is required: the page is generated from those checkouts." >&2
  echo "Typical local invocation, from inside the shared workspace:" >&2
  echo "  ./check.sh --repos .." >&2
  exit 2
fi
if [[ ! -d $REPOS ]]; then
  echo "--repos points at a directory that does not exist: $REPOS" >&2
  exit 2
fi
case $ENGINE in firefox|chromium|webkit) ;; *) echo "--engine must be firefox, chromium or webkit" >&2; exit 2 ;; esac

NODE=${NODE:-$(command -v node || true)}
PYTHON=${PYTHON:-$(command -v python3 || true)}
RUFF=${RUFF:-$(command -v ruff || true)}
STATUS=0
report() { printf '\n== %s ==\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1" >&2; STATUS=1; }

for tool in python3 node; do
  if [[ $tool == python3 && -z $PYTHON ]]; then fail "python3 not found"; fi
  if [[ $tool == node && -z $NODE ]]; then fail "node not found (goldens need it; nix develop provides it)"; fi
done
[[ $STATUS -ne 0 ]] && { printf '\nchecks did not run\n'; exit 1; }

report "lint (ruff)"
if [[ -z $RUFF ]]; then
  fail "ruff not found; run inside the pinned shell so the check is the same locally and in CI"
else
  "$RUFF" check --output-format=concise tools/ || fail "ruff check"
  "$RUFF" format --check tools/ || fail "ruff format"
fi

# Card images. Deliberately a build-time step whose absence is visible: the
# generator embeds whatever is committed in thumbs/, so an unregenerated thumb
# means a card still shows a screenshot the project no longer nominates. Running
# the tool is how that drift is caught -- it fails on a declared file that is
# missing or not embedded, and rewrites what should be byte-identical when
# nothing has changed.
report "card thumbnails"
if ! "$PYTHON" tools/make_thumbs.py --repos "$REPOS"; then
  fail "make_thumbs (a declared front page image is missing, not embedded, or over budget)"
fi

# The build itself. This is also the phase that proves the ordering claim: it
# prints each project's first commit, which comes from the clones, and fails on
# a shallow one rather than publishing a page where everything looks days old.
report "generate"
mkdir -p .tmp/site
GENERATE_ARGS=(--repos "$REPOS" --out .tmp/site/index.html)
[[ $SKIP_LINK_CHECKS -eq 1 ]] && GENERATE_ARGS+=(--skip-preview-checks)
if ! "$PYTHON" tools/generate.py "${GENERATE_ARGS[@]}"; then
  fail "generate (see above)"
fi

# Rendering checks. A page that generated successfully is not the same as a page
# that lays out: clipping, overflow and a silently unused @font-face all look
# fine as bytes on disk.
report "goldens ($ENGINE)"
if [[ ! -f .tmp/site/index.html ]]; then
  fail "no artifact to test"
else
  ARGS=(--engine "$ENGINE" --repos "$REPOS")
  [[ $UPDATE_GOLDENS -eq 1 ]] && ARGS+=(--update)
  "$NODE" tests/golden.test.mjs "${ARGS[@]}" || fail "golden checks"
fi

report "result"
if [[ $STATUS -eq 0 ]]; then
  echo "ALL CHECKS PASSED"
else
  echo "checks failed"
fi
exit $STATUS
