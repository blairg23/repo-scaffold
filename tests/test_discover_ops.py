"""Unit tests for discover_ops."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from repo_scaffold import discover_ops


class TestDeviceFlowAuth:
    def test_success_after_pending(self, monkeypatch: pytest.MonkeyPatch) -> None:
        responses = iter(
            [
                {
                    "device_code": "DC",
                    "user_code": "ABCD-1234",
                    "verification_uri": "https://github.com/login/device",
                    "interval": 1,
                    "expires_in": 900,
                },
                {"error": "authorization_pending"},
                {"access_token": "gho_real"},
            ]
        )
        monkeypatch.setattr(
            discover_ops, "_post_form", lambda url, data: next(responses)
        )
        monkeypatch.setattr(time, "sleep", lambda _: None)
        monkeypatch.setattr("builtins.print", lambda *a, **kw: None)

        assert discover_ops.device_flow_auth("client_id") == "gho_real"

    def test_slow_down_increases_interval(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        intervals: list[float] = []
        responses = iter(
            [
                {
                    "device_code": "D",
                    "user_code": "U",
                    "verification_uri": "https://x",
                    "interval": 2,
                    "expires_in": 900,
                },
                {"error": "slow_down"},
                {"access_token": "tok"},
            ]
        )
        monkeypatch.setattr(
            discover_ops, "_post_form", lambda url, data: next(responses)
        )
        monkeypatch.setattr(time, "sleep", intervals.append)
        monkeypatch.setattr("builtins.print", lambda *a, **kw: None)

        discover_ops.device_flow_auth("cid")
        assert intervals == [2, 7]

    def test_access_denied_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        responses = iter(
            [
                {
                    "device_code": "D",
                    "user_code": "U",
                    "verification_uri": "https://x",
                    "interval": 1,
                    "expires_in": 900,
                },
                {"error": "access_denied", "error_description": "User denied"},
            ]
        )
        monkeypatch.setattr(
            discover_ops, "_post_form", lambda url, data: next(responses)
        )
        monkeypatch.setattr(time, "sleep", lambda _: None)
        monkeypatch.setattr("builtins.print", lambda *a, **kw: None)

        with pytest.raises(RuntimeError, match="access_denied"):
            discover_ops.device_flow_auth("cid")

    def test_timeout_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            discover_ops,
            "_post_form",
            lambda url, data: {
                "device_code": "D",
                "user_code": "U",
                "verification_uri": "https://x",
                "interval": 1,
                "expires_in": 1,
            },
        )
        call_n = [0]

        def fake_monotonic() -> float:
            call_n[0] += 1
            return 0.0 if call_n[0] == 1 else 1e9

        monkeypatch.setattr(time, "monotonic", fake_monotonic)
        monkeypatch.setattr(time, "sleep", lambda _: None)
        monkeypatch.setattr(discover_ops, "_DEVICE_TIMEOUT", 100)
        monkeypatch.setattr("builtins.print", lambda *a, **kw: None)

        with pytest.raises(RuntimeError, match="timed out"):
            discover_ops.device_flow_auth("cid")


class TestUpsertEnvVar:
    def test_creates_new_file(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        discover_ops.upsert_env_var("GH_TOKEN", "tok123", env_file)
        assert "GH_TOKEN=tok123" in env_file.read_text()

    def test_appends_to_existing(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=bar\n")
        discover_ops.upsert_env_var("GH_TOKEN", "tok123", env_file)
        content = env_file.read_text()
        assert "FOO=bar" in content
        assert "GH_TOKEN=tok123" in content

    def test_replaces_existing_key(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("GH_TOKEN=old\nFOO=bar\n")
        discover_ops.upsert_env_var("GH_TOKEN", "new", env_file)
        content = env_file.read_text()
        assert "GH_TOKEN=new" in content
        assert "GH_TOKEN=old" not in content
        assert "FOO=bar" in content

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        env_file = tmp_path / "nested" / "dir" / ".env"
        discover_ops.upsert_env_var("KEY", "val", env_file)
        assert env_file.exists()


class TestDiscoverRepos:
    def test_returns_sorted_full_names(self, monkeypatch: pytest.MonkeyPatch) -> None:
        repos = [{"full_name": "acme/beta"}, {"full_name": "acme/alpha"}]
        monkeypatch.setattr(discover_ops, "_get_paginated", lambda token, url: repos)
        assert discover_ops.discover_repos("tok") == ["acme/alpha", "acme/beta"]

    def test_uses_user_repos_url_by_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: list[str] = []
        monkeypatch.setattr(
            discover_ops,
            "_get_paginated",
            lambda token, url: captured.append(url) or [{"full_name": "a/b"}],
        )
        discover_ops.discover_repos("tok")
        assert "/user/repos" in captured[0]

    def test_uses_org_url_when_org_given(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: list[str] = []
        monkeypatch.setattr(
            discover_ops,
            "_get_paginated",
            lambda token, url: captured.append(url) or [{"full_name": "acme/x"}],
        )
        discover_ops.discover_repos("tok", org="acme")
        assert "/orgs/acme/repos" in captured[0]

    def test_filters_items_without_full_name(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repos = [{"full_name": "ok/repo"}, {"id": 99}, {"full_name": ""}]
        monkeypatch.setattr(discover_ops, "_get_paginated", lambda token, url: repos)
        assert discover_ops.discover_repos("tok") == ["ok/repo"]


class TestParseNext:
    def test_extracts_next_url(self) -> None:
        link = '<https://api.github.com/user/repos?page=2>; rel="next", <https://api.github.com/user/repos?page=5>; rel="last"'
        assert (
            discover_ops._parse_next(link) == "https://api.github.com/user/repos?page=2"
        )

    def test_returns_empty_when_no_next(self) -> None:
        link = '<https://api.github.com/user/repos?page=1>; rel="prev"'
        assert discover_ops._parse_next(link) == ""

    def test_returns_empty_for_empty_string(self) -> None:
        assert discover_ops._parse_next("") == ""
