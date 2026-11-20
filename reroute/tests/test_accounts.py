"""Tests for student accounts: sign up, log in, password safety, and surviving a demo reset."""

import pytest

import accounts
import agent
import seed_data
import services


def signup(name="Laith", school="TechX Academy", pw="secret123", confirm=None):
    return accounts.create_account(name, school, pw, pw if confirm is None else confirm)


def test_signup_creates_account_wallet_and_sample_requests(db):
    account, problems = signup()
    assert problems == [] and account["student_id"].startswith("U")
    with services.db() as con:
        student = con.execute("SELECT * FROM students WHERE student_id = ?", (account["student_id"],)).fetchone()
        txns = con.execute("SELECT * FROM transactions WHERE student_id = ?", (account["student_id"],)).fetchall()
    assert student["name"] == "Laith" and student["school"] == "TechX Academy"
    assert student["wallet_balance_aed"] == accounts.STARTING_BALANCE_AED
    assert len(txns) == len(accounts.STARTER_PAYMENTS)
    assert all(t["state"] == "FLAGGED" for t in txns)


@pytest.mark.parametrize("kwargs, message", [
    ({"confirm": "different1"}, "don't match"),
    ({"pw": "abc", "confirm": "abc"}, "at least"),
    ({"name": "L"}, "Name"),
    ({"school": ""}, "School"),
])
def test_signup_rejects_bad_input(db, kwargs, message):
    account, problems = signup(**kwargs)
    assert account is None
    assert any(message in p for p in problems), problems


def test_same_name_at_same_school_is_rejected_ignoring_case_and_spaces(db):
    signup()
    account, problems = signup(name="  laith ", school="techx   academy")
    assert account is None and "already exists" in problems[0]


def test_same_name_at_a_different_school_is_allowed(db):
    signup()
    account, problems = signup(school="Another School")
    assert account is not None and problems == []


def test_login(db):
    signup()
    assert accounts.authenticate("Laith", "TechX Academy", "secret123")["name"] == "Laith"
    assert accounts.authenticate("laith", " techx academy ", "secret123") is not None  # case/spaces ignored
    assert accounts.authenticate("Laith", "TechX Academy", "wrong-password") is None
    assert accounts.authenticate("Laith", "Other School", "secret123") is None
    assert accounts.authenticate("Nobody", "TechX Academy", "secret123") is None


def test_password_is_never_stored_in_plain_text(db):
    signup(pw="my-very-secret-pw", confirm="my-very-secret-pw")
    with services.db() as con:
        row = con.execute("SELECT * FROM accounts").fetchone()
    assert row["password_hash"] != "my-very-secret-pw" and len(row["salt"]) == 32
    assert b"my-very-secret-pw" not in db.read_bytes()


def test_each_student_only_has_their_own_payments(db):
    a, _ = signup(name="Student A")
    b, _ = signup(name="Student B")
    with services.db() as con:
        owners = {r["student_id"] for r in con.execute(
            "SELECT student_id FROM transactions WHERE txn_id LIKE ?", (f"{a['student_id']}-%",))}
    assert owners == {a["student_id"]} and a["student_id"] != b["student_id"]


def test_demo_reset_keeps_accounts_and_rebuilds_wallets(db, fake_ai):
    account, _ = signup()
    agent.process_transaction(f"{account['student_id']}-1")
    seed_data.seed(db)
    assert accounts.authenticate("Laith", "TechX Academy", "secret123") is not None
    with services.db() as con:
        state = con.execute("SELECT state FROM transactions WHERE txn_id = ?",
                            (f"{account['student_id']}-1",)).fetchone()["state"]
    assert state == "FLAGGED"


# ---------------------------------------------------------------- staff account management

def test_search_requires_both_name_and_school(db):
    signup()
    with pytest.raises(ValueError):
        accounts.search("Laith", "")
    with pytest.raises(ValueError):
        accounts.search("", "TechX")


def test_search_matches_part_of_name_and_school(db):
    signup()
    signup(name="Laila", school="Dubai High")
    assert [a["name"] for a in accounts.search("lai", "techx")] == ["Laith"]
    assert {a["name"] for a in accounts.search("lai", "h")} == {"Laith", "Laila"}
    assert accounts.search("Laith", "Dubai") == []
    assert accounts.search("%", "%") == []  # wildcards are treated as plain text


def test_schools_lists_each_registered_school_once(db):
    assert accounts.schools() == []
    signup(name="Aa", school="TechX Academy")
    signup(name="Bb", school="techx  academy")  # same school, different spelling of case/spaces
    signup(name="Cc", school="Dubai High")
    assert accounts.schools() == ["Dubai High", "TechX Academy"]


def test_search_results_never_include_password_data(db):
    signup()
    result = accounts.search("Laith", "TechX")[0]
    assert "password_hash" not in result and "salt" not in result


def test_staff_password_reset(db):
    account, _ = signup()
    assert accounts.set_password(account["account_id"], "newpass1", "different")  # mismatch -> problems
    assert accounts.set_password(account["account_id"], "newpass1", "newpass1") == []
    assert accounts.authenticate("Laith", "TechX Academy", "newpass1") is not None
    assert accounts.authenticate("Laith", "TechX Academy", "secret123") is None


def test_student_change_password_needs_current_one(db):
    account, _ = signup()
    assert accounts.change_password(account["account_id"], "wrong", "newpass1", "newpass1")
    assert accounts.change_password(account["account_id"], "secret123", "newpass1", "newpass1") == []
    assert accounts.authenticate("Laith", "TechX Academy", "newpass1") is not None


def test_reset_wallet(db, fake_ai):
    account, _ = signup()
    agent.process_transaction(f"{account['student_id']}-1")
    accounts.reset_wallet(account["account_id"])
    summary = accounts.wallet_summary(account["student_id"])
    assert summary["new"] == len(accounts.STARTER_PAYMENTS) and summary["balance"] == accounts.STARTING_BALANCE_AED
    assert services.get_log(f"{account['student_id']}-1") == []


def test_delete_account_removes_everything(db, fake_ai):
    account, _ = signup()
    agent.process_transaction(f"{account['student_id']}-3")
    assert accounts.delete_account(account["account_id"])
    assert accounts.authenticate("Laith", "TechX Academy", "secret123") is None
    with services.db() as con:
        left = con.execute("SELECT COUNT(*) FROM transactions WHERE student_id = ?",
                           (account["student_id"],)).fetchone()[0]
        pending = con.execute("SELECT COUNT(*) FROM pending_confirmations WHERE txn_id LIKE ?",
                              (f"{account['student_id']}-%",)).fetchone()[0]
    assert (left, pending) == (0, 0)


# ---------------------------------------------------------------- beta test money

def test_add_funds_increases_balance_and_is_recorded(db):
    account, _ = signup()
    balance, problem = accounts.add_funds(account["student_id"], 100, "student")
    assert problem is None and balance == accounts.STARTING_BALANCE_AED + 100
    balance, _ = accounts.add_funds(account["student_id"], "50.5", "staff")
    assert balance == accounts.STARTING_BALANCE_AED + 150.5
    assert [t["added_by"] for t in accounts.topups_for(account["student_id"])] == ["staff", "student"]


@pytest.mark.parametrize("amount", [0, -10, accounts.MAX_TOPUP_AED + 1, "lots", None])
def test_add_funds_rejects_bad_amounts(db, amount):
    account, _ = signup()
    balance, problem = accounts.add_funds(account["student_id"], amount, "student")
    assert balance is None and problem
    assert accounts.wallet_summary(account["student_id"])["balance"] == accounts.STARTING_BALANCE_AED


def test_add_funds_works_for_demo_students_and_rejects_unknown_wallets(db):
    assert accounts.add_funds("S1001", 20, "staff")[1] is None
    assert accounts.add_funds("NOPE", 20, "staff")[0] is None


def test_add_funds_can_be_turned_off(db, monkeypatch):
    account, _ = signup()
    monkeypatch.setattr(accounts, "BETA_TOPUPS", False)
    assert accounts.add_funds(account["student_id"], 100, "staff")[0] is None


def test_wallet_reset_and_demo_reset_remove_test_money(db):
    account, _ = signup()
    accounts.add_funds(account["student_id"], 500, "student")
    accounts.reset_wallet(account["account_id"])
    assert accounts.wallet_summary(account["student_id"])["balance"] == accounts.STARTING_BALANCE_AED
    assert accounts.topups_for(account["student_id"]) == []
    accounts.add_funds(account["student_id"], 500, "student")
    seed_data.seed(db)
    assert accounts.wallet_summary(account["student_id"])["balance"] == accounts.STARTING_BALANCE_AED


def test_sample_requests_route_as_expected(db, fake_ai):
    account, _ = signup()
    for suffix, *_rest, expected, _minutes in accounts.STARTER_PAYMENTS:
        s = agent.process_transaction(f"{account['student_id']}-{suffix}")
        assert s["route"] == expected, s["log"]
