from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

import repo_scaffold.delete_ops as delete_ops
from repo_scaffold.delete_ops import delete_repositories

TOKEN = "ghp_test_token"


def _cp(
    args: list[str], *, code: int, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=args, returncode=code, stdout=stdout, stderr=stderr
    )


def _no_gh_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """`repo-scaffold delete` must work with no gh binary installed (#334).

    Emptying PATH is stronger than stubbing `shutil.which`: anything that tried to
    exec `gh` would fail outright rather than find a developer's local install.
    """
    monkeypatch.setenv("PATH", "")
    assert shutil.which("gh") is None


def _stub_token(monkeypatch: pytest.MonkeyPatch, token: str | None = TOKEN) -> None:
    monkeypatch.setattr(delete_ops.github_api, "token_from_repo", lambda _cwd: token)
    monkeypatch.setattr(delete_ops.github_api, "validate_token", lambda _token: True)


def _stub_repo_list(
    monkeypatch: pytest.MonkeyPatch,
    names: list[str],
    *,
    calls: list[tuple[str, str]] | None = None,
) -> None:
    payload = json.dumps([{"name": name} for name in names])

    def _fake_repo_list(owner: str, token: str) -> subprocess.CompletedProcess[str]:
        if calls is not None:
            calls.append((owner, token))
        return _cp(["repo_list"], code=0, stdout=payload)

    monkeypatch.setattr(delete_ops.github_api, "repo_list", _fake_repo_list)


def _forbid_repo_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    def _unexpected(repo: str, _token: str) -> subprocess.CompletedProcess[str]:
        raise AssertionError(f"repo_delete should not be called: {repo}")

    monkeypatch.setattr(delete_ops.github_api, "repo_delete", _unexpected)


def test_delete_repositories_dry_run_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_ORG", "acme")
    monkeypatch.delenv("GH_REPO", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    _no_gh_on_path(monkeypatch)
    _stub_token(monkeypatch)
    _forbid_repo_delete(monkeypatch)

    list_calls: list[tuple[str, str]] = []
    _stub_repo_list(
        monkeypatch,
        ["repo-scaffold-e2e", "repo-scaffold-e2e-20260311", "other"],
        calls=list_calls,
    )

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
    assert list_calls == [("acme", TOKEN)]
    assert err_lines == []


def test_delete_repositories_apply_exact_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GH_REPO", "github.com/acme/some-repo")
    monkeypatch.delenv("GITHUB_ORG", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    _no_gh_on_path(monkeypatch)
    _stub_token(monkeypatch)
    _stub_repo_list(
        monkeypatch,
        [
            "repo-scaffold-e2e",
            "repo-scaffold-e2e-20260311001924",
            "repo-scaffold-e2e-unwanted",
            "keep-me",
        ],
    )

    deleted_calls: list[str] = []

    def _fake_repo_delete(repo: str, token: str) -> subprocess.CompletedProcess[str]:
        assert token == TOKEN
        deleted_calls.append(repo)
        if repo.endswith("20260311001924"):
            return _cp(["repo_delete"], code=1, stderr="forbidden")
        return _cp(["repo_delete"], code=0)

    monkeypatch.setattr(delete_ops.github_api, "repo_delete", _fake_repo_delete)

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

    _no_gh_on_path(monkeypatch)
    _forbid_repo_delete(monkeypatch)

    def _unexpected_repo_list(
        _owner: str, _token: str
    ) -> subprocess.CompletedProcess[str]:
        raise AssertionError("GitHub API should not be hit in --delete-local mode")

    monkeypatch.setattr(delete_ops.github_api, "repo_list", _unexpected_repo_list)

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
    _no_gh_on_path(monkeypatch)
    _stub_token(monkeypatch)
    _stub_repo_list(monkeypatch, ["repo-scaffold-e2e-abc"])
    _forbid_repo_delete(monkeypatch)

    local_root = tmp_path / "local-root"
    local_root.mkdir(parents=True)
    (local_root / "repo-scaffold-e2e-abc").mkdir(parents=True)
    (local_root / "other").mkdir(parents=True)

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


def test_delete_helpers_cover_owner_repo_and_root_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert delete_ops._parse_owner_from_repo_ref("acme/repo") == "acme"
    assert delete_ops._parse_owner_from_repo_ref("github.com/acme/repo") == "acme"
    assert delete_ops._parse_owner_from_repo_ref("repo-only") is None

    monkeypatch.delenv("GH_REPO", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.delenv("GITHUB_ORG", raising=False)

    assert delete_ops._resolve_owner("  acme  ") == "acme"

    monkeypatch.setenv("GITHUB_REPOSITORY", "octo-org/demo")
    assert delete_ops._resolve_owner(None) == "octo-org"
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)

    monkeypatch.setenv("GITHUB_ORG", "fallback-org")
    assert delete_ops._resolve_owner(None) == "fallback-org"
    monkeypatch.delenv("GITHUB_ORG", raising=False)

    with pytest.raises(RuntimeError, match="Could not resolve owner"):
        delete_ops._resolve_owner(None)

    with pytest.raises(RuntimeError, match="--prefix must not be empty"):
        delete_ops._select_matches(repo_names=["demo"], prefix=" ", exact_names=())

    roots = delete_ops._resolve_local_roots(
        cwd=tmp_path,
        local_roots=(str(tmp_path / "a"), str(tmp_path / "a"), "relative-root"),
    )
    assert roots == [(tmp_path / "a").resolve(), (tmp_path / "relative-root").resolve()]

    with pytest.raises(RuntimeError, match="Refusing local cleanup root"):
        delete_ops._resolve_local_roots(cwd=tmp_path, local_roots=("/",))


def test_delete_helpers_cover_token_and_repo_list_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_gh_on_path(monkeypatch)

    monkeypatch.setattr(delete_ops.github_api, "token_from_repo", lambda _cwd: None)
    with pytest.raises(RuntimeError, match="No GitHub token found"):
        delete_ops._resolve_api_token(tmp_path)

    monkeypatch.setattr(delete_ops.github_api, "token_from_repo", lambda _cwd: TOKEN)
    monkeypatch.setattr(delete_ops.github_api, "validate_token", lambda _token: False)
    with pytest.raises(RuntimeError, match="GitHub token was rejected"):
        delete_ops._resolve_api_token(tmp_path)

    monkeypatch.setattr(delete_ops.github_api, "validate_token", lambda _token: True)
    assert delete_ops._resolve_api_token(tmp_path) == TOKEN

    monkeypatch.setattr(
        delete_ops.github_api,
        "repo_list",
        lambda _owner, _token: _cp(["repo_list"], code=1, stderr="boom"),
    )
    with pytest.raises(RuntimeError, match="boom"):
        delete_ops._list_repo_names(owner="acme", token=TOKEN)

    monkeypatch.setattr(
        delete_ops.github_api,
        "repo_list",
        lambda _owner, _token: _cp(["repo_list"], code=1, stderr=""),
    )
    with pytest.raises(RuntimeError, match="Failed listing repositories for owner"):
        delete_ops._list_repo_names(owner="acme", token=TOKEN)

    monkeypatch.setattr(
        delete_ops.github_api,
        "repo_list",
        lambda _owner, _token: _cp(["repo_list"], code=0, stdout="{"),
    )
    with pytest.raises(
        RuntimeError, match="Unexpected response while listing repositories"
    ):
        delete_ops._list_repo_names(owner="acme", token=TOKEN)

    monkeypatch.setattr(
        delete_ops.github_api,
        "repo_list",
        lambda _owner, _token: _cp(["repo_list"], code=0, stdout='{"name":"demo"}'),
    )
    with pytest.raises(RuntimeError, match="Unexpected repository list response"):
        delete_ops._list_repo_names(owner="acme", token=TOKEN)


def test_delete_repositories_covers_no_matches_and_noninteractive_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_ORG", "acme")
    _no_gh_on_path(monkeypatch)
    _stub_token(monkeypatch)
    _stub_repo_list(monkeypatch, ["other"])
    _forbid_repo_delete(monkeypatch)

    out_lines: list[str] = []
    summary = delete_repositories(
        owner=None,
        prefix="repo-scaffold-e2e",
        exact_names=(),
        include_local=False,
        delete_local_only=False,
        local_roots=(),
        apply=True,
        assume_yes=False,
        prompt=lambda _msg: "n",
        is_tty=False,
        cwd=tmp_path,
        out=out_lines.append,
        err=lambda _line: None,
    )

    assert summary.matched == 0
    assert "No matching delete targets found." in out_lines

    local_root = tmp_path / "local-root"
    local_root.mkdir()
    (local_root / "repo-scaffold-e2e").mkdir()
    out_lines.clear()
    summary = delete_repositories(
        owner=None,
        prefix="repo-scaffold-e2e",
        exact_names=(),
        include_local=True,
        delete_local_only=False,
        local_roots=(str(local_root),),
        apply=True,
        assume_yes=False,
        prompt=lambda _msg: "n",
        is_tty=False,
        cwd=tmp_path,
        out=out_lines.append,
        err=lambda _line: None,
    )

    assert summary.remote_skipped == 0
    assert summary.local_skipped == 1
    assert any("Non-interactive shell detected" in line for line in out_lines)


def test_delete_repositories_covers_abort_and_local_failure_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local_root = tmp_path / "local-root"
    local_root.mkdir()
    protected = local_root / "repo-scaffold-e2e"
    protected.mkdir()
    broken = local_root / "repo-scaffold-e2e-broken"
    broken.mkdir()

    out_lines: list[str] = []
    summary = delete_repositories(
        owner=None,
        prefix="repo-scaffold-e2e",
        exact_names=("repo-scaffold-e2e",),
        include_local=True,
        delete_local_only=True,
        local_roots=(str(local_root),),
        apply=True,
        assume_yes=False,
        prompt=lambda _msg: "n",
        is_tty=True,
        cwd=tmp_path,
        out=out_lines.append,
        err=lambda _line: None,
    )

    assert summary.local_skipped == 1
    assert "Aborted." in out_lines

    monkeypatch.setattr(
        delete_ops.shutil,
        "rmtree",
        lambda path: (
            (_ for _ in ()).throw(OSError("permission denied"))
            if Path(path).name == "repo-scaffold-e2e-broken"
            else None
        ),
    )

    err_lines: list[str] = []
    summary = delete_repositories(
        owner=None,
        prefix="repo-scaffold-e2e",
        exact_names=("repo-scaffold-e2e", "repo-scaffold-e2e-broken"),
        include_local=True,
        delete_local_only=True,
        local_roots=(str(local_root),),
        apply=True,
        assume_yes=True,
        prompt=lambda _msg: "y",
        is_tty=True,
        cwd=protected,
        out=lambda _line: None,
        err=err_lines.append,
    )

    assert summary.local_deleted == 0
    assert summary.local_failures == 2
    assert any(
        "refusing to delete current working directory" in line for line in err_lines
    )
    assert any("permission denied" in line for line in err_lines)


def test_delete_ops_module_never_shells_out() -> None:
    """delete_ops talks to the GitHub API directly -- no subprocess, no gh (#334)."""
    source = Path(delete_ops.__file__).read_text(encoding="utf-8")
    assert "subprocess" not in source
    assert '"gh"' not in source
    assert "'gh'" not in source
    assert not hasattr(delete_ops, "_run_gh")
    assert not hasattr(delete_ops, "_ensure_gh_ready")


def test_delete_repositories_applies_remote_delete_with_no_gh_on_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real remote delete must succeed with no binaries resolvable at all."""
    monkeypatch.setenv("GITHUB_ORG", "acme")
    monkeypatch.delenv("GH_REPO", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    _no_gh_on_path(monkeypatch)

    _stub_token(monkeypatch)
    _stub_repo_list(monkeypatch, ["repo-scaffold-e2e-abc", "keep-me"])

    deleted: list[str] = []

    def _fake_repo_delete(repo: str, _token: str) -> subprocess.CompletedProcess[str]:
        deleted.append(repo)
        return _cp(["repo_delete"], code=0)

    monkeypatch.setattr(delete_ops.github_api, "repo_delete", _fake_repo_delete)

    summary = delete_repositories(
        owner=None,
        prefix="repo-scaffold-e2e",
        exact_names=(),
        include_local=False,
        delete_local_only=False,
        local_roots=(),
        apply=True,
        assume_yes=True,
        prompt=lambda _msg: "y",
        is_tty=False,
        cwd=tmp_path,
        out=lambda _line: None,
        err=lambda _line: None,
    )

    assert deleted == ["acme/repo-scaffold-e2e-abc"]
    assert summary.remote_deleted == 1
    assert summary.remote_failures == 0
