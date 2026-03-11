from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import repo_scaffold.create_ops as create_ops


def test_resolve_repo_rejects_empty_owner_or_name() -> None:
    with pytest.raises(RuntimeError, match="owner/repo format"):
        create_ops._resolve_repo(
            repo_dir=Path("/tmp/example"),
            env={},
            repo="/demo",
            owner=None,
            name=None,
        )

    with pytest.raises(RuntimeError, match="owner/repo format"):
        create_ops._resolve_repo(
            repo_dir=Path("/tmp/example"),
            env={},
            repo="acme/",
            owner=None,
            name=None,
        )


def test_resolve_repo_accepts_host_owner_repo_from_env() -> None:
    resolved = create_ops._resolve_repo(
        repo_dir=Path("/tmp/example"),
        env={"GH_REPO": "github.com/acme/demo"},
        repo=None,
        owner=None,
        name=None,
    )
    assert resolved == "acme/demo"


def test_create_or_push_repo_uses_absolute_source_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    repo_dir = Path("rel-repo")
    repo_dir.mkdir(parents=True)

    run_calls: list[tuple[list[str], Path]] = []

    def _fake_repo_exists(*, repo_dir: Path, env: dict[str, str], repo: str) -> bool:
        assert repo_dir == repo_dir.resolve()
        return False

    def _fake_run(
        args: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        stdin_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        run_calls.append((args, cwd))
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    def _fake_push_main(*, repo_dir: Path, env: dict[str, str]) -> tuple[bool, str | None]:
        return True, None

    monkeypatch.setattr(create_ops, "_repo_exists", _fake_repo_exists)
    monkeypatch.setattr(create_ops, "_run", _fake_run)
    monkeypatch.setattr(create_ops, "_push_main", _fake_push_main)

    created, pushed, error = create_ops._create_or_push_repo(
        repo_dir=repo_dir,
        env={},
        repo="acme/example",
        visibility="public",
        dry_run=False,
        out=lambda _: None,
    )

    assert created is True
    assert pushed is True
    assert error is None
    assert run_calls, "Expected gh repo create call"
    gh_call_args, gh_call_cwd = run_calls[0]
    assert gh_call_args[:4] == ["gh", "repo", "create", "acme/example"]
    assert "--source" in gh_call_args
    source_idx = gh_call_args.index("--source") + 1
    assert Path(gh_call_args[source_idx]).is_absolute()
    assert gh_call_cwd.is_absolute()


def test_ensure_git_repo_initializes_when_inside_parent_repo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "out" / "child-repo"
    repo_dir.mkdir(parents=True)

    calls: list[list[str]] = []

    def _fake_run(
        args: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        stdin_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args == ["git", "rev-parse", "--show-toplevel"]:
            # Simulate being inside a parent git repo, not repo_dir itself.
            return subprocess.CompletedProcess(args=args, returncode=0, stdout=str(tmp_path), stderr="")
        if args == ["git", "init"]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
        if args == ["git", "rev-parse", "--verify", "HEAD"]:
            return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="")
        if args == ["git", "config", "user.name"]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="Tester\n", stderr="")
        if args == ["git", "config", "user.email"]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="tester@example.com\n", stderr="")
        if args == ["git", "add", "-A"]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
        if args == ["git", "diff", "--cached", "--quiet"]:
            return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="")
        if args == ["git", "commit", "-m", "Initial scaffold"]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
        raise AssertionError(f"Unexpected git args: {args}")

    monkeypatch.setattr(create_ops, "_run", _fake_run)

    create_ops._ensure_git_repo(
        repo_dir=repo_dir,
        env={},
        dry_run=False,
        out=lambda _: None,
    )

    assert ["git", "init"] in calls
    assert ["git", "branch", "-M", "main"] not in calls


def test_push_main_uses_head_ref_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def _fake_resolve_push_token(*, repo_dir: Path, env: dict[str, str]) -> str | None:
        return None

    def _fake_run(
        args: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        stdin_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(create_ops, "_resolve_push_token", _fake_resolve_push_token)
    monkeypatch.setattr(create_ops, "_run", _fake_run)

    pushed, error = create_ops._push_main(repo_dir=Path("/tmp/repo"), env={})
    assert pushed is True
    assert error is None
    assert calls == [["git", "push", "-u", "origin", "HEAD:main"]]


def test_push_main_uses_head_ref_with_token(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def _fake_resolve_push_token(*, repo_dir: Path, env: dict[str, str]) -> str | None:
        return "ghs_test"

    def _fake_run(
        args: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        stdin_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(create_ops, "_resolve_push_token", _fake_resolve_push_token)
    monkeypatch.setattr(create_ops, "_run", _fake_run)

    pushed, error = create_ops._push_main(repo_dir=Path("/tmp/repo"), env={})
    assert pushed is True
    assert error is None
    assert calls
    push_args = calls[0]
    assert push_args[-4:] == ["push", "-u", "origin", "HEAD:main"]
