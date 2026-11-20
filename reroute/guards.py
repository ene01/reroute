"""Deterministic guards - TRADITIONAL CODE, no AI here.

Two jobs:
  preflight()  - validate and sanitise the input BEFORE the AI sees it (prompt injection, malformed
                 fields, stale requests, duplicates).
  route()      - turn the AI's *opinion* plus hard facts into exactly one of three actions:

    CLEAR                  -> payment goes through
    HOLD_FOR_CONFIRMATION  -> payment waits for the student to confirm or cancel
    ESCALATE               -> payment waits for support staff (money is held, NOT frozen)

There is no "block" or "freeze" action, so missing data or low confidence can never lock a wallet.
"""

import re
from datetime import datetime

AUTO_CLEAR_LIMIT_AED = 100      # everyday purchases up to this amount can clear on their own
LARGE_AMOUNT_AED = 500          # anything above always needs the student's OK
MAX_VALID_AMOUNT_AED = 5000     # anything above (or <= 0) is malformed input
SPEND_MULTIPLIER = 10           # ...as does anything above 10x the student's normal daily spend
TRUSTED_PEER_SCORE = 70
TRUSTED_PEER_MIN_HISTORY = 3
STALE_AFTER_HOURS = 24          # payment requests older than this need a fresh confirmation
DUPLICATE_WINDOW_MIN = 10       # same student + recipient + amount within this window = possible duplicate
DETECTOR_NO_CLEAR = 0.80        # our trained scam detector at/above this: the payment can never auto-clear

# Fixed demo clock, so freshness checks give the same result on every run and on demo day.
DEMO_NOW = datetime(2026, 11, 19, 16, 0)

ACTIONS = ("CLEAR", "HOLD_FOR_CONFIRMATION", "ESCALATE")
ACTION_TO_STATE = {"CLEAR": "CLEARED", "HOLD_FOR_CONFIRMATION": "PENDING_CONFIRMATION", "ESCALATE": "ESCALATED"}

URL_RE = re.compile(r"(https?://|www\.|\b[\w-]+\.(xyz|top|click|link|ru|tk)\b)", re.I)
URGENCY_RE = re.compile(r"\b(urgent|fast|asap|quick(ly)?|hurry|before .{0,25}(someone|else|minutes|lost)|"
                        r"within \d+ ?(min|minutes|hours)|last one|now or never)\b", re.I)
IMPERSONATION_RE = re.compile(r"\b(pocketpay (support|team|security)|support:|account (locked|suspended)|"
                              r"wallet has been locked|verification fee|restore access)\b", re.I)
SECRECY_RE = re.compile(r"\b(don'?t tell|keep (it|this) (quiet|secret)|between us)\b", re.I)

# Text that tries to give orders to an AI instead of describing a payment.
INJECTION_RE = re.compile(
    r"(ignore (all |any )?(the |your )?(previous|prior|above|earlier) (instructions|rules|messages)"
    r"|disregard (all |your |the )?(rules|instructions|guidelines)"
    r"|system (note|override|prompt|message|instruction)"
    r"|note to (the )?(ai|assistant|bot|model)"
    r"|you are now|developer mode|jailbreak"
    r"|set risk[_ ]?level|risk[_ ]?level\s*=|confidence\s*="
    r"|mark (this |it )?as safe|(approve|clear) (this|it|the payment)( now| immediately| automatically)?\b(?! from)"
    r"|skip (the )?(verification|checks?|review)"
    r"|\bassistant\s*:|\bsystem\s*:"
    r"|</?\s*(system|tool|instructions?|prompt)\s*>)",
    re.I)
HANDLE_RE = re.compile(r"^@[a-z0-9_]{3,30}$")


# ------------------------------------------------------------------------------------------------
# Stage 2: validate and sanitise (runs before the AI)
# ------------------------------------------------------------------------------------------------

def preflight(ctx: dict, duplicates: list[str], detector_result: dict | None = None) -> dict:
    """Check the raw input before any AI call. Returns what was found; route() decides what to do."""
    msg = "" if ctx.get("message_text") == "(no message attached)" else str(ctx.get("message_text") or "")
    note = "" if ctx.get("note") == "(none)" else str(ctx.get("note") or "")
    injection = sorted({m.group(0).strip() for m in INJECTION_RE.finditer(f"{msg}\n{note}")})

    invalid = []
    amount = ctx.get("amount_aed")
    if not isinstance(amount, (int, float)) or not 0 < amount <= MAX_VALID_AMOUNT_AED:
        invalid.append(f"amount {amount!r} is not a valid payment amount")
    if not HANDLE_RE.match(str(ctx.get("recipient_handle") or "")):
        invalid.append(f"recipient handle {ctx.get('recipient_handle')!r} is malformed")
    age_h = None
    try:
        age_h = (DEMO_NOW - datetime.fromisoformat(str(ctx.get("created_at")))).total_seconds() / 3600
        if age_h < -0.1:
            invalid.append("timestamp is in the future")
    except ValueError:
        invalid.append("timestamp is missing or unreadable")

    return {
        "injection": injection,
        "invalid": invalid,
        "age_hours": None if age_h is None else round(age_h, 1),
        "stale": age_h is not None and age_h > STALE_AFTER_HOURS,
        "duplicate_of": duplicates,
        "detector": detector_result,   # Reroute's own trained scam detector (None = no message / no model)
    }


def detector_high(pre: dict) -> bool:
    det = pre.get("detector")
    return bool(det) and det["scam_probability"] >= DETECTOR_NO_CLEAR


def describe_preflight(pre: dict) -> str:
    parts = [f"INJECTION ATTEMPT: {', '.join(repr(x) for x in pre['injection'])}" if pre["injection"]
             else "no injection found",
             f"INVALID: {'; '.join(pre['invalid'])}" if pre["invalid"] else "fields valid",
             f"possible duplicate of {', '.join(pre['duplicate_of'])}" if pre["duplicate_of"] else "not a duplicate",
             (f"STALE ({pre['age_hours']:.0f}h old)" if pre["stale"] else f"fresh ({pre['age_hours']:.0f}h old)")
             if pre["age_hours"] is not None else "age unknown"]
    det = pre.get("detector")
    if det:
        words = f" ({', '.join(det['top_words'])})" if det["top_words"] else ""
        parts.append(f"Reroute detector: {det['scam_probability']:.0%} {det['label']}{words}")
    return " · ".join(parts)


def blocks_ai(pre: dict) -> bool:
    """Input the AI must never see: injection attempts and malformed records."""
    return bool(pre["injection"] or pre["invalid"])


# ------------------------------------------------------------------------------------------------
# Stage 6: guard check and routing
# ------------------------------------------------------------------------------------------------

def code_signals(ctx: dict, recipient: dict) -> dict:
    """Hard, rule-based scam signals computed without the LLM."""
    msg = ctx.get("message_text", "") if ctx.get("message_text") != "(no message attached)" else ""
    signals = {
        "suspicious_link": bool(URL_RE.search(msg)),
        "urgency_pressure": bool(URGENCY_RE.search(msg)),
        "impersonates_pocketpay": bool(IMPERSONATION_RE.search(msg)),
        "secrecy_request": bool(SECRECY_RE.search(msg)),
        "no_message_or_note": not msg and ctx.get("note") in ("(none)", "", None),
    }
    if recipient.get("status") == "ok":
        signals["recipient_reported_for_scams"] = recipient["scam_reports"] >= 2
        signals["brand_new_unverified_account"] = (not recipient["campus_verified"]
                                                   and recipient["account_age_days"] <= 7)
    return signals


def route(ctx: dict, recipient: dict, assessment: dict | None,
          pre: dict | None = None) -> tuple[str, list[str], dict]:
    """Return (action, reasons, code_signals). Rules are checked top to bottom; first match wins."""
    sig = code_signals(ctx, recipient)
    pre = pre or {"injection": [], "invalid": [], "stale": False, "duplicate_of": [], "age_hours": None}

    # 0a. Prompt injection -> staff. The AI never saw it, and code never auto-clears it.
    if pre["injection"]:
        return "ESCALATE", [f"Prompt-injection attempt blocked before the AI saw it "
                            f"(found: {', '.join(repr(x) for x in pre['injection'])})"], sig
    # 0b. Malformed record -> staff. Held, not frozen.
    if pre["invalid"]:
        return "ESCALATE", [f"Malformed payment data ({'; '.join(pre['invalid'])}); needs staff review"], sig

    amount = float(ctx["amount_aed"])
    avg_spend = float(ctx["student"]["avg_daily_spend_aed"])
    hard_scam = (sig["impersonates_pocketpay"]
                 or sig.get("recipient_reported_for_scams", False)
                 or (sig["suspicious_link"] and sig.get("brand_new_unverified_account", False)))

    # 1. Confirmed phishing: hard evidence in code -> staff, regardless of what the LLM says.
    if hard_scam:
        why = [k.replace("_", " ") for k in ("impersonates_pocketpay", "recipient_reported_for_scams",
                                             "suspicious_link", "brand_new_unverified_account") if sig.get(k)]
        return "ESCALATE", [f"Confirmed phishing pattern: {', '.join(why)}"], sig

    # 1b. Possible double payment -> the student confirms they meant to pay twice.
    if pre["duplicate_of"]:
        return "HOLD_FOR_CONFIRMATION", [
            f"Possible duplicate of {', '.join(pre['duplicate_of'])} (same recipient and amount within "
            f"{DUPLICATE_WINDOW_MIN} min); confirm you meant to pay twice"], sig

    # 1c. Old payment request -> the student confirms it's still wanted.
    if pre["stale"]:
        return "HOLD_FOR_CONFIRMATION", [
            f"Payment request is {pre['age_hours']:.0f} hours old (limit {STALE_AFTER_HOURS}h); "
            "confirm it is still wanted"], sig

    # 2. The registry is down -> we can't verify, so ask the student. Never block, never auto-clear.
    if recipient.get("status") != "ok":
        return "HOLD_FOR_CONFIRMATION", [
            f"Recipient could not be verified ({recipient.get('detail', recipient.get('status'))}); "
            "falling back to student confirmation instead of blocking"], sig

    # 3. If the LLM failed or gave up, we continue on code-only rules: safe vendor payments can still
    #    clear, everything else waits for a human. (risk/conf = None means "no AI opinion".)
    risk = assessment["risk_level"] if assessment else None
    conf = assessment["confidence"] if assessment else None
    no_ai = " (AI unavailable; code-only check)" if assessment is None else ""
    det_high = detector_high(pre)
    any_signal = any(v for k, v in sig.items() if k != "no_message_or_note") or det_high

    # 4. LLM is unsure (unfamiliar slang, vague message) -> staff. We do not guess.
    if conf == "low":
        return "ESCALATE", ["AI confidence is low (unclear slang or missing context); flagged for staff "
                            "review instead of guessing"], sig

    # 5. Missing data / secrecy on an unverified account -> staff (held, not frozen).
    if not recipient["campus_verified"] and (ctx.get("note") in ("(none)", "", None) or sig["secrecy_request"]):
        why = "asks to keep it secret" if sig["secrecy_request"] else "has no payment note saying what it is for"
        return "ESCALATE", [f"Transfer to an unverified account {why}; needs staff review"], sig

    # 6. High risk from the LLM but no hard evidence -> student decides with a warning.
    if risk == "high":
        return "HOLD_FOR_CONFIRMATION", ["AI rates this high risk but there is no hard scam evidence; "
                                         "asking the student to confirm"], sig

    # 7. Amount guard.
    if amount > LARGE_AMOUNT_AED or amount > SPEND_MULTIPLIER * avg_spend:
        return "HOLD_FOR_CONFIRMATION", [f"AED {amount:.0f} is far above this student's normal spend "
                                         f"(avg AED {avg_spend:.0f}/day); needs confirmation"], sig

    # Our trained detector can't block anything, but a likely-scam message never auto-clears.
    looks_safe = (risk == "low" or (risk is None and not any_signal)) and not det_high

    # 8. Safe everyday purchase from a verified campus vendor/club.
    if (recipient["kind"] in ("vendor", "org") and recipient["campus_verified"]
            and amount <= AUTO_CLEAR_LIMIT_AED and looks_safe):
        return "CLEAR", [f"Verified campus {recipient['kind']}, normal amount (AED {amount:.0f}), "
                         f"low risk{no_ai}"], sig

    # 9. Small transfer to a trusted friend with history.
    if (recipient["kind"] == "peer" and recipient["trust_score"] >= TRUSTED_PEER_SCORE
            and ctx["prior_txns_with_recipient"] >= TRUSTED_PEER_MIN_HISTORY
            and amount <= AUTO_CLEAR_LIMIT_AED and looks_safe):
        return "CLEAR", [f"Trusted peer (score {recipient['trust_score']}, "
                         f"{ctx['prior_txns_with_recipient']} past payments), small amount, low risk{no_ai}"], sig

    # 10. Everything else -> student confirms.
    if det_high:
        return "HOLD_FOR_CONFIRMATION", [f"Reroute's scam detector rates the message "
                                         f"{pre['detector']['scam_probability']:.0%} likely scam, so it can't "
                                         f"auto-clear; asking the student to confirm{no_ai}"], sig
    return "HOLD_FOR_CONFIRMATION", ["Not clearly safe (peer transfer without enough history or "
                                     f"medium risk); asking the student to confirm{no_ai}"], sig


# ------------------------------------------------------------------------------------------------
# Stage 8: last re-check inside the write gateway
# ------------------------------------------------------------------------------------------------

def final_clear_check(fresh_ctx: dict, pre: dict) -> str | None:
    """Re-check, from freshly read data, that an auto-clear is still allowed. Returns a problem or None."""
    if fresh_ctx.get("state") != "FLAGGED":
        return f"payment is no longer FLAGGED (now {fresh_ctx.get('state')})"
    if not 0 < float(fresh_ctx.get("amount_aed") or 0) <= AUTO_CLEAR_LIMIT_AED:
        return f"amount AED {fresh_ctx.get('amount_aed')} is outside the auto-clear limit"
    if pre["injection"] or pre["invalid"] or pre["duplicate_of"] or pre["stale"]:
        return "pre-flight flags present"
    if detector_high(pre):
        return "Reroute detector rates the message as a likely scam"
    return None
