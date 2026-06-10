from __future__ import annotations

import json
import urllib.error
import urllib.request
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import repo_scaffold.github_api as github_api

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_resp(status: int, body: str | bytes) -> MagicMock:
    raw = body.encode() if isinstance(body, str) else body
    resp = MagicMock()
    resp.read.return_value = raw
    resp.headers = {"Link": ""}
    resp.status = status
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _http_error(code: int, body: str = "") -> urllib.error.HTTPError:
    fp = BytesIO(body.encode())
    return urllib.error.HTTPError(url="", code=code, msg="", hdrs=MagicMock(), fp=fp)


# ---------------------------------------------------------------------------
# resolve_token
# ---------------------------------------------------------------------------


def test_resolve_token_reads_gh_token() -> None:
    assert github_api.resolve_token({"GH_TOKEN": "tok"}) == "tok"


def test_resolve_token_fallback_order() -> None:
    assert github_api.resolve_token({"GITHUB_TOKEN": "tok2"}) == "tok2"
    assert github_api.resolve_token({"github_token": "tok3"}) == "tok3"
    assert github_api.resolve_token({}) is None


# ---------------------------------------------------------------------------
# _parse_next_link
# ---------------------------------------------------------------------------


def test_parse_next_link_returns_url() -> None:
    link = '<https://api.github.com/repos/X/issues?page=2>; rel="next", <https://api.github.com/repos/X/issues?page=5>; rel="last"'
    assert (
        github_api._parse_next_link(link)
        == "https://api.github.com/repos/X/issues?page=2"
    )


def test_parse_next_link_no_next_returns_none() -> None:
    link = '<https://api.github.com/repos/X/issues?page=5>; rel="last"'
    assert github_api._parse_next_link(link) is None


def test_parse_next_link_empty_returns_none() -> None:
    assert github_api._parse_next_link("") is None


# ---------------------------------------------------------------------------
# rest
# ---------------------------------------------------------------------------


def test_rest_get_success() -> None:
    payload = json.dumps({"login": "octocat"})
    with patch("urllib.request.urlopen", return_value=_mock_resp(200, payload)):
        cp = github_api.rest("GET", "/user", "token")
    assert cp.returncode == 0
    assert json.loads(cp.stdout)["login"] == "octocat"


def test_rest_returns_err_on_http_error() -> None:
    with patch("urllib.request.urlopen", side_effect=_http_error(404, "not found")):
        cp = github_api.rest("GET", "/repos/x/y", "token")
    assert cp.returncode == 404
    assert "not found" in cp.stderr


def test_rest_returns_err_on_url_error() -> None:
    with patch(
        "urllib.request.urlopen",
        side_effect=urllib.error.URLError("connection refused"),
    ):
        cp = github_api.rest("GET", "/user", "token")
    assert cp.returncode != 0
    assert "connection refused" in cp.stderr


# ---------------------------------------------------------------------------
# rest_paginated
# ---------------------------------------------------------------------------


def test_rest_paginated_collects_all_pages() -> None:
    page1 = json.dumps([{"number": 1}])
    page2 = json.dumps([{"number": 2}])

    resp1 = _mock_resp(200, page1)
    resp1.headers = {"Link": '<https://api.github.com/issues?page=2>; rel="next"'}

    resp2 = _mock_resp(200, page2)
    resp2.headers = {"Link": ""}

    call_count = 0

    class _CM:
        def __init__(self, resp: MagicMock) -> None:
            self._resp = resp

        def __enter__(self) -> MagicMock:
            return self._resp

        def __exit__(self, *_: object) -> bool:
            return False

    responses = [_CM(resp1), _CM(resp2)]

    def _fake_urlopen(req: object) -> object:
        nonlocal call_count
        r = responses[call_count]
        call_count += 1
        return r

    with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
        cp = github_api.rest_paginated("/issues", "token")

    assert cp.returncode == 0
    items = json.loads(cp.stdout)
    assert len(items) == 2
    assert items[0]["number"] == 1
    assert items[1]["number"] == 2


# ---------------------------------------------------------------------------
# graphql
# ---------------------------------------------------------------------------


def test_graphql_success() -> None:
    payload = json.dumps({"data": {"user": {"id": "U_123"}}})
    with patch("urllib.request.urlopen", return_value=_mock_resp(200, payload)):
        cp = github_api.graphql("query { user { id } }", {}, "token")
    assert cp.returncode == 0
    assert json.loads(cp.stdout)["user"]["id"] == "U_123"


def test_graphql_surfaces_errors() -> None:
    payload = json.dumps({"errors": [{"message": "Field 'x' doesn't exist"}]})
    with patch("urllib.request.urlopen", return_value=_mock_resp(200, payload)):
        cp = github_api.graphql("query { x }", {}, "token")
    assert cp.returncode != 0
    assert "doesn't exist" in cp.stderr


def test_graphql_http_error() -> None:
    with patch("urllib.request.urlopen", side_effect=_http_error(401, "Unauthorized")):
        cp = github_api.graphql("query { user { id } }", {}, "bad-token")
    assert cp.returncode == 401


# ---------------------------------------------------------------------------
# project_list / project_view / project_create (smoke tests with mocks)
# ---------------------------------------------------------------------------


def _graphql_ok(data: dict) -> MagicMock:  # type: ignore[type-arg]
    return _mock_resp(200, json.dumps({"data": data}))


def test_project_list_user() -> None:
    data = {
        "user": {
            "projectsV2": {
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "nodes": [
                    {"id": "PV2_1", "number": 1, "title": "My Project", "closed": False}
                ],
            }
        }
    }
    with patch("urllib.request.urlopen", return_value=_graphql_ok(data)):
        cp = github_api.project_list("alice", "token")
    assert cp.returncode == 0
    result = json.loads(cp.stdout)
    assert result["totalCount"] == 1
    assert result["projects"][0]["title"] == "My Project"


def test_project_view_user() -> None:
    data = {
        "user": {
            "projectV2": {
                "id": "PV2_1",
                "number": 1,
                "title": "My Project",
                "closed": False,
            }
        }
    }
    with patch("urllib.request.urlopen", return_value=_graphql_ok(data)):
        cp = github_api.project_view("alice", 1, "token")
    assert cp.returncode == 0
    assert json.loads(cp.stdout)["title"] == "My Project"


def test_project_view_not_found() -> None:
    data: dict = {"user": {"projectV2": None}, "organization": {"projectV2": None}}
    with patch("urllib.request.urlopen", return_value=_graphql_ok(data)):
        cp = github_api.project_view("alice", 999, "token")
    assert cp.returncode != 0


def test_project_create() -> None:
    owner_data = {"user": {"id": "U_abc"}}
    create_data = {
        "createProjectV2": {
            "projectV2": {"id": "PV2_2", "number": 2, "title": "New Project"}
        }
    }

    responses = [_graphql_ok(owner_data), _graphql_ok(create_data)]
    call_count = 0

    class _CM:
        def __init__(self, resp: MagicMock) -> None:
            self._resp = resp

        def __enter__(self) -> MagicMock:
            return self._resp

        def __exit__(self, *_: object) -> bool:
            return False

    def _fake_urlopen(req: object) -> object:
        nonlocal call_count
        r = _CM(responses[call_count])
        call_count += 1
        return r

    with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
        cp = github_api.project_create("alice", "New Project", "token")

    assert cp.returncode == 0
    assert json.loads(cp.stdout)["title"] == "New Project"


# ---------------------------------------------------------------------------
# repo_create
# ---------------------------------------------------------------------------


def test_repo_create_user() -> None:
    user_data = json.dumps({"type": "User", "login": "alice"})
    repo_data = json.dumps(
        {"full_name": "alice/myrepo", "html_url": "https://github.com/alice/myrepo"}
    )

    responses = [_mock_resp(200, user_data), _mock_resp(201, repo_data)]
    call_count = 0

    class _CM:
        def __init__(self, resp: MagicMock) -> None:
            self._resp = resp

        def __enter__(self) -> MagicMock:
            return self._resp

        def __exit__(self, *_: object) -> bool:
            return False

    def _fake_urlopen(req: object) -> object:
        nonlocal call_count
        r = _CM(responses[call_count])
        call_count += 1
        return r

    with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
        cp = github_api.repo_create("alice", "myrepo", "token", visibility="public")

    assert cp.returncode == 0
    assert json.loads(cp.stdout)["full_name"] == "alice/myrepo"


# ---------------------------------------------------------------------------
# validate_token / get_authenticated_login
# ---------------------------------------------------------------------------


def test_validate_token_true_on_200() -> None:
    with patch(
        "urllib.request.urlopen", return_value=_mock_resp(200, '{"login":"alice"}')
    ):
        assert github_api.validate_token("good-token") is True


def test_validate_token_false_on_401() -> None:
    with patch("urllib.request.urlopen", side_effect=_http_error(401)):
        assert github_api.validate_token("bad-token") is False


def test_get_authenticated_login() -> None:
    with patch(
        "urllib.request.urlopen", return_value=_mock_resp(200, '{"login":"alice"}')
    ):
        assert github_api.get_authenticated_login("token") == "alice"


# ---------------------------------------------------------------------------
# token_from_repo
# ---------------------------------------------------------------------------


def test_token_from_repo_reads_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("github_token", raising=False)
    monkeypatch.chdir(tmp_path)
    env_file = tmp_path / ".env"
    env_file.write_text("GH_TOKEN=mytoken\n", encoding="utf-8")
    token = github_api.token_from_repo(tmp_path)
    assert token == "mytoken"


def test_token_from_repo_returns_none_without_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("github_token", raising=False)
    monkeypatch.chdir(tmp_path)  # avoid picking up project-root .env
    token = github_api.token_from_repo(tmp_path)
    assert token is None


# ---------------------------------------------------------------------------
# pr_update
# ---------------------------------------------------------------------------


def test_pr_update_body_only() -> None:
    response = {
        "number": 42,
        "title": "My PR",
        "html_url": "https://github.com/a/b/pull/42",
    }
    with patch(
        "urllib.request.urlopen", return_value=_mock_resp(200, json.dumps(response))
    ):
        cp = github_api.pr_update(
            "owner/repo", pr_number=42, token="tok", body="new body"
        )
    assert cp.returncode == 0
    result = json.loads(cp.stdout)
    assert result["number"] == 42


def test_pr_update_title_only() -> None:
    response = {
        "number": 7,
        "title": "New Title",
        "html_url": "https://github.com/a/b/pull/7",
    }
    with patch(
        "urllib.request.urlopen", return_value=_mock_resp(200, json.dumps(response))
    ):
        cp = github_api.pr_update(
            "owner/repo", pr_number=7, token="tok", title="New Title"
        )
    assert cp.returncode == 0
    result = json.loads(cp.stdout)
    assert result["title"] == "New Title"


def test_pr_update_title_and_body() -> None:
    response = {"number": 3, "title": "T", "html_url": "https://github.com/a/b/pull/3"}
    with patch(
        "urllib.request.urlopen", return_value=_mock_resp(200, json.dumps(response))
    ):
        cp = github_api.pr_update(
            "owner/repo", pr_number=3, token="tok", title="T", body="B"
        )
    assert cp.returncode == 0


# ---------------------------------------------------------------------------
# link_project_to_repository
# ---------------------------------------------------------------------------


def test_link_project_to_repository_success() -> None:
    repo_id_data = {"repository": {"id": "R_abc"}}
    link_data = {"linkProjectV2ToRepository": {"repository": {"id": "R_abc"}}}
    with patch(
        "urllib.request.urlopen",
        side_effect=[
            _graphql_ok(repo_id_data),
            _graphql_ok(link_data),
        ],
    ):
        cp = github_api.link_project_to_repository("PV2_1", "acme", "my-repo", "tok")
    assert cp.returncode == 0


def test_link_project_to_repository_no_repo_id() -> None:
    repo_id_data: dict = {"repository": None}
    with patch("urllib.request.urlopen", return_value=_graphql_ok(repo_id_data)):
        cp = github_api.link_project_to_repository("PV2_1", "acme", "missing", "tok")
    assert cp.returncode != 0


def test_issue_update_body_only() -> None:
    response = {
        "number": 42,
        "title": "My Issue",
        "html_url": "https://github.com/a/b/issues/42",
    }
    with patch(
        "urllib.request.urlopen", return_value=_mock_resp(200, json.dumps(response))
    ):
        cp = github_api.issue_update("owner/repo", 42, "tok", body="new body")
    assert cp.returncode == 0
    result = json.loads(cp.stdout)
    assert result["number"] == 42


def test_issue_update_title_only() -> None:
    response = {
        "number": 7,
        "title": "New Title",
        "html_url": "https://github.com/a/b/issues/7",
    }
    with patch(
        "urllib.request.urlopen", return_value=_mock_resp(200, json.dumps(response))
    ):
        cp = github_api.issue_update("owner/repo", 7, "tok", title="New Title")
    assert cp.returncode == 0
    result = json.loads(cp.stdout)
    assert result["title"] == "New Title"


def test_issue_update_title_and_body() -> None:
    response = {
        "number": 3,
        "title": "T",
        "html_url": "https://github.com/a/b/issues/3",
    }
    with patch(
        "urllib.request.urlopen", return_value=_mock_resp(200, json.dumps(response))
    ):
        cp = github_api.issue_update("owner/repo", 3, "tok", title="T", body="B")
    assert cp.returncode == 0


def test_issue_update_state_open() -> None:
    response = {
        "number": 7,
        "title": "Some Issue",
        "html_url": "https://github.com/a/b/issues/7",
        "state": "open",
    }
    with patch(
        "urllib.request.urlopen", return_value=_mock_resp(200, json.dumps(response))
    ):
        cp = github_api.issue_update("owner/repo", 7, "tok", state="open")
    assert cp.returncode == 0
    result = json.loads(cp.stdout)
    assert result["state"] == "open"


# ---------------------------------------------------------------------------
# project_fields / project_item_update_field
# ---------------------------------------------------------------------------


def test_project_fields_returns_single_select_fields() -> None:
    data = {
        "node": {
            "fields": {
                "nodes": [
                    {
                        "id": "F_1",
                        "name": "Status",
                        "options": [
                            {"id": "opt_todo", "name": "Todo"},
                            {"id": "opt_prog", "name": "In Progress"},
                            {"id": "opt_done", "name": "Done"},
                        ],
                    }
                ]
            }
        }
    }
    with patch("urllib.request.urlopen", return_value=_graphql_ok(data)):
        cp = github_api.project_fields("PV2_1", "tok")
    assert cp.returncode == 0
    result = json.loads(cp.stdout)
    assert len(result["fields"]) == 1
    assert result["fields"][0]["name"] == "Status"


def test_project_item_update_field_success() -> None:
    data = {"updateProjectV2ItemFieldValue": {"projectV2Item": {"id": "PVTI_1"}}}
    with patch("urllib.request.urlopen", return_value=_graphql_ok(data)):
        cp = github_api.project_item_update_field(
            "PV2_1", "PVTI_1", "F_1", "opt_prog", "tok"
        )
    assert cp.returncode == 0


def test_setup_project_status_field_preserves_existing_ids() -> None:
    fields_data = {
        "node": {
            "fields": {
                "nodes": [
                    {
                        "id": "F_status",
                        "name": "Status",
                        "options": [
                            {"id": "existing_todo", "name": "Todo"},
                            {"id": "existing_done", "name": "Done"},
                        ],
                    }
                ]
            }
        }
    }
    update_resp = {
        "updateProjectV2Field": {
            "projectV2Field": {
                "id": "F_status",
                "name": "Status",
                "options": [],
            }
        }
    }
    calls: list[dict[str, object]] = []

    def fake_urlopen(req: object) -> object:
        import json as _json

        body = _json.loads(getattr(req, "data", b"{}"))
        calls.append(body)
        if "updateProjectV2Field" in body.get("query", ""):
            return _graphql_ok(update_resp)
        return _graphql_ok(fields_data)

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        cp = github_api.setup_project_status_field("PV2_1", "tok")

    assert cp.returncode == 0
    update_call = next(c for c in calls if "updateProjectV2Field" in c.get("query", ""))
    sent_options: list[dict[str, str]] = update_call["variables"]["options"]
    todo_opt = next(o for o in sent_options if o["name"] == "Todo")
    done_opt = next(o for o in sent_options if o["name"] == "Done")
    in_progress_opt = next(o for o in sent_options if o["name"] == "In Progress")
    assert todo_opt.get("id") == "existing_todo"
    assert done_opt.get("id") == "existing_done"
    assert "id" not in in_progress_opt


def test_setup_project_status_field_new_project_no_ids() -> None:
    fields_data = {
        "node": {
            "fields": {
                "nodes": [
                    {
                        "id": "F_status",
                        "name": "Status",
                        "options": [],
                    }
                ]
            }
        }
    }
    update_resp = {
        "updateProjectV2Field": {
            "projectV2Field": {"id": "F_status", "name": "Status", "options": []}
        }
    }
    calls: list[dict[str, object]] = []

    def fake_urlopen(req: object) -> object:
        import json as _json

        body = _json.loads(getattr(req, "data", b"{}"))
        calls.append(body)
        if "updateProjectV2Field" in body.get("query", ""):
            return _graphql_ok(update_resp)
        return _graphql_ok(fields_data)

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        cp = github_api.setup_project_status_field("PV2_1", "tok")

    assert cp.returncode == 0
    update_call = next(c for c in calls if "updateProjectV2Field" in c.get("query", ""))
    sent_options: list[dict[str, str]] = update_call["variables"]["options"]
    assert all("id" not in o for o in sent_options)
    assert len(sent_options) == len(github_api.STANDARD_STATUS_OPTIONS)


# ---------------------------------------------------------------------------
# branch_create / branch_delete / pr_merge / pr_checks / issue_list --label
# ---------------------------------------------------------------------------


def test_branch_create_success() -> None:
    sha_data = [{"object": {"sha": "abc123"}}]
    ref_data = {"ref": "refs/heads/feat/foo", "object": {"sha": "abc123"}}
    with patch(
        "urllib.request.urlopen",
        side_effect=[
            _mock_resp(200, json.dumps(sha_data)),
            _mock_resp(201, json.dumps(ref_data)),
        ],
    ):
        cp = github_api.branch_create("owner/repo", "feat/foo", "tok", base="main")
    assert cp.returncode == 0
    assert json.loads(cp.stdout)["ref"] == "refs/heads/feat/foo"


def test_branch_create_bad_base() -> None:
    with patch(
        "urllib.request.urlopen",
        side_effect=_http_error(404, "not found"),
    ):
        cp = github_api.branch_create(
            "owner/repo", "feat/foo", "tok", base="nonexistent"
        )
    assert cp.returncode != 0


def test_branch_delete_success() -> None:
    with patch("urllib.request.urlopen", return_value=_mock_resp(204, b"")):
        cp = github_api.branch_delete("owner/repo", "feat/foo", "tok")
    assert cp.returncode == 0


def test_pr_merge_success() -> None:
    resp = {"sha": "abc", "merged": True, "message": "Pull Request successfully merged"}
    with patch(
        "urllib.request.urlopen", return_value=_mock_resp(200, json.dumps(resp))
    ):
        cp = github_api.pr_merge("owner/repo", 42, "tok", method="squash")
    assert cp.returncode == 0
    assert json.loads(cp.stdout)["merged"] is True


def test_pr_checks_success() -> None:
    pr_data = {"head": {"sha": "abc123"}}
    # Real API returns {total_count, check_runs: [...]} not a bare array
    runs_payload = {
        "total_count": 1,
        "check_runs": [
            {"id": 1, "name": "CI", "status": "completed", "conclusion": "success"}
        ],
    }
    with patch(
        "urllib.request.urlopen",
        side_effect=[
            _mock_resp(200, json.dumps(pr_data)),
            _mock_resp(200, json.dumps(runs_payload)),
        ],
    ):
        cp = github_api.pr_checks("owner/repo", 42, "tok")
    assert cp.returncode == 0
    result = json.loads(cp.stdout)
    assert result[0]["name"] == "CI"


def test_issue_list_with_label() -> None:
    issues = [{"number": 1, "title": "MVP thing", "labels": [{"name": "mvp"}]}]
    with patch(
        "urllib.request.urlopen", return_value=_mock_resp(200, json.dumps(issues))
    ):
        cp = github_api.issue_list("owner/repo", "tok", label="mvp")
    assert cp.returncode == 0
    assert len(json.loads(cp.stdout)) == 1


# ---------------------------------------------------------------------------
# project_workflows / enable_project_workflow
# ---------------------------------------------------------------------------


def _gql_resp(data: object) -> MagicMock:
    """GraphQL success response wrapping data under the 'data' key."""
    return _mock_resp(200, json.dumps({"data": data}))


def test_project_workflows_success() -> None:
    nodes = [
        {"id": "WF_1", "name": "Item closed", "enabled": False},
        {"id": "WF_2", "name": "Pull request merged", "enabled": True},
    ]
    payload = {"node": {"workflows": {"nodes": nodes}}}
    with patch("urllib.request.urlopen", return_value=_gql_resp(payload)):
        cp = github_api.project_workflows("PVT_abc", "tok")
    assert cp.returncode == 0
    result = json.loads(cp.stdout)
    assert len(result["workflows"]) == 2
    assert result["workflows"][0]["name"] == "Item closed"


def test_project_workflows_api_error() -> None:
    with patch("urllib.request.urlopen", side_effect=_http_error(403, "forbidden")):
        cp = github_api.project_workflows("PVT_abc", "tok")
    assert cp.returncode != 0


def test_project_workflows_bad_json() -> None:
    resp = _mock_resp(200, b"not json")
    with patch("urllib.request.urlopen", return_value=resp):
        cp = github_api.project_workflows("PVT_abc", "tok")
    assert cp.returncode != 0


def test_enable_project_workflow_success() -> None:
    workflow = {"id": "WF_1", "name": "Item closed", "enabled": True}
    payload = {"updateProjectV2WorkflowEnabled": {"projectV2Workflow": workflow}}
    with patch("urllib.request.urlopen", return_value=_gql_resp(payload)):
        cp = github_api.enable_project_workflow("WF_1", "tok", enabled=True)
    assert cp.returncode == 0
    assert json.loads(cp.stdout)["enabled"] is True


def test_enable_project_workflow_null_response() -> None:
    payload = {"updateProjectV2WorkflowEnabled": {"projectV2Workflow": None}}
    with patch("urllib.request.urlopen", return_value=_gql_resp(payload)):
        cp = github_api.enable_project_workflow("WF_1", "tok", enabled=True)
    assert cp.returncode != 0


def test_enable_project_workflow_api_error() -> None:
    with patch("urllib.request.urlopen", side_effect=_http_error(403, "forbidden")):
        cp = github_api.enable_project_workflow("WF_1", "tok", enabled=True)
    assert cp.returncode != 0


# ---------------------------------------------------------------------------
# pr_annotations
# ---------------------------------------------------------------------------


def test_pr_annotations_returns_flat_list() -> None:
    pr_payload = json.dumps({"head": {"sha": "abc123"}})
    runs_payload = json.dumps(
        {
            "check_runs": [
                {
                    "id": 1,
                    "name": "react",
                    "status": "completed",
                    "conclusion": "failure",
                }
            ]
        }
    )
    ann_payload = json.dumps(
        [
            {
                "annotation_level": "failure",
                "path": "src/App.tsx",
                "start_line": 10,
                "message": "'foo' is defined but never used",
            }
        ]
    )

    responses = [
        _mock_resp(200, pr_payload),
        _mock_resp(200, runs_payload),
        _mock_resp(200, ann_payload),
    ]
    with patch("urllib.request.urlopen", side_effect=responses):
        cp = github_api.pr_annotations("acme/repo", 42, "tok")
    assert cp.returncode == 0
    items = json.loads(cp.stdout)
    assert len(items) == 1
    assert items[0]["check_run"] == "react"
    assert items[0]["path"] == "src/App.tsx"
    assert items[0]["start_line"] == 10


def test_pr_annotations_pr_fetch_error() -> None:
    with patch("urllib.request.urlopen", side_effect=_http_error(404)):
        cp = github_api.pr_annotations("acme/repo", 99, "tok")
    assert cp.returncode != 0


def test_pr_annotations_no_annotations_returns_empty() -> None:
    pr_payload = json.dumps({"head": {"sha": "abc123"}})
    runs_payload = json.dumps({"check_runs": [{"id": 1, "name": "CI"}]})
    ann_payload = json.dumps([])

    responses = [
        _mock_resp(200, pr_payload),
        _mock_resp(200, runs_payload),
        _mock_resp(200, ann_payload),
    ]
    with patch("urllib.request.urlopen", side_effect=responses):
        cp = github_api.pr_annotations("acme/repo", 1, "tok")
    assert cp.returncode == 0
    assert json.loads(cp.stdout) == []


# ---------------------------------------------------------------------------
# pr_rerun
# ---------------------------------------------------------------------------


def test_pr_rerun_triggers_all_runs() -> None:
    pr_payload = json.dumps({"head": {"sha": "abc123"}})
    runs_payload = json.dumps({"workflow_runs": [{"id": 777}, {"id": 888}]})
    rerun_resp = _mock_resp(201, "")

    responses = [
        _mock_resp(200, pr_payload),
        _mock_resp(200, runs_payload),
        rerun_resp,
        rerun_resp,
    ]
    with patch("urllib.request.urlopen", side_effect=responses):
        cp = github_api.pr_rerun("acme/repo", 42, "tok")
    assert cp.returncode == 0
    result = json.loads(cp.stdout)
    assert set(result["triggered"]) == {777, 888}
    assert result["errors"] == []


def test_pr_rerun_failed_only_uses_correct_endpoint() -> None:
    pr_payload = json.dumps({"head": {"sha": "abc123"}})
    runs_payload = json.dumps({"workflow_runs": [{"id": 555}]})
    captured_urls: list[str] = []

    def _fake_urlopen(req: urllib.request.Request) -> MagicMock:
        captured_urls.append(req.full_url)
        return _mock_resp(201, "")

    responses_iter = iter([_mock_resp(200, pr_payload), _mock_resp(200, runs_payload)])

    def _side_effect(req: urllib.request.Request) -> MagicMock:
        try:
            return next(responses_iter)
        except StopIteration:
            return _fake_urlopen(req)

    with patch("urllib.request.urlopen", side_effect=_side_effect):
        cp = github_api.pr_rerun("acme/repo", 42, "tok", failed_only=True)
    assert cp.returncode == 0
    assert any("rerun-failed-jobs" in u for u in captured_urls)


def test_pr_rerun_no_runs_returns_error() -> None:
    pr_payload = json.dumps({"head": {"sha": "abc123"}})
    runs_payload = json.dumps({"workflow_runs": []})
    with patch(
        "urllib.request.urlopen",
        side_effect=[_mock_resp(200, pr_payload), _mock_resp(200, runs_payload)],
    ):
        cp = github_api.pr_rerun("acme/repo", 42, "tok")
    assert cp.returncode != 0
