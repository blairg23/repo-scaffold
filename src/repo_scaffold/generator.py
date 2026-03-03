from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

ALLOWED_LANGUAGES = ("go", "python", "react")
SUPPORTED_LICENSE = "apache-2.0"


@dataclass(frozen=True)
class ScaffoldConfig:
    name: str
    languages: tuple[str, ...]
    owner: str | None
    license_id: str
    out_dir: Path
    overwrite: bool = False


def parse_language_csv(raw: str) -> tuple[str, ...]:
    parts = [part.strip().lower() for part in raw.split(",")]
    if not parts or any(not part for part in parts):
        raise ValueError(
            "--languages must be a comma-separated list containing only: go, python, react"
        )

    unknown = [part for part in parts if part not in ALLOWED_LANGUAGES]
    if unknown:
        bad = ", ".join(sorted(set(unknown)))
        raise ValueError(f"Unknown language value(s): {bad}. Allowed: go, python, react")

    selected = set(parts)
    return tuple(lang for lang in ALLOWED_LANGUAGES if lang in selected)


def _write_file(path: Path, content: str, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = content.rstrip("\n") + "\n"
    path.write_text(normalized, encoding="utf-8", newline="\n")
    if executable:
        current = path.stat().st_mode
        path.chmod(current | 0o755)


def _ensure_license_supported(license_id: str) -> None:
    if license_id != SUPPORTED_LICENSE:
        raise ValueError(f"Unsupported license '{license_id}'. Only '{SUPPORTED_LICENSE}' is supported.")


def _render_codeowners(owner: str | None) -> str:
    if owner:
        handle = owner if owner.startswith("@") else f"@{owner}"
        return f"* {handle}\n"
    return "# Replace @TODO-owner with a real reviewer\n* @TODO-owner\n"


def _render_pr_template() -> str:
    return """## Summary

- 

## Motivation

- 

## Changes

- 

## Validation

- [ ] Local tests pass
- [ ] CI checks are green
- [ ] Docs updated if behavior changed

## Risks

- 

## Checklist

- [ ] Linked issue/epic
- [ ] Added tests (or documented why not)
- [ ] No secrets or credentials committed
"""


def _render_issue_epic_template() -> str:
    return """---
name: Epic
about: Track a multi-ticket initiative
title: "[EPIC] "
labels: ["epic", "needs-triage"]
assignees: []
---

## Problem statement

## Goals

## Non-goals

## Scope

## Milestones

- [ ] Milestone 1
- [ ] Milestone 2

## Success metrics

## Risks / unknowns

## Linked tickets
"""


def _render_issue_ticket_template() -> str:
    return """---
name: Ticket
about: Describe one implementation task
title: "[Ticket] "
labels: ["needs-triage"]
assignees: []
---

## Summary

## Acceptance criteria

- [ ] 

## Implementation notes

## Test plan

## Definition of done

- [ ] Tests added/updated
- [ ] Documentation updated
"""


def _render_issue_config(owner: str | None, name: str) -> str:
    if owner:
        security_url = f"https://github.com/{owner}/{name}/security/advisories/new"
    else:
        security_url = "https://github.com/OWNER/REPO/security/advisories/new"
    return f"""blank_issues_enabled: false
contact_links:
  - name: Security report
    url: {security_url}
    about: Please privately disclose security issues through GitHub Security Advisories.
"""


def _render_ci_yaml(languages: Iterable[str]) -> str:
    joined = ",".join(languages)
    return f"""name: CI

on:
  pull_request:
  push:
    branches: [main]

permissions:
  contents: read

env:
  LANGUAGES: "{joined}"

jobs:
  go:
    if: contains(env.LANGUAGES, 'go')
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-go@v5
        with:
          go-version-file: go.mod
      - name: Check gofmt
        run: |
          unformatted="$(gofmt -l .)"
          if [ -n "$unformatted" ]; then
            echo "Files not formatted with gofmt:"
            echo "$unformatted"
            exit 1
          fi
      - name: Run tests
        run: go test ./...
      - name: Run golangci-lint
        uses: golangci/golangci-lint-action@v6
        with:
          version: v1.61

  python:
    if: contains(env.LANGUAGES, 'python')
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -e .[dev]
      - name: Ruff
        run: ruff check .
      - name: Pytest
        run: pytest

  react:
    if: contains(env.LANGUAGES, 'react')
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: web
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: lts/*
          cache: npm
          cache-dependency-path: web/package-lock.json
      - name: Ensure lockfile exists
        run: |
          if [ ! -f package-lock.json ]; then
            npm install --package-lock-only
          fi
      - name: Install dependencies
        run: npm ci
      - name: Build
        run: npm run build
      - name: Test (optional)
        run: |
          if npm run | grep -qE '^[[:space:]]+test'; then
            npm test -- --watch=false
          else
            echo "No test script defined; skipping tests."
          fi
"""


def _render_codeql_yaml(languages: Iterable[str]) -> str:
    codeql_langs = [lang for lang in languages if lang in {"go", "python"}]

    if not codeql_langs:
        return """name: CodeQL

on:
  schedule:
    - cron: "0 6 * * 1"
  workflow_dispatch:

jobs:
  noop:
    runs-on: ubuntu-latest
    steps:
      - name: Skip
        run: echo "No Go/Python selected; CodeQL scan is skipped."
"""

    matrix_lines = "\n".join(f"          - {lang}" for lang in codeql_langs)
    return f"""name: CodeQL

on:
  pull_request:
  push:
    branches: [main]
  schedule:
    - cron: "0 6 * * 1"

permissions:
  actions: read
  contents: read
  security-events: write

jobs:
  analyze:
    name: Analyze (${{{{ matrix.language }}}})
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        language:
{matrix_lines}
    steps:
      - uses: actions/checkout@v4
      - uses: github/codeql-action/init@v3
        with:
          languages: ${{{{ matrix.language }}}}
      - uses: github/codeql-action/autobuild@v3
      - uses: github/codeql-action/analyze@v3
"""


def _render_dependabot_yaml(languages: Iterable[str]) -> str:
    entries: list[str] = []

    def block(ecosystem: str, directory: str, group_name: str) -> str:
        return f"""  - package-ecosystem: \"{ecosystem}\"
    directory: \"{directory}\"
    schedule:
      interval: weekly
    open-pull-requests-limit: 5
    groups:
      {group_name}:
        patterns:
          - \"*\"
"""

    entries.append(block("github-actions", "/", "github-actions"))

    selected = set(languages)
    if "react" in selected:
        entries.append(block("npm", "/web", "npm"))
    if "go" in selected:
        entries.append(block("gomod", "/", "gomod"))
    if "python" in selected:
        entries.append(block("pip", "/", "pip"))
        entries.append(block("uv", "/", "uv"))

    return "version: 2\nupdates:\n" + "".join(entries)


def _render_gitignore(languages: Iterable[str]) -> str:
    selected = set(languages)
    lines = [
        "# OS",
        ".DS_Store",
        "Thumbs.db",
        "",
        "# Editors / IDE",
        ".idea/",
        ".vscode/",
        "*.swp",
        "*.swo",
        "",
        "# Environment",
        ".env",
        ".env.*",
        "",
        "# Logs",
        "*.log",
        "",
    ]

    if "go" in selected:
        lines.extend(
            [
                "# Go",
                "bin/",
                "*.test",
                "coverage.out",
                "",
            ]
        )

    if "python" in selected:
        lines.extend(
            [
                "# Python",
                "__pycache__/",
                "*.py[cod]",
                ".pytest_cache/",
                ".ruff_cache/",
                ".mypy_cache/",
                ".venv/",
                "venv/",
                "dist/",
                "build/",
                "*.egg-info/",
                "",
            ]
        )

    if "react" in selected:
        lines.extend(
            [
                "# React / Node",
                "web/node_modules/",
                "web/dist/",
                "web/.vite/",
                "",
            ]
        )

    return "\n".join(lines).rstrip() + "\n"


def _render_editorconfig() -> str:
    return """root = true

[*]
charset = utf-8
end_of_line = lf
indent_style = space
indent_size = 2
insert_final_newline = true
trim_trailing_whitespace = true

[Makefile]
indent_style = tab
"""


def _render_makefile() -> str:
    return """.PHONY: lint test build

lint:
\t@if [ -f go.mod ]; then \\
\t\techo "Linting Go"; \\
\t\tif command -v golangci-lint >/dev/null 2>&1; then golangci-lint run ./...; else echo "golangci-lint not installed; skipping"; fi; \\
\tfi
\t@if [ -f pyproject.toml ]; then \\
\t\techo "Linting Python"; \\
\t\truff check .; \\
\tfi
\t@if [ -f web/package.json ]; then \\
\t\techo "Linting React"; \\
\t\tcd web && npm run lint --if-present; \\
\tfi

test:
\t@if [ -f go.mod ]; then go test ./...; fi
\t@if [ -f pyproject.toml ]; then pytest; fi
\t@if [ -f web/package.json ]; then cd web && npm test --if-present; fi

build:
\t@if [ -f go.mod ]; then go build ./...; fi
\t@if [ -f pyproject.toml ]; then python -m build; fi
\t@if [ -f web/package.json ]; then cd web && npm run build; fi
"""


def _render_repo_readme(config: ScaffoldConfig) -> str:
    langs = ", ".join(config.languages)
    owner_part = config.owner or "OWNER"
    return f"""# {config.name}

Repository scaffold generated by `repo-scaffold`.

## Enabled languages

{langs}

## Quick start

1. Create a GitHub repository named `{config.name}`.
2. Run `scripts/gh-apply-settings.sh {owner_part} {config.name}` after pushing.
3. Open issues with `.github/ISSUE_TEMPLATE/` forms and submit PRs with the PR template.

## Development

Use `make lint`, `make test`, and `make build` where applicable.
"""


def _apache_2_license() -> str:
    return """Apache License
                           Version 2.0, January 2004
                        http://www.apache.org/licenses/

   TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION

   1. Definitions.

      \"License\" shall mean the terms and conditions for use, reproduction,
      and distribution as defined by Sections 1 through 9 of this document.

      \"Licensor\" shall mean the copyright owner or entity authorized by
      the copyright owner that is granting the License.

      \"Legal Entity\" shall mean the union of the acting entity and all
      other entities that control, are controlled by, or are under common
      control with that entity. For the purposes of this definition,
      \"control\" means (i) the power, direct or indirect, to cause the
      direction or management of such entity, whether by contract or
      otherwise, or (ii) ownership of fifty percent (50%) or more of the
      outstanding shares, or (iii) beneficial ownership of such entity.

      \"You\" (or \"Your\") shall mean an individual or Legal Entity
      exercising permissions granted by this License.

      \"Source\" form shall mean the preferred form for making modifications,
      including but not limited to software source code, documentation
      source, and configuration files.

      \"Object\" form shall mean any form resulting from mechanical
      transformation or translation of a Source form, including but
      not limited to compiled object code, generated documentation,
      and conversions to other media types.

      \"Work\" shall mean the work of authorship, whether in Source or
      Object form, made available under the License, as indicated by a
      copyright notice that is included in or attached to the work
      (an example is provided in the Appendix below).

      \"Derivative Works\" shall mean any work, whether in Source or Object
      form, that is based on (or derived from) the Work and for which the
      editorial revisions, annotations, elaborations, or other modifications
      represent, as a whole, an original work of authorship. For the purposes
      of this License, Derivative Works shall not include works that remain
      separable from, or merely link (or bind by name) to the interfaces of,
      the Work and Derivative Works thereof.

      \"Contribution\" shall mean any work of authorship, including
      the original version of the Work and any modifications or additions
      to that Work or Derivative Works thereof, that is intentionally
      submitted to Licensor for inclusion in the Work by the copyright owner
      or by an individual or Legal Entity authorized to submit on behalf of
      the copyright owner. For the purposes of this definition, \"submitted\"
      means any form of electronic, verbal, or written communication sent
      to the Licensor or its representatives, including but not limited to
      communication on electronic mailing lists, source code control systems,
      and issue tracking systems that are managed by, or on behalf of, the
      Licensor for the purpose of discussing and improving the Work, but
      excluding communication that is conspicuously marked or otherwise
      designated in writing by the copyright owner as \"Not a Contribution.\"

      \"Contributor\" shall mean Licensor and any individual or Legal Entity
      on behalf of whom a Contribution has been received by Licensor and
      subsequently incorporated within the Work.

   2. Grant of Copyright License. Subject to the terms and conditions of
      this License, each Contributor hereby grants to You a perpetual,
      worldwide, non-exclusive, no-charge, royalty-free, irrevocable
      copyright license to reproduce, prepare Derivative Works of,
      publicly display, publicly perform, sublicense, and distribute the
      Work and such Derivative Works in Source or Object form.

   3. Grant of Patent License. Subject to the terms and conditions of
      this License, each Contributor hereby grants to You a perpetual,
      worldwide, non-exclusive, no-charge, royalty-free, irrevocable
      (except as stated in this section) patent license to make, have made,
      use, offer to sell, sell, import, and otherwise transfer the Work,
      where such license applies only to those patent claims licensable
      by such Contributor that are necessarily infringed by their
      Contribution(s) alone or by combination of their Contribution(s)
      with the Work to which such Contribution(s) was submitted. If You
      institute patent litigation against any entity (including a
      cross-claim or counterclaim in a lawsuit) alleging that the Work
      or a Contribution incorporated within the Work constitutes direct
      or contributory patent infringement, then any patent licenses
      granted to You under this License for that Work shall terminate
      as of the date such litigation is filed.

   4. Redistribution. You may reproduce and distribute copies of the
      Work or Derivative Works thereof in any medium, with or without
      modifications, and in Source or Object form, provided that You
      meet the following conditions:

      (a) You must give any other recipients of the Work or
          Derivative Works a copy of this License; and

      (b) You must cause any modified files to carry prominent notices
          stating that You changed the files; and

      (c) You must retain, in the Source form of any Derivative Works
          that You distribute, all copyright, patent, trademark, and
          attribution notices from the Source form of the Work,
          excluding those notices that do not pertain to any part of
          the Derivative Works; and

      (d) If the Work includes a \"NOTICE\" text file as part of its
          distribution, then any Derivative Works that You distribute must
          include a readable copy of the attribution notices contained
          within such NOTICE file, excluding those notices that do not
          pertain to any part of the Derivative Works, in at least one
          of the following places: within a NOTICE text file distributed
          as part of the Derivative Works; within the Source form or
          documentation, if provided along with the Derivative Works; or,
          within a display generated by the Derivative Works, if and
          wherever such third-party notices normally appear. The contents
          of the NOTICE file are for informational purposes only and
          do not modify the License. You may add Your own attribution
          notices within Derivative Works that You distribute, alongside
          or as an addendum to the NOTICE text from the Work, provided
          that such additional attribution notices cannot be construed
          as modifying the License.

      You may add Your own copyright statement to Your modifications and
      may provide additional or different license terms and conditions
      for use, reproduction, or distribution of Your modifications, or
      for any such Derivative Works as a whole, provided Your use,
      reproduction, and distribution of the Work otherwise complies with
      the conditions stated in this License.

   5. Submission of Contributions. Unless You explicitly state otherwise,
      any Contribution intentionally submitted for inclusion in the Work
      by You to the Licensor shall be under the terms and conditions of
      this License, without any additional terms or conditions.
      Notwithstanding the above, nothing herein shall supersede or modify
      the terms of any separate license agreement you may have executed
      with Licensor regarding such Contributions.

   6. Trademarks. This License does not grant permission to use the trade
      names, trademarks, service marks, or product names of the Licensor,
      except as required for reasonable and customary use in describing the
      origin of the Work and reproducing the content of the NOTICE file.

   7. Disclaimer of Warranty. Unless required by applicable law or
      agreed to in writing, Licensor provides the Work (and each
      Contributor provides its Contributions) on an \"AS IS\" BASIS,
      WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
      implied, including, without limitation, any warranties or conditions
      of TITLE, NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A
      PARTICULAR PURPOSE. You are solely responsible for determining the
      appropriateness of using or redistributing the Work and assume any
      risks associated with Your exercise of permissions under this License.

   8. Limitation of Liability. In no event and under no legal theory,
      whether in tort (including negligence), contract, or otherwise,
      unless required by applicable law (such as deliberate and grossly
      negligent acts) or agreed to in writing, shall any Contributor be
      liable to You for damages, including any direct, indirect, special,
      incidental, or consequential damages of any character arising as a
      result of this License or out of the use or inability to use the
      Work (including but not limited to damages for loss of goodwill,
      work stoppage, computer failure or malfunction, or any and all
      other commercial damages or losses), even if such Contributor
      has been advised of the possibility of such damages.

   9. Accepting Warranty or Additional Liability. While redistributing
      the Work or Derivative Works thereof, You may choose to offer,
      and charge a fee for, acceptance of support, warranty, indemnity,
      or other liability obligations and/or rights consistent with this
      License. However, in accepting such obligations, You may act only
      on Your own behalf and on Your sole responsibility, not on behalf
      of any other Contributor, and only if You agree to indemnify,
      defend, and hold each Contributor harmless for any liability
      incurred by, or claims asserted against, such Contributor by reason
      of your accepting any such warranty or additional liability.

   END OF TERMS AND CONDITIONS
"""


def _render_gh_apply_settings_script() -> str:
    return """#!/usr/bin/env bash
set -euo pipefail

OWNER="${1:-}"
REPO="${2:-}"

if [ -z "$OWNER" ] || [ -z "$REPO" ]; then
  echo "Usage: $0 <owner> <repo>" >&2
  exit 1
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "GitHub CLI (gh) is required." >&2
  exit 1
fi

if ! gh auth status >/dev/null 2>&1; then
  echo "Authenticate first: gh auth login" >&2
  exit 1
fi

echo "Applying repository-level merge settings..."
gh api \
  --method PATCH \
  -H "Accept: application/vnd.github+json" \
  "/repos/$OWNER/$REPO" \
  --input - >/dev/null <<'JSON'
{
  "allow_squash_merge": true,
  "allow_merge_commit": false,
  "allow_rebase_merge": false,
  "delete_branch_on_merge": true,
  "allow_auto_merge": true,
  "is_template": true
}
JSON

echo "Applying main branch protection..."
gh api \
  --method PUT \
  -H "Accept: application/vnd.github+json" \
  "/repos/$OWNER/$REPO/branches/main/protection" \
  --input - >/dev/null <<'JSON'
{
  "required_status_checks": {
    "strict": false,
    "contexts": []
  },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "dismiss_stale_reviews": false,
    "require_code_owner_reviews": false,
    "required_approving_review_count": 1,
    "require_last_push_approval": false
  },
  "restrictions": null,
  "required_linear_history": true,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "block_creations": false,
  "required_conversation_resolution": true,
  "lock_branch": false,
  "allow_fork_syncing": true
}
JSON

echo "Repository settings applied for $OWNER/$REPO"
"""


def _render_gh_create_project_script(name: str, owner: str | None) -> str:
    default_owner = owner or "<owner>"
    return f"""#!/usr/bin/env bash
set -euo pipefail

OWNER="${{1:-{default_owner}}}"
REPO="${{2:-{name}}}"
VISIBILITY="${{3:-private}}"

if [ "$OWNER" = "<owner>" ]; then
  echo "Usage: $0 <owner> [repo] [private|public|internal]" >&2
  exit 1
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "GitHub CLI (gh) is required." >&2
  exit 1
fi

echo "Creating repository $OWNER/$REPO ($VISIBILITY)..."
gh repo create "$OWNER/$REPO" --"$VISIBILITY" --source . --remote origin

SCRIPT_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
"$SCRIPT_DIR/gh-apply-settings.sh" "$OWNER" "$REPO"

echo "Repository created and settings applied."
"""


def _render_go_main(name: str) -> str:
    return f"""package main

import "fmt"

func main() {{
    fmt.Println("{name} scaffold")
}}
"""


def _render_go_mod(name: str, owner: str | None) -> str:
    if owner:
        module = f"github.com/{owner}/{name}"
    else:
        module = f"example.com/{name}"

    return f"""module {module}

go 1.22
"""


def _render_python_pyproject(name: str) -> str:
    return f"""[build-system]
requires = ["setuptools>=69", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "{name}"
version = "0.1.0"
description = "{name} service"
requires-python = ">=3.10"
dependencies = []

[project.optional-dependencies]
dev = [
  "pytest>=8.0.0",
  "ruff>=0.6.0",
]

[tool.setuptools]
package-dir = {{"" = "src"}}

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
addopts = "-q -s"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py310"

[tool.ruff.lint]
select = ["E", "F", "I", "UP"]
"""


def _render_python_init(name: str) -> str:
    return f"""\"\"\"{name} package.\"\"\"
"""


def _render_react_package_json(name: str) -> str:
    safe_name = name.replace("_", "-")
    return f"""{{
  "name": "{safe_name}-web",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {{
    "dev": "vite",
    "build": "vite build",
    "preview": "vite preview"
  }},
  "dependencies": {{
    "react": "^18.3.1",
    "react-dom": "^18.3.1"
  }},
  "devDependencies": {{
    "@vitejs/plugin-react": "^4.3.2",
    "vite": "^5.4.8"
  }}
}}
"""


def _render_react_index_html(name: str) -> str:
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>{name}</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.jsx"></script>
  </body>
</html>
"""


def _render_react_main_jsx() -> str:
    return """import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './styles.css'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
"""


def _render_react_app_jsx(name: str) -> str:
    return f"""export default function App() {{
  return (
    <main className="app">
      <h1>{name} scaffold</h1>
      <p>Edit <code>web/src/App.jsx</code> to start building.</p>
    </main>
  )
}}
"""


def _render_react_styles() -> str:
    return """* {
  box-sizing: border-box;
}

:root {
  color: #0f172a;
  background: #f8fafc;
  font-family: "Segoe UI", Tahoma, Geneva, Verdana, sans-serif;
  line-height: 1.5;
}

body {
  margin: 0;
}

.app {
  min-height: 100vh;
  display: grid;
  place-content: center;
  gap: 0.5rem;
  text-align: center;
  padding: 2rem;
}

h1 {
  margin: 0;
  font-size: clamp(2rem, 4vw, 3rem);
}
"""


def _render_react_vite_config() -> str:
    return """import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
})
"""


def generate_scaffold(config: ScaffoldConfig) -> None:
    _ensure_license_supported(config.license_id)

    files: list[tuple[Path, str, bool]] = [
        (config.out_dir / ".github" / "pull_request_template.md", _render_pr_template(), False),
        (config.out_dir / ".github" / "CODEOWNERS", _render_codeowners(config.owner), False),
        (config.out_dir / ".github" / "ISSUE_TEMPLATE" / "epic.md", _render_issue_epic_template(), False),
        (
            config.out_dir / ".github" / "ISSUE_TEMPLATE" / "ticket.md",
            _render_issue_ticket_template(),
            False,
        ),
        (
            config.out_dir / ".github" / "ISSUE_TEMPLATE" / "config.yml",
            _render_issue_config(config.owner, config.name),
            False,
        ),
        (config.out_dir / ".github" / "workflows" / "ci.yml", _render_ci_yaml(config.languages), False),
        (
            config.out_dir / ".github" / "workflows" / "codeql.yml",
            _render_codeql_yaml(config.languages),
            False,
        ),
        (
            config.out_dir / ".github" / "dependabot.yml",
            _render_dependabot_yaml(config.languages),
            False,
        ),
        (config.out_dir / "docs" / "requirements.md", "# Requirements\n\nTBD\n", False),
        (config.out_dir / "docs" / "api-v1.md", "# API v1\n\nTBD\n", False),
        (
            config.out_dir / "scripts" / "gh-apply-settings.sh",
            _render_gh_apply_settings_script(),
            True,
        ),
        (
            config.out_dir / "scripts" / "gh-create-project.sh",
            _render_gh_create_project_script(config.name, config.owner),
            True,
        ),
        (config.out_dir / "README.md", _render_repo_readme(config), False),
        (config.out_dir / "LICENSE", _apache_2_license(), False),
        (config.out_dir / ".gitignore", _render_gitignore(config.languages), False),
        (config.out_dir / ".editorconfig", _render_editorconfig(), False),
        (config.out_dir / "Makefile", _render_makefile(), False),
    ]

    selected = set(config.languages)
    if "go" in selected:
        files.extend(
            [
                (config.out_dir / "go.mod", _render_go_mod(config.name, config.owner), False),
                (
                    config.out_dir / "cmd" / config.name / "main.go",
                    _render_go_main(config.name),
                    False,
                ),
                (config.out_dir / "internal" / ".gitkeep", "", False),
            ]
        )

    if "python" in selected:
        files.extend(
            [
                (config.out_dir / "pyproject.toml", _render_python_pyproject(config.name), False),
                (
                    config.out_dir / "src" / config.name / "__init__.py",
                    _render_python_init(config.name),
                    False,
                ),
                (config.out_dir / "tests" / ".gitkeep", "", False),
            ]
        )

    if "react" in selected:
        files.extend(
            [
                (
                    config.out_dir / "web" / "package.json",
                    _render_react_package_json(config.name),
                    False,
                ),
                (
                    config.out_dir / "web" / "index.html",
                    _render_react_index_html(config.name),
                    False,
                ),
                (config.out_dir / "web" / "src" / "main.jsx", _render_react_main_jsx(), False),
                (
                    config.out_dir / "web" / "src" / "App.jsx",
                    _render_react_app_jsx(config.name),
                    False,
                ),
                (
                    config.out_dir / "web" / "src" / "styles.css",
                    _render_react_styles(),
                    False,
                ),
                (
                    config.out_dir / "web" / "vite.config.js",
                    _render_react_vite_config(),
                    False,
                ),
            ]
        )

    for path, content, executable in files:
        _write_file(path, content, executable=executable)


def remove_tree(path: Path) -> None:
    if not path.exists():
        return
    for child in sorted(path.glob("**/*"), reverse=True):
        if child.is_file() or child.is_symlink():
            child.unlink()
        elif child.is_dir():
            child.rmdir()
    path.rmdir()


def default_output_path(name: str) -> Path:
    return Path("out") / name
