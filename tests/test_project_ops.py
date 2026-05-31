from __future__ import annotations

import datetime
import json
import subprocess
from pathlib import Path

import pytest

import repo_scaffold.project_ops as project_ops


def _cp_ok(stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["gh"], returncode=0, stdout=stdout, stderr=""
    )


def test_list_projects_resolves_and_parses_projects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)

    monkeypatch.setattr(
        project_ops,
        "_list_projects",
        lambda _repo_dir, _owner: [
            {"number": 1, "title": "Roadmap", "closed": False, "public": True},
            {"number": 2, "title": "Archive", "closed": True, "public": False},
        ],
    )

    summary = project_ops.list_projects(repo_dir=repo_dir, owner="acme")

    assert summary.owner == "acme"
    assert [project.title for project in summary.projects] == ["Roadmap", "Archive"]
    assert summary.projects[0].visibility == "PUBLIC"
    assert summary.projects[1].closed is True


def test_find_existing_project_by_title_includes_closed_projects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)

    calls: list[bool] = []
    monkeypatch.setattr(
        project_ops,
        "_list_projects",
        lambda _repo_dir, _owner, *, include_closed=False: (
            calls.append(include_closed)
            or [
                {
                    "number": 7,
                    "title": "Archived Roadmap",
                    "closed": True,
                    "public": False,
                }
            ]
        ),
    )
    monkeypatch.setattr(
        project_ops,
        "_load_project_view",
        lambda *_args, **_kwargs: {
            "number": 7,
            "title": "Archived Roadmap",
            "closed": True,
            "public": False,
        },
    )

    project = project_ops._find_existing_project(
        repo_dir=repo_dir,
        owner="acme",
        project_number=None,
        project_title="Archived Roadmap",
    )

    assert calls == [True]
    assert project.number == 7
    assert project.closed is True


def test_backup_paths_are_unique_within_same_second(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)

    class _FixedDatetime:
        @staticmethod
        def now(_tz):
            return datetime.datetime(2026, 4, 29, 12, 0, 0, 123456)

    class _FakeUuid:
        def __init__(self, hex_value: str) -> None:
            self.hex = hex_value

    uuids = iter([_FakeUuid("aaaaaaaa12345678"), _FakeUuid("bbbbbbbb12345678")])
    monkeypatch.setattr(project_ops, "datetime", _FixedDatetime)
    monkeypatch.setattr(project_ops.uuid, "uuid4", lambda: next(uuids))

    first, first_stamp = project_ops._backup_paths(
        repo_dir=repo_dir,
        backup_dir=None,
        prefix="project-delete",
    )
    second, second_stamp = project_ops._backup_paths(
        repo_dir=repo_dir,
        backup_dir=None,
        prefix="project-delete",
    )

    assert first != second
    assert first_stamp == second_stamp
    assert first.name.endswith("-aaaaaaaa.json")
    assert second.name.endswith("-bbbbbbbb.json")
    assert first.parent.is_dir()


def test_list_project_items_parses_issue_and_draft_items(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)

    monkeypatch.setattr(
        project_ops,
        "_find_existing_project",
        lambda **_kwargs: project_ops.ProjectInfo(
            owner="acme", number=4, title="Roadmap"
        ),
    )
    monkeypatch.setattr(
        project_ops,
        "_load_project_items",
        lambda *_args, **_kwargs: [
            {
                "id": "PVTI_issue",
                "content": {
                    "type": "Issue",
                    "title": "Issue Ticket",
                    "url": "https://github.com/acme/repo/issues/11",
                    "number": 11,
                    "repository": {"nameWithOwner": "acme/repo"},
                },
            },
            {
                "id": "PVTI_draft",
                "title": "Draft Ticket",
                "body": "draft body",
                "type": "DraftIssue",
            },
        ],
    )

    summary = project_ops.list_project_items(
        repo_dir=repo_dir,
        owner="acme",
        project_number=4,
        project_title=None,
        limit=50,
    )

    assert summary.project.number == 4
    assert summary.items[0].issue_number == 11
    assert summary.items[0].repository == "acme/repo"
    assert summary.items[1].content_type == "DraftIssue"
    assert summary.items[1].body == "draft body"


def test_sync_project_metadata_writes_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)
    monkeypatch.setenv("GH_REPO", "acme/repo")
    monkeypatch.setattr(
        project_ops,
        "_find_existing_project",
        lambda **_kwargs: project_ops.ProjectInfo(
            owner="acme",
            number=4,
            title="Roadmap",
            closed=False,
            visibility="PRIVATE",
            description="desc",
        ),
    )

    summary = project_ops.sync_project_metadata(
        repo_dir=repo_dir,
        owner="acme",
        project_number=4,
        project_title=None,
        out=lambda _line: None,
    )

    assert summary.metadata_file is not None
    payload = json.loads(summary.metadata_file.read_text(encoding="utf-8"))
    assert payload["repo"] == "acme/repo"
    assert payload["owner"] == "acme"
    assert payload["number"] == 4
    assert payload["title"] == "Roadmap"
    assert payload["source"] == "project_sync_metadata"


def test_create_project_delegates_optional_metadata_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)

    calls: list[list[str]] = []
    edits: list[dict[str, object]] = []

    monkeypatch.setattr(
        project_ops,
        "_run_gh",
        lambda _repo_dir, args: calls.append(args)
        or _cp_ok('{"number": 9, "title": "Roadmap"}'),
    )
    monkeypatch.setattr(
        project_ops,
        "edit_project",
        lambda **kwargs: edits.append(kwargs)
        or project_ops.ProjectMutationSummary(
            action="edit",
            owner="acme",
            project_number=9,
            project_title="Roadmap",
            failures=0,
            changed=True,
        ),
    )

    summary = project_ops.create_project(
        repo_dir=repo_dir,
        owner="acme",
        project_title="Roadmap",
        description="desc",
        readme="body",
        visibility="private",
        dry_run=False,
        out=lambda _line: None,
    )

    assert calls[0][:4] == ["project", "create", "--owner", "acme"]
    assert summary.project_number == 9
    assert edits and edits[0]["project_number"] == 9


def test_delete_project_requires_danger(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)

    with pytest.raises(RuntimeError, match="without --danger"):
        project_ops.delete_project(
            repo_dir=repo_dir,
            owner="acme",
            project_number=4,
            project_title=None,
            danger=False,
            assume_yes=True,
            dry_run=False,
            backup_dir=None,
            prompt=lambda _msg: "yes",
            is_tty=True,
            out=lambda _line: None,
        )


def test_delete_project_writes_backup_and_undo_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)

    monkeypatch.setattr(
        project_ops,
        "_find_existing_project",
        lambda **_kwargs: project_ops.ProjectInfo(
            owner="acme", number=4, title="Roadmap"
        ),
    )
    monkeypatch.setattr(
        project_ops,
        "_load_project_view",
        lambda *_args, **_kwargs: {"number": 4, "title": "Roadmap", "id": "PVT_1"},
    )
    monkeypatch.setattr(
        project_ops,
        "_load_project_items",
        lambda *_args, **_kwargs: [
            {
                "id": "PVTI_1",
                "content": {
                    "type": "Issue",
                    "title": "Ticket A",
                    "url": "https://github.com/acme/repo/issues/11",
                    "number": 11,
                },
            }
        ],
    )
    monkeypatch.setattr(project_ops, "_run_gh", lambda *_args, **_kwargs: _cp_ok())

    summary = project_ops.delete_project(
        repo_dir=repo_dir,
        owner="acme",
        project_number=4,
        project_title=None,
        danger=True,
        assume_yes=True,
        dry_run=False,
        backup_dir=None,
        prompt=lambda _msg: "yes",
        is_tty=True,
        out=lambda _line: None,
    )

    assert summary.failures == 0
    assert summary.backup_file is not None
    payload = json.loads(summary.backup_file.read_text(encoding="utf-8"))
    assert payload["kind"] == "project_delete"
    assert "project undo --backup-file" in (summary.undo_command or "")


def test_delete_project_item_by_issue_number_writes_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)

    monkeypatch.setattr(
        project_ops,
        "_find_existing_project",
        lambda **_kwargs: project_ops.ProjectInfo(
            owner="acme", number=4, title="Roadmap"
        ),
    )
    monkeypatch.setattr(
        project_ops,
        "_load_project_view",
        lambda *_args, **_kwargs: {"number": 4, "title": "Roadmap", "id": "PVT_1"},
    )
    monkeypatch.setattr(
        project_ops,
        "_load_project_items",
        lambda *_args, **_kwargs: [
            {
                "id": "PVTI_1",
                "content": {
                    "type": "Issue",
                    "title": "Ticket A",
                    "url": "https://github.com/acme/repo/issues/11",
                    "number": 11,
                },
            }
        ],
    )
    monkeypatch.setattr(project_ops, "_run_gh", lambda *_args, **_kwargs: _cp_ok())

    summary = project_ops.delete_project_item(
        repo_dir=repo_dir,
        owner="acme",
        project_number=4,
        project_title=None,
        item_id=None,
        issue_number=11,
        danger=True,
        assume_yes=True,
        dry_run=False,
        backup_dir=None,
        prompt=lambda _msg: "yes",
        is_tty=True,
        out=lambda _line: None,
    )

    assert summary.failures == 0
    assert summary.backup_file is not None
    payload = json.loads(summary.backup_file.read_text(encoding="utf-8"))
    assert payload["kind"] == "project_item_delete"
    assert payload["item_summary"]["issue_number"] == 11


def test_undo_project_delete_restores_project_and_items(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)
    backup_file = repo_dir / "backup.json"
    backup_file.write_text(
        json.dumps(
            {
                "version": 1,
                "kind": "project_delete",
                "project_owner": "acme",
                "project_number": 4,
                "project_title": "Roadmap",
                "project": {
                    "number": 4,
                    "title": "Roadmap",
                    "shortDescription": "desc",
                    "readme": "hello",
                    "public": False,
                },
                "items": [
                    {
                        "id": "PVTI_issue",
                        "content": {
                            "type": "Issue",
                            "title": "Issue Ticket",
                            "url": "https://github.com/acme/repo/issues/11",
                            "number": 11,
                        },
                    },
                    {
                        "id": "PVTI_draft",
                        "title": "Draft Ticket",
                        "body": "draft body",
                        "type": "DraftIssue",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    calls: list[list[str]] = []
    monkeypatch.setattr(
        project_ops,
        "list_projects",
        lambda **_kwargs: project_ops.ProjectListSummary(owner="acme", projects=()),
    )

    def _fake_run_gh(
        _repo_dir: Path, args: list[str]
    ) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[:2] == ["project", "create"]:
            return _cp_ok('{"number": 7, "title": "Roadmap"}')
        if args[:2] == ["project", "view"]:
            return _cp_ok('{"number": 7, "title": "Roadmap"}')
        if args[:2] == ["project", "edit"]:
            return _cp_ok()
        if args[:2] == ["project", "item-add"]:
            return _cp_ok()
        if args[:2] == ["project", "item-create"]:
            return _cp_ok('{"id":"PVTI_new"}')
        raise AssertionError(f"Unexpected gh invocation: {args}")

    monkeypatch.setattr(project_ops, "_run_gh", _fake_run_gh)

    summary = project_ops.undo_project_backup(
        repo_dir=repo_dir,
        backup_file=backup_file,
        dry_run=False,
        out=lambda _line: None,
    )

    assert summary.failures == 0
    assert summary.restored_project_number == 7
    assert any(args[:2] == ["project", "item-add"] for args in calls)
    assert any(args[:2] == ["project", "item-create"] for args in calls)


def test_undo_project_item_delete_restores_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)
    backup_file = repo_dir / "item-backup.json"
    backup_file.write_text(
        json.dumps(
            {
                "version": 1,
                "kind": "project_item_delete",
                "project_owner": "acme",
                "project_number": 4,
                "project_title": "Roadmap",
                "item": {
                    "id": "PVTI_issue",
                    "content": {
                        "type": "Issue",
                        "title": "Issue Ticket",
                        "url": "https://github.com/acme/repo/issues/11",
                        "number": 11,
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    calls: list[list[str]] = []
    monkeypatch.setattr(
        project_ops,
        "_run_gh",
        lambda _repo_dir, args: calls.append(args) or _cp_ok(),
    )

    summary = project_ops.undo_project_backup(
        repo_dir=repo_dir,
        backup_file=backup_file,
        dry_run=False,
        out=lambda _line: None,
    )

    assert summary.failures == 0
    assert any(args[:2] == ["project", "item-add"] for args in calls)


# ---------------------------------------------------------------------------
# update_project_item_status
# ---------------------------------------------------------------------------


def _cp_err(stderr: str = "error") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr=stderr)


def _make_item_list_payload(issue_number: int, item_id: str) -> str:
    return json.dumps(
        {
            "items": [
                {
                    "id": item_id,
                    "content": {"type": "Issue", "number": issue_number},
                }
            ]
        }
    )


def _make_fields_payload(field_id: str, options: list[dict]) -> str:
    return json.dumps(
        {"fields": [{"id": field_id, "name": "Status", "options": options}]}
    )


def _make_update_payload() -> str:
    return json.dumps(
        {"updateProjectV2ItemFieldValue": {"projectV2Item": {"id": "PVTI_1"}}}
    )


def test_update_project_item_status_success(
    tmp_path: pytest.MonkeyPatch, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()

    options = [{"id": "opt_prog", "name": "In Progress"}]

    calls: list[str] = []

    def fake_find(repo_dir, owner, number, limit):
        return [{"id": "PVTI_1", "content": {"type": "Issue", "number": 17}}]

    def fake_project_item_list(project_id, token, limit=100):
        return subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=_make_item_list_payload(17, "PVTI_1"),
            stderr="",
        )

    def fake_project_fields(project_id, token):
        return subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=_make_fields_payload("F_status", options),
            stderr="",
        )

    def fake_project_item_update_field(project_id, item_id, field_id, option_id, token):
        calls.append(option_id)
        return subprocess.CompletedProcess(
            args=[], returncode=0, stdout=_make_update_payload(), stderr=""
        )

    def fake_find_project(*, repo_dir, owner, project_number, project_title):
        from repo_scaffold.project_ops import ProjectInfo

        return ProjectInfo(owner="acme", number=1, title="Roadmap", id="PV2_1")

    def fake_token(repo_dir):
        return "tok"

    monkeypatch.setattr(project_ops, "_find_existing_project", fake_find_project)
    import repo_scaffold.github_api as _ga

    monkeypatch.setattr(_ga, "project_item_list", fake_project_item_list)
    monkeypatch.setattr(_ga, "project_fields", fake_project_fields)
    monkeypatch.setattr(
        _ga, "project_item_update_field", fake_project_item_update_field
    )
    monkeypatch.setattr(_ga, "token_from_repo", fake_token)

    summary = project_ops.update_project_item_status(
        repo_dir=repo_dir,
        owner="acme",
        project_number=1,
        project_title="Roadmap",
        issue_repo="acme/repo",
        issue_number=17,
        status="In Progress",
        out=lambda _: None,
    )

    assert summary.failures == 0
    assert summary.action == "item-status"
    assert calls == ["opt_prog"]


def test_update_project_item_status_bad_status(
    tmp_path: pytest.MonkeyPatch, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()

    options = [{"id": "opt_todo", "name": "Todo"}]

    def fake_find_project(*, repo_dir, owner, project_number, project_title):
        from repo_scaffold.project_ops import ProjectInfo

        return ProjectInfo(owner="acme", number=1, title="Roadmap", id="PV2_1")

    def fake_project_item_list(project_id, token, limit=100):
        return subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=_make_item_list_payload(17, "PVTI_1"),
            stderr="",
        )

    def fake_project_fields(project_id, token):
        return subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=_make_fields_payload("F_status", options),
            stderr="",
        )

    def fake_token(repo_dir):
        return "tok"

    monkeypatch.setattr(project_ops, "_find_existing_project", fake_find_project)
    import repo_scaffold.github_api as _ga

    monkeypatch.setattr(_ga, "project_item_list", fake_project_item_list)
    monkeypatch.setattr(_ga, "project_fields", fake_project_fields)
    monkeypatch.setattr(_ga, "token_from_repo", fake_token)

    with pytest.raises(RuntimeError, match="not found"):
        project_ops.update_project_item_status(
            repo_dir=repo_dir,
            owner="acme",
            project_number=1,
            project_title="Roadmap",
            issue_repo="acme/repo",
            issue_number=17,
            status="Nonexistent",
            out=lambda _: None,
        )
