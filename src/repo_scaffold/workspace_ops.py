"""Managed git worktree workspace under repos/."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


def _run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
    )


def _ok(stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


def _err(stderr: str, returncode: int = 1) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout="", stderr=stderr
    )


def _slug(name: str) -> str:
    """Convert a repo name or branch name to a filesystem-safe slug."""
    return re.sub(r"[^a-zA-Z0-9._-]", "-", name).strip("-")


def _workspace_root(base: Path | None = None) -> Path:
    """Return the repos/ directory relative to a base or the caller's cwd."""
    return (base or Path.cwd()) / "repos"


def _bare_path(workspace: Path, repo: str) -> Path:
    repo_slug = _slug(repo.split("/")[-1])
    return workspace / repo_slug / ".bare"


def _worktree_path(workspace: Path, repo: str, branch: str) -> Path:
    repo_slug = _slug(repo.split("/")[-1])
    branch_slug = _slug(branch)
    return workspace / repo_slug / branch_slug


def workspace_create(
    repo: str,
    branch: str,
    token: str,
    base: str = "main",
    workspace_base: Path | None = None,
    _clone_url_override: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Clone repo as a bare clone (once) then add a worktree for branch."""
    workspace = _workspace_root(workspace_base)
    bare = _bare_path(workspace, repo)
    worktree = _worktree_path(workspace, repo, branch)

    if worktree.exists():
        return _err(f"Worktree already exists at {worktree}")

    clone_url = _clone_url_override or f"https://x-token:{token}@github.com/{repo}.git"

    if not bare.exists():
        bare.parent.mkdir(parents=True, exist_ok=True)
        cp = _run(["git", "clone", "--bare", clone_url, str(bare)])
        if cp.returncode != 0:
            return _err(f"git clone failed: {cp.stderr.strip()}")
        # point HEAD to main branch in bare clone for worktree add to work cleanly
        _run(["git", "symbolic-ref", "HEAD", f"refs/heads/{base}"], cwd=bare)
    else:
        cp = _run(
            ["git", "fetch", "origin", "--prune", "refs/heads/*:refs/heads/*"],
            cwd=bare,
        )
        if cp.returncode != 0:
            return _err(f"git fetch failed: {cp.stderr.strip()}")

    # In a bare clone refs land at refs/heads/*, so check local refs
    cp = _run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], cwd=bare
    )
    if cp.returncode == 0:
        # Branch already exists locally (was fetched from origin)
        cp = _run(["git", "worktree", "add", str(worktree), branch], cwd=bare)
    else:
        # New branch -- create it off the base ref
        cp = _run(
            ["git", "worktree", "add", "-b", branch, str(worktree), base],
            cwd=bare,
        )

    if cp.returncode != 0:
        return _err(f"git worktree add failed: {cp.stderr.strip()}")

    return _ok(f"Created worktree at {worktree}")


def workspace_list(
    repo: str | None = None,
    workspace_base: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """List active worktrees under repos/."""
    workspace = _workspace_root(workspace_base)
    if not workspace.exists():
        return _ok("No worktrees found.")

    lines: list[str] = []
    repo_dirs = (
        sorted(workspace.iterdir())
        if not repo
        else [workspace / _slug(repo.split("/")[-1])]
    )

    for repo_dir in repo_dirs:
        if not repo_dir.is_dir():
            continue
        bare = repo_dir / ".bare"
        if not bare.exists():
            continue
        cp = _run(["git", "worktree", "list", "--porcelain"], cwd=bare)
        if cp.returncode != 0:
            continue
        for block in cp.stdout.strip().split("\n\n"):
            info: dict[str, str] = {}
            for line in block.strip().splitlines():
                parts = line.split(" ", 1)
                info[parts[0]] = parts[1] if len(parts) > 1 else ""
            wt_path = info.get("worktree", "")
            branch_ref = info.get("branch", "")
            branch = (
                branch_ref.replace("refs/heads/", "") if branch_ref else "(detached)"
            )
            if wt_path and not wt_path.endswith(".bare"):
                lines.append(f"{repo_dir.name}  {branch}  {wt_path}")

    return _ok("\n".join(lines) if lines else "No worktrees found.")


def workspace_delete(
    repo: str,
    branch: str,
    workspace_base: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Remove a worktree for a branch."""
    workspace = _workspace_root(workspace_base)
    bare = _bare_path(workspace, repo)
    worktree = _worktree_path(workspace, repo, branch)

    if not worktree.exists():
        return _err(f"Worktree not found at {worktree}")

    if not bare.exists():
        return _err(f"Bare repo not found at {bare}")

    cp = _run(["git", "worktree", "remove", "--force", str(worktree)], cwd=bare)
    if cp.returncode != 0:
        return _err(f"git worktree remove failed: {cp.stderr.strip()}")

    _run(["git", "worktree", "prune"], cwd=bare)
    return _ok(f"Deleted worktree at {worktree}")


def workspace_prune(
    repo: str,
    workspace_base: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Remove local worktrees for branches that no longer exist on origin."""
    workspace = _workspace_root(workspace_base)
    bare = _bare_path(workspace, repo)

    if not bare.exists():
        return _err(f"Bare repo not found at {bare}")

    # Query remote directly -- avoids fetching HEAD which fails on some server configs
    remote_cp = _run(["git", "ls-remote", "--heads", "origin"], cwd=bare)
    if remote_cp.returncode != 0:
        return _err(f"git ls-remote failed: {remote_cp.stderr.strip()}")

    remote_branches = {
        line.split("refs/heads/", 1)[1].strip()
        for line in remote_cp.stdout.splitlines()
        if "\trefs/heads/" in line
    }

    removed: list[str] = []
    repo_dir = bare.parent
    for entry in sorted(repo_dir.iterdir()):
        if entry.name == ".bare" or not entry.is_dir():
            continue
        branch_slug = entry.name
        # Check if any remote branch maps to this slug
        matched = any(_slug(b) == branch_slug for b in remote_branches)
        if not matched:
            cp = _run(["git", "worktree", "remove", "--force", str(entry)], cwd=bare)
            if cp.returncode == 0:
                removed.append(str(entry))

    _run(["git", "worktree", "prune"], cwd=bare)

    if removed:
        return _ok("Pruned:\n" + "\n".join(removed))
    return _ok("Nothing to prune.")
