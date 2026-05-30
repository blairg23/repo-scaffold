# Pre-Implementation Requirements

Before writing any code, verify every item below. These are non-negotiable and apply to every agent working in every repo.

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

# repo-scaffold Agent Context

## What this repo does

`repo-scaffold` is a CLI toolkit for creating, scaffolding, and managing GitHub repositories. It generates project structure, applies settings, manages backlogs, and interacts with GitHub Projects — all via the GitHub REST and GraphQL APIs. No `gh` CLI required.

## Key commands

```bash
poetry run repo-scaffold issue view --repo OWNER/REPO --issue-number N [--json]
poetry run repo-scaffold project items --project-title "TITLE" --limit 40
poetry run repo-scaffold create --repo OWNER/REPO --visibility public --path /path/to/code
poetry run repo-scaffold apply backlog --repo OWNER/REPO --path .
poetry run repo-scaffold check rules --repo OWNER/REPO
poetry run repo-scaffold init --name NAME --languages go,gin,python,react --owner OWNER --out /path --yes
```

## Auth

`GH_TOKEN` in `.env`. Loaded automatically. Never requires `gh` on PATH.

## Testing

```bash
poetry run pytest
poetry run tox -e precommit   # full gate: format + lint + type + coverage
```

## Architecture notes

- `github_api.py` — all GitHub API calls via `urllib` (stdlib only, no CLI)
- `backlog_ops.py` — issue/milestone/label/project operations
- `create_ops.py` — repo creation, settings, git init
- `project_ops.py` — GitHub Projects v2 management
- `generator.py` — scaffold file generation (go, gin, python, react)
- `cli.py` — argparse entry point

## Still missing (file tickets, do NOT work around)
- `repo-scaffold pr list`
- `repo-scaffold pr create`
- `repo-scaffold pr comment`
- `repo-scaffold pr resolve-thread`
