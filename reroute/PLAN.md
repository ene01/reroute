# Reroute: PLAN.md (Master Specification)

**Project:** Reroute, the scam-defense agent for PocketPay, a campus wallet
**Competition:** TechX Dubai '26, Agentic AI, Domain 1: FinTech Fraud & Compliance (High School track)
**Format:** this plan follows the TechX manual's master prompt (`All_project_starting_prompt.md`, manual section 10), in its five sections.

> **About this document.** It describes Reroute as designed and built, and it is kept up to date as the project changes. Section 3 records the build phases in the order they were done, then lists the remaining nice-to-have work. Section 5 gives prompts for building that remaining work with an AI coding agent.

---

## The problem (from the challenge brief)

PocketPay's two-person support team hand-reviews about 200 flagged payments and 20 suspicious messages a day. Real scams, like fake event-ticket sellers and phishing links, sit in the same queue as harmless AED 15 canteen purchases. Students wait hours while fraud slips through.

**Goal:** an autonomous AI safety workflow that:
- reads the payment and its chat message
- calls verification tools
- explains the risk in plain language
- routes each payment to **CLEAR** (safe purchases clear automatically), **HOLD_FOR_CONFIRMATION** (the student confirms) or **ESCALATE** (staff review, with a full decision log)

It must never permanently freeze a wallet, never loop forever, and keep working when a tool or the AI is down.

---

## 1. System Architecture & Hackathon Tech Stack

### 1.1 End-to-end data flow

```
Browser (Streamlit UI: student wallet / staff dashboard)
   |  login (common.require_login) -> session cookie (sessions.py)
   v
app.py (top dropdown menu: Staff / Student pages)
   |
   |  "Check with Reroute" / "Run agent"
   v
agent.process_transaction(txn_id)
   1. services.get_transaction_context()        read payment + message (SQLite)
   2. guards.preflight()                        CODE: injection / malformed / stale / duplicate
        -> attack or broken data: AI SKIPPED, go to step 5
   3. AgentRun.run()  <-->  Ollama qwen3:4b-instruct (local LLM)
        tool calls: get_transaction_context, verify_campus_recipient, submit_risk_assessment
        output: risk level + confidence + signals + plain-language explanation (an OPINION only)
   4. code fetches anything the AI skipped (recipient check has a 2 s timeout)
   5. guards.route()                            CODE decides: CLEAR | HOLD_FOR_CONFIRMATION | ESCALATE
   6. services.update_payment_state()           SINGLE WRITE GATEWAY: allowed transitions,
                                                last re-check, one SQLite transaction (rollback)
   7. paused? -> pending_confirmations row      execution stops; nothing polls
   v
Human: student confirms/cancels, or staff release / confirm scam  -> agent.resolve() -> gateway
   v
decision_log (every step, labelled AGENT / TOOL / GUARD / HUMAN) shown in the UI
```

### 1.2 Who does what (the manual's golden rule)

| Who | Does | Never does | Where |
|---|---|---|---|
| **AI (LLM)** | Reads messy chat and slang, chooses which read-only tools to call, gives a risk opinion with a confidence level, writes the student explanation | Move money, change state, decide the outcome | `agent.py` |
| **Code** | Validates input, blocks prompt injection, applies every limit and rule, decides the action, writes all changes through one gateway | Guess | `guards.py`, `services.py` |
| **Human** | Student confirms or cancels a held payment. Staff release a payment, or confirm a scam (which cancels it and alerts every student). | Bypass the gateway | `student.py`, `dashboard.py` |

### 1.3 Tech stack

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3 | TechX baseline |
| Database | SQLite (`campus_wallet.sqlite`) | One file, transactions with rollback, easy reset |
| LLM | **Ollama + `qwen3:4b-instruct`** (local) | Free, offline, no API key, keeps data private. The brief names "local Ollama qwen3". The instruct build is used because plain `qwen3:4b` always reasons first (about 500 extra tokens per step, which takes minutes on a CPU). |
| LLM client | `openai` library (OpenAI-compatible API) for every provider, as in manual section 5 | One code path for Groq, Gemini and local Ollama; timeout plus 1 retry per call |
| Fallback chain | Groq Qwen3.8 27B, then Groq GPT-OSS 20B, then Gemini Flash, then local `reroute` (the manual's Llama names were retired) | Online providers are used only if their key is in `.env`; the laptop model is always last |
| Packaged model | **`reroute`** (Ollama `Modelfile`: qwen3:4b-instruct with Reroute's settings and identity) | Our named model; falls back to the base model if missing |
| Our own trained model | **Reroute scam detector**: TF-IDF + logistic regression (scikit-learn), trained on 800 synthetic campus messages | Milliseconds per message, explains itself (top words), 8/8 on unseen demo messages |
| UI | Streamlit (multi-page, top dropdown navigation) | TechX baseline. Desktop and phone layouts. |
| Public demo link | ngrok (free static domain) | Judges can open the demo on their own phones |
| Tests | pytest | 128 tests with a fake AI, about 45 seconds |

**Third-party services:** Ollama (local), ngrok (optional, for the public link). **No paid APIs and no API keys.**

### 1.4 Safety limits

| Limit | Value | Where |
|---|---|---|
| Agent steps per payment | 6 | `agent.MAX_AGENT_STEPS` |
| Tokens per model reply | 400 | `agent.MAX_ANSWER_TOKENS` |
| LLM call timeout | 120 s, then code-only fallback | `agent.LLM_TIMEOUT_S` |
| Recipient registry timeout | 2 s, then `unavailable` (fallback to student confirmation) | `services.VERIFY_TIMEOUT_S` |
| Auto-clear ceiling | AED 100 | `guards.AUTO_CLEAR_LIMIT_AED` |
| Always ask student above | AED 500, or 10x the student's average daily spend | `guards.LARGE_AMOUNT_AED`, `SPEND_MULTIPLIER` |
| Stale request | older than 24 h (fixed demo clock) | `guards.STALE_AFTER_HOURS`, `DEMO_NOW` |
| Duplicate window | same student + recipient + amount within 10 min | `guards.DUPLICATE_WINDOW_MIN` |
| Wallet freeze | **does not exist** in the state machine | `services.ALLOWED_TRANSITIONS` |
| Wrong logins | 5 attempts, then a 30 s pause | `common.MAX_FAILED_LOGINS` |
| Remembered login | 7 days | `sessions.SESSION_DAYS` |
| Beta test money | up to AED 1,000 per top-up, can be switched off | `accounts.MAX_TOPUP_AED`, `BETA_TOPUPS` |

### 1.5 Routing rules (`guards.route`, first match wins)

0. Prompt injection: **ESCALATE** (the AI never saw it). Malformed record: **ESCALATE**.
1. Hard phishing evidence (impersonating PocketPay, 2 or more scam reports, or a suspicious link from a new unverified account): **ESCALATE**.
2. Possible duplicate, or a request older than 24 h: **HOLD**.
3. Recipient registry unavailable: **HOLD** (never block, never auto-clear).
4. AI confidence low: **ESCALATE** (no guessing on unclear slang).
5. Unverified recipient with no payment note, or a request to keep it secret: **ESCALATE**.
6. AI rates it high risk but there's no hard evidence: **HOLD**.
7. Over AED 500, or over 10x the student's average daily spend: **HOLD**.
8. Verified vendor or club, AED 100 or less, low risk: **CLEAR**.
9. Trusted peer (score 70+, 3+ past payments), AED 100 or less, low risk: **CLEAR**.
10. Anything else: **HOLD**.

If the AI is unavailable there's no opinion, and the code-only rules still clear obviously safe vendor payments.

---

## 2. Complete File-by-File Project Structure

```
TechX/
└── reroute/
    ├── PLAN.md                 this master specification
    ├── README.md               setup, run commands, 90-second demo script
    ├── requirements.txt        ollama, streamlit, pandas, pytest
    ├── .gitignore              secrets, database, caches
    ├── staff_password.txt      staff password (secret, git-ignored)
    ├── campus_wallet.sqlite    generated database (git-ignored)
    │
    ├── app.py                  Streamlit entry point and dropdown navigation
    ├── common.py               login / sign-up screen, sessions cookie, layout switch, shared UI
    ├── dashboard.py            Staff pages
    ├── student.py              Student pages
    │
    ├── agent.py                the LLM agent loop and orchestration
    ├── Modelfile               the packaged "reroute" Ollama model
    ├── detector.py             Reroute's own scam detector: load, score, explain
    ├── train_detector.py       trains the detector and tests it honestly
    ├── detector_data.py        synthetic training messages (demo scenarios kept out)
    ├── models/                 scam_detector.joblib + detector_report.json
    ├── guards.py               deterministic pre-flight checks and routing rules
    ├── services.py             mock PocketPay services and the single write gateway
    ├── accounts.py             student accounts, staff account management, beta top-ups
    ├── sessions.py             remembered logins
    ├── seed_data.py            synthetic dataset (27 scenarios)
    │
    ├── run_demo.py             terminal demo and scorecard
    ├── start_public.bat        double-click launcher (runs start_public.ps1)
    ├── start_public.ps1        starts the app and the ngrok public link
    │
    └── tests/
        ├── conftest.py         temporary database per test, fake AI, AI-down fixture
        ├── test_guards.py      unit tests: injection, validation, routing rules
        ├── test_workflow.py    end-to-end: every scenario, rollback, loop prevention, humans
        ├── test_accounts.py    sign-up, login, search, reset, delete, top-ups
        └── test_sessions.py    remembered logins: expiry, revocation, staff password change
```

### Core agent and rules

**`agent.py`: the AI agent and orchestration**
- Purpose: runs the LLM investigation for one payment, then hands everything to the code guard and the write gateway.
- Key parts:
  - `MODEL`, `MAX_AGENT_STEPS`, `MAX_ANSWER_TOKENS`, `LLM_TIMEOUT_S`, `SYSTEM_PROMPT`.
  - `class AgentRun`, with the three tools exposed to the LLM:
    - `get_transaction_context()`: cached, and always the payment under review.
    - `verify_campus_recipient()`: corrects a wrong handle, tells the AI not to retry during an outage.
    - `submit_risk_assessment()`: normalises bad values; anything unreadable counts as low confidence.
  - `AgentRun.run()`: the capped tool-calling loop.
  - `process_transaction(txn_id)`: loop prevention, pre-flight, agent, guard, gateway.
  - `_decision_rows()`: the student explanation plus the pending approval, saved in the same transaction.
  - `resolve(txn_id, approve, actor)`: a student or staff decision; a confirmed scam also broadcasts an alert.
  - `summary()`.
- Connects to: `services` (tools, gateway, log), `guards` (pre-flight and routing), Ollama (LLM). Used by `dashboard.py`, `student.py`, `run_demo.py` and the tests.

**`guards.py`: deterministic rules (no AI)**
- Purpose: the code half of the golden rule.
- Key parts:
  - Limit constants and `DEMO_NOW`.
  - Regexes for links, urgency, impersonation, secrecy and **prompt injection**.
  - `preflight()` returns injection matches, invalid fields, staleness and duplicates. `describe_preflight()` and `blocks_ai()` go with it.
  - `code_signals()`: hard scam signals.
  - `route()`: the rules in section 1.5.
  - `final_clear_check()`: the last re-check inside the write gateway.
- Connects to: called by `agent.process_transaction`. Pure functions, so they're easy to unit-test.

**`services.py`: mock PocketPay back end and the write gateway**
- Purpose: the three services the brief recommends, plus logging.
- Key parts:
  - `db()`: a context manager that commits, rolls back on error, and always closes.
  - `get_transaction_context()`.
  - `verify_campus_recipient()`: a deterministic trust score from 0 to 100. A 2 s timeout through a thread pool returns `unavailable` instead of crashing.
  - `find_duplicates()`.
  - `update_payment_state()`: **the single write gateway**. It checks `ALLOWED_TRANSITIONS`, runs the optional `final_check`, then saves the state, assessment, pending approval, resolution and alert in **one transaction**. `GatewayCheckFailed` goes with it.
  - `log()`, `write_log()`, `get_log()`.
- Connects to: SQLite. Used by `agent`, `accounts`, the pages and the tests.

**`seed_data.py`: synthetic data**
- Purpose: builds the database with 3 demo students, 20 recipients and **27 scenarios** (safe, hold, phishing, uncertain slang, 2 outages, 3 injection attacks, duplicate, stale, 2 malformed). Each has an expected route for the scorecard.
- Key parts: `SCHEMA`, `STUDENTS`, `RECIPIENTS`, `TRANSACTIONS`, `seed(db_path)`. The reset **keeps** the `accounts` and `sessions` tables and rebuilds every account's wallet through `accounts.restore_all()`.
- Connects to: run by "Reset demo data", `run_demo.py` and the tests.

### Accounts and login

**`accounts.py`: student accounts and staff management**
- Purpose: sign-up and login with name, school and password (no email), each student's own wallet, staff tools, and beta test money.
- Key parts:
  - `ACCOUNTS_SCHEMA` (tables `accounts`, `sessions`, `topups`) and `ensure_table()`, which also adds `students.school` to older databases.
  - Passwords: salted PBKDF2-SHA256 (`_hash`), with constant-time comparison.
  - `validate_signup()`, `create_account()` (account, wallet and 3 sample requests in one transaction), `authenticate()`.
  - `build_wallet()`, `restore_all()`.
  - Staff tools: `schools()`, `search(name, school)` (both required, partial and case-insensitive), `wallet_summary()`, `set_password()`, `change_password()`, `reset_wallet()`, `delete_account()`.
  - Beta: `add_funds()`, `topups_for()`.
  - Password reset and account deletion also end that account's remembered logins.
- Connects to: `services.db`, `guards.DEMO_NOW`. Used by `common`, `student`, `dashboard`, `seed_data` and `sessions`.

**`sessions.py`: remembered logins**
- Purpose: refreshing the page doesn't log you out.
- Key parts:
  - `create()`: a random token. Only its SHA-256 hash is stored, with a 7-day expiry.
  - `lookup()`: rejects expired, revoked or deleted-account tokens, and staff tokens whose staff-password fingerprint no longer matches.
  - `revoke()`, `staff_fingerprint()`.
- Connects to: `accounts`, `services`. Used by `common`.

### User interface

**`app.py`: entry point**
- Purpose: page config, registering pages in a **top dropdown menu** (Staff and Student sections; students get only the Student section), creating the database if it's missing, writing any pending cookie, the login check, then running the page.
- Key parts: `STAFF` and `STUDENT` page lists, `make()`, `common.PAGES`.

**`common.py`: shared UI**
- Purpose: the login screen and shared helpers.
- Key parts:
  - `require_login()`: the Log in / Sign up / Staff tabs, a 5-attempt lockout, and restoring a remembered login from the cookie.
  - Cookie handling: `remember_login()`, `forget_login()`, `flush_cookie()`, `_write_cookie()`.
  - `log_out()`, `logout_button()`.
  - Layout: `device_is_mobile()`, `is_mobile()`, `layout_switch()`, `keep_layout_choice()`.
  - `pending()`, `show_log()` (table on desktop, colour-coded cards on phones), `go()`, `MOBILE_CSS`.

**`dashboard.py`: Staff pages (staff only; anyone else is redirected to the wallet)**
- Pages:
  - `triage_page()`: run the agent and read the decision log.
  - `inbox_page()`: payments waiting for a student.
  - `queue_page()`: release a payment, or confirm a scam and alert students.
  - `alerts_page()`.
  - `stats_page()`: counts, staff hours saved and routing accuracy.
  - `accounts_page()` and `_manage_account()`: search by name and school (the school is a type-ahead dropdown), open a wallet, add test money, reset a password or wallet, delete with name confirmation.
- Shared: `_setup()` (role guard and sidebar: Run all, Reset demo data, Layout, Log out, "Who does what"), `card_grid()`.

**`student.py`: Student pages (own wallet only; staff can pick any student)**
- Pages:
  - `wallet_page()`: balance, beta top-up, what needs attention, scam alerts.
  - `needs_ok_page()`: Confirm or Cancel.
  - `checking_page()`.
  - `requests_page()`: Check with Reroute.
  - `activity_page()`: includes test-money top-ups.
  - `profile_page()`: details, change password.
- Every page has a phone layout and a desktop layout. All user text is HTML-escaped.

### Running and testing

**`run_demo.py`** runs all (or chosen) scenarios with the real AI, prints colour-coded decision logs and a scorecard of expected against actual. `--interactive` lets you answer the confirmations.

**`start_public.bat` / `start_public.ps1`** start Streamlit (minimised) if it isn't already running, then ngrok on port 8501. If a public link is already running, they say so instead of starting a second one. If ngrok fails, the window stays open with the error.

**`tests/`** (93 tests, a fake AI, a temporary database per test):
- `test_guards.py`: injection detection and false positives, field validation, every routing rule, "only three actions are possible", and no freeze state.
- `test_workflow.py`: all 27 scenarios route as expected; attacks never reach the AI; the AI-outage fallback; loop prevention; student and staff decisions; the scam alert; **rollback after a simulated crash mid-write**; the gateway re-check; complete logging.
- `test_accounts.py`: sign-up validation, duplicates, login, password hashing, search rules, reset, delete, beta top-ups, demo reset keeping accounts.
- `test_sessions.py`: remembered login, hashed tokens, expiry, logout, revocation on password change or deletion, staff password change.

---

## 3. Step-by-Step Build Order

Each phase ended with a working demo and a human checkpoint (section 4). Phases 1 to 4 are the **must-have MVP**. Phases 5 and 6 are **stretch features** that were added. Phase 7 is what's left.

### Phase 1: Setup, data schema and mock services (MVP, done)
- `seed_data.py`: schema and synthetic scenarios, including 3 uncertain cases that must escalate.
- `services.py`: the three recommended services, the state machine with no freeze state, the decision log.
- **Happy path:** a payment can be read, its recipient verified, and its state changed.

### Phase 2: AI agent, code guard and terminal demo (MVP, done)
- `agent.py`: the Ollama tool-calling loop with caps and a timeout.
- `guards.py`: deterministic routing.
- `run_demo.py`: scorecard.
- Model switched from `qwen3:4b` to `qwen3:4b-instruct` after measuring 4 to 5 minutes per scenario. Now about 25 s.
- **Result:** 12 of 12 scenarios routed correctly with the real AI. The code-only fallback also routed 12 of 12.

### Phase 3: Streamlit UI and human-in-the-loop (MVP, done)
- Triage with a live decision log, a student inbox (Confirm / Cancel), a staff queue (Release / Confirm scam), scam alert broadcasting, and stats with staff hours saved.
- **Result:** the full flow clicked through in the browser.

### Phase 4: Hardening against the TechX manual checklist (MVP, done)
- A prompt-injection pre-flight that runs before the AI, field validation, stale and duplicate checks, and 5 new scenarios (2 attacks, duplicate, stale, malformed).
- The write gateway made all-or-nothing, with a last re-check before any auto-clear.
- A token cap per reply, and the pytest suite.
- **Result:** 17 of 17 with the real AI. Both attacks and the malformed record were blocked before the AI saw them.
- **Second batch (T018-T027):** 10 new made-up cases (fake teacher, crypto scam, a note-injection attack, a second shop outage, vague slang, a broken handle, and normal payments). Result: 10 of 10 with the real AI (Groq), 118 tests passing.
- **Send and request between students:** every account gets a pay-me handle (a verified campus peer). A request lands in the other student's New requests and goes through the full Reroute check before money moves; a direct payment is checked straight away. Name + school needed to find someone, max AED 1,000, max 10 open requests per student, and the requester only sees "Waiting" so a scammer can't tell they were flagged. 10 new tests (128 total).

### Phase 5: Demo access (stretch, done)
- A public link through ngrok, a password gate, a separate staff password.
- Phone and desktop layouts with automatic device detection plus a manual switch.

### Phase 6: Accounts and navigation (stretch, done)
- Student sign-up and login (name, school, password twice), each student's own profile and wallet, and sample requests for new accounts.
- A top dropdown menu with Staff and Student sub-pages.
- Staff **Manage accounts** (name and school required, with a type-ahead school dropdown).
- Beta test money, and remembered logins (refreshing no longer logs you out).

### Phase 6b: Our own AI pieces (stretch, done)
- Trained **Reroute's own scam detector** on 800 synthetic messages. Tested on the demo messages it never saw: 8 of 8 correct. Added as a fourth agent tool (`check_scam_score`) and as a guard rule (80% or higher can't auto-clear; it never blocks on its own).
- Packaged the LLM as the **`reroute`** Ollama model.

### Phase 7: Remaining nice-to-have work (not started)
| Priority | Feature | Why |
|---|---|---|
| 1 | ~~Fallback AI providers~~ **Done** (Groq Qwen3.8 27B, then Groq GPT-OSS 20B, then Gemini Flash, then local `reroute`). The Groq key is set up; a Gemini key is optional. | Manual checklist: "at least two different providers ... ending with Ollama" |
| 2 | Move the login cookie writer from `st.components.v1.html` to the newer API | Streamlit has deprecated it; it still works in 1.67 |
| 3 | Event-ticket ownership / barcode check | A stretch goal in the brief |
| 4 | Langfuse-style tracing, or exporting a case log as a report | The manual's "log and report" stage |

---

## 4. Human-in-the-Loop (HITL) Checkpoints

At each checkpoint the AI coding assistant stopped, and the team reviewed, decided or supplied something before work continued.

| # | When | What the team decided or checked |
|---|---|---|
| 1 | Before building | Shared the Domain 1 challenge brief (PocketPay). Chose free and local over paid APIs (no budget for API keys). |
| 2 | Choosing the model | Picked the qwen3 4B size for a 16 GB, CPU-only laptop. Approved switching to `qwen3:4b-instruct` after the speed test. |
| 3 | Choosing the interface | Picked Streamlit, plus a terminal scorecard. |
| 4 | After Phase 2 | Reviewed the routing rules and limits (AED 100 auto-clear, AED 500 / 10x spend, 24 h staleness). *Team: confirm or tune these numbers.* |
| 5 | After Phase 3 | Clicked through every scenario in the browser; checked student and staff decisions and the scam alert. |
| 6 | After reading the TechX manual | Compared the project with the demo checklist; approved the hardening work in Phase 4. Deferred the fallback providers (they need API keys). |
| 7 | Naming and branding | Named the project **Reroute**. Kept "PocketPay" as the wallet company from the brief. |
| 8 | Public demo | Chose ngrok with a fixed free domain; created the ngrok account and ran the authtoken command personally (secrets are never handled by the AI). Set the passwords. |
| 9 | Access control | Asked that only staff can see the staff pages; chose name + school + password accounts with no email. |
| 10 | Before each demo | Run `python -m pytest` (all tests must pass), run `python run_demo.py` (check the scorecard), click **Reset demo data**, keep the laptop plugged in with sleep off. |

**Rules for future checkpoints:**
- API keys are always typed by the team into `.env`, never pasted into chat.
- Any change to a limit in `guards.py` needs a team review and passing tests.
- Cut scope rather than demo something untested.

---

## 5. AI Coding Agent Handoff Prompts

**Use these for the remaining Phase 7 work.** Paste the general rules first, then one feature prompt. Use your planning model to write a feature spec first if the feature is large (manual section 10, step 4A), then your coding model to build it (step 4B).

### General rules (paste before every prompt)
```
You are working on Reroute (see PLAN.md, especially sections 1.2, 1.4 and 1.5).
Rules:
- The LLM only gives opinions. Every decision stays in guards.py; every state change goes
  through services.update_payment_state(). Never add a "freeze" or "block" state.
- Do not change limits in guards.py without asking us first.
- Keep all existing tests passing (python -m pytest) and add tests for anything new.
- Never ask us to paste API keys into chat. Read them from .env with python-dotenv.
- Work in small steps. After each step, stop and tell us what to check (a human checkpoint).
- If anything in this plan is unclear, ask before writing code.
```

### Prompt A: Fallback AI providers (priority 1)
```
Read PLAN.md and agent.py. Add a provider fallback chain for the agent loop, as described in the
TechX manual section 5: Groq (llama-3.3-70b-versatile) -> Groq (llama-3.1-8b-instant) ->
Gemini Flash -> local Ollama (qwen3:4b-instruct, always last).
- Use the OpenAI-compatible client (pip install openai python-dotenv) for all providers,
  max_retries=1 and timeout=30 per call; skip providers with no key in .env.
- Keep the same three tools and the same 6-step cap; guard against broken tool-call JSON
  and return an error to the model instead of crashing.
- Log which provider answered each step in the decision log (actor AGENT).
- Add .env.example with GROQ_API_KEY and GEMINI_API_KEY (no real values) and make sure .env
  stays in .gitignore.
- Tests: with no keys set, behaviour is identical to today (Ollama only); a provider that raises
  falls through to the next one.
Stop after the code compiles and tests pass, and tell us to add our keys to .env (checkpoint).
```

### Prompt B: Replace the deprecated cookie writer (priority 2)
```
Read common.py (_write_cookie, flush_cookie) and sessions.py. Streamlit warns that
st.components.v1.html is deprecated. Replace _write_cookie with a supported way to set and clear
the "reroute_session" cookie (Max-Age, Path=/, SameSite=Lax, Secure on https) without changing
behaviour: log in -> refresh stays logged in; log out -> refresh shows the login screen.
Do not store the raw token anywhere except the browser cookie. Keep tests/test_sessions.py passing.
Stop and ask us to test refresh on localhost and on the ngrok link (checkpoint).
```

### Prompt C: Event-ticket ownership check (priority 3)
```
Read PLAN.md, seed_data.py, services.py and guards.py. Add a mock service
verify_ticket_ownership(seller_handle, event, ticket_code) backed by a new SQLite table of issued
tickets. Expose it to the agent as a read-only tool for payments whose note or message mentions a
ticket/pass. In guards.route, a ticket that the seller does not own is hard evidence -> ESCALATE;
an unknown result must not block (fall back to the existing rules). Add 2 seed scenarios (genuine
resale -> HOLD, fake ticket -> ESCALATE) with expected routes, plus tests.
Stop and show us the new rule and scenarios before running the real-AI demo (checkpoint).
```

### Prompt D: Case report export (priority 4)
```
Read services.get_log and dashboard.py. On the Triage page, add a "Download case report" button
that exports one payment's full decision log, AI opinion, guard reason and human decision as a
Markdown file (no student passwords or tokens). Add a test that the report contains every log step.
Stop and ask us to review the report format (checkpoint).
```

---

## Assumptions (confirm or correct)

1. **Local model only for now.** Reroute runs fully offline on Ollama `qwen3:4b-instruct`. Fallback providers wait until the team has free Groq and Gemini keys.
2. **Synthetic data only.** No real students, payments or phone numbers. Every scenario is made up.
3. **Demo clock fixed** at 19 Nov 2026, 16:00 (`guards.DEMO_NOW`), so freshness checks give the same results on demo day.
4. **Amounts in AED.** Limits are AED 100 auto-clear, AED 500 / 10x average daily spend, AED 1,000 per beta top-up.
5. **One staff role** with a shared staff password. There are no per-staff accounts.
6. **Streamlit** is the only UI. There's no separate API server.
7. **Beta test money stays on** until beta testing ends (`REROUTE_BETA_TOPUPS=0` turns it off).
