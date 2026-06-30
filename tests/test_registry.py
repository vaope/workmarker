import tempfile
import unittest
from pathlib import Path

from workeventagent.registry import scan_workspace


class RegistryTest(unittest.TestCase):
    def test_empty_workspace_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = scan_workspace(Path(tmp))
        self.assertEqual(result, [])

    def test_nonexistent_directory_returns_empty(self):
        result = scan_workspace(Path("/nonexistent/path/12345"))
        self.assertEqual(result, [])

    def test_ignores_non_markdown_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "notes.txt").write_text("hello", encoding="utf-8")
            result = scan_workspace(Path(tmp))
        self.assertEqual(result, [])

    def test_ignores_markdown_without_doc_kind(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "draft.md").write_text(
                "---\ntitle: Draft\n---\n# Draft\n", encoding="utf-8"
            )
            result = scan_workspace(Path(tmp))
        self.assertEqual(result, [])

    def test_ignores_markdown_with_wrong_doc_kind(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "blog.md").write_text(
                "---\ntitle: Blog\ndoc_kind: blog_post\n---\n# Blog\n", encoding="utf-8"
            )
            result = scan_workspace(Path(tmp))
        self.assertEqual(result, [])

    def test_scans_work_project_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "multimodal-labeling.md").write_text(
                "---\nproject_id: multimodal-labeling\ntitle: Multimodal\ndoc_kind: work_project\nupdated: 2026-07-01\n---\n"
                "## Work Map\n"
                "#### Task: blockers <!-- task:blockers -->\n- status: in_progress\n- next_action: check\n- last_event_id: ev1\n"
                "#### Task: done-task <!-- task:done -->\n- status: done\n- next_action: \n- last_event_id: ev2\n",
                encoding="utf-8",
            )
            result = scan_workspace(Path(tmp))

        self.assertEqual(len(result), 1)
        p = result[0]
        self.assertEqual(p["project_id"], "multimodal-labeling")
        self.assertEqual(p["title"], "Multimodal")
        self.assertEqual(p["open_task_count"], 1)
        self.assertEqual(p["updated_at"], "2026-07-01")
        self.assertTrue(p["path"].endswith("multimodal-labeling.md"))

    def test_scans_multiple_projects_sorted_by_updated(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "older.md").write_text(
                "---\nproject_id: older\ntitle: Older\ndoc_kind: work_project\nupdated: 2026-06-01\n---\n",
                encoding="utf-8",
            )
            (Path(tmp) / "newer.md").write_text(
                "---\nproject_id: newer\ntitle: Newer\ndoc_kind: work_project\nupdated: 2026-07-01\n---\n",
                encoding="utf-8",
            )
            result = scan_workspace(Path(tmp))

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["project_id"], "newer")  # most recent first
        self.assertEqual(result[1]["project_id"], "older")


if __name__ == "__main__":
    unittest.main()
