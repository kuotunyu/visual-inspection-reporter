"""Gemini Batch orchestration tests with an in-memory fake SDK client."""

from __future__ import annotations

from types import SimpleNamespace

from google.genai import types

from inspector.batch_gemini import _build_inline_request, run_batch_assess
from inspector.providers.base import VLMRequest, VLMResponseError
from inspector.schema import ImageAssessment, Verdict


def _request(label: str) -> VLMRequest:
    return VLMRequest(
        prompt=f"prompt-{label}",
        annotated_jpeg=f"image-{label}".encode(),
        crops=[],
        findings_json=[{"id": 1, "label": label}],
    )


def _assessment(label: str) -> ImageAssessment:
    return ImageAssessment(verdict=Verdict.PASS, summary_zh=f"{label} 合格", findings=[])


def _response_item(key: str, label: str, *, input_tokens: int = 10):
    return SimpleNamespace(
        metadata={"vir_request_key": key},
        error=None,
        response=SimpleNamespace(
            parsed=_assessment(label),
            usage_metadata=SimpleNamespace(
                prompt_token_count=input_tokens,
                candidates_token_count=4,
                thoughts_token_count=2,
            ),
        ),
    )


class _FakeBatches:
    def __init__(self, terminal_job):
        self.terminal_job = terminal_job
        self.created = None
        self.get_names: list[str] = []

    def create(self, **kwargs):
        self.created = kwargs
        return SimpleNamespace(
            name="batches/test-job",
            state=types.JobState.JOB_STATE_RUNNING,
        )

    def get(self, *, name):
        self.get_names.append(name)
        return self.terminal_job


def test_inline_request_keeps_correlation_key_and_structured_schema():
    inline = _build_inline_request("board-01.jpg", _request("a"))

    assert inline.metadata == {"vir_request_key": "board-01.jpg"}
    assert inline.config.response_mime_type == "application/json"
    assert inline.config.response_schema is ImageAssessment
    assert len(inline.contents) == 4


def test_batch_maps_shuffled_results_by_metadata_and_reports_missing(monkeypatch):
    terminal_job = SimpleNamespace(
        state=types.JobState.JOB_STATE_SUCCEEDED,
        error=None,
        dest=SimpleNamespace(inlined_responses=[_response_item("second", "b", input_tokens=22)]),
    )
    batches = _FakeBatches(terminal_job)
    monkeypatch.setattr("inspector.batch_gemini.genai.Client", lambda: SimpleNamespace(batches=batches))
    monkeypatch.setattr("inspector.batch_gemini.time.sleep", lambda _seconds: None)
    polled: list[str] = []

    results = run_batch_assess(
        {"first": _request("a"), "second": _request("b")},
        "gemini-test",
        poll_interval_s=0,
        timeout_s=1,
        on_poll=polled.append,
    )

    assert batches.created["model"] == "gemini-test"
    assert len(batches.created["src"]) == 2
    assert batches.get_names == ["batches/test-job"]
    assert polled == ["JOB_STATE_RUNNING"]
    assessment, usage = results["second"]
    assert assessment.summary_zh == "b 合格"
    assert (usage.input_tokens, usage.output_tokens) == (22, 6)
    assert isinstance(results["first"], VLMResponseError)
    assert "缺少" in str(results["first"])


def test_batch_isolates_item_error_without_failing_other_items(monkeypatch):
    failed_item = SimpleNamespace(
        metadata={"vir_request_key": "bad"},
        error="safety filter",
        response=None,
    )
    terminal_job = SimpleNamespace(
        state=types.JobState.JOB_STATE_PARTIALLY_SUCCEEDED,
        error=None,
        dest=SimpleNamespace(
            inlined_responses=[failed_item, _response_item("good", "good")]
        ),
    )
    batches = _FakeBatches(terminal_job)
    monkeypatch.setattr("inspector.batch_gemini.genai.Client", lambda: SimpleNamespace(batches=batches))
    monkeypatch.setattr("inspector.batch_gemini.time.sleep", lambda _seconds: None)

    results = run_batch_assess(
        {"bad": _request("bad"), "good": _request("good")},
        "gemini-test",
        poll_interval_s=0,
    )

    assert isinstance(results["bad"], VLMResponseError)
    assert "safety filter" in str(results["bad"])
    assert results["good"][0].verdict is Verdict.PASS
