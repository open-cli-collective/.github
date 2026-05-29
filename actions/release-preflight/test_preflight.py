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


def test_main_exit_codes(tmp_path, capsys):
    good = _write(tmp_path, "release:\n  replace_existing_artifacts: true\n")
    assert preflight.main(["check-config", good]) == 0
    bad = _write(tmp_path, "release: {}\n")
    assert preflight.main(["check-config", bad]) == 1
    assert preflight.main(["bogus"]) == 2
