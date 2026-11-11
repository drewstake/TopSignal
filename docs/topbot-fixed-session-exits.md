# Fixed exits for TopBot

Set `strategy_params.exit_mode` to `fixed` for a persistent 25-point stop loss
and 25-point take profit on new entries. The preference is account-scoped and
survives Stop / Start, contract rolls, worker restarts and trading-day rollover.
The strategy panel displays fixed exits without an expiration date. Other
accounts and configs without this preference retain the `trailing` default.
Changing it does not enable a bot or alter an existing trade's saved exit plan.

The existing `fixed_bracket_session` override remains supported when the base
mode is trailing. Persistent fixed mode takes precedence over that dated override.

The DOM strategy accepts `strategy_params.fixed_bracket_session` as a canonical
`YYYY-MM-DD` trading date. When it matches the current New York trading session,
new entries submit a native 100-tick stop and 100-tick take-profit bracket:
25 MNQ points in each direction. The default remains the existing 25-point
trailing stop, activated after a 25-point favorable move.

The override expires for new entries at the existing 6 p.m. New York session
rollover. It survives a worker restart without extending into another session.
Each order saves its exit mode before submission. Subsequent config edits or
session rollover do not change an existing trade's management policy.

Fixed trades verify the owned protective stop and a matching same-parent,
same-contract, same-account, single-contract limit target at the entry fill
plus/minus 25 points. The worker never trails these stops. Missing or mismatched
protection uses the existing verified, fenced flatten/reconciliation path.
Position/session management and fill statistics continue as before.

The existing daily loss limit, +$500 daily profit target and session cutoff
remain effective and can close a position before its fixed stop or target.
The retired +$200-to-$0 break-even profit stop remains disabled.

For a dated override, the strategy panel checks the server-reported current
trading session. No migration, historical-data import, retention change,
or new market-data persistence is required.
