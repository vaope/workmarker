import unittest
from pathlib import Path
from unittest.mock import patch

from workeventagent.opencode_runner import (
    OpencodeRunnerError,
    parse_archivist_output,
    run_archivist,
)


_EXAMPLE_NDJSON = """\
{"type":"step_start","part":{"type":"step-start"}}
{"type":"text","part":{"type":"text","text":"```json\\n{\\n  \\\"target\\\": {\\n    \\\"project_id\\\": \\\"multimodal-labeling\\\",\\n    \\\"item_id\\\": \\\"kv-cache-few-shot\\\",\\n    \\\"task_id\\\": \\\"kv-cache-blockers\\\",\\n    \\\"new_item\\\": false,\\n    \\\"new_task\\\": false\\n  },\\n  \\\"confidence\\\": 0.91,\\n  \\\"reason\\\": \\\"Matched KV cache item.\\\",\\n  \\\"event\\\": {\\n    \\\"event_id\\\": \\\"agent-must-not-own-this\\\",\\n    \\\"task_id\\\": \\\"kv-cache-blockers\\\",\\n    \\\"input_text\\\": \\\"Reviewed blockers.\\\",\\n    \\\"summary\\\": \\\"Prefix reuse strategy is unclear.\\\",\\n    \\\"status\\\": \\\"in_progress\\\",\\n    \\\"next_action\\\": \\\"Map current inference chain.\\\"\\n  },\\n  \\\"attachment_paths\\\": []\\n}\\n```"}}
{"type":"step_finish","part":{"type":"step-finish"}}
"""


class OpencodeRunnerTest(unittest.TestCase):
    @patch("workeventagent.opencode_runner.subprocess.run")
    def test_run_archivist_calls_opencode_agent_with_file(self, run):
        run.return_value.stdout = '{"ok": true}'
        run.return_value.returncode = 0

        output = run_archivist("input", Path("project.md"), opencode_bin="opencode")

        self.assertEqual(output, '{"ok": true}')
        args = run.call_args.args[0]
        self.assertEqual(args[0], "opencode")
        self.assertIn("run", args)
        self.assertIn("--agent", args)
        self.assertIn("workevent-archivist", args)
        self.assertIn("--file", args)

    @patch("workeventagent.opencode_runner.subprocess.run")
    def test_run_archivist_raises_on_nonzero_exit(self, run):
        run.return_value.stdout = ""
        run.return_value.stderr = "bad flag"
        run.return_value.returncode = 2

        with self.assertRaises(OpencodeRunnerError):
            run_archivist("input", Path("project.md"), opencode_bin="opencode")

    @patch("workeventagent.opencode_runner.subprocess.run")
    def test_run_archivist_raises_on_empty_stdout(self, run):
        run.return_value.stdout = ""
        run.return_value.returncode = 0

        with self.assertRaises(OpencodeRunnerError):
            run_archivist("input", Path("project.md"), opencode_bin="opencode")

    def test_parse_archivist_output_rejects_empty_or_invalid_json(self):
        with self.assertRaises(OpencodeRunnerError):
            parse_archivist_output("", "event-1")
        with self.assertRaises(OpencodeRunnerError):
            parse_archivist_output("{not json", "event-1")

    def test_parse_archivist_output_uses_wrapper_event_id(self):
        raw = """\
{
  "target": {"project_id": "multimodal-labeling", "item_id": "kv-cache-few-shot", "task_id": "kv-cache-blockers"},
  "confidence": 0.91,
  "reason": "Matched KV cache item",
  "event": {"event_id": "agent-must-not-own-this", "task_id": "kv-cache-blockers", "input_text": "input", "summary": "summary", "status": "in_progress", "next_action": "next"},
  "attachment_paths": []
}
"""
        proposal = parse_archivist_output(raw, "wrapper-event-id")

        self.assertEqual(proposal.event.event_id, "wrapper-event-id")

    def test_parse_ndjson_extracts_json_from_text_line(self):
        proposal = parse_archivist_output(_EXAMPLE_NDJSON, "wrapper-event-id")

        self.assertEqual(proposal.target.project_id, "multimodal-labeling")
        self.assertEqual(proposal.target.item_id, "kv-cache-few-shot")
        self.assertEqual(proposal.target.task_id, "kv-cache-blockers")
        self.assertAlmostEqual(proposal.confidence, 0.91)
        self.assertEqual(proposal.event.event_id, "wrapper-event-id")
        self.assertEqual(proposal.event.summary, "Prefix reuse strategy is unclear.")

    def test_parse_archivist_output_rejects_missing_required_keys(self):
        bad = '{"target": {"project_id": "p"}}'
        with self.assertRaises(OpencodeRunnerError):
            parse_archivist_output(bad, "event-1")

    def test_parse_archivist_output_ignores_markdown_preview(self):
        raw = """\
{
  "target": {"project_id": "multimodal-labeling", "item_id": "kv-cache-few-shot", "task_id": "kv-cache-blockers"},
  "confidence": 0.91,
  "reason": "Matched KV cache item",
  "event": {"task_id": "kv-cache-blockers", "input_text": "input", "summary": "summary", "status": "in_progress", "next_action": "next"},
  "attachment_paths": [],
  "markdown_preview": "SHOULD BE IGNORED"
}
"""
        proposal = parse_archivist_output(raw, "event-1")
        self.assertEqual(proposal.event.event_id, "event-1")

    def test_parse_archivist_output_rejects_invalid_status(self):
        raw = """\
{
  "target": {"project_id": "p", "item_id": "i", "task_id": "t"},
  "confidence": 0.9,
  "reason": "ok",
  "event": {"task_id": "t", "input_text": "input", "summary": "summary", "status": "blocked", "next_action": "next"}
}
"""
        with self.assertRaises(OpencodeRunnerError):
            parse_archivist_output(raw, "event-1")

    def test_parse_archivist_output_rejects_new_task_without_task_title(self):
        """🟡 new_task=true with empty task_title → OpencodeRunnerError."""
        raw = """\
{
  "target": {"project_id": "p", "item_id": "i", "task_id": "t", "new_task": true, "task_title": ""},
  "confidence": 0.9,
  "reason": "new task",
  "event": {"task_id": "t", "input_text": "input", "summary": "summary", "status": "in_progress", "next_action": "next"}
}
"""
        with self.assertRaises(OpencodeRunnerError):
            parse_archivist_output(raw, "event-1")


if __name__ == "__main__":
    unittest.main()
