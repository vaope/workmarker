import unittest

from workeventagent.confirm import parse_confirmation_input, render_confirmation_card
from workeventagent.models import ArchiveProposal, TargetRef, TimelineEvent


class ConfirmTest(unittest.TestCase):
    def test_card_shows_new_task_flag_and_structured_fields(self):
        proposal = ArchiveProposal(
            target=TargetRef(
                "multimodal-labeling",
                "kv-cache-few-shot",
                "new-task",
                task_title="New task",
                new_task=True,
            ),
            confidence=0.8,
            reason="User mentioned a new task.",
            event=TimelineEvent(
                "event-1",
                "new-task",
                "input",
                "summary",
                "in_progress",
                "next",
            ),
        )

        card = render_confirmation_card(proposal)

        self.assertIn("new_task: true", card)
        self.assertIn("New task", card)
        self.assertIn("confirm / edit / cancel", card)

    def test_parse_confirmation_input(self):
        self.assertEqual(parse_confirmation_input("confirm").kind, "confirm")
        self.assertEqual(parse_confirmation_input(" CONFIRM ").kind, "confirm")
        self.assertEqual(parse_confirmation_input("edit").kind, "edit")
        self.assertEqual(parse_confirmation_input("cancel").kind, "cancel")
        self.assertEqual(parse_confirmation_input("").kind, "cancel")
        self.assertEqual(parse_confirmation_input("y").kind, "cancel")
        self.assertEqual(parse_confirmation_input("???").kind, "cancel")


if __name__ == "__main__":
    unittest.main()
