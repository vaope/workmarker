import sqlite3
import tempfile
import unittest
from pathlib import Path

from workeventagent.index_store import get_task, init_db, rebuild_index


class IndexStoreTest(unittest.TestCase):
    def test_init_db_adds_conclusion_to_existing_task_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "legacy.sqlite"
            conn = sqlite3.connect(db_path)
            conn.execute(
                """CREATE TABLE tasks (
                    task_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL DEFAULT '',
                    item_id TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT '',
                    next_action TEXT NOT NULL DEFAULT '',
                    doc_path TEXT NOT NULL DEFAULT '',
                    doc_anchor TEXT NOT NULL DEFAULT '',
                    last_event_id TEXT NOT NULL DEFAULT ''
                )"""
            )
            conn.commit()
            conn.close()

            init_db(db_path)

            conn = sqlite3.connect(db_path)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
            conn.close()
            self.assertIn("conclusion", columns)

    def test_rebuild_indexes_task_conclusion_from_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workeventagent.sqlite"
            project_path = Path(tmp) / "completion.md"
            project_path.write_text(
                """---
project_id: completion
title: Completion
doc_kind: work_project
schema_version: 2
status: active
phase: build
created: 2026-07-23
updated: 2026-07-23
---
## 工作地图 <!-- section:work-map -->
### 工作项：Cache <!-- item:cache -->

#### [x] 任务：Verify cache <!-- task:verify-cache -->
- 下一步：Run edge cases.
- 结论：Prefix reuse is stable.
<!-- task-meta:last_event_id=event-a -->
## 事件证据 <!-- section:timeline -->
""",
                encoding="utf-8",
            )

            init_db(db_path)
            rebuild_index(db_path, [project_path])

            self.assertEqual(
                get_task(db_path, "verify-cache")["conclusion"],
                "Prefix reuse is stable.",
            )

    def test_rebuild_indexes_task_state_from_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workeventagent.sqlite"
            project_path = Path("tests/fixtures/multimodal-labeling.md")

            init_db(db_path)
            rebuild_index(db_path, [project_path])
            rebuild_index(db_path, [project_path])
            task = get_task(db_path, "kv-cache-blockers")

            self.assertEqual(task["project_id"], "multimodal-labeling")
            self.assertEqual(task["item_id"], "kv-cache-few-shot")
            self.assertEqual(task["status"], "in_progress")

    def test_rebuild_indexes_indented_attachments(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workeventagent.sqlite"
            project_path = Path("tests/fixtures/multimodal-labeling.md")

            init_db(db_path)
            rebuild_index(db_path, [project_path])

            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            rows = [
                dict(row)
                for row in conn.execute(
                    "SELECT path, task_id, note FROM attachments ORDER BY path"
                )
            ]
            conn.close()

        self.assertEqual(
            rows,
            [
                {
                    "path": "attachments/2026-06-29/baseline.png",
                    "task_id": "kv-cache-blockers",
                    "note": "Existing archived image.",
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
