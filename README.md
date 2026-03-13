# repo-scaffold

`repo-scaffold` is a repo operations toolkit with four modes:

- `create`: create/push a GitHub repo from a local folder and apply baseline settings
- `init`: generate a new language-first repository scaffold
- `apply`: apply capabilities safely to an existing repository
- `delete`: safely clean up GitHub test repositories by prefix or exact name

Supported languages: `go`, `python`, `react`.

Workflow model:

- run `init` to generate local repo content
- run `create` to create/push the remote repository + baseline settings
- run `apply ...` from this toolkit repo to manage templates/CI/dependabot/backlog/rules for any target repo
- run `delete` from this toolkit repo to clean up test repositories

## Install

```bash
poetry install
```

## Commands

### `init`

Generate a new repository skeleton.

```bash
poetry run repo-scaffold init --name payments-api --languages go,python --owner acme
```

Defaults:

- `--name` defaults to `GITHUB_REPO` (if set), otherwise `repo-scaffold-e2e-<UTC timestamp>`
- `--languages` defaults to `go,python,react`
- output defaults to `./out/<name>`
- scaffolds include `.pre-commit-config.yaml`
- Python scaffolds include `tox.ini`; generated CI runs `tox` (`lint`, `type`, `test`)

Fast path (no required flags):

```bash
poetry run repo-scaffold init
```

Legacy compatibility: running without an explicit mode maps to `init`.

### `create`

Create/push a remote repository from a local folder.

```bash
poetry run repo-scaffold create --path /tmp/payments-api --repo acme/payments-api
```

Defaults:

- if `--path` is omitted, defaults to `./out/<repo-name>` and auto-runs `init` when the folder is missing or empty
- `<repo-name>` defaults in this order: `--repo` name part, then `GITHUB_REPO` (or `GH_REPO`/`GITHUB_REPOSITORY`), then `repo-scaffold-e2e-<UTC timestamp>`
- `--repo` is the primary target selector (`owner/repo`)
- `--name` is a fallback name only when `--repo` is omitted
- if `--repo` is omitted, resolve from env (`GH_REPO` or `GITHUB_ORG` + `GITHUB_REPO`)
- visibility defaults to `public` (override with `--visibility private|internal`)
- applies merge/branch protection settings unless `--skip-settings`
- pushes `HEAD` to remote `main` (`HEAD:main`) and does not rename/switch your local branch
- also attempts to enable Dependabot alerts and automated security updates (best-effort; warnings only if plan/policy blocks them)
- supports `--dry-run`

Tip: avoid shell-expanding possibly-unset vars in `--repo` (for example `--repo "$GITHUB_ORG/$TEST_REPO"`).
If you keep repo metadata in `.env`, omit `--repo` and let `repo-scaffold` resolve it.

### `apply`

Apply capabilities to an existing repo safely and idempotently.

```bash
poetry run repo-scaffold apply <subcommand> ...
```

Subcommands:

- `templates`: apply `.github` PR/issue templates, issue config, and `CODEOWNERS`
- `ci --languages <list>`: apply `.github/workflows/ci.yml`
- `dependabot [--low-noise]`: apply `.github/dependabot.yml`
- `backlog --repo owner/repo [--file PATH] [--dry-run] [--auth-check] [--with-project] [--project-number N | --project-title T] [--project-owner O]`: bulk-create milestones/issues using `gh`
- `rules [--repo owner/repo] [--apply]`: print or apply recommended `gh api` repo rules

### `delete`

Delete matching GitHub repositories (safe default: preview only).

```bash
poetry run repo-scaffold delete --owner OWNER --dry-run
```

Behavior:

- default is dry-run preview (no deletions)
- `--apply` performs deletion
- `--yes` skips confirmation prompt when `--apply` is used
- if `--owner` is omitted, resolve from env/`.env` (`GITHUB_ORG` or `GH_REPO`)
- if `--exact` is provided, delete only those exact names
- if `--exact` is not provided, delete repositories matching `--prefix` (`NAME` and `NAME-*`)
- `--cleanup` also deletes matching local directories
- `--local-only` deletes matching local directories only (no remote deletion)
- `--local-root` controls local search roots (repeatable; defaults to `/tmp` and `./out`)

## Overwrite policy

For file-writing commands (`init`, `apply templates`, `apply ci`, `apply dependabot`):

- default: never overwrite silently
- prompt: `Overwrite <path>? [y/N]` (default No)

Global flags:

- `--yes`
- `--no`
- `--force`
- `--dry-run`
- `--backup`

Per-file output labels:

- `CREATE    <path>`
- `SKIP      <path> (exists)`
- `OVERWRITE <path> (prompted)`
- `OVERWRITE <path> (--force)`
- `SKIP      <path> (--no)`

## Backlog apply notes

`apply backlog` uses GitHub CLI and Python stdlib only (no `jq`/`yq`/`node`/`pip` dependencies).

- idempotent by exact issue title and milestone title
- supports `.env` loading (`GH_TOKEN`, `GITHUB_ORG`, `GITHUB_REPO`, `GH_REPO`; legacy lowercase aliases still work)
- if `--repo` is omitted, resolves from `GH_REPO` or `GITHUB_ORG` + `GITHUB_REPO` from env/`.env`
- includes `.env.example` so you can run `cp .env.example .env` and fill credentials safely
- falls back to `gh auth status` / `gh auth login`
- supports `--auth-check` to validate token/session (`gh api /user`) before writing anything
- ticket bodies prepend an `Epic: #<number>` link to the created/found epic issue
- summary output splits epic issue counts and ticket issue counts (plus total issue counts)
- `--with-project` enables project integration with zero extra args
  if no project is specified, default project title is `<repo-name> Roadmap`
  optional env override: `GITHUB_PROJECT_TITLE` or `GITHUB_PROJECT_TITLE_TEMPLATE` (supports `{repo}`)
- optionally adds issues to GitHub Projects (`--project-number` or `--project-title`)
- with `--project-title`, creates the project if it does not exist
- attempts to link the project to the repository
- project operations require token/session access to GitHub Projects (`project` scope for classic PATs)

## Bulk GitHub Backlog Upload

Validate auth first:

```bash
poetry run repo-scaffold apply backlog --path /path/to/repo --repo OWNER/REPO --auth-check
```

Validate auth + project access preflight:

```bash
poetry run repo-scaffold apply backlog --path /path/to/repo --repo OWNER/REPO --project-title "Roadmap" --project-owner OWNER --auth-check
```

Preview writes:

```bash
poetry run repo-scaffold apply backlog --path /path/to/repo --repo OWNER/REPO --dry-run
```

Apply:

```bash
poetry run repo-scaffold apply backlog --path /path/to/repo --repo OWNER/REPO
```

Use an existing project board:

```bash
poetry run repo-scaffold apply backlog --path /path/to/repo --repo OWNER/REPO --project-number 1 --project-owner OWNER
```

Use or create a project by title:

```bash
poetry run repo-scaffold apply backlog --path /path/to/repo --repo OWNER/REPO --project-title "Roadmap" --project-owner OWNER
```

Use default project title from repo name (or env override) with one flag:

```bash
poetry run repo-scaffold apply backlog --path /path/to/repo --repo OWNER/REPO --with-project
```

## Backlog JSON format

Backlog input is JSON only. If `--file` is omitted, fallback order is:
1. `local/backlog/issues.json` (when present in the current working directory)
2. `<repo-path>/backlog/issues.json` (where `--path` defaults to `.`)

Completed sample file: `examples/backlog/issues.sample.json`.
Workspace-local private path in this repo: `local/backlog/issues.json` (git-ignored).

If you apply backlog to a different `--path` repo, pass an absolute `--file` path:

```bash
poetry run repo-scaffold apply backlog \
  --path /path/to/target/repo \
  --repo OWNER/REPO \
  --file "$(pwd)/local/backlog/issues.json"
```

```json
{
  "epics": [
    {
      "key": "A",
      "title": "Epic A - Example",
      "body": "## Summary\\nEpic issue body markdown (required).",
      "labels": ["platform"],
      "assignees": [],
      "tickets": [
        {
          "title": "A1: Example ticket",
          "body": "## Summary\\nTicket body markdown (required).",
          "labels": ["ticket", "epic:A"],
          "assignees": [],
          "priority": "P1"
        }
      ]
    }
  ]
}
```

Validation/behavior:

- Top-level `epics` is required and must be a list.
- Epic fields:
  - `key` required
  - `title` required
  - `body` required (full markdown body used to create the epic issue)
  - `labels` optional (additional labels; epic labels always include `epic` and `epic:<key>`)
  - `assignees` optional (default `[]`)
  - `tickets` required list
- Ticket fields:
  - `title` required
  - `body` required
  - `labels` optional (default `[]`)
  - `assignees` optional (default `[]`)
  - `priority` optional metadata (currently not used by GitHub API calls)
- Issue bodies are posted as-is (markdown).
- Ticket bodies are posted with a prepended epic link line: `Epic: #<epic_issue_number>`.
- Idempotency key is exact issue title and exact milestone title.

## GitHub token permissions

If you use `GH_TOKEN`/`GITHUB_TOKEN` instead of `gh auth login`, the token must include permissions for the features you run.

Classic PAT scopes:

- `repo` (required): repo create/push, issue/milestone/label reads+writes, repository API updates
- `workflow` (required if pushing `.github/workflows/*`): GitHub rejects workflow file pushes without this
- `read:org` (recommended/required in many org setups): org repo access checks and org-scoped operations
- `delete_repo` (optional): only required for automated cleanup/delete flows (for example E2E repo teardown)
- `project` (optional): only if you later automate GitHub Projects

Fine-grained PAT guidance:

- grant access to each target repository (or org-approved repository set)
- `Contents`: Read and write
- `Issues`: Read and write
- `Administration`: Read and write (settings + branch protection endpoints)
- `Workflows`: Read and write (workflow file pushes)

Note: if your org policy blocks some operations for fine-grained PATs (especially repo creation), use a classic PAT or `gh auth login`.

## Custom markdown templates

Edit these source templates to customize generated `.github` markdown:

- `src/repo_scaffold/templates/github/pull_request_template.md`
- `src/repo_scaffold/templates/github/ISSUE_TEMPLATE/epic.md`
- `src/repo_scaffold/templates/github/ISSUE_TEMPLATE/ticket.md`

`init` does not create a backlog file by default. Keep backlog JSON in this toolkit repo (for example `local/backlog/issues.json`); `apply backlog` now picks it up automatically when `--file` is omitted.

## PR Creation Workflow

`repo-scaffold` applies and generates `.github/pull_request_template.md`, which is used by GitHub when opening PRs.

There is currently no dedicated `repo-scaffold` PR-create command. Use `gh` directly:

```bash
gh pr create --base main --head <branch>
```

If you want to force the local template body explicitly:

```bash
gh pr create --base main --head <branch> --body-file .github/pull_request_template.md
```

## Generated structure (init)

```text
<out>/
  .pre-commit-config.yaml
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
  pyproject.toml   # when python selected
  tox.ini          # when python selected
  go.mod           # when go selected
  web/             # when react selected
  docs/
    requirements.md
    api-v1.md
  .env.example
```

## Test

```bash
poetry run pytest
```

Pre-commit checks:

```bash
poetry run pre-commit install
poetry run pre-commit run --all-files
```

`pre-commit` runs a local tox gate via the `tox-suite` hook: auto-format first, then `lint`, `type`, and `test-fast`.

Tox workflow (lint, type, test):

```bash
tox -e lint,type,test
```

Fast tox gate used by pre-commit:

```bash
tox -e precommit
```

Auto-fix Python formatting/lint issues:

```bash
tox -e format
```

If `tox` is not installed locally:

```bash
python -m pip install tox
```

Real GitHub E2E (creates a temporary remote repo and deletes it by default):

```bash
RUN_GITHUB_E2E=1 poetry run pytest -m e2e_github
```

Optional env toggles:

- `GITHUB_E2E_REPO_BASENAME` (default: `GITHUB_REPO` or `repo-scaffold-e2e`)
- `GITHUB_E2E_VISIBILITY` (`public` by default)
- `GITHUB_E2E_KEEP_REPO=1` to keep the remote repo after test
- `GITHUB_E2E_SKIP_SETTINGS_ASSERTS=1` to skip repository settings assertions

Note: automatic cleanup (`gh repo delete`) needs `delete_repo` scope; without it, the test warns and leaves the repo.

## E2E Repo Cleanup

Use the CLI `delete` mode to clean up test repos matching the protected prefix (`repo-scaffold-e2e`).

Preview only:

```bash
poetry run repo-scaffold delete --owner OWNER --dry-run
```

Delete all matches (`repo-scaffold-e2e` and `repo-scaffold-e2e-*`):

```bash
poetry run repo-scaffold delete --owner OWNER --apply --yes
```

Delete remote + local in one command:

```bash
poetry run repo-scaffold delete --owner OWNER --cleanup --apply --yes
```

Delete local only (leave remote repos untouched):

```bash
poetry run repo-scaffold delete --local-only --local-root /tmp --apply --yes
```

Delete specific exact names:

```bash
poetry run repo-scaffold delete --owner OWNER \
  --exact repo-scaffold-e2e \
  --exact repo-scaffold-e2e-20260311001924 \
  --apply --yes
```
