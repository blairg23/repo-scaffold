# Workflow

## Purpose
This project is built with a "human + spec + implementation" workflow:
- Product Owner provides taste, priorities, and real-world constraints.
- Architect/Maintainer provides architecture, specs, ticket slicing, and risk reduction.
- Implementer ships per-ticket inside the repo and opens PRs.

## Roles and responsibilities

### Product Owner
- Owns product taste: UX feel, naming, and "this is worth shipping".
- Owns priorities: chooses the next ticket, approves scope changes.
- Validates real-world behavior on the target machine(s).

### Architect/Maintainer
- Converts goals into requirements and acceptance criteria.
- Slices epics into PR-sized tickets (1 ticket = 1 PR).
- Performs threat modeling and designs safe defaults.
- Maintains locked docs: requirements, API contract, UX principles.

### Implementer
- Implements one ticket at a time.
- Adds/updates tests for all behavioral changes.
- Updates docs when behavior or interfaces change.
- Opens PRs referencing the issue and provides a runnable test plan.

## Golden rules (non-negotiable)
- One issue = one PR.
- Analyze-first: destructive operations require an analyze/dry-run step.
- Default safe: no destructive propagation unless explicitly configured.
- UI always shows the underlying CLI command(s).
- No "fake green": tests must be real and meaningful.
- Keep tickets small: target 1 to 3 hours per PR.

## Standard ticket structure
Each ticket must include:
- Goal (1 sentence)
- Scope (bullets)
- Out of scope (bullets)
- Acceptance criteria (Given/When/Then)
- Test plan (how to verify locally + in CI)
- Docs impact (what to update)

## PR standards

See [AGENTS.md](../AGENTS.md) for the canonical branch naming and PR title formats. Summary:

- Branch name: `type/NNN-short-description` (e.g., `feat/221-align-docs`)
- PR title: `type(scope): description (#NNN)` (e.g., `docs(agents): align agent docs (#221)`)
- Required: tests updated/added
- Required: screenshots for UI changes
- Required: `poetry run tox -e precommit` passes locally

## Decision locks
If a PR changes a locked decision, it must:
- Update the relevant doc(s) in `docs/`
- Include a short "Decision change" section in the PR description

Locked docs:
- `docs/requirements.md`
- `docs/api-v1.md`
- `docs/ui-notes.md` (when created)

## Daily rhythm
- Pick one ticket.
- Implementer opens PR.
- Product Owner reviews by running the test plan.
- Merge via squash once checks pass.

## Definition of done
A ticket is done when:
- Acceptance criteria pass
- Tests are added/updated
- CI is green
- Docs are updated if behavior changed
- Product Owner confirms the UX is not clunky
