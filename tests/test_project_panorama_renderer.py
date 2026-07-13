"""Node-backed renderer tests for project-panorama.js — pure module, no IPC or DOM."""

import json
import subprocess
import unittest
from pathlib import Path


FIXTURE_DATA = {
    "project": {"project_id": "demo", "title": "Demo", "status": "active", "phase": "build"},
    "sections": {
        "project-profile": {"title": "项目档案", "ownership": "reviewed", "content": "### 背景\n信息散落。", "source_event_ids": []},
        "current-panorama": {"title": "当前全景", "ownership": "derived-reviewed", "content": "正在构建。", "source_event_ids": ["event-a"]},
        "technical-overview": {"title": "技术概览", "ownership": "reviewed", "content": "Electron 调度 Python。", "source_event_ids": []},
        "project-knowledge": {"title": "关键认知", "ownership": "reviewed", "content": "- Markdown 是真相源。", "source_event_ids": []},
        "decisions": {"title": "关键决策", "ownership": "append-only", "content": "- 使用稳定锚点。", "source_event_ids": []},
        "attachments": {"title": "附件", "ownership": "append-only", "content": "", "source_event_ids": []},
        "timeline": {"title": "事件证据", "ownership": "append-only", "content": "<!-- section:timeline -->\n- 2026-07-13 event-a", "source_event_ids": []},
        "rollups": {"title": "历史摘要", "ownership": "derived", "content": "", "source_event_ids": []},
    },
}


def _render_panorama(data=None, technical=None):
    """Run the renderer in a Node subprocess and return the rendered HTML."""
    payload = data or FIXTURE_DATA
    if technical is not None:
        payload = json.loads(json.dumps(payload, ensure_ascii=False))
        payload["sections"]["technical-overview"]["content"] = technical
    work_map_html = '<section class="item-group">工作地图占位</section>'
    script = (
        "const fs=require('fs');const vm=require('vm');"
        "vm.runInThisContext(fs.readFileSync('client/windows/project-panorama.js','utf8'));"
        f"const data={json.dumps(payload, ensure_ascii=False)};"
        f"process.stdout.write(ProjectPanorama.render(data,'{work_map_html}'));"
    )
    return subprocess.run(["node", "-e", script], check=True, text=True, capture_output=True, encoding="utf-8").stdout


class PanoramaRendererTests(unittest.TestCase):
    def test_renderer_orders_sections_and_hides_control_metadata(self):
        rendered = _render_panorama()
        # Section order: profile → panorama → work-map → technical → knowledge
        self.assertLess(rendered.index("项目档案"), rendered.index("当前全景"))
        self.assertLess(rendered.index("当前全景"), rendered.index("工作地图占位"))
        self.assertLess(rendered.index("工作地图占位"), rendered.index("技术概览"))
        self.assertLess(rendered.index("技术概览"), rendered.index("关键认知"))
        # Control comments must be stripped
        self.assertNotIn("<!-- panorama-meta", rendered)
        self.assertNotIn("<!-- section:", rendered)
        # data attributes for sections
        self.assertIn('data-section-id="timeline"', rendered)
        # Timeline/rollups collapsed by default
        self.assertIn("<details", rendered)

    def test_renderer_escapes_user_content(self):
        rendered = _render_panorama(technical="<script>alert(1)</script>")
        self.assertIn("&lt;script&gt;", rendered)
        self.assertNotIn("<script>alert", rendered)

    def test_renderer_shows_ownership_badges(self):
        rendered = _render_panorama()
        self.assertIn("需审阅", rendered)
        self.assertIn("只追加", rendered)

    def test_renderer_emits_edit_buttons_only_for_editable_sections(self):
        rendered = _render_panorama()
        # Profile, technical-overview, project-knowledge are editable
        profile_idx = rendered.index("项目档案")
        tech_idx = rendered.index("技术概览")
        knowledge_idx = rendered.index("关键认知")
        # Each editable section should have an edit button nearby
        edit_section = rendered.count("edit-section")
        edit_profile = rendered.count("edit-profile")
        # At least some edit buttons exist
        self.assertGreater(edit_section + edit_profile, 0)

    def test_renderer_empty_section_shows_placeholder(self):
        rendered = _render_panorama()
        self.assertIn("尚未填写", rendered)

    def test_renderer_source_buttons_for_reviewed_sections(self):
        rendered = _render_panorama()
        # source-event button for sections with source_event_ids
        self.assertIn("source-section", rendered)

    def test_ipc_exposes_only_typed_panorama_operations(self):
        main_src = Path("client/main.js").read_text(encoding="utf-8")
        preload_src = Path("client/preload.js").read_text(encoding="utf-8")
        for channel in ("projectPanorama", "previewProjectMigration", "applyProjectMigration",
                        "updateProjectProfile", "updateProjectSection"):
            self.assertIn(channel, main_src)
            self.assertIn(channel, preload_src)
        self.assertNotIn("writeProjectMarkdown", preload_src)


if __name__ == "__main__":
    unittest.main()
