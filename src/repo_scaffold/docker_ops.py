"""Per-repo Docker container management.

Naming convention:
  container: {repo-name}-{branch-slug}
  base image: {repo-name}-base:latest

Each repo gets one base image (built once, rebuilt when deps change).
Each active branch gets its own container started from that image.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Naming helpers
# ---------------------------------------------------------------------------


def _slug(value: str) -> str:
    """Lowercase, replace non-alphanumeric runs with hyphens, strip edges."""
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def container_name(repo: str, branch: str) -> str:
    """Return the canonical container name for a repo/branch pair.

    repo:   'blairg23/repo-scaffold'  -> 'repo-scaffold'
    branch: 'feat/238-docker-model'   -> 'feat-238-docker-model'
    result: 'repo-scaffold-feat-238-docker-model'
    """
    repo_slug = _slug(repo.split("/", 1)[-1])
    branch_slug = _slug(branch)
    return f"{repo_slug}-{branch_slug}"


def image_name(repo: str) -> str:
    """Return the base image tag for a repo: '{repo-name}-base:latest'."""
    repo_slug = _slug(repo.split("/", 1)[-1])
    return f"{repo_slug}-base:latest"


# ---------------------------------------------------------------------------
# Docker client (lazy import so missing SDK gives a clear error at call time)
# ---------------------------------------------------------------------------


def _client():  # type: ignore[return]
    try:
        import docker  # type: ignore[import]

        return docker.from_env()
    except ImportError:
        raise RuntimeError("The 'docker' package is required: poetry add docker")
    except Exception as exc:
        raise RuntimeError(
            f"Cannot connect to Docker daemon -- is Docker running? ({exc})"
        )


# ---------------------------------------------------------------------------
# Result helpers (match the subprocess.CompletedProcess pattern used elsewhere)
# ---------------------------------------------------------------------------


def _ok(stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


def _err(msg: str, code: int = 1) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=code, stdout="", stderr=msg)


# ---------------------------------------------------------------------------
# Startup script baked into containers
# ---------------------------------------------------------------------------

_STARTUP_SCRIPT = """\
#!/bin/bash
set -e
echo "Cloning $REPO branch $BRANCH ..."
git clone --depth=1 --branch "$BRANCH" \
    "https://x-access-token:${GH_TOKEN}@github.com/${REPO}.git" /workspace 2>/dev/null \
    || git clone --depth=1 \
        "https://x-access-token:${GH_TOKEN}@github.com/${REPO}.git" /workspace
cd /workspace
git checkout "$BRANCH" 2>/dev/null || git checkout -b "$BRANCH" "origin/$BRANCH"
if [ -f pyproject.toml ]; then
    poetry install --quiet
fi
echo "Ready. Container: $CONTAINER_NAME"
exec sleep infinity
"""

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass
class ContainerInfo:
    name: str
    repo: str
    branch: str
    status: str
    image: str


def docker_build_base(
    repo: str,
    dockerfile_dir: Path,
) -> subprocess.CompletedProcess[str]:
    """Build (or rebuild) the base image for a repo.

    dockerfile_dir should contain a Dockerfile. If no Dockerfile is present,
    a minimal Python + poetry image is used.
    """
    tag = image_name(repo)
    client = _client()

    dockerfile = dockerfile_dir / "Dockerfile"
    if not dockerfile.exists():
        return _err(
            f"No Dockerfile found at {dockerfile}. "
            "Add one to the repo root or run 'docker build-base' from the repo directory."
        )

    try:
        _image, logs = client.images.build(
            path=str(dockerfile_dir),
            tag=tag,
            rm=True,
        )
        log_lines = [
            lg.get("stream", "").rstrip()
            for lg in logs
            if isinstance(lg, dict) and lg.get("stream", "").strip()
        ]
        return _ok(f"Built {tag}\n" + "\n".join(log_lines))
    except Exception as exc:
        return _err(f"docker build failed: {exc}")


def docker_spin_up(
    repo: str,
    branch: str,
    token: str,
    env_path: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Start a container for repo/branch.

    Pulls the base image if available; errors clearly if not built yet.
    The container clones the repo and checks out the branch on startup.
    """
    client = _client()
    name = container_name(repo, branch)
    tag = image_name(repo)

    # Verify base image exists
    try:
        client.images.get(tag)
    except Exception:
        return _err(
            f"Base image '{tag}' not found. "
            f"Run: repo-scaffold docker build-base --repo {repo}"
        )

    # Refuse to start a duplicate
    try:
        existing = client.containers.get(name)
        return _err(
            f"Container '{name}' already exists (status: {existing.status}). "
            "Run spin-down first or use a different branch name."
        )
    except Exception:
        pass  # container does not exist -- good

    env: dict[str, str] = {
        "GH_TOKEN": token,
        "REPO": repo,
        "BRANCH": branch,
        "CONTAINER_NAME": name,
    }

    volumes: dict[str, dict[str, str]] = {}
    if env_path and env_path.exists():
        volumes[str(env_path.resolve())] = {
            "bind": "/repo-scaffold.env",
            "mode": "ro",
        }

    try:
        client.containers.run(
            image=tag,
            name=name,
            detach=True,
            environment=env,
            volumes=volumes or None,
            command=["bash", "-c", _STARTUP_SCRIPT],
        )
        return _ok(f"Started container: {name}")
    except Exception as exc:
        return _err(f"docker run failed: {exc}")


def docker_spin_down(
    repo: str,
    branch: str,
) -> subprocess.CompletedProcess[str]:
    """Stop and remove the container for repo/branch."""
    client = _client()
    name = container_name(repo, branch)

    try:
        container = client.containers.get(name)
    except Exception:
        return _err(f"Container '{name}' not found.")

    try:
        container.stop(timeout=10)
        container.remove()
        return _ok(f"Removed container: {name}")
    except Exception as exc:
        return _err(f"Failed to stop/remove '{name}': {exc}")


def docker_list(
    repo: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """List running agent containers, optionally filtered by repo prefix."""

    client = _client()
    prefix = _slug(repo.split("/", 1)[-1]) + "-" if repo else ""

    try:
        containers = client.containers.list(all=True)
    except Exception as exc:
        return _err(f"Failed to list containers: {exc}")

    results = []
    for c in containers:
        name = c.name or ""
        if prefix and not name.startswith(prefix):
            continue
        results.append(
            {
                "name": name,
                "status": c.status,
                "image": c.image.tags[0] if c.image and c.image.tags else "",
            }
        )

    if not results:
        label = f"for repo '{repo}'" if repo else ""
        return _ok(f"No agent containers running {label}".strip())

    rows = "\n".join(
        f"  {r['status']:12s}  {r['name']:50s}  {r['image']}" for r in results
    )
    return _ok(f"{'STATUS':12s}  {'CONTAINER':50s}  IMAGE\n{rows}")
