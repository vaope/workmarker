import json
import subprocess


def _render(proposals: list[dict], jobs: list[dict] | None = None) -> str:
    script = (
        "const fs=require('fs');const vm=require('vm');"
        "vm.runInThisContext(fs.readFileSync('client/windows/knowledge-proposals.js','utf8'));"
        f"const proposals={json.dumps(proposals, ensure_ascii=False)};"
        f"const jobs={json.dumps(jobs or [], ensure_ascii=False)};"
        "process.stdout.write(KnowledgeProposals.renderReview(proposals,jobs));"
    )
    return subprocess.run(
        ["node", "-e", script], check=True, text=True, encoding="utf-8", capture_output=True
    ).stdout


def _section_proposal(**overrides: object) -> dict:
    proposal = {
        "proposal_id": "kp-safe",
        "proposal_kind": "section_bundle",
        "state": "needs_confirmation",
        "version": 4,
        "project_id": "demo",
        "project_path": "C:/workspace/demo.md",
        "trigger": "directed",
        "source_events": [
            {"event_id": "event-a", "summary": "完成 <script>alert(1)</script>"},
            {"event_id": "event-b", "summary": "确认风险"},
        ],
        "changes": [{
            "change_id": "change-current-panorama",
            "target_section": "current-panorama",
            "reason": "里程碑已变化",
            "before": "旧全景",
            "after": "<!-- panorama-meta source_events=event-a proposal=kp-safe -->\n新全景",
            "diff": "--- before/current-panorama\n+++ after/current-panorama\n-旧全景\n+新全景",
        }],
    }
    proposal.update(overrides)
    return proposal


def test_review_card_escapes_text_and_shows_evidence_reason_diff_and_target() -> None:
    html = _render([_section_proposal()])
    assert "<script>alert" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "event-a" in html and "event-b" in html
    assert "里程碑已变化" in html
    assert "current-panorama" in html
    assert "--- before/current-panorama" in html
    assert "<pre" in html
    assert "<!-- panorama-meta" not in html


def test_proposal_states_are_visually_distinct_and_only_pending_can_confirm() -> None:
    states = ["needs_confirmation", "applying", "rejected", "stale", "superseded", "applied"]
    html = _render([
        _section_proposal(proposal_id=f"kp-{state}", state=state, version=index + 1)
        for index, state in enumerate(states)
    ])
    for state in states:
        assert f"state-{state}" in html
    assert html.count("knowledge-confirm") == 1
    assert html.count("knowledge-reject") == 1


def test_document_card_shows_wrapper_identity_and_separate_confirmation() -> None:
    document = {
        "proposal_id": "kd-safe",
        "proposal_kind": "module_document",
        "state": "needs_confirmation",
        "version": 2,
        "project_id": "demo",
        "project_path": "C:/workspace/demo.md",
        "trigger": "weekly",
        "source_events": [{"event_id": "event-a", "summary": "架构完成"}],
        "filename": "architecture.md",
        "purpose": "保留架构细节",
        "retained_summary": "主文档保留摘要",
        "preview": "## 模块结论 <!-- section:module-conclusion -->\n结论\n## 详细内容 <!-- section:module-body -->\n正文",
    }
    html = _render([document])
    assert "architecture.md" in html
    assert "保留架构细节" in html
    assert "主文档保留摘要" in html
    assert "结论" in html and "正文" in html
    assert "knowledge-confirm-document" in html
    assert "target_path" not in html


def test_failed_jobs_render_retry_without_proposal_confirmation() -> None:
    html = _render([], [{
        "job_id": "kj-failed",
        "project_id": "demo",
        "state": "failed",
        "version": 3,
        "trigger": "daily",
        "last_error": "agent <b>failed</b>",
    }])
    assert "state-failed" in html
    assert "knowledge-retry" in html
    assert "&lt;b&gt;failed&lt;/b&gt;" in html
    assert "knowledge-confirm" not in html


def test_compact_banner_counts_pending_and_failed_work() -> None:
    script = (
        "const fs=require('fs');const vm=require('vm');"
        "vm.runInThisContext(fs.readFileSync('client/windows/knowledge-proposals.js','utf8'));"
        f"process.stdout.write(KnowledgeProposals.renderBanner({json.dumps([_section_proposal()])},"
        "[{state:'failed'}]));"
    )
    html = subprocess.run(
        ["node", "-e", script], check=True, text=True, encoding="utf-8", capture_output=True
    ).stdout
    assert "1" in html
    assert "待审核知识" in html
    assert "失败" in html


def test_high_impact_badge_shows_all_five_bounded_dimensions() -> None:
    script = (
        "const fs=require('fs');const vm=require('vm');"
        "vm.runInThisContext(fs.readFileSync('client/windows/knowledge-proposals.js','utf8'));"
        "process.stdout.write(KnowledgeProposals.renderImpactBadge({level:'high',"
        "dimensions:['goal','scope','architecture','risk','milestone'],reason:'项目方向变化'}));"
    )
    html = subprocess.run(
        ["node", "-e", script], check=True, text=True, encoding="utf-8", capture_output=True
    ).stdout
    for label in ("目标", "范围", "架构", "风险", "里程碑", "项目方向变化"):
        assert label in html
