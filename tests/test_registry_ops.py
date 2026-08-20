"""Tests for registry_ops: register/list/forget the local repo registry."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import repo_scaffold.registry_ops as registry_ops
from repo_scaffold.registry_ops import (
    forget_repo,
    list_registry,
    load_registry,
    register_repo,
    registry_path,
    save_registry,
)

# ---------------------------------------------------------------------------
# registry_path()
# ---------------------------------------------------------------------------


def test_registry_path_defaults_to_repo_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REPO_SCAFFOLD_REGISTRY_PATH", raising=False)
    path = registry_path()
    assert path.name == "registry.json"
    assert path.parent.name == ".repo-scaffold"
    assert (path.parent.parent / "pyproject.toml").exists()


def test_registry_path_env_var_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    override = tmp_path / "custom" / "registry.json"
    monkeypatch.setenv("REPO_SCAFFOLD_REGISTRY_PATH", str(override))
    assert registry_path() == override


# ---------------------------------------------------------------------------
# Legacy ~/.repo-scaffold/registry.json migration
# ---------------------------------------------------------------------------


def test_load_registry_migrates_legacy_home_dir_registry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    legacy = tmp_path / "legacy" / "registry.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        json.dumps({"acme/repo": {"local_path": "/old/path", "notes": ""}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(registry_ops, "_LEGACY_REGISTRY_PATH", legacy)

    target = tmp_path / "new" / "registry.json"
    entries = load_registry(target)

    assert "acme/repo" in entries
    assert target.exists()
    assert "Migrated registry" in capsys.readouterr().err


def test_load_registry_skips_migration_when_target_already_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    legacy = tmp_path / "legacy" / "registry.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        json.dumps({"legacy/repo": {"local_path": "/old", "notes": ""}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(registry_ops, "_LEGACY_REGISTRY_PATH", legacy)

    target = tmp_path / "new" / "registry.json"
    target.parent.mkdir(parents=True)
    target.write_text(
        json.dumps({"current/repo": {"local_path": "/new", "notes": ""}}),
        encoding="utf-8",
    )

    entries = load_registry(target)
    assert "current/repo" in entries
    assert "legacy/repo" not in entries


def test_load_registry_falls_back_to_legacy_when_migration_write_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A read-only checkout must not crash commands that could otherwise still
    read the existing legacy registry -- it should just skip persisting."""
    legacy = tmp_path / "legacy" / "registry.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        json.dumps({"acme/repo": {"local_path": "/old/path", "notes": ""}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(registry_ops, "_LEGACY_REGISTRY_PATH", legacy)

    def _raise_mkdir(*args: object, **kwargs: object) -> None:
        raise PermissionError("read-only filesystem")

    monkeypatch.setattr(Path, "mkdir", _raise_mkdir)

    target = tmp_path / "new" / "registry.json"
    entries = load_registry(target)

    assert "acme/repo" in entries
    assert not target.exists()
    assert "could not migrate" in capsys.readouterr().err


def test_load_registry_no_migration_when_legacy_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        registry_ops,
        "_LEGACY_REGISTRY_PATH",
        tmp_path / "nonexistent" / "registry.json",
    )
    target = tmp_path / "new" / "registry.json"
    assert load_registry(target) == {}
    assert not target.exists()


def test_load_registry_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_registry(tmp_path / "registry.json") == {}


def test_load_registry_invalid_json_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    path.write_text("not json", encoding="utf-8")
    assert load_registry(path) == {}


def test_register_repo_persists_entry(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    local_dir = tmp_path / "local" / "path"
    entry = register_repo("acme/repo", str(local_dir), "some notes", path=path)
    expected = str(local_dir.resolve())
    assert entry.repo == "acme/repo"
    assert entry.local_path == expected
    assert entry.notes == "some notes"

    reloaded = load_registry(path)
    assert "acme/repo" in reloaded
    assert reloaded["acme/repo"].local_path == expected


def test_register_repo_resolves_relative_path(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "registry.json"
    monkeypatch.chdir(tmp_path)
    entry = register_repo("acme/repo", ".", path=path)
    assert entry.local_path == str(tmp_path.resolve())


def test_register_repo_overwrites_existing_entry(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    old_dir = tmp_path / "old" / "path"
    new_dir = tmp_path / "new" / "path"
    register_repo("acme/repo", str(old_dir), path=path)
    register_repo("acme/repo", str(new_dir), path=path)
    reloaded = load_registry(path)
    assert reloaded["acme/repo"].local_path == str(new_dir.resolve())


def test_forget_repo_removes_entry(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    register_repo("acme/repo", str(tmp_path / "local" / "path"), path=path)
    removed = forget_repo("acme/repo", path=path)
    assert removed is True
    assert load_registry(path) == {}


def test_forget_repo_missing_entry_returns_false(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    assert forget_repo("acme/missing", path=path) is False


def test_list_registry_sorted_by_repo_name(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    register_repo("zeta/repo", str(tmp_path / "z"), path=path)
    register_repo("alpha/repo", str(tmp_path / "a"), path=path)
    entries = list_registry(path=path)
    assert [e.repo for e in entries] == ["alpha/repo", "zeta/repo"]


def test_save_registry_creates_parent_dirs(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir" / "registry.json"
    save_registry({}, path=path)
    assert path.exists()
