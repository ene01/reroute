"""Tests for the AI provider fallback chain (online providers first, laptop model last).

No keys or internet needed: the OpenAI client is replaced by scripted fakes.
"""

import json
from types import SimpleNamespace

import pytest

import agent


def call(name, args, cid="c1"):
    return SimpleNamespace(id=cid, function=SimpleNamespace(name=name, arguments=args))


def reply(content="", tool_calls=None):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content,
                                                                             tool_calls=tool_calls))])


class Script:
    """Per-provider queues of replies (or exceptions) the fake client hands out in order."""

    def __init__(self, **queues):
        self.queues = queues
        self.calls = []

    def factory(self, base_url, api_key, max_retries, timeout):
        assert max_retries == 1 and timeout <= agent.LLM_TIMEOUT_S  # fail-fast settings from the manual
        script = self

        class Completions:
            def create(self, model, messages, tools, temperature, max_tokens):
                script.calls.append(base_url)
                item = script.queues[base_url].pop(0)
                if isinstance(item, Exception):
                    raise item
                return item

        return SimpleNamespace(chat=SimpleNamespace(completions=Completions()))


def chain(*names):
    return [{"name": n, "url": n, "key": "k", "model": "m", "timeout": 5} for n in names]


@pytest.fixture(autouse=True)
def no_ollama_lookup(monkeypatch):
    monkeypatch.setattr(agent, "active_model", lambda: "reroute")


def test_no_keys_means_local_only(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert [p["name"] for p in agent.provider_chain()] == ["Local (laptop)"]


def test_keys_build_the_full_chain_with_local_last(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-groq")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini")
    names = [p["name"] for p in agent.provider_chain()]
    assert names == ["Groq Qwen3.8 27B", "Groq GPT-OSS 20B", "Gemini Flash", "Local (laptop)"]
    assert all(ready for _, ready in agent.provider_status())


def test_status_never_exposes_keys(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "secret-value")
    assert "secret-value" not in str(agent.provider_status())


def test_ask_falls_through_to_the_next_provider(monkeypatch):
    s = Script(A=[RuntimeError("429 rate limit")], B=[reply("hi")])
    monkeypatch.setattr(agent, "OpenAI", s.factory)
    failures = []
    msg, provider = agent.ask([], [], chain("A", "B"), on_fail=lambda n, e: failures.append(n))
    assert provider == "B" and msg.content == "hi" and failures == ["A"]


def test_ask_raises_when_every_provider_fails(monkeypatch):
    s = Script(A=[RuntimeError("down")], B=[TimeoutError("slow")])
    monkeypatch.setattr(agent, "OpenAI", s.factory)
    with pytest.raises(RuntimeError, match="Every AI provider failed"):
        agent.ask([], [], chain("A", "B"))


def test_full_run_with_a_fallback_and_broken_json(db, monkeypatch):
    """Provider A is down; B answers. B's first tool call has broken JSON, which is reported back."""
    submit_args = json.dumps({"risk_level": "low", "confidence": "high", "scam_signals": "none",
                              "slang_notes": "none", "student_explanation": "Looks normal."})
    s = Script(
        A=[RuntimeError("down")] * 6,
        B=[reply(tool_calls=[call("get_transaction_context", "{not json")]),
           reply(tool_calls=[call("get_transaction_context", '{"txn_id": "T001"}'),
                             call("verify_campus_recipient", '{"handle": "@campus_canteen"}', "c2")]),
           reply(tool_calls=[call("submit_risk_assessment", submit_args)])])
    monkeypatch.setattr(agent, "OpenAI", s.factory)
    run = agent.AgentRun("T001")
    run.run(chain("A", "B"))
    assert run.assessment["risk_level"] == "low" and run.recipient["status"] == "ok"
    events = [(e["event"], e["detail"]) for e in agent.svc.get_log("T001")]
    assert ("answered by", "B") in events
    assert any(ev == "provider failed" and "A" in d for ev, d in events)


def test_all_providers_down_falls_back_to_code_only_rules(db, monkeypatch):
    s = Script(L=[RuntimeError("ollama not running")])
    monkeypatch.setattr(agent, "OpenAI", s.factory)
    monkeypatch.setattr(agent, "provider_chain", lambda: chain("L"))
    result = agent.process_transaction("T001")  # safe canteen payment
    assert result["route"] == "CLEAR"
    assert any(e["event"] == "agent error" for e in result["log"])
