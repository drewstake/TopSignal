"""Reporting-only execution measurements. Never used by order/risk decisions."""
from datetime import datetime, timedelta
import math


def delta_ms(later, earlier):
    if not isinstance(later, str) or not isinstance(earlier, str) or not later or not earlier:
        return None
    try:
        a, b = (datetime.fromisoformat(v.replace("Z", "+00:00")) for v in (later, earlier))
        if a.tzinfo is None or b.tzinfo is None:
            return None
        return round((a-b).total_seconds()*1000, 3)
    except (TypeError, ValueError):
        return None


def entry_metrics(request, side, price, fill_at, observed_at):
    evidence, timing = request.get("entryEvidence") or {}, request.get("submissionTiming") or {}
    evidence = evidence if isinstance(evidence, dict) else {}
    timing = timing if isinstance(timing, dict) else {}
    quote = evidence.get("pre_submit") or {}
    quote = quote if isinstance(quote, dict) else {}
    reference = quote.get("ask" if side == "BUY" else "bid")
    age = delta_ms(quote.get("checked_at"), quote.get("observed_at"))
    valid = (quote.get("ready") and age is not None and 0 <= age <= 2000
             and isinstance(reference, (int, float)) and math.isfinite(reference) and reference > 0)
    recheck = evidence.get("recheck") or {}
    recheck = recheck if isinstance(recheck, dict) else {}
    qualified_at = None
    try:
        qualified_at = (datetime.fromisoformat(recheck["episode_at"])+timedelta(seconds=recheck["required_persistence_seconds"])).isoformat()
    except (KeyError, ValueError, TypeError, OverflowError):
        pass
    return {"submission": timing, "decision_to_submit_ms": delta_ms(timing.get("submitted_at"), evidence.get("decision_at")),
            "final_risk_check_to_submit_ms": delta_ms(timing.get("submitted_at"), recheck.get("checked_at")),
            "qualification_to_submit_ms": delta_ms(timing.get("submitted_at"), qualified_at),
            "submit_to_fill_ms": delta_ms(fill_at, timing.get("submitted_at")),
            "fill_to_observation_ms": delta_ms(observed_at, fill_at),
            "reference_price": reference if valid else None,
            "entry_slippage_ticks": round((price-reference)*(1 if side == "BUY" else -1)*4, 6) if valid else None,
            "reference": "Executable worker quote before submission; positive slippage is adverse",
            "clock_basis": "Request duration uses monotonic time; provider/local timestamp offsets are unverified",
            "pre_submit_evidence": quote.get("evidence"), "implementation": evidence.get("implementation")}
