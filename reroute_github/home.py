"""Command Center - the first page everyone sees after logging in.

A hero with the Reroute promise, live numbers (staff: every payment; a student: their own wallet),
the four steps every flagged payment goes through, and quick links. Read only: it never changes data.
"""

import base64
from pathlib import Path

import streamlit as st

import common
import theme

ICON = Path(__file__).resolve().parent / "assets" / "icon.svg"

STEPS = [
    ("&#9888;", "1 · Preflight guards", "Prompt injection and broken data are blocked before the AI ever sees them."),
    ("&#9881;", "2 · Registry + ML", "Campus registry lookup and Reroute's own trained detector score every message."),
    ("&#10022;", "3 · AI reasoning", "The model reads slang, urgency and impersonation, and says when it isn't sure."),
    ("&#10003;", "4 · Safe routing", "CLEAR, HOLD or ESCALATE. No block action exists, so nothing can lock a wallet."),
]


def _shield() -> str:
    svg = ICON.read_text(encoding="utf-8").replace(' width="48" height="48"', "")   # let it scale up
    data = base64.b64encode(svg.encode()).decode()
    return f'<img class="shield" src="data:image/svg+xml;base64,{data}" alt="">'


def command_center_page() -> None:
    mobile = common.is_mobile()
    staff = st.session_state.get("role") == "staff"
    theme.status_bar()

    st.markdown(
        f'<div class="rr-hero"><div class="rr-eyebrow">PocketPay · Campus wallet defense</div>'
        f'<h1>Stop scams.<br><span class="rr-grad">Never freeze<br>a wallet.</span></h1>'
        f'<p>Reroute reads the chat behind every flagged payment, runs fixed safety guards and a trained scam '
        f'detector, then routes it to exactly one of three outcomes.</p>{_shield()}</div>',
        unsafe_allow_html=True)

    row = st.container(horizontal=True)
    width = "stretch" if mobile else "content"
    if staff:
        if row.button("Open the staff queue", type="primary", width=width, icon=":material/gavel:"):
            common.go("queue")
        if row.button("Run triage", width=width, icon=":material/radar:"):
            common.go("triage")
    else:
        if row.button("Open my requests", type="primary", width=width, icon=":material/inbox:"):
            common.go("needs_ok")
        if row.button("Send or request", width=width, icon=":material/swap_horiz:"):
            common.go("transfer")
    st.write("")

    c = theme.counts(None if staff else st.session_state.get("student_id"))
    if staff:
        right, scored = theme.accuracy()
        first = theme.stat_card("Benchmark accuracy", f"{round(100 * right / scored)}%" if scored else "-",
                                f"{right}/{scored} scenarios" if scored else "run the agent to score")
    else:
        first = theme.stat_card("New requests", c["new"], "not checked yet")
    theme.columns_html([
        first,
        theme.stat_card("Cleared", c["cleared"], "auto-approved or confirmed", "c-green"),
        theme.stat_card("Held for student", c["held"], "awaiting confirmation", "c-amber"),
        theme.stat_card("Escalated", c["escalated"], "staff review", "c-red"),
    ], 4, mobile)

    theme.columns_html([theme.feature_card(i, t, b) for i, t, b in STEPS], 4, mobile)

    st.write("")
    links = ([("wallet", "Student wallet", "Balance, activity and beta top-ups.", ":material/account_balance_wallet:"),
              ("queue", "Staff review queue", "Escalated payments waiting on a person.", ":material/gavel:"),
              ("alerts", "Campus scam alerts", "Warnings sent to every student.", ":material/campaign:")]
             if staff else
             [("wallet", "My wallet", "Balance, scam alerts and what needs you.", ":material/account_balance_wallet:"),
              ("activity", "Activity", "Payments that went through or were cancelled.", ":material/receipt_long:"),
              ("how_it_works", "How it works", "The path every flagged payment takes.", ":material/route:")])
    cols = st.columns(1 if mobile else 3)
    for i, (key, title, sub, icon) in enumerate(links):
        with cols[0 if mobile else i]:
            if key in common.PAGES:
                st.page_link(common.PAGES[key], label=f"{title}  →", icon=icon)
                st.caption(sub)
