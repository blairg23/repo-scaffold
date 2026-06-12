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

**Never call `gh` CLI directly.** Always use `poetry run repo-scaffold` commands. The CLI uses GH_TOKEN and the GitHub REST/GraphQL API directly — no `gh` binary, no shell assumptions, no OS dependencies. Commands can be queued as CueQueue HTTP or build-tool jobs on any platform.

## Available commands

```bash
# Issues
poetry run repo-scaffold issue view --repo OWNER/REPO --issue-number N [--json]
poetry run repo-scaffold issue list --repo OWNER/REPO [--state open|closed|all] [--label LABEL] [--json]
poetry run repo-scaffold issue create --repo OWNER/REPO --title "TITLE" [--body "TEXT"] [--label L] [--assignee U]
poetry run repo-scaffold issue close --repo OWNER/REPO --issue-number N
poetry run repo-scaffold issue update --repo OWNER/REPO --issue-number N [--title "TITLE"] [--body "TEXT"]
poetry run repo-scaffold issue comment --repo OWNER/REPO --issue-number N --body "TEXT"
poetry run repo-scaffold issue label --repo OWNER/REPO --issue-number N [--add L] [--remove L]
poetry run repo-scaffold issue assign --repo OWNER/REPO --issue-number N [--add USER] [--remove USER]

# Pull requests
poetry run repo-scaffold pr list --repo OWNER/REPO [--json]
poetry run repo-scaffold pr view --repo OWNER/REPO --pr-number N [--json]
poetry run repo-scaffold pr comment --repo OWNER/REPO --pr-number N --body "TEXT" [--reply-to COMMENT_ID]
poetry run repo-scaffold pr resolve-thread --repo OWNER/REPO --thread-id THREAD_ID
poetry run repo-scaffold pr create --repo OWNER/REPO --title "TITLE" --head BRANCH [--base main] [--body "TEXT"] [--draft]
poetry run repo-scaffold pr update --repo OWNER/REPO --pr-number N [--title "TITLE"] [--body "TEXT"]
poetry run repo-scaffold pr merge --repo OWNER/REPO --pr-number N [--method squash|merge|rebase]
poetry run repo-scaffold pr checks --repo OWNER/REPO --pr-number N [--json]
poetry run repo-scaffold pr annotations --repo OWNER/REPO --pr-number N [--json]
poetry run repo-scaffold pr rerun --repo OWNER/REPO --pr-number N [--failed-only]
poetry run repo-scaffold pr list-comments --repo OWNER/REPO --pr-number N [--json]
poetry run repo-scaffold pr review-threads --repo OWNER/REPO --pr-number N [--json]

# Branches
poetry run repo-scaffold branch create --repo OWNER/REPO --name BRANCH [--from main]
poetry run repo-scaffold branch delete --repo OWNER/REPO --name BRANCH

# Projects
poetry run repo-scaffold project list --project-owner OWNER
poetry run repo-scaffold project view --project-title "TITLE"
poetry run repo-scaffold project items --project-title "TITLE" --limit 40
poetry run repo-scaffold project create --project-owner OWNER --project-title "TITLE" [--description "TEXT"] [--repo OWNER/REPO]
poetry run repo-scaffold project edit --project-owner OWNER --project-title "TITLE" [--title "NEW"] [--description "TEXT"]
poetry run repo-scaffold project sync-metadata --project-owner OWNER --project-title "TITLE" --repo OWNER/REPO
poetry run repo-scaffold project item-add --project-title "TITLE" --repo OWNER/REPO --issue-number N
poetry run repo-scaffold project item-delete --project-title "TITLE" --issue-number N
poetry run repo-scaffold project link-repo --project-title "TITLE" --repo OWNER/REPO
poetry run repo-scaffold project setup-statuses --project-title "TITLE"
poetry run repo-scaffold project delete --project-owner OWNER --project-title "TITLE"  # dangerous — requires backup
poetry run repo-scaffold project undo --project-owner OWNER  # restore from last backup

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

## Coverage
All GitHub operations (repos, issues, PRs, projects, backlog) are implemented via GH_TOKEN + urllib. No `gh` CLI required.


# Workspace Workflow

## How to work on branches

All branch work happens inside managed worktrees under `repos/` (gitignored). Never use
OS temp folders or arbitrary paths.

```bash
# Create a worktree for a branch (clones bare repo on first use)
poetry run repo-scaffold workspace create --repo OWNER/REPO --branch BRANCH [--from main]

# List all active worktrees
poetry run repo-scaffold workspace list [--repo OWNER/REPO]

# Remove a worktree when a PR is merged
poetry run repo-scaffold workspace delete --repo OWNER/REPO --branch BRANCH

# Remove worktrees for branches that no longer exist on origin
poetry run repo-scaffold workspace prune --repo OWNER/REPO
```

Layout: `repos/{repo-name}/{branch-slug}/`

## Agent rules for workspace work

- **No approval requests inside worktrees.** Do the work, use whatever tool is fastest,
  push a PR. Do not ask permission for file edits, git commands, or test runs inside a
  worktree. The PR is the gate.
- **Only the repo owner merges PRs.** Never call `pr merge`. Push the branch, open the PR,
  fix all review comments, then stop. Merging and closing are the owner's job.
- **Clean up after merge.** Once you observe a PR has merged, run `workspace delete` to
  remove the local worktree.
- **Fix CI and review comments immediately.** When a review agent or CI flags something,
  fix it in the same worktree and push. Do not ask whether to fix it.

---

## Running tests
```bash
poetry run pytest                          # all tests
poetry run pytest tests/test_cli.py -q    # CLI only
poetry run tox -e precommit               # full gate (format + lint + type + coverage)
```

## Pre-commit
Runs `tox -e precommit` on every commit. Must pass before commit lands.
Format with `poetry run black src tests` if it fails on formatting.
