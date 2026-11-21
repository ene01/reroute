"""Scenario Lab data helpers: let staff (and judges) type in their own flagged payment and run it through
the normal Reroute pipeline. Nothing here decides anything: every lab payment goes through the same
pre-flight checks, AI, routing guard and write gateway as any other payment.

Lab payments get ids L001, L002, ... and can be cleared without touching the demo scenarios.
"""

from datetime import timedelta

import guards
import services as svc

MAX_HANDLE_LEN, MAX_NOTE_LEN, MAX_MESSAGE_LEN = 40, 60, 500
CHANNELS = {"p2p": "Transfer to another student", "qr_pay": "Shop / canteen payment (QR)"}


def students() -> list[dict]:
    with svc.db() as con:
        return [dict(r) for r in con.execute("SELECT student_id, name, school, age FROM students "
                                             "WHERE student_id NOT LIKE 'LS%' ORDER BY name")]   # not load-test wallets


def _next_id(con) -> str:
    n = con.execute("SELECT COALESCE(MAX(CAST(SUBSTR(txn_id, 2) AS INTEGER)), 0) FROM transactions "
                    "WHERE txn_id GLOB 'L[0-9]*'").fetchone()[0]
    return f"L{n + 1:03d}"


def create_payment(student_id: str, handle: str, amount, channel: str = "p2p", note: str = "",
                   message: str = "", minutes_ago: int = 0) -> tuple[str | None, str | None]:
    """Insert a new FLAGGED payment. Returns (txn_id, None) or (None, problem).

    Only lengths are checked here. Odd values (a broken handle, a negative amount) are allowed on purpose,
    so the lab can show Reroute's own pre-flight checks catching them.
    """
    handle = str(handle or "").strip()
    if not handle or len(handle) > MAX_HANDLE_LEN:
        return None, f"Enter a recipient handle (up to {MAX_HANDLE_LEN} characters)."
    if len(note or "") > MAX_NOTE_LEN or len(message or "") > MAX_MESSAGE_LEN:
        return None, f"Keep the note under {MAX_NOTE_LEN} and the message under {MAX_MESSAGE_LEN} characters."
    if channel not in CHANNELS:
        return None, "Unknown payment type."
    try:
        amount = round(float(amount), 2)
        minutes_ago = int(minutes_ago)
    except (TypeError, ValueError):
        return None, "Amount and minutes must be numbers."
    if not 0 <= minutes_ago <= 24 * 60:
        return None, "Minutes ago must be between 0 and 1440."
    created = (guards.DEMO_NOW - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%d %H:%M")
    with svc.db() as con:
        if con.execute("SELECT 1 FROM students WHERE student_id = ?", (student_id,)).fetchone() is None:
            return None, "Pick a student."
        txn_id = _next_id(con)
        prior = con.execute("SELECT COUNT(*) FROM transactions WHERE student_id = ? AND recipient_handle = ? "
                            "AND state IN ('CLEARED', 'COMPLETED')", (student_id, handle)).fetchone()[0]
        con.execute(
            "INSERT INTO transactions (txn_id, student_id, recipient_handle, amount_aed, channel, note, created_at, "
            "prior_txns_with_recipient, message_text, expected_route, scenario, state) "
            "VALUES (?,?,?,?,?,?,?,?,?,NULL,?,'FLAGGED')",
            (txn_id, student_id, handle, amount, channel, (note or "").strip(), created, prior,
             (message or "").strip(), f"Scenario Lab: AED {amount:g} to {handle}"))
    return txn_id, None


def payments() -> list[dict]:
    """Every lab payment, newest first, with the student's name and the routing reason."""
    with svc.db() as con:
        return [dict(r) for r in con.execute(
            """SELECT t.*, s.name AS student_name, s.age AS student_age, a.route, a.guard_reasons,
                      a.student_explanation
               FROM transactions t JOIN students s USING (student_id) LEFT JOIN assessments a USING (txn_id)
               WHERE t.txn_id GLOB 'L[0-9]*' ORDER BY t.rowid DESC""")]


def clear() -> int:
    """Delete every lab payment (demo scenarios and student wallets are untouched)."""
    with svc.db() as con:
        for table in ("decision_log", "assessments", "pending_confirmations"):
            con.execute(f"DELETE FROM {table} WHERE txn_id GLOB 'L[0-9]*'")
        return con.execute("DELETE FROM transactions WHERE txn_id GLOB 'L[0-9]*'").rowcount
