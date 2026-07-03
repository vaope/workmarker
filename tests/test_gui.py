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
    handle_create_item,
    handle_create_task,
    handle_delete_item,
    handle_delete_task,
    handle_update_item,
    handle_update_task,
    handle_propose,
    handle_route_propose,
    handle_commit,
    handle_projects,
    handle_tasks,
    handle_timeline,
    handle_init,
    handle_generate_report,
)
from workeventagent.markdown_store import write_project_atomically
from workeventagent.index_store import get_task, init_db, rebuild_index

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

    def test_preserves_task_before_next_empty_item(self):
        text = """## Work Map

### Item: First Item <!-- item:first-item -->

#### Task: Existing Task <!-- task:existing-task -->
- status: in_progress
- next_action: Keep this task
- last_event_id:

### Item: New Empty Item <!-- item:new-empty-item -->

## Timeline
"""

        tasks = _parse_work_map_tasks(text)

        self.assertEqual([task["task_id"] for task in tasks], ["existing-task"])


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


class RouteProposeTest(unittest.TestCase):
    def _archive_output(self, project_id: str) -> str:
        return json.dumps({
            "target": {
                "project_id": project_id,
                "item_id": "item-a",
                "task_id": "task-a",
            },
            "confidence": 0.91,
            "reason": "Matched.",
            "event": {
                "task_id": "task-a",
                "input_text": "input",
                "summary": "summary",
                "status": "in_progress",
                "next_action": "next",
            },
            "attachment_paths": [],
        })

    def test_route_propose_fails_when_workspace_has_no_projects(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = handle_route_propose({
                "workspace": tmp,
                "text": "input",
            })

        self.assertFalse(result["ok"])
        self.assertEqual(result["kind"], "no_project")

    @patch("workeventagent.gui.run_project_router")
    @patch("workeventagent.gui.run_archivist")
    def test_route_propose_single_project_skips_router(self, run_archivist, run_project_router):
        run_archivist.return_value = self._archive_output("project-a")
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            db_path = workspace / "index.sqlite"
            handle_init({
                "workspace": str(workspace),
                "title": "Project A",
                "project_id": "project-a",
                "db_path": str(db_path),
                "items": [],
            })

            result = handle_route_propose({
                "workspace": str(workspace),
                "text": "input",
            })

        self.assertTrue(result["ok"])
        self.assertEqual(result["selected_project"]["project_id"], "project-a")
        run_project_router.assert_not_called()

    @patch("workeventagent.gui.run_project_router")
    @patch("workeventagent.gui.run_archivist")
    def test_route_propose_uses_router_selected_project(self, run_archivist, run_project_router):
        run_project_router.return_value = '{"project_id":"project-b","confidence":0.82,"reason":"matched B"}'
        run_archivist.return_value = self._archive_output("project-b")
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            db_path = workspace / "index.sqlite"
            for project_id in ("project-a", "project-b"):
                handle_init({
                    "workspace": str(workspace),
                    "title": project_id,
                    "project_id": project_id,
                    "db_path": str(db_path),
                    "items": [],
                })

            result = handle_route_propose({
                "workspace": str(workspace),
                "text": "input for B",
            })

        self.assertTrue(result["ok"])
        self.assertEqual(result["selected_project"]["project_id"], "project-b")
        self.assertEqual(result["route"]["confidence"], 0.82)
        called_project_path = Path(run_archivist.call_args.args[1])
        self.assertEqual(called_project_path.name, "project-b.md")


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

    def test_tasks_includes_empty_items(self):
        text = """---
project_id: test-proj
title: Test Project
doc_kind: work_project
created: 2026-07-01
updated: 2026-07-01
---

# Test Project

## Work Map

### Item: Empty Item <!-- item:empty-item -->

### Item: Item With Task <!-- item:item-with-task -->

#### Task: Task A <!-- task:task-a -->
- status: in_progress
- next_action:
- last_event_id:

## Timeline
"""
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project.md"
            project.write_text(text, encoding="utf-8")

            result = handle_tasks({"project_path": str(project)})

        self.assertTrue(result["ok"])
        self.assertEqual([item["item_id"] for item in result["items"]], ["empty-item", "item-with-task"])
        self.assertEqual(result["items"][0]["tasks"], [])


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


class ManualCreateTest(unittest.TestCase):
    def test_create_item_adds_visible_empty_item_and_rebuilds_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            db_path = workspace / "index.sqlite"
            init = handle_init({
                "workspace": str(workspace),
                "title": "Manual Project",
                "project_id": "manual-project",
                "db_path": str(db_path),
                "items": [],
            })
            project_path = Path(init["project_path"])

            result = handle_create_item({
                "project_path": str(project_path),
                "db_path": str(db_path),
                "title": "\u660e\u786e\u9879\u76ee\u9700\u6c42",
            })
            tasks = handle_tasks({"project_path": str(project_path)})

        self.assertTrue(result["ok"])
        self.assertRegex(result["item_id"], r"^id-[0-9a-f]{8}$")
        self.assertEqual(tasks["items"][0]["item_id"], result["item_id"])
        self.assertEqual(tasks["items"][0]["tasks"], [])

    def test_create_task_adds_unique_task_under_item(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            db_path = workspace / "index.sqlite"
            init = handle_init({
                "workspace": str(workspace),
                "title": "Manual Project",
                "project_id": "manual-project",
                "db_path": str(db_path),
                "items": [{"title": "\u9700\u6c42", "tasks": []}],
            })
            project_path = Path(init["project_path"])
            item_id = handle_tasks({"project_path": str(project_path)})["items"][0]["item_id"]

            first = handle_create_task({
                "project_path": str(project_path),
                "db_path": str(db_path),
                "item_id": item_id,
                "title": "\u8c03\u7814",
            })
            second = handle_create_task({
                "project_path": str(project_path),
                "db_path": str(db_path),
                "item_id": item_id,
                "title": "\u8c03\u7814",
            })
            tasks = handle_tasks({"project_path": str(project_path)})["items"][0]["tasks"]

        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertEqual(second["task_id"], f"{first['task_id']}-2")
        self.assertEqual([task["task_id"] for task in tasks], [first["task_id"], second["task_id"]])


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


# ── delete item ────────────────────────────────────────────

class DeleteItemTest(unittest.TestCase):
    def _setup(self):
        tmp = tempfile.TemporaryDirectory()
        ws = Path(tmp.name)
        db = ws / "index.sqlite"
        init = handle_init({
            "workspace": str(ws), "title": "Delete Test", "project_id": "del-test",
            "db_path": str(db), "items": [
                {"title": "Item A", "tasks": ["Task A1", "Task A2"]},
                {"title": "Item B", "tasks": ["Task B1"]},
            ],
        })
        return tmp, ws, db, Path(init["project_path"])

    def test_delete_item_removes_item_and_tasks(self):
        tmp, ws, db, proj = self._setup()
        try:
            tasks_before = handle_tasks({"project_path": str(proj)})
            self.assertEqual(len(tasks_before["items"]), 2)

            items = tasks_before["items"]
            item_a_id = items[0]["item_id"]
            result = handle_delete_item({
                "project_path": str(proj), "db_path": str(db), "item_id": item_a_id,
            })

            self.assertTrue(result["ok"])
            self.assertEqual(result["item_id"], item_a_id)
            self.assertEqual(result["deleted_task_count"], 2)

            # Verify round-trip: parser sees only Item B now
            tasks_after = handle_tasks({"project_path": str(proj)})
            self.assertEqual(len(tasks_after["items"]), 1)
            self.assertEqual(tasks_after["items"][0]["item_id"], items[1]["item_id"])
        finally:
            tmp.cleanup()

    def test_delete_item_preserves_other_items_and_tasks(self):
        tmp, ws, db, proj = self._setup()
        try:
            tasks_before = handle_tasks({"project_path": str(proj)})
            item_b_id = tasks_before["items"][1]["item_id"]
            result = handle_delete_item({
                "project_path": str(proj), "db_path": str(db), "item_id": item_b_id,
            })

            self.assertTrue(result["ok"])
            self.assertEqual(result["deleted_task_count"], 1)

            tasks_after = handle_tasks({"project_path": str(proj)})
            self.assertEqual(len(tasks_after["items"]), 1)
            self.assertEqual(len(tasks_after["items"][0]["tasks"]), 2)
        finally:
            tmp.cleanup()

    def test_delete_item_rejects_unknown_item(self):
        tmp, ws, db, proj = self._setup()
        try:
            result = handle_delete_item({
                "project_path": str(proj), "db_path": str(db), "item_id": "nonexistent",
            })
            self.assertFalse(result["ok"])
        finally:
            tmp.cleanup()

    def test_delete_item_parser_round_trip_two_item_layout(self):
        """Delete first of two items; parser must still see the second correctly."""
        tmp, ws, db, proj = self._setup()
        try:
            items = handle_tasks({"project_path": str(proj)})["items"]
            handle_delete_item({
                "project_path": str(proj), "db_path": str(db), "item_id": items[0]["item_id"],
            })
            tasks_after = handle_tasks({"project_path": str(proj)})
            self.assertEqual(len(tasks_after["items"]), 1)
            self.assertEqual(tasks_after["items"][0]["item_id"], items[1]["item_id"])
            self.assertEqual(len(tasks_after["items"][0]["tasks"]), 1)
        finally:
            tmp.cleanup()

    def test_delete_empty_item(self):
        """Deleting an item with no tasks should work (no cascading)."""
        tmp, ws, db, proj = self._setup()
        try:
            # Create an empty item
            r = handle_create_item({
                "project_path": str(proj), "db_path": str(db), "title": "Empty Item",
            })
            empty_id = r["item_id"]
            result = handle_delete_item({
                "project_path": str(proj), "db_path": str(db), "item_id": empty_id,
            })
            self.assertTrue(result["ok"])
            self.assertEqual(result["deleted_task_count"], 0)
        finally:
            tmp.cleanup()


# ── delete task ────────────────────────────────────────────

class DeleteTaskTest(unittest.TestCase):
    def _setup(self):
        tmp = tempfile.TemporaryDirectory()
        ws = Path(tmp.name)
        db = ws / "index.sqlite"
        init = handle_init({
            "workspace": str(ws), "title": "Delete Test", "project_id": "del-test",
            "db_path": str(db), "items": [
                {"title": "Item A", "tasks": ["Task A1", "Task A2"]},
            ],
        })
        return tmp, ws, db, Path(init["project_path"])

    def test_delete_task_removes_only_targeted_task(self):
        tmp, ws, db, proj = self._setup()
        try:
            tasks = handle_tasks({"project_path": str(proj)})["items"][0]["tasks"]
            self.assertEqual(len(tasks), 2)
            target = tasks[0]["task_id"]

            result = handle_delete_task({
                "project_path": str(proj), "db_path": str(db), "task_id": target,
            })

            self.assertTrue(result["ok"])
            after = handle_tasks({"project_path": str(proj)})["items"][0]["tasks"]
            self.assertEqual(len(after), 1)
            self.assertNotEqual(after[0]["task_id"], target)
        finally:
            tmp.cleanup()

    def test_delete_task_rejects_unknown_task(self):
        tmp, ws, db, proj = self._setup()
        try:
            result = handle_delete_task({
                "project_path": str(proj), "db_path": str(db), "task_id": "nonexistent",
            })
            self.assertFalse(result["ok"])
        finally:
            tmp.cleanup()

    def test_delete_task_preserves_timeline_events(self):
        """Timeline events referencing the deleted task should NOT be cascaded out."""
        tmp, ws, db, proj = self._setup()
        try:
            tasks = handle_tasks({"project_path": str(proj)})["items"][0]["tasks"]
            target = tasks[0]["task_id"]

            # First, commit an event for this task so we have a timeline
            proposal = _make_mock_proposal_data({
                "target": {"project_id": "del-test", "item_id": "item-a", "task_id": target},
                "event": {"task_id": target, "event_id": "20260701-ev1", "status": "in_progress",
                          "summary": "test event", "next_action": "keep going"},
            })
            handle_commit({
                "proposal": proposal, "project_path": str(proj), "db_path": str(db),
            })

            # Now delete the task
            result = handle_delete_task({
                "project_path": str(proj), "db_path": str(db), "task_id": target,
            })
            self.assertTrue(result["ok"])

            # Timeline should still have the event
            timeline = handle_timeline({"project_path": str(proj)})
            self.assertTrue(any(e["event_id"] == "20260701-ev1" for e in timeline["events"]),
                            "Timeline event should be preserved after task deletion")
        finally:
            tmp.cleanup()

    def test_delete_task_parser_round_trip(self):
        """After deleting first of two tasks, parser must still see the second."""
        tmp, ws, db, proj = self._setup()
        try:
            tasks = handle_tasks({"project_path": str(proj)})["items"][0]["tasks"]
            task_a1 = tasks[0]["task_id"]
            task_a2 = tasks[1]["task_id"]

            handle_delete_task({
                "project_path": str(proj), "db_path": str(db), "task_id": task_a1,
            })
            after = handle_tasks({"project_path": str(proj)})["items"][0]["tasks"]
            self.assertEqual([t["task_id"] for t in after], [task_a2])
        finally:
            tmp.cleanup()

    def test_delete_task_non_canonical_layout_no_orphans(self):
        """F-b regression: delete must not leave orphan lines when task block
        is non-canonical (e.g. multi-line next_action)."""
        tmp = tempfile.TemporaryDirectory()
        ws = Path(tmp.name)
        db = ws / "index.sqlite"
        try:
            init = handle_init({
                "workspace": str(ws), "title": "MultiLine Test", "project_id": "ml-test",
                "db_path": str(db), "items": [
                    {"title": "Item A", "tasks": ["Canonical Task"]},
                ],
            })
            proj = Path(init["project_path"])

            # Manually inject a non-canonical task with multi-line next_action
            text = proj.read_text(encoding="utf-8")
            non_canonical = (
                "#### Task: Multi-line Task <!-- task:ml-t1 -->\n"
                "- status: in_progress\n"
                "- next_action: |\n"
                "  Line 1 of action\n"
                "  Line 2 of action\n"
                "- last_event_id:\n"
                "\n"
            )
            # Insert before the end of Work Map (before ## Decisions or EOF)
            text = text.replace("## Decisions", non_canonical + "## Decisions")
            write_project_atomically(proj, text)
            init_db(db)
            rebuild_index(db, [proj])

            # Verify parser can read both tasks
            after_init = handle_tasks({"project_path": str(proj)})["items"][0]["tasks"]
            self.assertEqual(len(after_init), 2)

            # Delete the non-canonical task
            result = handle_delete_task({
                "project_path": str(proj), "db_path": str(db), "task_id": "ml-t1",
            })
            self.assertTrue(result["ok"])

            # Verify: no orphan lines (no "Line 1 of action" / "Line 2 of action")
            text_after = proj.read_text(encoding="utf-8")
            self.assertNotIn("Line 1 of action", text_after,
                             "orphan line from multi-line next_action should be removed")
            self.assertNotIn("Line 2 of action", text_after,
                             "orphan line from multi-line next_action should be removed")
            self.assertNotIn("<!-- task:ml-t1 -->", text_after,
                             "task anchor must be removed")

            # Parser must still see the canonical task
            after_del = handle_tasks({"project_path": str(proj)})["items"][0]["tasks"]
            self.assertEqual(len(after_del), 1)
            self.assertEqual(after_del[0]["title"], "Canonical Task")
        finally:
            tmp.cleanup()


# ── update item ────────────────────────────────────────────

class UpdateItemTest(unittest.TestCase):
    def _setup(self):
        tmp = tempfile.TemporaryDirectory()
        ws = Path(tmp.name)
        db = ws / "index.sqlite"
        init = handle_init({
            "workspace": str(ws), "title": "Update Test", "project_id": "upd-test",
            "db_path": str(db), "items": [
                {"title": "Old Item Name", "tasks": ["Task X"]},
            ],
        })
        return tmp, ws, db, Path(init["project_path"])

    def test_update_item_renames_title_preserves_anchor(self):
        tmp, ws, db, proj = self._setup()
        try:
            items = handle_tasks({"project_path": str(proj)})["items"]
            old_title = items[0]["title"]
            item_id = items[0]["item_id"]

            result = handle_update_item({
                "project_path": str(proj), "db_path": str(db),
                "item_id": item_id, "title": "New Item Name",
            })

            self.assertTrue(result["ok"])
            self.assertEqual(result["item_id"], item_id)
            self.assertEqual(result["title"], "New Item Name")

            # Verify markdown: title changed, anchor unchanged
            text = proj.read_text(encoding="utf-8")
            self.assertIn("### Item: New Item Name <!-- item:" + item_id, text)
            self.assertNotIn(old_title, text)

            # Verify parser round-trip
            after = handle_tasks({"project_path": str(proj)})["items"]
            self.assertEqual(after[0]["item_id"], item_id)
            self.assertEqual(after[0]["title"], "New Item Name")
            self.assertEqual(len(after[0]["tasks"]), 1)
        finally:
            tmp.cleanup()

    def test_update_item_rejects_unknown_item(self):
        tmp, ws, db, proj = self._setup()
        try:
            result = handle_update_item({
                "project_path": str(proj), "db_path": str(db),
                "item_id": "nonexistent", "title": "Nope",
            })
            self.assertFalse(result["ok"])
        finally:
            tmp.cleanup()

    def test_update_item_title_with_backslash(self):
        """Regression: titles with \\1 etc must be stored literally, not interpreted as regex backreferences."""
        tmp, ws, db, proj = self._setup()
        try:
            items = handle_tasks({"project_path": str(proj)})["items"]
            item_id = items[0]["item_id"]

            result = handle_update_item({
                "project_path": str(proj), "db_path": str(db),
                "item_id": item_id, "title": r"C:\Work\Item-1",
            })

            self.assertTrue(result["ok"])
            text = proj.read_text(encoding="utf-8")
            self.assertIn(r"C:\Work\Item-1", text)
            # Anchor must not be corrupted
            self.assertIn(f"<!-- item:{item_id} -->", text)
        finally:
            tmp.cleanup()

    def test_update_item_rejects_empty_title(self):
        tmp, ws, db, proj = self._setup()
        try:
            items = handle_tasks({"project_path": str(proj)})["items"]
            result = handle_update_item({
                "project_path": str(proj), "db_path": str(db),
                "item_id": items[0]["item_id"], "title": "   ",
            })
            self.assertFalse(result["ok"])
        finally:
            tmp.cleanup()


# ── update task ────────────────────────────────────────────

class UpdateTaskTest(unittest.TestCase):
    def _setup(self):
        tmp = tempfile.TemporaryDirectory()
        ws = Path(tmp.name)
        db = ws / "index.sqlite"
        init = handle_init({
            "workspace": str(ws), "title": "Update Test", "project_id": "upd-test",
            "db_path": str(db), "items": [
                {"title": "Item A", "tasks": ["Old Task Name"]},
            ],
        })
        return tmp, ws, db, Path(init["project_path"])

    def test_update_task_status(self):
        tmp, ws, db, proj = self._setup()
        try:
            tasks = handle_tasks({"project_path": str(proj)})["items"][0]["tasks"]
            task_id = tasks[0]["task_id"]
            self.assertEqual(tasks[0]["status"], "in_progress")

            result = handle_update_task({
                "project_path": str(proj), "db_path": str(db),
                "task_id": task_id, "field": "status", "value": "done",
            })

            self.assertTrue(result["ok"])
            after = handle_tasks({"project_path": str(proj)})["items"][0]["tasks"]
            self.assertEqual(after[0]["status"], "done")
        finally:
            tmp.cleanup()

    def test_update_task_title(self):
        tmp, ws, db, proj = self._setup()
        try:
            tasks = handle_tasks({"project_path": str(proj)})["items"][0]["tasks"]
            task_id = tasks[0]["task_id"]
            old_title = tasks[0]["title"]

            result = handle_update_task({
                "project_path": str(proj), "db_path": str(db),
                "task_id": task_id, "field": "title", "value": "New Task Name",
            })

            self.assertTrue(result["ok"])
            # Verify anchor preserved, title changed
            text = proj.read_text(encoding="utf-8")
            self.assertIn(f"<!-- task:{task_id} -->", text)
            self.assertIn("New Task Name", text)
            self.assertNotIn(f"Task: {old_title}", text)

            after = handle_tasks({"project_path": str(proj)})["items"][0]["tasks"]
            self.assertEqual(after[0]["title"], "New Task Name")
            self.assertEqual(after[0]["task_id"], task_id)
        finally:
            tmp.cleanup()

    def test_update_task_next_action(self):
        tmp, ws, db, proj = self._setup()
        try:
            tasks = handle_tasks({"project_path": str(proj)})["items"][0]["tasks"]
            task_id = tasks[0]["task_id"]

            result = handle_update_task({
                "project_path": str(proj), "db_path": str(db),
                "task_id": task_id, "field": "next_action", "value": "Review spec.",
            })

            self.assertTrue(result["ok"])
            after = handle_tasks({"project_path": str(proj)})["items"][0]["tasks"]
            self.assertEqual(after[0]["next_action"], "Review spec.")
        finally:
            tmp.cleanup()

    def test_update_task_title_with_backslash(self):
        """Regression: titles with \\1 etc must be stored literally, not interpreted as regex backreferences."""
        tmp, ws, db, proj = self._setup()
        try:
            tasks = handle_tasks({"project_path": str(proj)})["items"][0]["tasks"]
            task_id = tasks[0]["task_id"]

            result = handle_update_task({
                "project_path": str(proj), "db_path": str(db),
                "task_id": task_id, "field": "title", "value": r"Fix \1 backslash bug",
            })

            self.assertTrue(result["ok"])
            text = proj.read_text(encoding="utf-8")
            self.assertIn(r"Fix \1 backslash bug", text)
            self.assertIn(f"<!-- task:{task_id} -->", text)
        finally:
            tmp.cleanup()

    def test_update_task_rejects_unknown_task(self):
        tmp, ws, db, proj = self._setup()
        try:
            result = handle_update_task({
                "project_path": str(proj), "db_path": str(db),
                "task_id": "nonexistent", "field": "status", "value": "done",
            })
            self.assertFalse(result["ok"])
        finally:
            tmp.cleanup()

    def test_update_task_rejects_invalid_field(self):
        tmp, ws, db, proj = self._setup()
        try:
            tasks = handle_tasks({"project_path": str(proj)})["items"][0]["tasks"]
            result = handle_update_task({
                "project_path": str(proj), "db_path": str(db),
                "task_id": tasks[0]["task_id"], "field": "bogus", "value": "x",
            })
            self.assertFalse(result["ok"])
        finally:
            tmp.cleanup()

    def test_update_task_rejects_invalid_status(self):
        tmp, ws, db, proj = self._setup()
        try:
            tasks = handle_tasks({"project_path": str(proj)})["items"][0]["tasks"]
            result = handle_update_task({
                "project_path": str(proj), "db_path": str(db),
                "task_id": tasks[0]["task_id"], "field": "status", "value": "blocked",
            })
            self.assertFalse(result["ok"])
            self.assertEqual(result["kind"], "invalid_input")
        finally:
            tmp.cleanup()


# ── generate_report ────────────────────────────────────────

def _write_project_with_timeline(
    workspace: Path, filename: str, events: list[tuple[str, str, str, str]],
) -> Path:
    """Write a minimal work_project markdown with Timeline events."""
    lines = [
        "---",
        "project_id: report-project",
        "title: Report Project",
        "doc_kind: work_project",
        "created: 2026-07-01",
        "updated: 2026-07-01",
        "---",
        "",
        "## Current Snapshot",
        "",
        "## Work Map",
        "### Item: Report Item <!-- item:item-a -->",
        "#### Task: Report Task <!-- task:task-a -->",
        "- status: in_progress",
        "- next_action:",
        "- last_event_id:",
        "",
        "## Decisions",
        "",
        "## Attachments",
        "",
        "## Timeline",
    ]
    for timestamp, event_id, task_id, summary in events:
        lines.extend([
            f"- {timestamp} <!-- event:{event_id} -->",
            f"  - task_id: {task_id}",
            f"  - input: {summary}",
            f"  - summary: {summary}",
            "  - status: in_progress",
            "  - next_action:",
        ])
    lines.extend(["", "## Daily / Weekly Rollups", ""])
    path = workspace / filename
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


class ReportTest(unittest.TestCase):
    def _setup(self):
        tmp = tempfile.TemporaryDirectory()
        ws = Path(tmp.name)
        db = ws / "index.sqlite"
        init = handle_init({
            "workspace": str(ws), "title": "Report Test", "project_id": "rpt-test",
            "db_path": str(db), "items": [
                {"title": "Item A", "tasks": ["Task A1"]},
            ],
        })
        return tmp, ws, db, Path(init["project_path"])

    def _commit_event(self, proj, db, task_id, summary, event_id, status="in_progress"):
        proposal = {
            "target": {"project_id": "rpt-test", "item_id": "item-a", "task_id": task_id},
            "confidence": 0.9, "reason": "test",
            "event": {
                "event_id": event_id, "task_id": task_id, "status": status,
                "summary": summary, "next_action": "continue",
                "input": "test input", "event_type": "update",
            },
            "attachment_paths": [],
        }
        return handle_commit({"proposal": proposal, "project_path": str(proj), "db_path": str(db)})

    def test_generate_daily_report_with_events(self):
        tmp, ws, db, proj = self._setup()
        try:
            tasks = handle_tasks({"project_path": str(proj)})["items"][0]["tasks"]
            task_id = tasks[0]["task_id"]
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            self._commit_event(proj, db, task_id, "Fixed a bug", f"{today.replace('-', '')}-event1")

            result = handle_generate_report({
                "workspace": str(ws), "type": "daily", "date": today,
            })
            self.assertTrue(result["ok"])
            self.assertGreaterEqual(result["event_count"], 1)
            self.assertIn("Fixed a bug", result["report"])
            self.assertIn(today, result["report"])
        finally:
            tmp.cleanup()

    def test_generate_daily_empty(self):
        tmp, ws, db, proj = self._setup()
        try:
            result = handle_generate_report({
                "workspace": str(ws), "type": "daily",
                "date": "2020-01-01",
            })
            self.assertTrue(result["ok"])
            self.assertEqual(result["event_count"], 0)
            self.assertIn("无活动记录", result["report"])
        finally:
            tmp.cleanup()

    def test_generate_project_summary_requires_project_id(self):
        tmp, ws, db, proj = self._setup()
        try:
            result = handle_generate_report({
                "workspace": str(ws), "type": "project_summary",
            })
            self.assertFalse(result["ok"])
            self.assertIn("project_id", result["error"])
        finally:
            tmp.cleanup()

    def test_generate_rejects_invalid_type(self):
        tmp, ws, db, proj = self._setup()
        try:
            result = handle_generate_report({
                "workspace": str(ws), "type": "bogus",
            })
            self.assertFalse(result["ok"])
        finally:
            tmp.cleanup()

    def test_generate_rejects_invalid_date(self):
        tmp, ws, db, proj = self._setup()
        try:
            result = handle_generate_report({
                "workspace": str(ws), "type": "daily", "date": "not-a-date",
            })
            self.assertFalse(result["ok"])
        finally:
            tmp.cleanup()

    def test_generate_report_filters_by_explicit_local_date_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _write_project_with_timeline(
                workspace,
                "local-boundary.md",
                [
                    ("2026-07-02T15:30:00+00:00", "event-evening", "task-a", "Evening local event"),
                    ("2026-07-03T16:30:00+00:00", "event-next-day", "task-a", "Next local day event"),
                ],
            )

            result = handle_generate_report({
                "workspace": str(workspace),
                "type": "daily",
                "date_from": "2026-07-02",
                "date_to": "2026-07-02",
                "persist": False,
                "include_ai": False,
            })

            self.assertTrue(result["ok"])
            self.assertEqual(result["event_count"], 1)
            self.assertIn("Evening local event", result["report"])
            self.assertNotIn("Next local day event", result["report"])


if __name__ == "__main__":
    unittest.main()
