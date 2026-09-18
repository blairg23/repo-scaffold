"""Tests for local git credential configuration."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from repo_scaffold import auth_ops
from repo_scaffold.auth_ops import configure_auth
from repo_scaffold.cli import build_parser, main

TOKEN = "ghp_ExampleSecretValue0123456789abcd"


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never hit the network; github_username falls back on its own otherwise."""
    monkeypatch.setattr(auth_ops, "github_username", lambda _token: "alice")


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    d = tmp_path / "checkout"
    d.mkdir()
    subprocess.run(["git", "init", "-q", "."], cwd=d, check=True, capture_output=True)
    return d


def _helpers(repo: Path) -> list[str]:
    cp = subprocess.run(
        ["git", "config", "--get-all", "credential.helper"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    # Keep empty entries: the empty string is the reset marker that overrides the
    # system-level GCM helper, so filtering blanks would hide the thing under test.
    return cp.stdout.splitlines()


def test_configures_credential_store(repo: Path) -> None:
    cp = configure_auth(TOKEN, path=repo)

    assert cp.returncode == 0
    creds = repo / ".git" / ".git-credentials"
    assert creds.is_file()
    assert f"https://alice:{TOKEN}@github.com" in creds.read_text(encoding="utf-8")


def test_credentials_file_is_lf_only_without_bom(repo: Path) -> None:
    """git-credential-store rejects BOM and CRLF on every platform."""
    configure_auth(TOKEN, path=repo)

    raw = (repo / ".git" / ".git-credentials").read_bytes()
    assert b"\r" not in raw
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert raw.endswith(b"\n")


def test_local_helper_chain_is_reset_then_set(repo: Path) -> None:
    configure_auth(TOKEN, path=repo)

    helpers = _helpers(repo)
    assert helpers[0] == ""
    assert helpers[-1].startswith("store --file")


def test_is_idempotent(repo: Path) -> None:
    """Re-running must not accumulate helper entries."""
    configure_auth(TOKEN, path=repo)
    first = _helpers(repo)
    configure_auth(TOKEN, path=repo)

    assert _helpers(repo) == first


def test_errors_when_not_a_git_repository(tmp_path: Path) -> None:
    cp = configure_auth(TOKEN, path=tmp_path)

    assert cp.returncode != 0
    assert "Not a git repository" in cp.stderr


def test_errors_without_a_token(repo: Path) -> None:
    cp = configure_auth("", path=repo)

    assert cp.returncode != 0
    assert not (repo / ".git" / ".git-credentials").exists()


def test_auth_configure_is_a_recognised_top_level_command() -> None:
    """Regression: _normalize_argv rewrites unknown first args to `init`."""
    ns = build_parser().parse_args(["auth", "configure", "--path", "/tmp/x"])

    assert ns.mode == "auth"
    assert ns.auth_command == "configure"


def test_auth_configure_survives_argv_normalisation(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real entry point, not just the parser, must route `auth` correctly."""
    monkeypatch.setattr("repo_scaffold.cli.token_from_repo", lambda _path: TOKEN)

    assert main(["auth", "configure", "--path", str(repo)]) == 0
    assert (repo / ".git" / ".git-credentials").is_file()


def test_deprecated_workspace_alias_still_configures(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("repo_scaffold.cli.token_from_repo", lambda _path: TOKEN)

    assert main(["workspace", "configure-auth", "--path", str(repo)]) == 0
    assert (repo / ".git" / ".git-credentials").is_file()


def test_deprecated_workspace_alias_warns(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("repo_scaffold.cli.token_from_repo", lambda _path: TOKEN)

    with pytest.warns(DeprecationWarning, match="auth configure"):
        main(["workspace", "configure-auth", "--path", str(repo)])


def test_workspace_ops_no_longer_owns_the_implementation() -> None:
    """auth_ops must not route through the deprecated module (see #316)."""
    from repo_scaffold import workspace_ops

    assert workspace_ops.workspace_configure_auth is configure_auth
