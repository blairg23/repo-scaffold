from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import quote

from .auth_tokens import is_placeholder_token, resolve_gh_token
from .github_api import (
    repo_create as _github_repo_create,
    rest as _github_rest,
    token_from_repo as _token_from_repo,
)


@dataclass(frozen=True)
class CreateSummary:
    repo: str
    repo_created: bool
    pushed: bool
    settings_applied: bool
    failures: int


@dataclass(frozen=True)
class SettingsCheckSummary:
    repo: str
    passed: int
    failed: int
    skipped: int
    drifts: tuple[str, ...]


_API_VERSION = "2026-03-10"
_SETTINGS_RULESET_NAME = "default-branch (managed by repo-scaffold)"
_LEGACY_RULESET_NAMES: frozenset[str] = frozenset(
    {
        "repo-scaffold baseline branch rules",
        "repo-scaffold default-branch ruleset",
    }
)

_BEST_EFFORT_SECURITY_FEATURES: tuple[tuple[str, str], ...] = (
    ("Dependabot alerts", "/repos/{repo}/vulnerability-alerts"),
    ("Dependabot security updates", "/repos/{repo}/automated-security-fixes"),
)

_DEPENDABOT_YML_PATH = ".github/dependabot.yml"

_LANG_ECOSYSTEM_MAP: dict[str, str] = {
    "python": "pip",
    "go": "gomod",
    "gin": "gomod",
    "react": "npm",
}


def _is_managed_ruleset_name(name: object) -> bool:
    return isinstance(name, str) and (
        name == _SETTINGS_RULESET_NAME or name in _LEGACY_RULESET_NAMES
    )


def _api_headers() -> list[str]:
    return []  # kept for any residual callers; not used by _api anymore


def _repo_patch_payload() -> str:
    return json.dumps(
        {
            "allow_squash_merge": True,
            "squash_merge_commit_title": "PR_TITLE",
            "squash_merge_commit_message": "PR_BODY",
            "allow_merge_commit": False,
            "allow_rebase_merge": False,
            "delete_branch_on_merge": True,
            "allow_auto_merge": False,
            "is_template": False,
        },
        indent=2,
    )


_LANGUAGE_CI_CONTEXT: dict[str, str] = {
    "react": "react",
    "python": "python",
    "go": "go",
    "gin": "go",
}

_ALWAYS_REQUIRED_CONTEXTS: list[str] = ["check-sop", "validate-pr"]


def _default_branch_ruleset_payload(
    languages: list[str] | None = None,
    *,
    include_code_quality: bool = True,
) -> str:
    rules: list[dict[str, object]] = [
        {"type": "creation"},
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
    ]
    if include_code_quality:
        rules.append(
            {
                "type": "code_quality",
                "parameters": {
                    "code_quality_tools": [
                        {
                            "tool": "CodeQL",
                            "severity": "notes",
                        }
                    ]
                },
            }
        )
    rules.append(
        {
            "type": "copilot_code_review",
            "parameters": {
                "review_draft_pull_requests": True,
                "review_on_push": True,
            },
        }
    )

    contexts = list(_ALWAYS_REQUIRED_CONTEXTS)
    if languages:
        for ctx in dict.fromkeys(
            _LANGUAGE_CI_CONTEXT[lang]
            for lang in languages
            if lang in _LANGUAGE_CI_CONTEXT
        ):
            if ctx not in contexts:
                contexts.append(ctx)
    rules.append(
        {
            "type": "required_status_checks",
            "parameters": {
                "required_status_checks": [{"context": ctx} for ctx in contexts],
                "strict_required_status_checks_policy": False,
            },
        }
    )
    return json.dumps(
        {
            "name": _SETTINGS_RULESET_NAME,
            "target": "branch",
            "enforcement": "active",
            "conditions": {
                "ref_name": {
                    "include": ["~DEFAULT_BRANCH"],
                    "exclude": [],
                }
            },
            "rules": rules,
        },
        indent=2,
    )


def _security_and_analysis_payload(feature: str) -> str:
    return json.dumps(
        {"security_and_analysis": {feature: {"status": "enabled"}}},
        indent=2,
    )


def _load_env_file(path: Path) -> dict[str, str]:
    loaded: dict[str, str] = {}
    if not path.exists():
        return loaded

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().rstrip("\r")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        loaded[key] = value
    return loaded


def _build_env(repo_dir: Path) -> dict[str, str]:
    env = dict(os.environ)

    for env_file in (Path.cwd() / ".env", repo_dir / ".env"):
        for key, value in _load_env_file(env_file).items():
            env.setdefault(key, value)

    resolved_token = resolve_gh_token(env)
    current_gh_token = env.get("GH_TOKEN")
    if resolved_token and (
        not current_gh_token or is_placeholder_token(current_gh_token)
    ):
        env["GH_TOKEN"] = resolved_token

    if not env.get("GITHUB_ORG") and env.get("github_org"):
        env["GITHUB_ORG"] = env["github_org"]
    if not env.get("GITHUB_REPO") and env.get("github_repo"):
        env["GITHUB_REPO"] = env["github_repo"]
    if not env.get("GH_REPO"):
        if env.get("GITHUB_REPOSITORY"):
            env["GH_REPO"] = env["GITHUB_REPOSITORY"]
        elif env.get("github_full_repo"):
            env["GH_REPO"] = env["github_full_repo"]

    return env


def _run(
    args: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    stdin_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        env=env,
        text=True,
        input=stdin_text,
        capture_output=True,
        check=False,
    )


def _api(
    *,
    repo_dir: Path,
    env: dict[str, str],
    method: str,
    endpoint: str,
    stdin_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    token = (
        _token_from_repo(repo_dir)
        or env.get("GH_TOKEN")
        or env.get("GITHUB_TOKEN")
        or ""
    )
    data = json.loads(stdin_text) if stdin_text else None
    return _github_rest(method, endpoint, token, data)


def _load_json(text: str, *, error_message: str) -> object:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(error_message) from exc


def _get_repo_info(
    *, repo_dir: Path, env: dict[str, str], repo: str
) -> dict[str, object]:
    cp = _api(repo_dir=repo_dir, env=env, method="GET", endpoint=f"/repos/{repo}")
    if cp.returncode != 0:
        raise RuntimeError(cp.stderr.strip() or f"Failed loading repository: {repo}")
    payload = _load_json(
        cp.stdout,
        error_message=f"Unexpected repository metadata response for {repo}.",
    )
    if not isinstance(payload, dict):
        raise RuntimeError(f"Unexpected repository metadata response for {repo}.")
    return payload


def _list_repo_rulesets(
    *, repo_dir: Path, env: dict[str, str], repo: str
) -> list[dict[str, object]]:
    cp = _api(
        repo_dir=repo_dir,
        env=env,
        method="GET",
        endpoint=f"/repos/{repo}/rulesets?includes_parents=false&targets=branch",
    )
    if cp.returncode != 0:
        raise RuntimeError(cp.stderr.strip() or f"Failed listing rulesets for {repo}.")
    payload = _load_json(
        cp.stdout,
        error_message=f"Unexpected rulesets response for {repo}.",
    )
    if not isinstance(payload, list):
        raise RuntimeError(f"Unexpected rulesets response for {repo}.")
    return [item for item in payload if isinstance(item, dict)]


def _get_repo_ruleset(
    *, repo_dir: Path, env: dict[str, str], repo: str, ruleset_id: int
) -> dict[str, object]:
    cp = _api(
        repo_dir=repo_dir,
        env=env,
        method="GET",
        endpoint=f"/repos/{repo}/rulesets/{ruleset_id}",
    )
    if cp.returncode != 0:
        raise RuntimeError(
            cp.stderr.strip() or f"Failed loading ruleset {ruleset_id} for {repo}."
        )
    payload = _load_json(
        cp.stdout,
        error_message=f"Unexpected ruleset response for {repo}#{ruleset_id}.",
    )
    if not isinstance(payload, dict):
        raise RuntimeError(f"Unexpected ruleset response for {repo}#{ruleset_id}.")
    return payload


def _branch_protection_endpoint(*, repo: str, branch: str) -> str:
    return f"/repos/{repo}/branches/{quote(branch, safe='')}/protection"


def _clear_legacy_branch_protection(
    *,
    repo_dir: Path,
    env: dict[str, str],
    repo: str,
    default_branch: str,
    out: Callable[[str], None],
) -> None:
    cp = _api(
        repo_dir=repo_dir,
        env=env,
        method="DELETE",
        endpoint=_branch_protection_endpoint(repo=repo, branch=default_branch),
    )
    if cp.returncode == 0:
        out(f"Removed legacy branch protection from {default_branch}.")
        return

    message = (cp.stderr or cp.stdout or "").strip().lower()
    not_found_markers = (
        "branch not protected",
        "not found",
        "http 404",
    )
    if any(marker in message for marker in not_found_markers):
        return

    raise RuntimeError(
        cp.stderr.strip()
        or f"Failed removing legacy branch protection from {default_branch}."
    )


def _sync_default_branch_ruleset(
    *,
    repo_dir: Path,
    env: dict[str, str],
    repo: str,
    out: Callable[[str], None],
    warn: Callable[[str], None] | None = None,
    languages: list[str] | None = None,
) -> None:
    emit_warn = warn if warn is not None else out
    managed_rulesets = [
        item
        for item in _list_repo_rulesets(repo_dir=repo_dir, env=env, repo=repo)
        if _is_managed_ruleset_name(item.get("name"))
    ]

    def _apply_payload(payload: str, method: str, endpoint: str) -> bool:
        cp = _api(
            repo_dir=repo_dir,
            env=env,
            method=method,
            endpoint=endpoint,
            stdin_text=payload,
        )
        if cp.returncode == 0:
            return True
        err = cp.stderr.strip() or cp.stdout.strip() or ""
        if "code_quality" in err.lower() or "invalid property /rules/" in err.lower():
            return False
        raise RuntimeError(err or f"Failed applying managed ruleset ({method}).")

    payload = _default_branch_ruleset_payload(languages=languages)
    fallback_payload = _default_branch_ruleset_payload(
        languages=languages, include_code_quality=False
    )

    if managed_rulesets:
        ruleset_id = managed_rulesets[0].get("id")
        if not isinstance(ruleset_id, int):
            raise RuntimeError("Managed ruleset exists but is missing a numeric id.")
        endpoint = f"/repos/{repo}/rulesets/{ruleset_id}"
        if not _apply_payload(payload, "PUT", endpoint):
            emit_warn(
                "Warning: code_quality rule not supported on this repo; "
                "applying ruleset without it."
            )
            cp2 = _api(
                repo_dir=repo_dir,
                env=env,
                method="PUT",
                endpoint=endpoint,
                stdin_text=fallback_payload,
            )
            if cp2.returncode != 0:
                raise RuntimeError(
                    cp2.stderr.strip() or "Failed updating managed ruleset."
                )
        out(f"Updated ruleset '{_SETTINGS_RULESET_NAME}'.")
        return

    endpoint = f"/repos/{repo}/rulesets"
    if not _apply_payload(payload, "POST", endpoint):
        emit_warn(
            "Warning: code_quality rule not supported on this repo; "
            "applying ruleset without it."
        )
        cp2 = _api(
            repo_dir=repo_dir,
            env=env,
            method="POST",
            endpoint=endpoint,
            stdin_text=fallback_payload,
        )
        if cp2.returncode != 0:
            raise RuntimeError(cp2.stderr.strip() or "Failed creating managed ruleset.")
    out(f"Created ruleset '{_SETTINGS_RULESET_NAME}'.")


def _minimal_dependabot_yml(languages: list[str] | None) -> str:
    ecosystems: dict[str, str] = {"github-actions": "/"}
    for lang in languages or []:
        eco = _LANG_ECOSYSTEM_MAP.get(lang)
        if eco and eco not in ecosystems:
            ecosystems[eco] = "/"
    entries = []
    for eco, directory in ecosystems.items():
        entries += [
            f'  - package-ecosystem: "{eco}"',
            f'    directory: "{directory}"',
            "    schedule:",
            '      interval: "weekly"',
        ]
    return "version: 2\nupdates:\n" + "\n".join(entries) + "\n"


def _ensure_dependabot_version_updates(
    *,
    repo_dir: Path,
    env: dict[str, str],
    repo: str,
    languages: list[str] | None,
    out: Callable[[str], None],
    warn: Callable[[str], None],
) -> None:
    check_cp = _api(
        repo_dir=repo_dir,
        env=env,
        method="GET",
        endpoint=f"/repos/{repo}/contents/{_DEPENDABOT_YML_PATH}",
    )
    if check_cp.returncode == 0:
        out("Dependabot version updates already configured.")
        return
    content = _minimal_dependabot_yml(languages)
    encoded = base64.b64encode(content.encode()).decode()
    create_cp = _api(
        repo_dir=repo_dir,
        env=env,
        method="PUT",
        endpoint=f"/repos/{repo}/contents/{_DEPENDABOT_YML_PATH}",
        stdin_text=json.dumps(
            {
                "message": "chore: add dependabot version updates config",
                "content": encoded,
            }
        ),
    )
    if create_cp.returncode == 0:
        out(f"Created {_DEPENDABOT_YML_PATH}.")
        return
    feature_err = (
        create_cp.stderr.strip() or create_cp.stdout.strip() or "unknown error"
    )
    warn(f"Warning: could not create {_DEPENDABOT_YML_PATH}: {feature_err}")


def _enable_security_and_analysis_feature(
    *,
    repo_dir: Path,
    env: dict[str, str],
    repo: str,
    feature_name: str,
    feature_key: str,
    out: Callable[[str], None],
    warn: Callable[[str], None],
) -> None:
    cp = _api(
        repo_dir=repo_dir,
        env=env,
        method="PATCH",
        endpoint=f"/repos/{repo}",
        stdin_text=_security_and_analysis_payload(feature_key),
    )
    if cp.returncode == 0:
        out(f"Enabled {feature_name.lower()}.")
        return
    feature_err = cp.stderr.strip() or cp.stdout.strip() or "unknown error"
    warn(f"Warning: could not enable {feature_name.lower()}: {feature_err}")


def _enable_optional_endpoint_feature(
    *,
    repo_dir: Path,
    env: dict[str, str],
    endpoint: str,
    feature_name: str,
    out: Callable[[str], None],
    warn: Callable[[str], None],
) -> None:
    cp = _api(repo_dir=repo_dir, env=env, method="PUT", endpoint=endpoint)
    if cp.returncode == 0:
        out(f"Enabled {feature_name.lower()}.")
        return
    feature_err = cp.stderr.strip() or cp.stdout.strip() or "unknown error"
    warn(f"Warning: could not enable {feature_name.lower()}: {feature_err}")


def _enable_code_scanning_default_setup(
    *,
    repo_dir: Path,
    env: dict[str, str],
    repo: str,
    out: Callable[[str], None],
    warn: Callable[[str], None],
) -> None:
    cp = _api(
        repo_dir=repo_dir,
        env=env,
        method="PATCH",
        endpoint=f"/repos/{repo}/code-scanning/default-setup",
        stdin_text=json.dumps({"state": "configured"}),
    )
    if cp.returncode == 0:
        out("Enabled code scanning default setup.")
        return
    feature_err = cp.stderr.strip() or cp.stdout.strip() or "unknown error"
    warn(f"Warning: could not enable code scanning default setup: {feature_err}")


def _legacy_branch_protection_exists(
    *,
    repo_dir: Path,
    env: dict[str, str],
    repo: str,
    default_branch: str,
) -> bool:
    cp = _api(
        repo_dir=repo_dir,
        env=env,
        method="GET",
        endpoint=_branch_protection_endpoint(repo=repo, branch=default_branch),
    )
    if cp.returncode == 0:
        return True

    message = (cp.stderr or cp.stdout or "").strip().lower()
    not_found_markers = (
        "branch not protected",
        "not found",
        "http 404",
    )
    if any(marker in message for marker in not_found_markers):
        return False

    raise RuntimeError(
        cp.stderr.strip()
        or f"Failed checking legacy branch protection on {default_branch}."
    )


def _optional_endpoint_feature_enabled(
    *,
    repo_dir: Path,
    env: dict[str, str],
    endpoint: str,
) -> tuple[bool, str]:
    cp = _api(repo_dir=repo_dir, env=env, method="GET", endpoint=endpoint)
    if cp.returncode == 0:
        return True, "enabled"
    message = cp.stderr.strip() or cp.stdout.strip() or "unknown error"
    return False, message


def _security_feature_status(
    repo_info: dict[str, object], feature_key: str
) -> str | None:
    raw = repo_info.get("security_and_analysis")
    if not isinstance(raw, dict):
        return None
    feature = raw.get(feature_key)
    if not isinstance(feature, dict):
        return None
    status = feature.get("status")
    if isinstance(status, str):
        return status.strip().lower()
    return None


def _compare_ruleset_against_baseline(
    rulesets: list[dict[str, object]],
    *,
    default_branch: str,
    languages: list[str] | None = None,
) -> list[str]:
    drifts: list[str] = []
    managed_rulesets = [
        item for item in rulesets if _is_managed_ruleset_name(item.get("name"))
    ]
    if not managed_rulesets:
        return ["managed default-branch ruleset missing"]

    if len(managed_rulesets) > 1:
        drifts.append("multiple managed default-branch rulesets found")

    ruleset = managed_rulesets[0]
    if ruleset.get("target") != "branch":
        drifts.append(f"target expected 'branch' got {ruleset.get('target')!r}")
    if ruleset.get("enforcement") != "active":
        drifts.append(
            f"enforcement expected 'active' got {ruleset.get('enforcement')!r}"
        )

    conditions = ruleset.get("conditions")
    if not isinstance(conditions, dict):
        drifts.append("conditions missing or invalid")
        return drifts

    ref_name = conditions.get("ref_name")
    if not isinstance(ref_name, dict):
        drifts.append("conditions.ref_name missing or invalid")
        return drifts

    include = ref_name.get("include")
    exclude = ref_name.get("exclude")
    accepted_include_values = (
        ["~DEFAULT_BRANCH"],
        [default_branch],
        [f"refs/heads/{default_branch}"],
    )
    if include not in accepted_include_values:
        drifts.append(
            "conditions.ref_name.include expected one of "
            f"{accepted_include_values!r} got {include!r}"
        )
    if exclude != []:
        drifts.append(f"conditions.ref_name.exclude expected [] got {exclude!r}")

    raw_rules = ruleset.get("rules")
    if not isinstance(raw_rules, list):
        drifts.append("rules missing or invalid")
        return drifts

    rule_map: dict[str, dict[str, object]] = {}
    for item in raw_rules:
        if not isinstance(item, dict):
            continue
        rule_type = item.get("type")
        if isinstance(rule_type, str):
            rule_map[rule_type] = item

    for required_rule in (
        "creation",
        "deletion",
        "non_fast_forward",
        "required_linear_history",
        "pull_request",
        "code_scanning",
        "code_quality",
        "copilot_code_review",
    ):
        if required_rule not in rule_map:
            drifts.append(f"missing rule: {required_rule}")

    pull_request_rule = rule_map.get("pull_request")
    if not isinstance(pull_request_rule, dict):
        return drifts

    parameters = pull_request_rule.get("parameters")
    if not isinstance(parameters, dict):
        drifts.append("pull_request rule parameters missing or invalid")
        return drifts

    expected_params = {
        "dismiss_stale_reviews_on_push": False,
        "require_code_owner_review": False,
        "require_last_push_approval": False,
        "required_approving_review_count": 0,
        "required_review_thread_resolution": True,
    }
    for key, expected in expected_params.items():
        actual = parameters.get(key)
        if actual != expected:
            drifts.append(f"pull_request.{key} expected {expected!r} got {actual!r}")

    actual_methods = parameters.get("allowed_merge_methods")
    if not isinstance(actual_methods, list) or sorted(actual_methods) != ["squash"]:
        drifts.append(
            f"pull_request.allowed_merge_methods expected ['squash'] got {actual_methods!r}"
        )

    code_scanning_rule = rule_map.get("code_scanning")
    if isinstance(code_scanning_rule, dict):
        code_scanning_parameters = code_scanning_rule.get("parameters")
        if not isinstance(code_scanning_parameters, dict):
            drifts.append("code_scanning rule parameters missing or invalid")
        else:
            tools = code_scanning_parameters.get("code_scanning_tools")
            expected_tools = [
                {
                    "tool": "CodeQL",
                    "alerts_threshold": "errors_and_warnings",
                    "security_alerts_threshold": "high_or_higher",
                }
            ]
            if tools != expected_tools:
                drifts.append(
                    "code_scanning.code_scanning_tools expected "
                    f"{expected_tools!r} got {tools!r}"
                )

    code_quality_rule = rule_map.get("code_quality")
    if isinstance(code_quality_rule, dict):
        code_quality_parameters = code_quality_rule.get("parameters")
        if not isinstance(code_quality_parameters, dict):
            drifts.append("code_quality rule parameters missing or invalid")
        else:
            tools = code_quality_parameters.get("code_quality_tools")
            expected_tools = [{"tool": "CodeQL", "severity": "notes"}]
            if tools != expected_tools:
                drifts.append(
                    "code_quality.code_quality_tools expected "
                    f"{expected_tools!r} got {tools!r}"
                )

    copilot_code_review_rule = rule_map.get("copilot_code_review")
    if isinstance(copilot_code_review_rule, dict):
        copilot_parameters = copilot_code_review_rule.get("parameters")
        if not isinstance(copilot_parameters, dict):
            drifts.append("copilot_code_review rule parameters missing or invalid")
        else:
            expected_copilot_params = {
                "review_draft_pull_requests": True,
                "review_on_push": True,
            }
            for key, expected in expected_copilot_params.items():
                actual = copilot_parameters.get(key)
                if actual != expected:
                    drifts.append(
                        f"copilot_code_review.{key} expected {expected!r} got {actual!r}"
                    )

    expected_contexts = list(_ALWAYS_REQUIRED_CONTEXTS)
    if languages:
        for ctx in dict.fromkeys(
            _LANGUAGE_CI_CONTEXT[lang]
            for lang in languages
            if lang in _LANGUAGE_CI_CONTEXT
        ):
            if ctx not in expected_contexts:
                expected_contexts.append(ctx)

    status_checks_rule = rule_map.get("required_status_checks")
    if not isinstance(status_checks_rule, dict):
        drifts.append("missing rule: required_status_checks")
    else:
        status_parameters = status_checks_rule.get("parameters")
        actual_contexts = (
            [
                item.get("context")
                for item in status_parameters.get("required_status_checks", [])
                if isinstance(item, dict)
            ]
            if isinstance(status_parameters, dict)
            else []
        )
        missing = [c for c in expected_contexts if c not in actual_contexts]
        if missing:
            drifts.append("required_status_checks missing contexts: " f"{missing!r}")

    return drifts


def _ensure_tools() -> None:
    if shutil.which("git") is None:
        raise RuntimeError("git is required.")


def _ensure_gh_auth(repo_dir: Path, env: dict[str, str]) -> None:
    token = _token_from_repo(repo_dir) or env.get("GH_TOKEN") or env.get("GITHUB_TOKEN")
    if token and not is_placeholder_token(token):
        return
    raise RuntimeError(
        "Authenticate first: set GH_TOKEN/GITHUB_TOKEN or github_token in .env."
    )


def _resolve_repo(
    *,
    repo_dir: Path,
    env: dict[str, str],
    repo: str | None,
    owner: str | None,
    name: str | None,
) -> str:
    if repo:
        candidate = repo.strip()
        if "/" not in candidate:
            raise RuntimeError("--repo must be in owner/repo format.")
        repo_owner, repo_name = candidate.split("/", 1)
        if not repo_owner.strip() or not repo_name.strip():
            raise RuntimeError(
                "--repo must be in owner/repo format with non-empty owner and repo. "
                "If you used shell vars, ensure they are set; or omit --repo to resolve from .env."
            )
        return candidate

    full_repo = env.get("GH_REPO") or env.get("GITHUB_REPOSITORY")
    owner_value = owner or env.get("GITHUB_ORG")
    name_value = name or env.get("GITHUB_REPO")

    if full_repo:
        parts = [part.strip() for part in full_repo.split("/") if part.strip()]
        if len(parts) >= 2:
            full_owner = parts[-2]
            full_name = parts[-1]
            if not owner_value:
                owner_value = full_owner
            if not name_value:
                name_value = full_name

    if not owner_value or not name_value:
        if not name_value:
            name_value = repo_dir.name
    if not owner_value or not name_value:
        raise RuntimeError(
            "Could not resolve target repository. Pass --repo owner/repo or set GH_REPO (or GITHUB_ORG + GITHUB_REPO)."
        )
    return f"{owner_value}/{name_value}"


def _is_git_repo_root(*, repo_dir: Path, env: dict[str, str]) -> bool:
    cp = _run(["git", "rev-parse", "--show-toplevel"], cwd=repo_dir, env=env)
    if cp.returncode != 0:
        return False
    top_level_raw = (cp.stdout or "").strip()
    if not top_level_raw:
        return False
    return Path(top_level_raw).resolve() == repo_dir.resolve()


def _ensure_git_repo(
    *,
    repo_dir: Path,
    env: dict[str, str],
    dry_run: bool,
    out: Callable[[str], None],
    stage_files: bool = False,
) -> None:
    is_repo_root = _is_git_repo_root(repo_dir=repo_dir, env=env)
    if not is_repo_root:
        out(f"{'[dry-run] ' if dry_run else ''}init git repo: {repo_dir}")
        if not dry_run:
            cp = _run(["git", "init"], cwd=repo_dir, env=env)
            if cp.returncode != 0:
                raise RuntimeError(
                    cp.stderr.strip() or "Failed to initialize git repository."
                )
            is_repo_root = True

    has_commit = False
    if is_repo_root:
        has_commit = (
            _run(
                ["git", "rev-parse", "--verify", "HEAD"], cwd=repo_dir, env=env
            ).returncode
            == 0
        )
    if not has_commit:
        out(f"{'[dry-run] ' if dry_run else ''}create initial commit")
        if not dry_run:
            user_name = _run(["git", "config", "user.name"], cwd=repo_dir, env=env)
            user_email = _run(["git", "config", "user.email"], cwd=repo_dir, env=env)
            if user_name.returncode != 0 or user_email.returncode != 0:
                raise RuntimeError(
                    "Git user.name and user.email are required for the initial commit. "
                    "Set them with: git config --global user.name 'Your Name' and "
                    "git config --global user.email 'you@example.com'."
                )
            if stage_files:
                add_cp = _run(["git", "add", "-A"], cwd=repo_dir, env=env)
                if add_cp.returncode != 0:
                    raise RuntimeError(
                        add_cp.stderr.strip()
                        or "Failed staging files for initial commit."
                    )
            commit_args = ["git", "commit"]
            if not stage_files:
                commit_args.append("--allow-empty")
            commit_args += ["-m", "Initial scaffold"]
            commit_cp = _run(commit_args, cwd=repo_dir, env=env)
            if commit_cp.returncode != 0:
                raise RuntimeError(
                    commit_cp.stderr.strip() or "Failed creating initial commit."
                )

    out(f"{'[dry-run] ' if dry_run else ''}prepare remote main push")


def _repo_exists(*, repo_dir: Path, env: dict[str, str], repo: str) -> bool:
    token = (
        _token_from_repo(repo_dir)
        or env.get("GH_TOKEN")
        or env.get("GITHUB_TOKEN")
        or ""
    )
    cp = _github_rest("GET", f"/repos/{repo}", token)
    if cp.returncode == 200 or cp.returncode == 0:
        return True
    if cp.returncode == 404:
        return False
    stderr = (cp.stderr or "").lower()
    not_found_markers = ("not found", "404")
    if any(marker in stderr for marker in not_found_markers):
        return False
    raise RuntimeError(cp.stderr.strip() or f"Failed checking repository: {repo}")


def _ensure_origin_remote(
    *,
    repo_dir: Path,
    env: dict[str, str],
    repo: str,
    dry_run: bool,
    out: Callable[[str], None],
) -> None:
    expected_https = f"https://github.com/{repo}.git"
    expected_ssh = f"git@github.com:{repo}.git"
    current = _run(["git", "remote", "get-url", "origin"], cwd=repo_dir, env=env)

    if current.returncode != 0:
        out(f"{'[dry-run] ' if dry_run else ''}add origin remote: {expected_https}")
        if not dry_run:
            cp = _run(
                ["git", "remote", "add", "origin", expected_https],
                cwd=repo_dir,
                env=env,
            )
            if cp.returncode != 0:
                raise RuntimeError(cp.stderr.strip() or "Failed adding origin remote.")
        return

    current_url = current.stdout.strip()
    if current_url in {expected_https, expected_ssh}:
        return

    out(f"{'[dry-run] ' if dry_run else ''}set origin remote: {expected_https}")
    if not dry_run:
        cp = _run(
            ["git", "remote", "set-url", "origin", expected_https],
            cwd=repo_dir,
            env=env,
        )
        if cp.returncode != 0:
            raise RuntimeError(cp.stderr.strip() or "Failed updating origin remote.")


def _resolve_push_token(*, repo_dir: Path, env: dict[str, str]) -> str | None:
    token = _token_from_repo(repo_dir) or env.get("GH_TOKEN") or env.get("GITHUB_TOKEN")
    return token or None


def _push_main(*, repo_dir: Path, env: dict[str, str]) -> tuple[bool, str | None]:
    push_env = dict(env)
    push_env["GIT_TERMINAL_PROMPT"] = "0"
    token = _resolve_push_token(repo_dir=repo_dir, env=env)

    if token:
        # Use an in-memory auth header so git push can run non-interactively.
        basic = base64.b64encode(f"x-access-token:{token}".encode("utf-8")).decode(
            "ascii"
        )
        cp = _run(
            [
                "git",
                "-c",
                "credential.helper=",
                "-c",
                f"http.https://github.com/.extraheader=AUTHORIZATION: basic {basic}",
                "push",
                "-u",
                "origin",
                "HEAD:main",
            ],
            cwd=repo_dir,
            env=push_env,
        )
    else:
        cp = _run(
            ["git", "push", "-u", "origin", "HEAD:main"], cwd=repo_dir, env=push_env
        )

    if cp.returncode == 0:
        return True, None

    stderr = (cp.stderr or "").strip()
    if not token and (
        "could not read username" in stderr.lower()
        or "authentication failed" in stderr.lower()
    ):
        return (
            False,
            (
                f"{stderr}\n"
                "Non-interactive auth failed. Set GH_TOKEN/GITHUB_TOKEN (or legacy github_token) in .env, "
                "or run `gh auth setup-git`."
            ),
        )
    return False, stderr or "Failed pushing main branch."


def _format_push_failure(raw: str) -> str:
    message = raw.strip() or "Failed pushing main branch."
    lowered = message.lower()
    if "workflow" in lowered and "scope" in lowered:
        return (
            f"{message}\n"
            "Token is missing workflow write permission. "
            "For classic PATs add `workflow`; for fine-grained PATs grant Workflows: Read and write. "
            "Then retry `repo-scaffold create`."
        )
    return message


def apply_repository_settings(
    *,
    repo_dir: Path,
    repo: str,
    dry_run: bool,
    out: Callable[[str], None] = print,
    warn: Callable[[str], None] | None = None,
    languages: list[str] | None = None,
) -> None:
    _ensure_tools()
    env = _build_env(repo_dir)
    if not dry_run:
        _ensure_gh_auth(repo_dir, env)
    emit_warn = warn if warn is not None else out
    _apply_settings(
        repo_dir=repo_dir.resolve(),
        env=env,
        repo=repo,
        dry_run=dry_run,
        out=out,
        warn=emit_warn,
        languages=languages,
    )


def check_repository_settings(
    *,
    repo_dir: Path,
    repo: str,
    out: Callable[[str], None] = print,
    languages: list[str] | None = None,
) -> SettingsCheckSummary:
    _ensure_tools()
    env = _build_env(repo_dir)
    _ensure_gh_auth(repo_dir, env)
    return _check_settings(
        repo_dir=repo_dir.resolve(), env=env, repo=repo, out=out, languages=languages
    )


def sync_repository_ruleset(
    *,
    repo_dir: Path,
    repo: str,
    languages: list[str] | None = None,
    out: Callable[[str], None] = print,
    warn: Callable[[str], None] | None = None,
) -> None:
    _ensure_tools()
    env = _build_env(repo_dir)
    _ensure_gh_auth(repo_dir, env)
    _sync_default_branch_ruleset(
        repo_dir=repo_dir.resolve(),
        env=env,
        repo=repo,
        languages=languages,
        out=out,
        warn=warn,
    )


def _create_or_push_repo(
    *,
    repo_dir: Path,
    env: dict[str, str],
    repo: str,
    visibility: str,
    dry_run: bool,
    out: Callable[[str], None],
) -> tuple[bool, bool, str | None]:
    repo_dir = repo_dir.resolve()
    exists = _repo_exists(repo_dir=repo_dir, env=env, repo=repo)

    if exists:
        out(f"Repository already exists: {repo}")
        _ensure_origin_remote(
            repo_dir=repo_dir, env=env, repo=repo, dry_run=dry_run, out=out
        )
        out(f"{'[dry-run] ' if dry_run else ''}push main to origin")
        if not dry_run:
            pushed, push_error = _push_main(repo_dir=repo_dir, env=env)
            if not pushed:
                return False, False, _format_push_failure(push_error or "")
        return False, True, None

    out(f"{'[dry-run] ' if dry_run else ''}create repository: {repo} ({visibility})")
    if not dry_run:
        token = (
            _token_from_repo(repo_dir)
            or env.get("GH_TOKEN")
            or env.get("GITHUB_TOKEN")
            or ""
        )
        owner, name = repo.split("/", 1)
        cp = _github_repo_create(owner, name, token, visibility=visibility)
        if cp.returncode not in (0, 201):
            return (
                False,
                False,
                cp.stderr.strip() or f"Failed creating repository: {repo}",
            )

        _ensure_origin_remote(
            repo_dir=repo_dir, env=env, repo=repo, dry_run=False, out=out
        )
        out("push main to origin")
        pushed, push_error = _push_main(repo_dir=repo_dir, env=env)
        if not pushed:
            return True, False, _format_push_failure(push_error or "")
    return True, True, None


def _apply_settings(
    *,
    repo_dir: Path,
    env: dict[str, str],
    repo: str,
    dry_run: bool,
    out: Callable[[str], None],
    warn: Callable[[str], None],
    languages: list[str] | None = None,
) -> None:
    out(f"{'[dry-run] ' if dry_run else ''}apply repository settings: {repo}")
    if dry_run:
        out("[dry-run] sync repository merge settings")
        out(f"[dry-run] create {_DEPENDABOT_YML_PATH} if not present")
        out(
            "[dry-run] sync managed default-branch ruleset "
            "(pull request required, 0 approvals, squash-only, no force-push, no delete, "
            "CodeQL merge protection, Copilot review on new pushes)"
        )
        out("[dry-run] remove legacy default-branch protection if present")
        out("[dry-run] enable secret scanning")
        out("[dry-run] enable secret scanning push protection")
        for feature_name, _ in _BEST_EFFORT_SECURITY_FEATURES:
            out(f"[dry-run] enable {feature_name.lower()}")
        out("[dry-run] enable code scanning default setup")
        out("[dry-run] enable private vulnerability reporting when supported")
        return

    repo_info = _get_repo_info(repo_dir=repo_dir, env=env, repo=repo)
    default_branch = str(repo_info.get("default_branch") or "main")
    visibility = str(repo_info.get("visibility") or "")

    patch_cp = _api(
        repo_dir=repo_dir,
        env=env,
        method="PATCH",
        endpoint=f"/repos/{repo}",
        stdin_text=_repo_patch_payload(),
    )
    if patch_cp.returncode != 0:
        raise RuntimeError(
            patch_cp.stderr.strip() or "Failed applying repository merge settings."
        )
    out("Applied repository merge settings.")

    _ensure_dependabot_version_updates(
        repo_dir=repo_dir,
        env=env,
        repo=repo,
        languages=languages,
        out=out,
        warn=warn,
    )

    _sync_default_branch_ruleset(
        repo_dir=repo_dir, env=env, repo=repo, out=out, warn=warn, languages=languages
    )

    _clear_legacy_branch_protection(
        repo_dir=repo_dir,
        env=env,
        repo=repo,
        default_branch=default_branch,
        out=out,
    )

    _enable_security_and_analysis_feature(
        repo_dir=repo_dir,
        env=env,
        repo=repo,
        feature_name="Secret scanning",
        feature_key="secret_scanning",
        out=out,
        warn=warn,
    )
    _enable_security_and_analysis_feature(
        repo_dir=repo_dir,
        env=env,
        repo=repo,
        feature_name="Secret scanning push protection",
        feature_key="secret_scanning_push_protection",
        out=out,
        warn=warn,
    )

    for feature_name, endpoint_template in _BEST_EFFORT_SECURITY_FEATURES:
        endpoint = endpoint_template.format(repo=repo)
        _enable_optional_endpoint_feature(
            repo_dir=repo_dir,
            env=env,
            endpoint=endpoint,
            feature_name=feature_name,
            out=out,
            warn=warn,
        )

    _enable_code_scanning_default_setup(
        repo_dir=repo_dir,
        env=env,
        repo=repo,
        out=out,
        warn=warn,
    )

    if visibility == "public":
        _enable_optional_endpoint_feature(
            repo_dir=repo_dir,
            env=env,
            endpoint=f"/repos/{repo}/private-vulnerability-reporting",
            feature_name="Private vulnerability reporting",
            out=out,
            warn=warn,
        )


def _check_settings(
    *,
    repo_dir: Path,
    env: dict[str, str],
    repo: str,
    out: Callable[[str], None],
    languages: list[str] | None = None,
) -> SettingsCheckSummary:
    out(f"check repository settings: {repo}")
    repo_info = _get_repo_info(repo_dir=repo_dir, env=env, repo=repo)
    default_branch = str(repo_info.get("default_branch") or "main")
    visibility = str(repo_info.get("visibility") or "")

    passed = 0
    failed = 0
    skipped = 0
    drifts: list[str] = []

    expected_repo_settings = json.loads(_repo_patch_payload())
    merge_drifts: list[str] = []
    for key, expected in expected_repo_settings.items():
        actual = repo_info.get(key)
        if actual != expected:
            merge_drifts.append(f"{key} expected {expected!r} got {actual!r}")
    if merge_drifts:
        failed += 1
        for detail in merge_drifts:
            drifts.append(f"merge settings: {detail}")
            out(f"DRIFT merge settings: {detail}")
    else:
        passed += 1
        out("PASS  merge settings")

    rulesets = _list_repo_rulesets(repo_dir=repo_dir, env=env, repo=repo)
    detailed_rulesets: list[dict[str, object]] = []
    for ruleset in rulesets:
        if _is_managed_ruleset_name(ruleset.get("name")) and (
            not isinstance(ruleset.get("conditions"), dict)
            or not isinstance(ruleset.get("rules"), list)
        ):
            ruleset_id = ruleset.get("id")
            if isinstance(ruleset_id, int):
                detailed_rulesets.append(
                    _get_repo_ruleset(
                        repo_dir=repo_dir,
                        env=env,
                        repo=repo,
                        ruleset_id=ruleset_id,
                    )
                )
                continue
        detailed_rulesets.append(ruleset)

    ruleset_drifts = _compare_ruleset_against_baseline(
        detailed_rulesets,
        default_branch=default_branch,
        languages=languages,
    )
    if ruleset_drifts:
        failed += 1
        for detail in ruleset_drifts:
            drifts.append(f"managed default-branch ruleset: {detail}")
            out(f"DRIFT managed default-branch ruleset: {detail}")
    else:
        passed += 1
        out("PASS  managed default-branch ruleset")

    legacy_protection_exists = _legacy_branch_protection_exists(
        repo_dir=repo_dir,
        env=env,
        repo=repo,
        default_branch=default_branch,
    )
    if legacy_protection_exists:
        failed += 1
        detail = f"legacy branch protection still present on {default_branch}"
        drifts.append(detail)
        out(f"DRIFT legacy branch protection: still present on {default_branch}")
    else:
        passed += 1
        out("PASS  legacy branch protection cleared")

    for label, feature_key in (
        ("secret scanning", "secret_scanning"),
        ("secret scanning push protection", "secret_scanning_push_protection"),
    ):
        status = _security_feature_status(repo_info, feature_key)
        if status == "enabled":
            passed += 1
            out(f"PASS  {label}")
        else:
            failed += 1
            detail = f"{label} expected 'enabled' got {status!r}"
            drifts.append(detail)
            out(f"DRIFT {label}: expected 'enabled' got {status!r}")

    for label, endpoint in (
        ("dependabot alerts", "/repos/{repo}/vulnerability-alerts"),
        ("dependabot security updates", "/repos/{repo}/automated-security-fixes"),
    ):
        enabled, detail = _optional_endpoint_feature_enabled(
            repo_dir=repo_dir,
            env=env,
            endpoint=endpoint.format(repo=repo),
        )
        if enabled:
            passed += 1
            out(f"PASS  {label}")
        else:
            failed += 1
            drift = f"{label} expected enabled got {detail}"
            drifts.append(drift)
            out(f"DRIFT {label}: {detail}")

    if visibility == "public":
        enabled, detail = _optional_endpoint_feature_enabled(
            repo_dir=repo_dir,
            env=env,
            endpoint=f"/repos/{repo}/private-vulnerability-reporting",
        )
        if enabled:
            passed += 1
            out("PASS  private vulnerability reporting")
        else:
            failed += 1
            drift = f"private vulnerability reporting expected enabled got {detail}"
            drifts.append(drift)
            out(f"DRIFT private vulnerability reporting: {detail}")
    else:
        skipped += 1
        out("SKIP  private vulnerability reporting (repo is not public)")

    # Dependabot malware alerts have no separate REST endpoint -- they are bundled
    # with Dependabot alerts (checked above via /vulnerability-alerts).
    enabled, _ = _optional_endpoint_feature_enabled(
        repo_dir=repo_dir,
        env=env,
        endpoint=f"/repos/{repo}/contents/{_DEPENDABOT_YML_PATH}",
    )
    if enabled:
        passed += 1
        out("PASS  dependabot version updates")
    else:
        failed += 1
        drift = f"dependabot version updates: {_DEPENDABOT_YML_PATH} not configured"
        drifts.append(drift)
        out(f"DRIFT dependabot version updates: {_DEPENDABOT_YML_PATH} not configured")

    return SettingsCheckSummary(
        repo=repo,
        passed=passed,
        failed=failed,
        skipped=skipped,
        drifts=tuple(drifts),
    )


def create_repository(
    *,
    repo_dir: Path,
    repo: str | None,
    owner: str | None,
    name: str | None,
    visibility: str,
    apply_settings: bool,
    dry_run: bool,
    stage_files: bool = False,
    out: Callable[[str], None] = print,
    err: Callable[[str], None] | None = None,
) -> CreateSummary:
    repo_dir = repo_dir.resolve()
    emit_err = err if err is not None else out
    _ensure_tools()
    env = _build_env(repo_dir)
    _ensure_gh_auth(repo_dir, env)

    if visibility not in {"private", "public", "internal"}:
        raise RuntimeError("Visibility must be one of: private, public, internal.")

    target_repo = _resolve_repo(
        repo_dir=repo_dir, env=env, repo=repo, owner=owner, name=name
    )

    repo_created = False
    pushed = False
    settings_applied = False
    failures = 0

    try:
        _ensure_git_repo(
            repo_dir=repo_dir,
            env=env,
            dry_run=dry_run,
            out=out,
            stage_files=stage_files,
        )
        repo_created, pushed, push_or_create_error = _create_or_push_repo(
            repo_dir=repo_dir,
            env=env,
            repo=target_repo,
            visibility=visibility,
            dry_run=dry_run,
            out=out,
        )
        if push_or_create_error:
            failures += 1
            emit_err(push_or_create_error)
        if apply_settings and pushed and failures == 0:
            _apply_settings(
                repo_dir=repo_dir,
                env=env,
                repo=target_repo,
                dry_run=dry_run,
                out=out,
                warn=emit_err,
            )
            settings_applied = True
        elif apply_settings and not pushed:
            out("Skipping settings apply until main branch is pushed.")
    except RuntimeError as exc:
        failures += 1
        emit_err(str(exc))

    return CreateSummary(
        repo=target_repo,
        repo_created=repo_created,
        pushed=pushed,
        settings_applied=settings_applied,
        failures=failures,
    )
