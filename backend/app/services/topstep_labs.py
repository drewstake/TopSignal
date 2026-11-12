"""Lab prices supplied November 2026; account-derived fees are estimates.

Only explicit drop IDs or identifiable product names qualify. Provider naming
is not a billing feed: ambiguous Lab accounts must never use standard pricing.
Keep prices aligned with frontend/src/lib/topstepLabs.ts (including old drops).
"""

from dataclasses import dataclass
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


def is_labs_account_name(name: str) -> bool:
    return bool(re.search(
        r"\b(?:LABS?|DROP|STATIC|FREEDOM|CHALLENGE|DAILY[\s_-]+PAYOUTS)\b",
        name.replace("_", " "), re.I,
    ))


def lab_drop_from_account_name(name: str) -> LabDrop | None:
    name = re.sub(r"[\s_-]+", " ", name.upper()).strip()
    # Funded successors and practice accounts are not new purchases.
    if re.search(r"\b(?:\d+K)?(?:XFA|LFA|EXPRESS|LIVE|FUNDED|PRACTICE)\b", name):
        return None
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
