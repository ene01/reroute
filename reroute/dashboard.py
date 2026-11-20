"""Staff pages (the "Staff" dropdown). Registered in app.py; staff only.

Each page has a desktop layout and a phone layout (common.is_mobile()).
  Triage           - run the agent on a payment and read its decision log
  Student inbox    - payments waiting for a student
  Staff queue      - payments escalated to staff
  Scam alerts      - warnings sent to all students
  Stats            - queue numbers, staff time saved, routing accuracy
  Manage accounts  - find a student account by name AND school, then manage it
"""

import html

import pandas as pd
import streamlit as st

import accounts
import agent
import common
import detector
import seed_data
import services as svc

MINUTES_PER_MANUAL_REVIEW = 4


# ---------------------------------------------------------------- shared setup for every staff page
def _setup(title: str) -> bool:
    """Staff-only guard, sidebar controls, phone CSS and the page title. Returns True on phones."""
    if st.session_state.get("role") != "staff":
        common.go("wallet")
    with st.sidebar:
        st.caption("Scam defense for the PocketPay campus wallet")
        common.layout_switch()
        model = agent.active_model()
        try:
            agent.llm.show(model)
            st.success(f"Laptop AI model ready (`{model}`)")
        except Exception:
            st.error(f"Ollama or `{model}` not available. Online providers (if set up) or code-only checks are used.")
        chain = [name for name, ready in agent.provider_status() if ready]
        missing = [name for name, ready in agent.provider_status() if not ready]
        st.caption("AI chain: " + " > ".join(chain) + ("" if not missing else
                   f". Not set up: {', '.join(missing)} (add keys to .env)"))
        rep = detector.report()
        if detector.available() and rep:
            st.caption(f"Reroute scam detector: trained on {rep['training_messages']} messages, "
                       f"{rep['demo_scenarios']['correct']}/{rep['demo_scenarios']['total']} on unseen demo messages")
        else:
            st.caption("Reroute scam detector: not trained (run `python train_detector.py`)")
        if st.button("▶️ Run agent on ALL flagged payments", width="stretch"):
            flagged = transactions().query("state == 'FLAGGED'")["txn_id"].tolist()
            bar = st.progress(0.0)
            for i, tid in enumerate(flagged, 1):
                bar.progress(i / max(len(flagged), 1), text=f"{tid} ({i}/{len(flagged)})")
                agent.process_transaction(tid)
            st.rerun()
        if st.button("🔄 Reset demo data", width="stretch", help="Student accounts are kept."):
            seed_data.seed()
            st.rerun()
        st.divider()
        st.markdown("**Who does what**\n\n"
                    "🛡️ **Pre-flight (code)** blocks prompt injection and broken data before the AI sees it\n\n"
                    "🧠 **Agent (LLM)** reads chat + slang, calls tools, gives a risk opinion\n\n"
                    "🔧 **Tools (code)** fetch context, verify recipient, change payment state\n\n"
                    "🛡️ **Guard (code)** makes the decision: CLEAR / HOLD / ESCALATE. It can never freeze a wallet.\n\n"
                    "🙋 **Human** confirms or cancels anything held")
    mobile = common.is_mobile()
    if mobile:
        st.markdown(common.MOBILE_CSS, unsafe_allow_html=True)
    st.header(title)
    return mobile


def transactions() -> pd.DataFrame:
    with svc.db() as con:
        return pd.read_sql_query(
            """SELECT t.txn_id, t.scenario, t.amount_aed, t.recipient_handle, t.state, t.expected_route,
                      a.route, a.risk_level, a.confidence
               FROM transactions t LEFT JOIN assessments a USING (txn_id) ORDER BY t.txn_id""", con)


def card_grid(items: list, render, mobile: bool) -> None:
    """Desktop: two cards per row. Phone: one column."""
    if mobile:
        for item in items:
            render(item)
        return
    for i in range(0, len(items), 2):
        for col, item in zip(st.columns(2), items[i:i + 2]):
            with col:
                render(item)


# ---------------------------------------------------------------- 🔍 Triage
def triage_page() -> None:
    mobile = _setup("🔍 Triage")
    df = transactions()
    labels = df.set_index("txn_id")["scenario"].to_dict()
    states = df.set_index("txn_id")["state"].to_dict()

    def label(t: str) -> str:
        return f"{common.STATE_BADGE[states[t]].split()[0]} {t} · {labels[t]}"

    if mobile:
        txn_id = st.selectbox("Flagged payment", df["txn_id"].tolist(), format_func=label)
        _payment_details(txn_id, mobile)
    else:
        left, right = st.columns([1, 2.2], gap="large")
        with left:
            st.markdown("**Flagged payments**")
            txn_id = st.radio("Pick a payment", df["txn_id"].tolist(), format_func=label,
                              label_visibility="collapsed")
        with right:
            _payment_details(txn_id, mobile)


def _payment_details(txn_id: str, mobile: bool) -> None:
    ctx = svc.get_transaction_context(txn_id)
    st.subheader(f"AED {ctx['amount_aed']:.0f} → {ctx['recipient_handle']}" if mobile
                 else f"{txn_id}: AED {ctx['amount_aed']:.0f} → {ctx['recipient_handle']}")
    st.markdown(f"**Student:** {ctx['student']['name']} · **Channel:** {ctx['channel']}  \n"
                f"**State:** {common.STATE_BADGE[ctx['state']]}")
    st.markdown("**Message received:**")
    st.info(ctx["message_text"])
    st.caption(f"Payment note: {ctx['note']} · Past payments to this recipient: {ctx['prior_txns_with_recipient']}")
    width = "stretch" if mobile else "content"
    if ctx["state"] == "FLAGGED":
        if st.button("🧠 Run agent", type="primary", width=width):
            with st.spinner(f"Agent investigating {txn_id} with {agent.active_model()}..."):
                agent.process_transaction(txn_id)
            st.rerun()
    else:
        s = agent.summary(txn_id)
        st.markdown(f"### {common.STATE_BADGE[s['state']]}")
        if s["assessment"]:
            a = s["assessment"]
            st.markdown(f"**What the student sees:** {a['student_explanation']}")
            st.caption(f"AI opinion: risk **{a['risk_level']}**, confidence **{a['confidence']}** · "
                       f"Guard: {a['guard_reasons']}")
        if st.button("Run agent again (tests loop prevention)", width=width):
            agent.process_transaction(txn_id)
            st.rerun()
    st.markdown("**Decision log**")
    common.show_log(txn_id, as_table=not mobile)


# ---------------------------------------------------------------- 📥 Student inbox
def inbox_page() -> None:
    mobile = _setup("📥 Student inbox")
    st.caption("Payments waiting for a student to confirm or cancel. Students see these in their own wallet.")
    items = common.pending("student")
    if not items:
        st.caption("Nothing waiting. Run the agent on some payments first.")

    def render(p: dict) -> None:
        with st.container(border=True):
            st.markdown(f"**{p['txn_id']}: AED {p['amount_aed']:.0f} to {p['recipient_handle']}**")
            st.warning(p["student_explanation"] or "Please confirm this payment.")
            st.caption(f"Why it was held: {p['guard_reasons']}")
            b1, b2 = st.columns(2)
            if b1.button("✅ Confirm (as student)", key=f"ok_{p['txn_id']}", width="stretch"):
                agent.resolve(p["txn_id"], approve=True, actor="student")
                st.rerun()
            if b2.button("⛔ Cancel (as student)", key=f"no_{p['txn_id']}", width="stretch"):
                agent.resolve(p["txn_id"], approve=False, actor="student")
                st.rerun()

    card_grid(items, render, mobile)


# ---------------------------------------------------------------- 🧑‍💼 Staff queue
def queue_page() -> None:
    mobile = _setup("🧑‍💼 Staff queue")
    st.caption("Money is held while staff review. Wallets stay usable for everything else.")
    items = common.pending("staff")
    if not items:
        st.caption("Nothing escalated right now.")

    def render(p: dict) -> None:
        with st.container(border=True):
            st.markdown(f"**{p['txn_id']}: AED {p['amount_aed']:.0f} to {p['recipient_handle']}**")
            st.info(p["message_text"] or "(no message attached)")
            st.caption(f"Guard reason: {p['guard_reasons']}")
            with st.expander("Full decision log"):
                common.show_log(p["txn_id"])
            b1, b2 = st.columns(2)
            if b1.button("Release payment", key=f"rel_{p['txn_id']}", width="stretch"):
                agent.resolve(p["txn_id"], approve=True, actor="staff")
                st.rerun()
            if b2.button("Confirm scam & alert students", key=f"scam_{p['txn_id']}", width="stretch"):
                agent.resolve(p["txn_id"], approve=False, actor="staff")
                st.rerun()

    card_grid(items, render, mobile)


# ---------------------------------------------------------------- 📢 Scam alerts
def alerts_page() -> None:
    _setup("📢 Scam alerts")
    with svc.db() as con:
        alerts = con.execute("SELECT * FROM scam_alerts ORDER BY id DESC").fetchall()
    if not alerts:
        st.caption("No alerts yet. They appear when staff confirm a scam in the Staff queue.")
    for a in alerts:
        st.error(f"📢 {a['summary']}  \n_{a['ts']}_")


# ---------------------------------------------------------------- 📊 Stats
def stats_page() -> None:
    mobile = _setup("📊 Stats")
    df = transactions()
    done = df[df["route"].notna()]
    auto = int((done["route"] == "CLEAR").sum())
    n_student, n_staff = len(common.pending("student")), len(common.pending("staff"))
    metrics = [("Processed", len(done)), ("Auto-cleared", auto), ("Waiting on student", n_student),
               ("Waiting on staff", n_staff), ("Student accounts", accounts.count())]
    rows = [metrics[:2], metrics[2:4], metrics[4:]] if mobile else [metrics]
    for row in rows:
        for col, (label, value) in zip(st.columns(len(row)), row):
            col.metric(label, value)
    if len(done):
        held_by_student = int((done["route"] == "HOLD_FOR_CONFIRMATION").sum())
        saved = (auto + held_by_student) * MINUTES_PER_MANUAL_REVIEW
        st.markdown(f"Staff only review **{len(done) - auto - held_by_student} of {len(done)}** payments. "
                    f"That saves about **{saved} min** here. At the brief's 200 flagged payments a day, the same "
                    f"ratio saves about **{saved / len(done) * 200 / 60:.1f} staff hours per day**.")
        scored = done[done["expected_route"].notna()]   # live student-to-student payments have no expected answer
        match = (scored["route"] == scored["expected_route"]).sum()
        st.markdown(f"**Routing accuracy vs expected (demo scenarios):** {match}/{len(scored)}")
    cols = ["txn_id", "amount_aed", "state", "route", "expected_route"] if mobile else list(df.columns)
    st.dataframe(df[cols], hide_index=True, width="stretch")


# ---------------------------------------------------------------- 👥 Manage accounts
def accounts_page() -> None:
    mobile = _setup("👥 Manage accounts")
    st.caption(f"{accounts.count()} student account(s) at {len(accounts.schools())} school(s). To find one, "
               "enter **both** the student's name and their school. Part of each is fine, and the School box "
               "suggests registered schools as you type.")

    school_list = accounts.schools()
    with st.form("account_search"):
        c1, c2 = (st, st) if mobile else st.columns(2)
        name = c1.text_input("Student name")
        # Dropdown of every registered school. Typing filters the list (suggestions), and you can
        # also keep what you typed, e.g. part of a school's name.
        school = c2.selectbox("School", school_list, index=None, accept_new_options=True,
                              placeholder="Pick a school or start typing…" if school_list
                              else "No schools yet. Type a name…")
        searched = st.form_submit_button("🔎 Search", type="primary")
    if searched:
        school = school or ""
        if not name.strip() or not school.strip():
            st.error("Enter both the name and the school to search.")
            st.session_state.pop("acct_query", None)
        else:
            st.session_state.acct_query = (name, school)
            st.session_state.pop("acct_selected", None)

    query = st.session_state.get("acct_query")
    if not query:
        return
    results = accounts.search(*query)
    st.markdown(f"**{len(results)} result(s)** for name “{html.escape(query[0])}” at “{html.escape(query[1])}”")
    if not results:
        st.info("No account matches that name and school.")

    def render(acc: dict) -> None:
        w = accounts.wallet_summary(acc["student_id"])
        with st.container(border=True):
            st.markdown(f"**{acc['name']}** · {acc['school']}")
            st.caption(f"Joined {acc['created_at']} · Balance AED {w['balance']:,.2f} · "
                       f"{w['needs_ok']} waiting on them · {w['with_staff']} with staff")
            if st.button("Manage", key=f"manage_{acc['account_id']}", width="stretch"):
                st.session_state.acct_selected = acc["account_id"]
                st.rerun()

    card_grid(results, render, mobile)

    selected = st.session_state.get("acct_selected")
    if selected and any(a["account_id"] == selected for a in results):
        st.divider()
        _manage_account(selected, mobile)


def _manage_account(account_id: int, mobile: bool) -> None:
    acc = accounts.get(account_id)
    if not acc:
        st.warning("That account no longer exists.")
        return
    w = accounts.wallet_summary(acc["student_id"])
    st.subheader(f"{acc['name']} · {acc['school']}")
    figures = [("Balance", f"AED {w['balance']:,.0f}"), ("Payments", w["payments"]),
               ("Waiting on them", w["needs_ok"]), ("With staff", w["with_staff"])]
    for row in ([figures[:2], figures[2:]] if mobile else [figures]):
        for col, (label, value) in zip(st.columns(len(row)), row):
            col.metric(label, value)
    st.caption(f"Account ID {acc['account_id']} · wallet {acc['student_id']} · joined {acc['created_at']}")

    if st.button("📱 Open their wallet", width="stretch" if mobile else "content"):
        st.session_state.student_id = acc["student_id"]
        common.go("wallet")

    if accounts.BETA_TOPUPS:
        with st.expander("💰 Add test money (beta)"):
            st.caption(f"Pretend money for beta testing, up to AED {accounts.MAX_TOPUP_AED:,} per top-up. "
                       "It shows in the student's Activity as added by staff.")
            with st.form(f"topup_{account_id}"):
                amount = st.number_input("Amount (AED)", min_value=1, max_value=accounts.MAX_TOPUP_AED,
                                         value=100, step=10)
                if st.form_submit_button("Add to their wallet"):
                    balance, problem = accounts.add_funds(acc["student_id"], amount, "staff")
                    if problem:
                        st.error(problem)
                    else:
                        st.toast(f"Added AED {amount:,} to {acc['name']}. New balance AED {balance:,.2f}", icon="💰")
                        st.rerun()

    with st.expander("🔑 Reset their password"):
        with st.form(f"pw_reset_{account_id}"):
            new = st.text_input("New password", type="password")
            confirm = st.text_input("Type the new password again", type="password")
            if st.form_submit_button("Set new password"):
                problems = accounts.set_password(account_id, new, confirm)
                for p in problems:
                    st.error(p)
                if not problems:
                    st.success(f"Password changed. Tell {acc['name']} their new password in person.")

    with st.expander("♻️ Reset their wallet"):
        st.caption("Puts the balance back to the start and replaces their payments with fresh sample requests.")
        if st.checkbox("Yes, reset this wallet", key=f"rw_ok_{account_id}"):
            if st.button("Reset wallet", key=f"rw_{account_id}"):
                accounts.reset_wallet(account_id)
                st.toast(f"{acc['name']}'s wallet was reset.", icon="♻️")
                st.rerun()

    with st.expander("🗑️ Delete account"):
        st.caption("Removes the login, the wallet and every payment in it. This can't be undone.")
        typed = st.text_input(f"Type “{acc['name']}” to confirm", key=f"del_confirm_{account_id}")
        if st.button("Delete account", key=f"del_{account_id}", type="primary",
                     disabled=typed.strip() != acc["name"]):
            accounts.delete_account(account_id)
            st.session_state.pop("acct_selected", None)
            st.toast(f"Deleted {acc['name']}'s account.", icon="🗑️")
            st.rerun()
