"""Unit tests for identity.py — PASS plus every drift rule, built in tmp dirs."""
import copy
import os

import pytest
import yaml

import identity

BASE_MANIFEST = {
    "schema": "open-cli-identity/v1",
    "repo": "slack-chat-api",
    "binary": "slck",
    "version_file": "version.txt",
    "goreleaser_config": ".goreleaser.yml",
    "tag": {"prefix": "v", "version_scheme": "major_minor_run_patch"},
    "archives": {"name_template": "slck_v{{ .Version }}_{{ .Os }}_{{ .Arch }}"},
    "packages": {
        "homebrew": {"canonical_cask": "slck", "alias_casks": ["slack-chat-cli"]},
        "winget": {"id": "OpenCLICollective.slack-chat-cli"},
        "chocolatey": {"id": "slack-chat-cli"},
        "linux": {"package_name": "slck"},
    },
}

BASE_GORELEASER = {
    "builds": [{"binary": "slck"}],
    "archives": [{"name_template": "slck_v{{ .Version }}_{{ .Os }}_{{ .Arch }}"}],
    "nfpms": [{"package_name": "slck"}],
    "homebrew_casks": [{"name": "slck"}],
}

NUSPEC = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://schemas.microsoft.com/packaging/2015/06/nuspec.xsd">
  <metadata>
    <id>{id}</id>
    <version>0.0.0</version>
  </metadata>
</package>
"""


def build(tmp_path, manifest=None, goreleaser=None, winget_id=None, choco_id=None):
    """Write a full fixture set and return the working dir."""
    m = manifest if manifest is not None else copy.deepcopy(BASE_MANIFEST)
    g = goreleaser if goreleaser is not None else copy.deepcopy(BASE_GORELEASER)
    wd = tmp_path
    (wd / "packaging").mkdir(exist_ok=True)
    (wd / "packaging" / "identity.yml").write_text(yaml.safe_dump(m))
    (wd / ".goreleaser.yml").write_text(yaml.safe_dump(g))

    wid = winget_id if winget_id is not None else (m.get("packages", {}).get("winget", {}) or {}).get("id")
    if wid:
        w = wd / "packaging" / "winget"
        w.mkdir(parents=True, exist_ok=True)
        for suffix in (".yaml", ".installer.yaml", ".locale.en-US.yaml"):
            (w / f"{wid}{suffix}").write_text(yaml.safe_dump({"PackageIdentifier": wid}))

    cid = choco_id if choco_id is not None else (m.get("packages", {}).get("chocolatey", {}) or {}).get("id")
    if cid:
        c = wd / "packaging" / "chocolatey"
        c.mkdir(parents=True, exist_ok=True)
        (c / f"{cid}.nuspec").write_text(NUSPEC.format(id=cid))
    return str(wd)


def manifest_path(wd):
    return os.path.join(wd, "packaging", "identity.yml")


def test_pass(tmp_path):
    wd = build(tmp_path)
    assert identity.validate(manifest_path(wd), wd, wd) == []


def test_drift_binary(tmp_path):
    g = copy.deepcopy(BASE_GORELEASER)
    g["builds"][0]["binary"] = "wrong"
    wd = build(tmp_path, goreleaser=g)
    assert any("binary" in e for e in identity.validate(manifest_path(wd), wd, wd))


def test_drift_archive_template(tmp_path):
    g = copy.deepcopy(BASE_GORELEASER)
    g["archives"][0]["name_template"] = "slck_{{ .Version }}"
    wd = build(tmp_path, goreleaser=g)
    assert any("name_template" in e for e in identity.validate(manifest_path(wd), wd, wd))


def test_drift_nfpm_package_name(tmp_path):
    g = copy.deepcopy(BASE_GORELEASER)
    g["nfpms"][0]["package_name"] = "wrong"
    wd = build(tmp_path, goreleaser=g)
    assert any("nfpm" in e for e in identity.validate(manifest_path(wd), wd, wd))


def test_declared_homebrew_without_block_fails(tmp_path):
    g = copy.deepcopy(BASE_GORELEASER)
    del g["homebrew_casks"]
    wd = build(tmp_path, goreleaser=g)
    assert any("homebrew" in e for e in identity.validate(manifest_path(wd), wd, wd))


def test_drift_winget_id(tmp_path):
    wd = build(tmp_path)
    # rewrite one winget manifest with a wrong PackageIdentifier
    bad = os.path.join(wd, "packaging", "winget", "OpenCLICollective.slack-chat-cli.installer.yaml")
    with open(bad, "w") as fh:
        yaml.safe_dump({"PackageIdentifier": "OpenCLICollective.wrong"}, fh)
    assert any("PackageIdentifier" in e for e in identity.validate(manifest_path(wd), wd, wd))


def test_missing_winget_manifest(tmp_path):
    wd = build(tmp_path)
    os.remove(os.path.join(wd, "packaging", "winget", "OpenCLICollective.slack-chat-cli.installer.yaml"))
    assert any("installer manifest missing" in e for e in identity.validate(manifest_path(wd), wd, wd))


def test_drift_choco_id(tmp_path):
    m = copy.deepcopy(BASE_MANIFEST)
    m["packages"]["chocolatey"]["id"] = "expected-id"
    # build writes a nuspec named/ided after the manifest; mutate to mismatch
    wd = build(tmp_path, manifest=m)
    nuspec = os.path.join(wd, "packaging", "chocolatey", "expected-id.nuspec")
    open(nuspec, "w").write(NUSPEC.format(id="actually-different"))
    assert any("chocolatey.id" in e for e in identity.validate(manifest_path(wd), wd, wd))


def test_namespaced_nuspec_id_extracted(tmp_path):
    # nrq's real choco id differs from its binary — proves namespace-aware <id> read
    m = copy.deepcopy(BASE_MANIFEST)
    m["binary"] = "nrq"
    m["packages"]["chocolatey"]["id"] = "nrq-cli"
    m["packages"]["homebrew"]["canonical_cask"] = "nrq"
    m["packages"]["linux"]["package_name"] = "nrq"
    m["packages"]["winget"]["id"] = "OpenCLICollective.newrelic-cli"
    m["archives"]["name_template"] = "nrq_v{{ .Version }}_{{ .Os }}_{{ .Arch }}"
    g = {"builds": [{"binary": "nrq"}], "archives": [{"name_template": "nrq_v{{ .Version }}_{{ .Os }}_{{ .Arch }}"}],
         "nfpms": [{"package_name": "nrq"}], "homebrew_casks": [{"name": "nrq"}]}
    wd = build(tmp_path, manifest=m, goreleaser=g)
    assert identity.validate(manifest_path(wd), wd, wd) == []


def test_missing_manifest_required_fails(tmp_path):
    rc = identity.main(["validate", "--working-dir", str(tmp_path), "--require-manifest", "true"])
    assert rc == 1


def test_missing_manifest_not_required_skips(tmp_path):
    rc = identity.main(["validate", "--working-dir", str(tmp_path), "--require-manifest", "false"])
    assert rc == 0


def test_binary_omitted_fails(tmp_path):
    g = copy.deepcopy(BASE_GORELEASER)
    del g["builds"][0]["binary"]  # goreleaser would infer — unverifiable
    wd = build(tmp_path, goreleaser=g)
    assert any("binary:' explicitly" in e for e in identity.validate(manifest_path(wd), wd, wd))


def test_goreleaser_missing_still_reports_winget_drift(tmp_path):
    m = copy.deepcopy(BASE_MANIFEST)
    m["goreleaser_config"] = "nope.yml"  # mis-named
    wd = build(tmp_path, manifest=m)
    bad = os.path.join(wd, "packaging", "winget", "OpenCLICollective.slack-chat-cli.yaml")
    with open(bad, "w") as fh:
        yaml.safe_dump({"PackageIdentifier": "OpenCLICollective.wrong"}, fh)
    errs = identity.validate(manifest_path(wd), wd, wd)
    assert any("goreleaser_config not found" in e for e in errs)
    assert any("PackageIdentifier" in e for e in errs)  # not hidden by the goreleaser miss


def test_malformed_nuspec_clean_error(tmp_path):
    wd = build(tmp_path)
    nuspec = os.path.join(wd, "packaging", "chocolatey", "slack-chat-cli.nuspec")
    open(nuspec, "w").write("<package><metadata><id>oops")  # truncated XML
    with pytest.raises(identity.ManifestError):
        identity.validate(manifest_path(wd), wd, wd)


def test_no_builds_fails(tmp_path):
    g = copy.deepcopy(BASE_GORELEASER)
    g["builds"] = []
    wd = build(tmp_path, goreleaser=g)
    assert any("no builds" in e for e in identity.validate(manifest_path(wd), wd, wd))


def test_no_archives_when_template_declared_fails(tmp_path):
    g = copy.deepcopy(BASE_GORELEASER)
    g["archives"] = []
    wd = build(tmp_path, goreleaser=g)
    assert any("no archives" in e for e in identity.validate(manifest_path(wd), wd, wd))


def test_stale_extra_nuspec_fails(tmp_path):
    wd = build(tmp_path)
    # a second .nuspec with a different <id> must fail, not be ignored
    extra = os.path.join(wd, "packaging", "chocolatey", "stale.nuspec")
    open(extra, "w").write(NUSPEC.format(id="stale-id"))
    assert any("stale-id" in e for e in identity.validate(manifest_path(wd), wd, wd))


def test_export_json_missing_manifest_errors(tmp_path):
    assert identity.main(["export-json", "--working-dir", str(tmp_path)]) == 1


def test_export_json_shape(tmp_path):
    wd = build(tmp_path)
    norm = identity.normalize(identity.load_manifest(manifest_path(wd)))
    assert norm["binary"] == "slck"
    assert norm["tag"]["prefix"] == "v"
    assert norm["archives"]["name_template"].startswith("slck_v")
    assert norm["packages"]["homebrew"]["alias_casks"] == ["slack-chat-cli"]
    assert norm["packages"]["linux"]["package_name"] == "slck"
    assert norm["version_file"] == "version.txt"


# --- monorepo: tool-local identity + packaging under tools/<tool>, but the
# goreleaser config lives at the repo root and resolves via --repo-root, not
# --working-dir (distribution.md §8.3). Models atlassian-cli's cfl tool. ---

def build_monorepo(tmp_path, tool="cfl", goreleaser_config=".goreleaser-cfl.yml"):
    """cfl-shaped fixture: root-level goreleaser config, tool-local packaging.
    Returns (repo_root, working_dir)."""
    m = {
        "schema": "open-cli-identity/v1",
        "repo": "atlassian-cli",
        "binary": tool,
        "version_file": "version.txt",
        "goreleaser_config": goreleaser_config,
        "tag": {"prefix": f"{tool}-v", "version_scheme": "major_minor_run_patch"},
        "archives": {"name_template": "{{ .ProjectName }}_{{ .Version }}_{{ .Os }}_{{ .Arch }}"},
        "packages": {
            "homebrew": {"canonical_cask": tool},
            "winget": {"id": f"OpenCLICollective.{tool}"},
            "chocolatey": {"id": "confluence-cli"},
            "linux": {"package_name": tool},
        },
    }
    g = {
        "builds": [{"binary": tool, "dir": f"tools/{tool}"}],
        "archives": [{"name_template": "{{ .ProjectName }}_{{ .Version }}_{{ .Os }}_{{ .Arch }}"}],
        "nfpms": [{"package_name": tool}],
        "homebrew_casks": [{"name": tool}],
    }
    # goreleaser config at the REPO ROOT
    (tmp_path / goreleaser_config).write_text(yaml.safe_dump(g))
    # tool-local identity + packaging under tools/<tool>
    wd = tmp_path / "tools" / tool
    (wd / "packaging" / "winget").mkdir(parents=True, exist_ok=True)
    (wd / "packaging" / "chocolatey").mkdir(parents=True, exist_ok=True)
    (wd / "packaging" / "identity.yml").write_text(yaml.safe_dump(m))
    wid = m["packages"]["winget"]["id"]
    for suffix in (".yaml", ".installer.yaml", ".locale.en-US.yaml"):
        (wd / "packaging" / "winget" / f"{wid}{suffix}").write_text(yaml.safe_dump({"PackageIdentifier": wid}))
    cid = m["packages"]["chocolatey"]["id"]
    (wd / "packaging" / "chocolatey" / f"{cid}.nuspec").write_text(NUSPEC.format(id=cid))
    return str(tmp_path), str(wd)


def test_monorepo_root_relative_goreleaser_passes(tmp_path):
    repo_root, wd = build_monorepo(tmp_path)
    # working_dir=tools/cfl finds tool-local packaging; repo_root finds the
    # root goreleaser config. Asymmetric resolution → clean.
    assert identity.validate(os.path.join(wd, "packaging", "identity.yml"), wd, repo_root) == []


def test_monorepo_goreleaser_not_found_under_working_dir(tmp_path):
    # Regression guard: the root goreleaser config must NOT be resolved relative
    # to working_dir — if repo_root is (wrongly) the tool dir, it isn't found.
    repo_root, wd = build_monorepo(tmp_path)
    errs = identity.validate(os.path.join(wd, "packaging", "identity.yml"), wd, wd)
    assert any("goreleaser_config not found" in e for e in errs)
