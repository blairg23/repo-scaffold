from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from repo_scaffold.delete_ops import delete_repositories


def _cp(
    args: list[str], *, code: int, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=args, returncode=code, stdout=stdout, stderr=stderr
    )


def test_delete_repositories_dry_run_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_ORG", "acme")
    monkeypatch.delenv("GH_REPO", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.setattr(
        "repo_scaffold.delete_ops.shutil.which", lambda _name: "/usr/bin/gh"
    )

    calls: list[list[str]] = []

    def _fake_run_gh(_cwd: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args == ["auth", "status"]:
            return _cp(args, code=0)
        if args == ["repo", "list", "acme", "--limit", "1000", "--json", "name"]:
            return _cp(
                args,
                code=0,
                stdout='[{"name":"repo-scaffold-e2e"},{"name":"repo-scaffold-e2e-20260311"},{"name":"other"}]',
            )
        raise AssertionError(f"unexpected gh args: {args}")

    monkeypatch.setattr("repo_scaffold.delete_ops._run_gh", _fake_run_gh)

    out_lines: list[str] = []
    err_lines: list[str] = []
    summary = delete_repositories(
        owner=None,
        prefix="repo-scaffold-e2e",
        exact_names=(),
        include_local=False,
        delete_local_only=False,
        local_roots=(),
        apply=False,
        assume_yes=False,
        prompt=lambda _msg: "y",
        is_tty=True,
        cwd=tmp_path,
        out=out_lines.append,
        err=err_lines.append,
    )

    assert summary.owner == "acme"
    assert summary.remote_matched == 2
    assert summary.remote_deleted == 0
    assert summary.remote_skipped == 2
    assert summary.remote_failures == 0
    assert summary.local_matched == 0
    assert summary.local_deleted == 0
    assert summary.local_skipped == 0
    assert summary.local_failures == 0
    assert any("Dry-run only" in line for line in out_lines)
    assert not any(args[:2] == ["repo", "delete"] for args in calls)
    assert err_lines == []


def test_delete_repositories_apply_exact_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GH_REPO", "github.com/acme/some-repo")
    monkeypatch.delenv("GITHUB_ORG", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.setattr(
        "repo_scaffold.delete_ops.shutil.which", lambda _name: "/usr/bin/gh"
    )

    deleted_calls: list[str] = []

    def _fake_run_gh(_cwd: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
        if args == ["auth", "status"]:
            return _cp(args, code=0)
        if args == ["repo", "list", "acme", "--limit", "1000", "--json", "name"]:
            return _cp(
                args,
                code=0,
                stdout=(
                    '[{"name":"repo-scaffold-e2e"},'
                    '{"name":"repo-scaffold-e2e-20260311001924"},'
                    '{"name":"repo-scaffold-e2e-unwanted"},'
                    '{"name":"keep-me"}]'
                ),
            )
        if len(args) == 4 and args[:2] == ["repo", "delete"]:
            deleted_calls.append(args[2])
            if args[2].endswith("20260311001924"):
                return _cp(args, code=1, stderr="forbidden")
            return _cp(args, code=0)
        raise AssertionError(f"unexpected gh args: {args}")

    monkeypatch.setattr("repo_scaffold.delete_ops._run_gh", _fake_run_gh)

    out_lines: list[str] = []
    err_lines: list[str] = []
    summary = delete_repositories(
        owner=None,
        prefix="repo-scaffold-e2e",
        exact_names=("repo-scaffold-e2e", "repo-scaffold-e2e-20260311001924"),
        include_local=False,
        delete_local_only=False,
        local_roots=(),
        apply=True,
        assume_yes=False,
        prompt=lambda _msg: "y",
        is_tty=True,
        cwd=tmp_path,
        out=out_lines.append,
        err=err_lines.append,
    )

    assert summary.owner == "acme"
    assert summary.remote_matched == 2
    assert summary.remote_deleted == 1
    assert summary.remote_failures == 1
    assert summary.local_matched == 0
    assert summary.local_deleted == 0
    assert summary.local_failures == 0
    assert deleted_calls == [
        "acme/repo-scaffold-e2e",
        "acme/repo-scaffold-e2e-20260311001924",
    ]
    assert all("repo-scaffold-e2e-unwanted" not in call for call in deleted_calls)
    assert any(
        line.startswith("FAILED  acme/repo-scaffold-e2e-20260311001924")
        for line in err_lines
    )


def test_delete_repositories_delete_local_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local_root = tmp_path / "sandbox"
    local_root.mkdir(parents=True)
    match_a = local_root / "repo-scaffold-e2e"
    match_b = local_root / "repo-scaffold-e2e-20260311"
    keep = local_root / "keep-me"
    for path in (match_a, match_b, keep):
        path.mkdir(parents=True)
        (path / "marker.txt").write_text("x", encoding="utf-8")

    def _unexpected_gh(
        _cwd: Path, _args: list[str]
    ) -> subprocess.CompletedProcess[str]:
        raise AssertionError("gh should not be called in --delete-local mode")

    monkeypatch.setattr("repo_scaffold.delete_ops._run_gh", _unexpected_gh)

    out_lines: list[str] = []
    err_lines: list[str] = []
    summary = delete_repositories(
        owner=None,
        prefix="repo-scaffold-e2e",
        exact_names=(),
        include_local=True,
        delete_local_only=True,
        local_roots=(str(local_root),),
        apply=True,
        assume_yes=True,
        prompt=lambda _msg: "n",
        is_tty=False,
        cwd=tmp_path,
        out=out_lines.append,
        err=err_lines.append,
    )

    assert summary.owner is None
    assert summary.remote_matched == 0
    assert summary.remote_deleted == 0
    assert summary.remote_failures == 0
    assert summary.local_matched == 2
    assert summary.local_deleted == 2
    assert summary.local_failures == 0
    assert not match_a.exists()
    assert not match_b.exists()
    assert keep.exists()
    assert err_lines == []


def test_delete_repositories_cleanup_remote_and_local_dry_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_ORG", "acme")
    monkeypatch.delenv("GH_REPO", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.setattr(
        "repo_scaffold.delete_ops.shutil.which", lambda _name: "/usr/bin/gh"
    )

    local_root = tmp_path / "local-root"
    local_root.mkdir(parents=True)
    (local_root / "repo-scaffold-e2e-abc").mkdir(parents=True)
    (local_root / "other").mkdir(parents=True)

    def _fake_run_gh(_cwd: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
        if args == ["auth", "status"]:
            return _cp(args, code=0)
        if args == ["repo", "list", "acme", "--limit", "1000", "--json", "name"]:
            return _cp(args, code=0, stdout='[{"name":"repo-scaffold-e2e-abc"}]')
        raise AssertionError(f"unexpected gh args: {args}")

    monkeypatch.setattr("repo_scaffold.delete_ops._run_gh", _fake_run_gh)

    out_lines: list[str] = []
    err_lines: list[str] = []
    summary = delete_repositories(
        owner=None,
        prefix="repo-scaffold-e2e",
        exact_names=(),
        include_local=True,
        delete_local_only=False,
        local_roots=(str(local_root),),
        apply=False,
        assume_yes=False,
        prompt=lambda _msg: "n",
        is_tty=True,
        cwd=tmp_path,
        out=out_lines.append,
        err=err_lines.append,
    )

    assert summary.remote_matched == 1
    assert summary.remote_deleted == 0
    assert summary.remote_skipped == 1
    assert summary.local_matched == 1
    assert summary.local_deleted == 0
    assert summary.local_skipped == 1
    assert summary.failures == 0
    assert any("Matched remote repositories" in line for line in out_lines)
    assert any("Matched local directories" in line for line in out_lines)
    assert err_lines == []
