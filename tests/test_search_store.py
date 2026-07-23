from __future__ import annotations

from pathlib import Path

from workeventagent.search_store import build_search_documents, search_workspace


PROJECT = """---
project_id: search-project
title: Search Project
doc_kind: work_project
created: 2026-07-04
updated: 2026-07-04
---

# Search Project

## Current Snapshot

## Work Map
### Item: Retrieval Trust <!-- item:retrieval-trust -->
- background: user needs to find archived blockers later
#### Task: KV cache blocker search <!-- task:kv-cache-search -->
- status: in_progress
- next_action: Build deterministic search
- last_event_id: event-1

## Decisions

## Attachments

## Timeline
- 2026-07-04T12:00:00+00:00 <!-- event:event-1 -->
  - task_id: kv-cache-search
  - input: Looked at KV cache routing
  - summary: Fixed KV cache blocker notes
  - status: in_progress
  - next_action: Build deterministic search

## Daily / Weekly Rollups
"""


def test_search_finds_task_title_timeline_and_item_background(tmp_path: Path) -> None:
    (tmp_path / "project.md").write_text(PROJECT, encoding="utf-8")
    docs = build_search_documents(tmp_path)

    kinds = {d["kind"] for d in docs}

    assert {"project", "item", "task", "timeline"} <= kinds
    assert search_workspace(tmp_path, "KV cache blocker")[0]["kind"] in {"task", "timeline"}
    assert search_workspace(tmp_path, "archived blockers later")[0]["kind"] == "item"


def test_search_reads_report_files(tmp_path: Path) -> None:
    reports = tmp_path / "reports" / "daily"
    reports.mkdir(parents=True)
    (reports / "2026-07-04.md").write_text("# Daily\n\nInference chain summary", encoding="utf-8")

    results = search_workspace(tmp_path, "Inference chain")

    assert results[0]["kind"] == "report"


def test_search_finds_task_conclusion(tmp_path: Path) -> None:
    project = PROJECT.replace(
        "- next_action: Build deterministic search\n",
        "- next_action: Build deterministic search\n"
        "- conclusion: Prefix reuse is stable under concurrency\n",
        1,
    )
    (tmp_path / "project.md").write_text(project, encoding="utf-8")

    results = search_workspace(tmp_path, "stable under concurrency")

    assert results
    assert results[0]["kind"] == "task"
    assert results[0]["task_id"] == "kv-cache-search"
