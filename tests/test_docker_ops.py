"""Tests for docker_ops -- all Docker SDK calls are mocked."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from repo_scaffold.docker_ops import (
    _slug,
    container_name,
    docker_build_base,
    docker_list,
    docker_spin_down,
    docker_spin_up,
    image_name,
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


def test_image_name() -> None:
    assert image_name("blairg23/repo-scaffold") == "repo-scaffold-base:latest"


def test_image_name_strips_owner() -> None:
    assert image_name("org/project-x") == "project-x-base:latest"


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
        result = docker_build_base("owner/myrepo", tmp_path)
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
        result = docker_build_base("owner/myrepo", tmp_path)

    assert result.returncode == 0
    assert "myrepo-base:latest" in result.stdout
    client.images.build.assert_called_once_with(
        path=str(tmp_path), tag="myrepo-base:latest", rm=True
    )


def test_build_base_sdk_error(tmp_path: Path) -> None:
    (tmp_path / "Dockerfile").write_text("FROM python:3.12")

    client = _make_client()
    client.images.build.side_effect = RuntimeError("daemon down")

    with patch("repo_scaffold.docker_ops._client", return_value=client):
        result = docker_build_base("owner/myrepo", tmp_path)

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
