"""Lightweight local registry of repos repo-scaffold knows about.

Stores only what can't be re-derived live from git/GitHub: a local path and
free-text notes per repo. Everything else (last commit, owner, status) is one
git/GitHub call away and isn't worth caching here.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

_REGISTRY_ENV_VAR = "REPO_SCAFFOLD_REGISTRY_PATH"


@dataclass(frozen=True)
class RegistryEntry:
    repo: str
    local_path: str
    notes: str = ""


def registry_path() -> Path:
    override = os.environ.get(_REGISTRY_ENV_VAR)
    if override:
        return Path(override)
    return Path.home() / ".repo-scaffold" / "registry.json"


def load_registry(path: Path | None = None) -> dict[str, RegistryEntry]:
    target = path or registry_path()
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
    entry = RegistryEntry(repo=repo, local_path=local_path, notes=notes)
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
