"""Tests for workspace_ops -- uses real git but no network (bare clone from local)."""

from __future__ import annotations

import subprocess
from pathlib import Path


from repo_scaffold.workspace_ops import (
    _slug,
    workspace_create,
    workspace_delete,
    workspace_list,
    workspace_prune,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_local_remote(tmp_path: Path, name: str = "myrepo") -> Path:
    """Create a local bare git repo to stand in for a remote."""
    remote = tmp_path / f"{name}.git"
    remote.mkdir()
    subprocess.run(
        ["git", "init", "--bare", str(remote)], check=True, capture_output=True
    )

    # Seed it with an initial commit so main exists
    seed = tmp_path / f"{name}_seed"
    seed.mkdir()
    subprocess.run(["git", "init", str(seed)], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=str(seed),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(seed),
        check=True,
        capture_output=True,
    )
    (seed / "README.md").write_text("hello")
    subprocess.run(["git", "add", "."], cwd=str(seed), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(seed),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "remote", "add", "origin", str(remote)],
        cwd=str(seed),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "push", "origin", "HEAD:main"],
        cwd=str(seed),
        check=True,
        capture_output=True,
    )
    return remote


def _make_workspace_base(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


# ---------------------------------------------------------------------------
# _slug
# ---------------------------------------------------------------------------


def test_slug_replaces_slashes():
    assert _slug("feat/my-branch") == "feat-my-branch"


def test_slug_replaces_spaces():
    assert _slug("my branch") == "my-branch"


def test_slug_preserves_dots_and_hyphens():
    assert _slug("v1.2-beta") == "v1.2-beta"


# ---------------------------------------------------------------------------
# workspace_create
# ---------------------------------------------------------------------------


def test_workspace_create_new_branch(tmp_path: Path):
    remote = _init_local_remote(tmp_path)
    ws_base = _make_workspace_base(tmp_path)

    cp = workspace_create(
        repo="owner/myrepo",
        branch="feat/test-branch",
        token="unused",
        base="main",
        workspace_base=ws_base,
        _clone_url_override=str(remote),
    )

    assert cp.returncode == 0
    worktree = ws_base / "repos" / "owner" / "myrepo" / "feat-test-branch"
    assert worktree.is_dir()
    assert (worktree / "README.md").exists()


def test_workspace_create_twice_fails(tmp_path: Path):
    remote = _init_local_remote(tmp_path)
    ws_base = _make_workspace_base(tmp_path)

    workspace_create(
        repo="owner/myrepo",
        branch="feat/test-branch",
        token="unused",
        base="main",
        workspace_base=ws_base,
        _clone_url_override=str(remote),
    )
    cp = workspace_create(
        repo="owner/myrepo",
        branch="feat/test-branch",
        token="unused",
        base="main",
        workspace_base=ws_base,
        _clone_url_override=str(remote),
    )

    assert cp.returncode != 0
    assert "already exists" in cp.stderr


def test_workspace_create_second_branch_reuses_bare(tmp_path: Path):
    remote = _init_local_remote(tmp_path)
    ws_base = _make_workspace_base(tmp_path)

    workspace_create(
        repo="owner/myrepo",
        branch="feat/branch-a",
        token="unused",
        base="main",
        workspace_base=ws_base,
        _clone_url_override=str(remote),
    )
    cp = workspace_create(
        repo="owner/myrepo",
        branch="feat/branch-b",
        token="unused",
        base="main",
        workspace_base=ws_base,
        _clone_url_override=str(remote),
    )

    assert cp.returncode == 0
    assert (ws_base / "repos" / "owner" / "myrepo" / "feat-branch-a").is_dir()
    assert (ws_base / "repos" / "owner" / "myrepo" / "feat-branch-b").is_dir()


# ---------------------------------------------------------------------------
# workspace_list
# ---------------------------------------------------------------------------


def test_workspace_list_shows_worktrees(tmp_path: Path):
    remote = _init_local_remote(tmp_path)
    ws_base = _make_workspace_base(tmp_path)

    workspace_create(
        repo="owner/myrepo",
        branch="feat/alpha",
        token="unused",
        base="main",
        workspace_base=ws_base,
        _clone_url_override=str(remote),
    )
    workspace_create(
        repo="owner/myrepo",
        branch="feat/beta",
        token="unused",
        base="main",
        workspace_base=ws_base,
        _clone_url_override=str(remote),
    )

    cp = workspace_list(workspace_base=ws_base)

    assert cp.returncode == 0
    assert "feat-alpha" in cp.stdout or "feat/alpha" in cp.stdout
    assert "feat-beta" in cp.stdout or "feat/beta" in cp.stdout


def test_workspace_list_empty(tmp_path: Path):
    ws_base = _make_workspace_base(tmp_path)
    cp = workspace_list(workspace_base=ws_base)
    assert cp.returncode == 0
    assert "No worktrees" in cp.stdout


# ---------------------------------------------------------------------------
# workspace_delete
# ---------------------------------------------------------------------------


def test_workspace_delete_removes_worktree(tmp_path: Path):
    remote = _init_local_remote(tmp_path)
    ws_base = _make_workspace_base(tmp_path)

    workspace_create(
        repo="owner/myrepo",
        branch="feat/to-delete",
        token="unused",
        base="main",
        workspace_base=ws_base,
        _clone_url_override=str(remote),
    )
    worktree = ws_base / "repos" / "owner" / "myrepo" / "feat-to-delete"
    assert worktree.is_dir()

    cp = workspace_delete(
        repo="owner/myrepo", branch="feat/to-delete", workspace_base=ws_base
    )

    assert cp.returncode == 0
    assert not worktree.exists()


def test_workspace_delete_missing_fails(tmp_path: Path):
    ws_base = _make_workspace_base(tmp_path)
    cp = workspace_delete(
        repo="owner/myrepo", branch="feat/ghost", workspace_base=ws_base
    )
    assert cp.returncode != 0


# ---------------------------------------------------------------------------
# workspace_prune
# ---------------------------------------------------------------------------


def test_workspace_prune_removes_stale_worktrees(tmp_path: Path):
    remote = _init_local_remote(tmp_path)
    ws_base = _make_workspace_base(tmp_path)

    # Push feat/keep to origin BEFORE creating any worktrees so it's a real remote branch
    seed = tmp_path / "prune_seed"
    seed.mkdir()
    subprocess.run(["git", "init", str(seed)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(seed), "config", "user.email", "t@t.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(seed), "config", "user.name", "T"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(seed), "remote", "add", "origin", str(remote)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(seed), "fetch", "origin"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(seed), "checkout", "-b", "feat/keep", "FETCH_HEAD"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(seed), "push", "origin", "feat/keep"],
        check=True,
        capture_output=True,
    )

    # Create worktrees: feat/keep comes from remote, feat/stale is local-only
    workspace_create(
        repo="owner/myrepo",
        branch="feat/keep",
        token="unused",
        base="main",
        workspace_base=ws_base,
        _clone_url_override=str(remote),
    )
    workspace_create(
        repo="owner/myrepo",
        branch="feat/stale",
        token="unused",
        base="main",
        workspace_base=ws_base,
        _clone_url_override=str(remote),
    )

    # Delete feat/keep from origin to simulate a merged+deleted branch
    subprocess.run(
        ["git", "-C", str(seed), "push", "origin", "--delete", "feat/keep"],
        check=True,
        capture_output=True,
    )

    cp = workspace_prune(repo="owner/myrepo", workspace_base=ws_base)

    assert cp.returncode == 0
    # feat/stale was never on origin and feat/keep was deleted -- both should be pruned
    assert not (ws_base / "repos" / "owner" / "myrepo" / "feat-keep").exists()
    assert not (ws_base / "repos" / "owner" / "myrepo" / "feat-stale").exists()
