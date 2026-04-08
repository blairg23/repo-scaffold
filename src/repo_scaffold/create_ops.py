from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class CreateSummary:
    repo: str
    repo_created: bool
    pushed: bool
    settings_applied: bool
    failures: int


_API_VERSION = "2026-03-10"
_SETTINGS_RULESET_NAME = "repo-scaffold default-branch ruleset"

_BEST_EFFORT_SECURITY_FEATURES: tuple[tuple[str, str], ...] = (
    ("Dependabot alerts", "/repos/{repo}/vulnerability-alerts"),
    ("Dependabot security updates", "/repos/{repo}/automated-security-fixes"),
)


def _api_headers() -> list[str]:
    return [
        "-H",
        "Accept: application/vnd.github+json",
        "-H",
        f"X-GitHub-Api-Version: {_API_VERSION}",
    ]


def _repo_patch_payload() -> str:
    return json.dumps(
        {
            "allow_squash_merge": True,
            "allow_merge_commit": False,
            "allow_rebase_merge": False,
            "delete_branch_on_merge": True,
            "allow_auto_merge": True,
            "is_template": False,
        },
        indent=2,
    )


def _default_branch_ruleset_payload() -> str:
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
            ],
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

    if not env.get("GH_TOKEN"):
        if env.get("GITHUB_TOKEN"):
            env["GH_TOKEN"] = env["GITHUB_TOKEN"]
        elif env.get("github_token"):
            env["GH_TOKEN"] = env["github_token"]

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
    args = ["gh", "api", "--method", method, *_api_headers(), endpoint]
    if stdin_text is not None:
        args.extend(["--input", "-"])
    return _run(args, cwd=repo_dir, env=env, stdin_text=stdin_text)


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
        endpoint=f"/repos/{repo}/branches/{default_branch}/protection",
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
) -> None:
    managed_rulesets = [
        item
        for item in _list_repo_rulesets(repo_dir=repo_dir, env=env, repo=repo)
        if item.get("name") == _SETTINGS_RULESET_NAME
    ]
    payload = _default_branch_ruleset_payload()

    if managed_rulesets:
        ruleset_id = managed_rulesets[0].get("id")
        if not isinstance(ruleset_id, int):
            raise RuntimeError("Managed ruleset exists but is missing a numeric id.")
        cp = _api(
            repo_dir=repo_dir,
            env=env,
            method="PUT",
            endpoint=f"/repos/{repo}/rulesets/{ruleset_id}",
            stdin_text=payload,
        )
        if cp.returncode != 0:
            raise RuntimeError(cp.stderr.strip() or "Failed updating managed ruleset.")
        out(f"Updated ruleset '{_SETTINGS_RULESET_NAME}'.")
        return

    cp = _api(
        repo_dir=repo_dir,
        env=env,
        method="POST",
        endpoint=f"/repos/{repo}/rulesets",
        stdin_text=payload,
    )
    if cp.returncode != 0:
        raise RuntimeError(cp.stderr.strip() or "Failed creating managed ruleset.")
    out(f"Created ruleset '{_SETTINGS_RULESET_NAME}'.")


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


def _ensure_tools() -> None:
    if shutil.which("gh") is None:
        raise RuntimeError("GitHub CLI (gh) is required.")
    if shutil.which("git") is None:
        raise RuntimeError("git is required.")


def _ensure_gh_auth(repo_dir: Path, env: dict[str, str]) -> None:
    if env.get("GH_TOKEN"):
        return

    cp = _run(["gh", "auth", "status"], cwd=repo_dir, env=env)
    if cp.returncode != 0:
        raise RuntimeError(
            "Authenticate first: gh auth login (or set GH_TOKEN/GITHUB_TOKEN; legacy alias github_token is supported)."
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
            add_cp = _run(["git", "add", "-A"], cwd=repo_dir, env=env)
            if add_cp.returncode != 0:
                raise RuntimeError(
                    add_cp.stderr.strip() or "Failed staging files for initial commit."
                )
            staged = (
                _run(
                    ["git", "diff", "--cached", "--quiet"], cwd=repo_dir, env=env
                ).returncode
                != 0
            )
            commit_cmd = ["git", "commit", "-m", "Initial scaffold"]
            if not staged:
                commit_cmd = [
                    "git",
                    "commit",
                    "--allow-empty",
                    "-m",
                    "Initial scaffold",
                ]
            commit_cp = _run(commit_cmd, cwd=repo_dir, env=env)
            if commit_cp.returncode != 0:
                raise RuntimeError(
                    commit_cp.stderr.strip() or "Failed creating initial commit."
                )

    out(f"{'[dry-run] ' if dry_run else ''}prepare remote main push")


def _repo_exists(*, repo_dir: Path, env: dict[str, str], repo: str) -> bool:
    cp = _run(
        ["gh", "repo", "view", repo, "--json", "nameWithOwner"], cwd=repo_dir, env=env
    )
    if cp.returncode == 0:
        return True

    stderr = (cp.stderr or "").lower()
    not_found_markers = ("not found", "http 404", "could not resolve to a repository")
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
    token = env.get("GH_TOKEN")
    if token:
        return token

    cp = _run(["gh", "auth", "token"], cwd=repo_dir, env=env)
    if cp.returncode != 0:
        return None
    resolved = cp.stdout.strip()
    return resolved or None


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
        cp = _run(
            [
                "gh",
                "repo",
                "create",
                repo,
                f"--{visibility}",
                "--source",
                str(repo_dir),
                "--remote",
                "origin",
            ],
            cwd=repo_dir,
            env=env,
        )
        if cp.returncode != 0:
            return (
                False,
                False,
                cp.stderr.strip() or f"Failed creating repository: {repo}",
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
) -> None:
    out(f"{'[dry-run] ' if dry_run else ''}apply repository settings: {repo}")
    if dry_run:
        out("[dry-run] sync repository merge settings")
        out("[dry-run] remove legacy default-branch protection if present")
        out(
            "[dry-run] sync managed default-branch ruleset "
            "(pull request required, 0 approvals, squash-only, no force-push, no delete)"
        )
        out("[dry-run] enable secret scanning")
        out("[dry-run] enable secret scanning push protection")
        for feature_name, _ in _BEST_EFFORT_SECURITY_FEATURES:
            out(f"[dry-run] enable {feature_name.lower()}")
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

    _clear_legacy_branch_protection(
        repo_dir=repo_dir,
        env=env,
        repo=repo,
        default_branch=default_branch,
        out=out,
    )

    _sync_default_branch_ruleset(repo_dir=repo_dir, env=env, repo=repo, out=out)

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

    if visibility == "public":
        _enable_optional_endpoint_feature(
            repo_dir=repo_dir,
            env=env,
            endpoint=f"/repos/{repo}/private-vulnerability-reporting",
            feature_name="Private vulnerability reporting",
            out=out,
            warn=warn,
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
        _ensure_git_repo(repo_dir=repo_dir, env=env, dry_run=dry_run, out=out)
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
