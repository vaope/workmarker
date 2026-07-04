from __future__ import annotations

import json
from pathlib import Path

from workeventagent.inbox_store import (
    archive_capture,
    cancel_capture,
    capture_pending_dir,
    create_capture,
    list_captures,
    update_capture,
)


def test_create_capture_persists_card_and_copies_attachment(tmp_path: Path) -> None:
    source = tmp_path / "clip.png"
    source.write_bytes(b"image-bytes")

    card = create_capture(tmp_path, "mapped KV cache blockers", [
        {"temp_path": str(source), "filename": "clip.png"},
    ])

    cards = list_captures(tmp_path)
    assert cards[0]["capture_id"] == card["capture_id"]
    assert cards[0]["state"] == "processing"
    assert cards[0]["text"] == "mapped KV cache blockers"
    pending_file = capture_pending_dir(tmp_path, card["capture_id"]) / "clip.png"
    assert pending_file.read_bytes() == b"image-bytes"


def test_update_capture_writes_proposal_without_losing_original_text(tmp_path: Path) -> None:
    card = create_capture(tmp_path, "original", [])

    updated = update_capture(tmp_path, card["capture_id"], {
        "state": "needs_confirmation",
        "proposal": {"event": {"summary": "summary"}},
        "selected_project": {"path": "project.md"},
    })

    assert updated["text"] == "original"
    assert updated["state"] == "needs_confirmation"
    assert updated["proposal"]["event"]["summary"] == "summary"


def test_cancel_capture_deletes_pending_directory_but_keeps_bounded_record(tmp_path: Path) -> None:
    source = tmp_path / "clip.png"
    source.write_bytes(b"image-bytes")
    card = create_capture(tmp_path, "cancel me", [{"temp_path": str(source), "filename": "clip.png"}])

    canceled = cancel_capture(tmp_path, card["capture_id"])

    assert canceled["state"] == "canceled"
    assert not capture_pending_dir(tmp_path, card["capture_id"]).exists()
    assert list_captures(tmp_path)[0]["state"] == "canceled"


def test_archive_capture_deletes_pending_directory_and_trims_only_terminal_cards(tmp_path: Path) -> None:
    for idx in range(105):
        card = create_capture(tmp_path, f"text {idx}", [])
        archive_capture(tmp_path, card["capture_id"], {
            "project_path": f"project-{idx}.md",
            "event_id": f"event-{idx}",
        })
    active = create_capture(tmp_path, "still active", [])

    cards = list_captures(tmp_path)

    assert any(c["capture_id"] == active["capture_id"] for c in cards)
    assert len([c for c in cards if c["state"] == "archived"]) <= 100
