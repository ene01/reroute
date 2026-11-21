"""End-to-end tests of the full workflow, with the AI replaced by a fixed fake (see conftest.py)."""

import pytest

import agent
import seed_data
import services


ALL_IDS = [row[0] for row in seed_data.TRANSACTIONS]


@pytest.mark.parametrize("txn_id", ALL_IDS)
def test_every_scenario_routes_as_expected(db, fake_ai, txn_id):
    s = agent.process_transaction(txn_id)
    assert s["route"] == s["expected_route"], s["log"]


def test_attacks_and_malformed_input_never_reach_the_ai(db, fake_ai):
    for txn_id in ALL_IDS:
        agent.process_transaction(txn_id)
    assert not {"T013", "T014", "T017", "T023", "T027"} & set(fake_ai)
    assert {"T005", "T012", "T026"} <= set(fake_ai)       # the rules are unsure: the AI is asked once
    assert not {"T001", "T003", "T006"} & set(fake_ai)    # the rules are sure: zero model calls


def test_ai_outage_falls_back_to_code_only(db, ai_down):
    """With the AI down, safe payments still clear and nothing is frozen or crashes."""
    expected_without_ai = {"T001": "CLEAR", "T003": "ESCALATE", "T005": "HOLD_FOR_CONFIRMATION",
                           "T009": "ESCALATE", "T010": "HOLD_FOR_CONFIRMATION", "T013": "ESCALATE"}
    for txn_id, route in expected_without_ai.items():
        assert agent.process_transaction(txn_id)["route"] == route, txn_id


def test_pending_payment_is_not_rerun(db, fake_ai):
    """Loop prevention: a payment waiting on a human is never re-run or re-asked."""
    agent.process_transaction("T005")
    assert agent.process_transaction("T005")["state"] == "PENDING_CONFIRMATION"
    assert fake_ai.count("T005") == 1


def test_student_confirm_and_cancel(db, fake_ai):
    agent.process_transaction("T005")
    assert agent.resolve("T005", approve=True, actor="student")["state"] == "CONFIRMED_CANCELLABLE"
    agent.process_transaction("T012")
    assert agent.resolve("T012", approve=False, actor="student")["state"] == "CANCELLED"


def test_wrong_person_cannot_resolve(db, fake_ai):
    agent.process_transaction("T003")  # escalated -> waits for staff
    assert agent.resolve("T003", approve=True, actor="student")["ok"] is False


def test_staff_confirm_scam_broadcasts_alert(db, fake_ai):
    agent.process_transaction("T003")
    assert agent.resolve("T003", approve=False, actor="staff")["state"] == "CANCELLED"
    with services.db() as con:
        alerts = con.execute("SELECT handle FROM scam_alerts").fetchall()
    assert [a["handle"] for a in alerts] == ["@campus_tix_99"]


def test_write_gateway_rolls_back_everything_on_failure(db, fake_ai, monkeypatch):
    """If anything fails mid-write, no part of the decision is saved."""
    real_log = services.log

    def failing_log(con, txn_id, actor, event, detail):
        if event == "update_payment_state":
            raise RuntimeError("simulated crash in the middle of the write")
        real_log(con, txn_id, actor, event, detail)

    monkeypatch.setattr(services, "log", failing_log)
    with pytest.raises(RuntimeError):
        agent.process_transaction("T005")
    with services.db() as con:
        state = con.execute("SELECT state FROM transactions WHERE txn_id='T005'").fetchone()["state"]
        saved = con.execute("SELECT COUNT(*) FROM assessments WHERE txn_id='T005'").fetchone()[0]
        pending = con.execute("SELECT COUNT(*) FROM pending_confirmations WHERE txn_id='T005'").fetchone()[0]
    assert (state, saved, pending) == ("FLAGGED", 0, 0)


def test_gateway_recheck_downgrades_a_clear(db, fake_ai, monkeypatch):
    """If the last re-check in the write gateway fails, the payment is held instead of cleared."""
    monkeypatch.setattr(agent.guards, "final_clear_check", lambda fresh, pre: "simulated limit change")
    s = agent.process_transaction("T001")
    assert s["state"] == "PENDING_CONFIRMATION"
    assert any(e["event"] == "write gateway refused" for e in s["log"])


def test_every_step_is_logged(db, fake_ai):
    s = agent.process_transaction("T005")                      # the AI is asked
    events = [e["event"] for e in s["log"]]
    for expected in ("get_transaction_context", "verify_campus_recipient", "pre-flight check", "start",
                     "risk opinion", "route -> HOLD_FOR_CONFIRMATION", "update_payment_state", "paused"):
        assert expected in events, events
    s = agent.process_transaction("T003")                      # the rules decide alone
    events = [e["event"] for e in s["log"]]
    for expected in ("check_ticket_ownership", "pre-flight check", "AI not needed", "route -> ESCALATE",
                     "update_payment_state", "paused"):
        assert expected in events, events
