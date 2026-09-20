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

- [Privacy Policy](privacy-policy.md)

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

## Release identity manifests

The reusable `auto-release.yml` and `release.yml` workflows read
`packaging/identity.yml` through `actions/identity-check`. A manifest uses
schema `open-cli-identity/v1` and declares exactly one of these shapes:

```yaml
binary: tool
archives: {name_template: "tool_v{{ .Version }}_{{ .Os }}_{{ .Arch }}"}
packages: {}
keychain_probe: {}
```

```yaml
binaries:
  - name: tool
    archives: {name_template: "tool_v{{ .Version }}_{{ .Os }}_{{ .Arch }}"}
    packages: {}
    keychain_probe: {}
  - name: helper
    archives: {name_template: "helper_v{{ .Version }}_{{ .Os }}_{{ .Arch }}"}
    packages: {}
```

`repo`, `goreleaser_config`, `version_file`, and `tag` remain top-level. For a
multi-binary manifest, every GoReleaser build needs an `id`, and each archive,
and nfpm must use `ids` or `builds` to select builds belonging to one binary.
Each Homebrew cask must use `ids` to select archives belonging to one binary.
Multi-binary Chocolatey packages live under
`packaging/chocolatey/<id>/`; the single-binary flat layout is unchanged.

Call either reusable workflow with `manifest-path` and `working-directory`
when those files are not at their defaults. `release.yml` builds once, then
runs each declared Homebrew, Chocolatey, Winget, Linux-package, and Keychain
probe channel per binary.
