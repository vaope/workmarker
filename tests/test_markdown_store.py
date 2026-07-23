import unittest
import tempfile
from pathlib import Path

from workeventagent.markdown_store import ProjectDocument, write_project_atomically
from workeventagent.models import ArchiveProposal, TargetRef, TimelineEvent
from workeventagent.project_schema import schema_version, section_content
from workeventagent.work_map_store import parse_work_map

FIXTURE = Path("tests/fixtures/multimodal-labeling.md")
V2_FIXTURE = Path("tests/fixtures/project-v2.md")


class MarkdownStoreTest(unittest.TestCase):
    def proposal(self, new_task=False):
        task_id = "kv-cache-blockers-2" if new_task else "kv-cache-blockers"
        return ArchiveProposal(
            target=TargetRef(
                project_id="multimodal-labeling",
                item_id="kv-cache-few-shot",
                task_id=task_id,
                task_title="Review blocker details" if new_task else "",
                new_task=new_task,
            ),
            confidence=0.91,
            reason="Matched KV cache item.",
            event=TimelineEvent(
                event_id="20260629-153000123-kv-cache-blockers",
                task_id=task_id,
                input_text="Reviewed blockers.",
                summary="Prefix reuse strategy is unclear.",
                status="in_progress",
                next_action="Map current inference chain.",
            ),
        )

    def new_item_proposal(self):
        return ArchiveProposal(
            target=TargetRef(
                project_id="multimodal-labeling",
                item_id="capture-inbox",
                item_title="Capture Inbox",
                task_id="queue-processing",
                task_title="Queue processing",
                new_item=True,
                new_task=True,
            ),
            confidence=0.91,
            reason="User mentioned a new work stream.",
            event=TimelineEvent(
                event_id="20260706-100000123-queue-processing",
                task_id="queue-processing",
                input_text="Need capture queue support.",
                summary="Capture queue needs background processing.",
                status="in_progress",
                next_action="Design queue processing.",
            ),
        )

    def test_apply_existing_task_updates_block_and_appends_timeline(self):
        doc = ProjectDocument.from_text(FIXTURE.read_text(encoding="utf-8"))
        updated = doc.apply_proposal(self.proposal(), updated_date="2026-06-30")

        self.assertIn("last_event_id: 20260629-153000123-kv-cache-blockers", updated)
        self.assertIn("Map current inference chain.", updated)
        self.assertIn("<!-- event:20260629-153000123-kv-cache-blockers -->", updated)
        self.assertIn("updated: 2026-06-30", updated)
        # sibling task preserved
        self.assertIn("#### Task: Read KV cache fundamentals <!-- task:kv-cache-fundamentals -->", updated)
        # Decisions preserved
        self.assertIn("Keep current few-shot baseline until blocker review is complete.", updated)
        # Attachments preserved
        self.assertIn("attachments/2026-06-29/baseline.png", updated)

    def test_apply_existing_v1_task_preserves_conclusion_and_replaces_event_pointer(self):
        original = FIXTURE.read_text(encoding="utf-8").replace(
            "- next_action: Review current blocker list.\n- last_event_id:",
            "- next_action: Review current blocker list.\n"
            "- conclusion: Existing verified finding.\n"
            "- last_event_id:",
            1,
        )
        updated = ProjectDocument.from_text(original).apply_proposal(
            self.proposal(),
            updated_date="2026-06-30",
        )

        task = parse_work_map(updated)[0]["tasks"][0]
        self.assertEqual(task["conclusion"], "Existing verified finding.")
        self.assertEqual(task["last_event_id"], "20260629-153000123-kv-cache-blockers")
        target_block = updated.split("<!-- task:kv-cache-blockers -->", 1)[1].split("#### Task:", 1)[0]
        self.assertEqual(target_block.count("- last_event_id:"), 1)

    def test_apply_does_not_change_task_title_line(self):
        """砚砚验收点2: 更新已有 task 不许动标题行"""
        doc = ProjectDocument.from_text(FIXTURE.read_text(encoding="utf-8"))
        updated = doc.apply_proposal(self.proposal(), updated_date="2026-06-30")

        self.assertIn("#### Task: Review current blockers <!-- task:kv-cache-blockers -->", updated)

    def test_new_task_inserts_full_schema_block(self):
        """砚砚验收点1: new task 渲染必须产出完整 schema 行"""
        doc = ProjectDocument.from_text(FIXTURE.read_text(encoding="utf-8"))
        updated = doc.insert_new_task(self.proposal(new_task=True))

        # anchor
        self.assertIn("<!-- task:kv-cache-blockers-2 -->", updated)
        # title from task_title
        self.assertIn("#### Task: Review blocker details", updated)
        # mandatory schema lines
        self.assertIn("- status:", updated.split("#### Task: Review blocker details")[1])
        self.assertIn("- next_action:", updated.split("#### Task: Review blocker details")[1])
        self.assertIn("- conclusion:", updated.split("#### Task: Review blocker details")[1])
        self.assertIn("- last_event_id:", updated.split("#### Task: Review blocker details")[1])

    def test_new_item_inserts_item_and_task_before_timeline(self):
        doc = ProjectDocument.from_text(FIXTURE.read_text(encoding="utf-8"))
        updated = doc.insert_new_task(self.new_item_proposal())

        self.assertIn("### Item: Capture Inbox <!-- item:capture-inbox -->", updated)
        self.assertIn("#### Task: Queue processing <!-- task:queue-processing -->", updated)
        self.assertLess(
            updated.index("### Item: Capture Inbox <!-- item:capture-inbox -->"),
            updated.index("## Decisions"),
        )

    def test_atomic_write_replaces_whole_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "project.md"
            path.write_text("old", encoding="utf-8")

            write_project_atomically(path, "new")

            self.assertEqual(path.read_text(encoding="utf-8"), "new")


class V2MarkdownStoreTest(unittest.TestCase):
    def v2_proposal(self):
        return ArchiveProposal(
            target=TargetRef(
                project_id="report-project",
                item_id="capture",
                task_id="persist-card",
                task_title="Persist card",
            ),
            confidence=1.0,
            reason="v2 capture test",
            event=TimelineEvent(
                event_id="20260713-120000000-persist-card",
                task_id="persist-card",
                input_text="Finished persistence.",
                summary="Persistence is complete.",
                status="done",
                next_action="Add retry.",
            ),
        )

    def test_v2_capture_updates_only_target_task_and_appends_timeline(self):
        """Plan Task 4 Step 1: capture on v2 doc updates the task and appends timeline."""
        original = V2_FIXTURE.read_text(encoding="utf-8")
        doc = ProjectDocument.from_text(original)
        updated = doc.apply_proposal(self.v2_proposal(), updated_date="2026-07-13")

        # Schema stays v2
        assert schema_version(updated) == 2

        # Task checkbox updated: from [x] to [x] (already done, but should still work)
        assert "#### [x] 任务：Persist card <!-- task:persist-card -->" in updated

        # Only one a-task heading
        assert updated.count("<!-- task:persist-card -->") == 1

        # Timeline event appended
        assert "<!-- event:20260713-120000000-persist-card -->" in updated

        # Sibling task (route-archive) preserved
        assert "<!-- task:route-archive -->" in updated

        # Decisions section untouched
        assert section_content(updated, "decisions") == section_content(original, "decisions")

    def test_v2_capture_marks_in_progress_as_done(self):
        """Capture on an in_progress v2 task changes [ ] to [x]."""
        original = V2_FIXTURE.read_text(encoding="utf-8")
        proposal = ArchiveProposal(
            target=TargetRef(
                project_id="report-project",
                item_id="capture",
                task_id="route-archive",
                task_title="Route archive",
            ),
            confidence=1.0,
            reason="mark done",
            event=TimelineEvent(
                event_id="20260713-130000000-route-archive",
                task_id="route-archive",
                input_text="Completed routing.",
                summary="Routing is done.",
                status="done",
                next_action="",
            ),
        )
        doc = ProjectDocument.from_text(original)
        updated = doc.apply_proposal(proposal, updated_date="2026-07-13")

        assert "#### [x] 任务：Route archive <!-- task:route-archive -->" in updated
        assert updated.count("<!-- task:route-archive -->") == 1


if __name__ == "__main__":
    unittest.main()
