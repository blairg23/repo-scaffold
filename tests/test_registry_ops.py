"""Tests for registry_ops: register/list/forget the local repo registry."""

from __future__ import annotations

from pathlib import Path

from repo_scaffold.registry_ops import (
    forget_repo,
    list_registry,
    load_registry,
    register_repo,
    save_registry,
)


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
