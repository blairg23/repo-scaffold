"""OS-agnostic GitHub API client — no gh CLI required.

All operations use urllib (stdlib) + GH_TOKEN. Works on Windows, macOS, and
Linux without the gh binary installed.
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

_GITHUB_API = "https://api.github.com"
_GITHUB_GRAPHQL = "https://api.github.com/graphql"
_API_VERSION = "2022-11-28"


# ---------------------------------------------------------------------------
# Compatibility shim — callers expect subprocess.CompletedProcess[str]
# ---------------------------------------------------------------------------


def _ok(stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


def _err(stderr: str, returncode: int = 1) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout="", stderr=stderr
    )


# ---------------------------------------------------------------------------
# Token resolution
# ---------------------------------------------------------------------------


def resolve_token(env: dict[str, str] | None = None) -> str | None:
    src = env if env is not None else dict(os.environ)
    return src.get("GH_TOKEN") or src.get("GITHUB_TOKEN") or src.get("github_token")


# ---------------------------------------------------------------------------
# Low-level HTTP helpers
# ---------------------------------------------------------------------------


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": _API_VERSION,
        "Content-Type": "application/json",
    }


def _http(
    method: str,
    url: str,
    token: str,
    data: object = None,
) -> subprocess.CompletedProcess[str]:
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=_headers(token), method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return _ok(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8") if exc.fp else ""
        return _err(raw or str(exc), returncode=exc.code)
    except urllib.error.URLError as exc:
        return _err(str(exc))


def _parse_next_link(link_header: str) -> str | None:
    for part in link_header.split(","):
        segments = part.strip().split(";")
        if len(segments) < 2:
            continue
        url_part = segments[0].strip().strip("<>")
        if any('rel="next"' in seg for seg in segments[1:]):
            return url_part
    return None


# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------


def rest(
    method: str,
    endpoint: str,
    token: str,
    data: object = None,
) -> subprocess.CompletedProcess[str]:
    url = f"{_GITHUB_API}{endpoint}"
    return _http(method, url, token, data)


def rest_paginated(endpoint: str, token: str) -> subprocess.CompletedProcess[str]:
    """Fetch all pages and return concatenated JSON arrays."""
    all_items: list[object] = []
    url: str | None = f"{_GITHUB_API}{endpoint}"
    while url:
        req = urllib.request.Request(url, headers=_headers(token), method="GET")
        try:
            with urllib.request.urlopen(req) as resp:
                page = json.loads(resp.read().decode("utf-8"))
                if isinstance(page, list):
                    all_items.extend(page)
                link = resp.headers.get("Link", "")
                url = _parse_next_link(link)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8") if exc.fp else ""
            return _err(raw or str(exc), returncode=exc.code)
        except urllib.error.URLError as exc:
            return _err(str(exc))
    return _ok(json.dumps(all_items))


# ---------------------------------------------------------------------------
# GraphQL API
# ---------------------------------------------------------------------------


def graphql(
    query: str,
    variables: dict[str, object],
    token: str,
) -> subprocess.CompletedProcess[str]:
    cp = _http("POST", _GITHUB_GRAPHQL, token, {"query": query, "variables": variables})
    if cp.returncode != 0:
        return cp
    try:
        payload = json.loads(cp.stdout)
    except json.JSONDecodeError:
        return _err("GraphQL response was not valid JSON.")
    if "errors" in payload and "data" not in payload:
        msgs = "; ".join(e.get("message", "") for e in payload["errors"])
        return _err(msgs)
    return _ok(json.dumps(payload.get("data", {})))


# ---------------------------------------------------------------------------
# Owner ID (needed for project creation)
# ---------------------------------------------------------------------------

_GQL_OWNER_ID = """
query($login: String!) {
  user(login: $login) { id }
}
"""

_GQL_ORG_ID = """
query($login: String!) {
  organization(login: $login) { id }
}
"""


def resolve_owner_node_id(owner: str, token: str) -> str | None:
    cp = graphql(_GQL_OWNER_ID, {"login": owner}, token)
    if cp.returncode == 0:
        try:
            node_id = json.loads(cp.stdout).get("user", {}).get("id")
            if node_id:
                return node_id
        except (json.JSONDecodeError, AttributeError):
            pass
    cp = graphql(_GQL_ORG_ID, {"login": owner}, token)
    if cp.returncode == 0:
        try:
            node_id = json.loads(cp.stdout).get("organization", {}).get("id")
            if node_id:
                return node_id
        except (json.JSONDecodeError, AttributeError):
            pass
    return None


# ---------------------------------------------------------------------------
# GitHub Projects v2 — GraphQL queries / mutations
# ---------------------------------------------------------------------------

_GQL_LIST_PROJECTS = """
query($login: String!, $first: Int!, $after: String) {
  user(login: $login) {
    projectsV2(
      first: $first
      after: $after
      orderBy: {field: UPDATED_AT, direction: DESC}
    ) {
      pageInfo { hasNextPage endCursor }
      nodes {
        id number title closed url
        public shortDescription readme
      }
    }
  }
}
"""

_GQL_LIST_PROJECTS_ORG = """
query($login: String!, $first: Int!, $after: String) {
  organization(login: $login) {
    projectsV2(
      first: $first
      after: $after
      orderBy: {field: UPDATED_AT, direction: DESC}
    ) {
      pageInfo { hasNextPage endCursor }
      nodes {
        id number title closed url
        public shortDescription readme
      }
    }
  }
}
"""

_GQL_VIEW_PROJECT = """
query($login: String!, $number: Int!) {
  user(login: $login) {
    projectV2(number: $number) {
      id number title closed url
      public shortDescription readme
    }
  }
}
"""

_GQL_VIEW_PROJECT_ORG = """
query($login: String!, $number: Int!) {
  organization(login: $login) {
    projectV2(number: $number) {
      id number title closed url
      public shortDescription readme
    }
  }
}
"""

_GQL_CREATE_PROJECT = """
mutation($ownerId: ID!, $title: String!) {
  createProjectV2(input: {ownerId: $ownerId, title: $title}) {
    projectV2 {
      id number title closed url public shortDescription readme
    }
  }
}
"""

_GQL_UPDATE_PROJECT = """
mutation(
  $projectId: ID!
  $title: String
  $description: String
  $readme: String
  $public: Boolean
) {
  updateProjectV2(input: {
    projectId: $projectId
    title: $title
    shortDescription: $description
    readme: $readme
    public: $public
  }) {
    projectV2 {
      id number title closed url public shortDescription readme
    }
  }
}
"""

_GQL_DELETE_PROJECT = """
mutation($projectId: ID!) {
  deleteProjectV2(input: {projectId: $projectId}) {
    projectV2 { id }
  }
}
"""

_GQL_LIST_ITEMS = """
query($projectId: ID!, $first: Int!, $after: String) {
  node(id: $projectId) {
    ... on ProjectV2 {
      items(first: $first, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          content {
            ... on Issue {
              number title state url
              labels(first: 10) { nodes { name } }
              assignees(first: 5) { nodes { login } }
            }
            ... on PullRequest { number title state url }
            ... on DraftIssue { title body }
          }
        }
      }
    }
  }
}
"""

_GQL_ADD_ITEM = """
mutation($projectId: ID!, $contentId: ID!) {
  addProjectV2ItemById(input: {projectId: $projectId, contentId: $contentId}) {
    item { id }
  }
}
"""

_GQL_DELETE_ITEM = """
mutation($projectId: ID!, $itemId: ID!) {
  deleteProjectV2Item(input: {projectId: $projectId, itemId: $itemId}) {
    deletedItemId
  }
}
"""

_GQL_PROJECT_FIELDS = """
query($projectId: ID!) {
  node(id: $projectId) {
    ... on ProjectV2 {
      fields(first: 30) {
        nodes {
          ... on ProjectV2SingleSelectField {
            id
            name
            options { id name }
          }
        }
      }
    }
  }
}
"""

_GQL_UPDATE_ITEM_FIELD = """
mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $optionId: String!) {
  updateProjectV2ItemFieldValue(input: {
    projectId: $projectId
    itemId: $itemId
    fieldId: $fieldId
    value: { singleSelectOptionId: $optionId }
  }) {
    projectV2Item { id }
  }
}
"""

_GQL_UPDATE_STATUS_FIELD = """
mutation($fieldId: ID!, $options: [ProjectV2SingleSelectFieldOptionInput!]!) {
  updateProjectV2Field(input: {
    fieldId: $fieldId
    singleSelectOptions: $options
  }) {
    projectV2Field {
      ... on ProjectV2SingleSelectField {
        id name
        options { id name color description }
      }
    }
  }
}
"""

STANDARD_STATUS_OPTIONS: list[dict[str, str]] = [
    {"name": "Todo", "color": "GRAY", "description": "This item hasn't been started"},
    {
        "name": "In Progress",
        "color": "YELLOW",
        "description": "This is actively being worked on",
    },
    {"name": "To Review", "color": "BLUE", "description": "This is awaiting review"},
    {"name": "Done", "color": "GREEN", "description": "This has been completed"},
    {"name": "Won't Do", "color": "GRAY", "description": "This won't be done"},
    {"name": "Duplicate", "color": "BLUE", "description": "This is a duplicate"},
    {"name": "Blocked", "color": "RED", "description": "This is blocked"},
]


def _project_nodes_from_data(data: dict[str, object]) -> list[dict[str, object]]:
    for key in ("user", "organization"):
        owner_obj = data.get(key)
        if not isinstance(owner_obj, dict):
            continue
        projects = owner_obj.get("projectsV2")
        if not isinstance(projects, dict):
            continue
        nodes = projects.get("nodes")
        if isinstance(nodes, list):
            return [n for n in nodes if isinstance(n, dict)]
    return []


def _project_page_info(data: dict[str, object]) -> dict[str, object]:
    for key in ("user", "organization"):
        owner_obj = data.get(key)
        if not isinstance(owner_obj, dict):
            continue
        projects = owner_obj.get("projectsV2")
        if isinstance(projects, dict):
            pi = projects.get("pageInfo")
            if isinstance(pi, dict):
                return pi
    return {}


def project_list(
    owner: str,
    token: str,
    include_closed: bool = False,
) -> subprocess.CompletedProcess[str]:
    all_nodes: list[dict[str, object]] = []
    after: str | None = None

    for query in (_GQL_LIST_PROJECTS, _GQL_LIST_PROJECTS_ORG):
        after = None
        while True:
            cp = graphql(
                query,
                {"login": owner, "first": 100, "after": after},
                token,
            )
            if cp.returncode != 0:
                break
            try:
                data = json.loads(cp.stdout)
            except json.JSONDecodeError:
                break
            nodes = _project_nodes_from_data(data)
            all_nodes.extend(nodes)
            pi = _project_page_info(data)
            if pi.get("hasNextPage"):
                after = str(pi.get("endCursor", ""))
            else:
                break
        if all_nodes:
            break

    if not include_closed:
        all_nodes = [n for n in all_nodes if not n.get("closed", False)]

    payload = {"projects": all_nodes, "totalCount": len(all_nodes)}
    return _ok(json.dumps(payload))


def _project_from_graphql(data: dict[str, object]) -> dict[str, object] | None:
    for key in ("user", "organization"):
        owner_obj = data.get(key)
        if not isinstance(owner_obj, dict):
            continue
        proj = owner_obj.get("projectV2")
        if isinstance(proj, dict):
            return proj
    return None


def project_view(
    owner: str,
    number: int,
    token: str,
) -> subprocess.CompletedProcess[str]:
    for query in (_GQL_VIEW_PROJECT, _GQL_VIEW_PROJECT_ORG):
        cp = graphql(query, {"login": owner, "number": number}, token)
        if cp.returncode != 0:
            continue
        try:
            data = json.loads(cp.stdout)
        except json.JSONDecodeError:
            continue
        proj = _project_from_graphql(data)
        if proj:
            return _ok(json.dumps(proj))
    return _err(f"Project #{number} not found for owner {owner!r}.")


def project_create(
    owner: str,
    title: str,
    token: str,
) -> subprocess.CompletedProcess[str]:
    owner_id = resolve_owner_node_id(owner, token)
    if not owner_id:
        return _err(f"Could not resolve node ID for owner {owner!r}.")
    cp = graphql(_GQL_CREATE_PROJECT, {"ownerId": owner_id, "title": title}, token)
    if cp.returncode != 0:
        return cp
    try:
        data = json.loads(cp.stdout)
        proj = data.get("createProjectV2", {}).get("projectV2")
        if isinstance(proj, dict):
            return _ok(json.dumps(proj))
    except (json.JSONDecodeError, AttributeError):
        pass
    return _err("Unexpected response creating project.")


def project_edit(
    project_id: str,
    token: str,
    title: str | None = None,
    description: str | None = None,
    readme: str | None = None,
    visibility: str | None = None,
) -> subprocess.CompletedProcess[str]:
    public: bool | None = None
    if visibility is not None:
        public = visibility.upper() == "PUBLIC"
    cp = graphql(
        _GQL_UPDATE_PROJECT,
        {
            "projectId": project_id,
            "title": title,
            "description": description,
            "readme": readme,
            "public": public,
        },
        token,
    )
    if cp.returncode != 0:
        return cp
    try:
        data = json.loads(cp.stdout)
        proj = data.get("updateProjectV2", {}).get("projectV2")
        if isinstance(proj, dict):
            return _ok(json.dumps(proj))
    except (json.JSONDecodeError, AttributeError):
        pass
    return _err("Unexpected response updating project.")


def project_delete(project_id: str, token: str) -> subprocess.CompletedProcess[str]:
    return graphql(_GQL_DELETE_PROJECT, {"projectId": project_id}, token)


def project_item_list(
    project_id: str,
    token: str,
    limit: int = 100,
) -> subprocess.CompletedProcess[str]:
    all_items: list[dict[str, object]] = []
    after: str | None = None
    while len(all_items) < limit:
        fetch = min(100, limit - len(all_items))
        cp = graphql(
            _GQL_LIST_ITEMS,
            {"projectId": project_id, "first": fetch, "after": after},
            token,
        )
        if cp.returncode != 0:
            return cp
        try:
            data = json.loads(cp.stdout)
            node = data.get("node", {})
            if not isinstance(node, dict):
                break
            items_obj = node.get("items", {})
            nodes = items_obj.get("nodes", [])
            if isinstance(nodes, list):
                all_items.extend(n for n in nodes if isinstance(n, dict))
            pi = items_obj.get("pageInfo", {})
            if pi.get("hasNextPage"):
                after = str(pi.get("endCursor", ""))
            else:
                break
        except (json.JSONDecodeError, AttributeError):
            break
    payload = {"items": all_items, "totalCount": len(all_items)}
    return _ok(json.dumps(payload))


def project_fields(
    project_id: str,
    token: str,
) -> subprocess.CompletedProcess[str]:
    """Return single-select fields with their options for a project."""
    cp = graphql(_GQL_PROJECT_FIELDS, {"projectId": project_id}, token)
    if cp.returncode != 0:
        return cp
    try:
        data = json.loads(cp.stdout)
        nodes = data.get("node", {}).get("fields", {}).get("nodes", [])
        fields = [n for n in nodes if isinstance(n, dict) and "options" in n]
        return _ok(json.dumps({"fields": fields}))
    except (json.JSONDecodeError, AttributeError):
        return _err("Unexpected response fetching project fields.")


def project_item_update_field(
    project_id: str,
    item_id: str,
    field_id: str,
    option_id: str,
    token: str,
) -> subprocess.CompletedProcess[str]:
    return graphql(
        _GQL_UPDATE_ITEM_FIELD,
        {
            "projectId": project_id,
            "itemId": item_id,
            "fieldId": field_id,
            "optionId": option_id,
        },
        token,
    )


def setup_project_status_field(
    project_id: str,
    token: str,
    options: list[dict[str, str]] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Replace the Status single-select field options with the standard set.

    Options that already exist on the field are matched by name and their IDs
    are forwarded to the mutation so GitHub can update them in place rather than
    recreating them, which would clear any item field values pointing at the old
    option IDs.
    """
    cp = project_fields(project_id, token)
    if cp.returncode != 0:
        return cp
    try:
        fields = json.loads(cp.stdout).get("fields", [])
    except (json.JSONDecodeError, AttributeError):
        return _err("Could not parse project fields.")

    status_field = next(
        (f for f in fields if isinstance(f, dict) and f.get("name") == "Status"),
        None,
    )
    if status_field is None:
        return _err("No Status field found on project.")

    field_id = status_field.get("id", "")
    if not field_id:
        return _err("Could not resolve Status field ID.")

    existing_ids: dict[str, str] = {
        o["name"]: o["id"]
        for o in status_field.get("options", [])
        if isinstance(o, dict) and "name" in o and "id" in o
    }

    chosen = options if options is not None else STANDARD_STATUS_OPTIONS
    merged: list[dict[str, str]] = []
    for opt in chosen:
        entry: dict[str, str] = {
            "name": opt["name"],
            "color": opt["color"],
            "description": opt.get("description", ""),
        }
        existing_id = existing_ids.get(opt["name"])
        if existing_id:
            entry["id"] = existing_id
        merged.append(entry)

    cp2 = graphql(
        _GQL_UPDATE_STATUS_FIELD,
        {"fieldId": field_id, "options": merged},
        token,
    )
    if cp2.returncode != 0:
        return cp2
    try:
        data = json.loads(cp2.stdout)
        field = data.get("updateProjectV2Field", {}).get("projectV2Field") or {}
        return _ok(json.dumps(field))
    except (json.JSONDecodeError, AttributeError):
        return _err("Unexpected response setting status field options.")


def project_item_add(
    project_id: str,
    content_node_id: str,
    token: str,
) -> subprocess.CompletedProcess[str]:
    cp = graphql(
        _GQL_ADD_ITEM,
        {"projectId": project_id, "contentId": content_node_id},
        token,
    )
    if cp.returncode != 0:
        return cp
    try:
        data = json.loads(cp.stdout)
        item = data.get("addProjectV2ItemById", {}).get("item")
        if isinstance(item, dict):
            return _ok(json.dumps(item))
    except (json.JSONDecodeError, AttributeError):
        pass
    return _err("Unexpected response adding project item.")


def project_item_delete(
    project_id: str,
    item_id: str,
    token: str,
) -> subprocess.CompletedProcess[str]:
    return graphql(
        _GQL_DELETE_ITEM, {"projectId": project_id, "itemId": item_id}, token
    )


# ---------------------------------------------------------------------------
# Link project to repository
# ---------------------------------------------------------------------------

_GQL_LINK_PROJECT = """
mutation($projectId: ID!, $repositoryId: ID!) {
  linkProjectV2ToRepository(input: {projectId: $projectId, repositoryId: $repositoryId}) {
    repository { id }
  }
}
"""

_GQL_REPO_NODE_ID = """
query($owner: String!, $repo: String!) {
  repository(owner: $owner, name: $repo) { id }
}
"""


def repo_node_id(owner: str, repo: str, token: str) -> str | None:
    cp = graphql(_GQL_REPO_NODE_ID, {"owner": owner, "repo": repo}, token)
    if cp.returncode != 0:
        return None
    try:
        return json.loads(cp.stdout).get("repository", {}).get("id")
    except (json.JSONDecodeError, AttributeError):
        return None


def link_project_to_repository(
    project_id: str,
    owner: str,
    repo: str,
    token: str,
) -> subprocess.CompletedProcess[str]:
    repository_id = repo_node_id(owner, repo, token)
    if not repository_id:
        return _err(f"Could not resolve node ID for repository {owner}/{repo}.")
    return graphql(
        _GQL_LINK_PROJECT,
        {"projectId": project_id, "repositoryId": repository_id},
        token,
    )


# ---------------------------------------------------------------------------
# Issue node ID (needed for adding issues to projects)
# ---------------------------------------------------------------------------

_GQL_ISSUE_OR_PR_NODE_ID = """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    issue(number: $number) { id }
    pullRequest(number: $number) { id }
  }
}
"""


def issue_node_id(owner: str, repo: str, number: int, token: str) -> str | None:
    cp = graphql(
        _GQL_ISSUE_OR_PR_NODE_ID,
        {"owner": owner, "repo": repo, "number": number},
        token,
    )
    if cp.returncode != 0:
        return None
    try:
        repo_data = json.loads(cp.stdout).get("repository", {})
        issue = repo_data.get("issue") or {}
        pr = repo_data.get("pullRequest") or {}
        return issue.get("id") or pr.get("id")
    except (json.JSONDecodeError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# Repo creation
# ---------------------------------------------------------------------------


def repo_create(
    owner: str,
    name: str,
    token: str,
    visibility: str = "private",
) -> subprocess.CompletedProcess[str]:
    """Create a GitHub repo via REST API. Returns the created repo JSON."""
    is_org = _is_org(owner, token)
    if is_org:
        endpoint = f"/orgs/{owner}/repos"
    else:
        endpoint = "/user/repos"

    payload = {
        "name": name,
        "private": visibility.lower() != "public",
        "auto_init": False,
    }
    return rest("POST", endpoint, token, payload)


def _is_org(owner: str, token: str) -> bool:
    cp = rest("GET", f"/users/{owner}", token)
    if cp.returncode != 0:
        return False
    try:
        data = json.loads(cp.stdout)
        return data.get("type", "").lower() == "organization"
    except (json.JSONDecodeError, AttributeError):
        return False


# ---------------------------------------------------------------------------
# Auth validation (replaces gh auth status)
# ---------------------------------------------------------------------------


def validate_token(token: str) -> bool:
    """Return True if the token can reach the GitHub API."""
    cp = rest("GET", "/user", token)
    return cp.returncode == 0


def get_authenticated_login(token: str) -> str | None:
    cp = rest("GET", "/user", token)
    if cp.returncode != 0:
        return None
    try:
        return json.loads(cp.stdout).get("login")
    except (json.JSONDecodeError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# Token resolution from repo .env files
# ---------------------------------------------------------------------------


def _load_env_file(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not path.exists():
        return result
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().rstrip("\r")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        result[key] = value
    return result


# ---------------------------------------------------------------------------
# Issues
# ---------------------------------------------------------------------------


def issue_create(
    repo: str,
    title: str,
    token: str,
    body: str = "",
    labels: list[str] | None = None,
    assignees: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    payload: dict[str, object] = {"title": title, "body": body}
    if labels:
        payload["labels"] = labels
    if assignees:
        payload["assignees"] = assignees
    return rest("POST", f"/repos/{repo}/issues", token, payload)


def issue_list(
    repo: str,
    token: str,
    state: str = "open",
    label: str | None = None,
) -> subprocess.CompletedProcess[str]:
    qs = f"state={state}&per_page=100"
    if label:
        qs += f"&labels={urllib.parse.quote(label, safe='')}"
    return rest_paginated(f"/repos/{repo}/issues?{qs}", token)


def issue_close(repo: str, number: int, token: str) -> subprocess.CompletedProcess[str]:
    return rest("PATCH", f"/repos/{repo}/issues/{number}", token, {"state": "closed"})


def issue_update(
    repo: str,
    number: int,
    token: str,
    title: str | None = None,
    body: str | None = None,
    state: str | None = None,
) -> subprocess.CompletedProcess[str]:
    payload: dict[str, object] = {}
    if title is not None:
        payload["title"] = title
    if body is not None:
        payload["body"] = body
    if state is not None:
        payload["state"] = state
    return rest("PATCH", f"/repos/{repo}/issues/{number}", token, payload)


def issue_comment(
    repo: str, number: int, body: str, token: str
) -> subprocess.CompletedProcess[str]:
    return rest(
        "POST", f"/repos/{repo}/issues/{number}/comments", token, {"body": body}
    )


_GQL_ADD_REACTION = """
mutation($subjectId: ID!, $content: ReactionContent!) {
  addReaction(input: {subjectId: $subjectId, content: $content}) {
    reaction { content }
  }
}
"""


def react_by_node_id(
    node_id: str, content: str, token: str
) -> subprocess.CompletedProcess[str]:
    """Add a reaction to any reactionable GitHub object by its GraphQL node ID."""
    return graphql(_GQL_ADD_REACTION, {"subjectId": node_id, "content": content}, token)


def react(
    repo: str,
    subject: str,
    subject_id: int,
    content: str,
    token: str,
) -> subprocess.CompletedProcess[str]:
    """Add a reaction via REST. subject: 'issue_comment' or 'pull_request_review_comment'."""
    if subject == "issue_comment":
        endpoint = f"/repos/{repo}/issues/comments/{subject_id}/reactions"
    elif subject == "pull_request_review_comment":
        endpoint = f"/repos/{repo}/pulls/comments/{subject_id}/reactions"
    else:
        return _err(f"Use react_by_node_id() for subject: {subject}")
    return rest("POST", endpoint, token, {"content": content})


def issue_label(
    repo: str,
    number: int,
    token: str,
    add: list[str] | None = None,
    remove: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    if add:
        cp = rest("POST", f"/repos/{repo}/issues/{number}/labels", token, add)
        if cp.returncode not in (0, 200):
            return cp
    for label in remove or []:
        encoded = urllib.parse.quote(label, safe="")
        cp = rest("DELETE", f"/repos/{repo}/issues/{number}/labels/{encoded}", token)
        if cp.returncode not in (0, 200, 204):
            return cp
    return _ok("{}")


def issue_assign(
    repo: str,
    number: int,
    token: str,
    add: list[str] | None = None,
    remove: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    if add:
        cp = rest(
            "POST",
            f"/repos/{repo}/issues/{number}/assignees",
            token,
            {"assignees": add},
        )
        if cp.returncode not in (0, 200, 201):
            return cp
    if remove:
        cp = rest(
            "DELETE",
            f"/repos/{repo}/issues/{number}/assignees",
            token,
            {"assignees": remove},
        )
        if cp.returncode not in (0, 200, 204):
            return cp
    return _ok("{}")


# ---------------------------------------------------------------------------
# Pull requests
# ---------------------------------------------------------------------------

_GQL_RESOLVE_THREAD = """
mutation($threadId: ID!) {
  resolveReviewThread(input: {threadId: $threadId}) {
    thread { id isResolved }
  }
}
"""

_GQL_PR_REVIEW_THREADS = """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      reviewThreads(first: 50) {
        nodes { id isResolved }
      }
    }
  }
}
"""


def pr_list(repo: str, token: str) -> subprocess.CompletedProcess[str]:
    return rest_paginated(f"/repos/{repo}/pulls?state=open&per_page=100", token)


def pr_view(repo: str, number: int, token: str) -> subprocess.CompletedProcess[str]:
    return rest("GET", f"/repos/{repo}/pulls/{number}", token)


def pr_list_comments(
    repo: str, number: int, token: str
) -> subprocess.CompletedProcess[str]:
    return rest_paginated(f"/repos/{repo}/pulls/{number}/comments?per_page=100", token)


def pr_comment(
    repo: str,
    number: int,
    body: str,
    token: str,
    reply_to: int | None = None,
) -> subprocess.CompletedProcess[str]:
    if reply_to is not None:
        # Reply to an existing inline review comment
        return rest(
            "POST",
            f"/repos/{repo}/pulls/{number}/comments/{reply_to}/replies",
            token,
            {"body": body},
        )
    # Regular PR timeline comment (visible in the conversation tab)
    return rest(
        "POST", f"/repos/{repo}/issues/{number}/comments", token, {"body": body}
    )


def pr_review_threads(
    owner: str, repo: str, number: int, token: str
) -> subprocess.CompletedProcess[str]:
    return graphql(
        _GQL_PR_REVIEW_THREADS,
        {"owner": owner, "repo": repo, "number": number},
        token,
    )


def pr_resolve_thread(thread_id: str, token: str) -> subprocess.CompletedProcess[str]:
    cp = graphql(_GQL_RESOLVE_THREAD, {"threadId": thread_id}, token)
    if cp.returncode != 0:
        return cp
    try:
        thread = json.loads(cp.stdout).get("resolveReviewThread", {}).get("thread", {})
        return _ok(json.dumps(thread))
    except (json.JSONDecodeError, AttributeError):
        return _err("Unexpected response resolving thread.")


def pr_create(
    repo: str,
    title: str,
    body: str,
    head: str,
    base: str,
    token: str,
    draft: bool = False,
) -> subprocess.CompletedProcess[str]:
    payload: dict[str, object] = {
        "title": title,
        "body": body,
        "head": head,
        "base": base,
        "draft": draft,
    }
    return rest("POST", f"/repos/{repo}/pulls", token, payload)


def pr_update(
    repo: str,
    pr_number: int,
    token: str,
    title: str | None = None,
    body: str | None = None,
) -> subprocess.CompletedProcess[str]:
    payload: dict[str, object] = {}
    if title is not None:
        payload["title"] = title
    if body is not None:
        payload["body"] = body
    return rest("PATCH", f"/repos/{repo}/pulls/{pr_number}", token, payload)


def branch_get_sha(repo: str, branch: str, token: str) -> str | None:
    """Return the HEAD SHA of a branch, or None if not found."""
    cp = rest("GET", f"/repos/{repo}/git/refs/heads/{branch}", token)
    if cp.returncode != 0:
        return None
    try:
        data = json.loads(cp.stdout)
        # API returns a list when using prefix match; exact name is first entry
        entry = data[0] if isinstance(data, list) else data
        return entry.get("object", {}).get("sha")
    except (json.JSONDecodeError, IndexError, AttributeError):
        return None


def branch_create(
    repo: str,
    name: str,
    token: str,
    base: str = "main",
) -> subprocess.CompletedProcess[str]:
    sha = branch_get_sha(repo, base, token)
    if not sha:
        return _err(f"Could not resolve SHA for base branch '{base}'.")
    return rest(
        "POST",
        f"/repos/{repo}/git/refs",
        token,
        {"ref": f"refs/heads/{name}", "sha": sha},
    )


def branch_delete(
    repo: str,
    name: str,
    token: str,
) -> subprocess.CompletedProcess[str]:
    return rest("DELETE", f"/repos/{repo}/git/refs/heads/{name}", token)


def pr_merge(
    repo: str,
    pr_number: int,
    token: str,
    method: str = "squash",
) -> subprocess.CompletedProcess[str]:
    return rest(
        "PUT",
        f"/repos/{repo}/pulls/{pr_number}/merge",
        token,
        {"merge_method": method},
    )


def pr_checks(
    repo: str,
    pr_number: int,
    token: str,
) -> subprocess.CompletedProcess[str]:
    """Return check-run statuses for a PR's head commit."""
    cp = rest("GET", f"/repos/{repo}/pulls/{pr_number}", token)
    if cp.returncode != 0:
        return cp
    try:
        sha = json.loads(cp.stdout)["head"]["sha"]
    except (json.JSONDecodeError, KeyError):
        return _err("Could not extract head SHA from PR.")
    cp2 = rest("GET", f"/repos/{repo}/commits/{sha}/check-runs?per_page=100", token)
    if cp2.returncode != 0:
        return cp2
    try:
        payload = json.loads(cp2.stdout)
        items = payload.get("check_runs", []) if isinstance(payload, dict) else payload
        # Deduplicate by name, keeping latest by id
        seen: dict[str, dict[str, object]] = {}
        for run in items:
            n = str(run.get("name", ""))
            run_id = run.get("id", 0)
            seen_id = seen[n].get("id", 0) if n in seen else 0
            if n not in seen or (
                isinstance(run_id, int)
                and isinstance(seen_id, int)
                and run_id > seen_id
            ):
                seen[n] = run
        return _ok(json.dumps(list(seen.values())))
    except (json.JSONDecodeError, AttributeError):
        return _err("Unexpected response from check-runs API.")


def token_from_repo(repo_dir: Path) -> str | None:
    """Try to resolve a GH token from the repo .env and environment."""
    env = dict(os.environ)
    for env_file in (repo_dir / ".env", Path.cwd() / ".env"):
        for k, v in _load_env_file(env_file).items():
            env.setdefault(k, v)
    return resolve_token(env)
