# repo-scaffold

[![codecov](https://codecov.io/gh/blairg23/repo-scaffold/graph/badge.svg)](https://codecov.io/gh/blairg23/repo-scaffold)

`repo-scaffold` is a repo operations toolkit with seven modes:

- `create`: create/push a GitHub repo from a local folder and apply baseline settings
- `init`: generate a new language-first repository scaffold
- `apply`: apply capabilities safely to an existing repository
- `import`: convert markdown backlog notes into repo-scaffold JSON
- `check`: verify GitHub settings drift against the repo-scaffold baseline
- `project`: manage GitHub Projects with explicit destructive-op guards
- `delete`: safely clean up GitHub test repositories by prefix or exact name

Supported languages: `go`, `python`, `react`.

Workflow model:

- run `init` to generate local repo content
- run `create` to create/push the remote repository + baseline settings
- run `import backlog` inside a target repo when you want to turn `artifacts/tickets/*.md` into `artifacts/backlog/issues.json`
- run `apply ...` from this toolkit repo to manage templates/CI/dependabot/backlog/rules for any target repo
- run `check ...` from this toolkit repo to verify current GitHub settings against the repo-scaffold baseline
- run `project ...` from this toolkit repo when you need to inspect or manage GitHub Projects directly
- run `delete` from this toolkit repo to clean up test repositories

Repo-local GitHub convention:

- each repo's canonical roadmap/project metadata lives in `.repo-scaffold/project.json`
- `apply backlog --with-project` writes or refreshes that file automatically
- older repos can create or refresh it with `project sync-metadata`
- generated repos include `AGENTS.md` so local agents know to use `GH_REPO` and `.repo-scaffold/project.json` as their GitHub context
- generated repos include `.claude/settings.local.json` and `scripts/first_time_setup.sh` for the local GitHub Projects v2 token workflow

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
- Python scaffolds include `tox.ini`; generated CI runs `tox` (`lint`, `type`, `coverage`)
- scaffolds include `.env.example`, `.claude/settings.local.json`, and `scripts/first_time_setup.sh` for local GitHub Projects v2 setup

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
- applies merge settings, a managed default-branch ruleset, and security defaults unless `--skip-settings`
- pushes `HEAD` to remote `main` (`HEAD:main`) and does not rename/switch your local branch
- default branch policy uses a ruleset baseline: PR required, `0` approvals, conversation resolution, squash-only, linear history, no force-push, no delete, CodeQL merge protection, and automatic Copilot review on new pushes
- also attempts to enable secret scanning, push protection, Dependabot alerts, automated security updates, and public-repo private vulnerability reporting (best-effort; warnings only if plan/policy blocks them)
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
- `backlog --repo owner/repo [--file PATH] [--dry-run] [--auth-check] [--with-project] [--project-number N | --project-title T] [--project-owner O]`: bulk-create milestones/issues using `gh`; when `--file` is omitted, markdown tickets are auto-imported first if a ticket source directory exists
- `rules [--repo owner/repo] [--apply]`: preview or apply merge settings, the managed default-branch ruleset, and security defaults

### `import`

Convert markdown backlog notes into repo-scaffold backlog JSON.

```bash
poetry run repo-scaffold import backlog --path /path/to/repo --yes
```

Behavior:

- defaults source markdown to `<path>/artifacts/tickets`
- if `<path>/artifacts/tickets` is missing, falls back to `<path>/tickets`
- if both are missing, falls back to legacy `<path>/.future_tickets`
- defaults output JSON to `<path>/artifacts/backlog/issues.json`
- accepts the same overwrite-policy flags as other file-writing commands: `--yes`, `--no`, `--force`, `--dry-run`, `--backup`
- scans `*.md` recursively under the source directory
- imports explicit epic markdown files and ticket markdown files
- creates synthetic epics when tickets do not map to an existing epic
- merges into an existing backlog JSON file when one is already present
- skips epics already present by key and tickets already present by exact title
- produces backlog JSON that can be consumed immediately by `apply backlog`

Typical flow:

```bash
poetry run repo-scaffold import backlog --path /path/to/repo --yes
poetry run repo-scaffold apply backlog --path /path/to/repo --repo OWNER/REPO --dry-run
poetry run repo-scaffold apply backlog --path /path/to/repo --repo OWNER/REPO --with-project
```

### `check`

Verify current GitHub settings against the repo-scaffold baseline.

```bash
poetry run repo-scaffold check rules --repo acme/payments-api
```

Behavior:

- checks current merge settings
- checks the managed default-branch ruleset
- checks CodeQL merge protection and automatic Copilot review inside that ruleset baseline
- checks that legacy branch protection has been cleared
- checks secret scanning and push protection
- checks Dependabot alerts and Dependabot security updates
- checks private vulnerability reporting for public repos
- returns non-zero when drift is found

If `--repo` is omitted, resolves from `GH_REPO` or `GITHUB_ORG` + `GITHUB_REPO` from env/`.env`.

### `project`

Manage GitHub Projects directly.

```bash
poetry run repo-scaffold project list --project-owner acme
```

Subcommands:

- `list [--project-owner OWNER]`: list projects for an owner
- `view (--project-number N | --project-title T) [--project-owner OWNER]`: show project metadata
- `items (--project-number N | --project-title T) [--project-owner OWNER] [--limit N]`: list the contents of a project
- `sync-metadata (--project-number N | --project-title T) [--project-owner OWNER]`: write `.repo-scaffold/project.json` for the resolved project
- `create --project-title T [--project-owner OWNER] [--description TEXT] [--readme MD] [--visibility PUBLIC|PRIVATE] [--dry-run]`: create a project
- `edit (--project-number N | --project-title T) [--project-owner OWNER] [--title T] [--description TEXT] [--readme MD] [--visibility PUBLIC|PRIVATE] [--dry-run]`: update project metadata
- `delete (--project-number N | --project-title T) [--project-owner OWNER] --danger [--yes] [--dry-run] [--backup-dir PATH]`: delete a project with automatic backup + undo snapshot
- `item-add (--project-number N | --project-title T) [--project-owner OWNER] --repo OWNER/REPO --issue-number N`: add an existing issue to a project
- `item-delete (--project-number N | --project-title T) [--project-owner OWNER] (--item-id ID | --issue-number N) --danger [--yes] [--dry-run] [--backup-dir PATH]`: delete a project item with automatic backup + undo snapshot
- `link-repo (--project-number N | --project-title T) [--project-owner OWNER] --repo OWNER/REPO`: link a project to a repository so it appears in the repo's Projects tab
- `undo --backup-file PATH [--dry-run]`: restore a destructive backup snapshot

Behavior:

- owner resolution defaults in this order: `--project-owner`, then repo/env owner, then the authenticated GitHub login
- GitHub Projects operations require `project` scope (`gh auth refresh -h github.com -s project`)
- `list`, `view`, and `items` are read-only
- `create` and `edit` are standard write operations and support `--dry-run`
- destructive commands (`delete`, `item-delete`) require `--danger`
- destructive commands still prompt `y/N` unless `--yes` is passed
- destructive commands write a backup snapshot to `<path>/artifacts/project-backups` by default
- `sync-metadata`, `create`, `edit`, and project restores keep `<path>/.repo-scaffold/project.json` aligned with the resolved project
- the summary prints an exact `project undo --backup-file ...` command after a destructive write
- undo restores project membership and draft items, but does not recreate custom project fields or field values

Repo-local agent workflow:

- repo-local agents should treat `GH_REPO` (or `GITHUB_ORG` + `GITHUB_REPO`) as the canonical repo identity
- repo-local agents should read `.repo-scaffold/project.json` before doing project/ticket work
- repo-local agents should not mutate other repositories unless the user explicitly asks

Typical destructive flow:

```bash
poetry run repo-scaffold project items --project-owner acme --project-title "Roadmap"
poetry run repo-scaffold project item-delete --project-owner acme --project-title "Roadmap" --issue-number 42 --danger --yes
poetry run repo-scaffold project undo --backup-file /path/to/artifacts/project-backups/project-item-delete-<timestamp>-<suffix>.json
```

### `issue`

Query and manage GitHub issues.

```bash
poetry run repo-scaffold issue list --repo OWNER/REPO [--state open|closed|all] [--json]
poetry run repo-scaffold issue view --repo OWNER/REPO --issue-number N [--json]
poetry run repo-scaffold issue create --repo OWNER/REPO --title "TITLE" [--body "TEXT"] [--label L] [--assignee U]
poetry run repo-scaffold issue close --repo OWNER/REPO --issue-number N
poetry run repo-scaffold issue comment --repo OWNER/REPO --issue-number N --body "TEXT"
poetry run repo-scaffold issue label --repo OWNER/REPO --issue-number N [--add L] [--remove L]
poetry run repo-scaffold issue assign --repo OWNER/REPO --issue-number N [--add USER] [--remove USER]
```

---

### `pr`

Query and manage GitHub pull requests.

```bash
poetry run repo-scaffold pr list --repo OWNER/REPO [--json]
poetry run repo-scaffold pr view --repo OWNER/REPO --pr-number N [--json]
poetry run repo-scaffold pr create --repo OWNER/REPO --title "TITLE" --head BRANCH [--base main] [--body "TEXT"] [--draft]
poetry run repo-scaffold pr update --repo OWNER/REPO --pr-number N [--title "TITLE"] [--body "TEXT"]
poetry run repo-scaffold pr comment --repo OWNER/REPO --pr-number N --body "TEXT" [--reply-to COMMENT_ID]
poetry run repo-scaffold pr resolve-thread --repo OWNER/REPO --thread-id THREAD_ID
```

---

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

If `--file` is omitted and markdown source exists under `<repo-path>/artifacts/tickets` (or fallback source dirs), `apply backlog` auto-imports markdown into backlog JSON before applying. You can also override the markdown source with `GITHUB_TICKETS_DIR`. That means you can use repo-scaffold as the source-of-truth workspace:

```bash
poetry run repo-scaffold apply backlog --repo OWNER/REPO --with-project --dry-run
poetry run repo-scaffold apply backlog --repo OWNER/REPO --with-project
```

When the markdown tickets live in this repo but the target backlog belongs to another checkout, pass the target repo path and point `GITHUB_TICKETS_DIR` at this repo's ticket directory. Use an absolute path because relative `GITHUB_TICKETS_DIR` values resolve from `--path`.

```bash
GITHUB_TICKETS_DIR="$PWD/artifacts/tickets" \
  poetry run repo-scaffold apply backlog \
  --path /path/to/gallery-dl-wrapper \
  --repo OWNER/gallery-dl-wrapper \
  --dry-run

GITHUB_TICKETS_DIR="$PWD/artifacts/tickets" \
  poetry run repo-scaffold apply backlog \
  --path /path/to/gallery-dl-wrapper \
  --repo OWNER/gallery-dl-wrapper
```

When `--with-project` resolves or creates a GitHub Project, repo-scaffold also writes `<repo-path>/.repo-scaffold/project.json`. That gives the target repo a stable local pointer to its roadmap project for repo-local agents and scripts without depending on disposable `artifacts/`.

SOP for an older repo that already has a GitHub Project:

```bash
poetry run repo-scaffold project sync-metadata \
  --path /path/to/repo \
  --project-owner OWNER \
  --project-title "REPO_NAME Roadmap"
```

## Backlog import + JSON format

`apply backlog` still consumes JSON. If your source material is markdown, use `import backlog` first:

```bash
poetry run repo-scaffold import backlog --path /path/to/repo --yes
```

Default paths:

1. markdown source: `<repo-path>/artifacts/tickets`
2. JSON output: `<repo-path>/artifacts/backlog/issues.json`

Env override:

- `GITHUB_TICKETS_DIR=/absolute/or/relative/path`
- relative paths resolve from `<repo-path>`
- explicit `--source` still wins over the env var

Legacy fallback:

- if `<repo-path>/artifacts/tickets` does not exist, import tries `<repo-path>/tickets`
- if `<repo-path>/tickets` also does not exist, import uses `<repo-path>/.future_tickets`

Supported markdown import conventions:

- front matter keys like `name: Epic|Ticket`, `type: epic|ticket`, `key`, `epic`, `epic_key`, `milestone`, `labels`, `assignees`, `priority`
- `## Title` sections or first markdown heading for issue titles
- directory grouping for ticket-to-epic mapping
- `epic:<KEY>` labels for explicit ticket-to-epic association

If you already have JSON, or after import completes, `apply backlog` resolves `--file` with this fallback order:
1. `local/backlog/issues.json` (when present in the current working directory)
2. `<repo-path>/artifacts/backlog/issues.json` (where `--path` defaults to `.`)
3. legacy `<repo-path>/backlog/issues.json`

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
- `Administration`: Read and write (settings + rulesets + legacy branch-protection cleanup endpoints)
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

`pre-commit` runs a local tox gate via the `tox-suite` hook: auto-format first, then `lint`, `type`, and a fast branch-coverage gate.
If formatting or fixers changed tracked files, the hook exits non-zero so you can review, re-stage, and rerun the commit intentionally.

Tox workflow (lint, type, coverage):

```bash
poetry run tox
```

Generate coverage reports locally (`coverage.xml` + `htmlcov/`) and enforce the 70% threshold:

```bash
poetry run tox -e coverage
```

Fast tox gate used by pre-commit:

```bash
poetry run tox -e precommit
```

Auto-fix Python formatting/lint issues:

```bash
poetry run tox -e format
```

Optional local Codecov upload after generating `coverage.xml`:

If `CODECOV_TOKEN` is already in `.env`, this is enough:

```bash
poetry run tox -e codecov-upload
```

If you prefer an explicit shell export:

```bash
export CODECOV_TOKEN=your_codecov_token
poetry run tox -e codecov-upload
```

CI uploads `coverage.xml` as an artifact and attempts a Codecov upload when `CODECOV_TOKEN` is configured in GitHub Actions secrets.
The current minimum coverage gate is 70%.

If `tox` is not installed locally:

```bash
python -m pip install tox
```

Real GitHub E2E (creates a temporary remote repo and deletes it by default):

```bash
RUN_GITHUB_E2E=1 poetry run pytest -m e2e_github
```

You can also set `RUN_GITHUB_E2E=1` in `.env` and then run:

```bash
poetry run pytest -m e2e_github
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
