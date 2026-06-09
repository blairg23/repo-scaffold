from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .auth_tokens import is_placeholder_token, resolve_gh_token
from .github_api import (
    issue_node_id,
    link_project_to_repository,
    project_create,
    project_delete,
    project_edit,
    project_item_add,
    project_item_delete,
    project_item_list,
    project_list,
    project_view,
    rest,
    rest_paginated,
    token_from_repo,
)
from .project_metadata import write_project_metadata


@dataclass(frozen=True)
class IssueDetail:
    number: int
    title: str
    body: str
    state: str
    labels: list[str] = field(default_factory=list)
    assignees: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class BacklogApplySummary:
    milestones_created: int
    milestones_skipped: int
    issues_created: int
    issues_skipped: int
    failures: int
    epic_issues_created: int = 0
    epic_issues_skipped: int = 0
    ticket_issues_created: int = 0
    ticket_issues_skipped: int = 0
    project_created: bool = False
    project_items_added: int = 0
    project_items_skipped: int = 0


@dataclass(frozen=True)
class _ProjectTarget:
    owner: str
    number: int | None
    title: str
    created: bool


def resolve_authenticated_login(repo_dir: Path) -> str:
    _ensure_gh_auth(repo_dir)

    cp = _run_gh(repo_dir, ["api", "/user"])
    if cp.returncode != 0:
        raise RuntimeError(
            cp.stderr.strip()
            or "GitHub auth check failed. Ensure GH_TOKEN/GITHUB_TOKEN is valid, or run gh auth login."
        )

    try:
        payload = json.loads(cp.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError("GitHub auth check returned unexpected response.") from exc

    login = payload.get("login")
    if not isinstance(login, str) or not login.strip():
        raise RuntimeError(
            "GitHub auth check failed: could not resolve authenticated user login."
        )
    return login.strip()


def resolve_project_target_for_auth_check(
    *,
    repo_dir: Path,
    repo: str,
    project_number: int | None,
    project_title: str | None,
    project_owner: str | None,
) -> str | None:
    target = _resolve_project_target(
        repo_dir=repo_dir,
        repo=repo,
        project_number=project_number,
        project_title=project_title,
        project_owner=project_owner,
        dry_run=True,
        out=lambda _line: None,
    )
    if target is None:
        return None
    if target.number is not None:
        return f"{target.owner}/#{target.number} ({target.title})"
    return f"{target.owner}/{target.title} (will be created)"


def _project_scope_hint(stderr: str) -> str:
    lowered = stderr.lower()
    if "project scope" in lowered or "missing required token scopes" in lowered:
        return (
            "GitHub Projects access requires `project` scope. "
            "Run: gh auth refresh -h github.com -s project (or use a token with project access)."
        )
    return stderr


def _parse_repo_owner(repo: str) -> str:
    if "/" not in repo:
        raise RuntimeError(f"Invalid repo format: {repo}. Expected owner/repo.")
    return repo.split("/", 1)[0]


def _list_projects(
    repo_dir: Path, owner: str, *, include_closed: bool = False
) -> list[dict[str, object]]:
    args = ["project", "list", "--owner", owner, "--limit", "100"]
    if include_closed:
        args.append("--closed")
    args.extend(["--format", "json"])
    cp = _run_gh(
        repo_dir,
        args,
    )
    if cp.returncode != 0:
        detail = cp.stderr.strip() or cp.stdout.strip() or "Failed listing projects."
        raise RuntimeError(_project_scope_hint(detail))
    data = json.loads(cp.stdout or "[]")
    projects_obj: object
    if isinstance(data, list):
        projects_obj = data
    elif isinstance(data, dict):
        projects_obj = data.get("projects")
        if not isinstance(projects_obj, list):
            projects_obj = data.get("items")
        if not isinstance(projects_obj, list):
            projects_obj = data.get("nodes")
        if not isinstance(projects_obj, list):
            raise RuntimeError("Unexpected response from gh project list.")
    else:
        raise RuntimeError("Unexpected response from gh project list.")

    return [item for item in projects_obj if isinstance(item, dict)]


def _project_title_for_number(repo_dir: Path, owner: str, number: int) -> str:
    cp = _run_gh(
        repo_dir, ["project", "view", str(number), "--owner", owner, "--format", "json"]
    )
    if cp.returncode != 0:
        detail = (
            cp.stderr.strip()
            or cp.stdout.strip()
            or f"Failed viewing project #{number}."
        )
        raise RuntimeError(_project_scope_hint(detail))
    try:
        payload = json.loads(cp.stdout or "{}")
    except json.JSONDecodeError:
        return f"Project #{number}"
    title = payload.get("title")
    return (
        title.strip()
        if isinstance(title, str) and title.strip()
        else f"Project #{number}"
    )


def _resolve_project_target(
    *,
    repo_dir: Path,
    repo: str,
    project_number: int | None,
    project_title: str | None,
    project_owner: str | None,
    dry_run: bool,
    out: Callable[[str], None],
) -> _ProjectTarget | None:
    if project_number is None and not project_title:
        return None
    if project_number is not None and project_title:
        raise RuntimeError("Use only one of --project-number or --project-title.")

    owner = (project_owner or _parse_repo_owner(repo)).strip()
    if not owner:
        raise RuntimeError("Project owner could not be resolved.")

    if project_number is not None:
        title = _project_title_for_number(repo_dir, owner, project_number)
        out(f"Using project: {owner}/#{project_number} ({title})")
        return _ProjectTarget(
            owner=owner, number=project_number, title=title, created=False
        )

    assert project_title is not None
    wanted_title = project_title.strip()
    if not wanted_title:
        raise RuntimeError("--project-title must not be empty.")

    for item in _list_projects(repo_dir, owner):
        item_title = item.get("title")
        item_number = item.get("number")
        if (
            isinstance(item_title, str)
            and isinstance(item_number, int)
            and item_title == wanted_title
        ):
            out(f"Using project: {owner}/#{item_number} ({wanted_title})")
            return _ProjectTarget(
                owner=owner, number=item_number, title=wanted_title, created=False
            )

    if dry_run:
        out(f"[dry-run] create project: {wanted_title} (owner: {owner})")
        return _ProjectTarget(
            owner=owner, number=None, title=wanted_title, created=True
        )

    cp = _run_gh(
        repo_dir,
        [
            "project",
            "create",
            "--owner",
            owner,
            "--title",
            wanted_title,
            "--format",
            "json",
        ],
    )
    if cp.returncode != 0:
        detail = (
            cp.stderr.strip()
            or cp.stdout.strip()
            or f"Failed creating project: {wanted_title}"
        )
        raise RuntimeError(_project_scope_hint(detail))
    payload = json.loads(cp.stdout or "{}")
    number = payload.get("number")
    if not isinstance(number, int):
        for item in _list_projects(repo_dir, owner):
            item_title = item.get("title")
            num = item.get("number")
            if (
                isinstance(item_title, str)
                and isinstance(num, int)
                and item_title == wanted_title
            ):
                number = num
                break
    if not isinstance(number, int):
        raise RuntimeError(
            f"Created project '{wanted_title}' but could not resolve project number."
        )

    out(f"Created project: {owner}/#{number} ({wanted_title})")
    return _ProjectTarget(owner=owner, number=number, title=wanted_title, created=True)


def _link_project_to_repo(
    *,
    repo_dir: Path,
    repo: str,
    project: _ProjectTarget,
    dry_run: bool,
    out: Callable[[str], None],
    emit_err: Callable[[str], None],
) -> int:
    if project.number is None:
        if dry_run:
            out(f"[dry-run] link project to repo: {project.title} -> {repo}")
        return 0

    if dry_run:
        out(
            f"[dry-run] link project to repo: {project.owner}/#{project.number} -> {repo}"
        )
        return 0

    cp = _run_gh(
        repo_dir,
        [
            "project",
            "link",
            str(project.number),
            "--owner",
            project.owner,
            "--repo",
            repo,
        ],
    )
    if cp.returncode == 0:
        out(f"Linked project to repo: {project.owner}/#{project.number} -> {repo}")
        return 0

    stderr = (cp.stderr or "").lower()
    if "already" in stderr and "link" in stderr:
        out(f"Project already linked to repo: {project.owner}/#{project.number}")
        return 0

    detail = (
        cp.stderr.strip()
        or cp.stdout.strip()
        or f"Failed linking project #{project.number} to repo."
    )
    emit_err(_project_scope_hint(detail))
    return 1


def _add_issue_to_project(
    *,
    repo_dir: Path,
    repo: str,
    project: _ProjectTarget,
    issue_title: str,
    issue_number: int | None,
    dry_run: bool,
    out: Callable[[str], None],
    emit_err: Callable[[str], None],
) -> tuple[int, int, int]:
    if project.number is None:
        if dry_run:
            out(f"[dry-run] add issue to project: {issue_title} -> {project.title}")
            return (1, 0, 0)
        return (0, 0, 1)

    if dry_run:
        out(
            f"[dry-run] add issue to project: {issue_title} -> {project.owner}/#{project.number}"
        )
        return (1, 0, 0)

    if issue_number is None:
        emit_err(
            f"Failed to add issue to project (missing issue number): {issue_title}"
        )
        return (0, 0, 1)

    issue_url = f"https://github.com/{repo}/issues/{issue_number}"
    cp = _run_gh(
        repo_dir,
        [
            "project",
            "item-add",
            str(project.number),
            "--owner",
            project.owner,
            "--url",
            issue_url,
        ],
    )
    if cp.returncode == 0:
        out(f"Added issue to project: {issue_title}")
        return (1, 0, 0)

    stderr = (cp.stderr or "").lower()
    if "already" in stderr and ("exists" in stderr or "added" in stderr):
        out(f"Skip project item (exists): {issue_title}")
        return (0, 1, 0)

    detail = (
        cp.stderr.strip()
        or cp.stdout.strip()
        or f"Failed adding issue to project: {issue_title}"
    )
    emit_err(_project_scope_hint(detail))
    return (0, 0, 1)


def _load_token_from_env_file(env_file: Path) -> None:
    if not env_file.exists():
        return
    loaded: dict[str, str] = {}
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
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

    resolved_token = resolve_gh_token(loaded)
    if resolved_token:
        os.environ["GH_TOKEN"] = resolved_token


def _ensure_gh_auth(repo_dir: Path) -> None:
    for env_file in (Path.cwd() / ".env", repo_dir / ".env"):
        _load_token_from_env_file(env_file)
    resolved_token = resolve_gh_token(os.environ)
    current_gh_token = os.environ.get("GH_TOKEN")
    if resolved_token and (
        not current_gh_token or is_placeholder_token(current_gh_token)
    ):
        os.environ["GH_TOKEN"] = resolved_token

    token = os.environ.get("GH_TOKEN")
    if token and not is_placeholder_token(token):
        return

    raise RuntimeError(
        "Authenticate first: set GH_TOKEN/GITHUB_TOKEN or github_token in .env."
    )


def _get_token(repo_dir: Path) -> str:
    token = token_from_repo(repo_dir)
    if not token or is_placeholder_token(token):
        raise RuntimeError(
            "Authenticate first: set GH_TOKEN/GITHUB_TOKEN or github_token in .env."
        )
    return token


def _run_gh(repo_dir: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    """Route gh CLI args to direct GitHub API calls — no gh binary required."""
    token = token_from_repo(repo_dir) or ""

    if not args:
        return subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="Empty gh args."
        )

    subcmd = args[0]

    # ---- REST API calls: gh api [--paginate] [--method M] endpoint [-f k=v ...] ----
    if subcmd == "api":
        rest_args = args[1:]
        paginate = "--paginate" in rest_args
        if paginate:
            rest_args = [a for a in rest_args if a != "--paginate"]

        method = "GET"
        endpoint = ""
        fields: dict[str, str] = {}
        skip_next = False
        positional: list[str] = []

        i = 0
        while i < len(rest_args):
            a = rest_args[i]
            if skip_next:
                skip_next = False
                i += 1
                continue
            if a in ("--method", "-X") and i + 1 < len(rest_args):
                method = rest_args[i + 1]
                i += 2
                continue
            if a in ("-f", "--field") and i + 1 < len(rest_args):
                kv = rest_args[i + 1]
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    fields[k] = v
                i += 2
                continue
            if a in ("--input", "-") and i + 1 < len(rest_args):
                # skip --input - (we don't support stdin piping here)
                i += 2
                continue
            if not a.startswith("-"):
                positional.append(a)
            i += 1

        endpoint = positional[0] if positional else ""

        if paginate:
            return rest_paginated(endpoint, token)
        if fields:
            return rest(method, endpoint, token, fields)
        return rest(method, endpoint, token)

    # ---- gh issue create ----
    if subcmd == "issue" and len(args) > 1 and args[1] == "create":
        return _gh_issue_create(args[2:], token)

    # ---- gh project subcommands ----
    if subcmd == "project":
        return _gh_project(args[1:], repo_dir, token)

    return subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr=f"Unsupported gh subcommand: {subcmd}"
    )


def _gh_issue_create(args: list[str], token: str) -> subprocess.CompletedProcess[str]:
    repo = title = body_file = milestone = labels = assignees = None
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--repo" and i + 1 < len(args):
            repo = args[i + 1]
            i += 2
            continue
        if a == "--title" and i + 1 < len(args):
            title = args[i + 1]
            i += 2
            continue
        if a == "--body-file" and i + 1 < len(args):
            body_file = args[i + 1]
            i += 2
            continue
        if a == "--milestone" and i + 1 < len(args):
            milestone = args[i + 1]
            i += 2
            continue
        if a == "--label" and i + 1 < len(args):
            labels = args[i + 1]
            i += 2
            continue
        if a == "--assignee" and i + 1 < len(args):
            assignees = args[i + 1]
            i += 2
            continue
        i += 1

    if not repo or not title:
        return subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="--repo and --title are required."
        )

    body = ""
    if body_file:
        try:
            body = Path(body_file).read_text(encoding="utf-8")
        except OSError:
            pass

    payload: dict[str, object] = {"title": title, "body": body}
    if labels:
        label_list = [lb.strip() for lb in labels.split(",") if lb.strip()]
        if label_list:
            payload["labels"] = label_list
    if assignees:
        assignee_list = [a.strip() for a in assignees.split(",") if a.strip()]
        if assignee_list:
            payload["assignees"] = assignee_list
    if milestone:
        # resolve milestone number
        owner_repo = repo
        ms_cp = rest_paginated(
            f"/repos/{owner_repo}/milestones?state=all&per_page=100", token
        )
        if ms_cp.returncode == 0:
            try:
                for ms in json.loads(ms_cp.stdout):
                    if isinstance(ms, dict) and ms.get("title") == milestone:
                        payload["milestone"] = ms["number"]
                        break
            except (json.JSONDecodeError, KeyError):
                pass

    cp = rest("POST", f"/repos/{repo}/issues", token, payload)
    if cp.returncode != 0:
        return cp
    try:
        url = json.loads(cp.stdout).get("html_url", "")
        return subprocess.CompletedProcess(
            args=[], returncode=0, stdout=url + "\n", stderr=""
        )
    except (json.JSONDecodeError, AttributeError):
        return cp


def _gh_project(
    args: list[str], repo_dir: Path, token: str
) -> subprocess.CompletedProcess[str]:
    if not args:
        return subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="Missing project subcommand."
        )

    sub = args[0]
    rest_args = args[1:]

    def _get(flag: str) -> str | None:
        try:
            idx = rest_args.index(flag)
            return rest_args[idx + 1] if idx + 1 < len(rest_args) else None
        except ValueError:
            return None

    owner = _get("--owner") or ""
    number_str = (
        rest_args[0] if rest_args and not rest_args[0].startswith("-") else None
    )
    number = int(number_str) if number_str and number_str.isdigit() else None

    if sub == "list":
        include_closed = "--closed" in rest_args
        return project_list(owner, token, include_closed=include_closed)

    if sub == "view":
        if number is None:
            return subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="Project number required."
            )
        return project_view(owner, number, token)

    if sub == "create":
        title = _get("--title") or ""
        return project_create(owner, title, token)

    if sub == "edit":
        if number is None:
            return subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="Project number required."
            )
        # Get project ID first
        pv = project_view(owner, number, token)
        if pv.returncode != 0:
            return pv
        try:
            project_id = json.loads(pv.stdout).get("id", "")
        except (json.JSONDecodeError, AttributeError):
            return subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="Could not resolve project ID."
            )
        visibility = _get("--visibility")
        return project_edit(
            project_id,
            token,
            title=_get("--title"),
            description=_get("--description"),
            readme=_get("--readme"),
            visibility=visibility,
        )

    if sub == "delete":
        if number is None:
            return subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="Project number required."
            )
        pv = project_view(owner, number, token)
        if pv.returncode != 0:
            return pv
        try:
            project_id = json.loads(pv.stdout).get("id", "")
        except (json.JSONDecodeError, AttributeError):
            return subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="Could not resolve project ID."
            )
        return project_delete(project_id, token)

    if sub == "item-list":
        if number is None:
            return subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="Project number required."
            )
        limit_str = _get("--limit")
        limit = int(limit_str) if limit_str and limit_str.isdigit() else 100
        pv = project_view(owner, number, token)
        if pv.returncode != 0:
            return pv
        try:
            project_id = json.loads(pv.stdout).get("id", "")
        except (json.JSONDecodeError, AttributeError):
            return subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="Could not resolve project ID."
            )
        return project_item_list(project_id, token, limit=limit)

    if sub == "item-add":
        if number is None:
            return subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="Project number required."
            )
        url = _get("--url") or ""
        # Extract issue number and repo from URL
        # e.g. https://github.com/owner/repo/issues/42
        content_id = _resolve_content_node_id(url, token)
        if not content_id:
            return subprocess.CompletedProcess(
                args=[],
                returncode=1,
                stdout="",
                stderr=f"Could not resolve content ID for: {url}",
            )
        pv = project_view(owner, number, token)
        if pv.returncode != 0:
            return pv
        try:
            project_id = json.loads(pv.stdout).get("id", "")
        except (json.JSONDecodeError, AttributeError):
            return subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="Could not resolve project ID."
            )
        return project_item_add(project_id, content_id, token)

    if sub == "item-delete":
        if number is None:
            return subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="Project number required."
            )
        item_id = _get("--id") or ""
        pv = project_view(owner, number, token)
        if pv.returncode != 0:
            return pv
        try:
            project_id = json.loads(pv.stdout).get("id", "")
        except (json.JSONDecodeError, AttributeError):
            return subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="Could not resolve project ID."
            )
        return project_item_delete(project_id, item_id, token)

    if sub == "link":
        if number is None:
            return subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="Project number required."
            )
        repo = _get("--repo") or ""
        pv = project_view(owner, number, token)
        if pv.returncode != 0:
            return pv
        try:
            project_id = json.loads(pv.stdout).get("id", "")
        except (json.JSONDecodeError, AttributeError):
            return subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="Could not resolve project ID."
            )
        repo_owner, _, repo_name = repo.partition("/")
        cp = link_project_to_repository(project_id, repo_owner, repo_name, token)
        if cp.returncode != 0:
            stderr = (cp.stderr or "").lower()
            if "already" in stderr and "link" in stderr:
                return subprocess.CompletedProcess(
                    args=[], returncode=0, stdout="already linked", stderr=""
                )
        return cp

    return subprocess.CompletedProcess(
        args=[],
        returncode=1,
        stdout="",
        stderr=f"Unsupported project subcommand: {sub}",
    )


def _resolve_content_node_id(url: str, token: str) -> str | None:
    """Extract issue/PR node ID from a GitHub URL."""
    # https://github.com/owner/repo/issues/42
    parts = url.rstrip("/").split("/")
    try:
        idx = parts.index("issues") if "issues" in parts else parts.index("pull")
        number = int(parts[idx + 1])
        repo_owner = parts[idx - 2]
        repo_name = parts[idx - 1]
    except (ValueError, IndexError):
        return None
    return issue_node_id(repo_owner, repo_name, number, token)


def _parse_concatenated_json_arrays(raw: str) -> list[dict[str, object]]:
    decoder = json.JSONDecoder()
    idx = 0
    merged: list[dict[str, object]] = []
    while idx < len(raw):
        while idx < len(raw) and raw[idx].isspace():
            idx += 1
        if idx >= len(raw):
            break
        value, idx = decoder.raw_decode(raw, idx)
        if isinstance(value, list):
            merged.extend(item for item in value if isinstance(item, dict))
    return merged


def _find_issue_number(repo_dir: Path, repo: str, title: str) -> int | None:
    for item in _list_repo_issues(repo_dir, repo):
        if not isinstance(item, dict) or "pull_request" in item:
            continue

        number = item.get("number")
        if item.get("title") == title and isinstance(number, int):
            return number
    return None


def _list_repo_issues(repo_dir: Path, repo: str) -> list[dict[str, object]]:
    cp = _run_gh(
        repo_dir,
        ["api", "--paginate", f"/repos/{repo}/issues?state=all&per_page=100"],
    )
    if cp.returncode != 0:
        raise RuntimeError(
            cp.stderr.strip() or f"Failed listing issues for repo: {repo}"
        )
    return _parse_concatenated_json_arrays(cp.stdout)


def _wait_for_issue_titles_visible(
    repo_dir: Path,
    repo: str,
    titles: list[str],
) -> None:
    pending = {title for title in titles if title.strip()}
    if not pending:
        return

    for delay in (0.0, 0.25, 0.5, 1.0, 2.0):
        if delay > 0:
            time.sleep(delay)

        visible_titles = {
            item.get("title")
            for item in _list_repo_issues(repo_dir, repo)
            if isinstance(item, dict)
            and "pull_request" not in item
            and isinstance(item.get("title"), str)
        }
        still_missing = {title for title in pending if title not in visible_titles}
        if not still_missing:
            return
        pending = still_missing


def _list_existing_labels(repo_dir: Path, repo: str) -> set[str]:
    cp = _run_gh(repo_dir, ["api", "--paginate", f"/repos/{repo}/labels?per_page=100"])
    if cp.returncode != 0:
        raise RuntimeError(cp.stderr.strip() or "Failed listing labels.")

    labels: set[str] = set()
    for item in _parse_concatenated_json_arrays(cp.stdout):
        name = item.get("name")
        if isinstance(name, str) and name.strip():
            labels.add(name.strip())
    return labels


def _normalize_labels(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    normalized: list[str] = []
    for item in raw:
        if isinstance(item, str):
            label = item.strip()
            if label:
                normalized.append(label)
    return normalized


def _merge_labels(*groups: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for label in group:
            normalized = label.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            merged.append(normalized)
    return merged


def _label_color(label: str) -> str:
    # Deterministic fallback color for auto-created labels.
    return hashlib.sha1(label.encode("utf-8")).hexdigest()[:6]


def _ensure_missing_labels(
    repo_dir: Path,
    repo: str,
    existing_labels: set[str],
    required_labels: list[str],
    dry_run: bool,
    out: Callable[[str], None],
    emit_err: Callable[[str], None],
) -> int:
    failures = 0
    for label in required_labels:
        if label in existing_labels:
            continue

        if dry_run:
            out(f"[dry-run] create label: {label}")
            existing_labels.add(label)
            continue

        cp = _run_gh(
            repo_dir,
            [
                "api",
                "--method",
                "POST",
                f"/repos/{repo}/labels",
                "-f",
                f"name={label}",
                "-f",
                f"color={_label_color(label)}",
            ],
        )
        if cp.returncode == 0:
            out(f"Created label: {label}")
            existing_labels.add(label)
            continue

        stderr = (cp.stderr or "").lower()
        if (
            "already_exists" in stderr
            or "name already exists on this repository" in stderr
        ):
            existing_labels.add(label)
            continue

        failures += 1
        emit_err(f"Failed to create label: {label}")
    return failures


def _create_issue(
    repo_dir: Path,
    repo: str,
    title: str,
    body: str,
    labels: list[str],
    assignees: list[str],
    milestone: str | None,
) -> int:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as tf:
        tf.write(body)
        body_file = Path(tf.name)
    try:
        args = [
            "issue",
            "create",
            "--repo",
            repo,
            "--title",
            title,
            "--body-file",
            str(body_file),
        ]
        if milestone:
            args.extend(["--milestone", milestone])
        if labels:
            args.extend(["--label", ",".join(labels)])
        if assignees:
            args.extend(["--assignee", ",".join(assignees)])

        cp = _run_gh(repo_dir, args)
        if cp.returncode != 0:
            raise RuntimeError(cp.stderr.strip() or f"Failed creating issue: {title}")
        created_url = cp.stdout.strip().splitlines()[-1] if cp.stdout.strip() else ""
        if created_url:
            tail = created_url.rstrip("/").split("/")[-1]
            if tail.isdigit():
                return int(tail)
        number = _find_issue_number(repo_dir, repo, title)
        if number is None:
            raise RuntimeError(
                f"Created issue but could not resolve its number: {title}"
            )
        return number
    finally:
        body_file.unlink(missing_ok=True)


def apply_backlog(
    *,
    repo_dir: Path,
    repo: str,
    backlog_file: Path,
    dry_run: bool,
    project_number: int | None = None,
    project_title: str | None = None,
    project_owner: str | None = None,
    out: Callable[[str], None] = print,
    err: Callable[[str], None] | None = None,
) -> BacklogApplySummary:
    emit_err = err if err is not None else (lambda line: print(line))

    if not backlog_file.exists():
        raise RuntimeError(f"Backlog file not found: {backlog_file}")

    data = json.loads(backlog_file.read_text(encoding="utf-8"))
    file_repo = data.get("repo")
    if file_repo and file_repo != repo:
        raise RuntimeError(
            f"Backlog file declares repo '{file_repo}' but target is '{repo}'. "
            f"Pass --file explicitly to apply this backlog to a different repo."
        )

    resolve_authenticated_login(repo_dir)

    epics = data.get("epics")
    if not isinstance(epics, list):
        raise RuntimeError("Invalid backlog JSON: expected top-level 'epics' list.")

    milestones_created = 0
    milestones_skipped = 0
    issues_created = 0
    issues_skipped = 0
    failures = 0
    epic_issues_created = 0
    epic_issues_skipped = 0
    ticket_issues_created = 0
    ticket_issues_skipped = 0
    project_items_added = 0
    project_items_skipped = 0
    created_issue_titles: list[str] = []

    project = _resolve_project_target(
        repo_dir=repo_dir,
        repo=repo,
        project_number=project_number,
        project_title=project_title,
        project_owner=project_owner,
        dry_run=dry_run,
        out=out,
    )
    project_created = bool(project and project.created)
    if project is not None:
        failures += _link_project_to_repo(
            repo_dir=repo_dir,
            repo=repo,
            project=project,
            dry_run=dry_run,
            out=out,
            emit_err=emit_err,
        )
        if not dry_run and project.number is not None:
            metadata_file = write_project_metadata(
                repo_dir,
                owner=project.owner,
                number=project.number,
                title=project.title,
                repo=repo,
                source="apply_backlog",
            )
            out(f"Synced project metadata: {metadata_file}")

    existing_milestones: set[str] = set()
    cp = _run_gh(
        repo_dir,
        ["api", "--paginate", f"/repos/{repo}/milestones?state=all&per_page=100"],
    )
    if cp.returncode != 0:
        raise RuntimeError(cp.stderr.strip() or "Failed listing milestones.")
    for item in _parse_concatenated_json_arrays(cp.stdout):
        title = item.get("title")
        if isinstance(title, str):
            existing_milestones.add(title)
    existing_labels = _list_existing_labels(repo_dir, repo)

    epic_numbers: dict[str, int | None] = {}

    for epic in epics:
        if not isinstance(epic, dict):
            failures += 1
            emit_err("Invalid backlog JSON: epic must be an object.")
            continue

        epic_key = str(epic.get("key", "")).strip()
        epic_title = str(epic.get("title", "")).strip()
        epic_body = str(epic.get("body", "")).strip()
        epic_labels_raw = _normalize_labels(epic.get("labels", []))
        epic_assignees = epic.get("assignees", [])
        tickets = epic.get("tickets", [])

        if (
            not epic_key
            or not epic_title
            or not epic_body
            or not isinstance(tickets, list)
        ):
            failures += 1
            emit_err(
                "Invalid backlog JSON: epic.key, epic.title, epic.body are required and epic.tickets must be a list."
            )
            continue

        epic_labels = _merge_labels(["epic", f"epic:{epic_key}"], epic_labels_raw)

        failures += _ensure_missing_labels(
            repo_dir,
            repo,
            existing_labels,
            epic_labels,
            dry_run,
            out,
            emit_err,
        )

        if epic_title in existing_milestones:
            milestones_skipped += 1
            out(f"Skip milestone (exists): {epic_title}")
        else:
            if dry_run:
                milestones_created += 1
                out(f"[dry-run] create milestone: {epic_title}")
            else:
                cp = _run_gh(
                    repo_dir,
                    [
                        "api",
                        "--method",
                        "POST",
                        f"/repos/{repo}/milestones",
                        "-f",
                        f"title={epic_title}",
                    ],
                )
                if cp.returncode == 0:
                    milestones_created += 1
                    out(f"Created milestone: {epic_title}")
                    existing_milestones.add(epic_title)
                else:
                    failures += 1
                    emit_err(f"Failed to create milestone: {epic_title}")

        number = _find_issue_number(repo_dir, repo, epic_title)
        if number is not None:
            issues_skipped += 1
            epic_issues_skipped += 1
            epic_numbers[epic_title] = number
            out(f"Skip issue (exists): {epic_title}")
            if project is not None:
                added, skipped, failed = _add_issue_to_project(
                    repo_dir=repo_dir,
                    repo=repo,
                    project=project,
                    issue_title=epic_title,
                    issue_number=number,
                    dry_run=dry_run,
                    out=out,
                    emit_err=emit_err,
                )
                project_items_added += added
                project_items_skipped += skipped
                failures += failed
        else:
            if dry_run:
                issues_created += 1
                epic_issues_created += 1
                epic_numbers[epic_title] = None
                out(f"[dry-run] create issue: {epic_title}")
                if project is not None:
                    added, skipped, failed = _add_issue_to_project(
                        repo_dir=repo_dir,
                        repo=repo,
                        project=project,
                        issue_title=epic_title,
                        issue_number=None,
                        dry_run=dry_run,
                        out=out,
                        emit_err=emit_err,
                    )
                    project_items_added += added
                    project_items_skipped += skipped
                    failures += failed
            else:
                try:
                    number = _create_issue(
                        repo_dir,
                        repo,
                        epic_title,
                        epic_body,
                        epic_labels,
                        epic_assignees if isinstance(epic_assignees, list) else [],
                        None,
                    )
                    epic_numbers[epic_title] = number
                    issues_created += 1
                    epic_issues_created += 1
                    created_issue_titles.append(epic_title)
                    out(f"Created issue: {epic_title}")
                    if project is not None:
                        added, skipped, failed = _add_issue_to_project(
                            repo_dir=repo_dir,
                            repo=repo,
                            project=project,
                            issue_title=epic_title,
                            issue_number=number,
                            dry_run=dry_run,
                            out=out,
                            emit_err=emit_err,
                        )
                        project_items_added += added
                        project_items_skipped += skipped
                        failures += failed
                except RuntimeError as exc:
                    failures += 1
                    emit_err(str(exc))

        for ticket in tickets:
            if not isinstance(ticket, dict):
                failures += 1
                emit_err("Invalid backlog JSON: ticket entries must be objects.")
                continue

            title = str(ticket.get("title", "")).strip()
            body = str(ticket.get("body", "")).strip()
            labels = _normalize_labels(ticket.get("labels", []))
            assignees = ticket.get("assignees", [])
            if not title or not body:
                failures += 1
                emit_err(
                    "Invalid backlog JSON: ticket.title and ticket.body are required."
                )
                continue

            failures += _ensure_missing_labels(
                repo_dir,
                repo,
                existing_labels,
                labels,
                dry_run,
                out,
                emit_err,
            )

            existing_number = _find_issue_number(repo_dir, repo, title)
            if existing_number is not None:
                issues_skipped += 1
                ticket_issues_skipped += 1
                out(f"Skip issue (exists): {title}")
                if project is not None:
                    added, skipped, failed = _add_issue_to_project(
                        repo_dir=repo_dir,
                        repo=repo,
                        project=project,
                        issue_title=title,
                        issue_number=existing_number,
                        dry_run=dry_run,
                        out=out,
                        emit_err=emit_err,
                    )
                    project_items_added += added
                    project_items_skipped += skipped
                    failures += failed
                continue

            parent_number = epic_numbers.get(epic_title)
            if parent_number is None:
                parent_number = _find_issue_number(repo_dir, repo, epic_title)
            ticket_body = body
            if parent_number is not None:
                ticket_body = f"Epic: #{parent_number}\n\n{ticket_body}"
            elif dry_run:
                ticket_body = f"Epic: #<epic_issue_number>\n\n{ticket_body}"
            else:
                failures += 1
                emit_err(f"Failed to resolve epic issue number for ticket: {title}")
                continue

            if dry_run:
                issues_created += 1
                ticket_issues_created += 1
                out(f"[dry-run] create issue: {title}")
                if project is not None:
                    added, skipped, failed = _add_issue_to_project(
                        repo_dir=repo_dir,
                        repo=repo,
                        project=project,
                        issue_title=title,
                        issue_number=None,
                        dry_run=dry_run,
                        out=out,
                        emit_err=emit_err,
                    )
                    project_items_added += added
                    project_items_skipped += skipped
                    failures += failed
                continue

            try:
                created_number = _create_issue(
                    repo_dir,
                    repo,
                    title,
                    ticket_body,
                    labels,
                    assignees if isinstance(assignees, list) else [],
                    epic_title,
                )
                issues_created += 1
                ticket_issues_created += 1
                created_issue_titles.append(title)
                out(f"Created issue: {title}")
                if project is not None:
                    added, skipped, failed = _add_issue_to_project(
                        repo_dir=repo_dir,
                        repo=repo,
                        project=project,
                        issue_title=title,
                        issue_number=created_number,
                        dry_run=dry_run,
                        out=out,
                        emit_err=emit_err,
                    )
                    project_items_added += added
                    project_items_skipped += skipped
                    failures += failed
            except RuntimeError as exc:
                failures += 1
                emit_err(str(exc))

    if not dry_run and created_issue_titles:
        _wait_for_issue_titles_visible(repo_dir, repo, created_issue_titles)

    return BacklogApplySummary(
        milestones_created=milestones_created,
        milestones_skipped=milestones_skipped,
        issues_created=issues_created,
        issues_skipped=issues_skipped,
        failures=failures,
        epic_issues_created=epic_issues_created,
        epic_issues_skipped=epic_issues_skipped,
        ticket_issues_created=ticket_issues_created,
        ticket_issues_skipped=ticket_issues_skipped,
        project_created=project_created,
        project_items_added=project_items_added,
        project_items_skipped=project_items_skipped,
    )


def fetch_issue(repo_dir: Path, repo: str, issue_number: int) -> IssueDetail:
    _ensure_gh_auth(repo_dir)
    cp = _run_gh(repo_dir, ["api", f"/repos/{repo}/issues/{issue_number}"])
    if cp.returncode != 0:
        stderr = cp.stderr.strip()
        if "404" in stderr or "Not Found" in (cp.stdout or ""):
            raise RuntimeError(f"Issue #{issue_number} not found in {repo}.")
        raise RuntimeError(
            stderr or f"Failed to fetch issue #{issue_number} from {repo}."
        )
    try:
        data = json.loads(cp.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Unexpected response when fetching issue #{issue_number}."
        ) from exc
    if "pull_request" in data:
        raise RuntimeError(f"#{issue_number} is a pull request, not an issue.")
    return IssueDetail(
        number=int(data.get("number", issue_number)),
        title=str(data.get("title", "")),
        body=str(data.get("body", "") or ""),
        state=str(data.get("state", "")),
        labels=[
            lbl["name"]
            for lbl in data.get("labels", [])
            if isinstance(lbl, dict) and "name" in lbl
        ],
        assignees=[
            a["login"]
            for a in data.get("assignees", [])
            if isinstance(a, dict) and "login" in a
        ],
    )
