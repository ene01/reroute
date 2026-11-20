"""Shared pieces for the Reroute Streamlit pages: login / sign up, data helpers, log rendering, mobile CSS."""

import hmac
import html
import os
import time
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

import accounts
import services as svc
import sessions

STAFF_PASSWORD_FILE = Path(__file__).with_name("staff_password.txt")  # you: everything

STATE_BADGE = {"FLAGGED": "⚪ FLAGGED", "CLEARED": "🟢 CLEARED", "PENDING_CONFIRMATION": "🟡 PENDING CONFIRMATION",
               "ESCALATED": "🔴 ESCALATED TO STAFF", "COMPLETED": "✅ COMPLETED", "CANCELLED": "⛔ CANCELLED"}
ACTOR_ICON = {"AGENT": "🧠 AGENT", "TOOL": "🔧 TOOL", "GUARD": "🛡️ GUARD", "HUMAN": "🙋 HUMAN"}
ACTOR_NOTE = {"AGENT": "LLM", "TOOL": "code", "GUARD": "code", "HUMAN": "person"}

# Works on any screen; the max-width rules only kick in on phones.
MOBILE_CSS = """
<style>
@media (max-width: 640px) {
  .block-container { padding: 1rem 0.8rem 3rem 0.8rem !important; }
  h1 { font-size: 1.6rem !important; }
  h2, h3 { font-size: 1.2rem !important; }
  div[data-testid="stTabs"] button p { font-size: 0.85rem; }
  div[data-testid="stButton"] button { width: 100%; min-height: 2.8rem; }
}
.rr-log { border-left: 3px solid rgba(128,128,128,.35); padding: .35rem .7rem; margin: .3rem 0;
          border-radius: 0 6px 6px 0; background: rgba(128,128,128,.07); font-size: .88rem; line-height: 1.35; }
.rr-log .who { font-weight: 600; font-size: .78rem; opacity: .85; }
.rr-log.AGENT { border-left-color: #a371f7; } .rr-log.TOOL { border-left-color: #3b82f6; }
.rr-log.GUARD { border-left-color: #e3a008; } .rr-log.HUMAN { border-left-color: #22c55e; }
</style>
"""


PAGES: dict = {}  # filled by app.py each run: key -> st.Page, so pages can link and redirect to each other


def go(key: str) -> None:
    st.switch_page(PAGES[key])


MAX_FAILED_LOGINS = 5
LOCKOUT_SECONDS = 30

LOGIN_CSS = """
<style>
.block-container { max-width: 480px !important; padding-top: 2.5rem !important; }
div[data-testid="stFormSubmitButton"] button { width: 100%; min-height: 2.8rem; }
</style>
"""


def _staff_password() -> str:
    return os.getenv("REROUTE_STAFF_PASSWORD") or (
        STAFF_PASSWORD_FILE.read_text(encoding="utf-8-sig").strip() if STAFF_PASSWORD_FILE.exists() else "")


def _locked_out() -> bool:
    """After too many wrong logins in this browser session, wait a bit before trying again."""
    until = st.session_state.get("lockout_until", 0)
    if time.time() < until:
        st.error(f"Too many wrong attempts. Try again in {int(until - time.time()) + 1} seconds.")
        return True
    return False


def _failed_login() -> None:
    fails = st.session_state.get("failed_logins", 0) + 1
    st.session_state.failed_logins = fails
    if fails >= MAX_FAILED_LOGINS:
        st.session_state.failed_logins = 0
        st.session_state.lockout_until = time.time() + LOCKOUT_SECONDS


# ---------------------------------------------------------------- remembered logins (cookie)
def _staff_fp() -> str | None:
    pw = _staff_password()
    return sessions.staff_fingerprint(pw) if pw else None


def _write_cookie(token: str, max_age: int) -> None:
    """Set (or clear, with max_age 0) the login cookie from the browser side."""
    components.html(
        "<script>(function(){var d=window.parent.document;"
        "var secure=window.parent.location.protocol==='https:'?'; Secure':'';"
        f"d.cookie='{sessions.COOKIE_NAME}={token}; Max-Age={max_age}; Path=/; SameSite=Lax'+secure;"
        "})();</script>", height=0)


def flush_cookie() -> None:
    """Write any pending cookie change. Called on every run (a cookie can't be set mid-rerun)."""
    op = st.session_state.pop("cookie_op", None)
    if op:
        _write_cookie(*op)


def remember_login(role: str, account: dict | None = None) -> None:
    """Start a remembered login for this browser (written to the cookie on the next run)."""
    token = sessions.create(role, account["account_id"] if account else None,
                            _staff_fp() if role == "staff" else None)
    st.session_state.session_token = token
    st.session_state.cookie_op = (token, sessions.SESSION_DAYS * 86400)


def forget_login() -> None:
    """End this browser's remembered login and clear its cookie."""
    sessions.revoke(st.session_state.pop("session_token", None))
    try:
        sessions.revoke(st.context.cookies.get(sessions.COOKIE_NAME))
    except Exception:
        pass
    st.session_state.cookie_op = ("", 0)


def _restore_from_cookie() -> bool:
    """After a refresh: sign back in from the cookie if it's still valid."""
    try:
        token = st.context.cookies.get(sessions.COOKIE_NAME)
    except Exception:
        return False
    if not token or st.session_state.get("logged_out"):
        return False
    found = sessions.lookup(token, _staff_fp())
    if not found:
        st.session_state.cookie_op = ("", 0)  # expired or revoked: clear the stale cookie
        return False
    st.session_state.role = found["role"]
    st.session_state.session_token = token
    if found["account"]:
        st.session_state.account = found["account"]
        st.session_state.student_id = found["account"]["student_id"]
    return True


def _sign_in_student(account: dict) -> None:
    st.session_state.role = "student"
    st.session_state.account = account
    st.session_state.student_id = account["student_id"]
    st.session_state.failed_logins = 0
    st.session_state.pop("logged_out", None)
    remember_login("student", account)
    st.rerun()


def require_login() -> None:
    """Show the log in / sign up screen until someone is signed in. Sets st.session_state.role:
         'student' - a student account (name + school + password): their own wallet only
         'staff'   - the staff password (staff_password.txt / REROUTE_STAFF_PASSWORD): every page
    """
    if st.session_state.get("role"):
        return
    if _restore_from_cookie():
        st.rerun()
    st.markdown(MOBILE_CSS + LOGIN_CSS, unsafe_allow_html=True)
    st.title("🛡️ Reroute")
    st.caption("Scam defense for the PocketPay campus wallet")
    tab_login, tab_signup, tab_staff = st.tabs(["Log in", "Sign up", "Staff"])

    with tab_login:
        with st.form("login_form"):
            name = st.text_input("Your name")
            school = st.text_input("Your school")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Log in", type="primary")
        if submitted and not _locked_out():
            account = accounts.authenticate(name, school, password)
            if account:
                _sign_in_student(account)
            _failed_login()
            st.error("That name, school and password don't match an account.")
        st.caption("New here? Open **Sign up**.")

    with tab_signup:
        with st.form("signup_form"):
            name = st.text_input("Your name", key="su_name")
            school = st.text_input("Your school", key="su_school")
            password = st.text_input(f"Password (at least {accounts.MIN_PASSWORD_LEN} characters)",
                                     type="password", key="su_pw")
            confirm = st.text_input("Type the password again", type="password", key="su_pw2")
            submitted = st.form_submit_button("Create account", type="primary")
        if submitted:
            account, problems = accounts.create_account(name, school, password, confirm)
            if account:
                st.toast(f"Welcome to PocketPay, {account['name']}!", icon="🎉")
                _sign_in_student(account)
            for p in problems:
                st.error(p)
        st.caption("No email needed. You'll get a demo wallet with a few sample payment requests to try Reroute on.")

    with tab_staff:
        with st.form("staff_form"):
            password = st.text_input("Staff password", type="password", key="staff_pw")
            submitted = st.form_submit_button("Open staff dashboard")
        if submitted and not _locked_out():
            expected = _staff_password()
            if expected and hmac.compare_digest(password.encode(), expected.encode()):
                st.session_state.role = "staff"
                st.session_state.failed_logins = 0
                st.session_state.pop("logged_out", None)
                remember_login("staff")
                st.rerun()
            _failed_login()
            st.error("Wrong staff password.")
    st.stop()


MOBILE_UA_HINTS = ("iphone", "ipad", "android", "mobile", "silk", "kindle", "opera mini", "windows phone")
LAYOUTS = ["Auto", "Phone", "Desktop"]


def device_is_mobile() -> bool:
    """Guess from the browser's User-Agent whether this visitor is on a phone or tablet."""
    try:
        ua = (st.context.headers.get("User-Agent") or "").lower()
    except Exception:
        return False
    return any(hint in ua for hint in MOBILE_UA_HINTS)


def is_mobile() -> bool:
    """Mobile view? The visitor's manual choice wins; 'Auto' uses the device guess."""
    choice = st.session_state.get("layout_choice", "Auto")
    if choice == "Phone":
        return True
    if choice == "Desktop":
        return False
    return device_is_mobile()


def keep_layout_choice() -> None:
    """Call once per run BEFORE any page draws the switch, so the choice survives page changes."""
    if "layout_choice" in st.session_state:
        st.session_state.layout_choice = st.session_state.layout_choice


def layout_switch(where=st) -> None:
    """A small Auto / Phone / Desktop switch, in case the device guess is wrong."""
    detected = "phone" if device_is_mobile() else "desktop"
    extra = {} if "layout_choice" in st.session_state else {"default": "Auto"}
    where.segmented_control("Layout", LAYOUTS, key="layout_choice",
                            help=f"Auto picks the layout from your device (detected: {detected}).", **extra)


def logout_button(where=st, key: str = "logout") -> None:
    if where.button("🚪 Log out", width="stretch", key=key):
        log_out()


def sidebar_account() -> None:
    """Who is logged in, and a Log out button - shown in the side panel on every page."""
    if st.session_state.get("role") == "staff":
        who = "Staff"
    else:
        acc = st.session_state.get("account") or {}
        who = acc.get("name") or "Student"
    with st.sidebar:
        st.caption(f"Logged in as **{who}**")
        logout_button(key="logout_sidebar")
        st.divider()


def log_out() -> None:
    forget_login()
    for k in ("role", "account", "student_id", "student_picker"):
        st.session_state.pop(k, None)
    st.session_state.logged_out = True  # don't sign straight back in from the old cookie in this tab
    st.rerun()


def pending(awaiting: str, student_id: str | None = None) -> list[dict]:
    sql = """SELECT p.*, t.amount_aed, t.recipient_handle, t.message_text, t.student_id,
                    a.student_explanation, a.guard_reasons
             FROM pending_confirmations p JOIN transactions t USING (txn_id)
             LEFT JOIN assessments a USING (txn_id)
             WHERE p.awaiting = ? AND p.resolved = 0"""
    args = [awaiting]
    if student_id:
        sql += " AND t.student_id = ?"
        args.append(student_id)
    with svc.db() as con:
        return [dict(r) for r in con.execute(sql + " ORDER BY p.txn_id", args)]


def show_log(txn_id: str, as_table: bool = False) -> None:
    """Decision log. Desktop: a table. Phone: stacked cards colour-coded by who did each step."""
    log = svc.get_log(txn_id)
    if not log:
        st.caption("No decision log yet. Run the agent.")
        return
    if as_table:
        st.dataframe([{"step": e["step"], "who": f"{ACTOR_ICON.get(e['actor'], e['actor'])} "
                                                 f"({ACTOR_NOTE.get(e['actor'], '')})",
                       "event": e["event"], "detail": e["detail"]} for e in log],
                     hide_index=True, width="stretch",
                     column_config={"step": st.column_config.NumberColumn(width="small"),
                                    "who": st.column_config.TextColumn(width="medium"),
                                    "event": st.column_config.TextColumn(width="medium"),
                                    "detail": st.column_config.TextColumn(width="large")})
        return
    rows = []
    for e in log:
        actor = e["actor"]
        rows.append(
            f'<div class="rr-log {html.escape(actor)}"><span class="who">{e["step"]}. '
            f'{ACTOR_ICON.get(actor, html.escape(actor))} ({ACTOR_NOTE.get(actor, "")})</span><br>'
            f'<b>{html.escape(e["event"])}</b>: {html.escape(e["detail"])}</div>')
    st.markdown("".join(rows), unsafe_allow_html=True)
