"""Student pages (the "Student" dropdown): a student's own PocketPay wallet, protected by Reroute.

  Wallet         - balance, scam alerts, and what needs attention
  Send and request - ask another student for money, or pay them (both checked by Reroute)
  Needs my OK    - payments Reroute paused: Confirm or Cancel
  Team checking  - payments the PocketPay team is reviewing
  New requests   - payment requests not checked yet: "Check with Reroute"
  Activity       - payments that went through or were cancelled
  My profile     - name, school, join date, change password

A student only ever sees their own wallet. Staff get a dropdown to view any student's wallet.
Each page has a phone layout (narrow wallet app) and a desktop layout (wider, cards in a grid).
"""

import html

import streamlit as st

import accounts
import agent
import common
import services as svc

BASE_CSS = """
<style>
.pp-top { display:flex; justify-content:space-between; align-items:center; margin-bottom:.6rem; gap:.5rem; }
.pp-brand { font-weight:800; font-size:1.35rem; letter-spacing:-.02em; }
.pp-shield { font-size:.78rem; padding:.2rem .55rem; border-radius:999px;
             background: rgba(34,197,94,.15); color:#16a34a; font-weight:600; white-space:nowrap; }
.pp-balance { border-radius:20px; padding:1.1rem 1.2rem; color:#fff; margin-bottom:1rem;
              background: linear-gradient(135deg,#4f46e5 0%,#7c3aed 55%,#0ea5e9 100%); }
.pp-balance .lbl { opacity:.85; font-size:.85rem; }
.pp-balance .amt { font-size:2.1rem; font-weight:800; line-height:1.15; margin:.15rem 0 .35rem 0; }
.pp-balance .who { opacity:.9; font-size:.85rem; }
.pp-mini { font-size:.9rem; opacity:.8; margin:-.3rem 0 .8rem 0; }
.pp-sec { font-weight:700; font-size:1rem; margin:1.1rem 0 .45rem 0; }
.pp-card { border-radius:16px; padding:.85rem 1rem; margin:.4rem 0 .5rem 0;
           background: rgba(128,128,128,.09); border: 1px solid rgba(128,128,128,.18); }
.pp-card.warn { border-color: rgba(234,179,8,.55); background: rgba(234,179,8,.10); }
.pp-card.team { border-color: rgba(59,130,246,.45); background: rgba(59,130,246,.09); }
.pp-card.alert { border-color: rgba(239,68,68,.55); background: rgba(239,68,68,.10); }
.pp-row { display:flex; justify-content:space-between; gap:.6rem; align-items:baseline; }
.pp-to { font-weight:700; font-size:1rem; word-break: break-all; }
.pp-amt { font-weight:800; font-size:1.05rem; white-space:nowrap; }
.pp-msg { font-size:.9rem; opacity:.85; margin-top:.35rem; font-style:italic; }
.pp-say { font-size:.93rem; margin-top:.45rem; line-height:1.4; }
.pp-chip { display:inline-block; font-size:.72rem; font-weight:700; padding:.12rem .5rem;
           border-radius:999px; margin-top:.4rem; }
.chip-ok { background:rgba(34,197,94,.18); color:#16a34a; }
.chip-no { background:rgba(239,68,68,.16); color:#dc2626; }
.chip-wait { background:rgba(234,179,8,.2); color:#b45309; }
.chip-team { background:rgba(59,130,246,.18); color:#2563eb; }
.pp-empty { font-size:.92rem; opacity:.65; padding:.6rem 0; }
.pp-foot { text-align:center; font-size:.75rem; opacity:.6; margin-top:1.5rem; }
</style>
"""
PHONE_CSS = """
<style>
.block-container { max-width: 460px !important; padding: 1rem 0.9rem 4rem 0.9rem !important; }
div[data-testid="stButton"] button, div[data-testid="stFormSubmitButton"] button {
    width: 100%; min-height: 3rem; font-size: 1rem; border-radius: 14px; }
</style>
"""
DESKTOP_CSS = """
<style>
.block-container { max-width: 1150px !important; padding-top: 2rem !important; }
div[data-testid="stButton"] button { border-radius: 10px; }
.pp-balance .amt { font-size:2.4rem; }
</style>
"""

e = html.escape  # every piece of user text is escaped before it goes into HTML


# ---------------------------------------------------------------- shared helpers
def _setup() -> tuple[dict, list[dict], list[dict], bool]:
    """CSS, whose wallet this is, and its data. Returns (student, payments, alerts, is_phone)."""
    mobile = common.is_mobile()
    st.markdown(BASE_CSS + (PHONE_CSS if mobile else DESKTOP_CSS), unsafe_allow_html=True)
    with svc.db() as con:
        students = [dict(r) for r in con.execute("SELECT * FROM students ORDER BY name")]
    if st.session_state.get("role") == "student":
        acc = st.session_state.account
        me = next((s for s in students if s["student_id"] == acc["student_id"]), None)
        if me is None:  # account exists but its wallet is missing (e.g. database replaced): rebuild it
            with svc.db() as con:
                accounts.build_wallet(con, acc["account_id"], acc["name"], acc["school"])
            st.rerun()
    else:
        ids = [s["student_id"] for s in students]
        current = st.session_state.get("student_id")
        label = {s["student_id"]: f"{s['name']} · {s.get('school') or 'Demo High School'}" for s in students}
        sid = st.selectbox("Viewing wallet of (staff view)", ids,
                           index=ids.index(current) if current in ids else 0, format_func=label.get)
        st.session_state.student_id = sid
        me = next(s for s in students if s["student_id"] == sid)
    with svc.db() as con:
        txns = [dict(r) for r in con.execute(
            """SELECT t.*, a.student_explanation, a.guard_reasons, r.display_name AS to_name FROM transactions t
               LEFT JOIN assessments a USING (txn_id) LEFT JOIN recipients r ON r.handle = t.recipient_handle
               WHERE t.student_id = ? ORDER BY t.created_at DESC, t.rowid DESC""",
            (me["student_id"],))]
        alerts = [dict(r) for r in con.execute("SELECT * FROM scam_alerts ORDER BY id DESC LIMIT 5")]
    return me, txns, alerts, mobile


def _balance(me: dict, txns: list[dict]) -> float:
    sent = sum(t["amount_aed"] for t in txns if t["state"] in ("CLEARED", "COMPLETED") and t["amount_aed"] > 0)
    return me["wallet_balance_aed"] - sent + accounts.received_total(me["student_id"])


def _who(me: dict) -> str:
    who = f'{e(me["name"])} · {e(me.get("school") or "Demo High School")}'
    return who + (f' · Grade {me["grade"]}' if me.get("grade") else "")


def _title(title: str, me: dict, txns: list[dict]) -> None:
    """Sub-page heading with a one-line balance reminder."""
    st.subheader(title)
    st.markdown(f'<div class="pp-mini">{_who(me)} · Balance <b>AED {_balance(me, txns):,.2f}</b></div>',
                unsafe_allow_html=True)


def _chip(state: str) -> str:
    label, cls = {"CLEARED": ("Sent", "chip-ok"), "COMPLETED": ("Sent", "chip-ok"),
                  "CANCELLED": ("Cancelled", "chip-no"), "PENDING_CONFIRMATION": ("Needs your OK", "chip-wait"),
                  "ESCALATED": ("Team is checking", "chip-team"), "FLAGGED": ("New", "chip-wait")}[state]
    return f'<span class="pp-chip {cls}">{label}</span>'


def _card(t: dict, kind: str = "", say: str | None = None, show_msg: bool = True) -> None:
    msg = t["message_text"] or ""
    body = (f'<div class="pp-card {kind}"><div class="pp-row"><span class="pp-to">{e(t["recipient_handle"])}</span>'
            f'<span class="pp-amt">AED {t["amount_aed"]:.0f}</span></div>')
    if t.get("to_name") and t.get("channel") in (accounts.REQUEST_CHANNEL, accounts.SEND_CHANNEL):
        what = "Request from" if t["channel"] == accounts.REQUEST_CHANNEL else "Payment to"
        body += f'<div style="font-size:.85rem;font-weight:600">{what} {e(t["to_name"])}</div>'
    if t["note"]:
        body += f'<div style="font-size:.85rem;opacity:.75">{e(t["note"])}</div>'
    if show_msg and msg:
        body += f'<div class="pp-msg">"{e(msg[:180])}{"…" if len(msg) > 180 else ""}"</div>'
    if say:
        body += f'<div class="pp-say">🛡️ <b>Reroute:</b> {e(say)}</div>'
    st.markdown(body + _chip(t["state"]) + "</div>", unsafe_allow_html=True)


def _empty(text: str) -> None:
    st.markdown(f'<div class="pp-empty">{e(text)}</div>', unsafe_allow_html=True)


def _grid(items: list, render, mobile: bool) -> None:
    """Desktop: two cards per row. Phone: one column."""
    if mobile:
        for item in items:
            render(item)
        return
    for i in range(0, len(items), 2):
        for col, item in zip(st.columns(2, gap="medium"), items[i:i + 2]):
            with col:
                render(item)


def _topup(me: dict) -> None:
    """Beta only: add pretend AED to this wallet (students to their own, staff to anyone's)."""
    if not accounts.BETA_TOPUPS:
        return
    who = "staff" if st.session_state.get("role") == "staff" else "student"
    with st.expander("💰 Add test money (beta)"):
        st.caption(f"Beta testing only: this is pretend money. Up to AED {accounts.MAX_TOPUP_AED:,} per top-up.")
        quick = st.pills("Quick amounts", [50, 100, 250, 500], format_func=lambda v: f"AED {v}",
                         key=f"topup_quick_{me['student_id']}")
        amount = st.number_input("Amount (AED)", min_value=1, max_value=accounts.MAX_TOPUP_AED,
                                 value=int(quick or 100), step=10, key=f"topup_amt_{me['student_id']}_{quick}")
        if st.button("Add to wallet", type="primary", key=f"topup_btn_{me['student_id']}", width="stretch"):
            balance, problem = accounts.add_funds(me["student_id"], amount, who)
            if problem:
                st.error(problem)
            else:
                st.toast(f"Added AED {amount:,} test money. New balance AED {balance:,.2f}", icon="💰")
                st.rerun()


def _footer() -> None:
    st.markdown('<div class="pp-foot">Demo wallet · synthetic data · Reroute never freezes your wallet</div>',
                unsafe_allow_html=True)
    common.layout_switch()
    if st.session_state.get("role") == "student":
        common.logout_button(key="logout_footer")   # also at the bottom of the page, handy on phones


# ---------------------------------------------------------------- 🏠 Wallet
def wallet_page() -> None:
    me, txns, alerts, mobile = _setup()
    counts = {k: sum(t["state"] == k for t in txns) for k in ("PENDING_CONFIRMATION", "ESCALATED", "FLAGGED")}
    done = sum(t["state"] in ("CLEARED", "COMPLETED", "CANCELLED") for t in txns)

    def balance_card() -> None:
        st.markdown(
            f'<div class="pp-top"><span class="pp-brand">PocketPay</span>'
            f'<span class="pp-shield">🛡️ Protected by Reroute</span></div>'
            f'<div class="pp-balance"><div class="lbl">Wallet balance</div>'
            f'<div class="amt">AED {_balance(me, txns):,.2f}</div><div class="who">{_who(me)}</div></div>',
            unsafe_allow_html=True)

    def attention() -> None:
        st.markdown('<div class="pp-sec">What needs attention</div>', unsafe_allow_html=True)
        if "transfer" in common.PAGES:
            st.page_link(common.PAGES["transfer"], label="Send or request money", icon="💸")
        st.page_link(common.PAGES["needs_ok"], label=f"Needs my OK ({counts['PENDING_CONFIRMATION']})", icon="⚠️")
        st.page_link(common.PAGES["checking"], label=f"Team checking ({counts['ESCALATED']})", icon="🔎")
        st.page_link(common.PAGES["requests"], label=f"New requests ({counts['FLAGGED']})", icon="📨")
        st.page_link(common.PAGES["activity"], label=f"Activity ({done})", icon="🧾")

    def alert_list() -> None:
        st.markdown('<div class="pp-sec">📢 Campus scam alerts</div>', unsafe_allow_html=True)
        if not alerts:
            _empty("No scam alerts right now.")
        for a in alerts:
            st.markdown(f'<div class="pp-card alert">📢 {e(a["summary"])}</div>', unsafe_allow_html=True)

    if mobile:
        balance_card()
        _topup(me)
        attention()
        alert_list()
    else:
        left, right = st.columns([1, 1.2], gap="large")
        with left:
            balance_card()
            _topup(me)
            attention()
        with right:
            alert_list()
    _footer()


# ---------------------------------------------------------------- ⚠️ Needs my OK
def needs_ok_page() -> None:
    me, txns, _, mobile = _setup()
    _title("⚠️ Needs my OK", me, txns)
    items = [t for t in txns if t["state"] == "PENDING_CONFIRMATION"]
    if not items:
        _empty("Nothing needs your OK right now.")

    def render(t: dict) -> None:
        _card(t, "warn", t["student_explanation"])
        c1, c2 = st.columns(2)
        if c1.button("✅ I know them, send", key=f"s_ok_{t['txn_id']}", type="primary", width="stretch"):
            agent.resolve(t["txn_id"], approve=True, actor="student")
            st.toast(f"Sent AED {t['amount_aed']:.0f} to {t['recipient_handle']}", icon="✅")
            st.rerun()
        if c2.button("⛔ Cancel", key=f"s_no_{t['txn_id']}", width="stretch"):
            agent.resolve(t["txn_id"], approve=False, actor="student")
            st.toast("Transfer cancelled. Your money stays in your wallet.", icon="⛔")
            st.rerun()

    _grid(items, render, mobile)
    _footer()


# ---------------------------------------------------------------- 🔎 Team checking
def checking_page() -> None:
    me, txns, _, mobile = _setup()
    _title("🔎 Team checking", me, txns)
    st.caption("The PocketPay team is reviewing these. Your money is safe and your wallet still works.")
    items = [t for t in txns if t["state"] == "ESCALATED"]
    if not items:
        _empty("Nothing is being checked.")
    _grid(items, lambda t: _card(t, "team", t["student_explanation"]), mobile)
    _footer()


# ---------------------------------------------------------------- 📨 New requests
def requests_page() -> None:
    me, txns, _, mobile = _setup()
    _title("📨 New requests", me, txns)
    items = [t for t in txns if t["state"] == "FLAGGED"]
    if not items:
        _empty("No new payment requests.")

    def render(t: dict) -> None:
        _card(t)
        if st.button("🛡️ Check with Reroute", key=f"s_run_{t['txn_id']}", width="stretch"):
            with st.spinner("Reroute is checking this payment (about 20 seconds)…"):
                accounts.refresh_peer(t["recipient_handle"])   # up-to-date trust info if it's a student
                agent.process_transaction(t["txn_id"])
            st.rerun()

    _grid(items, render, mobile)
    _footer()


# ---------------------------------------------------------------- 💸 Send and request
# What the person who ASKED for money sees. "Waiting" covers new, held and staff-review on purpose:
# a scammer shouldn't learn that Reroute flagged their request.
OUTGOING_STATUS = {"FLAGGED": ("Waiting", "chip-wait"), "PENDING_CONFIRMATION": ("Waiting", "chip-wait"),
                   "ESCALATED": ("Waiting", "chip-wait"), "CLEARED": ("Paid", "chip-ok"),
                   "COMPLETED": ("Paid", "chip-ok"), "CANCELLED": ("Not paid", "chip-no")}


def _incoming_card(t: dict) -> None:
    who = e(f'{t.get("payer_name") or "A student"} · {t.get("payer_school") or ""}'.rstrip(" ·"))
    if t["channel"] == accounts.REQUEST_CHANNEL:
        label, cls = OUTGOING_STATUS.get(t["state"], ("Waiting", "chip-wait"))
        title = f"You asked {who}"
    else:
        label, cls, title = "Received", "chip-ok", f"From {who}"
    note = f'<div style="font-size:.85rem;opacity:.75">{e(t["note"])}</div>' if t["note"] else ""
    st.markdown(f'<div class="pp-card"><div class="pp-row"><span class="pp-to">{title}</span>'
                f'<span class="pp-amt">AED {t["amount_aed"]:.0f}</span></div>{note}'
                f'<span class="pp-chip {cls}">{label}</span></div>', unsafe_allow_html=True)


def _transfer_result(kind: str, s: dict, to: dict) -> None:
    name = to["name"]
    if kind == "request":
        st.success(f"Request sent to {name}. It's waiting in their New requests, where Reroute will check it "
                   "before they pay.")
    elif s.get("state") == "CLEARED":
        st.success(f"Sent AED {s.get('amount', 0):.0f} to {name}. Reroute checked it and it went through.")
    elif s.get("state") == "PENDING_CONFIRMATION":
        st.warning("Reroute paused this payment so you can double-check it. Open Needs my OK to send or cancel it.")
        st.page_link(common.PAGES["needs_ok"], label="Go to Needs my OK", icon="⚠️")
    else:
        st.info("The PocketPay team is checking this payment. Your money stays in your wallet until they finish.")


def transfer_page() -> None:
    me, txns, _, mobile = _setup()
    _title("💸 Send and request", me, txns)
    handle = accounts.handle_of_student(me["student_id"])
    if st.session_state.get("role") != "student" or not handle:
        st.info("Students send and request money from this page. Staff can't send or request money for a student."
                if st.session_state.get("role") == "staff" else "Only signed-up student accounts can send money.")
        _footer()
        return
    st.caption(f"Your pay-me handle is **{handle}**. Every payment between students is checked by Reroute, "
               "the same way as any other payment.")

    def form_side() -> None:
        st.markdown('<div class="pp-sec">1. Find a student</div>', unsafe_allow_html=True)
        c1, c2 = (st, st) if mobile else st.columns(2)
        name = c1.text_input("Their name", key="tr_name", placeholder="e.g. Sara")
        school = c2.selectbox("Their school", accounts.schools(), index=None, accept_new_options=True,
                              placeholder="Type or pick a school", key="tr_school")
        if not (name.strip() and school):
            _empty("Type their name and school to find them. You need both, so nobody can browse every student.")
            return
        matches = accounts.find_students(name, school, me["student_id"])
        if not matches:
            _empty("No student found. Check the spelling of the name and school.")
            return
        to = st.selectbox("Pick the student", matches, key="tr_pick",
                          format_func=lambda a: f"{a['name']} · {a['school']} ({a['handle']})")
        st.markdown('<div class="pp-sec">2. Request or send</div>', unsafe_allow_html=True)
        mode = st.segmented_control("What do you want to do?", ["Request money", "Send money"],
                                    default="Request money", key="tr_mode") or "Request money"
        with st.form("transfer", clear_on_submit=True):
            amount = st.number_input("Amount (AED)", min_value=1, max_value=accounts.MAX_TRANSFER_AED, value=20, step=5)
            note = st.text_input("What it's for", max_chars=accounts.MAX_NOTE_LEN, placeholder="e.g. pizza split")
            message = st.text_area("Message (optional)", max_chars=accounts.MAX_MESSAGE_LEN,
                                   placeholder="Reroute reads this message when it checks the payment.")
            submitted = st.form_submit_button(mode, type="primary", width="stretch")
        if not submitted:
            return
        if mode == "Request money":
            txn_id, problem = accounts.request_money(me["student_id"], to["student_id"], amount, note, message)
            if problem:
                st.error(problem)
            else:
                _transfer_result("request", {}, to)
            return
        txn_id, problem = accounts.send_money(me["student_id"], to["student_id"], amount, note, message)
        if problem:
            st.error(problem)
            return
        with st.spinner("Reroute is checking this payment (about 20 seconds)…"):
            s = agent.process_transaction(txn_id)
        _transfer_result("send", dict(s, amount=amount), to)

    def history_side() -> None:
        items = accounts.transfers_to_me(me["student_id"])
        st.markdown('<div class="pp-sec">Requests you sent and money you received</div>', unsafe_allow_html=True)
        if not items:
            _empty("Nothing yet. Payments you send show up in your Activity.")
        for t in items:
            _incoming_card(t)

    if mobile:
        form_side()
        history_side()
    else:
        left, right = st.columns([1.15, 1], gap="large")
        with left:
            form_side()
        with right:
            history_side()
    _footer()


# ---------------------------------------------------------------- 🧾 Activity
def activity_page() -> None:
    me, txns, _, mobile = _setup()
    _title("🧾 Activity", me, txns)
    items = [t for t in txns if t["state"] in ("CLEARED", "COMPLETED", "CANCELLED")]
    topups = accounts.topups_for(me["student_id"])
    if not items and not topups:
        _empty("No activity yet.")
    _grid(items, lambda t: _card(t, show_msg=False), mobile)
    if topups:
        st.markdown('<div class="pp-sec">💰 Test money added (beta)</div>', unsafe_allow_html=True)

        def render_topup(t: dict) -> None:
            by = "you" if t["added_by"] == "student" else "PocketPay staff"
            st.markdown(f'<div class="pp-card"><div class="pp-row"><span class="pp-to">Top-up by {by}</span>'
                        f'<span class="pp-amt" style="color:#16a34a">+AED {t["amount_aed"]:,.0f}</span></div>'
                        f'<div style="font-size:.85rem;opacity:.75">{e(t["ts"])} · beta test money</div></div>',
                        unsafe_allow_html=True)

        _grid(topups, render_topup, mobile)
    _footer()


# ---------------------------------------------------------------- 👤 My profile
def profile_page() -> None:
    me, txns, _, mobile = _setup()
    st.subheader("👤 My profile")
    account_id = accounts.account_id_from_student(me["student_id"])
    acc = accounts.get(account_id) if account_id else None
    w = accounts.wallet_summary(me["student_id"])

    with st.container(border=True):
        st.markdown(f"### {me['name']}")
        st.markdown(f"**School:** {me.get('school') or 'Demo High School'}  \n"
                    f"**Member since:** {acc['created_at'] if acc else 'demo account'}  \n"
                    f"**Wallet ID:** {me['student_id']}")
    figures = [("Balance", f"AED {w['balance']:,.0f}"), ("Payments", w["payments"]),
               ("Needs my OK", w["needs_ok"]), ("Team checking", w["with_staff"])]
    for row in ([figures[:2], figures[2:]] if mobile else [figures]):
        for col, (label, value) in zip(st.columns(len(row)), row):
            col.metric(label, value)

    if st.session_state.get("role") == "student" and acc:
        st.markdown('<div class="pp-sec">🔑 Change password</div>', unsafe_allow_html=True)
        with st.form("change_password", clear_on_submit=True):
            current = st.text_input("Current password", type="password")
            new = st.text_input(f"New password (at least {accounts.MIN_PASSWORD_LEN} characters)", type="password")
            confirm = st.text_input("Type the new password again", type="password")
            if st.form_submit_button("Change password", type="primary"):
                problems = accounts.change_password(account_id, current, new, confirm)
                for p in problems:
                    st.error(p)
                if not problems:
                    # A password change logs out every saved login for this account; keep this one.
                    common.remember_login("student", acc)
                    common.flush_cookie()
                    st.success("Password changed. Any other devices you were logged in on have been logged out.")
    elif st.session_state.get("role") == "staff":
        st.caption("Staff view. To reset this student's password or wallet, use Staff → Manage accounts.")
    _footer()
