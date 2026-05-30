# Open CLI Collective Shared GitHub Automation

This repository owns the shared GitHub Actions and reusable workflows used by
Open CLI Collective repositories. It is the automation source of truth; sibling
repositories should link here instead of copying action or workflow mechanics
into local agent guidance.

## Referencing Shared Automation

When another repository or agent entrypoint references shared automation, the
GitHub URL is the source of truth. An adjacent local path may be included as a
convenience for workspaces that keep the Open CLI Collective repos side by side,
but it is only a shortcut. The local path should be correct relative to the file
that contains the reference.

Use this shape for composite actions:

```md
Source of truth: https://github.com/open-cli-collective/.github/tree/main/actions/go-build
Local convenience copy, if present: `../.github/actions/go-build`
```

Use this shape for reusable workflows:

```md
Source of truth: https://github.com/open-cli-collective/.github/blob/main/.github/workflows/auto-release.yml
Local convenience copy, if present: `../.github/.github/workflows/auto-release.yml`
```

## Policy Documents

The repo-axis policy and behavior belong in `cli-common`; this repository
contains the automation that implements those standards.

```md
Source of truth: https://github.com/open-cli-collective/cli-common/blob/main/docs/ci.md
Local convenience copy, if present: `../cli-common/docs/ci.md`

Source of truth: https://github.com/open-cli-collective/cli-common/blob/main/docs/release.md
Local convenience copy, if present: `../cli-common/docs/release.md`

Source of truth: https://github.com/open-cli-collective/cli-common/blob/main/docs/distribution.md
Local convenience copy, if present: `../cli-common/docs/distribution.md`
```

## Contents

- `actions/` - shared composite actions used inside repository-owned CI jobs.
- `.github/workflows/` - reusable workflows for release and auto-release flows.
- `tests/fixtures/` - small fixtures used to test the actions and workflow
  support code.
