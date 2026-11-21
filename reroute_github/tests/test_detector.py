"""Tests for Reroute's own trained scam detector and how the rules use it."""

import pytest

import agent
import detector
import guards
import seed_data
from tests.test_guards import VENDOR, ctx

pytestmark = pytest.mark.skipif(not detector.available(), reason="run python train_detector.py first")


def test_detector_scores_obvious_scam_high_and_normal_low():
    scam = detector.score("URGENT your PocketPay wallet is locked, pay a 50 AED fee at secure-check.xyz now")
    normal = detector.score("thanks for the shawarma yesterday, sending you 15")
    assert scam["scam_probability"] >= detector.HIGH and scam["label"] == "likely scam"
    assert normal["scam_probability"] < detector.LOW and normal["label"] == "looks normal"


def test_detector_explains_itself():
    s = detector.score("claim your free Front Row Fest pass now, pay the fee here")
    assert s["top_words"], "should list the words that raised the score"


def test_no_message_means_no_score():
    assert detector.score("") is None
    assert detector.score(None) is None
    assert detector.score("(no message attached)") is None


def test_demo_scenarios_it_never_trained_on():
    """The training data is synthetic templates; the demo messages are kept out of it."""
    rows = {r[0]: r for r in seed_data.TRANSACTIONS}
    for txn_id in ("T003", "T006", "T013"):
        assert detector.score(rows[txn_id][8])["scam_probability"] >= 0.5, txn_id
    for txn_id in ("T004", "T005", "T011", "T016"):
        assert detector.score(rows[txn_id][8])["scam_probability"] < 0.5, txn_id


def test_demo_messages_are_not_in_the_training_data():
    import detector_data
    training = {t.lower() for t, _ in detector_data.build_dataset()}
    for row in seed_data.TRANSACTIONS:
        assert (row[8] or "").lower() not in training or not row[8]


def test_high_detector_score_blocks_auto_clear_but_never_blocks_the_payment():
    c = ctx(message_text="free prize, pay the fee now")
    pre = guards.preflight(c, [], {"scam_probability": 0.95, "label": "likely scam", "top_words": ["prize"]})
    action, reasons, _ = guards.route(c, VENDOR, {"risk_level": "low", "confidence": "high"}, pre)
    assert action == "HOLD_FOR_CONFIRMATION" and "detector" in reasons[0]
    assert guards.final_clear_check(c, pre)  # the write gateway would refuse a CLEAR too


def test_low_detector_score_changes_nothing():
    c = ctx(message_text="thanks for lunch")
    pre = guards.preflight(c, [], {"scam_probability": 0.05, "label": "looks normal", "top_words": []})
    assert guards.route(c, VENDOR, {"risk_level": "low", "confidence": "high"}, pre)[0] == "CLEAR"


def test_detector_result_reaches_the_ai_tool_and_the_log(db, fake_ai):
    s = agent.process_transaction("T003")
    preflight = next(e for e in s["log"] if e["event"] == "pre-flight check")
    assert "Reroute detector" in preflight["detail"]
    run = agent.AgentRun("T003", {"scam_probability": 0.9, "label": "likely scam", "top_words": ["claim"]})
    assert run.check_scam_score("T003")["scam_probability"] == 0.9
