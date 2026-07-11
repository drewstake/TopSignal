import math
import os

import pytest
from pydantic import ValidationError

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.bot_schemas import BotConfigCreateIn, BotConfigUpdateIn


def _create_payload(**overrides):
    values = {
        "name": "Safety bot",
        "account_id": 123,
        "contract_id": "CON.F.US.MNQ.U26",
    }
    values.update(overrides)
    return values


@pytest.mark.parametrize("field", ["order_size", "max_contracts", "max_open_position"])
def test_create_rejects_fractional_contract_quantities(field):
    with pytest.raises(ValidationError, match="whole numbers"):
        BotConfigCreateIn(**_create_payload(**{field: 1.5}))


@pytest.mark.parametrize("value", [math.inf, -math.inf, math.nan, 10_001])
def test_create_rejects_non_finite_or_excessive_order_sizes(value):
    with pytest.raises(ValidationError):
        BotConfigCreateIn(**_create_payload(order_size=value))


def test_update_rejects_fractional_contract_quantity():
    with pytest.raises(ValidationError, match="whole numbers"):
        BotConfigUpdateIn(order_size=2.25)
