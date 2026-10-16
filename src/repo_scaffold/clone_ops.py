"""Non-interactive clone and pull of GitHub repos.

A plain `git clone` or `git pull` on Windows goes through the system-level Git
Credential Manager, which opens a GUI login prompt and hangs any headless or
agent run. `auth configure` avoids that by writing the PAT to
`.git/.git-credentials`, but that is plaintext on disk, and most checkouts live
inside a synced folder (Dropbox), so the token syncs too.

These helpers never write a credential anywhere. Each git call gets:

- `GIT_TERMINAL_PROMPT=0`, `GCM_INTERACTIVE=never` and an empty `GIT_ASKPASS`
  (which also shadows `core.askPass` and `SSH_ASKPASS`), so nothing can prompt;
- an empty `credential.helper`, which resets the helper list for that one call
  so GCM (or any other configured helper) is never consulted;
- the token as an `http.https://github.com/.extraheader`, scoped to github.com.

The two config values travel as `GIT_CONFIG_COUNT`/`GIT_CONFIG_KEY_n`/
`GIT_CONFIG_VALUE_n` environment variables (git 2.31+), not `git -c`, so the
token is not in the process argument list either. Nothing reaches
`.git/config`, the remote URL, or a credentials file.
"""

from __future__ import annotations

import base64
import os
import subprocess
from collections.abc import Mapping
from pathlib import Path

GITHUB_HOST = "github.com"


def _ok(stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


def _err(stderr: str, returncode: int = 1) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout="", stderr=stderr
    )


def clone_url(repo: str) -> str:
    """The clean HTTPS URL for OWNER/REPO. Never carries a token."""
    return f"https://{GITHUB_HOST}/{repo}.git"


def git_env(token: str | None, base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Environment for one non-interactive git call.

    Appends to any `GIT_CONFIG_COUNT` entries already in `base` instead of
    clobbering them, so a caller's own env-config keeps working.
    """
    env = dict(os.environ if base is None else base)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    # An inherited askpass program (IDE and desktop git sessions set one) would
    # still run when credentials are missing or rejected. Git reads GIT_ASKPASS
    # first and stops there even when it is empty, so "" also shadows
    # core.askPass and SSH_ASKPASS; with the terminal prompt off, git then fails
    # fast. SSH_ASKPASS is cleared too, for ssh itself on an SSH remote.
    env["GIT_ASKPASS"] = ""
    env["SSH_ASKPASS"] = ""

    entries: list[tuple[str, str]] = [("credential.helper", "")]
    if token:
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode("ascii")
        entries.append(
            (
                f"http.https://{GITHUB_HOST}/.extraheader",
                f"AUTHORIZATION: basic {basic}",
            )
        )

    try:
        start = int(env.get("GIT_CONFIG_COUNT", "0") or "0")
    except ValueError:
        start = 0
    for offset, (key, value) in enumerate(entries):
        env[f"GIT_CONFIG_KEY_{start + offset}"] = key
        env[f"GIT_CONFIG_VALUE_{start + offset}"] = value
    env["GIT_CONFIG_COUNT"] = str(start + len(entries))
    return env


def _git(
    args: list[str], *, token: str | None, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        env=git_env(token),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )


def _scrub(text: str, token: str | None) -> str:
    """Belt and braces: git does not echo extra headers, but never risk it."""
    text = text.strip()
    if token:
        text = text.replace(token, "***")
    return text


def repo_clone(
    repo: str,
    path: Path,
    token: str | None,
    branch: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Clone OWNER/REPO into `path` without prompting or storing the token."""
    target = path.resolve()
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        return _err(f"Target exists and is not an empty directory: {target}")

    args = ["clone"]
    if branch:
        args += ["--branch", branch]
    args += ["--", clone_url(repo), str(target)]
    cp = _git(args, token=token)
    if cp.returncode != 0:
        return _err(
            f"git clone failed: {_scrub(cp.stderr, token)}", returncode=cp.returncode
        )
    return _ok(f"Cloned {repo} into {target}")


def repo_pull(path: Path, token: str | None) -> subprocess.CompletedProcess[str]:
    """Fast-forward the checkout at `path` from its upstream, never prompting.

    `--ff-only` so an unattended pull can never create a merge commit or open
    an editor; a diverged branch fails loudly instead.
    """
    worktree = path.resolve()
    # subprocess raises before git runs if cwd is missing or a file.
    if not worktree.is_dir():
        return _err(f"Not a git working tree: {worktree}")
    probe = _git(["rev-parse", "--is-inside-work-tree"], token=None, cwd=worktree)
    if probe.returncode != 0 or probe.stdout.strip() != "true":
        return _err(f"Not a git working tree: {worktree}")

    cp = _git(["pull", "--ff-only"], token=token, cwd=worktree)
    if cp.returncode != 0:
        return _err(
            f"git pull failed: {_scrub(cp.stderr, token)}", returncode=cp.returncode
        )
    return _ok(_scrub(cp.stdout, token) or f"Pulled {worktree}")
