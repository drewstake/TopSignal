from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import pytest
from tools.compare_paper_models import training_for_fold


def test_fold_never_trains_on_validation_dates_or_future_labels():
    at=datetime(2026,10,30,13,35,tzinfo=timezone.utc)
    safe=SimpleNamespace(features=SimpleNamespace(day='2026-10-29'),label_end=at-timedelta(minutes=15))
    future=SimpleNamespace(features=SimpleNamespace(day='2026-10-30'),label_end=at+timedelta(minutes=15))
    fold={'training':['2026-10-29'],'validation':['2026-10-30']}
    assert training_for_fold([safe]*300+[future],fold,at)==(safe,)*300
    assert training_for_fold([safe]*300+[future],dict(fold,validation='2026-10-30'),at)==(safe,)*300
    with pytest.raises(ValueError):training_for_fold([safe]*300,{'training':['2026-10-30'],'validation':['2026-10-30']},at)
    late=SimpleNamespace(features=safe.features,label_end=at-timedelta(minutes=14))
    with pytest.raises(ValueError):training_for_fold([late]*300,fold,at)
