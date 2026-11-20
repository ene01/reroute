"""Remembered logins, so refreshing the page doesn't log you out.

On login the browser gets a cookie holding a long random token. The database stores only a SHA-256
hash of that token (so a copied database can't be used to log in), who it belongs to, and when it
expires. On a fresh page load the app reads the cookie, looks up the hash, and signs you back in.

A remembered login ends when:
  - you log out (the row is deleted and the cookie cleared),
  - it is older than SESSION_DAYS,
  - (students) your password is changed or reset, or your account is deleted,
  - (staff) the staff password changes: each staff login stores a fingerprint of the password it
    used, and stops working once that no longer matches.
"""

import hashlib
import secrets
from datetime import datetime, timedelta

import accounts
import services as svc

SESSION_DAYS = 7
COOKIE_NAME = "reroute_session"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def staff_fingerprint(staff_password: str) -> str:
    """Identifies which staff password a staff login used, without storing the password."""
    return hashlib.sha256(("reroute-staff:" + staff_password).encode()).hexdigest()[:24]


def _now() -> datetime:
    return datetime.now()


def create(role: str, account_id: int | None = None, fingerprint: str | None = None) -> str:
    """Start a remembered login and return the token for the cookie (shown to the browser only)."""
    accounts.ensure_table()
    token = secrets.token_urlsafe(32)
    expires = (_now() + timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds")
    with svc.db() as con:
        con.execute("DELETE FROM sessions WHERE expires_at < ?", (_now().isoformat(timespec="seconds"),))
        con.execute("INSERT INTO sessions (token_hash, role, account_id, fingerprint, expires_at) "
                    "VALUES (?,?,?,?,?)", (_hash(token), role, account_id, fingerprint, expires))
    return token


def lookup(token: str | None, current_staff_fingerprint: str | None) -> dict | None:
    """Return {'role': ..., 'account': {...} or None} for a valid token, else None."""
    if not token:
        return None
    accounts.ensure_table()
    with svc.db() as con:
        row = con.execute("SELECT * FROM sessions WHERE token_hash = ?", (_hash(token),)).fetchone()
    if row is None or row["expires_at"] < _now().isoformat(timespec="seconds"):
        return None
    if row["role"] == "staff":
        if not current_staff_fingerprint or row["fingerprint"] != current_staff_fingerprint:
            revoke(token)  # staff password changed since this login
            return None
        return {"role": "staff", "account": None}
    if row["role"] == "student":
        acc = accounts.get(row["account_id"])
        if acc is None:
            revoke(token)
            return None
        return {"role": "student", "account": acc}
    return None


def revoke(token: str | None) -> None:
    if token:
        with svc.db() as con:
            con.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash(token),))
