"""Unit tests for the goreleaser-config preflight."""
import textwrap

import pytest

import preflight


def _write(tmp_path, body: str) -> str:
    p = tmp_path / "goreleaser.yml"
    p.write_text(textwrap.dedent(body))
    return str(p)


def test_clean_no_homebrew(tmp_path):
    cfg = _write(tmp_path, """
        release:
          replace_existing_artifacts: true
    """)
    assert preflight.check_config(cfg, homebrew=False) == []


def test_clean_with_homebrew(tmp_path):
    cfg = _write(tmp_path, """
        release:
          replace_existing_artifacts: true
        homebrew_casks:
          - name: jtk
            skip_upload: true
    """)
    assert preflight.check_config(cfg, homebrew=True) == []


def test_missing_replace_existing_artifacts(tmp_path):
    cfg = _write(tmp_path, "release: {}\n")
    errs = preflight.check_config(cfg, homebrew=False)
    assert any("replace_existing_artifacts" in e for e in errs)


def test_replace_existing_artifacts_false_fails(tmp_path):
    cfg = _write(tmp_path, """
        release:
          replace_existing_artifacts: false
    """)
    errs = preflight.check_config(cfg, homebrew=False)
    assert any("replace_existing_artifacts" in e for e in errs)


def test_no_release_section_fails(tmp_path):
    cfg = _write(tmp_path, "builds: []\n")
    errs = preflight.check_config(cfg, homebrew=False)
    assert any("replace_existing_artifacts" in e for e in errs)


def test_homebrew_declared_but_no_casks(tmp_path):
    cfg = _write(tmp_path, """
        release:
          replace_existing_artifacts: true
    """)
    errs = preflight.check_config(cfg, homebrew=True)
    assert any("no" in e and "homebrew_casks" in e for e in errs)


def test_cask_skip_upload_missing_fails(tmp_path):
    cfg = _write(tmp_path, """
        release:
          replace_existing_artifacts: true
        homebrew_casks:
          - name: jtk
    """)
    errs = preflight.check_config(cfg, homebrew=True)
    assert any("skip_upload" in e for e in errs)


def test_cask_skip_upload_false_fails(tmp_path):
    cfg = _write(tmp_path, """
        release:
          replace_existing_artifacts: true
        homebrew_casks:
          - name: jtk
            skip_upload: false
    """)
    errs = preflight.check_config(cfg, homebrew=True)
    assert any("skip_upload" in e for e in errs)


def test_one_of_many_casks_missing_skip_upload(tmp_path):
    cfg = _write(tmp_path, """
        release:
          replace_existing_artifacts: true
        homebrew_casks:
          - name: jtk
            skip_upload: true
          - name: other
    """)
    errs = preflight.check_config(cfg, homebrew=True)
    assert any("homebrew_casks[1]" in e for e in errs)


def test_homebrew_flag_off_ignores_casks(tmp_path):
    # not declared in manifest → cask config isn't our concern
    cfg = _write(tmp_path, """
        release:
          replace_existing_artifacts: true
        homebrew_casks:
          - name: jtk
    """)
    assert preflight.check_config(cfg, homebrew=False) == []


# --- prefixed-tag (monorepo) cask URL template ---

def _prefixed_cask(tmp_path, url_block: str) -> str:
    return _write(tmp_path, f"""
        release:
          replace_existing_artifacts: true
        homebrew_casks:
          - name: jtk
            skip_upload: true
            {url_block}
    """)


def test_bare_v_prefix_ignores_url_template(tmp_path):
    # single-module repos build under the real SemVer tag — no url-pin needed
    cfg = _prefixed_cask(tmp_path, "")
    assert preflight.check_config(cfg, homebrew=True, tag_prefix="v") == []


def test_prefixed_url_template_pinning_prefix_ok(tmp_path):
    cfg = _prefixed_cask(
        tmp_path,
        'url:\n              template: "https://github.com/o/r/releases/download/jtk-v{{ .Version }}/jtk_{{ .Version }}_{{ .Os }}_{{ .Arch }}.tar.gz"',
    )
    assert preflight.check_config(cfg, homebrew=True, tag_prefix="jtk-v") == []


def test_prefixed_url_template_string_form_ok(tmp_path):
    cfg = _prefixed_cask(
        tmp_path,
        'url: "https://github.com/o/r/releases/download/jtk-v{{ .Version }}/x.tar.gz"',
    )
    assert preflight.check_config(cfg, homebrew=True, tag_prefix="jtk-v") == []


def test_prefixed_url_template_using_dot_tag_fails(tmp_path):
    # {{ .Tag }} = the temp SemVer tag we delete post-publish → 404
    cfg = _prefixed_cask(
        tmp_path,
        'url:\n              template: "https://github.com/o/r/releases/download/{{ .Tag }}/x.tar.gz"',
    )
    errs = preflight.check_config(cfg, homebrew=True, tag_prefix="jtk-v")
    assert any(".Tag" in e for e in errs)


def test_prefixed_url_template_missing_fails(tmp_path):
    cfg = _prefixed_cask(tmp_path, "")
    errs = preflight.check_config(cfg, homebrew=True, tag_prefix="jtk-v")
    assert any("url template" in e for e in errs)


def test_main_exit_codes(tmp_path, capsys):
    good = tmp_path / "good.yml"
    good.write_text("release:\n  replace_existing_artifacts: true\n")
    assert preflight.main(["check-config", str(good)]) == 0
    bad = tmp_path / "bad.yml"
    bad.write_text("release: {}\n")
    assert preflight.main(["check-config", str(bad)]) == 1
    # --tag-prefix wiring (good config, no homebrew → prefix is a no-op)
    assert preflight.main(["check-config", str(good), "--tag-prefix", "jtk-v"]) == 0
    assert preflight.main(["bogus"]) == 2
