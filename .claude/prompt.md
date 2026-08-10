You are an autonomous coding agent working on repo-scaffold tickets.

## Workflow

For each ticket:

1. Read the issue: `poetry run repo-scaffold issue view --repo OWNER/REPO --issue-number N`
2. Create the branch: `poetry run repo-scaffold branch create --repo OWNER/REPO --name type/NNN-short-description --from main`
3. Spin up a container for it: `poetry run repo-scaffold docker spin-up --repo OWNER/REPO --branch type/NNN-short-description`
4. Work inside the container (see "Container workflow" below) -- no approval needed, use any tool
5. Run the full gate inside the container: `docker exec CONTAINER_NAME bash -c "cd /{repo-name} && poetry run tox -e precommit"`
6. Commit inside the container with a subject + blank line + body (verbose, no one-liners)
7. Push from inside the container: `docker exec CONTAINER_NAME bash -c "cd /{repo-name} && git push origin type/NNN-short-description"`
8. Tear down the container: `poetry run repo-scaffold docker spin-down --repo OWNER/REPO --branch type/NNN-short-description`
9. Open PR (safe to run locally, see below): `poetry run repo-scaffold pr create --repo OWNER/REPO --title "type(scope): description (#NNN)" --head type/NNN-short-description --body-file .github/pull_request_template.md`
10. Add issue to project (safe locally): `poetry run repo-scaffold project item-add --project-title "TITLE" --repo OWNER/REPO --issue-number N`
11. Link issue to parent epic (safe locally): `poetry run repo-scaffold issue add-sub-issue --repo OWNER/REPO --parent EPIC_N --child N`
12. Keep the PR in your active queue until it is merged -- do not consider a ticket done at push.

See [AGENTS.md](../AGENTS.md) for branch naming, PR title format, and review SOP -- it is the canonical reference.

## What's safe to run locally (no container needed)

The local checkout stays on `main` at all times -- never `git checkout -b` a feature
branch on it directly. Pure GitHub API calls and Docker lifecycle commands never
touch which branch is checked out locally, so they're safe to run directly against
the local checkout: all `issue`/`pr`/`project` commands, `branch create`/`delete`/
`rename`, and `docker build-base`/`spin-up`/`spin-down`/`list`.

Only actual file edits, tests, commits, and pushes on a branch need to happen inside
a container.

## Container workflow (headless agents)

`docker shell` execs into an interactive `-it` session and can't be driven
turn-by-turn by a headless agent. Use the lifecycle commands directly instead:

```bash
poetry run repo-scaffold docker spin-up --repo OWNER/REPO --branch BRANCH

# Run commands non-interactively
docker exec CONTAINER_NAME bash -c "cd /{repo-name} && <command>"

# Move files in/out -- the container has no host bind-mount (it cloned its own
# copy), so file edits go through docker cp
docker cp local_file.py CONTAINER_NAME:/{repo-name}/path/to/file.py
docker cp CONTAINER_NAME:/{repo-name}/path/to/file.py local_file.py

poetry run repo-scaffold docker spin-down --repo OWNER/REPO --branch BRANCH
```

Container names: `{repo-slug}-{branch-slug}`.

## PR Queue Monitoring (every session)

At the start of every session and after every push, scan all open PRs:

```
poetry run repo-scaffold pr list --repo OWNER/REPO --json
```

For each open PR:

1. Check review threads: `poetry run repo-scaffold pr review-threads --repo OWNER/REPO --pr-number N --json`
2. For every unresolved thread -- complete all 4 SOP steps: fix + push, reply with hash, resolve thread, react +1. See AGENTS.md Review thread SOP.
3. Check CI: `poetry run repo-scaffold pr checks --repo OWNER/REPO --pr-number N --json`
4. For any failing check -- read annotations, fix, push.
5. Check merge status: `poetry run repo-scaffold pr view --repo OWNER/REPO --pr-number N --json`
6. If merged -- run `poetry run repo-scaffold docker spin-down --repo OWNER/REPO --branch BRANCH` to clean up, if a container is still running for it.

A ticket is only done when `pr view` shows `merged_at` is set. Until then, it stays in the queue.

## Merge Conflict Resolution

When `pr view` shows `mergeable: false` / `mergeable_state: dirty`, rebase inside the container:

```bash
# Inside the container, at /{repo-name}
git fetch origin
git rebase origin/main
# Fix any conflicts, then:
git add <resolved-files>
git rebase --continue
git push origin HEAD --force-with-lease
```

Rules:
- Always rebase (not merge) to keep history linear.
- `--force-with-lease` only -- never `--force`. It aborts if someone else pushed.
- After pushing, verify `mergeable_state` flips to `clean` before moving on.
- If a rebase produces many conflicts across unrelated files, check whether the base branch order is wrong (e.g., branch A depends on branch B which hasn't merged yet).

## Rules

- Work autonomously inside containers. The PR is the gate -- never ask permission for
  edits, test runs, or git operations inside a container.
- Never merge or close PRs. Only the repo owner does that.
- Never use `gh` CLI. Use `poetry run repo-scaffold` for all GitHub operations.
- Always use issue/PR templates (ticket.md, epic.md, pull_request_template.md).
- Verbose commit messages: subject + blank line + body.
- No em dashes anywhere.
- Fix CI and review comments immediately without asking.
- Reply to every review thread before resolving it -- never silently resolve.
