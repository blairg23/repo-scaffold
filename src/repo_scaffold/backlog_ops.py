from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class BacklogApplySummary:
    milestones_created: int
    milestones_skipped: int
    issues_created: int
    issues_skipped: int
    failures: int
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
        raise RuntimeError("GitHub auth check failed: could not resolve authenticated user login.")
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


def _list_projects(repo_dir: Path, owner: str) -> list[dict[str, object]]:
    cp = _run_gh(repo_dir, ["project", "list", "--owner", owner, "--limit", "100", "--format", "json"])
    if cp.returncode != 0:
        detail = cp.stderr.strip() or cp.stdout.strip() or "Failed listing projects."
        raise RuntimeError(_project_scope_hint(detail))
    data = json.loads(cp.stdout or "[]")
    if not isinstance(data, list):
        raise RuntimeError("Unexpected response from gh project list.")
    return [item for item in data if isinstance(item, dict)]


def _project_title_for_number(repo_dir: Path, owner: str, number: int) -> str:
    cp = _run_gh(repo_dir, ["project", "view", str(number), "--owner", owner, "--format", "json"])
    if cp.returncode != 0:
        detail = cp.stderr.strip() or cp.stdout.strip() or f"Failed viewing project #{number}."
        raise RuntimeError(_project_scope_hint(detail))
    try:
        payload = json.loads(cp.stdout or "{}")
    except json.JSONDecodeError:
        return f"Project #{number}"
    title = payload.get("title")
    return title.strip() if isinstance(title, str) and title.strip() else f"Project #{number}"


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
        return _ProjectTarget(owner=owner, number=project_number, title=title, created=False)

    assert project_title is not None
    wanted_title = project_title.strip()
    if not wanted_title:
        raise RuntimeError("--project-title must not be empty.")

    for item in _list_projects(repo_dir, owner):
        title = item.get("title")
        number = item.get("number")
        if isinstance(title, str) and isinstance(number, int) and title == wanted_title:
            out(f"Using project: {owner}/#{number} ({wanted_title})")
            return _ProjectTarget(owner=owner, number=number, title=wanted_title, created=False)

    if dry_run:
        out(f"[dry-run] create project: {wanted_title} (owner: {owner})")
        return _ProjectTarget(owner=owner, number=None, title=wanted_title, created=True)

    cp = _run_gh(
        repo_dir,
        ["project", "create", "--owner", owner, "--title", wanted_title, "--format", "json"],
    )
    if cp.returncode != 0:
        detail = cp.stderr.strip() or cp.stdout.strip() or f"Failed creating project: {wanted_title}"
        raise RuntimeError(_project_scope_hint(detail))
    payload = json.loads(cp.stdout or "{}")
    number = payload.get("number")
    if not isinstance(number, int):
        for item in _list_projects(repo_dir, owner):
            title = item.get("title")
            num = item.get("number")
            if isinstance(title, str) and isinstance(num, int) and title == wanted_title:
                number = num
                break
    if not isinstance(number, int):
        raise RuntimeError(f"Created project '{wanted_title}' but could not resolve project number.")

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
        out(f"[dry-run] link project to repo: {project.owner}/#{project.number} -> {repo}")
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

    detail = cp.stderr.strip() or cp.stdout.strip() or f"Failed linking project #{project.number} to repo."
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
        out(f"[dry-run] add issue to project: {issue_title} -> {project.owner}/#{project.number}")
        return (1, 0, 0)

    if issue_number is None:
        emit_err(f"Failed to add issue to project (missing issue number): {issue_title}")
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

    detail = cp.stderr.strip() or cp.stdout.strip() or f"Failed adding issue to project: {issue_title}"
    emit_err(_project_scope_hint(detail))
    return (0, 0, 1)


def _load_token_from_env_file(env_file: Path) -> None:
    if not env_file.exists():
        return

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

        if key in {"github_token", "GH_TOKEN", "GITHUB_TOKEN"}:
            os.environ["GH_TOKEN"] = value
            break


def _ensure_gh_auth(repo_dir: Path) -> None:
    if shutil.which("gh") is None:
        raise RuntimeError("GitHub CLI (gh) is required.")

    for env_file in (Path.cwd() / ".env", repo_dir / ".env"):
        _load_token_from_env_file(env_file)
    if not os.environ.get("GH_TOKEN") and os.environ.get("GITHUB_TOKEN"):
        os.environ["GH_TOKEN"] = os.environ["GITHUB_TOKEN"]

    if os.environ.get("GH_TOKEN"):
        return

    status = subprocess.run(
        ["gh", "auth", "status"],
        cwd=repo_dir,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        text=True,
    )
    if status.returncode != 0:
        raise RuntimeError(
            "Authenticate first: gh auth login (or set GH_TOKEN/GITHUB_TOKEN or github_token in .env)."
        )


def _run_gh(repo_dir: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["gh", *args],
        cwd=repo_dir,
        text=True,
        capture_output=True,
        check=False,
    )


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
    cp = _run_gh(
        repo_dir,
        [
            "issue",
            "list",
            "--repo",
            repo,
            "--state",
            "all",
            "--limit",
            "100",
            "--search",
            f"{title} in:title",
            "--json",
            "title,number",
        ],
    )
    if cp.returncode != 0:
        raise RuntimeError(cp.stderr.strip() or f"Failed checking existing issue: {title}")
    data = json.loads(cp.stdout or "[]")
    for item in data:
        if isinstance(item, dict) and item.get("title") == title and isinstance(item.get("number"), int):
            return int(item["number"])
    return None


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
        if "already_exists" in stderr or "name already exists on this repository" in stderr:
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
            raise RuntimeError(f"Created issue but could not resolve its number: {title}")
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
    resolve_authenticated_login(repo_dir)

    if not backlog_file.exists():
        raise RuntimeError(f"Backlog file not found: {backlog_file}")

    data = json.loads(backlog_file.read_text(encoding="utf-8"))
    epics = data.get("epics")
    if not isinstance(epics, list):
        raise RuntimeError("Invalid backlog JSON: expected top-level 'epics' list.")

    milestones_created = 0
    milestones_skipped = 0
    issues_created = 0
    issues_skipped = 0
    failures = 0
    project_items_added = 0
    project_items_skipped = 0

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

    existing_milestones: set[str] = set()
    cp = _run_gh(repo_dir, ["api", "--paginate", f"/repos/{repo}/milestones?state=all&per_page=100"])
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

        if not epic_key or not epic_title or not epic_body or not isinstance(tickets, list):
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
                    repo_dir, ["api", "--method", "POST", f"/repos/{repo}/milestones", "-f", f"title={epic_title}"]
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
                emit_err("Invalid backlog JSON: ticket.title and ticket.body are required.")
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

    return BacklogApplySummary(
        milestones_created=milestones_created,
        milestones_skipped=milestones_skipped,
        issues_created=issues_created,
        issues_skipped=issues_skipped,
        failures=failures,
        project_created=project_created,
        project_items_added=project_items_added,
        project_items_skipped=project_items_skipped,
    )
