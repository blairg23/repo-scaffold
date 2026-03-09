from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import repo_scaffold.generator as generator_module
from repo_scaffold.generator import ScaffoldConfig, generate_scaffold


def _tree_hash(root: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
        mode = oct(path.stat().st_mode & 0o777).encode("ascii")
        h.update(mode)
        h.update(b"\0")
    return h.hexdigest()


def test_generate_full_scaffold(tmp_path: Path) -> None:
    out_dir = tmp_path / "demo"
    cfg = ScaffoldConfig(
        name="demo",
        languages=("go", "python", "react"),
        owner="acme",
        license_id="apache-2.0",
        out_dir=out_dir,
    )

    generate_scaffold(cfg)

    expected_files = [
        ".github/pull_request_template.md",
        ".github/CODEOWNERS",
        ".github/ISSUE_TEMPLATE/epic.md",
        ".github/ISSUE_TEMPLATE/ticket.md",
        ".github/ISSUE_TEMPLATE/config.yml",
        ".github/workflows/ci.yml",
        ".github/workflows/codeql.yml",
        ".github/dependabot.yml",
        "backlog/issues.json",
        "docs/requirements.md",
        "docs/api-v1.md",
        "scripts/create-issues.sh",
        "scripts/gh-apply-settings.sh",
        "scripts/gh-create-project.sh",
        "README.md",
        ".env.example",
        "LICENSE",
        ".gitignore",
        ".editorconfig",
        "Makefile",
        "go.mod",
        "cmd/demo/main.go",
        "internal/.gitkeep",
        "pyproject.toml",
        "src/demo/__init__.py",
        "web/package.json",
        "web/index.html",
        "web/src/main.jsx",
        "web/src/App.jsx",
        "web/src/styles.css",
        "web/vite.config.js",
    ]

    for rel in expected_files:
        assert (out_dir / rel).exists(), f"missing: {rel}"

    assert "module github.com/acme/demo" in (out_dir / "go.mod").read_text(encoding="utf-8")
    assert "package-ecosystem: \"gomod\"" in (out_dir / ".github/dependabot.yml").read_text(
        encoding="utf-8"
    )
    assert "package-ecosystem: \"pip\"" in (out_dir / ".github/dependabot.yml").read_text(
        encoding="utf-8"
    )
    assert "package-ecosystem: \"npm\"" in (out_dir / ".github/dependabot.yml").read_text(
        encoding="utf-8"
    )
    assert "language:" in (out_dir / ".github/workflows/codeql.yml").read_text(encoding="utf-8")
    apply_settings_script = (out_dir / "scripts/gh-apply-settings.sh").read_text(encoding="utf-8")
    assert '"is_template": true' in apply_settings_script
    assert ".env" in apply_settings_script
    assert "github_token" in apply_settings_script
    assert "/vulnerability-alerts" in apply_settings_script
    assert "/automated-security-fixes" in apply_settings_script

    create_project_script = (out_dir / "scripts/gh-create-project.sh").read_text(encoding="utf-8")
    assert ".env" in create_project_script
    assert "github_token" in create_project_script
    assert 'VISIBILITY="${3:-public}"' in create_project_script
    assert "git rev-parse --is-inside-work-tree" in create_project_script
    assert "git init" in create_project_script
    assert "--push" in create_project_script
    generated_readme = (out_dir / "README.md").read_text(encoding="utf-8")
    assert "## Backlog bootstrap" in generated_readme
    assert "./scripts/create-issues.sh" in generated_readme
    assert "--auth-check" in generated_readme
    assert "Optional project integration" in generated_readme
    assert "--project-title" in generated_readme
    assert "## PR workflow" in generated_readme
    assert "gh pr create" in generated_readme
    assert ".env.example" in generated_readme
    assert "--dry-run" in generated_readme
    assert "Dependabot alerts + automated security updates" in generated_readme
    env_example = (out_dir / ".env.example").read_text(encoding="utf-8")
    assert "GH_TOKEN=" in env_example
    assert "GITHUB_ORG=" in env_example
    assert "GH_REPO=" in env_example

    create_issues_script = (out_dir / "scripts/create-issues.sh").read_text(encoding="utf-8")
    assert "--repo owner/repo" in create_issues_script
    assert "--dry-run" in create_issues_script
    assert "--auth-check" in create_issues_script
    assert "--project-number" in create_issues_script
    assert "--project-title" in create_issues_script
    assert "--project-owner" in create_issues_script
    assert "gh project item-add" in create_issues_script
    assert "gh project create" in create_issues_script
    assert "gh auth status" in create_issues_script
    assert "gh api /user" in create_issues_script
    assert "python3" in create_issues_script
    assert "--search" in create_issues_script
    assert "--body-file" in create_issues_script
    assert ".env" in create_issues_script
    assert "github_token" in create_issues_script
    assert "issue_number_exact" in create_issues_script
    assert "ensure_labels_exist" in create_issues_script
    assert "/labels?per_page=100" in create_issues_script
    assert "Parent epic: #" in create_issues_script

    backlog = json.loads((out_dir / "backlog/issues.json").read_text(encoding="utf-8"))
    assert backlog == {"epics": []}
    assert "!.env.example" in (out_dir / ".gitignore").read_text(encoding="utf-8")

    apply_mode = (out_dir / "scripts/gh-apply-settings.sh").stat().st_mode & 0o111
    create_mode = (out_dir / "scripts/gh-create-project.sh").stat().st_mode & 0o111
    backlog_mode = (out_dir / "scripts/create-issues.sh").stat().st_mode & 0o111
    assert apply_mode != 0
    assert create_mode != 0
    assert backlog_mode != 0


def test_generation_is_deterministic(tmp_path: Path) -> None:
    cfg_a = ScaffoldConfig(
        name="demo",
        languages=("go", "python", "react"),
        owner="acme",
        license_id="apache-2.0",
        out_dir=tmp_path / "a",
    )
    cfg_b = ScaffoldConfig(
        name="demo",
        languages=("go", "python", "react"),
        owner="acme",
        license_id="apache-2.0",
        out_dir=tmp_path / "b",
    )

    generate_scaffold(cfg_a)
    generate_scaffold(cfg_b)

    assert _tree_hash(cfg_a.out_dir) == _tree_hash(cfg_b.out_dir)


def test_react_only_codeql_is_noop(tmp_path: Path) -> None:
    cfg = ScaffoldConfig(
        name="frontend",
        languages=("react",),
        owner=None,
        license_id="apache-2.0",
        out_dir=tmp_path / "frontend",
    )

    generate_scaffold(cfg)

    codeql = (cfg.out_dir / ".github/workflows/codeql.yml").read_text(encoding="utf-8")
    assert "noop:" in codeql
    assert "Analyze (${{ matrix.language }})" not in codeql

    dependabot = (cfg.out_dir / ".github/dependabot.yml").read_text(encoding="utf-8")
    assert 'package-ecosystem: "github-actions"' in dependabot
    assert 'package-ecosystem: "npm"' in dependabot
    assert 'package-ecosystem: "gomod"' not in dependabot
    assert 'package-ecosystem: "pip"' not in dependabot


def test_generate_scaffold_uses_custom_markdown_templates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    template_root = tmp_path / "templates"
    (template_root / "github" / "ISSUE_TEMPLATE").mkdir(parents=True)

    pr_template = "## Custom PR Template\n\n- custom\n"
    epic_template = "## Custom Epic Template\n"
    ticket_template = "## Custom Ticket Template\n"

    (template_root / "github" / "pull_request_template.md").write_text(
        pr_template, encoding="utf-8"
    )
    (template_root / "github" / "ISSUE_TEMPLATE" / "epic.md").write_text(
        epic_template, encoding="utf-8"
    )
    (template_root / "github" / "ISSUE_TEMPLATE" / "ticket.md").write_text(
        ticket_template, encoding="utf-8"
    )

    monkeypatch.setattr(generator_module, "TEMPLATE_ROOT", template_root)

    cfg = ScaffoldConfig(
        name="custom",
        languages=("go",),
        owner="acme",
        license_id="apache-2.0",
        out_dir=tmp_path / "custom",
    )
    generate_scaffold(cfg)

    assert (
        cfg.out_dir / ".github" / "pull_request_template.md"
    ).read_text(encoding="utf-8") == pr_template
    assert (cfg.out_dir / ".github" / "ISSUE_TEMPLATE" / "epic.md").read_text(
        encoding="utf-8"
    ) == epic_template
    assert (cfg.out_dir / ".github" / "ISSUE_TEMPLATE" / "ticket.md").read_text(
        encoding="utf-8"
    ) == ticket_template
