from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

import pytest

from repo_scaffold.backlog_ops import BacklogApplySummary
from repo_scaffold.create_ops import CreateSummary, SettingsCheckSummary
from repo_scaffold.cli import main
from repo_scaffold.delete_ops import DeleteSummary


def test_init_mode_supports_legacy_root_invocation(tmp_path: Path) -> None:
    out_dir = tmp_path / "demo"
    rc = main(
        [
            "--name",
            "demo",
            "--languages",
            "go,python",
            "--out",
            str(out_dir),
            "--dry-run",
        ]
    )
    assert rc == 0
    assert not out_dir.exists()


def test_root_help_shows_all_modes(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    stdout = capsys.readouterr().out
    assert "create" in stdout
    assert "init" in stdout
    assert "apply" in stdout
    assert "check" in stdout
    assert "delete" in stdout


def test_init_defaults_name_and_languages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key in ("GH_REPO", "GITHUB_REPOSITORY", "GITHUB_ORG", "GITHUB_REPO"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    out_dir = tmp_path / "default-init"
    rc = main(["init", "--out", str(out_dir), "--yes"])
    assert rc == 0
    cmd_dir = out_dir / "cmd"
    cmd_children = [path for path in cmd_dir.iterdir() if path.is_dir()]
    assert len(cmd_children) == 1
    default_name = cmd_children[0].name
    assert default_name.startswith("repo-scaffold-e2e-")
    assert len(default_name) == len("repo-scaffold-e2e-") + 14
    assert default_name.removeprefix("repo-scaffold-e2e-").isdigit()
    assert (cmd_children[0] / "main.go").exists()
    assert (out_dir / "pyproject.toml").exists()
    assert (out_dir / "web" / "package.json").exists()


def test_init_defaults_name_from_github_repo_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out_dir = tmp_path / "default-init-from-env"
    monkeypatch.setenv("GITHUB_REPO", "from-env-repo")
    rc = main(["init", "--out", str(out_dir), "--yes"])
    assert rc == 0
    assert (out_dir / "cmd" / "from-env-repo" / "main.go").exists()


def test_init_rejects_conflicting_overwrite_flags() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["init", "--name", "demo", "--languages", "go", "--yes", "--no"])
    assert exc.value.code == 2


def test_init_prompt_defaults_to_no(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out_dir = tmp_path / "demo"
    out_dir.mkdir(parents=True)
    readme = out_dir / "README.md"
    readme.write_text("keep me\n", encoding="utf-8")

    monkeypatch.setattr(
        "repo_scaffold.cli.sys.stdin", SimpleNamespace(isatty=lambda: True)
    )

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

    rc = main(
        [
            "apply",
            "templates",
            "--path",
            str(repo_dir),
            "--name",
            "repo",
            "--owner",
            "acme",
            "--yes",
        ]
    )
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

    rc = main(
        ["apply", "ci", "--path", str(repo_dir), "--languages", "go", "--dry-run"]
    )
    assert rc == 0
    assert not (repo_dir / ".github" / "workflows" / "ci.yml").exists()
    stdout = capsys.readouterr().out
    assert (
        f"CREATE    {(repo_dir / '.github' / 'workflows' / 'ci.yml').as_posix()}"
        in stdout
    )


def test_apply_dependabot_infers_languages_from_repo(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)
    (repo_dir / "go.mod").write_text(
        "module example.com/repo\ngo 1.22\n", encoding="utf-8"
    )
    (repo_dir / "pyproject.toml").write_text(
        "[project]\nname='repo'\n", encoding="utf-8"
    )

    rc = main(["apply", "dependabot", "--path", str(repo_dir), "--low-noise", "--yes"])
    assert rc == 0
    dependabot = (repo_dir / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    assert 'package-ecosystem: "gomod"' in dependabot
    assert 'package-ecosystem: "pip"' in dependabot
    assert 'package-ecosystem: "npm"' not in dependabot


def test_apply_backlog_subcommand_delegates_to_backlog_ops(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)
    backlog = repo_dir / "backlog"
    backlog.mkdir(parents=True)
    (backlog / "issues.json").write_text('{"epics":[]}', encoding="utf-8")

    called: dict[str, object] = {}

    def _fake_apply_backlog(
        *,
        repo_dir: Path,
        repo: str,
        backlog_file: Path,
        dry_run: bool,
        project_number,
        project_title,
        project_owner,
        out,
        err,
    ):
        called["repo_dir"] = repo_dir
        called["repo"] = repo
        called["backlog_file"] = backlog_file
        called["dry_run"] = dry_run
        called["project_number"] = project_number
        called["project_title"] = project_title
        called["project_owner"] = project_owner
        return BacklogApplySummary(
            milestones_created=1,
            milestones_skipped=2,
            issues_created=3,
            issues_skipped=4,
            failures=0,
            epic_issues_created=1,
            epic_issues_skipped=1,
            ticket_issues_created=2,
            ticket_issues_skipped=3,
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
    assert called["project_number"] is None
    assert called["project_title"] is None
    assert called["project_owner"] is None
    stdout = capsys.readouterr().out
    assert "epic issues created: 1" in stdout
    assert "epic issues skipped: 1" in stdout
    assert "ticket issues created: 2" in stdout
    assert "ticket issues skipped: 3" in stdout
    assert "issues created (total): 3" in stdout
    assert "issues skipped (total): 4" in stdout


def test_apply_backlog_defaults_to_local_backlog_when_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    local_backlog = workspace / "local" / "backlog"
    local_backlog.mkdir(parents=True)
    (local_backlog / "issues.json").write_text('{"epics":[]}', encoding="utf-8")

    repo_dir = workspace / "repo"
    repo_dir.mkdir(parents=True)
    (repo_dir / "backlog").mkdir(parents=True)
    (repo_dir / "backlog" / "issues.json").write_text('{"epics":[]}', encoding="utf-8")

    monkeypatch.chdir(workspace)

    called: dict[str, object] = {}

    def _fake_apply_backlog(
        *,
        repo_dir: Path,
        repo: str,
        backlog_file: Path,
        dry_run: bool,
        project_number,
        project_title,
        project_owner,
        out,
        err,
    ):
        called["repo_dir"] = repo_dir
        called["repo"] = repo
        called["backlog_file"] = backlog_file
        return BacklogApplySummary(
            milestones_created=0,
            milestones_skipped=0,
            issues_created=0,
            issues_skipped=0,
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
            "--dry-run",
        ]
    )
    assert rc == 0
    assert called["repo_dir"] == repo_dir
    assert called["repo"] == "acme/repo"
    assert called["backlog_file"] == local_backlog / "issues.json"


def test_apply_backlog_defaults_to_repo_backlog_when_local_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)

    repo_dir = workspace / "repo"
    repo_dir.mkdir(parents=True)
    repo_backlog = repo_dir / "backlog"
    repo_backlog.mkdir(parents=True)
    (repo_backlog / "issues.json").write_text('{"epics":[]}', encoding="utf-8")

    monkeypatch.chdir(workspace)

    called: dict[str, object] = {}

    def _fake_apply_backlog(
        *,
        repo_dir: Path,
        repo: str,
        backlog_file: Path,
        dry_run: bool,
        project_number,
        project_title,
        project_owner,
        out,
        err,
    ):
        called["repo_dir"] = repo_dir
        called["repo"] = repo
        called["backlog_file"] = backlog_file
        return BacklogApplySummary(
            milestones_created=0,
            milestones_skipped=0,
            issues_created=0,
            issues_skipped=0,
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
            "--dry-run",
        ]
    )
    assert rc == 0
    assert called["repo_dir"] == repo_dir
    assert called["repo"] == "acme/repo"
    assert called["backlog_file"] == repo_backlog / "issues.json"


def test_apply_backlog_auth_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)

    monkeypatch.setattr(
        "repo_scaffold.cli.resolve_authenticated_login", lambda _: "octocat"
    )
    monkeypatch.setattr(
        "repo_scaffold.cli.resolve_project_target_for_auth_check", lambda **_: None
    )

    rc = main(
        [
            "apply",
            "backlog",
            "--path",
            str(repo_dir),
            "--repo",
            "acme/repo",
            "--auth-check",
        ]
    )
    assert rc == 0
    stdout = capsys.readouterr().out
    assert "GitHub auth OK: octocat" in stdout


def test_apply_backlog_auth_check_with_project_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)

    monkeypatch.setattr(
        "repo_scaffold.cli.resolve_authenticated_login", lambda _: "octocat"
    )
    monkeypatch.setattr(
        "repo_scaffold.cli.resolve_project_target_for_auth_check",
        lambda **_: "acme/#1 (Roadmap)",
    )

    rc = main(
        [
            "apply",
            "backlog",
            "--path",
            str(repo_dir),
            "--repo",
            "acme/repo",
            "--project-number",
            "1",
            "--auth-check",
        ]
    )
    assert rc == 0
    stdout = capsys.readouterr().out
    assert "GitHub auth OK: octocat" in stdout
    assert "GitHub project access OK: acme/#1 (Roadmap)" in stdout


def test_apply_backlog_project_title_delegates_to_backlog_ops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)
    backlog = repo_dir / "backlog"
    backlog.mkdir(parents=True)
    (backlog / "issues.json").write_text('{"epics":[]}', encoding="utf-8")

    called: dict[str, object] = {}

    def _fake_apply_backlog(
        *,
        repo_dir: Path,
        repo: str,
        backlog_file: Path,
        dry_run: bool,
        project_number,
        project_title,
        project_owner,
        out,
        err,
    ):
        called["repo_dir"] = repo_dir
        called["repo"] = repo
        called["backlog_file"] = backlog_file
        called["dry_run"] = dry_run
        called["project_number"] = project_number
        called["project_title"] = project_title
        called["project_owner"] = project_owner
        return BacklogApplySummary(
            milestones_created=0,
            milestones_skipped=0,
            issues_created=0,
            issues_skipped=0,
            failures=0,
            project_created=True,
            project_items_added=2,
            project_items_skipped=1,
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
            "--project-title",
            "Roadmap",
            "--project-owner",
            "acme",
        ]
    )
    assert rc == 0
    assert called["project_number"] is None
    assert called["project_title"] == "Roadmap"
    assert called["project_owner"] == "acme"


def test_apply_backlog_with_project_defaults_title_from_repo_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)
    backlog = repo_dir / "backlog"
    backlog.mkdir(parents=True)
    (backlog / "issues.json").write_text('{"epics":[]}', encoding="utf-8")

    monkeypatch.delenv("GITHUB_PROJECT_TITLE", raising=False)
    monkeypatch.delenv("GITHUB_PROJECT_TITLE_TEMPLATE", raising=False)

    called: dict[str, object] = {}

    def _fake_apply_backlog(
        *,
        repo_dir: Path,
        repo: str,
        backlog_file: Path,
        dry_run: bool,
        project_number,
        project_title,
        project_owner,
        out,
        err,
    ):
        called["project_number"] = project_number
        called["project_title"] = project_title
        return BacklogApplySummary(
            milestones_created=0,
            milestones_skipped=0,
            issues_created=0,
            issues_skipped=0,
            failures=0,
            project_created=True,
            project_items_added=0,
            project_items_skipped=0,
        )

    monkeypatch.setattr("repo_scaffold.cli.apply_backlog", _fake_apply_backlog)

    rc = main(
        [
            "apply",
            "backlog",
            "--path",
            str(repo_dir),
            "--repo",
            "acme/repo-name",
            "--with-project",
            "--dry-run",
        ]
    )
    assert rc == 0
    assert called["project_number"] is None
    assert called["project_title"] == "repo-name Roadmap"


def test_apply_backlog_with_project_uses_env_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)
    backlog = repo_dir / "backlog"
    backlog.mkdir(parents=True)
    (backlog / "issues.json").write_text('{"epics":[]}', encoding="utf-8")

    monkeypatch.setenv("GITHUB_PROJECT_TITLE_TEMPLATE", "{repo} Delivery")
    monkeypatch.delenv("GITHUB_PROJECT_TITLE", raising=False)

    called: dict[str, object] = {}

    def _fake_apply_backlog(
        *,
        repo_dir: Path,
        repo: str,
        backlog_file: Path,
        dry_run: bool,
        project_number,
        project_title,
        project_owner,
        out,
        err,
    ):
        called["project_title"] = project_title
        return BacklogApplySummary(
            milestones_created=0,
            milestones_skipped=0,
            issues_created=0,
            issues_skipped=0,
            failures=0,
            project_created=True,
            project_items_added=0,
            project_items_skipped=0,
        )

    monkeypatch.setattr("repo_scaffold.cli.apply_backlog", _fake_apply_backlog)

    rc = main(
        [
            "apply",
            "backlog",
            "--path",
            str(repo_dir),
            "--repo",
            "acme/repo-name",
            "--with-project",
            "--dry-run",
        ]
    )
    assert rc == 0
    assert called["project_title"] == "repo-name Delivery"


def test_apply_backlog_resolves_repo_from_dotenv_when_repo_omitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    (workspace / ".env").write_text(
        "GITHUB_ORG=acme\nGITHUB_REPO=from-dotenv\n", encoding="utf-8"
    )

    repo_dir = workspace / "repo"
    repo_dir.mkdir(parents=True)
    backlog = repo_dir / "backlog"
    backlog.mkdir(parents=True)
    (backlog / "issues.json").write_text('{"epics":[]}', encoding="utf-8")

    for key in ("GH_REPO", "GITHUB_REPOSITORY", "GITHUB_ORG", "GITHUB_REPO"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(workspace)

    called: dict[str, object] = {}

    def _fake_apply_backlog(
        *,
        repo_dir: Path,
        repo: str,
        backlog_file: Path,
        dry_run: bool,
        project_number,
        project_title,
        project_owner,
        out,
        err,
    ):
        called["repo"] = repo
        return BacklogApplySummary(
            milestones_created=0,
            milestones_skipped=0,
            issues_created=0,
            issues_skipped=0,
            failures=0,
        )

    monkeypatch.setattr("repo_scaffold.cli.apply_backlog", _fake_apply_backlog)

    rc = main(
        [
            "apply",
            "backlog",
            "--path",
            str(repo_dir),
            "--file",
            "backlog/issues.json",
            "--dry-run",
        ]
    )
    assert rc == 0
    assert called["repo"] == "acme/from-dotenv"


def test_apply_rules_resolves_repo_from_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    (workspace / ".env").write_text("GH_REPO=acme/rules-repo\n", encoding="utf-8")

    for key in ("GH_REPO", "GITHUB_REPOSITORY", "GITHUB_ORG", "GITHUB_REPO"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(workspace)

    called: dict[str, object] = {}

    def _fake_apply_repository_settings(
        *, repo_dir: Path, repo: str, dry_run: bool, out, warn
    ):
        called["repo_dir"] = repo_dir
        called["repo"] = repo
        called["dry_run"] = dry_run
        out(f"{'[dry-run] ' if dry_run else ''}apply repository settings: {repo}")

    monkeypatch.setattr(
        "repo_scaffold.cli.apply_repository_settings", _fake_apply_repository_settings
    )

    rc = main(["apply", "rules"])
    assert rc == 0
    assert called["repo_dir"] == workspace
    assert called["repo"] == "acme/rules-repo"
    assert called["dry_run"] is True
    stdout = capsys.readouterr().out
    assert "apply repository settings: acme/rules-repo" in stdout
    assert "settings planned: True" in stdout


def test_apply_rules_dry_run_does_not_execute(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    called: dict[str, object] = {}

    def _fake_apply_repository_settings(
        *, repo_dir: Path, repo: str, dry_run: bool, out, warn
    ):
        called["repo"] = repo
        called["dry_run"] = dry_run

    monkeypatch.setattr(
        "repo_scaffold.cli.apply_repository_settings", _fake_apply_repository_settings
    )

    rc = main(["apply", "rules", "--repo", "acme/repo", "--apply", "--dry-run"])
    assert rc == 0
    assert called["repo"] == "acme/repo"
    assert called["dry_run"] is True
    stdout = capsys.readouterr().out
    assert "settings planned: True" in stdout


def test_check_rules_resolves_repo_from_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    (workspace / ".env").write_text("GH_REPO=acme/check-repo\n", encoding="utf-8")

    for key in ("GH_REPO", "GITHUB_REPOSITORY", "GITHUB_ORG", "GITHUB_REPO"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(workspace)

    called: dict[str, object] = {}

    def _fake_check_repository_settings(*, repo_dir: Path, repo: str, out):
        called["repo_dir"] = repo_dir
        called["repo"] = repo
        out(f"check repository settings: {repo}")
        return SettingsCheckSummary(
            repo=repo,
            passed=8,
            failed=0,
            skipped=0,
            drifts=(),
        )

    monkeypatch.setattr(
        "repo_scaffold.cli.check_repository_settings", _fake_check_repository_settings
    )

    rc = main(["check", "rules"])
    assert rc == 0
    assert called["repo_dir"] == workspace
    assert called["repo"] == "acme/check-repo"
    stdout = capsys.readouterr().out
    assert "check repository settings: acme/check-repo" in stdout
    assert "checks passed: 8" in stdout
    assert "checks failed: 0" in stdout


def test_check_rules_returns_nonzero_when_drift_found(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _fake_check_repository_settings(*, repo_dir: Path, repo: str, out):
        out("DRIFT merge settings: allow_merge_commit expected False got True")
        return SettingsCheckSummary(
            repo=repo,
            passed=5,
            failed=2,
            skipped=1,
            drifts=(
                "merge settings: allow_merge_commit expected False got True",
                "managed default-branch ruleset: managed default-branch ruleset missing",
            ),
        )

    monkeypatch.setattr(
        "repo_scaffold.cli.check_repository_settings", _fake_check_repository_settings
    )

    rc = main(["check", "rules", "--repo", "acme/repo"])
    assert rc == 1
    stdout = capsys.readouterr().out
    assert "checks failed: 2" in stdout
    assert "drift items: 2" in stdout


def test_create_subcommand_delegates_to_create_ops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)

    called: dict[str, object] = {}

    def _fake_create_repository(
        *,
        repo_dir: Path,
        repo,
        owner,
        name,
        visibility,
        apply_settings,
        dry_run,
        out,
        err,
    ):
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


def test_create_auto_inits_default_path_from_github_repo_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    monkeypatch.chdir(workspace)
    monkeypatch.setenv("GITHUB_ORG", "acme")
    monkeypatch.setenv("GITHUB_REPO", "repo-from-env")

    called: dict[str, object] = {}

    def _fake_create_repository(
        *,
        repo_dir: Path,
        repo,
        owner,
        name,
        visibility,
        apply_settings,
        dry_run,
        out,
        err,
    ):
        called["repo_dir"] = repo_dir
        called["repo"] = repo
        called["owner"] = owner
        called["name"] = name
        called["visibility"] = visibility
        called["apply_settings"] = apply_settings
        called["dry_run"] = dry_run
        return CreateSummary(
            repo="acme/repo-from-env",
            repo_created=True,
            pushed=True,
            settings_applied=True,
            failures=0,
        )

    monkeypatch.setattr("repo_scaffold.cli.create_repository", _fake_create_repository)

    rc = main(["create"])
    assert rc == 0
    expected_repo_dir = Path("out") / "repo-from-env"
    assert called["repo_dir"] == expected_repo_dir
    assert (workspace / expected_repo_dir).exists()
    assert (workspace / expected_repo_dir / "README.md").exists()
    assert called["repo"] is None
    assert called["owner"] is None
    assert called["name"] is None


def test_create_repo_flag_name_takes_precedence_for_auto_init_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    monkeypatch.chdir(workspace)
    monkeypatch.setenv("GITHUB_ORG", "acme")
    monkeypatch.setenv("GITHUB_REPO", "repo-from-env")

    called: dict[str, object] = {}

    def _fake_create_repository(
        *,
        repo_dir: Path,
        repo,
        owner,
        name,
        visibility,
        apply_settings,
        dry_run,
        out,
        err,
    ):
        called["repo_dir"] = repo_dir
        called["repo"] = repo
        return CreateSummary(
            repo="acme/repo-from-flag",
            repo_created=True,
            pushed=True,
            settings_applied=True,
            failures=0,
        )

    monkeypatch.setattr("repo_scaffold.cli.create_repository", _fake_create_repository)

    rc = main(["create", "--repo", "acme/repo-from-flag"])
    assert rc == 0
    expected_repo_dir = Path("out") / "repo-from-flag"
    assert called["repo"] == "acme/repo-from-flag"
    assert called["repo_dir"] == expected_repo_dir
    assert (workspace / expected_repo_dir).exists()
    assert (workspace / expected_repo_dir / "README.md").exists()


def test_delete_subcommand_delegates_to_delete_ops(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    called: dict[str, object] = {}

    def _fake_delete_repositories(
        *,
        owner: str | None,
        prefix: str,
        exact_names,
        include_local: bool,
        delete_local_only: bool,
        local_roots,
        apply: bool,
        assume_yes: bool,
        prompt,
        is_tty: bool,
        cwd: Path,
        out,
        err,
    ) -> DeleteSummary:
        called["owner"] = owner
        called["prefix"] = prefix
        called["exact_names"] = tuple(exact_names)
        called["include_local"] = include_local
        called["delete_local_only"] = delete_local_only
        called["local_roots"] = tuple(local_roots)
        called["apply"] = apply
        called["assume_yes"] = assume_yes
        called["is_tty"] = is_tty
        called["cwd"] = cwd
        out("matched output")
        return DeleteSummary(
            owner="acme",
            remote_matched=2,
            remote_deleted=2,
            remote_skipped=0,
            remote_failures=0,
            local_matched=1,
            local_deleted=1,
            local_skipped=0,
            local_failures=0,
        )

    monkeypatch.setattr(
        "repo_scaffold.cli.delete_repositories", _fake_delete_repositories
    )

    rc = main(
        [
            "delete",
            "--owner",
            "acme",
            "--exact",
            "repo-scaffold-e2e",
            "--exact",
            "repo-scaffold-e2e-20260311001924",
            "--cleanup",
            "--local-root",
            "/tmp",
            "--apply",
            "--yes",
        ]
    )
    assert rc == 0
    assert called["owner"] == "acme"
    assert called["prefix"] == "repo-scaffold-e2e"
    assert called["exact_names"] == (
        "repo-scaffold-e2e",
        "repo-scaffold-e2e-20260311001924",
    )
    assert called["include_local"] is True
    assert called["delete_local_only"] is False
    assert called["local_roots"] == ("/tmp",)
    assert called["apply"] is True
    assert called["assume_yes"] is True
    assert isinstance(called["cwd"], Path)
    stdout = capsys.readouterr().out
    assert "Summary:" in stdout
    assert "owner: acme" in stdout
    assert "remote matched: 2" in stdout
    assert "local matched: 1" in stdout


def test_delete_subcommand_delete_local_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: dict[str, object] = {}

    def _fake_delete_repositories(
        *,
        owner: str | None,
        prefix: str,
        exact_names,
        include_local: bool,
        delete_local_only: bool,
        local_roots,
        apply: bool,
        assume_yes: bool,
        prompt,
        is_tty: bool,
        cwd: Path,
        out,
        err,
    ) -> DeleteSummary:
        called["owner"] = owner
        called["include_local"] = include_local
        called["delete_local_only"] = delete_local_only
        return DeleteSummary(
            owner=None,
            remote_matched=0,
            remote_deleted=0,
            remote_skipped=0,
            remote_failures=0,
            local_matched=1,
            local_deleted=0,
            local_skipped=1,
            local_failures=0,
        )

    monkeypatch.setattr(
        "repo_scaffold.cli.delete_repositories", _fake_delete_repositories
    )
    rc = main(["delete", "--local-only"])
    assert rc == 0
    assert called["owner"] is None
    assert called["include_local"] is True
    assert called["delete_local_only"] is True
