from __future__ import annotations

import json
from pathlib import Path

from repo_scaffold.backlog_import import build_backlog_import_file


def test_build_backlog_import_file_parses_epics_and_tickets(tmp_path: Path) -> None:
    source_dir = tmp_path / "tickets" / "platform"
    source_dir.mkdir(parents=True)

    (source_dir / "epic.md").write_text(
        """---
name: Epic
key: PLATFORM
labels: ["epic", "needs-triage"]
assignees: ["blairg23"]
---

## 🧾 Title

Platform foundation

## 🧠 Summary

Establish baseline workflows and repository defaults.
""",
        encoding="utf-8",
    )
    (source_dir / "ticket-ci.md").write_text(
        """---
name: Ticket
labels: ["needs-triage"]
assignees: ["blairg23"]
priority: P1
---

## 🧾 Title

Add CI pipeline

## 🧠 Summary

Run formatting, typing, and tests in CI.
""",
        encoding="utf-8",
    )

    output_file = tmp_path / "backlog" / "issues.json"
    scaffold_file, summary = build_backlog_import_file(
        source_dir=source_dir.parent,
        output_file=output_file,
    )

    payload = json.loads(scaffold_file.content)
    assert summary.files_scanned == 2
    assert summary.epics_imported == 1
    assert summary.tickets_imported == 1
    assert summary.synthetic_epics == 0
    assert summary.epics_skipped_existing == 0
    assert summary.tickets_skipped_existing == 0
    assert scaffold_file.path == output_file.resolve()
    assert payload["epics"][0]["key"] == "PLATFORM"
    assert payload["epics"][0]["title"] == "Platform foundation"
    assert payload["epics"][0]["assignees"] == ["blairg23"]
    assert "Establish baseline workflows" in payload["epics"][0]["body"]
    assert payload["epics"][0]["tickets"][0]["title"] == "Add CI pipeline"
    assert payload["epics"][0]["tickets"][0]["priority"] == "P1"
    assert payload["epics"][0]["tickets"][0]["labels"] == [
        "ticket",
        "epic:PLATFORM",
        "needs-triage",
    ]


def test_build_backlog_import_file_parses_unquoted_bracket_front_matter_lists(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "tickets" / "platform"
    source_dir.mkdir(parents=True)

    (source_dir / "epic.md").write_text(
        """---
name: Epic
key: PLATFORM
labels: [epic, needs-triage]
assignees: [blairg23]
---

# Platform foundation
""",
        encoding="utf-8",
    )
    (source_dir / "ticket.md").write_text(
        """---
name: Ticket
labels: [needs-triage]
assignees: [blairg23]
priority: P1
---

# Add CI pipeline
""",
        encoding="utf-8",
    )

    scaffold_file, summary = build_backlog_import_file(
        source_dir=source_dir.parent,
        output_file=tmp_path / "artifacts" / "backlog" / "issues.json",
    )

    payload = json.loads(scaffold_file.content)
    assert summary.epics_imported == 1
    assert summary.tickets_imported == 1
    assert payload["epics"][0]["labels"] == ["epic", "needs-triage"]
    assert payload["epics"][0]["assignees"] == ["blairg23"]
    assert payload["epics"][0]["tickets"][0]["labels"] == [
        "ticket",
        "epic:PLATFORM",
        "needs-triage",
    ]
    assert payload["epics"][0]["tickets"][0]["assignees"] == ["blairg23"]


def test_build_backlog_import_file_creates_synthetic_epic_for_unmapped_tickets(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "tickets"
    source_dir.mkdir(parents=True)
    (source_dir / "backup-db.md").write_text(
        """# Backup database

## 🧠 Summary

Create a reliable database backup flow.
""",
        encoding="utf-8",
    )

    scaffold_file, summary = build_backlog_import_file(
        source_dir=source_dir,
        output_file=tmp_path / "backlog" / "issues.json",
    )

    payload = json.loads(scaffold_file.content)
    assert summary.files_scanned == 1
    assert summary.epics_imported == 1
    assert summary.tickets_imported == 1
    assert summary.synthetic_epics == 1
    assert summary.epics_skipped_existing == 0
    assert summary.tickets_skipped_existing == 0
    assert payload["epics"][0]["key"] == "TICKETS"
    assert payload["epics"][0]["title"] == "Tickets - Imported backlog"
    assert payload["epics"][0]["tickets"][0]["title"] == "Backup database"
    assert payload["epics"][0]["tickets"][0]["labels"] == [
        "ticket",
        "epic:TICKETS",
    ]


def test_build_backlog_import_file_skips_existing_epics_and_tickets(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "tickets" / "platform"
    source_dir.mkdir(parents=True)
    (source_dir / "epic.md").write_text(
        """---
name: Epic
key: PLATFORM
---

## 🧾 Title

Platform foundation
""",
        encoding="utf-8",
    )
    (source_dir / "ticket-existing.md").write_text(
        """---
name: Ticket
---

## 🧾 Title

Add CI pipeline
""",
        encoding="utf-8",
    )
    (source_dir / "ticket-new.md").write_text(
        """---
name: Ticket
---

## 🧾 Title

Ship release notes
""",
        encoding="utf-8",
    )

    output_file = tmp_path / "artifacts" / "backlog" / "issues.json"
    output_file.parent.mkdir(parents=True)
    output_file.write_text(
        json.dumps(
            {
                "epics": [
                    {
                        "key": "PLATFORM",
                        "title": "Platform foundation",
                        "body": "## Summary\nExisting epic.\n",
                        "labels": ["epic"],
                        "assignees": [],
                        "tickets": [
                            {
                                "title": "Add CI pipeline",
                                "body": "## Summary\nExisting ticket.\n",
                                "labels": ["ticket", "epic:PLATFORM"],
                                "assignees": [],
                            }
                        ],
                    }
                ]
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    scaffold_file, summary = build_backlog_import_file(
        source_dir=source_dir.parent,
        output_file=output_file,
    )

    payload = json.loads(scaffold_file.content)
    assert summary.epics_imported == 0
    assert summary.tickets_imported == 1
    assert summary.synthetic_epics == 0
    assert summary.epics_skipped_existing == 1
    assert summary.tickets_skipped_existing == 1
    assert len(payload["epics"]) == 1
    assert [ticket["title"] for ticket in payload["epics"][0]["tickets"]] == [
        "Add CI pipeline",
        "Ship release notes",
    ]
