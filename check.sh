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

# A workflow that does not parse produces no jobs and no log, so the failure
# looks like the browser suite broke rather than the YAML. Parse it here, where
# someone is about to push it.
report "workflows parse"
if ! "$PYTHON" tools/check-workflows.py; then
  fail "workflow YAML (an unparseable workflow runs nothing and logs nothing)"
fi

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
#
# The pixels are compared against a page whose per-card commit counts and date
# text are pinned, because both are printed inline and change rendered text
# width. Observed 2026-11-24: one project went 4 -> 10 commits between CI's
# clone and the checkout that painted the baselines, and the goldens then failed
# at 100% of pixels on a page that was laid out identically. A baseline that
# changes whenever another repository commits is asserting history, not layout.
#
# Pinned from .tmp/site/index.html -- the page this run just generated and
# verified -- so which projects are openable is fixed by that verified page
# rather than re-read from disk. openability is layout-affecting (with no
# preview a card gains a note that wraps its links row, measured at 13px), so
# this is the one place it must agree; the content checks in the golden tests
# still assert it against the live READMEs, so a preview link that really
# disappears fails the run.
#
# The masthead keeps the real project count, openable count, commit total and
# date range, so the image never shows a fabricated headline number.
report "goldens ($ENGINE)"
if [[ ! -f .tmp/site/index.html ]]; then
  fail "no artifact to test"
else
  # Pin the page that was just generated and verified, rather than generating a
  # second time. A second read of the repositories can see different facts than
  # the first -- a project committed between the two reads is enough -- and then
  # the image would be pinned from a page nobody checked.
  if ! "$PYTHON" tools/generate.py --repos "$REPOS" --from-page .tmp/site/index.html \
      --out .tmp/golden-page/index.html --pin-layout-facts; then
    fail "pin the verified page for the goldens"
  fi
  ARGS=(--engine "$ENGINE" --repos "$REPOS")
  [[ $UPDATE_GOLDENS -eq 1 ]] && ARGS+=(--update)
  "$NODE" tests/golden.test.mjs "${ARGS[@]}" || fail "golden checks"
fi

# The page's central claim, demonstrated rather than asserted. Each artifact is
# loaded in a sandboxed frame with every non-routed request aborted, and the
# frame reports its own state: silence is a failure, because "no errors" is also
# what a frame that never ran the artifact looks like. Two controls gate it --
# the sandboxed frame must deny storage, and the same frame WITHOUT the sandbox
# attribute must allow it, or a denial says nothing about the sandbox.
report "offline claim ($ENGINE)"
PROOF_JSON=.tmp/offline-proof.json
PROOF_ENV=()
if [[ -z ${PLAYWRIGHT_ENTRY:-} ]]; then
  # Resolve the driver the same way the golden tests do, rather than assuming a
  # path: a wrong guess here reads as "playwright is missing".
  RESOLVED=$("$NODE" -e 'process.stdout.write(require.resolve("playwright"))' 2>/dev/null || true)
  if [[ -z $RESOLVED ]]; then
    fail "offline-proof (PLAYWRIGHT_ENTRY is unset and playwright is not resolvable)"
  else
    PROOF_ENV=(PLAYWRIGHT_ENTRY="$RESOLVED")
  fi
fi
if [[ ${#PROOF_ENV[@]} -gt 0 || -n ${PLAYWRIGHT_ENTRY:-} ]]; then
  if env ${PROOF_ENV[@]+"${PROOF_ENV[@]}"} "$NODE" tools/offline-proof.mjs \
      --repos "$REPOS" --engine "$ENGINE" --out "$PROOF_JSON" > .tmp/offline-proof.log 2>&1; then
    "$NODE" -e '
      const d = JSON.parse(require("fs").readFileSync(process.argv[1], "utf8"));
      if (d.verdict !== "demonstrated") { console.error("FAIL: " + d.verdict); process.exit(1); }
      for (const p of d.projects) {
        console.log("  " + p.repo.padEnd(17) + " canvas=" + p.canvases +
          " painted=" + p.painted + " external requests=" + p.blockedRequests);
      }
      console.log("  " + d.projects.length + " artifacts in " + d.engine +
        "; sandbox denies storage: " + d.controls.sandboxed_ok +
        "; reachable without the sandbox: " + d.controls.unsandboxed_ok);
    ' "$PROOF_JSON" || fail "offline-proof summary"
  else
    cat .tmp/offline-proof.log >&2
    fail "offline-proof (an artifact needed the network, needed storage, or never ran)"
  fi
fi

report "result"
if [[ $STATUS -eq 0 ]]; then
  echo "ALL CHECKS PASSED"
else
  echo "checks failed"
fi
exit $STATUS
