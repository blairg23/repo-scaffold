"""Lightweight local registry of repos repo-scaffold knows about.

Stores only what can't be re-derived live from git/GitHub: a local path and
free-text notes per repo. Everything else (last commit, owner, status) is one
git/GitHub call away and isn't worth caching here.

Lives at {repo_root}/.repo-scaffold/registry.json (gitignored -- local state
per working copy) so it's visible to any container or agent that has this
repo checked out, unlike the old ~/.repo-scaffold/registry.json home-dir
location a Docker container or fresh clone would never see.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

_REGISTRY_ENV_VAR = "REPO_SCAFFOLD_REGISTRY_PATH"
_LEGACY_REGISTRY_PATH = Path.home() / ".repo-scaffold" / "registry.json"


@dataclass(frozen=True)
class RegistryEntry:
    repo: str
    local_path: str
    notes: str = ""


def _repo_root() -> Path:
    """Directory containing pyproject.toml, walking up from this file.

    Falls back to the current working directory if none is found (e.g. this
    module was somehow imported outside a repo-scaffold checkout).
    """
    current = Path(__file__).resolve().parent
    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate
    return Path.cwd()


def registry_path() -> Path:
    override = os.environ.get(_REGISTRY_ENV_VAR)
    if override:
        return Path(override)
    return _repo_root() / ".repo-scaffold" / "registry.json"


def _migrate_legacy_registry(target: Path) -> Path:
    """One-time copy of ~/.repo-scaffold/registry.json to the new location.

    Returns the path to actually read: `target` normally, or the legacy path
    itself if the copy failed (e.g. a read-only checkout) so existing state
    is still usable even though it can't be persisted at the new location.
    """
    if target.exists() or not _LEGACY_REGISTRY_PATH.exists():
        return target
    if _LEGACY_REGISTRY_PATH.resolve() == target.resolve():
        return target
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            _LEGACY_REGISTRY_PATH.read_text(encoding="utf-8"), encoding="utf-8"
        )
    except OSError as exc:
        print(
            f"Warning: could not migrate registry to {target} ({exc}); "
            f"reading from legacy location {_LEGACY_REGISTRY_PATH} instead.",
            file=sys.stderr,
        )
        return _LEGACY_REGISTRY_PATH
    print(
        f"Migrated registry from {_LEGACY_REGISTRY_PATH} to {target} "
        "(the old location is no longer used and can be deleted).",
        file=sys.stderr,
    )
    return target


def load_registry(path: Path | None = None) -> dict[str, RegistryEntry]:
    target = path or registry_path()
    target = _migrate_legacy_registry(target)
    if not target.exists():
        return {}
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    entries: dict[str, RegistryEntry] = {}
    for repo, data in raw.items():
        if not isinstance(data, dict):
            continue
        entries[repo] = RegistryEntry(
            repo=repo,
            local_path=str(data.get("local_path", "")),
            notes=str(data.get("notes", "")),
        )
    return entries


def save_registry(entries: dict[str, RegistryEntry], path: Path | None = None) -> None:
    target = path or registry_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        repo: {"local_path": entry.local_path, "notes": entry.notes}
        for repo, entry in entries.items()
    }
    target.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def register_repo(
    repo: str,
    local_path: str,
    notes: str = "",
    path: Path | None = None,
) -> RegistryEntry:
    entries = load_registry(path)
    resolved_path = str(Path(local_path).resolve())
    entry = RegistryEntry(repo=repo, local_path=resolved_path, notes=notes)
    entries[repo] = entry
    save_registry(entries, path)
    return entry


def forget_repo(repo: str, path: Path | None = None) -> bool:
    entries = load_registry(path)
    if repo not in entries:
        return False
    del entries[repo]
    save_registry(entries, path)
    return True


def list_registry(path: Path | None = None) -> list[RegistryEntry]:
    return sorted(load_registry(path).values(), key=lambda e: e.repo)
