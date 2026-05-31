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
