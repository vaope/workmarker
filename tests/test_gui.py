import json
import re
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from workeventagent.gui import (
    _event_id_timestamp,
    _parse_work_map_tasks,
    _parse_attachments_task_ids,
    _generate_init_markdown,
    _report_output_path,
    _safe_component,
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
    handle_inbox_create,
    handle_inbox_list,
    handle_inbox_process,
    handle_inbox_commit,
    handle_inbox_cancel,
    handle_search,
)
from workeventagent.project_schema import parse_timeline_events
from workeventagent.inbox_store import list_captures, update_capture
from workeventagent.knowledge_store import (
    enqueue_job,
    get_job,
    get_proposal,
    job_id_for,
    list_jobs,
    list_proposals as list_knowledge_proposals,
    transition_job,
)

from workeventagent.search_store import search_workspace
from workeventagent.markdown_store import write_project_atomically
from workeventagent.index_store import get_task, init_db, rebuild_index
from workeventagent.opencode_runner import OpencodeRunnerError

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
        events = parse_timeline_events(_SYNTHETIC_WITH_TIMELINE)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["event_id"], "ev2")
        self.assertEqual(events[0]["summary"], "Making progress.")
        self.assertEqual(events[1]["event_id"], "ev1")
        self.assertEqual(events[1]["task_id"], "task-two")

    def test_empty_timeline_returns_empty(self):
        text = "---\nproject_id: test\n---\n## Timeline\n\n## Other\n"
        events = parse_timeline_events(text)
        self.assertEqual(events, [])

    def test_fixture_timeline_is_empty(self):
        """The actual fixture has an empty Timeline section — parser handles this."""
        text = FIXTURE.read_text(encoding="utf-8")
        events = parse_timeline_events(text)
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

    @patch("workeventagent.gui.run_archivist")
    def test_propose_forwards_opencode_model_to_archivist(self, run_archivist):
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
                "text": "use selected model",
                "project_path": str(project),
                "opencode_model": "anthropic/claude-sonnet-4-5",
            })

        self.assertTrue(result["ok"])
        self.assertEqual(run_archivist.call_args.kwargs["model"], "anthropic/claude-sonnet-4-5")


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

    @patch("workeventagent.gui.run_project_router")
    @patch("workeventagent.gui.run_archivist")
    def test_route_propose_forwards_opencode_model_to_router_and_archivist(self, run_archivist, run_project_router):
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
                "opencode_model": "openai/gpt-5.1",
            })

        self.assertTrue(result["ok"])
        self.assertEqual(run_project_router.call_args.kwargs["model"], "openai/gpt-5.1")
        self.assertEqual(run_archivist.call_args.kwargs["model"], "openai/gpt-5.1")


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

    def test_commit_new_item_and_task_does_not_raise_missing_item_anchor(self):
        proposal_data = _make_mock_proposal_data({
            "target": {
                "project_id": "multimodal-labeling",
                "item_id": "capture-inbox",
                "item_title": "Capture Inbox",
                "task_id": "queue-processing",
                "task_title": "Queue processing",
                "new_item": True,
                "new_task": True,
            },
            "event": {
                "event_id": "20260706-100000123-queue-processing",
                "task_id": "queue-processing",
                "input_text": "Need to queue quick captures.",
                "summary": "Quick capture needs a queue.",
                "status": "in_progress",
                "next_action": "Design the queue.",
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
            task = get_task(db, "queue-processing")

            self.assertTrue(result["ok"], str(result))
            self.assertIn("### Item: Capture Inbox <!-- item:capture-inbox -->", updated)
            self.assertIn("#### Task: Queue processing <!-- task:queue-processing -->", updated)
            self.assertIn("<!-- event:20260706-100000123-queue-processing -->", updated)
            self.assertEqual(task["task_id"], "queue-processing")

    def test_commit_preserves_multiline_input_in_timeline(self):
        proposal_data = _make_mock_proposal_data({
            "event": {
                "event_id": "20260706-110000123-kv-cache-blockers",
                "input_text": "Line one\nLine two",
                "summary": "Captured two lines.",
                "next_action": "Keep both lines.",
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
            timeline = handle_timeline({"project_path": str(project)})

            self.assertTrue(result["ok"], str(result))
            self.assertEqual(timeline["events"][0]["input"], "Line one\nLine two")


class ProjectsTest(unittest.TestCase):
    def test_lists_projects_in_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "proj-a.md").write_text(
                "---\nproject_id: proj-a\ntitle: Project A\ndoc_kind: work_project\nupdated: 2026-07-01\n---\n"
                "## Work Map\n"
                "### Item: Tasks <!-- item:tasks -->\n"
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

    def test_update_item_noop_save_succeeds(self):
        """no-op save (same title, no background change) must return ok, not false error."""
        tmp, ws, db, proj = self._setup()
        try:
            items = handle_tasks({"project_path": str(proj)})["items"]
            item_id = items[0]["item_id"]
            orig_title = items[0]["title"]

            result = handle_update_item({
                "project_path": str(proj), "db_path": str(db),
                "item_id": item_id, "title": orig_title,
            })
            self.assertTrue(result["ok"], f"expected ok, got {result}")
            self.assertEqual(result["item_id"], item_id)
            self.assertEqual(result["title"], orig_title)

            # Verify markdown unchanged (item still exists)
            text = proj.read_text(encoding="utf-8")
            self.assertIn(f"<!-- item:{item_id} -->", text)
        finally:
            tmp.cleanup()

    def test_update_item_noop_with_background_succeeds(self):
        """no-op save (same title + same background) must return ok."""
        tmp, ws, db, proj = self._setup()
        try:
            items = handle_tasks({"project_path": str(proj)})["items"]
            item_id = items[0]["item_id"]
            orig_title = items[0]["title"]

            # First set a background
            result = handle_update_item({
                "project_path": str(proj), "db_path": str(db),
                "item_id": item_id, "title": orig_title,
                "background": "some context",
            })
            self.assertTrue(result["ok"])

            # Now no-op: same title and same background
            result = handle_update_item({
                "project_path": str(proj), "db_path": str(db),
                "item_id": item_id, "title": orig_title,
                "background": "some context",
            })
            self.assertTrue(result["ok"], f"no-op with same background should succeed, got {result}")
            self.assertEqual(result["item_id"], item_id)
        finally:
            tmp.cleanup()

    def test_update_item_noop_unknown_item_still_fails(self):
        """no-op on unknown item must still raise error (anchor genuinely not found)."""
        tmp, ws, db, proj = self._setup()
        try:
            result = handle_update_item({
                "project_path": str(proj), "db_path": str(db),
                "item_id": "nonexistent", "title": "Whatever",
            })
            self.assertFalse(result["ok"])
            self.assertEqual(result.get("kind"), "invalid_project")
        finally:
            tmp.cleanup()


class ItemBackgroundTests(unittest.TestCase):
    """Tests for item background field: parse, create, update, clear."""

    def _setup(self):
        tmp = tempfile.TemporaryDirectory()
        ws = Path(tmp.name)
        db = ws / "index.sqlite"
        init = handle_init({
            "workspace": str(ws), "title": "Background Test", "project_id": "bg-test",
            "db_path": str(db), "items": [
                {"title": "Normal Item", "tasks": ["Task A"]},
            ],
        })
        return tmp, ws, db, Path(init["project_path"])

    def _create_item(self, proj: Path, db: Path, title: str, background: str = ""):
        return handle_create_item({
            "project_path": str(proj), "db_path": str(db),
            "title": title, "background": background,
        })

    def test_parse_item_without_background_returns_empty_string(self):
        """Old doc without background: parse returns background=''."""
        tmp, ws, db, proj = self._setup()
        try:
            items = handle_tasks({"project_path": str(proj)})["items"]
            self.assertEqual(items[0]["background"], "")
            self.assertIn("background", items[0])  # key exists, just empty
        finally:
            tmp.cleanup()

    def test_create_item_with_background(self):
        """Create item with background; verify markdown output and parser round-trip."""
        tmp, ws, db, proj = self._setup()
        try:
            result = self._create_item(proj, db, "With BG", "解释为什么做这个需求")
            self.assertTrue(result["ok"])

            text = proj.read_text(encoding="utf-8")
            self.assertIn("- background: 解释为什么做这个需求", text)

            items = handle_tasks({"project_path": str(proj)})["items"]
            bg_item = next(it for it in items if it["title"] == "With BG")
            self.assertEqual(bg_item["background"], "解释为什么做这个需求")
        finally:
            tmp.cleanup()

    def test_create_item_without_background_has_no_background_line(self):
        """Create without background: no - background: line in markdown."""
        tmp, ws, db, proj = self._setup()
        try:
            result = self._create_item(proj, db, "No BG")
            self.assertTrue(result["ok"])

            text = proj.read_text(encoding="utf-8")
            self.assertNotIn("- background:", text)
        finally:
            tmp.cleanup()

    def test_update_item_set_background(self):
        """Set background on an existing item that had none."""
        tmp, ws, db, proj = self._setup()
        try:
            items = handle_tasks({"project_path": str(proj)})["items"]
            item_id = items[0]["item_id"]

            result = handle_update_item({
                "project_path": str(proj), "db_path": str(db),
                "item_id": item_id, "title": items[0]["title"],
                "background": "新的背景说明",
            })
            self.assertTrue(result["ok"])

            text = proj.read_text(encoding="utf-8")
            self.assertIn("- background: 新的背景说明", text)

            after = handle_tasks({"project_path": str(proj)})["items"]
            self.assertEqual(after[0]["background"], "新的背景说明")
            # Anchor preserved
            self.assertIn(f"<!-- item:{item_id} -->", text)
        finally:
            tmp.cleanup()

    def test_update_item_change_background(self):
        """Change existing background to a new value."""
        tmp, ws, db, proj = self._setup()
        try:
            items = handle_tasks({"project_path": str(proj)})["items"]
            item_id = items[0]["item_id"]

            # Set first
            handle_update_item({
                "project_path": str(proj), "db_path": str(db),
                "item_id": item_id, "title": items[0]["title"],
                "background": "原始背景",
            })
            # Change
            result = handle_update_item({
                "project_path": str(proj), "db_path": str(db),
                "item_id": item_id, "title": items[0]["title"],
                "background": "更新后的背景",
            })
            self.assertTrue(result["ok"])

            text = proj.read_text(encoding="utf-8")
            self.assertNotIn("原始背景", text)
            self.assertIn("- background: 更新后的背景", text)
            # Only one background line
            self.assertEqual(text.count("- background:"), 1)
        finally:
            tmp.cleanup()

    def test_update_item_clear_background(self):
        """Clear background by passing empty string."""
        tmp, ws, db, proj = self._setup()
        try:
            items = handle_tasks({"project_path": str(proj)})["items"]
            item_id = items[0]["item_id"]

            # Set first
            handle_update_item({
                "project_path": str(proj), "db_path": str(db),
                "item_id": item_id, "title": items[0]["title"],
                "background": "待清除",
            })
            # Clear
            result = handle_update_item({
                "project_path": str(proj), "db_path": str(db),
                "item_id": item_id, "title": items[0]["title"],
                "background": "",
            })
            self.assertTrue(result["ok"])

            text = proj.read_text(encoding="utf-8")
            self.assertNotIn("- background:", text)

            after = handle_tasks({"project_path": str(proj)})["items"]
            self.assertEqual(after[0]["background"], "")
        finally:
            tmp.cleanup()

    def test_update_item_title_only_does_not_affect_background(self):
        """Updating only title (no background key) leaves background unchanged."""
        tmp, ws, db, proj = self._setup()
        try:
            items = handle_tasks({"project_path": str(proj)})["items"]
            item_id = items[0]["item_id"]

            # Set background first
            handle_update_item({
                "project_path": str(proj), "db_path": str(db),
                "item_id": item_id, "title": items[0]["title"],
                "background": "保持不变",
            })
            # Update only title
            result = handle_update_item({
                "project_path": str(proj), "db_path": str(db),
                "item_id": item_id, "title": "Renamed Only",
            })
            self.assertTrue(result["ok"])

            text = proj.read_text(encoding="utf-8")
            self.assertIn("- background: 保持不变", text)
            self.assertIn("### Item: Renamed Only", text)
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
            self.assertEqual(list_jobs(ws), [], "manual task status must not enqueue synthesis")
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

    def test_checkbox_status_update_preserves_timeline_and_rebuilds_index(self):
        tmp, ws, db, proj = self._setup()
        try:
            task = handle_tasks({"project_path": str(proj)})["items"][0]["tasks"][0]
            before_text = proj.read_text(encoding="utf-8")
            before_timeline = before_text.split("## Timeline", 1)[1]

            result = handle_update_task({
                "project_path": str(proj),
                "db_path": str(db),
                "task_id": task["task_id"],
                "field": "status",
                "value": "done",
            })

            self.assertTrue(result["ok"])
            after_text = proj.read_text(encoding="utf-8")
            self.assertEqual(after_text.split("## Timeline", 1)[1], before_timeline)
            self.assertEqual(
                handle_tasks({"project_path": str(proj)})["items"][0]["tasks"][0]["status"],
                "done",
            )
            self.assertEqual(get_task(db, task["task_id"])["status"], "done")
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
            today = datetime.now().astimezone().strftime("%Y-%m-%d")
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

    def test_generate_report_persists_markdown_with_frontmatter(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _write_project_with_timeline(
                workspace,
                "report-project.md",
                [("2026-07-03T10:00:00+00:00", "event-one", "task-a", "Persist me")],
            )

            result = handle_generate_report({
                "workspace": str(workspace),
                "type": "daily",
                "date_from": "2026-07-03",
                "date_to": "2026-07-03",
                "persist": True,
                "include_ai": False,
            })

            self.assertTrue(result["ok"])
            self.assertFalse(result.get("skipped"))
            report_path = Path(result["written_path"])
            self.assertTrue(report_path.exists())
            text = report_path.read_text(encoding="utf-8")
            self.assertIn("doc_kind: work_report", text)
            self.assertIn("report_type: daily", text)
            self.assertIn("Persist me", text)

    @patch("workeventagent.gui.run_reporter")
    def test_generate_report_forwards_opencode_model_to_reporter(self, run_reporter):
        run_reporter.return_value = '{"highlight":"AI highlight"}'
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _write_project_with_timeline(
                workspace,
                "report-project.md",
                [("2026-07-03T10:00:00+00:00", "event-one", "task-a", "Summarize me")],
            )

            result = handle_generate_report({
                "workspace": str(workspace),
                "type": "daily",
                "date_from": "2026-07-03",
                "date_to": "2026-07-03",
                "persist": False,
                "include_ai": True,
                "opencode_model": "openai/gpt-5.1",
            })

        self.assertTrue(result["ok"])
        self.assertEqual(run_reporter.call_args.kwargs["model"], "openai/gpt-5.1")

    def test_scheduled_daily_skips_when_no_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _write_project_with_timeline(workspace, "empty-day.md", [])

            result = handle_generate_report({
                "workspace": str(workspace),
                "type": "daily",
                "date_from": "2026-07-03",
                "date_to": "2026-07-03",
                "persist": True,
                "mode": "scheduled",
                "include_ai": False,
            })

            self.assertTrue(result["ok"])
            self.assertTrue(result.get("skipped"))
            self.assertEqual(result.get("skip_reason"), "no_events")
            self.assertFalse((workspace / "reports").exists())

    def test_project_summary_requires_reporter_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _write_project_with_timeline(
                workspace,
                "report-project.md",
                [("2026-07-03T10:00:00+00:00", "event-one", "task-a", "Summarize me")],
            )

            with patch("workeventagent.gui.run_reporter",
                       side_effect=OpencodeRunnerError("reporter failed")):
                result = handle_generate_report({
                    "workspace": str(workspace),
                    "type": "project_summary",
                    "project_id": "report-project",
                    "date_from": "2026-07-03",
                    "date_to": "2026-07-03",
                    "persist": True,
                    "include_ai": True,
                })

            self.assertFalse(result["ok"])
            self.assertEqual(result["kind"], "opencode_error")
            self.assertFalse((workspace / "reports" / "project").exists())

    def test_project_summary_rejects_include_ai_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _write_project_with_timeline(
                workspace,
                "report-project.md",
                [("2026-07-03T10:00:00+00:00", "event-one", "task-a", "Summary")],
            )

            result = handle_generate_report({
                "workspace": str(workspace),
                "type": "project_summary",
                "project_id": "report-project",
                "date_from": "2026-07-03",
                "date_to": "2026-07-03",
                "persist": True,
                "include_ai": False,
            })

            self.assertFalse(result["ok"])
            self.assertEqual(result["kind"], "invalid_input")


class ReportPathSafetyTests(unittest.TestCase):
    """Path-traversal regression tests for _report_output_path / _safe_component."""

    def test_safe_component_keeps_valid_chars(self):
        self.assertEqual(_safe_component("my-project_01"), "my-project_01")
        self.assertEqual(_safe_component("quarterly"), "quarterly")
        self.assertEqual(_safe_component("semi_annual"), "semi_annual")
        self.assertEqual(_safe_component("custom"), "custom")

    def test_safe_component_replaces_traversal_chars(self):
        self.assertEqual(_safe_component("../../../etc"), "_________etc")
        self.assertEqual(_safe_component("a\\b/c"), "a_b_c")
        self.assertEqual(_safe_component("foo.bar"), "foo_bar")
        self.assertEqual(_safe_component("..%00"), "___00")

    def test_safe_component_none_or_empty_yields_x(self):
        self.assertEqual(_safe_component(None), "x")
        self.assertEqual(_safe_component(""), "x")
        self.assertEqual(_safe_component("   "), "___")  # spaces → _, then non-empty so stays

    def test_project_summary_path_stays_in_workspace(self):
        from pathlib import Path
        ws = Path("C:/work/my-workspace")
        p = _report_output_path(ws, "project_summary", "2026-01-01", "2026-01-01",
                                project_id="../../../Windows/Temp/pwned",
                                range_label="")
        # Must stay under workspace/reports/project/
        self.assertTrue(p.as_posix().startswith("C:/work/my-workspace/reports/project/"))
        self.assertNotIn("..", p.as_posix())

    def test_range_path_stays_in_workspace(self):
        from pathlib import Path
        ws = Path("C:/work/my-workspace")
        p = _report_output_path(ws, "range", "2026-01-01", "2026-01-01",
                                project_id=None,
                                range_label="../../../..")
        self.assertIn("C:/work/my-workspace/reports/range/", p.as_posix())
        self.assertNotIn("..", p.as_posix())

    def test_range_label_quarterly_stays_intact(self):
        from pathlib import Path
        ws = Path("C:/work/my-workspace")
        p = _report_output_path(ws, "range", "2026-01-01", "2026-01-01",
                                project_id=None, range_label="quarterly")
        self.assertIn("_to_2026-01-01-quarterly", p.name)

    def test_normal_project_id_unchanged(self):
        from pathlib import Path
        ws = Path("C:/work/my-workspace")
        p = _report_output_path(ws, "project_summary", "2026-01-01", "2026-01-01",
                                project_id="my-project", range_label="")
        self.assertIn("my-project", p.name)


class InboxHandlerTests(unittest.TestCase):
    """Tests for inbox_create, inbox_list, inbox_process, inbox_commit, inbox_cancel."""

    def test_inbox_create_and_list_returns_processing_card(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            result = handle_inbox_create({
                "workspace": str(ws),
                "text": "mapped the cache blocker",
                "attachments": [],
            })

            self.assertTrue(result["ok"])
            self.assertEqual(result["card"]["state"], "processing")

            listed = handle_inbox_list({"workspace": str(ws)})
            self.assertTrue(listed["ok"])
            self.assertEqual(listed["cards"][0]["capture_id"], result["card"]["capture_id"])

    def test_inbox_cancel_cleans_pending_attachment(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            src = ws / "clip.png"
            src.write_bytes(b"image")
            created = handle_inbox_create({
                "workspace": str(ws),
                "text": "cancel this",
                "attachments": [{"temp_path": str(src), "filename": "clip.png"}],
            })

            canceled = handle_inbox_cancel({
                "workspace": str(ws),
                "capture_id": created["card"]["capture_id"],
            })

            self.assertTrue(canceled["ok"])
            self.assertEqual(canceled["card"]["state"], "canceled")
            self.assertFalse((ws / ".workeventagent" / "pending" / created["card"]["capture_id"]).exists())

    @patch("workeventagent.gui.run_project_router")
    @patch("workeventagent.gui.run_archivist")
    def test_inbox_process_no_router(self, run_archivist, run_project_router):
        run_archivist.return_value = """```json
{
  "target": {"project_id": "test-proj", "item_id": "item-a", "task_id": "task-1"},
  "confidence": 0.95,
  "reason": "single project match",
  "event": {"task_id": "task-1", "input_text": "mapped the cache blocker", "summary": "Mapped cache blockers", "status": "in_progress", "next_action": "Continue mapping"}
}
```"""

        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            # Create a project so scan_workspace finds one
            from workeventagent.gui import handle_init
            handle_init({
                "workspace": str(ws),
                "title": "Test Project",
                "project_id": "test-proj",
                "items": [{"title": "Item A", "tasks": ["Task 1"]}],
                "db_path": str(ws / "index.sqlite"),
            })

            created = handle_inbox_create({
                "workspace": str(ws),
                "text": "mapped the cache blocker",
                "attachments": [],
            })

            result = handle_inbox_process({
                "workspace": str(ws),
                "capture_id": created["card"]["capture_id"],
                "opencode_model": "anthropic/claude-sonnet-4-5",
            })

            self.assertTrue(result["ok"], str(result))
            self.assertEqual(result["card"]["state"], "needs_confirmation")
            run_project_router.assert_not_called()  # single project skips router
            run_archivist.assert_called_once()
            self.assertEqual(run_archivist.call_args.kwargs["model"], "anthropic/claude-sonnet-4-5")

    @patch("workeventagent.gui.run_archivist")
    @patch("workeventagent.gui.run_project_router")
    def test_inbox_commit_from_confirmed_card(self, run_project_router, run_archivist):
        # Set up archivist to return a valid proposal
        run_archivist.return_value = """```json
{
  "target": {"project_id": "test-proj", "item_id": "item-a", "task_id": "task-1"},
  "confidence": 0.95,
  "reason": "single project match",
  "event": {"task_id": "task-1", "input_text": "mapped the cache blocker", "summary": "Mapped cache blockers", "status": "in_progress", "next_action": "Continue mapping"}
}
```"""

        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            from workeventagent.gui import handle_init
            handle_init({
                "workspace": str(ws),
                "title": "Test Project",
                "project_id": "test-proj",
                "items": [{"title": "Item A", "tasks": ["Task 1"]}],
                "db_path": str(ws / "index.sqlite"),
            })

            created = handle_inbox_create({
                "workspace": str(ws),
                "text": "mapped the cache blocker",
                "attachments": [],
            })

            # Process to get proposal
            processed = handle_inbox_process({
                "workspace": str(ws),
                "capture_id": created["card"]["capture_id"],
            })
            self.assertTrue(processed["ok"], str(processed))

            result = handle_inbox_commit({
                "workspace": str(ws),
                "capture_id": created["card"]["capture_id"],
            })

            self.assertTrue(result["ok"], str(result))
            pending_dir = ws / ".workeventagent" / "pending" / created["card"]["capture_id"]
            self.assertFalse(pending_dir.exists())


class PhaseBImpactCommitTest(unittest.TestCase):
    def _setup_confirmed_card(self, workspace: Path, *, impact_level: str) -> tuple[dict, Path]:
        initialized = handle_init({
            "workspace": str(workspace),
            "title": "Impact Test",
            "project_id": "impact-test",
            "items": [{"title": "Item A", "tasks": ["Task 1"]}],
            "db_path": str(workspace / "index.sqlite"),
        })
        project = Path(initialized["project_path"])
        task_id = handle_tasks({"project_path": str(project)})["items"][0]["tasks"][0]["task_id"]
        created = handle_inbox_create({"workspace": str(workspace), "text": "scope changed", "attachments": []})
        proposal = {
            "target": {
                "project_id": "impact-test",
                "item_id": "item-a",
                "task_id": task_id,
                "task_title": "",
                "new_item": False,
                "new_task": False,
            },
            "confidence": 0.96,
            "reason": "Matched",
            "event": {
                "event_id": "20260720-083000000-task-1",
                "task_id": task_id,
                "input_text": "Project scope changed.",
                "summary": "Scope now includes recovery.",
                "status": "in_progress",
                "next_action": "Implement recovery.",
            },
            "attachment_paths": [],
        }
        impact = {
            "level": impact_level,
            "dimensions": ["scope"] if impact_level == "high" else [],
            "reason": "Project scope changed." if impact_level == "high" else "Task evidence only.",
        }
        card = update_capture(
            workspace,
            created["card"]["capture_id"],
            {
                "state": "needs_confirmation",
                "proposal": proposal,
                "selected_project": {"project_id": "impact-test", "path": str(project)},
                "knowledge_impact": impact,
            },
        )
        return card, project

    @patch("workeventagent.gui.run_archivist")
    def test_propose_exposes_high_impact_before_confirmation(self, run_archivist):
        run_archivist.return_value = """{
          "target": {"project_id": "multimodal-labeling", "item_id": "kv-cache-few-shot", "task_id": "kv-cache-blockers"},
          "confidence": 0.91,
          "reason": "Matched.",
          "event": {"task_id": "kv-cache-blockers", "input_text": "input", "summary": "summary", "status": "in_progress", "next_action": "next"},
          "knowledge_impact": {"level": "high", "dimensions": ["architecture"], "reason": "Architecture changed."}
        }"""
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project.md"
            project.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
            result = handle_propose({"text": "architecture changed", "project_path": str(project)})

        self.assertEqual(result["knowledge_impact"]["level"], "high")
        self.assertEqual(result["knowledge_impact"]["dimensions"], ["architecture"])

    def test_ordinary_capture_commit_creates_no_knowledge_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            card, _project = self._setup_confirmed_card(workspace, impact_level="ordinary")

            result = handle_inbox_commit({"workspace": str(workspace), "capture_id": card["capture_id"]})

            self.assertTrue(result["ok"], str(result))
            self.assertEqual(list_jobs(workspace), [])
            self.assertNotIn("knowledge_job_id", result)

    def test_high_impact_job_exists_before_project_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            card, _project = self._setup_confirmed_card(workspace, impact_level="high")
            event_id = card["proposal"]["event"]["event_id"]
            expected_job_id = job_id_for(f"high-impact:impact-test:{event_id}")

            def stop_after_check(_request):
                self.assertEqual(get_job(workspace, expected_job_id)["state"], "awaiting_source")
                raise RuntimeError("stop after order assertion")

            with patch("workeventagent.gui.handle_commit", side_effect=stop_after_check):
                result = handle_inbox_commit({"workspace": str(workspace), "capture_id": card["capture_id"]})

            self.assertFalse(result["ok"])
            self.assertEqual(get_job(workspace, expected_job_id)["state"], "awaiting_source")

    def test_high_impact_job_uses_frontmatter_project_id_not_agent_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            card, _project = self._setup_confirmed_card(workspace, impact_level="high")
            card["proposal"]["target"]["project_id"] = "agent-forged-id"
            update_capture(workspace, card["capture_id"], {"proposal": card["proposal"]})

            result = handle_inbox_commit({"workspace": str(workspace), "capture_id": card["capture_id"]})

            event_id = card["proposal"]["event"]["event_id"]
            trusted_job = get_job(workspace, job_id_for(f"high-impact:impact-test:{event_id}"))
            self.assertTrue(result["ok"], str(result))
            self.assertEqual(trusted_job["project_id"], "impact-test")
            self.assertFalse(any(job["project_id"] == "agent-forged-id" for job in list_jobs(workspace)))

    def test_high_impact_job_is_queued_only_after_source_event_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            card, project = self._setup_confirmed_card(workspace, impact_level="high")

            result = handle_inbox_commit({"workspace": str(workspace), "capture_id": card["capture_id"]})

            event_id = card["proposal"]["event"]["event_id"]
            job_id = job_id_for(f"high-impact:impact-test:{event_id}")
            self.assertTrue(result["ok"], str(result))
            self.assertEqual(result["knowledge_job_id"], job_id)
            self.assertEqual(get_job(workspace, job_id)["state"], "queued")
            self.assertIn(event_id, {event["event_id"] for event in parse_timeline_events(project.read_text(encoding="utf-8"))})

    def test_failed_project_commit_leaves_visible_recoverable_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            card, _project = self._setup_confirmed_card(workspace, impact_level="high")
            event_id = card["proposal"]["event"]["event_id"]
            job_id = job_id_for(f"high-impact:impact-test:{event_id}")

            with patch("workeventagent.gui.handle_commit", side_effect=OSError("disk full")):
                result = handle_inbox_commit({"workspace": str(workspace), "capture_id": card["capture_id"]})

            self.assertFalse(result["ok"])
            self.assertEqual(get_job(workspace, job_id)["state"], "awaiting_source")

    def test_commit_returns_and_inbox_archives_real_event_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            card, _project = self._setup_confirmed_card(workspace, impact_level="ordinary")
            event_id = card["proposal"]["event"]["event_id"]

            result = handle_inbox_commit({"workspace": str(workspace), "capture_id": card["capture_id"]})

            self.assertTrue(result["ok"], str(result))
            archived = next(item for item in list_captures(workspace) if item["capture_id"] == card["capture_id"])
            self.assertEqual(archived["event_id"], event_id)


class PhaseBKnowledgeHandlersTest(unittest.TestCase):
    _setup_confirmed_card = PhaseBImpactCommitTest._setup_confirmed_card

    def _setup_project(self, workspace: Path, *, split_dates: bool = False) -> Path:
        text = Path("tests/fixtures/project-v2.md").read_text(encoding="utf-8")
        if split_dates:
            text = text.replace("2026-07-13T11:00:00+08:00", "2026-07-14T11:00:00+08:00")
        project = workspace / "report-project.md"
        project.write_text(text, encoding="utf-8")
        return project

    @staticmethod
    def _agent_output(*targets: str, with_document: bool = False) -> str:
        changes = []
        for target in targets:
            paragraph = (
                "Retain this architecture summary."
                if target == "technical-overview" and with_document
                else f"Updated {target}."
            )
            changes.append({
                "target_section": target,
                "reason": "Evidence supports this update.",
                "content": {"paragraphs": [paragraph], "bullets": []},
            })
        suggestion = None
        if with_document:
            suggestion = {
                "purpose": "Explain architecture in depth.",
                "title": "Architecture",
                "retained_summary": "Retain this architecture summary.",
                "module_conclusion": {"paragraphs": ["Architecture conclusion."], "bullets": []},
                "module_body": {"paragraphs": ["Architecture body."], "bullets": []},
            }
        return json.dumps({"changes": changes, "document_suggestion": suggestion})

    def test_directed_enqueue_rejects_cross_project_or_missing_events(self):
        from workeventagent import gui as gui_module

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            project = self._setup_project(workspace)
            ok = gui_module.handle_knowledge_enqueue({
                "workspace": str(workspace), "trigger": "directed",
                "project_path": str(project), "event_ids": ["event-b", "event-a"],
            })
            missing = gui_module.handle_knowledge_enqueue({
                "workspace": str(workspace), "trigger": "directed",
                "project_path": str(project), "event_ids": ["other-project-event"],
            })
            outside = workspace.parent / "outside.md"
            outside.write_text(project.read_text(encoding="utf-8"), encoding="utf-8")
            cross = gui_module.handle_knowledge_enqueue({
                "workspace": str(workspace), "trigger": "directed",
                "project_path": str(outside), "event_ids": ["event-a"],
            })

            self.assertTrue(ok["ok"], str(ok))
            self.assertEqual(ok["job"]["source_event_ids"], ["event-b", "event-a"])
            self.assertFalse(missing["ok"])
            self.assertFalse(cross["ok"])

    def test_high_impact_process_requires_committed_source(self):
        from workeventagent import gui as gui_module

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            project = self._setup_project(workspace)
            job = enqueue_job(workspace, {
                "idempotency_key": "high-impact:report-project:event-missing",
                "state": "awaiting_source", "project_id": "report-project",
                "project_path": str(project), "trigger": "high_impact",
                "source_event_ids": ["event-missing"],
            })

            result = gui_module.handle_knowledge_process_job({"workspace": str(workspace), "job_id": job["job_id"]})

            self.assertFalse(result["ok"])
            self.assertEqual(get_job(workspace, job["job_id"])["state"], "awaiting_source")

    @patch("workeventagent.gui.run_project_synthesizer")
    def test_daily_job_selects_only_local_date_range_evidence(self, run_synthesizer):
        from workeventagent import gui as gui_module

        run_synthesizer.return_value = self._agent_output("current-panorama")
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            project = self._setup_project(workspace, split_dates=True)
            job = enqueue_job(workspace, {
                "idempotency_key": "daily:report-project:2026-07-13", "state": "queued",
                "project_id": "report-project", "project_path": str(project), "trigger": "daily",
                "source_event_ids": [], "date_from": "2026-07-13", "date_to": "2026-07-13",
            })

            result = gui_module.handle_knowledge_process_job({"workspace": str(workspace), "job_id": job["job_id"]})

            self.assertTrue(result["ok"], str(result))
            prompt = run_synthesizer.call_args.args[0]
            self.assertIn("event-a", prompt)
            self.assertNotIn("event-b", prompt)

    @patch("workeventagent.gui.run_project_synthesizer")
    def test_job_consumer_rejects_project_id_that_does_not_match_frontmatter(self, run_synthesizer):
        from workeventagent import gui as gui_module

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            project = self._setup_project(workspace)
            job = enqueue_job(workspace, {
                "idempotency_key": "directed:agent-forged-id:event-a",
                "state": "queued",
                "project_id": "agent-forged-id",
                "project_path": str(project),
                "trigger": "directed",
                "source_event_ids": ["event-a"],
            })

            result = gui_module.handle_knowledge_process_job({
                "workspace": str(workspace), "job_id": job["job_id"]
            })

            self.assertFalse(result["ok"])
            self.assertIn("project identity", result["error"])
            self.assertEqual(get_job(workspace, job["job_id"])["state"], "failed")
            run_synthesizer.assert_not_called()

    @patch("workeventagent.gui.run_project_synthesizer")
    def test_job_consumer_rejects_project_path_outside_workspace(self, run_synthesizer):
        from workeventagent import gui as gui_module

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            outside = root / "outside.md"
            outside.write_text(Path("tests/fixtures/project-v2.md").read_text(encoding="utf-8"), encoding="utf-8")
            job = enqueue_job(workspace, {
                "idempotency_key": "directed:outside:event-a",
                "state": "queued",
                "project_id": "report-project",
                "project_path": str(outside),
                "trigger": "directed",
                "source_event_ids": ["event-a"],
            })

            result = gui_module.handle_knowledge_process_job({
                "workspace": str(workspace), "job_id": job["job_id"]
            })

            self.assertFalse(result["ok"])
            self.assertIn("inside workspace", result["error"])
            run_synthesizer.assert_not_called()

    @patch("workeventagent.gui.run_project_synthesizer")
    def test_weekly_job_runs_full_review_prompt_with_week_evidence(self, run_synthesizer):
        from workeventagent import gui as gui_module

        run_synthesizer.return_value = self._agent_output("current-panorama", "technical-overview", "project-knowledge")
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            project = self._setup_project(workspace)
            job = enqueue_job(workspace, {
                "idempotency_key": "weekly:report-project:2026-W29", "state": "queued",
                "project_id": "report-project", "project_path": str(project), "trigger": "weekly",
                "source_event_ids": [], "date_from": "2026-07-13", "date_to": "2026-07-19",
            })

            result = gui_module.handle_knowledge_process_job({"workspace": str(workspace), "job_id": job["job_id"]})

            self.assertTrue(result["ok"], str(result))
            prompt = run_synthesizer.call_args.args[0]
            self.assertIn("full Phase B review", prompt)
            self.assertIn("event-a", prompt)
            self.assertIn("event-b", prompt)

    def test_schedule_enqueue_persists_full_project_manifest_before_children(self):
        from workeventagent import gui as gui_module
        from workeventagent.knowledge_store import ensure_schedule_children as real_ensure

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            first = self._setup_project(workspace)
            second = workspace / "second.md"
            second.write_text(
                first.read_text(encoding="utf-8").replace("project_id: report-project", "project_id: second"),
                encoding="utf-8",
            )

            def assert_manifest_then_enqueue(ws, run_id):
                run_path = ws / ".workeventagent" / "knowledge" / "runs" / f"{run_id}.json"
                self.assertTrue(run_path.exists())
                self.assertEqual(len(json.loads(run_path.read_text(encoding="utf-8"))["expected_children"]), 2)
                return real_ensure(ws, run_id)

            with patch("workeventagent.knowledge_store.ensure_schedule_children", side_effect=assert_manifest_then_enqueue):
                result = gui_module.handle_knowledge_enqueue_schedule({
                    "workspace": str(workspace), "cadence": "daily", "schedule_key": "2026-07-20",
                    "date_from": "2026-07-20", "date_to": "2026-07-20",
                    "range_start_utc": "2026-07-19T16:00:00.000Z",
                    "range_end_utc": "2026-07-20T16:00:00.000Z",
                })

            self.assertTrue(result["ok"], str(result))
            self.assertEqual(len(result["run"]["expected_children"]), 2)
            for child in result["run"]["expected_children"]:
                self.assertEqual(child["job_spec"]["range_start_utc"], "2026-07-19T16:00:00.000Z")
                self.assertEqual(child["job_spec"]["range_end_utc"], "2026-07-20T16:00:00.000Z")

    def test_schedule_recovery_completes_missing_children_after_partial_enqueue(self):
        from workeventagent import gui as gui_module
        from workeventagent.knowledge_store import create_schedule_run

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            first = self._setup_project(workspace)
            second = workspace / "second.md"
            second.write_text(
                first.read_text(encoding="utf-8").replace("project_id: report-project", "project_id: second"),
                encoding="utf-8",
            )
            run = create_schedule_run(
                workspace, "daily", "2026-07-20",
                [{"project_id": "report-project", "project_path": str(first)},
                 {"project_id": "second", "project_path": str(second)}],
            )
            enqueue_job(workspace, run["expected_children"][0]["job_spec"])

            result = gui_module.handle_knowledge_recover({"workspace": str(workspace)})

            self.assertTrue(result["ok"])
            self.assertEqual(len(list_jobs(workspace)), 2)

    @patch("workeventagent.gui.run_project_synthesizer")
    def test_process_persists_proposal_before_completing_job(self, run_synthesizer):
        from workeventagent import gui as gui_module
        from workeventagent.knowledge_store import transition_job as real_transition

        run_synthesizer.return_value = self._agent_output("current-panorama")
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            project = self._setup_project(workspace)
            enqueued = gui_module.handle_knowledge_enqueue({
                "workspace": str(workspace), "trigger": "directed",
                "project_path": str(project), "event_ids": ["event-a"],
            })

            def assert_before_complete(ws, job_id, expected_version, from_states, to_state, patch=None):
                if to_state == "completed":
                    self.assertGreater(len(list_knowledge_proposals(ws)), 0)
                return real_transition(ws, job_id, expected_version, from_states, to_state, patch)

            with patch("workeventagent.gui.transition_job", side_effect=assert_before_complete):
                result = gui_module.handle_knowledge_process_job({
                    "workspace": str(workspace), "job_id": enqueued["job"]["job_id"]
                })

            self.assertTrue(result["ok"], str(result))

    @patch("workeventagent.gui.run_project_synthesizer", side_effect=RuntimeError("agent down"))
    def test_agent_failure_marks_job_failed_and_leaves_project_unchanged(self, _run_synthesizer):
        from workeventagent import gui as gui_module

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            project = self._setup_project(workspace)
            before = project.read_bytes()
            enqueued = gui_module.handle_knowledge_enqueue({
                "workspace": str(workspace), "trigger": "directed",
                "project_path": str(project), "event_ids": ["event-a"],
            })

            result = gui_module.handle_knowledge_process_job({
                "workspace": str(workspace), "job_id": enqueued["job"]["job_id"]
            })

            self.assertFalse(result["ok"])
            self.assertEqual(get_job(workspace, enqueued["job"]["job_id"])["state"], "failed")
            self.assertEqual(project.read_bytes(), before)

    @patch("workeventagent.gui.run_project_synthesizer")
    def test_no_evidence_and_no_change_are_explicit_terminal_states(self, run_synthesizer):
        from workeventagent import gui as gui_module

        run_synthesizer.return_value = self._agent_output()
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            project = self._setup_project(workspace)
            no_evidence = enqueue_job(workspace, {
                "idempotency_key": "daily:none", "state": "queued", "project_id": "report-project",
                "project_path": str(project), "trigger": "daily", "source_event_ids": [],
                "date_from": "2026-07-20", "date_to": "2026-07-20",
            })
            no_change = enqueue_job(workspace, {
                "idempotency_key": "directed:no-change", "state": "queued", "project_id": "report-project",
                "project_path": str(project), "trigger": "directed", "source_event_ids": ["event-a"],
            })

            first = gui_module.handle_knowledge_process_job({"workspace": str(workspace), "job_id": no_evidence["job_id"]})
            second = gui_module.handle_knowledge_process_job({"workspace": str(workspace), "job_id": no_change["job_id"]})

            self.assertEqual(first["job"]["state"], "skipped_no_evidence")
            self.assertEqual(second["job"]["state"], "skipped_no_change")

    def test_retry_is_explicit_idempotent_and_cas_guarded(self):
        from workeventagent import gui as gui_module

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            project = self._setup_project(workspace)
            job = enqueue_job(workspace, {
                "idempotency_key": "directed:failed", "state": "queued", "project_id": "report-project",
                "project_path": str(project), "trigger": "directed", "source_event_ids": ["event-a"],
            })
            job = transition_job(workspace, job["job_id"], 1, {"queued"}, "processing")
            job = transition_job(workspace, job["job_id"], 2, {"processing"}, "failed")

            retried = gui_module.handle_knowledge_retry_job({
                "workspace": str(workspace), "job_id": job["job_id"], "expected_version": job["version"]
            })
            stale = gui_module.handle_knowledge_retry_job({
                "workspace": str(workspace), "job_id": job["job_id"], "expected_version": job["version"]
            })

            self.assertTrue(retried["ok"])
            self.assertFalse(stale["ok"])

    @patch("workeventagent.gui.run_project_synthesizer")
    def test_knowledge_state_aggregates_jobs_and_proposals_without_capture_retention(self, run_synthesizer):
        from workeventagent import gui as gui_module

        run_synthesizer.return_value = self._agent_output("current-panorama")
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            project = self._setup_project(workspace)
            enqueued = gui_module.handle_knowledge_enqueue({
                "workspace": str(workspace), "trigger": "directed", "project_path": str(project),
                "event_ids": ["event-a"],
            })
            gui_module.handle_knowledge_process_job({
                "workspace": str(workspace), "job_id": enqueued["job"]["job_id"]
            })

            state = gui_module.handle_knowledge_state({"workspace": str(workspace)})

            self.assertTrue(state["ok"])
            self.assertEqual(len(state["jobs"]), 1)
            self.assertEqual(len(state["proposals"]), 1)
            self.assertNotIn("captures", state)

    @patch("workeventagent.gui.run_project_synthesizer")
    def test_document_suggestion_is_persisted_as_separate_confirmation(self, run_synthesizer):
        from workeventagent import gui as gui_module

        run_synthesizer.return_value = self._agent_output("technical-overview", with_document=True)
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            project = self._setup_project(workspace)
            enqueued = gui_module.handle_knowledge_enqueue({
                "workspace": str(workspace), "trigger": "directed", "project_path": str(project),
                "event_ids": ["event-a"],
            })

            result = gui_module.handle_knowledge_process_job({
                "workspace": str(workspace), "job_id": enqueued["job"]["job_id"]
            })

            self.assertTrue(result["ok"], str(result))
            proposals = list_knowledge_proposals(workspace)
            self.assertEqual({item["proposal_kind"] for item in proposals}, {"section_bundle", "module_document"})

    @patch("workeventagent.gui.run_project_synthesizer")
    def test_unicode_title_line_separators_fail_before_proposal_persistence(self, run_synthesizer):
        from workeventagent import gui as gui_module

        for separator in ("\u0085", "\u2028", "\u2029"):
            with self.subTest(separator=ascii(separator)), tempfile.TemporaryDirectory() as tmp:
                workspace = Path(tmp)
                project = self._setup_project(workspace)
                output = json.loads(self._agent_output("technical-overview", with_document=True))
                output["document_suggestion"]["title"] = f"Architecture{separator}---"
                run_synthesizer.return_value = json.dumps(output)
                enqueued = gui_module.handle_knowledge_enqueue({
                    "workspace": str(workspace), "trigger": "directed", "project_path": str(project),
                    "event_ids": ["event-a"],
                })

                result = gui_module.handle_knowledge_process_job({
                    "workspace": str(workspace), "job_id": enqueued["job"]["job_id"]
                })

                self.assertFalse(result["ok"])
                self.assertEqual(list_knowledge_proposals(workspace), [])

    @patch("workeventagent.gui.run_project_synthesizer")
    def test_revision_persists_subset_then_supersedes_old_with_cas(self, run_synthesizer):
        from workeventagent import gui as gui_module

        run_synthesizer.return_value = self._agent_output("current-panorama", "project-knowledge")
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            project = self._setup_project(workspace)
            enqueued = gui_module.handle_knowledge_enqueue({
                "workspace": str(workspace), "trigger": "directed", "project_path": str(project),
                "event_ids": ["event-a"],
            })
            processed = gui_module.handle_knowledge_process_job({
                "workspace": str(workspace), "job_id": enqueued["job"]["job_id"]
            })
            original = get_proposal(workspace, processed["proposal_ids"][0])

            revised = gui_module.handle_knowledge_revise_proposal({
                "workspace": str(workspace), "proposal_id": original["proposal_id"],
                "expected_version": original["version"],
                "included_change_ids": ["change-project-knowledge"],
            })
            stale = gui_module.handle_knowledge_revise_proposal({
                "workspace": str(workspace), "proposal_id": original["proposal_id"],
                "expected_version": original["version"],
                "included_change_ids": ["change-current-panorama"],
            })

            self.assertTrue(revised["ok"], str(revised))
            self.assertEqual(revised["superseded"]["state"], "superseded")
            self.assertEqual(
                [change["change_id"] for change in revised["proposal"]["changes"]],
                ["change-project-knowledge"],
            )
            self.assertFalse(stale["ok"])

    @patch("workeventagent.gui.run_project_synthesizer")
    def test_rejection_is_cas_guarded_and_remains_auditable(self, run_synthesizer):
        from workeventagent import gui as gui_module

        run_synthesizer.return_value = self._agent_output("current-panorama")
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            project = self._setup_project(workspace)
            enqueued = gui_module.handle_knowledge_enqueue({
                "workspace": str(workspace), "trigger": "directed", "project_path": str(project),
                "event_ids": ["event-a"],
            })
            processed = gui_module.handle_knowledge_process_job({
                "workspace": str(workspace), "job_id": enqueued["job"]["job_id"]
            })
            proposal = get_proposal(workspace, processed["proposal_ids"][0])

            rejected = gui_module.handle_knowledge_reject_proposal({
                "workspace": str(workspace), "proposal_id": proposal["proposal_id"],
                "expected_version": proposal["version"],
            })
            stale = gui_module.handle_knowledge_reject_proposal({
                "workspace": str(workspace), "proposal_id": proposal["proposal_id"],
                "expected_version": proposal["version"],
            })

            self.assertTrue(rejected["ok"])
            self.assertEqual(get_proposal(workspace, proposal["proposal_id"])["state"], "rejected")
            self.assertFalse(stale["ok"])

    @patch("workeventagent.gui.run_project_synthesizer")
    def test_apply_handler_uses_durable_proposal_and_marks_applied(self, run_synthesizer):
        from workeventagent import gui as gui_module

        run_synthesizer.return_value = self._agent_output("current-panorama")
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            project = self._setup_project(workspace)
            enqueued = gui_module.handle_knowledge_enqueue({
                "workspace": str(workspace), "trigger": "directed", "project_path": str(project),
                "event_ids": ["event-a"],
            })
            processed = gui_module.handle_knowledge_process_job({
                "workspace": str(workspace), "job_id": enqueued["job"]["job_id"]
            })
            proposal = get_proposal(workspace, processed["proposal_ids"][0])
            job_ids_before_apply = [job["job_id"] for job in list_jobs(workspace)]

            result = gui_module.handle_knowledge_apply_proposal({
                "workspace": str(workspace), "project_path": str(project),
                "db_path": str(workspace / "index.sqlite"), "proposal_id": proposal["proposal_id"],
                "expected_version": proposal["version"], "today": "2026-07-20",
            })

            self.assertTrue(result["ok"], str(result))
            self.assertEqual(get_proposal(workspace, proposal["proposal_id"])["state"], "applied")
            self.assertEqual(
                [job["job_id"] for job in list_jobs(workspace)],
                job_ids_before_apply,
                "applying synthesis must not trigger another synthesis job",
            )

    @patch("workeventagent.gui.run_project_synthesizer")
    def test_stale_proposal_regeneration_gets_new_idempotent_job(self, run_synthesizer):
        from workeventagent import gui as gui_module
        from workeventagent.knowledge_store import transition_proposal as durable_transition

        run_synthesizer.return_value = self._agent_output("current-panorama")
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            project = self._setup_project(workspace)
            request = {
                "workspace": str(workspace), "trigger": "directed",
                "project_path": str(project), "event_ids": ["event-a"],
            }
            original = gui_module.handle_knowledge_enqueue(request)
            processed = gui_module.handle_knowledge_process_job({
                "workspace": str(workspace), "job_id": original["job"]["job_id"]
            })
            proposal = get_proposal(workspace, processed["proposal_ids"][0])
            durable_transition(
                workspace, proposal["proposal_id"], proposal["version"],
                {"needs_confirmation"}, "stale",
            )

            regenerated = gui_module.handle_knowledge_enqueue({
                **request, "regenerate_of": proposal["proposal_id"]
            })
            repeated = gui_module.handle_knowledge_enqueue({
                **request, "regenerate_of": proposal["proposal_id"]
            })

            self.assertTrue(regenerated["ok"])
            self.assertEqual(regenerated["job"]["state"], "queued")
            self.assertNotEqual(regenerated["job"]["job_id"], original["job"]["job_id"])
            self.assertEqual(repeated["job"]["job_id"], regenerated["job"]["job_id"])

    @patch("workeventagent.gui.run_project_synthesizer")
    def test_document_apply_handler_requires_separate_confirmation(self, run_synthesizer):
        from workeventagent import gui as gui_module

        run_synthesizer.return_value = self._agent_output("technical-overview", with_document=True)
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            project = self._setup_project(workspace)
            enqueued = gui_module.handle_knowledge_enqueue({
                "workspace": str(workspace), "trigger": "directed", "project_path": str(project),
                "event_ids": ["event-a"],
            })
            processed = gui_module.handle_knowledge_process_job({
                "workspace": str(workspace), "job_id": enqueued["job"]["job_id"]
            })
            proposals = [get_proposal(workspace, proposal_id) for proposal_id in processed["proposal_ids"]]
            section = next(item for item in proposals if item["proposal_kind"] == "section_bundle")
            document = next(item for item in proposals if item["proposal_kind"] == "module_document")
            gui_module.handle_knowledge_apply_proposal({
                "workspace": str(workspace), "project_path": str(project),
                "db_path": str(workspace / "index.sqlite"), "proposal_id": section["proposal_id"],
                "expected_version": section["version"], "today": "2026-07-20",
            })

            result = gui_module.handle_knowledge_apply_document({
                "workspace": str(workspace), "project_path": str(project),
                "proposal_id": document["proposal_id"], "expected_version": document["version"],
                "today": "2026-07-20",
            })

            self.assertTrue(result["ok"], str(result))
            self.assertTrue((workspace / document["target_path"]).is_file())

    @patch("workeventagent.gui.run_project_synthesizer")
    def test_knowledge_recover_reconciles_applying_proposals_before_state(self, run_synthesizer):
        from workeventagent import gui as gui_module
        from workeventagent.knowledge_store import transition_proposal as durable_transition

        run_synthesizer.return_value = self._agent_output("current-panorama")
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            project = self._setup_project(workspace)
            enqueued = gui_module.handle_knowledge_enqueue({
                "workspace": str(workspace), "trigger": "directed", "project_path": str(project),
                "event_ids": ["event-a"],
            })
            processed = gui_module.handle_knowledge_process_job({
                "workspace": str(workspace), "job_id": enqueued["job"]["job_id"]
            })
            proposal = get_proposal(workspace, processed["proposal_ids"][0])
            durable_transition(
                workspace, proposal["proposal_id"], proposal["version"], {"needs_confirmation"}, "applying"
            )

            recovered = gui_module.handle_knowledge_recover({"workspace": str(workspace)})

            self.assertTrue(recovered["ok"])
            self.assertIn(proposal["proposal_id"], recovered["recovered_proposal_ids"])
            self.assertEqual(get_proposal(workspace, proposal["proposal_id"])["state"], "applied")

    def test_retry_same_event_id_and_content_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project.md"
            db = Path(tmp) / "index.sqlite"
            project.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
            proposal = _make_mock_proposal_data()
            first = handle_commit({"proposal": proposal, "project_path": str(project), "db_path": str(db)})
            after_first = project.read_text(encoding="utf-8")

            second = handle_commit({"proposal": proposal, "project_path": str(project), "db_path": str(db)})

            self.assertTrue(first["ok"] and second["ok"])
            self.assertTrue(second["idempotent"])
            self.assertEqual(project.read_text(encoding="utf-8"), after_first)
            self.assertEqual(after_first.count(f"<!-- event:{proposal['event']['event_id']} -->"), 1)

    def test_retry_same_event_id_with_different_content_is_hard_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project.md"
            db = Path(tmp) / "index.sqlite"
            project.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
            proposal = _make_mock_proposal_data()
            handle_commit({"proposal": proposal, "project_path": str(project), "db_path": str(db)})
            before = project.read_text(encoding="utf-8")
            conflicting = _make_mock_proposal_data({"event": {"summary": "Different meaning."}})

            result = handle_commit({"proposal": conflicting, "project_path": str(project), "db_path": str(db)})

            self.assertFalse(result["ok"])
            self.assertEqual(result["kind"], "event_id_conflict")
            self.assertEqual(project.read_text(encoding="utf-8"), before)

    def test_startup_recovers_event_written_before_inbox_archive_and_job_promote(self):
        from workeventagent.gui import handle_knowledge_recover

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            card, project = self._setup_confirmed_card(workspace, impact_level="high")
            event_id = card["proposal"]["event"]["event_id"]
            job = enqueue_job(
                workspace,
                {
                    "idempotency_key": f"high-impact:impact-test:{event_id}",
                    "state": "awaiting_source",
                    "project_id": "impact-test",
                    "project_path": str(project),
                    "trigger": "high_impact",
                    "source_event_ids": [event_id],
                    "capture_id": card["capture_id"],
                },
            )
            committed = handle_commit({
                "proposal": card["proposal"],
                "project_path": str(project),
                "db_path": str(workspace / "index.sqlite"),
            })
            self.assertTrue(committed["ok"])

            recovered = handle_knowledge_recover({"workspace": str(workspace)})

            self.assertTrue(recovered["ok"])
            self.assertEqual(get_job(workspace, job["job_id"])["state"], "queued")
            archived = next(item for item in list_captures(workspace) if item["capture_id"] == card["capture_id"])
            self.assertEqual(archived["state"], "archived")
            self.assertEqual(archived["event_id"], event_id)


class SearchHandlerTests(unittest.TestCase):
    def test_search_rejects_empty_query(self):
        result = handle_search({"workspace": ".", "query": ""})
        self.assertFalse(result["ok"])
        self.assertEqual(result["kind"], "invalid_input")

    def test_search_finds_project_text(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            from workeventagent.gui import handle_init
            handle_init({
                "workspace": str(ws),
                "title": "Search Test Project",
                "project_id": "search-test",
                "items": [{"title": "Item X", "tasks": ["Task Alpha"]}],
                "db_path": str(ws / "index.sqlite"),
            })

            result = handle_search({"workspace": str(ws), "query": "Search Test Project"})
            self.assertTrue(result["ok"])
            self.assertTrue(any("Search Test Project" in r.get("title", "") for r in result["results"]))


class V2ProjectReadTest(unittest.TestCase):
    """All existing read paths must understand schema v2."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name)
        self.db = self.ws / "index.sqlite"
        init_db(self.db)
        self.project = self.ws / "report-project.md"
        self.project.write_text(
            Path("tests/fixtures/project-v2.md").read_text(encoding="utf-8"),
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_v2_project_is_readable_by_projects(self):
        result = handle_projects({"workspace": str(self.ws)})
        self.assertTrue(result["ok"])
        projects = result["projects"]
        self.assertTrue(any(p["project_id"] == "report-project" for p in projects))
        v2_project = next(p for p in projects if p["project_id"] == "report-project")
        self.assertEqual(v2_project["open_task_count"], 2)

    def test_v2_tasks_uses_typed_work_map_state(self):
        result = handle_tasks({"project_path": str(self.project)})
        items = result["items"]
        self.assertEqual(len(items), 2)
        capture = next(it for it in items if it["item_id"] == "capture")
        self.assertIn("background", capture)
        persist = next(t for t in capture["tasks"] if t["task_id"] == "persist-card")
        self.assertEqual(persist["status"], "done")
        self.assertEqual(persist["next_action"], "Add retry.")
        route = next(t for t in capture["tasks"] if t["task_id"] == "route-archive")
        self.assertEqual(route["status"], "in_progress")

    def test_v2_timeline_returns_events(self):
        result = handle_timeline({"project_path": str(self.project)})
        self.assertTrue(result["ok"])
        events = result["events"]
        self.assertEqual(len(events), 2)
        self.assertIn("event-a", [e["event_id"] for e in events])

    def test_v2_sqlite_rebuild_and_readback(self):
        rebuild_index(self.db, [self.project])
        task = get_task(self.db, "persist-card")
        self.assertIsNotNone(task)
        self.assertEqual(task["next_action"], "Add retry.")

    def test_v2_search_finds_v2_content(self):
        rebuild_index(self.db, [self.project])
        results = search_workspace(self.ws, "Persist card")
        self.assertTrue(any(r.get("kind") == "task" for r in results))


class V2PanoramaTests(unittest.TestCase):
    """Project panorama read and reviewed-section edits for schema v2."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name)
        self.db = self.ws / "index.sqlite"
        init_db(self.db)
        self.project = self.ws / "report-project.md"
        self.project.write_text(
            Path("tests/fixtures/project-v2.md").read_text(encoding="utf-8"),
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_project_panorama_returns_owned_sections_and_hashes(self):
        from workeventagent.gui import handle_project_panorama
        result = handle_project_panorama({"project_path": str(self.project)})
        self.assertTrue(result["ok"])
        self.assertEqual(result["schema_version"], 2)
        self.assertFalse(result.get("migration_required"))
        self.assertEqual(result["project"]["status"], "active")
        self.assertEqual(result["project"]["phase"], "implementation")
        self.assertIn("metadata_hash", result["project"])
        self.assertTrue(result["project"]["metadata_hash"].startswith("sha256:"))

        sections = result["sections"]
        self.assertIn("project-profile", sections)
        self.assertEqual(sections["project-profile"]["ownership"], "reviewed")
        self.assertIn("hash", sections["project-profile"])
        self.assertTrue(sections["project-profile"]["hash"].startswith("sha256:"))
        self.assertIn("title", sections["project-profile"])
        self.assertIn("content", sections["project-profile"])
        self.assertIn("source_event_ids", sections["project-profile"])

        self.assertIn("current-panorama", sections)
        self.assertEqual(sections["current-panorama"]["ownership"], "derived-reviewed")

        self.assertIn("technical-overview", sections)
        self.assertEqual(sections["technical-overview"]["ownership"], "reviewed")

        self.assertIn("timeline", sections)
        self.assertEqual(sections["timeline"]["ownership"], "append-only")

        # Control metadata is stripped from visible content
        self.assertNotIn("panorama-meta", sections["project-profile"]["content"])
        self.assertNotIn("section:", sections["timeline"]["content"])

    def test_panorama_content_strips_control_comments(self):
        from workeventagent.gui import handle_project_panorama
        result = handle_project_panorama({"project_path": str(self.project)})
        content = result["sections"]["technical-overview"]["content"]
        self.assertIn("Python", content)
        self.assertNotIn("<!--", content)

    def test_reviewed_edit_rejects_stale_hash_without_write(self):
        from workeventagent.gui import handle_update_project_section
        before = self.project.read_text(encoding="utf-8")
        result = handle_update_project_section({
            "project_path": str(self.project),
            "db_path": str(self.db),
            "section_id": "technical-overview",
            "base_section_hash": "sha256:stale",
            "content": "Python 负责写入。",
        })
        self.assertFalse(result["ok"])
        self.assertEqual(result["kind"], "stale_section")
        self.assertEqual(self.project.read_text(encoding="utf-8"), before)

    def test_reviewed_edit_rejects_protected_section(self):
        from workeventagent.gui import handle_update_project_section
        result = handle_update_project_section({
            "project_path": str(self.project),
            "db_path": str(self.db),
            "section_id": "timeline",
            "base_section_hash": "sha256:any",
            "content": "not allowed",
        })
        self.assertFalse(result["ok"])
        self.assertEqual(result["kind"], "invalid_operation")

    def test_profile_edit_updates_explicit_metadata_and_fixed_subsections(self):
        from workeventagent.gui import handle_project_panorama, handle_update_project_profile
        current = handle_project_panorama({"project_path": str(self.project)})
        self.assertTrue(current["ok"])

        result = handle_update_project_profile({
            "project_path": str(self.project),
            "db_path": str(self.db),
            "base_section_hash": current["sections"]["project-profile"]["hash"],
            "base_metadata_hash": current["project"]["metadata_hash"],
            "status": "active",
            "phase": "implementation",
            "background": "信息散落。",
            "goal": "形成可信项目全景。",
            "scope": "本地优先。",
            "success_criteria": "单文档可读。",
        })
        self.assertTrue(result["ok"], str(result))
        text = self.project.read_text(encoding="utf-8")
        self.assertIn("phase: implementation", text)
        self.assertIn("### 成功标准\n单文档可读。", text)
        self.assertIn("### 背景\n信息散落。", text)

    def test_profile_edit_rejects_stale_metadata_hash(self):
        from workeventagent.gui import handle_project_panorama, handle_update_project_profile
        current = handle_project_panorama({"project_path": str(self.project)})
        before = self.project.read_text(encoding="utf-8")
        result = handle_update_project_profile({
            "project_path": str(self.project),
            "db_path": str(self.db),
            "base_section_hash": current["sections"]["project-profile"]["hash"],
            "base_metadata_hash": "sha256:stale",
            "status": "active",
            "phase": "planning",
            "background": "",
            "goal": "",
            "scope": "",
            "success_criteria": "",
        })
        self.assertFalse(result["ok"])
        self.assertEqual(result["kind"], "stale_metadata")
        self.assertEqual(self.project.read_text(encoding="utf-8"), before)


class V2MutationTest(unittest.TestCase):
    """Delete/update handlers must work correctly on schema v2 documents."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name)
        self.db = self.ws / "index.sqlite"
        init_db(self.db)
        self.project = self.ws / "report-project.md"
        self.project.write_text(
            Path("tests/fixtures/project-v2.md").read_text(encoding="utf-8"),
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    # ── delete_item v2 ───────────────────────────────────

    def test_delete_item_v2_removes_item_and_its_tasks(self):
        before = handle_tasks({"project_path": str(self.project)})
        self.assertEqual(len(before["items"]), 2)
        capture = next(it for it in before["items"] if it["item_id"] == "capture")
        self.assertGreater(len(capture["tasks"]), 0)

        result = handle_delete_item({
            "project_path": str(self.project),
            "db_path": str(self.db),
            "item_id": "capture",
        })
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["item_id"], "capture")
        self.assertGreater(result["deleted_task_count"], 0)

        after = handle_tasks({"project_path": str(self.project)})
        self.assertEqual(len(after["items"]), 1)
        self.assertEqual(after["items"][0]["item_id"], "reporting")

    def test_delete_item_v2_preserves_sibling_item_and_tasks(self):
        before = handle_tasks({"project_path": str(self.project)})
        reporting = next(it for it in before["items"] if it["item_id"] == "reporting")
        reporting_task_count = len(reporting["tasks"])

        handle_delete_item({
            "project_path": str(self.project),
            "db_path": str(self.db),
            "item_id": "capture",
        })
        after = handle_tasks({"project_path": str(self.project)})
        self.assertEqual(len(after["items"]), 1)
        survivor = after["items"][0]
        self.assertEqual(survivor["item_id"], "reporting")
        self.assertEqual(len(survivor["tasks"]), reporting_task_count)

    # ── delete_task v2 ───────────────────────────────────

    def test_delete_task_v2_removes_only_target(self):
        before = handle_tasks({"project_path": str(self.project)})
        capture = next(it for it in before["items"] if it["item_id"] == "capture")
        self.assertEqual(len(capture["tasks"]), 2)

        result = handle_delete_task({
            "project_path": str(self.project),
            "db_path": str(self.db),
            "task_id": "persist-card",
        })
        self.assertTrue(result["ok"], result)

        after = handle_tasks({"project_path": str(self.project)})
        capture_after = next(it for it in after["items"] if it["item_id"] == "capture")
        self.assertEqual(len(capture_after["tasks"]), 1)
        self.assertEqual(capture_after["tasks"][0]["task_id"], "route-archive")

    def test_delete_task_v2_preserves_other_item(self):
        handle_delete_task({
            "project_path": str(self.project),
            "db_path": str(self.db),
            "task_id": "persist-card",
        })
        after = handle_tasks({"project_path": str(self.project)})
        reporting = next(it for it in after["items"] if it["item_id"] == "reporting")
        self.assertEqual(len(reporting["tasks"]), 1)
        self.assertEqual(reporting["tasks"][0]["task_id"], "weekly-summary")

    # ── update_task v2 ───────────────────────────────────

    def test_update_task_v2_status(self):
        result = handle_update_task({
            "project_path": str(self.project),
            "db_path": str(self.db),
            "task_id": "route-archive",
            "field": "status",
            "value": "done",
        })
        self.assertTrue(result["ok"], result)

        after = handle_tasks({"project_path": str(self.project)})
        capture = next(it for it in after["items"] if it["item_id"] == "capture")
        route = next(t for t in capture["tasks"] if t["task_id"] == "route-archive")
        self.assertEqual(route["status"], "done")

    def test_update_task_v2_title(self):
        result = handle_update_task({
            "project_path": str(self.project),
            "db_path": str(self.db),
            "task_id": "persist-card",
            "field": "title",
            "value": "Persist v2 card",
        })
        self.assertTrue(result["ok"], result)

        text = self.project.read_text(encoding="utf-8")
        self.assertIn("Persist v2 card", text)
        self.assertIn("<!-- task:persist-card -->", text)

    def test_update_task_v2_next_action(self):
        result = handle_update_task({
            "project_path": str(self.project),
            "db_path": str(self.db),
            "task_id": "route-archive",
            "field": "next_action",
            "value": "Wire v2 inbox.",
        })
        self.assertTrue(result["ok"], result)

        text = self.project.read_text(encoding="utf-8")
        self.assertIn("Wire v2 inbox.", text)

    # ── update_item v2 ───────────────────────────────────

    def test_update_item_v2_rename_title(self):
        """Title rename on v2 should actually change the heading (not silent no-op)."""
        result = handle_update_item({
            "project_path": str(self.project),
            "db_path": str(self.db),
            "item_id": "capture",
            "title": "Event Capture",
        })
        self.assertTrue(result["ok"], result)

        text = self.project.read_text(encoding="utf-8")
        self.assertIn("### 工作项：Event Capture <!-- item:capture -->", text)
        self.assertNotIn("### 工作项：Capture <!-- item:capture -->", text)

    def test_update_item_v2_rename_preserves_sibling(self):
        """Renaming one v2 item must not affect the other item or its tasks."""
        handle_update_item({
            "project_path": str(self.project),
            "db_path": str(self.db),
            "item_id": "capture",
            "title": "Event Capture",
        })
        text = self.project.read_text(encoding="utf-8")
        self.assertIn("### 工作项：Reporting <!-- item:reporting -->", text)
        self.assertIn("<!-- task:weekly-summary -->", text)

    def test_update_item_v2_set_background(self):
        """Setting background on v2 item injects a - background: line."""
        result = handle_update_item({
            "project_path": str(self.project),
            "db_path": str(self.db),
            "item_id": "reporting",
            "title": "Reporting",
            "background": "Generate daily and weekly reports.",
        })
        self.assertTrue(result["ok"], result)

        text = self.project.read_text(encoding="utf-8")
        self.assertIn("- background: Generate daily and weekly reports.", text)
        self.assertIn("### 工作项：Reporting <!-- item:reporting -->", text)

    def test_update_item_v2_clear_background(self):
        """Clearing background on v2 item removes the - background: line."""
        # First set it
        handle_update_item({
            "project_path": str(self.project),
            "db_path": str(self.db),
            "item_id": "reporting",
            "title": "Reporting",
            "background": "Generate reports.",
        })
        text_after_set = self.project.read_text(encoding="utf-8")
        self.assertIn("- background: Generate reports.", text_after_set)

        # Then clear it
        result = handle_update_item({
            "project_path": str(self.project),
            "db_path": str(self.db),
            "item_id": "reporting",
            "title": "Reporting",
            "background": "",
        })
        self.assertTrue(result["ok"], result)

        text = self.project.read_text(encoding="utf-8")
        self.assertNotIn("- background:", text)
        self.assertIn("### 工作项：Reporting <!-- item:reporting -->", text)

    def test_update_item_v2_title_only_does_not_affect_background(self):
        """Title-only rename on v2 must preserve existing background."""
        # First set background
        handle_update_item({
            "project_path": str(self.project),
            "db_path": str(self.db),
            "item_id": "reporting",
            "title": "Reporting",
            "background": "Generate reports.",
        })
        # Then rename only (background=None → leave unchanged)
        result = handle_update_item({
            "project_path": str(self.project),
            "db_path": str(self.db),
            "item_id": "reporting",
            "title": "Report Gen v2",
        })
        self.assertTrue(result["ok"], result)

        text = self.project.read_text(encoding="utf-8")
        self.assertIn("### 工作项：Report Gen v2 <!-- item:reporting -->", text)
        self.assertIn("- background: Generate reports.", text)


if __name__ == "__main__":
    unittest.main()
