import pathlib
import hashlib
import io
from types import SimpleNamespace

import pytest
import yaml

import winget_submit


def test_package_id_to_winget_path():
    assert (
        winget_submit.package_id_to_winget_path("OpenCLICollective.codereview-cli")
        == "manifests/o/OpenCLICollective/codereview-cli"
    )
    assert (
        winget_submit.package_id_to_winget_path("Microsoft.PowerShell.Preview")
        == "manifests/m/Microsoft/PowerShell/Preview"
    )


def test_select_mode_prefers_update_for_existing_package():
    assert winget_submit.select_mode(exists=True, bootstrap=True) == winget_submit.MODE_UPDATE


def test_select_mode_missing_package_with_bootstrap_disabled():
    assert (
        winget_submit.select_mode(exists=False, bootstrap=False)
        == winget_submit.MODE_MISSING_BOOTSTRAP_DISABLED
    )


def test_select_mode_missing_package_with_bootstrap_enabled():
    assert (
        winget_submit.select_mode(exists=False, bootstrap=True)
        == winget_submit.MODE_FIRST_SUBMISSION
    )


def test_package_exists_returns_false_only_for_confirmed_404():
    def request_json(url, token):
        raise winget_submit.GitHubAPIError(404, "not found")

    assert winget_submit.package_exists(
        "OpenCLICollective.codereview-cli",
        "token",
        request_json=request_json,
    ) is False


def test_package_exists_fails_closed_for_non_404():
    def request_json(url, token):
        raise winget_submit.GitHubAPIError(500, "server error")

    with pytest.raises(winget_submit.SubmitError, match="could not verify"):
        winget_submit.package_exists(
            "OpenCLICollective.codereview-cli",
            "token",
            request_json=request_json,
        )


def test_resolve_windows_assets_from_checksums_and_release_assets():
    checksums = """\
abc123  cr_v1.2.3_windows_amd64.zip
def456  cr_v1.2.3_windows_arm64.zip
"""
    release_assets = {
        "cr_v1.2.3_windows_amd64.zip": "https://example.test/x64.zip",
        "cr_v1.2.3_windows_arm64.zip": "https://example.test/arm64.zip",
    }

    assets = winget_submit.resolve_windows_assets(checksums, release_assets)

    assert assets.x64.url == "https://example.test/x64.zip"
    assert assets.x64.sha256 == "abc123"
    assert assets.arm64.url == "https://example.test/arm64.zip"
    assert assets.arm64.sha256 == "def456"


def test_resolve_windows_assets_uses_configured_markers():
    checksums = """\
abc123  Retune-0.2.0-windows-x64-setup.exe
def456  Retune-0.2.0-windows-arm64-setup.exe
"""
    release_assets = {
        "Retune-0.2.0-windows-x64-setup.exe": "https://example.test/x64.exe",
        "Retune-0.2.0-windows-arm64-setup.exe": "https://example.test/arm64.exe",
    }

    assets = winget_submit.resolve_windows_assets(
        checksums,
        release_assets,
        x64_marker="windows-x64-setup.exe",
        arm64_marker="windows-arm64-setup.exe",
    )

    assert assets.x64.name == "Retune-0.2.0-windows-x64-setup.exe"
    assert assets.x64.url == "https://example.test/x64.exe"
    assert assets.arm64.name == "Retune-0.2.0-windows-arm64-setup.exe"
    assert assets.arm64.url == "https://example.test/arm64.exe"


def test_resolve_windows_assets_rejects_asset_matching_both_markers():
    with pytest.raises(winget_submit.SubmitError, match="both x64 and arm64"):
        winget_submit.resolve_windows_assets(
            "abc123  Retune-windows-x64-setup.exe\n",
            {"Retune-windows-x64-setup.exe": "https://example.test/x64.exe"},
            x64_marker="Retune",
            arm64_marker="windows",
        )


def test_resolve_windows_assets_rejects_duplicate_architecture_matches():
    with pytest.raises(winget_submit.SubmitError, match="multiple x64"):
        winget_submit.resolve_windows_assets(
            "abc123  first_windows_amd64.zip\n"
            "def456  second_windows_amd64.zip\n"
            "ghi789  only_windows_arm64.zip\n",
            {
                "first_windows_amd64.zip": "https://example.test/first-x64.zip",
                "second_windows_amd64.zip": "https://example.test/second-x64.zip",
                "only_windows_arm64.zip": "https://example.test/arm64.zip",
            },
        )


@pytest.mark.parametrize(
    ("x64_marker", "arm64_marker", "message"),
    [
        ("", "windows-arm64-setup.exe", "x64 asset marker"),
        ("windows-x64-setup.exe", "", "arm64 asset marker"),
        ("same", "same", "distinct"),
    ],
)
def test_resolve_windows_assets_rejects_malformed_markers(x64_marker, arm64_marker, message):
    with pytest.raises(winget_submit.SubmitError, match=message):
        winget_submit.resolve_windows_assets(
            "abc123  Retune-0.2.0-windows-x64-setup.exe\n"
            "def456  Retune-0.2.0-windows-arm64-setup.exe\n",
            {
                "Retune-0.2.0-windows-x64-setup.exe": "https://example.test/x64.exe",
                "Retune-0.2.0-windows-arm64-setup.exe": "https://example.test/arm64.exe",
            },
            x64_marker=x64_marker,
            arm64_marker=arm64_marker,
        )


def test_action_declares_defaults_and_forwards_asset_markers():
    action = yaml.safe_load(pathlib.Path(__file__).with_name("action.yml").read_text())
    inputs = action["inputs"]
    submit_step = action["runs"]["steps"][-1]

    assert inputs["x64-marker"]["required"] is False
    assert inputs["x64-marker"]["default"] == winget_submit.DEFAULT_X64_MARKER
    assert inputs["arm64-marker"]["required"] is False
    assert inputs["arm64-marker"]["default"] == winget_submit.DEFAULT_ARM64_MARKER
    assert submit_step["env"]["X64_MARKER"] == "${{ inputs.x64-marker }}"
    assert submit_step["env"]["ARM64_MARKER"] == "${{ inputs.arm64-marker }}"
    assert '--x64-marker "$X64_MARKER"' in submit_step["run"]
    assert '--arm64-marker "$ARM64_MARKER"' in submit_step["run"]


def test_cli_parses_configured_asset_markers(monkeypatch):
    received = {}

    def capture_args(args):
        received.update(vars(args))
        return 0

    monkeypatch.setattr(winget_submit, "run_submit", capture_args)

    assert (
        winget_submit.main(
            [
                "submit",
                "--package-id",
                "OpenCLICollective.Retune",
                "--version",
                "0.2.0",
                "--final-tag",
                "v0.2.0",
                "--repo",
                "open-cli-collective/Retune",
                "--x64-marker",
                "windows-x64-setup.exe",
                "--arm64-marker",
                "windows-arm64-setup.exe",
                "--github-token",
                "github-token",
                "--winget-token",
                "winget-token",
            ]
        )
        == 0
    )
    assert received["x64_marker"] == "windows-x64-setup.exe"
    assert received["arm64_marker"] == "windows-arm64-setup.exe"


def test_load_wingetcreate_asset_uses_github_release_digest():
    def request_json(url, token):
        return {
            "assets": [
                {
                    "name": "wingetcreate.exe",
                    "browser_download_url": "https://example.test/wingetcreate.exe",
                    "digest": "sha256:" + "a" * 64,
                }
            ]
        }

    asset = winget_submit.load_wingetcreate_asset("github-token", request_json=request_json)

    assert asset.url == "https://example.test/wingetcreate.exe"
    assert asset.sha256 == "a" * 64


def test_download_file_rejects_sha256_mismatch(tmp_path, monkeypatch):
    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(winget_submit, "urlopen", lambda request, timeout: Response(b"not the expected bytes"))
    dest = tmp_path / "wingetcreate.exe"

    with pytest.raises(winget_submit.SubmitError, match="checksum"):
        winget_submit.download_file("https://example.test/wingetcreate.exe", dest, 1, "a" * 64)

    assert not dest.exists()


def test_download_file_accepts_matching_sha256(tmp_path, monkeypatch):
    payload = b"expected bytes"

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(winget_submit, "urlopen", lambda request, timeout: Response(payload))
    dest = tmp_path / "wingetcreate.exe"

    winget_submit.download_file(
        "https://example.test/wingetcreate.exe",
        dest,
        1,
        hashlib.sha256(payload).hexdigest(),
    )

    assert dest.read_bytes() == payload


def test_render_bootstrap_manifests_updates_values_without_mutating_source(tmp_path):
    package_id = "OpenCLICollective.codereview-cli"
    source = tmp_path / "tool"
    winget_dir = source / "packaging" / "winget"
    winget_dir.mkdir(parents=True)
    _write_manifest(
        winget_dir / f"{package_id}.yaml",
        {
            "PackageIdentifier": package_id,
            "PackageVersion": "0.0.0",
            "DefaultLocale": "en-US",
            "ManifestType": " version ",
            "ManifestVersion": " 1.9.0 ",
        },
    )
    _write_manifest(
        winget_dir / f"{package_id}.locale.en-US.yaml",
        {
            "PackageIdentifier": package_id,
            "PackageVersion": "0.0.0",
            "PackageLocale": "en-US",
            "Publisher": "Open CLI Collective",
            "PackageName": "Code Review CLI",
            "ShortDescription": "Automated pull-request review CLI",
            "ManifestType": " defaultLocale ",
            "ManifestVersion": " 1.9.0 ",
        },
    )
    installer_path = winget_dir / f"{package_id}.installer.yaml"
    _write_manifest(
        installer_path,
        {
            "PackageIdentifier": package_id,
            "PackageVersion": "0.0.0",
            "InstallerType": "zip",
            "Installers": [
                {
                    "Architecture": "x64",
                    "InstallerUrl": "https://github.com/open-cli-collective/codereview-cli/releases/download/v0.0.0/cr_v0.0.0_windows_amd64.zip",
                    "InstallerSha256": "CHECKSUM_AMD64_PLACEHOLDER",
                },
                {
                    "Architecture": "arm64",
                    "InstallerUrl": "https://github.com/open-cli-collective/codereview-cli/releases/download/v0.0.0/cr_v0.0.0_windows_arm64.zip",
                    "InstallerSha256": "CHECKSUM_ARM64_PLACEHOLDER",
                },
            ],
            "ManifestType": " installer ",
            "ManifestVersion": " 1.9.0 ",
        },
    )
    original_installer = installer_path.read_text()
    rendered = tmp_path / "rendered"
    assets = winget_submit.WindowsAssets(
        x64=winget_submit.WindowsAsset(
            name="cr_v1.2.3_windows_amd64.zip",
            url="https://github.com/open-cli-collective/codereview-cli/releases/download/v1.2.3/cr_v1.2.3_windows_amd64.zip",
            sha256="a" * 64,
        ),
        arm64=winget_submit.WindowsAsset(
            name="cr_v1.2.3_windows_arm64.zip",
            url="https://github.com/open-cli-collective/codereview-cli/releases/download/v1.2.3/cr_v1.2.3_windows_arm64.zip",
            sha256="b" * 64,
        ),
    )

    output_paths = winget_submit.render_bootstrap_manifests(
        package_id=package_id,
        version="1.2.3",
        working_dir=source,
        output_dir=rendered,
        assets=assets,
    )

    assert installer_path.read_text() == original_installer
    assert {path.name for path in output_paths} == {
        f"{package_id}.yaml",
        f"{package_id}.locale.en-US.yaml",
        f"{package_id}.installer.yaml",
    }
    assert all(pathlib.Path(path).is_relative_to(rendered) for path in output_paths)
    rendered_version_text = (rendered / f"{package_id}.yaml").read_text()
    rendered_locale_text = (rendered / f"{package_id}.locale.en-US.yaml").read_text()
    rendered_installer_text = (rendered / f"{package_id}.installer.yaml").read_text()
    assert (
        rendered_version_text.splitlines()[0]
        == "# yaml-language-server: $schema=https://aka.ms/winget-manifest.version.1.9.0.schema.json"
    )
    assert (
        rendered_locale_text.splitlines()[0]
        == "# yaml-language-server: $schema=https://aka.ms/winget-manifest.defaultLocale.1.9.0.schema.json"
    )
    assert (
        rendered_installer_text.splitlines()[0]
        == "# yaml-language-server: $schema=https://aka.ms/winget-manifest.installer.1.9.0.schema.json"
    )
    rendered_version = yaml.safe_load(rendered_version_text)
    rendered_locale = yaml.safe_load(rendered_locale_text)
    assert rendered_version["PackageVersion"] == "1.2.3"
    assert rendered_version["ManifestType"] == "version"
    assert rendered_version["ManifestVersion"] == "1.9.0"
    assert rendered_locale["PackageVersion"] == "1.2.3"
    assert rendered_locale["ManifestType"] == "defaultLocale"
    assert rendered_locale["ManifestVersion"] == "1.9.0"
    rendered_installer = yaml.safe_load(rendered_installer_text)
    assert rendered_installer["PackageVersion"] == "1.2.3"
    assert rendered_installer["ManifestType"] == "installer"
    assert rendered_installer["ManifestVersion"] == "1.9.0"
    installers = {item["Architecture"]: item for item in rendered_installer["Installers"]}
    assert installers["x64"]["InstallerUrl"] == assets.x64.url
    assert installers["x64"]["InstallerSha256"] == "a" * 64
    assert installers["arm64"]["InstallerUrl"] == assets.arm64.url
    assert installers["arm64"]["InstallerSha256"] == "b" * 64


@pytest.mark.parametrize("manifest_name", ["version", "locale", "installer"])
@pytest.mark.parametrize("missing_key", ["ManifestType", "ManifestVersion"])
def test_render_bootstrap_manifests_requires_schema_metadata(tmp_path, manifest_name, missing_key):
    package_id = "OpenCLICollective.codereview-cli"
    source = tmp_path / "tool"
    winget_dir = source / "packaging" / "winget"
    winget_dir.mkdir(parents=True)
    manifests = _bootstrap_manifest_templates(winget_dir, package_id)
    manifests[manifest_name][1].pop(missing_key)
    for path, data in manifests.values():
        _write_manifest(path, data)

    rendered = tmp_path / "rendered"
    with pytest.raises(winget_submit.SubmitError, match=missing_key):
        winget_submit.render_bootstrap_manifests(
            package_id=package_id,
            version="1.2.3",
            working_dir=source,
            output_dir=rendered,
            assets=_assets(),
        )
    assert not rendered.exists()


def test_render_bootstrap_manifests_rejects_unknown_manifest_type(tmp_path):
    package_id = "OpenCLICollective.codereview-cli"
    source = tmp_path / "tool"
    winget_dir = source / "packaging" / "winget"
    winget_dir.mkdir(parents=True)
    manifests = _bootstrap_manifest_templates(winget_dir, package_id)
    manifests["version"][1]["ManifestType"] = "versions"
    for path, data in manifests.values():
        _write_manifest(path, data)

    rendered = tmp_path / "rendered"
    with pytest.raises(winget_submit.SubmitError, match="ManifestType"):
        winget_submit.render_bootstrap_manifests(
            package_id=package_id,
            version="1.2.3",
            working_dir=source,
            output_dir=rendered,
            assets=_assets(),
        )
    assert not rendered.exists()


def test_render_bootstrap_manifests_rejects_output_inside_source(tmp_path):
    source = tmp_path / "tool"
    source.mkdir()
    output = source / "packaging" / "winget" / "rendered"
    assets = winget_submit.WindowsAssets(
        x64=winget_submit.WindowsAsset("x64.zip", "https://example.test/x64.zip", "a"),
        arm64=winget_submit.WindowsAsset("arm64.zip", "https://example.test/arm64.zip", "b"),
    )

    with pytest.raises(winget_submit.SubmitError, match="outside"):
        winget_submit.render_bootstrap_manifests(
            package_id="OpenCLICollective.codereview-cli",
            version="1.2.3",
            working_dir=source,
            output_dir=output,
            assets=assets,
        )


def test_run_submit_existing_package_uses_update_command_and_token_contexts(monkeypatch):
    assets = _assets()
    calls = {}

    def load_release_assets(repo, final_tag, github_token):
        calls["load_release_assets"] = (repo, final_tag, github_token)
        return "checksums", {"asset": "url"}

    def resolve_windows_assets(checksums_text, release_assets, **_markers):
        calls["resolve_windows_assets"] = (checksums_text, release_assets)
        return assets

    def package_exists(package_id, github_token):
        calls["package_exists"] = (package_id, github_token)
        return True

    def download_file(url, dest, timeout_seconds, expected_sha256):
        calls["download_file"] = (url, pathlib.Path(dest).name, timeout_seconds, expected_sha256)

    def run(command, check, timeout):
        calls["run"] = (command, check, timeout)

    monkeypatch.setattr(winget_submit, "load_release_assets", load_release_assets)
    monkeypatch.setattr(winget_submit, "resolve_windows_assets", resolve_windows_assets)
    monkeypatch.setattr(winget_submit, "package_exists", package_exists)
    monkeypatch.setattr(
        winget_submit,
        "load_wingetcreate_asset",
        lambda github_token: winget_submit.DownloadAsset("https://example.test/wingetcreate.exe", "a" * 64),
    )
    monkeypatch.setattr(winget_submit, "download_file", download_file)
    monkeypatch.setattr(winget_submit.subprocess, "run", run)

    rc = winget_submit.run_submit(_args(bootstrap="true"))

    assert rc == 0
    assert calls["package_exists"] == ("OpenCLICollective.codereview-cli", "github-token")
    assert calls["load_release_assets"] == ("open-cli-collective/codereview-cli", "v1.2.3", "github-token")
    assert calls["download_file"] == (
        "https://example.test/wingetcreate.exe",
        "wingetcreate.exe",
        winget_submit.WINGETCREATE_DOWNLOAD_TIMEOUT_SECONDS,
        "a" * 64,
    )
    command, check, timeout = calls["run"]
    assert check is True
    assert timeout == winget_submit.WINGETCREATE_COMMAND_TIMEOUT_SECONDS
    assert command[1:5] == ["update", "OpenCLICollective.codereview-cli", "--version", "1.2.3"]
    assert command[5:8] == ["--urls", assets.x64.url, assets.arm64.url]
    assert command[-2:] == ["--token", "winget-token"]


def test_run_submit_missing_package_with_bootstrap_submits_rendered_directory(monkeypatch):
    assets = _assets()
    calls = {}

    monkeypatch.setattr(
        winget_submit,
        "load_release_assets",
        lambda repo, final_tag, github_token: ("checksums", {"asset": "url"}),
    )
    monkeypatch.setattr(
        winget_submit,
        "resolve_windows_assets",
        lambda checksums, release_assets, **_markers: assets,
    )
    monkeypatch.setattr(winget_submit, "package_exists", lambda package_id, github_token: False)
    monkeypatch.setattr(
        winget_submit,
        "download_file",
        lambda url, dest, timeout_seconds, expected_sha256: calls.setdefault("download_file", pathlib.Path(dest).name),
    )
    monkeypatch.setattr(
        winget_submit,
        "load_wingetcreate_asset",
        lambda github_token: winget_submit.DownloadAsset("https://example.test/wingetcreate.exe", "a" * 64),
    )

    def render_bootstrap_manifests(package_id, version, working_dir, output_dir, assets):
        calls["render"] = (package_id, version, pathlib.Path(working_dir), pathlib.Path(output_dir).exists())
        return [pathlib.Path(output_dir) / f"{package_id}.yaml"]

    def run(command, check, timeout):
        calls["run"] = (command, check, timeout, pathlib.Path(command[-1]).exists())

    monkeypatch.setattr(winget_submit, "render_bootstrap_manifests", render_bootstrap_manifests)
    monkeypatch.setattr(winget_submit.subprocess, "run", run)

    rc = winget_submit.run_submit(_args(bootstrap="true"))

    assert rc == 0
    assert calls["render"] == (
        "OpenCLICollective.codereview-cli",
        "1.2.3",
        pathlib.Path("."),
        True,
    )
    command, check, timeout, rendered_dir_exists_during_submit = calls["run"]
    assert check is True
    assert timeout == winget_submit.WINGETCREATE_COMMAND_TIMEOUT_SECONDS
    assert rendered_dir_exists_during_submit is True
    assert command[1] == "submit"
    assert command[2:5] == ["--prtitle", "New package: OpenCLICollective.codereview-cli version 1.2.3", "--token"]
    assert command[5] == "winget-token"
    assert command[6] == "--no-open"


def test_run_submit_missing_package_without_bootstrap_fails_before_download(monkeypatch):
    calls = {}

    monkeypatch.setattr(
        winget_submit,
        "load_release_assets",
        lambda repo, final_tag, github_token: ("checksums", {"asset": "url"}),
    )
    monkeypatch.setattr(winget_submit, "resolve_windows_assets", lambda checksums, release_assets: _assets())
    monkeypatch.setattr(winget_submit, "package_exists", lambda package_id, github_token: False)
    monkeypatch.setattr(
        winget_submit,
        "download_file",
        lambda url, dest, timeout_seconds, expected_sha256: calls.setdefault("download_file", True),
    )
    monkeypatch.setattr(winget_submit.subprocess, "run", lambda command, check, timeout: calls.setdefault("run", True))

    with pytest.raises(winget_submit.SubmitError, match="bootstrap is not true"):
        winget_submit.run_submit(_args(bootstrap="false"))

    assert "download_file" not in calls
    assert "run" not in calls


def test_run_submit_unverifiable_package_lookup_fails_closed(monkeypatch):
    calls = {}

    monkeypatch.setattr(
        winget_submit,
        "load_release_assets",
        lambda repo, final_tag, github_token: ("checksums", {"asset": "url"}),
    )
    monkeypatch.setattr(winget_submit, "resolve_windows_assets", lambda checksums, release_assets: _assets())

    def package_exists(package_id, github_token):
        raise winget_submit.SubmitError("could not verify whether package exists")

    monkeypatch.setattr(winget_submit, "package_exists", package_exists)
    monkeypatch.setattr(
        winget_submit,
        "download_file",
        lambda url, dest, timeout_seconds, expected_sha256: calls.setdefault("download_file", True),
    )
    monkeypatch.setattr(winget_submit.subprocess, "run", lambda command, check, timeout: calls.setdefault("run", True))

    with pytest.raises(winget_submit.SubmitError, match="could not verify"):
        winget_submit.run_submit(_args(bootstrap="true"))

    assert "download_file" not in calls
    assert "run" not in calls


def _write_manifest(path, data):
    path.write_text(yaml.safe_dump(data, sort_keys=False))


def _bootstrap_manifest_templates(winget_dir, package_id):
    return {
        "version": (
            winget_dir / f"{package_id}.yaml",
            {
                "PackageIdentifier": package_id,
                "PackageVersion": "0.0.0",
                "DefaultLocale": "en-US",
                "ManifestType": "version",
                "ManifestVersion": "1.10.0",
            },
        ),
        "locale": (
            winget_dir / f"{package_id}.locale.en-US.yaml",
            {
                "PackageIdentifier": package_id,
                "PackageVersion": "0.0.0",
                "PackageLocale": "en-US",
                "Publisher": "Open CLI Collective",
                "PackageName": "Code Review CLI",
                "ShortDescription": "Automated pull-request review CLI",
                "ManifestType": "defaultLocale",
                "ManifestVersion": "1.10.0",
            },
        ),
        "installer": (
            winget_dir / f"{package_id}.installer.yaml",
            {
                "PackageIdentifier": package_id,
                "PackageVersion": "0.0.0",
                "InstallerType": "zip",
                "Installers": [
                    {
                        "Architecture": "x64",
                        "InstallerUrl": "old-x64",
                        "InstallerSha256": "old-x64-sha",
                    },
                    {
                        "Architecture": "arm64",
                        "InstallerUrl": "old-arm64",
                        "InstallerSha256": "old-arm64-sha",
                    },
                ],
                "ManifestType": "installer",
                "ManifestVersion": "1.10.0",
            },
        ),
    }


def _assets():
    return winget_submit.WindowsAssets(
        x64=winget_submit.WindowsAsset("x64.zip", "https://example.test/x64.zip", "a"),
        arm64=winget_submit.WindowsAsset("arm64.zip", "https://example.test/arm64.zip", "b"),
    )


def _args(bootstrap):
    return SimpleNamespace(
        package_id="OpenCLICollective.codereview-cli",
        version="1.2.3",
        final_tag="v1.2.3",
        repo="open-cli-collective/codereview-cli",
        working_dir=".",
        bootstrap=bootstrap,
        github_token="github-token",
        winget_token="winget-token",
    )
