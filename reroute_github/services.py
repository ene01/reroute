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

import os
import re
import sqlite3
import time
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from pathlib import Path

DB_PATH = Path(__file__).with_name("campus_wallet.sqlite")
VERIFY_TIMEOUT_S = 2.0

# Allowed state machine. Anything not listed is rejected (and the attempt is logged).
#   FLAGGED -> CLEARED | PENDING_CONFIRMATION | ESCALATED | PENDING_GUARDIAN   (set by the routing guard)
#   PENDING_CONFIRMATION -> CONFIRMED_CANCELLABLE | CANCELLED    (the student confirms or cancels)
#   CONFIRMED_CANCELLABLE -> CANCELLED   only INSIDE the cancel window, only by the student   (HS-1.4)
#   CONFIRMED_CANCELLABLE -> COMPLETED   only AFTER the cancel window ends ("released")
#   ESCALATED -> COMPLETED | CANCELLED | PENDING_GUARDIAN        (staff; a guardian may still be needed)
#   PENDING_GUARDIAN -> COMPLETED | CANCELLED                    (the guardian decides; HS-1.5)
ALLOWED_TRANSITIONS = {
    "FLAGGED": {"CLEARED", "PENDING_CONFIRMATION", "ESCALATED", "PENDING_GUARDIAN"},
    "PENDING_CONFIRMATION": {"CONFIRMED_CANCELLABLE", "CANCELLED"},
    "CONFIRMED_CANCELLABLE": {"COMPLETED", "CANCELLED"},
    "ESCALATED": {"COMPLETED", "CANCELLED", "PENDING_GUARDIAN"},
    "PENDING_GUARDIAN": {"COMPLETED", "CANCELLED"},
    "CLEARED": set(), "COMPLETED": set(), "CANCELLED": set(),
}
CANCEL_WINDOW_S = int(os.getenv("REROUTE_CANCEL_WINDOW_S", "120"))   # HS-1.4: 2-minute regret window


def now() -> datetime:
    """Real wall-clock time, used for the 2-minute cancel window. (Tests replace this function.)"""
    return datetime.now()


def cancel_seconds_left(confirmed_at: str | None) -> float:
    """Seconds left in the cancel window. 0 when it is over (or the time is missing/unreadable)."""
    try:
        elapsed = (now() - datetime.fromisoformat(str(confirmed_at))).total_seconds()
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, CANCEL_WINDOW_S - elapsed)


# Columns added in Round 2. Older databases get them on start-up (ensure_columns).
NEW_COLUMNS = [("transactions", "confirmed_at", "TEXT"), ("transactions", "needs_guardian", "INTEGER DEFAULT 0"),
               ("students", "age", "INTEGER")]


def ensure_columns(con: sqlite3.Connection) -> None:
    for table, column, kind in NEW_COLUMNS:
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
        if cols and column not in cols:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")


_wal_on: set[str] = set()


@contextmanager
def db():
    """Open a connection, commit on success, and ALWAYS close it (Windows locks open SQLite files)."""
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    # WAL: readers never wait for a writer, so parallel checks don't queue up. busy_timeout: wait up to 10 s
    # for a lock instead of failing. (WAL is remembered by the database file once it is switched on.)
    con.execute("PRAGMA busy_timeout = 10000")
    if str(DB_PATH) not in _wal_on:
        con.execute("PRAGMA journal_mode = WAL")
        _wal_on.add(str(DB_PATH))
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
            """SELECT t.*, s.name AS student_name, s.grade, s.age AS student_age, s.avg_daily_spend_aed,
                      s.wallet_balance_aed
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
        "student": {"name": row["student_name"], "grade": row["grade"], "age": row["student_age"],
                    "avg_daily_spend_aed": row["avg_daily_spend_aed"],
                    "wallet_balance_aed": row["wallet_balance_aed"]},
        "state": row["state"],
        "needs_guardian": bool(row["needs_guardian"]),
        "confirmed_at": row["confirmed_at"],
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


_verify_cache: dict[str, tuple[float, dict]] = {}
VERIFY_CACHE_S = 60


def verify_cached(handle: str) -> dict:
    """verify_campus_recipient with a 60-second memory, for the trust chip shown while typing a recipient."""
    hit = _verify_cache.get(handle)
    if hit and time.monotonic() - hit[0] < VERIFY_CACHE_S:
        return hit[1]
    result = verify_campus_recipient(handle)
    _verify_cache[handle] = (time.monotonic(), result)
    return result


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


def recent_to_same_recipient(txn_id: str, window_min: int = 60) -> dict:
    """HS-1.1: earlier payments from the same student to the same recipient within window_min minutes
    (anything except cancelled ones). Returns {count, total, txn_ids}."""
    empty = {"count": 0, "total": 0.0, "txn_ids": []}
    with db() as con:
        me = con.execute("SELECT rowid, * FROM transactions WHERE txn_id = ?", (txn_id,)).fetchone()
        if me is None:
            return empty
        rows = con.execute(
            """SELECT rowid, txn_id, amount_aed, created_at FROM transactions
               WHERE student_id = ? AND recipient_handle = ? AND txn_id != ? AND state != 'CANCELLED'""",
            (me["student_id"], me["recipient_handle"], txn_id)).fetchall()
    try:
        mine = datetime.fromisoformat(me["created_at"])
    except (TypeError, ValueError):
        return empty
    hits = []
    for r in rows:
        try:
            gap = mine - datetime.fromisoformat(r["created_at"])
        except (TypeError, ValueError):
            continue
        earlier = gap > timedelta(0) or (gap == timedelta(0) and r["rowid"] < me["rowid"])
        if earlier and gap <= timedelta(minutes=window_min) and (r["amount_aed"] or 0) > 0:
            hits.append(r)
    return {"count": len(hits), "total": float(sum(r["amount_aed"] for r in hits)),
            "txn_ids": sorted(r["txn_id"] for r in hits)}


def verified_vendor_handles() -> list[str]:
    """HS-1.2: the verified campus vendors and clubs that lookalike names are compared against."""
    with db() as con:
        return [r[0] for r in con.execute(
            "SELECT handle FROM recipients WHERE campus_verified = 1 AND kind IN ('vendor', 'org')")]


# Mock campus ticket registry: who really holds tickets for each campus event (made-up data).
TICKET_OWNERS = {
    "front row fest": {"@sara_k", "@lina_m", "@adam_s", "@campus_tix_official"},
    "spring formal": {"@sara_k", "@omar_r2"},
    "robotics finals": {"@robotics_club", "@lina_m"},
}
TICKET_WORDS_RE = re.compile(r"\b(tickets?|tix|pass(es)?|wristbands?)\b", re.I)


def check_ticket_ownership(seller_handle: str, text: str) -> dict | None:
    """Mock ticket-ownership tool. If the payment text is about a ticket for a known campus event, look the
    seller up in the ticket registry. Returns {"event", "owns"} or None when it isn't a known-event ticket."""
    text = str(text or "").lower()
    if not TICKET_WORDS_RE.search(text):
        return None
    event = next((e for e in TICKET_OWNERS if e in text), None)
    if event is None:
        return None          # a ticket for something we don't know about: can't check, the AI judges it
    return {"event": event.title(), "owns": seller_handle in TICKET_OWNERS[event]}


class GatewayCheckFailed(Exception):
    """The last re-check inside the write gateway refused the change. Nothing was written."""


def update_payment_state(txn_id: str, new_state: str, reason: str, actor: str, *,
                         assessment: tuple | None = None, pending: tuple | None = None,
                         resolution: str | None = None, alert: tuple | None = None,
                         final_check=None, needs_guardian: bool | None = None) -> dict:
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
        row = con.execute("SELECT state, confirmed_at FROM transactions WHERE txn_id = ?", (txn_id,)).fetchone()
        if row is None:
            return {"ok": False, "error": "unknown transaction"}
        current = row["state"]
        if current == new_state:
            return {"ok": True, "state": current, "note": "already in this state"}
        problem = None
        if new_state not in ALLOWED_TRANSITIONS.get(current, set()):
            problem = f"Transition {current} -> {new_state} is not allowed"
        elif current == "CONFIRMED_CANCELLABLE":       # HS-1.4: the cancel window decides, in code
            left = cancel_seconds_left(row["confirmed_at"])
            if new_state == "CANCELLED" and actor != "student":
                problem = "Only the student can cancel during the cancel window"
            elif new_state == "CANCELLED" and left <= 0:
                problem = f"The {CANCEL_WINDOW_S}-second cancel window has ended; the payment is being released"
            elif new_state == "COMPLETED" and left > 0:
                problem = f"Still inside the cancel window ({left:.0f}s left); it can't be released yet"
        if problem:  # every refused change is written to the decision log, then refused
            log(con, txn_id, "GUARD", "change rejected", f"{actor} tried {current} -> {new_state}: {problem}")
            return {"ok": False, "state": current, "error": problem}

        if actor in ("student", "staff", "guardian"):
            log(con, txn_id, "HUMAN", f"{actor} decision", reason)
        con.execute("UPDATE transactions SET state = ? WHERE txn_id = ?", (new_state, txn_id))
        if new_state == "CONFIRMED_CANCELLABLE":
            con.execute("UPDATE transactions SET confirmed_at = ? WHERE txn_id = ?",
                        (now().isoformat(timespec="seconds"), txn_id))
        if needs_guardian is not None:
            con.execute("UPDATE transactions SET needs_guardian = ? WHERE txn_id = ?", (int(needs_guardian), txn_id))
        log(con, txn_id, "TOOL", "update_payment_state", f"{current} -> {new_state} by {actor}: {reason}")
        if assessment is not None:
            con.execute("INSERT OR REPLACE INTO assessments VALUES (?,?,?,?,?,?,?,?)", assessment)
        if resolution is not None:   # close the old question BEFORE opening a new one (staff -> guardian)
            con.execute("UPDATE pending_confirmations SET resolved = 1, resolution = ? WHERE txn_id = ?",
                        (resolution, txn_id))
        if pending is not None:
            con.execute("INSERT OR REPLACE INTO pending_confirmations (txn_id, awaiting, question) "
                        "VALUES (?,?,?)", (txn_id, *pending))
            log(con, txn_id, "GUARD", "paused",
                f"Waiting for {pending[0]}; execution stopped here (no polling). "
                "Money is held, wallet is NOT frozen.")
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
            "SELECT id, step, actor, event, detail, ts FROM decision_log WHERE txn_id = ? ORDER BY step", (txn_id,))]


def log_id(n: int) -> str:
    """How a decision-log row is shown and quoted: LOG-000123 (the row's permanent id in SQLite)."""
    return f"LOG-{int(n):06d}"
