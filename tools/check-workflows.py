"""Fail when a GitHub Actions workflow file cannot be parsed.

An unparseable workflow produces no jobs and no log: the run shows as a failure
with nothing to read, which is indistinguishable from a browser failing to
start. Observed 2026-11-24, where a heredoc written at column 0 inside a
`run: |` block broke the block scalar and cost three diagnostic runs that
reported nothing.

PyYAML maps the bare key `on:` to the boolean True, the same YAML 1.1 quirk
GitHub Actions itself exposes, so the trigger check accepts either spelling.
"""

import sys
from pathlib import Path

try:
    import yaml
except ModuleNotFoundError:
    # A guard that silently skips is how a broken workflow reaches CI and reads
    # as "the browser suite is broken": no jobs, no log, just a red tick.
    print(
        "FAIL: PyYAML is not installed, so workflow YAML cannot be validated. "
        "Run inside the pinned shell (nix develop --command ./check.sh).",
        file=sys.stderr,
    )
    raise SystemExit(1)


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    files = sorted((root / ".github" / "workflows").glob("*.y*ml"))
    if not files:
        print("no workflow files found", file=sys.stderr)
        return 1
    failures = 0
    for path in files:
        try:
            document = yaml.safe_load(path.read_text())
        except yaml.YAMLError as error:
            print(f"FAIL {path.relative_to(root)}: {error}", file=sys.stderr)
            failures += 1
            continue
        triggers = document.get("on", document.get(True))
        jobs = document.get("jobs")
        if not isinstance(triggers, dict) or not triggers:
            print(f"FAIL {path.relative_to(root)}: no triggers parsed", file=sys.stderr)
            failures += 1
        if not isinstance(jobs, dict) or not jobs:
            print(f"FAIL {path.relative_to(root)}: no jobs parsed", file=sys.stderr)
            failures += 1
    if failures:
        return 1
    for path in files:
        document = yaml.safe_load(path.read_text())
        jobs = document.get("jobs")
        steps = sum(len(job.get("steps", [])) for job in jobs.values())
        print(f"ok {path.relative_to(root)}: {len(jobs)} job(s), {steps} step(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
