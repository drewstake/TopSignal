"""Published TopstepX MNQ transaction fees, excluding slippage.

Verified September 4, 2026 against Topstep's July 28, 2026 fee schedule:
https://help.topstep.com/en/articles/8284213-topstepx-commissions-and-fees
Round trip: $0.70 exchange + $0.02 NFA + $0.50 commission = $1.22.
The replay engine charges half on entry and half on exit, per contract.
"""

MNQ_FEES_PER_CONTRACT_PER_SIDE = 0.61
