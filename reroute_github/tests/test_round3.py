"""Round 3: batch load test, fallback, the three safety cases on demand, and Log IDs in the audit trail."""

import pytest

import agent
import loadtest
import seed_data
import services

ALL_IDS = [r[0] for r in seed_data.TRANSACTIONS]


def test_batch_runs_every_scenario_correctly_in_parallel(db, fake_ai):
    r = loadtest.run_batch(ALL_IDS, workers=3)
    assert r["count"] == 27 and r["finished"] == 27
    assert r["correct"] == 27, [x for x in r["rows"] if x["route"] != x["expected"]]
    assert r["p50_s"] is not None and r["p95_s"] >= r["p50_s"]
    assert all(x["first_log_id"] for x in r["rows"])            # every payment has Log IDs


def test_batch_copies_stay_out_of_the_real_queues(db, fake_ai):
    import common
    import theme
    loadtest.run_batch(["T003", "T005"], workers=2)               # one escalated, one held
    assert common.pending("staff") == [] and common.pending("student") == []
    assert theme.counts()["escalated"] == 0


def test_batch_never_touches_the_real_demo_data(db, fake_ai):
    loadtest.run_batch(["T001", "T003"], workers=2)
    with services.db() as con:
        states = dict(con.execute("SELECT txn_id, state FROM transactions WHERE txn_id IN ('T001','T003')"))
    assert states == {"T001": "FLAGGED", "T003": "FLAGGED"}


def test_batch_can_be_rerun_with_the_same_results(db, fake_ai):
    first = loadtest.run_batch(ALL_IDS[:12], workers=3)
    second = loadtest.run_batch(ALL_IDS[:12], workers=3)       # old copies are cleared, so no false duplicates
    assert first["correct"] == second["correct"] == 12
    assert len(loadtest.past_runs()) == 2


def test_failures_get_a_clear_message_and_never_crash_the_batch(db, ai_down):
    r = loadtest.run_batch(["T001", "T005", "T013"], workers=2)
    by_id = {x["txn_id"]: x for x in r["rows"]}
    assert by_id["B-T005"]["outcome"] == "Code-only fallback"   # the AI was needed but is down
    assert "code-only" in by_id["B-T005"]["message"]
    assert by_id["B-T005"]["route"] == "HOLD_FOR_CONFIRMATION"  # unsure without the AI -> a person decides
    assert by_id["B-T013"]["outcome"] == "Blocked before AI"
    assert by_id["B-T001"]["outcome"] == "Decided by rules"     # safe payments clear without the AI at all
    assert by_id["B-T001"]["route"] == "CLEAR"


def test_simulated_outage_falls_through_to_the_next_provider():
    calls = []
    chain = [{"name": "First", "url": "x", "key": "k", "model": "m", "timeout": 1},
             {"name": "Second", "url": "x", "key": "k", "model": "m", "timeout": 1}]
    agent.SIMULATED_OUTAGES.update({"First", "Second"})
    try:
        with pytest.raises(RuntimeError, match="Every AI provider failed"):
            agent.ask([], [], chain, on_fail=lambda name, exc: calls.append(name))
    finally:
        agent.SIMULATED_OUTAGES.clear()
    assert calls == ["First", "Second"]                          # tried in order, each failure reported


def test_circuit_breaker_skips_a_provider_that_just_failed(monkeypatch):
    """After an online provider fails, the next call skips it straight away instead of waiting on it again."""
    tried = []

    class Boom(Exception):
        pass

    class FakeClient:
        def __init__(self, base_url, **kw):
            self.name = base_url
            self.chat = self
            self.completions = self

        def create(self, **kw):
            tried.append(self.name)
            raise Boom("rate limited")

    monkeypatch.setattr(agent, "OpenAI", FakeClient)
    monkeypatch.setattr(agent, "_cooldown_until", {})
    chain = [{"name": "Online", "url": "online", "key": "k", "model": "m", "timeout": 1, "key_env": "X"},
             {"name": "Laptop", "url": "laptop", "key": "k", "model": "m", "timeout": 1, "key_env": None}]
    skipped = []
    for _ in range(2):
        with pytest.raises(RuntimeError):
            agent.ask([], [], chain, on_fail=lambda n, e: skipped.append(type(e).__name__))
    assert tried == ["online", "laptop", "laptop"]       # 2nd time: online provider skipped, laptop never skipped
    assert skipped == ["Boom", "Boom", "CoolingDown", "Boom"]


@pytest.mark.parametrize("kind,state", [("safe", "CLEARED"), ("risky", "ESCALATED"),
                                        ("malicious", "ESCALATED"), ("malformed", "ESCALATED")])
def test_safety_cases_on_demand(db, fake_ai, kind, state):
    res = loadtest.run_safety_case(kind)
    assert res["state"] == state, res["reason"]
    assert res["log_ids"][0].startswith("LOG-")
    if kind in ("malicious", "malformed"):
        assert res["txn_id"] not in fake_ai                      # never reached the AI
    events = [e["event"] for e in res["log"]]
    assert any(e.startswith("route ->") for e in events)


def test_safety_cases_can_be_triggered_again_and_again(db, fake_ai):
    assert [loadtest.run_safety_case("safe")["state"] for _ in range(3)] == ["CLEARED"] * 3


def test_risky_case_waits_for_staff_and_staff_can_decide(db, fake_ai):
    res = loadtest.run_safety_case("risky")
    assert agent.resolve(res["txn_id"], approve=True, actor="student")["ok"] is False   # not the student's call
    assert agent.resolve(res["txn_id"], approve=False, actor="staff")["state"] == "CANCELLED"


def test_log_ids_are_permanent_and_increasing(db, fake_ai):
    agent.process_transaction("T001")
    log = services.get_log("T001")
    ids = [e["id"] for e in log]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)
    assert services.log_id(7) == "LOG-000007"
