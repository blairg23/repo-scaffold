from __future__ import annotations

import json
import subprocess
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


def test_issue_add_sub_issue_surfaces_parent_error() -> None:
    """addSubIssue null + errors (child already has a parent) surfaces the GH message."""
    node_resp = json.dumps({"data": {"repository": {"issue": {"id": "I_x"}}}})
    partial_resp = json.dumps(
        {
            "data": {"addSubIssue": None},
            "errors": [{"message": "Sub issue may only have one parent"}],
        }
    )
    responses = [
        _mock_resp(200, node_resp),  # resolve parent node ID
        _mock_resp(200, node_resp),  # resolve child node ID
        _mock_resp(200, partial_resp),  # addSubIssue mutation
    ]
    with patch("urllib.request.urlopen", side_effect=responses):
        cp = github_api.issue_add_sub_issue("owner", "repo", 1, 2, "tok")
    assert cp.returncode != 0
    assert "only have one parent" in cp.stderr


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
# issue_sync_hierarchy
# ---------------------------------------------------------------------------


def test_issue_sync_hierarchy_dry_run() -> None:
    issues = [
        {"number": 1, "labels": [{"name": "epic"}, {"name": "epic:foo"}]},
        {"number": 2, "labels": [{"name": "epic:foo"}]},
        {"number": 3, "labels": [{"name": "epic:foo"}]},
        {"number": 4, "labels": []},
    ]
    with patch.object(
        github_api,
        "issue_list",
        return_value=subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(issues), stderr=""
        ),
    ):
        with patch.object(github_api, "graphql") as mock_graphql:

            def _parent_response(query: str, variables: dict, token: str) -> object:
                number = variables["number"]
                if number == 2:
                    parent = {"number": 1}
                else:
                    parent = None
                return subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=json.dumps({"repository": {"issue": {"parent": parent}}}),
                    stderr="",
                )

            mock_graphql.side_effect = _parent_response
            cp = github_api.issue_sync_hierarchy("owner/repo", "tok", apply=False)

    assert cp.returncode == 0
    report = json.loads(cp.stdout)
    assert report["already_linked"] == [{"epic": 1, "child": 2}]
    assert report["would_link"] == [{"epic": 1, "child": 3}]
    assert report["unaffiliated"] == [4]
    assert report["ambiguous"] == []


def test_issue_sync_hierarchy_ambiguous_group() -> None:
    issues = [
        {"number": 1, "labels": [{"name": "epic"}, {"name": "epic:foo"}]},
        {"number": 2, "labels": [{"name": "epic"}, {"name": "epic:foo"}]},
    ]
    with patch.object(
        github_api,
        "issue_list",
        return_value=subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(issues), stderr=""
        ),
    ):
        cp = github_api.issue_sync_hierarchy("owner/repo", "tok", apply=False)

    assert cp.returncode == 0
    report = json.loads(cp.stdout)
    assert len(report["ambiguous"]) == 1
    assert report["ambiguous"][0]["slug"] == "foo"


def test_issue_sync_hierarchy_apply_links_unparented_child() -> None:
    issues = [
        {"number": 1, "labels": [{"name": "epic"}, {"name": "epic:foo"}]},
        {"number": 2, "labels": [{"name": "epic:foo"}]},
    ]
    with patch.object(
        github_api,
        "issue_list",
        return_value=subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(issues), stderr=""
        ),
    ):
        with patch.object(
            github_api,
            "graphql",
            return_value=subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=json.dumps({"repository": {"issue": {"parent": None}}}),
                stderr="",
            ),
        ):
            with patch.object(
                github_api,
                "issue_add_sub_issue",
                return_value=subprocess.CompletedProcess(
                    args=[], returncode=0, stdout="{}", stderr=""
                ),
            ) as mock_link:
                cp = github_api.issue_sync_hierarchy("owner/repo", "tok", apply=True)

    assert cp.returncode == 0
    report = json.loads(cp.stdout)
    assert report["linked"] == [{"epic": 1, "child": 2}]
    mock_link.assert_called_once_with("owner", "repo", 1, 2, "tok")


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


# ---------------------------------------------------------------------------
# pr_reviews
# ---------------------------------------------------------------------------


def test_pr_reviews_returns_reviewer_state() -> None:
    payload = json.dumps(
        [
            {
                "id": 1,
                "user": {"login": "alice"},
                "state": "APPROVED",
                "submitted_at": "2026-06-01T12:00:00Z",
                "body": "LGTM",
            },
            {
                "id": 2,
                "user": {"login": "bob"},
                "state": "CHANGES_REQUESTED",
                "submitted_at": "2026-06-02T08:00:00Z",
                "body": "Please fix the types.",
            },
        ]
    )
    with patch("urllib.request.urlopen", return_value=_mock_resp(200, payload)):
        cp = github_api.pr_reviews("acme/repo", 42, "tok")
    assert cp.returncode == 0
    reviews = json.loads(cp.stdout)
    assert len(reviews) == 2
    assert reviews[0]["user"] == "alice"
    assert reviews[0]["state"] == "APPROVED"
    assert reviews[1]["user"] == "bob"
    assert reviews[1]["state"] == "CHANGES_REQUESTED"


def test_pr_reviews_empty_returns_empty_list() -> None:
    with patch("urllib.request.urlopen", return_value=_mock_resp(200, "[]")):
        cp = github_api.pr_reviews("acme/repo", 99, "tok")
    assert cp.returncode == 0
    assert json.loads(cp.stdout) == []


def test_pr_reviews_api_error_propagates() -> None:
    with patch("urllib.request.urlopen", side_effect=_http_error(404, "Not Found")):
        cp = github_api.pr_reviews("acme/repo", 0, "tok")
    assert cp.returncode != 0


# ---------------------------------------------------------------------------
# pr_list_comments -- merges inline review comments and general conversation comments
# ---------------------------------------------------------------------------


def test_pr_list_comments_merges_both_endpoints() -> None:
    review_comments = [
        {"id": 1, "user": {"login": "alice"}, "body": "nit", "path": "foo.py", "created_at": "2026-01-01T00:00:00Z"},
    ]
    issue_comments = [
        {"id": 2, "user": {"login": "bob"}, "body": "lgtm", "created_at": "2026-01-02T00:00:00Z"},
    ]
    with patch(
        "urllib.request.urlopen",
        side_effect=[
            _mock_resp(200, json.dumps(review_comments)),
            _mock_resp(200, json.dumps(issue_comments)),
        ],
    ):
        cp = github_api.pr_list_comments("acme/repo", 42, "tok")
    assert cp.returncode == 0
    result = json.loads(cp.stdout)
    assert len(result) == 2
    logins = {c["user"]["login"] for c in result}
    assert logins == {"alice", "bob"}


def test_pr_list_comments_sorted_by_created_at() -> None:
    review_comments = [
        {"id": 1, "user": {"login": "alice"}, "body": "later", "path": "foo.py", "created_at": "2026-01-03T00:00:00Z"},
    ]
    issue_comments = [
        {"id": 2, "user": {"login": "bob"}, "body": "earlier", "created_at": "2026-01-01T00:00:00Z"},
    ]
    with patch(
        "urllib.request.urlopen",
        side_effect=[
            _mock_resp(200, json.dumps(review_comments)),
            _mock_resp(200, json.dumps(issue_comments)),
        ],
    ):
        cp = github_api.pr_list_comments("acme/repo", 42, "tok")
    assert cp.returncode == 0
    result = json.loads(cp.stdout)
    assert result[0]["user"]["login"] == "bob"
    assert result[1]["user"]["login"] == "alice"


def test_pr_list_comments_review_endpoint_error_propagates() -> None:
    with patch("urllib.request.urlopen", side_effect=_http_error(404, "Not Found")):
        cp = github_api.pr_list_comments("acme/repo", 42, "tok")
    assert cp.returncode != 0


def test_pr_list_comments_issue_endpoint_error_propagates() -> None:
    review_comments: list[dict[str, object]] = []
    with patch(
        "urllib.request.urlopen",
        side_effect=[
            _mock_resp(200, json.dumps(review_comments)),
            _http_error(403, "Forbidden"),
        ],
    ):
        cp = github_api.pr_list_comments("acme/repo", 42, "tok")
    assert cp.returncode != 0


# ---------------------------------------------------------------------------
# issue_label -- auto-remove needs-triage when epic:slug is added
# ---------------------------------------------------------------------------


def test_issue_label_epic_slug_removes_needs_triage() -> None:
    responses = [
        _mock_resp(200, "[]"),  # POST add epic:foo
        _mock_resp(204, ""),  # DELETE needs-triage
    ]
    with patch("urllib.request.urlopen", side_effect=responses) as m:
        cp = github_api.issue_label("acme/repo", 1, "tok", add=["epic:foo"])
    assert cp.returncode == 0
    urls = [c.args[0].full_url for c in m.call_args_list]
    assert any("needs-triage" in u for u in urls)


def test_issue_label_non_epic_does_not_remove_needs_triage() -> None:
    with patch("urllib.request.urlopen", return_value=_mock_resp(200, "[]")) as m:
        cp = github_api.issue_label("acme/repo", 1, "tok", add=["bug"])
    assert cp.returncode == 0
    assert all("needs-triage" not in str(c) for c in m.call_args_list)


def test_issue_label_404_on_needs_triage_delete_is_ignored() -> None:
    responses = [
        _mock_resp(200, "[]"),  # POST add epic:foo
        _http_error(
            404, "Label not found"
        ),  # DELETE needs-triage -- label not on issue
    ]
    with patch("urllib.request.urlopen", side_effect=responses):
        cp = github_api.issue_label("acme/repo", 1, "tok", add=["epic:foo"])
    assert cp.returncode == 0


def test_issue_label_epic_slug_does_not_duplicate_explicit_remove() -> None:
    responses = [
        _mock_resp(200, "[]"),  # POST add epic:foo
        _mock_resp(
            204, ""
        ),  # DELETE needs-triage (from explicit remove, not duplicated)
    ]
    with patch("urllib.request.urlopen", side_effect=responses) as m:
        cp = github_api.issue_label(
            "acme/repo", 1, "tok", add=["epic:foo"], remove=["needs-triage"]
        )
    assert cp.returncode == 0
    delete_calls = [c for c in m.call_args_list if c.args[0].method == "DELETE"]
    assert len(delete_calls) == 1


def test_issue_label_remove_only_makes_no_post() -> None:
    with patch("urllib.request.urlopen", return_value=_mock_resp(204, "")) as m:
        cp = github_api.issue_label("acme/repo", 1, "tok", remove=["stale"])
    assert cp.returncode == 0
    post_calls = [c for c in m.call_args_list if c.args[0].method == "POST"]
    assert len(post_calls) == 0


# ---------------------------------------------------------------------------
# Repo labels
# ---------------------------------------------------------------------------


def test_label_list_success() -> None:
    payload = json.dumps([{"name": "bug", "color": "ee0701"}])
    with patch("urllib.request.urlopen", return_value=_mock_resp(200, payload)):
        cp = github_api.label_list("acme/repo", "tok")
    assert cp.returncode == 0
    assert json.loads(cp.stdout)[0]["name"] == "bug"


def test_label_list_api_error_propagates() -> None:
    with patch("urllib.request.urlopen", side_effect=_http_error(404, "Not Found")):
        cp = github_api.label_list("acme/repo", "tok")
    assert cp.returncode != 0


def test_label_create_success() -> None:
    payload = json.dumps({"name": "bug", "color": "ee0701"})
    with patch("urllib.request.urlopen", return_value=_mock_resp(201, payload)) as m:
        cp = github_api.label_create(
            "acme/repo", "bug", "#ee0701", "tok", "Bug reports"
        )
    assert cp.returncode == 0
    sent_body = json.loads(m.call_args[0][0].data)
    assert sent_body == {"name": "bug", "color": "ee0701", "description": "Bug reports"}


def test_label_create_api_error_propagates() -> None:
    with patch(
        "urllib.request.urlopen", side_effect=_http_error(422, "already_exists")
    ):
        cp = github_api.label_create("acme/repo", "bug", "ee0701", "tok")
    assert cp.returncode != 0


def test_label_delete_success() -> None:
    with patch("urllib.request.urlopen", return_value=_mock_resp(204, "")):
        cp = github_api.label_delete("acme/repo", "good first issue", "tok")
    assert cp.returncode == 0


def test_label_delete_api_error_propagates() -> None:
    with patch("urllib.request.urlopen", side_effect=_http_error(404, "Not Found")):
        cp = github_api.label_delete("acme/repo", "bug", "tok")
    assert cp.returncode != 0


def test_label_apply_preset_creates_missing_labels() -> None:
    existing = json.dumps([{"name": "needs-triage"}])
    created_payload = json.dumps({"name": "x"})
    responses = [_mock_resp(200, existing)] + [
        _mock_resp(201, created_payload) for _ in github_api.STANDARD_LABELS[1:]
    ]
    with patch("urllib.request.urlopen", side_effect=responses):
        cp = github_api.label_apply_preset("acme/repo", "tok")
    assert cp.returncode == 0
    result = json.loads(cp.stdout)
    assert "needs-triage" not in result["created"]
    assert len(result["created"]) == len(github_api.STANDARD_LABELS) - 1
    assert result["skipped"] == 1


def test_label_apply_preset_list_error_propagates() -> None:
    with patch("urllib.request.urlopen", side_effect=_http_error(500, "boom")):
        cp = github_api.label_apply_preset("acme/repo", "tok")
    assert cp.returncode != 0


def test_label_apply_preset_unparseable_labels_returns_err() -> None:
    with patch("urllib.request.urlopen", return_value=_mock_resp(200, "not json")):
        cp = github_api.label_apply_preset("acme/repo", "tok")
    assert cp.returncode != 0
    assert "Failed to parse" in cp.stderr


def test_label_apply_preset_create_error_propagates() -> None:
    existing = json.dumps([])
    with patch(
        "urllib.request.urlopen",
        side_effect=[_mock_resp(200, existing), _http_error(422, "boom")],
    ):
        cp = github_api.label_apply_preset("acme/repo", "tok")
    assert cp.returncode != 0


# ---------------------------------------------------------------------------
# issue_remove_sub_issue
# ---------------------------------------------------------------------------


def test_issue_remove_sub_issue_success() -> None:
    node_id_resp = {"repository": {"issue": {"id": "I_child"}}}
    parent_id_resp = {"repository": {"issue": {"id": "I_parent"}}}
    remove_resp = {
        "removeSubIssue": {
            "issue": {"id": "I_parent", "number": 1},
            "subIssue": {"id": "I_child", "number": 2},
        }
    }
    with patch.object(
        github_api,
        "graphql",
        side_effect=[
            subprocess.CompletedProcess(
                args=[], returncode=0, stdout=json.dumps(parent_id_resp), stderr=""
            ),
            subprocess.CompletedProcess(
                args=[], returncode=0, stdout=json.dumps(node_id_resp), stderr=""
            ),
            subprocess.CompletedProcess(
                args=[], returncode=0, stdout=json.dumps(remove_resp), stderr=""
            ),
        ],
    ):
        cp = github_api.issue_remove_sub_issue("owner", "repo", 1, 2, "tok")
    assert cp.returncode == 0
    result = json.loads(cp.stdout)
    assert "issue" in result


def test_issue_remove_sub_issue_null_response() -> None:
    node_id_resp = {"repository": {"issue": {"id": "I_child"}}}
    parent_id_resp = {"repository": {"issue": {"id": "I_parent"}}}
    remove_resp = {"removeSubIssue": None}
    with patch.object(
        github_api,
        "graphql",
        side_effect=[
            subprocess.CompletedProcess(
                args=[], returncode=0, stdout=json.dumps(parent_id_resp), stderr=""
            ),
            subprocess.CompletedProcess(
                args=[], returncode=0, stdout=json.dumps(node_id_resp), stderr=""
            ),
            subprocess.CompletedProcess(
                args=[], returncode=0, stdout=json.dumps(remove_resp), stderr=""
            ),
        ],
    ):
        cp = github_api.issue_remove_sub_issue("owner", "repo", 1, 2, "tok")
    assert cp.returncode != 0
    assert "null" in cp.stderr.lower() or "unexpected" in cp.stderr.lower()


# ---------------------------------------------------------------------------
# project_all_fields
# ---------------------------------------------------------------------------


def test_project_all_fields_returns_all_types() -> None:
    fields = [
        {"id": "F_title", "name": "Title", "dataType": "TITLE"},
        {
            "id": "F_status",
            "name": "Status",
            "dataType": "SINGLE_SELECT",
            "options": [],
        },
        {"id": "F_labels", "name": "Labels", "dataType": "LABELS"},
        {"id": "F_parent", "name": "Parent issue", "dataType": "PARENT_ISSUE"},
    ]
    payload = {"node": {"fields": {"nodes": fields}}}
    with patch("urllib.request.urlopen", return_value=_gql_resp(payload)):
        cp = github_api.project_all_fields("PVT_x", "tok")
    assert cp.returncode == 0
    result = json.loads(cp.stdout)
    assert len(result["fields"]) == 4
    dtypes = {f["dataType"] for f in result["fields"]}
    assert "LABELS" in dtypes
    assert "PARENT_ISSUE" in dtypes


# ---------------------------------------------------------------------------
# project_views
# ---------------------------------------------------------------------------


def test_project_views_returns_list() -> None:
    views = [
        {"id": "PVV_1", "name": "Kanban Board", "layout": "BOARD_LAYOUT"},
        {"id": "PVV_2", "name": "Progress View", "layout": "TABLE_LAYOUT"},
    ]
    payload = {"node": {"views": {"nodes": views}}}
    with patch("urllib.request.urlopen", return_value=_gql_resp(payload)):
        cp = github_api.project_views("PVT_x", "tok")
    assert cp.returncode == 0
    result = json.loads(cp.stdout)
    assert len(result["views"]) == 2
    names = {v["name"] for v in result["views"]}
    assert "Kanban Board" in names
    assert "Progress View" in names


def test_issue_remove_sub_issue_parent_not_found() -> None:
    with patch.object(github_api, "_resolve_issue_node_id", return_value=None):
        cp = github_api.issue_remove_sub_issue("owner", "repo", 1, 2, "tok")
    assert cp.returncode != 0
    assert "parent" in cp.stderr.lower()


def test_issue_remove_sub_issue_graphql_failure() -> None:
    with patch.object(
        github_api, "_resolve_issue_node_id", side_effect=["I_parent", "I_child"]
    ):
        with patch.object(
            github_api, "graphql", return_value=github_api._err("GQL error")
        ):
            cp = github_api.issue_remove_sub_issue("owner", "repo", 1, 2, "tok")
    assert cp.returncode != 0


def test_issue_remove_sub_issue_bad_json() -> None:
    with patch.object(
        github_api, "_resolve_issue_node_id", side_effect=["I_parent", "I_child"]
    ):
        with patch.object(
            github_api, "graphql", return_value=github_api._ok("not-json{")
        ):
            cp = github_api.issue_remove_sub_issue("owner", "repo", 1, 2, "tok")
    assert cp.returncode != 0


def test_issue_remove_sub_issue_child_not_found() -> None:
    with patch.object(
        github_api, "_resolve_issue_node_id", side_effect=["I_parent", None]
    ):
        cp = github_api.issue_remove_sub_issue("owner", "repo", 1, 2, "tok")
    assert cp.returncode != 0
    assert "child" in cp.stderr.lower()


def test_project_all_fields_graphql_error() -> None:
    with patch.object(github_api, "graphql", return_value=github_api._err("API error")):
        cp = github_api.project_all_fields("PVT_x", "tok")
    assert cp.returncode != 0


def test_project_all_fields_unexpected_response() -> None:
    with patch.object(github_api, "graphql", return_value=github_api._ok("not-json")):
        cp = github_api.project_all_fields("PVT_x", "tok")
    assert cp.returncode != 0


def test_project_views_graphql_error() -> None:
    with patch.object(github_api, "graphql", return_value=github_api._err("API error")):
        cp = github_api.project_views("PVT_x", "tok")
    assert cp.returncode != 0


def test_project_views_unexpected_response() -> None:
    with patch.object(github_api, "graphql", return_value=github_api._ok("not-json")):
        cp = github_api.project_views("PVT_x", "tok")
    assert cp.returncode != 0


# ---------------------------------------------------------------------------
# pr_check_sop
# ---------------------------------------------------------------------------


def _make_thread(
    thread_id: str,
    is_resolved: bool,
    num_comments: int,
    first_has_thumbs_up: bool,
    reply_author: str = "agent",
) -> dict:
    reactions = (
        [{"content": "THUMBS_UP", "user": {"login": "me"}}]
        if first_has_thumbs_up
        else []
    )
    comments = [
        {
            "databaseId": 100,
            "author": {"login": "reviewer"},
            "body": "Please fix this.",
            "reactions": {"nodes": reactions},
        }
    ]
    for i in range(1, num_comments):
        comments.append(
            {
                "databaseId": 100 + i,
                "author": {"login": reply_author},
                "body": f"Fixed in abc1234. Change #{i}.",
                "reactions": {"nodes": []},
            }
        )
    return {
        "id": thread_id,
        "isResolved": is_resolved,
        "comments": {"nodes": comments},
    }


def _sop_resp(
    threads: list, has_next_page: bool = False, end_cursor: str | None = None
) -> str:
    return json.dumps(
        {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {
                            "hasNextPage": has_next_page,
                            "endCursor": end_cursor,
                        },
                        "nodes": threads,
                    }
                }
            }
        }
    )


def test_pr_check_sop_all_compliant() -> None:
    thread = _make_thread(
        "PRRT_1", is_resolved=True, num_comments=2, first_has_thumbs_up=True
    )
    with patch.object(
        github_api, "graphql", return_value=github_api._ok(_sop_resp([thread]))
    ):
        cp = github_api.pr_check_sop("acme", "repo", 1, "tok")
    assert cp.returncode == 0
    report = json.loads(cp.stdout)
    assert len(report) == 1
    assert report[0]["compliant"] is True
    assert report[0]["missing"] == []


def test_pr_check_sop_missing_reply() -> None:
    thread = _make_thread(
        "PRRT_2", is_resolved=True, num_comments=1, first_has_thumbs_up=True
    )
    with patch.object(
        github_api, "graphql", return_value=github_api._ok(_sop_resp([thread]))
    ):
        cp = github_api.pr_check_sop("acme", "repo", 2, "tok")
    assert cp.returncode == 0
    report = json.loads(cp.stdout)
    assert report[0]["compliant"] is False
    assert "reply" in report[0]["missing"]


def test_pr_check_sop_not_resolved() -> None:
    thread = _make_thread(
        "PRRT_3", is_resolved=False, num_comments=2, first_has_thumbs_up=True
    )
    with patch.object(
        github_api, "graphql", return_value=github_api._ok(_sop_resp([thread]))
    ):
        cp = github_api.pr_check_sop("acme", "repo", 3, "tok")
    assert cp.returncode == 0
    report = json.loads(cp.stdout)
    assert report[0]["compliant"] is False
    assert "resolved" in report[0]["missing"]


def test_pr_check_sop_missing_reaction() -> None:
    thread = _make_thread(
        "PRRT_4", is_resolved=True, num_comments=2, first_has_thumbs_up=False
    )
    with patch.object(
        github_api, "graphql", return_value=github_api._ok(_sop_resp([thread]))
    ):
        cp = github_api.pr_check_sop("acme", "repo", 4, "tok")
    assert cp.returncode == 0
    report = json.loads(cp.stdout)
    assert report[0]["compliant"] is False
    assert "reaction(+1)" in report[0]["missing"]


def test_pr_check_sop_all_missing() -> None:
    thread = _make_thread(
        "PRRT_5", is_resolved=False, num_comments=1, first_has_thumbs_up=False
    )
    with patch.object(
        github_api, "graphql", return_value=github_api._ok(_sop_resp([thread]))
    ):
        cp = github_api.pr_check_sop("acme", "repo", 5, "tok")
    assert cp.returncode == 0
    report = json.loads(cp.stdout)
    missing = set(report[0]["missing"])
    assert missing == {"reply", "resolved", "reaction(+1)"}


def test_pr_check_sop_empty_pr() -> None:
    with patch.object(
        github_api, "graphql", return_value=github_api._ok(_sop_resp([]))
    ):
        cp = github_api.pr_check_sop("acme", "repo", 6, "tok")
    assert cp.returncode == 0
    assert json.loads(cp.stdout) == []


def test_pr_check_sop_api_error_propagates() -> None:
    with patch.object(
        github_api, "graphql", return_value=github_api._err("GraphQL error")
    ):
        cp = github_api.pr_check_sop("acme", "repo", 7, "tok")
    assert cp.returncode != 0


def test_pr_check_sop_reply_same_author_not_compliant() -> None:
    thread = _make_thread(
        "PRRT_8",
        is_resolved=True,
        num_comments=2,
        first_has_thumbs_up=True,
        reply_author="reviewer",  # same author as thread opener -- not a valid SOP reply
    )
    with patch.object(
        github_api, "graphql", return_value=github_api._ok(_sop_resp([thread]))
    ):
        cp = github_api.pr_check_sop("acme", "repo", 8, "tok")
    assert cp.returncode == 0
    report = json.loads(cp.stdout)
    assert report[0]["has_reply"] is False
    assert "reply" in report[0]["missing"]


def test_pr_check_sop_paginates_all_threads() -> None:
    page1_thread = _make_thread(
        "PRRT_P1", is_resolved=True, num_comments=2, first_has_thumbs_up=True
    )
    page2_thread = _make_thread(
        "PRRT_P2", is_resolved=False, num_comments=1, first_has_thumbs_up=False
    )
    responses = iter(
        [
            github_api._ok(
                _sop_resp([page1_thread], has_next_page=True, end_cursor="cursor1")
            ),
            github_api._ok(_sop_resp([page2_thread], has_next_page=False)),
        ]
    )
    with patch.object(
        github_api, "graphql", side_effect=lambda *_a, **_kw: next(responses)
    ):
        cp = github_api.pr_check_sop("acme", "repo", 9, "tok")
    assert cp.returncode == 0
    report = json.loads(cp.stdout)
    assert len(report) == 2
    assert report[0]["thread_id"] == "PRRT_P1"
    assert report[0]["compliant"] is True
    assert report[1]["thread_id"] == "PRRT_P2"
    assert report[1]["compliant"] is False
