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
import unicodedata
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

# Round 2 curveballs (High School track, Domain 1)
SPLIT_WINDOW_MIN = 60                      # HS-1.1: rolling window per student + recipient
SPLIT_THRESHOLD_AED = AUTO_CLEAR_LIMIT_AED  # ...the hold threshold scammers try to stay under
LOOKALIKE_SWAPS = str.maketrans({"0": "o", "1": "l"})   # HS-1.2: 0 -> o, 1 -> l
GUARDIAN_UNDER_AGE = 16                    # HS-1.5: students under 16...
GUARDIAN_PEER_LIMIT_AED = 100              # ...need a guardian for peer transfers above this
PEER_CHANNELS = ("p2p", "p2p_link", "p2p_request", "p2p_student")

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
    r"|note (to|for) (the )?([\w-]+ ){0,2}(ai|assistant|bot|model|agent|reviewer|checker|system)\b"
    r"|\b(dear|hey|attention|hi) (ai|assistant|bot|reroute|model|agent)\b"
    r"|you are now|developer mode|jailbreak|\b(admin|system|security) override"
    r"|set risk[_ ]?level|risk[_ ]?level\s*=|confidence\s*="
    r"|mark (this |it |the |that )?(payment |transaction |transfer |seller )?(as )?"
    r"(safe|verified|approved|legit(imate)?|low[- ]risk)\b"
    r"|\bpre-?approved\b|\bwhitelisted\b"
    r"|(this|the) (seller|payment|transaction|transfer) (is|has been) (already )?(verified|approved|cleared)"
    r"|(approve|clear|release) (this|it|the (payment|transaction|transfer))"
    r"( now| immediately| automatically| right away)?\b(?! from)"
    r"|skip (the )?(verification|checks?|review)"
    r"|\bassistant\s*:|\bsystem\s*:|\[\s*(system|admin|assistant)\s*\]"
    r"|</?\s*(system|tool|instructions?|prompt)\s*>)",
    re.I)
# Characters scammers use to hide words from filters (zero-width spaces, joiners, BOM).
HIDDEN_CHARS_RE = re.compile("[​-‏⁠-⁤﻿­]")


def normalize_text(text: str) -> str:
    """HS-1.3: undo common tricks before looking for instructions (look-alike Unicode, hidden characters,
    extra spaces), so 'S​YSTEM :' or full-width letters can't slip past the filter."""
    text = unicodedata.normalize("NFKC", str(text or ""))
    return re.sub(r"[ \t]+", " ", HIDDEN_CHARS_RE.sub("", text))
HANDLE_RE = re.compile(r"^@[a-z0-9_]{3,30}$")


# ------------------------------------------------------------------------------------------------
# Stage 2: validate and sanitise (runs before the AI)
# ------------------------------------------------------------------------------------------------

def normalize_handle(handle: str) -> str:
    """HS-1.2: lower-case, swap 0 -> o and 1 -> l, then strip every symbol.
    '@CampusCanteen0' -> 'campuscanteeno', '@campus.canteen' -> 'campuscanteen'."""
    return re.sub(r"[^a-z0-9]", "", str(handle or "").lower().translate(LOOKALIKE_SWAPS))


def _edits(a: str, b: str) -> int:
    """Levenshtein distance: how many single-letter changes turn a into b."""
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def lookalike_of(handle: str, verified_handles: list[str]) -> str | None:
    """HS-1.2: the verified vendor/club this handle imitates, or None. An exact match is the real account.
    A close match that isn't exact (same name after normalising, or 1-2 letters off) is a lookalike."""
    handle = str(handle or "").strip()
    if handle in verified_handles:
        return None
    norm = normalize_handle(handle)
    best, best_d = None, None
    for real in verified_handles:
        target = normalize_handle(real)
        if len(target) < 5:
            continue
        allowed = 1 if len(target) < 9 else 2          # short names: only 1 change counts as "close"
        d = _edits(norm, target)
        if d <= allowed and (best_d is None or d < best_d):
            best, best_d = real, d
    return best


def preflight(ctx: dict, duplicates: list[str], detector_result: dict | None = None,
              recent: dict | None = None, verified_handles: list[str] | None = None,
              ticket: dict | None = None) -> dict:
    """Check the raw input before any AI call. Returns what was found; route() decides what to do.

    recent:           earlier payments from this student to this recipient in the split window (HS-1.1)
    verified_handles: the verified campus vendors and clubs, for the lookalike check (HS-1.2)
    """
    msg = "" if ctx.get("message_text") == "(no message attached)" else str(ctx.get("message_text") or "")
    note = "" if ctx.get("note") == "(none)" else str(ctx.get("note") or "")
    injection = sorted({m.group(0).strip() for m in INJECTION_RE.finditer(normalize_text(f"{msg}\n{note}"))})

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
        "recent": recent or {"count": 0, "total": 0.0, "txn_ids": []},
        "lookalike_of": lookalike_of(ctx.get("recipient_handle"), verified_handles or []),
        "ticket": ticket,              # ticket-ownership check for resale tickets (None = not a ticket sale)
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
    if pre.get("lookalike_of"):
        parts.append(f"LOOKALIKE of verified {pre['lookalike_of']}")
    ticket = pre.get("ticket")
    if ticket:
        parts.append(f"ticket resale for {ticket['event']}: seller "
                     + ("owns a ticket" if ticket["owns"] else "does NOT own a ticket"))
    recent = pre.get("recent") or {}
    if recent.get("count"):
        parts.append(f"already sent AED {recent['total']:.0f} to this recipient in the last {SPLIT_WINDOW_MIN} min "
                     f"({recent['count']} payment{'s' if recent['count'] != 1 else ''})")
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


STRICTNESS = {"CLEAR": 0, "HOLD_FOR_CONFIRMATION": 1, "ESCALATE": 2}


def hard_scam(sig: dict) -> bool:
    """Confirmed phishing evidence found by code alone."""
    return bool(sig["impersonates_pocketpay"] or sig.get("recipient_reported_for_scams", False)
                or (sig["suspicious_link"] and sig.get("brand_new_unverified_account", False)))


# Every kind of opinion the AI could give. If the rules route a payment the same way for all of them,
# the AI can't change the outcome, so it isn't asked (faster, cheaper, nothing to trick).
_POSSIBLE_OPINIONS = [None] + [{"risk_level": r, "confidence": c}
                               for r in ("low", "medium", "high") for c in ("high", "medium", "low")]


def code_only_decision(ctx: dict, recipient: dict, pre: dict) -> str | None:
    """Why the code rules alone decide this payment (no AI call needed), or None if the AI's opinion matters."""
    if blocks_ai(pre):
        return "Pre-flight check failed (injection or broken data), so the AI must never see it"
    if pre.get("lookalike_of"):
        return f"Lookalike of {pre['lookalike_of']}: code escalates it whatever the AI says"
    if (pre.get("ticket") or {}).get("owns") is False:
        return "Ticket resale by someone who doesn't own the ticket: code escalates it"
    sig = code_signals(ctx, recipient)
    if hard_scam(sig):
        return "Confirmed phishing evidence in code: escalated whatever the AI says"
    if detector_high(pre):
        return (f"Our scam detector rates the message {pre['detector']['scam_probability']:.0%}: it can't "
                "auto-clear, so the rules decide without the AI")
    amount = ctx.get("amount_aed")
    clean_vendor = (recipient.get("status") == "ok" and recipient.get("kind") in ("vendor", "org")
                    and recipient.get("campus_verified") and isinstance(amount, (int, float))
                    and 0 < amount <= AUTO_CLEAR_LIMIT_AED
                    and not any(v for k, v in sig.items() if k != "no_message_or_note")
                    and not pre["duplicate_of"] and not pre["stale"])
    if clean_vendor and route(ctx, recipient, None, pre)[0] == "CLEAR":
        return f"Verified campus {recipient['kind']}, AED {amount:g}, no scam signals: cleared by the rules"
    if len({route(ctx, recipient, a, pre)[0] for a in _POSSIBLE_OPINIONS}) == 1:
        return "The rules give the same decision whatever the AI would say"
    return None


def pattern_rules(ctx: dict, recipient: dict, pre: dict) -> list[tuple[str, str]]:
    """Round 2 rules that look beyond the single payment. Each returns (minimum action, reason).
    They can only make a decision stricter, never looser."""
    rules = []
    # Ticket resale: the campus ticket registry says the seller doesn't own a ticket for that event.
    ticket = pre.get("ticket")
    if ticket and ticket.get("owns") is False:
        rules.append(("ESCALATE", f"Ticket resale: {ctx.get('recipient_handle')} is selling a {ticket['event']} "
                                  "ticket but doesn't own one (campus ticket registry)"))
    # HS-1.2 Lookalike vendor: a close-but-not-exact copy of a verified vendor's name is treated as high risk.
    if pre.get("lookalike_of"):
        rules.append(("ESCALATE", f"Lookalike recipient: {ctx.get('recipient_handle')} imitates the verified "
                                  f"{pre['lookalike_of']} but is not that account (likely fake)"))
    # HS-1.1 Split payment: a run of small transfers to the same person adds up past the hold threshold.
    # Real campus shops and clubs are skipped (buying lunch twice is normal); peers and unknown accounts count.
    recent = pre.get("recent") or {}
    amount = ctx.get("amount_aed")
    vendor = (recipient.get("status") == "ok" and recipient.get("kind") in ("vendor", "org")
              and recipient.get("campus_verified"))
    if recent.get("count") and isinstance(amount, (int, float)) and amount > 0 and not vendor:
        total = recent["total"] + amount
        if total > SPLIT_THRESHOLD_AED:
            rules.append(("HOLD_FOR_CONFIRMATION",
                          f"Split-payment pattern: AED {recent['total']:.0f} already sent to "
                          f"{ctx.get('recipient_handle')} in the last {SPLIT_WINDOW_MIN} min "
                          f"({recent['count']} payment{'s' if recent['count'] != 1 else ''}); with this AED "
                          f"{amount:.0f} the total is AED {total:.0f}, over the AED {SPLIT_THRESHOLD_AED} hold limit"))
    return rules


def route(ctx: dict, recipient: dict, assessment: dict | None,
          pre: dict | None = None) -> tuple[str, list[str], dict]:
    """Return (action, reasons, code_signals): the base rules, then the pattern rules on top."""
    pre = pre or {"injection": [], "invalid": [], "stale": False, "duplicate_of": [], "age_hours": None}
    action, reasons, sig = _base_route(ctx, recipient, assessment, pre)
    for level, why in pattern_rules(ctx, recipient, pre):
        if STRICTNESS[level] > STRICTNESS[action]:      # the pattern makes it stricter: it becomes the reason
            action, reasons = level, [why, f"Other checks alone: {reasons[0]}"]
        elif STRICTNESS[level] == STRICTNESS[action] and not pre.get("injection"):
            reasons = [why] + reasons                     # same outcome: the more specific reason goes first
        else:
            reasons = reasons + [why]                     # already stricter: keep it on record
    return action, reasons, sig


def _base_route(ctx: dict, recipient: dict, assessment: dict | None,
                pre: dict) -> tuple[str, list[str], dict]:
    """The Round 1 rules. Rules are checked top to bottom; first match wins."""
    sig = code_signals(ctx, recipient)

    # 0a. Prompt injection -> staff. The AI never saw it, and code never auto-clears it.
    if pre["injection"]:
        return "ESCALATE", [f"Prompt-injection attempt blocked before the AI saw it "
                            f"(found: {', '.join(repr(x) for x in pre['injection'])})"], sig
    # 0b. Malformed record -> staff. Held, not frozen.
    if pre["invalid"]:
        return "ESCALATE", [f"Malformed payment data ({'; '.join(pre['invalid'])}); needs staff review"], sig

    amount = float(ctx["amount_aed"])
    avg_spend = float(ctx["student"]["avg_daily_spend_aed"])
    # 1. Confirmed phishing: hard evidence in code -> staff, regardless of what the LLM says.
    if hard_scam(sig):
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
    if pre.get("lookalike_of"):
        return f"recipient looks like {pre['lookalike_of']} but is not it"
    if fresh_ctx.get("needs_guardian"):
        return "a guardian has to approve this payment first"
    return None


# ------------------------------------------------------------------------------------------------
# HS-1.5: who has to approve a payment
# ------------------------------------------------------------------------------------------------

def guardian_rule(ctx: dict, recipient: dict) -> str | None:
    """Students under 16 need a parent or guardian to approve any peer transfer above AED 100.
    Returns the reason when the rule applies, else None. A missing age counts as under 16 (safe default)."""
    amount = ctx.get("amount_aed")
    if not isinstance(amount, (int, float)) or amount <= GUARDIAN_PEER_LIMIT_AED:
        return None
    peer = recipient.get("kind") == "peer" or (recipient.get("status") != "ok" and ctx.get("channel") in PEER_CHANNELS)
    if not peer:
        return None
    age = (ctx.get("student") or {}).get("age")
    if age is not None and age >= GUARDIAN_UNDER_AGE:
        return None
    who = f"Student is {age}" if age is not None else "Student's age is not on record (treated as under 16)"
    return (f"{who}: peer transfers above AED {GUARDIAN_PEER_LIMIT_AED} need a parent or guardian's approval; "
            "the student's own yes is not enough")


def approvals_needed(action: str, guardian: bool) -> list[str]:
    """Who must say yes, in order, before the money moves. Code decides this, never the chat."""
    if action == "ESCALATE":
        return ["staff", "guardian"] if guardian else ["staff"]
    if guardian:                                   # the guardian's yes replaces the student's
        return ["guardian"]
    return ["student"] if action == "HOLD_FOR_CONFIRMATION" else []


APPROVER_STATE = {"student": "PENDING_CONFIRMATION", "staff": "ESCALATED", "guardian": "PENDING_GUARDIAN"}


def first_state(action: str, guardian: bool) -> str:
    needed = approvals_needed(action, guardian)
    return APPROVER_STATE[needed[0]] if needed else "CLEARED"
