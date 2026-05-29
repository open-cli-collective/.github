#!/usr/bin/env python3
"""Open CLI Collective identity manifest tool (open-cli-identity/v1).

Single source of truth for reading packaging/identity.yml and enforcing that
its declared identifiers match the tool-native files (distribution.md §8.2).

Subcommands:
  validate     assert the manifest matches .goreleaser / winget / chocolatey
  export-json  print the normalized manifest as JSON (consumed by the
               auto-release / release workflows so they never re-parse YAML)

Paths in the manifest (goreleaser_config) and the packaging/ dirs resolve
relative to --working-dir.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

import defusedxml.ElementTree as ET  # hardened against XXE / billion-laughs
import yaml
from defusedxml.common import DefusedXmlException
from xml.etree.ElementTree import ParseError

SCHEMA = "open-cli-identity/v1"


class ManifestError(Exception):
    """A drift or schema problem worth failing the check for."""


def _load_yaml(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ManifestError(f"{path}: expected a YAML mapping")
    return data


def load_manifest(manifest_path: str) -> dict:
    m = _load_yaml(manifest_path)
    if m.get("schema") != SCHEMA:
        raise ManifestError(
            f"{manifest_path}: schema must be '{SCHEMA}', got {m.get('schema')!r}"
        )
    for required in ("binary", "goreleaser_config"):
        if not m.get(required):
            raise ManifestError(f"{manifest_path}: missing required field '{required}'")
    return m


def normalize(m: dict) -> dict:
    """The stable shape #7/#8 consume. Defaults fill what the workflows need."""
    pkgs = m.get("packages", {}) or {}
    hb = pkgs.get("homebrew", {}) or {}
    tag = m.get("tag", {}) or {}
    return {
        "binary": m["binary"],
        "repo": m.get("repo"),
        "goreleaser_config": m["goreleaser_config"],
        "tag": {"prefix": tag.get("prefix", "v"), "version_scheme": tag.get("version_scheme")},
        "archives": {"name_template": (m.get("archives", {}) or {}).get("name_template")},
        "packages": {
            "homebrew": {
                "canonical_cask": hb.get("canonical_cask"),
                "alias_casks": hb.get("alias_casks", []) or [],
            },
            "winget": {"id": (pkgs.get("winget", {}) or {}).get("id")},
            "chocolatey": {"id": (pkgs.get("chocolatey", {}) or {}).get("id")},
            "linux": {"package_name": (pkgs.get("linux", {}) or {}).get("package_name")},
            "snap": {"state": (pkgs.get("snap", {}) or {}).get("state")},
        },
        "keychain_probe": m.get("keychain_probe"),
    }


def _nuspec_id(path: str) -> str | None:
    """Read <id> from a .nuspec, tolerating the default xmlns nuspecs declare."""
    try:
        root = ET.parse(path).getroot()
    except (ParseError, DefusedXmlException) as exc:
        raise ManifestError(f"{path}: invalid nuspec XML: {exc}") from exc
    for el in root.iter():
        tag = el.tag.split("}")[-1]  # strip any {namespace}
        if tag == "id":
            return (el.text or "").strip()
    return None


def validate(manifest_path: str, working_dir: str) -> list[str]:
    """Return a list of drift errors (empty == clean)."""
    m = load_manifest(manifest_path)
    errors: list[str] = []

    # --- .goreleaser (binary + archive templates). If it's missing, record the
    # error but still run the packaging/ checks below — they don't need it, so a
    # mis-named goreleaser file shouldn't hide winget/choco drift. ---
    gor_path = os.path.join(working_dir, m["goreleaser_config"])
    gor: dict | None = None
    if not os.path.isfile(gor_path):
        errors.append(f"goreleaser_config not found: {gor_path}")
    else:
        gor = _load_yaml(gor_path)

    if gor is not None:
        builds = gor.get("builds", []) or []
        if builds:
            # GoReleaser infers binary from the module when `binary:` is omitted,
            # which we can't verify — so require it explicit (else the drift guard
            # silently passes on an inferred name that may differ).
            if any(not b.get("binary") for b in builds):
                errors.append("every .goreleaser build must set 'binary:' explicitly so it can be verified against the manifest")
            explicit = {b.get("binary") for b in builds if b.get("binary")}
            if explicit and explicit != {m["binary"]}:
                errors.append(f"goreleaser builds[].binary {sorted(explicit)} != manifest binary '{m['binary']}'")

        want_tmpl = (m.get("archives", {}) or {}).get("name_template")
        if want_tmpl:
            for arc in gor.get("archives", []) or []:
                got = arc.get("name_template")
                if got is not None and got != want_tmpl:
                    errors.append(f"goreleaser archive name_template '{got}' != manifest '{want_tmpl}'")

    pkgs = m.get("packages", {}) or {}

    # --- linux nfpm + homebrew cask (declared-channel; both read .goreleaser) ---
    # alias_casks are intentionally NOT checked here: they live only in the
    # manifest and are generated by the #8 alias post-step, so there is no
    # tool-native copy to enforce against (distribution.md §8.2, read-from-manifest).
    linux_pkg = (pkgs.get("linux", {}) or {}).get("package_name")
    cask = (pkgs.get("homebrew", {}) or {}).get("canonical_cask")
    if gor is not None and linux_pkg:
        nfpm_names = {n.get("package_name") for n in (gor.get("nfpms", []) or []) if n.get("package_name")}
        if not nfpm_names:
            errors.append(f"manifest declares linux.package_name '{linux_pkg}' but .goreleaser has no nfpms")
        elif nfpm_names != {linux_pkg}:
            errors.append(f"goreleaser nfpms package_name {sorted(nfpm_names)} != manifest '{linux_pkg}'")
    if gor is not None and cask:
        casks = gor.get("homebrew_casks", []) or []
        cask_names = {c.get("name") for c in casks if c.get("name")}
        if not casks:
            errors.append(f"manifest declares homebrew canonical_cask '{cask}' but .goreleaser has no homebrew_casks block")
        elif cask not in cask_names:
            errors.append(f"homebrew_casks names {sorted(cask_names)} do not include canonical_cask '{cask}'")

    # --- winget (declared-channel: all three manifests, every PackageIdentifier) ---
    winget_id = (pkgs.get("winget", {}) or {}).get("id")
    if winget_id:
        wdir = os.path.join(working_dir, "packaging", "winget")
        expected = {
            "version": os.path.join(wdir, f"{winget_id}.yaml"),
            "installer": os.path.join(wdir, f"{winget_id}.installer.yaml"),
        }
        locales = glob.glob(os.path.join(wdir, f"{winget_id}.locale.*.yaml"))
        for kind, path in expected.items():
            if not os.path.isfile(path):
                errors.append(f"winget {kind} manifest missing: {path}")
        if not locales:
            errors.append(f"winget locale manifest missing: {wdir}/{winget_id}.locale.*.yaml")
        for path in list(expected.values()) + locales:
            if os.path.isfile(path):
                got = (_load_yaml(path) or {}).get("PackageIdentifier")
                if got != winget_id:
                    errors.append(f"{path}: PackageIdentifier '{got}' != manifest winget.id '{winget_id}'")

    # --- chocolatey (declared-channel: a .nuspec with matching <id>) ---
    choco_id = (pkgs.get("chocolatey", {}) or {}).get("id")
    if choco_id:
        cdir = os.path.join(working_dir, "packaging", "chocolatey")
        nuspecs = glob.glob(os.path.join(cdir, "*.nuspec"))
        if not nuspecs:
            errors.append(f"manifest declares chocolatey.id '{choco_id}' but no .nuspec in {cdir}")
        elif not any(_nuspec_id(n) == choco_id for n in nuspecs):
            found = {_nuspec_id(n) for n in nuspecs}
            errors.append(f"no .nuspec <id> matches chocolatey.id '{choco_id}' (found {sorted(found)})")

    return errors


def _resolve_manifest(working_dir: str, manifest: str) -> str:
    return manifest if os.path.isabs(manifest) else os.path.join(working_dir, manifest)


def cmd_validate(args) -> int:
    manifest_path = _resolve_manifest(args.working_dir, args.manifest)
    if not os.path.isfile(manifest_path):
        if args.require_manifest:
            print(f"::error::no identity manifest at {manifest_path} (required for a distributed repo)")
            return 1
        print(f"no identity manifest at {manifest_path}; require-manifest is false — skipping")
        return 0
    try:
        errors = validate(manifest_path, args.working_dir)
    except ManifestError as exc:
        print(f"::error::{exc}")
        return 1
    if errors:
        for e in errors:
            print(f"::error::{e}")
        return 1
    print(f"identity-check ok: {manifest_path}")
    return 0


def cmd_export_json(args) -> int:
    manifest_path = _resolve_manifest(args.working_dir, args.manifest)
    if not os.path.isfile(manifest_path):
        print(f"::error::no identity manifest at {manifest_path} — cannot export")
        return 1
    try:
        print(json.dumps(normalize(load_manifest(manifest_path)), indent=2))
    except ManifestError as exc:
        print(f"::error::{exc}")
        return 1
    return 0


def _bool(s: str) -> bool:
    return str(s).strip().lower() in ("1", "true", "yes")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="identity.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("validate", "export-json"):
        sp = sub.add_parser(name)
        sp.add_argument("--working-dir", default=".")
        sp.add_argument("--manifest", default="packaging/identity.yml")
        if name == "validate":
            sp.add_argument("--require-manifest", type=_bool, default=True)
    args = p.parse_args(argv)
    return cmd_validate(args) if args.cmd == "validate" else cmd_export_json(args)


if __name__ == "__main__":
    sys.exit(main())
