from types import SimpleNamespace
from urllib.error import URLError

import pytest

import chocolatey_push


EMPTY_FEED = """\
<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title type="text">Packages</title>
</feed>
"""

ENTRY_FEED = """\
<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry><title>codereview-cli</title></entry>
</feed>
"""


def test_pack_and_push_success(tmp_path):
    calls = []
    work = _working_dir(tmp_path)
    choco_dir = work / "packaging" / "chocolatey"

    def runner(command, cwd, text, capture_output):
        calls.append((command, cwd))
        if command == ["choco", "pack"]:
            (cwd / "codereview-cli.1.0.0.nupkg").write_text("pkg")
            return _result(0, stdout="packed\n")
        return _result(0, stdout="pushed\n")

    rc = chocolatey_push.pack_and_push(
        package_id="codereview-cli",
        working_dir=work,
        api_key="key",
        command_runner=runner,
    )

    assert rc == 0
    assert calls == [
        (["choco", "pack"], choco_dir),
        (
            [
                "choco",
                "push",
                "codereview-cli.1.0.0.nupkg",
                "--source",
                "https://push.chocolatey.org/",
                "--key",
                "key",
            ],
            choco_dir,
        ),
    ]


def test_pack_and_push_requires_api_key(tmp_path):
    with pytest.raises(chocolatey_push.PushError, match="chocolatey-api-key"):
        chocolatey_push.pack_and_push(
            package_id="codereview-cli",
            working_dir=_working_dir(tmp_path),
            api_key="",
            command_runner=lambda command, cwd, text, capture_output: _result(0),
        )


def test_pack_failure_fails_release(tmp_path):
    def runner(command, cwd, text, capture_output):
        return _result(1, stderr="pack failed")

    with pytest.raises(chocolatey_push.PushError, match="choco pack failed"):
        chocolatey_push.pack_and_push(
            package_id="codereview-cli",
            working_dir=_working_dir(tmp_path),
            api_key="key",
            command_runner=runner,
        )


def test_non_forbidden_push_failure_fails_release(tmp_path):
    def runner(command, cwd, text, capture_output):
        if command == ["choco", "pack"]:
            (cwd / "codereview-cli.1.0.0.nupkg").write_text("pkg")
            return _result(0)
        return _result(1, stderr="500 server error")

    with pytest.raises(chocolatey_push.PushError, match="choco push failed"):
        chocolatey_push.pack_and_push(
            package_id="codereview-cli",
            working_dir=_working_dir(tmp_path),
            api_key="key",
            command_runner=runner,
        )


def test_forbidden_pending_first_submission_succeeds_with_warning(tmp_path):
    summary = tmp_path / "summary.md"
    work = _working_dir(tmp_path)
    choco_dir = work / "packaging" / "chocolatey"
    calls = []

    rc = chocolatey_push.pack_and_push(
        package_id="codereview-cli",
        working_dir=work,
        api_key="key",
        command_runner=_forbidden_push_runner(tmp_path, calls=calls),
        http_get=_http_get({"/package/codereview-cli": (200, "nupkg"), "/Packages()?": (200, EMPTY_FEED)}),
        summary_path=str(summary),
    )

    assert rc == 0
    assert calls == [
        (["choco", "pack"], choco_dir),
        (
            [
                "choco",
                "push",
                "codereview-cli.1.0.0.nupkg",
                "--source",
                "https://push.chocolatey.org/",
                "--key",
                "key",
            ],
            choco_dir,
        ),
    ]
    assert "was not accepted for this release" in summary.read_text()


@pytest.mark.parametrize(
    "stderr",
    [
        "403 (Forbidden): Invalid API Key",
        "403 (Forbidden): invalid apikey",
        "403 (Forbidden): API key is invalid",
        "403 (Forbidden): unauthorized",
        "403 (Forbidden): not authorized to push package codereview-cli",
        "403 (Forbidden): package is not owned by this user",
        "403 (Forbidden): not owned by this account",
        "403 (Forbidden): not the owner of package codereview-cli",
        "403 (Forbidden): package owner mismatch",
    ],
)
def test_forbidden_credential_or_owner_output_fails_before_probe(tmp_path, stderr):
    calls = []

    def http_get(url):
        calls.append(url)
        return chocolatey_push.HttpResponse(200, EMPTY_FEED)

    with pytest.raises(chocolatey_push.PushError, match="credential"):
        chocolatey_push.pack_and_push(
            package_id="codereview-cli",
            working_dir=_working_dir(tmp_path),
            api_key="key",
            command_runner=_forbidden_push_runner(tmp_path, stderr=stderr),
            http_get=http_get,
        )
    assert calls == []


def test_forbidden_visible_package_fails_release(tmp_path):
    with pytest.raises(chocolatey_push.PushError, match="pending first-submission"):
        chocolatey_push.pack_and_push(
            package_id="codereview-cli",
            working_dir=_working_dir(tmp_path),
            api_key="key",
            command_runner=_forbidden_push_runner(tmp_path),
            http_get=_http_get(
                {"/package/codereview-cli": (200, "nupkg"), "/Packages()?": (200, ENTRY_FEED)}
            ),
        )


def test_forbidden_package_not_found_fails_release(tmp_path):
    with pytest.raises(chocolatey_push.PushError, match="pending first-submission"):
        chocolatey_push.pack_and_push(
            package_id="codereview-cli",
            working_dir=_working_dir(tmp_path),
            api_key="key",
            command_runner=_forbidden_push_runner(tmp_path),
            http_get=_http_get({"/package/codereview-cli": (404, "")}),
        )


@pytest.mark.parametrize(
    "responses, error",
    [
        ({"/package/codereview-cli": (503, "")}, "package endpoint"),
        ({"/package/codereview-cli": (200, "nupkg"), "/Packages()?": (503, "")}, "listing"),
        (
            {"/package/codereview-cli": (200, "nupkg"), "/Packages()?": (200, "not xml")},
            "malformed",
        ),
        (
            {"/package/codereview-cli": (200, "nupkg"), "/Packages()?": (200, "<error />")},
            "Atom feed",
        ),
    ],
)
def test_forbidden_probe_uncertainty_fails_closed(tmp_path, responses, error):
    with pytest.raises(chocolatey_push.ProbeError, match=error):
        chocolatey_push.pack_and_push(
            package_id="codereview-cli",
            working_dir=_working_dir(tmp_path),
            api_key="key",
            command_runner=_forbidden_push_runner(tmp_path),
            http_get=_http_get(responses),
        )


def test_probe_package_state_builds_expected_urls():
    seen = []

    def http_get(url):
        seen.append(url)
        if "/package/codereview-cli" in url:
            return chocolatey_push.HttpResponse(200, "nupkg")
        return chocolatey_push.HttpResponse(200, EMPTY_FEED)

    state = chocolatey_push.probe_package_state("codereview-cli", http_get=http_get)

    assert state.pending_first_submission is True
    assert seen[0] == "https://community.chocolatey.org/api/v2/package/codereview-cli"
    assert "%24filter=Id+eq+%27codereview-cli%27" in seen[1]


def test_probe_package_state_escapes_odata_string_quotes():
    seen = []

    def http_get(url):
        seen.append(url)
        if "/package/code%27review-cli" in url:
            return chocolatey_push.HttpResponse(200, "nupkg")
        return chocolatey_push.HttpResponse(200, EMPTY_FEED)

    state = chocolatey_push.probe_package_state("code'review-cli", http_get=http_get)

    assert state.pending_first_submission is True
    assert "%24filter=Id+eq+%27code%27%27review-cli%27" in seen[1]


def test_request_text_transport_failure_fails_closed(monkeypatch):
    def fail(request, timeout):
        raise URLError("network down")

    monkeypatch.setattr(chocolatey_push, "urlopen", fail)

    with pytest.raises(chocolatey_push.ProbeError, match="request failed"):
        chocolatey_push.request_text("https://community.chocolatey.org/api/v2/package/codereview-cli")


def _working_dir(tmp_path):
    work = tmp_path / "repo"
    (work / "packaging" / "chocolatey").mkdir(parents=True)
    return work


def _forbidden_push_runner(
    tmp_path,
    stderr="Response status code does not indicate success: 403 (Forbidden).",
    calls=None,
):
    def runner(command, cwd, text, capture_output):
        if calls is not None:
            calls.append((command, cwd))
        if command == ["choco", "pack"]:
            (cwd / "codereview-cli.1.0.0.nupkg").write_text("pkg")
            return _result(0, stdout="packed\n")
        return _result(1, stderr=stderr)

    return runner


def _http_get(responses):
    def http_get(url):
        for needle, (status, body) in responses.items():
            if needle in url:
                return chocolatey_push.HttpResponse(status, body)
        raise AssertionError(f"unexpected URL: {url}")

    return http_get


def _result(returncode, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)
