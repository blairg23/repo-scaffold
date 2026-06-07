from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

ALLOWED_LANGUAGES = ("go", "gin", "python", "react")
SUPPORTED_LICENSE = "apache-2.0"
TEMPLATE_ROOT = Path(__file__).resolve().parent / "templates"


@dataclass(frozen=True)
class ScaffoldConfig:
    name: str
    languages: tuple[str, ...]
    owner: str | None
    license_id: str
    out_dir: Path


@dataclass(frozen=True)
class ScaffoldFile:
    path: Path
    content: str
    executable: bool = False


def parse_language_csv(raw: str) -> tuple[str, ...]:
    parts = [part.strip().lower() for part in raw.split(",")]
    if not parts or any(not part for part in parts):
        raise ValueError(
            "--languages must be a comma-separated list containing only: go, gin, python, react"
        )

    unknown = [part for part in parts if part not in ALLOWED_LANGUAGES]
    if unknown:
        bad = ", ".join(sorted(set(unknown)))
        raise ValueError(
            f"Unknown language value(s): {bad}. Allowed: go, gin, python, react"
        )

    selected = set(parts)
    return tuple(lang for lang in ALLOWED_LANGUAGES if lang in selected)


def _write_file(path: Path, content: str, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = content.rstrip("\n") + "\n"
    path.write_text(normalized, encoding="utf-8", newline="\n")
    if executable:
        current = path.stat().st_mode
        path.chmod(current | 0o755)


def _ensure_license_supported(license_id: str) -> None:
    if license_id != SUPPORTED_LICENSE:
        raise ValueError(
            f"Unsupported license '{license_id}'. Only '{SUPPORTED_LICENSE}' is supported."
        )


def _load_template(relative_path: str, fallback: str) -> str:
    template_path = TEMPLATE_ROOT / relative_path
    if template_path.exists():
        return template_path.read_text(encoding="utf-8")
    return fallback


def _render_codeowners(owner: str | None) -> str:
    if owner:
        handle = owner if owner.startswith("@") else f"@{owner}"
        return f"* {handle}\n"
    return "# Replace @TODO-owner with a real reviewer\n* @TODO-owner\n"


def _render_pr_template() -> str:
    fallback = """## Summary

-

## Motivation

-

## Changes

-

## Validation

- [ ] Local tests pass
- [ ] CI checks are green
- [ ] Docs updated if behavior changed

## Risks

-

## Checklist

- [ ] Linked issue/epic
- [ ] Added tests (or documented why not)
- [ ] No secrets or credentials committed
"""
    return _load_template("github/pull_request_template.md", fallback)


def _render_issue_epic_template() -> str:
    fallback = """---
name: Epic
about: Track a multi-ticket initiative
title: "[EPIC] "
labels: ["epic", "needs-triage"]
assignees: []
---

## Problem statement

## Goals

## Non-goals

## Scope

## Milestones

- [ ] Milestone 1
- [ ] Milestone 2

## Success metrics

## Risks / unknowns

## Linked tickets
"""
    return _load_template("github/ISSUE_TEMPLATE/epic.md", fallback)


def _render_issue_ticket_template() -> str:
    fallback = """---
name: Ticket
about: Describe one implementation task
title: "[Ticket] "
labels: ["needs-triage"]
assignees: []
---

## Summary

## Acceptance criteria

- [ ]

## Implementation notes

## Test plan

## Definition of done

- [ ] Tests added/updated
- [ ] Documentation updated
"""
    return _load_template("github/ISSUE_TEMPLATE/ticket.md", fallback)


def _render_issue_config(owner: str | None, name: str) -> str:
    if owner:
        security_url = f"https://github.com/{owner}/{name}/security/advisories/new"
    else:
        security_url = "https://github.com/OWNER/REPO/security/advisories/new"
    return f"""blank_issues_enabled: false
contact_links:
  - name: Security report
    url: {security_url}
    about: Please privately disclose security issues through GitHub Security Advisories.
"""


def _render_ci_yaml(languages: Iterable[str]) -> str:
    joined = ",".join(languages)
    return f"""name: CI

on: [push, pull_request]

permissions:
  contents: read

env:
  LANGUAGES: "{joined}"

jobs:
  pre-commit-hooks:
    if: hashFiles('.pre-commit-config.yaml') != ''
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install pre-commit
        run: |
          python -m pip install --upgrade pip
          python -m pip install pre-commit
      - name: Run non-tox pre-commit hooks
        env:
          SKIP: tox-suite
        run: pre-commit run --all-files --show-diff-on-failure

  go:
    if: contains(env.LANGUAGES, 'go') && hashFiles('go.mod') != ''
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-go@v5
        with:
          go-version-file: go.mod
      - name: Check gofmt
        run: |
          unformatted="$(gofmt -l .)"
          if [ -n "$unformatted" ]; then
            echo "Files not formatted with gofmt:"
            echo "$unformatted"
            exit 1
          fi
      - name: Run tests
        run: go test ./...
      - name: Run golangci-lint
        uses: golangci/golangci-lint-action@v6
        with:
          version: v1.61

  gin:
    if: contains(env.LANGUAGES, 'gin') && hashFiles('go.mod') != ''
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-go@v5
        with:
          go-version-file: go.mod
      - name: Download dependencies
        run: go mod tidy
      - name: Check gofmt
        run: |
          unformatted="$(gofmt -l .)"
          if [ -n "$unformatted" ]; then
            echo "Files not formatted with gofmt:"
            echo "$unformatted"
            exit 1
          fi
      - name: Run tests
        run: go test ./...
      - name: Run golangci-lint
        uses: golangci/golangci-lint-action@v6
        with:
          version: v1.61

  python:
    if: contains(env.LANGUAGES, 'python') && hashFiles('pyproject.toml') != ''
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        tox-env: [lint, type, coverage]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install tox
        run: |
          python -m pip install --upgrade pip
          python -m pip install tox
      - name: Run tox (${{{{ matrix.tox-env }}}})
        run: tox -e ${{{{ matrix.tox-env }}}}
      - name: Upload coverage.xml artifact
        if: matrix.tox-env == 'coverage'
        uses: actions/upload-artifact@v4
        with:
          name: coverage-xml
          path: coverage.xml
          if-no-files-found: error
      - name: Upload coverage to Codecov
        if: matrix.tox-env == 'coverage'
        uses: codecov/codecov-action@v5
        with:
          files: coverage.xml
          fail_ci_if_error: false
        env:
          CODECOV_TOKEN: ${{{{ secrets.CODECOV_TOKEN }}}}

  react:
    if: contains(env.LANGUAGES, 'react') && hashFiles('web/package.json') != ''
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: web
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: lts/*
          cache: npm
          cache-dependency-path: web/package-lock.json
      - name: Ensure lockfile exists
        run: |
          if [ ! -f package-lock.json ]; then
            npm install --package-lock-only
          fi
      - name: Install dependencies
        run: npm ci
      - name: Lint
        run: npm run lint --if-present
      - name: Build
        run: npm run build
      - name: Test (optional)
        run: |
          if npm run | grep -qE '^[[:space:]]+test'; then
            npm test -- --watch=false
          else
            echo "No test script defined; skipping tests."
          fi
"""


def _render_codeql_yaml(languages: Iterable[str]) -> str:
    codeql_langs = [
        (
            "go"
            if lang == "gin"
            else ("javascript-typescript" if lang == "react" else lang)
        )
        for lang in languages
        if lang in {"go", "gin", "python", "react"}
    ]
    codeql_langs = list(dict.fromkeys(codeql_langs))

    if not codeql_langs:
        return """name: CodeQL

on:
  schedule:
    - cron: "0 6 * * 1"
  workflow_dispatch:

jobs:
  noop:
    runs-on: ubuntu-latest
    steps:
      - name: Skip
        run: echo "No supported CodeQL languages selected; scan is skipped."
"""

    matrix_lines = "\n".join(f"          - {lang}" for lang in codeql_langs)
    return f"""name: CodeQL

on:
  pull_request:
  push:
    branches: [main]
  schedule:
    - cron: "0 6 * * 1"

permissions:
  actions: read
  contents: read
  security-events: write

jobs:
  analyze:
    name: Analyze (${{{{ matrix.language }}}})
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        language:
{matrix_lines}
    steps:
      - uses: actions/checkout@v4
      - uses: github/codeql-action/init@v3
        with:
          languages: ${{{{ matrix.language }}}}
      - uses: github/codeql-action/autobuild@v3
      - uses: github/codeql-action/analyze@v3
"""


def _render_dependabot_yaml(languages: Iterable[str]) -> str:
    entries: list[str] = []

    def block(ecosystem: str, directory: str, group_name: str) -> str:
        return f"""  - package-ecosystem: \"{ecosystem}\"
    directory: \"{directory}\"
    schedule:
      interval: weekly
    open-pull-requests-limit: 5
    groups:
      {group_name}:
        patterns:
          - \"*\"
"""

    entries.append(block("github-actions", "/", "github-actions"))

    selected = set(languages)
    if "react" in selected:
        entries.append(block("npm", "/web", "npm"))
    if "go" in selected or "gin" in selected:
        entries.append(block("gomod", "/", "gomod"))
    if "python" in selected:
        entries.append(block("pip", "/", "pip"))
        entries.append(block("uv", "/", "uv"))

    return "version: 2\nupdates:\n" + "".join(entries)


def _render_gitignore(languages: Iterable[str]) -> str:
    selected = set(languages)
    lines = [
        "# OS",
        ".DS_Store",
        "Thumbs.db",
        "",
        "# Editors / IDE",
        ".idea/",
        ".vscode/",
        "*.swp",
        "*.swo",
        "",
        "# Environment",
        ".env",
        ".env.*",
        "!.env.example",
        ".claude/settings.local.json",
        ".claude/settings.json",
        "",
        "# Repo-scaffold local metadata",
        ".repo-scaffold/",
        "",
        "# Local generated artifacts",
        "artifacts/",
        "",
        "# Logs",
        "*.log",
        "",
        "# Pre-commit",
        ".pre-commit-cache/",
        "",
    ]

    if "go" in selected or "gin" in selected:
        lines.extend(
            [
                "# Go",
                "bin/",
                "*.test",
                "coverage.out",
                "",
            ]
        )

    if "python" in selected:
        lines.extend(
            [
                "# Python",
                "__pycache__/",
                "*.py[cod]",
                ".pytest_cache/",
                ".ruff_cache/",
                ".mypy_cache/",
                ".tox/",
                ".coverage",
                ".coverage.*",
                "coverage.xml",
                "htmlcov/",
                ".venv/",
                "venv/",
                "dist/",
                "build/",
                "*.egg-info/",
                "",
            ]
        )

    if "react" in selected:
        lines.extend(
            [
                "# React / Node",
                "web/node_modules/",
                "web/dist/",
                "web/.vite/",
                "",
            ]
        )

    return "\n".join(lines).rstrip() + "\n"


def _render_editorconfig() -> str:
    return """root = true

[*]
charset = utf-8
end_of_line = lf
indent_style = space
indent_size = 2
insert_final_newline = true
trim_trailing_whitespace = true

[Makefile]
indent_style = tab
"""


def _render_makefile() -> str:
    return """.PHONY: format lint typecheck test build

format:
\t@if [ -f go.mod ]; then \\
\t\techo "Formatting Go"; \\
\t\tgofmt -w .; \\
\tfi
\t@if [ -f pyproject.toml ]; then \\
\t\techo "Formatting Python"; \\
\t\truff check --fix .; \\
\t\tblack .; \\
\tfi
\t@if [ -f web/package.json ]; then \\
\t\techo "Formatting React"; \\
\t\tcd web && npm run format --if-present; \\
\tfi

lint:
\t@if [ -f go.mod ]; then \\
\t\techo "Linting Go"; \\
\t\tunformatted="$(gofmt -l .)"; \\
\t\tif [ -n "$$unformatted" ]; then echo "$$unformatted"; exit 1; fi; \\
\t\tif command -v golangci-lint >/dev/null 2>&1; then golangci-lint run ./...; else echo "golangci-lint not installed; skipping"; fi; \\
\tfi
\t@if [ -f pyproject.toml ]; then \\
\t\techo "Linting Python"; \\
\t\truff check .; \\
\t\tblack --check .; \\
\tfi
\t@if [ -f web/package.json ]; then \\
\t\techo "Linting React"; \\
\t\tcd web && npm run lint --if-present; \\
\tfi

typecheck:
\t@if [ -f go.mod ]; then go vet ./...; fi
\t@if [ -f pyproject.toml ]; then mypy src; fi
\t@if [ -f web/package.json ]; then cd web && npm run typecheck --if-present; fi

test:
\t@if [ -f go.mod ]; then go test ./...; fi
\t@if [ -f pyproject.toml ]; then pytest; fi
\t@if [ -f web/package.json ]; then cd web && npm test --if-present; fi

build:
\t@if [ -f go.mod ]; then go build ./...; fi
\t@if [ -f pyproject.toml ]; then python -m build; fi
\t@if [ -f web/package.json ]; then cd web && npm run build; fi
"""


def _render_backlog_issues_json() -> str:
    # Backlog starts empty by default. Users can add project-specific epics/tickets later.
    return json.dumps({"epics": []}, indent=2, ensure_ascii=False)


def _render_repo_readme(config: ScaffoldConfig) -> str:
    badge_owner = config.owner or "OWNER"
    coverage_badge = (
        f"[![codecov](https://codecov.io/gh/{badge_owner}/{config.name}/graph/badge.svg)]"
        f"(https://codecov.io/gh/{badge_owner}/{config.name})"
    )
    lines: list[str] = [
        f"# {config.name}",
        "",
        coverage_badge,
        "",
        "Created by [repo-scaffold](https://github.com/your-org/repo-scaffold).",
        "",
        "## Enabled languages",
        "",
    ]
    lines.extend(f"- {lang}" for lang in config.languages)
    lines.extend(["", "## Setup", ""])

    if "python" in config.languages:
        lines.extend(
            [
                "### Python",
                "",
                "```bash",
                "python -m venv .venv",
                "source .venv/bin/activate",
                "pip install -e .[dev]",
                "```",
                "",
            ]
        )

    if "go" in config.languages:
        lines.extend(
            [
                "### Go",
                "",
                "```bash",
                "go mod tidy",
                "```",
                "",
            ]
        )

    if "gin" in config.languages:
        lines.extend(
            [
                "### Gin",
                "",
                "```bash",
                "go mod tidy",
                "```",
                "",
            ]
        )

    if "react" in config.languages:
        lines.extend(
            [
                "### React",
                "",
                "```bash",
                "cd web",
                "npm ci",
                "```",
                "",
            ]
        )

    lines.extend(
        [
            "## Git hooks",
            "",
            "```bash",
            "python -m pip install pre-commit",
            "pre-commit install",
            "pre-commit run --all-files",
            "```",
            "",
            "For parity with CI quality gates, pre-commit also runs `tox -e precommit`.",
            "If formatters or fixers change tracked files, the hook exits non-zero so you can re-stage and rerun the commit intentionally.",
            "The fast pre-commit gate also enforces the Python coverage threshold when Python is enabled.",
            "",
        ]
    )

    lines.extend(["## Day-to-day commands", ""])

    if "python" in config.languages:
        lines.extend(
            [
                "### Python",
                "",
                "```bash",
                "tox -e format",
                "ruff check .",
                "black --check .",
                "mypy src",
                "pytest",
                "tox -e lint,type,coverage",
                "tox -e coverage",
                "tox -e precommit",
                "tox -e codecov-upload",
                "export CODECOV_TOKEN=your_codecov_token",
                "tox -e codecov-upload",
                "```",
                "",
                "CI runs the same Python quality matrix via tox (`lint`, `type`, `coverage`).",
                "Run `tox -e coverage` to generate `coverage.xml` and `htmlcov/` locally.",
                "The current minimum coverage gate is 70%.",
                "If `CODECOV_TOKEN` is already present in `.env`, you can run `tox -e codecov-upload` directly.",
                "If you prefer an explicit shell export, set `CODECOV_TOKEN` and then run `tox -e codecov-upload`.",
                "",
            ]
        )

    if "go" in config.languages:
        lines.extend(
            [
                "### Go",
                "",
                "```bash",
                "gofmt -w .",
                "golangci-lint run ./...",
                "go test ./...",
                "```",
                "",
            ]
        )

    if "gin" in config.languages:
        lines.extend(
            [
                "### Gin",
                "",
                "```bash",
                "go run ./cmd/...",
                "go test ./...",
                "gofmt -w .",
                "golangci-lint run ./...",
                "```",
                "",
                "Server starts on `:8080`. Hit `GET /health` to verify.",
                "",
            ]
        )

    if "react" in config.languages:
        lines.extend(
            [
                "### React",
                "",
                "```bash",
                "cd web",
                "npm run lint --if-present",
                "npm test --if-present",
                "npm run build",
                "```",
                "",
            ]
        )

    lines.extend(
        [
            "## Convenience targets",
            "",
            "Optional wrappers are provided in `Makefile`: `make format`, `make lint`, `make typecheck`, `make test`, `make build`.",
            "",
            "## Repo-scaffold GitHub workflow",
            "",
            "- Keep repo planning markdown in `artifacts/tickets/`.",
            "- The canonical repo project metadata file is `.repo-scaffold/project.json` once a project has been created or synced.",
            "- `AGENTS.md` tells local agents to treat `GH_REPO` and `.repo-scaffold/project.json` as the repo-local GitHub context.",
            "- Prefer `gh auth login` or an OS-backed credential manager for local GitHub auth; use `.env` only when you intentionally want token-based local scripting.",
            "- Run `./scripts/first_time_setup.sh` once to wire the local GitHub Projects v2 token, Claude Code settings, and the `ghp` shell alias for WSL workflows.",
            "",
            "### GitHub Projects v2 auth for WSL / Claude Code",
            "",
            "```bash",
            "./scripts/first_time_setup.sh",
            "source ~/.bashrc  # or ~/.zshrc",
            "ghp project list --owner YOUR_OWNER",
            "GH_TOKEN=<classic-PAT> gh project item-list <PROJECT_NUMBER> --owner YOUR_OWNER",
            "```",
            "",
            "- `.env.example` includes `export GH_PROJECT_TOKEN=<classic-PAT>` because child processes need the export prefix.",
            "- `.claude/settings.local.json` is local-only and gives Claude Code the same project token context.",
            "- For direct `gh project ...` calls in WSL/Claude, prefer `ghp ...` or `GH_TOKEN=<classic-PAT> gh ...`.",
            "- Do not rely on `GH_TOKEN=$GH_PROJECT_TOKEN gh ...` for project board commands in this environment.",
            "",
            "## GitHub templates included",
            "",
            "- `.github/ISSUE_TEMPLATE/epic.md`",
            "- `.github/ISSUE_TEMPLATE/ticket.md`",
            "- `.github/pull_request_template.md`",
        ]
    )
    return "\n".join(lines) + "\n"


def _render_env_example(name: str) -> str:
    return f"""# Copy this file to .env and set real values.
# Do not commit .env.

# GitHub token used by scripts/backlog/apply/create flows.
# Recommended classic PAT scopes: repo, workflow, read:org.
# Optional scopes: delete_repo (cleanup), project (Projects automation).
GH_TOKEN=ghp_replace_with_real_token

# Optional metadata for local tooling/docs.
GITHUB_ORG=YOUR_ORG
GITHUB_REPO={name}
# Alternative single value instead of GITHUB_ORG + GITHUB_REPO:
# GH_REPO=YOUR_ORG/{name}

# Optional canonical project title for this repo's roadmap:
# GITHUB_PROJECT_TITLE={name} Roadmap

# Optional backlog project naming defaults (used with --with-project):
# GITHUB_PROJECT_TITLE=YOUR_FIXED_PROJECT_TITLE
# GITHUB_PROJECT_TITLE_TEMPLATE={{repo}} Roadmap

# Optional markdown backlog source override:
# GITHUB_TICKETS_DIR=artifacts/tickets

# Classic PAT for GitHub Projects v2 commands run from WSL / Claude Code.
# Keep the export prefix so child processes inherit it.
export GH_PROJECT_TOKEN=<classic-PAT>

# Optional local Codecov upload token (read automatically by `tox -e codecov-upload`):
# CODECOV_TOKEN=your_codecov_token

# Legacy lowercase aliases are still supported:
# github_token=...
# github_org=...
# github_repo=...
# github_full_repo=...
# github_project_title=...
# github_project_title_template=...
# github_tickets_dir=...
"""


def _render_claude_settings_local() -> str:
    return """{
  "env": {
    "GH_PROJECT_TOKEN": "<classic-PAT>"
  }
}
"""


def _render_agents_md(config: ScaffoldConfig) -> str:
    return f"""# AGENTS

Repo-scaffold conventions for local agents:

- Treat `GH_REPO` (or `GITHUB_ORG` + `GITHUB_REPO`) as the canonical GitHub repo identity for this workspace.
- Do not mutate other repositories unless the user explicitly asks.
- If `.repo-scaffold/project.json` exists, it is the canonical GitHub Project metadata for this repo. Read it before doing project or ticket work.
- Prefer repo issues and the repo-linked roadmap project for planning context.
- Planning markdown lives in `artifacts/tickets/`.
- Imported backlog JSON lives in `artifacts/backlog/issues.json`.
- For local GitHub auth, prefer `gh auth login` or an OS credential manager. Use `.env` tokens only when the user intentionally wants repo-local token-based scripting.
- For GitHub Projects v2 commands in WSL / Claude Code, prefer the `ghp` shell alias or `GH_TOKEN=<classic-PAT> gh ...`.
- Do not rely on `GH_TOKEN=$GH_PROJECT_TOKEN gh ...` for project board commands in this environment.
- `.claude/settings.local.json` is local-only and should carry `GH_PROJECT_TOKEN` for Claude Code sessions.

Expected default project title:

- `{config.name} Roadmap`
"""


def _render_first_time_setup_script() -> str:
    return """#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_EXAMPLE="$REPO_ROOT/.env.example"
ENV_FILE="$REPO_ROOT/.env"
CLAUDE_DIR="$REPO_ROOT/.claude"
CLAUDE_SETTINGS_FILE="$CLAUDE_DIR/settings.local.json"
PAT_PLACEHOLDER="<classic-PAT>"

pick_shell_rc() {
  local shell_name
  shell_name="$(basename "${SHELL:-bash}")"
  case "$shell_name" in
    zsh) printf '%s\n' "$HOME/.zshrc" ;;
    *) printf '%s\n' "$HOME/.bashrc" ;;
  esac
}

upsert_env_line() {
  local file="$1"
  local pattern="$2"
  local replacement="$3"
  local tmp

  tmp="$(mktemp)"
  if [ -f "$file" ]; then
    grep -v -E "$pattern" "$file" > "$tmp" || true
  fi
  printf '%s\n' "$replacement" >> "$tmp"
  mv "$tmp" "$file"
}

echo "Repo-scaffold first-time GitHub Projects setup"
echo
echo "This script will:"
echo "  1) ensure .env exists"
echo "  2) set export GH_PROJECT_TOKEN=..."
echo "  3) set GH_TOKEN=... for repo-scaffold compatibility"
echo "  4) write .claude/settings.local.json"
echo "  5) optionally append a ghp alias to your shell rc"
echo

if [ ! -f "$ENV_FILE" ]; then
  if [ -f "$ENV_EXAMPLE" ]; then
    cp "$ENV_EXAMPLE" "$ENV_FILE"
    echo "Created $ENV_FILE from .env.example"
  else
    : > "$ENV_FILE"
    echo "Created empty $ENV_FILE"
  fi
fi

read -r -p "Classic PAT for GitHub Projects v2 (leave blank to keep placeholder): " PROJECT_TOKEN
if [ -z "$PROJECT_TOKEN" ]; then
  PROJECT_TOKEN="$PAT_PLACEHOLDER"
fi

read -r -p "Repo-scaffold GH_TOKEN (leave blank to reuse the project token): " REPO_TOKEN
if [ -z "$REPO_TOKEN" ]; then
  REPO_TOKEN="$PROJECT_TOKEN"
fi

upsert_env_line "$ENV_FILE" '^(export[[:space:]]+)?GH_PROJECT_TOKEN=' "export GH_PROJECT_TOKEN=$PROJECT_TOKEN"
upsert_env_line "$ENV_FILE" '^GH_TOKEN=' "GH_TOKEN=$REPO_TOKEN"
echo "Updated $ENV_FILE"

mkdir -p "$CLAUDE_DIR"
cat > "$CLAUDE_SETTINGS_FILE" <<EOF
{
  "env": {
    "GH_PROJECT_TOKEN": "$PROJECT_TOKEN"
  }
}
EOF
echo "Wrote $CLAUDE_SETTINGS_FILE"

RC_FILE="$(pick_shell_rc)"
ALIAS_LINE="alias ghp='GH_TOKEN=$PROJECT_TOKEN gh'"
read -r -p "Append ghp alias to $RC_FILE? [y/N] " APPEND_ALIAS
case "$APPEND_ALIAS" in
  [yY]|[yY][eE][sS])
    touch "$RC_FILE"
    if ! grep -Fqx "$ALIAS_LINE" "$RC_FILE"; then
      printf '\n%s\n' "$ALIAS_LINE" >> "$RC_FILE"
      echo "Appended ghp alias to $RC_FILE"
    else
      echo "ghp alias already present in $RC_FILE"
    fi
    ;;
  *)
    echo "Skipped shell alias update."
    ;;
esac

echo
echo "Next steps:"
echo "  source $RC_FILE"
echo "  ghp project list --owner YOUR_OWNER"
echo "  GH_TOKEN=$PROJECT_TOKEN gh project item-list <PROJECT_NUMBER> --owner YOUR_OWNER"
echo
echo "For project board commands in WSL / Claude Code, use ghp or direct GH_TOKEN=... gh ... commands."
echo "Do not rely on GH_TOKEN=\\$GH_PROJECT_TOKEN gh ... in this environment."
"""


def _apache_2_license() -> str:
    return """Apache License
                           Version 2.0, January 2004
                        http://www.apache.org/licenses/

   TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION

   1. Definitions.

      \"License\" shall mean the terms and conditions for use, reproduction,
      and distribution as defined by Sections 1 through 9 of this document.

      \"Licensor\" shall mean the copyright owner or entity authorized by
      the copyright owner that is granting the License.

      \"Legal Entity\" shall mean the union of the acting entity and all
      other entities that control, are controlled by, or are under common
      control with that entity. For the purposes of this definition,
      \"control\" means (i) the power, direct or indirect, to cause the
      direction or management of such entity, whether by contract or
      otherwise, or (ii) ownership of fifty percent (50%) or more of the
      outstanding shares, or (iii) beneficial ownership of such entity.

      \"You\" (or \"Your\") shall mean an individual or Legal Entity
      exercising permissions granted by this License.

      \"Source\" form shall mean the preferred form for making modifications,
      including but not limited to software source code, documentation
      source, and configuration files.

      \"Object\" form shall mean any form resulting from mechanical
      transformation or translation of a Source form, including but
      not limited to compiled object code, generated documentation,
      and conversions to other media types.

      \"Work\" shall mean the work of authorship, whether in Source or
      Object form, made available under the License, as indicated by a
      copyright notice that is included in or attached to the work
      (an example is provided in the Appendix below).

      \"Derivative Works\" shall mean any work, whether in Source or Object
      form, that is based on (or derived from) the Work and for which the
      editorial revisions, annotations, elaborations, or other modifications
      represent, as a whole, an original work of authorship. For the purposes
      of this License, Derivative Works shall not include works that remain
      separable from, or merely link (or bind by name) to the interfaces of,
      the Work and Derivative Works thereof.

      \"Contribution\" shall mean any work of authorship, including
      the original version of the Work and any modifications or additions
      to that Work or Derivative Works thereof, that is intentionally
      submitted to Licensor for inclusion in the Work by the copyright owner
      or by an individual or Legal Entity authorized to submit on behalf of
      the copyright owner. For the purposes of this definition, \"submitted\"
      means any form of electronic, verbal, or written communication sent
      to the Licensor or its representatives, including but not limited to
      communication on electronic mailing lists, source code control systems,
      and issue tracking systems that are managed by, or on behalf of, the
      Licensor for the purpose of discussing and improving the Work, but
      excluding communication that is conspicuously marked or otherwise
      designated in writing by the copyright owner as \"Not a Contribution.\"

      \"Contributor\" shall mean Licensor and any individual or Legal Entity
      on behalf of whom a Contribution has been received by Licensor and
      subsequently incorporated within the Work.

   2. Grant of Copyright License. Subject to the terms and conditions of
      this License, each Contributor hereby grants to You a perpetual,
      worldwide, non-exclusive, no-charge, royalty-free, irrevocable
      copyright license to reproduce, prepare Derivative Works of,
      publicly display, publicly perform, sublicense, and distribute the
      Work and such Derivative Works in Source or Object form.

   3. Grant of Patent License. Subject to the terms and conditions of
      this License, each Contributor hereby grants to You a perpetual,
      worldwide, non-exclusive, no-charge, royalty-free, irrevocable
      (except as stated in this section) patent license to make, have made,
      use, offer to sell, sell, import, and otherwise transfer the Work,
      where such license applies only to those patent claims licensable
      by such Contributor that are necessarily infringed by their
      Contribution(s) alone or by combination of their Contribution(s)
      with the Work to which such Contribution(s) was submitted. If You
      institute patent litigation against any entity (including a
      cross-claim or counterclaim in a lawsuit) alleging that the Work
      or a Contribution incorporated within the Work constitutes direct
      or contributory patent infringement, then any patent licenses
      granted to You under this License for that Work shall terminate
      as of the date such litigation is filed.

   4. Redistribution. You may reproduce and distribute copies of the
      Work or Derivative Works thereof in any medium, with or without
      modifications, and in Source or Object form, provided that You
      meet the following conditions:

      (a) You must give any other recipients of the Work or
          Derivative Works a copy of this License; and

      (b) You must cause any modified files to carry prominent notices
          stating that You changed the files; and

      (c) You must retain, in the Source form of any Derivative Works
          that You distribute, all copyright, patent, trademark, and
          attribution notices from the Source form of the Work,
          excluding those notices that do not pertain to any part of
          the Derivative Works; and

      (d) If the Work includes a \"NOTICE\" text file as part of its
          distribution, then any Derivative Works that You distribute must
          include a readable copy of the attribution notices contained
          within such NOTICE file, excluding those notices that do not
          pertain to any part of the Derivative Works, in at least one
          of the following places: within a NOTICE text file distributed
          as part of the Derivative Works; within the Source form or
          documentation, if provided along with the Derivative Works; or,
          within a display generated by the Derivative Works, if and
          wherever such third-party notices normally appear. The contents
          of the NOTICE file are for informational purposes only and
          do not modify the License. You may add Your own attribution
          notices within Derivative Works that You distribute, alongside
          or as an addendum to the NOTICE text from the Work, provided
          that such additional attribution notices cannot be construed
          as modifying the License.

      You may add Your own copyright statement to Your modifications and
      may provide additional or different license terms and conditions
      for use, reproduction, or distribution of Your modifications, or
      for any such Derivative Works as a whole, provided Your use,
      reproduction, and distribution of the Work otherwise complies with
      the conditions stated in this License.

   5. Submission of Contributions. Unless You explicitly state otherwise,
      any Contribution intentionally submitted for inclusion in the Work
      by You to the Licensor shall be under the terms and conditions of
      this License, without any additional terms or conditions.
      Notwithstanding the above, nothing herein shall supersede or modify
      the terms of any separate license agreement you may have executed
      with Licensor regarding such Contributions.

   6. Trademarks. This License does not grant permission to use the trade
      names, trademarks, service marks, or product names of the Licensor,
      except as required for reasonable and customary use in describing the
      origin of the Work and reproducing the content of the NOTICE file.

   7. Disclaimer of Warranty. Unless required by applicable law or
      agreed to in writing, Licensor provides the Work (and each
      Contributor provides its Contributions) on an \"AS IS\" BASIS,
      WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
      implied, including, without limitation, any warranties or conditions
      of TITLE, NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A
      PARTICULAR PURPOSE. You are solely responsible for determining the
      appropriateness of using or redistributing the Work and assume any
      risks associated with Your exercise of permissions under this License.

   8. Limitation of Liability. In no event and under no legal theory,
      whether in tort (including negligence), contract, or otherwise,
      unless required by applicable law (such as deliberate and grossly
      negligent acts) or agreed to in writing, shall any Contributor be
      liable to You for damages, including any direct, indirect, special,
      incidental, or consequential damages of any character arising as a
      result of this License or out of the use or inability to use the
      Work (including but not limited to damages for loss of goodwill,
      work stoppage, computer failure or malfunction, or any and all
      other commercial damages or losses), even if such Contributor
      has been advised of the possibility of such damages.

   9. Accepting Warranty or Additional Liability. While redistributing
      the Work or Derivative Works thereof, You may choose to offer,
      and charge a fee for, acceptance of support, warranty, indemnity,
      or other liability obligations and/or rights consistent with this
      License. However, in accepting such obligations, You may act only
      on Your own behalf and on Your sole responsibility, not on behalf
      of any other Contributor, and only if You agree to indemnify,
      defend, and hold each Contributor harmless for any liability
      incurred by, or claims asserted against, such Contributor by reason
      of your accepting any such warranty or additional liability.

   END OF TERMS AND CONDITIONS
"""


def _render_gh_apply_settings_script() -> str:
    return """#!/usr/bin/env bash
set -euo pipefail

OWNER="${1:-}"
REPO="${2:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$REPO_ROOT/.env"

usage() {
  cat <<'EOF'
Usage: ./scripts/gh-apply-settings.sh [owner] [repo]

Values resolve in this order:
  1) positional args
  2) .env / exported env:
     - GITHUB_ORG / github_org
     - GITHUB_REPO / github_repo
     - GH_REPO / GITHUB_REPOSITORY / github_full_repo
EOF
}

if ! command -v gh >/dev/null 2>&1; then
  echo "GitHub CLI (gh) is required." >&2
  exit 1
fi

load_env_from_file() {
  local env_file="$1"
  local line key value

  if [ ! -f "$env_file" ]; then
    return 0
  fi

  while IFS= read -r line || [ -n "$line" ]; do
    line="${line#"${line%%[![:space:]]*}"}"
    [ -z "$line" ] && continue
    case "$line" in
      #*) continue ;;
      export[[:space:]]*) line="${line#export }" ;;
    esac
    if [[ "$line" != *=* ]]; then
      continue
    fi

    key="${line%%=*}"
    value="${line#*=}"
    key="$(printf '%s' "$key" | tr -d '[:space:]')"
    value="${value%$'\\r'}"

    if [ "${value#\\"}" != "$value" ] && [ "${value%\\"}" != "$value" ]; then
      value="${value#\\"}"
      value="${value%\\"}"
    elif [ "${value#\\'}" != "$value" ] && [ "${value%\\'}" != "$value" ]; then
      value="${value#\\'}"
      value="${value%\\'}"
    fi

    case "$key" in
      github_token|GH_TOKEN|GITHUB_TOKEN)
        export GH_TOKEN="$value"
        ;;
      github_org|GITHUB_ORG)
        export GITHUB_ORG="$value"
        ;;
      github_repo|GITHUB_REPO)
        export GITHUB_REPO="$value"
        ;;
      github_full_repo|GH_REPO|GITHUB_REPOSITORY)
        export GH_REPO="$value"
        ;;
    esac
  done < "$env_file"
}

resolve_repo_ref() {
  local full_repo
  full_repo="${GH_REPO:-${GITHUB_REPOSITORY:-}}"

  if [ -z "$OWNER" ] && [ -n "${GITHUB_ORG:-}" ]; then
    OWNER="$GITHUB_ORG"
  fi
  if [ -z "$REPO" ] && [ -n "${GITHUB_REPO:-}" ]; then
    REPO="$GITHUB_REPO"
  fi

  if { [ -z "$OWNER" ] || [ -z "$REPO" ]; } && [ -n "$full_repo" ] && [[ "$full_repo" == */* ]]; then
    if [ -z "$OWNER" ]; then
      OWNER="${full_repo%%/*}"
    fi
    if [ -z "$REPO" ]; then
      REPO="${full_repo##*/}"
    fi
  fi

  if [ -z "$OWNER" ] || [ -z "$REPO" ]; then
    usage >&2
    exit 1
  fi
}

ensure_gh_auth() {
  if [ -z "${GH_TOKEN:-}" ] && [ -n "${GITHUB_TOKEN:-}" ]; then
    export GH_TOKEN="$GITHUB_TOKEN"
  fi

  if [ -n "${GH_TOKEN:-}" ]; then
    return 0
  fi

  if gh auth status >/dev/null 2>&1; then
    return 0
  fi

  echo "Authenticate first: gh auth login" >&2
  echo "Or set GH_TOKEN/GITHUB_TOKEN (legacy .env alias: github_token)." >&2
  exit 1
}

load_env_from_file "$ENV_FILE"
resolve_repo_ref
ensure_gh_auth

echo "Applying repository-level merge settings..."
gh api \
  --method PATCH \
  -H "Accept: application/vnd.github+json" \
  "/repos/$OWNER/$REPO" \
  --input - >/dev/null <<'JSON'
{
  "allow_squash_merge": true,
  "squash_merge_commit_title": "PR_TITLE",
  "squash_merge_commit_message": "PR_BODY",
  "allow_merge_commit": false,
  "allow_rebase_merge": false,
  "delete_branch_on_merge": true,
  "allow_auto_merge": false,
  "is_template": false
}
JSON

DEFAULT_BRANCH="$(gh api "/repos/$OWNER/$REPO" --jq .default_branch)"

echo "Removing legacy branch protection if present..."
gh api \
  --method DELETE \
  -H "Accept: application/vnd.github+json" \
  "/repos/$OWNER/$REPO/branches/$DEFAULT_BRANCH/protection" >/dev/null 2>&1 || true

RULESET_NAME="repo-scaffold default-branch ruleset"
RULESET_ID="$(gh api "/repos/$OWNER/$REPO/rulesets?includes_parents=false&targets=branch" --jq ".[] | select(.name == \\\"$RULESET_NAME\\\") | .id" 2>/dev/null | head -n 1)"

echo "Syncing managed default-branch ruleset..."
if [ -n "$RULESET_ID" ]; then
  RULESET_METHOD="PUT"
  RULESET_ENDPOINT="/repos/$OWNER/$REPO/rulesets/$RULESET_ID"
else
  RULESET_METHOD="POST"
  RULESET_ENDPOINT="/repos/$OWNER/$REPO/rulesets"
fi

gh api \
  --method "$RULESET_METHOD" \
  -H "Accept: application/vnd.github+json" \
  "$RULESET_ENDPOINT" \
  --input - >/dev/null <<'JSON'
{
  "name": "repo-scaffold default-branch ruleset",
  "target": "branch",
  "enforcement": "active",
  "conditions": {
    "ref_name": {
      "include": ["~DEFAULT_BRANCH"],
      "exclude": []
    }
  },
  "rules": [
    {"type": "deletion"},
    {"type": "non_fast_forward"},
    {"type": "required_linear_history"},
    {
      "type": "pull_request",
      "parameters": {
        "allowed_merge_methods": ["squash"],
        "dismiss_stale_reviews_on_push": false,
        "require_code_owner_review": false,
        "require_last_push_approval": false,
        "required_approving_review_count": 0,
        "required_review_thread_resolution": true
      }
    }
  ]
}
JSON

enable_optional_feature() {
  local label="$1"
  local endpoint="$2"

  if gh api --method PUT -H "Accept: application/vnd.github+json" "$endpoint" >/dev/null 2>&1; then
    echo "Enabled $label."
    return 0
  fi

  echo "Warning: could not enable $label (continuing)." >&2
  return 0
}

enable_security_and_analysis_feature() {
  local label="$1"
  local feature_key="$2"

  if gh api \
    --method PATCH \
    -H "Accept: application/vnd.github+json" \
    "/repos/$OWNER/$REPO" \
    --input - >/dev/null 2>&1 <<JSON
{
  "security_and_analysis": {
    "$feature_key": {
      "status": "enabled"
    }
  }
}
JSON
  then
    echo "Enabled $label."
    return 0
  fi

  echo "Warning: could not enable $label (continuing)." >&2
  return 0
}

echo "Enabling optional security defaults..."
enable_security_and_analysis_feature "Secret scanning" "secret_scanning"
enable_security_and_analysis_feature "Secret scanning push protection" "secret_scanning_push_protection"
enable_optional_feature "Dependabot alerts" "/repos/$OWNER/$REPO/vulnerability-alerts"
enable_optional_feature "Dependabot security updates" "/repos/$OWNER/$REPO/automated-security-fixes"
if [ "$(gh api "/repos/$OWNER/$REPO" --jq .visibility)" = "public" ]; then
  enable_optional_feature "Private vulnerability reporting" "/repos/$OWNER/$REPO/private-vulnerability-reporting"
fi

echo "Repository settings applied for $OWNER/$REPO"
"""


def _render_gh_create_project_script(name: str) -> str:
    return """#!/usr/bin/env bash
set -euo pipefail

OWNER="${1:-}"
REPO="${2:-}"
VISIBILITY="${3:-public}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$REPO_ROOT/.env"

usage() {
  cat <<'EOF'
Usage: ./scripts/gh-create-project.sh [owner] [repo] [private|public|internal]

Values resolve in this order:
  1) positional args
  2) .env / exported env:
     - GITHUB_ORG / github_org
     - GITHUB_REPO / github_repo
     - GH_REPO / GITHUB_REPOSITORY / github_full_repo
EOF
}

if ! command -v gh >/dev/null 2>&1; then
  echo "GitHub CLI (gh) is required." >&2
  exit 1
fi

if ! command -v git >/dev/null 2>&1; then
  echo "git is required." >&2
  exit 1
fi

load_env_from_file() {
  local env_file="$1"
  local line key value

  if [ ! -f "$env_file" ]; then
    return 0
  fi

  while IFS= read -r line || [ -n "$line" ]; do
    line="${line#"${line%%[![:space:]]*}"}"
    [ -z "$line" ] && continue
    case "$line" in
      #*) continue ;;
      export[[:space:]]*) line="${line#export }" ;;
    esac
    if [[ "$line" != *=* ]]; then
      continue
    fi

    key="${line%%=*}"
    value="${line#*=}"
    key="$(printf '%s' "$key" | tr -d '[:space:]')"
    value="${value%$'\\r'}"

    if [ "${value#\\"}" != "$value" ] && [ "${value%\\"}" != "$value" ]; then
      value="${value#\\"}"
      value="${value%\\"}"
    elif [ "${value#\\'}" != "$value" ] && [ "${value%\\'}" != "$value" ]; then
      value="${value#\\'}"
      value="${value%\\'}"
    fi

    case "$key" in
      github_token|GH_TOKEN|GITHUB_TOKEN)
        export GH_TOKEN="$value"
        ;;
      github_org|GITHUB_ORG)
        export GITHUB_ORG="$value"
        ;;
      github_repo|GITHUB_REPO)
        export GITHUB_REPO="$value"
        ;;
      github_full_repo|GH_REPO|GITHUB_REPOSITORY)
        export GH_REPO="$value"
        ;;
    esac
  done < "$env_file"
}

resolve_repo_ref() {
  local full_repo
  full_repo="${GH_REPO:-${GITHUB_REPOSITORY:-}}"

  if [ -z "$OWNER" ] && [ -n "${GITHUB_ORG:-}" ]; then
    OWNER="$GITHUB_ORG"
  fi
  if [ -z "$REPO" ] && [ -n "${GITHUB_REPO:-}" ]; then
    REPO="$GITHUB_REPO"
  fi

  if { [ -z "$OWNER" ] || [ -z "$REPO" ]; } && [ -n "$full_repo" ] && [[ "$full_repo" == */* ]]; then
    if [ -z "$OWNER" ]; then
      OWNER="${full_repo%%/*}"
    fi
    if [ -z "$REPO" ]; then
      REPO="${full_repo##*/}"
    fi
  fi

  if [ -z "$REPO" ]; then
    REPO="__REPO_NAME__"
  fi

  if [ -z "$OWNER" ]; then
    usage >&2
    exit 1
  fi
}

ensure_gh_auth() {
  if [ -z "${GH_TOKEN:-}" ] && [ -n "${GITHUB_TOKEN:-}" ]; then
    export GH_TOKEN="$GITHUB_TOKEN"
  fi

  if [ -n "${GH_TOKEN:-}" ]; then
    return 0
  fi

  if gh auth status >/dev/null 2>&1; then
    return 0
  fi

  echo "Authenticate first: gh auth login" >&2
  echo "Or set GH_TOKEN/GITHUB_TOKEN (legacy .env alias: github_token)." >&2
  exit 1
}

ensure_git_repo() {
  local current_branch
  cd "$REPO_ROOT"

  if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "Initializing local git repository..."
    git init >/dev/null
  fi

  if ! git rev-parse --verify HEAD >/dev/null 2>&1; then
    if ! git config user.name >/dev/null 2>&1 || ! git config user.email >/dev/null 2>&1; then
      echo "Git user.name and user.email are required for the initial commit." >&2
      echo 'Set them with: git config --global user.name "Your Name"' >&2
      echo '               git config --global user.email "you@example.com"' >&2
      exit 1
    fi

    git add -A
    if git diff --cached --quiet; then
      git commit --allow-empty -m "Initial scaffold" >/dev/null
    else
      git commit -m "Initial scaffold" >/dev/null
    fi
  fi

  current_branch="$(git symbolic-ref --short HEAD 2>/dev/null || true)"
  if [ "$current_branch" != "main" ]; then
    git branch -M main
  fi
}

ensure_origin_remote() {
  local https_remote
  local ssh_remote
  local current_remote
  https_remote="https://github.com/$OWNER/$REPO.git"
  ssh_remote="git@github.com:$OWNER/$REPO.git"

  if git remote get-url origin >/dev/null 2>&1; then
    current_remote="$(git remote get-url origin)"
    if [ "$current_remote" != "$https_remote" ] && [ "$current_remote" != "$ssh_remote" ]; then
      echo "Updating origin remote to $https_remote"
      git remote set-url origin "$https_remote"
    fi
  else
    git remote add origin "$https_remote"
  fi
}

load_env_from_file "$ENV_FILE"
resolve_repo_ref
ensure_gh_auth
ensure_git_repo

if gh repo view "$OWNER/$REPO" >/dev/null 2>&1; then
  echo "Repository already exists: $OWNER/$REPO"
  ensure_origin_remote
  echo "Pushing local main branch..."
  git push -u origin main
else
  echo "Creating repository $OWNER/$REPO ($VISIBILITY)..."
  gh repo create "$OWNER/$REPO" --"$VISIBILITY" --source "$REPO_ROOT" --remote origin --push
fi

"$SCRIPT_DIR/gh-apply-settings.sh" "$OWNER" "$REPO"

echo "Repository created and settings applied."
""".replace("__REPO_NAME__", name)


def _render_create_issues_script() -> str:
    return """#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: ./scripts/create-issues.sh [--repo owner/repo] [--file backlog/issues.json] [--dry-run] [--auth-check] [--project-number N | --project-title TITLE] [--project-owner OWNER]

Bulk-create milestones and issues from backlog JSON.

Options:
  --repo owner/repo   Target repository (optional if set in .env)
  --file PATH         Backlog file path (default: backlog/issues.json)
  --dry-run           Print planned actions without creating resources
  --auth-check        Validate GitHub auth/token and exit
  --project-number N  Add issues to an existing GitHub Project number
  --project-title T   Add issues to a GitHub Project title (creates if missing)
  --project-owner O   Owner login/org for the project (default: repo owner)
  -h, --help          Show this help message
EOF
}

REPO=""
BACKLOG_FILE="backlog/issues.json"
DRY_RUN=0
AUTH_CHECK=0
PROJECT_NUMBER=""
PROJECT_TITLE=""
PROJECT_OWNER=""

while [ $# -gt 0 ]; do
  case "$1" in
    --repo)
      REPO="${2:-}"
      if [ -z "$REPO" ]; then
        echo "Error: --repo requires a value." >&2
        exit 1
      fi
      shift 2
      ;;
    --file)
      BACKLOG_FILE="${2:-}"
      if [ -z "$BACKLOG_FILE" ]; then
        echo "Error: --file requires a value." >&2
        exit 1
      fi
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --auth-check)
      AUTH_CHECK=1
      shift
      ;;
    --project-number)
      PROJECT_NUMBER="${2:-}"
      if [ -z "$PROJECT_NUMBER" ]; then
        echo "Error: --project-number requires a value." >&2
        exit 1
      fi
      shift 2
      ;;
    --project-title)
      PROJECT_TITLE="${2:-}"
      if [ -z "$PROJECT_TITLE" ]; then
        echo "Error: --project-title requires a value." >&2
        exit 1
      fi
      shift 2
      ;;
    --project-owner)
      PROJECT_OWNER="${2:-}"
      if [ -z "$PROJECT_OWNER" ]; then
        echo "Error: --project-owner requires a value." >&2
        exit 1
      fi
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Error: unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [ -n "$PROJECT_NUMBER" ] && [ -n "$PROJECT_TITLE" ]; then
  echo "Error: use only one of --project-number or --project-title." >&2
  exit 1
fi

if [ ! -f "$BACKLOG_FILE" ]; then
  echo "Error: backlog file not found: $BACKLOG_FILE" >&2
  exit 1
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "GitHub CLI (gh) is required." >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$REPO_ROOT/.env"

load_env_from_file() {
  local env_file="$1"
  local line key value

  if [ ! -f "$env_file" ]; then
    return 0
  fi

  while IFS= read -r line || [ -n "$line" ]; do
    line="${line#"${line%%[![:space:]]*}"}"
    [ -z "$line" ] && continue
    case "$line" in
      #*) continue ;;
      export[[:space:]]*) line="${line#export }" ;;
    esac
    if [[ "$line" != *=* ]]; then
      continue
    fi

    key="${line%%=*}"
    value="${line#*=}"
    key="$(printf '%s' "$key" | tr -d '[:space:]')"
    value="${value%$'\\r'}"

    if [ "${value#\\"}" != "$value" ] && [ "${value%\\"}" != "$value" ]; then
      value="${value#\\"}"
      value="${value%\\"}"
    elif [ "${value#\\'}" != "$value" ] && [ "${value%\\'}" != "$value" ]; then
      value="${value#\\'}"
      value="${value%\\'}"
    fi

    case "$key" in
      github_token|GH_TOKEN|GITHUB_TOKEN)
        export GH_TOKEN="$value"
        ;;
      github_org|GITHUB_ORG)
        export GITHUB_ORG="$value"
        ;;
      github_repo|GITHUB_REPO)
        export GITHUB_REPO="$value"
        ;;
      github_full_repo|GH_REPO|GITHUB_REPOSITORY)
        export GH_REPO="$value"
        ;;
    esac
  done < "$env_file"
}

resolve_repo_ref() {
  local full_repo
  full_repo="${GH_REPO:-${GITHUB_REPOSITORY:-}}"

  if [ -z "$REPO" ]; then
    if [ -n "$full_repo" ] && [[ "$full_repo" == */* ]]; then
      REPO="$full_repo"
    elif [ -n "${GITHUB_ORG:-}" ] && [ -n "${GITHUB_REPO:-}" ]; then
      REPO="${GITHUB_ORG}/${GITHUB_REPO}"
    fi
  fi

  if [ -z "$REPO" ]; then
    echo "Error: repo is required (pass --repo owner/repo or set GH_REPO or GITHUB_ORG + GITHUB_REPO)." >&2
    usage >&2
    exit 1
  fi
}

resolve_project_config() {
  REPO_OWNER="${REPO%%/*}"

  if [ -n "$PROJECT_NUMBER" ] || [ -n "$PROJECT_TITLE" ]; then
    PROJECT_ENABLED=1
    if [ -z "$PROJECT_OWNER" ]; then
      PROJECT_OWNER="$REPO_OWNER"
    fi
  else
    PROJECT_ENABLED=0
    PROJECT_OWNER="${PROJECT_OWNER:-$REPO_OWNER}"
  fi
}

ensure_gh_auth() {
  if [ -z "${GH_TOKEN:-}" ] && [ -n "${GITHUB_TOKEN:-}" ]; then
    export GH_TOKEN="$GITHUB_TOKEN"
  fi

  if [ -n "${GH_TOKEN:-}" ]; then
    return 0
  fi

  if gh auth status >/dev/null 2>&1; then
    return 0
  fi

  echo "Authenticate first: gh auth login" >&2
  echo "Or set GH_TOKEN/GITHUB_TOKEN (legacy .env alias: github_token)." >&2
  exit 1
}

resolve_auth_user() {
  local login
  if ! login="$(gh api /user --jq .login 2>/dev/null)"; then
    echo "GitHub auth check failed. Ensure GH_TOKEN/GITHUB_TOKEN is valid, or run gh auth login." >&2
    exit 1
  fi
  if [ -z "$login" ] || [ "$login" = "null" ]; then
    echo "GitHub auth check failed: could not resolve authenticated user login." >&2
    exit 1
  fi
  printf '%s' "$login"
}

load_env_from_file "$ENV_FILE"
resolve_repo_ref
resolve_project_config
ensure_gh_auth
AUTH_USER="$(resolve_auth_user)"

if [ "$AUTH_CHECK" -eq 1 ]; then
  echo "GitHub auth OK: $AUTH_USER"
  if [ "$PROJECT_ENABLED" -eq 1 ]; then
    if gh project list --owner "$PROJECT_OWNER" --limit 1 >/dev/null 2>&1; then
      echo "GitHub project access OK: owner $PROJECT_OWNER"
    else
      echo "GitHub project access failed for owner $PROJECT_OWNER. Ensure project scope is granted." >&2
      exit 1
    fi
  fi
  exit 0
fi

TMP_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

MILESTONE_TITLES_FILE="$TMP_DIR/milestones.txt"
KNOWN_ISSUE_TITLES_FILE="$TMP_DIR/known_issue_titles.txt"
LABEL_NAMES_FILE="$TMP_DIR/labels.txt"
PROJECT_CREATED=0
PROJECT_ITEMS_ADDED=0
PROJECT_ITEMS_SKIPPED=0
RESOLVED_PROJECT_NUMBER=""
RESOLVED_PROJECT_TITLE=""

line_exists() {
  local needle="$1"
  local file="$2"
  [ -f "$file" ] && grep -Fxq -- "$needle" "$file"
}

resolve_project_target() {
  local projects_json="$TMP_DIR/projects.json"
  local project_create_json="$TMP_DIR/project_create.json"

  [ "$PROJECT_ENABLED" -eq 1 ] || return 0

  if [ -n "$PROJECT_NUMBER" ]; then
    if ! gh project view "$PROJECT_NUMBER" --owner "$PROJECT_OWNER" --format json > "$TMP_DIR/project_view.json" 2>"$TMP_DIR/project_view.err"; then
      cat "$TMP_DIR/project_view.err" >&2
      echo "Hint: GitHub Projects requires project scope; run: gh auth refresh -h github.com -s project" >&2
      exit 1
    fi
    RESOLVED_PROJECT_NUMBER="$PROJECT_NUMBER"
    RESOLVED_PROJECT_TITLE="$(python3 - "$TMP_DIR/project_view.json" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8") or "{}")
title = payload.get("title")
print(title.strip() if isinstance(title, str) and title.strip() else "")
PY
)"
    [ -z "$RESOLVED_PROJECT_TITLE" ] && RESOLVED_PROJECT_TITLE="Project #$PROJECT_NUMBER"
    echo "Using project: $PROJECT_OWNER/#$RESOLVED_PROJECT_NUMBER ($RESOLVED_PROJECT_TITLE)"
    return 0
  fi

  if ! gh project list --owner "$PROJECT_OWNER" --limit 100 --format json > "$projects_json" 2>"$TMP_DIR/project_list.err"; then
    cat "$TMP_DIR/project_list.err" >&2
    echo "Hint: GitHub Projects requires project scope; run: gh auth refresh -h github.com -s project" >&2
    exit 1
  fi

  RESOLVED_PROJECT_NUMBER="$(python3 - "$projects_json" "$PROJECT_TITLE" <<'PY'
import json
import sys
from pathlib import Path

data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8") or "[]")
target = sys.argv[2]

if isinstance(data, list):
    for item in data:
        if isinstance(item, dict) and item.get("title") == target and isinstance(item.get("number"), int):
            print(item["number"])
            raise SystemExit(0)
PY
)"

  if [ -n "$RESOLVED_PROJECT_NUMBER" ]; then
    RESOLVED_PROJECT_TITLE="$PROJECT_TITLE"
    echo "Using project: $PROJECT_OWNER/#$RESOLVED_PROJECT_NUMBER ($RESOLVED_PROJECT_TITLE)"
    return 0
  fi

  if [ "$DRY_RUN" -eq 1 ]; then
    echo "[dry-run] create project: $PROJECT_TITLE (owner: $PROJECT_OWNER)"
    RESOLVED_PROJECT_TITLE="$PROJECT_TITLE"
    return 0
  fi

  if ! gh project create --owner "$PROJECT_OWNER" --title "$PROJECT_TITLE" --format json > "$project_create_json" 2>"$TMP_DIR/project_create.err"; then
    cat "$TMP_DIR/project_create.err" >&2
    echo "Hint: GitHub Projects requires project scope; run: gh auth refresh -h github.com -s project" >&2
    exit 1
  fi

  RESOLVED_PROJECT_NUMBER="$(python3 - "$project_create_json" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8") or "{}")
number = payload.get("number")
print(number if isinstance(number, int) else "")
PY
)"

  if [ -z "$RESOLVED_PROJECT_NUMBER" ]; then
    echo "Failed to resolve project number after project creation." >&2
    exit 1
  fi

  RESOLVED_PROJECT_TITLE="$PROJECT_TITLE"
  PROJECT_CREATED=1
  echo "Created project: $PROJECT_OWNER/#$RESOLVED_PROJECT_NUMBER ($RESOLVED_PROJECT_TITLE)"
}

link_project_to_repo() {
  local link_err="$TMP_DIR/project_link.err"
  [ "$PROJECT_ENABLED" -eq 1 ] || return 0

  if [ -z "$RESOLVED_PROJECT_NUMBER" ]; then
    if [ "$DRY_RUN" -eq 1 ]; then
      echo "[dry-run] link project to repo: ${RESOLVED_PROJECT_TITLE:-$PROJECT_TITLE} -> $REPO"
    fi
    return 0
  fi

  if [ "$DRY_RUN" -eq 1 ]; then
    echo "[dry-run] link project to repo: $PROJECT_OWNER/#$RESOLVED_PROJECT_NUMBER -> $REPO"
    return 0
  fi

  if gh project link "$RESOLVED_PROJECT_NUMBER" --owner "$PROJECT_OWNER" --repo "$REPO" >/dev/null 2>"$link_err"; then
    echo "Linked project to repo: $PROJECT_OWNER/#$RESOLVED_PROJECT_NUMBER -> $REPO"
    return 0
  fi

  if grep -qiE 'already.*link' "$link_err"; then
    echo "Project already linked to repo: $PROJECT_OWNER/#$RESOLVED_PROJECT_NUMBER"
    return 0
  fi

  cat "$link_err" >&2
  failures=$((failures + 1))
}

add_issue_to_project() {
  local issue_title="$1"
  local issue_number="${2:-}"
  local issue_url
  local add_err="$TMP_DIR/project_add.err"

  [ "$PROJECT_ENABLED" -eq 1 ] || return 0

  if [ -z "$RESOLVED_PROJECT_NUMBER" ]; then
    if [ "$DRY_RUN" -eq 1 ]; then
      echo "[dry-run] add issue to project: $issue_title -> ${RESOLVED_PROJECT_TITLE:-$PROJECT_TITLE}"
      PROJECT_ITEMS_ADDED=$((PROJECT_ITEMS_ADDED + 1))
      return 0
    fi
    echo "Failed to add issue to project (missing project number): $issue_title" >&2
    failures=$((failures + 1))
    return 0
  fi

  if [ "$DRY_RUN" -eq 1 ]; then
    echo "[dry-run] add issue to project: $issue_title -> $PROJECT_OWNER/#$RESOLVED_PROJECT_NUMBER"
    PROJECT_ITEMS_ADDED=$((PROJECT_ITEMS_ADDED + 1))
    return 0
  fi

  if [ -z "$issue_number" ]; then
    echo "Failed to add issue to project (missing issue number): $issue_title" >&2
    failures=$((failures + 1))
    return 0
  fi

  issue_url="https://github.com/$REPO/issues/$issue_number"
  if gh project item-add "$RESOLVED_PROJECT_NUMBER" --owner "$PROJECT_OWNER" --url "$issue_url" >/dev/null 2>"$add_err"; then
    echo "Added issue to project: $issue_title"
    PROJECT_ITEMS_ADDED=$((PROJECT_ITEMS_ADDED + 1))
    return 0
  fi

  if grep -qiE 'already.*(exists|added)' "$add_err"; then
    echo "Skip project item (exists): $issue_title"
    PROJECT_ITEMS_SKIPPED=$((PROJECT_ITEMS_SKIPPED + 1))
    return 0
  fi

  cat "$add_err" >&2
  failures=$((failures + 1))
}

gh api --paginate "/repos/$REPO/milestones?state=all&per_page=100" > "$TMP_DIR/milestones.json"
python3 - "$TMP_DIR/milestones.json" "$MILESTONE_TITLES_FILE" <<'PY'
import json
import sys
from pathlib import Path

raw = Path(sys.argv[1]).read_text(encoding="utf-8")
decoder = json.JSONDecoder()
idx = 0
titles = []

while True:
    while idx < len(raw) and raw[idx].isspace():
        idx += 1
    if idx >= len(raw):
        break
    data, idx = decoder.raw_decode(raw, idx)
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and isinstance(item.get("title"), str):
                titles.append(item["title"])

text = "\\n".join(sorted(set(titles)))
if text:
    text += "\\n"
Path(sys.argv[2]).write_text(text, encoding="utf-8")
PY

gh api --paginate "/repos/$REPO/labels?per_page=100" > "$TMP_DIR/labels.json"
python3 - "$TMP_DIR/labels.json" "$LABEL_NAMES_FILE" <<'PY'
import json
import sys
from pathlib import Path

raw = Path(sys.argv[1]).read_text(encoding="utf-8")
decoder = json.JSONDecoder()
idx = 0
labels = []

while True:
    while idx < len(raw) and raw[idx].isspace():
        idx += 1
    if idx >= len(raw):
        break
    data, idx = decoder.raw_decode(raw, idx)
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                labels.append(item["name"])

text = "\\n".join(sorted(set(labels)))
if text:
    text += "\\n"
Path(sys.argv[2]).write_text(text, encoding="utf-8")
PY

touch "$KNOWN_ISSUE_TITLES_FILE"
touch "$LABEL_NAMES_FILE"

label_color() {
  python3 - "$1" <<'PY'
import hashlib
import sys

print(hashlib.sha1(sys.argv[1].encode("utf-8")).hexdigest()[:6])
PY
}

ensure_labels_exist() {
  local labels_csv="$1"
  local label color
  local -a labels_arr
  local label_err_file="$TMP_DIR/label_create.err"

  [ -z "$labels_csv" ] && return 0
  IFS=',' read -r -a labels_arr <<< "$labels_csv"

  for label in "${labels_arr[@]}"; do
    label="${label#"${label%%[![:space:]]*}"}"
    label="${label%"${label##*[![:space:]]}"}"
    [ -z "$label" ] && continue

    if line_exists "$label" "$LABEL_NAMES_FILE"; then
      continue
    fi

    if [ "$DRY_RUN" -eq 1 ]; then
      echo "[dry-run] create label: $label"
      printf '%s\\n' "$label" >> "$LABEL_NAMES_FILE"
      continue
    fi

    color="$(label_color "$label")"
    if gh api --method POST "/repos/$REPO/labels" -f name="$label" -f color="$color" >/dev/null 2>"$label_err_file"; then
      echo "Created label: $label"
      printf '%s\\n' "$label" >> "$LABEL_NAMES_FILE"
      continue
    fi

    if grep -qiE 'already_exists|name already exists on this repository' "$label_err_file"; then
      printf '%s\\n' "$label" >> "$LABEL_NAMES_FILE"
      continue
    fi

    echo "Failed to create label: $label" >&2
    failures=$((failures + 1))
  done
}

issue_exists_exact() {
  local title="$1"
  local search_output="$TMP_DIR/issue_search.json"

  if line_exists "$title" "$KNOWN_ISSUE_TITLES_FILE"; then
    return 0
  fi

  if ! gh issue list \
    --repo "$REPO" \
    --state all \
    --limit 100 \
    --search "$title in:title" \
    --json title > "$search_output"; then
    return 2
  fi

  if python3 - "$search_output" "$title" <<'PY'
import json
import sys
from pathlib import Path

data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
target = sys.argv[2]

if not isinstance(data, list):
    raise SystemExit("Unexpected gh issue list response shape")

exists = any(
    isinstance(item, dict) and isinstance(item.get("title"), str) and item["title"] == target
    for item in data
)
raise SystemExit(0 if exists else 1)
PY
  then
    printf '%s\\n' "$title" >> "$KNOWN_ISSUE_TITLES_FILE"
    return 0
  fi

  return 1
}

issue_number_exact() {
  local title="$1"
  local search_output="$TMP_DIR/issue_search_number.json"

  if ! gh issue list \
    --repo "$REPO" \
    --state all \
    --limit 100 \
    --search "$title in:title" \
    --json title,number > "$search_output"; then
    return 2
  fi

  python3 - "$search_output" "$title" <<'PY'
import json
import sys
from pathlib import Path

data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
target = sys.argv[2]

if not isinstance(data, list):
    raise SystemExit("Unexpected gh issue list response shape")

for item in data:
    if (
        isinstance(item, dict)
        and isinstance(item.get("title"), str)
        and item["title"] == target
        and isinstance(item.get("number"), int)
    ):
        print(item["number"])
        raise SystemExit(0)

raise SystemExit(1)
PY
}

milestones_created=0
issues_created=0
skipped=0
failures=0

resolve_project_target
link_project_to_repo

while IFS= read -r -d '' kind \
  && IFS= read -r -d '' title \
  && IFS= read -r -d '' milestone \
  && IFS= read -r -d '' labels_csv \
  && IFS= read -r -d '' assignees_csv \
  && IFS= read -r -d '' body; do
  if [ "$kind" = "milestone" ]; then
    if line_exists "$title" "$MILESTONE_TITLES_FILE"; then
      echo "Skip milestone (exists): $title"
      skipped=$((skipped + 1))
      continue
    fi

    if [ "$DRY_RUN" -eq 1 ]; then
      echo "[dry-run] create milestone: $title"
      milestones_created=$((milestones_created + 1))
      printf '%s\\n' "$title" >> "$MILESTONE_TITLES_FILE"
      continue
    fi

    if gh api --method POST "/repos/$REPO/milestones" -f title="$title" >/dev/null; then
      echo "Created milestone: $title"
      milestones_created=$((milestones_created + 1))
      printf '%s\\n' "$title" >> "$MILESTONE_TITLES_FILE"
    else
      echo "Failed to create milestone: $title" >&2
      failures=$((failures + 1))
    fi
    continue
  fi

  if [ "$kind" = "issue" ]; then
    if issue_exists_exact "$title"; then
      echo "Skip issue (exists): $title"
      skipped=$((skipped + 1))
      if existing_issue_number="$(issue_number_exact "$title")"; then
        add_issue_to_project "$title" "$existing_issue_number"
      fi
      continue
    else
      exists_rc=$?
      if [ "$exists_rc" -eq 2 ]; then
        echo "Failed to check existing issue by title: $title" >&2
        failures=$((failures + 1))
        continue
      fi
    fi

    ensure_labels_exist "$labels_csv"

    body_payload="$body"
    if [ -n "$milestone" ]; then
      if parent_epic_number="$(issue_number_exact "$milestone")"; then
        body_payload="Epic: #$parent_epic_number"$'\\n\\n'"$body_payload"
      else
        parent_lookup_rc=$?
        if [ "$DRY_RUN" -eq 1 ] && [ "$parent_lookup_rc" -eq 1 ]; then
          body_payload="Epic: #<epic_issue_number>"$'\\n\\n'"$body_payload"
        elif [ "$parent_lookup_rc" -eq 2 ]; then
          echo "Failed to resolve parent epic issue by title: $milestone" >&2
          failures=$((failures + 1))
          continue
        else
          echo "Failed to resolve epic issue number for milestone: $milestone" >&2
          failures=$((failures + 1))
          continue
        fi
      fi
    fi

    body_file="$TMP_DIR/issue_body.md"
    printf '%s' "$body_payload" > "$body_file"

    cmd=(gh issue create --repo "$REPO" --title "$title" --body-file "$body_file")
    if [ -n "$milestone" ]; then
      cmd+=(--milestone "$milestone")
    fi
    if [ -n "$labels_csv" ]; then
      cmd+=(--label "$labels_csv")
    fi
    if [ -n "$assignees_csv" ]; then
      cmd+=(--assignee "$assignees_csv")
    fi

    if [ "$DRY_RUN" -eq 1 ]; then
      echo "[dry-run] create issue: $title"
      issues_created=$((issues_created + 1))
      printf '%s\\n' "$title" >> "$KNOWN_ISSUE_TITLES_FILE"
      add_issue_to_project "$title"
      continue
    fi

    issue_create_output=""
    if issue_create_output="$("${cmd[@]}" 2>"$TMP_DIR/issue_create.err")"; then
      echo "Created issue: $title"
      issues_created=$((issues_created + 1))
      printf '%s\\n' "$title" >> "$KNOWN_ISSUE_TITLES_FILE"
      created_issue_url="$(printf '%s\\n' "$issue_create_output" | tail -n 1)"
      created_issue_number=""
      created_issue_tail="${created_issue_url##*/}"
      if [[ "$created_issue_tail" =~ ^[0-9]+$ ]]; then
        created_issue_number="$created_issue_tail"
      elif created_issue_number="$(issue_number_exact "$title")"; then
        :
      else
        created_issue_number=""
      fi
      add_issue_to_project "$title" "$created_issue_number"
    else
      echo "Failed to create issue: $title" >&2
      cat "$TMP_DIR/issue_create.err" >&2
      failures=$((failures + 1))
    fi
    continue
  fi

  echo "Skipping unsupported operation type: $kind"
  skipped=$((skipped + 1))
done < <(python3 - "$BACKLOG_FILE" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
epics = data.get("epics")

if not isinstance(epics, list):
    raise SystemExit("Invalid backlog JSON: expected top-level 'epics' list.")

def validate_str_list(value, field_name):
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise SystemExit(f"Invalid {field_name}: expected list of strings.")
    return value

def emit(value):
    if "\\0" in value:
        raise SystemExit("Invalid backlog JSON: null byte is not allowed in text fields.")
    sys.stdout.write(value)
    sys.stdout.write("\\0")

for epic in epics:
    if not isinstance(epic, dict):
        raise SystemExit("Invalid backlog JSON: each epic must be an object.")

    key = str(epic.get("key", "")).strip()
    title = str(epic.get("title", "")).strip()
    body = str(epic.get("body", "")).strip()
    labels = validate_str_list(epic.get("labels", []), "epic.labels")
    assignees = validate_str_list(epic.get("assignees", []), "epic.assignees")
    tickets = epic.get("tickets", [])

    if not key:
        raise SystemExit("Invalid backlog JSON: epic.key is required.")
    if not title:
        raise SystemExit("Invalid backlog JSON: epic.title is required.")
    if not body:
        raise SystemExit(f"Invalid backlog JSON: epic body is required for '{title}'.")
    if not isinstance(tickets, list):
        raise SystemExit("Invalid backlog JSON: epic.tickets must be a list.")

    epic_labels = []
    for label in ["epic", f"epic:{key}", *labels]:
        normalized = label.strip()
        if normalized and normalized not in epic_labels:
            epic_labels.append(normalized)

    emit("milestone")
    emit(title)
    emit("")
    emit("")
    emit("")
    emit("")

    emit("issue")
    emit(title)
    emit("")
    emit(",".join(epic_labels))
    emit(",".join(assignees))
    emit(body)

    for ticket in tickets:
        if not isinstance(ticket, dict):
            raise SystemExit("Invalid backlog JSON: ticket entries must be objects.")

        ticket_title = str(ticket.get("title", "")).strip()
        ticket_body = str(ticket.get("body", "")).strip()
        ticket_labels = validate_str_list(ticket.get("labels", []), "ticket.labels")
        ticket_assignees = validate_str_list(ticket.get("assignees", []), "ticket.assignees")

        if not ticket_title:
            raise SystemExit("Invalid backlog JSON: ticket.title is required.")
        if not ticket_body:
            raise SystemExit(f"Invalid backlog JSON: ticket body is required for '{ticket_title}'.")

        emit("issue")
        emit(ticket_title)
        emit(title)
        emit(",".join(ticket_labels))
        emit(",".join(ticket_assignees))
        emit(ticket_body)
PY
)

echo ""
echo "Summary:"
if [ "$DRY_RUN" -eq 1 ]; then
  echo "  mode: dry-run (created counts are planned actions)"
fi
echo "  milestones created: $milestones_created"
echo "  issues created: $issues_created"
if [ "$PROJECT_ENABLED" -eq 1 ]; then
  echo "  project owner: $PROJECT_OWNER"
  if [ -n "$RESOLVED_PROJECT_NUMBER" ]; then
    echo "  project number: $RESOLVED_PROJECT_NUMBER"
  else
    echo "  project title: ${RESOLVED_PROJECT_TITLE:-$PROJECT_TITLE}"
  fi
  echo "  project created: $PROJECT_CREATED"
  echo "  project items added: $PROJECT_ITEMS_ADDED"
  echo "  project items skipped: $PROJECT_ITEMS_SKIPPED"
fi
echo "  skipped: $skipped"
echo "  failures: $failures"

if [ "$failures" -gt 0 ]; then
  exit 1
fi
"""


def _render_go_main(name: str) -> str:
    return f"""package main

import "fmt"

func main() {{
    fmt.Println("{name} scaffold")
}}
"""


def _render_go_mod(name: str, owner: str | None) -> str:
    if owner:
        module = f"github.com/{owner}/{name}"
    else:
        module = f"example.com/{name}"

    return f"""module {module}

go 1.22
"""


def _module_path(name: str, owner: str | None) -> str:
    if owner:
        return f"github.com/{owner}/{name}"
    return f"example.com/{name}"


def _render_gin_go_mod(name: str, owner: str | None) -> str:
    module = _module_path(name, owner)
    return f"""module {module}

go 1.22

require (
\tgithub.com/gin-gonic/gin v1.10.0
)
"""


def _render_gin_main(name: str, owner: str | None) -> str:
    module = _module_path(name, owner)
    return f"""package main

import (
\t"log"

\t"{module}/routers"
)

func main() {{
\tr := routers.SetupRouter()
\tif err := r.Run(":8080"); err != nil {{
\t\tlog.Fatal(err)
\t}}
}}
"""


def _render_gin_router(name: str, owner: str | None) -> str:
    module = _module_path(name, owner)
    return f"""package routers

import (
\t"github.com/gin-gonic/gin"
\t"{module}/handlers"
)

func SetupRouter() *gin.Engine {{
\tr := gin.Default()
\tr.GET("/health", handlers.HealthCheck)
\treturn r
}}
"""


def _render_gin_health_handler() -> str:
    return """package handlers

import (
\t"net/http"

\t"github.com/gin-gonic/gin"
)

func HealthCheck(c *gin.Context) {
\tc.JSON(http.StatusOK, gin.H{
\t\t"status": "ok",
\t})
}
"""


def _render_gin_health_test(name: str, owner: str | None) -> str:
    module = _module_path(name, owner)
    return f"""package handlers_test

import (
\t"net/http"
\t"net/http/httptest"
\t"testing"

\t"github.com/gin-gonic/gin"
\t"{module}/handlers"
)

func TestHealthCheck(t *testing.T) {{
\tgin.SetMode(gin.TestMode)
\tr := gin.New()
\tr.GET("/health", handlers.HealthCheck)

\tw := httptest.NewRecorder()
\treq, _ := http.NewRequest(http.MethodGet, "/health", nil)
\tr.ServeHTTP(w, req)

\tif w.Code != http.StatusOK {{
\t\tt.Fatalf("expected 200, got %d", w.Code)
\t}}
}}
"""


def _render_python_pyproject(name: str) -> str:
    return f"""[build-system]
requires = ["setuptools>=69", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "{name}"
version = "0.1.0"
description = "{name} service"
requires-python = ">=3.10"
dependencies = []

[project.optional-dependencies]
dev = [
  "black>=24.8.0",
  "mypy>=1.11.0",
  "pre-commit>=3.8.0",
  "pytest>=8.0.0",
  "ruff>=0.6.0",
  "tox>=4.20.0",
]

[tool.setuptools]
package-dir = {{"" = "src"}}

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
addopts = "-q -s"
testpaths = ["tests"]

[tool.coverage.run]
branch = true
source = ["src"]

[tool.coverage.report]
fail_under = 70
show_missing = true
skip_covered = false
omit = [
  "src/*/__init__.py",
  "src/*/__main__.py",
]
exclude_also = [
  "pragma: no cover",
  "if TYPE_CHECKING:",
  "if __name__ == .__main__.:",
  "raise NotImplementedError",
]

[tool.coverage.xml]
output = "coverage.xml"

[tool.coverage.html]
directory = "htmlcov"

[tool.black]
line-length = 100
target-version = ["py310"]

[tool.ruff]
line-length = 100
target-version = "py310"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]

[tool.mypy]
python_version = "3.10"
mypy_path = "src"
files = ["src", "tests"]
strict = false
warn_unused_configs = true
warn_redundant_casts = true
warn_unused_ignores = true
warn_return_any = true
no_implicit_optional = true
"""


def _render_tox_ini() -> str:
    return """[tox]
envlist = lint,type,coverage

[testenv]
deps = -e .[dev]
setenv =
    VIRTUALENV_OVERRIDE_APP_DATA={toxinidir}/.tox/venv_app_data

[testenv:lint]
deps = -e .[dev]
commands =
    black --check src tests
    ruff check src tests

[testenv:format]
deps = -e .[dev]
commands =
    black src tests
    ruff check src tests --fix

[testenv:type]
deps = -e .[dev]
commands =
    mypy src

[testenv:test]
deps = -e .[dev]
commands =
    pytest -q {posargs:tests}

[testenv:test-fast]
deps = -e .[dev]
commands =
    pytest -q -m "not e2e_github" {posargs:tests}

[testenv:coverage]
package = editable
deps =
    -e .[dev]
    pytest-cov>=6
setenv =
    COVERAGE_FILE={toxworkdir}/.coverage.{envname}
commands =
    pytest -q -m "not e2e_github" --cov=src --cov-branch --cov-report=term-missing --cov-report=xml --cov-report=html {posargs:tests}

[testenv:coverage-fast]
package = editable
deps =
    {[testenv:coverage]deps}
setenv =
    {[testenv:coverage]setenv}
commands =
    pytest -q -m "not e2e_github" --cov=src --cov-branch --cov-report=term-missing {posargs:tests}

[testenv:codecov-upload]
skip_install = true
passenv =
    CODECOV_TOKEN
deps =
    codecov-cli>=11
commands =
    python -c 'import os, subprocess; from pathlib import Path; env=os.environ.copy(); p=Path(".env"); raws=p.read_text(encoding="utf-8").splitlines() if (not env.get("CODECOV_TOKEN") and p.exists()) else []; clean=[r.strip() for r in raws]; pairs=[(line[7:] if line.startswith("export ") else line).split("=", 1) for line in clean if line and not line.startswith("#") and "=" in line]; [env.setdefault(k.strip(), v.strip().rstrip("\\r").strip(chr(34)).strip(chr(39))) for k, v in pairs if k.strip() == "CODECOV_TOKEN"]; subprocess.run(["codecovcli", "do-upload", "--file", "coverage.xml"], check=True, env=env)'

[testenv:precommit]
skip_install = true
allowlist_externals =
    git
setenv =
    {[testenv]setenv}
    PYTHONPATH={toxinidir}/src
deps =
    {[testenv:format]deps}
    {[testenv:lint]deps}
    {[testenv:type]deps}
    {[testenv:coverage-fast]deps}
commands =
    {[testenv:format]commands}
    {[testenv:lint]commands}
    {[testenv:type]commands}
    {[testenv:coverage-fast]commands}
    git diff --exit-code -- src tests
"""


def _render_pre_commit_config(languages: Iterable[str]) -> str:
    selected = set(languages)
    lines = [
        "repos:",
        "  - repo: https://github.com/pre-commit/pre-commit-hooks",
        "    rev: v4.6.0",
        "    hooks:",
        "      - id: check-merge-conflict",
        "      - id: check-yaml",
        "      - id: check-toml",
        "      - id: end-of-file-fixer",
        "      - id: trailing-whitespace",
    ]
    if "python" in selected:
        lines.extend(
            [
                "  - repo: local",
                "    hooks:",
                "      - id: tox-suite",
                "        name: run tox suite (format, lint, type, coverage)",
                "        entry: tox",
                "        language: python",
                "        additional_dependencies:",
                "          - tox>=4.20.0",
                "        pass_filenames: false",
                '        args: ["-e", "precommit", "-vv"]',
                "        verbose: true",
            ]
        )
    return "\n".join(lines) + "\n"


def _render_python_init(name: str) -> str:
    return f"""\"\"\"{name} package.\"\"\"
"""


def _render_husky_pre_commit() -> str:
    return "#!/usr/bin/env sh\nnpx lint-staged\n"


def _render_react_package_json(name: str) -> str:
    safe_name = name.replace("_", "-")
    return f"""{{
  "name": "{safe_name}-web",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {{
    "dev": "vite",
    "lint": "eslint .",
    "build": "vite build",
    "preview": "vite preview",
    "prepare": "husky"
  }},
  "dependencies": {{
    "react": "^18.3.1",
    "react-dom": "^18.3.1"
  }},
  "devDependencies": {{
    "@eslint/js": "^9.21.0",
    "@vitejs/plugin-react": "^4.3.2",
    "eslint": "^9.21.0",
    "eslint-plugin-react-hooks": "^5.1.0",
    "eslint-plugin-react-refresh": "^0.4.19",
    "globals": "^16.0.0",
    "husky": "^9.0.0",
    "lint-staged": "^15.0.0",
    "prettier": "^3.0.0",
    "vite": "^5.4.8"
  }},
  "lint-staged": {{
    "*.{{ts,tsx,js,jsx}}": ["eslint --fix", "prettier --write"]
  }}
}}
"""


def _render_react_eslint_config() -> str:
    return """import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'

export default [
  { ignores: ['dist'] },
  {
    files: ['**/*.{js,jsx}'],
    languageOptions: {
      ecmaVersion: 2021,
      globals: globals.browser,
      parserOptions: {
        ecmaVersion: 'latest',
        ecmaFeatures: { jsx: true },
        sourceType: 'module',
      },
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      ...js.configs.recommended.rules,
      ...reactHooks.configs.recommended.rules,
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
    },
  },
]
"""


def _render_react_index_html(name: str) -> str:
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>{name}</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.jsx"></script>
  </body>
</html>
"""


def _render_react_main_jsx() -> str:
    return """import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './styles.css'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
"""


def _render_react_app_jsx(name: str) -> str:
    return f"""export default function App() {{
  return (
    <main className="app">
      <h1>{name} scaffold</h1>
      <p>Edit <code>web/src/App.jsx</code> to start building.</p>
    </main>
  )
}}
"""


def _render_react_styles() -> str:
    return """* {
  box-sizing: border-box;
}

:root {
  color: #0f172a;
  background: #f8fafc;
  font-family: "Segoe UI", Tahoma, Geneva, Verdana, sans-serif;
  line-height: 1.5;
}

body {
  margin: 0;
}

.app {
  min-height: 100vh;
  display: grid;
  place-content: center;
  gap: 0.5rem;
  text-align: center;
  padding: 2rem;
}

h1 {
  margin: 0;
  font-size: clamp(2rem, 4vw, 3rem);
}
"""


def _render_react_vite_config() -> str:
    return """import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
})
"""


def build_scaffold_files(config: ScaffoldConfig) -> list[ScaffoldFile]:
    _ensure_license_supported(config.license_id)

    files: list[ScaffoldFile] = [
        ScaffoldFile(
            config.out_dir / ".pre-commit-config.yaml",
            _render_pre_commit_config(config.languages),
        ),
        ScaffoldFile(
            config.out_dir / ".github" / "pull_request_template.md",
            _render_pr_template(),
        ),
        ScaffoldFile(
            config.out_dir / ".github" / "CODEOWNERS", _render_codeowners(config.owner)
        ),
        ScaffoldFile(
            config.out_dir / ".github" / "ISSUE_TEMPLATE" / "epic.md",
            _render_issue_epic_template(),
        ),
        ScaffoldFile(
            config.out_dir / ".github" / "ISSUE_TEMPLATE" / "ticket.md",
            _render_issue_ticket_template(),
        ),
        ScaffoldFile(
            config.out_dir / ".github" / "ISSUE_TEMPLATE" / "config.yml",
            _render_issue_config(config.owner, config.name),
        ),
        ScaffoldFile(
            config.out_dir / ".github" / "workflows" / "ci.yml",
            _render_ci_yaml(config.languages),
        ),
        ScaffoldFile(
            config.out_dir / ".github" / "workflows" / "codeql.yml",
            _render_codeql_yaml(config.languages),
        ),
        ScaffoldFile(
            config.out_dir / ".github" / "dependabot.yml",
            _render_dependabot_yaml(config.languages),
        ),
        ScaffoldFile(
            config.out_dir / "docs" / "requirements.md", "# Requirements\n\nTBD\n"
        ),
        ScaffoldFile(config.out_dir / "docs" / "api-v1.md", "# API v1\n\nTBD\n"),
        ScaffoldFile(config.out_dir / ".env.example", _render_env_example(config.name)),
        ScaffoldFile(
            config.out_dir / ".claude" / "settings.local.json",
            _render_claude_settings_local(),
        ),
        ScaffoldFile(config.out_dir / "AGENTS.md", _render_agents_md(config)),
        ScaffoldFile(config.out_dir / "README.md", _render_repo_readme(config)),
        ScaffoldFile(config.out_dir / "LICENSE", _apache_2_license()),
        ScaffoldFile(
            config.out_dir / ".gitignore", _render_gitignore(config.languages)
        ),
        ScaffoldFile(config.out_dir / ".editorconfig", _render_editorconfig()),
        ScaffoldFile(config.out_dir / "Makefile", _render_makefile()),
        ScaffoldFile(
            config.out_dir / "scripts" / "first_time_setup.sh",
            _render_first_time_setup_script(),
            executable=True,
        ),
    ]

    selected = set(config.languages)
    if "go" in selected:
        files.extend(
            [
                ScaffoldFile(
                    config.out_dir / "go.mod", _render_go_mod(config.name, config.owner)
                ),
                ScaffoldFile(
                    config.out_dir / "cmd" / config.name / "main.go",
                    _render_go_main(config.name),
                ),
                ScaffoldFile(config.out_dir / "internal" / ".gitkeep", ""),
            ]
        )

    if "gin" in selected:
        files.extend(
            [
                ScaffoldFile(
                    config.out_dir / "go.mod",
                    _render_gin_go_mod(config.name, config.owner),
                ),
                ScaffoldFile(
                    config.out_dir / "cmd" / config.name / "main.go",
                    _render_gin_main(config.name, config.owner),
                ),
                ScaffoldFile(
                    config.out_dir / "routers" / "router.go",
                    _render_gin_router(config.name, config.owner),
                ),
                ScaffoldFile(
                    config.out_dir / "handlers" / "health.go",
                    _render_gin_health_handler(),
                ),
                ScaffoldFile(
                    config.out_dir / "handlers" / "health_test.go",
                    _render_gin_health_test(config.name, config.owner),
                ),
            ]
        )

    if "python" in selected:
        files.extend(
            [
                ScaffoldFile(
                    config.out_dir / "pyproject.toml",
                    _render_python_pyproject(config.name),
                ),
                ScaffoldFile(config.out_dir / "tox.ini", _render_tox_ini()),
                ScaffoldFile(
                    config.out_dir / "src" / config.name / "__init__.py",
                    _render_python_init(config.name),
                ),
                ScaffoldFile(config.out_dir / "tests" / ".gitkeep", ""),
            ]
        )

    if "react" in selected:
        files.extend(
            [
                ScaffoldFile(
                    config.out_dir / "web" / "package.json",
                    _render_react_package_json(config.name),
                ),
                ScaffoldFile(
                    config.out_dir / "web" / ".husky" / "pre-commit",
                    _render_husky_pre_commit(),
                    executable=True,
                ),
                ScaffoldFile(
                    config.out_dir / "web" / "index.html",
                    _render_react_index_html(config.name),
                ),
                ScaffoldFile(
                    config.out_dir / "web" / "eslint.config.js",
                    _render_react_eslint_config(),
                ),
                ScaffoldFile(
                    config.out_dir / "web" / "src" / "main.jsx",
                    _render_react_main_jsx(),
                ),
                ScaffoldFile(
                    config.out_dir / "web" / "src" / "App.jsx",
                    _render_react_app_jsx(config.name),
                ),
                ScaffoldFile(
                    config.out_dir / "web" / "src" / "styles.css",
                    _render_react_styles(),
                ),
                ScaffoldFile(
                    config.out_dir / "web" / "vite.config.js",
                    _render_react_vite_config(),
                ),
            ]
        )

    return files


TEMPLATE_FILE_PATHS = (
    ".github/pull_request_template.md",
    ".github/CODEOWNERS",
    ".github/ISSUE_TEMPLATE/epic.md",
    ".github/ISSUE_TEMPLATE/ticket.md",
    ".github/ISSUE_TEMPLATE/config.yml",
)


def _filter_files_for_paths(
    files: Iterable[ScaffoldFile], root: Path, wanted_rel_paths: Iterable[str]
) -> list[ScaffoldFile]:
    wanted = set(wanted_rel_paths)
    filtered: list[ScaffoldFile] = []
    for file in files:
        rel = file.path.relative_to(root).as_posix()
        if rel in wanted:
            filtered.append(file)
    return filtered


def detect_languages_from_repo(repo_dir: Path) -> tuple[str, ...]:
    selected: list[str] = []
    go_mod = repo_dir / "go.mod"
    if go_mod.exists():
        if "gin-gonic/gin" in go_mod.read_text(encoding="utf-8"):
            selected.append("gin")
        else:
            selected.append("go")
    if (repo_dir / "pyproject.toml").exists():
        selected.append("python")
    if (repo_dir / "web" / "package.json").exists():
        selected.append("react")
    return tuple(lang for lang in ALLOWED_LANGUAGES if lang in selected)


def build_template_files(
    target_dir: Path, *, owner: str | None = None, name: str | None = None
) -> list[ScaffoldFile]:
    repo_name = name or target_dir.name
    cfg = ScaffoldConfig(
        name=repo_name,
        languages=ALLOWED_LANGUAGES,
        owner=owner,
        license_id=SUPPORTED_LICENSE,
        out_dir=target_dir,
    )
    return _filter_files_for_paths(
        build_scaffold_files(cfg), target_dir, TEMPLATE_FILE_PATHS
    )


def build_ci_files(
    target_dir: Path,
    *,
    languages: tuple[str, ...],
    owner: str | None = None,
    name: str | None = None,
) -> list[ScaffoldFile]:
    repo_name = name or target_dir.name
    cfg = ScaffoldConfig(
        name=repo_name,
        languages=languages,
        owner=owner,
        license_id=SUPPORTED_LICENSE,
        out_dir=target_dir,
    )
    return _filter_files_for_paths(
        build_scaffold_files(cfg),
        target_dir,
        [
            ".github/workflows/ci.yml",
            ".github/workflows/codeql.yml",
            "web/.husky/pre-commit",
        ],
    )


def build_dependabot_files(
    target_dir: Path,
    *,
    languages: tuple[str, ...],
    owner: str | None = None,
    name: str | None = None,
) -> list[ScaffoldFile]:
    repo_name = name or target_dir.name
    cfg = ScaffoldConfig(
        name=repo_name,
        languages=languages,
        owner=owner,
        license_id=SUPPORTED_LICENSE,
        out_dir=target_dir,
    )
    return _filter_files_for_paths(
        build_scaffold_files(cfg), target_dir, [".github/dependabot.yml"]
    )


def generate_scaffold(config: ScaffoldConfig) -> None:
    for file in build_scaffold_files(config):
        _write_file(file.path, file.content, executable=file.executable)


def remove_tree(path: Path) -> None:
    if not path.exists():
        return
    for child in sorted(path.glob("**/*"), reverse=True):
        if child.is_file() or child.is_symlink():
            child.unlink()
        elif child.is_dir():
            child.rmdir()
    path.rmdir()


def default_output_path(name: str) -> Path:
    return Path("out") / name
