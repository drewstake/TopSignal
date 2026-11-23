"""Topstep one-time purchase prices; account-derived fees are estimates.

Only explicit drop IDs or identifiable product names qualify. Provider naming
is not a billing feed: ambiguous Lab accounts must never use standard pricing.
Keep prices aligned with the frontend Labs and Challenge presets.
"""

from dataclasses import dataclass
from datetime import date
import re


@dataclass(frozen=True)
class LabDrop:
    id: str
    name: str
    fee_cents: int


LAB_DROPS = {
    drop.id: drop for drop in [
        LabDrop("001", "$25K Static", 7500),
        LabDrop("006", "$50K Static", 14900),
        LabDrop("012", "$100K Static", 19900),
        LabDrop("013", "$150K Static", 27900),
        LabDrop("002", "$250K Freedom", 49900),
        LabDrop("010", "$50K Daily Payouts EOD", 11900),
        LabDrop("011", "$50K Static Daily Payouts", 14900),
        LabDrop("007", "The $1.5K Challenge", 9900),
        LabDrop("008", "The $3K Challenge - Return", 19900),
        LabDrop("009", "The $3K Challenge - Revenge", 6500),
    ]
}


@dataclass(frozen=True)
class OneTimeFee:
    label: str
    fee_cents: int
    source: str
    product_tag: str


CHALLENGE_FEES = {500: 4900, 1500: 9900, 3000: 19900, 6000: 39900, 9000: 59900}
_TARGET = r"(?:\d{1,2}(?:\.5)?K|\d{1,2},\d{3}|\d{1,5})"
_CHALLENGE_LABEL = re.compile(
    r"(?:TOPSTEP )?(?:THE )?(?:"
    rf"\$?(?P<before>{_TARGET}) (?:REWARD )?CHALLENGE"
    rf"|CHALLENGE(?: ROUND)? \$?(?P<after>{_TARGET})"
    r")(?: (?:CHALLENGE ROUND|ROUND 1|ROUND))?(?: \d+)?"
)


def is_labs_account_name(name: str) -> bool:
    return bool(re.search(
        r"\b(?:LABS?|DROP|STATIC|FREEDOM|CHALLENGE|DAILY[\s_-]+PAYOUTS|PAYOUT(?:[\s_-]*ROUND)?)\b",
        name.replace("_", " "), re.I,
    ))


def _lab_drop_from_account_name(name: str) -> LabDrop | None:
    ids = re.findall(r"\b(?:LABS?(?: DROP)?|DROP)\s*#?\s*(\d{1,3})\b", name)
    if ids:
        unique_ids = {item.zfill(3) for item in ids}
        return LAB_DROPS.get(unique_ids.pop()) if len(unique_ids) == 1 else None

    sizes = set(re.findall(r"\b(25|50|100|150|250)K(?:TC)?\b", name))
    daily = "DAILY PAYOUTS" in name
    static = bool(re.search(r"\bSTATIC\b", name))
    drop_id = None
    if len(sizes) == 1:
        size = sizes.pop()
        if daily:
            if size == "50":
                drop_id = "011" if static else "010" if re.search(r"\bEOD\b", name) else None
        elif static:
            drop_id = {"25": "001", "50": "006", "100": "012", "150": "013"}.get(size)
        elif size == "250" and re.search(r"\bFREEDOM\b", name):
            drop_id = "002"
    elif not sizes:
        if re.search(r"\b1\.5K CHALLENGE\b", name):
            drop_id = "007"
        elif re.search(r"\b3K CHALLENGE\b", name):
            if "RETURN" in name and "REVENGE" not in name:
                drop_id = "008"
            elif "REVENGE" in name and "RETURN" not in name:
                drop_id = "009"
    return LAB_DROPS.get(drop_id) if drop_id else None


def one_time_fee_from_account_name(name: str, first_seen: date) -> OneTimeFee | None:
    name = re.sub(r"[\s_-]+", " ", name.upper()).strip()
    if re.search(
        r"\b(?:(?:\d+K)?(?:XFA|LFA|EXPRESS|LIVE|FUNDED|PRACTICE)|PAYOUT(?: ?ROUND)?|(?:ROUND|PHASE) (?:2|TWO))\b",
        name,
    ):
        return None

    # Earlier Labs offers reused the same targets at different prices.
    if first_seen >= date(2026, 11, 23) and (match := _CHALLENGE_LABEL.fullmatch(name)):
        amount = (match["before"] or match["after"]).replace(",", "")
        target = int(float(amount[:-1]) * 1000) if amount.endswith("K") else int(amount)
        if target in CHALLENGE_FEES:
            return OneTimeFee(
                f"Topstep ${target:,} Challenge", CHALLENGE_FEES[target],
                "topstep_challenge", f"challenge_{target}",
            )
        return None

    if drop := _lab_drop_from_account_name(name):
        return OneTimeFee(f"Topstep Labs Drop #{drop.id} - {drop.name}", drop.fee_cents,
                          "topstep_labs", f"lab_drop_{drop.id}")
    return None
