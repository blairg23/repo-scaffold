# AGENTS.md -- repo-scaffold

Operational guide for agents working in this repo. Read this before touching anything.

---

## What this repo does

`repo-scaffold` is a CLI for creating, scaffolding, and managing GitHub repositories.
It generates project structure (CI, templates, CODEOWNERS, AGENTS.md, SPEC.md),
applies settings, manages issue backlogs, and interacts with GitHub Projects v2 --
all via the GitHub REST and GraphQL APIs. No `gh` CLI required.

---

## Auth

`GH_TOKEN` lives in `.env`. Commands load it automatically via `_seed_env_from_dotenv`.
Copy `.env.example` to `.env` and fill in the token. It must be a classic PAT with
`repo`, `workflow`, and `project` scopes.

```bash
cp .env.example .env
# Edit .env and set GH_TOKEN=<your-token>
```

---

## Docker dev environment

Each active branch gets its own container (`{repo-name}-{branch-slug}`). All containers
share a base image built once from the Dockerfile in the repo root. Credentials are
passed as env vars at runtime -- never baked into the image.

**Windows:** `repo-scaffold docker shell`/`spin-up`/`build-base` auto-start Docker
Desktop if it isn't already running (launches it, then polls the daemon for up to 90s
before proceeding) and fail with a clear error if it can't come up in time. No manual
"start Docker Desktop first" step is needed for these commands. This does not apply to
the Compose-based flow in the [README](README.md#docker-dev-environment), which still
requires Docker Desktop to already be running.

### Per-repo container workflow (preferred)

One command to get a shell inside an isolated container for any branch:

```bash
poetry run repo-scaffold docker shell --repo OWNER/REPO --branch feat/NNN-my-feature
```

This builds the base image if needed, tears down any existing container for the branch,
starts a fresh one (clones the branch, installs deps), and drops you straight into bash.
The repo is at `/{repo-name}` inside the container (e.g. `/repo-scaffold`).

Add `--rebuild` when the Dockerfile changes:

```bash
poetry run repo-scaffold docker shell --repo OWNER/REPO --branch feat/NNN-my-feature --rebuild
```

Other commands (rarely needed directly):

```bash
# Build or rebuild the base image without starting a container
poetry run repo-scaffold docker build-base --repo OWNER/REPO [--path .]

# Start a container without exec-ing in (for background agent use)
poetry run repo-scaffold docker spin-up --repo OWNER/REPO --branch BRANCH

# Tear down a container
poetry run repo-scaffold docker spin-down --repo OWNER/REPO --branch BRANCH

# List all agent containers
poetry run repo-scaffold docker list [--repo OWNER/REPO]

# Watch CI; exits 0 (green), 1 (red), or 2 (timeout)
poetry run repo-scaffold pr wait --repo OWNER/REPO --pr-number N
```

Container names are derived deterministically: `{repo-slug}-{branch-slug}`.
Example: `repo-scaffold-feat-238-docker-per-repo-containers`.

Multiple agents can run in parallel -- each gets an isolated container.

**If you are already inside a container: do not run any `docker` or `workspace` commands.**
Both are host-side tools. Inside your container, your branch is already checked out at
`/{repo-name}`. Just work there: edit files, run tests, commit, push. No worktrees,
no new containers, no workspace create.

### Compose-based container (legacy)

The Compose stack (`docker-compose.yml` + optional `docker-compose.override.yml`) runs
a single long-lived container for the repo root. Prefer the per-repo container workflow
above for branch work; the Compose stack is useful for quick interactive exploration.

```bash
docker compose build       # build the image
docker compose up -d       # start
docker compose down        # stop
docker exec -it repo-scaffold-dev bash
```

The entire project root is bind-mounted read-write at `/workspace`, so `.env` is
readable and writable (required for `repo discover` to save `GH_TOKEN`).

---

## Commands

### Issues

```bash
poetry run repo-scaffold issue view   --repo OWNER/REPO --issue-number N [--json]
poetry run repo-scaffold issue list   --repo OWNER/REPO [--state open|closed|all] [--label LABEL] [--json]
poetry run repo-scaffold issue create --repo OWNER/REPO --title "TITLE" [--body "TEXT"] [--label L] [--assignee U]
poetry run repo-scaffold issue update --repo OWNER/REPO --issue-number N [--title "TITLE"] [--body "TEXT"] [--state open|closed]
poetry run repo-scaffold issue close  --repo OWNER/REPO --issue-number N
poetry run repo-scaffold issue comment --repo OWNER/REPO --issue-number N --body "TEXT"
poetry run repo-scaffold issue label  --repo OWNER/REPO --issue-number N [--add L] [--remove L]
poetry run repo-scaffold issue assign --repo OWNER/REPO --issue-number N [--add USER] [--remove USER]
```

### Pull Requests

```bash
poetry run repo-scaffold pr list    --repo OWNER/REPO [--json]
poetry run repo-scaffold pr view    --repo OWNER/REPO --pr-number N [--json]
poetry run repo-scaffold pr create  --repo OWNER/REPO --title "TITLE" --head BRANCH [--base main] [--body "TEXT"] [--draft]
poetry run repo-scaffold pr update  --repo OWNER/REPO --pr-number N [--title "TITLE"] [--body "TEXT"]
poetry run repo-scaffold pr comment --repo OWNER/REPO --pr-number N --body "TEXT" [--reply-to COMMENT_ID]
poetry run repo-scaffold pr resolve-thread --repo OWNER/REPO --thread-id THREAD_ID
poetry run repo-scaffold pr checks  --repo OWNER/REPO --pr-number N [--json]
poetry run repo-scaffold pr wait    --repo OWNER/REPO --pr-number N [--interval 30] [--timeout 1800]
```

`pr wait` polls until all checks pass (exit 0), any check fails (exit 1), or the timeout
is reached (exit 2, default 30 min). Use it to block an agent loop on CI.

> Never call `pr merge` -- only CODEOWNERS may merge. See CLAUDE.md.

### Docker containers

```bash
poetry run repo-scaffold docker build-base --repo OWNER/REPO [--path .]
poetry run repo-scaffold docker spin-up    --repo OWNER/REPO --branch BRANCH [--env-file .env]
poetry run repo-scaffold docker spin-down  --repo OWNER/REPO --branch BRANCH
poetry run repo-scaffold docker list       [--repo OWNER/REPO] [--json]
```

### Branches

```bash
poetry run repo-scaffold branch create --repo OWNER/REPO --name BRANCH [--from main]
poetry run repo-scaffold branch delete --repo OWNER/REPO --name BRANCH
```

### Projects

```bash
poetry run repo-scaffold project list     --project-owner OWNER
poetry run repo-scaffold project view     --project-title "TITLE"
poetry run repo-scaffold project items    --project-title "TITLE" --limit 40
poetry run repo-scaffold project create   --project-owner OWNER --project-title "TITLE" [--description "TEXT"]
poetry run repo-scaffold project edit     --project-owner OWNER --project-title "TITLE" [--title "NEW"] [--description "TEXT"]
poetry run repo-scaffold project item-add --project-title "TITLE" --repo OWNER/REPO --issue-number N
poetry run repo-scaffold project item-delete --project-title "TITLE" --issue-number N
poetry run repo-scaffold project link-repo   --project-title "TITLE" --repo OWNER/REPO
poetry run repo-scaffold project sync-metadata --project-owner OWNER --project-title "TITLE" --repo OWNER/REPO
```

### Backlog

```bash
# Compile individual .md tickets into issues.json
poetry run repo-scaffold import backlog --repo OWNER/REPO

# Push issues.json to GitHub (creates issues; add --with-project for project board items)
poetry run repo-scaffold apply backlog --repo OWNER/REPO --path . --with-project
```

### Scaffold Generation

```bash
# New repo from scratch (git init + initial commit + GitHub create)
poetry run repo-scaffold create --repo OWNER/REPO --visibility public --path /path/to/code

# Init scaffold files into an existing local directory
poetry run repo-scaffold init --name NAME --languages go,gin,python,react --owner OWNER --out /path --yes

# Apply CI workflows to an existing repo
poetry run repo-scaffold apply ci --path . --languages go,gin,python,react

# Apply issue/PR templates to an existing repo
poetry run repo-scaffold apply templates --path . --name NAME --owner OWNER

# Check branch protection rules
poetry run repo-scaffold check rules --repo OWNER/REPO

# Apply/sync repo settings, ruleset, and security features (see check rules for what it covers).
# If .github/dependabot.yml can't be committed directly because the ruleset already requires
# PRs, this opens a branch + PR for it instead of just warning -- check for and merge that PR.
poetry run repo-scaffold apply rules --repo OWNER/REPO --apply

# Archive a repo (read-only; reversible via the GitHub UI)
# Prompts for confirmation unless --yes is passed; refuses in a non-interactive shell without --yes.
poetry run repo-scaffold repo archive --repo OWNER/REPO [--yes]
```

**Supported languages:** `go`, `gin`, `python`, `react`
(`gin` and `go` are mutually exclusive -- both use go.mod)

---

## Key file locations

| Path | What it is |
|------|-----------|
| `src/repo_scaffold/cli.py` | Argparse entry point -- all subcommands |
| `src/repo_scaffold/generator.py` | Scaffold file generation (CI, templates, SPEC.md, etc.) |
| `src/repo_scaffold/github_api.py` | All GitHub API calls (urllib, no CLI) |
| `src/repo_scaffold/docker_ops.py` | Per-repo Docker container lifecycle (spin-up/down/list/build-base) |
| `src/repo_scaffold/backlog_ops.py` | Issue, milestone, label, project operations |
| `src/repo_scaffold/create_ops.py` | Repo creation, settings, git init |
| `src/repo_scaffold/project_ops.py` | GitHub Projects v2 management |
| `src/repo_scaffold/overwrite_policy.py` | File write/skip/overwrite logic |
| `tests/` | pytest test suite |
| `examples/` | Sample spec, backlog, and ticket files -- see `examples/README.md` |
| `artifacts/` | Gitignored. Ephemeral backlog files live here -- import and discard. |
| `.env.example` | Token and config template |
| `.github/ISSUE_TEMPLATE/epic.md` | Epic issue template |
| `.github/ISSUE_TEMPLATE/ticket.md` | Ticket issue template |
| `.github/pull_request_template.md` | PR template |

---

## Branch naming

Format: `type/NNN-short-description`

- `type`: `feat`, `fix`, `docs`, `chore`, `refactor`, `test`
- `NNN`: the GitHub issue number -- create the issue first if one does not exist
- `short-description`: kebab-case, 3-4 words max

Examples: `feat/153-pr-update-state`, `fix/148-workspace-gcm`, `docs/161-pillar-crt-spec`

`main` is the only long-lived branch. Never reuse a branch after its PR has merged.

---

## PR titles

Format: `type(scope): description (#NNN)`

Example: `feat(pr): add --state flag to pr update (#153)`

The issue number at the end is required so the PR is immediately traceable to its ticket.

---

## How to contribute

1. Create or pick an issue from the [repo-scaffold Roadmap](https://github.com/users/blairg23/projects/6). Note the issue number (`NNN`).
2. Branch off `main` using the `type/NNN-short-description` format:
   ```bash
   git checkout main && git pull
   git checkout -b feat/NNN-short-description
   ```
3. Make changes. Run the gate before committing:
   ```bash
   poetry run tox -e precommit
   ```
   If Black reformats files, re-stage them and re-run.
4. Commit with a subject + blank line + body (see recent commits for style). No one-liners.
5. Open a PR using the PR template. Title must follow `type(scope): description (#NNN)`:
   ```bash
   poetry run repo-scaffold pr create \
     --repo blairg23/repo-scaffold \
     --title "feat(scope): your change (#NNN)" \
     --head feat/NNN-short-description \
     --body-file .github/pull_request_template.md
   ```
   Fill in the template's sections before opening the PR -- `--body-file` preserves the required fields that `--body "..."` would drop.
6. Add the issue to the project board (issues only -- not PRs):
   ```bash
   poetry run repo-scaffold project item-add \
     --project-title "repo-scaffold Roadmap" \
     --repo blairg23/repo-scaffold \
     --issue-number NNN
   ```
7. Link the issue as a sub-issue of its parent epic:
   ```bash
   poetry run repo-scaffold issue add-sub-issue \
     --repo blairg23/repo-scaffold \
     --parent EPIC_NUMBER \
     --child NNN
   ```

---

## Reading all PR feedback

GitHub stores PR feedback across three separate endpoints. Always check all three before
concluding there is no feedback -- each covers a different type:

```bash
# 1. Inline review threads (line-level comments, may be unresolved)
poetry run repo-scaffold pr review-threads --repo OWNER/REPO --pr-number N --json

# 2. General conversation comments (posted in the PR timeline)
poetry run repo-scaffold pr list-comments --repo OWNER/REPO --pr-number N --json

# 3. Submitted PR reviews (approve / request-changes / comment with a body)
poetry run repo-scaffold pr reviews --repo OWNER/REPO --pr-number N --json
```

`pr check-sop` only covers inline review threads (endpoint 1). Endpoints 2 and 3 must
be checked manually.

---

## Review thread SOP

For every review thread on a PR you are working on, complete ALL four steps in order:

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

A thread is **NOT done** until all four steps are complete. To get THREAD_ID and COMMENT_ID:
```bash
poetry run repo-scaffold pr review-threads --repo OWNER/REPO --pr-number N --json
```

Verify SOP compliance before declaring work done:
```bash
poetry run repo-scaffold pr check-sop --repo OWNER/REPO --pr-number N
```

---

## Testing

```bash
poetry run pytest                         # all tests
poetry run pytest tests/test_cli.py -q   # CLI only
poetry run tox -e precommit              # full gate: format + lint + type + coverage
```

Coverage must stay at or above 70%.

---

## Standing rules

- **Docs ship with code.** Any PR that adds, changes, or deprecates a feature must update AGENTS.md, CLAUDE.md, and any relevant docs/ files in the same commit. A feature is not done until the docs reflect it. Deprecating a workflow means removing or replacing its documentation immediately -- stale docs cause real agent failures.
- Never use `gh` CLI -- use repo-scaffold commands or `github_api.py` directly.
- Never merge or close PRs -- only CODEOWNERS may do that.
- Never push to a branch whose PR is already merged -- cut a fresh branch from main.
- Always use issue and PR templates -- no freeform bodies.
- Before creating a ticket, search open AND closed issues for overlap. Update the existing ticket instead of creating a duplicate.
- Always add issues to the project board after creating them (issues only, not PRs).
- Commit messages: subject + blank line + body. No one-liners.
- Git identity: confirm `user.name` and `user.email` are real values before the first commit.
- Every ticket must have an `epic:<slug>` label before moving to In Progress. No orphans in the active lane. Tickets without an epic belong in `epic:maintenance` (Maintenance & Chores) until triaged into the correct epic.
- Epic slugs must be lowercase-kebab names derived from the epic title -- never numbers. Example: `epic:agent-orchestrator`, not `epic:48`. A slug must be human-readable without looking up the issue.
- Issue titles must be plain descriptive text -- never invent a prefix scheme. No `REPO-N:`, no `[Ticket]`, no `[EPIC]`, no `A1:`, no Jira-style codes. The issue number is GitHub's job.

### Triage workflow for orphaned tickets

```bash
# 1. Dry run -- see what is unaffiliated
poetry run repo-scaffold issue sync-hierarchy --repo OWNER/REPO

# 2. Label each orphan with the correct epic slug
poetry run repo-scaffold issue label --repo OWNER/REPO --issue-number N --add epic:maintenance

# 3. Re-run with --apply to backfill parent/child links
poetry run repo-scaffold issue sync-hierarchy --repo OWNER/REPO --apply
```

This file is the canonical agent reference. All other docs (CLAUDE.md, docs/WORKFLOW.md, .claude/prompt.md) defer to it for branch naming, PR conventions, and review SOP.

See [CLAUDE.md](CLAUDE.md) for portability and agnosticism requirements.
See [examples/README.md](examples/README.md) for backlog and ticket format examples.
