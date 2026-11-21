"""Reroute look and feel: the dark "defense grid" theme shared by every page.

Only presentation lives here (CSS, the top status bar, the side-panel status box, page headers and
card HTML). It reads a few counts from the database to show them, but never changes anything:
all the decisions stay in agent.py / guards.py / services.py.

    theme.apply()                 once per run (app.py): fonts, colours, glow, cards, buttons
    theme.status_bar()            the thin "DEFENSE GRID LIVE" bar at the top of every page
    theme.sidebar_status()        the AGENT / GUARDS / AI / WALLET FREEZES box in the side panel
    theme.page_header(...)        small mono label + big title + optional lead line
"""

import html
import re

import streamlit as st

import services as svc

e = html.escape

# Colours (from the design): navy background, cyan primary, blue glow, pink accent.
BG = "#050b16"
CARD = "rgba(10, 20, 36, .72)"
BORDER = "rgba(0, 224, 234, .14)"
PRIMARY = "#00e0ea"
GLOW = "#11a8ff"
ACCENT = "#ee50e6"
SUCCESS = "#51e57e"
WARNING = "#ffbd34"
DANGER = "#fd374a"
MUTED = "#879ca8"
TEXT = "#e4f1f5"
GRADIENT = f"linear-gradient(120deg, {PRIMARY}, {GLOW} 55%, {ACCENT})"

CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Chakra+Petch:wght@500;600;700&family=JetBrains+Mono:wght@400;600&family=Manrope:wght@400;500;600;700;800&display=swap');

:root {{
  --rr-bg: {BG}; --rr-card: {CARD}; --rr-border: {BORDER}; --rr-primary: {PRIMARY}; --rr-glow: {GLOW};
  --rr-accent: {ACCENT}; --rr-success: {SUCCESS}; --rr-warning: {WARNING}; --rr-danger: {DANGER};
  --rr-muted: {MUTED}; --rr-text: {TEXT}; --rr-gradient: {GRADIENT};
  --rr-head: 'Chakra Petch', ui-sans-serif, sans-serif; --rr-body: 'Manrope', ui-sans-serif, sans-serif;
  --rr-mono: 'JetBrains Mono', ui-monospace, monospace;
}}

/* ---------- page background: soft blue and pink glows over a faint grid ---------- */
.stApp {{
  background-color: var(--rr-bg) !important;
  background-image:
    radial-gradient(960px 640px at 85% -10%, rgba(17, 168, 255, .16), transparent 60%),
    radial-gradient(800px 480px at -10% 110%, rgba(238, 80, 230, .10), transparent 60%),
    linear-gradient(rgba(0, 224, 234, .045) 1px, transparent 1px),
    linear-gradient(90deg, rgba(0, 224, 234, .045) 1px, transparent 1px) !important;
  background-size: auto, auto, 48px 48px, 48px 48px !important;
  background-attachment: fixed !important;
  color: var(--rr-text);
}}
.stApp, .stApp p, .stApp li, .stApp label, .stApp input, .stApp textarea, .stApp button,
.stApp [data-testid="stMarkdownContainer"] {{ font-family: var(--rr-body); }}
.stApp h1, .stApp h2, .stApp h3, .stApp h4 {{
  font-family: var(--rr-head) !important; letter-spacing: -.01em; color: var(--rr-text);
}}
.stApp h1 {{ font-weight: 700 !important; }}
.stApp code {{ font-family: var(--rr-mono); color: var(--rr-primary); background: rgba(0, 224, 234, .08); }}
.stApp [data-testid="stCaptionContainer"], .stApp small {{ color: var(--rr-muted) !important; }}
.stApp a {{ color: var(--rr-primary); }}
.stApp hr {{ border-color: var(--rr-border) !important; }}

[data-testid="stHeader"] {{ background: rgba(5, 11, 22, .55) !important; backdrop-filter: blur(14px); }}
[data-testid="stMainBlockContainer"], .block-container {{ padding-top: 3.2rem; }}

/* ---------- side panel ---------- */
[data-testid="stSidebar"] {{
  background: rgba(4, 11, 24, .88) !important; backdrop-filter: blur(20px);
  border-right: 1px solid var(--rr-border) !important;
}}
[data-testid="stSidebar"] > div {{ background: transparent !important; }}
[data-testid="stNavSectionHeader"], [data-testid="stNavSectionHeader"] *:not([data-testid="stIconMaterial"]) {{
  font-family: var(--rr-mono) !important; font-size: .68rem !important; letter-spacing: .18em;
  text-transform: uppercase; color: var(--rr-muted) !important;
}}
[data-testid="stSidebarNavLink"] {{
  border-radius: 12px !important; margin: 2px 0; padding-left: .75rem !important;
  border: 1px solid transparent; transition: background .15s, border-color .15s;
}}
[data-testid="stSidebarNavLink"] span:not([data-testid="stIconMaterial"]) {{
  font-family: var(--rr-body) !important; font-weight: 600; color: #c9d8e0; }}
[data-testid="stSidebarNavLink"]:hover {{ background: rgba(0, 224, 234, .06) !important; }}
[data-testid="stSidebarNavLink"][aria-current="page"] {{
  background: linear-gradient(90deg, rgba(0, 224, 234, .16), rgba(0, 224, 234, .04)) !important;
  border-color: rgba(0, 224, 234, .22); box-shadow: inset 3px 0 0 var(--rr-primary);
}}
[data-testid="stSidebarNavLink"][aria-current="page"] span:not([data-testid="stIconMaterial"]) {{ color: var(--rr-text) !important; }}
[data-testid="stSidebarNavLink"] [data-testid="stIconMaterial"] {{ color: var(--rr-primary) !important; }}
[data-testid="stSidebarNavSeparator"] {{ border-color: var(--rr-border) !important; }}

/* ---------- buttons: gradient + glow for primary, cyan outline for the rest ---------- */
.stApp button[kind="primary"], .stApp button[data-testid="stBaseButton-primary"],
.stApp button[kind="primaryFormSubmit"], .stApp button[data-testid="stBaseButton-primaryFormSubmit"] {{
  background: var(--rr-gradient) !important; color: #04101c !important; border: 0 !important;
  border-radius: 12px !important; font-weight: 700 !important;
  box-shadow: 0 0 24px -6px rgba(0, 224, 234, .7); transition: transform .15s, filter .15s;
}}
.stApp button[kind="primary"] p, .stApp button[data-testid="stBaseButton-primary"] p,
.stApp button[data-testid="stBaseButton-primaryFormSubmit"] p {{ color: #04101c !important; font-weight: 700; }}
.stApp button[kind="primary"]:hover, .stApp button[data-testid="stBaseButton-primary"]:hover,
.stApp button[data-testid="stBaseButton-primaryFormSubmit"]:hover {{ transform: translateY(-1px); filter: brightness(1.1); }}
.stApp button[data-testid="stBaseButton-secondary"], .stApp button[data-testid="stBaseButton-secondaryFormSubmit"],
.stApp button[kind="secondary"], .stApp button[kind="secondaryFormSubmit"] {{
  background: rgba(0, 224, 234, .05) !important; border: 1px solid rgba(0, 224, 234, .38) !important;
  color: var(--rr-primary) !important; border-radius: 12px !important; font-weight: 700 !important;
}}
.stApp button[data-testid="stBaseButton-secondary"] p,
.stApp button[data-testid="stBaseButton-secondaryFormSubmit"] p {{ color: var(--rr-primary) !important; font-weight: 700; }}
.stApp button[data-testid="stBaseButton-secondary"]:hover,
.stApp button[data-testid="stBaseButton-secondaryFormSubmit"]:hover {{ background: rgba(0, 224, 234, .14) !important; }}
.stApp button:disabled {{ opacity: .4; box-shadow: none !important; }}

/* page links (e.g. on the wallet) look like the nav */
[data-testid="stPageLink"] a {{
  border: 1px solid var(--rr-border); border-radius: 12px; background: var(--rr-card); margin: 3px 0;
}}
[data-testid="stPageLink"] a:hover {{ border-color: rgba(0, 224, 234, .45); background: rgba(0, 224, 234, .06); }}

/* ---------- cards: bordered containers, expanders, forms, metrics ---------- */
[data-testid="stVerticalBlockBorderWrapper"]:has(> div > [data-testid="stVerticalBlock"]),
[data-testid="stVerticalBlock"][class*="border"], [data-testid="stForm"], [data-testid="stExpander"] details {{
  background: var(--rr-card) !important; border: 1px solid var(--rr-border) !important;
  border-radius: 16px !important; backdrop-filter: blur(10px);
}}
[data-testid="stExpander"] summary:hover {{ color: var(--rr-primary); }}
[data-testid="stMetric"] {{
  background: var(--rr-card); border: 1px solid var(--rr-border); border-radius: 16px; padding: 1rem 1.1rem;
}}
[data-testid="stMetricLabel"], [data-testid="stMetricLabel"] * {{
  font-family: var(--rr-mono) !important; font-size: .7rem !important; letter-spacing: .14em;
  text-transform: uppercase; color: var(--rr-muted) !important;
}}
[data-testid="stMetricValue"] {{ font-family: var(--rr-head) !important; color: var(--rr-primary) !important; }}

/* ---------- inputs ---------- */
.stApp [data-baseweb="input"], .stApp [data-baseweb="select"] > div, .stApp [data-baseweb="textarea"],
.stApp [data-baseweb="base-input"] {{
  background: rgba(3, 9, 18, .7) !important; border-color: rgba(0, 224, 234, .2) !important; border-radius: 12px !important;
}}
.stApp [data-baseweb="input"]:focus-within, .stApp [data-baseweb="select"] > div:focus-within {{
  border-color: var(--rr-primary) !important; box-shadow: 0 0 0 1px var(--rr-primary), 0 0 18px -6px var(--rr-primary);
}}

/* ---------- tabs as pills (like the "Needs action / All / Cleared" filter) ---------- */
[data-testid="stTabs"] [role="tablist"] {{ gap: .45rem; border: 0 !important; box-shadow: none !important;
  background: none !important; padding-bottom: .5rem; }}
[data-testid="stTabs"] [role="tablist"]::before, [data-testid="stTabs"] [role="tablist"]::after,
[data-testid="stTabs"] [role="tab"]::before, [data-testid="stTabs"] [role="tab"]::after {{ display: none !important; }}
[data-testid="stTabs"] [role="tab"] {{
  border: 1px solid var(--rr-border) !important; border-radius: 999px !important; padding: .1rem 1.05rem !important;
  height: 2.2rem !important; background: rgba(3, 9, 18, .5) !important; box-shadow: none;
}}
[data-testid="stTabs"] [role="tab"][aria-selected="true"] {{
  border-color: var(--rr-primary) !important; background: rgba(0, 224, 234, .1) !important;
  box-shadow: 0 0 16px -6px var(--rr-primary);
}}
[data-testid="stTabs"] [role="tab"] p {{ font-weight: 600; }}
[data-testid="stTabs"] [role="tab"][aria-selected="true"] p {{ color: var(--rr-primary) !important; }}

/* pills and segmented controls */
.stApp button[data-testid="stBaseButton-pills"], .stApp button[data-testid="stBaseButton-segmented_control"] {{
  border-color: var(--rr-border) !important; background: rgba(3, 9, 18, .5) !important; border-radius: 10px !important;
}}
.stApp button[data-testid="stBaseButton-pillsActive"],
.stApp button[data-testid="stBaseButton-segmented_controlActive"] {{
  border-color: var(--rr-primary) !important; background: rgba(0, 224, 234, .12) !important; color: var(--rr-primary) !important;
}}

/* alerts and dataframes */
[data-testid="stAlert"] > div {{ border-radius: 14px !important; }}
[data-testid="stDataFrame"] {{ border: 1px solid var(--rr-border); border-radius: 14px; overflow: hidden; }}

/* ---------- Reroute pieces (status bar, headers, stat and feature cards) ---------- */
.rr-bar {{
  display: flex; flex-wrap: wrap; gap: .4rem 1.4rem; align-items: center; margin: -1.2rem 0 1.4rem 0;
  padding: .55rem .9rem; border: 1px solid var(--rr-border); border-radius: 12px; background: rgba(4, 11, 24, .6);
  font-family: var(--rr-mono); font-size: .7rem; letter-spacing: .12em; text-transform: uppercase; color: var(--rr-muted);
}}
.rr-bar b {{ font-weight: 600; }}
.rr-bar .live::before {{
  content: ""; display: inline-block; width: 7px; height: 7px; border-radius: 50%; margin-right: .5rem;
  background: var(--rr-success); box-shadow: 0 0 10px var(--rr-success); animation: rr-pulse 2s infinite;
}}
@keyframes rr-pulse {{ 50% {{ opacity: .35; }} }}
.rr-eyebrow {{
  font-family: var(--rr-mono); font-size: .72rem; letter-spacing: .28em; text-transform: uppercase;
  color: var(--rr-primary); margin-bottom: .2rem;
}}
.rr-title {{ font-family: var(--rr-head); font-weight: 700; font-size: 2.6rem; line-height: 1.1; margin: 0 0 .4rem 0; }}
.rr-lead {{ color: var(--rr-muted); font-size: 1rem; max-width: 760px; margin-bottom: 1.1rem; }}
.rr-grad {{ background: var(--rr-gradient); -webkit-background-clip: text; background-clip: text; color: transparent; }}

.rr-hero {{
  position: relative; overflow: hidden; border: 1px solid var(--rr-border); border-radius: 22px;
  padding: 2.4rem 2.4rem 2.2rem; background: linear-gradient(135deg, rgba(10, 22, 40, .85), rgba(5, 12, 24, .7));
  margin-bottom: 1.2rem;
}}
.rr-hero h1 {{ font-size: 3.4rem !important; line-height: 1.08 !important; margin: .3rem 0 1rem 0 !important; padding: 0 !important; }}
.rr-hero p {{ color: #b7c7d0; max-width: 470px; font-size: 1.02rem; line-height: 1.55; }}
.rr-hero .shield {{ position: absolute; right: 3.5rem; top: 50%; transform: translateY(-50%);
  width: 180px !important; height: 180px !important; max-width: none !important;
  filter: drop-shadow(0 0 28px rgba(0, 224, 234, .65)); }}
@media (max-width: 1250px) {{ .rr-hero .shield {{ width: 120px !important; height: 120px !important; right: 2rem; opacity: .8; }} }}
@media (max-width: 900px) {{ .rr-hero .shield {{ display: none; }} .rr-hero h1 {{ font-size: 2.3rem !important; }} }}

.rr-stat, .rr-feat {{
  border: 1px solid var(--rr-border); border-radius: 16px; background: var(--rr-card); padding: 1rem 1.15rem;
  height: 100%; margin-bottom: .8rem;
}}
.rr-stat .k {{ font-family: var(--rr-mono); font-size: .68rem; letter-spacing: .16em; text-transform: uppercase; color: var(--rr-muted); }}
.rr-stat .v {{ font-family: var(--rr-head); font-weight: 700; font-size: 2.1rem; line-height: 1.15; margin-top: .3rem; }}
.rr-stat .s {{ font-size: .85rem; color: var(--rr-muted); }}
.rr-feat .ic {{ font-size: 1.3rem; color: var(--rr-primary); margin-bottom: .55rem; }}
.rr-feat h4 {{ font-family: var(--rr-head) !important; font-size: 1.08rem !important; margin: 0 0 .35rem 0 !important; padding: 0 !important; }}
.rr-feat p {{ margin: 0; font-size: .9rem; color: #a9bac4; line-height: 1.45; }}
.c-cyan {{ color: var(--rr-primary); }} .c-green {{ color: var(--rr-success); }}
.c-amber {{ color: var(--rr-warning); }} .c-red {{ color: var(--rr-danger); }} .c-pink {{ color: var(--rr-accent); }}

.rr-status {{
  border: 1px solid var(--rr-border); border-radius: 12px; padding: .7rem .8rem; margin-top: .6rem;
  background: rgba(3, 9, 18, .6); font-family: var(--rr-mono); font-size: .66rem; letter-spacing: .1em;
  text-transform: uppercase; color: var(--rr-muted);
}}
.rr-status div {{ display: flex; justify-content: space-between; padding: .18rem 0; }}
.rr-status b {{ font-weight: 600; color: var(--rr-text); }}

[data-testid="stSidebar"] [data-testid="stCaptionContainer"] {{ font-family: var(--rr-body); }}

@media (max-width: 640px) {{
  .rr-title {{ font-size: 1.8rem; }}
  .rr-bar {{ font-size: .62rem; gap: .3rem .9rem; margin-top: -.4rem; }}
  .rr-bar .wide {{ display: none; }}
  .rr-hero {{ padding: 1.5rem 1.2rem; }}
}}
</style>
"""

_EMOJI_PREFIX = re.compile(r"^[^\w(\"'“]+")


def apply() -> None:
    """The theme CSS. Call once per run, before any page draws."""
    st.markdown(CSS, unsafe_allow_html=True)


def clean_title(title: str) -> str:
    """Drop a leading emoji ("🔍 Triage" -> "Triage") so headings match the clean design."""
    return _EMOJI_PREFIX.sub("", title).strip() or title


def page_header(title: str, eyebrow: str = "", lead: str = "") -> None:
    """Small cyan mono label, big title, optional grey lead line."""
    parts = [f'<div class="rr-eyebrow">{e(eyebrow)}</div>' if eyebrow else "",
             f'<div class="rr-title">{e(clean_title(title))}</div>',
             f'<div class="rr-lead">{lead}</div>' if lead else ""]
    st.markdown("".join(parts), unsafe_allow_html=True)


def counts(student_id: str | None = None) -> dict:
    """How many payments are in each state (all of them, or one student's). Read only."""
    sql = ("SELECT state, COUNT(*) FROM transactions WHERE txn_id NOT LIKE 'B-%'"   # load-test copies don't count
           + (" AND student_id = ?" if student_id else "") + " GROUP BY state")
    try:
        with svc.db() as con:
            rows = dict(con.execute(sql, (student_id,) if student_id else ()).fetchall())
    except Exception:
        rows = {}
    return {"cleared": rows.get("CLEARED", 0) + rows.get("COMPLETED", 0),
            "held": rows.get("PENDING_CONFIRMATION", 0) + rows.get("PENDING_GUARDIAN", 0)
                    + rows.get("CONFIRMED_CANCELLABLE", 0),
            "escalated": rows.get("ESCALATED", 0),
            "new": rows.get("FLAGGED", 0), "cancelled": rows.get("CANCELLED", 0), "total": sum(rows.values())}


def accuracy() -> tuple[int, int]:
    """Demo scenarios routed the way the scenario file expects: (correct, scored). Read only."""
    try:
        with svc.db() as con:
            row = con.execute("""SELECT SUM(a.route = t.expected_route), COUNT(*) FROM transactions t
                                 JOIN assessments a USING (txn_id)
                                 WHERE t.expected_route IS NOT NULL AND t.txn_id NOT LIKE 'B-%'""").fetchone()
        return int(row[0] or 0), int(row[1] or 0)
    except Exception:
        return 0, 0


def status_bar() -> None:
    """The thin live bar at the top of each page."""
    staff = st.session_state.get("role") == "staff"
    c = counts(None if staff else st.session_state.get("student_id"))
    held_label = "Threats held" if staff else "Held for you"
    st.markdown(
        f'<div class="rr-bar"><span class="live">Defense grid live</span>'
        f'<span>{held_label}: <b class="c-red">{c["escalated"] + c["held"]}</b></span>'
        f'<span>Auto-cleared: <b class="c-cyan">{c["cleared"]}</b></span>'
        f'<span class="wide">Wallet freezes: <b class="c-green">0 · never</b></span>'
        f'<span class="wide">TechX Dubai \'26</span></div>', unsafe_allow_html=True)


def sidebar_status() -> None:
    """AGENT / GUARDS / AI CHAIN / WALLET FREEZES box at the bottom of the side panel."""
    try:
        import agent
        ready = [name for name, ok in agent.provider_status() if ok]
    except Exception:
        ready = []
    agent_state = '<b class="c-green">● Online</b>' if ready else '<b class="c-amber">● Code only</b>'
    chain = " > ".join(ready) if ready else "guards only"
    st.sidebar.markdown(
        f'<div class="rr-status"><div><span>Agent</span>{agent_state}</div>'
        f'<div><span>Guards</span><b>Armed</b></div>'
        f'<div><span>AI chain</span><b>{e(chain)}</b></div>'
        f'<div><span>Wallet freezes</span><b class="c-cyan">0 · never</b></div></div>', unsafe_allow_html=True)


def stat_card(label: str, value, sub: str, color: str = "c-cyan") -> str:
    return (f'<div class="rr-stat"><div class="k">{e(label)}</div><div class="v {color}">{e(str(value))}</div>'
            f'<div class="s">{e(sub)}</div></div>')


def feature_card(icon: str, title: str, body: str) -> str:
    return f'<div class="rr-feat"><div class="ic">{icon}</div><h4>{e(title)}</h4><p>{e(body)}</p></div>'


def columns_html(cards: list[str], per_row: int, mobile: bool) -> None:
    """Cards side by side on desktop (per_row), two per row on phones."""
    n = min(2, per_row) if mobile else per_row
    for i in range(0, len(cards), n):
        for col, card in zip(st.columns(n), cards[i:i + n]):
            col.markdown(card, unsafe_allow_html=True)
