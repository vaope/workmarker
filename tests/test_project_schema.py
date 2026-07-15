from hashlib import sha256

import pytest

from workeventagent.project_schema import (
    find_section,
    metadata_hash,
    parse_frontmatter,
    parse_timeline_events,
    replace_section_content,
    schema_version,
    section_content,
    section_hash,
    update_frontmatter,
    validate_reviewed_content,
)


V2 = """---
project_id: demo
title: Demo
doc_kind: work_project
schema_version: 2
status: active
phase: build
created: 2026-07-13
updated: 2026-07-13
---
# Demo

## 任意可见标题 <!-- section:technical-overview -->

Electron 调度 Python。

## 事件证据 <!-- section:timeline -->

- 2026-07-13T10:00:00+08:00 <!-- event:event-a -->
  - task_id: task-a
  - summary: 完成基础验证
"""


def test_v2_section_lookup_uses_anchor_not_visible_title() -> None:
    section = find_section(V2, "technical-overview")
    assert section.heading == "## 任意可见标题 <!-- section:technical-overview -->"
    assert section_content(V2, "technical-overview").strip() == "Electron 调度 Python。"


def test_section_hash_covers_content_not_heading() -> None:
    content = "Electron 调度 Python。\n"
    assert section_hash(V2, "technical-overview") == "sha256:" + sha256(content.encode()).hexdigest()


def test_replace_section_preserves_neighbors_and_rejects_stale_control_text() -> None:
    updated = replace_section_content(V2, "technical-overview", "Python 负责确定性写入。\n")
    assert "Python 负责确定性写入。" in updated
    assert "<!-- event:event-a -->" in updated
    with pytest.raises(ValueError, match="control syntax"):
        validate_reviewed_content("## 伪造区块 <!-- section:timeline -->")
    with pytest.raises(ValueError, match="control syntax"):
        validate_reviewed_content("### 伪造子标题")


def test_timeline_parser_uses_stable_section_anchor_for_v2() -> None:
    events = parse_timeline_events(V2)
    assert [event["event_id"] for event in events] == ["event-a"]
    assert events[0]["summary"] == "完成基础验证"


def test_frontmatter_and_metadata_hash_are_explicit() -> None:
    assert schema_version(V2) == 2
    assert parse_frontmatter(V2)["phase"] == "build"
    assert metadata_hash(V2).startswith("sha256:")


def test_update_frontmatter_does_not_emit_six_dashes() -> None:
    """B5 regression: update_frontmatter must produce exactly one --- delimiter, not ------."""
    updated = update_frontmatter(V2, {"status": "completed"})
    assert "------" not in updated
    assert updated.count("---") == 2
    assert updated.startswith("---\n")
