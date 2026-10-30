#!/usr/bin/env python3
"""Submit Open CLI Collective winget updates, including first-time packages."""
from __future__ import annotations

import argparse
import base64
import datetime
import json
import os
import sys
import tempfile
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import yaml
import jsonschema
from packaging.version import InvalidVersion, Version

MODE_UPDATE = "update"
MODE_FIRST_SUBMISSION = "first-submission"
MODE_MISSING_BOOTSTRAP_DISABLED = "missing-with-bootstrap-disabled"

HTTP_TIMEOUT_SECONDS = 30
TARGET_MANIFEST_VERSION = "1.28.0"
WINGET_MANIFEST_TYPES = {"version", "installer", "defaultLocale", "locale"}
DEFAULT_X64_MARKER = "windows_amd64.zip"
DEFAULT_ARM64_MARKER = "windows_arm64.zip"


class SubmitError(Exception):
    """A release-channel failure that should surface as a GitHub Actions error."""


class GitHubAPIError(Exception):
    def __init__(self, status: int | None, message: str):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class WindowsAsset:
    name: str
    url: str
    sha256: str


@dataclass(frozen=True)
class WindowsAssets:
    x64: WindowsAsset
    arm64: WindowsAsset


def package_id_to_winget_path(package_id: str) -> str:
    parts = [part for part in package_id.split(".") if part]
    if len(parts) < 2:
        raise SubmitError(f"invalid winget package id: {package_id!r}")
    return "/".join(["manifests", parts[0][0].lower(), *parts])


def select_mode(exists: bool, bootstrap: bool) -> str:
    if exists:
        return MODE_UPDATE
    if bootstrap:
        return MODE_FIRST_SUBMISSION
    return MODE_MISSING_BOOTSTRAP_DISABLED


def package_exists(package_id: str, github_token: str, request_json=None) -> bool:
    request_json = request_json or github_request_json
    path = package_id_to_winget_path(package_id)
    url = f"https://api.github.com/repos/microsoft/winget-pkgs/contents/{quote(path, safe='/')}"
    try:
        request_json(url, github_token)
    except GitHubAPIError as exc:
        if exc.status == 404:
            return False
        raise SubmitError(
            f"could not verify whether {package_id} exists in microsoft/winget-pkgs "
            f"(GitHub API status {exc.status}); refusing to bootstrap"
        ) from exc
    return True


def load_release_assets(repo: str, final_tag: str, github_token: str) -> tuple[str, dict[str, str]]:
    if repo.count("/") != 1:
        raise SubmitError(f"repo must be owner/name, got {repo!r}")
    release_url = f"https://api.github.com/repos/{repo}/releases/tags/{quote(final_tag, safe='')}"
    release = github_request_json(release_url, github_token)
    assets = release.get("assets") or []
    by_name = {asset.get("name"): asset for asset in assets if asset.get("name")}
    checksums = by_name.get("checksums.txt")
    if not checksums:
        raise SubmitError(f"checksums.txt asset not found on release {repo}@{final_tag}")
    checksums_url = checksums.get("url")
    if not checksums_url:
        raise SubmitError("checksums.txt asset has no API URL")
    checksums_text = github_request_text(checksums_url, github_token, accept="application/octet-stream")
    download_urls = {
        name: asset.get("browser_download_url")
        for name, asset in by_name.items()
        if asset.get("browser_download_url")
    }
    return checksums_text, download_urls


def resolve_windows_assets(
    checksums_text: str,
    release_assets: dict[str, str],
    x64_marker: str = DEFAULT_X64_MARKER,
    arm64_marker: str = DEFAULT_ARM64_MARKER,
) -> WindowsAssets:
    if not isinstance(x64_marker, str) or not x64_marker.strip():
        raise SubmitError("x64 asset marker is required")
    if not isinstance(arm64_marker, str) or not arm64_marker.strip():
        raise SubmitError("arm64 asset marker is required")
    x64_marker = x64_marker.strip()
    arm64_marker = arm64_marker.strip()
    if x64_marker == arm64_marker:
        raise SubmitError("x64 and arm64 asset markers must be distinct")

    found: dict[str, WindowsAsset] = {}
    for raw in checksums_text.splitlines():
        parts = raw.strip().split()
        if len(parts) < 2:
            continue
        sha256, name = parts[0], parts[-1].lstrip("*")
        matches = []
        if x64_marker in name:
            matches.append("x64")
        if arm64_marker in name:
            matches.append("arm64")
        if not matches:
            continue
        if len(matches) > 1:
            raise SubmitError(f"release asset {name} matches both x64 and arm64 markers")
        arch = matches[0]
        if arch in found:
            raise SubmitError(
                f"multiple {arch} release assets matched markers: {found[arch].name}, {name}"
            )
        url = release_assets.get(name)
        if not url:
            raise SubmitError(f"release asset {name} listed in checksums.txt was not found")
        found[arch] = WindowsAsset(name=name, url=url, sha256=sha256)

    missing = [arch for arch in ("x64", "arm64") if arch not in found]
    if missing:
        raise SubmitError(f"missing Windows asset checksums for: {', '.join(missing)}")
    return WindowsAssets(x64=found["x64"], arm64=found["arm64"])


def render_bootstrap_manifests(
    *,
    package_id: str,
    version: str,
    working_dir: str | Path,
    output_dir: str | Path,
    assets: WindowsAssets,
) -> list[Path]:
    source_dir = Path(working_dir) / "packaging" / "winget"
    output = Path(output_dir)
    source_resolved = source_dir.resolve()
    output_resolved = output.resolve()
    if _is_relative_to(output_resolved, source_resolved):
        raise SubmitError("bootstrap render output must be outside the source packaging/winget directory")

    version_manifest = source_dir / f"{package_id}.yaml"
    installer_manifest = source_dir / f"{package_id}.installer.yaml"
    locale_manifests = sorted(source_dir.glob(f"{package_id}.locale.*.yaml"))
    missing = [str(path) for path in (version_manifest, installer_manifest) if not path.is_file()]
    if not locale_manifests:
        missing.append(str(source_dir / f"{package_id}.locale.*.yaml"))
    if missing:
        raise SubmitError(f"winget bootstrap template missing: {', '.join(missing)}")

    rendered: list[tuple[Path, str]] = []
    for src in [version_manifest, *locale_manifests, installer_manifest]:
        data = _load_manifest(src, package_id)
        data["PackageVersion"] = version
        if src == installer_manifest:
            _update_installer_manifest(data, assets)
        dest = output / src.name
        rendered.append((dest, _dump_manifest(data, src)))
    output.mkdir(parents=True, exist_ok=True)
    for dest, text in rendered:
        dest.write_text(text, encoding="utf-8")
    return [dest for dest, _ in rendered]


def upgrade_generated_manifests(directory: Path, package_id: str, version: str) -> list[Path]:
    """Upgrade generated manifests without passing them through the old serializer again."""
    paths = sorted(directory.glob("*.yaml"))
    types = set()
    for path in paths:
        data = _load_manifest(path, package_id)
        if str(data["PackageVersion"]) != version:
            raise SubmitError(f"{path}: unexpected package version")
        types.add(data.get("ManifestType"))
        data["ManifestVersion"] = TARGET_MANIFEST_VERSION
        path.write_text(_dump_manifest(data, path), encoding="utf-8")
    if not {"version", "installer", "defaultLocale"}.issubset(types):
        raise SubmitError(f"{directory}: incomplete generated manifests")
    return paths


def render_update_manifests(directory: Path, package_id: str, version: str, assets: WindowsAssets, token: str) -> list[Path]:
    """Copy latest published manifests and update only the release-specific fields."""
    package_path = package_id_to_winget_path(package_id)
    base_url = f"https://api.github.com/repos/microsoft/winget-pkgs/contents/{quote(package_path, safe='/')}"
    entries = github_request_json(base_url, token)
    versions = []
    for item in entries:
        if item.get("type") != "dir":
            continue
        try:
            versions.append((Version(item["name"]), item["name"]))
        except InvalidVersion as exc:
            raise SubmitError(f"cannot safely compare existing package version {item['name']!r}") from exc
    if not versions:
        raise SubmitError("existing package has no version directories")
    try:
        if Version(version) <= max(versions)[0]:
            raise SubmitError(f"{version} is not newer than latest published version {max(versions)[1]}")
    except InvalidVersion as exc:
        raise SubmitError(f"cannot compare new package version {version!r}") from exc
    current = max(versions)[1]
    listing = github_request_json(f"{base_url}/{quote(current, safe='')}", token)
    directory.mkdir(parents=True, exist_ok=True)
    output = []
    types = set()
    for item in listing:
        name = item.get("name", "")
        if not name.endswith(".yaml") or not name.startswith(package_id + "."):
            continue
        response = github_request_json(item["url"], token)
        data = yaml.safe_load(base64.b64decode(response["content"]))
        if not isinstance(data, dict) or data.get("PackageIdentifier") != package_id:
            raise SubmitError(f"{name}: invalid package identifier or manifest")
        if str(data.get("PackageVersion")) != current:
            raise SubmitError(f"{name}: unexpected package version")
        kind = data.get("ManifestType")
        if kind not in WINGET_MANIFEST_TYPES or kind in types and kind != "locale":
            raise SubmitError(f"{name}: unexpected or duplicate manifest type")
        types.add(kind)
        try:
            if Version(str(data.get("ManifestVersion", ""))) > Version(TARGET_MANIFEST_VERSION):
                raise SubmitError(f"{name}: refusing to downgrade newer manifest schema")
        except InvalidVersion as exc:
            raise SubmitError(f"{name}: invalid manifest schema version") from exc
        data["PackageVersion"] = version
        if kind in {"defaultLocale", "locale"}:
            data.pop("ReleaseNotes", None)
            data.pop("ReleaseNotesUrl", None)
        if kind == "installer":
            data.pop("ReleaseDate", None)
            if "InstallerUrl" in data or "InstallerSha256" in data:
                raise SubmitError("root-level installer URL or hash requires manual update")
            installers = data.get("Installers")
            if not isinstance(installers, list) or len(installers) != 2 or sorted(i.get("Architecture", "") for i in installers if isinstance(i, dict)) != ["arm64", "x64"]:
                raise SubmitError("expected exactly one x64 and one arm64 installer; refusing automatic update")
            for installer in installers:
                installer.pop("ReleaseDate", None)
            _update_installer_manifest(data, assets)
        data["ManifestVersion"] = TARGET_MANIFEST_VERSION
        dest = directory / name
        dest.write_text(_dump_manifest(data, dest), encoding="utf-8")
        output.append(dest)
    if not {"version", "installer", "defaultLocale"}.issubset(types):
        raise SubmitError("existing package does not have a complete set of multifile manifests")
    return output


def validate_manifests(files: list[Path]) -> None:
    schemas = {}
    for path in files:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        kind = data["ManifestType"]
        if kind not in schemas:
            url = f"https://aka.ms/winget-manifest.{kind}.{TARGET_MANIFEST_VERSION}.schema.json"
            with urlopen(Request(url, headers={"User-Agent": "open-cli-collective-winget-submit"}), timeout=HTTP_TIMEOUT_SECONDS) as response:
                schemas[kind] = json.load(response)
        # PyYAML recognizes dates; the schema expects their textual YAML representation.
        instance = json.loads(json.dumps(data, default=lambda value: value.isoformat() if isinstance(value, (datetime.date, datetime.datetime)) else str(value)))
        try:
            jsonschema.Draft7Validator(schemas[kind]).validate(instance)
        except jsonschema.ValidationError as exc:
            raise SubmitError(f"{path.name}: schema validation failed at {'.'.join(map(str, exc.absolute_path))}: {exc.message}") from exc


def github_api(url: str, token: str, method: str = "GET", payload=None):
    request = _github_request(url, token, "application/vnd.github+json")
    request.method = method
    if payload is not None:
        request.data = json.dumps(payload).encode("utf-8")
        request.add_header("Content-Type", "application/json")
    try:
        with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise GitHubAPIError(exc.code, exc.read().decode("utf-8", "replace")) from exc
    except (URLError, OSError) as exc:
        raise GitHubAPIError(None, str(exc)) from exc


def submit_manifest_pr(package_id: str, version: str, files: list[Path], token: str, *, new_package: bool):
    """Create an upstream PR from a fresh tree based on the current upstream commit."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", package_id) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+-]*", version):
        raise SubmitError("invalid package ID or version")
    api = "https://api.github.com"
    user = github_api(f"{api}/user", token)["login"]
    fork_url = f"{api}/repos/{quote(user, safe='')}/winget-pkgs"
    try:
        fork = github_api(fork_url, token)
    except GitHubAPIError as exc:
        if exc.status != 404:
            raise
        github_api(f"{api}/repos/microsoft/winget-pkgs/forks", token, "POST", {"default_branch_only": True})
        fork = None
        for _ in range(20):
            time.sleep(3)
            try:
                fork = github_api(fork_url, token)
                break
            except GitHubAPIError as error:
                if error.status != 404:
                    raise
        if fork is None:
            raise SubmitError("winget-pkgs fork was not ready after 60 seconds")
    if (fork.get("parent") or {}).get("full_name", "").lower() != "microsoft/winget-pkgs":
        raise SubmitError(f"{user}/winget-pkgs is not a fork of microsoft/winget-pkgs")
    upstream = github_api(f"{api}/repos/microsoft/winget-pkgs", token)
    base = upstream["default_branch"]
    head = github_api(f"{api}/repos/microsoft/winget-pkgs/git/ref/heads/{quote(base, safe='')}", token)["object"]["sha"]
    commit = github_api(f"{api}/repos/microsoft/winget-pkgs/git/commits/{head}", token)
    manifest_path = f"{package_id_to_winget_path(package_id)}/{version}"
    tree = [{"path": f"{manifest_path}/{f.name}", "mode": "100644", "type": "blob", "content": f.read_text(encoding="utf-8")} for f in files]
    tree_sha = github_api(f"{fork_url}/git/trees", token, "POST", {"base_tree": commit["tree"]["sha"], "tree": tree})["sha"]
    message = f"{package_id} version {version}"
    commit_sha = github_api(f"{fork_url}/git/commits", token, "POST", {"message": message, "tree": tree_sha, "parents": [head]})["sha"]
    branch = f"open-cli-collective-{uuid.uuid4().hex}"
    github_api(f"{fork_url}/git/refs", token, "POST", {"ref": f"refs/heads/{branch}", "sha": commit_sha})
    title = f"New package: {message}" if new_package else message
    pr = github_api(f"{api}/repos/microsoft/winget-pkgs/pulls", token, "POST", {"title": title, "head": f"{user}:{branch}", "base": base, "body": f"Automated WinGet manifest submission for {message}."})
    _write_summary(f"WinGet pull request: {pr['html_url']}")
    return pr["html_url"]


def github_request_json(url: str, token: str):
    try:
        with urlopen(
            _github_request(url, token, "application/vnd.github+json"),
            timeout=HTTP_TIMEOUT_SECONDS,
        ) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise GitHubAPIError(exc.code, exc.read().decode("utf-8", "replace")) from exc
    except (URLError, OSError) as exc:
        raise GitHubAPIError(None, str(exc)) from exc


def github_request_text(url: str, token: str, accept: str = "application/vnd.github+json") -> str:
    try:
        with urlopen(_github_request(url, token, accept), timeout=HTTP_TIMEOUT_SECONDS) as response:
            return response.read().decode("utf-8")
    except HTTPError as exc:
        raise GitHubAPIError(exc.code, exc.read().decode("utf-8", "replace")) from exc
    except (URLError, OSError) as exc:
        raise GitHubAPIError(None, str(exc)) from exc


def run_submit(args) -> int:
    bootstrap = _parse_bool(args.bootstrap)
    if not args.github_token:
        raise SubmitError("github-token is required")
    if not args.winget_token:
        raise SubmitError("winget-token is required")

    exists = package_exists(args.package_id, args.github_token)
    mode = select_mode(exists, bootstrap)
    _write_summary(f"winget-submit mode: {mode} for {args.package_id} {args.version}")

    if mode == MODE_MISSING_BOOTSTRAP_DISABLED:
        raise SubmitError(
            f"{args.package_id} does not exist in microsoft/winget-pkgs and "
            "packages.winget.bootstrap is not true"
        )

    checksums_text, release_assets = load_release_assets(args.repo, args.final_tag, args.github_token)
    assets = resolve_windows_assets(
        checksums_text,
        release_assets,
        x64_marker=getattr(args, "x64_marker", DEFAULT_X64_MARKER),
        arm64_marker=getattr(args, "arm64_marker", DEFAULT_ARM64_MARKER),
    )
    with tempfile.TemporaryDirectory(prefix="winget-submit-") as tmp:
        root = Path(tmp)
        rendered_dir = root / "rendered"
        if mode == MODE_UPDATE:
            files = render_update_manifests(rendered_dir, args.package_id, args.version, assets, args.github_token)
        else:
            files = render_bootstrap_manifests(package_id=args.package_id, version=args.version, working_dir=args.working_dir, output_dir=rendered_dir, assets=assets)
            files = upgrade_generated_manifests(rendered_dir, args.package_id, args.version)
        validate_manifests(files)
        submit_manifest_pr(args.package_id, args.version, files, args.winget_token, new_package=mode == MODE_FIRST_SUBMISSION)
    return 0


def _load_manifest(path: Path, package_id: str) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise SubmitError(f"{path}: expected a YAML mapping")
    if data.get("PackageIdentifier") != package_id:
        raise SubmitError(f"{path}: PackageIdentifier does not match {package_id}")
    if "PackageVersion" not in data:
        raise SubmitError(f"{path}: PackageVersion is required for bootstrap rendering")
    return data


def _update_installer_manifest(data: dict, assets: WindowsAssets) -> None:
    installers = data.get("Installers")
    if not isinstance(installers, list):
        raise SubmitError("installer manifest must contain Installers list")
    by_arch = {"x64": assets.x64, "arm64": assets.arm64}
    seen: set[str] = set()
    for installer in installers:
        if not isinstance(installer, dict):
            continue
        arch = installer.get("Architecture")
        if arch in by_arch:
            asset = by_arch[arch]
            installer["InstallerUrl"] = asset.url
            installer["InstallerSha256"] = asset.sha256
            seen.add(arch)
    missing = [arch for arch in ("x64", "arm64") if arch not in seen]
    if missing:
        raise SubmitError(f"installer manifest missing architectures: {', '.join(missing)}")


def _dump_manifest(data: dict, path: Path) -> str:
    manifest_type = _required_manifest_string(data, "ManifestType", path)
    manifest_version = _required_manifest_string(data, "ManifestVersion", path)
    if manifest_type not in WINGET_MANIFEST_TYPES:
        raise SubmitError(f"{path}: ManifestType must be one of {sorted(WINGET_MANIFEST_TYPES)}")
    rendered = dict(data)
    rendered["ManifestType"] = manifest_type
    rendered["ManifestVersion"] = manifest_version
    schema = (
        "https://aka.ms/"
        f"winget-manifest.{manifest_type}.{manifest_version}.schema.json"
    )
    return (
        f"# yaml-language-server: $schema={schema}\n\n"
        f"{yaml.safe_dump(rendered, sort_keys=False)}"
    )


def _required_manifest_string(data: dict, key: str, path: Path) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SubmitError(f"{path}: {key} is required for bootstrap rendering")
    return value.strip()


def _github_request(url: str, token: str, accept: str) -> Request:
    headers = {
        "Accept": accept,
        "User-Agent": "open-cli-collective-winget-submit",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return Request(url, headers=headers)


def _parse_bool(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes"}


def _write_summary(line: str) -> None:
    print(line)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(f"{line}\n")


def _is_relative_to(path: Path, other: Path) -> bool:
    try:
        path.relative_to(other)
        return True
    except ValueError:
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="winget_submit.py")
    sub = parser.add_subparsers(dest="cmd", required=True)
    submit = sub.add_parser("submit")
    submit.add_argument("--package-id", required=True)
    submit.add_argument("--version", required=True)
    submit.add_argument("--final-tag", required=True)
    submit.add_argument("--repo", required=True)
    submit.add_argument("--working-dir", default=".")
    submit.add_argument("--bootstrap", default="false")
    submit.add_argument("--x64-marker", default=DEFAULT_X64_MARKER)
    submit.add_argument("--arm64-marker", default=DEFAULT_ARM64_MARKER)
    submit.add_argument("--github-token", required=True)
    submit.add_argument("--winget-token", required=True)
    args = parser.parse_args(argv)
    try:
        if args.cmd == "submit":
            return run_submit(args)
    except SubmitError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    except GitHubAPIError as exc:
        print(f"::error::GitHub API request failed with status {exc.status}: {exc}", file=sys.stderr)
        return 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
