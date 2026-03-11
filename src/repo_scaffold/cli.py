from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .backlog_ops import (
    BacklogApplySummary,
    apply_backlog,
    resolve_authenticated_login,
    resolve_project_target_for_auth_check,
)
from .create_ops import CreateSummary, create_repository
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

DEFAULT_INIT_NAME_PREFIX = "repo-scaffold-e2e"
DEFAULT_INIT_LANGUAGES = "go,python,react"


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

    if not os.environ.get("GH_TOKEN"):
        if os.environ.get("GITHUB_TOKEN"):
            os.environ["GH_TOKEN"] = os.environ["GITHUB_TOKEN"]
        elif os.environ.get("github_token"):
            os.environ["GH_TOKEN"] = os.environ["github_token"]

    if not os.environ.get("GITHUB_ORG") and os.environ.get("github_org"):
        os.environ["GITHUB_ORG"] = os.environ["github_org"]
    if not os.environ.get("GITHUB_REPO") and os.environ.get("github_repo"):
        os.environ["GITHUB_REPO"] = os.environ["github_repo"]
    if not os.environ.get("GH_REPO"):
        if os.environ.get("GITHUB_REPOSITORY"):
            os.environ["GH_REPO"] = os.environ["GITHUB_REPOSITORY"]
        elif os.environ.get("github_full_repo"):
            os.environ["GH_REPO"] = os.environ["github_full_repo"]

    if not os.environ.get("GITHUB_PROJECT_TITLE") and os.environ.get("github_project_title"):
        os.environ["GITHUB_PROJECT_TITLE"] = os.environ["github_project_title"]
    if not os.environ.get("GITHUB_PROJECT_TITLE_TEMPLATE") and os.environ.get(
        "github_project_title_template"
    ):
        os.environ["GITHUB_PROJECT_TITLE_TEMPLATE"] = os.environ["github_project_title_template"]


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


def _seed_env_for_parsed_mode(ns: argparse.Namespace) -> None:
    _seed_env_from_dotenv(Path.cwd() / ".env")
    if ns.mode == "create" and getattr(ns, "path", None):
        _seed_env_from_dotenv(Path(ns.path) / ".env")
    if ns.mode == "apply" and hasattr(ns, "path"):
        _seed_env_from_dotenv(Path(ns.path) / ".env")


def _add_overwrite_policy_args(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--yes", action="store_true", help="Assume yes to overwrite prompts")
    group.add_argument("--no", action="store_true", help="Assume no to overwrite prompts")
    group.add_argument("--force", action="store_true", help="Overwrite without prompting")
    parser.add_argument("--dry-run", action="store_true", help="Print planned actions without changing state")
    parser.add_argument("--backup", action="store_true", help="Write <file>.bak.<timestamp> before overwrite")


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


def _add_apply_target_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--path", default=".", help="Target repository path (default: .)")
    parser.add_argument("--owner", help="GitHub owner for generated metadata")
    parser.add_argument("--name", help="Repository name override (defaults to folder name)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="repo-scaffold",
        description="Repository operations toolkit for scaffold create/init/apply/delete workflows.",
    )
    subparsers = parser.add_subparsers(dest="mode", required=True)

    create_cmd = subparsers.add_parser("create", help="Create/push remote repo and apply baseline settings")
    create_cmd.add_argument(
        "--path",
        help="Local repository path (default: ./out/<repo-name>; auto-inits when missing/empty)",
    )
    create_cmd.add_argument("--repo", help="Target GitHub repo in owner/repo format")
    create_cmd.add_argument("--owner", help="GitHub owner override (used when --repo is omitted)")
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
    create_cmd.add_argument("--dry-run", action="store_true", help="Print planned actions without changing state")

    delete_cmd = subparsers.add_parser("delete", help="Delete matching GitHub repositories")
    delete_cmd.add_argument("--owner", help="GitHub owner/org (defaults to .env/environment)")
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

    apply_cmd = subparsers.add_parser("apply", help="Apply capabilities to an existing repo")
    apply_sub = apply_cmd.add_subparsers(dest="apply_command", required=True)

    apply_templates = apply_sub.add_parser("templates", parents=[apply_parent], help="Apply GitHub templates")
    _add_apply_target_args(apply_templates)

    apply_ci = apply_sub.add_parser("ci", parents=[apply_parent], help="Apply CI workflow")
    _add_apply_target_args(apply_ci)
    apply_ci.add_argument(
        "--languages",
        required=True,
        help="Comma-separated language list: go, python, react",
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
        help="Create milestones/issues in GitHub from backlog/issues.json",
    )
    apply_backlog_cmd.add_argument("--path", default=".", help="Repo path containing backlog data (default: .)")
    apply_backlog_cmd.add_argument("--repo", help="Target GitHub repo (owner/repo)")
    apply_backlog_cmd.add_argument(
        "--file",
        default="backlog/issues.json",
        help="Backlog JSON path (default: backlog/issues.json)",
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
        help="Print recommended gh api commands for repository rules/settings",
    )
    apply_rules.add_argument("--repo", help="Target GitHub repo (owner/repo)")
    apply_rules.add_argument(
        "--apply",
        action="store_true",
        dest="do_apply",
        help="Execute commands instead of only printing them",
    )

    return parser


def _normalize_argv(argv: list[str] | None) -> list[str]:
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] in {"-h", "--help"}:
        return raw
    if raw and raw[0] in {"create", "init", "apply", "delete"}:
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


def _parse_languages_or_die(parser: argparse.ArgumentParser, raw: str) -> tuple[str, ...]:
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


def _render_rules_commands(repo: str) -> list[str]:
    patch_payload = """{
  "allow_squash_merge": true,
  "allow_merge_commit": false,
  "allow_rebase_merge": false,
  "delete_branch_on_merge": true
}"""
    protection_payload = """{
  "required_status_checks": {
    "strict": false,
    "contexts": []
  },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "dismiss_stale_reviews": false,
    "require_code_owner_reviews": false,
    "required_approving_review_count": 1,
    "require_last_push_approval": false
  },
  "restrictions": null,
  "required_linear_history": true,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "block_creations": false,
  "required_conversation_resolution": true,
  "lock_branch": false,
  "allow_fork_syncing": true
}"""
    return [
        f"gh api --method PATCH '/repos/{repo}' --input - <<'JSON'\n{patch_payload}\nJSON",
        (
            f"gh api --method PUT '/repos/{repo}/branches/main/protection' --input - <<'JSON'\n"
            f"{protection_payload}\nJSON"
        ),
        f"gh api --method PUT '/repos/{repo}/vulnerability-alerts'",
        f"gh api --method PUT '/repos/{repo}/automated-security-fixes'",
    ]


def _apply_rules(repo: str) -> int:
    commands = _render_rules_commands(repo)
    failures = 0
    for command in commands:
        cp = subprocess.run(["bash", "-lc", command], text=True, capture_output=True, check=False)
        if cp.returncode != 0:
            failures += 1
            if cp.stderr.strip():
                print(cp.stderr.strip(), file=sys.stderr)
    return failures


def main(argv: list[str] | None = None) -> int:
    _seed_env_from_dotenv(Path.cwd() / ".env")
    parser = build_parser()
    ns = parser.parse_args(_normalize_argv(argv))
    _seed_env_for_parsed_mode(ns)

    if ns.mode == "create":
        repo_name_hint = (
            _repo_name_from_repo_ref(ns.repo) or (ns.name or "").strip() or _default_init_name()
        )
        repo_dir = Path(ns.path) if ns.path else default_output_path(repo_name_hint)
        if repo_dir.exists() and not repo_dir.is_dir():
            print(f"Error: local repo path exists and is not a directory: {repo_dir}", file=sys.stderr)
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
                print("Error: failed to initialize scaffold for create.", file=sys.stderr)
                return 1

        create_repo_dir = repo_dir
        temp_create_dir: tempfile.TemporaryDirectory[str] | None = None
        if ns.dry_run and needs_init and not repo_dir.exists():
            temp_create_dir = tempfile.TemporaryDirectory(prefix="repo-scaffold-create-")
            create_repo_dir = Path(temp_create_dir.name)

        try:
            summary: CreateSummary = create_repository(
                repo_dir=create_repo_dir,
                repo=ns.repo,
                owner=ns.owner,
                name=ns.name,
                visibility=ns.visibility,
                apply_settings=not ns.skip_settings,
                dry_run=ns.dry_run,
                out=(
                    (lambda line: print(line.replace(create_repo_dir.as_posix(), repo_dir.as_posix())))
                    if temp_create_dir
                    else print
                ),
                err=(
                    (lambda line: print(line.replace(create_repo_dir.as_posix(), repo_dir.as_posix()), file=sys.stderr))
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
        print(f"  repo: {summary.repo}")
        print(f"  repo created: {summary.repo_created}")
        print(f"  pushed: {summary.pushed}")
        print(f"  settings applied: {summary.settings_applied}")
        print(f"  failures: {summary.failures}")
        return 1 if summary.failures > 0 else 0

    if ns.mode == "delete":
        try:
            summary: DeleteSummary = delete_repositories(
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
        if summary.owner is not None:
            print(f"  owner: {summary.owner}")
        print(f"  remote matched: {summary.remote_matched}")
        print(f"  remote deleted: {summary.remote_deleted}")
        print(f"  remote skipped: {summary.remote_skipped}")
        print(f"  remote failures: {summary.remote_failures}")
        print(f"  local matched: {summary.local_matched}")
        print(f"  local deleted: {summary.local_deleted}")
        print(f"  local skipped: {summary.local_skipped}")
        print(f"  local failures: {summary.local_failures}")
        print(f"  failures: {summary.failures}")
        return 1 if summary.failures > 0 else 0

    if ns.mode == "init":
        init_name = (ns.name or "").strip() or _default_init_name()
        languages = _parse_languages_or_die(parser, ns.languages)
        out_dir = Path(ns.out) if ns.out else default_output_path(init_name)
        if out_dir.exists() and not out_dir.is_dir():
            print(f"Error: output path '{out_dir}' exists and is not a directory.", file=sys.stderr)
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
            print(f"{'Dry-run complete for' if ns.dry_run else 'Scaffold initialized at'}: {out_dir}")
        return rc

    if ns.mode == "apply" and ns.apply_command == "templates":
        repo_dir = Path(ns.path)
        if repo_dir.exists() and not repo_dir.is_dir():
            print(f"Error: target path '{repo_dir}' exists and is not a directory.", file=sys.stderr)
            return 2
        files = build_template_files(repo_dir, owner=ns.owner, name=ns.name)
        rc = _run_file_apply(files, _policy_from_ns(ns))
        if rc == 0:
            print(f"{'Dry-run complete for' if ns.dry_run else 'Templates applied to'}: {repo_dir}")
        return rc

    if ns.mode == "apply" and ns.apply_command == "ci":
        repo_dir = Path(ns.path)
        if repo_dir.exists() and not repo_dir.is_dir():
            print(f"Error: target path '{repo_dir}' exists and is not a directory.", file=sys.stderr)
            return 2
        languages = _parse_languages_or_die(parser, ns.languages)
        files = build_ci_files(repo_dir, languages=languages, owner=ns.owner, name=ns.name)
        rc = _run_file_apply(files, _policy_from_ns(ns))
        if rc == 0:
            print(f"{'Dry-run complete for' if ns.dry_run else 'CI applied to'}: {repo_dir}")
        return rc

    if ns.mode == "apply" and ns.apply_command == "dependabot":
        repo_dir = Path(ns.path)
        if repo_dir.exists() and not repo_dir.is_dir():
            print(f"Error: target path '{repo_dir}' exists and is not a directory.", file=sys.stderr)
            return 2
        if ns.languages:
            languages = _parse_languages_or_die(parser, ns.languages)
        else:
            languages = detect_languages_from_repo(repo_dir)
        files = build_dependabot_files(repo_dir, languages=languages, owner=ns.owner, name=ns.name)
        rc = _run_file_apply(files, _policy_from_ns(ns))
        if rc == 0:
            print(f"{'Dry-run complete for' if ns.dry_run else 'Dependabot applied to'}: {repo_dir}")
        return rc

    if ns.mode == "apply" and ns.apply_command == "backlog":
        repo_dir = Path(ns.path)
        if not repo_dir.exists() or not repo_dir.is_dir():
            print(f"Error: repo path does not exist or is not a directory: {repo_dir}", file=sys.stderr)
            return 2
        target_repo, repo_error = _resolve_repo_from_args_or_env(repo=ns.repo, fallback_name=repo_dir.name)
        if repo_error:
            print(repo_error, file=sys.stderr)
            return 2
        assert target_repo is not None
        effective_project_number = ns.project_number
        effective_project_title = ns.project_title
        if ns.with_project and effective_project_number is None and not effective_project_title:
            repo_name = target_repo.split("/", 1)[1]
            env_project_title = (os.environ.get("GITHUB_PROJECT_TITLE") or "").strip()
            env_project_template = (os.environ.get("GITHUB_PROJECT_TITLE_TEMPLATE") or "").strip()
            if env_project_title:
                effective_project_title = env_project_title
            elif env_project_template:
                effective_project_title = env_project_template.replace("{repo}", repo_name)
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
        backlog_file = Path(ns.file)
        if not backlog_file.is_absolute():
            backlog_file = repo_dir / backlog_file
        try:
            summary: BacklogApplySummary = apply_backlog(
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
        print(f"  milestones created: {summary.milestones_created}")
        print(f"  milestones skipped: {summary.milestones_skipped}")
        print(f"  issues created: {summary.issues_created}")
        print(f"  issues skipped: {summary.issues_skipped}")
        if effective_project_number is not None or effective_project_title:
            print(f"  project created: {summary.project_created}")
            print(f"  project items added: {summary.project_items_added}")
            print(f"  project items skipped: {summary.project_items_skipped}")
        print(f"  failures: {summary.failures}")
        return 1 if summary.failures > 0 else 0

    if ns.mode == "apply" and ns.apply_command == "rules":
        target_repo, repo_error = _resolve_repo_from_args_or_env(repo=ns.repo, fallback_name=None)
        if repo_error:
            print(repo_error, file=sys.stderr)
            return 2
        assert target_repo is not None
        commands = _render_rules_commands(target_repo)
        if not ns.do_apply or getattr(ns, "dry_run", False):
            print("Recommended gh api commands:")
            for cmd in commands:
                print("")
                print(cmd)
            return 0

        failures = _apply_rules(target_repo)
        print(f"Applied rules commands with {failures} failure(s).")
        return 1 if failures > 0 else 0

    parser.error("Unsupported command.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
