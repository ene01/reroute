"""Reroute - Streamlit entry point (scam defense for the PocketPay campus wallet).

    python -m streamlit run reroute/app.py

Login screen (common.require_login): students log in or sign up with name + school + password;
staff use the staff password.

Navigation has two sections. On desktop it sits in the side panel (which opens and closes, with
the logo at its top left); on phones it is a dropdown menu at the top:
  Overview (home.py)     - Command Center: hero, live numbers, the 4 steps, quick links (default page)
  Staff   (dashboard.py) - Triage, Student inbox, Staff queue, Scam alerts, Stats, Manage accounts
  Student (student.py)   - Wallet, Needs my OK, Team checking, New requests, Activity, My profile
  Learn   (learn.py)     - About, How it works, Student guide, Staff guide, Safety and privacy, FAQ
Students only get the Student and Learn sections (without the Staff guide). Every staff page also checks the role itself and sends
anyone who isn't staff to the student wallet.
"""

from pathlib import Path

import streamlit as st

import accounts
import agent
import common
import dashboard
import home
import intro
import learn
import seed_data
import services as svc
import student
import theme

st.set_page_config(page_title="ReRoute · Scam Defense", page_icon=str(Path(__file__).resolve().parent / "assets" / "icon.svg"), layout="wide", menu_items={})

# Hide Streamlit's own top-right menu (Print, Record a screencast, Settings) and the Deploy button.
# Also set by client.toolbarMode = "minimal" (.streamlit/config.toml and start_public.ps1).
st.markdown("""<style>
#MainMenu, [data-testid="stMainMenu"], [data-testid="stAppDeployButton"], .stAppDeployButton,
[data-testid="stToolbarActions"] { display: none !important; }
</style>""", unsafe_allow_html=True)

theme.apply()   # dark "defense grid" look for every page, including the login screen

ASSETS = Path(__file__).resolve().parent / "assets"
role = st.session_state.get("role")
HOME = [("home", home.command_center_page, "Command Center", ":material/space_dashboard:")]
STAFF = [
    ("triage", dashboard.triage_page, "Triage", ":material/radar:"),
    ("inbox", dashboard.inbox_page, "Student inbox", ":material/inbox:"),
    ("queue", dashboard.queue_page, "Staff queue", ":material/gavel:"),
    ("guardian", dashboard.guardian_page, "Guardian approvals", ":material/family_restroom:"),
    ("lab", dashboard.lab_page, "Scenario Lab", ":material/science:"),
    ("safety_cases", dashboard.safety_page, "Safety cases", ":material/verified_user:"),
    ("loadtest", dashboard.loadtest_page, "Load test", ":material/speed:"),
    ("audit", dashboard.audit_page, "Audit trail", ":material/history:"),
    ("alerts", dashboard.alerts_page, "Scam alerts", ":material/campaign:"),
    ("stats", dashboard.stats_page, "Stats", ":material/monitoring:"),
    ("accounts", dashboard.accounts_page, "Manage accounts", ":material/group:"),
]
STUDENT = [
    ("wallet", student.wallet_page, "Wallet", ":material/account_balance_wallet:"),
    ("transfer", student.transfer_page, "Send and request", ":material/swap_horiz:"),
    ("needs_ok", student.needs_ok_page, "Needs my OK", ":material/pending_actions:"),
    ("checking", student.checking_page, "Team checking", ":material/policy:"),
    ("requests", student.requests_page, "New requests", ":material/mark_email_unread:"),
    ("activity", student.activity_page, "Activity", ":material/receipt_long:"),
    ("profile", student.profile_page, "My profile", ":material/person:"),
]
LEARN = [
    ("about", learn.about_page, "About", ":material/info:"),
    ("how_it_works", learn.how_page, "How it works", ":material/route:"),
    ("student_guide", learn.student_guide_page, "Student guide", ":material/school:"),
    ("staff_guide", learn.staff_guide_page, "Staff guide", ":material/badge:"),
    ("safety", learn.safety_page, "Safety and privacy", ":material/shield:"),
    ("faq", learn.faq_page, "FAQ", ":material/help:"),
]
default_key = "home"


def make(entries):
    pages = []
    for key, fn, title, icon in entries:
        page = st.Page(fn, title=title, icon=icon, url_path=key, default=(key == default_key))
        common.PAGES[key] = page
        pages.append(page)
    return pages


common.PAGES.clear()
learn_pages = [p for p in LEARN if role != "student" or p[0] != "staff_guide"]   # staff guide: staff only
sections = {"Overview": make(HOME)}
sections.update({"Student": make(STUDENT)} if role == "student" else {"Staff": make(STAFF), "Student": make(STUDENT)})
sections["Learn"] = make(learn_pages)
# Desktop: the Staff / Student menu lives in the side panel, which can be opened and closed.
# Phone: it stays as a dropdown at the top (a side panel is awkward on a small screen).
# Pages are registered BEFORE the login check so a link like /needs_ok survives logging in.
nav_position = ("top" if common.is_mobile() else "sidebar") if role else "hidden"
page = st.navigation(sections, position=nav_position, expanded=True)   # show every page, no "View more"

# Opening intro (on open / refresh, and again after logging in or out) and page-to-page transitions.
if st.session_state.get("intro_role", None) != (role or ""):
    if "intro_role" in st.session_state:           # the role changed this run: someone logged in or out
        st.session_state.intro_epoch = st.session_state.get("intro_epoch", 0) + 1
    st.session_state.intro_role = role or ""
PUBLIC_VIEWS = ["home", "how", "student", "staff"]       # the screens before logging in
if role:
    order = [p.url_path for group in sections.values() for p in group]
    current = page.url_path
else:
    order, current = PUBLIC_VIEWS, st.session_state.get("public_view", "home")
intro.play(st.session_state.get("intro_epoch", 0), f"{role or 'public'}:{current}",
           order.index(current) if current in order else 0)

# Logo: top left of the side panel. When the side panel is closed, Streamlit shows the small
# icon in the page header instead; the CSS below moves that icon to the top right.
st.logo(str(ASSETS / "logo.svg"), size="large", icon_image=str(ASSETS / "icon.svg"))
st.markdown("""<style>
[data-testid="stHeaderLogo"] { position: fixed; top: 0.75rem; right: 1.25rem; z-index: 999990; }
/* The menu always shows every page (expanded=True), so hide the "View more" / "View less" toggle. */
[data-testid="stSidebarNavViewButton"] { display: none !important; }
</style>""", unsafe_allow_html=True)

if not svc.DB_PATH.exists():
    seed_data.seed()
accounts.ensure_table()
with svc.db() as _con:
    seed_data.backfill_ages(_con)   # older databases: the demo students get their ages (guardian rule)

common.flush_cookie()   # write any pending "remember me" cookie change (login / logout)
common.require_login()
common.keep_layout_choice()
agent.release_due()        # HS-1.4: release confirmed payments whose 2-minute cancel window has ended
common.sidebar_account()   # "Logged in as ..." + Log out, right under the menu in the side panel
page.run()
theme.sidebar_status()     # AGENT / GUARDS / AI CHAIN / WALLET FREEZES box, at the bottom of the side panel
