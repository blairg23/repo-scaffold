from __future__ import annotations

import argparse
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .auth_tokens import is_placeholder_token, resolve_gh_token
from .backlog_ops import (
    BacklogApplySummary,
    IssueDetail,
    apply_backlog,
    fetch_issue,
    resolve_authenticated_login,
    resolve_project_target_for_auth_check,
)
from .github_api import (
    branch_create,
    branch_delete,
    issue_assign,
    issue_close,
    issue_comment,
    issue_create,
    issue_label,
    issue_list,
    issue_update,
    pr_checks,
    pr_comment,
    pr_create,
    pr_list,
    pr_list_comments,
    pr_merge,
    pr_resolve_thread,
    pr_review_threads,
    pr_update,
    pr_view,
    token_from_repo,
)
from .backlog_import import build_backlog_import_file
from .create_ops import (
    CreateSummary,
    SettingsCheckSummary,
    apply_repository_settings,
    check_repository_settings,
    create_repository,
)
from .delete_ops import DeleteSummary, delete_repositories
from .generator import (
    SUPPORTED_LICENSE,
    ScaffoldConfig,
    build_ci_files,
    build_dependabot_files,
    build_scaffold_files,
    build_template_files,
    default_output_path,
    detect_languages_from_repo,
    parse_language_csv,
)
from .overwrite_policy import ApplySummary, OverwritePolicy, apply_files
from .project_ops import (
    DEFAULT_PROJECT_BACKUP_REL_DIR,
    ProjectItemsSummary,
    ProjectListSummary,
    ProjectMutationSummary,
    add_project_item,
    create_project,
    delete_project,
    delete_project_item,
    edit_project,
    link_project_repo,
    list_project_items,
    list_projects,
    setup_project_statuses,
    sync_project_metadata,
    undo_project_backup,
    update_project_item_status,
    view_project,
)

DEFAULT_INIT_NAME_PREFIX = "repo-scaffold-e2e"
DEFAULT_INIT_LANGUAGES = "go,python,react"
DEFAULT_BACKLOG_REL_PATH = "artifacts/backlog/issues.json"
DEFAULT_LOCAL_BACKLOG_SLUG_DIR = "local/backlog"
DEFAULT_MARKDOWN_BACKLOG_REL_DIR = "artifacts/tickets"
BACKLOG_TICKETS_DIR_ENV = "GITHUB_TICKETS_DIR"


def _repo_name_from_repo_ref(raw: str | None) -> str | None:
    if not raw:
        return None
    normalized = _normalize_owner_repo(raw, allow_host_prefix=True)
    if normalized:
        return normalized.split("/", 1)[1]
    return None


def _default_init_name() -> str:
    env_repo = (os.environ.get("GITHUB_REPO") or "").strip()
    if env_repo:
        return env_repo
    full_repo = os.environ.get("GH_REPO") or os.environ.get("GITHUB_REPOSITORY")
    full_repo_name = _repo_name_from_repo_ref(full_repo)
    if full_repo_name:
        return full_repo_name
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"{DEFAULT_INIT_NAME_PREFIX}-{stamp}"


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


def _seed_env_from_dotenv(path: Path) -> None:
    for key, value in _load_env_file(path).items():
        os.environ.setdefault(key, value)

    resolved_token = resolve_gh_token(os.environ)
    current_gh_token = os.environ.get("GH_TOKEN")
    if resolved_token and (
        not current_gh_token or is_placeholder_token(current_gh_token)
    ):
        os.environ["GH_TOKEN"] = resolved_token

    if not os.environ.get("GITHUB_ORG") and os.environ.get("github_org"):
        os.environ["GITHUB_ORG"] = os.environ["github_org"]
    if not os.environ.get("GITHUB_REPO") and os.environ.get("github_repo"):
        os.environ["GITHUB_REPO"] = os.environ["github_repo"]
    if not os.environ.get("GH_REPO"):
        if os.environ.get("GITHUB_REPOSITORY"):
            os.environ["GH_REPO"] = os.environ["GITHUB_REPOSITORY"]
        elif os.environ.get("github_full_repo"):
            os.environ["GH_REPO"] = os.environ["github_full_repo"]

    if not os.environ.get("GITHUB_PROJECT_TITLE") and os.environ.get(
        "github_project_title"
    ):
        os.environ["GITHUB_PROJECT_TITLE"] = os.environ["github_project_title"]
    if not os.environ.get("GITHUB_PROJECT_TITLE_TEMPLATE") and os.environ.get(
        "github_project_title_template"
    ):
        os.environ["GITHUB_PROJECT_TITLE_TEMPLATE"] = os.environ[
            "github_project_title_template"
        ]
    if not os.environ.get(BACKLOG_TICKETS_DIR_ENV) and os.environ.get(
        "github_tickets_dir"
    ):
        os.environ[BACKLOG_TICKETS_DIR_ENV] = os.environ["github_tickets_dir"]


def _normalize_owner_repo(raw: str, *, allow_host_prefix: bool) -> str | None:
    parts = [part.strip() for part in raw.strip().split("/") if part.strip()]
    if len(parts) == 2:
        return f"{parts[0]}/{parts[1]}"
    if allow_host_prefix and len(parts) == 3:
        return f"{parts[1]}/{parts[2]}"
    return None


def _resolve_repo_from_args_or_env(
    *,
    repo: str | None,
    fallback_name: str | None,
) -> tuple[str | None, str | None]:
    if repo is not None:
        normalized = _normalize_owner_repo(repo, allow_host_prefix=False)
        if normalized is None:
            return None, "Error: --repo must be in owner/repo format."
        return normalized, None

    full_repo = os.environ.get("GH_REPO") or os.environ.get("GITHUB_REPOSITORY")
    if full_repo:
        normalized = _normalize_owner_repo(full_repo, allow_host_prefix=True)
        if normalized:
            return normalized, None

    owner = (os.environ.get("GITHUB_ORG") or "").strip()
    name = (os.environ.get("GITHUB_REPO") or fallback_name or "").strip()
    if owner and name:
        return f"{owner}/{name}", None

    return (
        None,
        "Error: could not resolve target repo. Pass --repo owner/repo or set GH_REPO (or GITHUB_ORG + GITHUB_REPO) in .env.",
    )


def _local_backlog_slug_path(repo: str) -> Path:
    return Path.cwd() / DEFAULT_LOCAL_BACKLOG_SLUG_DIR / repo / "issues.json"


def _resolve_backlog_file_path(
    *, repo_dir: Path, file_arg: str | None, repo: str | None = None
) -> Path:
    if file_arg:
        backlog_file = Path(file_arg)
        return backlog_file if backlog_file.is_absolute() else (repo_dir / backlog_file)

    if repo:
        slug_path = _local_backlog_slug_path(repo)
        if slug_path.exists():
            return slug_path

    return repo_dir / DEFAULT_BACKLOG_REL_PATH


def _resolve_markdown_source_dir(*, repo_dir: Path, source_arg: str | None) -> Path:
    source_value = source_arg or (os.environ.get(BACKLOG_TICKETS_DIR_ENV) or "").strip()
    if source_value:
        source_dir = Path(source_value)
        return source_dir if source_dir.is_absolute() else (repo_dir / source_dir)
    return repo_dir / DEFAULT_MARKDOWN_BACKLOG_REL_DIR


def _find_existing_markdown_source_dir(repo_dir: Path) -> Path | None:
    env_source = (os.environ.get(BACKLOG_TICKETS_DIR_ENV) or "").strip()
    if env_source:
        candidate = Path(env_source)
        resolved = candidate if candidate.is_absolute() else (repo_dir / candidate)
        if resolved.exists():
            return resolved
        raise RuntimeError(
            f"Error: {BACKLOG_TICKETS_DIR_ENV} points to a missing markdown source directory: {resolved}"
        )
    candidate = repo_dir / DEFAULT_MARKDOWN_BACKLOG_REL_DIR
    return candidate if candidate.exists() else None


def _seed_env_for_parsed_mode(ns: argparse.Namespace) -> None:
    _seed_env_from_dotenv(Path.cwd() / ".env")
    if ns.mode == "create" and getattr(ns, "path", None):
        _seed_env_from_dotenv(Path(ns.path) / ".env")
    if ns.mode in {"apply", "import", "project"} and hasattr(ns, "path"):
        _seed_env_from_dotenv(Path(ns.path) / ".env")


def _add_overwrite_policy_args(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--yes", action="store_true", help="Assume yes to overwrite prompts"
    )
    group.add_argument(
        "--no", action="store_true", help="Assume no to overwrite prompts"
    )
    group.add_argument(
        "--force", action="store_true", help="Overwrite without prompting"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned actions without changing state",
    )
    parser.add_argument(
        "--backup",
        action="store_true",
        help="Write <file>.bak.<timestamp> before overwrite",
    )


def _add_scaffold_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--name",
        help="Repository name (default: GITHUB_REPO, otherwise repo-scaffold-e2e-<UTC timestamp>)",
    )
    parser.add_argument(
        "--languages",
        default=DEFAULT_INIT_LANGUAGES,
        help=f"Comma-separated language list (default: {DEFAULT_INIT_LANGUAGES})",
    )
    parser.add_argument("--owner", help="GitHub owner (user or org)")
    parser.add_argument(
        "--license",
        dest="license_id",
        default=SUPPORTED_LICENSE,
        choices=[SUPPORTED_LICENSE],
        help="License identifier (only apache-2.0 is currently supported)",
    )
    parser.add_argument("--out", help="Output path (default: ./out/<name>)")


def _resolve_body(body: str | None, body_file: str | None) -> str | None:
    if body is not None and body_file is not None:
        raise SystemExit("error: --body and --body-file are mutually exclusive")
    if body_file is not None:
        try:
            return Path(body_file).read_text(encoding="utf-8")
        except OSError as exc:
            raise SystemExit(
                f"error: cannot read --body-file {body_file!r}: {exc}"
            ) from exc
        except UnicodeDecodeError as exc:
            raise SystemExit(
                f"error: --body-file {body_file!r} is not valid UTF-8: {exc}"
            ) from exc
    return body


def _add_apply_target_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--path", default=".", help="Target repository path (default: .)"
    )
    parser.add_argument("--owner", help="GitHub owner for generated metadata")
    parser.add_argument(
        "--name", help="Repository name override (defaults to folder name)"
    )


def _add_project_target_args(parser: argparse.ArgumentParser) -> None:
    project_group = parser.add_mutually_exclusive_group(required=True)
    project_group.add_argument(
        "--project-number",
        type=int,
        help="GitHub Project number to target",
    )
    project_group.add_argument(
        "--project-title",
        help="GitHub Project title to target",
    )
    parser.add_argument(
        "--project-owner",
        help="GitHub login/org owning the project (defaults to env or authenticated login)",
    )


def _add_danger_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--danger",
        action="store_true",
        help="Required acknowledgement for destructive project operations",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation prompt for dangerous project operations",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview the dangerous operation without changing state",
    )
    parser.add_argument(
        "--backup-dir",
        help=(
            "Backup directory for destructive project operations "
            f"(default: <path>/{DEFAULT_PROJECT_BACKUP_REL_DIR})"
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="repo-scaffold",
        description="Repository operations toolkit for scaffold create/init/apply/delete workflows.",
    )
    subparsers = parser.add_subparsers(dest="mode", required=True)

    create_cmd = subparsers.add_parser(
        "create", help="Create/push remote repo and apply baseline settings"
    )
    create_cmd.add_argument(
        "--path",
        help="Local repository path (default: ./out/<repo-name>; auto-inits when missing/empty)",
    )
    create_cmd.add_argument("--repo", help="Target GitHub repo in owner/repo format")
    create_cmd.add_argument(
        "--owner", help="GitHub owner override (used when --repo is omitted)"
    )
    create_cmd.add_argument(
        "--name",
        help="Repository name fallback when --repo is omitted",
    )
    create_cmd.add_argument(
        "--languages",
        default=DEFAULT_INIT_LANGUAGES,
        help=f"Languages for auto-init when path is missing/empty (default: {DEFAULT_INIT_LANGUAGES})",
    )
    create_cmd.add_argument(
        "--visibility",
        default="public",
        choices=["private", "public", "internal"],
        help="Visibility used when creating the repository",
    )
    create_cmd.add_argument(
        "--skip-settings",
        action="store_true",
        help="Skip applying repository settings/protections after creation/push",
    )
    create_cmd.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned actions without changing state",
    )

    delete_cmd = subparsers.add_parser(
        "delete", help="Delete matching GitHub repositories"
    )
    delete_cmd.add_argument(
        "--owner", help="GitHub owner/org (defaults to .env/environment)"
    )
    delete_cmd.add_argument(
        "--prefix",
        default="repo-scaffold-e2e",
        help="Prefix match target: deletes NAME and NAME-* when --exact is not provided",
    )
    delete_cmd.add_argument(
        "--exact",
        action="append",
        default=[],
        help="Exact repository name to delete (repeatable). When provided, prefix matching is ignored.",
    )
    delete_cmd.add_argument(
        "--cleanup",
        action="store_true",
        help="Also delete matching local directories/artifacts.",
    )
    delete_cmd.add_argument(
        "--local-only",
        dest="local_only",
        action="store_true",
        help="Delete matching local directories only (no remote GitHub deletion).",
    )
    delete_cmd.add_argument(
        "--local-root",
        action="append",
        default=[],
        help="Local root path to scan for matching directories (repeatable). Defaults: /tmp and ./out.",
    )
    delete_mode = delete_cmd.add_mutually_exclusive_group()
    delete_mode.add_argument(
        "--apply",
        action="store_true",
        dest="do_apply",
        help="Actually delete repositories. Default behavior is preview-only.",
    )
    delete_mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview matched repositories without deleting (default behavior).",
    )
    delete_cmd.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation prompt when --apply is used",
    )

    init_cmd = subparsers.add_parser("init", help="Generate a new repository scaffold")
    _add_scaffold_args(init_cmd)
    _add_overwrite_policy_args(init_cmd)

    apply_parent = argparse.ArgumentParser(add_help=False)
    _add_overwrite_policy_args(apply_parent)

    apply_cmd = subparsers.add_parser(
        "apply", help="Apply capabilities to an existing repo"
    )
    apply_sub = apply_cmd.add_subparsers(dest="apply_command", required=True)

    apply_templates = apply_sub.add_parser(
        "templates", parents=[apply_parent], help="Apply GitHub templates"
    )
    _add_apply_target_args(apply_templates)

    apply_ci = apply_sub.add_parser(
        "ci", parents=[apply_parent], help="Apply CI workflow"
    )
    _add_apply_target_args(apply_ci)
    apply_ci.add_argument(
        "--languages",
        required=True,
        help="Comma-separated language list: go, gin, python, react",
    )

    apply_dependabot = apply_sub.add_parser(
        "dependabot", parents=[apply_parent], help="Apply Dependabot configuration"
    )
    _add_apply_target_args(apply_dependabot)
    apply_dependabot.add_argument(
        "--languages",
        help="Optional language override. If omitted, languages are inferred from repo files.",
    )
    apply_dependabot.add_argument(
        "--low-noise",
        action="store_true",
        help="Use low-noise grouped weekly defaults",
    )

    apply_backlog_cmd = apply_sub.add_parser(
        "backlog",
        parents=[apply_parent],
        help="Create milestones/issues in GitHub from backlog JSON",
    )
    apply_backlog_cmd.add_argument(
        "repo_ref",
        nargs="?",
        help="Target GitHub repo (owner/repo). Shorthand for --repo.",
    )
    apply_backlog_cmd.add_argument(
        "--path", default=".", help="Repo path containing backlog data (default: .)"
    )
    apply_backlog_cmd.add_argument("--repo", help="Target GitHub repo (owner/repo)")
    apply_backlog_cmd.add_argument(
        "--file",
        help=(
            "Backlog JSON path. If omitted, auto-imports from markdown when "
            "<repo-path>/artifacts/tickets (or fallback source dirs) exists; otherwise resolves "
            "in this order: ./local/backlog/<owner>/<repo>/issues.json (when --repo is set), "
            "<repo-path>/artifacts/backlog/issues.json"
        ),
    )
    apply_backlog_cmd.add_argument(
        "--with-project",
        action="store_true",
        help="Enable project integration. If no project is specified, defaults to '<repo-name> Roadmap'.",
    )
    project_group = apply_backlog_cmd.add_mutually_exclusive_group()
    project_group.add_argument(
        "--project-number",
        type=int,
        help="GitHub Project number to attach issues to",
    )
    project_group.add_argument(
        "--project-title",
        help="GitHub Project title to use (creates it if missing)",
    )
    apply_backlog_cmd.add_argument(
        "--project-owner",
        help="GitHub login/org owning the project (defaults to repo owner)",
    )
    apply_backlog_cmd.add_argument(
        "--auth-check",
        action="store_true",
        help="Validate GitHub auth/token for this repo context and exit",
    )

    apply_rules = apply_sub.add_parser(
        "rules",
        parents=[apply_parent],
        help="Preview or apply GitHub repository settings",
    )
    apply_rules.add_argument("--repo", help="Target GitHub repo (owner/repo)")
    apply_rules.add_argument(
        "--apply",
        action="store_true",
        dest="do_apply",
        help="Execute commands instead of only printing them",
    )

    check = subparsers.add_parser(
        "check",
        help="Check GitHub settings/capabilities for drift",
    )
    check_sub = check.add_subparsers(dest="check_command", required=True)
    check_rules = check_sub.add_parser(
        "rules",
        help="Check merge settings, managed ruleset, and security defaults",
    )
    check_rules.add_argument("--repo", help="Target GitHub repo (owner/repo)")

    project_cmd = subparsers.add_parser(
        "project",
        help="Manage GitHub Projects with explicit destructive-op guards",
    )
    project_sub = project_cmd.add_subparsers(dest="project_command", required=True)

    project_list_cmd = project_sub.add_parser("list", help="List projects for an owner")
    project_list_cmd.add_argument(
        "--path",
        default=".",
        help="Workspace path used for .env resolution and backups (default: .)",
    )
    project_list_cmd.add_argument(
        "--project-owner",
        help="GitHub login/org owning the projects (defaults to env or authenticated login)",
    )

    project_view_cmd = project_sub.add_parser(
        "view", help="View metadata for a single project"
    )
    project_view_cmd.add_argument(
        "--path",
        default=".",
        help="Workspace path used for .env resolution and backups (default: .)",
    )
    _add_project_target_args(project_view_cmd)

    project_items_cmd = project_sub.add_parser(
        "items", help="List the contents of a single project"
    )
    project_items_cmd.add_argument(
        "--path",
        default=".",
        help="Workspace path used for .env resolution and backups (default: .)",
    )
    _add_project_target_args(project_items_cmd)
    project_items_cmd.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum number of project items to fetch (default: 100)",
    )

    project_sync_cmd = project_sub.add_parser(
        "sync-metadata",
        help="Write .repo-scaffold/project.json for a resolved project",
    )
    project_sync_cmd.add_argument(
        "--path",
        default=".",
        help="Workspace path used for .env resolution and metadata output (default: .)",
    )
    _add_project_target_args(project_sync_cmd)

    project_create_cmd = project_sub.add_parser("create", help="Create a new project")
    project_create_cmd.add_argument(
        "--path",
        default=".",
        help="Workspace path used for .env resolution and backups (default: .)",
    )
    project_create_cmd.add_argument(
        "--project-owner",
        help="GitHub login/org owning the project (defaults to env or authenticated login)",
    )
    project_create_cmd.add_argument(
        "--project-title",
        required=True,
        help="Title for the new project",
    )
    project_create_cmd.add_argument(
        "--description",
        help="Optional project description",
    )
    project_create_cmd.add_argument(
        "--readme",
        help="Optional project readme markdown",
    )
    project_create_cmd.add_argument(
        "--visibility",
        choices=["PRIVATE", "PUBLIC", "private", "public"],
        help="Optional project visibility override",
    )
    project_create_cmd.add_argument(
        "--repo",
        help="Link this repo (owner/repo) as the project default repository and setup standard statuses",
    )
    project_create_cmd.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview the project creation without changing state",
    )

    project_setup_statuses_cmd = project_sub.add_parser(
        "setup-statuses",
        help="Configure the standard Status field options on an existing project",
    )
    project_setup_statuses_cmd.add_argument(
        "--path",
        default=".",
        help="Workspace path used for .env resolution (default: .)",
    )
    _add_project_target_args(project_setup_statuses_cmd)

    project_edit_cmd = project_sub.add_parser(
        "edit", help="Edit metadata for an existing project"
    )
    project_edit_cmd.add_argument(
        "--path",
        default=".",
        help="Workspace path used for .env resolution and backups (default: .)",
    )
    _add_project_target_args(project_edit_cmd)
    project_edit_cmd.add_argument(
        "--title",
        help="Rename the project",
    )
    project_edit_cmd.add_argument(
        "--description",
        help="Set the project description",
    )
    project_edit_cmd.add_argument(
        "--readme",
        help="Set the project readme markdown",
    )
    project_edit_cmd.add_argument(
        "--visibility",
        choices=["PRIVATE", "PUBLIC", "private", "public"],
        help="Set the project visibility",
    )
    project_edit_cmd.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview the project edit without changing state",
    )

    project_delete_cmd = project_sub.add_parser(
        "delete", help="Delete a project (dangerous; backup + confirmation required)"
    )
    project_delete_cmd.add_argument(
        "--path",
        default=".",
        help="Workspace path used for .env resolution and backups (default: .)",
    )
    _add_project_target_args(project_delete_cmd)
    _add_danger_args(project_delete_cmd)

    project_item_add_cmd = project_sub.add_parser(
        "item-add",
        help="Add an existing issue to a project",
    )
    project_item_add_cmd.add_argument(
        "--path",
        default=".",
        help="Workspace path used for .env resolution (default: .)",
    )
    _add_project_target_args(project_item_add_cmd)
    project_item_add_cmd.add_argument(
        "--issue-number",
        required=True,
        type=int,
        dest="issue_number",
        help="GitHub issue number to add to the project",
    )
    project_item_add_cmd.add_argument(
        "--repo",
        required=True,
        help="Repo containing the issue (owner/repo)",
    )

    project_item_status_cmd = project_sub.add_parser(
        "item-status",
        help="Move a project board card to a different status column",
    )
    project_item_status_cmd.add_argument(
        "--path",
        default=".",
        help="Workspace path used for .env resolution (default: .)",
    )
    _add_project_target_args(project_item_status_cmd)
    project_item_status_cmd.add_argument(
        "--repo",
        required=True,
        help="Repo containing the issue (owner/repo)",
    )
    project_item_status_cmd.add_argument(
        "--issue-number",
        required=True,
        type=int,
        dest="issue_number",
        help="Issue number to update",
    )
    project_item_status_cmd.add_argument(
        "--status",
        required=True,
        help="Target status column name (e.g. 'In Progress', 'Done')",
    )

    project_item_delete_cmd = project_sub.add_parser(
        "item-delete",
        help="Delete a project item by item id or linked issue number (dangerous)",
    )
    project_item_delete_cmd.add_argument(
        "--path",
        default=".",
        help="Workspace path used for .env resolution and backups (default: .)",
    )
    _add_project_target_args(project_item_delete_cmd)
    item_group = project_item_delete_cmd.add_mutually_exclusive_group(required=True)
    item_group.add_argument(
        "--item-id",
        help="Exact project item id to delete",
    )
    item_group.add_argument(
        "--issue-number",
        type=int,
        help="Linked GitHub issue number for the project item to delete",
    )
    _add_danger_args(project_item_delete_cmd)

    project_link_repo_cmd = project_sub.add_parser(
        "link-repo", help="Link a project to a GitHub repository"
    )
    project_link_repo_cmd.add_argument(
        "--path",
        default=".",
        help="Workspace path used for .env resolution (default: .)",
    )
    _add_project_target_args(project_link_repo_cmd)
    project_link_repo_cmd.add_argument(
        "--repo",
        required=True,
        help="Repository to link (owner/repo)",
    )

    project_undo_cmd = project_sub.add_parser(
        "undo", help="Undo a destructive project backup snapshot"
    )
    project_undo_cmd.add_argument(
        "--path",
        default=".",
        help="Workspace path used for .env resolution and backups (default: .)",
    )
    project_undo_cmd.add_argument(
        "--backup-file",
        required=True,
        help="Backup snapshot JSON produced by a dangerous project operation",
    )
    project_undo_cmd.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview the undo without changing state",
    )

    import_cmd = subparsers.add_parser(
        "import",
        help="Import markdown artifacts into repo-scaffold formats",
    )
    import_sub = import_cmd.add_subparsers(dest="import_command", required=True)
    import_backlog = import_sub.add_parser(
        "backlog",
        parents=[apply_parent],
        help="Import markdown backlog files into artifacts/backlog/issues.json",
    )
    import_backlog.add_argument(
        "--path",
        default=".",
        help="Target repository path containing markdown backlog notes (default: .)",
    )
    import_backlog.add_argument(
        "--source",
        help=(
            "Markdown source directory "
            "(default: <path>/artifacts/tickets; env override: GITHUB_TICKETS_DIR)"
        ),
    )
    import_backlog.add_argument(
        "--repo",
        help=(
            "Target GitHub repo (owner/repo). When provided and --out is omitted, "
            "output defaults to local/backlog/<owner>/<repo>/issues.json"
        ),
    )
    import_backlog.add_argument(
        "--out",
        help=(
            "Backlog JSON output path. Defaults to local/backlog/<owner>/<repo>/issues.json "
            "when --repo is provided, otherwise <path>/artifacts/backlog/issues.json"
        ),
    )

    issue_cmd = subparsers.add_parser("issue", help="Query GitHub issues")
    issue_sub = issue_cmd.add_subparsers(dest="issue_command", required=True)
    issue_view_cmd = issue_sub.add_parser(
        "view", help="View details for a single issue"
    )
    issue_view_cmd.add_argument(
        "--repo",
        required=True,
        help="Target GitHub repo (owner/repo)",
    )
    issue_view_cmd.add_argument(
        "--issue-number",
        type=int,
        required=True,
        help="Issue number to fetch",
    )
    issue_view_cmd.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output structured JSON instead of human-readable text",
    )

    issue_list_cmd = issue_sub.add_parser("list", help="List issues")
    issue_list_cmd.add_argument("--repo", required=True)
    issue_list_cmd.add_argument(
        "--state", default="open", choices=["open", "closed", "all"]
    )
    issue_list_cmd.add_argument("--label", default=None, help="Filter by label name")
    issue_list_cmd.add_argument("--json", action="store_true", dest="json_output")

    issue_create_cmd = issue_sub.add_parser("create", help="Create a new issue")
    issue_create_cmd.add_argument("--repo", required=True)
    issue_create_cmd.add_argument("--title", required=True)
    issue_create_cmd.add_argument("--body", default=None)
    issue_create_cmd.add_argument(
        "--body-file",
        dest="body_file",
        default=None,
        metavar="PATH",
        help="Read body from a UTF-8 file (mutually exclusive with --body)",
    )
    issue_create_cmd.add_argument(
        "--label", action="append", dest="labels", metavar="LABEL"
    )
    issue_create_cmd.add_argument(
        "--assignee", action="append", dest="assignees", metavar="USER"
    )

    issue_close_cmd = issue_sub.add_parser("close", help="Close an issue")
    issue_close_cmd.add_argument("--repo", required=True)
    issue_close_cmd.add_argument("--issue-number", type=int, required=True)

    issue_comment_cmd = issue_sub.add_parser(
        "comment", help="Post a comment on an issue"
    )
    issue_comment_cmd.add_argument("--repo", required=True)
    issue_comment_cmd.add_argument("--issue-number", type=int, required=True)
    issue_comment_cmd.add_argument("--body", default=None)
    issue_comment_cmd.add_argument(
        "--body-file",
        dest="body_file",
        default=None,
        metavar="PATH",
        help="Read body from a UTF-8 file (mutually exclusive with --body)",
    )

    issue_label_cmd = issue_sub.add_parser(
        "label", help="Add or remove labels on an issue"
    )
    issue_label_cmd.add_argument("--repo", required=True)
    issue_label_cmd.add_argument("--issue-number", type=int, required=True)
    issue_label_cmd.add_argument(
        "--add", action="append", dest="add_labels", metavar="LABEL"
    )
    issue_label_cmd.add_argument(
        "--remove", action="append", dest="remove_labels", metavar="LABEL"
    )

    issue_assign_cmd = issue_sub.add_parser(
        "assign", help="Add or remove assignees on an issue"
    )
    issue_assign_cmd.add_argument("--repo", required=True)
    issue_assign_cmd.add_argument("--issue-number", type=int, required=True)
    issue_assign_cmd.add_argument(
        "--add", action="append", dest="add_users", metavar="USER"
    )
    issue_assign_cmd.add_argument(
        "--remove", action="append", dest="remove_users", metavar="USER"
    )

    issue_update_cmd = issue_sub.add_parser("update", help="Update an existing issue")
    issue_update_cmd.add_argument("--repo", required=True)
    issue_update_cmd.add_argument(
        "--issue-number", type=int, required=True, dest="issue_number"
    )
    issue_update_cmd.add_argument("--title", default=None)
    issue_update_cmd.add_argument("--body", default=None)
    issue_update_cmd.add_argument(
        "--body-file",
        dest="body_file",
        default=None,
        metavar="PATH",
        help="Read body from a UTF-8 file (mutually exclusive with --body)",
    )
    issue_update_cmd.add_argument("--state", choices=["open", "closed"], default=None)

    pr_cmd = subparsers.add_parser("pr", help="Interact with GitHub pull requests")
    pr_sub = pr_cmd.add_subparsers(dest="pr_command", required=True)

    pr_list_cmd = pr_sub.add_parser("list", help="List open pull requests")
    pr_list_cmd.add_argument(
        "--repo", required=True, help="Target GitHub repo (owner/repo)"
    )
    pr_list_cmd.add_argument("--json", action="store_true", dest="json_output")

    pr_view_cmd = pr_sub.add_parser("view", help="View a single pull request")
    pr_view_cmd.add_argument(
        "--repo", required=True, help="Target GitHub repo (owner/repo)"
    )
    pr_view_cmd.add_argument("--pr-number", type=int, required=True)
    pr_view_cmd.add_argument("--json", action="store_true", dest="json_output")

    pr_comment_cmd = pr_sub.add_parser("comment", help="Post a review comment on a PR")
    pr_comment_cmd.add_argument("--repo", required=True)
    pr_comment_cmd.add_argument("--pr-number", type=int, required=True)
    pr_comment_cmd.add_argument("--body", default=None)
    pr_comment_cmd.add_argument(
        "--body-file",
        dest="body_file",
        default=None,
        metavar="PATH",
        help="Read body from a UTF-8 file (mutually exclusive with --body)",
    )
    pr_comment_cmd.add_argument(
        "--reply-to", type=int, dest="reply_to", help="Comment ID to reply to"
    )

    pr_create_cmd = pr_sub.add_parser("create", help="Open a new pull request")
    pr_create_cmd.add_argument("--repo", required=True)
    pr_create_cmd.add_argument("--title", required=True)
    pr_create_cmd.add_argument("--body", default=None)
    pr_create_cmd.add_argument(
        "--body-file",
        dest="body_file",
        default=None,
        metavar="PATH",
        help="Read body from a UTF-8 file (mutually exclusive with --body)",
    )
    pr_create_cmd.add_argument("--head", required=True, help="Source branch")
    pr_create_cmd.add_argument(
        "--base", default="main", help="Target branch (default: main)"
    )
    pr_create_cmd.add_argument("--draft", action="store_true")

    pr_update_cmd = pr_sub.add_parser("update", help="Update an existing pull request")
    pr_update_cmd.add_argument("--repo", required=True)
    pr_update_cmd.add_argument("--pr-number", required=True, type=int, dest="pr_number")
    pr_update_cmd.add_argument("--title", default=None)
    pr_update_cmd.add_argument("--body", default=None)
    pr_update_cmd.add_argument(
        "--body-file",
        dest="body_file",
        default=None,
        metavar="PATH",
        help="Read body from a UTF-8 file (mutually exclusive with --body)",
    )

    pr_resolve_cmd = pr_sub.add_parser(
        "resolve-thread", help="Resolve a PR review thread"
    )
    pr_resolve_cmd.add_argument("--repo", required=True)
    pr_resolve_cmd.add_argument("--thread-id", required=True, dest="thread_id")

    pr_merge_cmd = pr_sub.add_parser("merge", help="Merge a pull request")
    pr_merge_cmd.add_argument("--repo", required=True)
    pr_merge_cmd.add_argument("--pr-number", required=True, type=int, dest="pr_number")
    pr_merge_cmd.add_argument(
        "--method",
        default="squash",
        choices=["squash", "merge", "rebase"],
        help="Merge method (default: squash)",
    )

    pr_checks_cmd = pr_sub.add_parser("checks", help="Show CI check statuses for a PR")
    pr_checks_cmd.add_argument("--repo", required=True)
    pr_checks_cmd.add_argument("--pr-number", required=True, type=int, dest="pr_number")
    pr_checks_cmd.add_argument("--json", action="store_true", dest="json_output")

    pr_review_threads_cmd = pr_sub.add_parser(
        "review-threads", help="List review threads (comments) on a PR"
    )
    pr_review_threads_cmd.add_argument("--repo", required=True)
    pr_review_threads_cmd.add_argument(
        "--pr-number", required=True, type=int, dest="pr_number"
    )
    pr_review_threads_cmd.add_argument(
        "--json", action="store_true", dest="json_output"
    )

    pr_list_comments_cmd = pr_sub.add_parser(
        "list-comments", help="List all inline review comments on a PR"
    )
    pr_list_comments_cmd.add_argument("--repo", required=True)
    pr_list_comments_cmd.add_argument(
        "--pr-number", required=True, type=int, dest="pr_number"
    )
    pr_list_comments_cmd.add_argument("--json", action="store_true", dest="json_output")

    branch_cmd = subparsers.add_parser("branch", help="Manage GitHub branches")
    branch_sub = branch_cmd.add_subparsers(dest="branch_command", required=True)

    branch_create_cmd = branch_sub.add_parser("create", help="Create a new branch")
    branch_create_cmd.add_argument("--repo", required=True)
    branch_create_cmd.add_argument("--name", required=True, help="New branch name")
    branch_create_cmd.add_argument(
        "--from", default="main", dest="base", help="Base branch (default: main)"
    )

    branch_delete_cmd = branch_sub.add_parser("delete", help="Delete a branch")
    branch_delete_cmd.add_argument("--repo", required=True)
    branch_delete_cmd.add_argument("--name", required=True, help="Branch to delete")

    return parser


def _normalize_argv(argv: list[str] | None) -> list[str]:
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] in {"-h", "--help"}:
        return raw
    if raw and raw[0] in {
        "create",
        "init",
        "apply",
        "check",
        "delete",
        "import",
        "project",
        "issue",
        "pr",
        "branch",
    }:
        return raw
    # Backward-compatible behavior: previous root command maps to init.
    return ["init", *raw]


def _policy_from_ns(ns: argparse.Namespace) -> OverwritePolicy:
    return OverwritePolicy(
        yes=getattr(ns, "yes", False),
        no=getattr(ns, "no", False),
        force=getattr(ns, "force", False),
        dry_run=getattr(ns, "dry_run", False),
        backup=getattr(ns, "backup", False),
    )


def _parse_languages_or_die(
    parser: argparse.ArgumentParser, raw: str
) -> tuple[str, ...]:
    try:
        return parse_language_csv(raw)
    except ValueError as exc:
        parser.error(str(exc))
        raise RuntimeError("unreachable")


def _print_file_summary(summary: ApplySummary, *, dry_run: bool) -> None:
    print("")
    print("Summary:")
    if dry_run:
        print("  mode: dry-run")
    print(f"  created: {summary.created}")
    print(f"  overwritten: {summary.overwritten}")
    print(f"  skipped: {summary.skipped}")
    print(f"  failures: {summary.failures}")


def _run_file_apply(files: list, policy: OverwritePolicy) -> int:
    summary = apply_files(
        files,
        policy,
        prompt=input,
        is_tty=sys.stdin.isatty(),
    )
    _print_file_summary(summary, dry_run=policy.dry_run)
    return 1 if summary.failures > 0 else 0


def main(argv: list[str] | None = None) -> int:
    _seed_env_from_dotenv(Path.cwd() / ".env")
    parser = build_parser()
    ns = parser.parse_args(_normalize_argv(argv))
    _seed_env_for_parsed_mode(ns)

    if ns.mode == "create":
        repo_name_hint = (
            _repo_name_from_repo_ref(ns.repo)
            or (ns.name or "").strip()
            or _default_init_name()
        )
        repo_dir = Path(ns.path) if ns.path else default_output_path(repo_name_hint)
        if repo_dir.exists() and not repo_dir.is_dir():
            print(
                f"Error: local repo path exists and is not a directory: {repo_dir}",
                file=sys.stderr,
            )
            return 2

        needs_init = not repo_dir.exists()
        if repo_dir.exists() and repo_dir.is_dir():
            needs_init = not any(repo_dir.iterdir())

        if needs_init:
            languages = _parse_languages_or_die(parser, ns.languages)
            cfg = ScaffoldConfig(
                name=repo_name_hint,
                languages=languages,
                owner=ns.owner,
                license_id=SUPPORTED_LICENSE,
                out_dir=repo_dir,
            )
            print(f"{'[dry-run] ' if ns.dry_run else ''}init scaffold: {repo_dir}")
            init_summary = apply_files(
                build_scaffold_files(cfg),
                OverwritePolicy(yes=True, dry_run=ns.dry_run),
                prompt=input,
                is_tty=sys.stdin.isatty(),
            )
            if init_summary.failures > 0:
                print(
                    "Error: failed to initialize scaffold for create.", file=sys.stderr
                )
                return 1

        create_repo_dir = repo_dir
        temp_create_dir: tempfile.TemporaryDirectory[str] | None = None
        if ns.dry_run and needs_init and not repo_dir.exists():
            temp_create_dir = tempfile.TemporaryDirectory(
                prefix="repo-scaffold-create-"
            )
            create_repo_dir = Path(temp_create_dir.name)

        try:
            create_summary: CreateSummary = create_repository(
                repo_dir=create_repo_dir,
                repo=ns.repo,
                owner=ns.owner,
                name=ns.name,
                visibility=ns.visibility,
                apply_settings=not ns.skip_settings,
                dry_run=ns.dry_run,
                stage_files=needs_init,
                out=(
                    (
                        lambda line: print(
                            line.replace(
                                create_repo_dir.as_posix(), repo_dir.as_posix()
                            )
                        )
                    )
                    if temp_create_dir
                    else print
                ),
                err=(
                    (
                        lambda line: print(
                            line.replace(
                                create_repo_dir.as_posix(), repo_dir.as_posix()
                            ),
                            file=sys.stderr,
                        )
                    )
                    if temp_create_dir
                    else (lambda line: print(line, file=sys.stderr))
                ),
            )
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            if temp_create_dir is not None:
                temp_create_dir.cleanup()
            return 1
        if temp_create_dir is not None:
            temp_create_dir.cleanup()

        print("")
        print("Summary:")
        if ns.dry_run:
            print("  mode: dry-run")
        print(f"  repo: {create_summary.repo}")
        print(f"  repo created: {create_summary.repo_created}")
        print(f"  pushed: {create_summary.pushed}")
        print(f"  settings applied: {create_summary.settings_applied}")
        print(f"  failures: {create_summary.failures}")
        return 1 if create_summary.failures > 0 else 0

    if ns.mode == "delete":
        try:
            delete_summary: DeleteSummary = delete_repositories(
                owner=ns.owner,
                prefix=ns.prefix,
                exact_names=tuple(ns.exact or ()),
                include_local=bool(ns.cleanup or ns.local_only),
                delete_local_only=bool(ns.local_only),
                local_roots=tuple(ns.local_root or ()),
                apply=bool(getattr(ns, "do_apply", False)),
                assume_yes=ns.yes,
                prompt=input,
                is_tty=sys.stdin.isatty(),
                cwd=Path.cwd(),
                out=print,
                err=lambda line: print(line, file=sys.stderr),
            )
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1

        print("")
        print("Summary:")
        if not bool(getattr(ns, "do_apply", False)):
            print("  mode: dry-run")
        if delete_summary.owner is not None:
            print(f"  owner: {delete_summary.owner}")
        print(f"  remote matched: {delete_summary.remote_matched}")
        print(f"  remote deleted: {delete_summary.remote_deleted}")
        print(f"  remote skipped: {delete_summary.remote_skipped}")
        print(f"  remote failures: {delete_summary.remote_failures}")
        print(f"  local matched: {delete_summary.local_matched}")
        print(f"  local deleted: {delete_summary.local_deleted}")
        print(f"  local skipped: {delete_summary.local_skipped}")
        print(f"  local failures: {delete_summary.local_failures}")
        print(f"  failures: {delete_summary.failures}")
        return 1 if delete_summary.failures > 0 else 0

    if ns.mode == "init":
        init_name = (ns.name or "").strip() or _default_init_name()
        languages = _parse_languages_or_die(parser, ns.languages)
        out_dir = Path(ns.out) if ns.out else default_output_path(init_name)
        if out_dir.exists() and not out_dir.is_dir():
            print(
                f"Error: output path '{out_dir}' exists and is not a directory.",
                file=sys.stderr,
            )
            return 2

        cfg = ScaffoldConfig(
            name=init_name,
            languages=languages,
            owner=ns.owner,
            license_id=ns.license_id,
            out_dir=out_dir,
        )
        rc = _run_file_apply(build_scaffold_files(cfg), _policy_from_ns(ns))
        if rc == 0:
            print(
                f"{'Dry-run complete for' if ns.dry_run else 'Scaffold initialized at'}: {out_dir}"
            )
        return rc

    if ns.mode == "apply" and ns.apply_command == "templates":
        repo_dir = Path(ns.path)
        if repo_dir.exists() and not repo_dir.is_dir():
            print(
                f"Error: target path '{repo_dir}' exists and is not a directory.",
                file=sys.stderr,
            )
            return 2
        files = build_template_files(repo_dir, owner=ns.owner, name=ns.name)
        rc = _run_file_apply(files, _policy_from_ns(ns))
        if rc == 0:
            print(
                f"{'Dry-run complete for' if ns.dry_run else 'Templates applied to'}: {repo_dir}"
            )
        return rc

    if ns.mode == "apply" and ns.apply_command == "ci":
        repo_dir = Path(ns.path)
        if repo_dir.exists() and not repo_dir.is_dir():
            print(
                f"Error: target path '{repo_dir}' exists and is not a directory.",
                file=sys.stderr,
            )
            return 2
        languages = _parse_languages_or_die(parser, ns.languages)
        files = build_ci_files(
            repo_dir, languages=languages, owner=ns.owner, name=ns.name
        )
        rc = _run_file_apply(files, _policy_from_ns(ns))
        if rc == 0:
            print(
                f"{'Dry-run complete for' if ns.dry_run else 'CI applied to'}: {repo_dir}"
            )
        return rc

    if ns.mode == "apply" and ns.apply_command == "dependabot":
        repo_dir = Path(ns.path)
        if repo_dir.exists() and not repo_dir.is_dir():
            print(
                f"Error: target path '{repo_dir}' exists and is not a directory.",
                file=sys.stderr,
            )
            return 2
        if ns.languages:
            languages = _parse_languages_or_die(parser, ns.languages)
        else:
            languages = detect_languages_from_repo(repo_dir)
        files = build_dependabot_files(
            repo_dir, languages=languages, owner=ns.owner, name=ns.name
        )
        rc = _run_file_apply(files, _policy_from_ns(ns))
        if rc == 0:
            print(
                f"{'Dry-run complete for' if ns.dry_run else 'Dependabot applied to'}: {repo_dir}"
            )
        return rc

    if ns.mode == "apply" and ns.apply_command == "backlog":
        repo_dir = Path(ns.path)
        if not repo_dir.exists() or not repo_dir.is_dir():
            print(
                f"Error: repo path does not exist or is not a directory: {repo_dir}",
                file=sys.stderr,
            )
            return 2
        if ns.repo and ns.repo_ref and ns.repo != ns.repo_ref:
            print(
                "Error: positional repo and --repo disagree. Use only one target repo.",
                file=sys.stderr,
            )
            return 2
        target_repo, repo_error = _resolve_repo_from_args_or_env(
            repo=ns.repo or ns.repo_ref, fallback_name=repo_dir.name
        )
        if repo_error:
            print(repo_error, file=sys.stderr)
            return 2
        assert target_repo is not None
        effective_project_number = ns.project_number
        effective_project_title = ns.project_title
        if (
            ns.with_project
            and effective_project_number is None
            and not effective_project_title
        ):
            repo_name = target_repo.split("/", 1)[1]
            env_project_title = (os.environ.get("GITHUB_PROJECT_TITLE") or "").strip()
            env_project_template = (
                os.environ.get("GITHUB_PROJECT_TITLE_TEMPLATE") or ""
            ).strip()
            if env_project_title:
                effective_project_title = env_project_title
            elif env_project_template:
                effective_project_title = env_project_template.replace(
                    "{repo}", repo_name
                )
            else:
                effective_project_title = f"{repo_name} Roadmap"
        if ns.auth_check:
            try:
                login = resolve_authenticated_login(repo_dir)
                project_target = resolve_project_target_for_auth_check(
                    repo_dir=repo_dir,
                    repo=target_repo,
                    project_number=effective_project_number,
                    project_title=effective_project_title,
                    project_owner=ns.project_owner,
                )
            except RuntimeError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            print(f"GitHub auth OK: {login}")
            if project_target is not None:
                print(f"GitHub project access OK: {project_target}")
            return 0
        backlog_file: Path
        if ns.file:
            backlog_file = _resolve_backlog_file_path(
                repo_dir=repo_dir, file_arg=ns.file, repo=target_repo
            )
        else:
            try:
                markdown_source_dir = _find_existing_markdown_source_dir(repo_dir)
            except RuntimeError as exc:
                print(str(exc), file=sys.stderr)
                return 2
            if markdown_source_dir is not None:
                try:
                    imported_backlog_file, import_summary = build_backlog_import_file(
                        source_dir=markdown_source_dir,
                        output_file=repo_dir / DEFAULT_BACKLOG_REL_PATH,
                    )
                except RuntimeError as exc:
                    print(str(exc), file=sys.stderr)
                    return 1

                if ns.dry_run:
                    temp_dir = Path(tempfile.mkdtemp(prefix="repo-scaffold-backlog-"))
                    backlog_file = temp_dir / "issues.json"
                    backlog_file.write_text(
                        imported_backlog_file.content, encoding="utf-8"
                    )
                    print(
                        f"[dry-run] auto-imported backlog JSON from {import_summary.source_dir}"
                    )
                else:
                    import_apply_summary = apply_files(
                        [imported_backlog_file],
                        _policy_from_ns(ns),
                        prompt=input,
                        is_tty=sys.stdin.isatty(),
                    )
                    if import_apply_summary.failures > 0:
                        return 1
                    if (
                        import_apply_summary.skipped > 0
                        and not imported_backlog_file.path.exists()
                    ):
                        print(
                            "Error: auto-imported backlog JSON was skipped and no existing output file is available.",
                            file=sys.stderr,
                        )
                        return 1
                    backlog_file = imported_backlog_file.path
                    print(
                        f"Auto-imported backlog JSON from {import_summary.source_dir}"
                    )
            else:
                backlog_file = _resolve_backlog_file_path(
                    repo_dir=repo_dir, file_arg=None, repo=target_repo
                )
        try:
            backlog_summary: BacklogApplySummary = apply_backlog(
                repo_dir=repo_dir,
                repo=target_repo,
                backlog_file=backlog_file,
                dry_run=ns.dry_run,
                project_number=effective_project_number,
                project_title=effective_project_title,
                project_owner=ns.project_owner,
                out=print,
                err=lambda line: print(line, file=sys.stderr),
            )
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1

        print("")
        print("Summary:")
        if ns.dry_run:
            print("  mode: dry-run")
        print(f"  milestones created: {backlog_summary.milestones_created}")
        print(f"  milestones skipped: {backlog_summary.milestones_skipped}")
        print(f"  epic issues created: {backlog_summary.epic_issues_created}")
        print(f"  epic issues skipped: {backlog_summary.epic_issues_skipped}")
        print(f"  ticket issues created: {backlog_summary.ticket_issues_created}")
        print(f"  ticket issues skipped: {backlog_summary.ticket_issues_skipped}")
        print(f"  issues created (total): {backlog_summary.issues_created}")
        print(f"  issues skipped (total): {backlog_summary.issues_skipped}")
        if effective_project_number is not None or effective_project_title:
            print(f"  project created: {backlog_summary.project_created}")
            print(f"  project items added: {backlog_summary.project_items_added}")
            print(f"  project items skipped: {backlog_summary.project_items_skipped}")
        print(f"  failures: {backlog_summary.failures}")
        return 1 if backlog_summary.failures > 0 else 0

    if ns.mode == "apply" and ns.apply_command == "rules":
        target_repo, repo_error = _resolve_repo_from_args_or_env(
            repo=ns.repo, fallback_name=None
        )
        if repo_error:
            print(repo_error, file=sys.stderr)
            return 2
        assert target_repo is not None
        preview_only = not ns.do_apply or getattr(ns, "dry_run", False)
        try:
            apply_repository_settings(
                repo_dir=Path.cwd(),
                repo=target_repo,
                dry_run=preview_only,
                out=print,
                warn=lambda line: print(line, file=sys.stderr),
            )
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1

        print("")
        print("Summary:")
        if preview_only:
            print("  mode: dry-run")
            print("  settings planned: True")
        else:
            print("  settings applied: True")
        return 0

    if ns.mode == "check" and ns.check_command == "rules":
        target_repo, repo_error = _resolve_repo_from_args_or_env(
            repo=ns.repo, fallback_name=None
        )
        if repo_error:
            print(repo_error, file=sys.stderr)
            return 2
        assert target_repo is not None
        try:
            check_summary: SettingsCheckSummary = check_repository_settings(
                repo_dir=Path.cwd(),
                repo=target_repo,
                out=print,
            )
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1

        print("")
        print("Summary:")
        print(f"  repo: {check_summary.repo}")
        print(f"  checks passed: {check_summary.passed}")
        print(f"  checks failed: {check_summary.failed}")
        print(f"  checks skipped: {check_summary.skipped}")
        print(f"  drift items: {len(check_summary.drifts)}")
        return 1 if check_summary.failed > 0 else 0

    if ns.mode == "project":
        repo_dir = Path(ns.path)
        if not repo_dir.exists() or not repo_dir.is_dir():
            print(
                f"Error: repo path does not exist or is not a directory: {repo_dir}",
                file=sys.stderr,
            )
            return 2

        mutation_summary: ProjectMutationSummary
        try:
            if ns.project_command == "list":
                list_summary: ProjectListSummary = list_projects(
                    repo_dir=repo_dir,
                    owner=ns.project_owner,
                )
                print("Projects:")
                if not list_summary.projects:
                    print("  (none)")
                for project in list_summary.projects:
                    closed_suffix = " [closed]" if project.closed else ""
                    print(
                        f"  - {project.owner}/#{project.number} {project.title}{closed_suffix}"
                    )
                print("")
                print("Summary:")
                print(f"  owner: {list_summary.owner}")
                print(f"  projects: {len(list_summary.projects)}")
                return 0

            if ns.project_command == "view":
                project = view_project(
                    repo_dir=repo_dir,
                    owner=ns.project_owner,
                    project_number=ns.project_number,
                    project_title=ns.project_title,
                )
                print("Project:")
                print(f"  owner: {project.owner}")
                print(f"  number: {project.number}")
                print(f"  title: {project.title}")
                if project.id:
                    print(f"  id: {project.id}")
                if project.closed is not None:
                    print(f"  closed: {project.closed}")
                if project.visibility:
                    print(f"  visibility: {project.visibility}")
                if project.description:
                    print(f"  description: {project.description}")
                if project.readme:
                    print(f"  readme: {project.readme}")
                return 0

            if ns.project_command == "items":
                items_summary: ProjectItemsSummary = list_project_items(
                    repo_dir=repo_dir,
                    owner=ns.project_owner,
                    project_number=ns.project_number,
                    project_title=ns.project_title,
                    limit=ns.limit,
                )
                print(
                    f"Project: {items_summary.project.owner}/#{items_summary.project.number} ({items_summary.project.title})"
                )
                print("Items:")
                if not items_summary.items:
                    print("  (none)")
                for item in items_summary.items:
                    number_display = (
                        f" #{item.issue_number}"
                        if item.issue_number is not None
                        else ""
                    )
                    print(
                        f"  - [{item.content_type}] item={item.id}{number_display} {item.title}"
                    )
                    if item.repository:
                        print(f"    repo: {item.repository}")
                    if item.content_url:
                        print(f"    url: {item.content_url}")
                print("")
                print("Summary:")
                print(f"  items: {len(items_summary.items)}")
                return 0

            if ns.project_command == "sync-metadata":
                mutation_summary = sync_project_metadata(
                    repo_dir=repo_dir,
                    owner=ns.project_owner,
                    project_number=ns.project_number,
                    project_title=ns.project_title,
                    out=print,
                )
            elif ns.project_command == "create":
                mutation_summary = create_project(
                    repo_dir=repo_dir,
                    owner=ns.project_owner,
                    project_title=ns.project_title,
                    description=ns.description,
                    readme=ns.readme,
                    visibility=ns.visibility,
                    repo=getattr(ns, "repo", None),
                    dry_run=ns.dry_run,
                    out=print,
                )
            elif ns.project_command == "setup-statuses":
                mutation_summary = setup_project_statuses(
                    repo_dir=repo_dir,
                    owner=ns.project_owner,
                    project_number=ns.project_number,
                    project_title=ns.project_title,
                    out=print,
                )
            elif ns.project_command == "edit":
                mutation_summary = edit_project(
                    repo_dir=repo_dir,
                    owner=ns.project_owner,
                    project_number=ns.project_number,
                    project_title=ns.project_title,
                    title=ns.title,
                    description=ns.description,
                    readme=ns.readme,
                    visibility=ns.visibility,
                    dry_run=ns.dry_run,
                    out=print,
                )
            elif ns.project_command == "delete":
                mutation_summary = delete_project(
                    repo_dir=repo_dir,
                    owner=ns.project_owner,
                    project_number=ns.project_number,
                    project_title=ns.project_title,
                    danger=ns.danger,
                    assume_yes=ns.yes,
                    dry_run=ns.dry_run,
                    backup_dir=ns.backup_dir,
                    prompt=input,
                    is_tty=sys.stdin.isatty(),
                    out=print,
                    err=lambda line: print(line, file=sys.stderr),
                )
            elif ns.project_command == "item-add":
                mutation_summary = add_project_item(
                    repo_dir=repo_dir,
                    owner=ns.project_owner,
                    project_number=ns.project_number,
                    project_title=ns.project_title,
                    issue_repo=ns.repo,
                    issue_number=ns.issue_number,
                    out=print,
                )
            elif ns.project_command == "item-status":
                mutation_summary = update_project_item_status(
                    repo_dir=repo_dir,
                    owner=ns.project_owner,
                    project_number=ns.project_number,
                    project_title=ns.project_title,
                    issue_repo=ns.repo,
                    issue_number=ns.issue_number,
                    status=ns.status,
                    out=print,
                )
            elif ns.project_command == "item-delete":
                mutation_summary = delete_project_item(
                    repo_dir=repo_dir,
                    owner=ns.project_owner,
                    project_number=ns.project_number,
                    project_title=ns.project_title,
                    item_id=ns.item_id,
                    issue_number=ns.issue_number,
                    danger=ns.danger,
                    assume_yes=ns.yes,
                    dry_run=ns.dry_run,
                    backup_dir=ns.backup_dir,
                    prompt=input,
                    is_tty=sys.stdin.isatty(),
                    out=print,
                    err=lambda line: print(line, file=sys.stderr),
                )
            elif ns.project_command == "link-repo":
                mutation_summary = link_project_repo(
                    repo_dir=repo_dir,
                    owner=ns.project_owner,
                    project_number=ns.project_number,
                    project_title=ns.project_title,
                    repo=ns.repo,
                    out=print,
                )
            elif ns.project_command == "undo":
                backup_file = Path(ns.backup_file)
                if not backup_file.is_absolute():
                    backup_file = repo_dir / backup_file
                mutation_summary = undo_project_backup(
                    repo_dir=repo_dir,
                    backup_file=backup_file,
                    dry_run=ns.dry_run,
                    out=print,
                )
            else:
                parser.error("Unsupported project command.")
                return 2
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1

        print("")
        print("Summary:")
        if getattr(ns, "dry_run", False):
            print("  mode: dry-run")
        print(f"  action: {mutation_summary.action}")
        print(f"  owner: {mutation_summary.owner}")
        if mutation_summary.project_number is not None:
            print(f"  project number: {mutation_summary.project_number}")
        if mutation_summary.project_title:
            print(f"  project title: {mutation_summary.project_title}")
        print(f"  changed: {mutation_summary.changed}")
        if mutation_summary.backup_file is not None:
            print(f"  backup file: {mutation_summary.backup_file}")
        if mutation_summary.metadata_file is not None:
            print(f"  metadata file: {mutation_summary.metadata_file}")
        if mutation_summary.undo_command is not None:
            print(f"  undo: {mutation_summary.undo_command}")
        if mutation_summary.restored_project_number is not None:
            print(
                f"  restored project number: {mutation_summary.restored_project_number}"
            )
        if mutation_summary.restored_item_id is not None:
            print(f"  restored item id: {mutation_summary.restored_item_id}")
        print(f"  failures: {mutation_summary.failures}")
        return 1 if mutation_summary.failures > 0 else 0

    if ns.mode == "import" and ns.import_command == "backlog":
        repo_dir = Path(ns.path)
        if not repo_dir.exists() or not repo_dir.is_dir():
            print(
                f"Error: repo path does not exist or is not a directory: {repo_dir}",
                file=sys.stderr,
            )
            return 2

        source_dir = _resolve_markdown_source_dir(
            repo_dir=repo_dir, source_arg=ns.source
        )
        if ns.out:
            p = Path(ns.out)
            output_file = p if p.is_absolute() else repo_dir / p
        elif ns.repo:
            normalized_repo = _normalize_owner_repo(ns.repo, allow_host_prefix=True)
            if normalized_repo is None:
                print(
                    "Error: --repo must be in owner/repo format.",
                    file=sys.stderr,
                )
                return 2
            output_file = _local_backlog_slug_path(normalized_repo)
        else:
            output_file = repo_dir / DEFAULT_BACKLOG_REL_PATH

        try:
            imported_backlog_file, import_summary = build_backlog_import_file(
                source_dir=source_dir,
                output_file=output_file,
            )
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1

        apply_summary = apply_files(
            [imported_backlog_file],
            _policy_from_ns(ns),
            prompt=input,
            is_tty=sys.stdin.isatty(),
        )
        print("")
        print("Summary:")
        if ns.dry_run:
            print("  mode: dry-run")
        print(f"  source dir: {import_summary.source_dir}")
        print(f"  output file: {import_summary.output_file}")
        print(f"  markdown files scanned: {import_summary.files_scanned}")
        print(f"  epics imported: {import_summary.epics_imported}")
        print(
            f"  epics skipped (already present): {import_summary.epics_skipped_existing}"
        )
        print(f"  tickets imported: {import_summary.tickets_imported}")
        print(
            f"  tickets skipped (already present): {import_summary.tickets_skipped_existing}"
        )
        print(f"  synthetic epics: {import_summary.synthetic_epics}")
        print(f"  created: {apply_summary.created}")
        print(f"  overwritten: {apply_summary.overwritten}")
        print(f"  skipped: {apply_summary.skipped}")
        print(f"  failures: {apply_summary.failures}")
        if apply_summary.failures == 0:
            print(
                f"{'Dry-run complete for' if ns.dry_run else 'Imported backlog to'}: {output_file}"
            )
        return 1 if apply_summary.failures > 0 else 0

    if ns.mode == "issue" and ns.issue_command == "view":
        target_repo, repo_error = _resolve_repo_from_args_or_env(
            repo=ns.repo, fallback_name=None
        )
        if repo_error:
            print(repo_error, file=sys.stderr)
            return 2
        assert target_repo is not None
        try:
            issue: IssueDetail = fetch_issue(
                repo_dir=Path.cwd(),
                repo=target_repo,
                issue_number=ns.issue_number,
            )
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if ns.json_output:
            import json as _json

            print(
                _json.dumps(
                    {
                        "number": issue.number,
                        "title": issue.title,
                        "body": issue.body,
                        "labels": issue.labels,
                        "assignees": issue.assignees,
                        "state": issue.state,
                    },
                    indent=2,
                )
            )
        else:
            print(f"Issue #{issue.number}: {issue.title}")
            print(f"State: {issue.state}")
            if issue.labels:
                print(f"Labels: {', '.join(issue.labels)}")
            if issue.assignees:
                print(f"Assignees: {', '.join(issue.assignees)}")
            if issue.body:
                print("")
                print(issue.body)
        return 0

    if ns.mode == "issue" and ns.issue_command != "view":
        import json as _json

        target_repo, repo_error = _resolve_repo_from_args_or_env(
            repo=ns.repo, fallback_name=None
        )
        if repo_error:
            print(repo_error, file=sys.stderr)
            return 2
        assert target_repo is not None
        token = token_from_repo(Path.cwd()) or ""

        if ns.issue_command == "list":
            cp = issue_list(target_repo, token, state=ns.state, label=ns.label)
            if cp.returncode != 0:
                print(cp.stderr.strip() or "Failed listing issues.", file=sys.stderr)
                return 1
            issues = [i for i in _json.loads(cp.stdout) if "pull_request" not in i]
            if ns.json_output:
                print(_json.dumps(issues, indent=2))
            else:
                if not issues:
                    print("No issues found.")
                for i in issues:
                    labels = ", ".join(lb["name"] for lb in i.get("labels", []))
                    suffix = f"  [{labels}]" if labels else ""
                    print(f"  #{i['number']} [{i['state']}] {i['title']}{suffix}")
            return 0

        if ns.issue_command == "create":
            cp = issue_create(
                target_repo,
                ns.title,
                token,
                body=_resolve_body(ns.body, ns.body_file) or "",
                labels=ns.labels,
                assignees=ns.assignees,
            )
            if cp.returncode not in (0, 201):
                print(cp.stderr.strip() or "Failed creating issue.", file=sys.stderr)
                return 1
            created = _json.loads(cp.stdout)
            print(f"Issue created: #{created['number']} {created['title']}")
            print(f"URL: {created['html_url']}")
            return 0

        if ns.issue_command == "close":
            cp = issue_close(target_repo, ns.issue_number, token)
            if cp.returncode != 0:
                print(cp.stderr.strip() or "Failed closing issue.", file=sys.stderr)
                return 1
            print(f"Issue #{ns.issue_number} closed.")
            return 0

        if ns.issue_command == "comment":
            resolved_body = _resolve_body(ns.body, ns.body_file)
            if not resolved_body:
                print("error: --body or --body-file is required.", file=sys.stderr)
                return 2
            cp = issue_comment(target_repo, ns.issue_number, resolved_body, token)
            if cp.returncode not in (0, 201):
                print(cp.stderr.strip() or "Failed posting comment.", file=sys.stderr)
                return 1
            url = _json.loads(cp.stdout).get("html_url", "")
            print(f"Comment posted: {url}")
            return 0

        if ns.issue_command == "label":
            cp = issue_label(
                target_repo,
                ns.issue_number,
                token,
                add=ns.add_labels,
                remove=ns.remove_labels,
            )
            if cp.returncode != 0:
                print(cp.stderr.strip() or "Failed updating labels.", file=sys.stderr)
                return 1
            print(f"Labels updated on #{ns.issue_number}.")
            return 0

        if ns.issue_command == "assign":
            cp = issue_assign(
                target_repo,
                ns.issue_number,
                token,
                add=ns.add_users,
                remove=ns.remove_users,
            )
            if cp.returncode != 0:
                print(
                    cp.stderr.strip() or "Failed updating assignees.", file=sys.stderr
                )
                return 1
            print(f"Assignees updated on #{ns.issue_number}.")
            return 0

        if ns.issue_command == "update":
            resolved_body = _resolve_body(ns.body, ns.body_file)
            if ns.title is None and resolved_body is None and ns.state is None:
                print("Provide at least --title, --body, or --state.", file=sys.stderr)
                return 2
            cp = issue_update(
                target_repo,
                ns.issue_number,
                token,
                title=ns.title,
                body=resolved_body,
                state=ns.state,
            )
            if cp.returncode != 0:
                print(cp.stderr.strip() or "Failed updating issue.", file=sys.stderr)
                return 1
            updated = _json.loads(cp.stdout)
            print(f"Issue updated: #{updated['number']} {updated['title']}")
            print(f"URL: {updated['html_url']}")
            return 0

    if ns.mode == "pr":
        import json as _json

        target_repo, repo_error = _resolve_repo_from_args_or_env(
            repo=ns.repo, fallback_name=None
        )
        if repo_error:
            print(repo_error, file=sys.stderr)
            return 2
        assert target_repo is not None
        token = token_from_repo(Path.cwd()) or ""

        if ns.pr_command == "list":
            cp = pr_list(target_repo, token)
            if cp.returncode != 0:
                print(cp.stderr.strip() or "Failed listing PRs.", file=sys.stderr)
                return 1
            prs = _json.loads(cp.stdout)
            if ns.json_output:
                print(_json.dumps(prs, indent=2))
            else:
                if not prs:
                    print("No open pull requests.")
                for p in prs:
                    print(
                        f"  #{p['number']} [{p['state']}] {p['title']}  ({p['head']['ref']})"
                    )
            return 0

        if ns.pr_command == "view":
            cp = pr_view(target_repo, ns.pr_number, token)
            if cp.returncode != 0:
                print(
                    cp.stderr.strip() or f"Failed fetching PR #{ns.pr_number}.",
                    file=sys.stderr,
                )
                return 1
            pr = _json.loads(cp.stdout)
            if ns.json_output:
                print(_json.dumps(pr, indent=2))
            else:
                print(f"PR #{pr['number']}: {pr['title']}")
                print(f"State: {pr['state']}")
                print(f"Branch: {pr['head']['ref']} -> {pr['base']['ref']}")
                print(f"Author: {pr['user']['login']}")
                if pr.get("body"):
                    print("")
                    print(pr["body"])
            return 0

        if ns.pr_command == "comment":
            resolved_body = _resolve_body(ns.body, ns.body_file)
            if not resolved_body:
                print("error: --body or --body-file is required.", file=sys.stderr)
                return 2
            cp = pr_comment(
                target_repo, ns.pr_number, resolved_body, token, reply_to=ns.reply_to
            )
            if cp.returncode not in (0, 201):
                print(cp.stderr.strip() or "Failed posting comment.", file=sys.stderr)
                return 1
            comment = _json.loads(cp.stdout)
            print(f"Comment posted: {comment.get('html_url', '')}")
            return 0

        if ns.pr_command == "resolve-thread":
            cp = pr_resolve_thread(ns.thread_id, token)
            if cp.returncode != 0:
                print(cp.stderr.strip() or "Failed resolving thread.", file=sys.stderr)
                return 1
            thread = _json.loads(cp.stdout)
            print(
                f"Thread resolved: {thread.get('id', ns.thread_id)} (isResolved: {thread.get('isResolved', True)})"
            )
            return 0

        if ns.pr_command == "create":
            cp = pr_create(
                target_repo,
                title=ns.title,
                body=_resolve_body(ns.body, ns.body_file) or "",
                head=ns.head,
                base=ns.base,
                token=token,
                draft=ns.draft,
            )
            if cp.returncode not in (0, 201):
                print(cp.stderr.strip() or "Failed creating PR.", file=sys.stderr)
                return 1
            created = _json.loads(cp.stdout)
            print(f"PR created: #{created['number']} {created['title']}")
            print(f"URL: {created['html_url']}")
            return 0

        if ns.pr_command == "update":
            resolved_body = _resolve_body(ns.body, ns.body_file)
            if ns.title is None and resolved_body is None:
                print(
                    "Error: at least one of --title or --body is required.",
                    file=sys.stderr,
                )
                return 2
            cp = pr_update(
                target_repo,
                pr_number=ns.pr_number,
                token=token,
                title=ns.title,
                body=resolved_body,
            )
            if cp.returncode != 0:
                print(cp.stderr.strip() or "Failed updating PR.", file=sys.stderr)
                return 1
            updated = _json.loads(cp.stdout)
            print(f"PR updated: #{updated['number']} {updated['title']}")
            print(f"URL: {updated['html_url']}")
            return 0

        if ns.pr_command == "merge":
            cp = pr_merge(target_repo, ns.pr_number, token, method=ns.method)
            if cp.returncode not in (0, 200):
                print(cp.stderr.strip() or "Failed merging PR.", file=sys.stderr)
                return 1
            result = _json.loads(cp.stdout)
            print(f"PR #{ns.pr_number} merged: {result.get('message', 'OK')}")
            return 0

        if ns.pr_command == "checks":
            cp = pr_checks(target_repo, ns.pr_number, token)
            if cp.returncode != 0:
                print(cp.stderr.strip() or "Failed fetching checks.", file=sys.stderr)
                return 1
            runs = _json.loads(cp.stdout)
            if ns.json_output:
                print(_json.dumps(runs, indent=2))
            else:
                if not runs:
                    print("No check runs found.")
                for r in runs:
                    status = r.get("status", "?")
                    conclusion = r.get("conclusion") or status
                    print(f"  {r.get('name', '?')}: {conclusion}")
            return 0

        if ns.pr_command == "review-threads":
            owner, repo_name = target_repo.split("/", 1)
            cp = pr_review_threads(owner, repo_name, ns.pr_number, token)
            if cp.returncode != 0:
                print(
                    cp.stderr.strip() or "Failed fetching review threads.",
                    file=sys.stderr,
                )
                return 1
            data = _json.loads(cp.stdout)
            threads = (
                data.get("repository", {})
                .get("pullRequest", {})
                .get("reviewThreads", {})
                .get("nodes", [])
            )
            if ns.json_output:
                print(_json.dumps(threads, indent=2))
            else:
                if not threads:
                    print("No review threads found.")
                for t in threads:
                    resolved = t.get("isResolved", False)
                    state = "resolved" if resolved else "open"
                    comments = t.get("comments", {}).get("nodes", [])
                    for c in comments:
                        author = c.get("author", {}).get("login", "?")
                        path = c.get("path", "")
                        line = c.get("line") or c.get("originalLine") or "?"
                        body = c.get("body", "").strip()
                        print(f"[{state}] {author} on {path}:{line}")
                        print(f"  {body[:200]}")
            return 0

        if ns.pr_command == "list-comments":
            cp = pr_list_comments(target_repo, ns.pr_number, token)
            if cp.returncode != 0:
                print(
                    cp.stderr.strip() or "Failed fetching review comments.",
                    file=sys.stderr,
                )
                return 1
            comments = _json.loads(cp.stdout)
            if ns.json_output:
                print(_json.dumps(comments, indent=2))
            else:
                if not comments:
                    print("No inline review comments found.")
                for c in comments:
                    author = c.get("user", {}).get("login", "?")
                    path = c.get("path", "")
                    line = c.get("line") or c.get("original_line") or "?"
                    body = (c.get("body") or "").strip()
                    print(f"{author} on {path}:{line}")
                    print(f"  {body[:200]}")
            return 0

    if ns.mode == "branch":
        import json as _json

        target_repo, repo_error = _resolve_repo_from_args_or_env(
            repo=ns.repo, fallback_name=None
        )
        if repo_error:
            print(repo_error, file=sys.stderr)
            return 2
        assert target_repo is not None
        token = token_from_repo(Path.cwd()) or ""

        if ns.branch_command == "create":
            cp = branch_create(target_repo, ns.name, token, base=ns.base)
            if cp.returncode not in (0, 201):
                print(cp.stderr.strip() or "Failed creating branch.", file=sys.stderr)
                return 1
            ref = _json.loads(cp.stdout)
            print(f"Branch created: {ref.get('ref', ns.name)}")
            return 0

        if ns.branch_command == "delete":
            cp = branch_delete(target_repo, ns.name, token)
            if cp.returncode not in (0, 204):
                print(cp.stderr.strip() or "Failed deleting branch.", file=sys.stderr)
                return 1
            print(f"Branch deleted: {ns.name}")
            return 0

    parser.error("Unsupported command.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
