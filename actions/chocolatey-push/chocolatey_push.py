#!/usr/bin/env python3
"""Pack and push Chocolatey packages with first-submission moderation handling."""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from defusedxml import ElementTree as ET

HTTP_TIMEOUT_SECONDS = 30
CHOCO_SOURCE = "https://push.chocolatey.org/"
COMMUNITY_API = "https://community.chocolatey.org/api/v2"
ATOM_NS = "{http://www.w3.org/2005/Atom}"
URL_PLACEHOLDERS = ("URL_AMD64_PLACEHOLDER", "URL_ARM64_PLACEHOLDER")
CHECKSUM_PLACEHOLDERS = ("CHECKSUM_AMD64_PLACEHOLDER", "CHECKSUM_ARM64_PLACEHOLDER")
RUNTIME_VERSION_EXPRESSION = re.compile(r"(?i)ChocolateyPackageVersion|\$\{\s*version\s*\}")


class PushError(Exception):
    """A Chocolatey publishing failure that should fail the release."""


class ProbeError(Exception):
    """A Chocolatey package-state probe was unavailable or ambiguous."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def output(self) -> str:
        return "\n".join(part for part in (self.stdout, self.stderr) if part)


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: str


@dataclass(frozen=True)
class PackageState:
    direct_package_exists: bool
    approved_entries: int

    @property
    def pending_first_submission(self) -> bool:
        return self.direct_package_exists and self.approved_entries == 0


def pack_and_push(
    *,
    package_id: str,
    working_dir: str | Path,
    package_dir: str | Path = "packaging/chocolatey",
    api_key: str,
    command_runner=None,
    http_get=None,
    summary_path: str | None = None,
    require_static_urls: bool = False,
) -> int:
    if not api_key:
        raise PushError("chocolatey-api-key secret is required")

    choco_dir = Path(working_dir) / package_dir
    pack = run_command(["choco", "pack"], choco_dir, command_runner)
    _print_command_output(pack)
    if pack.returncode != 0:
        raise PushError(f"choco pack failed with exit code {pack.returncode}")

    packages = sorted(choco_dir.glob("*.nupkg"))
    if len(packages) != 1:
        raise PushError(f"expected exactly one .nupkg, found {len(packages)}")
    if require_static_urls:
        validate_static_package(packages[0])

    push = run_command(
        [
            "choco",
            "push",
            packages[0].name,
            "--source",
            CHOCO_SOURCE,
            "--key",
            api_key,
        ],
        choco_dir,
        command_runner,
    )
    _print_command_output(push)
    if push.returncode == 0:
        return 0
    if not _looks_like_forbidden(push.output):
        raise PushError(f"choco push failed with exit code {push.returncode}")
    if _looks_like_credential_or_owner_failure(push.output):
        raise PushError("choco push returned 403 with a credential or ownership failure")

    state = probe_package_state(package_id, http_get=http_get)
    if not state.pending_first_submission:
        raise PushError(
            "choco push returned 403, but Chocolatey did not report a pending first-submission state"
        )

    message = (
        f"Chocolatey package {package_id} has a submitted version in moderation and no "
        "approved/listed versions yet. The current .nupkg was not accepted for this "
        "release; retry after Chocolatey approves the first submitted version."
    )
    print(f"::warning::{message}")
    _write_summary(summary_path, f"WARNING: {message}")
    return 0


def validate_static_package(package: Path) -> None:
    """Reject migrated packages that still defer release URLs to install time."""
    try:
        with zipfile.ZipFile(package) as archive:
            scripts = [name for name in archive.namelist() if name.lower().endswith((".ps1", ".nuspec"))]
            if not scripts:
                raise PushError(f"{package.name} contains no Chocolatey scripts or nuspec")
            if not any(name.replace("\\", "/").rsplit("/", 1)[-1].lower() == "chocolateyinstall.ps1" for name in scripts):
                raise PushError(f"{package.name} does not contain tools/chocolateyInstall.ps1")
            for name in scripts:
                # Windows PowerShell 5.1 may pack UTF-16 scripts; removing its
                # NUL padding keeps the token checks encoding-independent.
                text = archive.read(name).decode("utf-8", errors="replace").replace("\x00", "")
                leftovers = [token for token in URL_PLACEHOLDERS + CHECKSUM_PLACEHOLDERS if token in text]
                if leftovers:
                    raise PushError(f"{package.name}/{name} retains placeholders: {', '.join(leftovers)}")
                if RUNTIME_VERSION_EXPRESSION.search(text):
                    raise PushError(f"{package.name}/{name} retains a runtime package-version expression")
    except zipfile.BadZipFile as exc:
        raise PushError(f"cannot inspect invalid Chocolatey package {package.name}") from exc


def run_command(command: list[str], cwd: Path, command_runner=None) -> CommandResult:
    command_runner = command_runner or subprocess.run
    result = command_runner(command, cwd=cwd, text=True, capture_output=True)
    return CommandResult(
        returncode=result.returncode,
        stdout=result.stdout or "",
        stderr=result.stderr or "",
    )


def probe_package_state(package_id: str, http_get=None) -> PackageState:
    http_get = http_get or request_text
    package_url = f"{COMMUNITY_API}/package/{quote(package_id, safe='')}"
    package_response = http_get(package_url)
    if package_response.status == 404:
        return PackageState(direct_package_exists=False, approved_entries=0)
    if package_response.status != 200:
        raise ProbeError(f"Chocolatey package endpoint returned HTTP {package_response.status}")

    query = urlencode(
        {"$filter": f"Id eq '{_odata_string(package_id)}'", "$orderby": "Version desc"},
        quote_via=quote,
    )
    feed_response = http_get(f"{COMMUNITY_API}/Packages()?{query}")
    if feed_response.status != 200:
        raise ProbeError(f"Chocolatey package listing returned HTTP {feed_response.status}")
    entries = _parse_atom_entries(feed_response.body)
    return PackageState(direct_package_exists=True, approved_entries=entries)


def request_text(url: str) -> HttpResponse:
    request = Request(url, headers={"User-Agent": "open-cli-collective-chocolatey-push"})
    try:
        with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            return HttpResponse(response.status, response.read().decode("utf-8", "replace"))
    except HTTPError as exc:
        return HttpResponse(exc.code, exc.read().decode("utf-8", "replace"))
    except (URLError, OSError, TimeoutError) as exc:
        raise ProbeError(f"Chocolatey request failed for {url}: {exc}") from exc


def _parse_atom_entries(body: str) -> int:
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise ProbeError("Chocolatey package listing returned malformed XML") from exc
    if root.tag != f"{ATOM_NS}feed":
        raise ProbeError("Chocolatey package listing did not return an Atom feed")
    return len(root.findall(f"{ATOM_NS}entry"))


def _looks_like_forbidden(output: str) -> bool:
    return "403" in output and "Forbidden" in output


def _odata_string(value: str) -> str:
    return value.replace("'", "''")


def _looks_like_credential_or_owner_failure(output: str) -> bool:
    lower = output.lower()
    patterns = (
        "invalid api key",
        "invalid apikey",
        "api key is invalid",
        "unauthorized",
        "not authorized",
        "not owned",
        "not the owner",
        "package owner",
    )
    return any(pattern in lower for pattern in patterns)


def _print_command_output(result: CommandResult) -> None:
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.stderr:
        print(result.stderr, end="" if result.stderr.endswith("\n") else "\n", file=sys.stderr)


def _write_summary(summary_path: str | None, line: str) -> None:
    summary = summary_path or os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(f"{line}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="chocolatey_push.py")
    sub = parser.add_subparsers(dest="cmd", required=True)
    push = sub.add_parser("push")
    push.add_argument("--package-id", required=True)
    push.add_argument("--working-directory", default=".")
    push.add_argument("--package-directory", default="packaging/chocolatey")
    push.add_argument("--api-key-env", default="CHOCO_API_KEY")
    push.add_argument("--require-static-urls", action="store_true")
    args = parser.parse_args(argv)

    try:
        if args.cmd == "push":
            return pack_and_push(
                package_id=args.package_id,
                working_dir=args.working_directory,
                package_dir=args.package_directory,
                api_key=os.environ.get(args.api_key_env, ""),
                require_static_urls=args.require_static_urls,
            )
    except (PushError, ProbeError) as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
