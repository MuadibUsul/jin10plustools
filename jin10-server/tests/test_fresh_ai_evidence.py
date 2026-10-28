"""Evidence repair must recover literal source quotes without bypassing guards."""
from __future__ import annotations

import io
import json

import pytest

from app.news_skill_os import fresh_ai


ARTICLE = "Professor Oak said Anthropic acquired a one-person biotech company for $1 million."
CLAIM = "Anthropic acquired a one-person biotech company for $1 million."


def _reply(content: dict) -> bytes:
    return json.dumps({"choices": [{"finish_reason": "stop", "message": {
        "content": json.dumps(content),
    }}]}).encode()


def _analysis(evidence: str) -> dict:
    return {
        "event_title": "Anthropic收购一家生物技术公司",
        "claims": [{"subject": "Anthropic", "predicate": "收购", "text": CLAIM,
                    "evidence": evidence}],
        "analysis": {"dateline": "【纽约】", "title": "Anthropic收购一家生物技术公司",
                     "body": "【纽约】Anthropic收购一家生物技术公司。"},
        "sources": [],
    }


def _run(monkeypatch, answers: list[bytes]) -> tuple[dict, list]:
    calls = []
    responses = iter(answers)
    monkeypatch.setattr(fresh_ai, "briefs_ai", lambda team: {
        "key": "test", "model": "test-model", "base_url": "https://ai.example.test",
    })

    def fake_urlopen(request, timeout):
        calls.append(json.loads(request.data))
        return io.BytesIO(next(responses))

    monkeypatch.setattr(fresh_ai, "urlopen", fake_urlopen)
    result = fresh_ai.analyze(None, {"url": "https://x.com/example/status/1", "text": ARTICLE})
    return result, calls


def test_literal_evidence_keeps_single_ai_request(monkeypatch):
    result, calls = _run(monkeypatch, [_reply(_analysis(CLAIM))])
    assert result["claims"][0]["evidence"] == CLAIM
    assert len(calls) == 1


def test_stored_evidence_uses_exact_source_whitespace():
    assert fresh_ai._source_span("First line\n  second line.", "First line second line.") == (
        "First line\n  second line."
    )


def test_paraphrased_evidence_is_replaced_with_literal_source_span(monkeypatch):
    result, calls = _run(monkeypatch, [
        _reply(_analysis("Anthropic bought a solo biotech company for $1 million.")),
        _reply({"quotes": {"0": CLAIM}}),
    ])
    assert result["claims"][0]["evidence"] == CLAIM
    assert len(calls) == 2
    assert "原文定位器" in calls[1]["messages"][0]["content"]


def test_invented_repair_quote_still_fails(monkeypatch):
    with pytest.raises(fresh_ai.FreshAIError, match="原文证据在新闻页面中不存在"):
        _run(monkeypatch, [
            _reply(_analysis("Anthropic bought a solo biotech company for $1 million.")),
            _reply({"quotes": {"0": "A made-up quote for $1 million."}}),
        ])


def test_repair_cannot_add_a_claim_number_not_in_source_quote(monkeypatch):
    altered = _analysis("Anthropic bought a solo biotech company for $1 million.")
    altered["claims"][0]["text"] = "Anthropic acquired a company for $2 million."
    with pytest.raises(fresh_ai.FreshAIError, match="原文证据中没有的数字"):
        _run(monkeypatch, [
            _reply(altered),
            _reply({"quotes": {"0": CLAIM}}),
        ])
