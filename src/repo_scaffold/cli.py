from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from .backlog_ops import BacklogApplySummary, apply_backlog
from .create_ops import CreateSummary, create_repository
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


def _add_overwrite_policy_args(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--yes", action="store_true", help="Assume yes to overwrite prompts")
    group.add_argument("--no", action="store_true", help="Assume no to overwrite prompts")
    group.add_argument("--force", action="store_true", help="Overwrite without prompting")
    parser.add_argument("--dry-run", action="store_true", help="Print planned actions without changing state")
    parser.add_argument("--backup", action="store_true", help="Write <file>.bak.<timestamp> before overwrite")


def _add_scaffold_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--name", required=True, help="Repository name")
    parser.add_argument(
        "--languages",
        required=True,
        help="Comma-separated language list: go, python, react",
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
        description="Repository operations toolkit for scaffold init/apply workflows.",
    )
    subparsers = parser.add_subparsers(dest="mode", required=True)

    create_cmd = subparsers.add_parser("create", help="Create/push remote repo and apply baseline settings")
    create_cmd.add_argument("--path", default=".", help="Local repository path (default: .)")
    create_cmd.add_argument("--repo", help="Target GitHub repo in owner/repo format")
    create_cmd.add_argument("--owner", help="GitHub owner override (used when --repo is omitted)")
    create_cmd.add_argument("--name", help="Repository name override (used when --repo is omitted)")
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
    apply_backlog_cmd.add_argument("--repo", required=True, help="Target GitHub repo (owner/repo)")
    apply_backlog_cmd.add_argument(
        "--file",
        default="backlog/issues.json",
        help="Backlog JSON path (default: backlog/issues.json)",
    )

    apply_rules = apply_sub.add_parser(
        "rules",
        parents=[apply_parent],
        help="Print recommended gh api commands for repository rules/settings",
    )
    apply_rules.add_argument("--repo", required=True, help="Target GitHub repo (owner/repo)")
    apply_rules.add_argument(
        "--apply",
        action="store_true",
        dest="do_apply",
        help="Execute commands instead of only printing them",
    )

    return parser


def _normalize_argv(argv: list[str] | None) -> list[str]:
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] in {"create", "init", "apply"}:
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
    parser = build_parser()
    ns = parser.parse_args(_normalize_argv(argv))

    if ns.mode == "create":
        repo_dir = Path(ns.path)
        if not repo_dir.exists() or not repo_dir.is_dir():
            print(f"Error: local repo path does not exist or is not a directory: {repo_dir}", file=sys.stderr)
            return 2

        try:
            summary: CreateSummary = create_repository(
                repo_dir=repo_dir,
                repo=ns.repo,
                owner=ns.owner,
                name=ns.name,
                visibility=ns.visibility,
                apply_settings=not ns.skip_settings,
                dry_run=ns.dry_run,
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
        print(f"  repo: {summary.repo}")
        print(f"  repo created: {summary.repo_created}")
        print(f"  pushed: {summary.pushed}")
        print(f"  settings applied: {summary.settings_applied}")
        print(f"  failures: {summary.failures}")
        return 1 if summary.failures > 0 else 0

    if ns.mode == "init":
        languages = _parse_languages_or_die(parser, ns.languages)
        out_dir = Path(ns.out) if ns.out else default_output_path(ns.name)
        if out_dir.exists() and not out_dir.is_dir():
            print(f"Error: output path '{out_dir}' exists and is not a directory.", file=sys.stderr)
            return 2

        cfg = ScaffoldConfig(
            name=ns.name,
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
        backlog_file = Path(ns.file)
        if not backlog_file.is_absolute():
            backlog_file = repo_dir / backlog_file
        try:
            summary: BacklogApplySummary = apply_backlog(
                repo_dir=repo_dir,
                repo=ns.repo,
                backlog_file=backlog_file,
                dry_run=ns.dry_run,
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
        print(f"  failures: {summary.failures}")
        return 1 if summary.failures > 0 else 0

    if ns.mode == "apply" and ns.apply_command == "rules":
        commands = _render_rules_commands(ns.repo)
        if not ns.do_apply or getattr(ns, "dry_run", False):
            print("Recommended gh api commands:")
            for cmd in commands:
                print("")
                print(cmd)
            return 0

        failures = _apply_rules(ns.repo)
        print(f"Applied rules commands with {failures} failure(s).")
        return 1 if failures > 0 else 0

    parser.error("Unsupported command.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
