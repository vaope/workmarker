import tempfile
import unittest
from pathlib import Path

from workeventagent.index_store import get_task, init_db, rebuild_index


class IndexStoreTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
