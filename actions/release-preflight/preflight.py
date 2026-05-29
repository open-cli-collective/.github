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
    preflight.py check-config <goreleaser.yml> [--homebrew] [--tag-prefix <p>]

Exits non-zero with a ::error:: line on any violation. Pure (no goreleaser run),
so it is unit-tested with fixture configs.
"""
import re
import sys


import yaml

_DOT_TAG = re.compile(r"\{\{\s*\.Tag\s*\}\}")


def _cask_url_template(cask: dict) -> str | None:
    """The cask download-URL template, whether url is a string or {template:}."""
    url = (cask or {}).get("url")
    if isinstance(url, dict):
        return url.get("template")
    if isinstance(url, str):
        return url
    return None


def check_config(path: str, homebrew: bool, tag_prefix: str = "v") -> list[str]:
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
        # Monorepo prefixed-tag repos build under a temporary bare-SemVer tag
        # (.Tag = the temp tag), so a cask URL that relies on {{ .Tag }} points
        # at the tag we delete post-publish → 404. The url template MUST hardcode
        # the final prefix instead (atlassian CLAUDE.md, the #1 release pitfall).
        prefixed = tag_prefix not in ("", "v")
        for i, cask in enumerate(casks):
            if (cask or {}).get("skip_upload") is not True:
                errors.append(
                    f"homebrew_casks[{i}].skip_upload must be true so the "
                    "homebrew-alias step is the single atomic writer "
                    "(distribution.md §8.2)"
                )
            if prefixed:
                tmpl = _cask_url_template(cask)
                if not tmpl:
                    errors.append(
                        f"homebrew_casks[{i}] needs an explicit url template "
                        f"pinning the '{tag_prefix}' tag prefix (monorepo tag "
                        "rename would otherwise 404)"
                    )
                elif f"/download/{tag_prefix}" not in tmpl or _DOT_TAG.search(tmpl):
                    errors.append(
                        f"homebrew_casks[{i}].url template must pin the release "
                        f"path to '/download/{tag_prefix}…' and not use "
                        "{{ .Tag }} (the temp SemVer tag is deleted post-publish)"
                    )
    return errors


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[0] != "check-config":
        print(
            "usage: preflight.py check-config <goreleaser.yml> "
            "[--homebrew] [--tag-prefix <p>]",
            file=sys.stderr,
        )
        return 2
    path = argv[1]
    rest = argv[2:]
    homebrew = "--homebrew" in rest
    tag_prefix = "v"
    if "--tag-prefix" in rest:
        i = rest.index("--tag-prefix")
        if i + 1 >= len(rest):
            print("::error::--tag-prefix requires a value", file=sys.stderr)
            return 2
        tag_prefix = rest[i + 1]
    errors = check_config(path, homebrew, tag_prefix)
    for e in errors:
        print(f"::error::{e}", file=sys.stderr)
    if errors:
        return 1
    print("release preflight OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
