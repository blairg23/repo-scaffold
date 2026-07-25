"""Per-repo Docker container management.

Naming convention:
  container: {repo-name}-{branch-slug}
  base image: {repo-name}-base:latest

Each repo gets one base image (built once, rebuilt when deps change).
Each active branch gets its own container started from that image.
"""

from __future__ import annotations

import os
import platform
import re
import subprocess
import time
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

# Windows and macOS only: Docker Desktop on both platforms is a background GUI
# app, not a system service, so a stopped daemon needs the app launched before
# its API socket comes up.
#
# Linux is intentionally excluded. Docker there normally runs as a systemd
# service, and starting a stopped one means `systemctl start docker` (or
# `service docker start`) -- a real privilege-escalation step, not just
# launching a user-space app. Most Linux dev/CI environments already have
# dockerd running as a service. Auto-`sudo`-ing on a user's behalf is a
# materially different risk than opening an app, so this stays out of scope
# here (see #270).
_DOCKER_DESKTOP_WINDOWS_CANDIDATE_PATHS = [
    r"C:\Program Files\Docker\Docker\Docker Desktop.exe",
    r"C:\ProgramData\DockerDesktop\Docker Desktop.exe",
]
_DOCKER_DESKTOP_MACOS_APP_PATHS = [
    "/Applications/Docker.app",
]
_DOCKER_DESKTOP_START_TIMEOUT_SECONDS = 90
_DOCKER_DESKTOP_POLL_INTERVAL_SECONDS = 5


def _is_docker_unreachable(exc: Exception) -> bool:
    text = str(exc).lower()
    # "pipe" / "connection refused" cover Windows (named pipe) and an actively
    # rejected socket connection. On macOS a stopped Docker Desktop typically
    # presents as the socket *file* being absent instead -- a FileNotFoundError
    # for the default docker.sock path/symlink, not a connection-level error --
    # so that needs its own check or auto-start never triggers there.
    #
    # The exact "cannot find the file specified" phrasing (not a bare
    # "createfile" match) is required: pywin32's win32file.CreateFile() also
    # raises for unrelated failures like access-denied pipe permissions, and a
    # broad match would misdiagnose those as a stopped daemon, burning the
    # full auto-start timeout on a problem restarting Docker Desktop can't fix.
    # e.g.: "(2, 'CreateFile', 'The system cannot find the file specified.')"
    return (
        "pipe" in text
        or "connectionrefused" in text
        or "connection refused" in text
        or "no such file or directory" in text
        or "docker.sock" in text
        or "the system cannot find the file specified" in text
    )


def _find_docker_desktop_windows_exe() -> str | None:
    for candidate in _DOCKER_DESKTOP_WINDOWS_CANDIDATE_PATHS:
        if Path(candidate).exists():
            return candidate
    return None


def _find_docker_desktop_macos_app() -> str | None:
    for candidate in _DOCKER_DESKTOP_MACOS_APP_PATHS:
        if Path(candidate).exists():
            return candidate
    return None


def _wait_for_docker_ready(timeout: float) -> bool:
    import docker  # type: ignore[import]

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            docker.from_env().ping()
            return True
        except Exception:
            time.sleep(_DOCKER_DESKTOP_POLL_INTERVAL_SECONDS)
    return False


def _docker_desktop_launch_args() -> list[str] | None:
    """Return the subprocess args to launch Docker Desktop, or None if this
    platform isn't supported or the app isn't installed at a known location."""
    system = platform.system()

    if system == "Windows":
        exe = _find_docker_desktop_windows_exe()
        return None if exe is None else [exe]

    if system == "Darwin":
        app = _find_docker_desktop_macos_app()
        return None if app is None else ["open", "-a", "Docker"]

    return None


def _try_auto_start_docker_desktop() -> bool:
    """Best-effort: launch Docker Desktop on Windows/macOS and wait for the
    daemon. Returns True if the daemon became reachable, False otherwise.
    Never raises -- callers fall back to the original connection error on
    failure. No-op on Linux and any platform without a known install path
    (see the module-level comment above for why Linux is excluded)."""
    launch_args = _docker_desktop_launch_args()
    if launch_args is None:
        return False

    try:
        subprocess.Popen(
            launch_args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except Exception:
        return False

    return _wait_for_docker_ready(_DOCKER_DESKTOP_START_TIMEOUT_SECONDS)


def _client():  # type: ignore[return]
    try:
        import docker  # type: ignore[import]
    except ImportError:
        raise RuntimeError("The 'docker' package is required: poetry add docker")

    try:
        return docker.from_env()
    except Exception as exc:
        if not _is_docker_unreachable(exc):
            raise RuntimeError(f"Cannot connect to Docker daemon: {exc}")

        if _try_auto_start_docker_desktop():
            try:
                return docker.from_env()
            except Exception as retry_exc:
                exc = retry_exc

        raise RuntimeError(
            "Cannot connect to Docker daemon -- is Docker running? "
            f"Auto-start was attempted and did not bring it up in time. ({exc})"
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
REPO_DIR="/$(echo "$REPO" | cut -d'/' -f2)"
echo "Cloning $REPO branch $BRANCH ..."
git clone --depth=1 --branch "$BRANCH" \
    "https://x-access-token:${GH_TOKEN}@github.com/${REPO}.git" "$REPO_DIR" 2>/dev/null \
    || git clone --depth=1 \
        "https://x-access-token:${GH_TOKEN}@github.com/${REPO}.git" "$REPO_DIR"
cd "$REPO_DIR"
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


def docker_shell(
    repo: str,
    branch: str,
    token: str,
    dockerfile_dir: Path,
    rebuild: bool = False,
    env_path: Path | None = None,
) -> None:
    """Build image if needed, restart container, and exec into bash.

    Replaces the current process with `docker exec -it <name> bash` -- never
    returns on success. Raises RuntimeError on any failure before exec.
    """
    client = _client()
    name = container_name(repo, branch)
    tag = image_name(repo)

    needs_build = rebuild
    if not needs_build:
        try:
            client.images.get(tag)
        except Exception:
            needs_build = True

    if needs_build:
        result = docker_build_base(repo, dockerfile_dir)
        if result.returncode != 0:
            raise RuntimeError(f"Base image build failed:\n{result.stderr}")
        print(result.stdout)

    try:
        existing = client.containers.get(name)
        existing.stop(timeout=10)
        existing.remove()
    except Exception:
        pass

    result = docker_spin_up(repo, branch, token, env_path=env_path)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to start container:\n{result.stderr}")
    print(result.stdout)

    repo_slug = _slug(repo.split("/", 1)[-1])
    os.execvp("docker", ["docker", "exec", "-it", "-w", f"/{repo_slug}", name, "bash"])


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
