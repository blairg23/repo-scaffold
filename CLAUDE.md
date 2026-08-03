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
- [ ] No shelling out to OS-installed binaries (no `subprocess` calls to `gh`, `brew`, `apt`, etc.)
- [ ] Python library imports (`import requests`, `import copier`, etc.) are fine and encouraged
- [ ] HTTP/REST preferred over SDK when SDK adds OS assumptions

## Execution Layer
- [ ] Agents decide WHAT to do, never HOW it executes
- [ ] All execution goes through CueQueue jobs
- [ ] CueQueue is the only layer that touches the OS
- [ ] Acceptable CueQueue job types: HTTP calls, file sync, local git, build tools, agent chains, any shell command routed through CueQueue

## Dependencies
- [ ] Before building any feature, search PyPI for a well-maintained library that already does it
- [ ] Wrapping a good library (`copier`, `requests`, etc.) is always better than rebuilding it
- [ ] Every external dependency is explicitly justified
- [ ] No shelling out to OS-installed CLIs — use the Python library or HTTP API directly
- [ ] Each dependency's purpose is documented

## Standing Rules (non-negotiable)
- [ ] Never use `gh` CLI (subprocess) — use GitHub REST API directly or repo-scaffold commands
- [ ] Never assume a shell exists — queue a CueQueue job instead
- [ ] Never write LLM-specific code — stay model-agnostic
- [ ] Never write OS-specific code — Python libraries and HTTP work everywhere
- [ ] CueQueue is the universal executor for all shell operations
- [ ] Check PyPI first — do not rebuild what a well-maintained Python library already does well

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
poetry run repo-scaffold pr check-sop --repo OWNER/REPO --pr-number N [--json]

# Branches
poetry run repo-scaffold branch create --repo OWNER/REPO --name BRANCH [--from main]
poetry run repo-scaffold branch delete --repo OWNER/REPO --name BRANCH

# Rename -- WARNING: an open PR against the branch may close instead of
# following the rename; check the PR after running this.
poetry run repo-scaffold branch rename --repo OWNER/REPO --name BRANCH --new-name NEW_BRANCH

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

# Apply/sync repo settings, ruleset, and security features. If .github/dependabot.yml
# can't be committed directly because the ruleset already requires PRs, this opens a
# branch + PR for it instead of just warning -- check for and merge that PR.
poetry run repo-scaffold apply rules --repo OWNER/REPO --apply

# Archive a repo (read-only; reversible via the GitHub UI). Prompts for confirmation
# unless --yes is passed; refuses in a non-interactive shell without --yes.
poetry run repo-scaffold repo archive --repo OWNER/REPO [--yes]
```

## PR Conventions

### Title format (required on every PR)

```
type(scope): description (#issue-number)
```

- `type`: `feat`, `fix`, `chore`, `docs`, `refactor`, `test`
- `(#issue-number)` at the end is **required** -- always include the tracking issue number
- Example: `feat(ruleset): add DRY sync command (#208)`
- If you forgot the issue number: `poetry run repo-scaffold pr update --repo OWNER/REPO --pr-number N --title "type(scope): description (#N)"`

### Review comment SOP (all three steps, in order, non-negotiable)

For every review thread on a PR you are working on, complete ALL of the following:

**Step 1 -- Push the fix.** Make the code change, commit, push. Note the commit hash.

**Step 2 -- Reply to the thread** with the commit hash and a one-sentence explanation:
```bash
poetry run repo-scaffold pr comment --repo OWNER/REPO --pr-number N \
  --body "Fixed in <hash>. <one sentence what changed>." \
  --reply-to COMMENT_ID
```

**Step 3 -- Resolve the thread:**
```bash
poetry run repo-scaffold pr resolve-thread --repo OWNER/REPO --thread-id THREAD_ID
```

**Step 4 -- React +1 to the original reviewer comment:**
```bash
poetry run repo-scaffold pr react --repo OWNER/REPO --comment-id COMMENT_ID --reaction "+1"
```

A thread is **NOT done** until all four steps are complete (fix, reply, resolve, react).

To get THREAD_ID and COMMENT_ID (databaseId of the first comment in each thread):
```bash
poetry run repo-scaffold pr review-threads --repo OWNER/REPO --pr-number N --json
```

To verify all threads are SOP-compliant before declaring work done:
```bash
poetry run repo-scaffold pr check-sop --repo OWNER/REPO --pr-number N
```

## Supported languages for init/apply ci
`go`, `gin`, `python`, `react`

- `gin` = Go web server with Gin framework (router, health handler, CI, CodeQL)
- `gin` and `go` are mutually exclusive (both use go.mod)

## GitHub auth
Token lives in `.env` as `GH_TOKEN`. Commands pick it up automatically via `_seed_env_from_dotenv`.

## Coverage
All GitHub operations (repos, issues, PRs, projects, backlog) are implemented via GH_TOKEN + urllib. No `gh` CLI required.


# Branch Workflow

## What needs a container vs. what's safe locally

Only actual branch work -- editing files, running tests, committing, pushing on a
specific branch -- needs a container. The local checkout stays on `main`/`master`
always; never `git checkout -b` a feature branch directly on it.

Everything else is a pure GitHub API call or a Docker lifecycle command and is
safe to run directly against the local checkout: all `issue`/`pr`/`project`
commands, `branch create`/`delete`/`rename`, and `docker build-base`/`spin-up`/
`spin-down`/`list`. None of these touch which branch is checked out locally.

## How to work on branches

Each branch gets its own isolated Docker container. The container clones the branch
and installs deps on startup. Your code is at `/{repo-name}` inside the container.
On Windows/macOS, Docker Desktop is auto-started if it isn't already running -- no
manual start step needed before `docker shell`. On Linux, start `dockerd` yourself
first (no auto-start there -- it's normally a sudo-gated systemd service, not an app).

```bash
# Get a shell in a branch container (builds image if needed, replaces any existing container)
poetry run repo-scaffold docker shell --repo OWNER/REPO --branch BRANCH

# Add --rebuild if the Dockerfile changed
poetry run repo-scaffold docker shell --repo OWNER/REPO --branch BRANCH --rebuild
```

`docker shell` execs into an interactive `-it` session -- it's built for a human at
a terminal and can't be driven turn-by-turn by a headless/scripted agent. If you
are a headless agent, use the lifecycle commands directly instead:

```bash
poetry run repo-scaffold docker spin-up --repo OWNER/REPO --branch BRANCH
docker exec CONTAINER_NAME bash -c "cd /{repo-name} && poetry run pytest -q"
docker cp local_file.py CONTAINER_NAME:/{repo-name}/path/to/file.py
docker exec CONTAINER_NAME bash -c "cd /{repo-name} && git add -A && git commit -m '...' && git push origin BRANCH"
poetry run repo-scaffold docker spin-down --repo OWNER/REPO --branch BRANCH
```

All editing, testing, committing, and pushing happens inside the container.
Do not use `workspace` commands -- they are obsolete.

## Agent rules

- **No approval requests.** Do the work, push a PR. The PR is the gate.
- **Only the repo owner merges PRs.** Never call `pr merge`.
- **Fix CI and review comments immediately.** Push the fix, don't ask.
- **Never run `docker` or `workspace` commands from inside a container.**
  You are already in the right place. Just work.

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
