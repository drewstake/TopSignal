"""Explicit synthetic eligibility evidence for isolated ledger unit tests."""
from app.services.simulation_eligibility import assessment


def eligible(close, *, owner='a', account=1, config=32, contract='CON.F.US.MNQ.Z26'):
    return assessment(close=close.isoformat(), owner=owner, account=account, config=config, contract=contract,
                      limits=dict(max_contracts=1, max_daily_loss=250, max_trades_per_day=30,
                                  cooldown_seconds=300, max_age_seconds=120))
