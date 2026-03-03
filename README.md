# repo-scaffold

`repo-scaffold` generates a ready-to-push GitHub repository skeleton with CI, CodeQL, Dependabot, issue/PR templates, and language-specific starter files.

Supported languages:

- `go`
- `python`
- `react`

No Windows-specific automation files are generated.

## Install

```bash
python -m pip install -e .
```

## Usage

Required flags:

- `--name <repo_name>`
- `--languages <comma-separated>` where each value is one of `go`, `python`, `react`

Optional flags:

- `--owner <github_owner>`
- `--license apache-2.0` (default, only supported value)
- `--out <path>` (default: `./out/<name>`)
- `--overwrite` (replace an existing non-empty output directory without prompting)

### Examples

```bash
repo-scaffold --name payments-api --languages go --owner acme
repo-scaffold --name platform-core --languages go,python --owner acme
repo-scaffold --name web-client --languages react --out /tmp/web-client
repo-scaffold --name fullstack --languages python,react --owner acme --out ./generated/fullstack
repo-scaffold --name payments-api --languages go --out ./out/payments-api --overwrite
```

If `--out` already exists and is non-empty, the CLI prompts:
`Overwrite? [y/N]`.
In non-interactive shells, use `--overwrite` to proceed.

## What it does

`repo-scaffold` generates files locally at `--out`.
It does not automatically modify existing repository files.

For GitHub repository settings:

- `scripts/gh-create-project.sh` creates a new GitHub repo from the generated folder and applies settings.
- `scripts/gh-apply-settings.sh <owner> <repo>` updates settings on an existing GitHub repo.

## Generated structure

```text
<out>/
  .github/
    pull_request_template.md
    CODEOWNERS
    ISSUE_TEMPLATE/
      epic.md
      ticket.md
      config.yml
    workflows/
      ci.yml
      codeql.yml
    dependabot.yml
  docs/
    requirements.md
    api-v1.md
  scripts/
    gh-apply-settings.sh
    gh-create-project.sh
  README.md
  LICENSE
  .gitignore
  .editorconfig
  Makefile
```

Language-specific files are added only when selected.

## GitHub settings automation

Run `scripts/gh-apply-settings.sh <owner> <repo>` after creating/pushing the repository. It applies:

- squash-only merges
- delete branch on merge
- marks the repository as a template (`is_template: true`)
- `main` branch protection (PR required, force-push/deletion blocked, conversation resolution required, no required status contexts yet)

Authentication note: the scripts use GitHub CLI API calls (`gh api`). Run `gh auth login` first. SSH keys are fine for git transport, but API calls still require `gh` auth.

## Test

```bash
python -m pip install -e .[dev]
pytest
```
