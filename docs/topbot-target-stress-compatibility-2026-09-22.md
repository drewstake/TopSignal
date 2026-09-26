# Target-fill stress compatibility revision

This prospective revision registers the target-fill wrapper against replay engine
`5.4.0-topbot-all-sessions` before any new historical replay. The resulting engine
is `5.4.0-topbot-all-sessions+target-through-1tick-v2`, with execution model
`observed_1m_target_through_one_tick_v2`. No historical experiment or candidate
promotion is part of this compatibility update.

The reviewed dependency changes allow runtime TopBot to evaluate all exchange
sessions and reject mathematical-model artifacts in the ordinary replay engine.
Frozen fixtures bearing `research_revision` retain their configured decision,
entry and pending-fill session limits. The research runner explicitly imports
the legacy preset so the new mathematical default cannot replace its fixture.
The original opening-drive fixture is unchanged.

The one-tick target confirmation rule, original target price, adverse slippage,
explicit $0.61 per-side fee, stop priority, independent calendar exits, zero
added entry delay and original nine-case design remain unchanged. Synthetic
tests cover these execution rules, session boundaries, immutable manifests and
rejection of changed dependencies. Exact normalized source hashes remain pinned
in the wrapper; the guard is not bypassed or weakened.

The September 4 protocol and reported v1 results remain immutable. Their source
captures identify the old implementation. New v2 results, if generated later,
must carry the new engine/model identifiers and source hashes and must not be
presented as a rerun on the identical v1 engine or as unseen validation.

## v3 dependency revision — 26 September 2026

Removing the EMA/VWAP pullback strategy changed all three pinned dependencies:
`bot_backtesting.py` now rejects in-app TopBot replays and no longer normalizes
research fixture parameters to the removed preset; `research_topbot.py` uses the
current TopBot settings as its base (fixtures still override strategy parameters
and the 200-bar warmup); and `fixtures/topbot_research.py` drops the
`baseline_v5`, `v5_long` and `v5_long_atr` controls. The `opening_drive` rule,
its settings and its calendar exit are unchanged. The wrapper is re-registered as
engine `5.4.0-topbot-all-sessions+target-through-1tick-v3`, execution model
`observed_1m_target_through_one_tick_v3`, with the new normalized hashes pinned.
No historical replay was run for this revision. Earlier v1/v2 results remain
immutable and cannot be reproduced byte-identically from the current sources.
