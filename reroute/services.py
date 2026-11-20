"""Reroute: mock PocketPay wallet back-end services - TRADITIONAL CODE, no AI here.

  get_transaction_context()  - read the payment + message + student profile from SQLite
  verify_campus_recipient()  - campus trust check, with a timeout + graceful outage fallback
  update_payment_state()     - THE single write gateway: the only way a payment's state changes.
                               Enforces allowed transitions, re-checks limits one last time, and saves
                               the state, the AI opinion, the pending approval and any alert in ONE
                               transaction, so either everything is saved or nothing is (rollback).

There is deliberately no "freeze wallet" state anywhere in this system.
"""

from datetime import datetime, timedelta

import sqlite3
import time
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from pathlib import Path

DB_PATH = Path(__file__).with_name("campus_wallet.sqlite")
VERIFY_TIMEOUT_S = 2.0

# Allowed state machine. Anything not listed is rejected.
#   FLAGGED -> CLEARED | PENDING_CONFIRMATION | ESCALATED     (set by the routing guard)
#   PENDING_CONFIRMATION -> COMPLETED | CANCELLED              (set by the student)
#   ESCALATED -> COMPLETED | CANCELLED                         (set by support staff)
ALLOWED_TRANSITIONS = {
    "FLAGGED": {"CLEARED", "PENDING_CONFIRMATION", "ESCALATED"},
    "PENDING_CONFIRMATION": {"COMPLETED", "CANCELLED"},
    "ESCALATED": {"COMPLETED", "CANCELLED"},
    "CLEARED": set(), "COMPLETED": set(), "CANCELLED": set(),
}


@contextmanager
def db():
    """Open a connection, commit on success, and ALWAYS close it (Windows locks open SQLite files)."""
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def get_transaction_context(txn_id: str) -> dict:
    """Return the flagged payment, the chat message that triggered it, and the student's spending profile."""
    with db() as con:
        row = con.execute(
            """SELECT t.*, s.name AS student_name, s.grade, s.avg_daily_spend_aed, s.wallet_balance_aed
               FROM transactions t JOIN students s ON s.student_id = t.student_id
               WHERE t.txn_id = ?""", (txn_id,)).fetchone()
    if row is None:
        return {"error": f"Transaction {txn_id} not found"}
    return {
        "txn_id": row["txn_id"],
        "created_at": row["created_at"],
        "amount_aed": row["amount_aed"],
        "recipient_handle": row["recipient_handle"],
        "channel": row["channel"],
        "note": row["note"] or "(none)",
        "message_text": row["message_text"] or "(no message attached)",
        "prior_txns_with_recipient": row["prior_txns_with_recipient"],
        "student": {"name": row["student_name"], "grade": row["grade"],
                    "avg_daily_spend_aed": row["avg_daily_spend_aed"],
                    "wallet_balance_aed": row["wallet_balance_aed"]},
        "state": row["state"],
    }


def _lookup_recipient(handle: str) -> dict:
    with db() as con:
        row = con.execute("SELECT * FROM recipients WHERE handle = ?", (handle,)).fetchone()
    if row is None:
        return {"status": "not_found", "handle": handle, "campus_verified": False, "trust_score": 0}
    if row["simulate_outage"]:
        time.sleep(VERIFY_TIMEOUT_S + 1.5)  # pretend the campus registry is down / slow
    # Deterministic trust score, 0-100.
    score = 40 if row["campus_verified"] else 0
    score += min(row["account_age_days"] / 365, 1) * 25
    score += min(row["completed_txns"] / 100, 1) * 25
    score += 10 if row["scam_reports"] == 0 else 0
    score -= row["scam_reports"] * 15
    return {
        "status": "ok", "handle": handle, "display_name": row["display_name"], "kind": row["kind"],
        "campus_verified": bool(row["campus_verified"]), "account_age_days": row["account_age_days"],
        "completed_txns": row["completed_txns"], "scam_reports": row["scam_reports"],
        "trust_score": max(0, min(100, round(score))),
    }


_pool = ThreadPoolExecutor(max_workers=4)


def verify_campus_recipient(handle: str) -> dict:
    """Check whether a recipient is a verified campus vendor/club/student and return their trust score.

    If the registry doesn't answer within VERIFY_TIMEOUT_S, returns status 'unavailable' instead of crashing.
    """
    future = _pool.submit(_lookup_recipient, handle)
    try:
        return future.result(timeout=VERIFY_TIMEOUT_S)
    except FutureTimeout:
        return {"status": "unavailable", "handle": handle,
                "detail": f"Campus registry timed out after {VERIFY_TIMEOUT_S:.0f}s"}
    except Exception as exc:  # any other failure also degrades gracefully
        return {"status": "unavailable", "handle": handle, "detail": f"Registry error: {exc}"}


def find_duplicates(txn_id: str, window_min: int = 10) -> list[str]:
    """Earlier payments with the same student, recipient and amount within window_min minutes."""
    with db() as con:
        me = con.execute("SELECT * FROM transactions WHERE txn_id = ?", (txn_id,)).fetchone()
        if me is None:
            return []
        rows = con.execute(
            """SELECT txn_id, created_at FROM transactions
               WHERE student_id = ? AND recipient_handle = ? AND amount_aed = ? AND txn_id != ?""",
            (me["student_id"], me["recipient_handle"], me["amount_aed"], txn_id)).fetchall()
    try:
        mine = datetime.fromisoformat(me["created_at"])
    except (TypeError, ValueError):
        return []
    dupes = []
    for r in rows:
        try:
            gap = mine - datetime.fromisoformat(r["created_at"])
        except (TypeError, ValueError):
            continue
        if timedelta(0) <= gap <= timedelta(minutes=window_min):
            dupes.append(r["txn_id"])
    return sorted(dupes)


class GatewayCheckFailed(Exception):
    """The last re-check inside the write gateway refused the change. Nothing was written."""


def update_payment_state(txn_id: str, new_state: str, reason: str, actor: str, *,
                         assessment: tuple | None = None, pending: tuple | None = None,
                         resolution: str | None = None, alert: tuple | None = None,
                         final_check=None) -> dict:
    """THE single write gateway. Every payment state change goes through here, all-or-nothing.

    assessment: row for the assessments table (saved with the decision)
    pending:    (awaiting, question) - opens a human confirmation
    resolution: text - closes the open human confirmation
    alert:      (handle, text) - broadcasts a scam alert
    final_check: callable returning a problem string (or None); run first, raises GatewayCheckFailed.
    """
    if final_check is not None:
        problem = final_check()
        if problem:
            raise GatewayCheckFailed(problem)

    with db() as con:  # one transaction: commit at the end, roll back everything on any error
        row = con.execute("SELECT state FROM transactions WHERE txn_id = ?", (txn_id,)).fetchone()
        if row is None:
            return {"ok": False, "error": "unknown transaction"}
        current = row["state"]
        if current == new_state:
            return {"ok": True, "state": current, "note": "already in this state"}
        if new_state not in ALLOWED_TRANSITIONS.get(current, set()):
            return {"ok": False, "state": current,
                    "error": f"Transition {current} -> {new_state} is not allowed"}

        if actor in ("student", "staff"):
            log(con, txn_id, "HUMAN", f"{actor} decision", reason)
        con.execute("UPDATE transactions SET state = ? WHERE txn_id = ?", (new_state, txn_id))
        log(con, txn_id, "TOOL", "update_payment_state", f"{current} -> {new_state} by {actor}: {reason}")
        if assessment is not None:
            con.execute("INSERT OR REPLACE INTO assessments VALUES (?,?,?,?,?,?,?,?)", assessment)
        if pending is not None:
            con.execute("INSERT OR REPLACE INTO pending_confirmations (txn_id, awaiting, question) "
                        "VALUES (?,?,?)", (txn_id, *pending))
            log(con, txn_id, "GUARD", "paused",
                f"Waiting for {pending[0]}; execution stopped here (no polling). "
                "Money is held, wallet is NOT frozen.")
        if resolution is not None:
            con.execute("UPDATE pending_confirmations SET resolved = 1, resolution = ? WHERE txn_id = ?",
                        (resolution, txn_id))
        if alert is not None:
            con.execute("INSERT INTO scam_alerts (handle, txn_id, summary) VALUES (?,?,?)",
                        (alert[0], txn_id, alert[1]))
            log(con, txn_id, "TOOL", "broadcast_alert", alert[1])
    return {"ok": True, "state": new_state, "previous": current}


def log(con: sqlite3.Connection, txn_id: str, actor: str, event: str, detail: str) -> None:
    step = con.execute("SELECT COALESCE(MAX(step), 0) + 1 FROM decision_log WHERE txn_id = ?",
                       (txn_id,)).fetchone()[0]
    con.execute("INSERT INTO decision_log (txn_id, step, actor, event, detail) VALUES (?,?,?,?,?)",
                (txn_id, step, actor, event, detail))


def write_log(txn_id: str, actor: str, event: str, detail: str) -> None:
    with db() as con:
        log(con, txn_id, actor, event, detail)


def get_log(txn_id: str) -> list[dict]:
    with db() as con:
        return [dict(r) for r in con.execute(
            "SELECT step, actor, event, detail, ts FROM decision_log WHERE txn_id = ? ORDER BY step", (txn_id,))]
