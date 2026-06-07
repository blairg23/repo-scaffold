from __future__ import annotations

import hashlib
import sys
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


@pytest.mark.skipif(
    sys.platform == "win32", reason="chmod executable bits not supported on Windows"
)
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
        "AGENTS.md",
        ".env.example",
        ".claude/settings.local.json",
        "LICENSE",
        ".gitignore",
        ".editorconfig",
        "Makefile",
        "scripts/first_time_setup.sh",
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
    assert "pre-commit-hooks:" in ci_yaml
    assert "name: Install pre-commit" in ci_yaml
    assert "SKIP: tox-suite" in ci_yaml
    assert "pre-commit run --all-files --show-diff-on-failure" in ci_yaml
    assert "tox-env: [lint, type, coverage]" in ci_yaml
    assert "- name: Install tox" in ci_yaml
    assert "run: tox -e ${{ matrix.tox-env }}" in ci_yaml
    assert "Upload coverage.xml artifact" in ci_yaml
    assert "codecov/codecov-action@v5" in ci_yaml
    assert "CODECOV_TOKEN" in ci_yaml
    assert "language:" in (out_dir / ".github/workflows/codeql.yml").read_text(
        encoding="utf-8"
    )
    generated_readme = (out_dir / "README.md").read_text(encoding="utf-8")
    assert "[![codecov](" in generated_readme
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
    assert "tox -e precommit" in generated_readme
    assert "the hook exits non-zero" in generated_readme
    assert "tox -e lint,type,coverage" in generated_readme
    assert "tox -e coverage" in generated_readme
    assert "tox -e codecov-upload" in generated_readme
    assert "CODECOV_TOKEN" in generated_readme
    assert "already present in `.env`" in generated_readme
    assert "minimum coverage gate is 70%" in generated_readme
    assert "make typecheck" in generated_readme
    assert "## Repo-scaffold GitHub workflow" in generated_readme
    assert ".repo-scaffold/project.json" in generated_readme
    assert "AGENTS.md" in generated_readme
    assert "./scripts/first_time_setup.sh" in generated_readme
    assert "GH_TOKEN=<classic-PAT> gh project item-list" in generated_readme
    assert ".claude/settings.local.json" in generated_readme
    assert "## Backlog bootstrap" not in generated_readme
    assert "## GitHub token permissions" not in generated_readme
    assert "./scripts/create-issues.sh" not in generated_readme
    env_example = (out_dir / ".env.example").read_text(encoding="utf-8")
    assert "GH_TOKEN=" in env_example
    assert "GITHUB_ORG=" in env_example
    assert "GH_REPO=" in env_example
    assert f"GITHUB_PROJECT_TITLE={cfg.name} Roadmap" in env_example
    assert "GITHUB_PROJECT_TITLE=" in env_example
    assert "GITHUB_PROJECT_TITLE_TEMPLATE=" in env_example
    assert "GITHUB_TICKETS_DIR=" in env_example
    assert "export GH_PROJECT_TOKEN=<classic-PAT>" in env_example
    assert "CODECOV_TOKEN=" in env_example
    assert "read automatically by `tox -e codecov-upload`" in env_example
    claude_settings = (out_dir / ".claude" / "settings.local.json").read_text(
        encoding="utf-8"
    )
    assert '"GH_PROJECT_TOKEN": "<classic-PAT>"' in claude_settings
    agents_md = (out_dir / "AGENTS.md").read_text(encoding="utf-8")
    assert ".repo-scaffold/project.json" in agents_md
    assert "GH_REPO" in agents_md
    assert f"{cfg.name} Roadmap" in agents_md
    assert "ghp" in agents_md
    assert "GH_TOKEN=$GH_PROJECT_TOKEN gh ..." in agents_md
    pyproject = (out_dir / "pyproject.toml").read_text(encoding="utf-8")
    assert "black>=" in pyproject
    assert "mypy>=" in pyproject
    assert "pre-commit>=" in pyproject
    assert "tox>=" in pyproject
    assert "[tool.mypy]" in pyproject
    assert "[tool.coverage.run]" in pyproject
    assert "fail_under = 70" in pyproject
    assert "[tool.tox]" not in pyproject
    tox_ini = (out_dir / "tox.ini").read_text(encoding="utf-8")
    assert "envlist = lint,type,coverage" in tox_ini
    assert "black --check src tests" in tox_ini
    assert "ruff check src tests" in tox_ini
    assert "[testenv:format]" in tox_ini
    assert "[testenv:test-fast]" in tox_ini
    assert 'pytest -q -m "not e2e_github" {posargs:tests}' in tox_ini
    assert "[testenv:coverage]" in tox_ini
    assert "[testenv:coverage-fast]" in tox_ini
    assert "[testenv:codecov-upload]" in tox_ini
    assert "package = editable" in tox_ini
    assert "pytest-cov>=6" in tox_ini
    assert "codecov-cli>=11" in tox_ini
    assert "--cov=src --cov-branch" in tox_ini
    assert "COVERAGE_FILE={toxworkdir}/.coverage.{envname}" in tox_ini
    assert "[testenv:precommit]" in tox_ini
    assert "skip_install = true" in tox_ini
    assert "allowlist_externals =" in tox_ini
    assert "git" in tox_ini
    assert "PYTHONPATH={toxinidir}/src" in tox_ini
    assert "deps =" in tox_ini
    assert "{[testenv:format]commands}" in tox_ini
    assert "{[testenv:lint]commands}" in tox_ini
    assert "{[testenv:type]commands}" in tox_ini
    assert "{[testenv:coverage-fast]commands}" in tox_ini
    assert "git diff --exit-code -- src tests" in tox_ini
    assert "black src tests" in tox_ini
    assert "ruff check src tests --fix" in tox_ini
    assert "pytest -q {posargs:tests}" in tox_ini
    pre_commit = (out_dir / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    assert "id: tox-suite" in pre_commit
    assert "entry: tox" in pre_commit
    assert "language: python" in pre_commit
    assert "additional_dependencies:" in pre_commit
    assert "tox>=4.20.0" in pre_commit
    assert 'args: ["-e", "precommit", "-vv"]' in pre_commit
    assert "run tox suite (format, lint, type, coverage)" in pre_commit
    web_package = (out_dir / "web/package.json").read_text(encoding="utf-8")
    assert '"lint": "eslint ."' in web_package
    assert '"eslint": "^9.21.0"' in web_package
    gitignore = (out_dir / ".gitignore").read_text(encoding="utf-8")
    assert "!.env.example" in gitignore
    assert ".claude/settings.local.json" in gitignore
    assert ".repo-scaffold/" in gitignore
    assert "artifacts/" in gitignore
    assert ".coverage" in gitignore
    assert "coverage.xml" in gitignore
    assert "htmlcov/" in gitignore
    assert ".pre-commit-cache/" in gitignore
    assert not (out_dir / "backlog").exists()
    first_time_setup = out_dir / "scripts" / "first_time_setup.sh"
    assert first_time_setup.exists()
    if sys.platform != "win32":
        assert first_time_setup.stat().st_mode & 0o111
    script_text = first_time_setup.read_text(encoding="utf-8")
    assert "alias ghp='GH_TOKEN=$PROJECT_TOKEN gh'" in script_text
    assert "GH_TOKEN=$PROJECT_TOKEN gh project item-list" in script_text
    assert (
        "Repo-scaffold GH_TOKEN (leave blank to reuse the project token): "
        in script_text
    )
    assert (
        'upsert_env_line "$ENV_FILE" \'^GH_TOKEN=\' "GH_TOKEN=$REPO_TOKEN"'
        in script_text
    )


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


def test_react_only_codeql_scans_javascript(tmp_path: Path) -> None:
    cfg = ScaffoldConfig(
        name="frontend",
        languages=("react",),
        owner=None,
        license_id="apache-2.0",
        out_dir=tmp_path / "frontend",
    )

    generate_scaffold(cfg)

    codeql = (cfg.out_dir / ".github/workflows/codeql.yml").read_text(encoding="utf-8")
    assert "javascript-typescript" in codeql
    assert "Analyze (${{ matrix.language }})" in codeql

    dependabot = (cfg.out_dir / ".github/dependabot.yml").read_text(encoding="utf-8")
    assert 'package-ecosystem: "github-actions"' in dependabot
    assert 'package-ecosystem: "npm"' in dependabot
    assert 'package-ecosystem: "gomod"' not in dependabot
    assert 'package-ecosystem: "pip"' not in dependabot


def test_react_vite_scaffold_file_contents(tmp_path: Path) -> None:
    cfg = ScaffoldConfig(
        name="my-app",
        languages=("react",),
        owner="acme",
        license_id="apache-2.0",
        out_dir=tmp_path / "my-app",
    )
    generate_scaffold(cfg)

    pkg = (cfg.out_dir / "web" / "package.json").read_text(encoding="utf-8")
    assert '"name": "my-app-web"' in pkg
    assert '"dev": "vite"' in pkg
    assert '"build": "vite build"' in pkg
    assert '"preview": "vite preview"' in pkg
    assert '"react": "^18' in pkg
    assert '"react-dom": "^18' in pkg
    assert '"vite": "^5' in pkg
    assert '"@vitejs/plugin-react"' in pkg

    vite_cfg = (cfg.out_dir / "web" / "vite.config.js").read_text(encoding="utf-8")
    assert "defineConfig" in vite_cfg
    assert "@vitejs/plugin-react" in vite_cfg
    assert "plugins: [react()]" in vite_cfg

    main_jsx = (cfg.out_dir / "web" / "src" / "main.jsx").read_text(encoding="utf-8")
    assert "ReactDOM.createRoot" in main_jsx
    assert "React.StrictMode" in main_jsx
    assert "import App from './App'" in main_jsx

    app_jsx = (cfg.out_dir / "web" / "src" / "App.jsx").read_text(encoding="utf-8")
    assert "export default function App()" in app_jsx
    assert "my-app scaffold" in app_jsx

    index_html = (cfg.out_dir / "web" / "index.html").read_text(encoding="utf-8")
    assert '<div id="root">' in index_html
    assert 'src="/src/main.jsx"' in index_html
    assert "<title>my-app</title>" in index_html

    eslint_cfg = (cfg.out_dir / "web" / "eslint.config.js").read_text(encoding="utf-8")
    assert "eslint-plugin-react-hooks" in eslint_cfg
    assert "eslint-plugin-react-refresh" in eslint_cfg

    ci = (cfg.out_dir / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "react:" in ci
    assert "working-directory: web" in ci
    assert "contains(env.LANGUAGES" not in ci
    assert "hashFiles(" not in ci

    gitignore = (cfg.out_dir / ".gitignore").read_text(encoding="utf-8")
    assert "web/node_modules/" in gitignore
    assert "web/dist/" in gitignore
    assert "web/.vite/" in gitignore


def test_react_husky_pre_commit_generated(tmp_path: Path) -> None:
    cfg = ScaffoldConfig(
        name="my-app",
        languages=("react",),
        owner="acme",
        license_id="apache-2.0",
        out_dir=tmp_path / "my-app",
    )
    generate_scaffold(cfg)

    hook = cfg.out_dir / "web" / ".husky" / "pre-commit"
    assert hook.exists(), ".husky/pre-commit not generated"
    content = hook.read_text(encoding="utf-8")
    assert "lint-staged" in content

    pkg = (cfg.out_dir / "web" / "package.json").read_text(encoding="utf-8")
    assert '"prepare": "husky"' in pkg
    assert '"husky"' in pkg
    assert '"lint-staged"' in pkg
    assert '"prettier"' in pkg
    assert "lint-staged" in pkg


def test_build_ci_files_includes_husky_for_react(tmp_path: Path) -> None:
    from repo_scaffold.generator import build_ci_files

    files = build_ci_files(tmp_path, languages=("react",))
    rel_paths = {f.path.relative_to(tmp_path).as_posix() for f in files}
    assert "web/.husky/pre-commit" in rel_paths


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


def test_gin_scaffold_generates_expected_files(tmp_path: Path) -> None:
    out_dir = tmp_path / "myapi"
    cfg = ScaffoldConfig(
        name="myapi",
        languages=("gin",),
        owner="acme",
        license_id="apache-2.0",
        out_dir=out_dir,
    )

    generate_scaffold(cfg)

    expected_files = [
        "go.mod",
        "cmd/myapi/main.go",
        "routers/router.go",
        "handlers/health.go",
        "handlers/health_test.go",
    ]
    for rel in expected_files:
        assert (out_dir / rel).exists(), f"missing: {rel}"

    go_mod = (out_dir / "go.mod").read_text(encoding="utf-8")
    assert "gin-gonic/gin" in go_mod
    assert "module github.com/acme/myapi" in go_mod

    main_go = (out_dir / "cmd" / "myapi" / "main.go").read_text(encoding="utf-8")
    assert "routers.SetupRouter()" in main_go
    assert ":8080" in main_go
    assert "log.Fatal" in main_go

    router_go = (out_dir / "routers" / "router.go").read_text(encoding="utf-8")
    assert "gin.Default()" in router_go
    assert "handlers.HealthCheck" in router_go
    assert "/health" in router_go

    health_go = (out_dir / "handlers" / "health.go").read_text(encoding="utf-8")
    assert "HealthCheck" in health_go
    assert "gin.H{" in health_go
    assert '"status"' in health_go

    health_test_go = (out_dir / "handlers" / "health_test.go").read_text(
        encoding="utf-8"
    )
    assert "TestHealthCheck" in health_test_go
    assert "gin.TestMode" in health_test_go
    assert "httptest.NewRecorder()" in health_test_go

    ci_yaml = (out_dir / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "gin:" in ci_yaml
    assert "go mod tidy" in ci_yaml

    codeql_yaml = (out_dir / ".github" / "workflows" / "codeql.yml").read_text(
        encoding="utf-8"
    )
    assert "- go" in codeql_yaml
    assert "No Go/Python selected" not in codeql_yaml

    dependabot = (out_dir / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    assert 'package-ecosystem: "gomod"' in dependabot

    readme = (out_dir / "README.md").read_text(encoding="utf-8")
    assert "gin" in readme.lower()
    assert ":8080" in readme
    assert "/health" in readme


def test_detect_languages_gin(tmp_path: Path) -> None:
    go_mod = tmp_path / "go.mod"
    go_mod.write_text(
        "module example.com/myapi\n\ngo 1.22\n\nrequire (\n\tgithub.com/gin-gonic/gin v1.10.0\n)\n",
        encoding="utf-8",
    )

    detected = generator_module.detect_languages_from_repo(tmp_path)

    assert detected == ("gin",)


def test_detect_languages_plain_go(tmp_path: Path) -> None:
    go_mod = tmp_path / "go.mod"
    go_mod.write_text("module example.com/myapp\n\ngo 1.22\n", encoding="utf-8")

    detected = generator_module.detect_languages_from_repo(tmp_path)

    assert detected == ("go",)


def test_parse_language_csv_accepts_gin() -> None:
    from repo_scaffold.generator import parse_language_csv

    result = parse_language_csv("gin")
    assert result == ("gin",)


def test_parse_language_csv_rejects_unknown_still() -> None:
    from repo_scaffold.generator import parse_language_csv
    import pytest

    with pytest.raises(ValueError, match="Unknown language"):
        parse_language_csv("rust")
