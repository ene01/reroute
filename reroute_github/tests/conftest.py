"""Shared test setup: a fresh copy of the database for every test, and a fake AI.

The fake AI calls the same tools the real one does and returns a fixed opinion per scenario,
so tests run in seconds and give the same result every time (no Ollama needed).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent  # noqa: E402
import seed_data  # noqa: E402
import services  # noqa: E402

# A plausible opinion for each scenario: (risk_level, confidence)
OPINIONS = {
    "T001": ("low", "high"), "T002": ("low", "high"), "T003": ("high", "high"), "T004": ("low", "high"),
    "T005": ("medium", "medium"), "T006": ("high", "high"), "T007": ("high", "low"), "T008": ("high", "low"),
    "T009": ("high", "low"), "T010": ("medium", "medium"), "T011": ("low", "high"), "T012": ("low", "high"),
    "T013": ("low", "high"), "T014": ("low", "high"), "T015": ("low", "high"), "T016": ("low", "high"),
    "T017": ("low", "high"),
    "T018": ("low", "high"), "T019": ("low", "high"), "T020": ("low", "high"), "T021": ("high", "high"),
    "T022": ("high", "high"), "T023": ("low", "high"), "T024": ("low", "high"), "T025": ("high", "low"),
    "T026": ("medium", "medium"), "T027": ("low", "high"),
}
# Sample requests every new account gets (txn ids end in -1, -2, -3)
STARTER_OPINIONS = {"1": ("low", "high"), "2": ("medium", "medium"), "3": ("high", "high")}


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "test_wallet.sqlite"
    monkeypatch.setattr(services, "DB_PATH", path)
    seed_data.seed(path)
    return path


@pytest.fixture
def fake_ai(monkeypatch):
    """Replace the LLM loop. Returns the list of txn_ids the AI was asked about."""
    asked = []

    def fake_run(self, pre=None):
        asked.append(self.txn_id)
        ctx = self.get_transaction_context(self.txn_id)
        self.verify_campus_recipient(ctx["recipient_handle"])
        # Demo scenario -> fixed opinion; sample request -> by suffix; student-to-student payment -> per message
        risk, conf = (OPINIONS.get(self.txn_id) or STARTER_OPINIONS.get(self.txn_id.rsplit("-", 1)[-1])
                      or (("high", "high") if "urgent" in (ctx.get("message_text") or "").lower()
                          else ("low", "high")))
        self.submit_risk_assessment(risk, conf, "test signals", "test slang", "Test explanation.")

    monkeypatch.setattr(agent.AgentRun, "run", fake_run)
    return asked


@pytest.fixture
def clock(monkeypatch):
    """A controllable wall clock for the 2-minute cancel window: clock.advance(seconds)."""
    from datetime import datetime, timedelta

    class Clock:
        t = datetime(2026, 11, 21, 12, 0, 0)

        def advance(self, seconds):
            self.t += timedelta(seconds=seconds)

    c = Clock()
    monkeypatch.setattr(services, "now", lambda: c.t)
    return c


@pytest.fixture
def ai_down(monkeypatch):
    def broken_run(self, pre=None):
        raise ConnectionError("Ollama is not running")

    monkeypatch.setattr(agent.AgentRun, "run", broken_run)
