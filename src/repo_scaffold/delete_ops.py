from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence


@dataclass(frozen=True)
class DeleteSummary:
    owner: str | None
    remote_matched: int
    remote_deleted: int
    remote_skipped: int
    remote_failures: int
    local_matched: int
    local_deleted: int
    local_skipped: int
    local_failures: int

    @property
    def matched(self) -> int:
        return self.remote_matched + self.local_matched

    @property
    def deleted(self) -> int:
        return self.remote_deleted + self.local_deleted

    @property
    def skipped(self) -> int:
        return self.remote_skipped + self.local_skipped

    @property
    def failures(self) -> int:
        return self.remote_failures + self.local_failures


def _run_gh(cwd: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["gh", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )


def _parse_owner_from_repo_ref(raw: str) -> str | None:
    parts = [part.strip() for part in raw.strip().split("/") if part.strip()]
    if len(parts) == 2:
        return parts[0]
    if len(parts) == 3:
        return parts[1]
    return None


def _resolve_owner(owner: str | None) -> str:
    if owner and owner.strip():
        return owner.strip()

    for env_key in ("GH_REPO", "GITHUB_REPOSITORY"):
        env_value = os.environ.get(env_key)
        if env_value:
            resolved = _parse_owner_from_repo_ref(env_value)
            if resolved:
                return resolved

    org = (os.environ.get("GITHUB_ORG") or "").strip()
    if org:
        return org

    raise RuntimeError(
        "Could not resolve owner. Pass --owner or set GITHUB_ORG (or GH_REPO) in .env/environment."
    )


def _ensure_gh_ready(cwd: Path) -> None:
    if shutil.which("gh") is None:
        raise RuntimeError("GitHub CLI (gh) is required.")

    cp = _run_gh(cwd, ["auth", "status"])
    if cp.returncode != 0:
        raise RuntimeError(
            "Authenticate first: gh auth login (or set GH_TOKEN/GITHUB_TOKEN in .env/environment)."
        )


def _list_repo_names(*, cwd: Path, owner: str) -> list[str]:
    cp = _run_gh(cwd, ["repo", "list", owner, "--limit", "1000", "--json", "name"])
    if cp.returncode != 0:
        raise RuntimeError(
            cp.stderr.strip() or f"Failed listing repositories for owner '{owner}'."
        )

    try:
        payload = json.loads(cp.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise RuntimeError("Unexpected response while listing repositories.") from exc

    if not isinstance(payload, list):
        raise RuntimeError("Unexpected repository list response.")

    names: set[str] = set()
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if isinstance(name, str) and name.strip():
            names.add(name.strip())
    return sorted(names)


def _select_matches(
    *, repo_names: Sequence[str], prefix: str, exact_names: Sequence[str]
) -> list[str]:
    exact_set = {name.strip() for name in exact_names if name and name.strip()}
    if exact_set:
        return [name for name in repo_names if name in exact_set]

    clean_prefix = prefix.strip()
    if not clean_prefix:
        raise RuntimeError("--prefix must not be empty when --exact is not provided.")
    return [
        name
        for name in repo_names
        if name == clean_prefix or name.startswith(f"{clean_prefix}-")
    ]


def _default_local_roots(cwd: Path) -> tuple[Path, ...]:
    return (Path("/tmp"), cwd / "out")


def _resolve_local_roots(*, cwd: Path, local_roots: Sequence[str]) -> list[Path]:
    if local_roots:
        roots = [Path(item).expanduser() for item in local_roots]
    else:
        roots = list(_default_local_roots(cwd))

    resolved: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        candidate = root if root.is_absolute() else (cwd / root)
        try:
            normalized = candidate.resolve()
        except FileNotFoundError:
            normalized = candidate.absolute()
        key = normalized.as_posix()
        if key in seen:
            continue
        seen.add(key)
        if normalized == normalized.parent:
            raise RuntimeError(
                f"Refusing local cleanup root '{normalized}'. Use a specific path such as /tmp."
            )
        resolved.append(normalized)
    return resolved


def _list_local_matches(
    *,
    cwd: Path,
    prefix: str,
    exact_names: Sequence[str],
    local_roots: Sequence[str],
) -> list[Path]:
    roots = _resolve_local_roots(cwd=cwd, local_roots=local_roots)
    matches: list[Path] = []
    for root in roots:
        if not root.exists() or not root.is_dir():
            continue
        children = [child for child in root.iterdir() if child.is_dir()]
        names = sorted({child.name for child in children})
        selected_names = _select_matches(
            repo_names=names, prefix=prefix, exact_names=exact_names
        )
        for name in selected_names:
            for child in children:
                if child.name == name:
                    matches.append(child)
    return sorted(matches, key=lambda p: p.as_posix())


def delete_repositories(
    *,
    owner: str | None,
    prefix: str,
    exact_names: Sequence[str],
    include_local: bool,
    delete_local_only: bool,
    local_roots: Sequence[str],
    apply: bool,
    assume_yes: bool,
    prompt: Callable[[str], str],
    is_tty: bool,
    cwd: Path,
    out: Callable[[str], None],
    err: Callable[[str], None],
) -> DeleteSummary:
    remote_enabled = not delete_local_only
    local_enabled = include_local or delete_local_only

    if delete_local_only:
        local_enabled = True

    resolved_owner: str | None = None
    remote_matches: list[str] = []
    if remote_enabled:
        resolved_owner = _resolve_owner(owner)
        _ensure_gh_ready(cwd)
        repo_names = _list_repo_names(cwd=cwd, owner=resolved_owner)
        remote_matches = _select_matches(
            repo_names=repo_names, prefix=prefix, exact_names=exact_names
        )

    local_matches: list[Path] = []
    if local_enabled:
        local_matches = _list_local_matches(
            cwd=cwd,
            prefix=prefix,
            exact_names=exact_names,
            local_roots=local_roots,
        )

    if not remote_matches and not local_matches:
        out("No matching delete targets found.")
        return DeleteSummary(
            owner=resolved_owner,
            remote_matched=0,
            remote_deleted=0,
            remote_skipped=0,
            remote_failures=0,
            local_matched=0,
            local_deleted=0,
            local_skipped=0,
            local_failures=0,
        )

    if remote_matches and resolved_owner is not None:
        out(f"Matched remote repositories (owner: {resolved_owner}):")
        for name in remote_matches:
            out(f"  - {resolved_owner}/{name}")
    if local_matches:
        out("Matched local directories:")
        for path in local_matches:
            out(f"  - {path}")

    if not apply:
        out("Dry-run only. Re-run with --apply to delete these targets.")
        return DeleteSummary(
            owner=resolved_owner,
            remote_matched=len(remote_matches),
            remote_deleted=0,
            remote_skipped=len(remote_matches),
            remote_failures=0,
            local_matched=len(local_matches),
            local_deleted=0,
            local_skipped=len(local_matches),
            local_failures=0,
        )

    if not assume_yes:
        if not is_tty:
            out("Non-interactive shell detected and --yes not set; skipping deletes.")
            return DeleteSummary(
                owner=resolved_owner,
                remote_matched=len(remote_matches),
                remote_deleted=0,
                remote_skipped=len(remote_matches),
                remote_failures=0,
                local_matched=len(local_matches),
                local_deleted=0,
                local_skipped=len(local_matches),
                local_failures=0,
            )
        prompt_parts: list[str] = []
        if remote_matches:
            prompt_parts.append(f"{len(remote_matches)} remote repos")
        if local_matches:
            prompt_parts.append(f"{len(local_matches)} local dirs")
        scope = " and ".join(prompt_parts)
        reply = prompt(f"Delete {scope}? [y/N] ").strip().lower()
        if reply not in {"y", "yes"}:
            out("Aborted.")
            return DeleteSummary(
                owner=resolved_owner,
                remote_matched=len(remote_matches),
                remote_deleted=0,
                remote_skipped=len(remote_matches),
                remote_failures=0,
                local_matched=len(local_matches),
                local_deleted=0,
                local_skipped=len(local_matches),
                local_failures=0,
            )

    remote_deleted = 0
    remote_failures = 0
    if remote_enabled and resolved_owner is not None:
        for name in remote_matches:
            full_repo = f"{resolved_owner}/{name}"
            out(f"DELETE  {full_repo}")
            cp = _run_gh(cwd, ["repo", "delete", full_repo, "--yes"])
            if cp.returncode == 0:
                remote_deleted += 1
            else:
                remote_failures += 1
                detail = cp.stderr.strip() or cp.stdout.strip() or "unknown error"
                err(f"FAILED  {full_repo}: {detail}")

    local_deleted = 0
    local_failures = 0
    for path in local_matches:
        try:
            resolved_path = path.resolve()
        except FileNotFoundError:
            resolved_path = path.absolute()

        try:
            cwd_resolved = cwd.resolve()
        except FileNotFoundError:
            cwd_resolved = cwd.absolute()

        if resolved_path == cwd_resolved:
            local_failures += 1
            err(
                f"FAILED  {resolved_path}: refusing to delete current working directory"
            )
            continue

        out(f"DELETE  {resolved_path}")
        try:
            shutil.rmtree(resolved_path)
            local_deleted += 1
        except OSError as exc:
            local_failures += 1
            err(f"FAILED  {resolved_path}: {exc}")

    remote_skipped = max(len(remote_matches) - remote_deleted - remote_failures, 0)
    local_skipped = max(len(local_matches) - local_deleted - local_failures, 0)
    return DeleteSummary(
        owner=resolved_owner,
        remote_matched=len(remote_matches),
        remote_deleted=remote_deleted,
        remote_skipped=remote_skipped,
        remote_failures=remote_failures,
        local_matched=len(local_matches),
        local_deleted=local_deleted,
        local_skipped=local_skipped,
        local_failures=local_failures,
    )
