from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_PROJECT_METADATA_REL_PATH = ".repo-scaffold/project.json"


def project_metadata_path(
    repo_dir: Path, rel_path: str = DEFAULT_PROJECT_METADATA_REL_PATH
) -> Path:
    metadata_path = Path(rel_path)
    return metadata_path if metadata_path.is_absolute() else (repo_dir / metadata_path)


def write_project_metadata(
    repo_dir: Path,
    *,
    owner: str,
    number: int,
    title: str,
    source: str,
    repo: str | None = None,
    closed: bool | None = None,
    visibility: str | None = None,
    description: str | None = None,
    readme: str | None = None,
    url: str | None = None,
) -> Path:
    metadata_file = project_metadata_path(repo_dir)
    metadata_file.parent.mkdir(parents=True, exist_ok=True)

    payload: dict[str, object] = {
        "version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "owner": owner,
        "number": number,
        "title": title,
    }
    if repo:
        payload["repo"] = repo
    if closed is not None:
        payload["closed"] = closed
    if visibility:
        payload["visibility"] = visibility
    if description:
        payload["description"] = description
    if readme:
        payload["readme"] = readme
    if url:
        payload["url"] = url

    metadata_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return metadata_file
