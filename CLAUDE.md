# Pre-Implementation Requirements

Before writing any code, verify every item below. These are non-negotiable and apply to every change in every repo.

## Portability
- [ ] No OS-specific tools or CLIs (no `gh`, no `brew`, no `apt`, no bash-only commands)
- [ ] No assumptions about shell availability
- [ ] Works on Windows, Linux, and Mac equally
- [ ] Works in headless and agent environments

## Agnosticism
- [ ] No LLM-specific code, prompts, or dependencies
- [ ] No assumptions about which AI tool is running
- [ ] No CLI wrappers around HTTP APIs — use HTTP directly
- [ ] HTTP/REST preferred over SDK when SDK adds OS assumptions

## Execution Layer
- [ ] Agents decide WHAT to do, never HOW it executes
- [ ] All execution goes through CueQueue jobs
- [ ] CueQueue is the only layer that touches the OS
- [ ] Acceptable CueQueue job types: HTTP calls, file sync, local git, build tools, agent chains, any shell command routed through CueQueue

## Dependencies
- [ ] Every external dependency is explicitly justified
- [ ] HTTP APIs preferred over installed CLIs
- [ ] If a CLI wraps an HTTP API, the API is used directly
- [ ] Each dependency's purpose is documented

## Standing Rules (non-negotiable)
- [ ] Never use `gh` CLI — use GitHub REST API directly
- [ ] Never assume a shell exists — queue a CueQueue job instead
- [ ] Never write LLM-specific code — stay model-agnostic
- [ ] Never write OS-specific code — HTTP works everywhere
- [ ] CueQueue is the universal executor for all shell operations

---

# repo-scaffold

## Golden rule: use repo-scaffold for everything GitHub

**Never use PowerShell + `gh.exe` directly.** Always use `poetry run repo-scaffold` commands (via Bash/WSL). The CLI should NEVER require `gh` on the PATH — it must use GH_TOKEN/GitHub API directly for all operations.

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
# Both flows use the same command — create handles git init + initial commit internally:
# New project OR existing local code with no .git:
poetry run repo-scaffold create --repo OWNER/REPO --visibility public --path /path/to/code

poetry run repo-scaffold check rules --repo OWNER/REPO
```

## Supported languages for init/apply ci
`go`, `gin`, `python`, `react`

- `gin` = Go web server with Gin framework (router, health handler, CI, CodeQL)
- `gin` and `go` are mutually exclusive (both use go.mod)

## GitHub auth
Token lives in `.env` as `GH_TOKEN`. Commands pick it up automatically via `_seed_env_from_dotenv`.

## Still missing (file tickets, do NOT work around with gh/PS)
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
