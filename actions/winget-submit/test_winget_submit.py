import pathlib

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
    assert winget_submit.select_mode(package_exists=True, bootstrap=True) == winget_submit.MODE_UPDATE


def test_select_mode_missing_package_with_bootstrap_disabled():
    assert (
        winget_submit.select_mode(package_exists=False, bootstrap=False)
        == winget_submit.MODE_MISSING_BOOTSTRAP_DISABLED
    )


def test_select_mode_missing_package_with_bootstrap_enabled():
    assert (
        winget_submit.select_mode(package_exists=False, bootstrap=True)
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
            "ManifestType": "version",
            "ManifestVersion": "1.10.0",
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
            "ManifestType": "defaultLocale",
            "ManifestVersion": "1.10.0",
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
            "ManifestType": "installer",
            "ManifestVersion": "1.10.0",
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
    assert all(pathlib.Path(path).is_relative_to(rendered) for path in output_paths)
    rendered_installer = yaml.safe_load((rendered / f"{package_id}.installer.yaml").read_text())
    assert rendered_installer["PackageVersion"] == "1.2.3"
    installers = {item["Architecture"]: item for item in rendered_installer["Installers"]}
    assert installers["x64"]["InstallerUrl"] == assets.x64.url
    assert installers["x64"]["InstallerSha256"] == "a" * 64
    assert installers["arm64"]["InstallerUrl"] == assets.arm64.url
    assert installers["arm64"]["InstallerSha256"] == "b" * 64


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


def _write_manifest(path, data):
    path.write_text(yaml.safe_dump(data, sort_keys=False))
