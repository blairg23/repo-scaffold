# repo-scaffold

## Golden rule: use repo-scaffold for everything GitHub

**Never use PowerShell + `gh.exe` directly.** Always use `poetry run repo-scaffold` commands (via Bash/WSL). The CLI wraps `gh` internally with proper auth from `.env`.

## Available commands

```bash
# Issues
poetry run repo-scaffold issue view --repo OWNER/REPO --issue-number N
poetry run repo-scaffold issue view --repo OWNER/REPO --issue-number N --json

# Projects
poetry run repo-scaffold project items --project-title "TITLE" --limit 40
poetry run repo-scaffold project list --project-owner OWNER
poetry run repo-scaffold project view --project-title "TITLE"

# Backlog
poetry run repo-scaffold apply backlog --repo OWNER/REPO --path .
poetry run repo-scaffold import backlog --repo OWNER/REPO

# Scaffold generation
poetry run repo-scaffold init --name NAME --languages go,gin,python,react --owner OWNER --out /path --yes
poetry run repo-scaffold apply ci --path . --languages go,gin,python,react
poetry run repo-scaffold apply templates --path . --name NAME --owner OWNER

# Repo management
poetry run repo-scaffold create --repo OWNER/REPO
poetry run repo-scaffold check rules --repo OWNER/REPO
```

## Supported languages for init/apply ci
`go`, `gin`, `python`, `react`

- `gin` = Go web server with Gin framework (router, health handler, CI, CodeQL)
- `gin` and `go` are mutually exclusive (both use go.mod)

## GitHub auth
Token lives in `.env` as `GH_TOKEN`. Commands pick it up automatically via `_seed_env_from_dotenv`.

## Still missing (potential future tickets)
- `repo-scaffold pr list`
- `repo-scaffold pr create`
- `repo-scaffold pr comment --reply-to <id>`
- `repo-scaffold pr resolve-thread`

## Running tests
```bash
poetry run pytest                          # all tests
poetry run pytest tests/test_cli.py -q    # CLI only
poetry run tox -e precommit               # full gate (format + lint + type + coverage)
```

## Pre-commit
Runs `tox -e precommit` on every commit. Must pass before commit lands.
Format with `poetry run black src tests` if it fails on formatting.
