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
    branch_rename,
    issue_add_sub_issue,
    issue_node_id,
    issue_remove_sub_issue,
    issue_assign,
    issue_close,
    issue_comment,
    issue_create,
    issue_delete,
    issue_label,
    issue_list,
    issue_sync_hierarchy,
    issue_update,
    label_apply_preset,
    label_create,
    label_delete,
    label_list,
    pr_annotations,
    pr_checks,
    pr_comment,
    pr_create,
    pr_list,
    pr_list_comments,
    pr_merge,
    react,
    pr_rerun,
    pr_resolve_thread,
    pr_review_threads,
    pr_check_sop,
    pr_request_reviewer,
    pr_reviews,
    pr_update,
    pr_view,
    repo_archive,
    token_from_repo,
)
from .backlog_import import build_backlog_import_file
from .create_ops import (
    CreateSummary,
    SettingsCheckSummary,
    TemplatesCheckSummary,
    TemplatesSyncResult,
    apply_repository_settings,
    check_repository_settings,
    check_repository_templates,
    create_repository,
    sync_repository_ruleset,
    sync_repository_templates,
)
from .delete_ops import DeleteSummary, delete_repositories
from .generator import (
    SUPPORTED_LICENSE,
    ScaffoldConfig,
    build_ci_files,
    build_dependabot_files,
    build_scaffold_files,
    build_template_files,
    detect_languages_from_repo,
    parse_language_csv,
)
from .overwrite_policy import ApplySummary, OverwritePolicy, apply_files
from .project_config import resolve_languages_for_repo
from .discover_ops import (
    discover_repos,
    parse_repo_selection,
    prompt_for_token,
    upsert_env_var,
)
from .registry_ops import (
    RegistryEntry,
    forget_repo,
    list_registry,
    load_registry,
    register_repo,
    save_registry,
)
from .project_ops import (
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
    setup_project,
    setup_project_statuses,
    setup_project_views,
    sync_project_metadata,
    undo_project_backup,
    update_project_item_status,
    view_project,
)

DEFAULT_INIT_NAME_PREFIX = "repo-scaffold-e2e"
DEFAULT_INIT_LANGUAGES = "go,python,react"
BACKLOG_TICKETS_DIR_ENV = "GITHUB_TICKETS_DIR"
SCAFFOLD_OUTPUT_DIR_ENV = "SCAFFOLD_OUTPUT_DIR"


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


def _resolve_repo_targets(
    ns: argparse.Namespace,
) -> tuple[list[tuple[str, Path]], str | None]:
    """Resolve one or more (repo, repo_dir) targets from --repo/--repos/--all.

    --repo uses the current working directory as repo_dir (existing single-repo
    behavior). --repos/--all resolve repo_dir from the local registry, since
    settings checks need a local git checkout to authenticate against.
    """
    selected = [
        flag
        for flag, value in (
            ("--repo", getattr(ns, "repo", None)),
            ("--repos", getattr(ns, "repos", None)),
            ("--all", getattr(ns, "all_repos", False)),
        )
        if value
    ]
    if len(selected) > 1:
        return [], f"Error: pass only one of {', '.join(selected)}."

    if getattr(ns, "all_repos", False):
        entries = list_registry()
        if not entries:
            return [], "Error: no repos registered. Run 'repo register' first."
        return [(e.repo, Path(e.local_path)) for e in entries], None

    if getattr(ns, "repos", None):
        registry = {e.repo: e for e in list_registry()}
        targets: list[tuple[str, Path]] = []
        for raw in ns.repos.split(","):
            repo = raw.strip()
            if not repo:
                continue
            entry = registry.get(repo)
            if entry is None:
                return (
                    [],
                    f"Error: repo not registered: {repo}. Run 'repo register' first.",
                )
            targets.append((repo, Path(entry.local_path)))
        return targets, None

    target_repo, repo_error = _resolve_repo_from_args_or_env(
        repo=getattr(ns, "repo", None), fallback_name=None
    )
    if repo_error:
        return [], repo_error
    assert target_repo is not None
    return [(target_repo, Path.cwd())], None


def _local_backlog_path(repo: str) -> Path:
    # repo is owner/repo — resolves to local/{owner}/{repo}/backlog.json relative to CWD
    return Path.cwd() / "local" / repo / "backlog.json"


def _resolve_backlog_file_path(
    *, repo_dir: Path, file_arg: str | None, repo: str | None = None
) -> Path:
    if file_arg:
        backlog_file = Path(file_arg)
        return backlog_file if backlog_file.is_absolute() else (repo_dir / backlog_file)

    if repo:
        local_path = _local_backlog_path(repo)
        if local_path.exists():
            return local_path
        raise FileNotFoundError(
            f"No backlog file found for {repo!r}. "
            f"Expected: {local_path}. "
            f"Run 'repo-scaffold import backlog --repo {repo}' to generate it, "
            f"or pass --file to specify the path explicitly."
        )

    return repo_dir / "local" / "backlog.json"


def _resolve_markdown_source_dir(*, repo_dir: Path, source_arg: str | None) -> Path:
    source_value = source_arg or (os.environ.get(BACKLOG_TICKETS_DIR_ENV) or "").strip()
    if source_value:
        source_dir = Path(source_value)
        return source_dir if source_dir.is_absolute() else (repo_dir / source_dir)
    raise RuntimeError(
        f"No markdown source directory specified. "
        f"Pass --source <dir> or set {BACKLOG_TICKETS_DIR_ENV} in .env."
    )


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
    return None


def _resolve_output_path(name: str) -> Path:
    base = (os.environ.get(SCAFFOLD_OUTPUT_DIR_ENV) or "").strip()
    if base:
        return Path(base) / name
    return Path(name)


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
    parser.add_argument(
        "--out",
        help="Output path (default: $SCAFFOLD_OUTPUT_DIR/<name> or ./<name>)",
    )


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
        help="Backup directory for destructive project operations (default: local/<owner>/backups/)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="repo-scaffold",
        description="Repository operations toolkit for scaffold create/init/apply/delete workflows.",
    )
    subparsers = parser.add_subparsers(
        dest="mode",
        metavar="{create,delete,init,apply,check,repo,sync,project,import,issue,pr,branch,label,workspace}",
        required=True,
    )

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
    apply_sub = apply_cmd.add_subparsers(
        dest="apply_command",
        metavar="{templates,ci,dependabot,backlog,rules,settings}",
        required=True,
    )

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
    apply_ci.add_argument(
        "--repo",
        help="GitHub repo (owner/repo) to sync the managed branch ruleset after writing CI files",
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
            "GITHUB_TICKETS_DIR is set; otherwise resolves to "
            "./local/<owner>/<repo>/backlog.json (when --repo is set), "
            "or <repo-path>/local/backlog.json"
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
        "--repos", help="Comma-separated registered repos (owner/repo,owner/repo)"
    )
    apply_rules.add_argument(
        "--all",
        action="store_true",
        dest="all_repos",
        help="Target every repo in the local registry",
    )
    apply_rules.add_argument(
        "--apply",
        action="store_true",
        dest="do_apply",
        help="Execute commands instead of only printing them",
    )

    apply_settings_cmd = apply_sub.add_parser(
        "settings",
        parents=[apply_parent],
        help="Apply repository settings including required status checks",
    )
    apply_settings_cmd.add_argument(
        "--repo",
        required=True,
        help="Target GitHub repo (owner/repo)",
    )
    apply_settings_cmd.add_argument(
        "--languages",
        default="",
        help="Comma-separated language list (go, gin, python, react) to require as status checks",
    )

    check = subparsers.add_parser(
        "check",
        help="Check GitHub settings/capabilities for drift",
    )
    check_sub = check.add_subparsers(
        dest="check_command",
        metavar="{rules,templates,settings}",
        required=True,
    )
    check_rules = check_sub.add_parser(
        "rules",
        help="Check merge settings, managed ruleset, and security defaults",
    )
    check_rules.add_argument("--repo", help="Target GitHub repo (owner/repo)")
    check_rules.add_argument(
        "--repos", help="Comma-separated registered repos (owner/repo,owner/repo)"
    )
    check_rules.add_argument(
        "--all",
        action="store_true",
        dest="all_repos",
        help="Target every repo in the local registry",
    )

    check_templates_cmd = check_sub.add_parser(
        "templates",
        help="Check issue/PR templates for drift against repo-scaffold's current templates",
    )
    check_templates_cmd.add_argument("--repo", help="Target GitHub repo (owner/repo)")
    check_templates_cmd.add_argument(
        "--repos", help="Comma-separated registered repos (owner/repo,owner/repo)"
    )
    check_templates_cmd.add_argument(
        "--all",
        action="store_true",
        dest="all_repos",
        help="Target every repo in the local registry",
    )

    check_settings_cmd = check_sub.add_parser(
        "settings",
        help="Check repository settings including required status checks",
    )
    check_settings_cmd.add_argument(
        "--repo",
        required=True,
        help="Target GitHub repo (owner/repo)",
    )
    check_settings_cmd.add_argument(
        "--languages",
        default="",
        help=(
            "Comma-separated language list to require as status checks "
            "(default: read from .repo-scaffold.yml, falling back to file detection)"
        ),
    )

    repo_cmd = subparsers.add_parser(
        "repo",
        help="Manage the local registry of repos repo-scaffold knows about",
    )
    repo_sub = repo_cmd.add_subparsers(
        dest="repo_command",
        metavar="{register,list,forget,discover}",
        required=True,
    )
    repo_register_cmd = repo_sub.add_parser(
        "register", help="Register a repo in the local registry"
    )
    repo_register_cmd.add_argument(
        "--repo", required=True, help="GitHub repo (owner/repo)"
    )
    repo_register_cmd.add_argument(
        "--path", required=True, help="Local path to the repo's checkout"
    )
    repo_register_cmd.add_argument(
        "--notes", default="", help="Free-text notes about this repo"
    )
    repo_sub.add_parser("list", help="List registered repos")
    repo_forget_cmd = repo_sub.add_parser(
        "forget", help="Remove a repo from the local registry"
    )
    repo_forget_cmd.add_argument(
        "--repo", required=True, help="GitHub repo (owner/repo)"
    )
    repo_discover_cmd = repo_sub.add_parser(
        "discover",
        help="Discover GitHub repos visible to your token and optionally register them",
    )
    repo_discover_cmd.add_argument(
        "--org",
        help="Scope discovery to a specific GitHub org/user (default: authenticated user)",
    )
    repo_discover_cmd.add_argument(
        "--register",
        action="store_true",
        help="Bulk-register all discovered repos into the local registry",
    )
    repo_discover_cmd.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation before registering",
    )
    repo_archive_cmd = repo_sub.add_parser(
        "archive",
        help="Archive a GitHub repository (read-only; reversible via the GitHub UI)",
    )
    repo_archive_cmd.add_argument(
        "--repo", required=True, help="GitHub repo (owner/repo)"
    )
    repo_archive_cmd.add_argument(
        "--yes",
        action="store_true",
        help="Skip the confirmation prompt",
    )

    sync_cmd = subparsers.add_parser(
        "sync",
        help="Check then apply settings/rules across one or more repos",
    )
    sync_sub = sync_cmd.add_subparsers(
        dest="sync_command",
        metavar="{rules,templates}",
        required=True,
    )
    sync_rules_cmd = sync_sub.add_parser(
        "rules",
        help="Check merge settings/ruleset/security defaults, then apply per-repo on confirm",
    )
    sync_rules_cmd.add_argument("--repo", help="Target GitHub repo (owner/repo)")
    sync_rules_cmd.add_argument(
        "--repos", help="Comma-separated registered repos (owner/repo,owner/repo)"
    )
    sync_rules_cmd.add_argument(
        "--all",
        action="store_true",
        dest="all_repos",
        help="Target every repo in the local registry",
    )
    sync_rules_cmd.add_argument(
        "--yes",
        action="store_true",
        help="Skip the per-repo confirmation prompt and apply all drifted repos",
    )

    sync_templates_cmd = sync_sub.add_parser(
        "templates",
        help=(
            "Check issue/PR templates for drift, then open a PR per drifted repo "
            "with the updated templates (never commits to the default branch directly)"
        ),
    )
    sync_templates_cmd.add_argument("--repo", help="Target GitHub repo (owner/repo)")
    sync_templates_cmd.add_argument(
        "--repos", help="Comma-separated registered repos (owner/repo,owner/repo)"
    )
    sync_templates_cmd.add_argument(
        "--all",
        action="store_true",
        dest="all_repos",
        help="Target every repo in the local registry",
    )
    sync_templates_cmd.add_argument(
        "--yes",
        action="store_true",
        help="Skip the per-repo confirmation prompt and open PRs for all drifted repos",
    )

    project_cmd = subparsers.add_parser(
        "project",
        help="Manage GitHub Projects with explicit destructive-op guards",
    )
    project_sub = project_cmd.add_subparsers(
        dest="project_command",
        metavar="{list,view,items,sync-metadata,create,setup,setup-statuses,setup-views,edit,delete,item-add,item-status,item-delete,link-repo,undo}",
        required=True,
    )

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

    project_setup_cmd = project_sub.add_parser(
        "setup",
        help="Configure project statuses and automation workflows from .repo-scaffold.yml",
    )
    project_setup_cmd.add_argument(
        "--path",
        default=".",
        help="Workspace path (default: .); also the directory searched for .repo-scaffold.yml",
    )
    project_setup_cmd.add_argument(
        "--repo",
        help="Repository to write the Actions template into (OWNER/REPO)",
    )
    project_setup_cmd.add_argument(
        "--interactive",
        action="store_true",
        help="Run wizard to customise settings, then save to .repo-scaffold.yml",
    )
    project_setup_cmd.add_argument(
        "--no-actions-template",
        dest="no_actions_template",
        action="store_true",
        help="Skip writing .github/workflows/issue-status-sync.yml",
    )
    _add_project_target_args(project_setup_cmd)

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

    project_setup_views_cmd = project_sub.add_parser(
        "setup-views",
        help=(
            "Ensure 'Kanban Board' (board layout) and 'Progress View' "
            "(table layout, Labels + Parent issue columns, grouped by Parent issue) "
            "exist on the project"
        ),
    )
    project_setup_views_cmd.add_argument(
        "--path",
        default=".",
        help="Workspace path used for .env resolution (default: .)",
    )
    _add_project_target_args(project_setup_views_cmd)

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
    import_sub = import_cmd.add_subparsers(
        dest="import_command",
        metavar="{backlog}",
        required=True,
    )
    import_backlog = import_sub.add_parser(
        "backlog",
        parents=[apply_parent],
        help="Import markdown backlog files into local/<owner>/<repo>/backlog.json",
    )
    import_backlog.add_argument(
        "--path",
        default=".",
        help="Target repository path containing markdown backlog notes (default: .)",
    )
    import_backlog.add_argument(
        "--source",
        help=(
            "Markdown source directory (required unless GITHUB_TICKETS_DIR is set in .env)"
        ),
    )
    import_backlog.add_argument(
        "--repo",
        help=(
            "Target GitHub repo (owner/repo). When provided and --out is omitted, "
            "output defaults to local/<owner>/<repo>/backlog.json"
        ),
    )
    import_backlog.add_argument(
        "--out",
        help=(
            "Backlog JSON output path. Defaults to local/<owner>/<repo>/backlog.json "
            "when --repo is provided, otherwise <path>/local/backlog.json"
        ),
    )

    issue_cmd = subparsers.add_parser("issue", help="Query GitHub issues")
    issue_sub = issue_cmd.add_subparsers(
        dest="issue_command",
        metavar="{view,list,create,close,comment,label,assign,update,delete,add-sub-issue,sync-hierarchy,re-parent}",
        required=True,
    )
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

    issue_delete_cmd = issue_sub.add_parser(
        "delete", help="Permanently delete an issue"
    )
    issue_delete_cmd.add_argument("--repo", required=True)
    issue_delete_cmd.add_argument("--issue-number", type=int, required=True)

    issue_sub_issue_cmd = issue_sub.add_parser(
        "add-sub-issue", help="Link an issue as a sub-issue of a parent issue"
    )
    issue_sub_issue_cmd.add_argument("--repo", required=True)
    issue_sub_issue_cmd.add_argument(
        "--parent", type=int, required=True, dest="parent_number", metavar="N"
    )
    issue_sub_issue_cmd.add_argument(
        "--child", type=int, required=True, dest="child_number", metavar="N"
    )

    issue_sync_hierarchy_cmd = issue_sub.add_parser(
        "sync-hierarchy",
        help="Backfill parent/child sub-issue links from the epic label convention",
    )
    issue_sync_hierarchy_cmd.add_argument("--repo", required=True)
    issue_sync_hierarchy_cmd.add_argument(
        "--apply",
        action="store_true",
        help="Apply the backfill (default is dry-run)",
    )

    issue_reparent_cmd = issue_sub.add_parser(
        "re-parent",
        help=(
            "Move a sub-issue to a new parent: removes it from its current parent "
            "then links it under the new one"
        ),
    )
    issue_reparent_cmd.add_argument("--repo", required=True)
    issue_reparent_cmd.add_argument(
        "--issue",
        type=int,
        required=True,
        dest="child_number",
        metavar="N",
        help="Issue number to re-parent",
    )
    issue_reparent_cmd.add_argument(
        "--from-parent",
        type=int,
        required=True,
        dest="old_parent_number",
        metavar="N",
        help="Current parent issue number (will be removed)",
    )
    issue_reparent_cmd.add_argument(
        "--to-parent",
        type=int,
        required=True,
        dest="new_parent_number",
        metavar="N",
        help="New parent issue number (will be linked)",
    )

    pr_cmd = subparsers.add_parser("pr", help="Interact with GitHub pull requests")
    pr_sub = pr_cmd.add_subparsers(
        dest="pr_command",
        metavar="{list,view,comment,react,create,update,resolve-thread,merge,checks,annotations,rerun,review-threads,check-sop,reviews,list-comments,request-reviewer}",
        required=True,
    )

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

    pr_react_cmd = pr_sub.add_parser(
        "react", help="Add a reaction to a PR review comment"
    )
    pr_react_cmd.add_argument("--repo", required=True)
    pr_react_cmd.add_argument(
        "--comment-id", type=int, required=True, dest="comment_id"
    )
    pr_react_cmd.add_argument(
        "--reaction",
        required=True,
        choices=["+1", "-1", "laugh", "confused", "heart", "hooray", "rocket", "eyes"],
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
    pr_update_cmd.add_argument(
        "--state",
        choices=["open", "closed"],
        default=None,
        help="Set PR state to open or closed",
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

    pr_annotations_cmd = pr_sub.add_parser(
        "annotations",
        help="Show check-run annotations (lint errors, test failures) for a PR",
    )
    pr_annotations_cmd.add_argument("--repo", required=True)
    pr_annotations_cmd.add_argument(
        "--pr-number", required=True, type=int, dest="pr_number"
    )
    pr_annotations_cmd.add_argument("--json", action="store_true", dest="json_output")

    pr_rerun_cmd = pr_sub.add_parser("rerun", help="Re-run workflow jobs for a PR")
    pr_rerun_cmd.add_argument("--repo", required=True)
    pr_rerun_cmd.add_argument("--pr-number", required=True, type=int, dest="pr_number")
    pr_rerun_cmd.add_argument(
        "--failed-only",
        action="store_true",
        dest="failed_only",
        help="Only re-run failed jobs (default: re-run all)",
    )

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

    pr_check_sop_cmd = pr_sub.add_parser(
        "check-sop",
        help="Check each review thread for SOP compliance: replied, resolved, reacted +1",
    )
    pr_check_sop_cmd.add_argument("--repo", required=True)
    pr_check_sop_cmd.add_argument(
        "--pr-number", required=True, type=int, dest="pr_number"
    )
    pr_check_sop_cmd.add_argument("--json", action="store_true", dest="json_output")

    pr_reviews_cmd = pr_sub.add_parser(
        "reviews", help="List submitted reviews for a PR"
    )
    pr_reviews_cmd.add_argument("--repo", required=True)
    pr_reviews_cmd.add_argument(
        "--pr-number", required=True, type=int, dest="pr_number"
    )
    pr_reviews_cmd.add_argument("--json", action="store_true", dest="json_output")

    pr_list_comments_cmd = pr_sub.add_parser(
        "list-comments",
        help="List all comments on a PR (inline review and general conversation)",
    )
    pr_list_comments_cmd.add_argument("--repo", required=True)
    pr_list_comments_cmd.add_argument(
        "--pr-number", required=True, type=int, dest="pr_number"
    )
    pr_list_comments_cmd.add_argument("--json", action="store_true", dest="json_output")

    pr_request_reviewer_cmd = pr_sub.add_parser(
        "request-reviewer", help="Request one or more reviewers on a PR"
    )
    pr_request_reviewer_cmd.add_argument("--repo", required=True)
    pr_request_reviewer_cmd.add_argument(
        "--pr-number", required=True, type=int, dest="pr_number"
    )
    pr_request_reviewer_cmd.add_argument(
        "--reviewer",
        action="append",
        dest="reviewers",
        required=True,
        metavar="USER",
        help="GitHub username to request (repeatable)",
    )

    pr_wait_cmd = pr_sub.add_parser(
        "wait", help="Block until all PR checks pass or any fail (exit 0/1/2)"
    )
    pr_wait_cmd.add_argument("--repo", required=True)
    pr_wait_cmd.add_argument("--pr-number", required=True, type=int, dest="pr_number")
    pr_wait_cmd.add_argument(
        "--interval",
        type=int,
        default=30,
        help="Seconds between polls (default: 30)",
    )
    pr_wait_cmd.add_argument(
        "--timeout",
        type=int,
        default=1800,
        help="Max seconds to wait before giving up (default: 1800)",
    )

    branch_cmd = subparsers.add_parser("branch", help="Manage GitHub branches")
    branch_sub = branch_cmd.add_subparsers(
        dest="branch_command",
        metavar="{create,delete,rename}",
        required=True,
    )

    branch_create_cmd = branch_sub.add_parser("create", help="Create a new branch")
    branch_create_cmd.add_argument("--repo", required=True)
    branch_create_cmd.add_argument("--name", required=True, help="New branch name")
    branch_create_cmd.add_argument(
        "--from", default="main", dest="base", help="Base branch (default: main)"
    )

    branch_delete_cmd = branch_sub.add_parser("delete", help="Delete a branch")
    branch_delete_cmd.add_argument("--repo", required=True)
    branch_delete_cmd.add_argument("--name", required=True, help="Branch to delete")

    branch_rename_cmd = branch_sub.add_parser(
        "rename",
        help="Rename a branch. WARNING: an open PR against it may close instead of following the rename -- check after running",
    )
    branch_rename_cmd.add_argument("--repo", required=True)
    branch_rename_cmd.add_argument("--name", required=True, help="Current branch name")
    branch_rename_cmd.add_argument("--new-name", required=True, help="New branch name")

    label_cmd = subparsers.add_parser("label", help="Manage repository labels")
    label_sub = label_cmd.add_subparsers(
        dest="label_command",
        metavar="{list,create,delete,apply-preset}",
        required=True,
    )

    label_list_cmd = label_sub.add_parser("list", help="List all labels in a repo")
    label_list_cmd.add_argument("--repo", required=True)
    label_list_cmd.add_argument("--json", action="store_true")

    label_create_cmd = label_sub.add_parser("create", help="Create a label in a repo")
    label_create_cmd.add_argument("--repo", required=True)
    label_create_cmd.add_argument("--name", required=True, help="Label name")
    label_create_cmd.add_argument(
        "--color", required=True, help="6-char hex color (without #)"
    )
    label_create_cmd.add_argument("--description", default="", help="Label description")

    label_delete_cmd = label_sub.add_parser("delete", help="Delete a label from a repo")
    label_delete_cmd.add_argument("--repo", required=True)
    label_delete_cmd.add_argument("--name", required=True, help="Label name to delete")

    label_preset_cmd = label_sub.add_parser(
        "apply-preset",
        help="Idempotently create the standard label set on a repo",
    )
    label_preset_cmd.add_argument("--repo", required=True)

    workspace_cmd = subparsers.add_parser(
        "workspace", help="Manage per-branch git worktrees under repos/"
    )
    workspace_sub = workspace_cmd.add_subparsers(
        dest="workspace_command",
        metavar="{create,list,delete,prune,configure-auth}",
        required=True,
    )

    ws_create = workspace_sub.add_parser(
        "create", help="Create a worktree for a branch"
    )
    ws_create.add_argument("--repo", required=True, help="OWNER/REPO")
    ws_create.add_argument("--branch", required=True, help="Branch name")
    ws_create.add_argument(
        "--from", default="main", dest="base", help="Base branch (default: main)"
    )
    ws_create.add_argument(
        "--env-source",
        default=None,
        dest="env_source",
        help=(
            "Path to a directory whose gitignored root-level files (e.g. .env, "
            "secrets.json) should be copied into the new worktree. "
            "Subdirectories are skipped. Missing path is a warning, not an error."
        ),
    )

    ws_list = workspace_sub.add_parser("list", help="List active worktrees")
    ws_list.add_argument("--repo", default=None, help="Filter by OWNER/REPO (optional)")

    ws_delete = workspace_sub.add_parser("delete", help="Remove a worktree")
    ws_delete.add_argument("--repo", required=True, help="OWNER/REPO")
    ws_delete.add_argument("--branch", required=True, help="Branch name")

    ws_prune = workspace_sub.add_parser(
        "prune", help="Remove worktrees for branches no longer on origin"
    )
    ws_prune.add_argument("--repo", required=True, help="OWNER/REPO")

    ws_configure_auth = workspace_sub.add_parser(
        "configure-auth",
        help=(
            "Configure git credential-store from .env GH_TOKEN, "
            "bypassing Windows Credential Manager (GCM)"
        ),
    )
    ws_configure_auth.add_argument(
        "--path",
        default=None,
        dest="auth_path",
        help="Path to a git working tree (default: current directory)",
    )

    docker_cmd = subparsers.add_parser(
        "docker",
        help="Manage per-repo Docker containers for branch-level agent isolation",
    )
    docker_sub = docker_cmd.add_subparsers(dest="docker_command", required=True)

    dk_spin_up = docker_sub.add_parser(
        "spin-up", help="Start a container for a repo/branch"
    )
    dk_spin_up.add_argument("--repo", required=True, help="OWNER/REPO")
    dk_spin_up.add_argument("--branch", required=True, help="Branch name")
    dk_spin_up.add_argument(
        "--env-file",
        default=None,
        dest="env_file",
        help="Path to .env file to bind-mount read-only into the container",
    )

    dk_spin_down = docker_sub.add_parser(
        "spin-down", help="Stop and remove the container for a repo/branch"
    )
    dk_spin_down.add_argument("--repo", required=True, help="OWNER/REPO")
    dk_spin_down.add_argument("--branch", required=True, help="Branch name")

    dk_list = docker_sub.add_parser("list", help="List running agent containers")
    dk_list.add_argument("--repo", default=None, help="Filter by repo name (optional)")

    dk_build_base = docker_sub.add_parser(
        "build-base", help="Build or rebuild the base Docker image for a repo"
    )
    dk_build_base.add_argument("--repo", required=True, help="OWNER/REPO")
    dk_build_base.add_argument(
        "--path",
        default=".",
        dest="dockerfile_dir",
        help="Directory containing the Dockerfile (default: current directory)",
    )

    dk_shell = docker_sub.add_parser(
        "shell",
        help=(
            "One command: build image if needed, restart container, exec into bash. "
            "Use --rebuild to force a fresh image build (required after Dockerfile changes)."
        ),
    )
    dk_shell.add_argument("--repo", required=True, help="OWNER/REPO")
    dk_shell.add_argument("--branch", required=True, help="Branch name")
    dk_shell.add_argument(
        "--path",
        default=".",
        dest="dockerfile_dir",
        help="Directory containing the Dockerfile (default: current directory)",
    )
    dk_shell.add_argument(
        "--env-file",
        default=None,
        dest="env_file",
        help="Path to .env file to bind-mount read-only into the container",
    )
    dk_shell.add_argument(
        "--rebuild",
        action="store_true",
        default=False,
        help="Rebuild the base image before starting (use after Dockerfile changes)",
    )

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
        "label",
        "project",
        "issue",
        "pr",
        "branch",
        "workspace",
        "repo",
        "sync",
        "docker",
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
        repo_dir = Path(ns.path) if ns.path else _resolve_output_path(repo_name_hint)
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
        else:
            # An existing, non-empty local directory wasn't scaffolded by this
            # run -- detect its actual language stack from disk rather than
            # leaving settings (e.g. CodeQL default setup) with no language
            # info at all.
            languages = detect_languages_from_repo(repo_dir)

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
                languages=list(languages),
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
        out_dir = Path(ns.out) if ns.out else _resolve_output_path(init_name)
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
        if rc == 0 and not ns.dry_run and ns.repo:
            try:
                sync_repository_ruleset(
                    repo_dir=repo_dir,
                    repo=ns.repo,
                    languages=list(languages),
                    out=print,
                    warn=lambda msg: print(msg, file=sys.stderr),
                )
            except RuntimeError as exc:
                print(f"Warning: ruleset sync failed: {exc}", file=sys.stderr)
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
            try:
                backlog_file = _resolve_backlog_file_path(
                    repo_dir=repo_dir, file_arg=ns.file, repo=target_repo
                )
            except FileNotFoundError as exc:
                print(str(exc), file=sys.stderr)
                return 2
        else:
            try:
                markdown_source_dir = _find_existing_markdown_source_dir(repo_dir)
            except RuntimeError as exc:
                print(str(exc), file=sys.stderr)
                return 2
            if markdown_source_dir is not None:
                try:
                    auto_output = (
                        _local_backlog_path(target_repo)
                        if target_repo
                        else repo_dir / "local" / "backlog.json"
                    )
                    imported_backlog_file, import_summary = build_backlog_import_file(
                        source_dir=markdown_source_dir,
                        output_file=auto_output,
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
                try:
                    backlog_file = _resolve_backlog_file_path(
                        repo_dir=repo_dir, file_arg=None, repo=target_repo
                    )
                except FileNotFoundError as exc:
                    print(str(exc), file=sys.stderr)
                    return 2
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

    if ns.mode == "apply" and ns.apply_command == "settings":
        langs: tuple[str, ...] = ()
        if ns.languages:
            langs = _parse_languages_or_die(parser, ns.languages)
        try:
            apply_repository_settings(
                repo_dir=Path.cwd(),
                repo=ns.repo,
                dry_run=getattr(ns, "dry_run", False),
                out=print,
                warn=lambda line: print(line, file=sys.stderr),
                languages=list(langs) if langs else None,
            )
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1

        print("")
        print("Summary:")
        if getattr(ns, "dry_run", False):
            print("  mode: dry-run")
            print("  settings planned: True")
        else:
            print("  settings applied: True")
            if langs:
                print(f"  required status checks: {', '.join(langs)}")
        return 0

    if ns.mode == "apply" and ns.apply_command == "rules":
        targets, targets_error = _resolve_repo_targets(ns)
        if targets_error:
            print(targets_error, file=sys.stderr)
            return 2
        preview_only = not ns.do_apply or getattr(ns, "dry_run", False)
        failures = 0
        for target_repo, repo_dir in targets:
            try:
                apply_repository_settings(
                    repo_dir=repo_dir,
                    repo=target_repo,
                    dry_run=preview_only,
                    out=print,
                    warn=lambda line: print(line, file=sys.stderr),
                    languages=resolve_languages_for_repo(
                        repo_dir,
                        target_repo,
                        warn=lambda line: print(line, file=sys.stderr),
                    ),
                )
            except RuntimeError as exc:
                print(str(exc), file=sys.stderr)
                failures += 1

        print("")
        print("Summary:")
        if len(targets) > 1:
            print(f"  repos: {len(targets)}")
            print(f"  failures: {failures}")
        if preview_only:
            print("  mode: dry-run")
            print("  settings planned: True")
        else:
            print("  settings applied: True")
        return 1 if failures else 0

    if ns.mode == "check" and ns.check_command == "rules":
        targets, targets_error = _resolve_repo_targets(ns)
        if targets_error:
            print(targets_error, file=sys.stderr)
            return 2
        total_failed = 0
        for target_repo, repo_dir in targets:
            try:
                check_summary: SettingsCheckSummary = check_repository_settings(
                    repo_dir=repo_dir,
                    repo=target_repo,
                    out=print,
                    languages=resolve_languages_for_repo(
                        repo_dir,
                        target_repo,
                        warn=lambda line: print(line, file=sys.stderr),
                    ),
                )
            except RuntimeError as exc:
                print(str(exc), file=sys.stderr)
                total_failed += 1
                continue

            print("")
            print("Summary:")
            print(f"  repo: {check_summary.repo}")
            print(f"  checks passed: {check_summary.passed}")
            print(f"  checks failed: {check_summary.failed}")
            print(f"  checks skipped: {check_summary.skipped}")
            print(f"  drift items: {len(check_summary.drifts)}")
            total_failed += check_summary.failed
        return 1 if total_failed > 0 else 0

    if ns.mode == "check" and ns.check_command == "templates":
        targets, targets_error = _resolve_repo_targets(ns)
        if targets_error:
            print(targets_error, file=sys.stderr)
            return 2
        total_drifted = 0
        errors = 0
        for target_repo, repo_dir in targets:
            try:
                templates_summary: TemplatesCheckSummary = check_repository_templates(
                    repo_dir=repo_dir,
                    repo=target_repo,
                    out=print,
                )
            except RuntimeError as exc:
                print(str(exc), file=sys.stderr)
                errors += 1
                continue
            status = "DRIFT" if templates_summary.drifted_files else "PASS"
            print(f"{status}  {target_repo}")
            total_drifted += len(templates_summary.drifted_files)
        return 1 if (total_drifted > 0 or errors) else 0

    if ns.mode == "check" and ns.check_command == "settings":
        langs = (
            _parse_languages_or_die(parser, ns.languages)
            if ns.languages
            else tuple(
                resolve_languages_for_repo(
                    Path.cwd(),
                    ns.repo,
                    warn=lambda line: print(line, file=sys.stderr),
                )
            )
        )
        try:
            settings_summary: SettingsCheckSummary = check_repository_settings(
                repo_dir=Path.cwd(),
                repo=ns.repo,
                out=print,
                languages=list(langs) if langs else None,
            )
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1

        print("")
        print("Summary:")
        print(f"  repo: {settings_summary.repo}")
        if langs:
            print(f"  languages: {', '.join(langs)}")
        print(f"  checks passed: {settings_summary.passed}")
        print(f"  checks failed: {settings_summary.failed}")
        print(f"  checks skipped: {settings_summary.skipped}")
        print(f"  drift items: {len(settings_summary.drifts)}")
        return 1 if settings_summary.failed > 0 else 0

    if ns.mode == "repo" and ns.repo_command == "register":
        entry: RegistryEntry = register_repo(ns.repo, ns.path, ns.notes)
        print(f"Registered {entry.repo} -> {entry.local_path}")
        return 0

    if ns.mode == "repo" and ns.repo_command == "list":
        entries = list_registry()
        if not entries:
            print("No repos registered.")
            return 0
        for entry in entries:
            suffix = f"  ({entry.notes})" if entry.notes else ""
            print(f"{entry.repo} -> {entry.local_path}{suffix}")
        return 0

    if ns.mode == "repo" and ns.repo_command == "forget":
        removed = forget_repo(ns.repo)
        if not removed:
            print(f"Error: repo not registered: {ns.repo}", file=sys.stderr)
            return 2
        print(f"Removed {ns.repo} from the registry.")
        return 0

    if ns.mode == "repo" and ns.repo_command == "archive":
        if not ns.yes:
            if not sys.stdin.isatty():
                print(
                    "Non-interactive shell detected and --yes not set; refusing to archive.",
                    file=sys.stderr,
                )
                return 2
            reply = (
                input(
                    f"Archive {ns.repo}? This makes it read-only (reversible via the GitHub UI). [y/N] "
                )
                .strip()
                .lower()
            )
            if reply not in {"y", "yes"}:
                print("Aborted.")
                return 0
        token = token_from_repo(Path.cwd())
        if not token:
            print("Error: no GH_TOKEN found.", file=sys.stderr)
            return 2
        cp = repo_archive(ns.repo, token)
        if cp.returncode not in (0, 200):
            print(f"Error: {cp.stderr.strip()}", file=sys.stderr)
            return 1
        print(f"Archived {ns.repo}.")
        return 0

    if ns.mode == "repo" and ns.repo_command == "discover":
        token = token_from_repo(Path.cwd())
        if not token:
            client_id = os.environ.get("GITHUB_CLIENT_ID")
            try:
                token = prompt_for_token(client_id)
            except RuntimeError as exc:
                print(str(exc), file=sys.stderr)
                return 2
            upsert_env_var("GH_TOKEN", token, Path.cwd() / ".env")
            print("Token saved to .env")
        try:
            all_repos = discover_repos(token, org=getattr(ns, "org", None))
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        registered = set(load_registry().keys())
        new_repos = [r for r in all_repos if r not in registered]
        if not new_repos:
            print("No new repos found (all visible repos are already registered).")
            return 0
        print(f"Found {len(new_repos)} repo(s) not yet registered:")
        for i, r in enumerate(new_repos, 1):
            print(f"  [{i:>3}] {r}")
        if not ns.register:
            print("\nRun with --register to choose which to add to the local registry.")
            return 0
        if ns.yes:
            selected = list(new_repos)
        else:
            print()
            answer = input(
                'Enter numbers to register (e.g. 1,3,5-8), "all", or press Enter to skip: '
            ).strip()
            indices = parse_repo_selection(answer, len(new_repos))
            if not indices:
                print("Nothing registered.")
                return 0
            selected = [new_repos[i] for i in indices]
        reg = load_registry()
        for r in selected:
            reg[r] = RegistryEntry(repo=r, local_path="", notes="discovered")
        save_registry(reg)
        for r in selected:
            print(f"Registered {r}")
        return 0

    if ns.mode == "sync" and ns.sync_command == "rules":
        targets, targets_error = _resolve_repo_targets(ns)
        if targets_error:
            print(targets_error, file=sys.stderr)
            return 2

        drifted: list[tuple[str, Path]] = []
        errors = 0
        for target_repo, repo_dir in targets:
            try:
                drift_summary: SettingsCheckSummary = check_repository_settings(
                    repo_dir=repo_dir,
                    repo=target_repo,
                    out=print,
                    languages=list(
                        resolve_languages_for_repo(
                            repo_dir,
                            target_repo,
                            warn=lambda line: print(line, file=sys.stderr),
                        )
                    ),
                )
            except RuntimeError as exc:
                print(str(exc), file=sys.stderr)
                errors += 1
                continue
            print(
                f"  {target_repo}: {drift_summary.failed} drifted, {len(drift_summary.drifts)} drift items"
            )
            if drift_summary.failed > 0:
                drifted.append((target_repo, repo_dir))

        if not drifted:
            print("")
            print("No drift found. Nothing to apply.")
            return 1 if errors else 0

        print("")
        applied = 0
        for target_repo, repo_dir in drifted:
            if not ns.yes:
                answer = input(f"Apply fixes for {target_repo}? [y/N] ").strip().lower()
                if answer != "y":
                    print(f"Skipped {target_repo}.")
                    continue
            try:
                apply_repository_settings(
                    repo_dir=repo_dir,
                    repo=target_repo,
                    dry_run=False,
                    out=print,
                    warn=lambda line: print(line, file=sys.stderr),
                    languages=resolve_languages_for_repo(
                        repo_dir,
                        target_repo,
                        warn=lambda line: print(line, file=sys.stderr),
                    ),
                )
                applied += 1
            except RuntimeError as exc:
                print(str(exc), file=sys.stderr)
                errors += 1

        print("")
        print("Summary:")
        print(f"  repos checked: {len(targets)}")
        print(f"  repos drifted: {len(drifted)}")
        print(f"  repos applied: {applied}")
        return 1 if errors else 0

    if ns.mode == "sync" and ns.sync_command == "templates":
        targets, targets_error = _resolve_repo_targets(ns)
        if targets_error:
            print(targets_error, file=sys.stderr)
            return 2

        drifted_targets: list[tuple[str, Path]] = []
        errors = 0
        for target_repo, repo_dir in targets:
            try:
                templates_summary = check_repository_templates(
                    repo_dir=repo_dir, repo=target_repo, out=print
                )
            except RuntimeError as exc:
                print(str(exc), file=sys.stderr)
                errors += 1
                continue
            print(f"  {target_repo}: {len(templates_summary.drifted_files)} drifted")
            if templates_summary.drifted_files:
                drifted_targets.append((target_repo, repo_dir))

        if not drifted_targets:
            print("")
            print("No drift found. Nothing to sync.")
            return 1 if errors else 0

        print("")
        opened = 0
        for target_repo, repo_dir in drifted_targets:
            if not ns.yes:
                answer = (
                    input(f"Open sync PR for {target_repo}? [y/N] ").strip().lower()
                )
                if answer != "y":
                    print(f"Skipped {target_repo}.")
                    continue
            try:
                sync_result: TemplatesSyncResult = sync_repository_templates(
                    repo_dir=repo_dir,
                    repo=target_repo,
                    out=print,
                    warn=lambda line: print(line, file=sys.stderr),
                )
            except RuntimeError as exc:
                print(str(exc), file=sys.stderr)
                errors += 1
                continue
            if sync_result.pr_url is not None:
                opened += 1

        print("")
        print("Summary:")
        print(f"  repos checked: {len(targets)}")
        print(f"  repos drifted: {len(drifted_targets)}")
        print(f"  sync PRs opened: {opened}")
        return 1 if errors else 0

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
            elif ns.project_command == "setup":
                from .project_config import load_config, prompt_config, save_config

                project_cfg = load_config(repo_dir)
                if ns.interactive:
                    if not sys.stdin.isatty():
                        print(
                            "Error: --interactive requires an interactive terminal.",
                            file=sys.stderr,
                        )
                        return 1
                    project_cfg = prompt_config(project_cfg, prompt_fn=input, out=print)
                    config_path = repo_dir / ".repo-scaffold.yml"
                    save_config(project_cfg, config_path)
                    print(f"Saved: {config_path}")
                mutation_summary = setup_project(
                    repo_dir=repo_dir,
                    owner=ns.project_owner,
                    project_number=ns.project_number,
                    project_title=ns.project_title,
                    config_path=None,
                    write_actions_template=not ns.no_actions_template,
                    repo=getattr(ns, "repo", None),
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
            elif ns.project_command == "setup-views":
                mutation_summary = setup_project_views(
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
            output_file = _local_backlog_path(normalized_repo)
        else:
            output_file = repo_dir / "local" / "backlog.json"

        try:
            source_dir = _resolve_markdown_source_dir(
                repo_dir=repo_dir, source_arg=ns.source
            )
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1

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

        if ns.issue_command == "delete":
            owner, _, repo_name = target_repo.partition("/")
            cp = issue_delete(owner, repo_name, ns.issue_number, token)
            if cp.returncode != 0:
                print(cp.stderr.strip() or "Failed deleting issue.", file=sys.stderr)
                return 1
            print(f"Issue #{ns.issue_number} deleted.")
            return 0

        if ns.issue_command == "add-sub-issue":
            owner, _, repo_name = target_repo.partition("/")
            cp = issue_add_sub_issue(
                owner, repo_name, ns.parent_number, ns.child_number, token
            )
            if cp.returncode != 0:
                print(cp.stderr.strip() or "Failed linking sub-issue.", file=sys.stderr)
                return 1
            print(
                f"Issue #{ns.child_number} linked as sub-issue of #{ns.parent_number}."
            )
            return 0

        if ns.issue_command == "sync-hierarchy":
            cp = issue_sync_hierarchy(target_repo, token, apply=ns.apply)
            if cp.returncode != 0:
                print(
                    cp.stderr.strip() or "Failed syncing issue hierarchy.",
                    file=sys.stderr,
                )
                return 1
            report = _json.loads(cp.stdout)
            mode = "APPLY" if ns.apply else "DRY RUN"
            print(f"Hierarchy sync for {target_repo} ({mode})")
            for key in (
                "linked",
                "already_linked",
                "would_link",
                "conflict",
                "ambiguous",
                "unaffiliated",
                "errors",
            ):
                items = report.get(key, [])
                print(f"  {key}: {len(items)}")
                for item in items:
                    print(f"    {item}")
            return 0

        if ns.issue_command == "re-parent":
            owner, _, repo_name = target_repo.partition("/")
            if not issue_node_id(owner, repo_name, ns.new_parent_number, token):
                print(
                    f"Error: could not resolve --to-parent #{ns.new_parent_number}.",
                    file=sys.stderr,
                )
                return 1
            rm_cp = issue_remove_sub_issue(
                owner, repo_name, ns.old_parent_number, ns.child_number, token
            )
            if rm_cp.returncode != 0:
                print(
                    rm_cp.stderr.strip() or "Failed removing existing parent link.",
                    file=sys.stderr,
                )
                return 1
            add_cp = issue_add_sub_issue(
                owner, repo_name, ns.new_parent_number, ns.child_number, token
            )
            if add_cp.returncode != 0:
                print(
                    add_cp.stderr.strip() or "Failed linking to new parent.",
                    file=sys.stderr,
                )
                rb_cp = issue_add_sub_issue(
                    owner, repo_name, ns.old_parent_number, ns.child_number, token
                )
                if rb_cp.returncode != 0:
                    print(
                        f"Rollback failed. Issue #{ns.child_number} is now detached.",
                        file=sys.stderr,
                    )
                else:
                    print(
                        f"Rolled back: #{ns.child_number} re-linked to #{ns.old_parent_number}.",
                        file=sys.stderr,
                    )
                return 1
            print(
                f"Issue #{ns.child_number} re-parented: "
                f"#{ns.old_parent_number} -> #{ns.new_parent_number}."
            )
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

        if ns.pr_command == "react":
            cp = react(
                target_repo,
                "pull_request_review_comment",
                ns.comment_id,
                ns.reaction,
                token,
            )
            if cp.returncode not in (0, 201):
                print(cp.stderr.strip() or "Failed adding reaction.", file=sys.stderr)
                return 1
            reaction = _json.loads(cp.stdout)
            print(f"Reaction added: {reaction.get('content', ns.reaction)}")
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
            if ns.title is None and resolved_body is None and ns.state is None:
                print(
                    "Error: at least one of --title, --body, or --state is required.",
                    file=sys.stderr,
                )
                return 2
            cp = pr_update(
                target_repo,
                pr_number=ns.pr_number,
                token=token,
                title=ns.title,
                body=resolved_body,
                state=ns.state,
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

        if ns.pr_command == "annotations":
            cp = pr_annotations(target_repo, ns.pr_number, token)
            if cp.returncode != 0:
                print(
                    cp.stderr.strip() or "Failed fetching annotations.", file=sys.stderr
                )
                return 1
            items = _json.loads(cp.stdout)
            if ns.json_output:
                print(_json.dumps(items, indent=2))
            else:
                if not items:
                    print("No annotations found.")
                for a in items:
                    level = a.get("annotation_level", "?")
                    check = a.get("check_run", "?")
                    path = a.get("path", "?")
                    line = a.get("start_line", "?")
                    msg = (a.get("message") or "").strip()
                    print(f"[{level}] {check} -- {path}:{line}")
                    print(f"  {msg}")
            return 0

        if ns.pr_command == "rerun":
            cp = pr_rerun(target_repo, ns.pr_number, token, failed_only=ns.failed_only)
            if cp.returncode != 0:
                print(cp.stderr.strip() or "Failed to re-run jobs.", file=sys.stderr)
                return 1
            result = _json.loads(cp.stdout)
            triggered = result.get("triggered", [])
            errors = result.get("errors", [])
            if triggered:
                print(
                    f"Re-triggered {len(triggered)} run(s): {', '.join(str(r) for r in triggered)}"
                )
            if errors:
                for e in errors:
                    print(f"  error: {e}", file=sys.stderr)
            if not triggered and not errors:
                print("No runs found to re-trigger.")
            return 0 if not errors else 1

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

        if ns.pr_command == "check-sop":
            owner, repo_name = target_repo.split("/", 1)
            cp = pr_check_sop(owner, repo_name, ns.pr_number, token)
            if cp.returncode != 0:
                print(cp.stderr.strip() or "Failed checking SOP.", file=sys.stderr)
                return 1
            report = _json.loads(cp.stdout)
            non_compliant = sum(1 for t in report if not t.get("compliant"))
            if ns.json_output:
                print(_json.dumps(report, indent=2))
                return 1 if non_compliant else 0
            if not report:
                print("No review threads found.")
                return 0
            for t in report:
                tid = t.get("thread_id", "?")
                cid = t.get("first_comment_id", "?")
                if t.get("compliant"):
                    print(f"  OK  thread={tid}  comment={cid}")
                else:
                    missing = ", ".join(t.get("missing", []))
                    print(f"  FAIL  thread={tid}  comment={cid}  missing: {missing}")
            total = len(report)
            print(f"\n{total - non_compliant}/{total} threads SOP-compliant.")
            return 1 if non_compliant else 0

        if ns.pr_command == "reviews":
            cp = pr_reviews(target_repo, ns.pr_number, token)
            if cp.returncode != 0:
                print(
                    cp.stderr.strip() or "Failed fetching reviews.",
                    file=sys.stderr,
                )
                return 1
            reviews = _json.loads(cp.stdout)
            if ns.json_output:
                print(_json.dumps(reviews, indent=2))
            else:
                if not reviews:
                    print("No reviews found.")
                for r in reviews:
                    state = r.get("state", "?")
                    user = r.get("user", "?")
                    submitted = r.get("submitted_at", "")[:10]
                    body = (r.get("body") or "").strip()
                    print(f"{user}  {state}  {submitted}")
                    if body:
                        print(f"  {body[:200]}")
            return 0

        if ns.pr_command == "list-comments":
            cp = pr_list_comments(target_repo, ns.pr_number, token)
            if cp.returncode != 0:
                print(
                    cp.stderr.strip() or "Failed fetching PR comments.",
                    file=sys.stderr,
                )
                return 1
            comments = _json.loads(cp.stdout)
            if ns.json_output:
                print(_json.dumps(comments, indent=2))
            else:
                if not comments:
                    print("No comments found.")
                for c in comments:
                    author = c.get("user", {}).get("login", "?")
                    body = (c.get("body") or "").strip()
                    path = c.get("path", "")
                    if path:
                        line = c.get("line") or c.get("original_line") or "?"
                        print(f"{author} on {path}:{line}")
                    else:
                        created = (c.get("created_at") or "")[:10]
                        print(f"{author} ({created})")
                    print(f"  {body[:200]}")
            return 0

        if ns.pr_command == "request-reviewer":
            cp = pr_request_reviewer(target_repo, ns.pr_number, token, ns.reviewers)
            if cp.returncode not in (0, 201):
                print(
                    cp.stderr.strip() or "Failed requesting reviewer.",
                    file=sys.stderr,
                )
                return 1
            data = _json.loads(cp.stdout)
            requested = [
                r.get("login", "") for r in data.get("requested_reviewers", [])
            ]
            print(f"Reviewers requested on PR #{ns.pr_number}: {', '.join(requested)}")
            return 0

        if ns.pr_command == "wait":
            from .github_api import pr_wait

            cp = pr_wait(
                target_repo,
                ns.pr_number,
                token,
                interval=ns.interval,
                timeout=ns.timeout,
            )
            if cp.returncode == 0:
                print(cp.stdout.strip())
                return 0
            print(cp.stderr.strip(), file=sys.stderr)
            return cp.returncode

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

        if ns.branch_command == "rename":
            cp = branch_rename(target_repo, ns.name, ns.new_name, token)
            if cp.returncode not in (0, 201):
                print(cp.stderr.strip() or "Failed renaming branch.", file=sys.stderr)
                return 1
            print(f"Branch renamed: {ns.name} -> {ns.new_name}")
            print("If a PR was open against this branch, check its state now -- it may")
            print("have closed instead of following the rename. See branch_rename()'s")
            print("docstring for details.")
            return 0

    if ns.mode == "label":
        import json as _json

        target_repo, repo_error = _resolve_repo_from_args_or_env(
            repo=ns.repo, fallback_name=None
        )
        if repo_error:
            print(repo_error, file=sys.stderr)
            return 2
        assert target_repo is not None
        token = token_from_repo(Path.cwd()) or ""

        if ns.label_command == "list":
            cp = label_list(target_repo, token)
            if cp.returncode not in (0, 200):
                print(cp.stderr.strip() or "Failed listing labels.", file=sys.stderr)
                return 1
            labels = _json.loads(cp.stdout)
            if getattr(ns, "json", False):
                print(cp.stdout)
                return 0
            for lbl in labels:
                desc = f" -- {lbl['description']}" if lbl.get("description") else ""
                print(f"  #{lbl['color']}  {lbl['name']}{desc}")
            print(f"\nTotal: {len(labels)}")
            return 0

        if ns.label_command == "create":
            cp = label_create(target_repo, ns.name, ns.color, token, ns.description)
            if cp.returncode not in (0, 200, 201):
                print(cp.stderr.strip() or "Failed creating label.", file=sys.stderr)
                return 1
            print(f"Label created: {ns.name}")
            return 0

        if ns.label_command == "delete":
            cp = label_delete(target_repo, ns.name, token)
            if cp.returncode not in (0, 200, 204):
                print(cp.stderr.strip() or "Failed deleting label.", file=sys.stderr)
                return 1
            print(f"Label deleted: {ns.name}")
            return 0

        if ns.label_command == "apply-preset":
            cp = label_apply_preset(target_repo, token)
            if cp.returncode not in (0, 200):
                print(cp.stderr.strip() or "Failed applying preset.", file=sys.stderr)
                return 1
            result = _json.loads(cp.stdout)
            created = result.get("created", [])
            skipped = result.get("skipped", 0)
            if created:
                print(f"Created: {', '.join(created)}")
            print(f"Skipped (already exist): {skipped}")
            return 0

    if ns.mode == "workspace":
        import warnings

        warnings.warn(
            "The 'workspace' command group is deprecated. "
            "Use 'repo-scaffold docker spin-up/spin-down/list' instead.",
            DeprecationWarning,
            stacklevel=1,
        )
        from .workspace_ops import (
            workspace_configure_auth,
            workspace_create,
            workspace_delete,
            workspace_list,
            workspace_prune,
        )

        token = token_from_repo(Path.cwd()) or ""

        if ns.workspace_command == "create":
            env_source = Path(ns.env_source) if ns.env_source else None
            cp = workspace_create(
                ns.repo, ns.branch, token, base=ns.base, env_source=env_source
            )
            if cp.returncode != 0:
                print(cp.stderr.strip(), file=sys.stderr)
                return 1
            print(cp.stdout.strip())
            return 0

        if ns.workspace_command == "list":
            cp = workspace_list(repo=ns.repo)
            if cp.returncode != 0:
                print(cp.stderr.strip(), file=sys.stderr)
                return 1
            print(cp.stdout.strip())
            return 0

        if ns.workspace_command == "delete":
            cp = workspace_delete(ns.repo, ns.branch)
            if cp.returncode != 0:
                print(cp.stderr.strip(), file=sys.stderr)
                return 1
            print(cp.stdout.strip())
            return 0

        if ns.workspace_command == "prune":
            cp = workspace_prune(ns.repo)
            if cp.returncode != 0:
                print(cp.stderr.strip(), file=sys.stderr)
                return 1
            print(cp.stdout.strip())
            return 0

        if ns.workspace_command == "configure-auth":
            auth_path = Path(ns.auth_path).resolve() if ns.auth_path else None
            effective_token = (auth_path and token_from_repo(auth_path)) or token
            cp = workspace_configure_auth(effective_token, path=auth_path)
            if cp.returncode != 0:
                print(cp.stderr.strip(), file=sys.stderr)
                return 1
            print(cp.stdout.strip())
            return 0

    if ns.mode == "docker":
        from .docker_ops import (
            docker_build_base,
            docker_list,
            docker_spin_down,
            docker_spin_up,
        )

        token = token_from_repo(Path.cwd()) or ""

        if ns.docker_command == "spin-up":
            env_file = Path(ns.env_file) if ns.env_file else None
            cp = docker_spin_up(ns.repo, ns.branch, token, env_path=env_file)
            if cp.returncode != 0:
                print(cp.stderr.strip(), file=sys.stderr)
                return 1
            print(cp.stdout.strip())
            return 0

        if ns.docker_command == "spin-down":
            cp = docker_spin_down(ns.repo, ns.branch)
            if cp.returncode != 0:
                print(cp.stderr.strip(), file=sys.stderr)
                return 1
            print(cp.stdout.strip())
            return 0

        if ns.docker_command == "list":
            cp = docker_list(repo=ns.repo)
            if cp.returncode != 0:
                print(cp.stderr.strip(), file=sys.stderr)
                return 1
            print(cp.stdout.strip())
            return 0

        if ns.docker_command == "build-base":
            cp = docker_build_base(ns.repo, Path(ns.dockerfile_dir))
            if cp.returncode != 0:
                print(cp.stderr.strip(), file=sys.stderr)
                return 1
            print(cp.stdout.strip())
            return 0

        if ns.docker_command == "shell":
            from .docker_ops import docker_shell

            env_file = Path(ns.env_file) if ns.env_file else None
            try:
                docker_shell(
                    ns.repo,
                    ns.branch,
                    token,
                    Path(ns.dockerfile_dir),
                    rebuild=ns.rebuild,
                    env_path=env_file,
                )
            except RuntimeError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            return 0

    parser.error("Unsupported command.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
