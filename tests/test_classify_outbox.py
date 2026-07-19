from pathlib import Path

from workeventagent.classify_outbox import (
    dequeue_pending,
    enqueue_classification,
    mark_done,
    mark_failed,
    reconcile_from_timeline,
)


def _make_outbox(tmp_path: Path) -> Path:
    return tmp_path / "classify_outbox"


class TestEnqueueDequeue:
    def test_enqueue_and_dequeue(self, tmp_path: Path) -> None:
        outbox = _make_outbox(tmp_path)
        enqueue_classification(outbox, "demo", "ev-1")
        enqueue_classification(outbox, "demo", "ev-2")

        pending = dequeue_pending(outbox, limit=10)
        assert len(pending) == 2
        event_ids = {j["event_id"] for j in pending}
        assert event_ids == {"ev-1", "ev-2"}

    def test_enqueue_is_idempotent(self, tmp_path: Path) -> None:
        outbox = _make_outbox(tmp_path)
        enqueue_classification(outbox, "demo", "ev-1")
        enqueue_classification(outbox, "demo", "ev-1")  # duplicate

        pending = dequeue_pending(outbox, limit=10)
        assert len(pending) == 1

    def test_dequeue_respects_limit(self, tmp_path: Path) -> None:
        outbox = _make_outbox(tmp_path)
        for i in range(5):
            enqueue_classification(outbox, "demo", f"ev-{i}")

        pending = dequeue_pending(outbox, limit=3)
        assert len(pending) == 3


class TestMarkDone:
    def test_mark_done_removes_from_pending(self, tmp_path: Path) -> None:
        outbox = _make_outbox(tmp_path)
        enqueue_classification(outbox, "demo", "ev-1")
        enqueue_classification(outbox, "demo", "ev-2")
        mark_done(outbox, "demo", "ev-1")

        pending = dequeue_pending(outbox, limit=10)
        assert len(pending) == 1
        assert pending[0]["event_id"] == "ev-2"

    def test_mark_done_idempotent(self, tmp_path: Path) -> None:
        outbox = _make_outbox(tmp_path)
        enqueue_classification(outbox, "demo", "ev-1")
        mark_done(outbox, "demo", "ev-1")
        mark_done(outbox, "demo", "ev-1")  # second call just missing_ok=True

        pending = dequeue_pending(outbox, limit=10)
        assert len(pending) == 0


class TestMarkFailed:
    def test_mark_failed_moves_to_failed_dir(self, tmp_path: Path) -> None:
        outbox = _make_outbox(tmp_path)
        enqueue_classification(outbox, "demo", "ev-1")
        mark_failed(outbox, "demo", "ev-1", "LLM timeout")

        pending = dequeue_pending(outbox, limit=10)
        assert len(pending) == 0

        failed_path = outbox / "failed" / "demo.ev-1.json"
        assert failed_path.exists()

    def test_mark_failed_increments_retry(self, tmp_path: Path) -> None:
        outbox = _make_outbox(tmp_path)
        enqueue_classification(outbox, "demo", "ev-1")
        mark_failed(outbox, "demo", "ev-1", "error 1")

        import json
        failed_path = outbox / "failed" / "demo.ev-1.json"
        data = json.loads(failed_path.read_text(encoding="utf-8"))
        assert data["retries"] == 1
        assert data["last_error"] == "error 1"

    def test_mark_failed_on_missing_job_is_noop(self, tmp_path: Path) -> None:
        outbox = _make_outbox(tmp_path)
        mark_failed(outbox, "demo", "no-such-event", "error")
        # Should not raise


class TestReconcile:
    def test_reconcile_finds_missing_events(self, tmp_path: Path) -> None:
        outbox = _make_outbox(tmp_path)
        events = [
            {"event_id": "ev-a", "summary": "done A"},
            {"event_id": "ev-b", "summary": "done B"},
        ]
        # Only ev-a is in outbox and marked done
        enqueue_classification(outbox, "demo", "ev-a")
        mark_done(outbox, "demo", "ev-a")

        missing = reconcile_from_timeline(outbox, "demo", events)
        assert missing == ["ev-b"]

    def test_reconcile_sees_failed_jobs_as_tracked(self, tmp_path: Path) -> None:
        outbox = _make_outbox(tmp_path)
        events = [
            {"event_id": "ev-x", "summary": "failed before"},
        ]
        enqueue_classification(outbox, "demo", "ev-x")
        mark_failed(outbox, "demo", "ev-x", "error")

        missing = reconcile_from_timeline(outbox, "demo", events)
        assert missing == []  # failed is still tracked

    def test_reconcile_all_tracked(self, tmp_path: Path) -> None:
        outbox = _make_outbox(tmp_path)
        events = [
            {"event_id": "ev-1", "summary": "done"},
            {"event_id": "ev-2", "summary": "pending"},
        ]
        enqueue_classification(outbox, "demo", "ev-1")
        mark_done(outbox, "demo", "ev-1")
        enqueue_classification(outbox, "demo", "ev-2")

        missing = reconcile_from_timeline(outbox, "demo", events)
        assert missing == []
