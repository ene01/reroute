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

import csv
import html
import io
import re

import streamlit as st

import accounts
import agent
import common
import services as svc
import theme

BASE_CSS = """
<style>
.pp-shield { display:inline-block; font-family:var(--rr-mono); font-size:.68rem; letter-spacing:.08em;
             padding:.22rem .6rem; border-radius:999px; border:1px solid rgba(81,229,126,.45);
             background: rgba(81,229,126,.1); color:var(--rr-success); white-space:nowrap; margin-top:.7rem; }
.pp-balance { position:relative; border-radius:20px; padding:1.3rem 1.4rem; margin-bottom:1rem; overflow:hidden;
              background: linear-gradient(135deg, rgba(8,32,52,.9), rgba(6,14,28,.85));
              border:1px solid rgba(0,224,234,.55); box-shadow: 0 0 34px -10px rgba(0,224,234,.7); }
.pp-balance .who { font-family:var(--rr-mono); font-size:.7rem; letter-spacing:.14em; text-transform:uppercase;
                   color:var(--rr-muted); }
.pp-balance .lbl { color:#b7c7d0; font-size:.9rem; margin-top:.8rem; }
.pp-balance .amt { font-family:var(--rr-head); font-size:2.3rem; font-weight:700; line-height:1.15; margin:.1rem 0 0 0; }
.pp-balance .amt span { color:var(--rr-primary); }
.pp-mini { font-size:.88rem; color:var(--rr-muted); margin:-.6rem 0 1rem 0; }
.pp-mini b { color:var(--rr-primary); }
.pp-sec { font-family:var(--rr-head); font-weight:700; font-size:1.08rem; margin:1.2rem 0 .5rem 0; }
.pp-card { border-radius:16px; padding:.95rem 1.1rem; margin:.4rem 0 .6rem 0;
           background: var(--rr-card); border: 1px solid var(--rr-border); }
.pp-card.warn { box-shadow: inset 3px 0 0 var(--rr-warning); }
.pp-card.team { box-shadow: inset 3px 0 0 var(--rr-accent); }
.pp-card.alert { box-shadow: inset 3px 0 0 var(--rr-danger); border-color: rgba(253,55,74,.3); }
.pp-row { display:flex; justify-content:space-between; gap:.6rem; align-items:baseline; }
.pp-to { font-family:var(--rr-mono); font-weight:600; font-size:.98rem; color:var(--rr-primary); word-break: break-all; }
.pp-amt { font-family:var(--rr-head); font-weight:700; font-size:1.15rem; white-space:nowrap; }
.pp-msg { font-family:var(--rr-mono); font-size:.82rem; color:#b7c7d0; margin-top:.5rem; padding:.55rem .7rem;
          border-radius:10px; background:rgba(3,9,18,.65); border:1px solid rgba(0,224,234,.08); }
.pp-say { font-size:.93rem; margin-top:.55rem; line-height:1.45; }
.pp-chip { display:inline-block; font-family:var(--rr-mono); font-size:.66rem; font-weight:600; letter-spacing:.1em;
           text-transform:uppercase; padding:.15rem .55rem; border-radius:999px; margin-top:.55rem; border:1px solid; }
.chip-ok { background:rgba(81,229,126,.1); color:var(--rr-success); border-color:rgba(81,229,126,.4); }
.chip-no { background:rgba(253,55,74,.1); color:var(--rr-danger); border-color:rgba(253,55,74,.4); }
.chip-wait { background:rgba(255,189,52,.1); color:var(--rr-warning); border-color:rgba(255,189,52,.4); }
.chip-team { background:rgba(238,80,230,.1); color:var(--rr-accent); border-color:rgba(238,80,230,.4); }
.pp-empty { font-size:.92rem; color:var(--rr-muted); padding:.6rem 0; }
.pp-foot { text-align:center; font-family:var(--rr-mono); font-size:.66rem; letter-spacing:.12em; text-transform:uppercase;
           color:var(--rr-muted); margin-top:1.8rem; }
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
    st.markdown(BASE_CSS + common.MOBILE_CSS + (PHONE_CSS if mobile else DESKTOP_CSS), unsafe_allow_html=True)
    theme.status_bar()
    st.markdown('<div style="margin:-.8rem 0 .8rem 0;font-size:.75rem;color:#ffbd34;font-family:var(--rr-mono);'
                'letter-spacing:.08em">PRETEND CAMPUS MONEY · NO REAL MONEY MOVES</div>', unsafe_allow_html=True)
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
    theme.page_header(title, f"Student · {me['name']}")
    st.markdown(f'<div class="pp-mini">{_who(me)} · Balance <b>AED {_balance(me, txns):,.2f}</b></div>',
                unsafe_allow_html=True)


def _chip(state: str) -> str:
    label, cls = {"CLEARED": ("Sent", "chip-ok"), "COMPLETED": ("Sent", "chip-ok"),
                  "CANCELLED": ("Cancelled", "chip-no"), "PENDING_CONFIRMATION": ("Needs your OK", "chip-wait"),
                  "ESCALATED": ("Team is checking", "chip-team"), "FLAGGED": ("New", "chip-wait"),
                  "CONFIRMED_CANCELLABLE": ("Confirmed · can still cancel", "chip-wait"),
                  "PENDING_GUARDIAN": ("Waiting for guardian", "chip-team")}.get(state, (state, "chip-wait"))
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


def _reason(t: dict) -> str:
    """The rule's reason in plain words (first part of the saved guard reason)."""
    r = (t.get("guard_reasons") or "").split(";")[0].strip()
    return r[0].upper() + r[1:] if r else ""


def _ok_card(t: dict) -> None:
    """A held payment with the amount, who it goes to, and why it was paused, all up front."""
    to = e(t.get("to_name") or "")
    body = (f'<div class="pp-card warn"><div class="pp-row"><span class="pp-amt" style="font-size:1.6rem">'
            f'AED {t["amount_aed"]:,.2f}</span>{_chip(t["state"])}</div>'
            f'<div style="margin-top:.35rem">to <span class="pp-to">{e(t["recipient_handle"])}</span>'
            f'{f" · {to}" if to else ""}</div>')
    if t["note"]:
        body += f'<div style="font-size:.85rem;opacity:.8">For: {e(t["note"])}</div>'
    if _reason(t):
        body += (f'<div class="pp-say"><b>Why Reroute paused it:</b> {e(_reason(t))}.</div>')
    say = t.get("student_explanation") or ""
    if say.startswith("Paused until you confirm.") and _reason(t):   # the reason is already shown just above
        cut = say.find(_reason(t)[1:] + ".")
        say = say[cut + len(_reason(t)):].strip() if cut != -1 else say
    if say:
        body += f'<div class="pp-say">🛡️ {e(say)}</div>'
    if t["message_text"]:
        body += f'<div class="pp-msg">"{e(t["message_text"][:180])}{"…" if len(t["message_text"]) > 180 else ""}"</div>'
    st.markdown(body + "</div>", unsafe_allow_html=True)


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

    theme.page_header("Student Wallet", "PocketPay")

    def balance_card() -> None:
        whole, cents = f"{_balance(me, txns):,.2f}".split(".")
        st.markdown(
            f'<div class="pp-balance"><div class="who">{_who(me)}</div>'
            f'<div class="lbl">Available balance</div>'
            f'<div class="amt">AED <span>{whole}.{cents}</span></div>'
            f'<span class="pp-shield">Protected by Reroute · never frozen</span></div>',
            unsafe_allow_html=True)

    def attention() -> None:
        st.markdown('<div class="pp-sec">What needs attention</div>', unsafe_allow_html=True)
        need, team = counts["PENDING_CONFIRMATION"], counts["ESCALATED"]
        if need:
            st.warning(f"{need} payment{'s' if need != 1 else ''} need{'' if need != 1 else 's'} your OK. "
                       "Open Needs my OK to send or cancel.")
        elif not team and not counts["FLAGGED"]:
            _empty("All clear: nothing needs you right now. Send money below, or check back after a friend "
                   "requests money from you.")
        if "transfer" in common.PAGES:
            st.page_link(common.PAGES["transfer"], label="Send or request money", icon="💸")
        st.page_link(common.PAGES["needs_ok"], label=f"Needs my OK ({need})", icon="⚠️")
        st.page_link(common.PAGES["checking"], label=f"Team checking ({team})", icon="🔎")
        st.page_link(common.PAGES["requests"], label=f"New requests ({counts['FLAGGED']})", icon="📨")
        st.page_link(common.PAGES["activity"], label=f"Activity ({done})", icon="🧾")

    def alert_list() -> None:
        st.markdown('<div class="pp-sec">📢 Campus scam alerts</div>', unsafe_allow_html=True)
        if not alerts:
            _empty("No scam alerts right now. When the PocketPay team confirms a scam, a warning appears here "
                   "so you know which account to avoid.")
        for a in alerts:
            st.markdown(f'<div class="pp-card alert">📢 {e(a["summary"])}'
                        f'<div style="font-size:.75rem;color:var(--rr-muted);margin-top:.3rem">{e(a["ts"])}</div>'
                        '</div>', unsafe_allow_html=True)

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
        _empty("Nothing needs your OK right now. When Reroute pauses a payment, it shows up here with the reason.")

    def render(t: dict) -> None:
        _ok_card(t)
        c1, c2 = st.columns(1 if mobile else 2) * (2 if mobile else 1)
        if c1.button("✅ I know them, send", key=f"s_ok_{t['txn_id']}", type="primary", width="stretch"):
            agent.resolve(t["txn_id"], approve=True, actor="student")
            st.toast(f"Confirmed. You have {svc.CANCEL_WINDOW_S // 60} minutes to change your mind.", icon="✅")
            st.rerun()
        if c2.button("⛔ Cancel", key=f"s_no_{t['txn_id']}", width="stretch"):
            agent.resolve(t["txn_id"], approve=False, actor="student")
            st.toast("Transfer cancelled. Your money stays in your wallet.", icon="⛔")
            st.rerun()

    _grid(items, render, mobile)
    _cancel_window(me["student_id"], mobile)
    guardian = [t for t in txns if t["state"] == "PENDING_GUARDIAN"]
    if guardian:
        st.markdown('<div class="pp-sec">Waiting for your parent or guardian</div>', unsafe_allow_html=True)

        def render_guardian(t: dict) -> None:
            _card(t, "team", t["student_explanation"])
            if st.button("Cancel this payment", key=f"s_gc_{t['txn_id']}", width="stretch"):
                out = agent.cancel_payment(t["txn_id"], actor="student")
                st.toast("Cancelled. Your money stays in your wallet." if out.get("ok") else out["error"])
                st.rerun()

        _grid(guardian, render_guardian, mobile)
    _footer()


@st.fragment(run_every=5)
def _cancel_window(student_id: str, mobile: bool) -> None:
    """HS-1.4: payments confirmed in the last 2 minutes can still be cancelled. Counts down every 5 s."""
    agent.release_due()
    with svc.db() as con:
        items = [dict(r) for r in con.execute(
            """SELECT t.*, a.student_explanation, r.display_name AS to_name FROM transactions t
               LEFT JOIN assessments a USING (txn_id) LEFT JOIN recipients r ON r.handle = t.recipient_handle
               WHERE t.student_id = ? AND t.state = 'CONFIRMED_CANCELLABLE' ORDER BY t.confirmed_at DESC""",
            (student_id,))]
    if not items:
        return
    st.markdown('<div class="pp-sec">Just confirmed: you can still cancel</div>', unsafe_allow_html=True)

    def render(t: dict) -> None:
        left = svc.cancel_seconds_left(t["confirmed_at"])
        _card(t, "warn", "You confirmed this payment. It is sent when the timer runs out, unless you cancel.",
              show_msg=False)
        st.progress(max(0.0, min(1.0, left / max(svc.CANCEL_WINDOW_S, 1))),
                    text=f"⏱ {int(left // 60)}:{int(left % 60):02d} left to cancel")
        if st.button("Wait, cancel it!", key=f"s_cw_{t['txn_id']}", width="stretch", type="primary"):
            out = agent.cancel_payment(t["txn_id"], actor="student")
            st.toast("Cancelled. Your money stays in your wallet." if out.get("ok")
                     else f"Too late: {out['error']}")
            st.rerun()

    _grid(items, render, mobile)


# ---------------------------------------------------------------- 🔎 Team checking
def checking_page() -> None:
    me, txns, _, mobile = _setup()
    _title("🔎 Team checking", me, txns)
    st.caption("The PocketPay team is reviewing these. Your money is safe and your wallet still works.")
    items = [t for t in txns if t["state"] == "ESCALATED"]
    if not items:
        _empty("Nothing is being checked. If Reroute ever sends a payment to the PocketPay team, it waits here "
               "and your money stays in your wallet until they decide.")
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
            with st.spinner("Reroute is checking this payment…"):
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
                   "COMPLETED": ("Paid", "chip-ok"), "CANCELLED": ("Not paid", "chip-no"),
                   "CONFIRMED_CANCELLABLE": ("Waiting", "chip-wait"), "PENDING_GUARDIAN": ("Waiting", "chip-wait")}


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
    elif s.get("state") == "PENDING_GUARDIAN":
        st.warning(f"Students under {agent.guards.GUARDIAN_UNDER_AGE} need a parent or guardian to approve "
                   f"transfers above AED {agent.guards.GUARDIAN_PEER_LIMIT_AED}. Your guardian has been asked; the "
                   "money stays in your wallet until they say yes.")
    else:
        st.info("The PocketPay team is checking this payment. Your money stays in your wallet until they finish.")


QUICK_AMOUNTS = (5, 10, 15, 25)
HANDLE_RE = re.compile(r"@u(\d+)_[a-z0-9_]+")


def _student_from_handle(handle: str, my_sid: str) -> dict | None:
    """Turn a pay-me handle into the student it belongs to, using existing account lookups (read-only)."""
    m = HANDLE_RE.fullmatch((handle or "").strip())
    acc = accounts.get(int(m.group(1))) if m else None
    if not acc or accounts.handle_for(acc["account_id"], acc["name"]) != handle.strip() or acc["student_id"] == my_sid:
        return None
    return dict(acc, handle=handle.strip())


def _remember_recipient(to: dict) -> None:
    """Recent recipients live only in this browser session (no new table)."""
    recent = [r for r in st.session_state.get("tr_recent", []) if r["handle"] != to["handle"]]
    st.session_state.tr_recent = ([{k: to[k] for k in ("student_id", "name", "school", "handle")}] + recent)[:5]


def _clear_form() -> None:
    for k in ("tr_amount", "tr_note", "tr_msg", "tr_handle_input", "tr_review"):
        st.session_state.pop(k, None)


def _do_submit(me: dict, review: dict) -> None:
    """The real submit, unchanged: request_money, or send_money then process_transaction."""
    to, amount, note, message = review["to"], review["amount"], review["note"], review["message"]
    if review["mode"] == "Request money":
        txn_id, problem = accounts.request_money(me["student_id"], to["student_id"], amount, note, message)
        if problem:
            st.session_state.tr_problem = problem
            return
        st.session_state.tr_last = {"kind": "request", "txn_id": txn_id, "to": to, "amount": amount}
    else:
        txn_id, problem = accounts.send_money(me["student_id"], to["student_id"], amount, note, message)
        if problem:
            st.session_state.tr_problem = problem
            return
        with st.spinner("Reroute is checking this payment…"):
            s = agent.process_transaction(txn_id)
        st.session_state.tr_last = {"kind": "send", "txn_id": txn_id, "to": to, "amount": amount,
                                    "state": s.get("state")}
    _remember_recipient(to)
    _clear_form()


def _show_result(last: dict) -> None:
    """The outcome (same cases as before), then the read-only timeline and the case report."""
    _transfer_result(last["kind"], {"state": last.get("state"), "amount": last["amount"]}, last["to"])
    if last["kind"] == "send":
        st.markdown('<div class="pp-sec">What Reroute did, step by step</div>', unsafe_allow_html=True)
        common.timeline(last["txn_id"])
        common.case_report_button(last["txn_id"], key=f"tr_report_{last['txn_id']}")
    if st.button("Make another payment", type="primary", width="stretch", key="tr_again"):
        st.session_state.pop("tr_last", None)
        st.rerun()


def _review(me: dict, r: dict, balance: float) -> None:
    """Check everything before the real submit. Cancel goes back to the form with the boxes still filled."""
    to = r["to"]
    st.markdown('<div class="pp-sec">Review</div>', unsafe_allow_html=True)
    verb = "Send" if r["mode"] == "Send money" else "Request"
    st.markdown(f'<div class="pp-card"><div class="pp-row"><span class="pp-amt" style="font-size:1.6rem">'
                f'AED {r["amount"]:,.2f}</span><span class="pp-chip chip-wait">{verb}</span></div>'
                f'<div style="margin-top:.35rem">{"to" if verb == "Send" else "from"} <b>{e(to["name"])}</b> · '
                f'{e(to["school"])}</div><div class="pp-to">{e(to["handle"])}</div>'
                + (f'<div style="font-size:.88rem;margin-top:.3rem">For: {e(r["note"])}</div>' if r["note"] else "")
                + (f'<div class="pp-msg">"{e(r["message"])}"</div>' if r["message"] else "") + "</div>",
                unsafe_allow_html=True)
    common.trust_chip(to["handle"])
    if verb == "Send":
        st.markdown(f'<div class="pp-mini" style="margin:.2rem 0 .6rem 0">You would have '
                    f'<b>AED {balance - r["amount"]:,.2f}</b> left</div>', unsafe_allow_html=True)
        p = agent.preview_send(me["student_id"], to["handle"], r["amount"], r["note"], r["message"],
                               accounts.SEND_CHANNEL)
        st.markdown(f'<span class="pp-chip {"chip-ok" if p["label"] == "Goes through now" else "chip-wait"}">'
                    f'{e(p["label"])}</span><div class="pp-mini" style="margin-top:.3rem">{e(p["detail"])}</div>',
                    unsafe_allow_html=True)
    else:
        st.caption("They'll get your request in their New requests, and Reroute checks it before they pay.")
    c1, c2 = (st, st) if common.is_mobile() else st.columns(2)
    if c1.button(verb, type="primary", width="stretch", key="tr_send"):
        _do_submit(me, r)
        st.rerun()
    if c2.button("Cancel", width="stretch", key="tr_cancel"):
        st.session_state.pop("tr_review", None)
        st.rerun()


def transfer_page() -> None:
    me, txns, _, mobile = _setup()
    _title("💸 Send and request", me, txns)
    handle = accounts.handle_of_student(me["student_id"])
    if st.session_state.get("role") != "student" or not handle:
        st.info("Students send and request money from this page. Staff can't send or request money for a student."
                if st.session_state.get("role") == "staff" else "Only signed-up student accounts can send money.")
        _footer()
        return
    balance = _balance(me, txns)
    st.caption("Your pay-me handle (tap the icon to copy it and share it with friends):")
    st.code(handle, language=None)

    def form_side() -> None:
        last = st.session_state.get("tr_last")
        if last:
            _show_result(last)
            return
        problem = st.session_state.pop("tr_problem", None)
        if problem:
            common.friendly_error(problem)
        review = st.session_state.get("tr_review")
        if review:
            _review(me, review, balance)
            return

        st.markdown('<div class="pp-sec">1. Who?</div>', unsafe_allow_html=True)
        recent = st.session_state.get("tr_recent", [])
        if recent:
            st.caption("Recent")
            for i, r in enumerate(recent):
                st.button(f"{r['name']} · {r['handle']}", key=f"tr_recent_{i}", width="stretch",
                          on_click=lambda h=r["handle"]: st.session_state.update(tr_find="Their handle",
                                                                                 tr_handle_input=h))
        find = st.segmented_control("Find them by", ["Their handle", "Name and school"],
                                    default="Name and school", key="tr_find") or "Name and school"
        to = None
        if find == "Their handle":
            typed = st.text_input("Their pay-me handle", key="tr_handle_input", placeholder="e.g. @u12_sara_k")
            if typed.strip():
                common.trust_chip(typed.strip())
                to = _student_from_handle(typed, me["student_id"])
                if not to:
                    _empty("No student has that handle. Ask them to copy theirs from their Send page.")
        else:
            c1, c2 = (st, st) if mobile else st.columns(2)
            name = c1.text_input("Their name", key="tr_name", placeholder="e.g. Sara")
            school = c2.selectbox("Their school", accounts.schools(), index=None, accept_new_options=True,
                                  placeholder="Type or pick a school", key="tr_school")
            if name.strip() and school:
                matches = accounts.find_students(name, school, me["student_id"])
                if matches:
                    to = st.selectbox("Pick the student", matches, key="tr_pick",
                                      format_func=lambda a: f"{a['name']} · {a['school']} ({a['handle']})")
                    common.trust_chip(to["handle"])
                else:
                    _empty("No student found. Check the spelling of the name and school.")
            else:
                _empty("Type their name and school (you need both, so nobody can browse every student), "
                       "or switch to Their handle.")
        if not to:
            return

        st.markdown('<div class="pp-sec">2. How much?</div>', unsafe_allow_html=True)
        mode = st.segmented_control("What do you want to do?", ["Send money", "Request money"],
                                    default="Send money", key="tr_mode") or "Send money"
        chips = st.columns(len(QUICK_AMOUNTS))
        for col, v in zip(chips, QUICK_AMOUNTS):
            col.button(f"AED {v}", key=f"tr_chip_{v}", width="stretch",
                       on_click=lambda v=v: st.session_state.update(tr_amount=v))
        st.session_state.setdefault("tr_amount", 20)
        amount = st.number_input("Amount (AED)", min_value=1, max_value=accounts.MAX_TRANSFER_AED, step=5,
                                 key="tr_amount")
        if mode == "Send money":
            left_after = balance - amount
            st.markdown(f'<div class="pp-mini" style="margin:.2rem 0 .6rem 0">Balance <b>AED {balance:,.2f}</b> · '
                        f'you would have <b>AED {left_after:,.2f}</b> left</div>', unsafe_allow_html=True)
        note = st.text_input("What it's for", max_chars=accounts.MAX_NOTE_LEN, placeholder="e.g. pizza split",
                             key="tr_note")
        message = st.text_area("Message (optional)", max_chars=accounts.MAX_MESSAGE_LEN, key="tr_msg",
                               placeholder="Reroute reads this message when it checks the payment.")
        if st.button("Review", type="primary", width="stretch", key="tr_review_btn"):
            st.session_state.tr_review = {"to": to, "mode": mode, "amount": amount, "note": note,
                                          "message": message}
            st.rerun()

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
STATE_LABEL = {"CLEARED": "Sent (cleared)", "COMPLETED": "Sent (after OK)", "CANCELLED": "Cancelled",
               "PENDING_CONFIRMATION": "Needs your OK", "ESCALATED": "Team is checking", "FLAGGED": "New request",
               "CONFIRMED_CANCELLABLE": "Confirmed, can still cancel", "PENDING_GUARDIAN": "Waiting for guardian"}


def _receipt(t: dict) -> None:
    st.markdown(f'<div class="pp-card"><div class="pp-row"><span class="pp-to">Receipt · {e(t["txn_id"])}</span>'
                f'<span class="pp-amt">AED {t["amount_aed"]:,.2f}</span></div>'
                f'<div style="font-size:.88rem;line-height:1.7;margin-top:.4rem">'
                f'To: <b>{e(t["recipient_handle"])}</b>{" · " + e(t["to_name"]) if t.get("to_name") else ""}<br>'
                f'Date: {e(t["created_at"])}<br>For: {e(t["note"] or "-")}<br>'
                f'Status: <b>{e(STATE_LABEL.get(t["state"], t["state"]))}</b><br>'
                f'Why: {e(_reason(t) or "-")}</div>'
                '<div style="font-size:.72rem;color:var(--rr-muted);margin-top:.4rem">Pretend campus money.</div>'
                '</div>', unsafe_allow_html=True)


def activity_page() -> None:
    me, txns, _, mobile = _setup()
    _title("🧾 Activity", me, txns)
    c1, c2 = (st, st) if mobile else st.columns([1.3, 1])
    text = c1.text_input("Search by name or note", placeholder="e.g. canteen, pizza, Sara").strip().lower()
    present = [s for s in STATE_LABEL if any(t["state"] == s for t in txns)]
    states = c2.multiselect("Show", present, default=present, format_func=STATE_LABEL.get)
    items = [t for t in txns if t["state"] in states and (not text or text in " ".join(
        str(t.get(k) or "") for k in ("recipient_handle", "to_name", "note")).lower())]
    topups = accounts.topups_for(me["student_id"])
    if not txns and not topups:
        _empty("No activity yet. Payments you send or check with Reroute show up here.")
    elif not items:
        _empty("Nothing matches. Try another word, or show more statuses.")
    _grid(items, lambda t: _card(t, show_msg=False), mobile)
    if items:
        pick = st.selectbox("Open a receipt", [t["txn_id"] for t in items], index=None,
                            placeholder="Pick a payment to see its receipt",
                            format_func=lambda i: next(f"{t['recipient_handle']} · AED {t['amount_aed']:g} · {i}"
                                                       for t in items if t["txn_id"] == i))
        if pick:
            _receipt(next(t for t in items if t["txn_id"] == pick))
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["payment", "date", "to", "name", "amount_aed", "note", "status"])
        for t in items:
            w.writerow([t["txn_id"], t["created_at"], t["recipient_handle"], t.get("to_name") or "",
                        t["amount_aed"], t["note"] or "", STATE_LABEL.get(t["state"], t["state"])])
        st.download_button("Download these rows (CSV)", buf.getvalue(), file_name="pocketpay_activity.csv",
                           mime="text/csv", icon=":material/download:")
    if topups:
        st.markdown('<div class="pp-sec">💰 Test money added (beta)</div>', unsafe_allow_html=True)

        def render_topup(t: dict) -> None:
            by = "you" if t["added_by"] == "student" else "PocketPay staff"
            st.markdown(f'<div class="pp-card"><div class="pp-row"><span class="pp-to">Top-up by {by}</span>'
                        f'<span class="pp-amt" style="color:var(--rr-success)">+AED {t["amount_aed"]:,.0f}</span></div>'
                        f'<div style="font-size:.85rem;opacity:.75">{e(t["ts"])} · beta test money</div></div>',
                        unsafe_allow_html=True)

        _grid(topups, render_topup, mobile)
    _footer()


# ---------------------------------------------------------------- 👤 My profile
def profile_page() -> None:
    me, txns, _, mobile = _setup()
    theme.page_header("My profile", f"Student · {me['name']}")
    account_id = accounts.account_id_from_student(me["student_id"])
    acc = accounts.get(account_id) if account_id else None
    w = accounts.wallet_summary(me["student_id"])

    handle = accounts.handle_of_student(me["student_id"])
    with st.container(border=True):
        st.markdown(f"### {me['name']}")
        st.markdown(f"**School:** {me.get('school') or 'Demo High School'}  \n"
                    f"**Member since:** {acc['created_at'] if acc else 'demo account'}  \n"
                    f"**Age:** {me.get('age') if me.get('age') is not None else 'not set (ask staff to add it)'}  \n"
                    f"**Wallet ID:** {me['student_id']}")
        if handle:
            st.caption("Your pay-me handle (tap the icon to copy):")
            st.code(handle, language=None)
    figures = [("Balance", f"AED {w['balance']:,.2f}"), ("Payments", w["payments"]),
               ("Needs my OK", w["needs_ok"]), ("Team checking", w["with_staff"])]
    for row in ([figures[:2], figures[2:]] if mobile else [figures]):
        for col, (label, value) in zip(st.columns(len(row)), row):
            col.metric(label, value)
    if handle:   # the same campus record other students see in their trust chip (read-only)
        r = common.recipient_check(handle)
        if r.get("status") == "ok":
            st.markdown('<div class="pp-sec">Your campus record</div>', unsafe_allow_html=True)
            st.caption("This is what other students see when they pay you.")
            common.trust_chip(handle)
            rec = [("Trust score", f"{r['trust_score']}/100"), ("Payments received", r["completed_txns"]),
                   ("Account age", f"{r['account_age_days']} days"), ("Scam reports", r["scam_reports"])]
            for row in ([rec[:2], rec[2:]] if mobile else [rec]):
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
