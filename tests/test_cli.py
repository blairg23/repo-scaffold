from __future__ import annotations

import subprocess
from types import SimpleNamespace
from pathlib import Path

import pytest
import repo_scaffold.cli as cli_module

from repo_scaffold.backlog_ops import BacklogApplySummary, IssueDetail
from repo_scaffold.create_ops import CreateSummary, SettingsCheckSummary
from repo_scaffold.cli import main
from repo_scaffold.delete_ops import DeleteSummary
from repo_scaffold.project_ops import (
    ProjectInfo,
    ProjectItemInfo,
    ProjectItemsSummary,
    ProjectListSummary,
    ProjectMutationSummary,
)


@pytest.fixture(autouse=True)
def _isolate_cli_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("GITHUB_TICKETS_DIR", raising=False)
    monkeypatch.delenv("github_tickets_dir", raising=False)
    monkeypatch.chdir(tmp_path)


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
    assert "import" in stdout
    assert "project" in stdout
    assert "issue" in stdout
    assert "pr" in stdout


def test_seed_env_from_dotenv_promotes_project_token_when_gh_token_is_placeholder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "GH_TOKEN=ghp_replace_with_real_token",
                "export GH_PROJECT_TOKEN=ghp_project_real_token",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GH_PROJECT_TOKEN", raising=False)

    cli_module._seed_env_from_dotenv(env_file)

    assert cli_module.os.environ["GH_TOKEN"] == "ghp_project_real_token"


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
    assert (repo_dir / ".github" / "workflows" / "validate-issue.yml").exists()
    assert not (repo_dir / ".github" / "workflows" / "codeql.yml").exists()
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


def test_apply_ci_with_repo_syncs_ruleset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)
    synced: list[dict] = []
    monkeypatch.setattr(
        "repo_scaffold.cli.sync_repository_ruleset",
        lambda **kwargs: synced.append(kwargs),
    )

    rc = main(
        [
            "apply",
            "ci",
            "--path",
            str(repo_dir),
            "--languages",
            "python",
            "--repo",
            "acme/repo",
        ]
    )

    assert rc == 0
    assert len(synced) == 1
    assert synced[0]["repo"] == "acme/repo"
    assert "python" in synced[0]["languages"]


def test_apply_ci_with_repo_sync_failure_warns_but_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)
    monkeypatch.setattr(
        "repo_scaffold.cli.sync_repository_ruleset",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("auth failed")),
    )

    rc = main(
        [
            "apply",
            "ci",
            "--path",
            str(repo_dir),
            "--languages",
            "go",
            "--repo",
            "acme/repo",
        ]
    )

    assert rc == 0
    err = capsys.readouterr().err
    assert "Warning: ruleset sync failed" in err
    assert "auth failed" in err


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


def test_import_backlog_writes_json_from_markdown(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    source_dir = tmp_path / "tickets"
    source_dir.mkdir(parents=True)
    repo_dir.mkdir(parents=True)
    (source_dir / "ticket.md").write_text(
        """## 🧾 Title

Document import flow

## 🧠 Summary

Turn markdown backlog notes into JSON.
""",
        encoding="utf-8",
    )

    rc = main(
        [
            "import",
            "backlog",
            "--path",
            str(repo_dir),
            "--source",
            str(source_dir),
            "--yes",
        ]
    )

    assert rc == 0
    output_file = repo_dir / "local" / "backlog.json"
    assert output_file.exists()
    payload = output_file.read_text(encoding="utf-8")
    assert "Document import flow" in payload


def test_import_backlog_dry_run_does_not_write(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    source_dir = tmp_path / "tickets"
    source_dir.mkdir(parents=True)
    repo_dir.mkdir(parents=True)
    (source_dir / "ticket.md").write_text(
        "# Dry Run Ticket\n\n## Summary\n\nPreview only.\n",
        encoding="utf-8",
    )

    rc = main(
        [
            "import",
            "backlog",
            "--path",
            str(repo_dir),
            "--source",
            str(source_dir),
            "--dry-run",
        ]
    )

    assert rc == 0
    assert not (repo_dir / "local" / "backlog.json").exists()


def test_import_backlog_uses_env_ticket_dir_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)
    source_dir = tmp_path / "shared-tickets"
    source_dir.mkdir(parents=True)
    (source_dir / "ticket.md").write_text(
        "# Env Ticket\n\n## Summary\n\nImported from env override.\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GITHUB_TICKETS_DIR", str(source_dir))

    rc = main(
        [
            "import",
            "backlog",
            "--path",
            str(repo_dir),
            "--yes",
        ]
    )

    assert rc == 0
    payload = (repo_dir / "local" / "backlog.json").read_text(encoding="utf-8")
    assert "Env Ticket" in payload


def test_import_backlog_uses_repo_local_dotenv_ticket_dir_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)
    source_dir = tmp_path / "shared-tickets"
    source_dir.mkdir(parents=True)
    (source_dir / "ticket.md").write_text(
        "# Repo Local Env Ticket\n\n## Summary\n\nImported from repo-local .env override.\n",
        encoding="utf-8",
    )
    (repo_dir / ".env").write_text(
        f"GITHUB_TICKETS_DIR={source_dir}\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(workspace)

    rc = main(
        [
            "import",
            "backlog",
            "--path",
            str(repo_dir),
            "--yes",
        ]
    )

    assert rc == 0
    payload = (repo_dir / "local" / "backlog.json").read_text(encoding="utf-8")
    assert "Repo Local Env Ticket" in payload


def test_import_backlog_uses_local_path_when_repo_provided(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    repo_dir = workspace / "target-repo"
    source_dir = tmp_path / "tickets"
    source_dir.mkdir(parents=True)
    repo_dir.mkdir(parents=True)
    (source_dir / "ticket.md").write_text(
        "# Local Path Ticket\n\n## Summary\n\nShould go to local path.\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(workspace)

    rc = main(
        [
            "import",
            "backlog",
            "--path",
            str(repo_dir),
            "--source",
            str(source_dir),
            "--repo",
            "acme/my-repo",
            "--yes",
        ]
    )

    assert rc == 0
    local_file = workspace / "local" / "acme" / "my-repo" / "backlog.json"
    assert local_file.exists()
    payload = local_file.read_text(encoding="utf-8")
    assert "Local Path Ticket" in payload


def test_import_backlog_rejects_invalid_repo_format(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)

    rc = main(
        [
            "import",
            "backlog",
            "--path",
            str(repo_dir),
            "--repo",
            "not-valid-repo",
            "--yes",
        ]
    )

    assert rc == 2


def test_import_backlog_defaults_to_local_when_no_repo(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    source_dir = tmp_path / "tickets"
    source_dir.mkdir(parents=True)
    repo_dir.mkdir(parents=True)
    (source_dir / "ticket.md").write_text(
        "# No Repo Ticket\n\n## Summary\n\nNo repo flag provided.\n",
        encoding="utf-8",
    )

    rc = main(
        [
            "import",
            "backlog",
            "--path",
            str(repo_dir),
            "--source",
            str(source_dir),
            "--yes",
        ]
    )

    assert rc == 0
    local_file = repo_dir / "local" / "backlog.json"
    assert local_file.exists()
    payload = local_file.read_text(encoding="utf-8")
    assert "No Repo Ticket" in payload


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


def test_apply_backlog_auto_imports_markdown_when_no_file_is_provided(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)
    source_dir = tmp_path / "tickets"
    source_dir.mkdir(parents=True)
    (source_dir / "ticket.md").write_text(
        "# Imported Ticket\n\n## Summary\n\nImported automatically before apply.\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GITHUB_TICKETS_DIR", str(source_dir))

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
        called["backlog_payload"] = backlog_file.read_text(encoding="utf-8")
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
    assert Path(str(called["backlog_file"])).exists()
    assert "Imported Ticket" in str(called["backlog_payload"])
    stdout = capsys.readouterr().out
    assert "[dry-run] auto-imported backlog JSON from" in stdout


def test_apply_backlog_auto_imports_markdown_from_env_ticket_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)
    source_dir = tmp_path / "workspace-artifacts" / "tickets"
    source_dir.mkdir(parents=True)
    (source_dir / "ticket.md").write_text(
        "# Imported From Env\n\n## Summary\n\nImported automatically from env.\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GITHUB_TICKETS_DIR", str(source_dir))

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
        called["backlog_payload"] = backlog_file.read_text(encoding="utf-8")
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
    assert "Imported From Env" in str(called["backlog_payload"])
    stdout = capsys.readouterr().out
    assert "[dry-run] auto-imported backlog JSON from" in stdout


def test_apply_backlog_accepts_positional_repo_ref(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)
    # Create local path so --repo resolution succeeds without --file
    slug_dir = workspace / "local" / "acme" / "repo"
    slug_dir.mkdir(parents=True)
    (slug_dir / "backlog.json").write_text('{"epics":[]}', encoding="utf-8")
    backlog = slug_dir
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
            "acme/repo",
            "--path",
            str(repo_dir),
            "--dry-run",
        ]
    )

    assert rc == 0
    assert called["repo"] == "acme/repo"
    assert called["backlog_file"] == backlog / "backlog.json"


def test_apply_backlog_errors_when_repo_set_but_slug_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    repo_dir = workspace / "repo"
    repo_dir.mkdir(parents=True)
    monkeypatch.chdir(workspace)

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
    assert rc != 0


def test_apply_backlog_uses_local_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)

    local_backlog = workspace / "local" / "acme" / "repo"
    local_backlog.mkdir(parents=True)
    (local_backlog / "backlog.json").write_text('{"epics":[]}', encoding="utf-8")

    repo_dir = workspace / "repo"
    repo_dir.mkdir(parents=True)

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
    assert called["backlog_file"] == local_backlog / "backlog.json"


def test_apply_backlog_raises_when_local_path_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)

    repo_dir = workspace / "repo"
    repo_dir.mkdir(parents=True)

    monkeypatch.chdir(workspace)
    monkeypatch.setattr("repo_scaffold.cli.apply_backlog", lambda **_: None)

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
    assert rc != 0


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
    slug_dir = tmp_path / "local" / "acme" / "repo"
    slug_dir.mkdir(parents=True)
    (slug_dir / "backlog.json").write_text('{"epics":[]}', encoding="utf-8")
    monkeypatch.chdir(tmp_path)

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


def test_project_sync_metadata_delegates_to_project_ops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)

    called: dict[str, object] = {}

    def _fake_sync_project_metadata(
        *,
        repo_dir: Path,
        owner: str | None,
        project_number: int | None,
        project_title: str | None,
        out,
    ) -> ProjectMutationSummary:
        called["repo_dir"] = repo_dir
        called["owner"] = owner
        called["project_number"] = project_number
        called["project_title"] = project_title
        return ProjectMutationSummary(
            action="sync-metadata",
            owner="acme",
            project_number=4,
            project_title="Roadmap",
            failures=0,
            changed=True,
            metadata_file=repo_dir / ".repo-scaffold" / "project.json",
        )

    monkeypatch.setattr(
        "repo_scaffold.cli.sync_project_metadata", _fake_sync_project_metadata
    )

    rc = main(
        [
            "project",
            "sync-metadata",
            "--path",
            str(repo_dir),
            "--project-owner",
            "acme",
            "--project-title",
            "Roadmap",
        ]
    )

    assert rc == 0
    assert called["repo_dir"] == repo_dir
    assert called["owner"] == "acme"
    assert called["project_number"] is None
    assert called["project_title"] == "Roadmap"


def test_apply_backlog_with_project_defaults_title_from_repo_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)
    slug_dir = tmp_path / "local" / "acme" / "repo-name"
    slug_dir.mkdir(parents=True)
    (slug_dir / "backlog.json").write_text('{"epics":[]}', encoding="utf-8")
    monkeypatch.chdir(tmp_path)

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
    slug_dir = tmp_path / "local" / "acme" / "repo-name"
    slug_dir.mkdir(parents=True)
    (slug_dir / "backlog.json").write_text('{"epics":[]}', encoding="utf-8")
    monkeypatch.chdir(tmp_path)

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
        *, repo_dir: Path, repo: str, dry_run: bool, out, warn, languages=None
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
        *, repo_dir: Path, repo: str, dry_run: bool, out, warn, languages=None
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


def test_apply_settings_forwards_languages(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    captured: dict[str, object] = {}

    def _fake_apply(*, repo_dir, repo, dry_run, out, warn, languages):
        captured["repo"] = repo
        captured["languages"] = languages
        captured["dry_run"] = dry_run

    monkeypatch.setattr("repo_scaffold.cli.apply_repository_settings", _fake_apply)

    rc = main(
        ["apply", "settings", "--repo", "acme/repo", "--languages", "react,python"]
    )
    assert rc == 0
    assert captured["repo"] == "acme/repo"
    assert captured["languages"] == ["python", "react"]
    assert captured["dry_run"] is False
    stdout = capsys.readouterr().out
    assert "settings applied: True" in stdout
    assert "python, react" in stdout


def test_apply_settings_rejects_unknown_language(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("repo_scaffold.cli.apply_repository_settings", lambda **_: None)

    with pytest.raises(SystemExit) as exc_info:
        main(["apply", "settings", "--repo", "acme/repo", "--languages", "pyhton"])
    assert exc_info.value.code != 0


def test_apply_settings_dry_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    captured: dict[str, object] = {}

    def _fake_apply(*, repo_dir, repo, dry_run, out, warn, languages):
        captured["dry_run"] = dry_run

    monkeypatch.setattr("repo_scaffold.cli.apply_repository_settings", _fake_apply)

    rc = main(["apply", "settings", "--repo", "acme/repo", "--dry-run"])
    assert rc == 0
    assert captured["dry_run"] is True
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

    def _fake_check_repository_settings(
        *, repo_dir: Path, repo: str, out, languages=None
    ):
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
    def _fake_check_repository_settings(
        *, repo_dir: Path, repo: str, out, languages=None
    ):
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
        stage_files,
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
        stage_files,
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
    expected_repo_dir = Path("repo-from-env")
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
        stage_files,
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
    expected_repo_dir = Path("repo-from-flag")
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


def test_project_list_subcommand_prints_projects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)

    monkeypatch.setattr(
        "repo_scaffold.cli.list_projects",
        lambda **_kwargs: ProjectListSummary(
            owner="acme",
            projects=(
                ProjectInfo(owner="acme", number=1, title="Roadmap", closed=False),
                ProjectInfo(owner="acme", number=2, title="Archive", closed=True),
            ),
        ),
    )

    rc = main(["project", "list", "--path", str(repo_dir), "--project-owner", "acme"])

    assert rc == 0
    stdout = capsys.readouterr().out
    assert "acme/#1 Roadmap" in stdout
    assert "acme/#2 Archive [closed]" in stdout
    assert "projects: 2" in stdout


def test_project_items_subcommand_prints_project_contents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)

    def _fake_items(**_kwargs) -> ProjectItemsSummary:
        return ProjectItemsSummary(
            project=ProjectInfo(owner="acme", number=4, title="Roadmap"),
            items=(
                ProjectItemInfo(
                    id="PVTI_1",
                    title="Ticket A",
                    content_type="Issue",
                    content_url="https://github.com/acme/repo/issues/11",
                    issue_number=11,
                    repository="acme/repo",
                ),
            ),
        )

    monkeypatch.setattr("repo_scaffold.cli.list_project_items", _fake_items)

    rc = main(
        [
            "project",
            "items",
            "--path",
            str(repo_dir),
            "--project-owner",
            "acme",
            "--project-number",
            "4",
        ]
    )

    assert rc == 0
    stdout = capsys.readouterr().out
    assert "Project: acme/#4 (Roadmap)" in stdout
    assert "[Issue] item=PVTI_1 #11 Ticket A" in stdout
    assert "repo: acme/repo" in stdout
    assert "items: 1" in stdout


def test_project_delete_subcommand_delegates_to_project_ops(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)

    called: dict[str, object] = {}

    def _fake_delete_project(**kwargs) -> ProjectMutationSummary:
        called.update(kwargs)
        return ProjectMutationSummary(
            action="delete",
            owner="acme",
            project_number=4,
            project_title="Roadmap",
            failures=0,
            changed=True,
            backup_file=repo_dir / "local" / "acme" / "backups" / "backup.json",
            undo_command="undo-cmd",
        )

    monkeypatch.setattr("repo_scaffold.cli.delete_project", _fake_delete_project)

    rc = main(
        [
            "project",
            "delete",
            "--path",
            str(repo_dir),
            "--project-owner",
            "acme",
            "--project-number",
            "4",
            "--danger",
            "--yes",
        ]
    )

    assert rc == 0
    assert called["danger"] is True
    assert called["assume_yes"] is True
    stdout = capsys.readouterr().out
    assert "backup file:" in stdout
    assert "undo: undo-cmd" in stdout


# ---------------------------------------------------------------------------
# project setup tests
# ---------------------------------------------------------------------------


def test_project_setup_delegates_to_setup_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)

    called: dict[str, object] = {}

    def _fake_setup_project(**kwargs) -> ProjectMutationSummary:
        called.update(kwargs)
        return ProjectMutationSummary(
            action="setup",
            owner="acme",
            project_number=5,
            project_title="Roadmap",
            failures=0,
            changed=True,
        )

    monkeypatch.setattr("repo_scaffold.cli.setup_project", _fake_setup_project)

    rc = main(
        [
            "project",
            "setup",
            "--path",
            str(repo_dir),
            "--project-owner",
            "acme",
            "--project-title",
            "Roadmap",
        ]
    )
    assert rc == 0
    assert called["owner"] == "acme"
    assert called["project_title"] == "Roadmap"
    assert called["write_actions_template"] is True


def test_project_setup_no_actions_template_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)

    called: dict[str, object] = {}

    def _fake_setup_project(**kwargs) -> ProjectMutationSummary:
        called.update(kwargs)
        return ProjectMutationSummary(
            action="setup",
            owner="acme",
            project_number=5,
            project_title="Roadmap",
            failures=0,
            changed=True,
        )

    monkeypatch.setattr("repo_scaffold.cli.setup_project", _fake_setup_project)

    rc = main(
        [
            "project",
            "setup",
            "--path",
            str(repo_dir),
            "--project-owner",
            "acme",
            "--project-title",
            "Roadmap",
            "--no-actions-template",
        ]
    )
    assert rc == 0
    assert called["write_actions_template"] is False


def test_project_setup_interactive_rejected_when_not_tty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)
    monkeypatch.setattr("repo_scaffold.cli.sys.stdin.isatty", lambda: False)

    rc = main(
        [
            "project",
            "setup",
            "--path",
            str(repo_dir),
            "--project-owner",
            "acme",
            "--project-title",
            "Roadmap",
            "--interactive",
        ]
    )
    assert rc == 1
    assert "interactive" in capsys.readouterr().err.lower()


# ---------------------------------------------------------------------------
# issue view tests
# ---------------------------------------------------------------------------


def test_issue_view_human_readable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    fake_issue = IssueDetail(
        number=42,
        title="Fix the bug",
        body="This is the body.",
        state="open",
        labels=["bug", "high-priority"],
        assignees=["alice"],
    )
    monkeypatch.setattr(
        "repo_scaffold.cli.fetch_issue",
        lambda repo_dir, repo, issue_number: fake_issue,
    )

    rc = main(["issue", "view", "--repo", "acme/repo", "--issue-number", "42"])

    assert rc == 0
    stdout = capsys.readouterr().out
    assert "Issue #42: Fix the bug" in stdout
    assert "State: open" in stdout
    assert "bug" in stdout
    assert "high-priority" in stdout
    assert "alice" in stdout
    assert "This is the body." in stdout


def test_issue_view_json_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import json

    monkeypatch.chdir(tmp_path)
    fake_issue = IssueDetail(
        number=7,
        title="JSON issue",
        body="Body text.",
        state="closed",
        labels=["wontfix"],
        assignees=[],
    )
    monkeypatch.setattr(
        "repo_scaffold.cli.fetch_issue",
        lambda repo_dir, repo, issue_number: fake_issue,
    )

    rc = main(["issue", "view", "--repo", "acme/repo", "--issue-number", "7", "--json"])

    assert rc == 0
    stdout = capsys.readouterr().out
    data = json.loads(stdout)
    assert data["number"] == 7
    assert data["title"] == "JSON issue"
    assert data["state"] == "closed"
    assert data["labels"] == ["wontfix"]
    assert data["assignees"] == []
    assert data["body"] == "Body text."


def test_issue_view_not_found_returns_1(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "repo_scaffold.cli.fetch_issue",
        lambda repo_dir, repo, issue_number: (_ for _ in ()).throw(
            RuntimeError("Issue #999 not found in acme/repo.")
        ),
    )

    rc = main(["issue", "view", "--repo", "acme/repo", "--issue-number", "999"])
    assert rc == 1


def test_issue_view_bad_repo_format_returns_2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    rc = main(["issue", "view", "--repo", "not-valid", "--issue-number", "1"])
    assert rc == 2


def _fake_issue_cp(data: object) -> object:
    import json
    import subprocess

    return subprocess.CompletedProcess(
        args=[], returncode=201, stdout=json.dumps(data), stderr=""
    )


def test_issue_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import json
    import subprocess

    monkeypatch.chdir(tmp_path)
    issues = [
        {"number": 1, "title": "Bug", "state": "open", "labels": [{"name": "bug"}]}
    ]
    monkeypatch.setattr(
        "repo_scaffold.cli.issue_list",
        lambda repo, token, state="open", label=None: subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(issues), stderr=""
        ),
    )
    monkeypatch.setattr("repo_scaffold.cli.token_from_repo", lambda _: "tok")
    rc = main(["issue", "list", "--repo", "acme/repo"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "#1" in out and "Bug" in out


def test_issue_create(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "repo_scaffold.cli.issue_create",
        lambda repo, title, token, **kw: _fake_issue_cp(
            {
                "number": 10,
                "title": title,
                "html_url": "https://github.com/acme/repo/issues/10",
            }
        ),
    )
    monkeypatch.setattr("repo_scaffold.cli.token_from_repo", lambda _: "tok")
    rc = main(["issue", "create", "--repo", "acme/repo", "--title", "New bug"])
    assert rc == 0
    assert "#10" in capsys.readouterr().out


def test_issue_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import subprocess

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "repo_scaffold.cli.issue_close",
        lambda repo, number, token: subprocess.CompletedProcess(
            args=[], returncode=0, stdout="{}", stderr=""
        ),
    )
    monkeypatch.setattr("repo_scaffold.cli.token_from_repo", lambda _: "tok")
    rc = main(["issue", "close", "--repo", "acme/repo", "--issue-number", "5"])
    assert rc == 0
    assert "closed" in capsys.readouterr().out


def test_issue_comment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "repo_scaffold.cli.issue_comment",
        lambda repo, number, body, token: _fake_issue_cp(
            {"html_url": "https://github.com/acme/repo/issues/5#comment-1"}
        ),
    )
    monkeypatch.setattr("repo_scaffold.cli.token_from_repo", lambda _: "tok")
    rc = main(
        [
            "issue",
            "comment",
            "--repo",
            "acme/repo",
            "--issue-number",
            "5",
            "--body",
            "LGTM",
        ]
    )
    assert rc == 0
    assert "Comment posted" in capsys.readouterr().out


def test_issue_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import subprocess

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "repo_scaffold.cli.issue_label",
        lambda repo, number, token, add=None, remove=None: subprocess.CompletedProcess(
            args=[], returncode=0, stdout="{}", stderr=""
        ),
    )
    monkeypatch.setattr("repo_scaffold.cli.token_from_repo", lambda _: "tok")
    rc = main(
        ["issue", "label", "--repo", "acme/repo", "--issue-number", "5", "--add", "bug"]
    )
    assert rc == 0
    assert "Labels updated" in capsys.readouterr().out


def test_issue_assign(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import subprocess

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "repo_scaffold.cli.issue_assign",
        lambda repo, number, token, add=None, remove=None: subprocess.CompletedProcess(
            args=[], returncode=0, stdout="{}", stderr=""
        ),
    )
    monkeypatch.setattr("repo_scaffold.cli.token_from_repo", lambda _: "tok")
    rc = main(
        [
            "issue",
            "assign",
            "--repo",
            "acme/repo",
            "--issue-number",
            "5",
            "--add",
            "alice",
        ]
    )
    assert rc == 0
    assert "Assignees updated" in capsys.readouterr().out


def test_issue_delete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import json
    import subprocess

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "repo_scaffold.cli.issue_delete",
        lambda owner, repo, number, token: subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps({"deleted": True, "number": number}),
            stderr="",
        ),
    )
    monkeypatch.setattr("repo_scaffold.cli.token_from_repo", lambda _: "tok")
    rc = main(["issue", "delete", "--repo", "acme/repo", "--issue-number", "7"])
    assert rc == 0
    assert "deleted" in capsys.readouterr().out


def test_issue_delete_failure_returns_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "repo_scaffold.cli.issue_delete",
        lambda owner, repo, number, token: subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="Not found"
        ),
    )
    monkeypatch.setattr("repo_scaffold.cli.token_from_repo", lambda _: "tok")
    rc = main(["issue", "delete", "--repo", "acme/repo", "--issue-number", "99"])
    assert rc == 1


def test_issue_add_sub_issue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import json
    import subprocess

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "repo_scaffold.cli.issue_add_sub_issue",
        lambda owner, repo, parent_number, child_number, token: subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(
                {
                    "issue": {"number": parent_number},
                    "subIssue": {"number": child_number},
                }
            ),
            stderr="",
        ),
    )
    monkeypatch.setattr("repo_scaffold.cli.token_from_repo", lambda _: "tok")
    rc = main(
        [
            "issue",
            "add-sub-issue",
            "--repo",
            "acme/repo",
            "--parent",
            "10",
            "--child",
            "11",
        ]
    )
    assert rc == 0
    assert "linked" in capsys.readouterr().out.lower()


def test_issue_add_sub_issue_failure_returns_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "repo_scaffold.cli.issue_add_sub_issue",
        lambda owner, repo, parent_number, child_number, token: subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="Could not resolve node ID"
        ),
    )
    monkeypatch.setattr("repo_scaffold.cli.token_from_repo", lambda _: "tok")
    rc = main(
        [
            "issue",
            "add-sub-issue",
            "--repo",
            "acme/repo",
            "--parent",
            "10",
            "--child",
            "11",
        ]
    )
    assert rc == 1


def test_issue_sync_hierarchy_dry_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import json
    import subprocess

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "repo_scaffold.cli.issue_sync_hierarchy",
        lambda repo, token, apply=False: subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(
                {
                    "linked": [],
                    "already_linked": [{"epic": 1, "child": 2}],
                    "would_link": [{"epic": 1, "child": 3}],
                    "conflict": [],
                    "ambiguous": [],
                    "unaffiliated": [4],
                    "errors": [],
                }
            ),
            stderr="",
        ),
    )
    monkeypatch.setattr("repo_scaffold.cli.token_from_repo", lambda _: "tok")
    rc = main(["issue", "sync-hierarchy", "--repo", "acme/repo"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "DRY RUN" in out
    assert "would_link: 1" in out


def test_issue_sync_hierarchy_apply_failure_returns_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "repo_scaffold.cli.issue_sync_hierarchy",
        lambda repo, token, apply=False: subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="boom"
        ),
    )
    monkeypatch.setattr("repo_scaffold.cli.token_from_repo", lambda _: "tok")
    rc = main(["issue", "sync-hierarchy", "--repo", "acme/repo", "--apply"])
    assert rc == 1


# ---------------------------------------------------------------------------
# pr command tests
# ---------------------------------------------------------------------------


def test_pr_list_human_readable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import json
    import subprocess

    monkeypatch.chdir(tmp_path)
    fake_prs = [
        {
            "number": 5,
            "title": "Fix bug",
            "state": "open",
            "head": {"ref": "fix/bug"},
            "base": {"ref": "main"},
        },
    ]
    monkeypatch.setattr(
        "repo_scaffold.cli.pr_list",
        lambda repo, token: subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(fake_prs), stderr=""
        ),
    )
    monkeypatch.setattr("repo_scaffold.cli.token_from_repo", lambda _: "tok")

    rc = main(["pr", "list", "--repo", "acme/repo"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "#5" in out
    assert "Fix bug" in out


def test_pr_view_human_readable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import json
    import subprocess

    monkeypatch.chdir(tmp_path)
    fake_pr = {
        "number": 5,
        "title": "Fix bug",
        "state": "open",
        "head": {"ref": "fix/bug"},
        "base": {"ref": "main"},
        "user": {"login": "alice"},
        "body": "Fixes the thing.",
    }
    monkeypatch.setattr(
        "repo_scaffold.cli.pr_view",
        lambda repo, number, token: subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(fake_pr), stderr=""
        ),
    )
    monkeypatch.setattr("repo_scaffold.cli.token_from_repo", lambda _: "tok")

    rc = main(["pr", "view", "--repo", "acme/repo", "--pr-number", "5"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "PR #5: Fix bug" in out
    assert "alice" in out


def test_pr_comment_posts_and_prints_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import json
    import subprocess

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "repo_scaffold.cli.pr_comment",
        lambda repo, number, body, token, reply_to=None: subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(
                {"html_url": "https://github.com/acme/repo/pull/5#comment-1"}
            ),
            stderr="",
        ),
    )
    monkeypatch.setattr("repo_scaffold.cli.token_from_repo", lambda _: "tok")

    rc = main(
        ["pr", "comment", "--repo", "acme/repo", "--pr-number", "5", "--body", "LGTM"]
    )
    assert rc == 0
    assert "Comment posted" in capsys.readouterr().out


def test_pr_react_posts_reaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import json
    import subprocess

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "repo_scaffold.cli.react",
        lambda repo, subject, subject_id, content, token: subprocess.CompletedProcess(
            args=[],
            returncode=201,
            stdout=json.dumps({"content": "+1", "id": 99}),
            stderr="",
        ),
    )
    monkeypatch.setattr("repo_scaffold.cli.token_from_repo", lambda _: "tok")

    rc = main(
        [
            "pr",
            "react",
            "--repo",
            "acme/repo",
            "--comment-id",
            "12345",
            "--reaction",
            "+1",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "Reaction added" in out
    assert "+1" in out


def test_pr_react_error_returns_nonzero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import subprocess

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "repo_scaffold.cli.react",
        lambda repo, subject, subject_id, content, token: subprocess.CompletedProcess(
            args=[],
            returncode=422,
            stdout="",
            stderr="Unprocessable Entity",
        ),
    )
    monkeypatch.setattr("repo_scaffold.cli.token_from_repo", lambda _: "tok")

    rc = main(
        [
            "pr",
            "react",
            "--repo",
            "acme/repo",
            "--comment-id",
            "12345",
            "--reaction",
            "+1",
        ]
    )
    assert rc == 1
    assert "Unprocessable Entity" in capsys.readouterr().err


def test_pr_resolve_thread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import json
    import subprocess

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "repo_scaffold.cli.pr_resolve_thread",
        lambda thread_id, token: subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps({"id": "PRRT_abc", "isResolved": True}),
            stderr="",
        ),
    )
    monkeypatch.setattr("repo_scaffold.cli.token_from_repo", lambda _: "tok")

    rc = main(
        ["pr", "resolve-thread", "--repo", "acme/repo", "--thread-id", "PRRT_abc"]
    )
    assert rc == 0
    assert "resolved" in capsys.readouterr().out.lower()


def test_pr_create(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import json
    import subprocess

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "repo_scaffold.cli.pr_create",
        lambda repo, title, body, head, base, token, draft=False: subprocess.CompletedProcess(
            args=[],
            returncode=201,
            stdout=json.dumps(
                {
                    "number": 10,
                    "title": "My PR",
                    "html_url": "https://github.com/acme/repo/pull/10",
                }
            ),
            stderr="",
        ),
    )
    monkeypatch.setattr("repo_scaffold.cli.token_from_repo", lambda _: "tok")

    rc = main(
        [
            "pr",
            "create",
            "--repo",
            "acme/repo",
            "--title",
            "My PR",
            "--head",
            "feat/my-branch",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "PR created: #10" in out
    assert "https://github.com/acme/repo/pull/10" in out


def test_pr_merge(tmp_path, monkeypatch, capsys) -> None:
    import json
    import subprocess

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "repo_scaffold.cli.pr_merge",
        lambda repo, pr_number, token, method="squash": subprocess.CompletedProcess(
            args=[],
            returncode=200,
            stdout=json.dumps(
                {
                    "sha": "abc",
                    "merged": True,
                    "message": "Pull Request successfully merged",
                }
            ),
            stderr="",
        ),
    )
    from repo_scaffold.cli import main

    rc = main(["pr", "merge", "--repo", "acme/repo", "--pr-number", "42"])
    assert rc == 0
    assert "merged" in capsys.readouterr().out


def test_pr_checks(tmp_path, monkeypatch, capsys) -> None:
    import json
    import subprocess

    monkeypatch.chdir(tmp_path)
    runs = [{"id": 1, "name": "CI", "status": "completed", "conclusion": "success"}]
    monkeypatch.setattr(
        "repo_scaffold.cli.pr_checks",
        lambda repo, pr_number, token: subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(runs), stderr=""
        ),
    )
    from repo_scaffold.cli import main

    rc = main(["pr", "checks", "--repo", "acme/repo", "--pr-number", "42"])
    assert rc == 0
    assert "CI" in capsys.readouterr().out


def test_pr_annotations(tmp_path, monkeypatch, capsys) -> None:
    import json
    import subprocess

    monkeypatch.chdir(tmp_path)
    anns = [
        {
            "check_run": "react",
            "annotation_level": "failure",
            "path": "src/App.tsx",
            "start_line": 10,
            "message": "'foo' is defined but never used",
        }
    ]
    monkeypatch.setattr(
        "repo_scaffold.cli.pr_annotations",
        lambda repo, pr_number, token: subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(anns), stderr=""
        ),
    )
    from repo_scaffold.cli import main

    rc = main(["pr", "annotations", "--repo", "acme/repo", "--pr-number", "42"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "failure" in out
    assert "src/App.tsx:10" in out
    assert "never used" in out


def test_pr_annotations_json(tmp_path, monkeypatch, capsys) -> None:
    import json
    import subprocess

    monkeypatch.chdir(tmp_path)
    anns = [
        {
            "check_run": "CI",
            "annotation_level": "warning",
            "path": "x.ts",
            "start_line": 1,
            "message": "msg",
        }
    ]
    monkeypatch.setattr(
        "repo_scaffold.cli.pr_annotations",
        lambda repo, pr_number, token: subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(anns), stderr=""
        ),
    )
    from repo_scaffold.cli import main

    rc = main(
        ["pr", "annotations", "--repo", "acme/repo", "--pr-number", "5", "--json"]
    )
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data[0]["path"] == "x.ts"


def test_pr_list_comments_inline_review(tmp_path, monkeypatch, capsys) -> None:
    import json
    import subprocess

    monkeypatch.chdir(tmp_path)
    comments = [
        {
            "id": 1,
            "user": {"login": "alice"},
            "body": "nit: rename this",
            "path": "src/foo.py",
            "line": 42,
            "created_at": "2026-01-01T00:00:00Z",
        }
    ]
    monkeypatch.setattr(
        "repo_scaffold.cli.pr_list_comments",
        lambda repo, pr_number, token: subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(comments), stderr=""
        ),
    )
    from repo_scaffold.cli import main

    rc = main(["pr", "list-comments", "--repo", "acme/repo", "--pr-number", "42"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "alice" in out
    assert "src/foo.py:42" in out
    assert "nit: rename this" in out


def test_pr_list_comments_general_conversation(tmp_path, monkeypatch, capsys) -> None:
    import json
    import subprocess

    monkeypatch.chdir(tmp_path)
    comments = [
        {
            "id": 2,
            "user": {"login": "bob"},
            "body": "lgtm overall",
            "created_at": "2026-01-02T00:00:00Z",
        }
    ]
    monkeypatch.setattr(
        "repo_scaffold.cli.pr_list_comments",
        lambda repo, pr_number, token: subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(comments), stderr=""
        ),
    )
    from repo_scaffold.cli import main

    rc = main(["pr", "list-comments", "--repo", "acme/repo", "--pr-number", "42"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "bob" in out
    assert "2026-01-02" in out
    assert "lgtm overall" in out


def test_pr_list_comments_json(tmp_path, monkeypatch, capsys) -> None:
    import json
    import subprocess

    monkeypatch.chdir(tmp_path)
    comments = [{"id": 1, "user": {"login": "alice"}, "body": "nit"}]
    monkeypatch.setattr(
        "repo_scaffold.cli.pr_list_comments",
        lambda repo, pr_number, token: subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(comments), stderr=""
        ),
    )
    from repo_scaffold.cli import main

    rc = main(
        ["pr", "list-comments", "--repo", "acme/repo", "--pr-number", "42", "--json"]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert json.loads(out)[0]["user"]["login"] == "alice"


def test_pr_list_comments_empty(tmp_path, monkeypatch, capsys) -> None:
    import json
    import subprocess

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "repo_scaffold.cli.pr_list_comments",
        lambda repo, pr_number, token: subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps([]), stderr=""
        ),
    )
    from repo_scaffold.cli import main

    rc = main(["pr", "list-comments", "--repo", "acme/repo", "--pr-number", "42"])
    assert rc == 0
    assert "No comments found" in capsys.readouterr().out


def test_pr_list_comments_error(tmp_path, monkeypatch, capsys) -> None:
    import subprocess

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "repo_scaffold.cli.pr_list_comments",
        lambda repo, pr_number, token: subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="API error"
        ),
    )
    from repo_scaffold.cli import main

    rc = main(["pr", "list-comments", "--repo", "acme/repo", "--pr-number", "42"])
    assert rc != 0


def test_pr_rerun(tmp_path, monkeypatch, capsys) -> None:
    import json
    import subprocess

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "repo_scaffold.cli.pr_rerun",
        lambda repo, pr_number, token, failed_only=False: subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps({"triggered": [12345], "errors": []}),
            stderr="",
        ),
    )
    from repo_scaffold.cli import main

    rc = main(
        ["pr", "rerun", "--repo", "acme/repo", "--pr-number", "42", "--failed-only"]
    )
    assert rc == 0
    assert "12345" in capsys.readouterr().out


def test_pr_reviews_human_readable(tmp_path, monkeypatch, capsys) -> None:
    import json
    import subprocess

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "repo_scaffold.cli.pr_reviews",
        lambda repo, pr_number, token: subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(
                [
                    {
                        "id": 1,
                        "user": "alice",
                        "state": "APPROVED",
                        "submitted_at": "2026-06-01T12:00:00Z",
                        "body": "LGTM",
                    }
                ]
            ),
            stderr="",
        ),
    )
    from repo_scaffold.cli import main

    rc = main(["pr", "reviews", "--repo", "acme/repo", "--pr-number", "5"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "alice" in out
    assert "APPROVED" in out


def test_pr_reviews_json(tmp_path, monkeypatch, capsys) -> None:
    import json
    import subprocess

    monkeypatch.chdir(tmp_path)
    payload = [
        {
            "id": 2,
            "user": "bob",
            "state": "CHANGES_REQUESTED",
            "submitted_at": "",
            "body": "",
        }
    ]
    monkeypatch.setattr(
        "repo_scaffold.cli.pr_reviews",
        lambda repo, pr_number, token: subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(payload), stderr=""
        ),
    )
    from repo_scaffold.cli import main

    rc = main(["pr", "reviews", "--repo", "acme/repo", "--pr-number", "5", "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    assert json.loads(out) == payload


def test_branch_create(tmp_path, monkeypatch, capsys) -> None:
    import json
    import subprocess

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "repo_scaffold.cli.branch_create",
        lambda repo, name, token, base="main": subprocess.CompletedProcess(
            args=[],
            returncode=201,
            stdout=json.dumps({"ref": "refs/heads/feat/foo", "object": {"sha": "abc"}}),
            stderr="",
        ),
    )
    from repo_scaffold.cli import main

    rc = main(["branch", "create", "--repo", "acme/repo", "--name", "feat/foo"])
    assert rc == 0
    assert "feat/foo" in capsys.readouterr().out


def test_branch_delete(tmp_path, monkeypatch, capsys) -> None:
    import subprocess

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "repo_scaffold.cli.branch_delete",
        lambda repo, name, token: subprocess.CompletedProcess(
            args=[], returncode=204, stdout="", stderr=""
        ),
    )
    from repo_scaffold.cli import main

    rc = main(["branch", "delete", "--repo", "acme/repo", "--name", "feat/foo"])
    assert rc == 0


def test_label_list(tmp_path, monkeypatch, capsys) -> None:
    import json
    import subprocess

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "repo_scaffold.cli.label_list",
        lambda repo, token: subprocess.CompletedProcess(
            args=[],
            returncode=200,
            stdout=json.dumps(
                [{"name": "bug", "color": "d73a4a", "description": "Something broken"}]
            ),
            stderr="",
        ),
    )
    from repo_scaffold.cli import main

    rc = main(["label", "list", "--repo", "acme/repo"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "bug" in out
    assert "Total: 1" in out


def test_label_create(tmp_path, monkeypatch, capsys) -> None:
    import subprocess

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "repo_scaffold.cli.label_create",
        lambda repo, name, color, token, description="": subprocess.CompletedProcess(
            args=[], returncode=201, stdout="{}", stderr=""
        ),
    )
    from repo_scaffold.cli import main

    rc = main(
        [
            "label",
            "create",
            "--repo",
            "acme/repo",
            "--name",
            "needs-triage",
            "--color",
            "e4e669",
        ]
    )
    assert rc == 0
    assert "needs-triage" in capsys.readouterr().out


def test_label_delete(tmp_path, monkeypatch, capsys) -> None:
    import subprocess

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "repo_scaffold.cli.label_delete",
        lambda repo, name, token: subprocess.CompletedProcess(
            args=[], returncode=204, stdout="", stderr=""
        ),
    )
    from repo_scaffold.cli import main

    rc = main(["label", "delete", "--repo", "acme/repo", "--name", "needs-triage"])
    assert rc == 0
    assert "needs-triage" in capsys.readouterr().out


def test_label_apply_preset(tmp_path, monkeypatch, capsys) -> None:
    import json
    import subprocess

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "repo_scaffold.cli.label_apply_preset",
        lambda repo, token: subprocess.CompletedProcess(
            args=[],
            returncode=200,
            stdout=json.dumps(
                {"created": ["needs-triage", "good first issue"], "skipped": 3}
            ),
            stderr="",
        ),
    )
    from repo_scaffold.cli import main

    rc = main(["label", "apply-preset", "--repo", "acme/repo"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "needs-triage" in out
    assert "Skipped (already exist): 3" in out


def test_issue_comment_body_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json
    import subprocess

    body_file = tmp_path / "comment.md"
    body_file.write_text("Hello from file", encoding="utf-8")
    captured: list[str] = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "repo_scaffold.cli.issue_comment",
        lambda repo, number, body, token: (
            captured.append(body),
            subprocess.CompletedProcess(
                args=[],
                returncode=201,
                stdout=json.dumps({"html_url": "https://github.com/x"}),
                stderr="",
            ),
        )[1],
    )
    rc = main(
        [
            "issue",
            "comment",
            "--repo",
            "acme/repo",
            "--issue-number",
            "1",
            "--body-file",
            str(body_file),
        ]
    )
    assert rc == 0
    assert captured == ["Hello from file"]


def test_pr_create_body_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import json
    import subprocess

    body_file = tmp_path / "pr_body.md"
    body_file.write_text("PR body from file", encoding="utf-8")
    captured: list[str] = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "repo_scaffold.cli.pr_create",
        lambda repo, title, body, head, base, token, draft=False: (
            captured.append(body),
            subprocess.CompletedProcess(
                args=[],
                returncode=201,
                stdout=json.dumps(
                    {
                        "number": 99,
                        "title": title,
                        "html_url": "https://github.com/x/99",
                    }
                ),
                stderr="",
            ),
        )[1],
    )
    rc = main(
        [
            "pr",
            "create",
            "--repo",
            "acme/repo",
            "--title",
            "My PR",
            "--head",
            "feat/x",
            "--body-file",
            str(body_file),
        ]
    )
    assert rc == 0
    assert captured == ["PR body from file"]


def test_body_and_body_file_are_mutually_exclusive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body_file = tmp_path / "body.md"
    body_file.write_text("text", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        main(
            [
                "issue",
                "comment",
                "--repo",
                "acme/repo",
                "--issue-number",
                "1",
                "--body",
                "inline",
                "--body-file",
                str(body_file),
            ]
        )


def test_issue_comment_missing_body_returns_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    rc = main(
        [
            "issue",
            "comment",
            "--repo",
            "acme/repo",
            "--issue-number",
            "1",
        ]
    )
    assert rc == 2


def test_body_file_missing_path_exits_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "issue",
                "comment",
                "--repo",
                "acme/repo",
                "--issue-number",
                "1",
                "--body-file",
                str(tmp_path / "does_not_exist.md"),
            ]
        )
    assert "cannot read" in str(exc.value)


def test_body_file_non_utf8_exits_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad_file = tmp_path / "latin1.md"
    bad_file.write_bytes(b"caf\xe9")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "issue",
                "comment",
                "--repo",
                "acme/repo",
                "--issue-number",
                "1",
                "--body-file",
                str(bad_file),
            ]
        )
    assert "UTF-8" in str(exc.value)


def test_pr_request_reviewer(tmp_path, monkeypatch, capsys) -> None:
    import json
    import subprocess

    monkeypatch.chdir(tmp_path)
    payload = {
        "number": 42,
        "requested_reviewers": [{"login": "blairg23"}],
    }
    monkeypatch.setattr(
        "repo_scaffold.cli.pr_request_reviewer",
        lambda repo, pr_number, token, reviewers: subprocess.CompletedProcess(
            args=[], returncode=201, stdout=json.dumps(payload), stderr=""
        ),
    )
    from repo_scaffold.cli import main

    rc = main(
        [
            "pr",
            "request-reviewer",
            "--repo",
            "acme/repo",
            "--pr-number",
            "42",
            "--reviewer",
            "blairg23",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "blairg23" in out
    assert "42" in out


def test_repo_register_list_forget_roundtrip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    registry_path = tmp_path / "registry.json"
    monkeypatch.setenv("REPO_SCAFFOLD_REGISTRY_PATH", str(registry_path))
    local_dir = tmp_path / "acme-repo"
    expected_path = str(local_dir.resolve())

    rc = main(
        [
            "repo",
            "register",
            "--repo",
            "acme/repo",
            "--path",
            str(local_dir),
            "--notes",
            "primary checkout",
        ]
    )
    assert rc == 0
    stdout = capsys.readouterr().out
    assert f"Registered acme/repo -> {expected_path}" in stdout

    rc = main(["repo", "list"])
    assert rc == 0
    stdout = capsys.readouterr().out
    assert f"acme/repo -> {expected_path}" in stdout
    assert "primary checkout" in stdout

    rc = main(["repo", "forget", "--repo", "acme/repo"])
    assert rc == 0
    stdout = capsys.readouterr().out
    assert "Removed acme/repo from the registry." in stdout

    rc = main(["repo", "list"])
    assert rc == 0
    stdout = capsys.readouterr().out
    assert "No repos registered." in stdout


def test_repo_forget_missing_repo_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("REPO_SCAFFOLD_REGISTRY_PATH", str(tmp_path / "registry.json"))
    rc = main(["repo", "forget", "--repo", "acme/missing"])
    assert rc == 2
    stderr = capsys.readouterr().err
    assert "not registered" in stderr


def test_repo_archive_with_yes_flag_skips_prompt(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import subprocess

    monkeypatch.setattr("repo_scaffold.cli.token_from_repo", lambda _: "tok")
    monkeypatch.setattr(
        "repo_scaffold.cli.repo_archive",
        lambda repo, token: subprocess.CompletedProcess(
            args=[], returncode=0, stdout='{"archived": true}', stderr=""
        ),
    )
    rc = main(["repo", "archive", "--repo", "acme/repo", "--yes"])
    assert rc == 0
    assert "Archived acme/repo." in capsys.readouterr().out


def test_repo_archive_refuses_without_yes_in_non_interactive_shell(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "repo_scaffold.cli.sys.stdin", SimpleNamespace(isatty=lambda: False)
    )
    rc = main(["repo", "archive", "--repo", "acme/repo"])
    assert rc == 2
    assert "refusing to archive" in capsys.readouterr().err


def test_repo_archive_prompts_and_aborts_on_no(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "repo_scaffold.cli.sys.stdin", SimpleNamespace(isatty=lambda: True)
    )
    monkeypatch.setattr("builtins.input", lambda _: "n")
    rc = main(["repo", "archive", "--repo", "acme/repo"])
    assert rc == 0
    assert "Aborted." in capsys.readouterr().out


def test_apply_rules_with_repos_flag_uses_registry_paths(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from repo_scaffold.registry_ops import RegistryEntry

    monkeypatch.setattr(
        "repo_scaffold.cli.list_registry",
        lambda: [
            RegistryEntry(repo="acme/a", local_path="/local/a"),
            RegistryEntry(repo="acme/b", local_path="/local/b"),
        ],
    )

    calls: list[dict[str, object]] = []

    def _fake_apply(*, repo_dir, repo, dry_run, out, warn, languages=None):
        calls.append({"repo_dir": repo_dir, "repo": repo})

    monkeypatch.setattr("repo_scaffold.cli.apply_repository_settings", _fake_apply)

    rc = main(["apply", "rules", "--repos", "acme/a,acme/b", "--apply"])
    assert rc == 0
    assert [c["repo"] for c in calls] == ["acme/a", "acme/b"]
    assert calls[0]["repo_dir"] == Path("/local/a")
    assert calls[1]["repo_dir"] == Path("/local/b")
    stdout = capsys.readouterr().out
    assert "repos: 2" in stdout


def test_apply_rules_with_repos_flag_rejects_unregistered_repo(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("repo_scaffold.cli.list_registry", lambda: [])

    rc = main(["apply", "rules", "--repos", "acme/unknown", "--apply"])
    assert rc == 2
    stderr = capsys.readouterr().err
    assert "not registered" in stderr


def test_check_rules_with_all_flag_iterates_registry(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from repo_scaffold.registry_ops import RegistryEntry

    monkeypatch.setattr(
        "repo_scaffold.cli.list_registry",
        lambda: [RegistryEntry(repo="acme/a", local_path="/local/a")],
    )

    def _fake_check(*, repo_dir, repo, out, languages=None):
        out(f"check repository settings: {repo}")
        return SettingsCheckSummary(repo=repo, passed=8, failed=0, skipped=0, drifts=())

    monkeypatch.setattr("repo_scaffold.cli.check_repository_settings", _fake_check)

    rc = main(["check", "rules", "--all"])
    assert rc == 0
    stdout = capsys.readouterr().out
    assert "check repository settings: acme/a" in stdout


def test_check_rules_rejects_multiple_selectors(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = main(["check", "rules", "--repo", "acme/a", "--all"])
    assert rc == 2
    stderr = capsys.readouterr().err
    assert "only one of" in stderr


def test_sync_rules_applies_only_confirmed_repos(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from repo_scaffold.registry_ops import RegistryEntry

    monkeypatch.setattr(
        "repo_scaffold.cli.list_registry",
        lambda: [
            RegistryEntry(repo="acme/drifted", local_path="/local/drifted"),
            RegistryEntry(repo="acme/clean", local_path="/local/clean"),
        ],
    )

    def _fake_check(*, repo_dir, repo, out, languages=None):
        failed = 1 if repo == "acme/drifted" else 0
        return SettingsCheckSummary(
            repo=repo, passed=7, failed=failed, skipped=0, drifts=()
        )

    applied: list[str] = []

    def _fake_apply(*, repo_dir, repo, dry_run, out, warn, languages=None):
        applied.append(repo)

    monkeypatch.setattr("repo_scaffold.cli.check_repository_settings", _fake_check)
    monkeypatch.setattr("repo_scaffold.cli.apply_repository_settings", _fake_apply)
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")

    rc = main(["sync", "rules", "--all"])
    assert rc == 0
    assert applied == ["acme/drifted"]
    stdout = capsys.readouterr().out
    assert "repos applied: 1" in stdout


def test_sync_rules_no_drift_skips_apply(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from repo_scaffold.registry_ops import RegistryEntry

    monkeypatch.setattr(
        "repo_scaffold.cli.list_registry",
        lambda: [RegistryEntry(repo="acme/clean", local_path="/local/clean")],
    )

    def _fake_check(*, repo_dir, repo, out, languages=None):
        return SettingsCheckSummary(repo=repo, passed=8, failed=0, skipped=0, drifts=())

    apply_called = False

    def _fake_apply(**_kwargs):
        nonlocal apply_called
        apply_called = True

    monkeypatch.setattr("repo_scaffold.cli.check_repository_settings", _fake_check)
    monkeypatch.setattr("repo_scaffold.cli.apply_repository_settings", _fake_apply)

    rc = main(["sync", "rules", "--all"])
    assert rc == 0
    assert apply_called is False
    stdout = capsys.readouterr().out
    assert "No drift found" in stdout


def test_sync_rules_returns_nonzero_when_apply_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from repo_scaffold.registry_ops import RegistryEntry

    monkeypatch.setattr(
        "repo_scaffold.cli.list_registry",
        lambda: [RegistryEntry(repo="acme/drifted", local_path="/local/drifted")],
    )

    def _fake_check(*, repo_dir, repo, out, languages=None):
        return SettingsCheckSummary(repo=repo, passed=6, failed=1, skipped=0, drifts=())

    def _fake_apply(*, repo_dir, repo, dry_run, out, warn, languages=None):
        raise RuntimeError("apply failed")

    monkeypatch.setattr("repo_scaffold.cli.check_repository_settings", _fake_check)
    monkeypatch.setattr("repo_scaffold.cli.apply_repository_settings", _fake_apply)
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")

    rc = main(["sync", "rules", "--all"])
    assert rc == 1


def test_sync_rules_returns_nonzero_when_check_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from repo_scaffold.registry_ops import RegistryEntry

    monkeypatch.setattr(
        "repo_scaffold.cli.list_registry",
        lambda: [RegistryEntry(repo="acme/broken", local_path="/local/broken")],
    )

    def _fake_check(*, repo_dir, repo, out, languages=None):
        raise RuntimeError("check failed")

    monkeypatch.setattr("repo_scaffold.cli.check_repository_settings", _fake_check)

    rc = main(["sync", "rules", "--all"])
    assert rc == 1


def test_check_settings_uses_resolved_languages(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "repo_scaffold.cli.resolve_languages_for_repo",
        lambda _repo_dir, _repo, **_kwargs: ["python", "react"],
    )

    captured: dict[str, object] = {}

    def _fake_check(*, repo_dir, repo, out, languages):
        captured["languages"] = languages
        return SettingsCheckSummary(repo=repo, passed=8, failed=0, skipped=0, drifts=())

    monkeypatch.setattr("repo_scaffold.cli.check_repository_settings", _fake_check)

    rc = main(["check", "settings", "--repo", "acme/repo"])
    assert rc == 0
    assert captured["languages"] == ["python", "react"]
    stdout = capsys.readouterr().out
    assert "languages: python, react" in stdout


def test_apply_rules_uses_resolved_languages(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """apply rules must auto-detect languages so the ruleset it writes actually
    matches what check rules/check settings expect -- otherwise apply immediately
    drifts against its own output."""
    monkeypatch.setattr(
        "repo_scaffold.cli.resolve_languages_for_repo",
        lambda _repo_dir, _repo, **_kwargs: ["python"],
    )

    captured: dict[str, object] = {}

    def _fake_apply(*, repo_dir, repo, dry_run, out, warn, languages=None):
        captured["languages"] = languages

    monkeypatch.setattr("repo_scaffold.cli.apply_repository_settings", _fake_apply)

    rc = main(["apply", "rules", "--repo", "acme/repo", "--apply"])
    assert rc == 0
    assert captured["languages"] == ["python"]


def test_sync_rules_uses_resolved_languages(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from repo_scaffold.registry_ops import RegistryEntry

    monkeypatch.setattr(
        "repo_scaffold.cli.list_registry",
        lambda: [RegistryEntry(repo="acme/drifted", local_path="/local/drifted")],
    )
    monkeypatch.setattr(
        "repo_scaffold.cli.resolve_languages_for_repo",
        lambda _repo_dir, _repo, **_kwargs: ["python"],
    )

    check_languages: dict[str, object] = {}
    apply_languages: dict[str, object] = {}

    def _fake_check(*, repo_dir, repo, out, languages=None):
        check_languages["languages"] = languages
        return SettingsCheckSummary(repo=repo, passed=6, failed=1, skipped=0, drifts=())

    def _fake_apply(*, repo_dir, repo, dry_run, out, warn, languages=None):
        apply_languages["languages"] = languages

    monkeypatch.setattr("repo_scaffold.cli.check_repository_settings", _fake_check)
    monkeypatch.setattr("repo_scaffold.cli.apply_repository_settings", _fake_apply)
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")

    rc = main(["sync", "rules", "--all"])
    assert rc == 0
    assert check_languages["languages"] == ["python"]
    assert apply_languages["languages"] == ["python"]


def test_check_settings_languages_flag_overrides_config(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "repo_scaffold.cli.resolve_languages_for_repo",
        lambda _repo_dir, _repo, **_kwargs: ["go"],
    )

    captured: dict[str, object] = {}

    def _fake_check(*, repo_dir, repo, out, languages):
        captured["languages"] = languages
        return SettingsCheckSummary(repo=repo, passed=8, failed=0, skipped=0, drifts=())

    monkeypatch.setattr("repo_scaffold.cli.check_repository_settings", _fake_check)

    rc = main(["check", "settings", "--repo", "acme/repo", "--languages", "python"])
    assert rc == 0
    assert captured["languages"] == ["python"]


# ---------------------------------------------------------------------------
# project setup-views CLI dispatch
# ---------------------------------------------------------------------------


def test_project_setup_views_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI routes 'project setup-views' to setup_project_views and prints summary."""
    from unittest.mock import MagicMock

    from repo_scaffold.project_ops import ProjectMutationSummary

    summary = ProjectMutationSummary(
        action="setup-views",
        owner="acme",
        project_number=1,
        project_title="Test Project",
        failures=0,
        changed=True,
        metadata_file=None,
    )
    mock_fn = MagicMock(return_value=summary)
    monkeypatch.setattr("repo_scaffold.cli.setup_project_views", mock_fn)
    monkeypatch.setenv("GH_TOKEN", "tok")

    rc = main(["project", "setup-views", "--project-title", "Test Project"])
    assert rc == 0
    mock_fn.assert_called_once()


def test_project_setup_views_propagates_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import MagicMock

    mock_fn = MagicMock(side_effect=RuntimeError("API call failed"))
    monkeypatch.setattr("repo_scaffold.cli.setup_project_views", mock_fn)
    monkeypatch.setenv("GH_TOKEN", "tok")

    rc = main(["project", "setup-views", "--project-title", "Test Project"])
    assert rc != 0


# ---------------------------------------------------------------------------
# issue re-parent CLI dispatch
# ---------------------------------------------------------------------------


def test_issue_reparent_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess as _sub
    from unittest.mock import MagicMock

    ok = _sub.CompletedProcess(args=[], returncode=0, stdout="{}", stderr="")
    mock_node_id = MagicMock(return_value="I_20")
    mock_remove = MagicMock(return_value=ok)
    mock_add = MagicMock(return_value=ok)
    monkeypatch.setattr("repo_scaffold.cli.issue_node_id", mock_node_id)
    monkeypatch.setattr("repo_scaffold.cli.issue_remove_sub_issue", mock_remove)
    monkeypatch.setattr("repo_scaffold.cli.issue_add_sub_issue", mock_add)
    monkeypatch.setenv("GH_TOKEN", "tok")

    rc = main(
        [
            "issue",
            "re-parent",
            "--repo",
            "acme/repo",
            "--issue",
            "5",
            "--from-parent",
            "10",
            "--to-parent",
            "20",
        ]
    )
    assert rc == 0
    mock_node_id.assert_called_once_with("acme", "repo", 20, "tok")
    mock_remove.assert_called_once_with("acme", "repo", 10, 5, "tok")
    mock_add.assert_called_once_with("acme", "repo", 20, 5, "tok")


def test_issue_reparent_invalid_new_parent_returns_1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import MagicMock

    monkeypatch.setattr("repo_scaffold.cli.issue_node_id", MagicMock(return_value=None))
    monkeypatch.setenv("GH_TOKEN", "tok")

    rc = main(
        [
            "issue",
            "re-parent",
            "--repo",
            "acme/repo",
            "--issue",
            "5",
            "--from-parent",
            "10",
            "--to-parent",
            "99",
        ]
    )
    assert rc == 1


def test_issue_reparent_remove_failure_returns_1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess as _sub
    from unittest.mock import MagicMock

    fail = _sub.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="remove failed"
    )
    monkeypatch.setattr(
        "repo_scaffold.cli.issue_node_id", MagicMock(return_value="I_20")
    )
    mock_remove = MagicMock(return_value=fail)
    monkeypatch.setattr("repo_scaffold.cli.issue_remove_sub_issue", mock_remove)
    monkeypatch.setenv("GH_TOKEN", "tok")

    rc = main(
        [
            "issue",
            "re-parent",
            "--repo",
            "acme/repo",
            "--issue",
            "5",
            "--from-parent",
            "10",
            "--to-parent",
            "20",
        ]
    )
    assert rc == 1


def test_issue_reparent_add_failure_rolls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess as _sub
    from unittest.mock import MagicMock, call

    ok = _sub.CompletedProcess(args=[], returncode=0, stdout="{}", stderr="")
    fail = _sub.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="already has parent"
    )
    monkeypatch.setattr(
        "repo_scaffold.cli.issue_node_id", MagicMock(return_value="I_20")
    )
    monkeypatch.setattr(
        "repo_scaffold.cli.issue_remove_sub_issue", MagicMock(return_value=ok)
    )
    mock_add = MagicMock(side_effect=[fail, ok])
    monkeypatch.setattr("repo_scaffold.cli.issue_add_sub_issue", mock_add)
    monkeypatch.setenv("GH_TOKEN", "tok")

    rc = main(
        [
            "issue",
            "re-parent",
            "--repo",
            "acme/repo",
            "--issue",
            "5",
            "--from-parent",
            "10",
            "--to-parent",
            "20",
        ]
    )
    assert rc == 1
    assert mock_add.call_count == 2
    mock_add.assert_has_calls(
        [call("acme", "repo", 20, 5, "tok"), call("acme", "repo", 10, 5, "tok")]
    )


def test_issue_reparent_rollback_also_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess as _sub
    from unittest.mock import MagicMock

    ok = _sub.CompletedProcess(args=[], returncode=0, stdout="{}", stderr="")
    fail = _sub.CompletedProcess(args=[], returncode=1, stdout="", stderr="add failed")
    monkeypatch.setattr(
        "repo_scaffold.cli.issue_node_id", MagicMock(return_value="I_20")
    )
    monkeypatch.setattr(
        "repo_scaffold.cli.issue_remove_sub_issue", MagicMock(return_value=ok)
    )
    mock_add = MagicMock(side_effect=[fail, fail])
    monkeypatch.setattr("repo_scaffold.cli.issue_add_sub_issue", mock_add)
    monkeypatch.setenv("GH_TOKEN", "tok")

    rc = main(
        [
            "issue",
            "re-parent",
            "--repo",
            "acme/repo",
            "--issue",
            "5",
            "--from-parent",
            "10",
            "--to-parent",
            "20",
        ]
    )
    assert rc == 1
    assert mock_add.call_count == 2


# ---------------------------------------------------------------------------
# workspace configure-auth
# ---------------------------------------------------------------------------


def test_workspace_configure_auth_cli_returns_1_on_error(
    tmp_path: Path,
) -> None:
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()
    rc = main(["workspace", "configure-auth", "--path", str(not_a_repo)])
    assert rc == 1


def test_workspace_configure_auth_cli_defaults_to_cwd_on_error(
    tmp_path: Path,
) -> None:
    # autouse fixture already chdirs to tmp_path (not a git repo)
    rc = main(["workspace", "configure-auth"])
    assert rc == 1


def test_workspace_configure_auth_cli_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "repo_scaffold.workspace_ops.workspace_configure_auth",
        lambda token, path=None: SimpleNamespace(
            returncode=0, stdout="Configured.", stderr=""
        ),
    )
    rc = main(["workspace", "configure-auth"])
    assert rc == 0


# ---------------------------------------------------------------------------
# pr check-sop
# ---------------------------------------------------------------------------


def test_pr_check_sop_all_compliant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import json
    import subprocess

    monkeypatch.chdir(tmp_path)
    report = [
        {
            "thread_id": "PRRT_1",
            "first_comment_id": 100,
            "is_resolved": True,
            "has_reply": True,
            "has_plus_one": True,
            "compliant": True,
            "missing": [],
        }
    ]
    monkeypatch.setattr(
        "repo_scaffold.cli.pr_check_sop",
        lambda owner, repo, number, token: subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(report), stderr=""
        ),
    )
    monkeypatch.setattr("repo_scaffold.cli.token_from_repo", lambda _: "tok")

    rc = main(["pr", "check-sop", "--repo", "acme/repo", "--pr-number", "42"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "OK" in out
    assert "1/1 threads SOP-compliant" in out


def test_pr_check_sop_non_compliant_exits_nonzero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import json
    import subprocess

    monkeypatch.chdir(tmp_path)
    report = [
        {
            "thread_id": "PRRT_2",
            "first_comment_id": 200,
            "is_resolved": False,
            "has_reply": False,
            "has_plus_one": False,
            "compliant": False,
            "missing": ["reply", "resolved", "reaction(+1)"],
        }
    ]
    monkeypatch.setattr(
        "repo_scaffold.cli.pr_check_sop",
        lambda owner, repo, number, token: subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(report), stderr=""
        ),
    )
    monkeypatch.setattr("repo_scaffold.cli.token_from_repo", lambda _: "tok")

    rc = main(["pr", "check-sop", "--repo", "acme/repo", "--pr-number", "7"])
    assert rc != 0
    out = capsys.readouterr().out
    assert "FAIL" in out
    assert "reply" in out
    assert "0/1 threads SOP-compliant" in out


def test_pr_check_sop_no_threads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import json
    import subprocess

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "repo_scaffold.cli.pr_check_sop",
        lambda owner, repo, number, token: subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps([]), stderr=""
        ),
    )
    monkeypatch.setattr("repo_scaffold.cli.token_from_repo", lambda _: "tok")

    rc = main(["pr", "check-sop", "--repo", "acme/repo", "--pr-number", "1"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "No review threads" in out


def test_pr_check_sop_api_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import subprocess

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "repo_scaffold.cli.pr_check_sop",
        lambda owner, repo, number, token: subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="GraphQL error"
        ),
    )
    monkeypatch.setattr("repo_scaffold.cli.token_from_repo", lambda _: "tok")

    rc = main(["pr", "check-sop", "--repo", "acme/repo", "--pr-number", "9"])
    assert rc != 0


def test_pr_check_sop_json_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import json
    import subprocess

    monkeypatch.chdir(tmp_path)
    report = [
        {
            "thread_id": "PRRT_3",
            "first_comment_id": 300,
            "is_resolved": True,
            "has_reply": True,
            "has_plus_one": True,
            "compliant": True,
            "missing": [],
        }
    ]
    monkeypatch.setattr(
        "repo_scaffold.cli.pr_check_sop",
        lambda owner, repo, number, token: subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(report), stderr=""
        ),
    )
    monkeypatch.setattr("repo_scaffold.cli.token_from_repo", lambda _: "tok")

    rc = main(["pr", "check-sop", "--repo", "acme/repo", "--pr-number", "3", "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed[0]["thread_id"] == "PRRT_3"


def test_pr_check_sop_json_output_non_compliant_exits_nonzero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import json
    import subprocess

    monkeypatch.chdir(tmp_path)
    report = [
        {
            "thread_id": "PRRT_4",
            "first_comment_id": 400,
            "is_resolved": False,
            "has_reply": False,
            "has_plus_one": False,
            "compliant": False,
            "missing": ["reply", "resolved", "reaction(+1)"],
        }
    ]
    monkeypatch.setattr(
        "repo_scaffold.cli.pr_check_sop",
        lambda owner, repo, number, token: subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(report), stderr=""
        ),
    )
    monkeypatch.setattr("repo_scaffold.cli.token_from_repo", lambda _: "tok")

    rc = main(["pr", "check-sop", "--repo", "acme/repo", "--pr-number", "4", "--json"])
    assert rc == 1
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed[0]["compliant"] is False


# ---------------------------------------------------------------------------
# docker subcommands
# ---------------------------------------------------------------------------


def _docker_ok(stdout: str = "ok") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


def _docker_err(stderr: str = "err") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr=stderr)


def test_docker_build_base_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("repo_scaffold.cli.token_from_repo", lambda _: "tok")
    monkeypatch.setattr(
        "repo_scaffold.docker_ops.docker_build_base",
        lambda repo, path: _docker_ok("Built repo-scaffold-base:latest"),
    )

    rc = main(["docker", "build-base", "--repo", "owner/repo-scaffold"])
    assert rc == 0
    assert "Built" in capsys.readouterr().out


def test_docker_build_base_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("repo_scaffold.cli.token_from_repo", lambda _: "tok")
    monkeypatch.setattr(
        "repo_scaffold.docker_ops.docker_build_base",
        lambda repo, path: _docker_err("No Dockerfile found"),
    )

    rc = main(["docker", "build-base", "--repo", "owner/repo-scaffold"])
    assert rc == 1
    assert "No Dockerfile" in capsys.readouterr().err


def test_docker_spin_up_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("repo_scaffold.cli.token_from_repo", lambda _: "tok")
    monkeypatch.setattr(
        "repo_scaffold.docker_ops.docker_spin_up",
        lambda repo, branch, token, env_path=None: _docker_ok(
            "Started container: owner-main"
        ),
    )

    rc = main(["docker", "spin-up", "--repo", "owner/myrepo", "--branch", "main"])
    assert rc == 0
    assert "Started" in capsys.readouterr().out


def test_docker_spin_up_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("repo_scaffold.cli.token_from_repo", lambda _: "tok")
    monkeypatch.setattr(
        "repo_scaffold.docker_ops.docker_spin_up",
        lambda repo, branch, token, env_path=None: _docker_err("already exists"),
    )

    rc = main(["docker", "spin-up", "--repo", "owner/myrepo", "--branch", "main"])
    assert rc == 1
    assert "already exists" in capsys.readouterr().err


def test_docker_spin_down_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("repo_scaffold.cli.token_from_repo", lambda _: "tok")
    monkeypatch.setattr(
        "repo_scaffold.docker_ops.docker_spin_down",
        lambda repo, branch: _docker_ok("Removed container: owner-main"),
    )

    rc = main(["docker", "spin-down", "--repo", "owner/myrepo", "--branch", "main"])
    assert rc == 0
    assert "Removed" in capsys.readouterr().out


def test_docker_spin_down_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("repo_scaffold.cli.token_from_repo", lambda _: "tok")
    monkeypatch.setattr(
        "repo_scaffold.docker_ops.docker_spin_down",
        lambda repo, branch: _docker_err("Container not found"),
    )

    rc = main(["docker", "spin-down", "--repo", "owner/myrepo", "--branch", "main"])
    assert rc == 1
    assert "not found" in capsys.readouterr().err


def test_docker_list_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("repo_scaffold.cli.token_from_repo", lambda _: "tok")
    monkeypatch.setattr(
        "repo_scaffold.docker_ops.docker_list",
        lambda repo=None: _docker_ok("running  myrepo-main"),
    )

    rc = main(["docker", "list"])
    assert rc == 0
    assert "myrepo-main" in capsys.readouterr().out


def test_docker_list_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("repo_scaffold.cli.token_from_repo", lambda _: "tok")
    monkeypatch.setattr(
        "repo_scaffold.docker_ops.docker_list",
        lambda repo=None: _docker_err("Failed to list containers"),
    )

    rc = main(["docker", "list"])
    assert rc == 1
    assert "Failed to list" in capsys.readouterr().err
