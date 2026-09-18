"""Tests for detecting credentials persisted into local git configuration."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from repo_scaffold.credentials_ops import (
    check_repository_credentials,
    redact,
    strip_url_secret,
    url_has_secret,
)

SECRET = "ghp_ExampleSecretValue0123456789abcd"


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    d = tmp_path / "checkout"
    d.mkdir()
    _git("init", "-q", ".", cwd=d)
    return d


def test_tokenless_remote_is_clean(repo: Path) -> None:
    _git("remote", "add", "origin", "https://github.com/o/r.git", cwd=repo)

    summary = check_repository_credentials(repo_dir=repo, repo="o/r")

    assert summary.clean
    assert summary.findings == []


def test_credentialed_remote_is_flagged(repo: Path) -> None:
    _git(
        "remote",
        "add",
        "origin",
        f"https://alice:{SECRET}@github.com/o/r.git",
        cwd=repo,
    )

    summary = check_repository_credentials(repo_dir=repo, repo="o/r")

    assert not summary.clean
    assert [f.key for f in summary.findings] == ["remote.origin.url"]


def test_username_form_that_the_bare_clone_fix_missed_is_flagged(repo: Path) -> None:
    """b8a9f12 stripped the `x-token:` form; the real incident used a real login."""
    _git(
        "remote",
        "add",
        "origin",
        f"https://blairg23:{SECRET}@github.com/o/r.git",
        cwd=repo,
    )

    summary = check_repository_credentials(repo_dir=repo, repo="o/r")

    assert [f.key for f in summary.findings] == ["remote.origin.url"]


def test_x_token_form_is_flagged(repo: Path) -> None:
    _git(
        "remote",
        "add",
        "origin",
        f"https://x-token:{SECRET}@github.com/o/r.git",
        cwd=repo,
    )

    summary = check_repository_credentials(repo_dir=repo, repo="o/r")

    assert [f.key for f in summary.findings] == ["remote.origin.url"]


def test_ssh_style_userinfo_without_secret_is_not_flagged(repo: Path) -> None:
    """`git@host` has userinfo but no secret, and is the normal SSH remote shape."""
    _git("remote", "add", "origin", "ssh://git@github.com/o/r.git", cwd=repo)

    summary = check_repository_credentials(repo_dir=repo, repo="o/r")

    assert summary.clean


def test_persisted_extraheader_is_flagged(repo: Path) -> None:
    _git("remote", "add", "origin", "https://github.com/o/r.git", cwd=repo)
    _git(
        "config",
        "http.https://github.com/.extraheader",
        "AUTHORIZATION: basic eC1hY2Nlc3MtdG9rZW46Zm9v",
        cwd=repo,
    )

    summary = check_repository_credentials(repo_dir=repo, repo="o/r")

    assert [f.key for f in summary.findings] == ["http.https://github.com/.extraheader"]


def test_repo_without_persisted_extraheader_is_clean(repo: Path) -> None:
    """The supported form is an in-memory `-c` flag, which never lands in config."""
    _git("remote", "add", "origin", "https://github.com/o/r.git", cwd=repo)

    summary = check_repository_credentials(repo_dir=repo, repo="o/r")

    assert summary.clean


def test_bare_clone_config_under_repos_is_scanned(repo: Path) -> None:
    bare = repo / "repos" / "owner" / "name" / ".bare"
    bare.mkdir(parents=True)
    subprocess.run(
        ["git", "init", "-q", "--bare", "."], cwd=bare, check=True, capture_output=True
    )
    _git("remote", "add", "origin", "https://github.com/o/r.git", cwd=repo)
    subprocess.run(
        [
            "git",
            "remote",
            "add",
            "origin",
            f"https://x-token:{SECRET}@github.com/o/r.git",
        ],
        cwd=bare,
        check=True,
        capture_output=True,
    )

    summary = check_repository_credentials(repo_dir=repo, repo="o/r")

    assert not summary.clean
    assert any(".bare" in str(f.config_path) for f in summary.findings)


def test_secret_never_appears_in_output(repo: Path) -> None:
    _git(
        "remote",
        "add",
        "origin",
        f"https://alice:{SECRET}@github.com/o/r.git",
        cwd=repo,
    )
    _git(
        "config",
        "http.https://github.com/.extraheader",
        f"AUTHORIZATION: basic {SECRET}",
        cwd=repo,
    )

    summary = check_repository_credentials(repo_dir=repo, repo="o/r")

    rendered = " ".join(f"{f.key} {f.detail} {f.config_path}" for f in summary.findings)
    assert SECRET not in rendered
    assert "<REDACTED>" in rendered


def test_missing_checkout_is_skipped_not_failed(tmp_path: Path) -> None:
    """--all across the registry routinely hits repos never cloned on this machine."""
    summary = check_repository_credentials(repo_dir=tmp_path / "absent", repo="o/r")

    assert summary.skipped is not None
    assert summary.findings == []


def test_non_git_directory_is_skipped(tmp_path: Path) -> None:
    summary = check_repository_credentials(repo_dir=tmp_path, repo="o/r")

    assert summary.skipped is not None


def test_fix_rewrites_origin_to_tokenless_url(repo: Path) -> None:
    _git(
        "remote",
        "add",
        "origin",
        f"https://alice:{SECRET}@github.com/o/r.git",
        cwd=repo,
    )

    check_repository_credentials(
        repo_dir=repo, repo="o/r", fix=True, out=lambda _: None
    )

    after = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert after.stdout.strip() == "https://github.com/o/r.git"
    assert check_repository_credentials(repo_dir=repo, repo="o/r").clean


def test_fix_unsets_persisted_extraheader(repo: Path) -> None:
    _git("remote", "add", "origin", "https://github.com/o/r.git", cwd=repo)
    _git(
        "config",
        "http.https://github.com/.extraheader",
        "AUTHORIZATION: basic eC1hY2Nlc3MtdG9rZW46Zm9v",
        cwd=repo,
    )

    check_repository_credentials(
        repo_dir=repo, repo="o/r", fix=True, out=lambda _: None
    )

    assert check_repository_credentials(repo_dir=repo, repo="o/r").clean


def test_fix_preserves_host_and_path_for_non_origin_remotes(repo: Path) -> None:
    _git(
        "remote",
        "add",
        "upstream",
        f"https://bob:{SECRET}@example.com/a/b.git",
        cwd=repo,
    )

    check_repository_credentials(
        repo_dir=repo, repo="o/r", fix=True, out=lambda _: None
    )

    after = subprocess.run(
        ["git", "remote", "get-url", "upstream"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert after.stdout.strip() == "https://example.com/a/b.git"


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://github.com/o/r.git", False),
        (f"https://alice:{SECRET}@github.com/o/r.git", True),
        ("ssh://git@github.com/o/r.git", False),
        ("git@github.com:o/r.git", False),
    ],
)
def test_url_has_secret(url: str, expected: bool) -> None:
    assert url_has_secret(url) is expected


def test_strip_url_secret_is_a_noop_on_clean_urls() -> None:
    assert (
        strip_url_secret("https://github.com/o/r.git") == "https://github.com/o/r.git"
    )


def test_redact_masks_an_unparseable_value_entirely() -> None:
    """A bare token does not parse as a URL, so mask all of it rather than guess."""
    assert SECRET not in redact(SECRET)
