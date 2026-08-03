import json

import pytest

from workeventagent.opencode_runner import OpencodeRunnerError, parse_archivist_output


def _archivist_output(summary: str, next_action: str = "继续处理。") -> str:
    return json.dumps({
        "target": {"project_id": "p", "item_id": "i", "task_id": "t"},
        "confidence": 0.9,
        "reason": "matched",
        "event": {
            "task_id": "t",
            "summary": summary,
            "status": "in_progress",
            "next_action": next_action,
        },
    }, ensure_ascii=False)


def test_preserves_source_and_rejects_lossy_summary() -> None:
    source = (
        "学习一下具身领域这些都是怎么做的\n"
        "· 基于多模态大模型（VLM/VLA）构建自动标注管线，对视频、动作轨迹、触觉信号等"
        "进行结构化标注（动作阶段、接触事件、成功/失败标签），降低人工标注成本；"
    )
    raw = json.dumps({
        "target": {
            "project_id": "job-search",
            "item_id": "knowledge",
            "task_id": "embodied-ai",
            "task_title": "学习具身领域实践方法",
            "new_task": True,
        },
        "confidence": 0.92,
        "reason": "new learning task",
        "event": {
            "task_id": "embodied-ai",
            "summary": "开始调研具身智能领域的常见做法、技术路线和实践方法。",
            "status": "in_progress",
            "next_action": "检索具身智能领域综述。",
        },
    }, ensure_ascii=False)

    proposal = parse_archivist_output(raw, "event-1", source_text=source)

    assert proposal.event.input_text == source
    for detail in (
        "VLM/VLA",
        "视频",
        "动作轨迹",
        "触觉信号",
        "动作阶段",
        "接触事件",
        "成功/失败标签",
        "降低人工标注成本",
    ):
        assert detail in proposal.event.summary


def test_keeps_specific_faithful_summary() -> None:
    source = "使用 VLM/VLA 对视频进行自动标注，输出动作阶段标签。"
    summary = "调研使用 VLM/VLA 对视频自动标注并输出动作阶段标签的方法。"

    proposal = parse_archivist_output(
        _archivist_output(summary, "调研 VLM/VLA 视频自动标注方案。"),
        "event-1",
        source_text=source,
    )

    assert proposal.event.summary == summary


def test_requires_an_authoritative_source() -> None:
    with pytest.raises(OpencodeRunnerError, match="source text"):
        parse_archivist_output(_archivist_output("整理进展。"), "event-1")


def test_rejects_lossy_short_chinese_summary() -> None:
    source = "排查登录接口在令牌过期后重复报错的问题。"

    proposal = parse_archivist_output(
        _archivist_output("排查系统问题。", "继续排查。"),
        "event-1",
        source_text=source,
    )

    assert proposal.event.summary == source


def test_rejects_lossy_multibullet_summary() -> None:
    source = "\n".join((
        "梳理登录稳定性问题：",
        "· 复现访问令牌过期后的接口报错链路，并记录网关与服务端响应差异；",
        "· 核对刷新令牌并发更新时的状态覆盖，确认缓存与数据库写入顺序；",
        "· 验证客户端重试与服务端幂等处理是否配合，排除请求重复提交；",
        "· 检查跨设备登录时旧会话的失效过程，确认账户状态能够及时同步；",
        "· 对比弱网和网络切换场景下的恢复行为，记录每次重连的状态变化；",
        "· 检查后台任务延迟执行时的凭据读取逻辑，不再继续使用过期数据；",
        "· 核对用户主动退出后的本地清理过程，确认敏感信息不会继续留存；",
        "· 补充失败告警渠道和恢复结果记录，便于后续定位同类异常。",
    ))
    assert len(source) > 240

    proposal = parse_archivist_output(
        _archivist_output("排查并改善系统稳定性问题。", "检查系统链路。"),
        "event-1",
        source_text=source,
    )

    for detail in ("访问令牌", "刷新令牌", "幂等处理", "失败告警"):
        assert detail in proposal.event.summary


def test_keeps_faithful_multibullet_summary() -> None:
    source = "\n".join((
        "梳理登录稳定性问题：",
        "· 复现访问令牌过期后的接口报错链路；",
        "· 核对刷新令牌并发更新时的状态覆盖。",
    ))
    summary = "复现访问令牌过期后的报错链路，并核对刷新令牌并发更新时的状态覆盖。"

    proposal = parse_archivist_output(
        _archivist_output(summary, "先复现访问令牌过期后的报错。"),
        "event-1",
        source_text=source,
    )

    assert proposal.event.summary == summary
