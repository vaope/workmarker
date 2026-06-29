import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from workeventagent.cli import main
from workeventagent.index_store import get_task, init_db, rebuild_index
from workeventagent.opencode_runner import OpencodeRunnerError


class CliTest(unittest.TestCase):
    @patch("workeventagent.cli.run_archivist")
    def test_capture_dry_run_prints_confirmation_card(self, run_archivist):
        run_archivist.return_value = """
{
  "target": {"project_id": "multimodal-labeling", "item_id": "kv-cache-few-shot", "task_id": "kv-cache-blockers"},
  "confidence": 0.91,
  "reason": "Matched KV cache item",
  "event": {"task_id": "kv-cache-blockers", "input_text": "input", "summary": "summary", "status": "in_progress", "next_action": "next"},
  "attachment_paths": []
}
"""
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project.md"
            project.write_text(
                Path("tests/fixtures/multimodal-labeling.md").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            code = main(
                [
                    "capture",
                    "--project",
                    str(project),
                    "--db",
                    str(Path(tmp) / "index.sqlite"),
                    "--text",
                    "input",
                    "--dry-run",
                ]
            )

        self.assertEqual(code, 0)

    @patch("workeventagent.cli.input", return_value="confirm")
    @patch("workeventagent.cli.run_archivist")
    def test_capture_confirmed_write_updates_markdown_and_sqlite(
        self, run_archivist, input_mock
    ):
        run_archivist.return_value = """
{
  "target": {"project_id": "multimodal-labeling", "item_id": "kv-cache-few-shot", "task_id": "kv-cache-blockers"},
  "confidence": 0.91,
  "reason": "Matched KV cache item",
  "event": {"task_id": "kv-cache-blockers", "input_text": "Reviewed blockers for KV cache few-shot optimization today.", "summary": "Prefix reuse strategy is unclear.", "status": "in_progress", "next_action": "Map current inference chain."},
  "attachment_paths": ["attachments/baseline.png"]
}
"""
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project.md"
            db = Path(tmp) / "index.sqlite"
            attachment = Path(tmp) / "baseline.png"
            attachment.write_bytes(b"not-analyzed")
            project.write_text(
                Path("tests/fixtures/multimodal-labeling.md").read_text(encoding="utf-8"),
                encoding="utf-8",
            )

            fixed_now = datetime(2026, 6, 29, 15, 30, 0, 123000, tzinfo=timezone.utc)
            code = main(
                [
                    "capture",
                    "--project",
                    str(project),
                    "--db",
                    str(db),
                    "--text",
                    "Reviewed blockers for KV cache few-shot optimization today.",
                    "--attach",
                    str(attachment),
                ],
                now=fixed_now,
            )
            updated = project.read_text(encoding="utf-8")
            task = get_task(db, "kv-cache-blockers")

        self.assertEqual(code, 0)
        self.assertIn("20260629-153000123-kv-cache-blockers", updated)
        self.assertIn("Map current inference chain.", updated)
        self.assertEqual(task["next_action"], "Map current inference chain.")

    @patch("workeventagent.cli.edit_proposal_with_editor")
    @patch("workeventagent.cli.input", side_effect=["edit", "confirm"])
    @patch("workeventagent.cli.run_archivist")
    def test_capture_edit_reconfirms_before_write(
        self, run_archivist, input_mock, edit_mock
    ):
        run_archivist.return_value = """
{
  "target": {"project_id": "multimodal-labeling", "item_id": "kv-cache-few-shot", "task_id": "kv-cache-blockers"},
  "confidence": 0.91,
  "reason": "Matched KV cache item",
  "event": {"task_id": "kv-cache-blockers", "input_text": "input", "summary": "summary", "status": "in_progress", "next_action": "next"},
  "attachment_paths": []
}
"""
        edit_mock.side_effect = lambda proposal: proposal

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project.md"
            project.write_text(
                Path("tests/fixtures/multimodal-labeling.md").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            code = main(
                [
                    "capture",
                    "--project",
                    str(project),
                    "--db",
                    str(Path(tmp) / "index.sqlite"),
                    "--text",
                    "input",
                ]
            )

        self.assertEqual(code, 0)
        edit_mock.assert_called_once()

    # ── New tests for 砚砚's Task 7 checklist ──

    @patch("workeventagent.cli.input", return_value="confirm")
    @patch("workeventagent.cli.run_archivist")
    def test_capture_new_task_inserts_timeline_and_indexes(
        self, run_archivist, input_mock
    ):
        """A-point: new_task path = insert structure + append timeline + bump updated, indexable after rebuild."""
        run_archivist.return_value = """
{
  "target": {"project_id": "multimodal-labeling", "item_id": "kv-cache-few-shot", "task_id": "new-blocker-task", "task_title": "New Blocker Task", "new_task": true},
  "confidence": 0.91,
  "reason": "User mentioned a new task.",
  "event": {"task_id": "new-blocker-task", "input_text": "Need to check new blockers.", "summary": "New blocker investigation needed.", "status": "in_progress", "next_action": "List current blockers."},
  "attachment_paths": []
}
"""
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project.md"
            db = Path(tmp) / "index.sqlite"
            project.write_text(
                Path("tests/fixtures/multimodal-labeling.md").read_text(encoding="utf-8"),
                encoding="utf-8",
            )

            fixed_now = datetime(2026, 6, 29, 15, 30, 0, 123000, tzinfo=timezone.utc)
            code = main(
                [
                    "capture",
                    "--project",
                    str(project),
                    "--db",
                    str(db),
                    "--text",
                    "input",
                ],
                now=fixed_now,
            )
            updated = project.read_text(encoding="utf-8")

        self.assertEqual(code, 0)
        # Task block inserted
        self.assertIn(
            "#### Task: New Blocker Task <!-- task:new-blocker-task -->", updated
        )
        # Timeline appended with event_id
        self.assertIn(
            "<!-- event:20260629-153000123-new-blocker-task -->", updated
        )

    @patch("workeventagent.cli.input", return_value="confirm")
    @patch("workeventagent.cli.run_archivist")
    def test_capture_new_task_rebuild_finds_task(
        self, run_archivist, input_mock
    ):
        """A-point: new task via CLI → rebuild → get_task finds it."""
        run_archivist.return_value = """
{
  "target": {"project_id": "multimodal-labeling", "item_id": "kv-cache-few-shot", "task_id": "new-blocker-task", "task_title": "New Blocker Task", "new_task": true},
  "confidence": 0.91,
  "reason": "User mentioned a new task.",
  "event": {"task_id": "new-blocker-task", "input_text": "Need to check new blockers.", "summary": "New blocker investigation needed.", "status": "in_progress", "next_action": "List current blockers."},
  "attachment_paths": []
}
"""
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project.md"
            db = Path(tmp) / "index.sqlite"
            project.write_text(
                Path("tests/fixtures/multimodal-labeling.md").read_text(encoding="utf-8"),
                encoding="utf-8",
            )

            fixed_now = datetime(2026, 6, 29, 15, 30, 0, 123000, tzinfo=timezone.utc)
            code = main(
                [
                    "capture",
                    "--project",
                    str(project),
                    "--db",
                    str(db),
                    "--text",
                    "input",
                ],
                now=fixed_now,
            )
            # Rebuild picks up the new task (db already created by CLI)
            task = get_task(db, "new-blocker-task")

        self.assertEqual(code, 0)
        self.assertEqual(task["task_id"], "new-blocker-task")
        self.assertEqual(task["status"], "in_progress")

    @patch("workeventagent.cli.run_archivist")
    def test_capture_low_confidence_rejected(self, run_archivist):
        """Confidence < 0.6 → exit 1 without writing."""
        run_archivist.return_value = """
{
  "target": {"project_id": "multimodal-labeling", "item_id": "kv-cache-few-shot", "task_id": "kv-cache-blockers"},
  "confidence": 0.3,
  "reason": "Uncertain match.",
  "event": {"task_id": "kv-cache-blockers", "input_text": "input", "summary": "summary", "status": "in_progress", "next_action": "next"},
  "attachment_paths": []
}
"""
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project.md"
            original = Path("tests/fixtures/multimodal-labeling.md").read_text(
                encoding="utf-8"
            )
            project.write_text(original, encoding="utf-8")
            code = main(
                [
                    "capture",
                    "--project",
                    str(project),
                    "--db",
                    str(Path(tmp) / "index.sqlite"),
                    "--text",
                    "input",
                ]
            )
            updated = project.read_text(encoding="utf-8")

        self.assertEqual(code, 1)
        self.assertEqual(updated, original)

    @patch("workeventagent.cli.input", return_value="cancel")
    @patch("workeventagent.cli.run_archivist")
    def test_capture_cancel_no_write(self, run_archivist, input_mock):
        """cancel → exit 2, file untouched."""
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
            original = Path("tests/fixtures/multimodal-labeling.md").read_text(
                encoding="utf-8"
            )
            project.write_text(original, encoding="utf-8")
            code = main(
                [
                    "capture",
                    "--project",
                    str(project),
                    "--db",
                    str(Path(tmp) / "index.sqlite"),
                    "--text",
                    "input",
                ]
            )
            updated = project.read_text(encoding="utf-8")

        self.assertEqual(code, 2)
        self.assertEqual(updated, original)

    @patch("workeventagent.cli.run_archivist")
    def test_capture_opencode_error_returns_nonzero(self, run_archivist):
        """OpencodeRunnerError → exit 1."""
        run_archivist.side_effect = OpencodeRunnerError("test error")
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project.md"
            project.write_text("---\nproject_id: test\n---\n# Test\n", encoding="utf-8")
            code = main(
                [
                    "capture",
                    "--project",
                    str(project),
                    "--db",
                    str(Path(tmp) / "index.sqlite"),
                    "--text",
                    "input",
                ]
            )

        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
