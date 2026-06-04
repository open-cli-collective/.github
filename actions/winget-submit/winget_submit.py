#!/usr/bin/env python3
"""Submit Open CLI Collective winget updates, including first-time packages."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen, urlretrieve

import yaml

MODE_UPDATE = "update"
MODE_FIRST_SUBMISSION = "first-submission"
MODE_MISSING_BOOTSTRAP_DISABLED = "missing-with-bootstrap-disabled"

WINGETCREATE_URL = "https://aka.ms/wingetcreate/latest"


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


def select_mode(package_exists: bool, bootstrap: bool) -> str:
    if package_exists:
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


def resolve_windows_assets(checksums_text: str, release_assets: dict[str, str]) -> WindowsAssets:
    found: dict[str, WindowsAsset] = {}
    for raw in checksums_text.splitlines():
        parts = raw.strip().split()
        if len(parts) < 2:
            continue
        sha256, name = parts[0], parts[-1].lstrip("*")
        if "windows_amd64.zip" in name:
            arch = "x64"
        elif "windows_arm64.zip" in name:
            arch = "arm64"
        else:
            continue
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

    output.mkdir(parents=True, exist_ok=True)
    rendered: list[Path] = []
    for src in [version_manifest, *locale_manifests, installer_manifest]:
        data = _load_manifest(src, package_id)
        data["PackageVersion"] = version
        if src == installer_manifest:
            _update_installer_manifest(data, assets)
        dest = output / src.name
        dest.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        rendered.append(dest)
    return rendered


def build_update_command(wingetcreate: Path, package_id: str, version: str, assets: WindowsAssets, token: str) -> list[str]:
    return [
        str(wingetcreate),
        "update",
        package_id,
        "--version",
        version,
        "--urls",
        assets.x64.url,
        assets.arm64.url,
        "--submit",
        "--token",
        token,
    ]


def build_submit_command(wingetcreate: Path, package_id: str, version: str, rendered_dir: Path, token: str) -> list[str]:
    return [
        str(wingetcreate),
        "submit",
        "--prtitle",
        f"New package: {package_id} version {version}",
        "--token",
        token,
        "--no-open",
        str(rendered_dir),
    ]


def github_request_json(url: str, token: str):
    try:
        with urlopen(_github_request(url, token, "application/vnd.github+json")) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise GitHubAPIError(exc.code, exc.read().decode("utf-8", "replace")) from exc
    except (URLError, OSError) as exc:
        raise GitHubAPIError(None, str(exc)) from exc


def github_request_text(url: str, token: str, accept: str = "application/vnd.github+json") -> str:
    try:
        with urlopen(_github_request(url, token, accept)) as response:
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

    checksums_text, release_assets = load_release_assets(args.repo, args.final_tag, args.github_token)
    assets = resolve_windows_assets(checksums_text, release_assets)
    exists = package_exists(args.package_id, args.github_token)
    mode = select_mode(exists, bootstrap)
    _write_summary(f"winget-submit mode: {mode} for {args.package_id} {args.version}")

    if mode == MODE_MISSING_BOOTSTRAP_DISABLED:
        raise SubmitError(
            f"{args.package_id} does not exist in microsoft/winget-pkgs and "
            "packages.winget.bootstrap is not true"
        )

    wingetcreate = Path.cwd() / "wingetcreate.exe"
    urlretrieve(WINGETCREATE_URL, wingetcreate)

    if mode == MODE_UPDATE:
        subprocess.run(
            build_update_command(wingetcreate, args.package_id, args.version, assets, args.winget_token),
            check=True,
        )
        return 0

    with tempfile.TemporaryDirectory(prefix="winget-submit-") as tmp:
        rendered_dir = Path(tmp)
        render_bootstrap_manifests(
            package_id=args.package_id,
            version=args.version,
            working_dir=args.working_dir,
            output_dir=rendered_dir,
            assets=assets,
        )
        subprocess.run(
            build_submit_command(
                wingetcreate,
                args.package_id,
                args.version,
                rendered_dir,
                args.winget_token,
            ),
            check=True,
        )
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
    except subprocess.CalledProcessError as exc:
        print(f"::error::wingetcreate failed with exit code {exc.returncode}", file=sys.stderr)
        return exc.returncode or 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
