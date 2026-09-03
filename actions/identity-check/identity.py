#!/usr/bin/env python3
"""Open CLI Collective identity manifest tool (open-cli-identity/v1).

Single source of truth for reading packaging/identity.yml and enforcing that
its declared identifiers match the tool-native files (distribution.md §8.2).

Subcommands:
  validate     assert the manifest matches .goreleaser / winget / chocolatey
  export-json  print the normalized manifest as JSON (consumed by the
               auto-release / release workflows so they never re-parse YAML)

Path resolution is asymmetric (distribution.md §8.3): the tool-local identity —
the manifest, its packaging/ dirs, and version_file — resolves relative to
--working-dir, while goreleaser_config resolves relative to --repo-root (the
checkout root), because goreleaser is a repo-root release operation even in a
monorepo. For a flat repo both default to "." so behavior is unchanged.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from pathlib import PurePosixPath

import defusedxml.ElementTree as ET  # hardened against XXE / billion-laughs
import yaml
from defusedxml.common import DefusedXmlException
from xml.etree.ElementTree import ParseError

SCHEMA = "open-cli-identity/v1"
WINGET_BOOTSTRAP_TYPE_ERROR = "packages.winget.bootstrap must be a boolean"


class ManifestError(Exception):
    """A drift or schema problem worth failing the check for."""


def _load_yaml(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise ManifestError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ManifestError(f"{path}: expected a YAML mapping")
    return data


# Binary names and chocolatey ids become path segments (packaging/chocolatey/<id>,
# dist/<binary>) so they must be plain identifiers — no separators, no traversal.
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _check_safe_id(manifest_path: str, what: str, value) -> None:
    if not isinstance(value, str) or not SAFE_ID_RE.match(value):
        raise ManifestError(f"{manifest_path}: {what} {value!r} must match {SAFE_ID_RE.pattern}")


def _chocolatey_id(binary: dict):
    return ((binary.get("packages", {}) or {}).get("chocolatey", {}) or {}).get("id")


def load_manifest(manifest_path: str) -> dict:
    m = _load_yaml(manifest_path)
    if m.get("schema") != SCHEMA:
        raise ManifestError(
            f"{manifest_path}: schema must be '{SCHEMA}', got {m.get('schema')!r}"
        )
    if not m.get("goreleaser_config"):
        raise ManifestError(f"{manifest_path}: missing required field 'goreleaser_config'")
    has_binary = "binary" in m
    has_binaries = "binaries" in m
    if has_binary == has_binaries:
        raise ManifestError(f"{manifest_path}: declare exactly one of 'binary' or 'binaries'")
    if has_binary:
        _check_safe_id(manifest_path, "binary", m["binary"])
        if (choco_id := _chocolatey_id(m)) is not None:
            _check_safe_id(manifest_path, "chocolatey.id", choco_id)
    if has_binaries:
        binaries = m["binaries"]
        if not isinstance(binaries, list) or not binaries:
            raise ManifestError(f"{manifest_path}: binaries must be a non-empty list")
        for binary in binaries:
            if not isinstance(binary, dict):
                raise ManifestError(f"{manifest_path}: every binaries entry must be a mapping")
            _check_safe_id(manifest_path, "binaries[].name", binary.get("name"))
            if (choco_id := _chocolatey_id(binary)) is not None:
                _check_safe_id(manifest_path, f"binaries[{binary['name']}].chocolatey.id", choco_id)
        names = [binary["name"] for binary in binaries]
        if len(names) != len(set(names)):
            raise ManifestError(f"{manifest_path}: binary names must be unique")
    return m


def _normalize_binary(binary: dict, chocolatey_dir: str) -> dict:
    pkgs = binary.get("packages", {}) or {}
    hb = pkgs.get("homebrew", {}) or {}
    winget = pkgs.get("winget", {}) or {}
    choco = pkgs.get("chocolatey", {}) or {}
    choco_id = choco.get("id")
    return {
        "name": binary["name"],
        "archives": {"name_template": (binary.get("archives", {}) or {}).get("name_template")},
        "packages": {
            "homebrew": {
                "canonical_cask": hb.get("canonical_cask"),
                "alias_casks": hb.get("alias_casks", []) or [],
            },
            "winget": {"id": winget.get("id"), "bootstrap": _winget_bootstrap(winget)},
            "chocolatey": {
                "id": choco_id,
                "dir": chocolatey_dir,
            },
            "linux": {"package_name": (pkgs.get("linux", {}) or {}).get("package_name")},
            "snap": {"state": (pkgs.get("snap", {}) or {}).get("state")},
        },
        "keychain_probe": binary.get("keychain_probe"),
    }


def normalize(m: dict) -> dict:
    """The stable shape release workflows consume."""
    tag = m.get("tag", {}) or {}
    if "binaries" in m:
        # Several binaries share packaging/, so each chocolatey package gets its
        # own directory named by its id.
        binaries = [
            _normalize_binary(
                binary,
                f"packaging/chocolatey/{_chocolatey_id(binary)}" if _chocolatey_id(binary) else "packaging/chocolatey",
            )
            for binary in m["binaries"]
        ]
    else:
        binary = {
            "name": m["binary"],
            "archives": m.get("archives"),
            "packages": m.get("packages"),
            "keychain_probe": m.get("keychain_probe"),
        }
        binaries = [_normalize_binary(binary, "packaging/chocolatey")]
    return {
        "repo": m.get("repo"),
        "version_file": m.get("version_file", "version.txt"),
        "goreleaser_config": m["goreleaser_config"],
        "tag": {"prefix": tag.get("prefix", "v"), "version_scheme": tag.get("version_scheme")},
        "binaries": binaries,
    }


def _winget_bootstrap(winget: dict) -> bool:
    if "bootstrap" not in winget:
        return False
    value = winget.get("bootstrap")
    if not isinstance(value, bool):
        raise ManifestError(WINGET_BOOTSTRAP_TYPE_ERROR)
    return value


def _validate_keychain_probe(binary: dict) -> list[str]:
    errors: list[str] = []
    probe = binary.get("keychain_probe")
    if probe is None:
        return errors
    if not isinstance(probe, dict):
        return ["keychain_probe must be a mapping"]

    seed = probe.get("seed_config")
    if seed is None:
        return errors
    if not isinstance(seed, dict):
        return ["keychain_probe.seed_config must be a mapping"]

    base = seed.get("base", "xdg_config")
    if base not in ("xdg_config", "native_user_config"):
        errors.append(
            "keychain_probe.seed_config.base must be one of "
            "'xdg_config' or 'native_user_config'"
        )

    path = seed.get("path")
    if path is None:
        return errors
    if not isinstance(path, str):
        errors.append("keychain_probe.seed_config.path must be a string")
        return errors
    if not path.strip():
        errors.append("keychain_probe.seed_config.path must not be empty")
        return errors

    parsed = PurePosixPath(path)
    if parsed.is_absolute():
        errors.append("keychain_probe.seed_config.path must be relative")
    if ".." in parsed.parts:
        errors.append("keychain_probe.seed_config.path must not contain '..'")
    return errors


def _manifest_binaries(m: dict) -> tuple[list[dict], bool]:
    if "binaries" in m:
        return m["binaries"], True
    return [{
        "name": m["binary"],
        "archives": m.get("archives"),
        "packages": m.get("packages"),
        "keychain_probe": m.get("keychain_probe"),
    }], False


def _entry_owner(entry: dict, build_to_binary: dict[str, str], multi: bool, kind: str) -> tuple[str | None, str | None]:
    filters = entry.get("ids", entry.get("builds"))
    if filters is None:
        if multi:
            return None, f"goreleaser {kind} entry must filter one binary with ids/builds"
        if not build_to_binary:
            return None, f"goreleaser {kind} entry cannot be attributed without a build"
        return next(iter(build_to_binary.values()), None), None
    if not isinstance(filters, list) or not filters:
        return None, f"goreleaser {kind} entry has an empty ids/builds filter"
    unknown = sorted(set(filters) - set(build_to_binary))
    if unknown:
        return None, f"goreleaser {kind} entry references unknown build ids {unknown}"
    owners = {build_to_binary[build_id] for build_id in filters}
    if len(owners) != 1:
        return None, f"goreleaser {kind} entry mixes binaries {sorted(owners)}"
    return owners.pop(), None


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


def validate(manifest_path: str, working_dir: str, repo_root: str = ".") -> list[str]:
    """Return a list of drift errors (empty == clean).

    Path resolution is intentionally ASYMMETRIC (distribution.md §8.3):
    `goreleaser_config` resolves relative to `repo_root` (goreleaser is the
    release-orchestration layer and, in a monorepo, runs from the repo root with
    root context — go.work, shared modules, root tags), while the tool-local
    identity (`packaging/*`, `version_file`, and the manifest itself) resolves
    relative to `working_dir`. For a flat repo the two are the same dir, so
    behavior is unchanged; a monorepo passes `working_dir=tools/<tool>` and leaves
    `repo_root` at the checkout root.
    """
    m = load_manifest(manifest_path)
    errors: list[str] = []
    binaries, multi = _manifest_binaries(m)
    by_name = {binary["name"]: binary for binary in binaries}

    # --- .goreleaser (binary + archive templates). If it's missing, record the
    # error but still run the packaging/ checks below — they don't need it, so a
    # mis-named goreleaser file shouldn't hide winget/choco drift. ---
    gor_path = os.path.join(repo_root, m["goreleaser_config"])
    gor: dict | None = None
    if not os.path.isfile(gor_path):
        errors.append(f"goreleaser_config not found: {gor_path}")
    else:
        gor = _load_yaml(gor_path)

    owned: dict[str, dict[str, list[dict]]] = {
        name: {"archives": [], "nfpms": [], "homebrew_casks": []} for name in by_name
    }
    if gor is not None:
        builds = gor.get("builds", []) or []
        build_to_binary: dict[str, str] = {}
        if not builds:
            errors.append("goreleaser has no builds — cannot verify the binary against the manifest")
        else:
            # GoReleaser infers binary from the module when `binary:` is omitted,
            # which we can't verify — so require it explicit (else the drift guard
            # silently passes on an inferred name that may differ).
            if any(not b.get("binary") for b in builds):
                errors.append("every .goreleaser build must set 'binary:' explicitly so it can be verified against the manifest")
            explicit = {b.get("binary") for b in builds if b.get("binary")}
            wanted = set(by_name)
            if explicit and explicit != wanted:
                errors.append(f"goreleaser builds[].binary {sorted(explicit)} != manifest binaries {sorted(wanted)}")
            if multi and any(not b.get("id") for b in builds):
                errors.append("every .goreleaser build must set 'id:' explicitly for a binaries manifest")
            # Builds without an explicit id (allowed for a single-binary manifest)
            # get a synthetic per-index key so only builds that SHARE an explicit
            # id are flagged as duplicates.
            for index, build in enumerate(builds):
                if not build.get("binary"):
                    continue
                dedupe_key = build.get("id", f"__single_{index}")
                if dedupe_key in build_to_binary:
                    errors.append(f"duplicate goreleaser build id '{build.get('id')}'")
                else:
                    build_to_binary[dedupe_key] = build["binary"]

        archive_to_binary: dict[str, str] = {}
        for kind in ("archives", "nfpms"):
            for entry in gor.get(kind, []) or []:
                owner, error = _entry_owner(entry, build_to_binary, multi, kind)
                if error:
                    errors.append(error)
                elif owner in owned:
                    owned[owner][kind].append(entry)
                    if kind == "archives" and entry.get("id"):
                        archive_to_binary[entry["id"]] = owner

        # GoReleaser casks consume archives, so their ids refer to archive ids,
        # not build ids. Single-binary configs may omit filters and retain the
        # historical build-map attribution fallback.
        cask_owners = archive_to_binary if multi else build_to_binary
        for entry in gor.get("homebrew_casks", []) or []:
            owner, error = _entry_owner(entry, cask_owners, multi, "homebrew_casks")
            if error:
                errors.append(error)
            elif owner in owned:
                owned[owner]["homebrew_casks"].append(entry)

    for name, binary in by_name.items():
        pkgs = binary.get("packages", {}) or {}
        errors.extend(f"{name}: {error}" for error in _validate_keychain_probe(binary))
        try:
            _winget_bootstrap(pkgs.get("winget", {}) or {})
        except ManifestError as exc:
            errors.append(f"{name}: {exc}")

        want_tmpl = (binary.get("archives", {}) or {}).get("name_template")
        if gor is not None and want_tmpl:
            entries = owned[name]["archives"]
            if not entries:
                errors.append(f"{name}: manifest declares archives.name_template but .goreleaser has no archives owning this binary")
            for entry in entries:
                if entry.get("name_template") != want_tmpl:
                    errors.append(f"{name}: goreleaser archive name_template '{entry.get('name_template')}' != manifest '{want_tmpl}'")

        linux_pkg = (pkgs.get("linux", {}) or {}).get("package_name")
        if gor is not None and linux_pkg:
            entries = owned[name]["nfpms"]
            if not entries:
                errors.append(f"{name}: manifest declares linux.package_name '{linux_pkg}' but .goreleaser has no owning nfpm")
            for entry in entries:
                if entry.get("package_name") != linux_pkg:
                    errors.append(f"{name}: goreleaser nfpm package_name '{entry.get('package_name')}' != manifest '{linux_pkg}'")

        # alias_casks are intentionally NOT checked: they live only in the
        # manifest and are generated by the homebrew alias post-step, so there is
        # no tool-native copy to enforce against (distribution.md §8.2).
        cask = (pkgs.get("homebrew", {}) or {}).get("canonical_cask")
        if gor is not None and cask:
            entries = owned[name]["homebrew_casks"]
            if not entries:
                errors.append(f"{name}: manifest declares homebrew canonical_cask '{cask}' but .goreleaser has no owning homebrew_cask")
            for entry in entries:
                if entry.get("name") != cask:
                    errors.append(f"{name}: goreleaser homebrew_cask name '{entry.get('name')}' != manifest '{cask}'")

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
                    errors.append(f"{name}: winget {kind} manifest missing: {path}")
            if not locales:
                errors.append(f"{name}: winget locale manifest missing: {wdir}/{winget_id}.locale.*.yaml")
            for path in list(expected.values()) + locales:
                if os.path.isfile(path):
                    got = (_load_yaml(path) or {}).get("PackageIdentifier")
                    if got != winget_id:
                        errors.append(f"{name}: {path}: PackageIdentifier '{got}' != manifest winget.id '{winget_id}'")

        choco_id = (pkgs.get("chocolatey", {}) or {}).get("id")
        if choco_id:
            cdir = os.path.join(working_dir, "packaging", "chocolatey", choco_id) if multi else os.path.join(working_dir, "packaging", "chocolatey")
            nuspecs = glob.glob(os.path.join(cdir, "*.nuspec"))
            if not nuspecs:
                errors.append(f"{name}: manifest declares chocolatey.id '{choco_id}' but no .nuspec in {cdir}")
            for nuspec in nuspecs:
                got = _nuspec_id(nuspec)
                if got != choco_id:
                    errors.append(f"{name}: {nuspec}: <id> '{got}' != chocolatey.id '{choco_id}'")

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
        errors = validate(manifest_path, args.working_dir, args.repo_root)
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
            # goreleaser_config resolves relative to --repo-root (the checkout
            # root), NOT --working-dir — see validate(). Defaults to "." so flat
            # repos (working-dir ".") are unchanged; a monorepo leaves it at the
            # checkout root while pointing --working-dir at tools/<tool>.
            sp.add_argument("--repo-root", default=".")
    args = p.parse_args(argv)
    return cmd_validate(args) if args.cmd == "validate" else cmd_export_json(args)


if __name__ == "__main__":
    sys.exit(main())
