"""Six fixed one-parameter neighbors and one unchanged opening-drive control.

Preparation only: these rules do not report an experiment or establish profit.
The conditional protocol requires the original opening drive to pass its
corrected-fee numerical screen before running these neighbors. No neighbor
results informed these choices. Timing, calendar, sizing and risk controls stay
with the original rule; only the declared single parameter changes per case.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
from pathlib import Path
from types import ModuleType

from tools.fixtures import topbot_research as _original


REVISION = "mnq_opening_drive_fixed_neighbors_20260904_v1"
SOURCE_REVISION = _original.REVISION
# Normalize CRLF/LF via read_text, so identical source remains reproducible on
# Windows and Linux. A changed original must not silently change this protocol.
SOURCE_SHA256 = "d0230d261f3e5f00f6f876756086b873987eb540ae2c6a6b1798ba2b376d80e6"
_SOURCE_PATH = Path(_original.__file__)
_SOURCE_TEXT = _SOURCE_PATH.read_text(encoding="utf-8")
if hashlib.sha256(_SOURCE_TEXT.encode("utf-8")).hexdigest() != SOURCE_SHA256:
    raise RuntimeError("Opening-drive source changed; review and preregister before updating its frozen hash")

_BASE_PARAMETERS = deepcopy(_original.CANDIDATES["opening_drive"]["parameters"])
_NEIGHBORS = {
    "opening_drive_center": (None, None),
    "opening_drive_displacement_060": ("displacement_fraction", 0.60),
    "opening_drive_displacement_070": ("displacement_fraction", 0.70),
    "opening_drive_stop_040": ("range_stop_multiple", 0.40),
    "opening_drive_stop_060": ("range_stop_multiple", 0.60),
    "opening_drive_reward_175": ("reward_multiple", 1.75),
    "opening_drive_reward_225": ("reward_multiple", 2.25),
}
CANDIDATES = {}
_RULE_MODULES = {}
for _variant, (_parameter, _value) in _NEIGHBORS.items():
    _parameters = deepcopy(_BASE_PARAMETERS)
    if _parameter is not None:
        _parameters[_parameter] = _value
    CANDIDATES[_variant] = {
        "description": (
            "Unchanged original opening drive; verifies center parity."
            if _parameter is None else
            f"Original opening drive with only {_parameter} changed to {_value:g}."
        ),
        "hypothesis": (
            "The center must reproduce the original rule under identical data, costs and risk; this is a repeated control, not a new independent hypothesis."
            if _parameter is None else
            "A credible opening-drive effect should remain useful under this predeclared nearby single-parameter change; this is a robustness check, not a search for the best setting."
        ),
        "parameters": _parameters,
        "changed_parameter": _parameter,
        "source_revision": SOURCE_REVISION,
        "source_normalized_sha256": SOURCE_SHA256,
        "search_budget": "six additional neighbors; one repeated unchanged center; no combinations",
    }
    # Each variant gets its own globals and parameter table. Neither evaluation
    # nor module initialization mutates the imported original or another case.
    _module = ModuleType(f"_private_{REVISION}_{_variant}")
    _module.__file__ = str(_SOURCE_PATH)
    _module.__package__ = "tools.fixtures"
    exec(compile(_SOURCE_TEXT, str(_SOURCE_PATH), "exec"), _module.__dict__)
    _module.CANDIDATES = {
        "opening_drive": {
            **deepcopy(_module.CANDIDATES["opening_drive"]),
            "parameters": deepcopy(_parameters),
        },
    }
    _RULE_MODULES[_variant] = _module


def required_warmup_bars(variant):
    return _RULE_MODULES[variant].required_warmup_bars("opening_drive")


def get_settings(variant):
    settings = deepcopy(_RULE_MODULES[variant].get_settings("opening_drive"))
    settings["strategy_params"]["research_revision"] = REVISION
    return settings


def should_flatten(entry_timestamp, event_time, variant):
    return _RULE_MODULES[variant].should_flatten(entry_timestamp, event_time, "opening_drive")


def evaluate(candles, variant, position_qty=0.0):
    signal = _RULE_MODULES[variant].evaluate(candles, "opening_drive", position_qty=position_qty)
    payload = {
        **signal.raw_payload,
        "strategy_revision": REVISION,
        "research_variant": variant,
        "source_strategy_revision": SOURCE_REVISION,
        "source_normalized_sha256": SOURCE_SHA256,
    }
    return replace(signal, raw_payload=payload)
