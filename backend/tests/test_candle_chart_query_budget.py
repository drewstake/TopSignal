import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from app import main
from app.models import ProjectXMarketCandle
from app.services.projectx_client import ProjectXClientError


@pytest.mark.parametrize("resolve_alias", [False, True])
@pytest.mark.parametrize("provider_fails", [False, True])
def test_chart_refresh_does_not_reload_each_cached_candle(monkeypatch, resolve_alias, provider_fails):
    owner = "00000000-0000-0000-0000-000000000001"
    contract = "CON.F.US.MNQ.M26"
    start = datetime(2026, 4, 1, 13, 30, tzinfo=timezone.utc)
    engine = create_engine("sqlite+pysqlite:///:memory:")
    ProjectXMarketCandle.__table__.create(engine)
    factory = sessionmaker(bind=engine)
    with factory() as seed:
        seed.add_all([ProjectXMarketCandle(user_id=owner, contract_id=contract, symbol="MNQ",
            live=False, unit="minute", unit_number=5, candle_timestamp=start+timedelta(minutes=5*i),
            open_price=100+i, high_price=101+i, low_price=99+i, close_price=100+i,
            volume=10, is_partial=False, fetched_at=start) for i in range(40)])
        seed.commit()
    with factory() as db:
        column_loads = []
        def observe(state):
            if state.is_column_load:
                column_loads.append(str(state.statement))
        event.listen(db, "do_orm_execute", observe)

        class Client:
            def retrieve_bars(self, **kwargs):
                assert not db.in_transaction(), "Provider I/O must not occupy a database connection"
                if provider_fails:
                    raise ProjectXClientError("Provider unavailable", status_code=503)
                return [{"timestamp": start+timedelta(minutes=200), "open": 140,
                    "high": 141, "low": 139, "close": 140, "volume": 10}]

        monkeypatch.setattr(main, "get_authenticated_user_id", lambda: owner)
        monkeypatch.setattr(main, "_projectx_client_for_user", lambda *_a, **_kw: Client())
        monkeypatch.setattr(main, "resolve_market_contract", lambda *_a, **_kw: (contract, "MNQ"))
        # The service resolver receives an already resolved delivery contract.
        result = main.get_projectx_market_candles(contract_id="MNQ" if resolve_alias else contract,
            symbol="MNQ", start=start, end=start+timedelta(minutes=210), live=False,
            unit="minute", unit_number=5, limit=500, include_partial_bar=False,
            refresh=False, repair=False, db=db)
        assert len(result) == (40 if provider_fails else 41)
        assert result[0]["close"] == 100
        assert not column_loads, f"Chart reloaded {len(column_loads)} expired candle rows"
    engine.dispose()
