from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

import pytest

from repo_scaffold.backlog_ops import BacklogApplySummary
from repo_scaffold.create_ops import CreateSummary
from repo_scaffold.cli import main


def test_init_mode_supports_legacy_root_invocation(tmp_path: Path) -> None:
    out_dir = tmp_path / "demo"
    rc = main(["--name", "demo", "--languages", "go,python", "--out", str(out_dir), "--dry-run"])
    assert rc == 0
    assert not out_dir.exists()


def test_init_rejects_conflicting_overwrite_flags() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["init", "--name", "demo", "--languages", "go", "--yes", "--no"])
    assert exc.value.code == 2


def test_init_prompt_defaults_to_no(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out_dir = tmp_path / "demo"
    out_dir.mkdir(parents=True)
    readme = out_dir / "README.md"
    readme.write_text("keep me\n", encoding="utf-8")

    monkeypatch.setattr("repo_scaffold.cli.sys.stdin", SimpleNamespace(isatty=lambda: True))

    prompts: list[str] = []

    def _prompt(message: str) -> str:
        prompts.append(message)
        return ""

    monkeypatch.setattr("builtins.input", _prompt)

    rc = main(["init", "--name", "demo", "--languages", "go", "--out", str(out_dir)])
    assert rc == 0
    assert readme.read_text(encoding="utf-8") == "keep me\n"
    assert prompts == [f"Overwrite {readme.as_posix()}? [y/N]"]


def test_apply_templates_only_writes_template_files(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)
    (repo_dir / "README.md").write_text("# Existing\n", encoding="utf-8")

    rc = main(["apply", "templates", "--path", str(repo_dir), "--name", "repo", "--owner", "acme", "--yes"])
    assert rc == 0
    assert (repo_dir / ".github" / "pull_request_template.md").exists()
    assert (repo_dir / ".github" / "ISSUE_TEMPLATE" / "ticket.md").exists()
    assert (repo_dir / ".github" / "ISSUE_TEMPLATE" / "epic.md").exists()
    assert (repo_dir / ".github" / "CODEOWNERS").exists()
    assert not (repo_dir / "go.mod").exists()
    assert (repo_dir / "README.md").read_text(encoding="utf-8") == "# Existing\n"


def test_apply_ci_requires_languages() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["apply", "ci", "--path", "."])
    assert exc.value.code == 2


def test_apply_ci_dry_run_prints_plan_without_writes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)

    rc = main(["apply", "ci", "--path", str(repo_dir), "--languages", "go", "--dry-run"])
    assert rc == 0
    assert not (repo_dir / ".github" / "workflows" / "ci.yml").exists()
    stdout = capsys.readouterr().out
    assert f"CREATE    {(repo_dir / '.github' / 'workflows' / 'ci.yml').as_posix()}" in stdout


def test_apply_dependabot_infers_languages_from_repo(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)
    (repo_dir / "go.mod").write_text("module example.com/repo\ngo 1.22\n", encoding="utf-8")
    (repo_dir / "pyproject.toml").write_text("[project]\nname='repo'\n", encoding="utf-8")

    rc = main(["apply", "dependabot", "--path", str(repo_dir), "--low-noise", "--yes"])
    assert rc == 0
    dependabot = (repo_dir / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    assert 'package-ecosystem: "gomod"' in dependabot
    assert 'package-ecosystem: "pip"' in dependabot
    assert 'package-ecosystem: "npm"' not in dependabot


def test_apply_backlog_subcommand_delegates_to_backlog_ops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)
    backlog = repo_dir / "backlog"
    backlog.mkdir(parents=True)
    (backlog / "issues.json").write_text('{"epics":[]}', encoding="utf-8")

    called: dict[str, object] = {}

    def _fake_apply_backlog(*, repo_dir: Path, repo: str, backlog_file: Path, dry_run: bool, out, err):
        called["repo_dir"] = repo_dir
        called["repo"] = repo
        called["backlog_file"] = backlog_file
        called["dry_run"] = dry_run
        return BacklogApplySummary(
            milestones_created=1,
            milestones_skipped=2,
            issues_created=3,
            issues_skipped=4,
            failures=0,
        )

    monkeypatch.setattr("repo_scaffold.cli.apply_backlog", _fake_apply_backlog)

    rc = main(
        [
            "apply",
            "backlog",
            "--path",
            str(repo_dir),
            "--repo",
            "acme/repo",
            "--file",
            "backlog/issues.json",
            "--dry-run",
        ]
    )
    assert rc == 0
    assert called["repo_dir"] == repo_dir
    assert called["repo"] == "acme/repo"
    assert called["backlog_file"] == backlog / "issues.json"
    assert called["dry_run"] is True


def test_apply_rules_dry_run_does_not_execute(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    called: dict[str, object] = {"applied": False}

    def _fake_apply_rules(repo: str) -> int:
        called["applied"] = True
        called["repo"] = repo
        return 0

    monkeypatch.setattr("repo_scaffold.cli._apply_rules", _fake_apply_rules)

    rc = main(["apply", "rules", "--repo", "acme/repo", "--apply", "--dry-run"])
    assert rc == 0
    assert called["applied"] is False
    stdout = capsys.readouterr().out
    assert "Recommended gh api commands:" in stdout


def test_create_subcommand_delegates_to_create_ops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)

    called: dict[str, object] = {}

    def _fake_create_repository(*, repo_dir: Path, repo, owner, name, visibility, apply_settings, dry_run, out, err):
        called["repo_dir"] = repo_dir
        called["repo"] = repo
        called["owner"] = owner
        called["name"] = name
        called["visibility"] = visibility
        called["apply_settings"] = apply_settings
        called["dry_run"] = dry_run
        return CreateSummary(
            repo="acme/repo",
            repo_created=True,
            pushed=True,
            settings_applied=True,
            failures=0,
        )

    monkeypatch.setattr("repo_scaffold.cli.create_repository", _fake_create_repository)

    rc = main(["create", "--path", str(repo_dir), "--repo", "acme/repo", "--dry-run"])
    assert rc == 0
    assert called["repo_dir"] == repo_dir
    assert called["repo"] == "acme/repo"
    assert called["owner"] is None
    assert called["name"] is None
    assert called["visibility"] == "public"
    assert called["apply_settings"] is True
    assert called["dry_run"] is True
