"""GitHub repo discovery, Device Flow auth, and .env token management."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

_GITHUB_API = "https://api.github.com"
_DEVICE_AUTH_URL = "https://github.com/login/device/code"
_DEVICE_TOKEN_URL = "https://github.com/login/oauth/access_token"
_DEVICE_SCOPE = "repo"
_DEVICE_TIMEOUT = 300  # 5 minutes


def _post_form(url: str, data: dict[str, str]) -> dict[str, object]:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return dict(json.loads(resp.read().decode()))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode() if exc.fp else ""
        raise RuntimeError(raw or str(exc)) from exc


def _parse_next(link: str) -> str:
    for part in link.split(","):
        part = part.strip()
        if 'rel="next"' in part:
            return part.split(";")[0].strip().strip("<>")
    return ""


def _get_paginated(token: str, url: str) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    while url:
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req) as resp:
                results.extend(json.loads(resp.read().decode()))
                url = _parse_next(resp.headers.get("Link", ""))
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode() if exc.fp else ""
            raise RuntimeError(raw or str(exc)) from exc
    return results


def device_flow_auth(client_id: str) -> str:
    """Run GitHub Device Flow and return the access token."""
    resp = _post_form(
        _DEVICE_AUTH_URL, {"client_id": client_id, "scope": _DEVICE_SCOPE}
    )
    device_code = str(resp["device_code"])
    user_code = str(resp["user_code"])
    verification_uri = str(
        resp.get("verification_uri", "https://github.com/login/device")
    )
    _raw_interval = resp.get("interval", 5)
    interval = int(_raw_interval) if isinstance(_raw_interval, int) else 5

    print(f"\nOpen: {verification_uri}")
    print(f"Enter code: {user_code}\n")
    print("Waiting for authorization...", flush=True)

    deadline = time.monotonic() + _DEVICE_TIMEOUT
    while time.monotonic() < deadline:
        time.sleep(interval)
        token_resp = _post_form(
            _DEVICE_TOKEN_URL,
            {
                "client_id": client_id,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
        )
        if "access_token" in token_resp:
            return str(token_resp["access_token"])
        error = str(token_resp.get("error", ""))
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval += 5
            continue
        desc = str(token_resp.get("error_description", ""))
        raise RuntimeError(f"Device Flow failed: {error} -- {desc}")

    raise RuntimeError("Device Flow timed out. Run repo discover again to retry.")


def prompt_for_token(client_id: str | None) -> str:
    """Prompt interactively for a GitHub token; use Device Flow if client_id is available."""
    print("No GitHub token found.")
    if client_id:
        choice = input(
            "Paste a GitHub token, or press Enter to authenticate via browser: "
        ).strip()
        if choice:
            return choice
        return device_flow_auth(client_id)
    token = input(
        "Paste your GitHub token"
        " (set GITHUB_CLIENT_ID in .env to enable browser-based auth): "
    ).strip()
    if not token:
        raise RuntimeError("No token provided. Set GH_TOKEN in .env and retry.")
    return token


def upsert_env_var(key: str, value: str, path: Path) -> None:
    """Write or update KEY=value in the .env file at *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    prefix = f"{key}="
    updated = [line for line in existing if not line.startswith(prefix)]
    updated.append(f"{key}={value}")
    path.write_text("\n".join(updated) + "\n", encoding="utf-8")


def discover_repos(token: str, org: str | None = None) -> list[str]:
    """Return sorted list of 'owner/repo' strings visible to *token*."""
    if org:
        url = f"{_GITHUB_API}/orgs/{org}/repos?per_page=100&type=all&sort=full_name"
    else:
        url = (
            f"{_GITHUB_API}/user/repos"
            "?per_page=100&affiliation=owner,collaborator,organization_member&sort=full_name"
        )
    items = _get_paginated(token, url)
    return sorted(str(item["full_name"]) for item in items if item.get("full_name"))
