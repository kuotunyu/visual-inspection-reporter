"""Provider adapter contract tests using mocked SDK clients (zero network)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from inspector.providers.base import VLMRequest, VLMResponseError
from inspector.providers.claude import ClaudeProvider
from inspector.providers.gemini import GeminiProvider
from inspector.schema import FindingAssessment, ImageAssessment, Severity, Verdict


def _request() -> VLMRequest:
    return VLMRequest(
        prompt="你是品管工程師",
        annotated_jpeg=b"annotated-jpeg",
        crops=[(7, b"crop-jpeg")],
        findings_json=[{"id": 7, "class_name": "short"}],
    )


def _assessment() -> ImageAssessment:
    return ImageAssessment(
        verdict=Verdict.FAIL,
        summary_zh="發現一處重大短路。",
        findings=[
            FindingAssessment(
                finding_id=7,
                severity=Severity.CRITICAL,
                description_zh="銅箔形成橋接。",
                action_zh="隔離並返修。",
            )
        ],
    )


def test_claude_provider_builds_multimodal_request_and_maps_usage(monkeypatch):
    client = MagicMock()
    client.messages.parse.return_value = SimpleNamespace(
        parsed_output=_assessment(),
        content=[],
        usage=SimpleNamespace(input_tokens=123, output_tokens=45),
    )
    monkeypatch.setattr("inspector.providers.claude.Anthropic", lambda: client)

    assessment, usage = ClaudeProvider("claude-test").assess_image(_request())

    assert assessment.verdict is Verdict.FAIL
    assert (usage.model_id, usage.input_tokens, usage.output_tokens) == ("claude-test", 123, 45)
    call = client.messages.parse.call_args.kwargs
    assert call["model"] == "claude-test"
    assert call["system"] == "你是品管工程師"
    assert call["output_format"] is ImageAssessment
    content = call["messages"][0]["content"]
    assert [block["type"] for block in content] == ["text", "image", "text", "image", "text"]
    assert content[1]["source"]["data"] == "YW5ub3RhdGVkLWpwZWc="
    assert '"id": 7' in content[-1]["text"]


def test_claude_provider_rejects_unparsed_response(monkeypatch):
    client = MagicMock()
    client.messages.parse.return_value = SimpleNamespace(
        parsed_output=None,
        content=[SimpleNamespace(type="text", text="not valid JSON")],
        usage=SimpleNamespace(input_tokens=1, output_tokens=1),
    )
    monkeypatch.setattr("inspector.providers.claude.Anthropic", lambda: client)

    with pytest.raises(VLMResponseError, match="not valid JSON"):
        ClaudeProvider("claude-test").assess_image(_request())


def test_gemini_provider_builds_schema_request_and_includes_thought_tokens(monkeypatch):
    client = MagicMock()
    client.models.generate_content.return_value = SimpleNamespace(
        parsed=_assessment(),
        text=None,
        usage_metadata=SimpleNamespace(
            prompt_token_count=80,
            candidates_token_count=20,
            thoughts_token_count=5,
        ),
    )
    monkeypatch.setattr("inspector.providers.gemini.genai.Client", lambda: client)

    assessment, usage = GeminiProvider("gemini-test").assess_image(_request())

    assert assessment.findings[0].finding_id == 7
    assert (usage.model_id, usage.input_tokens, usage.output_tokens) == ("gemini-test", 80, 25)
    call = client.models.generate_content.call_args.kwargs
    assert call["model"] == "gemini-test"
    assert call["config"].response_mime_type == "application/json"
    assert call["config"].response_schema is ImageAssessment
    assert len(call["contents"]) == 6
    assert call["contents"][0].text == "你是品管工程師"
    assert '"id": 7' in call["contents"][-1].text


def test_gemini_provider_rejects_unparsed_response(monkeypatch):
    client = MagicMock()
    client.models.generate_content.return_value = SimpleNamespace(
        parsed=None,
        text="not valid JSON",
        usage_metadata=SimpleNamespace(),
    )
    monkeypatch.setattr("inspector.providers.gemini.genai.Client", lambda: client)

    with pytest.raises(VLMResponseError, match="not valid JSON"):
        GeminiProvider("gemini-test").assess_image(_request())
