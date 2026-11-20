"""Reroute - Streamlit entry point (scam defense for the PocketPay campus wallet).

    python -m streamlit run reroute/app.py

Login screen (common.require_login): students log in or sign up with name + school + password;
staff use the staff password.

Navigation has two sections. On desktop it sits in the side panel (which opens and closes, with
the logo at its top left); on phones it is a dropdown menu at the top:
  Staff   (dashboard.py) - Triage, Student inbox, Staff queue, Scam alerts, Stats, Manage accounts
  Student (student.py)   - Wallet, Needs my OK, Team checking, New requests, Activity, My profile
  Learn   (learn.py)     - About, How it works, Student guide, Staff guide, Safety and privacy, FAQ
Students only get the Student and Learn sections (without the Staff guide). Every staff page also checks the role itself and sends
anyone who isn't staff to the student wallet.
"""

from pathlib import Path

import streamlit as st

import accounts
import common
import dashboard
import learn
import seed_data
import services as svc
import student

st.set_page_config(page_title="Reroute · Scam Defense", page_icon="🛡️", layout="wide", menu_items={})

# Hide Streamlit's own top-right menu (Print, Record a screencast, Settings) and the Deploy button.
# Also set by client.toolbarMode = "minimal" (.streamlit/config.toml and start_public.ps1).
st.markdown("""<style>
#MainMenu, [data-testid="stMainMenu"], [data-testid="stAppDeployButton"], .stAppDeployButton,
[data-testid="stToolbarActions"] { display: none !important; }
</style>""", unsafe_allow_html=True)

ASSETS = Path(__file__).resolve().parent / "assets"
role = st.session_state.get("role")
STAFF = [
    ("triage", dashboard.triage_page, "Triage", "🔍"),
    ("inbox", dashboard.inbox_page, "Student inbox", "📥"),
    ("queue", dashboard.queue_page, "Staff queue", "🧑‍💼"),
    ("alerts", dashboard.alerts_page, "Scam alerts", "📢"),
    ("stats", dashboard.stats_page, "Stats", "📊"),
    ("accounts", dashboard.accounts_page, "Manage accounts", "👥"),
]
STUDENT = [
    ("wallet", student.wallet_page, "Wallet", "🏠"),
    ("transfer", student.transfer_page, "Send and request", "💸"),
    ("needs_ok", student.needs_ok_page, "Needs my OK", "⚠️"),
    ("checking", student.checking_page, "Team checking", "🔎"),
    ("requests", student.requests_page, "New requests", "📨"),
    ("activity", student.activity_page, "Activity", "🧾"),
    ("profile", student.profile_page, "My profile", "👤"),
]
LEARN = [
    ("about", learn.about_page, "About", ":material/info:"),
    ("how_it_works", learn.how_page, "How it works", ":material/route:"),
    ("student_guide", learn.student_guide_page, "Student guide", ":material/school:"),
    ("staff_guide", learn.staff_guide_page, "Staff guide", ":material/badge:"),
    ("safety", learn.safety_page, "Safety and privacy", ":material/shield:"),
    ("faq", learn.faq_page, "FAQ", ":material/help:"),
]
default_key = "wallet" if role == "student" else "triage"


def make(entries):
    pages = []
    for key, fn, title, icon in entries:
        page = st.Page(fn, title=title, icon=icon, url_path=key, default=(key == default_key))
        common.PAGES[key] = page
        pages.append(page)
    return pages


common.PAGES.clear()
learn_pages = [p for p in LEARN if role != "student" or p[0] != "staff_guide"]   # staff guide: staff only
sections = ({"Student": make(STUDENT)} if role == "student" else {"Staff": make(STAFF), "Student": make(STUDENT)})
sections["Learn"] = make(learn_pages)
# Desktop: the Staff / Student menu lives in the side panel, which can be opened and closed.
# Phone: it stays as a dropdown at the top (a side panel is awkward on a small screen).
# Pages are registered BEFORE the login check so a link like /needs_ok survives logging in.
nav_position = ("top" if common.is_mobile() else "sidebar") if role else "hidden"
page = st.navigation(sections, position=nav_position, expanded=True)   # show every page, no "View more"

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

common.flush_cookie()   # write any pending "remember me" cookie change (login / logout)
common.require_login()
common.keep_layout_choice()
common.sidebar_account()   # "Logged in as ..." + Log out, right under the menu in the side panel
page.run()
