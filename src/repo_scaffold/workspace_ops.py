"""Managed git worktree workspace under repos/."""

from __future__ import annotations

import re
import shutil
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


def _repo_dir(workspace: Path, repo: str) -> Path:
    """repos/{owner}/{name} -- owner-scoped so alice/api and bob/api don't collide."""
    owner, name = repo.split("/", 1)
    return workspace / _slug(owner) / _slug(name)


def _bare_path(workspace: Path, repo: str) -> Path:
    return _repo_dir(workspace, repo) / ".bare"


def _worktree_path(workspace: Path, repo: str, branch: str) -> Path:
    return _repo_dir(workspace, repo) / _slug(branch)


_CONFIG_KEYWORDS = ("env", "config", "secret", "credential", "token", "key", "auth")


def _is_config_filename(name: str) -> bool:
    """Return True if a filename looks like a config/secrets file.

    Covers dotfiles (.env, .env.local, .npmrc) and files whose name contains
    a config-oriented keyword (config.yml, secrets.json, prod.env, etc.).
    """
    lower = name.lower()
    if lower.startswith("."):
        return True
    return any(kw in lower for kw in _CONFIG_KEYWORDS)


def _copy_ignored_files(source: Path, dest: Path) -> list[str]:
    """Copy gitignored root-level config files from source into dest.

    A file is copied when it satisfies either condition:
    - It is matched by an exact-name ignore rule (no glob chars), or
    - Its filename looks like a config/secrets file (_is_config_filename).

    This means data globs like *.csv or *.log are skipped, but patterns like
    .env.* or *.env still pull in matching config files. Subdirectories are
    always skipped (reinstall node_modules, don't bulk-copy them).

    Returns a list of copied filenames (empty if source has no git or no matches).
    """
    ls_cp = _run(
        ["git", "ls-files", "--others", "--ignored", "--exclude-standard"],
        cwd=source,
    )
    if ls_cp.returncode != 0:
        return []

    candidates = [
        name.strip()
        for name in ls_cp.stdout.splitlines()
        if name.strip()
        and "/" not in name.strip()
        and "\\" not in name.strip()
        and (source / name.strip()).is_file()
    ]
    if not candidates:
        return []

    # Use check-ignore -v to get the matching rule for each candidate
    check_cp = _run(
        ["git", "check-ignore", "-v", "--", *candidates],
        cwd=source,
    )
    # check-ignore exits non-zero when some candidates are not ignored -- use stdout only
    rule_by_file: dict[str, str] = {}
    for line in check_cp.stdout.splitlines():
        # Format: <source>:<linenum>:<pattern>\t<pathname>
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        pattern_token = parts[0].rsplit(":", 1)[-1]
        rule_by_file[parts[1].strip()] = pattern_token

    copied: list[str] = []
    for name in candidates:
        pattern = rule_by_file.get(name, "")
        is_exact_rule = pattern and not any(c in pattern for c in ("*", "?", "["))
        if is_exact_rule or _is_config_filename(name):
            shutil.copy2(str(source / name), str(dest / name))
            copied.append(name)
    return copied


def workspace_create(
    repo: str,
    branch: str,
    token: str,
    base: str = "main",
    workspace_base: Path | None = None,
    env_source: Path | None = None,
    _clone_url_override: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Clone repo as a bare clone (once) then add a worktree for branch.

    env_source: if provided, copy all gitignored root-level files from that
    directory into the new worktree (covers .env, .env.local, secrets.json, etc.).
    Missing or non-git source directories are skipped with a warning, not an error.
    """
    workspace = _workspace_root(workspace_base)
    bare = _bare_path(workspace, repo)
    worktree = _worktree_path(workspace, repo, branch)

    if worktree.exists():
        return _err(f"Worktree already exists at {worktree}")

    if not bare.exists():
        bare.parent.mkdir(parents=True, exist_ok=True)
        clone_url = (
            _clone_url_override or f"https://x-token:{token}@github.com/{repo}.git"
        )
        cp = _run(["git", "clone", "--bare", clone_url, str(bare)])
        if cp.returncode != 0:
            return _err(f"git clone failed: {cp.stderr.strip()}")
        # Rewrite remote URL to strip token from stored config (repos/ is gitignored,
        # but clean URLs are still better practice), then store the token via
        # http.extraHeader so git push/fetch work without prompting a credential dialog.
        if not _clone_url_override:
            _run(
                [
                    "git",
                    "remote",
                    "set-url",
                    "origin",
                    f"https://github.com/{repo}.git",
                ],
                cwd=bare,
            )
            # Scope auth header to github.com so it doesn't leak to other hosts.
            # Stored in the bare repo's config (inside gitignored repos/) -- same
            # security posture as the .env file that holds GH_TOKEN.
            _run(
                [
                    "git",
                    "config",
                    "http.https://github.com/.extraHeader",
                    f"Authorization: Bearer {token}",
                ],
                cwd=bare,
            )
        _run(["git", "symbolic-ref", "HEAD", f"refs/heads/{base}"], cwd=bare)
    else:
        auth_args = (
            []
            if _clone_url_override
            else ["-c", f"http.extraHeader=Authorization: Bearer {token}"]
        )
        cp = _run(
            [
                "git",
                *auth_args,
                "fetch",
                "origin",
                "--prune",
                "refs/heads/*:refs/heads/*",
            ],
            cwd=bare,
        )
        if cp.returncode != 0:
            return _err(f"git fetch failed: {cp.stderr.strip()}")

    cp = _run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], cwd=bare
    )
    if cp.returncode == 0:
        cp = _run(["git", "worktree", "add", str(worktree), branch], cwd=bare)
    else:
        cp = _run(
            ["git", "worktree", "add", "-b", branch, str(worktree), base],
            cwd=bare,
        )

    if cp.returncode != 0:
        return _err(f"git worktree add failed: {cp.stderr.strip()}")

    notes: list[str] = [f"Created worktree at {worktree}"]

    if env_source is not None:
        if not env_source.is_dir():
            notes.append(f"Warning: --env-source {env_source} not found, skipping.")
        else:
            copied = _copy_ignored_files(env_source, worktree)
            if copied:
                notes.append(f"Copied ignored files: {', '.join(copied)}")
            else:
                notes.append("No gitignored root files found in env-source.")

    return _ok("\n".join(notes))


def workspace_list(
    repo: str | None = None,
    workspace_base: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """List active worktrees under repos/."""
    workspace = _workspace_root(workspace_base)
    if not workspace.exists():
        return _ok("No worktrees found.")

    lines: list[str] = []

    if repo:
        candidates = [_repo_dir(workspace, repo)]
    else:
        # Walk two levels: repos/{owner}/{name}
        candidates = [
            name_dir
            for owner_dir in sorted(workspace.iterdir())
            if owner_dir.is_dir()
            for name_dir in sorted(owner_dir.iterdir())
            if name_dir.is_dir()
        ]

    for repo_dir in candidates:
        bare = repo_dir / ".bare"
        if not bare.exists():
            continue
        cp = _run(["git", "worktree", "list", "--porcelain"], cwd=bare)
        if cp.returncode != 0:
            continue
        label = f"{repo_dir.parent.name}/{repo_dir.name}"
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
                lines.append(f"{label}  {branch}  {wt_path}")

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
        matched = any(_slug(b) == branch_slug for b in remote_branches)
        if not matched:
            cp = _run(["git", "worktree", "remove", "--force", str(entry)], cwd=bare)
            if cp.returncode == 0:
                removed.append(str(entry))

    _run(["git", "worktree", "prune"], cwd=bare)

    if removed:
        return _ok("Pruned:\n" + "\n".join(removed))
    return _ok("Nothing to prune.")
