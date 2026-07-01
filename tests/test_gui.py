import json
import re
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from workeventagent.gui import (
    _event_id_timestamp,
    _parse_timeline_events,
    _parse_work_map_tasks,
    _parse_attachments_task_ids,
    _generate_init_markdown,
    handle_propose,
    handle_commit,
    handle_projects,
    handle_tasks,
    handle_timeline,
    handle_init,
)
from workeventagent.index_store import get_task

FIXTURE = Path("tests/fixtures/multimodal-labeling.md")

# ── Synthetic project with timeline events ───────────────

_SYNTHETIC_WITH_TIMELINE = """---
project_id: test-proj
title: Test Project
doc_kind: work_project
created: 2026-07-01
updated: 2026-07-01
---

# Test Project

## Current Snapshot



## Work Map

### Item: Item A <!-- item:item-a -->

#### Task: Task One <!-- task:task-one -->
- status: in_progress
- next_action: Keep going.
- last_event_id: ev2

#### Task: Task Two <!-- task:task-two -->
- status: done
- next_action: 
- last_event_id: ev1

## Decisions



## Attachments

- 2026-07-01T10:00:00+00:00
  - path: attachments/task-one/img.png
  - related_task_id: task-one
  - note: Screenshot.

## Timeline

- 2026-07-01T11:00:00+00:00 <!-- event:ev2 -->
  - task_id: task-one
  - input: Still working on task one.
  - summary: Making progress.
  - status: in_progress
  - next_action: Continue tomorrow.

- 2026-07-01T10:00:00+00:00 <!-- event:ev1 -->
  - task_id: task-two
  - input: Finished task two.
  - summary: Done with task two.
  - status: done
  - next_action: 

## Daily / Weekly Rollups


"""


# ── Helpers ──────────────────────────────────────────────

def _make_mock_proposal_data(overrides=None):
    data = {
        "target": {
            "project_id": "multimodal-labeling",
            "item_id": "kv-cache-few-shot",
            "task_id": "kv-cache-blockers",
            "task_title": "",
            "new_item": False,
            "new_task": False,
        },
        "confidence": 0.91,
        "reason": "Matched KV cache item.",
        "event": {
            "event_id": "20260629-153000123-kv-cache-blockers",
            "task_id": "kv-cache-blockers",
            "input_text": "Reviewed blockers.",
            "summary": "Prefix reuse strategy is unclear.",
            "status": "in_progress",
            "next_action": "Map current inference chain.",
        },
        "attachment_paths": [],
    }
    if overrides:
        _deep_update(data, overrides)
    return data


def _deep_update(d, u):
    for k, v in u.items():
        if isinstance(v, dict) and isinstance(d.get(k), dict):
            _deep_update(d[k], v)
        else:
            d[k] = v


# ── Unit tests for parsers ──────────────────────────────

class TimelineParserTest(unittest.TestCase):
    def test_parses_timeline_events_from_synthetic(self):
        """Fixture uses real append layout: newest (ev2) on top, oldest (ev1) below."""
        events = _parse_timeline_events(_SYNTHETIC_WITH_TIMELINE)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["event_id"], "ev2")
        self.assertEqual(events[0]["summary"], "Making progress.")
        self.assertEqual(events[1]["event_id"], "ev1")
        self.assertEqual(events[1]["task_id"], "task-two")

    def test_empty_timeline_returns_empty(self):
        text = "---\nproject_id: test\n---\n## Timeline\n\n## Other\n"
        events = _parse_timeline_events(text)
        self.assertEqual(events, [])

    def test_fixture_timeline_is_empty(self):
        """The actual fixture has an empty Timeline section — parser handles this."""
        text = FIXTURE.read_text(encoding="utf-8")
        events = _parse_timeline_events(text)
        self.assertEqual(events, [])


class WorkMapParserTest(unittest.TestCase):
    def test_parses_tasks_from_fixture(self):
        text = FIXTURE.read_text(encoding="utf-8")
        tasks = _parse_work_map_tasks(text)
        self.assertGreaterEqual(len(tasks), 2)
        task_ids = {t["task_id"] for t in tasks}
        self.assertIn("kv-cache-blockers", task_ids)
        self.assertIn("kv-cache-fundamentals", task_ids)


class AttachmentsTaskIdsTest(unittest.TestCase):
    def test_extracts_task_ids_from_attachments(self):
        text = FIXTURE.read_text(encoding="utf-8")
        task_ids = _parse_attachments_task_ids(text)
        self.assertIn("kv-cache-blockers", task_ids)

    def test_extracts_task_ids_from_synthetic(self):
        task_ids = _parse_attachments_task_ids(_SYNTHETIC_WITH_TIMELINE)
        self.assertIn("task-one", task_ids)
        self.assertNotIn("task-two", task_ids)


class EventIdTimestampTest(unittest.TestCase):
    def test_extracts_timestamp_prefix_simple(self):
        self.assertEqual(
            _event_id_timestamp("20260701-153000123-kv-cache-blockers"),
            "20260701-153000123",
        )

    def test_extracts_timestamp_prefix_with_hyphenated_task_id(self):
        self.assertEqual(
            _event_id_timestamp("20260629-153000123-kv-cache-blockers-2"),
            "20260629-153000123",
        )

    def test_extracts_timestamp_prefix_short(self):
        self.assertEqual(
            _event_id_timestamp("20260701-153000123"),
            "20260701-153000123",
        )


# ── propose ─────────────────────────────────────────────

class ProposeTest(unittest.TestCase):
    @patch("workeventagent.gui.run_archivist")
    def test_propose_returns_valid_proposal(self, run_archivist):
        run_archivist.return_value = """
{
  "target": {"project_id": "multimodal-labeling", "item_id": "kv-cache-few-shot", "task_id": "kv-cache-blockers"},
  "confidence": 0.91,
  "reason": "Matched.",
  "event": {"task_id": "kv-cache-blockers", "input_text": "input", "summary": "summary", "status": "in_progress", "next_action": "next"},
  "attachment_paths": []
}
"""
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project.md"
            project.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")

            result = handle_propose({
                "text": "Reviewed blockers.",
                "project_path": str(project),
            })

        self.assertTrue(result["ok"])
        self.assertIn("proposal", result)
        p = result["proposal"]
        self.assertEqual(p["target"]["project_id"], "multimodal-labeling")
        self.assertIn("confidence", p)
        self.assertIn("event", p)

    @patch("workeventagent.gui.run_archivist")
    def test_propose_low_confidence_flag(self, run_archivist):
        run_archivist.return_value = """
{
  "target": {"project_id": "multimodal-labeling", "item_id": "kv-cache-few-shot", "task_id": "kv-cache-blockers"},
  "confidence": 0.55,
  "reason": "Unsure.",
  "event": {"task_id": "kv-cache-blockers", "input_text": "input", "summary": "summary", "status": "in_progress", "next_action": "next"},
  "attachment_paths": []
}
"""
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project.md"
            project.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")

            result = handle_propose({
                "text": "vague input",
                "project_path": str(project),
            })

        self.assertTrue(result["ok"])
        self.assertTrue(result["low_confidence"])

    @patch("workeventagent.gui.run_archivist")
    def test_propose_new_task_anti_collision(self, run_archivist):
        run_archivist.return_value = """
{
  "target": {"project_id": "multimodal-labeling", "item_id": "kv-cache-few-shot", "task_id": "kv-cache-blockers", "task_title": "KV cache blockers", "new_task": true},
  "confidence": 0.91,
  "reason": "New task.",
  "event": {"task_id": "kv-cache-blockers", "input_text": "input", "summary": "summary", "status": "in_progress", "next_action": "next"},
  "attachment_paths": []
}
"""
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project.md"
            project.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")

            result = handle_propose({
                "text": "new task",
                "project_path": str(project),
            })

        self.assertTrue(result["ok"])
        p = result["proposal"]
        self.assertEqual(p["target"]["task_id"], "kv-cache-blockers-2")


# ── commit ───────────────────────────────────────────────

class CommitTest(unittest.TestCase):
    def test_commit_writes_markdown_and_updates_sqlite(self):
        proposal_data = _make_mock_proposal_data()

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project.md"
            db = Path(tmp) / "index.sqlite"
            project.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")

            result = handle_commit({
                "proposal": proposal_data,
                "project_path": str(project),
                "db_path": str(db),
            })

            updated = project.read_text(encoding="utf-8")
            task = get_task(db, "kv-cache-blockers")

            self.assertTrue(result["ok"])
            self.assertIn("written_path", result)
            self.assertIn("20260629-153000123-kv-cache-blockers", updated)
            self.assertIn("Map current inference chain.", updated)
            self.assertEqual(task["next_action"], "Map current inference chain.")

    def test_commit_copies_attachments(self):
        proposal_data = _make_mock_proposal_data({
            "event": {"event_id": "20260701-100000123-kv-cache-blockers"},
        })

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project.md"
            db = Path(tmp) / "index.sqlite"
            project.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")

            pending_dir = Path(tmp) / "pending"
            pending_dir.mkdir()
            temp_img = pending_dir / "screenshot.png"
            temp_img.write_bytes(b"fake-png-data")

            result = handle_commit({
                "proposal": proposal_data,
                "project_path": str(project),
                "db_path": str(db),
                "pending_attachments": [
                    {"temp_path": str(temp_img), "filename": "screenshot.png"},
                ],
            })

            updated = project.read_text(encoding="utf-8")

            self.assertTrue(result["ok"])
            self.assertEqual(len(result["archived_attachments"]), 1)
            archived_path = result["archived_attachments"][0]
            self.assertIn("kv-cache-blockers", archived_path)
            self.assertIn("20260701-100000123", archived_path)

            # Verify file was actually copied
            dest = Path(tmp) / archived_path
            self.assertTrue(dest.exists(), f"Expected {dest} to exist after copy")

            # Verify path recorded in markdown attachments
            self.assertIn(archived_path, updated)

    def test_commit_new_task_inserts_and_writes(self):
        proposal_data = _make_mock_proposal_data({
            "target": {
                "project_id": "multimodal-labeling",
                "item_id": "kv-cache-few-shot",
                "task_id": "new-blocker-task",
                "task_title": "New Blocker Task",
                "new_item": False,
                "new_task": True,
            },
            "event": {
                "event_id": "20260701-100000123-new-blocker-task",
                "task_id": "new-blocker-task",
                "input_text": "Need to check new blockers.",
                "summary": "New blocker investigation needed.",
                "status": "in_progress",
                "next_action": "List blockers.",
            },
        })

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project.md"
            db = Path(tmp) / "index.sqlite"
            project.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")

            result = handle_commit({
                "proposal": proposal_data,
                "project_path": str(project),
                "db_path": str(db),
            })

            updated = project.read_text(encoding="utf-8")
            task = get_task(db, "new-blocker-task")

            self.assertTrue(result["ok"])
            self.assertIn("#### Task: New Blocker Task <!-- task:new-blocker-task -->", updated)
            self.assertIn("<!-- event:20260701-100000123-new-blocker-task -->", updated)
            self.assertEqual(task["task_id"], "new-blocker-task")
            self.assertEqual(task["status"], "in_progress")


# ── projects ─────────────────────────────────────────────

class ProjectsTest(unittest.TestCase):
    def test_lists_projects_in_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "proj-a.md").write_text(
                "---\nproject_id: proj-a\ntitle: Project A\ndoc_kind: work_project\nupdated: 2026-07-01\n---\n"
                "## Work Map\n"
                "#### Task: t1 <!-- task:t1 -->\n- status: in_progress\n- next_action: go\n- last_event_id: \n",
                encoding="utf-8",
            )
            (workspace / "ignored.md").write_text(
                "---\ntitle: Not a project\n---\n# Not\n", encoding="utf-8"
            )

            result = handle_projects({"workspace": str(workspace)})

            self.assertTrue(result["ok"])
            self.assertEqual(len(result["projects"]), 1)
            self.assertEqual(result["projects"][0]["project_id"], "proj-a")
            self.assertEqual(result["projects"][0]["open_task_count"], 1)


# ── tasks ────────────────────────────────────────────────

class TasksTest(unittest.TestCase):
    def test_returns_task_tree_from_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project.md"
            project.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")

            result = handle_tasks({"project_path": str(project)})

        self.assertTrue(result["ok"])
        self.assertEqual(result["project_id"], "multimodal-labeling")
        self.assertGreaterEqual(len(result["items"]), 1)

        kv_item = None
        for item in result["items"]:
            if item["item_id"] == "kv-cache-few-shot":
                kv_item = item
                break
        self.assertIsNotNone(kv_item, "kv-cache-few-shot item not found")
        assert kv_item is not None

        task_ids = [t["task_id"] for t in kv_item["tasks"]]
        self.assertIn("kv-cache-blockers", task_ids)

    def test_tasks_include_updated_at_from_timeline(self):
        """tasks command should include updated_at for tasks with timeline events."""
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project.md"
            project.write_text(_SYNTHETIC_WITH_TIMELINE, encoding="utf-8")

            result = handle_tasks({"project_path": str(project)})

        self.assertTrue(result["ok"])
        for item in result["items"]:
            for task in item["tasks"]:
                if task["task_id"] == "task-one":
                    self.assertEqual(task["updated_at"], "2026-07-01T11:00:00+00:00")
                if task["task_id"] == "task-two":
                    self.assertEqual(task["updated_at"], "2026-07-01T10:00:00+00:00")


# ── timeline ─────────────────────────────────────────────

class TimelineTest(unittest.TestCase):
    def test_returns_timeline_events_reverse_chronological(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project.md"
            project.write_text(_SYNTHETIC_WITH_TIMELINE, encoding="utf-8")

            result = handle_timeline({"project_path": str(project)})

        self.assertTrue(result["ok"])
        self.assertIn("events", result)
        self.assertEqual(len(result["events"]), 2)
        # Most recent first (reverse chronological)
        self.assertEqual(result["events"][0]["event_id"], "ev2")
        self.assertEqual(result["events"][0]["summary"], "Making progress.")
        self.assertEqual(result["events"][1]["event_id"], "ev1")

    def test_timeline_includes_has_attachment_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project.md"
            project.write_text(_SYNTHETIC_WITH_TIMELINE, encoding="utf-8")

            result = handle_timeline({"project_path": str(project)})

        self.assertTrue(result["ok"])
        for ev in result["events"]:
            if ev["task_id"] == "task-one":
                self.assertTrue(ev["has_attachment"])
            if ev["task_id"] == "task-two":
                self.assertFalse(ev["has_attachment"])


# ── init ─────────────────────────────────────────────────

class InitTest(unittest.TestCase):
    def test_creates_valid_project_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            db_path = workspace / "index.sqlite"

            result = handle_init({
                "workspace": str(workspace),
                "title": "Test Project",
                "project_id": "test-project",
                "db_path": str(db_path),
                "items": [
                    {"title": "Item One", "tasks": ["Task A", "Task B"]},
                ],
            })

            self.assertTrue(result["ok"])
            self.assertEqual(result["project_id"], "test-project")

            project_path = Path(result["project_path"])
            self.assertTrue(project_path.exists())

            text = project_path.read_text(encoding="utf-8")
            self.assertIn("project_id: test-project", text)
            self.assertIn("doc_kind: work_project", text)
            self.assertIn("## Work Map", text)
            self.assertIn("## Timeline", text)
            self.assertIn("## Attachments", text)
            self.assertIn("<!-- item:item-one -->", text)
            self.assertIn("<!-- task:task-a -->", text)
            self.assertIn("<!-- task:task-b -->", text)

            # Verify attachments dir was created
            self.assertTrue((workspace / "attachments").is_dir())

    def test_init_rejects_existing_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            db_path = workspace / "index.sqlite"
            (workspace / "exists.md").write_text("---\nproject_id: exists\n---\n", encoding="utf-8")

            result = handle_init({
                "workspace": str(workspace),
                "title": "Exists",
                "project_id": "exists",
                "db_path": str(db_path),
                "items": [],
            })

        self.assertFalse(result["ok"])
        self.assertEqual(result["kind"], "exists")

    def test_init_auto_generates_project_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            db_path = workspace / "index.sqlite"

            result = handle_init({
                "workspace": str(workspace),
                "title": "基于大模型的多模态标注系统",
                "db_path": str(db_path),
                "items": [],
            })

            self.assertTrue(result["ok"])
            self.assertTrue(result["project_id"])

            project_path = Path(result["project_path"])
            self.assertTrue(project_path.exists())

            text = project_path.read_text(encoding="utf-8")
            self.assertIn("doc_kind: work_project", text)

    def test_init_empty_items_still_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            db_path = workspace / "index.sqlite"

            result = handle_init({
                "workspace": str(workspace),
                "title": "Empty Project",
                "project_id": "empty-project",
                "db_path": str(db_path),
                "items": [],
            })

            self.assertTrue(result["ok"])

            project_path = Path(result["project_path"])
            self.assertTrue(project_path.exists())

            text = project_path.read_text(encoding="utf-8")
            self.assertIn("## Work Map", text)
            self.assertIn("## Timeline", text)


# ── generate_init_markdown ───────────────────────────────

class GenerateInitMarkdownTest(unittest.TestCase):
    def test_generates_complete_schema(self):
        md = _generate_init_markdown(
            "test-proj", "Test Project", "2026-07-01",
            [{"title": "Item 1", "tasks": ["Task A"]}],
        )
        self.assertIn("project_id: test-proj", md)
        self.assertIn("doc_kind: work_project", md)
        self.assertIn("## Current Snapshot", md)
        self.assertIn("## Work Map", md)
        self.assertIn("## Decisions", md)
        self.assertIn("## Attachments", md)
        self.assertIn("## Timeline", md)
        self.assertIn("## Daily / Weekly Rollups", md)
        self.assertIn("### Item: Item 1 <!-- item:item-1 -->", md)
        self.assertIn("#### Task: Task A <!-- task:task-a -->", md)
        self.assertIn("- status: in_progress", md)

    def test_duplicate_init_titles_get_unique_anchors(self):
        md = _generate_init_markdown(
            "test-proj", "Test Project", "2026-07-01",
            [
                {
                    "title": "\u8c03\u7814",
                    "tasks": ["\u8c03\u7814", "\u8c03\u7814"],
                },
                {
                    "title": "\u8c03\u7814",
                    "tasks": ["\u8c03\u7814"],
                },
            ],
        )

        item_ids = re.findall(r"<!-- item:(.+?) -->", md)
        task_ids = re.findall(r"<!-- task:(.+?) -->", md)

        self.assertRegex(item_ids[0], r"^id-[0-9a-f]{8}$")
        self.assertEqual(item_ids[1], f"{item_ids[0]}-2")
        self.assertEqual(task_ids[0], item_ids[0])
        self.assertEqual(task_ids[1], f"{task_ids[0]}-2")
        self.assertEqual(task_ids[2], f"{task_ids[0]}-3")


if __name__ == "__main__":
    unittest.main()
