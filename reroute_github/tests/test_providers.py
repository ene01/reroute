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


# ---------------------------------------------------------------- the single quick AI call
def online(*names):
    return [{"name": n, "url": n, "key": "k", "model": "m", "timeout": 5, "key_env": "X"} for n in names]


class Quick:
    """Fake client for the one quick call: checks the budget settings and hands out scripted replies."""

    def __init__(self, **queues):
        self.queues, self.calls = queues, []

    def factory(self, base_url, api_key, max_retries, timeout):
        assert max_retries == 0 and timeout == agent.QUICK_BUDGET_S      # one try, 8 s budget
        outer = self

        class Completions:
            def create(self, model, messages, temperature, max_tokens):
                assert max_tokens == agent.QUICK_MAX_TOKENS
                assert "<untrusted_sender_message>" in messages[-1]["content"]
                outer.calls.append(base_url)
                item = outer.queues[base_url].pop(0)
                if isinstance(item, Exception):
                    raise item
                return item

        return SimpleNamespace(chat=SimpleNamespace(completions=Completions()))


OPINION = json.dumps({"risk_level": "medium", "confidence": "high", "scam_signals": "new contact",
                      "slang_notes": "none", "student_explanation": "Make sure you know them."})


@pytest.fixture
def quick_setup(monkeypatch):
    monkeypatch.setattr(agent, "_cooldown_until", {})

    def setup(chain_list, **queues):
        q = Quick(**queues)
        monkeypatch.setattr(agent, "OpenAI", q.factory)
        monkeypatch.setattr(agent, "provider_chain", lambda: chain_list)
        return q
    return setup


def test_unsure_payment_gets_exactly_one_ai_call(db, quick_setup):
    q = quick_setup(online("A", "B"), A=[reply("<think>hmm</think>" + OPINION)])
    s = agent.process_transaction("T005")                      # peer sale: the rules need an opinion
    assert q.calls == ["A"]                                    # one call, one provider
    assert s["assessment"]["risk_level"] == "medium" and s["route"] == "HOLD_FOR_CONFIRMATION"


def test_a_failed_call_is_not_retried_and_code_decides(db, quick_setup):
    q = quick_setup(online("A", "B"), A=[TimeoutError("8 s passed")], B=[reply(OPINION)])
    s = agent.process_transaction("T005")
    assert q.calls == ["A"]                                    # no second provider, no retry
    assert s["route"] == "HOLD_FOR_CONFIRMATION" and s["assessment"]["risk_level"] == "not assessed"
    assert any(e["event"] == "agent error" for e in s["log"])


def test_skips_a_provider_that_is_cooling_down(db, quick_setup):
    q = quick_setup(online("A", "B"), B=[reply(OPINION)])
    agent._cooldown_until["A"] = agent.time.monotonic() + 30
    agent.process_transaction("T005")
    assert q.calls == ["B"]


def test_unreadable_reply_falls_back_to_code_only(db, quick_setup):
    quick_setup(online("A"), A=[reply("sure, looks fine to me")])
    s = agent.process_transaction("T005")
    assert s["route"] == "HOLD_FOR_CONFIRMATION"
    assert any(e["event"] == "agent error" for e in s["log"])


def test_no_online_provider_means_no_wait_on_the_laptop_model(db, quick_setup):
    q = quick_setup(chain("L"))                                # only the local model: not used on this path
    s = agent.process_transaction("T005")
    assert q.calls == [] and s["route"] == "HOLD_FOR_CONFIRMATION"
