#!/usr/bin/env python3
"""Static goreleaser-config preflight for the reusable release workflow.

Two invariants the release MUST satisfy before goreleaser runs (release.md §4.1,
distribution.md §8):

  * release.replace_existing_artifacts: true  — makes a re-run idempotent
    (a retried release overwrites the same GitHub-release assets instead of
    erroring on "asset already exists").
  * when homebrew is declared, every homebrew_casks entry sets
    skip_upload: true — so goreleaser renders the cask into dist/ but does NOT
    push it; the homebrew-alias step is the single writer that lands the
    canonical cask + aliases atomically.

Usage:
    preflight.py check-config <goreleaser.yml> [--homebrew]

Exits non-zero with a ::error:: line on any violation. Pure (no goreleaser run),
so it is unit-tested with fixture configs.
"""
import sys

import yaml


def check_config(path: str, homebrew: bool) -> list[str]:
    with open(path) as fh:
        cfg = yaml.safe_load(fh) or {}
    errors: list[str] = []

    release = cfg.get("release") or {}
    if release.get("replace_existing_artifacts") is not True:
        errors.append(
            "release.replace_existing_artifacts must be true for idempotent "
            "re-runs (release.md §4.1)"
        )

    if homebrew:
        casks = cfg.get("homebrew_casks") or []
        if not casks:
            errors.append(
                "homebrew is declared in the manifest but .goreleaser has no "
                "homebrew_casks entry"
            )
        for i, cask in enumerate(casks):
            if (cask or {}).get("skip_upload") is not True:
                errors.append(
                    f"homebrew_casks[{i}].skip_upload must be true so the "
                    "homebrew-alias step is the single atomic writer "
                    "(distribution.md §8.2)"
                )
    return errors


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[0] != "check-config":
        print("usage: preflight.py check-config <goreleaser.yml> [--homebrew]", file=sys.stderr)
        return 2
    path = argv[1]
    homebrew = "--homebrew" in argv[2:]
    errors = check_config(path, homebrew)
    for e in errors:
        print(f"::error::{e}", file=sys.stderr)
    if errors:
        return 1
    print("release preflight OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
