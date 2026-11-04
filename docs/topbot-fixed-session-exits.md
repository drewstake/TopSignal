# Fixed exits for one TopBot session

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

The existing daily loss limit, +$400 daily profit target and session cutoff
remain effective and can close a position before its fixed stop or target.
The retired +$200-to-$0 break-even profit stop remains disabled.

The strategy panel shows whether the override applies to the server-reported
current trading session. No migration, historical-data import, retention change,
or new market-data persistence is required.
