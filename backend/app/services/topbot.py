"""TopBot's code-owned MNQ preset and account-scoped preparation.

Tune TOPBOT_SETTINGS here; each new run applies these values. Stored configs
remain the execution/audit snapshots, rather than an operator-facing editor.
"""

from copy import deepcopy
import re

from sqlalchemy.orm import Session

from ..bot_schemas import BotConfigCreateIn, BotConfigUpdateIn
from ..models import BotConfig, BotRun
from .bot_service import (
    _require_owned_account,
    _require_projectx_trade_data_source,
    create_bot_config,
    update_bot_config,
)
from .projectx_client import ProjectXClient
from .topbot_strategy import HISTORY_BARS, normalize_params


TOPBOT_SETTINGS = {
    "symbol": "MNQ",
    "strategy_type": "topbot_adaptive",
    "strategy_params": normalize_params(),
    "timeframe_unit": "minute",
    "timeframe_unit_number": 5,
    "lookback_bars": HISTORY_BARS,
    "fast_period": 9,
    "slow_period": 21,
    "order_size": 1,
    "max_contracts": 1,
    "max_daily_loss": 250,
    "max_open_position": 1,
    "max_trades_per_day": 30,
    "allowed_contracts": ["MNQ", "F.US.MNQ"],
    "trading_start_time": "09:30",
    "trading_end_time": "15:45",
    "cooldown_seconds": 300,
    "max_data_staleness_seconds": 600,
    "allow_market_depth": False,
}


def resolve_topbot_contract(client: ProjectXClient) -> str:
    """Select only the provider's active MNQ delivery, never a search near-match."""
    rows = client.search_contracts(search_text="F.US.MNQ", live=False)
    for row in rows:
        contract_id = str(row.get("id") or "").strip()
        if row.get("active_contract") is True and re.fullmatch(r"CON\.F\.US\.MNQ\.[A-Z]\d{2}", contract_id):
            return contract_id
    raise ValueError("No active MNQ contract is available from ProjectX. Try again when market data is available.")


def prepare_topbot(
    db: Session, *, user_id: str, account_id: int, dry_run: bool, contract_id: str
) -> BotConfig:
    """Apply the preset only to a stopped bot belonging to this account.

    Lock existing configs before the account, matching start_bot_run's lock
    order. The account lock also serializes first-run creation requests.
    The caller starts the run in this same transaction.
    """
    if not re.fullmatch(r"CON\.F\.US\.MNQ\.[A-Z]\d{2}", contract_id):
        raise ValueError("TopBot requires an MNQ contract.")
    configs = (
        db.query(BotConfig)
        .filter(BotConfig.user_id == user_id, BotConfig.account_id == account_id)
        .order_by(BotConfig.id)
        .populate_existing()
        .with_for_update()
        .all()
    )
    account = _require_owned_account(db, user_id=user_id, account_id=account_id, lock_for_update=True)
    _require_projectx_trade_data_source(account)
    # Re-read after the account lock: a concurrent first-run request may have
    # inserted a config while this request waited for that lock.
    configs = (
        db.query(BotConfig)
        .filter(BotConfig.user_id == user_id, BotConfig.account_id == account_id)
        .order_by(BotConfig.id)
        .populate_existing()
        .all()
    )
    running = (
        db.query(BotRun.id)
        .filter(BotRun.user_id == user_id, BotRun.account_id == account_id, BotRun.status == "running")
        .first()
    )
    if running is not None or any(config.enabled for config in configs):
        raise ValueError("Stop automation on this account before starting a new TopBot run.")

    existing = next((config for config in configs if config.strategy_type == "topbot_adaptive"), None)
    values = deepcopy(TOPBOT_SETTINGS)
    values.update(
        account_id=account_id, execution_mode="dry_run" if dry_run else "live",
        enabled=False, contract_id=contract_id,
    )
    if existing is not None:
        return update_bot_config(
            db, user_id=user_id, bot_config_id=existing.id, payload=BotConfigUpdateIn(**values)
        )
    # Names are unique per user, so include the account for automatic creation.
    return create_bot_config(
        db, user_id=user_id, payload=BotConfigCreateIn(name=f"TopBot {account_id}", **values)
    )
