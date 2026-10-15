"""Regression tests for #343: printing non-ASCII GitHub text on a non-UTF-8 stdout.

On Windows, Python encodes a piped or redirected stdout with the locale code page
(usually cp1252) and a strict error handler, so ``issue view`` on any templated
issue crashed on the emoji heading in its body. These tests stand in for that
console with a ``TextIOWrapper`` over a ``BytesIO``.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from repo_scaffold.backlog_ops import IssueDetail
from repo_scaffold.cli import _is_utf8, _make_stdio_unicode_safe, main

TEMPLATED_BODY = (
    "## \U0001f9fe Title\nSet up SOPS + age secrets\n\n## \U0001f9e0 Summary\n"
)


class _TtyBytesIO(io.BytesIO):
    def isatty(self) -> bool:
        return True


def _stream(
    encoding: str, errors: str = "strict", tty: bool = False
) -> io.TextIOWrapper:
    raw = _TtyBytesIO() if tty else io.BytesIO()
    return io.TextIOWrapper(raw, encoding=encoding, errors=errors, newline="\n")


def _written(stream: io.TextIOWrapper) -> bytes:
    stream.flush()
    raw = stream.buffer
    assert isinstance(raw, io.BytesIO)
    return raw.getvalue()


@pytest.fixture(autouse=True)
def _no_pythonioencoding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)


def test_issue_view_prints_emoji_body_on_cp1252_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    stdout = _stream("cp1252")
    stderr = _stream("cp1252", errors="backslashreplace")
    monkeypatch.setattr("sys.stdout", stdout)
    monkeypatch.setattr("sys.stderr", stderr)
    fake_issue = IssueDetail(
        number=3,
        title="Set up SOPS + age secrets with per-machine keys",
        body=TEMPLATED_BODY,
        state="open",
        labels=["ticket"],
        assignees=[],
    )
    monkeypatch.setattr(
        "repo_scaffold.cli.fetch_issue",
        lambda repo_dir, repo, issue_number: fake_issue,
    )

    rc = main(["issue", "view", "--repo", "acme/repo", "--issue-number", "3"])

    assert rc == 0
    output = _written(stdout).decode("utf-8")
    assert "Issue #3: Set up SOPS + age secrets with per-machine keys" in output
    assert "## \U0001f9fe Title" in output
    assert "## \U0001f9e0 Summary" in output
    assert stdout.encoding == "utf-8"
    assert stderr.encoding == "utf-8"


def test_pipe_or_file_is_switched_to_utf8_with_replace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stdout = _stream("cp1252")
    monkeypatch.setattr("sys.stdout", stdout)

    _make_stdio_unicode_safe()

    assert stdout.encoding == "utf-8"
    assert stdout.errors == "replace"
    stdout.write("\U0001f9fe café")
    assert _written(stdout) == "\U0001f9fe café".encode()


def test_utf8_stream_is_left_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    stdout = _stream("UTF-8")
    monkeypatch.setattr("sys.stdout", stdout)

    _make_stdio_unicode_safe()

    assert stdout.encoding == "UTF-8"
    assert stdout.errors == "strict"


def test_tty_keeps_console_encoding_and_replaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stdout = _stream("cp1252", tty=True)
    monkeypatch.setattr("sys.stdout", stdout)

    _make_stdio_unicode_safe()

    assert stdout.encoding == "cp1252"
    assert stdout.errors == "replace"
    stdout.write("\U0001f9fe café")
    assert _written(stdout) == "? café".encode("cp1252")


@pytest.mark.parametrize("errors", ["strict", "surrogateescape", "surrogatepass"])
def test_explicit_pythonioencoding_keeps_encoding_and_replaces(
    monkeypatch: pytest.MonkeyPatch, errors: str
) -> None:
    monkeypatch.setenv("PYTHONIOENCODING", f"cp1252:{errors}")
    stdout = _stream("cp1252", errors=errors)
    monkeypatch.setattr("sys.stdout", stdout)

    _make_stdio_unicode_safe()

    assert stdout.encoding == "cp1252"
    assert stdout.errors == "replace"
    stdout.write("\U0001f9fe café")
    assert _written(stdout) == "? café".encode("cp1252")


def test_tty_with_raising_non_strict_handler_is_normalized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stdout = _stream("cp1252", errors="surrogateescape", tty=True)
    monkeypatch.setattr("sys.stdout", stdout)

    _make_stdio_unicode_safe()

    assert stdout.errors == "replace"


def test_explicit_non_strict_error_handler_is_respected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTHONIOENCODING", "cp1252:backslashreplace")
    stdout = _stream("cp1252", errors="backslashreplace")
    monkeypatch.setattr("sys.stdout", stdout)

    _make_stdio_unicode_safe()

    assert stdout.encoding == "cp1252"
    assert stdout.errors == "backslashreplace"


def test_errors_only_pythonioencoding_still_switches_to_utf8(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTHONIOENCODING", ":strict")
    stdout = _stream("cp1252")
    monkeypatch.setattr("sys.stdout", stdout)

    _make_stdio_unicode_safe()

    assert stdout.encoding == "utf-8"


def test_streams_that_cannot_be_reconfigured_are_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plain = io.StringIO()
    closed = _stream("cp1252")
    closed.close()
    monkeypatch.setattr("sys.stdout", plain)
    monkeypatch.setattr("sys.stderr", None)

    _make_stdio_unicode_safe()

    plain.write("\U0001f9fe")
    assert plain.getvalue() == "\U0001f9fe"

    monkeypatch.setattr("sys.stdout", closed)
    monkeypatch.setattr("sys.stderr", closed)

    _make_stdio_unicode_safe()

    assert closed.closed


@pytest.mark.parametrize(
    ("encoding", "expected"),
    [
        ("utf-8", True),
        ("UTF8", True),
        ("cp1252", False),
        (None, False),
        ("nope", False),
    ],
)
def test_is_utf8(encoding: str | None, expected: bool) -> None:
    assert _is_utf8(encoding) is expected
