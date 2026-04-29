from __future__ import annotations

import json
import os
import shlex
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .backlog_ops import (
    _list_projects,
    _parse_repo_owner,
    _project_scope_hint,
    _run_gh,
    resolve_authenticated_login,
)

DEFAULT_PROJECT_BACKUP_REL_DIR = "artifacts/project-backups"


@dataclass(frozen=True)
class ProjectInfo:
    owner: str
    number: int
    title: str
    id: str | None = None
    closed: bool | None = None
    visibility: str | None = None
    description: str | None = None
    readme: str | None = None


@dataclass(frozen=True)
class ProjectItemInfo:
    id: str
    title: str
    content_type: str
    content_url: str | None
    issue_number: int | None
    repository: str | None
    body: str | None = None


@dataclass(frozen=True)
class ProjectListSummary:
    owner: str
    projects: tuple[ProjectInfo, ...]


@dataclass(frozen=True)
class ProjectItemsSummary:
    project: ProjectInfo
    items: tuple[ProjectItemInfo, ...]


@dataclass(frozen=True)
class ProjectMutationSummary:
    action: str
    owner: str
    project_number: int | None
    project_title: str | None
    failures: int
    changed: bool
    backup_file: Path | None = None
    undo_command: str | None = None
    restored_project_number: int | None = None
    restored_item_id: str | None = None


@dataclass(frozen=True)
class _ProjectBackup:
    kind: str
    payload: dict[str, object]


def _emit_err_factory(err: Callable[[str], None] | None) -> Callable[[str], None]:
    return err if err is not None else (lambda line: print(line, file=sys.stderr))


def _normalize_visibility(payload: dict[str, object]) -> str | None:
    visibility = payload.get("visibility")
    if isinstance(visibility, str) and visibility.strip():
        return visibility.strip().upper()
    public = payload.get("public")
    if isinstance(public, bool):
        return "PUBLIC" if public else "PRIVATE"
    return None


def _project_from_payload(
    payload: dict[str, object], *, owner: str, fallback_number: int | None = None
) -> ProjectInfo:
    number = payload.get("number")
    if not isinstance(number, int):
        if fallback_number is None:
            raise RuntimeError(
                "Project payload did not include a numeric project number."
            )
        number = fallback_number

    title = payload.get("title")
    if not isinstance(title, str) or not title.strip():
        title = f"Project #{number}"

    description_value = payload.get("shortDescription")
    if not isinstance(description_value, str) or not description_value.strip():
        description_value = payload.get("description")
    description = (
        description_value.strip()
        if isinstance(description_value, str) and description_value.strip()
        else None
    )

    readme_value = payload.get("readme")
    readme = (
        readme_value.strip()
        if isinstance(readme_value, str) and readme_value.strip()
        else None
    )

    project_id = payload.get("id")
    project_id_value = (
        project_id.strip()
        if isinstance(project_id, str) and project_id.strip()
        else None
    )

    closed_value = payload.get("closed")
    closed = closed_value if isinstance(closed_value, bool) else None

    return ProjectInfo(
        owner=owner,
        number=number,
        title=title.strip(),
        id=project_id_value,
        closed=closed,
        visibility=_normalize_visibility(payload),
        description=description,
        readme=readme,
    )


def _parse_project_item(item: dict[str, object]) -> ProjectItemInfo:
    item_id = item.get("id")
    if not isinstance(item_id, str) or not item_id.strip():
        raise RuntimeError("Project item payload did not include a stable item id.")

    content = item.get("content")
    content_dict = content if isinstance(content, dict) else {}

    title: str | None = None
    for candidate in (item.get("title"), content_dict.get("title")):
        if isinstance(candidate, str) and candidate.strip():
            title = candidate.strip()
            break
    if title is None:
        title = f"Item {item_id.strip()}"

    body_value = item.get("body")
    if not isinstance(body_value, str) or not body_value.strip():
        body_value = content_dict.get("body")
    body = (
        body_value.strip()
        if isinstance(body_value, str) and body_value.strip()
        else None
    )

    content_type_value = content_dict.get("type")
    if not isinstance(content_type_value, str) or not content_type_value.strip():
        content_type_value = item.get("type")
    content_type = (
        content_type_value.strip()
        if isinstance(content_type_value, str) and content_type_value.strip()
        else "DraftIssue"
    )

    content_url_value = content_dict.get("url")
    if not isinstance(content_url_value, str) or not content_url_value.strip():
        content_url_value = item.get("url")
    content_url = (
        content_url_value.strip()
        if isinstance(content_url_value, str) and content_url_value.strip()
        else None
    )

    issue_number_value = content_dict.get("number")
    if not isinstance(issue_number_value, int):
        issue_number_value = item.get("number")
    issue_number = issue_number_value if isinstance(issue_number_value, int) else None

    repository_value = content_dict.get("repository")
    repository: str | None = None
    if isinstance(repository_value, dict):
        for key in ("nameWithOwner", "full_name"):
            raw = repository_value.get(key)
            if isinstance(raw, str) and raw.strip():
                repository = raw.strip()
                break
    elif isinstance(repository_value, str) and repository_value.strip():
        repository = repository_value.strip()

    return ProjectItemInfo(
        id=item_id.strip(),
        title=title,
        content_type=content_type,
        content_url=content_url,
        issue_number=issue_number,
        repository=repository,
        body=body,
    )


def _load_project_view(repo_dir: Path, owner: str, number: int) -> dict[str, object]:
    cp = _run_gh(
        repo_dir,
        ["project", "view", str(number), "--owner", owner, "--format", "json"],
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
    except json.JSONDecodeError as exc:
        raise RuntimeError("Unexpected response from gh project view.") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected response from gh project view.")
    return payload


def _load_project_items(
    repo_dir: Path, owner: str, number: int, limit: int
) -> list[dict[str, object]]:
    cp = _run_gh(
        repo_dir,
        [
            "project",
            "item-list",
            str(number),
            "--owner",
            owner,
            "--limit",
            str(limit),
            "--format",
            "json",
        ],
    )
    if cp.returncode != 0:
        detail = (
            cp.stderr.strip()
            or cp.stdout.strip()
            or f"Failed listing items for project #{number}."
        )
        raise RuntimeError(_project_scope_hint(detail))
    try:
        payload = json.loads(cp.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise RuntimeError("Unexpected response from gh project item-list.") from exc

    items_obj: list[object]
    if isinstance(payload, list):
        items_obj = payload
    elif isinstance(payload, dict):
        raw_items = payload.get("items")
        if isinstance(raw_items, list):
            items_obj = raw_items
        else:
            raw_nodes = payload.get("nodes")
            if not isinstance(raw_nodes, list):
                raise RuntimeError("Unexpected response from gh project item-list.")
            items_obj = raw_nodes
        if not isinstance(items_obj, list):
            raise RuntimeError("Unexpected response from gh project item-list.")
    else:
        raise RuntimeError("Unexpected response from gh project item-list.")

    return [item for item in items_obj if isinstance(item, dict)]


def _resolve_project_owner(owner: str | None, *, repo_dir: Path) -> str:
    if owner and owner.strip():
        return owner.strip()

    for env_key in ("GH_REPO", "GITHUB_REPOSITORY"):
        env_value = os.environ.get(env_key)
        if env_value:
            resolved = _parse_repo_owner(env_value)
            if resolved:
                return resolved

    env_owner = (os.environ.get("GITHUB_ORG") or "").strip()
    if env_owner:
        return env_owner

    return resolve_authenticated_login(repo_dir)


def list_projects(*, repo_dir: Path, owner: str | None = None) -> ProjectListSummary:
    project_owner = _resolve_project_owner(owner, repo_dir=repo_dir)
    projects = tuple(
        _project_from_payload(item, owner=project_owner)
        for item in _list_projects(repo_dir, project_owner)
    )
    return ProjectListSummary(owner=project_owner, projects=projects)


def _find_existing_project(
    *,
    repo_dir: Path,
    owner: str | None,
    project_number: int | None,
    project_title: str | None,
) -> ProjectInfo:
    if project_number is None and not project_title:
        raise RuntimeError("Specify --project-number or --project-title.")
    if project_number is not None and project_title:
        raise RuntimeError("Use only one of --project-number or --project-title.")

    project_owner = _resolve_project_owner(owner, repo_dir=repo_dir)

    if project_number is not None:
        payload = _load_project_view(repo_dir, project_owner, project_number)
        return _project_from_payload(
            payload, owner=project_owner, fallback_number=project_number
        )

    assert project_title is not None
    wanted_title = project_title.strip()
    if not wanted_title:
        raise RuntimeError("--project-title must not be empty.")

    for item in _list_projects(repo_dir, project_owner):
        item_title = item.get("title")
        item_number = item.get("number")
        if (
            isinstance(item_title, str)
            and isinstance(item_number, int)
            and item_title == wanted_title
        ):
            payload = _load_project_view(repo_dir, project_owner, item_number)
            return _project_from_payload(
                payload, owner=project_owner, fallback_number=item_number
            )

    raise RuntimeError(f"Project not found: {project_owner}/{wanted_title}")


def view_project(
    *,
    repo_dir: Path,
    owner: str | None,
    project_number: int | None,
    project_title: str | None,
) -> ProjectInfo:
    return _find_existing_project(
        repo_dir=repo_dir,
        owner=owner,
        project_number=project_number,
        project_title=project_title,
    )


def list_project_items(
    *,
    repo_dir: Path,
    owner: str | None,
    project_number: int | None,
    project_title: str | None,
    limit: int,
) -> ProjectItemsSummary:
    project = _find_existing_project(
        repo_dir=repo_dir,
        owner=owner,
        project_number=project_number,
        project_title=project_title,
    )
    raw_items = _load_project_items(repo_dir, project.owner, project.number, limit)
    items = tuple(_parse_project_item(item) for item in raw_items)
    return ProjectItemsSummary(project=project, items=items)


def create_project(
    *,
    repo_dir: Path,
    owner: str | None,
    project_title: str,
    description: str | None,
    readme: str | None,
    visibility: str | None,
    dry_run: bool,
    out: Callable[[str], None] = print,
) -> ProjectMutationSummary:
    project_owner = _resolve_project_owner(owner, repo_dir=repo_dir)
    title = project_title.strip()
    if not title:
        raise RuntimeError("--project-title must not be empty.")

    if dry_run:
        out(f"[dry-run] create project: {title} (owner: {project_owner})")
        if description:
            out(f"[dry-run] set project description: {title}")
        if readme:
            out(f"[dry-run] set project readme: {title}")
        if visibility:
            out(f"[dry-run] set project visibility: {visibility.upper()}")
        return ProjectMutationSummary(
            action="create",
            owner=project_owner,
            project_number=None,
            project_title=title,
            failures=0,
            changed=True,
        )

    cp = _run_gh(
        repo_dir,
        [
            "project",
            "create",
            "--owner",
            project_owner,
            "--title",
            title,
            "--format",
            "json",
        ],
    )
    if cp.returncode != 0:
        detail = (
            cp.stderr.strip()
            or cp.stdout.strip()
            or f"Failed creating project: {title}"
        )
        raise RuntimeError(_project_scope_hint(detail))

    payload = json.loads(cp.stdout or "{}")
    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected response from gh project create.")

    number = payload.get("number")
    if not isinstance(number, int):
        for item in _list_projects(repo_dir, project_owner):
            item_title = item.get("title")
            item_number = item.get("number")
            if (
                isinstance(item_title, str)
                and isinstance(item_number, int)
                and item_title == title
            ):
                number = item_number
                break
    if not isinstance(number, int):
        raise RuntimeError(
            f"Created project '{title}' but could not resolve its number."
        )

    out(f"Created project: {project_owner}/#{number} ({title})")

    if description or readme or visibility:
        edit_project(
            repo_dir=repo_dir,
            owner=project_owner,
            project_number=number,
            project_title=None,
            title=None,
            description=description,
            readme=readme,
            visibility=visibility,
            dry_run=False,
            out=out,
        )

    return ProjectMutationSummary(
        action="create",
        owner=project_owner,
        project_number=number,
        project_title=title,
        failures=0,
        changed=True,
    )


def edit_project(
    *,
    repo_dir: Path,
    owner: str | None,
    project_number: int | None,
    project_title: str | None,
    title: str | None,
    description: str | None,
    readme: str | None,
    visibility: str | None,
    dry_run: bool,
    out: Callable[[str], None] = print,
) -> ProjectMutationSummary:
    if not any(value for value in (title, description, readme, visibility)):
        raise RuntimeError(
            "Project edit requires at least one of --title, --description, --readme, or --visibility."
        )

    project = _find_existing_project(
        repo_dir=repo_dir,
        owner=owner,
        project_number=project_number,
        project_title=project_title,
    )

    if dry_run:
        if title:
            out(f"[dry-run] rename project: {project.title} -> {title.strip()}")
        if description is not None:
            out(f"[dry-run] update project description: {project.title}")
        if readme is not None:
            out(f"[dry-run] update project readme: {project.title}")
        if visibility is not None:
            out(f"[dry-run] update project visibility: {visibility.upper()}")
        return ProjectMutationSummary(
            action="edit",
            owner=project.owner,
            project_number=project.number,
            project_title=title.strip() if title else project.title,
            failures=0,
            changed=True,
        )

    args = ["project", "edit", str(project.number), "--owner", project.owner]
    if title and title.strip():
        args.extend(["--title", title.strip()])
    if description is not None:
        args.extend(["--description", description])
    if readme is not None:
        args.extend(["--readme", readme])
    if visibility is not None:
        args.extend(["--visibility", visibility.upper()])

    cp = _run_gh(repo_dir, args)
    if cp.returncode != 0:
        detail = (
            cp.stderr.strip()
            or cp.stdout.strip()
            or f"Failed editing project #{project.number}."
        )
        raise RuntimeError(_project_scope_hint(detail))

    out(
        f"Updated project: {project.owner}/#{project.number} ({title.strip() if title else project.title})"
    )
    return ProjectMutationSummary(
        action="edit",
        owner=project.owner,
        project_number=project.number,
        project_title=title.strip() if title else project.title,
        failures=0,
        changed=True,
    )


def _default_backup_dir(repo_dir: Path) -> Path:
    return repo_dir / DEFAULT_PROJECT_BACKUP_REL_DIR


def _backup_paths(
    *, repo_dir: Path, backup_dir: str | None, prefix: str
) -> tuple[Path, str]:
    root = Path(backup_dir) if backup_dir else _default_backup_dir(repo_dir)
    backup_root = root if root.is_absolute() else (repo_dir / root)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    backup_root.mkdir(parents=True, exist_ok=True)
    return backup_root / f"{prefix}-{stamp}.json", stamp


def _write_backup_file(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _build_undo_command(repo_dir: Path, backup_file: Path) -> str:
    quoted_repo = shlex.quote(repo_dir.as_posix())
    quoted_backup = shlex.quote(backup_file.as_posix())
    return f"cd {quoted_repo} && poetry run repo-scaffold project undo --backup-file {quoted_backup}"


def _project_backup_payload(
    *,
    action: str,
    project: ProjectInfo,
    project_payload: dict[str, object],
    item_payloads: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "version": 1,
        "kind": action,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "project_owner": project.owner,
        "project_number": project.number,
        "project_title": project.title,
        "project": project_payload,
        "items": item_payloads,
        "notes": {
            "restore": "repo-scaffold restores project structure and item membership, but does not recreate custom project fields or field values.",
        },
    }


def _project_item_backup_payload(
    *,
    project: ProjectInfo,
    project_payload: dict[str, object],
    item_payload: dict[str, object],
) -> dict[str, object]:
    item = _parse_project_item(item_payload)
    return {
        "version": 1,
        "kind": "project_item_delete",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "project_owner": project.owner,
        "project_number": project.number,
        "project_title": project.title,
        "project": project_payload,
        "item": item_payload,
        "item_summary": {
            "id": item.id,
            "title": item.title,
            "content_type": item.content_type,
            "content_url": item.content_url,
            "issue_number": item.issue_number,
            "repository": item.repository,
        },
    }


def _require_danger(action: str, *, danger: bool) -> None:
    if not danger:
        raise RuntimeError(f"Refusing dangerous operation '{action}' without --danger.")


def _confirm_dangerous_action(
    *,
    prompt_message: str,
    assume_yes: bool,
    prompt: Callable[[str], str],
    is_tty: bool,
    out: Callable[[str], None],
) -> bool:
    if assume_yes:
        return True
    if not is_tty:
        out(
            "Non-interactive shell detected and --yes not set; skipping dangerous operation."
        )
        return False
    reply = prompt(f"{prompt_message} [y/N] ").strip().lower()
    return reply in {"y", "yes"}


def delete_project(
    *,
    repo_dir: Path,
    owner: str | None,
    project_number: int | None,
    project_title: str | None,
    danger: bool,
    assume_yes: bool,
    dry_run: bool,
    backup_dir: str | None,
    prompt: Callable[[str], str],
    is_tty: bool,
    out: Callable[[str], None] = print,
    err: Callable[[str], None] | None = None,
) -> ProjectMutationSummary:
    _require_danger("project delete", danger=danger)
    emit_err = _emit_err_factory(err)
    project = _find_existing_project(
        repo_dir=repo_dir,
        owner=owner,
        project_number=project_number,
        project_title=project_title,
    )

    if dry_run:
        out(
            f"[dry-run] backup project before delete: {project.owner}/#{project.number} ({project.title})"
        )
        out(
            f"[dry-run] delete project: {project.owner}/#{project.number} ({project.title})"
        )
        return ProjectMutationSummary(
            action="delete",
            owner=project.owner,
            project_number=project.number,
            project_title=project.title,
            failures=0,
            changed=True,
        )

    if not _confirm_dangerous_action(
        prompt_message=f"Delete project {project.owner}/#{project.number} ({project.title})?",
        assume_yes=assume_yes,
        prompt=prompt,
        is_tty=is_tty,
        out=out,
    ):
        out("Aborted.")
        return ProjectMutationSummary(
            action="delete",
            owner=project.owner,
            project_number=project.number,
            project_title=project.title,
            failures=0,
            changed=False,
        )

    project_payload = _load_project_view(repo_dir, project.owner, project.number)
    item_payloads = _load_project_items(repo_dir, project.owner, project.number, 500)
    backup_file, _ = _backup_paths(
        repo_dir=repo_dir,
        backup_dir=backup_dir,
        prefix=f"project-delete-{project.owner}-{project.number}",
    )
    _write_backup_file(
        backup_file,
        _project_backup_payload(
            action="project_delete",
            project=project,
            project_payload=project_payload,
            item_payloads=item_payloads,
        ),
    )
    undo_command = _build_undo_command(repo_dir, backup_file)

    cp = _run_gh(
        repo_dir,
        ["project", "delete", str(project.number), "--owner", project.owner],
    )
    if cp.returncode != 0:
        detail = (
            cp.stderr.strip()
            or cp.stdout.strip()
            or f"Failed deleting project #{project.number}."
        )
        emit_err(_project_scope_hint(detail))
        return ProjectMutationSummary(
            action="delete",
            owner=project.owner,
            project_number=project.number,
            project_title=project.title,
            failures=1,
            changed=False,
            backup_file=backup_file,
            undo_command=undo_command,
        )

    out(f"Deleted project: {project.owner}/#{project.number} ({project.title})")
    out(f"Backup written: {backup_file}")
    out(f"Undo with: {undo_command}")
    return ProjectMutationSummary(
        action="delete",
        owner=project.owner,
        project_number=project.number,
        project_title=project.title,
        failures=0,
        changed=True,
        backup_file=backup_file,
        undo_command=undo_command,
    )


def _find_project_item(
    *,
    repo_dir: Path,
    project: ProjectInfo,
    item_id: str | None,
    issue_number: int | None,
) -> tuple[dict[str, object], ProjectItemInfo]:
    if not item_id and issue_number is None:
        raise RuntimeError("Specify --item-id or --issue-number.")
    if item_id and issue_number is not None:
        raise RuntimeError("Use only one of --item-id or --issue-number.")

    for raw_item in _load_project_items(repo_dir, project.owner, project.number, 500):
        item = _parse_project_item(raw_item)
        if item_id and item.id == item_id.strip():
            return raw_item, item
        if issue_number is not None and item.issue_number == issue_number:
            return raw_item, item

    selector = item_id.strip() if item_id else f"issue #{issue_number}"
    raise RuntimeError(
        f"Project item not found in {project.owner}/#{project.number}: {selector}"
    )


def delete_project_item(
    *,
    repo_dir: Path,
    owner: str | None,
    project_number: int | None,
    project_title: str | None,
    item_id: str | None,
    issue_number: int | None,
    danger: bool,
    assume_yes: bool,
    dry_run: bool,
    backup_dir: str | None,
    prompt: Callable[[str], str],
    is_tty: bool,
    out: Callable[[str], None] = print,
    err: Callable[[str], None] | None = None,
) -> ProjectMutationSummary:
    _require_danger("project item delete", danger=danger)
    emit_err = _emit_err_factory(err)
    project = _find_existing_project(
        repo_dir=repo_dir,
        owner=owner,
        project_number=project_number,
        project_title=project_title,
    )
    raw_item, item = _find_project_item(
        repo_dir=repo_dir,
        project=project,
        item_id=item_id,
        issue_number=issue_number,
    )

    if dry_run:
        out(f"[dry-run] backup project item before delete: {item.title} ({item.id})")
        out(f"[dry-run] delete project item: {item.title} ({item.id})")
        return ProjectMutationSummary(
            action="item-delete",
            owner=project.owner,
            project_number=project.number,
            project_title=project.title,
            failures=0,
            changed=True,
        )

    if not _confirm_dangerous_action(
        prompt_message=(
            f"Delete project item '{item.title}' ({item.id}) from {project.owner}/#{project.number}?"
        ),
        assume_yes=assume_yes,
        prompt=prompt,
        is_tty=is_tty,
        out=out,
    ):
        out("Aborted.")
        return ProjectMutationSummary(
            action="item-delete",
            owner=project.owner,
            project_number=project.number,
            project_title=project.title,
            failures=0,
            changed=False,
        )

    project_payload = _load_project_view(repo_dir, project.owner, project.number)
    backup_file, _ = _backup_paths(
        repo_dir=repo_dir,
        backup_dir=backup_dir,
        prefix=f"project-item-delete-{project.owner}-{project.number}",
    )
    _write_backup_file(
        backup_file,
        _project_item_backup_payload(
            project=project,
            project_payload=project_payload,
            item_payload=raw_item,
        ),
    )
    undo_command = _build_undo_command(repo_dir, backup_file)

    cp = _run_gh(
        repo_dir,
        [
            "project",
            "item-delete",
            str(project.number),
            "--owner",
            project.owner,
            "--id",
            item.id,
        ],
    )
    if cp.returncode != 0:
        detail = (
            cp.stderr.strip()
            or cp.stdout.strip()
            or f"Failed deleting project item {item.id}."
        )
        emit_err(_project_scope_hint(detail))
        return ProjectMutationSummary(
            action="item-delete",
            owner=project.owner,
            project_number=project.number,
            project_title=project.title,
            failures=1,
            changed=False,
            backup_file=backup_file,
            undo_command=undo_command,
        )

    out(f"Deleted project item: {item.title} ({item.id})")
    out(f"Backup written: {backup_file}")
    out(f"Undo with: {undo_command}")
    return ProjectMutationSummary(
        action="item-delete",
        owner=project.owner,
        project_number=project.number,
        project_title=project.title,
        failures=0,
        changed=True,
        backup_file=backup_file,
        undo_command=undo_command,
    )


def _restore_item_to_project(
    *,
    repo_dir: Path,
    project_owner: str,
    project_number: int,
    raw_item: dict[str, object],
    out: Callable[[str], None],
) -> str | None:
    item = _parse_project_item(raw_item)
    if item.content_url:
        cp = _run_gh(
            repo_dir,
            [
                "project",
                "item-add",
                str(project_number),
                "--owner",
                project_owner,
                "--url",
                item.content_url,
            ],
        )
        if cp.returncode != 0:
            detail = (
                cp.stderr.strip()
                or cp.stdout.strip()
                or f"Failed restoring item: {item.title}"
            )
            raise RuntimeError(_project_scope_hint(detail))
        out(f"Restored project item: {item.title}")
        return item.id

    args = [
        "project",
        "item-create",
        str(project_number),
        "--owner",
        project_owner,
        "--title",
        item.title,
    ]
    if item.body:
        args.extend(["--body", item.body])
    cp = _run_gh(repo_dir, args)
    if cp.returncode != 0:
        detail = (
            cp.stderr.strip()
            or cp.stdout.strip()
            or f"Failed restoring draft item: {item.title}"
        )
        raise RuntimeError(_project_scope_hint(detail))
    out(f"Restored draft project item: {item.title}")

    try:
        payload = json.loads(cp.stdout or "{}")
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict):
        restored_id = payload.get("id")
        if isinstance(restored_id, str) and restored_id.strip():
            return restored_id.strip()
    return None


def _next_restore_title(repo_dir: Path, owner: str, title: str) -> str:
    existing_titles = {
        item.title for item in list_projects(repo_dir=repo_dir, owner=owner).projects
    }
    if title not in existing_titles:
        return title
    restored = f"{title} (Restored)"
    if restored not in existing_titles:
        return restored
    counter = 2
    while True:
        candidate = f"{title} (Restored {counter})"
        if candidate not in existing_titles:
            return candidate
        counter += 1


def undo_project_backup(
    *,
    repo_dir: Path,
    backup_file: Path,
    dry_run: bool,
    out: Callable[[str], None] = print,
) -> ProjectMutationSummary:
    try:
        payload = json.loads(backup_file.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"Backup file not found: {backup_file}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Backup file is not valid JSON: {backup_file}") from exc

    if not isinstance(payload, dict):
        raise RuntimeError("Backup file is not a valid project backup payload.")

    kind = payload.get("kind")
    if not isinstance(kind, str) or not kind.strip():
        raise RuntimeError("Backup file is missing backup kind metadata.")

    owner_value = payload.get("project_owner")
    if not isinstance(owner_value, str) or not owner_value.strip():
        raise RuntimeError("Backup file is missing project owner metadata.")
    owner = owner_value.strip()

    if kind == "project_delete":
        project_payload = payload.get("project")
        raw_items = payload.get("items")
        if not isinstance(project_payload, dict) or not isinstance(raw_items, list):
            raise RuntimeError("Backup file is missing project snapshot data.")
        title = project_payload.get("title")
        if not isinstance(title, str) or not title.strip():
            raise RuntimeError("Backup file is missing project title.")
        restore_title = _next_restore_title(repo_dir, owner, title.strip())
        description = project_payload.get("shortDescription")
        if not isinstance(description, str):
            description = project_payload.get("description")
        readme = project_payload.get("readme")
        visibility = _normalize_visibility(project_payload)

        if dry_run:
            out(
                f"[dry-run] restore project from backup: {restore_title} (owner: {owner})"
            )
            for raw_item in raw_items:
                if isinstance(raw_item, dict):
                    item = _parse_project_item(raw_item)
                    out(f"[dry-run] restore project item: {item.title}")
            return ProjectMutationSummary(
                action="undo",
                owner=owner,
                project_number=None,
                project_title=restore_title,
                failures=0,
                changed=True,
            )

        summary = create_project(
            repo_dir=repo_dir,
            owner=owner,
            project_title=restore_title,
            description=description if isinstance(description, str) else None,
            readme=readme if isinstance(readme, str) else None,
            visibility=visibility,
            dry_run=False,
            out=out,
        )
        if summary.project_number is None:
            raise RuntimeError(
                "Restored project creation did not resolve a project number."
            )
        restored_number = summary.project_number
        for raw_item in raw_items:
            if isinstance(raw_item, dict):
                _restore_item_to_project(
                    repo_dir=repo_dir,
                    project_owner=owner,
                    project_number=restored_number,
                    raw_item=raw_item,
                    out=out,
                )
        return ProjectMutationSummary(
            action="undo",
            owner=owner,
            project_number=restored_number,
            project_title=restore_title,
            failures=0,
            changed=True,
            backup_file=backup_file,
            restored_project_number=restored_number,
        )

    if kind == "project_item_delete":
        project_number_value = payload.get("project_number")
        raw_item = payload.get("item")
        if not isinstance(project_number_value, int) or not isinstance(raw_item, dict):
            raise RuntimeError("Backup file is missing project item snapshot data.")
        item = _parse_project_item(raw_item)
        if dry_run:
            out(
                f"[dry-run] restore project item: {item.title} -> {owner}/#{project_number_value}"
            )
            return ProjectMutationSummary(
                action="undo",
                owner=owner,
                project_number=project_number_value,
                project_title=None,
                failures=0,
                changed=True,
            )
        restored_item_id = _restore_item_to_project(
            repo_dir=repo_dir,
            project_owner=owner,
            project_number=project_number_value,
            raw_item=raw_item,
            out=out,
        )
        return ProjectMutationSummary(
            action="undo",
            owner=owner,
            project_number=project_number_value,
            project_title=None,
            failures=0,
            changed=True,
            backup_file=backup_file,
            restored_item_id=restored_item_id,
        )

    raise RuntimeError(f"Unsupported backup kind: {kind}")
