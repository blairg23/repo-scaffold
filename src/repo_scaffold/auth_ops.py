"""Local git credential configuration.

Git Credential Manager is configured at the system level on Windows and pops a
GUI prompt on any authenticated operation, which hangs in non-interactive and
agent contexts. This module points a checkout's local credential helper at
git-credential-store instead, so pushes and fetches resolve without a prompt.

This lives outside workspace_ops deliberately. The `workspace` worktree model is
deprecated in favour of per-branch containers, but the need for non-interactive
local auth outlives it: when the sanctioned way to get working credentials goes
away, what fills the gap is embedding the token directly in `remote.origin.url`,
which is the exact leak `check credentials` exists to catch.
"""

from __future__ import annotations

import json
import subprocess
import urllib.request
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


def github_username(token: str) -> str:
    """Resolve the token's owner, falling back to a placeholder.

    git-credential-store keys entries by username, but GitHub accepts any
    username when the password is a PAT, so the fallback stays functional if the
    API is unreachable.
    """
    try:
        req = urllib.request.Request(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "repo-scaffold",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read()).get("login", "x-token")
    except Exception:
        return "x-token"


def configure_auth(
    token: str,
    path: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Configure git credential-store for a regular (non-bare) working tree.

    Writes credentials to .git/.git-credentials (inside the .git dir, never
    tracked) and configures the local credential.helper chain to bypass Windows
    Credential Manager. Safe to run repeatedly: any accumulated helper entries
    are cleared before fresh config is written.
    """
    worktree = (path or Path.cwd()).resolve()
    git_dir = worktree / ".git"
    if not git_dir.is_dir():
        return _err(f"Not a git repository: {worktree}")

    if not token:
        return _err(
            "No token available. Set GH_TOKEN in .env or pass --path to a repo that has one."
        )

    username = github_username(token)
    creds_file = git_dir / ".git-credentials"
    # LF-only, no BOM: git-credential-store rejects BOM and CRLF on all platforms
    creds_file.write_bytes(f"https://{username}:{token}@github.com\n".encode())
    try:
        creds_file.chmod(0o600)
    except NotImplementedError:
        pass  # Windows: chmod is a no-op; the PAT is protected by NTFS ACLs

    creds_posix = creds_file.as_posix()
    # Clear any accumulated local helpers (idempotent -- exit 5 = key not found = ok)
    _run(["git", "config", "--unset-all", "credential.helper"], cwd=worktree)
    # Empty string resets the credential helper list, overriding the system GCM
    _run(["git", "config", "credential.helper", ""], cwd=worktree)
    _run(
        [
            "git",
            "config",
            "--add",
            "credential.helper",
            f'store --file "{creds_posix}"',
        ],
        cwd=worktree,
    )
    return _ok(f"Configured credential-store for {worktree}")
