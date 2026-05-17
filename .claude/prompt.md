You are working on repo-scaffold to set up agent-driven development.

Your task: Complete these tickets in order:

1. #84: Add issue detail query command
2. #85: Slug-based backlog directory structure per repo
3. #60: React framework support (with Vite locked in)
4. #73: Gin framework support

Steps for each ticket:
1. Run: gh issue view <issue-number> --repo blairg23/repo-scaffold --json body,title,labels
2. Parse the body. Extract Scope, Acceptance Criteria, Implementation Notes.
3. Create feature branch: git checkout -b feature/<ticket-title-slugified>
4. Implement per the ticket requirements
5. Run: poetry run pre-commit run --all-files
6. Run: poetry run pytest
7. Commit: git commit -m "feat(scaffold): <title> (#<issue-number>)"
8. Push: git push origin <branch-name>
9. Create PR: gh pr create --title "<title>" --body "Implements #<issue-number>"
10. Move to next ticket in the list

Work independently. Show progress as you go.

Start with #84.
