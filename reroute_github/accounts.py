"""Student accounts: sign up and log in with name + school + password. No email, nothing else.

Passwords are never stored. Each one is turned into a salted PBKDF2-SHA256 hash, and logging in
re-computes the hash and compares it in constant time.

Each account gets its own wallet (a row in `students`) with a starting balance and a few sample
payment requests, so a new student can try Reroute straight away. Accounts live in their own table,
which "Reset demo data" keeps; the reset rebuilds each account's wallet and sample requests.
"""

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
from datetime import datetime, timedelta

import guards
import services as svc

# Beta test money: lets testers and staff add pretend AED to a wallet. Turn off for real use with
# $env:REROUTE_BETA_TOPUPS = "0" (or set BETA_TOPUPS = False).
BETA_TOPUPS = os.getenv("REROUTE_BETA_TOPUPS", "1") == "1"
MAX_TOPUP_AED = 1000

PBKDF2_ROUNDS = 200_000
MIN_PASSWORD_LEN = 6
STARTING_BALANCE_AED = 250.0
STARTING_AVG_SPEND_AED = 25.0

ACCOUNTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    account_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL, school TEXT NOT NULL,
    name_key TEXT NOT NULL, school_key TEXT NOT NULL,   -- lower-case, single-spaced, for matching
    password_hash TEXT NOT NULL, salt TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    age INTEGER,                                         -- HS-1.5 guardian rule (NULL = not given)
    UNIQUE (name_key, school_key)
);
CREATE TABLE IF NOT EXISTS sessions (                   -- remembered logins (see sessions.py)
    token_hash TEXT PRIMARY KEY, role TEXT NOT NULL, account_id INTEGER, fingerprint TEXT,
    created_at TEXT DEFAULT (datetime('now', 'localtime')), expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS topups (                     -- beta test money, kept for the record
    id INTEGER PRIMARY KEY AUTOINCREMENT, student_id TEXT NOT NULL, amount_aed REAL NOT NULL,
    added_by TEXT NOT NULL,                              -- 'student' | 'staff'
    ts TEXT DEFAULT (datetime('now', 'localtime'))
);
"""

# Sample payment requests every new account starts with: one safe, one to confirm, one scam.
STARTER_PAYMENTS = [
    # suffix, recipient, amount, channel, note, prior payments, message, expected, minutes before demo clock
    ("1", "@campus_canteen", 12, "qr_pay", "1x falafel wrap", 5, "", "CLEAR", 50),
    ("2", "@omar_r2", 200, "p2p", "used AirPods", 0,
     "selling my airpods for 200, barely used, can meet at lunch tmrw?", "HOLD_FOR_CONFIRMATION", 35),
    ("3", "@campus_tix_99", 60, "p2p_link", "Front Row Fest pass", 0,
     "LAST TICKET!! Front Row Fest pass 60 AED, send to @campus_tix_99 fast before someone else takes it, "
     "claim here: http://frontrow-fest-claim.xyz/vip", "ESCALATE", 20),
]


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _key(text: str) -> str:
    return _clean(text).lower()


def _hash(password: str, salt_hex: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), PBKDF2_ROUNDS).hex()


def student_id_for(account_id: int) -> str:
    return f"U{account_id:04d}"


MIN_AGE, MAX_AGE = 10, 25


def _migrate(con: sqlite3.Connection) -> None:
    """Add columns that older databases don't have yet (students.school, ages, Round 2 columns)."""
    cols = [r[1] for r in con.execute("PRAGMA table_info(students)")]
    if cols and "school" not in cols:
        con.execute("ALTER TABLE students ADD COLUMN school TEXT DEFAULT 'Demo High School'")
    if "age" not in [r[1] for r in con.execute("PRAGMA table_info(accounts)")]:
        con.execute("ALTER TABLE accounts ADD COLUMN age INTEGER")   # HS-1.5
    svc.ensure_columns(con)


def ensure_table() -> None:
    """Create the accounts table, and add any missing columns to databases made before they existed."""
    with svc.db() as con:
        con.executescript(ACCOUNTS_SCHEMA)
        _migrate(con)


def validate_signup(name: str, school: str, password: str, confirm: str, age=None) -> list[str]:
    problems = []
    name, school = _clean(name), _clean(school)
    if not 2 <= len(name) <= 40:
        problems.append("Name must be 2 to 40 characters.")
    if not 2 <= len(school) <= 60:
        problems.append("School must be 2 to 60 characters.")
    if len(password) < MIN_PASSWORD_LEN:
        problems.append(f"Password must be at least {MIN_PASSWORD_LEN} characters.")
    if password != confirm:
        problems.append("The two passwords don't match.")
    if age is not None and not (isinstance(age, int) and MIN_AGE <= age <= MAX_AGE):
        problems.append(f"Age must be a whole number from {MIN_AGE} to {MAX_AGE}.")
    return problems


def create_account(name: str, school: str, password: str, confirm: str,
                   age: int | None = None) -> tuple[dict | None, list[str]]:
    """Returns (account, []) on success or (None, [problems]). age: used for the guardian rule (HS-1.5)."""
    problems = validate_signup(name, school, password, confirm, age)
    if problems:
        return None, problems
    name, school = _clean(name), _clean(school)
    salt = secrets.token_hex(16)
    ensure_table()
    try:
        with svc.db() as con:  # account + wallet + sample payments: all saved together, or none
            cur = con.execute(
                "INSERT INTO accounts (name, school, name_key, school_key, password_hash, salt, age) "
                "VALUES (?,?,?,?,?,?,?)",
                (name, school, _key(name), _key(school), _hash(password, salt), salt, age))
            account_id = cur.lastrowid
            build_wallet(con, account_id, name, school)
    except sqlite3.IntegrityError:
        return None, ["An account with this name already exists at this school. Try logging in."]
    return {"account_id": account_id, "name": name, "school": school,
            "student_id": student_id_for(account_id)}, []


def authenticate(name: str, school: str, password: str) -> dict | None:
    ensure_table()
    with svc.db() as con:
        row = con.execute("SELECT * FROM accounts WHERE name_key = ? AND school_key = ?",
                          (_key(name), _key(school))).fetchone()
    # Hash even when the account doesn't exist, so a wrong name takes as long as a wrong password.
    salt = row["salt"] if row else "00" * 16
    ok = hmac.compare_digest(_hash(password, salt), row["password_hash"] if row else "x")
    if not (row and ok):
        return None
    return {"account_id": row["account_id"], "name": row["name"], "school": row["school"],
            "student_id": student_id_for(row["account_id"]), "created_at": row["created_at"]}


def build_wallet(con: sqlite3.Connection, account_id: int, name: str, school: str) -> None:
    """Create (or recreate) an account's wallet, its pay-me handle and its sample payment requests."""
    sid = student_id_for(account_id)
    row = con.execute("SELECT age FROM accounts WHERE account_id = ?", (account_id,)).fetchone()
    age = row[0] if row else None
    con.execute("INSERT OR REPLACE INTO students (student_id, name, grade, avg_daily_spend_aed, "
                "wallet_balance_aed, school, age) VALUES (?,?,?,?,?,?,?)",
                (sid, name, None, STARTING_AVG_SPEND_AED, STARTING_BALANCE_AED, school, age))
    _sync_peer(con, account_id, name, school)
    for suffix, handle, amount, channel, note, prior, msg, expected, minutes_ago in STARTER_PAYMENTS:
        created = (guards.DEMO_NOW - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%d %H:%M")
        con.execute(
            "INSERT OR REPLACE INTO transactions (txn_id, student_id, recipient_handle, amount_aed, channel, note, "
            "created_at, prior_txns_with_recipient, message_text, expected_route, scenario, state) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?, 'FLAGGED')",
            (f"{sid}-{suffix}", sid, handle, amount, channel, note, created, prior, msg, expected,
             f"{name}'s sample request: {note}"))


def restore_all(con: sqlite3.Connection) -> int:
    """After a demo reset: rebuild every account's wallet. Returns how many accounts there are."""
    con.executescript(ACCOUNTS_SCHEMA)
    _migrate(con)
    rows = con.execute("SELECT account_id, name, school FROM accounts").fetchall()
    for account_id, name, school in rows:
        build_wallet(con, account_id, name, school)
    return len(rows)


def count() -> int:
    ensure_table()
    with svc.db() as con:
        return con.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]


# ------------------------------------------------------------------------------------------------
# Staff account management
# ------------------------------------------------------------------------------------------------

def _public(row) -> dict:
    """Account details that are safe to show: never the hash or salt."""
    return {"account_id": row["account_id"], "name": row["name"], "school": row["school"],
            "created_at": row["created_at"], "student_id": student_id_for(row["account_id"]),
            "age": row["age"] if "age" in row.keys() else None}


def set_age(student_id: str, age: int) -> str | None:
    """Staff (or the Scenario Lab) set a student's age in the dataset. Returns a problem or None."""
    if not (isinstance(age, int) and MIN_AGE <= age <= MAX_AGE):
        return f"Age must be a whole number from {MIN_AGE} to {MAX_AGE}."
    ensure_table()
    with svc.db() as con:
        if con.execute("UPDATE students SET age = ? WHERE student_id = ?", (age, student_id)).rowcount == 0:
            return "That wallet doesn't exist."
        account_id = account_id_from_student(student_id)
        if account_id:
            con.execute("UPDATE accounts SET age = ? WHERE account_id = ?", (age, account_id))
    return None


def account_id_from_student(student_id: str) -> int | None:
    return int(student_id[1:]) if re.fullmatch(r"U\d{4,}", student_id or "") else None


def get(account_id: int) -> dict | None:
    ensure_table()
    with svc.db() as con:
        row = con.execute("SELECT * FROM accounts WHERE account_id = ?", (account_id,)).fetchone()
    return _public(row) if row else None


def schools() -> list[str]:
    """Every school that has at least one registered account, A to Z (one entry per school)."""
    ensure_table()
    with svc.db() as con:
        rows = con.execute("SELECT school_key, MIN(school) AS school FROM accounts "
                           "GROUP BY school_key ORDER BY school_key").fetchall()
    return [r["school"] for r in rows]


def search(name: str, school: str) -> list[dict]:
    """Staff search. BOTH name and school are required; each matches any part (case-insensitive)."""
    name_k, school_k = _key(name), _key(school)
    if not name_k or not school_k:
        raise ValueError("Both the name and the school are required.")
    ensure_table()
    like = lambda s: "%" + s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"  # noqa: E731
    with svc.db() as con:
        rows = con.execute(
            "SELECT * FROM accounts WHERE name_key LIKE ? ESCAPE '\\' AND school_key LIKE ? ESCAPE '\\' "
            "ORDER BY name_key, school_key LIMIT 50", (like(name_k), like(school_k))).fetchall()
    return [_public(r) for r in rows]


def wallet_summary(student_id: str) -> dict:
    with svc.db() as con:
        stu = con.execute("SELECT * FROM students WHERE student_id = ?", (student_id,)).fetchone()
        rows = con.execute("SELECT state, amount_aed FROM transactions WHERE student_id = ?",
                           (student_id,)).fetchall()
    sent = sum(r["amount_aed"] for r in rows if r["state"] in ("CLEARED", "COMPLETED") and r["amount_aed"] > 0)
    states = [r["state"] for r in rows]
    balance = (stu["wallet_balance_aed"] - sent + received_total(student_id)) if stu else 0.0
    return {"balance": balance, "payments": len(rows),
            "needs_ok": states.count("PENDING_CONFIRMATION"), "with_staff": states.count("ESCALATED"),
            "new": states.count("FLAGGED")}


def set_password(account_id: int, new: str, confirm: str) -> list[str]:
    """Staff password reset (new password typed twice). Returns problems, or [] on success."""
    problems = []
    if len(new) < MIN_PASSWORD_LEN:
        problems.append(f"Password must be at least {MIN_PASSWORD_LEN} characters.")
    if new != confirm:
        problems.append("The two passwords don't match.")
    if problems:
        return problems
    salt = secrets.token_hex(16)
    with svc.db() as con:
        con.execute("UPDATE accounts SET password_hash = ?, salt = ? WHERE account_id = ?",
                    (_hash(new, salt), salt, account_id))
        con.execute("DELETE FROM sessions WHERE account_id = ?", (account_id,))  # log out saved logins
    return []


def change_password(account_id: int, current: str, new: str, confirm: str) -> list[str]:
    """A student changing their own password: needs the current one first."""
    acc = get(account_id)
    if not acc or not authenticate(acc["name"], acc["school"], current):
        return ["Your current password is wrong."]
    return set_password(account_id, new, confirm)


def _clear_wallet(con: sqlite3.Connection, student_id: str) -> None:
    pattern = f"{student_id}-%"
    for table in ("decision_log", "assessments", "pending_confirmations"):
        con.execute(f"DELETE FROM {table} WHERE txn_id LIKE ?", (pattern,))
    con.execute("DELETE FROM transactions WHERE student_id = ?", (student_id,))
    con.execute("DELETE FROM topups WHERE student_id = ?", (student_id,))


def reset_wallet(account_id: int) -> None:
    """Put the student's wallet back to the starting balance and fresh sample requests."""
    acc = get(account_id)
    if not acc:
        return
    with svc.db() as con:
        _clear_wallet(con, acc["student_id"])
        build_wallet(con, account_id, acc["name"], acc["school"])


# ------------------------------------------------------------------------------------------------
# Beta test money
# ------------------------------------------------------------------------------------------------

def add_funds(student_id: str, amount, added_by: str) -> tuple[float | None, str | None]:
    """Add beta test money to a wallet. Returns (new_balance, None) or (None, problem)."""
    if not BETA_TOPUPS:
        return None, "Adding test money is turned off."
    if added_by not in ("student", "staff"):
        return None, "Unknown requester."
    try:
        amount = round(float(amount), 2)
    except (TypeError, ValueError):
        return None, "Enter an amount in AED."
    if not 0 < amount <= MAX_TOPUP_AED:
        return None, f"Amount must be between AED 1 and AED {MAX_TOPUP_AED:,} per top-up."
    ensure_table()
    with svc.db() as con:  # balance change and the record are saved together, or not at all
        if con.execute("SELECT 1 FROM students WHERE student_id = ?", (student_id,)).fetchone() is None:
            return None, "That wallet doesn't exist."
        con.execute("UPDATE students SET wallet_balance_aed = wallet_balance_aed + ? WHERE student_id = ?",
                    (amount, student_id))
        con.execute("INSERT INTO topups (student_id, amount_aed, added_by) VALUES (?,?,?)",
                    (student_id, amount, added_by))
    return wallet_summary(student_id)["balance"], None


def topups_for(student_id: str) -> list[dict]:
    ensure_table()
    with svc.db() as con:
        return [dict(r) for r in con.execute(
            "SELECT * FROM topups WHERE student_id = ? ORDER BY id DESC", (student_id,))]


def delete_account(account_id: int) -> bool:
    """Remove the login, the wallet and all of its payments, in one transaction."""
    acc = get(account_id)
    if not acc:
        return False
    handle = handle_for(account_id, acc["name"])
    with svc.db() as con:
        _clear_wallet(con, acc["student_id"])
        # Requests this student sent that nobody has acted on yet are withdrawn with the account.
        con.execute("DELETE FROM transactions WHERE recipient_handle = ? AND state = 'FLAGGED'", (handle,))
        con.execute("DELETE FROM recipients WHERE handle = ?", (handle,))
        con.execute("DELETE FROM students WHERE student_id = ?", (acc["student_id"],))
        con.execute("DELETE FROM sessions WHERE account_id = ?", (account_id,))
        con.execute("DELETE FROM accounts WHERE account_id = ?", (account_id,))
    return True


# ------------------------------------------------------------------------------------------------
# Sending and requesting money between students
# ------------------------------------------------------------------------------------------------
# Every student account has a pay-me handle (e.g. @u7_laith_yusuf) listed in the recipients table as
# a verified campus peer. A request from A to B becomes a payment in B's wallet (B pays A) that waits
# in B's "New requests" until B runs it through Reroute. A payment from A to B is checked straight away.
# Nothing here decides whether money moves: that is always Reroute's normal check plus a human.

MAX_TRANSFER_AED = 1000
MAX_NOTE_LEN = 60
MAX_MESSAGE_LEN = 280
MAX_OPEN_REQUESTS = 10          # unanswered requests one student can have out at once (anti-spam)
REQUEST_CHANNEL, SEND_CHANNEL = "p2p_request", "p2p_student"   # own names, so demo "p2p" rows aren't mixed in
DONE_STATES = ("CLEARED", "COMPLETED")


def handle_for(account_id: int, name: str) -> str:
    """The account's pay-me handle. The 'u<id>_' prefix keeps it unique and never clashes with demo handles."""
    slug = re.sub(r"[^a-z0-9]+", "_", _key(name)).strip("_")[:20] or "student"
    return f"@u{account_id}_{slug}"


def _sync_peer(con: sqlite3.Connection, account_id: int, name: str, school: str) -> str:
    """Create or refresh the account's recipients row from live data (age, completed payments, scam alerts)."""
    handle = handle_for(account_id, name)
    row = con.execute("SELECT created_at FROM accounts WHERE account_id = ?", (account_id,)).fetchone()
    try:
        age_days = max(0, (datetime.now() - datetime.fromisoformat(row[0])).days) if row else 0  # row[0]: works with or without row_factory
    except (TypeError, ValueError):
        age_days = 0
    done = con.execute(f"SELECT COUNT(*) FROM transactions WHERE recipient_handle = ? AND state IN {DONE_STATES}",
                       (handle,)).fetchone()[0]
    has_alerts = con.execute("SELECT name FROM sqlite_master WHERE name = 'scam_alerts'").fetchone()
    reports = con.execute("SELECT COUNT(*) FROM scam_alerts WHERE handle = ?",
                          (handle,)).fetchone()[0] if has_alerts else 0
    con.execute("INSERT OR REPLACE INTO recipients (handle, display_name, kind, campus_verified, account_age_days, "
                "completed_txns, scam_reports, simulate_outage) VALUES (?,?,'peer',1,?,?,?,0)",
                (handle, f"{name} ({school})", age_days, done, reports))
    return handle


def refresh_peer(handle: str) -> None:
    """Bring a student's recipients row up to date (call just before Reroute checks a payment to them)."""
    m = re.fullmatch(r"@u(\d+)_[a-z0-9_]+", handle or "")
    acc = get(int(m.group(1))) if m else None
    if acc and handle_for(acc["account_id"], acc["name"]) == handle:
        with svc.db() as con:
            _sync_peer(con, acc["account_id"], acc["name"], acc["school"])


def handle_of_student(student_id: str) -> str | None:
    account_id = account_id_from_student(student_id)
    acc = get(account_id) if account_id else None
    return handle_for(account_id, acc["name"]) if acc else None


def received_total(student_id: str) -> float:
    """Money other students paid to this student (only payments that went through)."""
    handle = handle_of_student(student_id)
    if not handle:
        return 0.0
    with svc.db() as con:
        total = con.execute(f"SELECT SUM(amount_aed) FROM transactions WHERE recipient_handle = ? "
                            f"AND state IN {DONE_STATES} AND amount_aed > 0", (handle,)).fetchone()[0]
    return float(total or 0)


def find_students(name: str, school: str, exclude_student_id: str | None = None) -> list[dict]:
    """Student search for sending/requesting: name AND school required, never returns yourself."""
    return [dict(a, handle=handle_for(a["account_id"], a["name"])) for a in search(name, school)
            if a["student_id"] != exclude_student_id]


def _check_transfer(from_sid: str, to_sid: str, amount, note: str, message: str):
    """Validate a send/request. Returns (from_account, to_account, amount, note, message, problem)."""
    from_acc = get(account_id_from_student(from_sid) or 0)
    to_acc = get(account_id_from_student(to_sid) or 0)
    if not from_acc:
        return None, None, 0, "", "", "Only student accounts can send or request money."
    if not to_acc:
        return None, None, 0, "", "", "That student wasn't found. Search again."
    if from_sid == to_sid:
        return None, None, 0, "", "", "You can't send money to yourself."
    try:
        amount = round(float(amount), 2)
    except (TypeError, ValueError):
        return None, None, 0, "", "", "Enter an amount in AED."
    if not 1 <= amount <= MAX_TRANSFER_AED:
        return None, None, 0, "", "", f"Amount must be between AED 1 and AED {MAX_TRANSFER_AED:,}."
    note, message = _clean(note), str(message or "").strip()
    if len(note) > MAX_NOTE_LEN:
        return None, None, 0, "", "", f"Keep the note under {MAX_NOTE_LEN} characters."
    if len(message) > MAX_MESSAGE_LEN:
        return None, None, 0, "", "", f"Keep the message under {MAX_MESSAGE_LEN} characters."
    return from_acc, to_acc, amount, note, message, None


def _insert_transfer(con, payer_sid: str, to_handle: str, amount: float, channel: str, note: str,
                     message: str, scenario: str) -> str:
    while True:  # short random id, checked for clashes
        txn_id = f"{payer_sid}-{'R' if channel == REQUEST_CHANNEL else 'S'}{secrets.token_hex(3).upper()}"
        if not con.execute("SELECT 1 FROM transactions WHERE txn_id = ?", (txn_id,)).fetchone():
            break
    prior = con.execute(f"SELECT COUNT(*) FROM transactions WHERE student_id = ? AND recipient_handle = ? "
                        f"AND state IN {DONE_STATES}", (payer_sid, to_handle)).fetchone()[0]
    # Stamped with the demo clock so Reroute's freshness checks treat it as just sent.
    con.execute(
        "INSERT INTO transactions (txn_id, student_id, recipient_handle, amount_aed, channel, note, created_at, "
        "prior_txns_with_recipient, message_text, expected_route, scenario, state) "
        "VALUES (?,?,?,?,?,?,?,?,?,NULL,?,'FLAGGED')",
        (txn_id, payer_sid, to_handle, amount, channel, note, guards.DEMO_NOW.strftime("%Y-%m-%d %H:%M"),
         prior, message, scenario))
    return txn_id


def request_money(from_sid: str, to_sid: str, amount, note: str = "", message: str = "") -> tuple[str | None, str | None]:
    """Ask another student to pay you. It lands in THEIR New requests. Returns (txn_id, None) or (None, problem)."""
    from_acc, to_acc, amount, note, message, problem = _check_transfer(from_sid, to_sid, amount, note, message)
    if problem:
        return None, problem
    with svc.db() as con:
        handle = _sync_peer(con, from_acc["account_id"], from_acc["name"], from_acc["school"])
        open_count = con.execute("SELECT COUNT(*) FROM transactions WHERE recipient_handle = ? AND channel = ? "
                                 "AND state = 'FLAGGED'", (handle, REQUEST_CHANNEL)).fetchone()[0]
        if open_count >= MAX_OPEN_REQUESTS:
            return None, f"You already have {MAX_OPEN_REQUESTS} requests waiting. Wait for some to be answered."
        txn_id = _insert_transfer(con, to_acc["student_id"], handle, amount, REQUEST_CHANNEL, note, message,
                                  f"Request from {from_acc['name']} ({from_acc['school']})")
    return txn_id, None


def send_money(from_sid: str, to_sid: str, amount, note: str = "", message: str = "") -> tuple[str | None, str | None]:
    """Start a payment to another student. The caller then runs it through Reroute straight away."""
    from_acc, to_acc, amount, note, message, problem = _check_transfer(from_sid, to_sid, amount, note, message)
    if problem:
        return None, problem
    if amount > wallet_summary(from_sid)["balance"]:
        return None, "You don't have enough money in your wallet for this payment."
    with svc.db() as con:
        handle = _sync_peer(con, to_acc["account_id"], to_acc["name"], to_acc["school"])
        txn_id = _insert_transfer(con, from_sid, handle, amount, SEND_CHANNEL, note, message,
                                  f"Payment to {to_acc['name']} ({to_acc['school']})")
    return txn_id, None


def transfers_to_me(student_id: str) -> list[dict]:
    """Requests I sent (any state) and payments other students sent me, newest first."""
    handle = handle_of_student(student_id)
    if not handle:
        return []
    with svc.db() as con:
        return [dict(r) for r in con.execute(
            """SELECT t.*, s.name AS payer_name, s.school AS payer_school FROM transactions t
               LEFT JOIN students s USING (student_id)
               WHERE t.recipient_handle = ? AND (t.channel = ? OR t.state IN ('CLEARED', 'COMPLETED'))
               ORDER BY t.rowid DESC""", (handle, REQUEST_CHANNEL))]
