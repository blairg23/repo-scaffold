"""Tests for workspace_ops -- uses real git but no network (bare clone from local)."""

from __future__ import annotations

import subprocess
from pathlib import Path


from repo_scaffold.workspace_ops import (
    _copy_ignored_files,
    _slug,
    workspace_configure_auth,
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


# ---------------------------------------------------------------------------
# _copy_ignored_files / --env-source
# ---------------------------------------------------------------------------


def _init_git_repo_with_ignored_files(tmp_path: Path) -> Path:
    """Create a plain git repo with a mix of gitignored files to test copy filtering."""
    repo = tmp_path / "source_repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t.com"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "T"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )

    # .gitignore with a mix of exact-name rules and glob patterns
    (repo / ".gitignore").write_text(
        ".env\n"
        ".env.*\n"
        "*.env\n"
        "config.yml\n"
        "*.csv\n"
        "*.log\n"
        "secrets.json\n"
    )

    # Exact-name ignored files -- should be copied
    (repo / ".env").write_text("SECRET=abc")
    (repo / "config.yml").write_text("db: localhost")
    (repo / "secrets.json").write_text('{"key": "val"}')

    # Glob-matched config files -- should be copied (.env.* and *.env are config globs)
    (repo / ".env.local").write_text("LOCAL=1")
    (repo / "production.env").write_text("PROD=1")

    # Glob-matched data/log files -- should NOT be copied
    (repo / "data.csv").write_text("a,b,c")
    (repo / "output.log").write_text("log line")

    # Tracked file -- not ignored, should not be copied
    (repo / "README.md").write_text("hello")
    subprocess.run(
        ["git", "add", ".gitignore", "README.md"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=str(repo), check=True, capture_output=True
    )

    return repo


def test_copy_ignored_files_copies_exact_name_rules(tmp_path: Path):
    source = _init_git_repo_with_ignored_files(tmp_path)
    dest = tmp_path / "dest"
    dest.mkdir()

    copied = _copy_ignored_files(source, dest)

    # Exact-name rules and glob-matched config files all copied
    assert {
        ".env",
        "config.yml",
        "secrets.json",
        ".env.local",
        "production.env",
    }.issubset(set(copied))
    assert (dest / ".env").read_text() == "SECRET=abc"
    assert (dest / "config.yml").read_text() == "db: localhost"
    assert (dest / "secrets.json").read_text() == '{"key": "val"}'
    assert (dest / ".env.local").read_text() == "LOCAL=1"
    assert (dest / "production.env").read_text() == "PROD=1"


def test_copy_ignored_files_skips_glob_matched_data(tmp_path: Path):
    source = _init_git_repo_with_ignored_files(tmp_path)
    dest = tmp_path / "dest"
    dest.mkdir()

    _copy_ignored_files(source, dest)

    # Data/log globs must not be copied
    assert not (dest / "data.csv").exists()
    assert not (dest / "output.log").exists()


def test_workspace_create_copies_env_source(tmp_path: Path):
    remote = _init_local_remote(tmp_path)
    ws_base = _make_workspace_base(tmp_path)
    source = _init_git_repo_with_ignored_files(tmp_path)

    cp = workspace_create(
        repo="owner/myrepo",
        branch="feat/with-env",
        token="unused",
        base="main",
        workspace_base=ws_base,
        env_source=source,
        _clone_url_override=str(remote),
    )

    assert cp.returncode == 0
    worktree = ws_base / "repos" / "owner" / "myrepo" / "feat-with-env"
    assert (worktree / ".env").exists()
    assert not (worktree / "data.csv").exists()


def test_workspace_create_missing_env_source_warns_not_errors(tmp_path: Path):
    remote = _init_local_remote(tmp_path)
    ws_base = _make_workspace_base(tmp_path)

    cp = workspace_create(
        repo="owner/myrepo",
        branch="feat/no-env",
        token="unused",
        base="main",
        workspace_base=ws_base,
        env_source=tmp_path / "nonexistent",
        _clone_url_override=str(remote),
    )

    assert cp.returncode == 0
    assert "Warning" in cp.stdout


# ---------------------------------------------------------------------------
# workspace_configure_auth
# ---------------------------------------------------------------------------


def _init_working_tree(tmp_path: Path, name: str = "worktree") -> Path:
    """Create a regular (non-bare) git repo for configure-auth tests."""
    repo = tmp_path / name
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )
    return repo


def test_workspace_configure_auth_writes_credentials(tmp_path: Path):
    repo = _init_working_tree(tmp_path)
    cp = workspace_configure_auth("fake-token", path=repo)
    assert cp.returncode == 0
    creds = repo / ".git" / ".git-credentials"
    assert creds.exists()
    raw = creds.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "must not have UTF-8 BOM"
    assert b"fake-token" in raw
    assert b"github.com" in raw
    assert raw.endswith(b"\n"), "must have trailing LF"
    assert b"\r" not in raw, "must not have CRLF"


def _local_credential_helpers(repo: Path) -> list[str]:
    """Return credential.helper values from local git config as 'key=value' strings."""
    r = subprocess.run(
        ["git", "config", "--local", "--list"],
        cwd=str(repo),
        capture_output=True,
        text=True,
    )
    return [
        line for line in r.stdout.splitlines() if line.startswith("credential.helper=")
    ]


def test_workspace_configure_auth_sets_credential_helper(tmp_path: Path):
    repo = _init_working_tree(tmp_path)
    workspace_configure_auth("fake-token", path=repo)
    helpers = _local_credential_helpers(repo)
    assert (
        helpers[0] == "credential.helper="
    ), "first entry must be empty string (chain reset)"
    assert any(
        "store" in h and ".git-credentials" in h for h in helpers
    ), "must include a store helper pointing at .git-credentials"


def test_workspace_configure_auth_is_idempotent(tmp_path: Path):
    repo = _init_working_tree(tmp_path)
    workspace_configure_auth("fake-token", path=repo)
    workspace_configure_auth("fake-token", path=repo)
    helpers = _local_credential_helpers(repo)
    assert (
        len(helpers) == 2
    ), f"expected exactly 2 entries (empty + store), got {helpers}"


def test_workspace_configure_auth_fails_for_non_repo(tmp_path: Path):
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()
    cp = workspace_configure_auth("fake-token", path=not_a_repo)
    assert cp.returncode != 0
    assert "Not a git repository" in cp.stderr
