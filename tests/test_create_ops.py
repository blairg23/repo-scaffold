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


def test_build_env_promotes_project_token_when_gh_token_is_placeholder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cwd = tmp_path / "cwd"
    repo_dir = tmp_path / "repo"
    cwd.mkdir()
    repo_dir.mkdir()
    (repo_dir / ".env").write_text(
        "\n".join(
            [
                "GH_TOKEN=ghp_replace_with_real_token",
                "export GH_PROJECT_TOKEN=ghp_project_real_token",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(cwd)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_PROJECT_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_PROJECT_TOKEN", raising=False)

    env = create_ops._build_env(repo_dir)

    assert env["GH_TOKEN"] == "ghp_project_real_token"


def test_load_json_invalid_raises_runtime_error() -> None:
    with pytest.raises(RuntimeError, match="bad json"):
        create_ops._load_json("not-json", error_message="bad json")


def test_default_branch_ruleset_payload_uses_zero_review_baseline() -> None:
    payload = json.loads(create_ops._default_branch_ruleset_payload())
    assert payload["name"] == "default-branch (managed by repo-scaffold)"
    assert payload["conditions"]["ref_name"]["include"] == ["~DEFAULT_BRANCH"]
    rule_types = [r["type"] for r in payload["rules"]]
    assert "creation" in rule_types
    assert "update" not in rule_types
    assert "deletion" in rule_types
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
            "alerts_threshold": "errors_and_warnings",
            "security_alerts_threshold": "high_or_higher",
        }
    ]
    code_quality_rule = next(
        rule for rule in payload["rules"] if rule["type"] == "code_quality"
    )
    assert code_quality_rule["parameters"]["code_quality_tools"] == [
        {"tool": "CodeQL", "severity": "notes"}
    ]
    assert copilot_code_review_rule["parameters"]["review_draft_pull_requests"] is True
    assert copilot_code_review_rule["parameters"]["review_on_push"] is True


def test_default_branch_ruleset_payload_includes_required_status_checks_with_languages() -> (
    None
):
    payload = json.loads(
        create_ops._default_branch_ruleset_payload(languages=["python"])
    )
    rule = next(
        (r for r in payload["rules"] if r["type"] == "required_status_checks"), None
    )
    assert rule is not None
    contexts = [c["context"] for c in rule["parameters"]["required_status_checks"]]
    assert "check-sop" in contexts
    assert "validate-pr" in contexts
    assert rule["parameters"]["strict_required_status_checks_policy"] is False


def test_default_branch_ruleset_payload_keeps_baseline_contexts_with_no_languages() -> (
    None
):
    """check-sop and validate-pr come from validate-pr.yml/validate-pr-sop.yml,
    which repo-scaffold's own `init` generates unconditionally regardless of
    language -- so an unknown/empty language set must still require those
    baseline contexts. Only the language-specific CI contexts are optional."""
    payload = json.loads(create_ops._default_branch_ruleset_payload(languages=None))
    rule_types = [r["type"] for r in payload["rules"]]
    assert "required_status_checks" in rule_types

    status_rule = next(
        r for r in payload["rules"] if r["type"] == "required_status_checks"
    )
    contexts = [
        c["context"] for c in status_rule["parameters"]["required_status_checks"]
    ]
    assert contexts == list(create_ops._ALWAYS_REQUIRED_CONTEXTS)

    payload_empty_list = json.loads(
        create_ops._default_branch_ruleset_payload(languages=[])
    )
    assert "required_status_checks" in [r["type"] for r in payload_empty_list["rules"]]


def test_default_branch_ruleset_payload_with_react_adds_required_status_checks() -> (
    None
):
    payload = json.loads(
        create_ops._default_branch_ruleset_payload(languages=["react"])
    )
    rule = next(
        (r for r in payload["rules"] if r["type"] == "required_status_checks"), None
    )
    assert rule is not None
    contexts = [c["context"] for c in rule["parameters"]["required_status_checks"]]
    assert contexts == ["check-sop", "validate-pr", "react"]
    assert rule["parameters"]["strict_required_status_checks_policy"] is False


def test_default_branch_ruleset_payload_deduplicates_go_gin_context() -> None:
    payload = json.loads(
        create_ops._default_branch_ruleset_payload(languages=["go", "gin"])
    )
    rule = next(r for r in payload["rules"] if r["type"] == "required_status_checks")
    contexts = [c["context"] for c in rule["parameters"]["required_status_checks"]]
    assert contexts == ["check-sop", "validate-pr", "go"]


def test_default_branch_ruleset_payload_python_expands_to_matrix_contexts() -> None:
    """Python's CI job is a lint/type/coverage matrix, so the required check
    must be each matrixed check-run name -- a bare "python" context can never
    be satisfied by any actual check GitHub reports."""
    payload = json.loads(
        create_ops._default_branch_ruleset_payload(languages=["python"])
    )
    rule = next(r for r in payload["rules"] if r["type"] == "required_status_checks")
    contexts = [c["context"] for c in rule["parameters"]["required_status_checks"]]
    assert contexts == [
        "check-sop",
        "validate-pr",
        "python (lint)",
        "python (type)",
        "python (coverage)",
    ]
    assert "python" not in contexts


def test_apply_repository_settings_forwards_languages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    captured: dict[str, object] = {}

    monkeypatch.setattr(create_ops, "_ensure_tools", lambda: None)
    monkeypatch.setattr(create_ops, "_build_env", lambda _repo_dir: {"GH_TOKEN": "x"})
    monkeypatch.setattr(create_ops, "_ensure_gh_auth", lambda _repo_dir, _env: None)
    monkeypatch.setattr(
        create_ops,
        "_apply_settings",
        lambda **kwargs: captured.update(kwargs),
    )

    create_ops.apply_repository_settings(
        repo_dir=repo_dir,
        repo="acme/repo",
        dry_run=False,
        languages=["react", "python"],
    )

    assert captured["languages"] == ["react", "python"]


def test_sync_repository_ruleset_calls_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    captured: dict[str, object] = {}

    monkeypatch.setattr(create_ops, "_ensure_tools", lambda: None)
    monkeypatch.setattr(create_ops, "_build_env", lambda _repo_dir: {"GH_TOKEN": "x"})
    monkeypatch.setattr(create_ops, "_ensure_gh_auth", lambda _repo_dir, _env: None)
    monkeypatch.setattr(
        create_ops,
        "_sync_default_branch_ruleset",
        lambda **kwargs: captured.update(kwargs),
    )

    create_ops.sync_repository_ruleset(
        repo_dir=repo_dir,
        repo="acme/repo",
        languages=["python"],
    )

    assert captured["repo"] == "acme/repo"
    assert captured["languages"] == ["python"]


def test_branch_protection_endpoint_url_encodes_branch_name() -> None:
    assert (
        create_ops._branch_protection_endpoint(
            repo="acme/repo",
            branch="release/2026",
        )
        == "/repos/acme/repo/branches/release%2F2026/protection"
    )


def test_is_managed_ruleset_name_accepts_new_and_legacy_names() -> None:
    assert create_ops._is_managed_ruleset_name(
        "default-branch (managed by repo-scaffold)"
    )
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
                            "review_draft_pull_requests": False,
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
        languages=["python"],
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
    assert "missing rule: code_quality" in drifts
    assert "missing rule: required_status_checks" in drifts
    assert (
        "copilot_code_review.review_draft_pull_requests expected True got False"
        in drifts
    )
    assert "copilot_code_review.review_on_push expected True got False" in drifts


def _clean_baseline_ruleset(
    extra_rules: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    rules: list[dict[str, object]] = [
        {"type": "deletion"},
        {"type": "non_fast_forward"},
        {"type": "required_linear_history"},
        {
            "type": "pull_request",
            "parameters": {
                "allowed_merge_methods": ["squash"],
                "dismiss_stale_reviews_on_push": False,
                "require_code_owner_review": False,
                "require_last_push_approval": False,
                "required_approving_review_count": 0,
                "required_review_thread_resolution": True,
            },
        },
        {
            "type": "code_scanning",
            "parameters": {
                "code_scanning_tools": [
                    {
                        "tool": "CodeQL",
                        "alerts_threshold": "errors_and_warnings",
                        "security_alerts_threshold": "high_or_higher",
                    }
                ]
            },
        },
        {
            "type": "code_quality",
            "parameters": {
                "code_quality_tools": [{"tool": "CodeQL", "severity": "notes"}]
            },
        },
        {
            "type": "copilot_code_review",
            "parameters": {"review_draft_pull_requests": True, "review_on_push": True},
        },
    ]
    if extra_rules:
        rules.extend(extra_rules)
    return {
        "name": create_ops._SETTINGS_RULESET_NAME,
        "target": "branch",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
        "rules": rules,
    }


def test_compare_ruleset_missing_required_status_checks_reports_drift() -> None:
    drifts = create_ops._compare_ruleset_against_baseline(
        [_clean_baseline_ruleset()],
        default_branch="main",
        languages=["python"],
    )
    assert "missing rule: required_status_checks" in drifts


def test_compare_ruleset_no_languages_still_requires_baseline_contexts() -> None:
    """check-sop and validate-pr run unconditionally (validate-pr.yml and
    validate-pr-sop.yml are generated regardless of language), so a ruleset
    missing required_status_checks is real drift even with no languages
    detected -- only the language-specific contexts are optional."""
    drifts = create_ops._compare_ruleset_against_baseline(
        [_clean_baseline_ruleset()],
        default_branch="main",
        languages=None,
    )
    assert "missing rule: required_status_checks" in drifts


def test_compare_ruleset_no_languages_satisfied_by_baseline_contexts_only() -> None:
    """With no languages detected, a ruleset that already has the baseline
    check-sop/validate-pr contexts (and nothing language-specific) is clean --
    no drift."""
    ruleset = _clean_baseline_ruleset(
        extra_rules=[
            {
                "type": "required_status_checks",
                "parameters": {
                    "required_status_checks": [
                        {"context": ctx} for ctx in create_ops._ALWAYS_REQUIRED_CONTEXTS
                    ],
                    "strict_required_status_checks_policy": False,
                },
            }
        ]
    )
    drifts = create_ops._compare_ruleset_against_baseline(
        [ruleset], default_branch="main", languages=None
    )
    assert not any("required_status_checks" in d for d in drifts)


def test_compare_ruleset_required_status_checks_missing_always_required_contexts() -> (
    None
):
    ruleset = _clean_baseline_ruleset(
        extra_rules=[
            {
                "type": "required_status_checks",
                "parameters": {
                    "required_status_checks": [{"context": "python"}],
                    "strict_required_status_checks_policy": False,
                },
            }
        ]
    )
    drifts = create_ops._compare_ruleset_against_baseline(
        [ruleset], default_branch="main", languages=["python"]
    )
    assert any("required_status_checks missing contexts" in d for d in drifts)
    missing_drift = next(
        d for d in drifts if "required_status_checks missing contexts" in d
    )
    assert "check-sop" in missing_drift
    assert "validate-pr" in missing_drift


def test_compare_ruleset_required_status_checks_all_contexts_present_no_drift() -> None:
    ruleset = _clean_baseline_ruleset(
        extra_rules=[
            {
                "type": "required_status_checks",
                "parameters": {
                    "required_status_checks": [
                        {"context": "check-sop"},
                        {"context": "validate-pr"},
                    ],
                    "strict_required_status_checks_policy": False,
                },
            }
        ]
    )
    drifts = create_ops._compare_ruleset_against_baseline(
        [ruleset], default_branch="main", languages=["unrecognized-lang"]
    )
    assert not any("required_status_checks" in d for d in drifts)


def test_compare_ruleset_always_required_plus_language_contexts() -> None:
    # Python's CI job is a lint/type/coverage matrix, so GitHub reports three
    # separate check-run names -- a bare "python" context never appears and
    # can never be satisfied. All three matrix leg contexts must be present.
    ruleset = _clean_baseline_ruleset(
        extra_rules=[
            {
                "type": "required_status_checks",
                "parameters": {
                    "required_status_checks": [
                        {"context": "check-sop"},
                        {"context": "validate-pr"},
                        {"context": "python (lint)"},
                        {"context": "python (type)"},
                        {"context": "python (coverage)"},
                    ],
                    "strict_required_status_checks_policy": False,
                },
            }
        ]
    )
    drifts = create_ops._compare_ruleset_against_baseline(
        [ruleset], default_branch="main", languages=["python"]
    )
    assert not any("required_status_checks" in d for d in drifts)


def test_compare_ruleset_bare_python_context_is_insufficient() -> None:
    """A bare "python" required context can never be satisfied by the matrixed
    CI job, so it should still be reported as drift even when present."""
    ruleset = _clean_baseline_ruleset(
        extra_rules=[
            {
                "type": "required_status_checks",
                "parameters": {
                    "required_status_checks": [
                        {"context": "check-sop"},
                        {"context": "validate-pr"},
                        {"context": "python"},
                    ],
                    "strict_required_status_checks_policy": False,
                },
            }
        ]
    )
    drifts = create_ops._compare_ruleset_against_baseline(
        [ruleset], default_branch="main", languages=["python"]
    )
    missing_drift = next(
        d for d in drifts if "required_status_checks missing contexts" in d
    )
    assert "python (lint)" in missing_drift
    assert "python (type)" in missing_drift
    assert "python (coverage)" in missing_drift


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


def test_enable_code_scanning_default_setup_success_and_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out_lines: list[str] = []
    warn_lines: list[str] = []
    endpoints: list[str] = []
    payloads: list[str | None] = []

    api_responses = iter(
        [
            subprocess.CompletedProcess(
                args=["gh"], returncode=0, stdout="", stderr=""
            ),
            subprocess.CompletedProcess(
                args=["gh"], returncode=1, stdout="", stderr="not enabled"
            ),
        ]
    )

    monkeypatch.setattr(
        create_ops,
        "_api",
        lambda **kwargs: (
            endpoints.append(kwargs["endpoint"])
            or payloads.append(kwargs.get("stdin_text"))
            or next(api_responses)
        ),
    )

    create_ops._enable_code_scanning_default_setup(
        repo_dir=Path("/tmp/repo"),
        env={},
        repo="acme/repo",
        out=out_lines.append,
        warn=warn_lines.append,
    )
    create_ops._enable_code_scanning_default_setup(
        repo_dir=Path("/tmp/repo"),
        env={},
        repo="acme/repo",
        out=out_lines.append,
        warn=warn_lines.append,
    )

    assert endpoints == [
        "/repos/acme/repo/code-scanning/default-setup",
        "/repos/acme/repo/code-scanning/default-setup",
    ]
    assert all(p is not None and '"state"' in p for p in payloads)
    assert any(
        "enabled code scanning default setup" in line.lower() for line in out_lines
    )
    assert any(
        "could not enable code scanning default setup" in line.lower()
        for line in warn_lines
    )


def test_enable_code_scanning_default_setup_includes_mapped_languages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payloads: list[str | None] = []

    monkeypatch.setattr(
        create_ops,
        "_api",
        lambda **kwargs: (
            payloads.append(kwargs.get("stdin_text"))
            or subprocess.CompletedProcess(
                args=["gh"], returncode=0, stdout="", stderr=""
            )
        ),
    )

    create_ops._enable_code_scanning_default_setup(
        repo_dir=Path("/tmp/repo"),
        env={},
        repo="acme/repo",
        out=lambda _line: None,
        warn=lambda _line: None,
        languages=["gin", "react", "python"],
    )

    assert payloads[0] is not None
    payload = json.loads(payloads[0])
    assert payload["state"] == "configured"
    assert payload["query_suite"] == "extended"
    assert sorted(payload["languages"]) == ["go", "javascript-typescript", "python"]


def test_enable_code_scanning_default_setup_no_languages_matches_old_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No known languages -> unchanged from the pre-existing behavior, since
    GitHub's own auto-detection is the only sensible fallback when we don't
    know the repo's language stack."""
    payloads: list[str | None] = []

    monkeypatch.setattr(
        create_ops,
        "_api",
        lambda **kwargs: (
            payloads.append(kwargs.get("stdin_text"))
            or subprocess.CompletedProcess(
                args=["gh"], returncode=0, stdout="", stderr=""
            )
        ),
    )

    create_ops._enable_code_scanning_default_setup(
        repo_dir=Path("/tmp/repo"),
        env={},
        repo="acme/repo",
        out=lambda _line: None,
        warn=lambda _line: None,
        languages=None,
    )

    assert payloads[0] is not None
    assert json.loads(payloads[0]) == {"state": "configured"}


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


def test_default_branch_ruleset_payload_excludes_code_quality_when_flagged() -> None:
    payload = json.loads(
        create_ops._default_branch_ruleset_payload(include_code_quality=False)
    )
    types = [r["type"] for r in payload["rules"]]
    assert "code_quality" not in types
    assert "code_scanning" in types
    assert "copilot_code_review" in types


def test_sync_ruleset_falls_back_when_code_quality_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines: list[str] = []
    warnings: list[str] = []
    payloads: list[str | None] = []

    api_responses = iter(
        [
            subprocess.CompletedProcess(
                args=["gh"],
                returncode=1,
                stdout="",
                stderr="code_quality not supported",
            ),
            subprocess.CompletedProcess(
                args=["gh"], returncode=0, stdout="", stderr=""
            ),
        ]
    )

    monkeypatch.setattr(create_ops, "_list_repo_rulesets", lambda **_: [])
    monkeypatch.setattr(
        create_ops,
        "_api",
        lambda **kwargs: (
            payloads.append(kwargs.get("stdin_text")) or next(api_responses)
        ),
    )

    create_ops._sync_default_branch_ruleset(
        repo_dir=Path("/tmp/repo"),
        env={},
        repo="acme/repo",
        out=lines.append,
        warn=warnings.append,
    )

    assert any("Created ruleset" in line for line in lines)
    assert any("code_quality" in w.lower() for w in warnings)
    assert len(payloads) == 2
    first = json.loads(payloads[0])
    second = json.loads(payloads[1])
    assert any(r["type"] == "code_quality" for r in first["rules"])
    assert all(r["type"] != "code_quality" for r in second["rules"])


def test_compare_ruleset_code_quality_tools_drift() -> None:
    """code_quality rule present but tools mismatch → drift reported."""
    drifts = create_ops._compare_ruleset_against_baseline(
        [
            {
                "name": create_ops._SETTINGS_RULESET_NAME,
                "target": "branch",
                "enforcement": "active",
                "conditions": {
                    "ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}
                },
                "rules": [
                    {"type": "deletion"},
                    {"type": "non_fast_forward"},
                    {"type": "required_linear_history"},
                    {
                        "type": "pull_request",
                        "parameters": {
                            "allowed_merge_methods": ["squash"],
                            "dismiss_stale_reviews_on_push": False,
                            "require_code_owner_review": False,
                            "require_last_push_approval": False,
                            "required_approving_review_count": 0,
                            "required_review_thread_resolution": True,
                        },
                    },
                    {
                        "type": "code_scanning",
                        "parameters": {
                            "code_scanning_tools": [
                                {
                                    "tool": "CodeQL",
                                    "alerts_threshold": "errors_and_warnings",
                                    "security_alerts_threshold": "high_or_higher",
                                }
                            ]
                        },
                    },
                    {
                        "type": "code_quality",
                        "parameters": {
                            "code_quality_tools": [
                                {"tool": "CodeQL", "severity": "errors"}
                            ]
                        },
                    },
                    {
                        "type": "copilot_code_review",
                        "parameters": {
                            "review_draft_pull_requests": True,
                            "review_on_push": True,
                        },
                    },
                ],
            }
        ],
        default_branch="main",
    )
    assert any("code_quality.code_quality_tools" in d for d in drifts)
    assert not any("missing rule: code_quality" in d for d in drifts)


def test_compare_ruleset_code_quality_parameters_invalid() -> None:
    """code_quality rule present but parameters is not a dict → drift reported."""
    drifts = create_ops._compare_ruleset_against_baseline(
        [
            {
                "name": create_ops._SETTINGS_RULESET_NAME,
                "target": "branch",
                "enforcement": "active",
                "conditions": {
                    "ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}
                },
                "rules": [
                    {"type": "deletion"},
                    {"type": "non_fast_forward"},
                    {"type": "required_linear_history"},
                    {
                        "type": "pull_request",
                        "parameters": {
                            "allowed_merge_methods": ["squash"],
                            "dismiss_stale_reviews_on_push": False,
                            "require_code_owner_review": False,
                            "require_last_push_approval": False,
                            "required_approving_review_count": 0,
                            "required_review_thread_resolution": True,
                        },
                    },
                    {
                        "type": "code_scanning",
                        "parameters": {
                            "code_scanning_tools": [
                                {
                                    "tool": "CodeQL",
                                    "alerts_threshold": "errors_and_warnings",
                                    "security_alerts_threshold": "high_or_higher",
                                }
                            ]
                        },
                    },
                    {"type": "code_quality", "parameters": None},
                    {
                        "type": "copilot_code_review",
                        "parameters": {
                            "review_draft_pull_requests": True,
                            "review_on_push": True,
                        },
                    },
                ],
            }
        ],
        default_branch="main",
    )
    assert any("code_quality rule parameters missing or invalid" in d for d in drifts)


def test_sync_ruleset_falls_back_on_put_when_code_quality_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warnings: list[str] = []
    lines: list[str] = []
    payloads: list[str | None] = []

    api_responses = iter(
        [
            subprocess.CompletedProcess(
                args=["gh"],
                returncode=1,
                stdout="",
                stderr="code_quality not supported",
            ),
            subprocess.CompletedProcess(
                args=["gh"], returncode=0, stdout="", stderr=""
            ),
        ]
    )

    monkeypatch.setattr(
        create_ops,
        "_list_repo_rulesets",
        lambda **_: [{"id": 42, "name": create_ops._SETTINGS_RULESET_NAME}],
    )
    monkeypatch.setattr(
        create_ops,
        "_api",
        lambda **kwargs: (
            payloads.append(kwargs.get("stdin_text")) or next(api_responses)
        ),
    )

    create_ops._sync_default_branch_ruleset(
        repo_dir=Path("/tmp/repo"),
        env={},
        repo="acme/repo",
        out=lines.append,
        warn=warnings.append,
    )

    assert any("Updated ruleset" in line for line in lines)
    assert any("code_quality" in w.lower() for w in warnings)
    assert len(payloads) == 2
    first = json.loads(payloads[0])
    second = json.loads(payloads[1])
    assert any(r["type"] == "code_quality" for r in first["rules"])
    assert all(r["type"] != "code_quality" for r in second["rules"])


def test_sync_ruleset_put_fallback_failure_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_responses = iter(
        [
            subprocess.CompletedProcess(
                args=["gh"],
                returncode=1,
                stdout="",
                stderr="code_quality not supported",
            ),
            subprocess.CompletedProcess(
                args=["gh"], returncode=1, stdout="", stderr="internal server error"
            ),
        ]
    )

    monkeypatch.setattr(
        create_ops,
        "_list_repo_rulesets",
        lambda **_: [{"id": 42, "name": create_ops._SETTINGS_RULESET_NAME}],
    )
    monkeypatch.setattr(create_ops, "_api", lambda **_: next(api_responses))

    with pytest.raises(RuntimeError, match="internal server error"):
        create_ops._sync_default_branch_ruleset(
            repo_dir=Path("/tmp/repo"),
            env={},
            repo="acme/repo",
            out=lambda _: None,
            warn=lambda _: None,
        )


def test_sync_ruleset_post_fallback_failure_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_responses = iter(
        [
            subprocess.CompletedProcess(
                args=["gh"],
                returncode=1,
                stdout="",
                stderr="code_quality not supported",
            ),
            subprocess.CompletedProcess(
                args=["gh"], returncode=1, stdout="", stderr="permission denied"
            ),
        ]
    )

    monkeypatch.setattr(create_ops, "_list_repo_rulesets", lambda **_: [])
    monkeypatch.setattr(create_ops, "_api", lambda **_: next(api_responses))

    with pytest.raises(RuntimeError, match="permission denied"):
        create_ops._sync_default_branch_ruleset(
            repo_dir=Path("/tmp/repo"),
            env={},
            repo="acme/repo",
            out=lambda _: None,
            warn=lambda _: None,
        )


def test_apply_payload_raises_for_non_code_quality_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(create_ops, "_list_repo_rulesets", lambda **_: [])
    monkeypatch.setattr(
        create_ops,
        "_api",
        lambda **_: subprocess.CompletedProcess(
            args=["gh"], returncode=1, stdout="", stderr="unrelated API error"
        ),
    )
    with pytest.raises(RuntimeError, match="unrelated API error"):
        create_ops._sync_default_branch_ruleset(
            repo_dir=Path("/tmp/repo"),
            env={},
            repo="acme/repo",
            out=lambda _: None,
            warn=lambda _: None,
        )


def test_apply_payload_falls_back_on_github_invalid_property_rules_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GitHub returns 'Invalid property /rules/N' rather than 'code_quality' when
    the rule type is unsupported -- the fallback must trigger on that pattern too."""
    lines: list[str] = []
    warnings: list[str] = []

    api_responses = iter(
        [
            subprocess.CompletedProcess(
                args=["gh"],
                returncode=1,
                stdout="",
                stderr="Invalid property /rules/5: data matches no possible input.",
            ),
            subprocess.CompletedProcess(
                args=["gh"], returncode=0, stdout="", stderr=""
            ),
        ]
    )

    monkeypatch.setattr(create_ops, "_list_repo_rulesets", lambda **_: [])
    monkeypatch.setattr(create_ops, "_api", lambda **_: next(api_responses))

    create_ops._sync_default_branch_ruleset(
        repo_dir=Path("/tmp/repo"),
        env={},
        repo="acme/repo",
        out=lines.append,
        warn=warnings.append,
    )

    assert any("Created ruleset" in line for line in lines)
    assert any("code_quality" in w.lower() for w in warnings)


def test_tool_auth_remote_and_push_helpers_cover_common_error_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()

    monkeypatch.setattr(create_ops.shutil, "which", lambda tool: None)
    with pytest.raises(RuntimeError, match="git is required"):
        create_ops._ensure_tools()

    monkeypatch.setattr(create_ops.shutil, "which", lambda _tool: "/usr/bin/git")
    create_ops._ensure_tools()

    import repo_scaffold.github_api as github_api_module

    monkeypatch.setattr(github_api_module, "token_from_repo", lambda _repo_dir: None)
    monkeypatch.setattr(create_ops, "_token_from_repo", lambda _repo_dir: None)
    with pytest.raises(RuntimeError, match="Authenticate first"):
        create_ops._ensure_gh_auth(repo_dir, {})

    monkeypatch.setattr(
        github_api_module,
        "_http",
        lambda method, url, token, data=None: subprocess.CompletedProcess(
            args=[], returncode=0, stdout='{"nameWithOwner":"acme/repo"}', stderr=""
        ),
    )
    assert create_ops._repo_exists(repo_dir=repo_dir, env={}, repo="acme/repo") is True

    monkeypatch.setattr(
        github_api_module,
        "_http",
        lambda method, url, token, data=None: subprocess.CompletedProcess(
            args=[], returncode=404, stdout="", stderr="not found"
        ),
    )
    assert create_ops._repo_exists(repo_dir=repo_dir, env={}, repo="acme/repo") is False

    monkeypatch.setattr(
        github_api_module,
        "_http",
        lambda method, url, token, data=None: subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="boom"
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

    assert (
        create_ops._resolve_push_token(repo_dir=repo_dir, env={"GH_TOKEN": "gh-token"})
        == "gh-token"
    )
    monkeypatch.setattr(create_ops, "_token_from_repo", lambda _: None)
    assert create_ops._resolve_push_token(repo_dir=repo_dir, env={}) is None
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
        "_github_repo_create",
        lambda owner, name, token, visibility="private": subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="create failed"
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
    assert f"[dry-run] create {create_ops._DEPENDABOT_YML_PATH} if not present" in lines


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
    monkeypatch.setattr(
        create_ops,
        "_enable_code_scanning_default_setup",
        lambda **_: None,
    )
    monkeypatch.setattr(
        create_ops,
        "_ensure_dependabot_version_updates",
        lambda **_: None,
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
    assert payload["squash_merge_commit_title"] == "PR_TITLE"
    assert payload["squash_merge_commit_message"] == "PR_BODY"
    assert payload["delete_branch_on_merge"] is True
    assert payload["allow_auto_merge"] is False
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
    assert summary.passed == 9
    assert summary.failed == 0
    assert summary.skipped == 0
    assert summary.drifts == ()
    assert "check repository settings: acme/repo" in lines
    assert "PASS  managed default-branch ruleset" in lines
    assert "PASS  dependabot version updates" in lines


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
    assert summary.passed == 9
    assert "PASS  managed default-branch ruleset" in lines
    assert "PASS  dependabot version updates" in lines


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

    assert summary.failed == 8
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

    create_calls: list[tuple[str, str, str, str]] = []

    def _fake_repo_exists(*, repo_dir: Path, env: dict[str, str], repo: str) -> bool:
        assert repo_dir == repo_dir.resolve()
        return False

    def _fake_repo_create(
        owner: str, name: str, token: str, visibility: str = "private"
    ) -> subprocess.CompletedProcess[str]:
        create_calls.append((owner, name, token, visibility))
        return subprocess.CompletedProcess(
            args=[], returncode=201, stdout="{}", stderr=""
        )

    def _fake_push_main(
        *, repo_dir: Path, env: dict[str, str]
    ) -> tuple[bool, str | None]:
        return True, None

    monkeypatch.setattr(create_ops, "_repo_exists", _fake_repo_exists)
    monkeypatch.setattr(create_ops, "_github_repo_create", _fake_repo_create)
    monkeypatch.setattr(create_ops, "_ensure_origin_remote", lambda **_: None)
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
    assert create_calls, "Expected repo create call"
    owner, name, _, visibility = create_calls[0]
    assert owner == "acme"
    assert name == "example"
    assert visibility == "public"


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
        if args == ["git", "commit", "--allow-empty", "-m", "Initial scaffold"]:
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
        stage_files=False,
    )

    assert ["git", "init"] in calls
    assert ["git", "add", "-A"] not in calls
    assert ["git", "commit", "--allow-empty", "-m", "Initial scaffold"] in calls
    assert ["git", "branch", "-M", "main"] not in calls


def test_ensure_git_repo_stages_files_when_scaffold_generated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "new-repo"
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
            return subprocess.CompletedProcess(
                args=args, returncode=1, stdout="", stderr="not a git repo"
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
        stage_files=True,
    )

    assert ["git", "init"] in calls
    assert ["git", "add", "-A"] in calls
    assert ["git", "commit", "-m", "Initial scaffold"] in calls
    assert ["git", "commit", "--allow-empty", "-m", "Initial scaffold"] not in calls


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

    applied_languages: list[list[str] | None] = []
    monkeypatch.setattr(
        create_ops,
        "_apply_settings",
        lambda **kwargs: applied_languages.append(kwargs.get("languages")),
    )
    create_ops.create_repository(
        repo_dir=repo_dir,
        repo=None,
        owner=None,
        name=None,
        visibility="public",
        apply_settings=True,
        dry_run=False,
        languages=["python"],
        out=lambda _line: None,
        err=lambda _line: None,
    )
    assert applied_languages == [["python"]]

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


def test_create_repository_rejects_invalid_visibility(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()

    monkeypatch.setattr(create_ops, "_ensure_tools", lambda: None)
    monkeypatch.setattr(create_ops, "_build_env", lambda _: {"GH_TOKEN": "x"})
    monkeypatch.setattr(create_ops, "_ensure_gh_auth", lambda _d, _e: None)

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


def test_minimal_dependabot_yml_always_includes_github_actions() -> None:
    yml = create_ops._minimal_dependabot_yml(None)
    assert "version: 2" in yml
    assert 'package-ecosystem: "github-actions"' in yml


def test_minimal_dependabot_yml_adds_language_ecosystems() -> None:
    yml = create_ops._minimal_dependabot_yml(["python", "react"])
    assert 'package-ecosystem: "pip"' in yml
    assert 'package-ecosystem: "npm"' in yml
    assert 'package-ecosystem: "github-actions"' in yml


def test_minimal_dependabot_yml_deduplicates_go_gin() -> None:
    yml = create_ops._minimal_dependabot_yml(["go", "gin"])
    assert yml.count('package-ecosystem: "gomod"') == 1


def test_ensure_dependabot_version_updates_skips_when_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out_lines: list[str] = []
    api_calls: list[str] = []

    def _fake_api(
        *,
        method: str,
        endpoint: str,
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        api_calls.append(f"{method} {endpoint}")
        return subprocess.CompletedProcess(
            args=[], returncode=0, stdout="{}", stderr=""
        )

    monkeypatch.setattr(create_ops, "_api", _fake_api)
    create_ops._ensure_dependabot_version_updates(
        repo_dir=Path("/tmp/repo"),
        env={},
        repo="acme/repo",
        default_branch="main",
        languages=["python"],
        out=out_lines.append,
        warn=lambda _: None,
    )

    assert any("GET" in c for c in api_calls)
    assert len([c for c in api_calls if "PUT" in c]) == 0
    assert any("already configured" in line for line in out_lines)


def test_ensure_dependabot_version_updates_creates_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out_lines: list[str] = []
    call_count = {"n": 0}

    def _fake_api(
        *,
        method: str,
        endpoint: str,
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        call_count["n"] += 1
        if method == "GET":
            return subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="HTTP 404"
            )
        return subprocess.CompletedProcess(
            args=[], returncode=0, stdout="{}", stderr=""
        )

    monkeypatch.setattr(create_ops, "_api", _fake_api)
    create_ops._ensure_dependabot_version_updates(
        repo_dir=Path("/tmp/repo"),
        env={},
        repo="acme/repo",
        default_branch="main",
        languages=["python"],
        out=out_lines.append,
        warn=lambda _: None,
    )

    assert call_count["n"] == 2
    assert any("Created" in line for line in out_lines)


def test_ensure_dependabot_version_updates_falls_back_to_pr_when_protected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out_lines: list[str] = []

    def _fake_api(
        *,
        method: str,
        endpoint: str,
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        if method == "GET":
            return subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="HTTP 404"
            )
        # Direct PUT to the default branch is rejected by the ruleset.
        return subprocess.CompletedProcess(
            args=[],
            returncode=409,
            stdout="",
            stderr=(
                "Repository rule violations found\n\n"
                "Changes must be made through a pull request.\n"
            ),
        )

    branch_calls: list[tuple[str, str, str]] = []
    file_calls: list[dict[str, object]] = []
    pr_calls: list[tuple[str, str, str, str, str]] = []

    def _fake_branch_create(
        repo: str, name: str, token: str, base: str = "main"
    ) -> subprocess.CompletedProcess[str]:
        branch_calls.append((repo, name, base))
        return subprocess.CompletedProcess(
            args=[], returncode=201, stdout="{}", stderr=""
        )

    def _fake_github_rest(
        method: str, endpoint: str, token: str, data: object = None
    ) -> subprocess.CompletedProcess[str]:
        file_calls.append({"method": method, "endpoint": endpoint, "data": data})
        return subprocess.CompletedProcess(
            args=[], returncode=201, stdout="{}", stderr=""
        )

    def _fake_pr_create(
        repo: str, title: str, body: str, head: str, base: str, token: str
    ) -> subprocess.CompletedProcess[str]:
        pr_calls.append((repo, title, head, base, body))
        return subprocess.CompletedProcess(
            args=[],
            returncode=201,
            stdout='{"html_url": "https://github.com/acme/repo/pull/99"}',
            stderr="",
        )

    monkeypatch.setattr(create_ops, "_api", _fake_api)
    monkeypatch.setattr(create_ops, "_github_branch_create", _fake_branch_create)
    monkeypatch.setattr(create_ops, "_github_rest", _fake_github_rest)
    monkeypatch.setattr(create_ops, "_github_pr_create", _fake_pr_create)
    monkeypatch.setattr(create_ops, "_token_from_repo", lambda _: "tok")

    create_ops._ensure_dependabot_version_updates(
        repo_dir=Path("/tmp/repo"),
        env={},
        repo="acme/repo",
        default_branch="main",
        languages=["python"],
        out=out_lines.append,
        warn=lambda msg: out_lines.append(f"WARN:{msg}"),
    )

    assert branch_calls == [("acme/repo", "chore/add-dependabot-yml", "main")]
    assert len(file_calls) == 1
    assert (
        file_calls[0]["endpoint"] == "/repos/acme/repo/contents/.github/dependabot.yml"
    )
    assert pr_calls[0][2] == "chore/add-dependabot-yml"
    assert pr_calls[0][3] == "main"
    assert "## 🧾 Title" in pr_calls[0][4]
    assert any("opened one" in line and "pull/99" in line for line in out_lines)
    assert not any(line.startswith("WARN:") for line in out_lines)


def test_ensure_dependabot_version_updates_resets_stale_fallback_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A leftover chore/add-dependabot-yml branch is force-reset to the current
    default-branch tip rather than reused as-is, so it can't carry over stale or
    foreign commits."""
    out_lines: list[str] = []

    def _fake_api(
        *,
        method: str,
        endpoint: str,
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        if method == "GET":
            return subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="HTTP 404"
            )
        return subprocess.CompletedProcess(
            args=[],
            returncode=409,
            stdout="",
            stderr="Changes must be made through a pull request.",
        )

    def _fake_branch_create(
        repo: str, name: str, token: str, base: str = "main"
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=[], returncode=422, stdout="", stderr="Reference already exists"
        )

    def _fake_branch_get_sha(repo: str, branch: str, token: str) -> str | None:
        assert branch == "main"
        return "deadbeef"

    rest_calls: list[dict[str, object]] = []

    def _fake_github_rest(
        method: str, endpoint: str, token: str, data: object = None
    ) -> subprocess.CompletedProcess[str]:
        rest_calls.append({"method": method, "endpoint": endpoint, "data": data})
        # PATCH (ref reset) returns 200, PUT (file create) returns 201 -- match
        # real GitHub API status codes so the two-step flow is exercised properly.
        code = 200 if method == "PATCH" else 201
        return subprocess.CompletedProcess(
            args=[], returncode=code, stdout="{}", stderr=""
        )

    def _fake_pr_create(
        repo: str, title: str, body: str, head: str, base: str, token: str
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=[],
            returncode=201,
            stdout='{"html_url": "https://github.com/acme/repo/pull/100"}',
            stderr="",
        )

    monkeypatch.setattr(create_ops, "_api", _fake_api)
    monkeypatch.setattr(create_ops, "_github_branch_create", _fake_branch_create)
    monkeypatch.setattr(create_ops, "_github_branch_get_sha", _fake_branch_get_sha)
    monkeypatch.setattr(create_ops, "_github_rest", _fake_github_rest)
    monkeypatch.setattr(create_ops, "_github_pr_create", _fake_pr_create)
    monkeypatch.setattr(create_ops, "_token_from_repo", lambda _: "tok")

    create_ops._ensure_dependabot_version_updates(
        repo_dir=Path("/tmp/repo"),
        env={},
        repo="acme/repo",
        default_branch="main",
        languages=["python"],
        out=out_lines.append,
        warn=lambda msg: out_lines.append(f"WARN:{msg}"),
    )

    assert rest_calls[0]["method"] == "PATCH"
    assert (
        rest_calls[0]["endpoint"]
        == "/repos/acme/repo/git/refs/heads/chore/add-dependabot-yml"
    )
    assert rest_calls[0]["data"] == {"sha": "deadbeef", "force": True}
    assert rest_calls[1]["method"] == "PUT"
    assert not any(line.startswith("WARN:") for line in out_lines)


def test_ensure_dependabot_version_updates_warns_when_stale_branch_reset_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out_lines: list[str] = []

    def _fake_api(
        *,
        method: str,
        endpoint: str,
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        if method == "GET":
            return subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="HTTP 404"
            )
        return subprocess.CompletedProcess(
            args=[],
            returncode=409,
            stdout="",
            stderr="Changes must be made through a pull request.",
        )

    def _fake_branch_create(
        repo: str, name: str, token: str, base: str = "main"
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=[], returncode=422, stdout="", stderr="Reference already exists"
        )

    def _fake_branch_get_sha(repo: str, branch: str, token: str) -> str | None:
        return None

    monkeypatch.setattr(create_ops, "_api", _fake_api)
    monkeypatch.setattr(create_ops, "_github_branch_create", _fake_branch_create)
    monkeypatch.setattr(create_ops, "_github_branch_get_sha", _fake_branch_get_sha)
    monkeypatch.setattr(create_ops, "_token_from_repo", lambda _: "tok")

    create_ops._ensure_dependabot_version_updates(
        repo_dir=Path("/tmp/repo"),
        env={},
        repo="acme/repo",
        default_branch="main",
        languages=["python"],
        out=out_lines.append,
        warn=lambda msg: out_lines.append(f"WARN:{msg}"),
    )

    assert any(
        "WARN:" in line and "could not resolve main SHA to reset" in line
        for line in out_lines
    )


def test_ensure_dependabot_version_updates_warns_when_stale_branch_patch_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out_lines: list[str] = []

    def _fake_api(
        *,
        method: str,
        endpoint: str,
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        if method == "GET":
            return subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="HTTP 404"
            )
        return subprocess.CompletedProcess(
            args=[],
            returncode=409,
            stdout="",
            stderr="Changes must be made through a pull request.",
        )

    def _fake_branch_create(
        repo: str, name: str, token: str, base: str = "main"
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=[], returncode=422, stdout="", stderr="Reference already exists"
        )

    def _fake_branch_get_sha(repo: str, branch: str, token: str) -> str | None:
        return "deadbeef"

    def _fake_github_rest(
        method: str, endpoint: str, token: str, data: object = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=[], returncode=403, stdout="", stderr="protected ref"
        )

    monkeypatch.setattr(create_ops, "_api", _fake_api)
    monkeypatch.setattr(create_ops, "_github_branch_create", _fake_branch_create)
    monkeypatch.setattr(create_ops, "_github_branch_get_sha", _fake_branch_get_sha)
    monkeypatch.setattr(create_ops, "_github_rest", _fake_github_rest)
    monkeypatch.setattr(create_ops, "_token_from_repo", lambda _: "tok")

    create_ops._ensure_dependabot_version_updates(
        repo_dir=Path("/tmp/repo"),
        env={},
        repo="acme/repo",
        default_branch="main",
        languages=["python"],
        out=out_lines.append,
        warn=lambda msg: out_lines.append(f"WARN:{msg}"),
    )

    assert any(
        "WARN:" in line and "could not reset stale branch" in line for line in out_lines
    )


def test_ensure_dependabot_version_updates_warns_when_pr_fallback_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out_lines: list[str] = []

    def _fake_api(
        *,
        method: str,
        endpoint: str,
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        if method == "GET":
            return subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="HTTP 404"
            )
        return subprocess.CompletedProcess(
            args=[],
            returncode=409,
            stdout="",
            stderr="Changes must be made through a pull request.",
        )

    def _fake_branch_create(
        repo: str, name: str, token: str, base: str = "main"
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=[], returncode=422, stdout="", stderr="permission denied"
        )

    monkeypatch.setattr(create_ops, "_api", _fake_api)
    monkeypatch.setattr(create_ops, "_github_branch_create", _fake_branch_create)
    monkeypatch.setattr(create_ops, "_token_from_repo", lambda _: "tok")

    create_ops._ensure_dependabot_version_updates(
        repo_dir=Path("/tmp/repo"),
        env={},
        repo="acme/repo",
        default_branch="main",
        languages=["python"],
        out=out_lines.append,
        warn=lambda msg: out_lines.append(f"WARN:{msg}"),
    )

    assert any(
        "WARN:" in line and "could not create branch" in line for line in out_lines
    )
    assert any(
        "WARN:" in line
        and "could not create" in line
        and ".github/dependabot.yml" in line
        for line in out_lines
    )


def test_ensure_dependabot_version_updates_warns_when_file_write_on_branch_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out_lines: list[str] = []

    def _fake_api(
        *,
        method: str,
        endpoint: str,
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        if method == "GET":
            return subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="HTTP 404"
            )
        return subprocess.CompletedProcess(
            args=[],
            returncode=409,
            stdout="",
            stderr="Changes must be made through a pull request.",
        )

    def _fake_branch_create(
        repo: str, name: str, token: str, base: str = "main"
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=[], returncode=201, stdout="{}", stderr=""
        )

    def _fake_github_rest(
        method: str, endpoint: str, token: str, data: object = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=[], returncode=422, stdout="", stderr="sha wasn't supplied"
        )

    monkeypatch.setattr(create_ops, "_api", _fake_api)
    monkeypatch.setattr(create_ops, "_github_branch_create", _fake_branch_create)
    monkeypatch.setattr(create_ops, "_github_rest", _fake_github_rest)
    monkeypatch.setattr(create_ops, "_token_from_repo", lambda _: "tok")

    create_ops._ensure_dependabot_version_updates(
        repo_dir=Path("/tmp/repo"),
        env={},
        repo="acme/repo",
        default_branch="main",
        languages=["python"],
        out=out_lines.append,
        warn=lambda msg: out_lines.append(f"WARN:{msg}"),
    )

    assert any(
        "WARN:" in line
        and "could not create .github/dependabot.yml on chore/add-dependabot-yml"
        in line
        for line in out_lines
    )


def test_ensure_dependabot_version_updates_warns_when_pr_create_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out_lines: list[str] = []

    def _fake_api(
        *,
        method: str,
        endpoint: str,
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        if method == "GET":
            return subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="HTTP 404"
            )
        return subprocess.CompletedProcess(
            args=[],
            returncode=409,
            stdout="",
            stderr="Changes must be made through a pull request.",
        )

    def _fake_branch_create(
        repo: str, name: str, token: str, base: str = "main"
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=[], returncode=201, stdout="{}", stderr=""
        )

    def _fake_github_rest(
        method: str, endpoint: str, token: str, data: object = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=[], returncode=201, stdout="{}", stderr=""
        )

    def _fake_pr_create(
        repo: str, title: str, body: str, head: str, base: str, token: str
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=[], returncode=422, stdout="", stderr="A pull request already exists"
        )

    monkeypatch.setattr(create_ops, "_api", _fake_api)
    monkeypatch.setattr(create_ops, "_github_branch_create", _fake_branch_create)
    monkeypatch.setattr(create_ops, "_github_rest", _fake_github_rest)
    monkeypatch.setattr(create_ops, "_github_pr_create", _fake_pr_create)
    monkeypatch.setattr(create_ops, "_token_from_repo", lambda _: "tok")

    create_ops._ensure_dependabot_version_updates(
        repo_dir=Path("/tmp/repo"),
        env={},
        repo="acme/repo",
        default_branch="main",
        languages=["python"],
        out=out_lines.append,
        warn=lambda msg: out_lines.append(f"WARN:{msg}"),
    )

    assert any(
        "WARN:" in line and "could not open PR for .github/dependabot.yml" in line
        for line in out_lines
    )


def test_ensure_dependabot_version_updates_pr_fallback_handles_malformed_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A PR is still reported as opened even if the response body isn't valid JSON."""
    out_lines: list[str] = []

    def _fake_api(
        *,
        method: str,
        endpoint: str,
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        if method == "GET":
            return subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="HTTP 404"
            )
        return subprocess.CompletedProcess(
            args=[],
            returncode=409,
            stdout="",
            stderr="Changes must be made through a pull request.",
        )

    def _fake_branch_create(
        repo: str, name: str, token: str, base: str = "main"
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=[], returncode=201, stdout="{}", stderr=""
        )

    def _fake_github_rest(
        method: str, endpoint: str, token: str, data: object = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=[], returncode=201, stdout="{}", stderr=""
        )

    def _fake_pr_create(
        repo: str, title: str, body: str, head: str, base: str, token: str
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=[], returncode=201, stdout="not json", stderr=""
        )

    monkeypatch.setattr(create_ops, "_api", _fake_api)
    monkeypatch.setattr(create_ops, "_github_branch_create", _fake_branch_create)
    monkeypatch.setattr(create_ops, "_github_rest", _fake_github_rest)
    monkeypatch.setattr(create_ops, "_github_pr_create", _fake_pr_create)
    monkeypatch.setattr(create_ops, "_token_from_repo", lambda _: "tok")

    create_ops._ensure_dependabot_version_updates(
        repo_dir=Path("/tmp/repo"),
        env={},
        repo="acme/repo",
        default_branch="main",
        languages=["python"],
        out=out_lines.append,
        warn=lambda msg: out_lines.append(f"WARN:{msg}"),
    )

    assert any("opened one for .github/dependabot.yml." in line for line in out_lines)
    assert not any(line.startswith("WARN:") for line in out_lines)


def _fake_contents_get(rel_path_to_content: dict[str, str]) -> object:
    import base64 as _b64

    def _fake(
        method: str, endpoint: str, token: str, data: object = None
    ) -> subprocess.CompletedProcess[str]:
        assert method == "GET"
        # endpoint looks like /repos/acme/repo/contents/<rel_path>?ref=main
        path_and_ref = endpoint.split("/contents/", 1)[1]
        rel_path = path_and_ref.split("?", 1)[0]
        if rel_path not in rel_path_to_content:
            return subprocess.CompletedProcess(
                args=[], returncode=404, stdout="", stderr="Not Found"
            )
        encoded = _b64.b64encode(rel_path_to_content[rel_path].encode()).decode()
        return subprocess.CompletedProcess(
            args=[], returncode=200, stdout=f'{{"content": "{encoded}"}}', stderr=""
        )

    return _fake


def test_check_templates_reports_drift_for_stale_remote_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from repo_scaffold.generator import build_template_files

    files = build_template_files(tmp_path, owner="acme", name="repo")
    remote = {f.path.relative_to(tmp_path).as_posix(): f.content for f in files}
    # Simulate a stale remote pull_request_template.md -- everything else current.
    remote[".github/pull_request_template.md"] = "stale jira-style content\n"

    monkeypatch.setattr(create_ops, "_github_rest", _fake_contents_get(remote))
    monkeypatch.setattr(create_ops, "_token_from_repo", lambda _: "tok")

    out_lines: list[str] = []
    summary = create_ops._check_templates(
        repo_dir=tmp_path,
        env={},
        repo="acme/repo",
        default_branch="main",
        out=out_lines.append,
    )

    assert summary.drifted_files == (".github/pull_request_template.md",)
    assert any(
        line.startswith("DRIFT") and "pull_request_template.md" in line
        for line in out_lines
    )


def test_check_templates_passes_when_remote_is_up_to_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from repo_scaffold.generator import build_template_files

    files = build_template_files(tmp_path, owner="acme", name="repo")
    remote = {f.path.relative_to(tmp_path).as_posix(): f.content for f in files}

    monkeypatch.setattr(create_ops, "_github_rest", _fake_contents_get(remote))
    monkeypatch.setattr(create_ops, "_token_from_repo", lambda _: "tok")

    out_lines: list[str] = []
    summary = create_ops._check_templates(
        repo_dir=tmp_path,
        env={},
        repo="acme/repo",
        default_branch="main",
        out=out_lines.append,
    )

    assert summary.drifted_files == ()
    assert all(line.startswith("PASS") for line in out_lines if line.strip())


def test_check_templates_treats_missing_remote_file_as_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No remote files at all -- every template is missing on the default branch.
    monkeypatch.setattr(create_ops, "_github_rest", _fake_contents_get({}))
    monkeypatch.setattr(create_ops, "_token_from_repo", lambda _: "tok")

    summary = create_ops._check_templates(
        repo_dir=tmp_path,
        env={},
        repo="acme/repo",
        default_branch="main",
        out=lambda _l: None,
    )

    assert ".github/pull_request_template.md" in summary.drifted_files


def test_check_templates_ignores_local_working_tree_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale/uncommitted local checkout must not affect the result --
    only the remote default branch content matters."""
    from repo_scaffold.generator import build_template_files

    github_dir = tmp_path / ".github"
    github_dir.mkdir()
    (github_dir / "pull_request_template.md").write_text(
        "totally different local content that would look drifted\n",
        encoding="utf-8",
    )

    files = build_template_files(tmp_path, owner="acme", name="repo")
    remote = {f.path.relative_to(tmp_path).as_posix(): f.content for f in files}
    monkeypatch.setattr(create_ops, "_github_rest", _fake_contents_get(remote))
    monkeypatch.setattr(create_ops, "_token_from_repo", lambda _: "tok")

    summary = create_ops._check_templates(
        repo_dir=tmp_path,
        env={},
        repo="acme/repo",
        default_branch="main",
        out=lambda _l: None,
    )

    assert summary.drifted_files == ()


def test_open_templates_sync_pr_writes_files_and_opens_pr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    branch_calls: list[tuple[str, str, str]] = []
    get_calls: list[str] = []
    put_calls: list[dict[str, object]] = []
    pr_calls: list[tuple[str, str, str, str, str]] = []

    def _fake_branch_create(
        repo: str, name: str, token: str, base: str = "main"
    ) -> subprocess.CompletedProcess[str]:
        branch_calls.append((repo, name, base))
        return subprocess.CompletedProcess(
            args=[], returncode=201, stdout="{}", stderr=""
        )

    def _fake_github_rest(
        method: str, endpoint: str, token: str, data: object = None
    ) -> subprocess.CompletedProcess[str]:
        if method == "GET":
            get_calls.append(endpoint)
            return subprocess.CompletedProcess(
                args=[], returncode=200, stdout='{"sha": "abc123"}', stderr=""
            )
        put_calls.append({"method": method, "endpoint": endpoint, "data": data})
        return subprocess.CompletedProcess(
            args=[], returncode=201, stdout="{}", stderr=""
        )

    def _fake_pr_create(
        repo: str, title: str, body: str, head: str, base: str, token: str
    ) -> subprocess.CompletedProcess[str]:
        pr_calls.append((repo, title, head, base, body))
        return subprocess.CompletedProcess(
            args=[],
            returncode=201,
            stdout='{"html_url": "https://github.com/acme/repo/pull/42"}',
            stderr="",
        )

    monkeypatch.setattr(create_ops, "_github_branch_create", _fake_branch_create)
    monkeypatch.setattr(create_ops, "_github_rest", _fake_github_rest)
    monkeypatch.setattr(create_ops, "_github_pr_create", _fake_pr_create)
    monkeypatch.setattr(create_ops, "_token_from_repo", lambda _: "tok")

    out_lines: list[str] = []
    result = create_ops._open_templates_sync_pr(
        repo_dir=Path("/tmp/repo"),
        env={},
        repo="acme/repo",
        default_branch="main",
        files=[(".github/pull_request_template.md", "new content\n")],
        out=out_lines.append,
        warn=lambda msg: out_lines.append(f"WARN:{msg}"),
    )

    assert result == "https://github.com/acme/repo/pull/42"
    assert branch_calls == [("acme/repo", "chore/sync-templates", "main")]
    assert (
        put_calls[0]["endpoint"]
        == "/repos/acme/repo/contents/.github/pull_request_template.md"
    )
    assert put_calls[0]["data"]["sha"] == "abc123"
    assert pr_calls[0][2] == "chore/sync-templates"
    assert pr_calls[0][3] == "main"


def test_open_templates_sync_pr_resets_stale_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_branch_create(
        repo: str, name: str, token: str, base: str = "main"
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=[], returncode=422, stdout="", stderr="Reference already exists"
        )

    def _fake_branch_get_sha(repo: str, branch: str, token: str) -> str:
        return "def456"

    reset_calls: list[dict[str, object]] = []

    def _fake_github_rest(
        method: str, endpoint: str, token: str, data: object = None
    ) -> subprocess.CompletedProcess[str]:
        if method == "PATCH":
            reset_calls.append({"endpoint": endpoint, "data": data})
            return subprocess.CompletedProcess(
                args=[], returncode=200, stdout="{}", stderr=""
            )
        if method == "GET":
            return subprocess.CompletedProcess(
                args=[], returncode=200, stdout='{"sha": "abc"}', stderr=""
            )
        return subprocess.CompletedProcess(
            args=[], returncode=201, stdout="{}", stderr=""
        )

    def _fake_pr_create(
        repo: str, title: str, body: str, head: str, base: str, token: str
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=[], returncode=201, stdout='{"html_url": "https://x/pr/1"}', stderr=""
        )

    monkeypatch.setattr(create_ops, "_github_branch_create", _fake_branch_create)
    monkeypatch.setattr(create_ops, "_github_branch_get_sha", _fake_branch_get_sha)
    monkeypatch.setattr(create_ops, "_github_rest", _fake_github_rest)
    monkeypatch.setattr(create_ops, "_github_pr_create", _fake_pr_create)
    monkeypatch.setattr(create_ops, "_token_from_repo", lambda _: "tok")

    result = create_ops._open_templates_sync_pr(
        repo_dir=Path("/tmp/repo"),
        env={},
        repo="acme/repo",
        default_branch="main",
        files=[(".github/pull_request_template.md", "content\n")],
        out=lambda _l: None,
        warn=lambda _l: None,
    )

    assert result == "https://x/pr/1"
    assert (
        reset_calls[0]["endpoint"]
        == "/repos/acme/repo/git/refs/heads/chore/sync-templates"
    )
    assert reset_calls[0]["data"] == {"sha": "def456", "force": True}


def test_sync_templates_skips_pr_when_no_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from repo_scaffold.generator import build_template_files

    files = build_template_files(tmp_path, owner="acme", name="repo")
    remote = {f.path.relative_to(tmp_path).as_posix(): f.content for f in files}

    def _fake_rest(
        method: str, endpoint: str, token: str, data: object = None
    ) -> subprocess.CompletedProcess[str]:
        if method == "GET" and "/contents/" in endpoint:
            return _fake_contents_get(remote)(method, endpoint, token, data)
        raise AssertionError(f"unexpected {method} {endpoint} -- should not open a PR")

    monkeypatch.setattr(create_ops, "_github_rest", _fake_rest)
    monkeypatch.setattr(create_ops, "_token_from_repo", lambda _: "tok")
    monkeypatch.setattr(
        create_ops,
        "_get_repo_info",
        lambda *, repo_dir, env, repo: {"default_branch": "main"},
    )

    def _fail_if_called(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("should not open a branch/PR when there is no drift")

    monkeypatch.setattr(create_ops, "_github_branch_create", _fail_if_called)

    result = create_ops._sync_templates(
        repo_dir=tmp_path,
        env={},
        repo="acme/repo",
        out=lambda _l: None,
        warn=lambda _l: None,
    )
    assert result.summary.drifted_files == ()
    assert result.pr_url is None


def test_sync_templates_returns_pr_url_when_drifted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No remote files -- everything drifted -- forces the PR path.
    monkeypatch.setattr(create_ops, "_github_rest", _fake_contents_get({}))
    monkeypatch.setattr(create_ops, "_token_from_repo", lambda _: "tok")
    monkeypatch.setattr(
        create_ops,
        "_get_repo_info",
        lambda *, repo_dir, env, repo: {"default_branch": "main"},
    )
    monkeypatch.setattr(
        create_ops,
        "_open_templates_sync_pr",
        lambda **_kwargs: "https://github.com/acme/repo/pull/9",
    )

    result = create_ops._sync_templates(
        repo_dir=tmp_path,
        env={},
        repo="acme/repo",
        out=lambda _l: None,
        warn=lambda _l: None,
    )
    assert result.summary.drifted_files != ()
    assert result.pr_url == "https://github.com/acme/repo/pull/9"


def test_check_configs_reports_drift_for_missing_remote_poetry_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(create_ops, "_github_rest", _fake_contents_get({}))

    out_lines: list[str] = []
    summary = create_ops._check_configs(
        repo_dir=tmp_path,
        env={},
        repo="acme/repo",
        default_branch="main",
        languages=("python",),
        out=out_lines.append,
    )

    assert summary.drifted_files == ("poetry.toml",)
    assert any("DRIFT config: poetry.toml" in line for line in out_lines)


def test_check_configs_passes_when_remote_content_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from repo_scaffold.generator import build_config_files

    [wanted] = build_config_files(tmp_path, languages=("python",))
    monkeypatch.setattr(
        create_ops,
        "_github_rest",
        _fake_contents_get({"poetry.toml": wanted.content}),
    )

    summary = create_ops._check_configs(
        repo_dir=tmp_path,
        env={},
        repo="acme/repo",
        default_branch="main",
        languages=("python",),
        out=lambda _line: None,
    )

    assert summary.drifted_files == ()


def test_check_configs_empty_for_non_python_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def _fake_github_rest(
        method: str, endpoint: str, token: str, data: object = None
    ) -> subprocess.CompletedProcess[str]:
        calls.append(endpoint)
        return subprocess.CompletedProcess(
            args=[], returncode=404, stdout="", stderr="Not Found"
        )

    monkeypatch.setattr(create_ops, "_github_rest", _fake_github_rest)

    summary = create_ops._check_configs(
        repo_dir=tmp_path,
        env={},
        repo="acme/repo",
        default_branch="main",
        languages=("go",),
        out=lambda _line: None,
    )

    assert summary.drifted_files == ()
    assert calls == []


def test_check_configs_raises_on_non_404_fetch_error_instead_of_reporting_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 403/5xx/network failure fetching the remote file must not be treated
    the same as a confirmed-absent (404) file -- that would report false
    drift (or false PASS) from a source we never actually read."""

    def _fake_github_rest(
        method: str, endpoint: str, token: str, data: object = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=[], returncode=403, stdout="", stderr="API rate limit exceeded"
        )

    monkeypatch.setattr(create_ops, "_github_rest", _fake_github_rest)

    with pytest.raises(RuntimeError, match="poetry.toml"):
        create_ops._check_configs(
            repo_dir=tmp_path,
            env={},
            repo="acme/repo",
            default_branch="main",
            languages=("python",),
            out=lambda _line: None,
        )


def test_open_configs_sync_pr_writes_drifted_files_and_opens_pr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    branch_calls: list[tuple[str, str, str]] = []
    put_calls: list[dict[str, object]] = []
    pr_calls: list[tuple[str, str, str, str, str]] = []

    def _fake_branch_create(
        repo: str, name: str, token: str, base: str = "main"
    ) -> subprocess.CompletedProcess[str]:
        branch_calls.append((repo, name, base))
        return subprocess.CompletedProcess(
            args=[], returncode=201, stdout="{}", stderr=""
        )

    def _fake_github_rest(
        method: str, endpoint: str, token: str, data: object = None
    ) -> subprocess.CompletedProcess[str]:
        if method == "GET":
            return subprocess.CompletedProcess(
                args=[], returncode=404, stdout="", stderr="Not Found"
            )
        put_calls.append({"method": method, "endpoint": endpoint, "data": data})
        return subprocess.CompletedProcess(
            args=[], returncode=201, stdout="{}", stderr=""
        )

    def _fake_pr_create(
        repo: str, title: str, body: str, head: str, base: str, token: str
    ) -> subprocess.CompletedProcess[str]:
        pr_calls.append((repo, title, head, base, body))
        return subprocess.CompletedProcess(
            args=[],
            returncode=201,
            stdout='{"html_url": "https://github.com/acme/repo/pull/101"}',
            stderr="",
        )

    monkeypatch.setattr(create_ops, "_github_branch_create", _fake_branch_create)
    monkeypatch.setattr(create_ops, "_github_rest", _fake_github_rest)
    monkeypatch.setattr(create_ops, "_github_pr_create", _fake_pr_create)
    monkeypatch.setattr(create_ops, "_token_from_repo", lambda _: "tok")

    out_lines: list[str] = []
    pr_url = create_ops._open_configs_sync_pr(
        repo_dir=Path("/tmp/repo"),
        env={},
        repo="acme/repo",
        default_branch="main",
        files=[("poetry.toml", "[virtualenvs]\nin-project = false\n")],
        out=out_lines.append,
        warn=lambda msg: out_lines.append(f"WARN:{msg}"),
    )

    assert pr_url == "https://github.com/acme/repo/pull/101"
    assert branch_calls == [("acme/repo", "chore/sync-configs", "main")]
    assert put_calls[0]["endpoint"] == "/repos/acme/repo/contents/poetry.toml"
    assert pr_calls[0][2] == "chore/sync-configs"
    assert pr_calls[0][3] == "main"
    assert "Updated environment configuration" in pr_calls[0][4]
    assert "poetry.toml" in pr_calls[0][4]
    assert not any(line.startswith("WARN:") for line in out_lines)


def test_open_configs_sync_pr_resets_stale_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_branch_create(
        repo: str, name: str, token: str, base: str = "main"
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=[], returncode=422, stdout="", stderr="Reference already exists"
        )

    def _fake_branch_get_sha(repo: str, branch: str, token: str) -> str | None:
        assert branch == "main"
        return "deadbeef"

    reset_calls: list[dict[str, object]] = []

    def _fake_github_rest(
        method: str, endpoint: str, token: str, data: object = None
    ) -> subprocess.CompletedProcess[str]:
        if method == "PATCH":
            reset_calls.append({"endpoint": endpoint, "data": data})
            return subprocess.CompletedProcess(
                args=[], returncode=200, stdout="{}", stderr=""
            )
        if method == "GET":
            return subprocess.CompletedProcess(
                args=[], returncode=404, stdout="", stderr="Not Found"
            )
        return subprocess.CompletedProcess(
            args=[], returncode=201, stdout="{}", stderr=""
        )

    def _fake_pr_create(
        repo: str, title: str, body: str, head: str, base: str, token: str
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=[],
            returncode=201,
            stdout='{"html_url": "https://github.com/acme/repo/pull/102"}',
            stderr="",
        )

    monkeypatch.setattr(create_ops, "_github_branch_create", _fake_branch_create)
    monkeypatch.setattr(create_ops, "_github_branch_get_sha", _fake_branch_get_sha)
    monkeypatch.setattr(create_ops, "_github_rest", _fake_github_rest)
    monkeypatch.setattr(create_ops, "_github_pr_create", _fake_pr_create)
    monkeypatch.setattr(create_ops, "_token_from_repo", lambda _: "tok")

    pr_url = create_ops._open_configs_sync_pr(
        repo_dir=Path("/tmp/repo"),
        env={},
        repo="acme/repo",
        default_branch="main",
        files=[("poetry.toml", "[virtualenvs]\nin-project = false\n")],
        out=lambda _line: None,
        warn=lambda _line: None,
    )

    assert pr_url == "https://github.com/acme/repo/pull/102"
    assert (
        reset_calls[0]["endpoint"]
        == "/repos/acme/repo/git/refs/heads/chore/sync-configs"
    )
    assert reset_calls[0]["data"] == {"sha": "deadbeef", "force": True}


def test_sync_configs_skips_pr_when_no_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from repo_scaffold.generator import build_config_files

    [wanted] = build_config_files(tmp_path, languages=("python",))
    monkeypatch.setattr(
        create_ops,
        "_github_rest",
        _fake_contents_get({"poetry.toml": wanted.content}),
    )
    monkeypatch.setattr(create_ops, "_token_from_repo", lambda _: "tok")
    monkeypatch.setattr(
        create_ops,
        "_get_repo_info",
        lambda *, repo_dir, env, repo: {"default_branch": "main"},
    )

    def _fail_if_called(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("should not open a branch/PR when there is no drift")

    monkeypatch.setattr(create_ops, "_github_branch_create", _fail_if_called)

    result = create_ops._sync_configs(
        repo_dir=tmp_path,
        env={},
        repo="acme/repo",
        languages=("python",),
        out=lambda _l: None,
        warn=lambda _l: None,
    )
    assert result.summary.drifted_files == ()
    assert result.pr_url is None


def test_sync_configs_returns_pr_url_when_drifted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(create_ops, "_github_rest", _fake_contents_get({}))
    monkeypatch.setattr(create_ops, "_token_from_repo", lambda _: "tok")
    monkeypatch.setattr(
        create_ops,
        "_get_repo_info",
        lambda *, repo_dir, env, repo: {"default_branch": "main"},
    )
    monkeypatch.setattr(
        create_ops,
        "_open_configs_sync_pr",
        lambda **_kwargs: "https://github.com/acme/repo/pull/10",
    )

    result = create_ops._sync_configs(
        repo_dir=tmp_path,
        env={},
        repo="acme/repo",
        languages=("python",),
        out=lambda _l: None,
        warn=lambda _l: None,
    )
    assert result.summary.drifted_files != ()
    assert result.pr_url == "https://github.com/acme/repo/pull/10"
