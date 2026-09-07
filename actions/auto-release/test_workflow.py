"""Exercise the actual workflow shell blocks without minting release tags."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/auto-release.yml").read_text())
STEPS = WORKFLOW["jobs"]["auto-release"]["steps"]
META = next(step for step in STEPS if step.get("id") == "meta")
GATE = next(step for step in STEPS if step.get("id") == "gate")
TAG = next(step for step in STEPS if step.get("name") == "Tag")
ACTION = yaml.safe_load((ROOT / "actions/auto-release/action.yml").read_text())


class ManualReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.git("init", "-q")
        self.git("config", "user.name", "Release Test")
        self.git("config", "user.email", "release-test@example.com")
        (self.repo / "version.txt").write_text("1.2\n")
        self.git("add", "version.txt")

    def git(self, *args):
        return subprocess.check_output(
            ["git", *args], cwd=self.repo, text=True, stderr=subprocess.STDOUT
        ).strip()

    def commit(self, message):
        self.git("-c", "commit.gpgsign=false", "commit", "-qm", message)
        return self.git("rev-parse", "HEAD")

    def run_script(self, script, **env):
        output = self.repo / "output"
        output.write_text("")
        result = subprocess.run(
            ["bash", "-e", "-o", "pipefail", "-c", script],
            cwd=self.repo,
            env={**os.environ, "GITHUB_OUTPUT": str(output), **env},
            text=True,
            capture_output=True,
        )
        values = dict(line.split("=", 1) for line in output.read_text().splitlines())
        return result, values

    def meta(self, sha):
        return self.run_script(
            META["run"], SHA=sha, WD=".",
            IDJSON=json.dumps({"tag": {"prefix": "v"}, "version_file": "version.txt"}),
        )

    def test_workflow_wiring(self):
        self.assertEqual(META["env"]["SHA"], "${{ github.sha }}")
        self.assertEqual(GATE["with"]["after-sha"], "${{ github.sha }}")
        self.assertEqual(
            GATE["with"]["commit-message"], "${{ steps.meta.outputs.commit-subject }}"
        )
        self.assertEqual(TAG["if"], "steps.gate.outputs.should-release == 'true'")

    def test_manual_release_and_dry_run(self):
        sha = self.commit("feat: ship it\n\nBody must not enter the output file.")
        result, meta = self.meta(sha)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(meta["commit-subject"], "feat: ship it")
        result, gate = self.run_script(
            ACTION["runs"]["steps"][0]["run"],
            ACTION_PATH=str(ROOT / "actions/auto-release"),
            RELEASE_PATHS="**.go,go.mod,go.sum,version.txt", TOOL_PATHS="",
            VERSION_FILE=meta["version-file"], TAG_PREFIX=meta["prefix"],
            VERSION=meta["version"], RUN="9", BEFORE="", AFTER=sha,
            COMMIT_MESSAGE=meta["commit-subject"],
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(gate, {"tag": "v1.2.9", "should-release": "true"})
        result, _ = self.run_script(
            TAG["run"], TAG=gate["tag"], DRY_RUN="true", TAG_TOKEN="",
            REPO="example/test", SHA=sha,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"dry-run: would create tag v1.2.9 at {sha}", result.stdout)
        self.assertEqual(self.git("tag", "--list"), "")

    def test_exact_sha_not_latest_head(self):
        sha = self.commit("fix(scope)!: ship the selected commit")
        self.git("-c", "commit.gpgsign=false", "commit", "--allow-empty", "-qm", "docs: newer HEAD")
        result, meta = self.meta(sha)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(meta["commit-subject"], "fix(scope)!: ship the selected commit")

    def test_subjects_still_use_conventional_commit_gate(self):
        for subject, expected in [("docs: update guide", 1), ("not conventional", 2)]:
            with self.subTest(subject=subject):
                self.git("add", "version.txt")
                self.git("-c", "commit.gpgsign=false", "commit", "--allow-empty", "-qm", subject)
                result, meta = self.meta(self.git("rev-parse", "HEAD"))
                self.assertEqual(result.returncode, 0, result.stderr)
                result = subprocess.run(
                    ["bash", str(ROOT / "actions/conventional-commit/check.sh"),
                     "release-gate", meta["commit-subject"]], capture_output=True,
                )
                self.assertEqual(result.returncode, expected)

    def test_shell_characters_remain_data(self):
        subject = 'fix: preserve $(touch injected) `touch injected` "quotes"'
        result, meta = self.meta(self.commit(subject))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(meta["commit-subject"], subject)
        self.assertFalse((self.repo / "injected").exists())

    def test_missing_sha_fails_without_subject_output(self):
        self.commit("feat: initial")
        result, meta = self.meta("0" * 40)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("commit-subject", meta)


if __name__ == "__main__":
    unittest.main()
