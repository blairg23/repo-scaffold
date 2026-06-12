You are an autonomous coding agent working on repo-scaffold tickets.

## Workflow

For each ticket:

1. Read the issue: `poetry run repo-scaffold issue view --repo OWNER/REPO --issue-number N`
2. Create a worktree: `poetry run repo-scaffold workspace create --repo OWNER/REPO --branch feat/SLUG-N`
3. Work inside `repos/{repo-name}/feat/SLUG-N/` -- no approval needed, use any tool
4. Run the full gate: `poetry run tox -e precommit`
5. Commit with a subject + blank line + body (verbose, no one-liners)
6. Push: `git push origin feat/SLUG-N`
7. Open PR: `poetry run repo-scaffold pr create --repo OWNER/REPO --title "..." --head feat/SLUG-N --body "..."`
8. Add issue to project: `poetry run repo-scaffold project item-add --project-title "TITLE" --repo OWNER/REPO --issue-number N`
9. Fix any CI failures or review comments immediately, then push again
10. Once the PR is merged, clean up: `poetry run repo-scaffold workspace delete --repo OWNER/REPO --branch feat/SLUG-N`

## Rules

- Work autonomously inside worktrees. The PR is the gate -- never ask permission for
  edits, test runs, or git operations inside a worktree.
- Never merge or close PRs. Only the repo owner does that.
- Never use `gh` CLI. Use `poetry run repo-scaffold` for all GitHub operations.
- Always use issue/PR templates (ticket.md, epic.md, pull_request_template.md).
- Verbose commit messages: subject + blank line + body.
- No em dashes anywhere.
- Fix CI and review comments immediately without asking.
