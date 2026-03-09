# repo-scaffold

`repo-scaffold` is a repo operations toolkit with three modes:

- `create`: create/push a GitHub repo from a local folder and apply baseline settings
- `init`: generate a new GitHub-ready repository scaffold
- `apply`: apply capabilities safely to an existing repository

Supported languages: `go`, `python`, `react`.

## Install

```bash
poetry install --with dev
```

## Commands

### `init`

Generate a new repository skeleton.

```bash
poetry run repo-scaffold init --name payments-api --languages go,python --owner acme
```

Legacy compatibility: running without an explicit mode maps to `init`.

### `create`

Create/push a remote repository from an existing local folder.

```bash
poetry run repo-scaffold create --path /tmp/payments-api --repo acme/payments-api
```

Defaults:

- if `--repo` is omitted, resolve from env (`GH_REPO` or `GITHUB_ORG` + `GITHUB_REPO`)
- visibility defaults to `public` (override with `--visibility private|internal`)
- applies merge/branch protection settings unless `--skip-settings`
- also attempts to enable Dependabot alerts and automated security updates (best-effort; warnings only if plan/policy blocks them)
- supports `--dry-run`

### `apply`

Apply capabilities to an existing repo safely and idempotently.

```bash
poetry run repo-scaffold apply <subcommand> ...
```

Subcommands:

- `templates`: apply `.github` PR/issue templates, issue config, and `CODEOWNERS`
- `ci --languages <list>`: apply `.github/workflows/ci.yml`
- `dependabot [--low-noise]`: apply `.github/dependabot.yml`
- `backlog --repo owner/repo [--file backlog/issues.json] [--dry-run] [--auth-check]`: bulk-create milestones/issues using `gh`
- `rules --repo owner/repo [--apply]`: print or apply recommended `gh api` repo rules

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
- includes `.env.example` so you can run `cp .env.example .env` and fill credentials safely
- falls back to `gh auth status` / `gh auth login`
- supports `--auth-check` to validate token/session (`gh api /user`) before writing anything
- ticket bodies include a `Parent epic: #<number>` link when the epic issue exists/was created
- creates milestones/issues only; it does not create a GitHub Project

## Bulk GitHub Backlog Upload

Validate auth first:

```bash
poetry run repo-scaffold apply backlog --path /path/to/repo --repo OWNER/REPO --auth-check
```

Preview writes:

```bash
poetry run repo-scaffold apply backlog --path /path/to/repo --repo OWNER/REPO --dry-run
```

Apply:

```bash
poetry run repo-scaffold apply backlog --path /path/to/repo --repo OWNER/REPO
```

Equivalent generated-repo script:

```bash
./scripts/create-issues.sh --repo OWNER/REPO --auth-check
./scripts/create-issues.sh --repo OWNER/REPO --dry-run
./scripts/create-issues.sh --repo OWNER/REPO
```

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

`backlog/issues.json` starts empty by default; add your own epic/ticket entries (you can base bodies on these templates).

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
  backlog/
    issues.json
  docs/
    requirements.md
    api-v1.md
  scripts/
    create-issues.sh
    gh-apply-settings.sh
    gh-create-project.sh
  .env.example
```

## Test

```bash
poetry run pytest
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
