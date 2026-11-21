"""Tests for remembered logins (staying logged in after a page refresh)."""

from datetime import datetime, timedelta

import accounts
import seed_data
import services
import sessions

FP = sessions.staff_fingerprint("staff-pass")


def make_student():
    account, _ = accounts.create_account("Laith", "TechX Academy", "secret123", "secret123")
    return account


def test_student_login_is_remembered(db):
    acc = make_student()
    token = sessions.create("student", acc["account_id"])
    found = sessions.lookup(token, FP)
    assert found["role"] == "student" and found["account"]["student_id"] == acc["student_id"]


def test_token_is_stored_only_as_a_hash(db):
    acc = make_student()
    token = sessions.create("student", acc["account_id"])
    assert token.encode() not in db.read_bytes()


def test_unknown_or_missing_token_is_rejected(db):
    assert sessions.lookup("not-a-real-token", FP) is None
    assert sessions.lookup(None, FP) is None
    assert sessions.lookup("", FP) is None


def test_logout_revokes(db):
    acc = make_student()
    token = sessions.create("student", acc["account_id"])
    sessions.revoke(token)
    assert sessions.lookup(token, FP) is None


def test_expired_login_is_rejected(db, monkeypatch):
    acc = make_student()
    token = sessions.create("student", acc["account_id"])
    monkeypatch.setattr(sessions, "_now", lambda: datetime.now() + timedelta(days=sessions.SESSION_DAYS + 1))
    assert sessions.lookup(token, FP) is None


def test_password_change_and_reset_log_out_saved_logins(db):
    acc = make_student()
    t1 = sessions.create("student", acc["account_id"])
    accounts.change_password(acc["account_id"], "secret123", "newpass1", "newpass1")
    assert sessions.lookup(t1, FP) is None
    t2 = sessions.create("student", acc["account_id"])
    accounts.set_password(acc["account_id"], "another1", "another1")  # staff reset
    assert sessions.lookup(t2, FP) is None


def test_deleted_account_cannot_log_back_in(db):
    acc = make_student()
    token = sessions.create("student", acc["account_id"])
    accounts.delete_account(acc["account_id"])
    assert sessions.lookup(token, FP) is None


def test_staff_login_ends_when_staff_password_changes(db):
    token = sessions.create("staff", None, FP)
    assert sessions.lookup(token, FP)["role"] == "staff"
    assert sessions.lookup(token, sessions.staff_fingerprint("a-new-password")) is None
    assert sessions.lookup(token, FP) is None  # and it was removed, not just refused


def test_demo_reset_keeps_remembered_logins(db):
    acc = make_student()
    token = sessions.create("student", acc["account_id"])
    seed_data.seed(db)
    assert sessions.lookup(token, FP) is not None
