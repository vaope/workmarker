from pathlib import Path


def test_archivist_prompt_requires_faithful_information_preservation() -> None:
    prompt = Path(".opencode/agent/workevent-archivist.md").read_text(encoding="utf-8")

    assert "faithful compression" in prompt
    assert "named technologies" in prompt
    assert "input data types or modalities" in prompt
    assert "expected outputs" in prompt
    assert "Do not replace a specific technical topic with a broader field" in prompt
