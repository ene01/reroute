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
               "ESCALATED": "🔴 ESCALATED TO STAFF", "COMPLETED": "✅ COMPLETED", "CANCELLED": "⛔ CANCELLED",
               "CONFIRMED_CANCELLABLE": "🟠 CONFIRMED (CANCEL WINDOW OPEN)",
               "PENDING_GUARDIAN": "🟣 WAITING FOR GUARDIAN"}
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
.rr-log { border-left: 3px solid rgba(0,224,234,.35); padding: .45rem .8rem; margin: .35rem 0;
          border-radius: 0 10px 10px 0; background: rgba(3,9,18,.6); font-size: .88rem; line-height: 1.4; }
.rr-log .who { font-family: var(--rr-mono, monospace); font-weight: 600; font-size: .68rem; letter-spacing: .1em;
               text-transform: uppercase; color: var(--rr-muted, #879ca8); }
.rr-log.AGENT { border-left-color: #ee50e6; } .rr-log.TOOL { border-left-color: #11a8ff; }
.rr-log.GUARD { border-left-color: #ffbd34; } .rr-log.HUMAN { border-left-color: #51e57e; }
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
    view = st.session_state.get("public_view", "home")
    if view == "home":
        _public_home()
        st.stop()
    if view == "how":
        import learn
        st.markdown(MOBILE_CSS, unsafe_allow_html=True)
        if st.button("← Back", key="how_back"):
            _set_view("home")
        learn.how_content(is_mobile())
        _public_buttons("how_bottom")
        st.stop()
    st.markdown(MOBILE_CSS + LOGIN_CSS, unsafe_allow_html=True)
    if st.button("← Back", key="login_back"):
        _set_view("home")
    staff_view = view == "staff"
    st.markdown(
        f'<div class="rr-eyebrow">{"Staff" if staff_view else "Student"} · PocketPay</div>'
        f'<div class="rr-title">{"Staff log in" if staff_view else "Your campus wallet"}</div>'
        '<div class="rr-lead">Pretend campus money only: no real money moves on this site.</div>',
        unsafe_allow_html=True)
    if staff_view:
        _staff_login_form()
        st.stop()
    tab_login, tab_signup = st.tabs(["Log in", "Sign up"])

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
            age = st.number_input("Your age", min_value=accounts.MIN_AGE, max_value=accounts.MAX_AGE, value=None,
                                  step=1, key="su_age", placeholder="e.g. 15",
                                  help="Students under 16 need a parent or guardian to approve transfers to "
                                       "other students above AED 100.")
            password = st.text_input(f"Password (at least {accounts.MIN_PASSWORD_LEN} characters)",
                                     type="password", key="su_pw")
            confirm = st.text_input("Type the password again", type="password", key="su_pw2")
            submitted = st.form_submit_button("Create account", type="primary")
        if submitted and age is None:
            st.error("Enter your age.")
        elif submitted:
            account, problems = accounts.create_account(name, school, password, confirm, int(age))
            if account:
                st.toast(f"Welcome to PocketPay, {account['name']}!", icon="🎉")
                _sign_in_student(account)
            for p in problems:
                st.error(p)
        st.caption("No email needed. You'll get a demo wallet with a few sample payment requests to try Reroute on.")
    st.stop()


def _set_view(view: str) -> None:
    st.session_state.public_view = view
    st.rerun()


def _public_buttons(key: str) -> None:
    """The three entry buttons: Student, Staff, How it works."""
    cols = st.columns(1 if is_mobile() else 3)
    pick = [cols[0], cols[0], cols[0]] if is_mobile() else cols
    if pick[0].button("Student", type="primary", width="stretch", key=f"{key}_student", icon=":material/person:"):
        _set_view("student")
    if pick[1].button("Staff", width="stretch", key=f"{key}_staff", icon=":material/badge:"):
        _set_view("staff")
    if pick[2].button("How it works", width="stretch", key=f"{key}_how", icon=":material/route:"):
        _set_view("how")


def _public_home() -> None:
    """The short page everyone sees before logging in."""
    st.markdown(MOBILE_CSS + "<style>.block-container{max-width:900px !important;}</style>", unsafe_allow_html=True)
    st.markdown(
        '<div class="rr-hero"><div class="rr-eyebrow">PocketPay · Campus wallet defense</div>'
        '<h1>Stop scams.<br><span class="rr-grad">Never freeze a wallet.</span></h1>'
        '<p>ReRoute checks every flagged campus payment. Safe ones go through, unusual ones wait for your OK, '
        'and scams go to the PocketPay team. Your wallet is never locked.</p></div>', unsafe_allow_html=True)
    _public_buttons("home")
    pretend_money_banner()


def pretend_money_banner() -> None:
    st.markdown('<div style="margin:.8rem 0;padding:.45rem .8rem;border:1px dashed rgba(255,189,52,.5);'
                'border-radius:10px;color:#ffbd34;font-size:.85rem;text-align:center">'
                'Pretend campus money only. No real money moves on this site.</div>', unsafe_allow_html=True)


def _staff_login_form() -> None:
    with st.form("staff_form"):
        password = st.text_input("Staff password", type="password", key="staff_pw")
        submitted = st.form_submit_button("Open staff dashboard", type="primary")
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
    st.session_state.public_view = "home"
    st.rerun()


def pending(awaiting: str, student_id: str | None = None) -> list[dict]:
    sql = """SELECT p.*, t.amount_aed, t.recipient_handle, t.message_text, t.student_id,
                    a.student_explanation, a.guard_reasons
             FROM pending_confirmations p JOIN transactions t USING (txn_id)
             LEFT JOIN assessments a USING (txn_id)
             WHERE p.awaiting = ? AND p.resolved = 0 AND p.txn_id NOT LIKE 'B-%'"""   # not load-test copies
    args = [awaiting]
    if student_id:
        sql += " AND t.student_id = ?"
        args.append(student_id)
    with svc.db() as con:
        return [dict(r) for r in con.execute(sql + " ORDER BY p.txn_id", args)]


def recipient_check(handle: str) -> dict:
    """The existing verify_campus_recipient result for a handle, remembered in this browser session."""
    cache = st.session_state.setdefault("trust_cache", {})
    if handle not in cache:
        cache[handle] = svc.verify_campus_recipient(handle)
    return cache[handle]


def trust_chip(handle: str) -> None:
    """Campus trust chip for a recipient (existing verify_campus_recipient, cached in the session)."""
    handle = (handle or "").strip()
    if not handle:
        return
    r = recipient_check(handle)
    if r.get("status") == "unavailable":
        color, text = "#ffbd34", "Campus registry not answering: Reroute will ask you to confirm"
    elif r.get("status") != "ok":
        color, text = "#ffbd34", "Not on the campus registry: unknown account, be careful"
    else:
        kind = {"vendor": "shop", "org": "club", "peer": "student"}.get(r["kind"], r["kind"])
        verified = "Verified campus " + kind if r["campus_verified"] else "Unverified " + kind
        reports = f" · {r['scam_reports']} scam report(s)" if r["scam_reports"] else ""
        color = "#fd374a" if r["scam_reports"] else ("#51e57e" if r["campus_verified"] else "#ffbd34")
        text = f"{verified} · trust {r['trust_score']}/100 · account {r['account_age_days']} days old{reports}"
    st.markdown(f'<span style="display:inline-block;font-family:var(--rr-mono,monospace);font-size:.72rem;'
                f'padding:.2rem .6rem;border-radius:999px;border:1px solid {color};color:{color};'
                f'background:rgba(255,255,255,.03);margin:.2rem 0 .5rem 0">{html.escape(text)}</span>',
                unsafe_allow_html=True)


WHO = {"AGENT": "Agent", "TOOL": "Tool", "GUARD": "Guard", "HUMAN": "Human"}
WHO_NOTE = {"AGENT": "the AI", "TOOL": "code fetching facts", "GUARD": "code rules", "HUMAN": "a person"}


def case_markdown(txn_id: str) -> str:
    """A payment's case report, built here in the page from the existing payment record and decision log."""
    ctx = svc.get_transaction_context(txn_id)
    if "error" in ctx:
        return f"# Case {txn_id}\n\nNot found.\n"
    with svc.db() as con:
        a = con.execute("SELECT * FROM assessments WHERE txn_id = ?", (txn_id,)).fetchone()
    a = dict(a) if a else {}
    clean = lambda t: str(t if t is not None else "-").replace("|", "\\|").replace("\n", " ")  # noqa: E731
    out = [f"# Reroute case report: {txn_id}", "",
           f"- **Payment:** AED {ctx['amount_aed']:g} to `{ctx['recipient_handle']}`",
           f"- **From:** {ctx['student']['name']} · **Created:** {ctx['created_at']}",
           f"- **Note:** {clean(ctx['note'])}", f"- **Message:** {clean(ctx['message_text'])}",
           f"- **Current state:** {ctx['state']} · **Decision:** {clean(a.get('route'))}",
           f"- **Why:** {clean(a.get('guard_reasons'))}",
           f"- **Explanation shown to the student:** {clean(a.get('student_explanation'))}", "",
           "## Timeline", "", "| Log ID | Time | Who | What happened | Detail |", "|---|---|---|---|---|"]
    out += [f"| {svc.log_id(e['id'])} | {e['ts']} | {WHO.get(e['actor'], e['actor'])} | {clean(e['event'])} | "
            f"{clean(e['detail'])} |" for e in svc.get_log(txn_id)]
    out += ["", "_Pretend campus money. The AI gives an opinion; code rules decide; people approve holds._", ""]
    return "\n".join(out)


def case_report_button(txn_id: str, key: str | None = None) -> None:
    """Download a payment's decision log as a Markdown case report (built in the page)."""
    st.download_button("Download case report", case_markdown(txn_id), file_name=f"reroute_case_{txn_id}.md",
                       mime="text/markdown", key=key or f"report_{txn_id}", icon=":material/description:")


def timeline(txn_id: str) -> None:
    """Read-only timeline of a payment's decision log, labelled Agent / Tool / Guard / Human."""
    log = svc.get_log(txn_id)
    if not log:
        st.caption("No steps logged yet.")
        return
    rows = []
    for e in log:
        rows.append(f'<div class="rr-log {html.escape(e["actor"])}"><span class="who">{svc.log_id(e["id"])} · '
                    f'{WHO.get(e["actor"], e["actor"])} ({WHO_NOTE.get(e["actor"], "")})</span><br>'
                    f'<b>{html.escape(e["event"])}</b>: {html.escape(e["detail"])}</div>')
    st.markdown("".join(rows), unsafe_allow_html=True)


FRIENDLY_ERRORS = [
    ("enough money", "Not enough money in your wallet for this payment. Lower the amount, or add test money on "
                     "your Wallet page (beta)."),
    ("Amount must be", None), ("Keep the note", None), ("Keep the message", None),
    ("yourself", "You can't send money to yourself. Pick another student."),
    ("wasn't found", "We couldn't find that student any more. Search for them again."),
    ("requests waiting", None),
]


def friendly_error(problem: str) -> None:
    """Show an existing problem message from the send/request functions, with a 'what to do next' hint."""
    for needle, better in FRIENDLY_ERRORS:
        if needle in problem:
            st.error(better or f"{problem} Fix it and press Review again.")
            return
    st.error(problem)


def show_log(txn_id: str, as_table: bool = False) -> None:
    """Decision log. Desktop: a table. Phone: stacked cards colour-coded by who did each step."""
    log = svc.get_log(txn_id)
    if not log:
        st.caption("No decision log yet. Run the agent.")
        return
    if as_table:
        st.dataframe([{"log id": svc.log_id(e["id"]), "step": e["step"],
                       "who": f"{ACTOR_ICON.get(e['actor'], e['actor'])} "
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
            f'<div class="rr-log {html.escape(actor)}"><span class="who">{svc.log_id(e["id"])} · {e["step"]}. '
            f'{ACTOR_ICON.get(actor, html.escape(actor))} ({ACTOR_NOTE.get(actor, "")})</span><br>'
            f'<b>{html.escape(e["event"])}</b>: {html.escape(e["detail"])}</div>')
    st.markdown("".join(rows), unsafe_allow_html=True)
