"""Tests for docker_ops -- all Docker SDK calls are mocked."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from repo_scaffold.docker_ops import (
    WORKSPACE_IMAGE_TAG,
    _STARTUP_SCRIPT,
    _err,
    _ok,
    _slug,
    container_name,
    default_workspace_dockerfile_dir,
    docker_build_base,
    docker_list,
    docker_shell,
    docker_spin_down,
    docker_spin_up,
)

# ---------------------------------------------------------------------------
# Naming helpers
# ---------------------------------------------------------------------------


def test_slug_lowercases() -> None:
    assert _slug("FeatBranch") == "featbranch"


def test_slug_replaces_non_alphanum_with_hyphens() -> None:
    assert _slug("feat/238-my-feature") == "feat-238-my-feature"


def test_slug_strips_edge_hyphens() -> None:
    assert _slug("/leading-and-trailing/") == "leading-and-trailing"


def test_container_name_standard() -> None:
    assert (
        container_name("blairg23/repo-scaffold", "feat/238-docker-model")
        == "repo-scaffold-feat-238-docker-model"
    )


def test_container_name_simple_branch() -> None:
    assert container_name("owner/myrepo", "main") == "myrepo-main"


def test_workspace_image_tag_is_fixed_not_per_repo() -> None:
    assert WORKSPACE_IMAGE_TAG == "repo-scaffold-workspace:latest"


def test_default_workspace_dockerfile_dir_is_bundled_asset() -> None:
    assert default_workspace_dockerfile_dir().name == "docker_assets"


def test_startup_script_detects_all_language_manifests() -> None:
    """Post-clone dependency install must cover every ALLOWED_LANGUAGES case,
    detected from the freshly-cloned repo content inside the container -- not
    from a host-side checkout or a per-repo Dockerfile."""
    assert "pyproject.toml" in _STARTUP_SCRIPT
    assert "poetry install" in _STARTUP_SCRIPT
    assert "go.mod" in _STARTUP_SCRIPT
    assert "go mod download" in _STARTUP_SCRIPT
    assert "web/package.json" in _STARTUP_SCRIPT
    assert "npm install" in _STARTUP_SCRIPT
    assert ".repo-scaffold/setup.sh" in _STARTUP_SCRIPT


def test_setup_hook_runs_before_dependency_installs() -> None:
    """.repo-scaffold/setup.sh is the escape hatch for OS packages a repo's own
    poetry/go/npm install needs. It must run first -- under `set -e`, an install
    failing before the hook runs would kill the container before the hook that's
    supposed to fix exactly that ever executes."""
    setup_hook_index = _STARTUP_SCRIPT.index(".repo-scaffold/setup.sh")
    assert setup_hook_index < _STARTUP_SCRIPT.index("poetry install")
    assert setup_hook_index < _STARTUP_SCRIPT.index("go mod download")
    assert setup_hook_index < _STARTUP_SCRIPT.index("npm install")


# ---------------------------------------------------------------------------
# _client helper (lazy import)
# ---------------------------------------------------------------------------


def test_client_raises_on_missing_docker_package() -> None:
    from repo_scaffold.docker_ops import _client

    with patch("builtins.__import__", side_effect=ImportError("no docker")):
        with pytest.raises(RuntimeError, match="'docker' package is required"):
            _client()


# ---------------------------------------------------------------------------
# docker_build_base
# ---------------------------------------------------------------------------


def _make_client() -> MagicMock:
    """Return a minimal mock docker client."""
    client = MagicMock()
    return client


def test_build_base_missing_dockerfile(tmp_path: Path) -> None:
    with patch("repo_scaffold.docker_ops._client", return_value=_make_client()):
        result = docker_build_base(tmp_path)
    assert result.returncode == 1
    assert "No Dockerfile" in result.stderr


def test_build_base_success(tmp_path: Path) -> None:
    (tmp_path / "Dockerfile").write_text("FROM python:3.12")

    fake_image = MagicMock()
    fake_logs = [
        {"stream": "Step 1/1 : FROM python:3.12\n"},
        {"stream": " ---> done\n"},
    ]

    client = _make_client()
    client.images.build.return_value = (fake_image, iter(fake_logs))

    with patch("repo_scaffold.docker_ops._client", return_value=client):
        result = docker_build_base(tmp_path)

    assert result.returncode == 0
    assert WORKSPACE_IMAGE_TAG in result.stdout
    client.images.build.assert_called_once_with(
        path=str(tmp_path), tag=WORKSPACE_IMAGE_TAG, rm=True
    )


def test_build_base_defaults_to_bundled_dockerfile() -> None:
    """No dockerfile_dir given -> resolves to repo-scaffold's own bundled asset,
    never a target repo's local checkout."""
    fake_image = MagicMock()
    client = _make_client()
    client.images.build.return_value = (fake_image, iter([]))

    with patch("repo_scaffold.docker_ops._client", return_value=client):
        docker_build_base()

    call_kwargs = client.images.build.call_args.kwargs
    assert call_kwargs["path"] == str(default_workspace_dockerfile_dir())


def test_build_base_sdk_error(tmp_path: Path) -> None:
    (tmp_path / "Dockerfile").write_text("FROM python:3.12")

    client = _make_client()
    client.images.build.side_effect = RuntimeError("daemon down")

    with patch("repo_scaffold.docker_ops._client", return_value=client):
        result = docker_build_base(tmp_path)

    assert result.returncode == 1
    assert "docker build failed" in result.stderr


# ---------------------------------------------------------------------------
# docker_spin_up
# ---------------------------------------------------------------------------


def test_spin_up_missing_base_image() -> None:
    client = _make_client()
    client.images.get.side_effect = Exception("not found")

    with patch("repo_scaffold.docker_ops._client", return_value=client):
        result = docker_spin_up("owner/myrepo", "main", token="tok")

    assert result.returncode == 1
    assert "build-base" in result.stderr


def test_spin_up_duplicate_container() -> None:
    client = _make_client()
    client.images.get.return_value = MagicMock()
    existing = MagicMock(status="running")
    client.containers.get.return_value = existing

    with patch("repo_scaffold.docker_ops._client", return_value=client):
        result = docker_spin_up("owner/myrepo", "main", token="tok")

    assert result.returncode == 1
    assert "already exists" in result.stderr


def test_spin_up_success() -> None:
    client = _make_client()
    client.images.get.return_value = MagicMock()
    client.containers.get.side_effect = Exception("not found")
    client.containers.run.return_value = MagicMock()

    with patch("repo_scaffold.docker_ops._client", return_value=client):
        result = docker_spin_up("owner/myrepo", "feat/1-test", token="tok")

    assert result.returncode == 0
    assert "myrepo-feat-1-test" in result.stdout
    client.containers.run.assert_called_once()
    call_kwargs = client.containers.run.call_args.kwargs
    assert call_kwargs["name"] == "myrepo-feat-1-test"
    assert call_kwargs["detach"] is True
    assert call_kwargs["environment"]["GH_TOKEN"] == "tok"
    assert call_kwargs["environment"]["REPO"] == "owner/myrepo"


def test_spin_up_mounts_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("GH_TOKEN=x")

    client = _make_client()
    client.images.get.return_value = MagicMock()
    client.containers.get.side_effect = Exception("not found")
    client.containers.run.return_value = MagicMock()

    with patch("repo_scaffold.docker_ops._client", return_value=client):
        docker_spin_up("owner/myrepo", "main", token="tok", env_path=env_file)

    call_kwargs = client.containers.run.call_args.kwargs
    volumes = call_kwargs["volumes"]
    assert str(env_file.resolve()) in volumes


# ---------------------------------------------------------------------------
# docker_spin_down
# ---------------------------------------------------------------------------


def test_spin_down_not_found() -> None:
    client = _make_client()
    client.containers.get.side_effect = Exception("not found")

    with patch("repo_scaffold.docker_ops._client", return_value=client):
        result = docker_spin_down("owner/myrepo", "main")

    assert result.returncode == 1
    assert "not found" in result.stderr


def test_spin_down_success() -> None:
    client = _make_client()
    container = MagicMock()
    client.containers.get.return_value = container

    with patch("repo_scaffold.docker_ops._client", return_value=client):
        result = docker_spin_down("owner/myrepo", "main")

    assert result.returncode == 0
    container.stop.assert_called_once_with(timeout=10)
    container.remove.assert_called_once()


# ---------------------------------------------------------------------------
# docker_list
# ---------------------------------------------------------------------------


def _fake_container(name: str, status: str, tags: list[str]) -> MagicMock:
    c = MagicMock()
    c.name = name
    c.status = status
    c.image.tags = tags
    return c


def test_list_no_containers() -> None:
    client = _make_client()
    client.containers.list.return_value = []

    with patch("repo_scaffold.docker_ops._client", return_value=client):
        result = docker_list()

    assert result.returncode == 0
    assert "No agent containers" in result.stdout


def test_list_all_containers() -> None:
    client = _make_client()
    client.containers.list.return_value = [
        _fake_container("repo-scaffold-main", "running", ["repo-scaffold-base:latest"]),
        _fake_container("other-main", "exited", ["other-base:latest"]),
    ]

    with patch("repo_scaffold.docker_ops._client", return_value=client):
        result = docker_list()

    assert result.returncode == 0
    assert "repo-scaffold-main" in result.stdout
    assert "other-main" in result.stdout


def test_list_filtered_by_repo() -> None:
    client = _make_client()
    client.containers.list.return_value = [
        _fake_container("repo-scaffold-main", "running", ["repo-scaffold-base:latest"]),
        _fake_container("other-main", "exited", ["other-base:latest"]),
    ]

    with patch("repo_scaffold.docker_ops._client", return_value=client):
        result = docker_list(repo="owner/repo-scaffold")

    assert result.returncode == 0
    assert "repo-scaffold-main" in result.stdout
    assert "other-main" not in result.stdout


def test_list_filtered_by_repo_no_matches() -> None:
    client = _make_client()
    client.containers.list.return_value = [
        _fake_container("other-main", "exited", ["other-base:latest"]),
    ]

    with patch("repo_scaffold.docker_ops._client", return_value=client):
        result = docker_list(repo="owner/repo-scaffold")

    assert result.returncode == 0
    assert "No agent containers" in result.stdout
    assert "repo-scaffold" in result.stdout


def test_list_container_no_image_tags() -> None:
    client = _make_client()
    c = MagicMock()
    c.name = "somerepo-main"
    c.status = "running"
    c.image.tags = []
    client.containers.list.return_value = [c]

    with patch("repo_scaffold.docker_ops._client", return_value=client):
        result = docker_list()

    assert result.returncode == 0
    assert "somerepo-main" in result.stdout


def test_list_sdk_error() -> None:
    client = _make_client()
    client.containers.list.side_effect = Exception("daemon error")

    with patch("repo_scaffold.docker_ops._client", return_value=client):
        result = docker_list()

    assert result.returncode == 1
    assert "Failed to list containers" in result.stderr


# ---------------------------------------------------------------------------
# _client -- daemon connection error path
# ---------------------------------------------------------------------------


def test_client_raises_on_daemon_error() -> None:
    from repo_scaffold.docker_ops import _client

    mock_docker = MagicMock()
    mock_docker.from_env.side_effect = Exception("daemon not running")
    with patch.dict("sys.modules", {"docker": mock_docker}):
        with pytest.raises(RuntimeError, match="Cannot connect to Docker daemon"):
            _client()


# ---------------------------------------------------------------------------
# docker_spin_up -- run failure and missing env file
# ---------------------------------------------------------------------------


def test_spin_up_run_failure() -> None:
    client = _make_client()
    client.images.get.return_value = MagicMock()
    client.containers.get.side_effect = Exception("not found")
    client.containers.run.side_effect = Exception("run failed")

    with patch("repo_scaffold.docker_ops._client", return_value=client):
        result = docker_spin_up("owner/myrepo", "main", token="tok")

    assert result.returncode == 1
    assert "docker run failed" in result.stderr


def test_spin_up_nonexistent_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / "missing.env"  # does not exist

    client = _make_client()
    client.images.get.return_value = MagicMock()
    client.containers.get.side_effect = Exception("not found")
    client.containers.run.return_value = MagicMock()

    with patch("repo_scaffold.docker_ops._client", return_value=client):
        result = docker_spin_up("owner/myrepo", "main", token="tok", env_path=env_file)

    assert result.returncode == 0
    call_kwargs = client.containers.run.call_args.kwargs
    assert call_kwargs["volumes"] is None


# ---------------------------------------------------------------------------
# docker_spin_down -- stop/remove failure
# ---------------------------------------------------------------------------


def test_spin_down_stop_failure() -> None:
    client = _make_client()
    container = MagicMock()
    container.stop.side_effect = Exception("stop failed")
    client.containers.get.return_value = container

    with patch("repo_scaffold.docker_ops._client", return_value=client):
        result = docker_spin_down("owner/myrepo", "main")

    assert result.returncode == 1
    assert "Failed to stop/remove" in result.stderr


# ---------------------------------------------------------------------------
# docker_shell
# ---------------------------------------------------------------------------


def test_shell_builds_image_when_missing_and_execs() -> None:
    client = _make_client()
    # First call (shell checks if image exists) raises; second call (spin_up verifies) succeeds.
    client.images.get.side_effect = [Exception("not found"), MagicMock()]
    client.containers.get.side_effect = Exception("not found")
    client.containers.run.return_value = MagicMock()

    exec_calls: list[tuple[str, list[str]]] = []

    def fake_execvp(file: str, args: list[str]) -> None:
        exec_calls.append((file, args))

    with patch("repo_scaffold.docker_ops._client", return_value=client), patch(
        "repo_scaffold.docker_ops.docker_build_base", return_value=_ok("built")
    ) as build_mock, patch(
        "repo_scaffold.docker_ops.os.execvp", side_effect=fake_execvp
    ):
        docker_shell("owner/myrepo", "main", "tok")

    build_mock.assert_called_once_with()
    assert exec_calls == [
        ("docker", ["docker", "exec", "-it", "-w", "/myrepo", "myrepo-main", "bash"])
    ]


def test_shell_skips_build_when_image_exists() -> None:
    client = _make_client()
    client.images.get.return_value = MagicMock()
    client.containers.get.side_effect = Exception("not found")
    client.containers.run.return_value = MagicMock()

    with patch("repo_scaffold.docker_ops._client", return_value=client), patch(
        "repo_scaffold.docker_ops.docker_build_base"
    ) as build_mock, patch("repo_scaffold.docker_ops.os.execvp"):
        docker_shell("owner/myrepo", "main", "tok")

    build_mock.assert_not_called()


def test_shell_rebuild_flag_forces_build() -> None:
    client = _make_client()
    # image exists, but rebuild=True bypasses the check
    client.images.get.return_value = MagicMock()
    client.containers.get.side_effect = Exception("not found")
    client.containers.run.return_value = MagicMock()

    with patch("repo_scaffold.docker_ops._client", return_value=client), patch(
        "repo_scaffold.docker_ops.docker_build_base", return_value=_ok("built")
    ) as build_mock, patch("repo_scaffold.docker_ops.os.execvp"):
        docker_shell("owner/myrepo", "main", "tok", rebuild=True)

    build_mock.assert_called_once_with()


def test_shell_stops_existing_container_before_spinup() -> None:
    client = _make_client()
    client.images.get.return_value = MagicMock()
    existing = MagicMock()
    # First containers.get (shell teardown) returns existing; second (spin_up dupe check) raises.
    client.containers.get.side_effect = [existing, Exception("not found")]
    client.containers.run.return_value = MagicMock()

    with patch("repo_scaffold.docker_ops._client", return_value=client), patch(
        "repo_scaffold.docker_ops.os.execvp"
    ):
        docker_shell("owner/myrepo", "main", "tok")

    existing.stop.assert_called_once_with(timeout=10)
    existing.remove.assert_called_once()


def test_shell_raises_on_build_failure() -> None:
    client = _make_client()
    client.images.get.side_effect = Exception("not found")

    with patch("repo_scaffold.docker_ops._client", return_value=client), patch(
        "repo_scaffold.docker_ops.docker_build_base",
        return_value=_err("docker build failed: daemon down"),
    ):
        with pytest.raises(RuntimeError, match="Workspace image build failed"):
            docker_shell("owner/myrepo", "main", "tok")


def test_shell_raises_on_spinup_failure() -> None:
    client = _make_client()
    client.images.get.return_value = MagicMock()
    client.containers.get.side_effect = Exception("not found")
    client.containers.run.side_effect = Exception("run failed")

    with patch("repo_scaffold.docker_ops._client", return_value=client):
        with pytest.raises(RuntimeError, match="Failed to start container"):
            docker_shell("owner/myrepo", "main", "tok")


# ---------------------------------------------------------------------------
# Windows Docker Desktop auto-start
# ---------------------------------------------------------------------------


def test_is_docker_unreachable_pipe_error() -> None:
    from repo_scaffold.docker_ops import _is_docker_unreachable

    exc = Exception("open //./pipe/dockerDesktopLinuxEngine: not found")
    assert _is_docker_unreachable(exc) is True


def test_is_docker_unreachable_connection_refused() -> None:
    from repo_scaffold.docker_ops import _is_docker_unreachable

    assert _is_docker_unreachable(Exception("Connection refused")) is True


def test_is_docker_unreachable_unrelated_error() -> None:
    from repo_scaffold.docker_ops import _is_docker_unreachable

    assert _is_docker_unreachable(Exception("permission denied")) is False


def test_is_docker_unreachable_windows_createfile_error() -> None:
    from repo_scaffold.docker_ops import _is_docker_unreachable

    exc = Exception(
        "Error while fetching server API version: "
        "(2, 'CreateFile', 'The system cannot find the file specified.')"
    )
    assert _is_docker_unreachable(exc) is True


def test_is_docker_unreachable_windows_createfile_access_denied_not_matched() -> None:
    """A CreateFile failure that isn't "file not found" (e.g. a pipe
    permissions problem) must not be misdiagnosed as a stopped daemon --
    restarting Docker Desktop won't fix an access-denied error, and treating
    it as unreachable just burns the full auto-start timeout."""
    from repo_scaffold.docker_ops import _is_docker_unreachable

    exc = Exception(
        "Error while fetching server API version: "
        "(5, 'CreateFile', 'Access is denied.')"
    )
    assert _is_docker_unreachable(exc) is False


def test_is_docker_unreachable_macos_missing_socket() -> None:
    from repo_scaffold.docker_ops import _is_docker_unreachable

    exc = Exception(
        "Error while fetching server API version: "
        "FileNotFoundError(2, 'No such file or directory')"
    )
    assert _is_docker_unreachable(exc) is True


def test_is_docker_unreachable_docker_sock_mention() -> None:
    from repo_scaffold.docker_ops import _is_docker_unreachable

    exc = Exception("[Errno 2] No such file or directory: '/var/run/docker.sock'")
    assert _is_docker_unreachable(exc) is True


def test_find_docker_desktop_windows_exe_found() -> None:
    from repo_scaffold.docker_ops import (
        _DOCKER_DESKTOP_WINDOWS_CANDIDATE_PATHS,
        _find_docker_desktop_windows_exe,
    )

    with patch("repo_scaffold.docker_ops.Path.exists", return_value=True):
        assert (
            _find_docker_desktop_windows_exe()
            == _DOCKER_DESKTOP_WINDOWS_CANDIDATE_PATHS[0]
        )


def test_find_docker_desktop_windows_exe_not_found() -> None:
    from repo_scaffold.docker_ops import _find_docker_desktop_windows_exe

    with patch("repo_scaffold.docker_ops.Path.exists", return_value=False):
        assert _find_docker_desktop_windows_exe() is None


def test_find_docker_desktop_macos_app_found() -> None:
    from repo_scaffold.docker_ops import (
        _DOCKER_DESKTOP_MACOS_APP_PATHS,
        _find_docker_desktop_macos_app,
    )

    with patch("repo_scaffold.docker_ops.Path.exists", return_value=True):
        assert _find_docker_desktop_macos_app() == _DOCKER_DESKTOP_MACOS_APP_PATHS[0]


def test_find_docker_desktop_macos_app_not_found() -> None:
    from repo_scaffold.docker_ops import _find_docker_desktop_macos_app

    with patch("repo_scaffold.docker_ops.Path.exists", return_value=False):
        assert _find_docker_desktop_macos_app() is None


def test_launch_args_windows_found() -> None:
    from repo_scaffold.docker_ops import _docker_desktop_launch_args

    with patch(
        "repo_scaffold.docker_ops.platform.system", return_value="Windows"
    ), patch(
        "repo_scaffold.docker_ops._find_docker_desktop_windows_exe",
        return_value="C:\\fake.exe",
    ):
        assert _docker_desktop_launch_args() == ["C:\\fake.exe"]


def test_launch_args_windows_not_found() -> None:
    from repo_scaffold.docker_ops import _docker_desktop_launch_args

    with patch(
        "repo_scaffold.docker_ops.platform.system", return_value="Windows"
    ), patch(
        "repo_scaffold.docker_ops._find_docker_desktop_windows_exe", return_value=None
    ):
        assert _docker_desktop_launch_args() is None


def test_launch_args_macos_found() -> None:
    from repo_scaffold.docker_ops import _docker_desktop_launch_args

    with patch(
        "repo_scaffold.docker_ops.platform.system", return_value="Darwin"
    ), patch(
        "repo_scaffold.docker_ops._find_docker_desktop_macos_app",
        return_value="/Applications/Docker.app",
    ):
        assert _docker_desktop_launch_args() == ["open", "-a", "Docker"]


def test_launch_args_macos_not_found() -> None:
    from repo_scaffold.docker_ops import _docker_desktop_launch_args

    with patch(
        "repo_scaffold.docker_ops.platform.system", return_value="Darwin"
    ), patch(
        "repo_scaffold.docker_ops._find_docker_desktop_macos_app", return_value=None
    ):
        assert _docker_desktop_launch_args() is None


def test_launch_args_linux_unsupported() -> None:
    from repo_scaffold.docker_ops import _docker_desktop_launch_args

    with patch("repo_scaffold.docker_ops.platform.system", return_value="Linux"):
        assert _docker_desktop_launch_args() is None


def test_auto_start_noop_on_linux_never_calls_popen() -> None:
    from repo_scaffold.docker_ops import _try_auto_start_docker_desktop

    with patch("repo_scaffold.docker_ops.platform.system", return_value="Linux"), patch(
        "repo_scaffold.docker_ops.subprocess.Popen"
    ) as popen_mock:
        assert _try_auto_start_docker_desktop() is False
        popen_mock.assert_not_called()


def test_auto_start_noop_when_windows_exe_missing() -> None:
    from repo_scaffold.docker_ops import _try_auto_start_docker_desktop

    with patch(
        "repo_scaffold.docker_ops.platform.system", return_value="Windows"
    ), patch(
        "repo_scaffold.docker_ops._find_docker_desktop_windows_exe", return_value=None
    ):
        assert _try_auto_start_docker_desktop() is False


def test_auto_start_returns_false_when_popen_fails() -> None:
    from repo_scaffold.docker_ops import _try_auto_start_docker_desktop

    with patch(
        "repo_scaffold.docker_ops.platform.system", return_value="Windows"
    ), patch(
        "repo_scaffold.docker_ops._find_docker_desktop_windows_exe",
        return_value="C:\\fake.exe",
    ), patch(
        "repo_scaffold.docker_ops.subprocess.Popen", side_effect=OSError("nope")
    ):
        assert _try_auto_start_docker_desktop() is False


def test_auto_start_launches_and_waits_on_windows() -> None:
    from repo_scaffold.docker_ops import _try_auto_start_docker_desktop

    with patch(
        "repo_scaffold.docker_ops.platform.system", return_value="Windows"
    ), patch(
        "repo_scaffold.docker_ops._find_docker_desktop_windows_exe",
        return_value="C:\\fake.exe",
    ), patch(
        "repo_scaffold.docker_ops.subprocess.Popen"
    ) as popen_mock, patch(
        "repo_scaffold.docker_ops._wait_for_docker_ready", return_value=True
    ) as wait_mock:
        assert _try_auto_start_docker_desktop() is True
        popen_mock.assert_called_once_with(
            ["C:\\fake.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        wait_mock.assert_called_once()


def test_auto_start_launches_and_waits_on_macos() -> None:
    from repo_scaffold.docker_ops import _try_auto_start_docker_desktop

    with patch(
        "repo_scaffold.docker_ops.platform.system", return_value="Darwin"
    ), patch(
        "repo_scaffold.docker_ops._find_docker_desktop_macos_app",
        return_value="/Applications/Docker.app",
    ), patch(
        "repo_scaffold.docker_ops.subprocess.Popen"
    ) as popen_mock, patch(
        "repo_scaffold.docker_ops._wait_for_docker_ready", return_value=True
    ) as wait_mock:
        assert _try_auto_start_docker_desktop() is True
        popen_mock.assert_called_once_with(
            ["open", "-a", "Docker"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        wait_mock.assert_called_once()


def test_wait_for_docker_ready_success() -> None:
    from repo_scaffold.docker_ops import _wait_for_docker_ready

    mock_docker = MagicMock()
    with patch.dict("sys.modules", {"docker": mock_docker}), patch(
        "repo_scaffold.docker_ops.time.sleep"
    ):
        assert _wait_for_docker_ready(timeout=5) is True


def test_wait_for_docker_ready_times_out() -> None:
    from repo_scaffold.docker_ops import _wait_for_docker_ready

    mock_docker = MagicMock()
    mock_docker.from_env.side_effect = Exception("still down")
    with patch.dict("sys.modules", {"docker": mock_docker}), patch(
        "repo_scaffold.docker_ops.time.sleep"
    ), patch("repo_scaffold.docker_ops.time.monotonic", side_effect=[0, 1, 100]):
        assert _wait_for_docker_ready(timeout=5) is False


def test_client_auto_starts_and_recovers() -> None:
    from repo_scaffold.docker_ops import _client

    mock_docker = MagicMock()
    mock_docker.from_env.side_effect = [
        Exception("open //./pipe/dockerDesktopLinuxEngine: not found"),
        MagicMock(),
    ]
    with patch.dict("sys.modules", {"docker": mock_docker}), patch(
        "repo_scaffold.docker_ops._try_auto_start_docker_desktop", return_value=True
    ):
        _client()

    assert mock_docker.from_env.call_count == 2


def test_client_auto_starts_but_retry_still_fails() -> None:
    from repo_scaffold.docker_ops import _client

    mock_docker = MagicMock()
    mock_docker.from_env.side_effect = [
        Exception("open //./pipe/dockerDesktopLinuxEngine: not found"),
        Exception("still unreachable after auto-start"),
    ]
    with patch.dict("sys.modules", {"docker": mock_docker}), patch(
        "repo_scaffold.docker_ops._try_auto_start_docker_desktop", return_value=True
    ):
        with pytest.raises(RuntimeError, match="still unreachable after auto-start"):
            _client()

    assert mock_docker.from_env.call_count == 2


def test_client_raises_with_auto_start_context_when_recovery_fails() -> None:
    from repo_scaffold.docker_ops import _client

    mock_docker = MagicMock()
    mock_docker.from_env.side_effect = Exception(
        "open //./pipe/dockerDesktopLinuxEngine: not found"
    )
    with patch.dict("sys.modules", {"docker": mock_docker}), patch(
        "repo_scaffold.docker_ops._try_auto_start_docker_desktop", return_value=False
    ):
        with pytest.raises(RuntimeError, match="Auto-start was attempted"):
            _client()
