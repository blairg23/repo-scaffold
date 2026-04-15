from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

import repo_scaffold.backlog_ops as backlog_ops


def _cp_ok(stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["gh"], returncode=0, stdout=stdout, stderr=""
    )


def test_apply_backlog_creates_missing_labels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)
    backlog_file = repo_dir / "backlog.json"
    backlog_file.write_text(
        json.dumps(
            {
                "epics": [
                    {
                        "key": "E2E",
                        "title": "Epic e2e",
                        "body": "epic body",
                        "labels": ["epic"],
                        "tickets": [
                            {
                                "title": "Ticket e2e",
                                "body": "ticket body",
                                "labels": ["ticket", "epic:E2E"],
                                "assignees": [],
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(backlog_ops, "_ensure_gh_auth", lambda repo_dir: None)
    monkeypatch.setattr(
        backlog_ops, "_find_issue_number", lambda repo_dir, repo, title: None
    )

    created_labels: list[str] = []

    def _fake_run_gh(
        repo_dir: Path, args: list[str]
    ) -> subprocess.CompletedProcess[str]:
        if args == ["api", "/user"]:
            return _cp_ok('{"login":"octocat"}')
        if args[:2] == ["api", "--paginate"] and "milestones" in args[2]:
            return _cp_ok("[]")
        if args[:2] == ["api", "--paginate"] and "labels" in args[2]:
            return _cp_ok("[]")
        if args[:2] == ["api", "--paginate"] and "issues" in args[2]:
            return _cp_ok(
                json.dumps(
                    [
                        {"number": 1, "title": "Epic e2e"},
                        {"number": 2, "title": "Ticket e2e"},
                    ]
                )
            )
        if args[:3] == ["api", "--method", "POST"] and args[3].endswith("/milestones"):
            return _cp_ok("{}")
        if args[:3] == ["api", "--method", "POST"] and args[3].endswith("/labels"):
            for idx, token in enumerate(args):
                if (
                    token == "-f"
                    and idx + 1 < len(args)
                    and args[idx + 1].startswith("name=")
                ):
                    created_labels.append(args[idx + 1].split("=", 1)[1])
                    break
            return _cp_ok("{}")
        raise AssertionError(f"Unexpected gh invocation: {args}")

    monkeypatch.setattr(backlog_ops, "_run_gh", _fake_run_gh)

    created_issue_numbers = [1, 2]
    created_issues: list[dict[str, object]] = []

    def _fake_create_issue(
        repo_dir: Path,
        repo: str,
        title: str,
        body: str,
        labels: list[str],
        assignees: list[str],
        milestone: str | None,
    ) -> int:
        created_issues.append(
            {
                "title": title,
                "body": body,
                "labels": labels,
                "assignees": assignees,
                "milestone": milestone,
            }
        )
        return created_issue_numbers.pop(0)

    monkeypatch.setattr(backlog_ops, "_create_issue", _fake_create_issue)

    summary = backlog_ops.apply_backlog(
        repo_dir=repo_dir,
        repo="acme/repo",
        backlog_file=backlog_file,
        dry_run=False,
        out=lambda _: None,
        err=lambda _: None,
    )

    assert summary.failures == 0
    assert summary.milestones_created == 1
    assert summary.issues_created == 2
    assert summary.epic_issues_created == 1
    assert summary.ticket_issues_created == 1
    assert summary.epic_issues_skipped == 0
    assert summary.ticket_issues_skipped == 0
    assert set(created_labels) == {"epic", "ticket", "epic:E2E"}
    assert len(created_issues) == 2
    assert created_issues[0]["title"] == "Epic e2e"
    assert created_issues[0]["labels"] == ["epic", "epic:E2E"]
    assert created_issues[0]["milestone"] is None
    assert created_issues[1]["title"] == "Ticket e2e"
    assert str(created_issues[1]["body"]).startswith("Epic: #1\n\n")
    assert created_issues[1]["milestone"] == "Epic e2e"


def test_ensure_missing_labels_dry_run_updates_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = {"epic"}
    calls: list[list[str]] = []

    def _fake_run_gh(
        repo_dir: Path, args: list[str]
    ) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return _cp_ok("{}")

    monkeypatch.setattr(backlog_ops, "_run_gh", _fake_run_gh)
    failures = backlog_ops._ensure_missing_labels(
        repo_dir=Path("."),
        repo="acme/repo",
        existing_labels=existing,
        required_labels=["epic", "ticket"],
        dry_run=True,
        out=lambda _: None,
        emit_err=lambda _: None,
    )

    assert failures == 0
    assert existing == {"epic", "ticket"}
    assert calls == []


def test_resolve_authenticated_login_returns_login(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)
    monkeypatch.setattr(backlog_ops, "_ensure_gh_auth", lambda _: None)
    monkeypatch.setattr(
        backlog_ops, "_run_gh", lambda _repo_dir, _args: _cp_ok('{"login":"octocat"}')
    )

    assert backlog_ops.resolve_authenticated_login(repo_dir) == "octocat"


def test_backlog_helper_parsers_and_label_merging() -> None:
    assert (
        backlog_ops._project_scope_hint("missing required token scopes")
        != "missing required token scopes"
    )
    assert backlog_ops._project_scope_hint("plain error") == "plain error"
    assert backlog_ops._parse_repo_owner("acme/repo") == "acme"
    with pytest.raises(RuntimeError, match="owner/repo"):
        backlog_ops._parse_repo_owner("broken")

    merged = backlog_ops._merge_labels(
        backlog_ops._normalize_labels([" epic ", "", "epic", 1]),
        ["ticket", "epic", " ticket "],
    )
    assert merged == ["epic", "ticket"]

    raw = '[{"title":"one"}]\n [{"title":"two"}, 1]\n'
    assert backlog_ops._parse_concatenated_json_arrays(raw) == [
        {"title": "one"},
        {"title": "two"},
    ]
    assert len(backlog_ops._label_color("epic")) == 6


def test_load_token_from_env_file_and_ensure_gh_auth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    env_file = repo_dir / ".env"
    env_file.write_text('export github_token="token-from-env"\n', encoding="utf-8")

    monkeypatch.delenv("GH_TOKEN", raising=False)
    backlog_ops._load_token_from_env_file(env_file)
    assert os.environ["GH_TOKEN"] == "token-from-env"

    monkeypatch.setattr(
        backlog_ops.shutil,
        "which",
        lambda tool: "/usr/bin/gh" if tool == "gh" else None,
    )
    monkeypatch.setattr(backlog_ops.subprocess, "run", lambda *args, **kwargs: _cp_ok())
    backlog_ops._ensure_gh_auth(repo_dir)

    monkeypatch.delenv("GH_TOKEN", raising=False)
    clean_repo_dir = tmp_path / "clean-repo"
    clean_repo_dir.mkdir()
    monkeypatch.chdir(clean_repo_dir)
    monkeypatch.setattr(
        backlog_ops.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=["gh", "auth", "status"], returncode=1, stdout="", stderr=""
        ),
    )
    with pytest.raises(RuntimeError, match="Authenticate first"):
        backlog_ops._ensure_gh_auth(clean_repo_dir)


def test_find_issue_number_uses_repo_issues_api_exact_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)

    def _fake_run_gh(
        _repo_dir: Path, args: list[str]
    ) -> subprocess.CompletedProcess[str]:
        assert args == [
            "api",
            "--paginate",
            "/repos/acme/repo/issues?state=all&per_page=100",
        ]
        return _cp_ok(
            json.dumps(
                [
                    {"number": 10, "title": "Some other issue"},
                    {
                        "number": 11,
                        "title": "Same title, but PR",
                        "pull_request": {"url": "https://example.test/pr"},
                    },
                    {"number": 12, "title": "Wanted issue"},
                ]
            )
        )

    monkeypatch.setattr(backlog_ops, "_run_gh", _fake_run_gh)

    assert backlog_ops._find_issue_number(repo_dir, "acme/repo", "Wanted issue") == 12
    assert (
        backlog_ops._find_issue_number(repo_dir, "acme/repo", "Missing issue") is None
    )


def test_resolve_project_target_variants_and_project_linking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)
    lines: list[str] = []
    errors: list[str] = []

    monkeypatch.setattr(
        backlog_ops,
        "_list_projects",
        lambda *_args, **_kwargs: [{"title": "Roadmap", "number": 7}],
    )
    monkeypatch.setattr(
        backlog_ops,
        "_project_title_for_number",
        lambda *_args, **_kwargs: "Exact Project",
    )

    target = backlog_ops._resolve_project_target(
        repo_dir=repo_dir,
        repo="acme/repo",
        project_number=7,
        project_title=None,
        project_owner=None,
        dry_run=False,
        out=lines.append,
    )
    assert target == backlog_ops._ProjectTarget(
        owner="acme", number=7, title="Exact Project", created=False
    )

    target = backlog_ops._resolve_project_target(
        repo_dir=repo_dir,
        repo="acme/repo",
        project_number=None,
        project_title="Roadmap",
        project_owner=None,
        dry_run=False,
        out=lines.append,
    )
    assert target == backlog_ops._ProjectTarget(
        owner="acme", number=7, title="Roadmap", created=False
    )

    target = backlog_ops._resolve_project_target(
        repo_dir=repo_dir,
        repo="acme/repo",
        project_number=None,
        project_title="New Roadmap",
        project_owner="octo-org",
        dry_run=True,
        out=lines.append,
    )
    assert target == backlog_ops._ProjectTarget(
        owner="octo-org", number=None, title="New Roadmap", created=True
    )

    with pytest.raises(
        RuntimeError, match="only one of --project-number or --project-title"
    ):
        backlog_ops._resolve_project_target(
            repo_dir=repo_dir,
            repo="acme/repo",
            project_number=1,
            project_title="dup",
            project_owner=None,
            dry_run=False,
            out=lines.append,
        )

    monkeypatch.setattr(
        backlog_ops,
        "_run_gh",
        lambda _repo_dir, args: (
            _cp_ok()
            if args[:2] == ["project", "link"]
            else subprocess.CompletedProcess(
                args=["gh"], returncode=1, stdout="", stderr="already linked"
            )
        ),
    )

    assert (
        backlog_ops._link_project_to_repo(
            repo_dir=repo_dir,
            repo="acme/repo",
            project=backlog_ops._ProjectTarget(
                owner="acme", number=None, title="New Roadmap", created=True
            ),
            dry_run=True,
            out=lines.append,
            emit_err=errors.append,
        )
        == 0
    )
    assert (
        backlog_ops._link_project_to_repo(
            repo_dir=repo_dir,
            repo="acme/repo",
            project=backlog_ops._ProjectTarget(
                owner="acme", number=7, title="Roadmap", created=False
            ),
            dry_run=False,
            out=lines.append,
            emit_err=errors.append,
        )
        == 0
    )


def test_add_issue_to_project_handles_dry_run_existing_missing_number_and_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)
    lines: list[str] = []
    errors: list[str] = []
    project = backlog_ops._ProjectTarget(
        owner="acme", number=7, title="Roadmap", created=False
    )

    assert backlog_ops._add_issue_to_project(
        repo_dir=repo_dir,
        repo="acme/repo",
        project=project,
        issue_title="A1",
        issue_number=None,
        dry_run=True,
        out=lines.append,
        emit_err=errors.append,
    ) == (1, 0, 0)

    assert backlog_ops._add_issue_to_project(
        repo_dir=repo_dir,
        repo="acme/repo",
        project=project,
        issue_title="A1",
        issue_number=None,
        dry_run=False,
        out=lines.append,
        emit_err=errors.append,
    ) == (0, 0, 1)

    states = iter(
        [
            subprocess.CompletedProcess(
                args=["gh"], returncode=1, stdout="", stderr="already added"
            ),
            subprocess.CompletedProcess(
                args=["gh"], returncode=1, stdout="", stderr="boom"
            ),
        ]
    )
    monkeypatch.setattr(backlog_ops, "_run_gh", lambda *_args, **_kwargs: next(states))

    assert backlog_ops._add_issue_to_project(
        repo_dir=repo_dir,
        repo="acme/repo",
        project=project,
        issue_title="A1",
        issue_number=11,
        dry_run=False,
        out=lines.append,
        emit_err=errors.append,
    ) == (0, 1, 0)
    assert backlog_ops._add_issue_to_project(
        repo_dir=repo_dir,
        repo="acme/repo",
        project=project,
        issue_title="A2",
        issue_number=12,
        dry_run=False,
        out=lines.append,
        emit_err=errors.append,
    ) == (0, 0, 1)
    assert any("Failed to add issue to project" in line for line in errors)


def test_wait_for_issue_titles_visible_retries_until_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)

    scans: list[int] = []

    def _fake_list_repo_issues(_repo_dir: Path, _repo: str) -> list[dict[str, object]]:
        scans.append(len(scans))
        if len(scans) < 3:
            return [{"number": 10, "title": "Epic A"}]
        return [{"number": 10, "title": "Epic A"}, {"number": 11, "title": "A1"}]

    monkeypatch.setattr(backlog_ops, "_list_repo_issues", _fake_list_repo_issues)
    monkeypatch.setattr(backlog_ops.time, "sleep", lambda _seconds: None)

    backlog_ops._wait_for_issue_titles_visible(repo_dir, "acme/repo", ["Epic A", "A1"])

    assert len(scans) == 3


def test_apply_backlog_project_title_creates_and_adds_items(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)
    backlog_file = repo_dir / "backlog.json"
    backlog_file.write_text(
        json.dumps(
            {
                "epics": [
                    {
                        "key": "A",
                        "title": "Epic A",
                        "body": "epic body",
                        "labels": [],
                        "tickets": [
                            {
                                "title": "A1",
                                "body": "ticket body",
                                "labels": [],
                                "assignees": [],
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(backlog_ops, "_ensure_gh_auth", lambda _: None)

    numbers = {"Epic A": 10, "A1": 11}
    monkeypatch.setattr(
        backlog_ops,
        "_find_issue_number",
        lambda _repo_dir, _repo, title: numbers.get(title),
    )
    monkeypatch.setattr(backlog_ops, "_create_issue", lambda *args, **kwargs: 999)

    calls: list[list[str]] = []

    def _fake_run_gh(
        _repo_dir: Path, args: list[str]
    ) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args == ["api", "/user"]:
            return _cp_ok('{"login":"octocat"}')
        if args[:2] == ["api", "--paginate"] and "milestones" in args[2]:
            return _cp_ok("[]")
        if args[:2] == ["api", "--paginate"] and "labels" in args[2]:
            return _cp_ok("[]")
        if args[:3] == ["api", "--method", "POST"] and args[3].endswith("/milestones"):
            return _cp_ok("{}")
        if args[:3] == ["api", "--method", "POST"] and args[3].endswith("/labels"):
            return _cp_ok("{}")
        if args[:5] == ["project", "list", "--owner", "acme", "--limit"]:
            return _cp_ok("[]")
        if args[:4] == ["project", "create", "--owner", "acme"]:
            return _cp_ok('{"number":42,"title":"Roadmap"}')
        if args[:4] == ["project", "link", "42", "--owner"]:
            return _cp_ok("")
        if args[:4] == ["project", "item-add", "42", "--owner"]:
            return _cp_ok("{}")
        raise AssertionError(f"Unexpected gh invocation: {args}")

    monkeypatch.setattr(backlog_ops, "_run_gh", _fake_run_gh)

    summary = backlog_ops.apply_backlog(
        repo_dir=repo_dir,
        repo="acme/repo",
        backlog_file=backlog_file,
        dry_run=False,
        project_title="Roadmap",
        project_owner="acme",
        out=lambda _: None,
        err=lambda _: None,
    )

    assert summary.failures == 0
    assert summary.project_created is True
    assert summary.project_items_added == 2
    assert summary.project_items_skipped == 0
    assert summary.milestones_created == 1
    assert summary.issues_skipped == 2
    assert summary.epic_issues_skipped == 1
    assert summary.ticket_issues_skipped == 1


def test_list_projects_supports_wrapped_json_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)

    monkeypatch.setattr(
        backlog_ops,
        "_run_gh",
        lambda _repo_dir, _args: _cp_ok(
            '{"projects":[{"number":1,"title":"Roadmap"}]}'
        ),
    )

    projects = backlog_ops._list_projects(repo_dir, "acme")
    assert projects == [{"number": 1, "title": "Roadmap"}]


def test_apply_backlog_requires_epic_body_and_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)
    backlog_file = repo_dir / "backlog.json"
    backlog_file.write_text(
        json.dumps(
            {
                "epics": [
                    {
                        "key": "",
                        "title": "Epic Missing Fields",
                        "body": "",
                        "tickets": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(backlog_ops, "_ensure_gh_auth", lambda _: None)

    def _fake_run_gh(
        _repo_dir: Path, args: list[str]
    ) -> subprocess.CompletedProcess[str]:
        if args == ["api", "/user"]:
            return _cp_ok('{"login":"octocat"}')
        if args[:2] == ["api", "--paginate"] and "milestones" in args[2]:
            return _cp_ok("[]")
        if args[:2] == ["api", "--paginate"] and "labels" in args[2]:
            return _cp_ok("[]")
        raise AssertionError(f"Unexpected gh invocation: {args}")

    monkeypatch.setattr(backlog_ops, "_run_gh", _fake_run_gh)

    errors: list[str] = []
    summary = backlog_ops.apply_backlog(
        repo_dir=repo_dir,
        repo="acme/repo",
        backlog_file=backlog_file,
        dry_run=False,
        out=lambda _: None,
        err=errors.append,
    )

    assert summary.failures == 1
    assert errors
    assert "epic.key, epic.title, epic.body are required" in errors[0]
