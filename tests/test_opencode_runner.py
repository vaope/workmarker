import json
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from workeventagent.opencode_runner import (
    OpencodeRunnerError,
    parse_archivist_output,
    parse_knowledge_impact,
    parse_project_route_output,
    parse_synthesis_output,
    run_archivist,
    run_project_synthesizer,
    run_project_router,
    run_reporter,
)


class KnowledgeImpactParserTest(unittest.TestCase):
    def test_valid_ordinary_and_high_objects_parse(self):
        ordinary = parse_knowledge_impact(
            '{"knowledge_impact":{"level":"ordinary","dimensions":[],"reason":"Task evidence only."}}'
        )
        high = parse_knowledge_impact(
            '{"knowledge_impact":{"level":"high","dimensions":["architecture","risk"],"reason":"Architecture changed."}}'
        )

        self.assertEqual(ordinary, {"level": "ordinary", "dimensions": [], "reason": "Task evidence only."})
        self.assertEqual(
            high,
            {"level": "high", "dimensions": ["architecture", "risk"], "reason": "Architecture changed."},
        )

    def test_invalid_or_missing_impact_fails_closed_to_ordinary(self):
        invalid_values = [
            "{}",
            '{"knowledge_impact":{"level":"urgent","dimensions":[],"reason":"x"}}',
            '{"knowledge_impact":{"level":"high","dimensions":["status"],"reason":"x"}}',
            '{"knowledge_impact":{"level":"high","dimensions":["goal"],"reason":""}}',
            '{"knowledge_impact":{"level":"high","dimensions":[],"reason":"Goal changed"}}',
        ]

        for raw in invalid_values:
            with self.subTest(raw=raw):
                result = parse_knowledge_impact(raw)
                self.assertEqual(result["level"], "ordinary")
                self.assertEqual(result["dimensions"], [])
                self.assertTrue(result["reason"])

    def test_agent_owned_ids_are_ignored(self):
        result = parse_knowledge_impact(
            '{"knowledge_impact":{"level":"high","dimensions":["scope"],"reason":"Scope changed",'
            '"source_event_ids":["agent-event"],"job_id":"agent-job"}}'
        )

        self.assertEqual(set(result), {"level", "dimensions", "reason"})

    def test_done_without_supported_dimension_is_ordinary(self):
        result = parse_knowledge_impact(
            '{"event":{"status":"done"},"knowledge_impact":{"level":"high","dimensions":[],"reason":"Task done"}}'
        )

        self.assertEqual(result["level"], "ordinary")


class ProjectSynthesizerRunnerTest(unittest.TestCase):
    @patch("workeventagent.opencode_runner.subprocess.run")
    def test_runner_selects_synthesizer_file_model_json_timeout_and_no_stdin(self, run):
        run.return_value.stdout = '{"changes":[],"document_suggestion":null}'
        run.return_value.returncode = 0

        output = run_project_synthesizer(
            "prompt", Path("project.md"), opencode_bin="opencode", model="provider/model"
        )

        self.assertIn('"changes"', output)
        args = run.call_args.args[0]
        self.assertEqual(args[args.index("--agent") + 1], "workevent-synthesizer")
        self.assertEqual(args[args.index("--file") + 1], "project.md")
        self.assertEqual(args[args.index("--model") + 1], "provider/model")
        self.assertEqual(args[args.index("--format") + 1], "json")
        self.assertEqual(run.call_args.kwargs["timeout"], 600)
        self.assertEqual(run.call_args.kwargs["stdin"], subprocess.DEVNULL)

    def test_parser_accepts_bounded_output(self):
        parsed = parse_synthesis_output(
            '{"changes":[{"target_section":"current-panorama","reason":"Evidence changed",'
            '"content":{"paragraphs":["Now."],"bullets":["Next."]}}],"document_suggestion":null}'
        )

        self.assertEqual(parsed["changes"][0]["target_section"], "current-panorama")

    def test_parser_rejects_unknown_duplicate_or_structural_content(self):
        invalid = [
            '{"changes":[{"target_section":"timeline","reason":"x","content":{"paragraphs":["x"],"bullets":[]}}],"document_suggestion":null}',
            '{"changes":[{"target_section":"current-panorama","reason":"x","content":{"paragraphs":["x"],"bullets":[]}},{"target_section":"current-panorama","reason":"y","content":{"paragraphs":["y"],"bullets":[]}}],"document_suggestion":null}',
            '{"changes":[{"target_section":"current-panorama","reason":"x","content":"raw"}],"document_suggestion":null}',
            '{"changes":[{"target_section":"current-panorama","reason":"x","content":{"paragraphs":["## Heading"],"bullets":[]}}],"document_suggestion":null}',
            '{"changes":[{"target_section":"current-panorama","reason":"x","content":{"paragraphs":["<!-- comment -->"],"bullets":[]}}],"document_suggestion":null}',
            '{"changes":[{"target_section":"current-panorama","reason":"x","content":{"paragraphs":["---"],"bullets":[]}}],"document_suggestion":null}',
            '{"changes":[{"target_section":"current-panorama","reason":"x","content":{"paragraphs":["C:\\\\secret\\\\file.md"],"bullets":[]}}],"document_suggestion":null}',
            '{"changes":[],"document_suggestion":[],"another_document":{}}',
        ]
        for raw in invalid:
            with self.subTest(raw=raw):
                with self.assertRaises(OpencodeRunnerError):
                    parse_synthesis_output(raw)

    def test_parser_rejects_agent_owned_identity_fields(self):
        invalid_keys = ("filename", "module_id", "order", "project_id", "source_event_ids", "base_section_hash")
        for key in invalid_keys:
            raw = (
                '{"changes":[],"document_suggestion":{"purpose":"p","title":"Architecture",'
                '"retained_summary":"summary","module_conclusion":{"paragraphs":["c"],"bullets":[]},'
                '"module_body":{"paragraphs":["b"],"bullets":[]},'
                f'"{key}":"agent-owned"}}'
            )
            with self.subTest(key=key):
                with self.assertRaises(OpencodeRunnerError):
                    parse_synthesis_output(raw)

    def test_parser_rejects_all_unicode_document_title_line_separators(self):
        for separator in ("\n", "\u0085", "\u2028", "\u2029"):
            raw = json.dumps(
                {
                    "changes": [],
                    "document_suggestion": {
                        "purpose": "p",
                        "title": f"Architecture{separator}extra_control: agent-owned",
                        "retained_summary": "summary",
                        "module_conclusion": {"paragraphs": ["c"], "bullets": []},
                        "module_body": {"paragraphs": ["b"], "bullets": []},
                    },
                }
            )

            with self.subTest(separator=ascii(separator)):
                with self.assertRaises(OpencodeRunnerError):
                    parse_synthesis_output(raw)


_EXAMPLE_NDJSON = """\
{"type":"step_start","part":{"type":"step-start"}}
{"type":"text","part":{"type":"text","text":"```json\\n{\\n  \\\"target\\\": {\\n    \\\"project_id\\\": \\\"multimodal-labeling\\\",\\n    \\\"item_id\\\": \\\"kv-cache-few-shot\\\",\\n    \\\"task_id\\\": \\\"kv-cache-blockers\\\",\\n    \\\"new_item\\\": false,\\n    \\\"new_task\\\": false\\n  },\\n  \\\"confidence\\\": 0.91,\\n  \\\"reason\\\": \\\"Matched KV cache item.\\\",\\n  \\\"event\\\": {\\n    \\\"event_id\\\": \\\"agent-must-not-own-this\\\",\\n    \\\"task_id\\\": \\\"kv-cache-blockers\\\",\\n    \\\"input_text\\\": \\\"Reviewed blockers.\\\",\\n    \\\"summary\\\": \\\"Prefix reuse strategy is unclear.\\\",\\n    \\\"status\\\": \\\"in_progress\\\",\\n    \\\"next_action\\\": \\\"Map current inference chain.\\\"\\n  },\\n  \\\"attachment_paths\\\": []\\n}\\n```"}}
{"type":"step_finish","part":{"type":"step-finish"}}
"""

_EXAMPLE_ROUTE_NDJSON = """\
{"type":"step_start","part":{"type":"step-start"}}
{"type":"text","part":{"type":"text","text":"```json\\n{\\n  \\\"project_id\\\": \\\"project-b\\\",\\n  \\\"confidence\\\": 0.86,\\n  \\\"reason\\\": \\\"The update mentions project B details.\\\"\\n}\\n```"}}
{"type":"step_finish","part":{"type":"step-finish"}}
"""


class OpencodeRunnerTest(unittest.TestCase):
    @patch("workeventagent.opencode_runner.subprocess.run")
    def test_run_archivist_calls_opencode_agent_with_file(self, run):
        run.return_value.stdout = '{"ok": true}'
        run.return_value.returncode = 0

        with patch.object(shutil, "which", return_value=None):
            output = run_archivist("input", Path("project.md"), opencode_bin="opencode")

        self.assertEqual(output, '{"ok": true}')
        args = run.call_args.args[0]
        self.assertEqual(args[0], "opencode")
        self.assertIn("run", args)
        self.assertIn("--agent", args)
        self.assertIn("workevent-archivist", args)
        self.assertIn("--file", args)

    @patch("workeventagent.opencode_runner.subprocess.run")
    def test_run_project_router_calls_opencode_router_agent_with_file(self, run):
        run.return_value.stdout = '{"ok": true}'
        run.return_value.returncode = 0

        with patch.object(shutil, "which", return_value=None):
            output = run_project_router("input", Path("projects.md"), opencode_bin="opencode")

        self.assertEqual(output, '{"ok": true}')
        args = run.call_args.args[0]
        self.assertEqual(args[0], "opencode")
        self.assertIn("run", args)
        self.assertIn("--agent", args)
        self.assertIn("workevent-router", args)
        self.assertIn("--file", args)

    @patch("workeventagent.opencode_runner.subprocess.run")
    def test_run_archivist_resolves_opencode_cmd_shim(self, run):
        run.return_value.stdout = '{"ok": true}'
        run.return_value.returncode = 0

        with patch.object(
            shutil,
            "which",
            return_value=r"C:\Users\lsy\AppData\Roaming\npm\opencode.CMD",
        ):
            run_archivist("input", Path("project.md"))

        args = run.call_args.args[0]
        self.assertEqual(args[0], r"C:\Users\lsy\AppData\Roaming\npm\opencode.CMD")

    @patch("workeventagent.opencode_runner.subprocess.run")
    def test_run_archivist_wraps_missing_executable(self, run):
        run.side_effect = FileNotFoundError("opencode")

        with self.assertRaises(OpencodeRunnerError):
            run_archivist("input", Path("project.md"), opencode_bin="opencode")

    @patch("workeventagent.opencode_runner.subprocess.run")
    def test_run_archivist_decodes_stdout_as_utf8(self, run):
        run.return_value.stdout = '{"ok": true}'
        run.return_value.returncode = 0

        run_archivist("input", Path("project.md"), opencode_bin="opencode")

        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs["encoding"], "utf-8")
        self.assertEqual(kwargs["errors"], "replace")

    @patch("workeventagent.opencode_runner.subprocess.run")
    def test_run_archivist_does_not_inherit_confirmation_stdin(self, run):
        run.return_value.stdout = '{"ok": true}'
        run.return_value.returncode = 0

        run_archivist("input", Path("project.md"), opencode_bin="opencode")

        self.assertIs(run.call_args.kwargs["stdin"], subprocess.DEVNULL)

    @patch("workeventagent.opencode_runner.subprocess.run")
    def test_run_archivist_uses_ten_minute_timeout(self, run):
        run.return_value.stdout = '{"ok": true}'
        run.return_value.returncode = 0

        run_archivist("input", Path("project.md"), opencode_bin="opencode")

        self.assertEqual(run.call_args.kwargs["timeout"], 600)

    @patch("workeventagent.opencode_runner.subprocess.run")
    def test_run_archivist_passes_model_flag_when_configured(self, run):
        run.return_value.stdout = '{"ok": true}'
        run.return_value.returncode = 0

        run_archivist(
            "input",
            Path("project.md"),
            opencode_bin="opencode",
            model="openai/gpt-5.1",
        )

        args = run.call_args.args[0]
        model_idx = args.index("--model")
        self.assertEqual(args[model_idx + 1], "openai/gpt-5.1")

    @patch("workeventagent.opencode_runner.subprocess.run")
    def test_run_archivist_omits_model_flag_when_unconfigured(self, run):
        run.return_value.stdout = '{"ok": true}'
        run.return_value.returncode = 0

        run_archivist("input", Path("project.md"), opencode_bin="opencode", model="")

        args = run.call_args.args[0]
        self.assertNotIn("--model", args)

    @patch("workeventagent.opencode_runner.subprocess.run")
    def test_run_archivist_raises_on_none_stdout(self, run):
        run.return_value.stdout = None
        run.return_value.returncode = 0

        with self.assertRaises(OpencodeRunnerError):
            run_archivist("input", Path("project.md"), opencode_bin="opencode")

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

    def test_parse_project_route_output_from_ndjson(self):
        route = parse_project_route_output(_EXAMPLE_ROUTE_NDJSON, {"project-a", "project-b"})

        self.assertEqual(route["project_id"], "project-b")
        self.assertAlmostEqual(route["confidence"], 0.86)
        self.assertIn("project B", route["reason"])

    def test_parse_project_route_output_rejects_unknown_project(self):
        raw = '{"project_id":"missing-project","confidence":0.9,"reason":"bad"}'

        with self.assertRaises(OpencodeRunnerError):
            parse_project_route_output(raw, {"project-a"})

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

    def test_parse_archivist_output_normalizes_completed_status(self):
        raw = """\
{
  "target": {"project_id": "p", "item_id": "i", "task_id": "t"},
  "confidence": 0.9,
  "reason": "ok",
  "event": {"task_id": "t", "input_text": "input", "summary": "summary", "status": "completed", "next_action": ""}
}
"""
        proposal = parse_archivist_output(raw, "event-1")

        self.assertEqual(proposal.event.status, "done")

    def test_parse_archivist_output_defaults_unknown_status_to_in_progress(self):
        raw = """\
{
  "target": {"project_id": "p", "item_id": "i", "task_id": "t"},
  "confidence": 0.9,
  "reason": "ok",
  "event": {"task_id": "t", "input_text": "input", "summary": "summary", "status": "blocked", "next_action": "next"}
}
"""
        proposal = parse_archivist_output(raw, "event-1")

        self.assertEqual(proposal.event.status, "in_progress")

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

    @patch("workeventagent.opencode_runner.subprocess.run")
    def test_run_reporter_calls_opencode_reporter_agent_with_file(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout='{"type":"text","part":{"text":"{}"}}\n',
            stderr="",
        )

        output = run_reporter("summarize", Path("report-context.md"), opencode_bin="opencode")

        self.assertTrue(output)
        cmd = run.call_args.args[0]
        self.assertIn("--agent", cmd)
        self.assertIn("workevent-reporter", cmd)
        self.assertIn("--file", cmd)
        self.assertIn("report-context.md", cmd)


if __name__ == "__main__":
    unittest.main()
