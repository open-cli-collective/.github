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


def test_run_submit_existing_package_copies_upstream_manifests(monkeypatch):
    assets = _assets()
    calls = {}
    monkeypatch.setattr(winget_submit, "load_release_assets", lambda *a: ("checksums", {}))
    monkeypatch.setattr(winget_submit, "resolve_windows_assets", lambda *a, **kw: assets)
    monkeypatch.setattr(winget_submit, "package_exists", lambda *a: True)
    monkeypatch.setattr(winget_submit, "render_update_manifests", lambda directory, package_id, version, assets, token: [directory / "a.yaml"])
    monkeypatch.setattr(winget_submit, "validate_manifests", lambda *a: None)
    monkeypatch.setattr(winget_submit, "submit_manifest_pr", lambda package_id, version, files, token, **kw: calls.setdefault("submit", (package_id, version, files, token, kw)))
    assert winget_submit.run_submit(_args(bootstrap="true")) == 0
    assert calls["submit"][3:] == ("winget-token", {"new_package": False})


def test_run_submit_bootstrap_uses_new_github_path(monkeypatch):
    monkeypatch.setattr(winget_submit, "load_release_assets", lambda *a: ("checksums", {}))
    monkeypatch.setattr(winget_submit, "resolve_windows_assets", lambda *a, **kw: _assets())
    monkeypatch.setattr(winget_submit, "package_exists", lambda *a: False)
    monkeypatch.setattr(winget_submit, "render_bootstrap_manifests", lambda **kw: [])
    monkeypatch.setattr(winget_submit, "upgrade_generated_manifests", lambda *a: [pathlib.Path("one.yaml")])
    calls = []
    monkeypatch.setattr(winget_submit, "validate_manifests", lambda *a: None)
    monkeypatch.setattr(winget_submit, "submit_manifest_pr", lambda *a, **kw: calls.append((a, kw)))
    assert winget_submit.run_submit(_args(bootstrap="true")) == 0
    assert calls[0][1] == {"new_package": True}
    assert calls[0][0][3] == "winget-token"


def test_upgrade_generated_manifests_updates_schema_and_checks_types(tmp_path):
    package_id = "OpenCLICollective.example"
    for suffix, kind in [("", "version"), (".installer", "installer"), (".locale.en-US", "defaultLocale")]:
        _write_manifest(tmp_path / f"{package_id}{suffix}.yaml", {"PackageIdentifier": package_id, "PackageVersion": "1.2.3", "ManifestType": kind, "ManifestVersion": "1.12.0"})
    paths = winget_submit.upgrade_generated_manifests(tmp_path, package_id, "1.2.3")
    assert len(paths) == 3
    for path in paths:
        content = path.read_text()
        assert "1.28.0.schema.json" in content
        assert yaml.safe_load(content)["ManifestVersion"] == "1.28.0"


def test_submit_manifest_pr_builds_tree_on_upstream_head(monkeypatch, tmp_path):
    manifest = tmp_path / "OpenCLICollective.example.yaml"
    manifest.write_text("content")
    calls = []
    def api(url, token, method="GET", payload=None):
        calls.append((url, token, method, payload))
        if url.endswith("/user"): return {"login": "alice"}
        if url.endswith("/repos/alice/winget-pkgs"): return {"parent": {"full_name": "microsoft/winget-pkgs"}}
        if url.endswith("/repos/microsoft/winget-pkgs"): return {"default_branch": "master"}
        if "/git/ref/heads/" in url: return {"object": {"sha": "base"}}
        if url.endswith("/git/commits/base"): return {"tree": {"sha": "base-tree"}}
        if url.endswith("/git/trees"): return {"sha": "new-tree"}
        if url.endswith("/git/commits"): return {"sha": "new-commit"}
        if url.endswith("/git/refs"): return {}
        if url.endswith("/pulls"): return {"html_url": "https://github.com/microsoft/winget-pkgs/pull/1"}
        raise AssertionError(url)
    monkeypatch.setattr(winget_submit, "github_api", api)
    result = winget_submit.submit_manifest_pr("OpenCLICollective.example", "1.2.3", [manifest], "token", new_package=True)
    assert result.endswith("/1")
    tree = next(c[3] for c in calls if c[0].endswith("/git/trees"))
    assert tree["base_tree"] == "base-tree"
    assert tree["tree"][0]["path"] == "manifests/o/OpenCLICollective/example/1.2.3/OpenCLICollective.example.yaml"
    commit = next(c[3] for c in calls if c[0].endswith("/git/commits"))
    assert commit["parents"] == ["base"]
    pr = next(c[3] for c in calls if c[0].endswith("/pulls"))
    assert pr["base"] == "master" and pr["head"].startswith("alice:")


def test_missing_package_without_bootstrap_fails_before_release_lookup(monkeypatch):
    monkeypatch.setattr(winget_submit, "package_exists", lambda *a: False)
    monkeypatch.setattr(winget_submit, "load_release_assets", lambda *a: pytest.fail("unexpected release lookup"))
    with pytest.raises(winget_submit.SubmitError, match="bootstrap is not true"):
        winget_submit.run_submit(_args(bootstrap="false"))


def test_unverifiable_package_lookup_fails_closed(monkeypatch):
    def fail(*args):
        raise winget_submit.SubmitError("could not verify")
    monkeypatch.setattr(winget_submit, "package_exists", fail)
    monkeypatch.setattr(winget_submit, "load_release_assets", lambda *a: pytest.fail("unexpected release lookup"))
    with pytest.raises(winget_submit.SubmitError, match="could not verify"):
        winget_submit.run_submit(_args(bootstrap="true"))


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



def test_render_update_preserves_new_fields_and_updates_assets(monkeypatch, tmp_path):
    import base64, json
    package = "OpenCLICollective.codereview-cli"
    records = {
        f"{package}.yaml": {"PackageIdentifier": package, "PackageVersion": "1.0.0", "ManifestType": "version", "ManifestVersion": "1.28.0", "UnknownNewField": {"keep": "me"}},
        f"{package}.locale.en-US.yaml": {"PackageIdentifier": package, "PackageVersion": "1.0.0", "ManifestType": "defaultLocale", "ManifestVersion": "1.28.0"},
        f"{package}.installer.yaml": {"PackageIdentifier": package, "PackageVersion": "1.0.0", "ManifestType": "installer", "ManifestVersion": "1.28.0", "Installers": [{"Architecture": "x64", "InstallerUrl": "old", "InstallerSha256": "old", "UnknownField": "keep"}, {"Architecture": "arm64", "InstallerUrl": "old", "InstallerSha256": "old"}]},
    }
    listing = [{"name": name, "url": f"https://test/{name}"} for name in records]
    def fetch(url, token):
        if url.endswith("/1.0.0"): return listing
        if url.startswith("https://test/"):
            name = url.rsplit("/", 1)[-1]
            return {"content": base64.b64encode(json.dumps(records[name]).encode()).decode()}
        return [{"name": "1.0.0", "type": "dir"}]
    monkeypatch.setattr(winget_submit, "github_request_json", fetch)
    output = winget_submit.render_update_manifests(tmp_path, package, "1.2.3", _assets(), "token")
    assert len(output) == 3
    version = yaml.safe_load((tmp_path / f"{package}.yaml").read_text())
    assert version["UnknownNewField"] == {"keep": "me"}
    assert version["PackageVersion"] == "1.2.3"
    installer = yaml.safe_load((tmp_path / f"{package}.installer.yaml").read_text())
    assert installer["Installers"][0]["UnknownField"] == "keep"
    assert installer["Installers"][0]["InstallerUrl"] == _assets().x64.url
    assert installer["Installers"][1]["InstallerSha256"] == _assets().arm64.sha256
