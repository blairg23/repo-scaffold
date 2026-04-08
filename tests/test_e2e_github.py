from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from repo_scaffold.backlog_ops import apply_backlog
from repo_scaffold.cli import main
from repo_scaffold.create_ops import create_repository


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


def _seed_env_from_dotenv(project_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_values = _load_env_file(project_root / ".env")
    for key, value in env_values.items():
        if not os.environ.get(key):
            monkeypatch.setenv(key, value)

    if not os.environ.get("GH_TOKEN"):
        if os.environ.get("GITHUB_TOKEN"):
            monkeypatch.setenv("GH_TOKEN", os.environ["GITHUB_TOKEN"])
        elif os.environ.get("github_token"):
            monkeypatch.setenv("GH_TOKEN", os.environ["github_token"])

    if not os.environ.get("GITHUB_ORG") and os.environ.get("github_org"):
        monkeypatch.setenv("GITHUB_ORG", os.environ["github_org"])
    if not os.environ.get("GITHUB_REPO") and os.environ.get("github_repo"):
        monkeypatch.setenv("GITHUB_REPO", os.environ["github_repo"])


def _run_gh(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["gh", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )


def _run_gh_json(args: list[str], *, cwd: Path) -> object:
    cp = _run_gh(args, cwd=cwd)
    assert cp.returncode == 0, cp.stderr.strip() or cp.stdout.strip()
    return json.loads(cp.stdout)


@pytest.mark.e2e_github
def test_real_world_github_e2e(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project_root = Path(__file__).resolve().parents[1]
    _seed_env_from_dotenv(project_root, monkeypatch)

    if os.environ.get("RUN_GITHUB_E2E") != "1":
        pytest.skip("Set RUN_GITHUB_E2E=1 to run real GitHub E2E.")
    if shutil.which("gh") is None:
        pytest.skip("GitHub CLI (gh) is not installed.")

    owner = os.environ.get("GITHUB_ORG")
    assert owner, "Set GITHUB_ORG in env/.env."

    base_name = (
        os.environ.get("GITHUB_E2E_REPO_BASENAME")
        or os.environ.get("GITHUB_REPO")
        or "repo-scaffold-e2e"
    )
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    suffix = uuid.uuid4().hex[:8]
    repo_name = f"{base_name}-{timestamp}-{suffix}"
    target_repo = f"{owner}/{repo_name}"
    visibility = os.environ.get("GITHUB_E2E_VISIBILITY", "public")
    keep_repo = os.environ.get("GITHUB_E2E_KEEP_REPO") == "1"
    skip_settings_asserts = os.environ.get("GITHUB_E2E_SKIP_SETTINGS_ASSERTS") == "1"

    local_repo = tmp_path / repo_name
    backlog_file = local_repo / "backlog" / "issues.json"

    token_present = bool(
        os.environ.get("GH_TOKEN")
        or os.environ.get("GITHUB_TOKEN")
        or os.environ.get("github_token")
    )
    if token_present:
        auth = _run_gh(["api", "/user"], cwd=project_root)
        assert auth.returncode == 0, (
            "GitHub token auth failed. Ensure GH_TOKEN/GITHUB_TOKEN is valid.\n"
            f"{auth.stderr.strip() or auth.stdout.strip()}"
        )
    else:
        auth = _run_gh(["auth", "status"], cwd=project_root)
        assert auth.returncode == 0, (
            "gh auth is not ready. Run `gh auth login` (or export GH_TOKEN).\n"
            f"{auth.stderr.strip() or auth.stdout.strip()}"
        )

    created_remote = False
    try:
        rc = main(
            [
                "init",
                "--name",
                repo_name,
                "--languages",
                "go,python,react",
                "--out",
                str(local_repo),
                "--yes",
            ]
        )
        assert rc == 0

        git_name = subprocess.run(
            ["git", "config", "user.name"],
            cwd=local_repo,
            text=True,
            capture_output=True,
            check=False,
        )
        if git_name.returncode != 0:
            subprocess.run(
                ["git", "config", "user.name", "repo-scaffold-e2e"],
                cwd=local_repo,
                check=True,
                text=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.email", "repo-scaffold-e2e@example.com"],
                cwd=local_repo,
                check=True,
                text=True,
                capture_output=True,
            )

        dry_create = create_repository(
            repo_dir=local_repo,
            repo=target_repo,
            owner=None,
            name=None,
            visibility=visibility,
            apply_settings=True,
            dry_run=True,
            out=lambda _: None,
            err=lambda _: None,
        )
        assert dry_create.failures == 0
        assert dry_create.repo == target_repo
        assert dry_create.pushed is True
        assert dry_create.settings_applied is True

        create_summary = create_repository(
            repo_dir=local_repo,
            repo=target_repo,
            owner=None,
            name=None,
            visibility=visibility,
            apply_settings=True,
            dry_run=False,
            out=lambda _: None,
            err=lambda _: None,
        )
        assert create_summary.failures == 0
        assert create_summary.repo == target_repo
        assert create_summary.pushed is True
        assert create_summary.settings_applied is True
        created_remote = create_summary.repo_created

        repo_view = _run_gh_json(
            [
                "repo",
                "view",
                target_repo,
                "--json",
                "nameWithOwner,defaultBranchRef,visibility",
            ],
            cwd=local_repo,
        )
        assert isinstance(repo_view, dict)
        assert str(repo_view["nameWithOwner"]).lower() == target_repo.lower()
        assert isinstance(repo_view.get("defaultBranchRef"), dict)
        assert repo_view["defaultBranchRef"]["name"] == "main"

        if not skip_settings_asserts:
            repo_api = _run_gh_json(["api", f"/repos/{target_repo}"], cwd=local_repo)
            assert isinstance(repo_api, dict)
            assert repo_api["allow_squash_merge"] is True
            assert repo_api["allow_merge_commit"] is False
            assert repo_api["allow_rebase_merge"] is False
            assert repo_api["delete_branch_on_merge"] is True

            active_rules = _run_gh_json(
                ["api", f"/repos/{target_repo}/rules/branches/main"],
                cwd=local_repo,
            )
            assert isinstance(active_rules, list)
            pull_request_rule = next(
                (
                    rule
                    for rule in active_rules
                    if isinstance(rule, dict) and rule.get("type") == "pull_request"
                ),
                None,
            )
            assert isinstance(pull_request_rule, dict)
            assert pull_request_rule["parameters"]["required_approving_review_count"] == 0
            assert (
                pull_request_rule["parameters"]["required_review_thread_resolution"]
                is True
            )
            assert pull_request_rule["parameters"]["allowed_merge_methods"] == [
                "squash"
            ]
            assert any(
                isinstance(rule, dict) and rule.get("type") == "required_linear_history"
                for rule in active_rules
            )
            assert any(
                isinstance(rule, dict) and rule.get("type") == "non_fast_forward"
                for rule in active_rules
            )
            assert any(
                isinstance(rule, dict) and rule.get("type") == "deletion"
                for rule in active_rules
            )

        epic_title = f"[E2E] Epic - {timestamp}-{suffix}"
        ticket_title = f"[E2E] Ticket - {timestamp}-{suffix}"
        backlog_data = {
            "epics": [
                {
                    "key": "E2E",
                    "title": epic_title,
                    "body": "## Summary\nValidate repo-scaffold end-to-end against GitHub.\n",
                    "labels": ["epic"],
                    "tickets": [
                        {
                            "title": ticket_title,
                            "body": "## Summary\nCreate one real ticket linked to its epic milestone.\n",
                            "labels": ["ticket", "epic:E2E"],
                            "assignees": [],
                            "priority": "P1",
                        }
                    ],
                }
            ]
        }
        backlog_file.write_text(json.dumps(backlog_data, indent=2), encoding="utf-8")

        dry_backlog_logs: list[str] = []
        dry_backlog_errors: list[str] = []
        dry_backlog = apply_backlog(
            repo_dir=local_repo,
            repo=target_repo,
            backlog_file=backlog_file,
            dry_run=True,
            out=lambda line: dry_backlog_logs.append(line),
            err=lambda line: dry_backlog_errors.append(line),
        )
        assert dry_backlog.failures == 0, "\n".join(dry_backlog_errors)
        assert dry_backlog.milestones_created == 1
        assert dry_backlog.issues_created == 2

        apply_once_logs: list[str] = []
        apply_once_errors: list[str] = []
        apply_once = apply_backlog(
            repo_dir=local_repo,
            repo=target_repo,
            backlog_file=backlog_file,
            dry_run=False,
            out=lambda line: apply_once_logs.append(line),
            err=lambda line: apply_once_errors.append(line),
        )
        assert apply_once.failures == 0, "\n".join(apply_once_errors)
        assert apply_once.milestones_created == 1
        assert apply_once.issues_created == 2

        apply_twice_logs: list[str] = []
        apply_twice_errors: list[str] = []
        apply_twice = apply_backlog(
            repo_dir=local_repo,
            repo=target_repo,
            backlog_file=backlog_file,
            dry_run=False,
            out=lambda line: apply_twice_logs.append(line),
            err=lambda line: apply_twice_errors.append(line),
        )
        assert apply_twice.failures == 0, "\n".join(apply_twice_errors)
        assert apply_twice.milestones_skipped >= 1
        assert apply_twice.issues_skipped >= 2

        issues = _run_gh_json(
            [
                "issue",
                "list",
                "--repo",
                target_repo,
                "--state",
                "all",
                "--limit",
                "100",
                "--search",
                f"{ticket_title} in:title",
                "--json",
                "title,milestone,body",
            ],
            cwd=local_repo,
        )
        assert isinstance(issues, list)
        match = next(
            (
                item
                for item in issues
                if isinstance(item, dict) and item.get("title") == ticket_title
            ),
            None,
        )
        assert isinstance(match, dict), f"Expected ticket not found: {ticket_title}"
        milestone = match.get("milestone")
        assert isinstance(milestone, dict)
        assert milestone.get("title") == epic_title
        body = match.get("body")
        assert isinstance(body, str)
        assert body.startswith("Epic: #")
    finally:
        if created_remote and not keep_repo:
            delete_cp = _run_gh(
                ["repo", "delete", target_repo, "--yes"], cwd=project_root
            )
            if delete_cp.returncode != 0:
                print(
                    "WARNING: cleanup could not delete E2E repo. "
                    f"Repository: {target_repo}\n"
                    f"{delete_cp.stderr.strip() or delete_cp.stdout.strip()}",
                    file=sys.stderr,
                )
