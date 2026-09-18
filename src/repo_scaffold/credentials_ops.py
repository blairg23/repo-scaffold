"""Detect credentials that have been persisted into local git configuration.

A token embedded in `remote.origin.url` never reaches GitHub -- `.git/config` is
not tracked -- but it sits in plaintext on disk, survives in any synced or backed
up copy of the checkout, and is printed verbatim by `git remote -v`, which makes
it easy to paste into an issue or a screenshot without noticing.

This has already happened twice in this project (see b8a9f12, which stripped the
`x-token:` form out of bare clone configs). The incentive is structural: an agent
blocked by a credential prompt can clear it with one `git remote set-url`, the
task proceeds, and the residue lands in a file nobody reads again. So the check
is automated rather than left to whoever is mid-task to remember.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# userinfo in an HTTP(S) remote: scheme://<user>[:<secret>]@host/...
# Captured so the secret can be redacted without discarding the rest of the URL.
_URL_USERINFO = re.compile(
    r"^(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*://)(?P<user>[^/@:]+)(?::(?P<secret>[^/@]*))?@(?P<rest>.+)$"
)

# Config keys git reports in lowercase; extraheader is only a finding when it
# carries an actual authorization value.
_EXTRAHEADER_KEY = re.compile(r"^http\..*\.extraheader$|^http\.extraheader$")
_REMOTE_URL_KEY = re.compile(r"^remote\.(?P<name>.+)\.url$")

_AUTHORIZATION = re.compile(r"authorization\s*:", re.IGNORECASE)


@dataclass
class CredentialFinding:
    """One offending config entry. `detail` is always redacted."""

    config_path: Path
    key: str
    detail: str


@dataclass
class CredentialsCheckSummary:
    repo: str
    findings: list[CredentialFinding] = field(default_factory=list)
    fixed: list[str] = field(default_factory=list)
    scanned: list[Path] = field(default_factory=list)
    skipped: str | None = None

    @property
    def clean(self) -> bool:
        return not self.findings


def redact(value: str) -> str:
    """Mask any secret in `value` so findings can be printed safely.

    Keeps enough of the surrounding text to identify the entry (scheme, user,
    host) while replacing the secret itself. Falls back to masking the whole
    value when it does not parse as a URL, since an unparsed value may itself be
    a bare token.
    """
    match = _URL_USERINFO.match(value.strip())
    if match:
        secret = match.group("secret")
        if secret:
            return f"{match.group('scheme')}{match.group('user')}:<REDACTED>@{match.group('rest')}"
        return value.strip()
    if _AUTHORIZATION.search(value):
        return re.sub(
            r"(authorization\s*:\s*\S+\s+).*",
            r"\1<REDACTED>",
            value,
            flags=re.IGNORECASE,
        )
    return "<REDACTED>"


def url_has_secret(url: str) -> bool:
    """True when the URL carries a password/token in its userinfo.

    A bare `user@host` (no colon) is normal for SSH-style remotes and is not a
    finding; only an actual secret component counts.
    """
    match = _URL_USERINFO.match(url.strip())
    return bool(match and match.group("secret"))


def strip_url_secret(url: str) -> str:
    """Return `url` with any userinfo removed, preserving scheme, host and path."""
    match = _URL_USERINFO.match(url.strip())
    if not match:
        return url.strip()
    return f"{match.group('scheme')}{match.group('rest')}"


def _git_config_entries(config_path: Path) -> list[tuple[str, str]]:
    """Read one git config file as (key, value) pairs, lowercased keys.

    Uses git itself rather than an ad-hoc parser so includes, quoting and
    section/subsection syntax behave exactly as git would interpret them.
    """
    cp = subprocess.run(
        ["git", "config", "--file", str(config_path), "--list", "--null"],
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    if cp.returncode != 0:
        return []
    entries: list[tuple[str, str]] = []
    for record in cp.stdout.split("\0"):
        if not record:
            continue
        key, _, value = record.partition("\n")
        entries.append((key.strip().lower(), value))
    return entries


def _config_paths(repo_dir: Path) -> list[Path]:
    """Every git config under `repo_dir` worth scanning.

    Covers the working tree's own config plus any bare clones repo-scaffold
    keeps under repos/<owner>/<name>/.bare, which is where the first instance of
    this bug was found.
    """
    paths: list[Path] = []
    working = repo_dir / ".git" / "config"
    if working.is_file():
        paths.append(working)
    # A bare repo_dir (no .git dir) keeps its config at the top level.
    bare_self = repo_dir / "config"
    if not working.is_file() and (repo_dir / "HEAD").is_file() and bare_self.is_file():
        paths.append(bare_self)
    repos_root = repo_dir / "repos"
    if repos_root.is_dir():
        paths.extend(sorted(repos_root.glob("*/*/.bare/config")))
    return paths


def scan_git_config(config_path: Path) -> list[CredentialFinding]:
    """Findings for a single git config file."""
    findings: list[CredentialFinding] = []
    for key, value in _git_config_entries(config_path):
        remote = _REMOTE_URL_KEY.match(key)
        if remote and url_has_secret(value):
            findings.append(
                CredentialFinding(
                    config_path=config_path,
                    key=key,
                    detail=redact(value),
                )
            )
            continue
        if _EXTRAHEADER_KEY.match(key) and _AUTHORIZATION.search(value):
            findings.append(
                CredentialFinding(
                    config_path=config_path,
                    key=key,
                    detail=redact(value),
                )
            )
    return findings


def check_repository_credentials(
    *,
    repo_dir: Path,
    repo: str,
    fix: bool = False,
    out: Callable[[str], None] = print,
) -> CredentialsCheckSummary:
    """Scan `repo_dir` for persisted credentials, optionally rewriting them away.

    A repo with no local checkout, or a directory that is not a git repository,
    is reported as skipped rather than as an error. Under --all the registry
    routinely contains repos that were never cloned on this machine, and failing
    the whole run for those would make the check useless where it matters most.
    Skips are still surfaced so an unscanned repo is never mistaken for a clean
    one.
    """
    summary = CredentialsCheckSummary(repo=repo)
    if not repo_dir.is_dir():
        summary.skipped = f"no local checkout at {repo_dir}"
        return summary

    summary.scanned = _config_paths(repo_dir)
    if not summary.scanned:
        summary.skipped = f"not a git repository: {repo_dir}"
        return summary

    for config_path in summary.scanned:
        summary.findings.extend(scan_git_config(config_path))

    if fix and summary.findings:
        _apply_fixes(summary=summary, repo=repo, out=out)

    return summary


def _apply_fixes(
    *,
    summary: CredentialsCheckSummary,
    repo: str,
    out: Callable[[str], None],
) -> None:
    """Rewrite offending entries in place.

    Remote URLs are rewritten to their tokenless form. `origin` goes through
    create_ops._ensure_origin_remote so the canonical URL shape stays defined in
    exactly one place; other remotes keep their existing host and path with only
    the userinfo removed, since repo-scaffold has no opinion about where they
    point. A persisted extraheader is unset outright -- the supported form is
    the in-memory `-c http.extraheader=...` used by create_ops._push_main, which
    never touches disk.
    """
    from repo_scaffold.create_ops import _ensure_origin_remote

    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    for finding in summary.findings:
        git_dir = finding.config_path.parent
        work_dir = git_dir.parent if git_dir.name == ".git" else git_dir
        remote = _REMOTE_URL_KEY.match(finding.key)
        if remote:
            name = remote.group("name")
            if name == "origin" and work_dir.joinpath(".git").is_dir():
                _ensure_origin_remote(
                    repo_dir=work_dir,
                    env=env,
                    repo=repo,
                    dry_run=False,
                    out=out,
                )
            else:
                current = subprocess.run(
                    [
                        "git",
                        "config",
                        "--file",
                        str(finding.config_path),
                        "--get",
                        finding.key,
                    ],
                    capture_output=True,
                    text=True,
                    env=env,
                )
                cleaned = strip_url_secret(current.stdout.strip())
                subprocess.run(
                    [
                        "git",
                        "config",
                        "--file",
                        str(finding.config_path),
                        finding.key,
                        cleaned,
                    ],
                    capture_output=True,
                    text=True,
                    env=env,
                )
            summary.fixed.append(finding.key)
            continue
        subprocess.run(
            [
                "git",
                "config",
                "--file",
                str(finding.config_path),
                "--unset-all",
                finding.key,
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        summary.fixed.append(finding.key)
