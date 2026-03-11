from __future__ import annotations

import hashlib
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
        ".pre-commit-config.yaml",
        ".github/pull_request_template.md",
        ".github/CODEOWNERS",
        ".github/ISSUE_TEMPLATE/epic.md",
        ".github/ISSUE_TEMPLATE/ticket.md",
        ".github/ISSUE_TEMPLATE/config.yml",
        ".github/workflows/ci.yml",
        ".github/workflows/codeql.yml",
        ".github/dependabot.yml",
        "docs/requirements.md",
        "docs/api-v1.md",
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
        "tox.ini",
        "src/demo/__init__.py",
        "web/package.json",
        "web/eslint.config.js",
        "web/index.html",
        "web/src/main.jsx",
        "web/src/App.jsx",
        "web/src/styles.css",
        "web/vite.config.js",
    ]

    for rel in expected_files:
        assert (out_dir / rel).exists(), f"missing: {rel}"

    assert "module github.com/acme/demo" in (out_dir / "go.mod").read_text(
        encoding="utf-8"
    )
    assert 'package-ecosystem: "gomod"' in (
        out_dir / ".github/dependabot.yml"
    ).read_text(encoding="utf-8")
    assert 'package-ecosystem: "pip"' in (out_dir / ".github/dependabot.yml").read_text(
        encoding="utf-8"
    )
    assert 'package-ecosystem: "npm"' in (out_dir / ".github/dependabot.yml").read_text(
        encoding="utf-8"
    )
    ci_yaml = (out_dir / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "name: Install pre-commit" in ci_yaml
    assert "pre-commit run --all-files --show-diff-on-failure" in ci_yaml
    assert "tox-env: [lint, type, test]" in ci_yaml
    assert "- name: Install tox" in ci_yaml
    assert "run: tox -e ${{ matrix.tox-env }}" in ci_yaml
    assert "language:" in (out_dir / ".github/workflows/codeql.yml").read_text(
        encoding="utf-8"
    )
    generated_readme = (out_dir / "README.md").read_text(encoding="utf-8")
    assert "Created by [repo-scaffold]" in generated_readme
    assert "## Setup" in generated_readme
    assert "## Git hooks" in generated_readme
    assert "pre-commit install" in generated_readme
    assert "## Day-to-day commands" in generated_readme
    assert "pip install -e .[dev]" in generated_readme
    assert "black --check ." in generated_readme
    assert "mypy src" in generated_readme
    assert "go test ./..." in generated_readme
    assert "npm run build" in generated_readme
    assert "tox -e format" in generated_readme
    assert "tox -e lint,type,test" in generated_readme
    assert "make typecheck" in generated_readme
    assert "## Backlog bootstrap" not in generated_readme
    assert "## GitHub token permissions" not in generated_readme
    assert "./scripts/create-issues.sh" not in generated_readme
    env_example = (out_dir / ".env.example").read_text(encoding="utf-8")
    assert "GH_TOKEN=" in env_example
    assert "GITHUB_ORG=" in env_example
    assert "GH_REPO=" in env_example
    assert "GITHUB_PROJECT_TITLE=" in env_example
    assert "GITHUB_PROJECT_TITLE_TEMPLATE=" in env_example
    pyproject = (out_dir / "pyproject.toml").read_text(encoding="utf-8")
    assert "black>=" in pyproject
    assert "mypy>=" in pyproject
    assert "pre-commit>=" in pyproject
    assert "tox>=" in pyproject
    assert "[tool.mypy]" in pyproject
    assert "[tool.tox]" not in pyproject
    tox_ini = (out_dir / "tox.ini").read_text(encoding="utf-8")
    assert "envlist = lint,type,test" in tox_ini
    assert "black --check src tests" in tox_ini
    assert "ruff check src tests" in tox_ini
    assert "[testenv:format]" in tox_ini
    assert "black src tests" in tox_ini
    assert "ruff check src tests --fix" in tox_ini
    assert "pytest -q {posargs:tests}" in tox_ini
    web_package = (out_dir / "web/package.json").read_text(encoding="utf-8")
    assert '"lint": "eslint ."' in web_package
    assert '"eslint": "^9.21.0"' in web_package
    gitignore = (out_dir / ".gitignore").read_text(encoding="utf-8")
    assert "!.env.example" in gitignore
    assert ".pre-commit-cache/" in gitignore
    assert not (out_dir / "backlog").exists()
    assert not (out_dir / "scripts").exists()


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

    assert (cfg.out_dir / ".github" / "pull_request_template.md").read_text(
        encoding="utf-8"
    ) == pr_template
    assert (cfg.out_dir / ".github" / "ISSUE_TEMPLATE" / "epic.md").read_text(
        encoding="utf-8"
    ) == epic_template
    assert (cfg.out_dir / ".github" / "ISSUE_TEMPLATE" / "ticket.md").read_text(
        encoding="utf-8"
    ) == ticket_template
