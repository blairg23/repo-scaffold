from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import repo_scaffold.backlog_ops as backlog_ops


def _cp_ok(stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["gh"], returncode=0, stdout=stdout, stderr="")


def test_apply_backlog_creates_missing_labels(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    monkeypatch.setattr(backlog_ops, "_find_issue_number", lambda repo_dir, repo, title: None)

    created_labels: list[str] = []

    def _fake_run_gh(repo_dir: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
        if args == ["api", "/user"]:
            return _cp_ok('{"login":"octocat"}')
        if args[:2] == ["api", "--paginate"] and "milestones" in args[2]:
            return _cp_ok("[]")
        if args[:2] == ["api", "--paginate"] and "labels" in args[2]:
            return _cp_ok("[]")
        if args[:3] == ["api", "--method", "POST"] and args[3].endswith("/milestones"):
            return _cp_ok("{}")
        if args[:3] == ["api", "--method", "POST"] and args[3].endswith("/labels"):
            for idx, token in enumerate(args):
                if token == "-f" and idx + 1 < len(args) and args[idx + 1].startswith("name="):
                    created_labels.append(args[idx + 1].split("=", 1)[1])
                    break
            return _cp_ok("{}")
        raise AssertionError(f"Unexpected gh invocation: {args}")

    monkeypatch.setattr(backlog_ops, "_run_gh", _fake_run_gh)

    created_issue_numbers = [1, 2]

    def _fake_create_issue(
        repo_dir: Path,
        repo: str,
        title: str,
        body: str,
        labels: list[str],
        assignees: list[str],
        milestone: str | None,
    ) -> int:
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
    assert set(created_labels) == {"epic", "ticket", "epic:E2E"}


def test_ensure_missing_labels_dry_run_updates_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    existing = {"epic"}
    calls: list[list[str]] = []

    def _fake_run_gh(repo_dir: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
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


def test_resolve_authenticated_login_returns_login(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)
    monkeypatch.setattr(backlog_ops, "_ensure_gh_auth", lambda _: None)
    monkeypatch.setattr(backlog_ops, "_run_gh", lambda _repo_dir, _args: _cp_ok('{"login":"octocat"}'))

    assert backlog_ops.resolve_authenticated_login(repo_dir) == "octocat"
