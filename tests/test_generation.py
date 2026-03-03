from __future__ import annotations

import hashlib
from pathlib import Path

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
        "docs/requirements.md",
        "docs/api-v1.md",
        "scripts/gh-apply-settings.sh",
        "scripts/gh-create-project.sh",
        "README.md",
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
    assert '"is_template": true' in (out_dir / "scripts/gh-apply-settings.sh").read_text(
        encoding="utf-8"
    )

    apply_mode = (out_dir / "scripts/gh-apply-settings.sh").stat().st_mode & 0o111
    create_mode = (out_dir / "scripts/gh-create-project.sh").stat().st_mode & 0o111
    assert apply_mode != 0
    assert create_mode != 0


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
