import dataclasses
from datetime import datetime, timezone
from pathlib import Path

import pytest

from workeventagent import project_migration
from workeventagent.project_migration import apply_v1_to_v2, preview_v1_to_v2
from workeventagent.project_schema import schema_version


def fixed_now() -> datetime:
    return datetime(2026, 7, 13, 12, 0, 0, tzinfo=timezone.utc)


def write_v1_fixture(tmp_path: Path) -> Path:
    project = tmp_path / "multimodal-labeling.md"
    project.write_text(
        Path("tests/fixtures/multimodal-labeling.md").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return project


def test_preview_preserves_identity_and_unknown_content() -> None:
    source = Path("tests/fixtures/multimodal-labeling.md").read_text(encoding="utf-8")
    source += "\n## Custom Notes\n\nkeep-this-byte-for-byte\n"
    preview = preview_v1_to_v2(source, status="active", phase="delivery")
    assert preview.source_schema == 1
    assert preview.target_schema == 2
    assert "schema_version: 2" in preview.migrated_text
    assert "keep-this-byte-for-byte" in preview.migrated_text
    assert preview.before_identity == preview.after_identity
    assert preview.diff.startswith("--- ")


def test_apply_rejects_stale_source_without_backup_or_write(tmp_path: Path) -> None:
    project = write_v1_fixture(tmp_path)
    original = project.read_text(encoding="utf-8")
    result = apply_v1_to_v2(
        project,
        tmp_path / "index.sqlite",
        source_hash="sha256:stale",
        status="active",
        phase="delivery",
        now=fixed_now(),
    )
    assert result["kind"] == "stale_source"
    assert project.read_text(encoding="utf-8") == original
    assert not (tmp_path / ".workeventagent" / "backups").exists()


def test_apply_writes_backup_then_verified_v2(tmp_path: Path) -> None:
    project = write_v1_fixture(tmp_path)
    preview = preview_v1_to_v2(project.read_text(encoding="utf-8"), "active", "delivery")
    result = apply_v1_to_v2(project, tmp_path / "index.sqlite", preview.source_hash, "active", "delivery", fixed_now())
    assert result["ok"] is True
    assert Path(result["backup_path"]).read_text(encoding="utf-8") == preview.original_text
    assert schema_version(project.read_text(encoding="utf-8")) == 2


def test_apply_restores_backup_when_readback_identity_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = write_v1_fixture(tmp_path)
    original = project.read_text(encoding="utf-8")
    preview = preview_v1_to_v2(original, "active", "delivery")

    calls = {"count": 0}
    real_manifest = project_migration.identity_manifest

    def flaky_identity(text: str):
        calls["count"] += 1
        if calls["count"] >= 3:
            return dataclasses.replace(real_manifest(text), timeline_event_count=999)
        return real_manifest(text)

    monkeypatch.setattr(project_migration, "identity_manifest", flaky_identity)
    result = apply_v1_to_v2(project, tmp_path / "index.sqlite", preview.source_hash, "active", "delivery", fixed_now())
    assert result["ok"] is False
    assert result["kind"] == "migration_verify_failed"
    assert result["restored"] is True
    # After restore, the project file should have the original content
    assert project.read_text(encoding="utf-8") == original


def test_migration_injects_missing_sections_in_correct_order() -> None:
    """Regression: v1 doc missing both project-knowledge and technical-overview
    must have both injected, with project-knowledge before technical-overview."""
    source = Path("tests/fixtures/multimodal-labeling.md").read_text(encoding="utf-8")
    assert "## 关键认知" not in source
    assert "## 技术概览" not in source
    preview = preview_v1_to_v2(source, status="active", phase="delivery")
    text = preview.migrated_text
    assert "## 关键认知" in text
    assert "## 技术概览" in text
    pk_pos = text.index("## 关键认知")
    to_pos = text.index("## 技术概览")
    assert to_pos < pk_pos, "technical-overview must appear before project-knowledge"


def test_preview_requires_explicit_status_and_phase() -> None:
    source = Path("tests/fixtures/multimodal-labeling.md").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="status"):
        preview_v1_to_v2(source, status="", phase="delivery")
    with pytest.raises(ValueError, match="phase"):
        preview_v1_to_v2(source, status="active", phase="")
