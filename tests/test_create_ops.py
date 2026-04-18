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


def test_load_env_file_and_build_env_support_aliases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cwd = tmp_path / "cwd"
    repo_dir = tmp_path / "repo"
    cwd.mkdir()
    repo_dir.mkdir()
    (cwd / ".env").write_text(
        "\n".join(
            [
                "export github_token=legacy-token",
                "github_org=legacy-org",
                "GH_REPO=from/cwd",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (repo_dir / ".env").write_text(
        "\n".join(
            [
                'GITHUB_TOKEN="repo-token"',
                "GITHUB_REPO=repo-name",
                "GITHUB_ORG=repo-org",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(cwd)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_ORG", raising=False)
    monkeypatch.delenv("GITHUB_REPO", raising=False)
    monkeypatch.delenv("GH_REPO", raising=False)
    monkeypatch.delenv("github_token", raising=False)
    monkeypatch.delenv("github_org", raising=False)
    monkeypatch.delenv("github_repo", raising=False)

    env = create_ops._build_env(repo_dir)

    assert env["GH_TOKEN"] == "repo-token"
    assert env["GITHUB_ORG"] == "repo-org"
    assert env["GITHUB_REPO"] == "repo-name"
    assert env["GH_REPO"] == "from/cwd"


def test_load_json_invalid_raises_runtime_error() -> None:
    with pytest.raises(RuntimeError, match="bad json"):
        create_ops._load_json("not-json", error_message="bad json")


def test_default_branch_ruleset_payload_uses_zero_review_baseline() -> None:
    payload = json.loads(create_ops._default_branch_ruleset_payload())
    assert payload["name"] == "repo-scaffold baseline branch rules"
    assert payload["conditions"]["ref_name"]["include"] == ["~DEFAULT_BRANCH"]
    pull_request_rule = next(
        rule for rule in payload["rules"] if rule["type"] == "pull_request"
    )
    code_scanning_rule = next(
        rule for rule in payload["rules"] if rule["type"] == "code_scanning"
    )
    copilot_code_review_rule = next(
        rule for rule in payload["rules"] if rule["type"] == "copilot_code_review"
    )
    assert pull_request_rule["parameters"]["required_approving_review_count"] == 0
    assert pull_request_rule["parameters"]["allowed_merge_methods"] == ["squash"]
    assert pull_request_rule["parameters"]["required_review_thread_resolution"] is True
    assert code_scanning_rule["parameters"]["code_scanning_tools"] == [
        {
            "tool": "CodeQL",
            "alerts_threshold": "errors",
            "security_alerts_threshold": "high_or_higher",
        }
    ]
    assert copilot_code_review_rule["parameters"]["review_draft_pull_requests"] is False
    assert copilot_code_review_rule["parameters"]["review_on_push"] is True


def test_branch_protection_endpoint_url_encodes_branch_name() -> None:
    assert (
        create_ops._branch_protection_endpoint(
            repo="acme/repo",
            branch="release/2026",
        )
        == "/repos/acme/repo/branches/release%2F2026/protection"
    )


def test_is_managed_ruleset_name_accepts_new_and_legacy_names() -> None:
    assert create_ops._is_managed_ruleset_name("repo-scaffold baseline branch rules")
    assert create_ops._is_managed_ruleset_name("repo-scaffold default-branch ruleset")
    assert create_ops._is_managed_ruleset_name("something else") is False


def test_compare_ruleset_against_baseline_reports_multiple_drifts() -> None:
    drifts = create_ops._compare_ruleset_against_baseline(
        [
            {
                "name": "repo-scaffold baseline branch rules",
                "target": "tag",
                "enforcement": "evaluate",
                "conditions": {
                    "ref_name": {
                        "include": ["refs/heads/dev"],
                        "exclude": ["refs/heads/tmp"],
                    }
                },
                "rules": [
                    {"type": "deletion"},
                    {
                        "type": "pull_request",
                        "parameters": {
                            "allowed_merge_methods": ["merge"],
                            "dismiss_stale_reviews_on_push": True,
                            "require_code_owner_review": True,
                            "require_last_push_approval": True,
                            "required_approving_review_count": 2,
                            "required_review_thread_resolution": False,
                        },
                    },
                    {
                        "type": "code_scanning",
                        "parameters": {
                            "code_scanning_tools": [
                                {
                                    "tool": "CodeQL",
                                    "alerts_threshold": "all",
                                    "security_alerts_threshold": "all",
                                }
                            ]
                        },
                    },
                    {
                        "type": "copilot_code_review",
                        "parameters": {
                            "review_draft_pull_requests": True,
                            "review_on_push": False,
                        },
                    },
                ],
            },
            {
                "name": "repo-scaffold default-branch ruleset",
                "target": "branch",
                "enforcement": "active",
                "conditions": {
                    "ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}
                },
                "rules": [],
            },
        ],
        default_branch="main",
    )

    assert "multiple managed default-branch rulesets found" in drifts
    assert "target expected 'branch' got 'tag'" in drifts
    assert "enforcement expected 'active' got 'evaluate'" in drifts
    assert any("conditions.ref_name.include expected one of" in item for item in drifts)
    assert "conditions.ref_name.exclude expected [] got ['refs/heads/tmp']" in drifts
    assert "missing rule: non_fast_forward" in drifts
    assert "missing rule: required_linear_history" in drifts
    assert "pull_request.required_approving_review_count expected 0 got 2" in drifts
    assert any("pull_request.allowed_merge_methods" in item for item in drifts)
    assert any("code_scanning.code_scanning_tools" in item for item in drifts)
    assert (
        "copilot_code_review.review_draft_pull_requests expected False got True"
        in drifts
    )
    assert "copilot_code_review.review_on_push expected True got False" in drifts


def test_repo_metadata_and_ruleset_loaders_cover_success_and_error_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(
        [
            subprocess.CompletedProcess(
                args=["gh"], returncode=0, stdout='{"default_branch":"main"}', stderr=""
            ),
            subprocess.CompletedProcess(
                args=["gh"], returncode=0, stdout='[{"id":1}]', stderr=""
            ),
            subprocess.CompletedProcess(
                args=["gh"], returncode=0, stdout='{"id":1}', stderr=""
            ),
            subprocess.CompletedProcess(
                args=["gh"], returncode=1, stdout="", stderr="boom"
            ),
            subprocess.CompletedProcess(
                args=["gh"], returncode=0, stdout="{}", stderr=""
            ),
            subprocess.CompletedProcess(
                args=["gh"], returncode=0, stdout="[]", stderr=""
            ),
        ]
    )

    monkeypatch.setattr(create_ops, "_api", lambda **_: next(responses))

    assert create_ops._get_repo_info(
        repo_dir=Path("/tmp/repo"), env={}, repo="acme/repo"
    ) == {"default_branch": "main"}
    assert create_ops._list_repo_rulesets(
        repo_dir=Path("/tmp/repo"), env={}, repo="acme/repo"
    ) == [{"id": 1}]
    assert create_ops._get_repo_ruleset(
        repo_dir=Path("/tmp/repo"), env={}, repo="acme/repo", ruleset_id=1
    ) == {"id": 1}

    with pytest.raises(RuntimeError, match="boom"):
        create_ops._list_repo_rulesets(
            repo_dir=Path("/tmp/repo"), env={}, repo="acme/repo"
        )
    with pytest.raises(RuntimeError, match="Unexpected rulesets response"):
        create_ops._list_repo_rulesets(
            repo_dir=Path("/tmp/repo"), env={}, repo="acme/repo"
        )
    with pytest.raises(RuntimeError, match="Unexpected ruleset response"):
        create_ops._get_repo_ruleset(
            repo_dir=Path("/tmp/repo"), env={}, repo="acme/repo", ruleset_id=1
        )


def test_security_feature_status_and_optional_endpoint_feature_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_info = {
        "security_and_analysis": {
            "secret_scanning": {"status": "enabled"},
            "other": {"status": "disabled"},
        }
    }
    assert (
        create_ops._security_feature_status(repo_info, "secret_scanning") == "enabled"
    )
    assert create_ops._security_feature_status(repo_info, "missing") is None
    assert create_ops._security_feature_status({}, "secret_scanning") is None

    monkeypatch.setattr(
        create_ops,
        "_api",
        lambda **kwargs: subprocess.CompletedProcess(
            args=["gh", "api"],
            returncode=1 if kwargs["endpoint"].endswith("/disabled") else 0,
            stdout="",
            stderr="not enabled" if kwargs["endpoint"].endswith("/disabled") else "",
        ),
    )

    assert create_ops._optional_endpoint_feature_enabled(
        repo_dir=Path("/tmp/repo"), env={}, endpoint="/repos/acme/repo/enabled"
    ) == (True, "enabled")
    assert create_ops._optional_endpoint_feature_enabled(
        repo_dir=Path("/tmp/repo"), env={}, endpoint="/repos/acme/repo/disabled"
    ) == (False, "not enabled")


def test_ruleset_and_legacy_branch_helpers_cover_error_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    api_responses = iter(
        [
            subprocess.CompletedProcess(
                args=["gh"], returncode=0, stdout="", stderr=""
            ),
            subprocess.CompletedProcess(
                args=["gh"], returncode=1, stdout="", stderr="branch not protected"
            ),
            subprocess.CompletedProcess(
                args=["gh"], returncode=1, stdout="", stderr="boom"
            ),
            subprocess.CompletedProcess(
                args=["gh"], returncode=0, stdout="", stderr=""
            ),
            subprocess.CompletedProcess(
                args=["gh"], returncode=1, stdout="", stderr="not found"
            ),
            subprocess.CompletedProcess(
                args=["gh"], returncode=1, stdout="", stderr="boom"
            ),
            subprocess.CompletedProcess(
                args=["gh"], returncode=1, stdout="", stderr="warn"
            ),
            subprocess.CompletedProcess(
                args=["gh"], returncode=1, stdout="", stderr="warn"
            ),
        ]
    )

    monkeypatch.setattr(
        create_ops,
        "_api",
        lambda **kwargs: (
            calls.append((kwargs["method"], kwargs["endpoint"])) or next(api_responses)
        ),
    )

    create_ops._clear_legacy_branch_protection(
        repo_dir=Path("/tmp/repo"),
        env={},
        repo="acme/repo",
        default_branch="main",
        out=lambda _line: None,
    )
    create_ops._clear_legacy_branch_protection(
        repo_dir=Path("/tmp/repo"),
        env={},
        repo="acme/repo",
        default_branch="main",
        out=lambda _line: None,
    )
    with pytest.raises(RuntimeError, match="boom"):
        create_ops._clear_legacy_branch_protection(
            repo_dir=Path("/tmp/repo"),
            env={},
            repo="acme/repo",
            default_branch="main",
            out=lambda _line: None,
        )

    assert create_ops._legacy_branch_protection_exists(
        repo_dir=Path("/tmp/repo"),
        env={},
        repo="acme/repo",
        default_branch="main",
    )
    assert (
        create_ops._legacy_branch_protection_exists(
            repo_dir=Path("/tmp/repo"),
            env={},
            repo="acme/repo",
            default_branch="main",
        )
        is False
    )
    with pytest.raises(RuntimeError, match="boom"):
        create_ops._legacy_branch_protection_exists(
            repo_dir=Path("/tmp/repo"),
            env={},
            repo="acme/repo",
            default_branch="main",
        )

    warnings: list[str] = []
    create_ops._enable_security_and_analysis_feature(
        repo_dir=Path("/tmp/repo"),
        env={},
        repo="acme/repo",
        feature_name="Secret scanning",
        feature_key="secret_scanning",
        out=lambda _line: None,
        warn=warnings.append,
    )
    create_ops._enable_optional_endpoint_feature(
        repo_dir=Path("/tmp/repo"),
        env={},
        endpoint="/repos/acme/repo/private-vulnerability-reporting",
        feature_name="Private vulnerability reporting",
        out=lambda _line: None,
        warn=warnings.append,
    )
    assert any("could not enable secret scanning" in item.lower() for item in warnings)
    assert any(
        "could not enable private vulnerability reporting" in item.lower()
        for item in warnings
    )
    assert any(endpoint.endswith("/protection") for _, endpoint in calls)


def test_sync_ruleset_covers_update_create_missing_id_and_api_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines: list[str] = []

    monkeypatch.setattr(
        create_ops,
        "_list_repo_rulesets",
        lambda **_: [{"name": "repo-scaffold default-branch ruleset", "id": 9}],
    )
    monkeypatch.setattr(
        create_ops,
        "_api",
        lambda **kwargs: (
            subprocess.CompletedProcess(args=["gh"], returncode=0, stdout="", stderr="")
            if kwargs["method"] == "PUT"
            else subprocess.CompletedProcess(
                args=["gh"], returncode=0, stdout="", stderr=""
            )
        ),
    )
    create_ops._sync_default_branch_ruleset(
        repo_dir=Path("/tmp/repo"), env={}, repo="acme/repo", out=lines.append
    )
    assert any("Updated ruleset" in line for line in lines)

    monkeypatch.setattr(create_ops, "_list_repo_rulesets", lambda **_: [])
    lines.clear()
    create_ops._sync_default_branch_ruleset(
        repo_dir=Path("/tmp/repo"), env={}, repo="acme/repo", out=lines.append
    )
    assert any("Created ruleset" in line for line in lines)

    monkeypatch.setattr(
        create_ops,
        "_list_repo_rulesets",
        lambda **_: [{"name": "repo-scaffold baseline branch rules", "id": "bad"}],
    )
    with pytest.raises(RuntimeError, match="missing a numeric id"):
        create_ops._sync_default_branch_ruleset(
            repo_dir=Path("/tmp/repo"), env={}, repo="acme/repo", out=lambda _line: None
        )

    monkeypatch.setattr(create_ops, "_list_repo_rulesets", lambda **_: [])
    monkeypatch.setattr(
        create_ops,
        "_api",
        lambda **_: subprocess.CompletedProcess(
            args=["gh"], returncode=1, stdout="", stderr="failed"
        ),
    )
    with pytest.raises(RuntimeError, match="failed"):
        create_ops._sync_default_branch_ruleset(
            repo_dir=Path("/tmp/repo"), env={}, repo="acme/repo", out=lambda _line: None
        )


def test_tool_auth_remote_and_push_helpers_cover_common_error_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()

    monkeypatch.setattr(
        create_ops.shutil,
        "which",
        lambda tool: None if tool == "gh" else "/usr/bin/git",
    )
    with pytest.raises(RuntimeError, match="GitHub CLI"):
        create_ops._ensure_tools()

    monkeypatch.setattr(
        create_ops.shutil, "which", lambda tool: "/usr/bin/gh" if tool == "gh" else None
    )
    with pytest.raises(RuntimeError, match="git is required"):
        create_ops._ensure_tools()

    monkeypatch.setattr(create_ops.shutil, "which", lambda _tool: "/usr/bin/tool")
    create_ops._ensure_tools()

    monkeypatch.setattr(
        create_ops,
        "_run",
        lambda args, **kwargs: (
            subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="")
            if args[:3] == ["gh", "auth", "status"]
            else subprocess.CompletedProcess(
                args=args, returncode=0, stdout="", stderr=""
            )
        ),
    )
    with pytest.raises(RuntimeError, match="Authenticate first"):
        create_ops._ensure_gh_auth(repo_dir, {})

    assert create_ops._repo_exists(repo_dir=repo_dir, env={}, repo="acme/repo") is True
    monkeypatch.setattr(
        create_ops,
        "_run",
        lambda args, **kwargs: subprocess.CompletedProcess(
            args=args, returncode=1, stdout="", stderr="not found"
        ),
    )
    assert create_ops._repo_exists(repo_dir=repo_dir, env={}, repo="acme/repo") is False
    monkeypatch.setattr(
        create_ops,
        "_run",
        lambda args, **kwargs: subprocess.CompletedProcess(
            args=args, returncode=1, stdout="", stderr="boom"
        ),
    )
    with pytest.raises(RuntimeError, match="boom"):
        create_ops._repo_exists(repo_dir=repo_dir, env={}, repo="acme/repo")

    calls: list[list[str]] = []
    monkeypatch.setattr(
        create_ops,
        "_run",
        lambda args, **kwargs: (
            calls.append(args)
            or subprocess.CompletedProcess(
                args=args,
                returncode=(
                    1 if args[:4] == ["git", "remote", "get-url", "origin"] else 0
                ),
                stdout="" if args[:4] == ["git", "remote", "get-url", "origin"] else "",
                stderr="",
            )
        ),
    )
    create_ops._ensure_origin_remote(
        repo_dir=repo_dir,
        env={},
        repo="acme/repo",
        dry_run=False,
        out=lambda _line: None,
    )
    assert [
        "git",
        "remote",
        "add",
        "origin",
        "https://github.com/acme/repo.git",
    ] in calls

    monkeypatch.setattr(
        create_ops,
        "_run",
        lambda args, **kwargs: subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=(
                "https://github.com/other/repo.git\n"
                if args[:4] == ["git", "remote", "get-url", "origin"]
                else ""
            ),
            stderr="",
        ),
    )
    create_ops._ensure_origin_remote(
        repo_dir=repo_dir,
        env={},
        repo="acme/repo",
        dry_run=False,
        out=lambda _line: None,
    )

    monkeypatch.setattr(
        create_ops,
        "_run",
        lambda args, **kwargs: subprocess.CompletedProcess(
            args=args,
            returncode=0 if args[:3] == ["gh", "auth", "token"] else 1,
            stdout="gh-token\n" if args[:3] == ["gh", "auth", "token"] else "",
            stderr="",
        ),
    )
    assert create_ops._resolve_push_token(repo_dir=repo_dir, env={}) == "gh-token"
    assert "workflow write permission" in create_ops._format_push_failure(
        "workflow missing scope"
    )


def test_create_or_push_repo_covers_existing_and_create_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()

    monkeypatch.setattr(create_ops, "_repo_exists", lambda **_: True)
    monkeypatch.setattr(create_ops, "_ensure_origin_remote", lambda **_: None)
    monkeypatch.setattr(
        create_ops, "_push_main", lambda **_: (False, "could not read username")
    )
    created, pushed, error = create_ops._create_or_push_repo(
        repo_dir=repo_dir,
        env={},
        repo="acme/repo",
        visibility="public",
        dry_run=False,
        out=lambda _line: None,
    )
    assert (created, pushed) == (False, False)
    assert error and "could not read username" in error

    monkeypatch.setattr(create_ops, "_repo_exists", lambda **_: False)
    monkeypatch.setattr(
        create_ops,
        "_run",
        lambda args, **kwargs: subprocess.CompletedProcess(
            args=args, returncode=1, stdout="", stderr="create failed"
        ),
    )
    created, pushed, error = create_ops._create_or_push_repo(
        repo_dir=repo_dir,
        env={},
        repo="acme/repo",
        visibility="public",
        dry_run=False,
        out=lambda _line: None,
    )
    assert (created, pushed) == (False, False)
    assert error == "create failed"


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
    call_order: list[str] = []
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
        call_order.append("clear_legacy")
        calls["default_branch"] = kwargs["default_branch"]

    def _fake_sync_default_branch_ruleset(**kwargs):
        call_order.append("sync_ruleset")
        calls["ruleset_repo"] = kwargs["repo"]

    def _fake_enable_security_and_analysis_feature(**kwargs):
        security_features.append((kwargs["feature_name"], kwargs["feature_key"]))

    def _fake_enable_optional_endpoint_feature(**kwargs):
        optional_features.append((kwargs["feature_name"], kwargs["endpoint"]))

    monkeypatch.setattr(create_ops, "_get_repo_info", _fake_get_repo_info)
    monkeypatch.setattr(create_ops, "_api", _fake_api)
    monkeypatch.setattr(
        create_ops,
        "_clear_legacy_branch_protection",
        _fake_clear_legacy_branch_protection,
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
    assert call_order == ["sync_ruleset", "clear_legacy"]
    assert ("Secret scanning", "secret_scanning") in security_features
    assert (
        "Secret scanning push protection",
        "secret_scanning_push_protection",
    ) in security_features
    assert (
        "Private vulnerability reporting",
        "/repos/acme/repo/private-vulnerability-reporting",
    ) in optional_features


def test_check_settings_reports_clean_public_repo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines: list[str] = []
    ruleset = json.loads(create_ops._default_branch_ruleset_payload())
    ruleset["id"] = 123

    monkeypatch.setattr(
        create_ops,
        "_get_repo_info",
        lambda **_: {
            **json.loads(create_ops._repo_patch_payload()),
            "default_branch": "main",
            "visibility": "public",
            "security_and_analysis": {
                "secret_scanning": {"status": "enabled"},
                "secret_scanning_push_protection": {"status": "enabled"},
            },
        },
    )
    monkeypatch.setattr(create_ops, "_list_repo_rulesets", lambda **_: [ruleset])
    monkeypatch.setattr(
        create_ops, "_legacy_branch_protection_exists", lambda **_: False
    )
    monkeypatch.setattr(
        create_ops, "_optional_endpoint_feature_enabled", lambda **_: (True, "enabled")
    )

    summary = create_ops._check_settings(
        repo_dir=Path("/tmp/repo"),
        env={},
        repo="acme/repo",
        out=lines.append,
    )

    assert summary.repo == "acme/repo"
    assert summary.passed == 8
    assert summary.failed == 0
    assert summary.skipped == 0
    assert summary.drifts == ()
    assert "check repository settings: acme/repo" in lines
    assert "PASS  managed default-branch ruleset" in lines


def test_check_settings_fetches_ruleset_details_and_accepts_expanded_default_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines: list[str] = []
    detailed_ruleset = json.loads(create_ops._default_branch_ruleset_payload())
    detailed_ruleset["id"] = 123
    detailed_ruleset["conditions"]["ref_name"]["include"] = ["refs/heads/main"]

    monkeypatch.setattr(
        create_ops,
        "_get_repo_info",
        lambda **_: {
            **json.loads(create_ops._repo_patch_payload()),
            "default_branch": "main",
            "visibility": "public",
            "security_and_analysis": {
                "secret_scanning": {"status": "enabled"},
                "secret_scanning_push_protection": {"status": "enabled"},
            },
        },
    )
    monkeypatch.setattr(
        create_ops,
        "_list_repo_rulesets",
        lambda **_: [
            {
                "id": 123,
                "name": "repo-scaffold baseline branch rules",
                "target": "branch",
                "enforcement": "active",
            }
        ],
    )
    monkeypatch.setattr(create_ops, "_get_repo_ruleset", lambda **_: detailed_ruleset)
    monkeypatch.setattr(
        create_ops, "_legacy_branch_protection_exists", lambda **_: False
    )
    monkeypatch.setattr(
        create_ops, "_optional_endpoint_feature_enabled", lambda **_: (True, "enabled")
    )

    summary = create_ops._check_settings(
        repo_dir=Path("/tmp/repo"),
        env={},
        repo="acme/repo",
        out=lines.append,
    )

    assert summary.failed == 0
    assert summary.passed == 8
    assert "PASS  managed default-branch ruleset" in lines


def test_check_settings_reports_drift_and_skips_private_repo_only_feature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines: list[str] = []

    monkeypatch.setattr(
        create_ops,
        "_get_repo_info",
        lambda **_: {
            "allow_squash_merge": True,
            "allow_merge_commit": True,
            "allow_rebase_merge": False,
            "delete_branch_on_merge": False,
            "allow_auto_merge": False,
            "is_template": False,
            "default_branch": "main",
            "visibility": "private",
            "security_and_analysis": {
                "secret_scanning": {"status": "disabled"},
            },
        },
    )
    monkeypatch.setattr(create_ops, "_list_repo_rulesets", lambda **_: [])
    monkeypatch.setattr(
        create_ops, "_legacy_branch_protection_exists", lambda **_: True
    )
    monkeypatch.setattr(
        create_ops,
        "_optional_endpoint_feature_enabled",
        lambda **_: (False, "HTTP 404"),
    )

    summary = create_ops._check_settings(
        repo_dir=Path("/tmp/repo"),
        env={},
        repo="acme/repo",
        out=lines.append,
    )

    assert summary.failed == 7
    assert summary.passed == 0
    assert summary.skipped == 1
    assert any("merge settings" in drift for drift in summary.drifts)
    assert any("managed default-branch ruleset" in drift for drift in summary.drifts)
    assert "SKIP  private vulnerability reporting (repo is not public)" in lines


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


def test_push_main_formats_noninteractive_auth_failure_without_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(create_ops, "_resolve_push_token", lambda **_: None)
    monkeypatch.setattr(
        create_ops,
        "_run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=["git"],
            returncode=1,
            stdout="",
            stderr="could not read Username for 'https://github.com'",
        ),
    )

    pushed, error = create_ops._push_main(repo_dir=Path("/tmp/repo"), env={})

    assert pushed is False
    assert error is not None
    assert "Non-interactive auth failed" in error


def test_apply_and_check_repository_settings_wrappers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    events: list[str] = []

    monkeypatch.setattr(create_ops, "_ensure_tools", lambda: events.append("tools"))
    monkeypatch.setattr(create_ops, "_build_env", lambda _repo_dir: {"GH_TOKEN": "x"})
    monkeypatch.setattr(
        create_ops,
        "_ensure_gh_auth",
        lambda _repo_dir, _env: events.append("auth"),
    )
    monkeypatch.setattr(
        create_ops,
        "_apply_settings",
        lambda **kwargs: events.append(
            f"apply:{kwargs['repo']}:{kwargs['dry_run']}:{kwargs['repo_dir']}"
        ),
    )
    expected_summary = create_ops.SettingsCheckSummary(
        repo="acme/repo", passed=1, failed=0, skipped=0, drifts=()
    )
    monkeypatch.setattr(create_ops, "_check_settings", lambda **_: expected_summary)

    create_ops.apply_repository_settings(
        repo_dir=repo_dir,
        repo="acme/repo",
        dry_run=True,
        out=lambda _line: None,
    )
    create_ops.apply_repository_settings(
        repo_dir=repo_dir,
        repo="acme/repo",
        dry_run=False,
        out=lambda _line: None,
    )
    summary = create_ops.check_repository_settings(
        repo_dir=repo_dir,
        repo="acme/repo",
        out=lambda _line: None,
    )

    assert summary == expected_summary
    assert events.count("tools") == 3
    assert events.count("auth") == 2
    assert any(
        item.startswith(f"apply:acme/repo:True:{repo_dir.resolve()}") for item in events
    )
    assert any(
        item.startswith(f"apply:acme/repo:False:{repo_dir.resolve()}")
        for item in events
    )


def test_apply_settings_covers_dry_run_and_patch_failure_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines: list[str] = []
    create_ops._apply_settings(
        repo_dir=Path("/tmp/repo"),
        env={},
        repo="acme/repo",
        dry_run=True,
        out=lines.append,
        warn=lambda _line: None,
    )

    assert "[dry-run] sync repository merge settings" in lines
    assert any("private vulnerability reporting" in line for line in lines)

    monkeypatch.setattr(
        create_ops,
        "_get_repo_info",
        lambda **_: {"default_branch": "main", "visibility": "public"},
    )
    monkeypatch.setattr(
        create_ops,
        "_api",
        lambda **_: subprocess.CompletedProcess(
            args=["gh"], returncode=1, stdout="", stderr="boom"
        ),
    )

    with pytest.raises(RuntimeError, match="boom"):
        create_ops._apply_settings(
            repo_dir=Path("/tmp/repo"),
            env={},
            repo="acme/repo",
            dry_run=False,
            out=lambda _line: None,
            warn=lambda _line: None,
        )


def test_create_repository_covers_success_skip_and_error_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()

    monkeypatch.setattr(create_ops, "_ensure_tools", lambda: None)
    monkeypatch.setattr(create_ops, "_build_env", lambda _repo_dir: {"GH_TOKEN": "x"})
    monkeypatch.setattr(create_ops, "_ensure_gh_auth", lambda _repo_dir, _env: None)
    monkeypatch.setattr(create_ops, "_resolve_repo", lambda **_: "acme/repo")
    monkeypatch.setattr(create_ops, "_ensure_git_repo", lambda **_: None)

    applied: list[str] = []
    monkeypatch.setattr(
        create_ops,
        "_apply_settings",
        lambda **kwargs: applied.append(kwargs["repo"]),
    )
    monkeypatch.setattr(
        create_ops,
        "_create_or_push_repo",
        lambda **_: (True, True, None),
    )

    summary = create_ops.create_repository(
        repo_dir=repo_dir,
        repo=None,
        owner=None,
        name=None,
        visibility="public",
        apply_settings=True,
        dry_run=False,
        out=lambda _line: None,
        err=lambda _line: None,
    )

    assert summary.repo == "acme/repo"
    assert summary.repo_created is True
    assert summary.pushed is True
    assert summary.settings_applied is True
    assert summary.failures == 0
    assert applied == ["acme/repo"]

    skipped_lines: list[str] = []
    monkeypatch.setattr(
        create_ops,
        "_create_or_push_repo",
        lambda **_: (False, False, "push failed"),
    )
    errors: list[str] = []
    skipped = create_ops.create_repository(
        repo_dir=repo_dir,
        repo=None,
        owner=None,
        name=None,
        visibility="public",
        apply_settings=True,
        dry_run=False,
        out=skipped_lines.append,
        err=errors.append,
    )

    assert skipped.pushed is False
    assert skipped.settings_applied is False
    assert skipped.failures == 1
    assert errors == ["push failed"]
    assert "Skipping settings apply until main branch is pushed." in skipped_lines

    monkeypatch.setattr(
        create_ops,
        "_ensure_git_repo",
        lambda **_: (_ for _ in ()).throw(RuntimeError("bad repo")),
    )
    errors.clear()
    failed = create_ops.create_repository(
        repo_dir=repo_dir,
        repo=None,
        owner=None,
        name=None,
        visibility="public",
        apply_settings=False,
        dry_run=False,
        out=lambda _line: None,
        err=errors.append,
    )

    assert failed.failures == 1
    assert errors == ["bad repo"]


def test_create_repository_rejects_invalid_visibility(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()

    with pytest.raises(RuntimeError, match="Visibility must be one of"):
        create_ops.create_repository(
            repo_dir=repo_dir,
            repo="acme/repo",
            owner=None,
            name=None,
            visibility="friends-only",
            apply_settings=False,
            dry_run=True,
            out=lambda _line: None,
            err=lambda _line: None,
        )
