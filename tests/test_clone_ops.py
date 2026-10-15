"""Tests for non-interactive `repo clone` / `repo pull` (#351)."""

from __future__ import annotations

import base64
import os
import subprocess
from pathlib import Path

import pytest

from repo_scaffold import clone_ops
from repo_scaffold.cli import build_parser, main
from repo_scaffold.clone_ops import clone_url, git_env, repo_clone, repo_pull

TOKEN = "ghp_ExampleSecretValue0123456789abcd"
HEADER_KEY = "http.https://github.com/.extraheader"


def _git(*args: str, cwd: Path | None = None) -> str:
    cp = subprocess.run(
        [
            "git",
            "-c",
            "user.email=a@b",
            "-c",
            "user.name=a",
            "-c",
            "init.defaultBranch=main",
            *args,
        ],
        cwd=str(cwd) if cwd else None,
        check=True,
        capture_output=True,
        text=True,
    )
    return cp.stdout


@pytest.fixture()
def remote(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A local bare repo with one commit on main, standing in for GitHub."""
    seed = tmp_path / "seed"
    seed.mkdir()
    _git("init", "-q", ".", cwd=seed)
    (seed / "README.md").write_text("one\n", encoding="utf-8")
    _git("add", ".", cwd=seed)
    _git("commit", "-q", "-m", "one", cwd=seed)
    _git("branch", "-q", "feature", cwd=seed)
    bare = tmp_path / "remote.git"
    _git("clone", "-q", "--bare", str(seed), str(bare))
    monkeypatch.setattr(clone_ops, "clone_url", lambda _repo: str(bare))
    return bare


def _push_second_commit(remote: Path, tmp_path: Path) -> None:
    work = tmp_path / "pusher"
    _git("clone", "-q", str(remote), str(work))
    (work / "README.md").write_text("two\n", encoding="utf-8")
    _git("commit", "-q", "-am", "two", cwd=work)
    _git("push", "-q", "origin", "HEAD:main", cwd=work)


# -- git_env ---------------------------------------------------------------


def test_clone_url_is_clean() -> None:
    assert clone_url("o/r") == "https://github.com/o/r.git"


def test_env_disables_every_prompt() -> None:
    env = git_env(TOKEN, base={})

    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GCM_INTERACTIVE"] == "never"


def test_env_resets_helpers_and_scopes_header_to_github() -> None:
    env = git_env(TOKEN, base={})

    assert env["GIT_CONFIG_COUNT"] == "2"
    assert (env["GIT_CONFIG_KEY_0"], env["GIT_CONFIG_VALUE_0"]) == (
        "credential.helper",
        "",
    )
    assert env["GIT_CONFIG_KEY_1"] == HEADER_KEY
    expected = base64.b64encode(f"x-access-token:{TOKEN}".encode()).decode()
    assert env["GIT_CONFIG_VALUE_1"] == f"AUTHORIZATION: basic {expected}"


def test_env_without_token_still_resets_helpers() -> None:
    env = git_env(None, base={})

    assert env["GIT_CONFIG_COUNT"] == "1"
    assert env["GIT_CONFIG_KEY_0"] == "credential.helper"
    assert HEADER_KEY not in env.values()


def test_env_appends_to_existing_env_config() -> None:
    base = {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.autocrlf",
        "GIT_CONFIG_VALUE_0": "false",
    }
    env = git_env(TOKEN, base=base)

    assert env["GIT_CONFIG_COUNT"] == "3"
    assert env["GIT_CONFIG_KEY_0"] == "core.autocrlf"
    assert env["GIT_CONFIG_KEY_1"] == "credential.helper"
    assert env["GIT_CONFIG_KEY_2"] == HEADER_KEY


def test_git_actually_reads_the_env_config(tmp_path: Path) -> None:
    """The helper reset must land last, after any system/global/local helper."""
    _git("init", "-q", ".", cwd=tmp_path)
    _git("config", "credential.helper", "manager", cwd=tmp_path)
    env = git_env(TOKEN, base=dict(os.environ))

    helpers = subprocess.run(
        ["git", "config", "--get-all", "credential.helper"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    header = subprocess.run(
        ["git", "config", "--get", HEADER_KEY],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    ).stdout.strip()

    assert helpers[-1] == ""
    assert header.startswith("AUTHORIZATION: basic ")


# -- token never in argv or on disk ----------------------------------------


def test_token_is_never_in_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[list[str]] = []

    def fake_run(args, **kwargs):  # type: ignore[no-untyped-def]
        seen.append(list(args))
        assert kwargs["stdin"] is subprocess.DEVNULL
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(clone_ops.subprocess, "run", fake_run)
    repo_clone("o/r", tmp_path / "x", TOKEN, branch="main")

    assert seen
    flat = " ".join(" ".join(a) for a in seen)
    assert TOKEN not in flat
    assert base64.b64encode(f"x-access-token:{TOKEN}".encode()).decode() not in flat


def test_clone_leaves_no_credential_on_disk(remote: Path, tmp_path: Path) -> None:
    target = tmp_path / "clone"
    cp = repo_clone("o/r", target, TOKEN)

    assert cp.returncode == 0, cp.stderr
    assert (target / "README.md").read_text(encoding="utf-8") == "one\n"
    config = (target / ".git" / "config").read_text(encoding="utf-8")
    assert TOKEN not in config
    assert "extraheader" not in config
    assert "credential" not in config
    assert not (target / ".git" / ".git-credentials").exists()


def test_clone_checks_out_requested_branch(remote: Path, tmp_path: Path) -> None:
    target = tmp_path / "clone"
    assert repo_clone("o/r", target, TOKEN, branch="feature").returncode == 0

    head = _git("rev-parse", "--abbrev-ref", "HEAD", cwd=target).strip()
    assert head == "feature"


def test_clone_into_empty_existing_dir_is_allowed(remote: Path, tmp_path: Path) -> None:
    target = tmp_path / "empty"
    target.mkdir()

    assert repo_clone("o/r", target, TOKEN).returncode == 0


def test_clone_refuses_non_empty_target(remote: Path, tmp_path: Path) -> None:
    target = tmp_path / "busy"
    target.mkdir()
    (target / "keep.txt").write_text("x", encoding="utf-8")

    cp = repo_clone("o/r", target, TOKEN)

    assert cp.returncode != 0
    assert "not an empty directory" in cp.stderr
    assert (target / "keep.txt").exists()


def test_clone_failure_is_reported_without_the_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        clone_ops, "clone_url", lambda _repo: str(tmp_path / "missing.git")
    )
    cp = repo_clone("o/r", tmp_path / "clone", TOKEN)

    assert cp.returncode != 0
    assert cp.stderr.startswith("git clone failed:")
    assert TOKEN not in cp.stderr


# -- pull ------------------------------------------------------------------


def test_pull_fast_forwards(remote: Path, tmp_path: Path) -> None:
    target = tmp_path / "clone"
    assert repo_clone("o/r", target, TOKEN).returncode == 0
    _push_second_commit(remote, tmp_path)

    cp = repo_pull(target, TOKEN)

    assert cp.returncode == 0, cp.stderr
    assert (target / "README.md").read_text(encoding="utf-8") == "two\n"
    assert TOKEN not in (target / ".git" / "config").read_text(encoding="utf-8")


def test_pull_refuses_to_merge_diverged_history(remote: Path, tmp_path: Path) -> None:
    target = tmp_path / "clone"
    assert repo_clone("o/r", target, TOKEN).returncode == 0
    _push_second_commit(remote, tmp_path)
    (target / "local.txt").write_text("local\n", encoding="utf-8")
    _git("add", ".", cwd=target)
    _git("commit", "-q", "-m", "local", cwd=target)

    cp = repo_pull(target, TOKEN)

    assert cp.returncode != 0
    assert cp.stderr.startswith("git pull failed:")


def test_pull_refuses_non_checkout(tmp_path: Path) -> None:
    cp = repo_pull(tmp_path, TOKEN)

    assert cp.returncode != 0
    assert "Not a git working tree" in cp.stderr


# -- CLI -------------------------------------------------------------------


def test_parser_knows_clone_and_pull() -> None:
    ns = build_parser().parse_args(
        ["repo", "clone", "--repo", "o/r", "--path", "/tmp/x", "--branch", "b"]
    )
    assert (ns.mode, ns.repo_command, ns.repo, ns.branch) == (
        "repo",
        "clone",
        "o/r",
        "b",
    )

    ns = build_parser().parse_args(["repo", "pull", "--path", "/tmp/x"])
    assert (ns.mode, ns.repo_command, ns.path) == ("repo", "pull", "/tmp/x")


def test_cli_clone_and_pull_end_to_end(
    remote: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("repo_scaffold.cli._token_for_auth_path", lambda _p: TOKEN)
    target = tmp_path / "clone"

    assert main(["repo", "clone", "--repo", "o/r", "--path", str(target)]) == 0
    _push_second_commit(remote, tmp_path)
    assert main(["repo", "pull", "--path", str(target)]) == 0
    assert (target / "README.md").read_text(encoding="utf-8") == "two\n"


def test_cli_clone_rejects_bad_repo(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["repo", "clone", "--repo", "nope", "--path", "/tmp/x"]) == 2
    assert "owner/repo" in capsys.readouterr().err


def test_cli_warns_without_token(
    remote: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("repo_scaffold.cli._token_for_auth_path", lambda _p: "")
    target = tmp_path / "clone"

    assert main(["repo", "clone", "--repo", "o/r", "--path", str(target)]) == 0
    assert "no GH_TOKEN" in capsys.readouterr().err


def test_cli_pull_failure_exits_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("repo_scaffold.cli._token_for_auth_path", lambda _p: TOKEN)

    assert main(["repo", "pull", "--path", str(tmp_path)]) == 1
    assert "Not a git working tree" in capsys.readouterr().err
