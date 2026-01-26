# Open CLI Collective

## Table of Contents

- [Meta](#meta)
  - [What's the Point?](#whats-the-point)
  - [Who We Are](#who-we-are)
- [Where to Find It](#where-to-find-it)
- [Software](#software)
  - [`cfl` - Confluence CLI](#cfl---confluence-cli)
  - [`jtk` - Jira Ticket CLI](#jtk---jira-ticket-cli)
  - [`slck` - Slack CLI](#slck---slack-cli)
  - [`nrq` - New Relic CLI](#nrq---new-relic-cli)
  - [`gro` - Google Workspace Read-Only CLI](#gro---google-workspace-read-only-cli)
  - [`cpm` - Claude Plugin Manager](#cpm---claude-plugin-manager)
  - [`kitty-themes`](#kitty-themes)
- [Conventions](#conventions)
  - [Builds](#builds)

# Meta

## What's the Point?

An open source collective focused on CLI utilities, largely for LLM orchestration—but there's no reason humans can't use them too.

## Who We Are

Anyone can join if they contribute well. That's it.

# Where to Find It

All CLIs are distributed through the same channels:

| Platform | Method |
|----------|--------|
| **macOS** | Homebrew via `open-cli-collective/tap` |
| **Windows** | Chocolatey, Winget |
| **Linux** | APT (Debian/Ubuntu), DNF/YUM (Fedora/RHEL), Snap, Homebrew |
| **All** | Binary downloads from GitHub Releases, `go install` |

# Software

## [`cfl`](https://github.com/open-cli-collective/confluence-cli) - Confluence CLI

A command-line interface for Atlassian Confluence Cloud. Manage pages from the terminal with markdown-first authoring—write and view pages in markdown, auto-converted to/from Confluence format. Search with CQL, manage attachments, and open pages in browser.

## [`jtk`](https://github.com/open-cli-collective/jira-ticket-cli) - Jira Ticket CLI

A command-line interface for managing Jira Cloud tickets. List, create, update, and search issues. Manage sprints and boards, add comments, perform transitions.

## [`slck`](https://github.com/open-cli-collective/slack-chat-api) - Slack CLI

A lightweight CLI for the Slack Web API. Send messages, manage channels, search—designed for automation, CI/CD pipelines, and scripting. Not the official Slack CLI (that's for building Slack apps).

## [`nrq`](https://github.com/open-cli-collective/newrelic-cli) - New Relic CLI

A CLI for New Relic APIs. APM applications, alert policies, dashboards, deployments, entities, log parsing rules, NerdGraph queries, NRQL, synthetic monitors, and user management.

## [`gro`](https://github.com/open-cli-collective/google-readonly) - Google Workspace Read-Only CLI

A read-only CLI for Google services. Gmail, Calendar, Contacts, and Drive access using only readonly OAuth scopes. Search, view, and download without any write capabilities.

## [`cpm`](https://github.com/open-cli-collective/cpm) - Claude Plugin Manager

A terminal UI for managing Claude Code plugins. Two-pane TUI with clear scope indicators (global/project/local), batch operations, search, and keyboard/mouse support.

## [`kitty-themes`](https://github.com/open-cli-collective/kitty-themes)

Theme collection for the kitty terminal emulator. Installable via Homebrew.

# Conventions

## Builds

All Go CLIs use the same build toolchain:

- **Go 1.22+** required
- **Cobra** for command framework
- **golangci-lint** for linting
- **Make** for build orchestration

Standard targets:

```bash
make build   # Build binary
make test    # Run tests
make lint    # Run linter
```
