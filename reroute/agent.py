"""Reroute - scam-defense agent for the PocketPay campus wallet.

Who does what:
  AGENT (LLM, qwen3 via Ollama) - reads the messy chat message, spots phishing / urgency / slang,
                                  decides which tools to call, and writes a plain-language risk opinion.
  TOOLS (code)                  - get_transaction_context, verify_campus_recipient, check_scam_score
                                  (Reroute's own trained detector), update_payment_state.
  GUARD (code)                  - guards.route() turns the opinion + hard facts into CLEAR / HOLD / ESCALATE.
  HUMAN                         - student confirms/cancels held payments; staff resolve escalations.

Stateful + loop-safe: every step is written to SQLite. A payment that is already waiting on a human is
never re-run or re-asked; it simply stays PENDING until resolve() is called with the human's answer.
"""

import json
import os
from pathlib import Path

import ollama
from dotenv import load_dotenv
from openai import OpenAI

import detector
import guards
import services as svc

# "reroute" is our packaged model (see Modelfile: qwen3:4b-instruct with Reroute's settings built in).
# If it hasn't been created on this computer, the plain base model is used instead.
# The instruct build of qwen3 answers directly. Plain "qwen3:4b" always reasons first (~500 extra
# tokens per step), which takes minutes per payment on a CPU.
MODEL = os.getenv("REROUTE_MODEL", "reroute")
BASE_MODEL = "qwen3:4b-instruct"
_active_model: str | None = None


def active_model() -> str:
    """The model actually used: MODEL if Ollama has it, else the base model."""
    global _active_model
    if _active_model is None:
        try:
            llm.show(MODEL)
            _active_model = MODEL
        except Exception:
            _active_model = BASE_MODEL
    return _active_model
MAX_AGENT_STEPS = 6                                 # hard cap on LLM turns per payment (no infinite loops)
MAX_ANSWER_TOKENS = 400                             # cap on each model reply (saves time and quota)
LLM_TIMEOUT_S = 120                                # a hung local model call falls back to code-only checks
ONLINE_TIMEOUT_S = 30                              # online providers: fail fast so the next one can take over
llm = ollama.Client(timeout=LLM_TIMEOUT_S)         # used to check which local model is installed

# ---------------------------------------------------------------------------------------------------
# Fallback chain (TechX manual section 5): online providers first, the laptop model always last.
# A provider with no key in .env is skipped, so with no keys Reroute runs exactly as before (local only).
# Keys live ONLY in reroute/.env (git-ignored); see .env.example. Model names can be changed in .env.
# ---------------------------------------------------------------------------------------------------
load_dotenv(Path(__file__).with_name(".env"))

PROVIDERS = [
    # Groq model names change over time (the manual's Llama names were retired). These two were tested
    # with Reroute's tools on 19 Nov 2026; each has its own rate limit. Override in .env if Groq renames them.
    {"name": "Groq Qwen3.8 27B", "url": "https://api.groq.com/openai/v1", "key_env": "GROQ_API_KEY",
     "model": os.getenv("REROUTE_GROQ_MODEL", "qwen/qwen3.8-27b")},
    {"name": "Groq GPT-OSS 20B", "url": "https://api.groq.com/openai/v1", "key_env": "GROQ_API_KEY",
     "model": os.getenv("REROUTE_GROQ_SMALL_MODEL", "openai/gpt-oss-20b")},
    {"name": "Gemini Flash", "url": "https://generativelanguage.googleapis.com/v1beta/openai/",
     "key_env": "GEMINI_API_KEY", "model": os.getenv("REROUTE_GEMINI_MODEL", "gemini-flash-latest")},
    {"name": "Local (laptop)", "url": os.getenv("REROUTE_OLLAMA_URL", "http://localhost:11434/v1"),
     "key_env": None, "model": None},   # model filled in with active_model(): always available, always last
]


def provider_chain() -> list[dict]:
    """The providers that will actually be tried, in order (those with keys, then local)."""
    chain = []
    for p in PROVIDERS:
        if p["key_env"] is None:
            chain.append(p | {"key": "ollama", "model": active_model(), "timeout": LLM_TIMEOUT_S})
        elif os.getenv(p["key_env"]):
            chain.append(p | {"key": os.getenv(p["key_env"]), "timeout": ONLINE_TIMEOUT_S})
    return chain


def provider_status() -> list[tuple[str, bool]]:
    """(name, ready) for every provider, for the staff sidebar. Never exposes the keys."""
    return [(p["name"], p["key_env"] is None or bool(os.getenv(p["key_env"]))) for p in PROVIDERS]


def ask(messages: list, tools: list, chain: list[dict] | None = None, on_fail=None):
    """Send one turn to the first provider that answers. Returns (message, provider_name).

    Each call: max 1 retry and a timeout, so a blocked or rate-limited provider can't stall the agent.
    Raises RuntimeError if every provider fails (the caller then uses code-only checks).
    """
    errors = []
    for p in chain if chain is not None else provider_chain():
        try:
            client = OpenAI(base_url=p["url"], api_key=p["key"], max_retries=1, timeout=p["timeout"])
            reply = client.chat.completions.create(model=p["model"], messages=messages, tools=tools,
                                                   temperature=0, max_tokens=MAX_ANSWER_TOKENS)
            return reply.choices[0].message, p["name"]
        except Exception as exc:
            errors.append(f"{p['name']}: {type(exc).__name__}")
            if on_fail:
                on_fail(p["name"], exc)
    raise RuntimeError("Every AI provider failed (" + "; ".join(errors) + ")")


def _tool(name: str, description: str, params: dict[str, str]) -> dict:
    """An OpenAI-style tool definition with every parameter a required string."""
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": {k: {"type": "string", "description": v}
                                                        for k, v in params.items()},
                       "required": list(params)}}}


TOOL_SCHEMAS = [
    _tool("get_transaction_context",
          "Get the flagged payment details, the chat message that triggered it, and the student's spending profile.",
          {"txn_id": "The transaction id, e.g. T003."}),
    _tool("verify_campus_recipient",
          "Check if the recipient is a verified campus vendor, club or student and get their trust score (0-100).",
          {"handle": "The recipient handle, e.g. @campus_canteen."}),
    _tool("check_scam_score",
          "Run Reroute's own trained scam detector on the payment's message. Returns a scam probability (0-1), "
          "a label and the words that raised the score. It is a hint, not proof.",
          {"txn_id": "The transaction id, e.g. T003."}),
    _tool("submit_risk_assessment",
          "Submit your final risk opinion. Call this exactly once, after checking the context and the recipient.",
          {"risk_level": "low, medium or high.",
           "confidence": "high, medium or low. Use low if slang or the item being bought is unclear.",
           "scam_signals": "Comma-separated phishing signs you found, or 'none'.",
           "slang_notes": "What any slang means, or which slang you could not understand.",
           "student_explanation": "1-2 friendly sentences explaining the risk to the student."}),
]

# What the student is told about the DECISION (from code), shown before the AI's explanation.
DECISION_HEADLINE = {
    "CLEAR": "",
    "HOLD_FOR_CONFIRMATION": "Paused until you confirm.",
    "ESCALATE": "The PocketPay team will check this first. Your money is safe and your wallet still works.",
}

SYSTEM_PROMPT = """You are Reroute, the scam-defense analyst for PocketPay, a high-school campus wallet.
A payment was flagged. Investigate it, then give your risk opinion.

Steps:
1. Call get_transaction_context with the txn_id.
2. Call verify_campus_recipient with the recipient_handle from the context.
3. Call check_scam_score with the txn_id. It is Reroute's own trained detector: a useful hint, not proof.
4. Call submit_risk_assessment exactly once with your opinion.

How to judge:
- Phishing signs: urgency or pressure, links to claim something, "too good to be true" prices,
  pretending to be PocketPay staff, asking to keep it secret, brand-new or unverified accounts.
- Normal signs: verified campus canteen/shop/club, small everyday amounts, friends with payment history.
- Students use slang (e.g. "bro", "fr", "no cap", "habibi", "yalla"). If you are NOT sure what slang
  means or what is actually being bought, set confidence to "low". Never guess.
- You do NOT decide what happens to the money. Separate code makes the final decision.
- student_explanation: 1-2 short, friendly sentences a 15-year-old understands. No jargon."""


def _summ(d: dict, keys: tuple) -> str:
    return ", ".join(f"{k}={d[k]}" for k in keys if k in d)


class AgentRun:
    """One agent investigation of one payment. Tool functions are methods so results are captured."""

    def __init__(self, txn_id: str, detector_result: dict | None = None):
        self.txn_id = txn_id
        self.ctx: dict | None = None
        self.recipient: dict | None = None
        self.assessment: dict | None = None
        self.detector_result = detector_result  # already computed by code in the pre-flight check

    # ---- tools exposed to the LLM ------------------------------------------------------------
    def get_transaction_context(self, txn_id: str) -> dict:
        """Get the flagged payment details, the chat message that triggered it, and the student's spending profile.

        Args:
            txn_id: The transaction id, e.g. T003.
        """
        if self.ctx is None:
            self.ctx = svc.get_transaction_context(self.txn_id)  # always the payment under review
            svc.write_log(self.txn_id, "TOOL", "get_transaction_context",
                          _summ(self.ctx, ("amount_aed", "recipient_handle", "channel", "note",
                                           "prior_txns_with_recipient")))
        else:
            svc.write_log(self.txn_id, "GUARD", "duplicate call", "get_transaction_context already ran; cached result reused")
        return self.ctx

    def verify_campus_recipient(self, handle: str) -> dict:
        """Check if the recipient is a verified campus vendor, club or student and get their trust score (0-100).

        Args:
            handle: The recipient handle, e.g. @campus_canteen.
        """
        if self.recipient is None:
            real = (self.ctx or svc.get_transaction_context(self.txn_id))["recipient_handle"]
            if handle != real:  # the LLM may only check the actual recipient of this payment
                svc.write_log(self.txn_id, "GUARD", "handle corrected", f"LLM asked for {handle}; checking {real}")
            self.recipient = svc.verify_campus_recipient(real)
            detail = (_summ(self.recipient, ("kind", "campus_verified", "account_age_days", "scam_reports",
                                             "trust_score"))
                      if self.recipient["status"] == "ok" else f"{self.recipient['status']}: "
                      f"{self.recipient.get('detail', '')}")
            svc.write_log(self.txn_id, "TOOL", "verify_campus_recipient", detail)
        else:
            svc.write_log(self.txn_id, "GUARD", "duplicate call", "verify_campus_recipient already ran; cached result reused")
        if self.recipient["status"] != "ok":
            return self.recipient | {"instruction": "The registry is down. Do NOT retry. Judge from the message "
                                                    "and context, then call submit_risk_assessment now."}
        return self.recipient

    def check_scam_score(self, txn_id: str) -> dict:
        """Run Reroute's own trained scam detector on the payment's message. Returns a scam probability (0-1), a label and the words that raised the score. It is a hint, not proof.

        Args:
            txn_id: The transaction id, e.g. T003.
        """
        r = self.detector_result
        svc.write_log(self.txn_id, "TOOL", "check_scam_score",
                      f"{r['scam_probability']:.0%} {r['label']}; words: {', '.join(r['top_words']) or 'none'}"
                      if r else "no message to score (or detector not trained)")
        return r or {"result": "No message to score. Judge from the payment details and recipient."}

    def submit_risk_assessment(self, risk_level: str, confidence: str, scam_signals: str,
                               slang_notes: str, student_explanation: str) -> dict:
        """Submit your final risk opinion. Call this exactly once, after checking the context and the recipient.

        Args:
            risk_level: low, medium or high.
            confidence: high, medium or low. Use low if slang or the item being bought is unclear.
            scam_signals: Comma-separated phishing signs you found, or "none".
            slang_notes: What any slang means, or which slang you could not understand.
            student_explanation: 1-2 friendly sentences explaining the risk to the student.
        """
        risk = str(risk_level).strip().lower()
        conf = str(confidence).strip().lower()
        self.assessment = {
            "risk_level": risk if risk in ("low", "medium", "high") else "medium",
            "confidence": conf if conf in ("low", "medium", "high") else "low",  # unparseable -> unsure
            "scam_signals": str(scam_signals), "slang_notes": str(slang_notes),
            "student_explanation": str(student_explanation).strip(),
        }
        svc.write_log(self.txn_id, "AGENT", "risk opinion",
                      f"risk={self.assessment['risk_level']}, confidence={self.assessment['confidence']}; "
                      f"signals: {self.assessment['scam_signals']}; slang: {self.assessment['slang_notes']}")
        return {"received": True, "note": "Thanks. Code will now make the final routing decision."}

    # ---- the agent loop ----------------------------------------------------------------------
    def run(self, chain: list[dict] | None = None) -> None:
        by_name = {f.__name__: f for f in (self.get_transaction_context, self.verify_campus_recipient,
                                           self.check_scam_score, self.submit_risk_assessment)}
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": f"Investigate flagged payment txn_id={self.txn_id}."}]
        last_provider = None

        def failed(name: str, exc: Exception) -> None:
            svc.write_log(self.txn_id, "GUARD", "provider failed",
                          f"{name} didn't answer ({type(exc).__name__}); trying the next one")

        for _ in range(MAX_AGENT_STEPS):
            msg, provider = ask(messages, TOOL_SCHEMAS, chain, on_fail=failed)
            if provider != last_provider:  # log which AI answered (and when a fallback took over)
                svc.write_log(self.txn_id, "AGENT", "answered by", provider)
                last_provider = provider
            calls = msg.tool_calls or []
            messages.append({"role": "assistant", "content": msg.content or "",
                             **({"tool_calls": [{"id": c.id, "type": "function",
                                                 "function": {"name": c.function.name,
                                                              "arguments": c.function.arguments or "{}"}}
                                                for c in calls]} if calls else {})})
            if not calls:
                if self.assessment:
                    return
                messages.append({"role": "user", "content": "Use the tools, then call submit_risk_assessment."})
                continue
            for call in calls:
                # Models sometimes write broken JSON: tell the model instead of crashing.
                try:
                    args = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = None
                fn = by_name.get(call.function.name)
                if not isinstance(args, dict):
                    out = {"error": "Your tool arguments were not valid JSON. Call the tool again with valid JSON."}
                elif fn is None:
                    out = {"error": f"unknown tool {call.function.name}"}
                else:
                    try:
                        out = fn(**args)
                    except TypeError as exc:
                        out = {"error": f"bad arguments: {exc}"}
                messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(out)})
            if self.assessment:
                return
        svc.write_log(self.txn_id, "GUARD", "step limit",
                      f"Agent did not finish within {MAX_AGENT_STEPS} steps; stopping (no infinite loop)")


# -------------------------------------------------------------------------------------------------
# Orchestration
# -------------------------------------------------------------------------------------------------

def process_transaction(txn_id: str) -> dict:
    """Run the full triage for one flagged payment and return a summary."""
    ctx = svc.get_transaction_context(txn_id)
    if "error" in ctx:
        return ctx
    if ctx["state"] != "FLAGGED":
        # Loop prevention: a payment already routed (or waiting on a human) is never re-run or re-asked.
        svc.write_log(txn_id, "GUARD", "already routed",
                      f"State is {ctx['state']}; not re-running the agent or re-asking the human")
        return summary(txn_id)

    # Stage 2: validate and sanitise the input BEFORE the AI sees anything.
    # Includes Reroute's own trained scam detector (milliseconds, runs on any laptop).
    det = detector.score(ctx.get("message_text"))
    pre = guards.preflight(ctx, svc.find_duplicates(txn_id, guards.DUPLICATE_WINDOW_MIN), det)
    svc.write_log(txn_id, "GUARD", "pre-flight check", guards.describe_preflight(pre))

    run = AgentRun(txn_id, det)
    if guards.blocks_ai(pre):
        svc.write_log(txn_id, "GUARD", "AI skipped",
                      "Input failed the pre-flight check, so it is never sent to the AI")
    else:
        chain = " > ".join(p["name"] for p in provider_chain())
        svc.write_log(txn_id, "AGENT", "start", f"Investigating AED {ctx['amount_aed']:.0f} to "
                                               f"{ctx['recipient_handle']} (AI chain: {chain})")
        try:
            run.run()
        except Exception as exc:  # Ollama down, model missing, etc. -> fall back, never crash
            svc.write_log(txn_id, "GUARD", "agent error",
                          f"LLM unavailable ({exc}); continuing with code-only checks")

    # The code guard needs the facts even if the agent skipped a tool.
    if run.ctx is None:
        run.ctx = ctx
        svc.write_log(txn_id, "GUARD", "fetched by code", "Code fetched the transaction context")
    if run.recipient is None:
        run.recipient = svc.verify_campus_recipient(ctx["recipient_handle"])
        svc.write_log(txn_id, "GUARD", "fetched by code",
                      f"Code checked the recipient itself (status={run.recipient['status']})")

    # Stage 6: guard check in code.
    action, reasons, sig = guards.route(run.ctx, run.recipient, run.assessment, pre)
    hits = [k for k, v in sig.items() if v]
    svc.write_log(txn_id, "GUARD", f"route -> {action}",
                  f"{'; '.join(reasons)} | code signals: {', '.join(hits) or 'none'}")

    # Stages 7-8: execute through the single write gateway (all-or-nothing, last re-check for CLEAR).
    final_check = ((lambda: guards.final_clear_check(svc.get_transaction_context(txn_id), pre))
                   if action == "CLEAR" else None)
    try:
        svc.update_payment_state(txn_id, guards.ACTION_TO_STATE[action], reasons[0], "routing guard",
                                 final_check=final_check,
                                 **_decision_rows(txn_id, ctx, action, reasons, run.assessment, pre))
    except svc.GatewayCheckFailed as exc:
        svc.write_log(txn_id, "GUARD", "write gateway refused", f"Last re-check failed ({exc}); holding instead")
        action, reasons = "HOLD_FOR_CONFIRMATION", [f"Write gateway re-check failed: {exc}"]
        svc.update_payment_state(txn_id, "PENDING_CONFIRMATION", reasons[0], "routing guard",
                                 **_decision_rows(txn_id, ctx, action, reasons, run.assessment, pre))
    return summary(txn_id)


def _decision_rows(txn_id: str, ctx: dict, action: str, reasons: list[str],
                   assessment: dict | None, pre: dict) -> dict:
    """The rows saved together with a routing decision (in the same transaction)."""
    a = assessment or {}
    ai_text = a.get("student_explanation")
    headline = DECISION_HEADLINE[action]
    if action == "HOLD_FOR_CONFIRMATION":  # say WHY the code paused it, so it never contradicts the AI's text
        headline = f"Paused until you confirm. Reason: {reasons[0].split(';')[0]}."
    if pre["injection"]:
        explanation = ("We stopped this payment because its message contained hidden instructions trying to "
                       "trick our safety system. The PocketPay team will check it. Your wallet still works.")
    elif pre["invalid"]:
        explanation = ("Something in this payment's details looks broken, so the PocketPay team will check it "
                       "before anything happens. Your wallet still works.")
    elif ai_text:
        explanation = f"{headline} {ai_text}".strip()
    elif action == "HOLD_FOR_CONFIRMATION":
        explanation = f"{headline} Please confirm you know who you're paying."
    else:
        explanation = _fallback_explanation(action)
    rows = {"assessment": (txn_id, a.get("risk_level", "not assessed"), a.get("confidence", "not assessed"),
                           a.get("scam_signals", ""), a.get("slang_notes", ""), explanation, action,
                           "; ".join(reasons))}
    if action != "CLEAR":
        awaiting = "student" if action == "HOLD_FOR_CONFIRMATION" else "staff"
        question = (f"Do you know {ctx['recipient_handle']} and want to send AED {ctx['amount_aed']:.0f}?"
                    if awaiting == "student" else
                    f"Review AED {ctx['amount_aed']:.0f} to {ctx['recipient_handle']}: release or cancel?")
        rows["pending"] = (awaiting, question)
    return rows


def resolve(txn_id: str, approve: bool, actor: str) -> dict:
    """Apply a human's answer to a paused payment. actor is 'student' or 'staff'."""
    with svc.db() as con:
        p = con.execute("SELECT * FROM pending_confirmations WHERE txn_id = ? AND resolved = 0",
                        (txn_id,)).fetchone()
    if p is None:
        return {"ok": False, "error": "Nothing is waiting for a decision on this payment"}
    if p["awaiting"] != actor:
        return {"ok": False, "error": f"This payment is waiting for {p['awaiting']}, not {actor}"}

    label = {("student", True): "Student confirmed: I know this seller",
             ("student", False): "Student cancelled the transfer",
             ("staff", True): "Staff reviewed and released the payment",
             ("staff", False): "Staff confirmed scam and cancelled the payment"}[(actor, approve)]
    alert = None
    if actor == "staff" and not approve:  # stretch goal: warn other students about a confirmed scam
        handle = svc.get_transaction_context(txn_id)["recipient_handle"]
        alert = (handle, f"Scam alert: {handle} was confirmed as a scam by PocketPay staff. "
                         "Do not send money to this account or click its links.")
    # State change, closing the confirmation and the alert are saved together, or not at all.
    return svc.update_payment_state(txn_id, "COMPLETED" if approve else "CANCELLED", label, actor,
                                    resolution=label, alert=alert)


def summary(txn_id: str) -> dict:
    with svc.db() as con:
        t = con.execute("SELECT * FROM transactions WHERE txn_id = ?", (txn_id,)).fetchone()
        a = con.execute("SELECT * FROM assessments WHERE txn_id = ?", (txn_id,)).fetchone()
    return {"txn_id": txn_id, "state": t["state"], "expected_route": t["expected_route"],
            "route": a["route"] if a else None, "assessment": dict(a) if a else None,
            "log": svc.get_log(txn_id)}


def _fallback_explanation(action: str) -> str:
    return {"CLEAR": "This looks like a normal campus payment, so it went through.",
            "HOLD_FOR_CONFIRMATION": "We couldn't fully check this payment, so please confirm you know who you're paying.",
            "ESCALATE": "This payment needs a quick check by the PocketPay team before it goes through. "
                        "Your money is safe and your wallet still works."}[action]
