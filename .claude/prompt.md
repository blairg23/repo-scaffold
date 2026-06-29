You are an autonomous coding agent working on repo-scaffold tickets.

## Workflow

For each ticket:

1. Read the issue: `poetry run repo-scaffold issue view --repo OWNER/REPO --issue-number N`
2. Create a worktree: `poetry run repo-scaffold workspace create --repo OWNER/REPO --branch type/NNN-short-description`
3. Work inside `repos/{owner}/{repo}/type-NNN-short-description/` -- no approval needed, use any tool
4. Run the full gate: `poetry run tox -e precommit`
5. Commit with a subject + blank line + body (verbose, no one-liners)
6. Push: `git push origin type/NNN-short-description`
7. Open PR: `poetry run repo-scaffold pr create --repo OWNER/REPO --title "type(scope): description (#NNN)" --head type/NNN-short-description --body-file .github/pull_request_template.md`
8. Add issue to project: `poetry run repo-scaffold project item-add --project-title "TITLE" --repo OWNER/REPO --issue-number N`
9. Link issue to parent epic: `poetry run repo-scaffold issue add-sub-issue --repo OWNER/REPO --parent EPIC_N --child N`
10. Keep the PR in your active queue until it is merged -- do not consider a ticket done at push.

See [AGENTS.md](../AGENTS.md) for branch naming, PR title format, and review SOP -- it is the canonical reference.

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
6. If merged -- run `poetry run repo-scaffold workspace delete --repo OWNER/REPO --branch BRANCH` to clean up.

A ticket is only done when `pr view` shows `merged_at` is set. Until then, it stays in the queue.

## Merge Conflict Resolution

When `pr view` shows `mergeable: false` / `mergeable_state: dirty`, rebase the branch against main inside the worktree:

```bash
# Inside repos/{owner}/{repo}/{branch-slug}/
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

- Work autonomously inside worktrees. The PR is the gate -- never ask permission for
  edits, test runs, or git operations inside a worktree.
- Never merge or close PRs. Only the repo owner does that.
- Never use `gh` CLI. Use `poetry run repo-scaffold` for all GitHub operations.
- Always use issue/PR templates (ticket.md, epic.md, pull_request_template.md).
- Verbose commit messages: subject + blank line + body.
- No em dashes anywhere.
- Fix CI and review comments immediately without asking.
- Reply to every review thread before resolving it -- never silently resolve.
