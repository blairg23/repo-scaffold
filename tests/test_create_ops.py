from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import repo_scaffold.create_ops as create_ops


def test_resolve_repo_rejects_empty_owner_or_name() -> None:
    with pytest.raises(RuntimeError, match="owner/repo format"):
        create_ops._resolve_repo(
            repo_dir=Path("/tmp/example"),
            env={},
            repo="/demo",
            owner=None,
            name=None,
        )

    with pytest.raises(RuntimeError, match="owner/repo format"):
        create_ops._resolve_repo(
            repo_dir=Path("/tmp/example"),
            env={},
            repo="acme/",
            owner=None,
            name=None,
        )


def test_resolve_repo_accepts_host_owner_repo_from_env() -> None:
    resolved = create_ops._resolve_repo(
        repo_dir=Path("/tmp/example"),
        env={"GH_REPO": "github.com/acme/demo"},
        repo=None,
        owner=None,
        name=None,
    )
    assert resolved == "acme/demo"


def test_default_branch_ruleset_payload_uses_zero_review_baseline() -> None:
    payload = json.loads(create_ops._default_branch_ruleset_payload())
    assert payload["name"] == "repo-scaffold default-branch ruleset"
    assert payload["conditions"]["ref_name"]["include"] == ["~DEFAULT_BRANCH"]
    pull_request_rule = next(
        rule for rule in payload["rules"] if rule["type"] == "pull_request"
    )
    assert pull_request_rule["parameters"]["required_approving_review_count"] == 0
    assert pull_request_rule["parameters"]["allowed_merge_methods"] == ["squash"]
    assert pull_request_rule["parameters"]["required_review_thread_resolution"] is True


def test_apply_settings_dry_run_previews_ruleset_and_security_defaults() -> None:
    lines: list[str] = []

    create_ops._apply_settings(
        repo_dir=Path("/tmp/repo"),
        env={},
        repo="acme/repo",
        dry_run=True,
        out=lines.append,
        warn=lines.append,
    )

    assert "sync managed default-branch ruleset" in "\n".join(lines)
    assert "0 approvals" in "\n".join(lines)
    assert "[dry-run] enable secret scanning" in lines
    assert "[dry-run] enable secret scanning push protection" in lines


def test_apply_settings_uses_ruleset_and_public_security_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, object] = {}
    optional_features: list[tuple[str, str]] = []
    security_features: list[tuple[str, str]] = []

    def _fake_get_repo_info(*, repo_dir: Path, env: dict[str, str], repo: str):
        return {"default_branch": "main", "visibility": "public"}

    def _fake_api(
        *,
        repo_dir: Path,
        env: dict[str, str],
        method: str,
        endpoint: str,
        stdin_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        calls["method"] = method
        calls["endpoint"] = endpoint
        calls["payload"] = stdin_text
        return subprocess.CompletedProcess(
            args=["gh", "api"], returncode=0, stdout="", stderr=""
        )

    def _fake_clear_legacy_branch_protection(**kwargs):
        calls["default_branch"] = kwargs["default_branch"]

    def _fake_sync_default_branch_ruleset(**kwargs):
        calls["ruleset_repo"] = kwargs["repo"]

    def _fake_enable_security_and_analysis_feature(**kwargs):
        security_features.append((kwargs["feature_name"], kwargs["feature_key"]))

    def _fake_enable_optional_endpoint_feature(**kwargs):
        optional_features.append((kwargs["feature_name"], kwargs["endpoint"]))

    monkeypatch.setattr(create_ops, "_get_repo_info", _fake_get_repo_info)
    monkeypatch.setattr(create_ops, "_api", _fake_api)
    monkeypatch.setattr(
        create_ops, "_clear_legacy_branch_protection", _fake_clear_legacy_branch_protection
    )
    monkeypatch.setattr(
        create_ops, "_sync_default_branch_ruleset", _fake_sync_default_branch_ruleset
    )
    monkeypatch.setattr(
        create_ops,
        "_enable_security_and_analysis_feature",
        _fake_enable_security_and_analysis_feature,
    )
    monkeypatch.setattr(
        create_ops,
        "_enable_optional_endpoint_feature",
        _fake_enable_optional_endpoint_feature,
    )

    create_ops._apply_settings(
        repo_dir=Path("/tmp/repo"),
        env={},
        repo="acme/repo",
        dry_run=False,
        out=lambda _: None,
        warn=lambda _: None,
    )

    assert calls["method"] == "PATCH"
    assert calls["endpoint"] == "/repos/acme/repo"
    payload = json.loads(str(calls["payload"]))
    assert payload["allow_merge_commit"] is False
    assert payload["allow_rebase_merge"] is False
    assert payload["allow_squash_merge"] is True
    assert payload["delete_branch_on_merge"] is True
    assert payload["allow_auto_merge"] is True
    assert payload["is_template"] is False
    assert calls["default_branch"] == "main"
    assert calls["ruleset_repo"] == "acme/repo"
    assert ("Secret scanning", "secret_scanning") in security_features
    assert (
        "Secret scanning push protection",
        "secret_scanning_push_protection",
    ) in security_features
    assert (
        "Private vulnerability reporting",
        "/repos/acme/repo/private-vulnerability-reporting",
    ) in optional_features


def test_create_or_push_repo_uses_absolute_source_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    repo_dir = Path("rel-repo")
    repo_dir.mkdir(parents=True)

    run_calls: list[tuple[list[str], Path]] = []

    def _fake_repo_exists(*, repo_dir: Path, env: dict[str, str], repo: str) -> bool:
        assert repo_dir == repo_dir.resolve()
        return False

    def _fake_run(
        args: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        stdin_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        run_calls.append((args, cwd))
        return subprocess.CompletedProcess(
            args=args, returncode=0, stdout="", stderr=""
        )

    def _fake_push_main(
        *, repo_dir: Path, env: dict[str, str]
    ) -> tuple[bool, str | None]:
        return True, None

    monkeypatch.setattr(create_ops, "_repo_exists", _fake_repo_exists)
    monkeypatch.setattr(create_ops, "_run", _fake_run)
    monkeypatch.setattr(create_ops, "_push_main", _fake_push_main)

    created, pushed, error = create_ops._create_or_push_repo(
        repo_dir=repo_dir,
        env={},
        repo="acme/example",
        visibility="public",
        dry_run=False,
        out=lambda _: None,
    )

    assert created is True
    assert pushed is True
    assert error is None
    assert run_calls, "Expected gh repo create call"
    gh_call_args, gh_call_cwd = run_calls[0]
    assert gh_call_args[:4] == ["gh", "repo", "create", "acme/example"]
    assert "--source" in gh_call_args
    source_idx = gh_call_args.index("--source") + 1
    assert Path(gh_call_args[source_idx]).is_absolute()
    assert gh_call_cwd.is_absolute()


def test_ensure_git_repo_initializes_when_inside_parent_repo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "out" / "child-repo"
    repo_dir.mkdir(parents=True)

    calls: list[list[str]] = []

    def _fake_run(
        args: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        stdin_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args == ["git", "rev-parse", "--show-toplevel"]:
            # Simulate being inside a parent git repo, not repo_dir itself.
            return subprocess.CompletedProcess(
                args=args, returncode=0, stdout=str(tmp_path), stderr=""
            )
        if args == ["git", "init"]:
            return subprocess.CompletedProcess(
                args=args, returncode=0, stdout="", stderr=""
            )
        if args == ["git", "rev-parse", "--verify", "HEAD"]:
            return subprocess.CompletedProcess(
                args=args, returncode=1, stdout="", stderr=""
            )
        if args == ["git", "config", "user.name"]:
            return subprocess.CompletedProcess(
                args=args, returncode=0, stdout="Tester\n", stderr=""
            )
        if args == ["git", "config", "user.email"]:
            return subprocess.CompletedProcess(
                args=args, returncode=0, stdout="tester@example.com\n", stderr=""
            )
        if args == ["git", "add", "-A"]:
            return subprocess.CompletedProcess(
                args=args, returncode=0, stdout="", stderr=""
            )
        if args == ["git", "diff", "--cached", "--quiet"]:
            return subprocess.CompletedProcess(
                args=args, returncode=1, stdout="", stderr=""
            )
        if args == ["git", "commit", "-m", "Initial scaffold"]:
            return subprocess.CompletedProcess(
                args=args, returncode=0, stdout="", stderr=""
            )
        raise AssertionError(f"Unexpected git args: {args}")

    monkeypatch.setattr(create_ops, "_run", _fake_run)

    create_ops._ensure_git_repo(
        repo_dir=repo_dir,
        env={},
        dry_run=False,
        out=lambda _: None,
    )

    assert ["git", "init"] in calls
    assert ["git", "branch", "-M", "main"] not in calls


def test_push_main_uses_head_ref_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def _fake_resolve_push_token(*, repo_dir: Path, env: dict[str, str]) -> str | None:
        return None

    def _fake_run(
        args: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        stdin_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(
            args=args, returncode=0, stdout="", stderr=""
        )

    monkeypatch.setattr(create_ops, "_resolve_push_token", _fake_resolve_push_token)
    monkeypatch.setattr(create_ops, "_run", _fake_run)

    pushed, error = create_ops._push_main(repo_dir=Path("/tmp/repo"), env={})
    assert pushed is True
    assert error is None
    assert calls == [["git", "push", "-u", "origin", "HEAD:main"]]


def test_push_main_uses_head_ref_with_token(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def _fake_resolve_push_token(*, repo_dir: Path, env: dict[str, str]) -> str | None:
        return "ghs_test"

    def _fake_run(
        args: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        stdin_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(
            args=args, returncode=0, stdout="", stderr=""
        )

    monkeypatch.setattr(create_ops, "_resolve_push_token", _fake_resolve_push_token)
    monkeypatch.setattr(create_ops, "_run", _fake_run)

    pushed, error = create_ops._push_main(repo_dir=Path("/tmp/repo"), env={})
    assert pushed is True
    assert error is None
    assert calls
    push_args = calls[0]
    assert push_args[-4:] == ["push", "-u", "origin", "HEAD:main"]
