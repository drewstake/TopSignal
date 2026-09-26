"""Auditable offline MNQ hypothesis replays. Never connects to a broker.

Every invocation first writes a unique, immutable hypothesis manifest and source
snapshots, then runs each requested period/cost from a fresh portfolio. All of
the existing 2019-2026 cache is reused evidence, including the diagnostic split.
No output of this tool constitutes an untouched evaluation or a live approval.

Fixture contract: CANDIDATES[name] has description, hypothesis and parameters;
evaluate(closed_candles, name) returns SignalResult. Optional get_settings(name)
returns BotConfig overrides and required_warmup_bars(name) returns history size.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from importlib import metadata as package_metadata
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
from time import perf_counter
from types import ModuleType
from uuid import uuid4

BACKEND = Path(__file__).resolve().parents[1]
REPOSITORY = BACKEND.parent
REUSED_HISTORY = (
    "All available 2019-July 2026 history was previously examined. Development "
    "and later diagnostic periods are retrospective; untouched final evaluation "
    "is unavailable. Bootstrap intervals do not correct for repeated strategy selection."
)


def write_new_json(path: Path, value: object) -> None:
    """Never silently replace a previous hypothesis, failed run or ledger."""
    payload = json.dumps(value, indent=2, sort_keys=True, allow_nan=False, default=str)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(payload + "\n")


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def create_run_directory(parent: Path, label: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", label):
        raise ValueError("label must be 1-80 ASCII letters, digits, dots, underscores or hyphens")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    path = parent / f"{stamp}-{label}-{uuid4().hex[:12]}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def resolve_periods(bounds, requested):
    earliest, latest = bounds
    split = datetime(2024, 1, 1, tzinfo=timezone.utc)
    definitions = {
        "full": (earliest, latest),
        "development": (earliest, min(split, latest)),
        "diagnostic": (max(split, earliest), latest),
    }
    periods = {}
    for name in requested:
        start, end = definitions[name]
        if start >= end:
            raise ValueError(f"{name} does not overlap cached history")
        periods[name] = (start, end)
    return periods


def offline_environment() -> None:
    # This changes only this research process, not operator files or settings.
    for name in tuple(os.environ):
        if name.startswith(("PROJECTX_", "TOPSTEP_", "TOPSTEPX_", "DATABENTO_", "SUPABASE_")):
            os.environ.pop(name, None)
    os.environ.update(
        PYTHON_DOTENV_DISABLED="1", DATABASE_URL="sqlite+pysqlite:///:memory:",
        TOPSIGNAL_DB_SCHEMA_INIT="skip", TOPSIGNAL_LIVE_EXECUTION_ENABLED="false",
        TOPSIGNAL_BOT_WORKER_ALLOW_LIVE_EXECUTION="false", TOPSIGNAL_BOT_WORKER_ENABLED="false",
    )

    def block_network(event, _args):
        if event in {"socket.connect", "socket.getaddrinfo"}:
            raise RuntimeError("Offline research forbids network access")

    sys.addaudithook(block_network)


def capture_sources(directory: Path, fixture_path: Path) -> dict:
    paths = sorted(set((BACKEND / "app").rglob("*.py")) | set((BACKEND / "tools").rglob("*.py")) | {
        Path(__file__).resolve(), fixture_path.resolve(), BACKEND / "requirements.txt",
    })
    entries = {}
    for path in paths:
        relative = path.relative_to(REPOSITORY) if path.is_relative_to(REPOSITORY) else Path("external-fixture") / path.name
        payload = path.read_bytes()
        destination = directory / "sources" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as handle:
            handle.write(payload)
        entries[str(relative).replace("\\", "/")] = {
            "sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload),
        }

    def git(*arguments):
        result = subprocess.run(["git", *arguments], cwd=REPOSITORY, check=True, capture_output=True)
        return result.stdout.decode("utf-8", errors="replace")

    diff = git("diff", "HEAD", "--", "backend", "docs")
    with (directory / "working-tree.patch").open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(diff)
    installed_distributions = {}
    for name in ("numpy", "pyarrow", "databento", "SQLAlchemy", "pydantic", "tzdata"):
        try:
            installed_distributions[name] = package_metadata.version(name)
        except package_metadata.PackageNotFoundError:
            installed_distributions[name] = None
    return {
        "git_revision": git("rev-parse", "HEAD").strip(),
        "git_status": git("status", "--short"),
        "working_tree_patch_sha256": hashlib.sha256(diff.encode()).hexdigest(),
        "files": entries, "combined_sha256": fingerprint(entries),
        "python": sys.version,
        "installed_distributions": installed_distributions,
    }


def _trade_summary(trades):
    net = [float(row["net_pnl"]) for row in trades]
    wins = sum(value for value in net if value > 0)
    losses = -sum(value for value in net if value < 0)
    return {
        "trade_count": len(net), "net_pnl": sum(net),
        "gross_pnl_after_slippage": sum(float(row["gross_pnl"]) for row in trades),
        "commission": sum(float(row["commission"]) for row in trades),
        "expectancy": sum(net) / len(net) if net else None,
        "win_rate_percent": 100 * sum(value > 0 for value in net) / len(net) if net else None,
        "profit_factor": wins / losses if losses else None,
        "mean_holding_minutes": sum(
            (datetime.fromisoformat(row["exit_timestamp"]) - datetime.fromisoformat(row["entry_timestamp"])).total_seconds() / 60
            for row in trades
        ) / len(net) if net else None,
    }


def concentration(trades, sessions):
    descending = sorted((float(row["net_pnl"]) for row in trades), reverse=True)
    positive = [value for value in descending if value > 0]
    total = sum(descending)
    positive_sum = sum(positive)
    top_count = max(1, math.ceil(len(descending) * .01)) if descending else 0
    session_net = sorted((float(row["net_pnl"]) for row in sessions), reverse=True)
    year_net = defaultdict(float)
    for row in sessions:
        year_net[row["session"][:4]] += float(row["net_pnl"])
    return {
        "best_trade": descending[0] if descending else None,
        "worst_trade": descending[-1] if descending else None,
        "top_1_percent_trade_count": top_count,
        "top_1_percent_positive_profit_share": sum(positive[:top_count]) / positive_sum if positive_sum else None,
        "net_excluding_best_trade": total - sum(descending[:1]),
        "net_excluding_best_5_trades": total - sum(descending[:5]),
        "net_excluding_best_1_percent_trades": total - sum(descending[:top_count]),
        "net_excluding_best_5_sessions": sum(session_net) - sum(session_net[:5]),
        "net_excluding_best_calendar_year": sum(session_net) - max(year_net.values(), default=0),
        "best_calendar_year_net_pnl": max(year_net.values(), default=0),
    }


def session_bootstrap(sessions, *, repetitions=2000, block_lengths=(5, 20), seed=20260905):
    """Circular moving blocks of observed sessions, including no-trade sessions.

    The P&L series uses exact session-end marks, not the sampled chart. This
    retains dependence within each block but cannot account for unseen regimes,
    parameter selection, data defects or execution-model error.
    """
    import numpy as np

    values = np.asarray([float(row["net_pnl"]) for row in sessions], dtype=float)
    if len(values) < 2:
        return {"session_count": len(values), "estimates": [], "limitation": "Insufficient observed sessions"}
    estimates = []
    for requested_length in block_lengths:
        length = min(requested_length, len(values))
        rng = np.random.default_rng(seed + requested_length)
        sample_means = np.empty(repetitions)
        # Bound temporary memory independently of full-history sample length.
        for offset in range(0, repetitions, 100):
            count = min(100, repetitions - offset)
            starts = rng.integers(0, len(values), size=(count, math.ceil(len(values) / length)))
            indices = ((starts[:, :, None] + np.arange(length)) % len(values)).reshape(count, -1)[:, :len(values)]
            sample_means[offset:offset + count] = values[indices].mean(axis=1)
        low, high = np.quantile(sample_means, [.025, .975])
        estimates.append({
            "block_sessions": length, "repetitions": repetitions,
            "mean_session_pnl_95_percent_interval": [float(low), float(high)],
            "same_length_net_pnl_95_percent_interval": [float(low * len(values)), float(high * len(values))],
            "resampled_fraction_positive": float((sample_means > 0).mean()),
        })
    return {
        "method": "circular moving-block bootstrap of exact session-marked equity changes",
        "session_count": len(values), "seed": seed, "mean_session_pnl": float(values.mean()),
        "estimates": estimates,
        "limitation": "Conditional retrospective uncertainty; positive bootstrap fraction is not probability of future profitability. Blocks do not adjust for strategy selection or model error.",
    }


def summarize_result(result, sessions, exposure_by_side, *, repetitions):
    trades = result["trades"]
    years = defaultdict(list)
    for row in trades:
        years[row["exit_timestamp"][:4]].append(row)
    year_marks = defaultdict(float)
    for row in sessions:
        year_marks[row["session"][:4]] += row["net_pnl"]
    return {
        "metrics": result["metrics"],
        "years": {
            year: {**_trade_summary(years[year]), "mark_to_market_net_pnl": year_marks[year],
                   "long": _trade_summary([row for row in years[year] if row["side"] == "long"]),
                   "short": _trade_summary([row for row in years[year] if row["side"] == "short"])}
            for year in sorted(set(years) | set(year_marks))
        },
        "year_trade_attribution": "exit UTC year; separate session-marked net allocates overnight unrealized P&L",
        "exposure_by_side": exposure_by_side,
        "concentration": concentration(trades, sessions),
        "uncertainty": session_bootstrap(sessions, repetitions=repetitions),
        "trades_sha256": fingerprint(trades), "sessions_sha256": fingerprint(sessions),
        **{key: result[key] for key in ("range", "config_snapshot", "assumptions", "data_quality", "warnings", "notes")},
    }


def historical_screen(summaries):
    """Apply the registered numerical screen; never imply final confirmation.

    Keys are (period, slippage_ticks). Missing experiments remain unevaluated,
    including failed runs, never silently passing. Parameter-neighbor and unseen
    data requirements remain pending until their separate evidence is supplied.
    """
    gates = []

    def gate(name, observed, threshold, passed):
        gates.append({"gate": name, "observed": observed, "required": threshold,
                      "status": "not_evaluated" if observed is None else "pass" if passed else "fail"})

    full = summaries.get(("full", 1.0))
    total = full["metrics"]["trade_count"] if full else None
    gate("full_history_base_cost_trade_count", total, ">= 500", total is not None and total >= 500)
    for period in ("development", "diagnostic"):
        for slip in (1.0, 2.0):
            result = summaries.get((period, slip))
            net = result["metrics"]["net_pnl"] if result else None
            gate(f"{period}_net_at_{slip:g}_ticks", net, "> 0", net is not None and net > 0)
    profitable_years = (
        sum(full["years"].get(str(year), {}).get("mark_to_market_net_pnl", 0) > 0 for year in range(2020, 2026))
        if full else None
    )
    gate("profitable_2020_through_2025_session_marked_years", profitable_years, ">= 5", profitable_years is not None and profitable_years >= 5)
    diagnostic = summaries.get(("diagnostic", 1.0))
    pf = diagnostic["metrics"]["profit_factor"] if diagnostic else None
    gate("diagnostic_base_cost_profit_factor", pf, ">= 1.10", pf is not None and pf >= 1.10)
    estimates = {row["block_sessions"]: row for row in full["uncertainty"]["estimates"]} if full else {}
    for block in (5, 20):
        interval = estimates.get(block, {}).get("mean_session_pnl_95_percent_interval")
        lower = interval[0] if interval else None
        gate(f"full_base_cost_{block}_session_bootstrap_lower_95_percent", lower, "> 0", lower is not None and lower > 0)
    for concentration_key in ("net_excluding_best_5_trades", "net_excluding_best_calendar_year"):
        net = full["concentration"][concentration_key] if full else None
        gate(concentration_key, net, "> 0", net is not None and net > 0)
    status = "fails_registered_screen" if any(row["status"] == "fail" for row in gates) else (
        "incomplete" if any(row["status"] == "not_evaluated" for row in gates) else "passes_measured_gates_only"
    )
    return {
        "status": status, "gates": gates,
        "pending_requirements": ["predeclared nearby parameter variants", "frozen candidate on genuinely untouched evaluation data"],
        "confirmed_profitability": False,
        "scope": "Fixed September 4, 2026 protocol; bootstrap/concentration/year gates use full-history one-tick cost run; diagnostic PF uses one-tick run.",
        "limitations": REUSED_HISTORY,
    }


def make_engine_class(replay, fixture, variant):
    class ResearchEngine(replay.BacktestEngine):
        def __init__(self, **kwargs):
            self.research_session_marks = {}
            self.research_exposure = defaultdict(int)
            self.research_bar_count = 0
            super().__init__(**kwargs)

        def _evaluate_topbot_adaptive(self, candles):
            position_qty = 0.0 if self.position is None else self.position.quantity * (1 if self.position.side == "long" else -1)
            return fixture.evaluate(candles, variant, position_qty=position_qty)

        def _process_open_gap(self, candle):
            # Resting orders have priority. The optional known-calendar risk
            # deadline acts at an actually observed minute open, independently
            # of whether a complete five-minute signal candle was available.
            super()._process_open_gap(candle)
            if self.position is None:
                return
            should_flatten = getattr(fixture, "should_flatten", None)
            if should_flatten is not None and should_flatten(
                self.position.entry_timestamp, candle.candle_timestamp, variant,
            ):
                raw_open = float(candle.open_price)
                self._update_excursion(raw_open, raw_open)
                self._close_position(
                    raw_exit_price=raw_open, exit_timestamp=candle.candle_timestamp,
                    exit_reason="scheduled_session_flatten",
                )

        def _record_equity(self, *, event_time, mark_price):
            super()._record_equity(event_time=event_time, mark_price=mark_price)
            self.research_bar_count += 1
            if self.position is not None:
                self.research_exposure[self.position.side] += 1
            equity = self.cash
            if self.position is not None:
                equity += replay._price_pnl(
                    side=self.position.side, entry=self.position.entry_price, exit=mark_price,
                    quantity=self.position.quantity, tick_size=self.settings.tick_size,
                    tick_value=self.settings.tick_value,
                )
            self.research_session_marks[replay.trading_day_date(event_time).isoformat()] = float(equity)

        def _replace_last_equity(self, *, event_time):
            super()._replace_last_equity(event_time=event_time)
            self.research_session_marks[replay.trading_day_date(event_time).isoformat()] = float(self.cash)

        def session_ledger(self):
            previous = self.settings.starting_balance
            rows = []
            for day, equity in self.research_session_marks.items():
                rows.append({"session": day, "ending_equity": equity, "net_pnl": equity - previous})
                previous = equity
            return rows

    return ResearchEngine


def main() -> int:
    sys.path.insert(0, str(BACKEND))
    from app.trading_costs import MNQ_FEES_PER_CONTRACT_PER_SIDE

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=BACKEND / "tools/fixtures/topbot_research.py")
    parser.add_argument("--cache-dir", type=Path, default=BACKEND / "storage/databento")
    parser.add_argument("--output-root", type=Path, default=BACKEND / "storage/research/experiments")
    parser.add_argument("--label", required=True)
    parser.add_argument("--variants", nargs="+", required=True)
    parser.add_argument("--periods", nargs="+", choices=("full", "development", "diagnostic"), default=["full", "development", "diagnostic"])
    parser.add_argument("--slippage-ticks", type=float, nargs="+", default=[1, 2, 4])
    parser.add_argument("--commission-per-side", type=float, default=MNQ_FEES_PER_CONTRACT_PER_SIDE,
                        help="all transaction fees per contract per side; default 0.61 ($1.22 round trip)")
    parser.add_argument("--execution-minutes", type=int, choices=(1, 5), default=1)
    parser.add_argument("--entry-delay-minutes", type=int, choices=(0, 1), default=0,
                        help="additional observed-minute entry delay stress; exits keep normal timing")
    parser.add_argument("--bootstrap-repetitions", type=int, default=2000)
    parser.add_argument("--protocol", type=Path, required=True, help="predeclared hypothesis/evidence criteria document, captured before replay")
    args = parser.parse_args()
    if args.entry_delay_minutes and args.execution_minutes != 1:
        parser.error("entry delay requires --execution-minutes 1")
    if any(not math.isfinite(number) or number < 0 for number in [*args.slippage_ticks, args.commission_per_side]):
        parser.error("costs must be finite and nonnegative")
    if args.bootstrap_repetitions < 100:
        parser.error("at least 100 bootstrap repetitions are required")
    if len(args.variants) != len(set(args.variants)) or len(args.slippage_ticks) != len(set(args.slippage_ticks)):
        parser.error("duplicate variants or cost cases are not allowed")
    if len(args.periods) != len(set(args.periods)):
        parser.error("duplicate periods are not allowed")
    offline_environment()
    sys.path.insert(0, str(BACKEND))
    from app.models import BotConfig
    from app.services import bot_backtesting as replay
    from app.services.databento_cache import DatabentoReplayStore
    from app.services.topbot import TOPBOT_SETTINGS
    from tools.research_rolls import RawContractRollResolver

    fixture_source = args.fixture.read_text(encoding="utf-8")
    fixture = ModuleType("app.services._topbot_research_fixture")
    fixture.__package__ = "app.services"
    sys.modules[fixture.__name__] = fixture
    exec(compile(fixture_source, str(args.fixture), "exec"), fixture.__dict__)
    for name in args.variants:
        if name not in fixture.CANDIDATES:
            parser.error(f"unknown variant {name!r}; choices: {', '.join(fixture.CANDIDATES)}")
        for key in ("description", "hypothesis", "parameters"):
            if key not in fixture.CANDIDATES[name]:
                parser.error(f"{name} must declare {key} before replay")
    protocol = args.protocol.read_text(encoding="utf-8")
    if not protocol.strip():
        parser.error("pretest protocol must not be empty")
    store = DatabentoReplayStore(args.cache_dir)
    try:
        bounds = store.history_bounds("MNQ")
        if bounds is None:
            parser.error("verified MNQ history is unavailable in the local cache")
        roll_resolver = RawContractRollResolver(args.cache_dir)
        periods = resolve_periods(bounds, args.periods)
        configs = {}
        for name in args.variants:
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", name):
                parser.error("variant names must be safe single filename components")
            settings = deepcopy(TOPBOT_SETTINGS)
            settings.update(getattr(fixture, "get_settings", lambda _: {})(name))
            settings["lookback_bars"] = max(int(settings["lookback_bars"]), int(getattr(fixture, "required_warmup_bars", lambda _: 200)(name)))
            if settings["symbol"] != "MNQ" or settings["strategy_type"] != "topbot_adaptive":
                parser.error("research is restricted to MNQ TopBot Adaptive")
            for field in ("order_size", "max_contracts", "max_open_position"):
                if float(settings[field]) != 1:
                    parser.error(f"fixed-risk comparison requires {field}=1")
            configs[name] = BotConfig(
                id=1, user_id="offline-research", account_id=1, name="TopBot research",
                provider="projectx", enabled=False, execution_mode="dry_run",
                contract_id="CON.F.US.MNQ.U26", **settings,
            )
        directory = create_run_directory(args.output_root, args.label)
        code = capture_sources(directory, args.fixture)
        cache_manifest = json.loads((args.cache_dir / "current.json").read_text(encoding="utf-8"))
        manifest = {
            "run_id": directory.name, "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "predeclared_before_replay", "command": sys.argv,
            "hypotheses": {name: fixture.CANDIDATES[name] for name in args.variants},
            "protocol": protocol, "protocol_sha256": hashlib.sha256(protocol.encode()).hexdigest(),
            "code": code, "cache_manifest": cache_manifest, "cache_manifest_sha256": fingerprint(cache_manifest),
            "periods": {name: {"start": start.isoformat(), "end": end.isoformat(), "fresh_portfolio": True} for name, (start, end) in periods.items()},
            "settings": {name: replay._config_snapshot(config) for name, config in configs.items()},
            "costs": {"commission_per_side": args.commission_per_side, "slippage_ticks_each_side": args.slippage_ticks, "tick_size": .25, "tick_value": .5},
            "starting_balance": 50_000, "execution_minutes": args.execution_minutes,
            "entry_delay_minutes": args.entry_delay_minutes,
            "execution_model": "separate observed 1-minute bars" if args.execution_minutes == 1 else "legacy 5-minute signal-bar replay; delayed fills and coarser ambiguity",
            "engine_version": replay.BACKTEST_ENGINE_VERSION,
            "roll_policy": replay.ROLL_POLICY_VERSION,
            "roll_execution": (
                "Observed old-delivery one-minute opening price at exact delivery switch time; missing price rejects experiment"
                if args.execution_minutes == 1 else "Legacy approximation: prior-delivery final close uses future roll knowledge"
            ),
            "calendar_risk_hook": "Optional fixture.should_flatten evaluated at observed execution opens after resting gap brackets; uses known session deadline, independent of signal aggregates",
            "bootstrap_repetitions": args.bootstrap_repetitions, "limitations": REUSED_HISTORY,
            "experiment_count": len(args.variants) * len(periods) * len(args.slippage_ticks),
        }
        write_new_json(directory / "manifest.json", manifest)
        print(json.dumps({"event": "predeclared", "run_directory": str(directory), "experiment_count": manifest["experiment_count"]}), flush=True)
        results = {}
        summaries = defaultdict(dict)
        failures = 0
        for name, config in configs.items():
            for period, (start, end) in periods.items():
                for slip in args.slippage_ticks:
                    key = f"{name}__{period}__slip-{slip:g}"
                    started = perf_counter()
                    write_new_json(directory / f"{key}.started.json", {
                        "started_at": datetime.now(timezone.utc).isoformat(), "pid": os.getpid(),
                        "hypothesis_manifest_sha256": fingerprint(manifest),
                    })
                    try:
                        warmup_start = max(bounds[0], replay._databento_warmup_start(
                            start, unit=config.timeframe_unit, unit_number=config.timeframe_unit_number,
                            warmup_bars=config.lookback_bars,
                        ))
                        primary = store.open_candles(
                            user_id=config.user_id, contract_id=config.contract_id, root_symbol="MNQ",
                            unit=config.timeframe_unit, unit_number=config.timeframe_unit_number,
                            start=warmup_start, end=end, closed_by=end,
                        )
                        streams = {replay._topbot_asset_stream_key(config.timeframe_unit, config.timeframe_unit_number): primary}
                        execution = store.open_candles(
                            user_id=config.user_id, contract_id=config.contract_id, root_symbol="MNQ",
                            unit="minute", unit_number=1, start=start, end=end, closed_by=end,
                        ) if args.execution_minutes == 1 else None
                        kwargs = {"execution_candles": execution} if execution is not None else {}
                        engine = make_engine_class(replay, fixture, name)(
                            config=deepcopy(config), candles=primary, replay_streams=streams,
                            roll_exit_candle_resolver=roll_resolver,
                            entry_delay_minutes=args.entry_delay_minutes,
                            settings=replay.BacktestSettings(
                                start=start, end=end, starting_balance=50_000,
                                commission_per_contract=args.commission_per_side,
                                slippage_ticks=slip, tick_size=.25, tick_value=.5,
                            ), **kwargs,
                        )
                        result = engine.run()
                        sessions = engine.session_ledger()
                        exposure = {
                            "definition": "side open at each observed bar close; intrabar-only trades are in overall exposure but excluded here",
                            "bar_count": engine.research_bar_count,
                            **{side: {"bars": engine.research_exposure[side], "percent": 100 * engine.research_exposure[side] / max(1, engine.research_bar_count)} for side in ("long", "short")},
                        }
                        summary = summarize_result(result, sessions, exposure, repetitions=args.bootstrap_repetitions)
                        summary.update(
                            status="completed", seconds=perf_counter() - started,
                            source_fingerprint=primary[0].source_file_sha256,
                            candidate_name=name, candidate_definition=deepcopy(fixture.CANDIDATES[name]),
                            candidate_fixture_revision=getattr(fixture, "REVISION", None),
                            entry_delay_minutes=args.entry_delay_minutes,
                            candidate_settings=replay._config_snapshot(config),
                            config_note="Production engine normalizes strategy_params to its code-owned baseline; research evaluation uses the separately captured candidate_definition and frozen fixture source.",
                        )
                        write_new_json(directory / f"{key}.trades.json", result["trades"])
                        write_new_json(directory / f"{key}.sessions.json", sessions)
                        write_new_json(directory / f"{key}.replay.json", result)
                        write_new_json(directory / f"{key}.summary.json", summary)
                        results[key] = {"status": "completed", "metrics": summary["metrics"], "seconds": summary["seconds"], "trades_sha256": summary["trades_sha256"]}
                        summaries[name][(period, slip)] = summary
                    except Exception as error:
                        failures += 1
                        results[key] = {"status": "failed", "error_type": type(error).__name__, "error": str(error), "seconds": perf_counter() - started}
                        write_new_json(directory / f"{key}.failure.json", results[key])
                    print(json.dumps({"experiment": key, **results[key]}), flush=True)
        write_new_json(directory / "results.json", {
            "manifest_sha256": fingerprint(manifest), "results": results, "limitations": REUSED_HISTORY,
            "historical_screens": {name: historical_screen(summaries[name]) for name in args.variants},
        })
        print(json.dumps({"event": "finished", "run_directory": str(directory), "failed_experiments": failures}), flush=True)
        return 1 if failures else 0
    finally:
        store.clear()


if __name__ == "__main__":
    raise SystemExit(main())
