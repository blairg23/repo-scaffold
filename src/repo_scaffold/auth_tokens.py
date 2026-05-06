from __future__ import annotations

from collections.abc import Mapping

_TOKEN_PLACEHOLDERS = {
    "ghp_replace_with_real_token",
    "<classic-PAT>",
    "<classic-PAT-placeholder>",
}

_TOKEN_KEYS = (
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "github_token",
    "GH_PROJECT_TOKEN",
    "GITHUB_PROJECT_TOKEN",
    "github_project_token",
)


def is_placeholder_token(value: str | None) -> bool:
    if value is None:
        return False
    normalized = value.strip()
    return normalized in _TOKEN_PLACEHOLDERS


def resolve_gh_token(values: Mapping[str, str]) -> str | None:
    for key in _TOKEN_KEYS:
        value = values.get(key)
        if value is None:
            continue
        normalized = value.strip()
        if normalized and normalized not in _TOKEN_PLACEHOLDERS:
            return normalized
    return None
