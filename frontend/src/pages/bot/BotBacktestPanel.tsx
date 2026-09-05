import { useEffect, useMemo, useRef, useState, type ComponentProps, type FormEvent } from "react";

import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/Card";
import { Input } from "../../components/ui/Input";
import { Progress } from "../../components/ui/Progress";
import { botsApi } from "../../lib/api";
import type {
  BotBacktestDrawdownPoint,
  BotBacktestEquityPoint,
  BotBacktestPeriodResult,
  BotBacktestProgress,
  BotBacktestResult,
  BotConfig,
} from "../../lib/types";
import { cn } from "../../components/ui/cn";
import {
  BACKTEST_CHART_WIDTH,
  BACKTEST_DRAWDOWN_HEIGHT,
  BACKTEST_DRAWDOWN_TOP,
  BACKTEST_EQUITY_HEIGHT,
  BACKTEST_EQUITY_TOP,
  buildBacktestPayload,
  buildBacktestChartPaths,
  describeBacktestError,
  describeBacktestProgress,
  validateBacktestForm,
  type BotBacktestFormState,
} from "./botBacktest";

import { BacktestTradeAnalysis } from "./BacktestTradeAnalysis";
import { BacktestDiagnostics } from "./BacktestDiagnostics";

const EASTERN_TIME_ZONE = "America/New_York";
const currencyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const numberFormatter = new Intl.NumberFormat("en-US", {
  maximumFractionDigits: 2,
});
const integerFormatter = new Intl.NumberFormat("en-US", {
  maximumFractionDigits: 0,
});
const timestampFormatter = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  timeZoneName: "short",
  timeZone: EASTERN_TIME_ZONE,
});

interface BotBacktestPanelProps {
  bot: BotConfig | null;
  demoMode?: boolean;
}

export function BotBacktestPanel({ bot, demoMode = false }: BotBacktestPanelProps) {
  const [form, setForm] = useState<BotBacktestFormState>(() =>
    buildDefaultForm(),
  );
  const [result, setResult] = useState<BotBacktestResult | null>(null);
  const [progress, setProgress] = useState<BotBacktestProgress | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestSequence = useRef(0);
  const requestController = useRef<AbortController | null>(null);

  useEffect(() => () => {
    requestSequence.current += 1;
    requestController.current?.abort();
    requestController.current = null;
  }, []);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (demoMode) {
      setError("Backtest jobs are disabled in Demo Mode because they run on the connected server workspace.");
      return;
    }
    if (!bot) {
      setError("Select a saved bot before running a backtest.");
      return;
    }

    const validationError = validateBacktestForm(form);
    if (validationError) {
      setError(validationError);
      return;
    }

    const sequence = requestSequence.current + 1;
    requestSequence.current = sequence;
    requestController.current?.abort();
    const controller = new AbortController();
    requestController.current = controller;
    setRunning(true);
    setProgress(null);
    setError(null);
    try {
      const nextResult = await botsApi.runBacktest(bot.id, buildBacktestPayload(form), {
        signal: controller.signal,
        onProgress: (nextProgress) => {
          if (requestSequence.current === sequence) {
            setProgress(nextProgress);
          }
        },
      });
      if (requestSequence.current === sequence) {
        setResult(nextResult);
      }
    } catch (err) {
      if (requestSequence.current === sequence) {
        setError(describeBacktestError(err));
      }
    } finally {
      if (requestSequence.current === sequence) {
        setRunning(false);
        requestController.current = null;
      }
    }
  }

  return (
    <Card className="min-w-0">
      <CardHeader className="space-y-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle>Backtest</CardTitle>
            <CardDescription>TopBot Adaptive · MNQ 5m · EMA/VWAP pullback · Long bias · Hold for 50-point stop / 50-point target</CardDescription>
          </div>
          <div className="flex flex-wrap gap-2">
            {demoMode ? <Badge variant="accent">Demo snapshot</Badge> : null}
            <Badge variant="accent">Next-bar fills</Badge>
            <Badge variant="neutral">No order routing</Badge>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-5">
        <form className="grid gap-3 md:grid-cols-2 xl:grid-cols-[1fr_1fr_1fr_auto] xl:items-end" onSubmit={handleSubmit}>
          <BacktestInput
            label="Starting balance"
            type="number"
            min="0.01"
            step="0.01"
            value={form.startingBalance}
            onChange={(value) => setForm((current) => ({ ...current, startingBalance: value }))}
          />
          <BacktestInput
            label="Commission / contract"
            type="number"
            min="0"
            step="0.01"
            value={form.commissionPerContract}
            onChange={(value) => setForm((current) => ({ ...current, commissionPerContract: value }))}
          />
          <BacktestInput
            label="Slippage (ticks)"
            type="number"
            min="0"
            step="1"
            value={form.slippageTicks}
            onChange={(value) => setForm((current) => ({ ...current, slippageTicks: value }))}
          />
          <Button
            type="submit"
            disabled={demoMode || !bot}
            className="w-full xl:w-auto"
            title={demoMode ? "Backtest server jobs are disabled in Demo Mode." : undefined}
          >
            {running ? "Stop & Run New Backtest" : "Run Stored History Replay"}
          </Button>
        </form>

        {error ? (
          <div role="alert" className="rounded-xl border border-app-negative/35 bg-app-negative/10 px-4 py-3 text-sm text-app-negative">
            {error}
          </div>
        ) : null}

        {demoMode ? (
          <BacktestEmptyState message="Backtest inputs are available for review, but Demo Mode does not start a server replay job." />
        ) : !bot ? (
          <BacktestEmptyState message="Save or select a bot to configure a historical replay." />
        ) : running ? (
          <BacktestRunningState progress={progress} />
        ) : result ? (
          <BacktestResults result={result} />
        ) : (
          <BacktestEmptyState message="Set the execution costs, then replay the stored closed candles for this contract." />
        )}
      </CardContent>
    </Card>
  );
}

function BacktestInput({
  label,
  onChange,
  ...inputProps
}: {
  label: string;
  onChange: (value: string) => void;
} & Omit<ComponentProps<typeof Input>, "onChange">) {
  return (
    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-app-muted">
      <span>{label}</span>
      <Input {...inputProps} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

function BacktestEmptyState({ message }: { message: string }) {
  return (
    <div className="rounded-xl border border-dashed border-app-border bg-app-bg/25 px-4 py-6 text-center">
      <p className="text-sm text-app-text-soft">{message}</p>
      <p className="mx-auto mt-2 max-w-3xl text-xs leading-5 text-app-muted">
        Signals are evaluated only after their candles close, fills occur no earlier than the next bar, and this workflow never submits an external
        order. The result records the bot configuration and execution assumptions used for the run.
      </p>
    </div>
  );
}

export function BacktestRunningState({ progress }: { progress: BotBacktestProgress | null }) {
  const copy = describeBacktestProgress(progress);
  return (
    <div role="status" aria-live="polite" className="space-y-3 rounded-xl border border-app-accent/30 bg-app-accent/10 px-4 py-4 text-app-text-soft">
      <div className="flex items-center gap-3">
        <span className="h-4 w-4 shrink-0 animate-spin rounded-full border-2 border-app-accent/25 border-t-app-accent" aria-hidden="true" />
        <div className="min-w-0">
          <p className="text-sm font-medium text-app-text">{copy.title}</p>
          <p className="mt-0.5 text-xs text-app-muted">{copy.detail}</p>
        </div>
      </div>
      {copy.percent === null ? (
        <div className="h-2 overflow-hidden rounded-full bg-app-raised" aria-label="Preparing backtest history">
          <div className="h-full w-1/3 animate-pulse rounded-full bg-gradient-to-r from-app-accent to-app-secondary" />
        </div>
      ) : (
        <Progress
          value={copy.percent}
          role="progressbar"
          aria-label="Backtest replay progress"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={copy.percent}
        />
      )}
    </div>
  );
}

export function BacktestResults({ result }: { result: BotBacktestResult }) {
  const metrics = result.metrics;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-app-border pt-5">
        <div>
          <h4 className="text-sm font-semibold text-app-text md:text-base">Historical replay (not validation)</h4>
          <p className="mt-1 text-xs text-app-muted">
            {formatContract(result.range.symbol, result.range.contract_id)} · {formatTimeframe(result.range.timeframe_unit, result.range.timeframe_unit_number)} · {integerFormatter.format(result.range.bar_count)} closed bars
          </p>
          <p className="mt-1 text-xs text-app-muted">
            {formatTimestamp(result.range.start)} – {formatTimestamp(result.range.end)} · generated {formatTimestamp(result.created_at)}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Badge variant="accent">Engine {result.engine_version}</Badge>
          <Badge variant="neutral">Run #{result.id}</Badge>
        </div>
      </div>

      <BacktestDiagnostics result={result} />

      {result.evaluation_split ? (
        <ChronologicalHoldout split={result.evaluation_split} />
      ) : (
        <div className="rounded-xl border border-app-warning/35 bg-app-warning/10 px-4 py-3 text-sm text-app-text-soft">
          No isolated chronological holdout is available for this saved result. Treat every metric below as in-sample and descriptive only.
        </div>
      )}

      <div className="space-y-2">
        <div>
          <h5 className="text-sm font-semibold text-app-text">Full-replay metrics</h5>
          <p className="mt-0.5 text-xs text-app-muted">Descriptive in-sample results; these are not evidence of future performance.</p>
        </div>
        <div className="grid grid-cols-2 gap-2.5 md:grid-cols-4 xl:grid-cols-6">
          <ResultMetric label="Net P&L" value={formatCurrency(metrics.net_pnl)} tone={pnlTone(metrics.net_pnl)} />
          <ResultMetric label="Gross P&L" value={formatCurrency(metrics.gross_pnl)} tone={pnlTone(metrics.gross_pnl)} />
          <ResultMetric label="Trades" value={integerFormatter.format(metrics.trade_count)} />
          <ResultMetric label="Win rate" value={formatPercent(metrics.win_rate)} />
          <ResultMetric label="Profit factor" value={formatRatio(metrics.profit_factor)} />
          <ResultMetric label="Expectancy" value={formatCurrency(metrics.expectancy)} tone={pnlTone(metrics.expectancy)} />
          <ResultMetric label="Average win" value={formatCurrency(metrics.average_win)} tone="positive" />
          <ResultMetric label="Average loss" value={formatCurrency(metrics.average_loss)} tone="negative" />
          <ResultMetric label="Payoff ratio" value={formatRatio(metrics.payoff_ratio)} />
          <ResultMetric label="Max drawdown" value={formatCurrency(-Math.abs(metrics.max_drawdown_dollars))} tone="negative" />
          <ResultMetric label="Drawdown %" value={formatPercent(metrics.max_drawdown_percent)} tone="negative" />
          <ResultMetric label="Exposure" value={formatPercent(metrics.exposure_percent)} />
          <ResultMetric label="Average MAE" value={formatCurrency(metrics.average_mae)} tone="negative" />
          <ResultMetric label="Average MFE" value={formatCurrency(metrics.average_mfe)} tone="positive" />
          <ResultMetric label="Commission" value={formatCurrency(metrics.total_commission)} />
          <ResultMetric label="Win streak" value={integerFormatter.format(metrics.max_consecutive_wins)} />
          <ResultMetric label="Loss streak" value={integerFormatter.format(metrics.max_consecutive_losses)} />
          <ResultMetric label="W / L" value={`${integerFormatter.format(metrics.winning_trades)} / ${integerFormatter.format(metrics.losing_trades)}`} />
        </div>
      </div>

      <EquityDrawdownChart equity={result.equity_curve} drawdown={result.drawdown_series} />
      <BacktestTradeAnalysis key={result.id} result={result} />

      <Assumptions result={result} />

      {(result.daily_results.length > 0 || result.monthly_results.length > 0) ? (
        <details className="rounded-xl border border-app-border bg-app-bg/25 p-4">
          <summary className="cursor-pointer text-sm font-semibold text-app-text">Daily and monthly results</summary>
          <div className="mt-4 grid gap-4 xl:grid-cols-2">
            <PeriodResults title="Daily" rows={result.daily_results} />
            <PeriodResults title="Monthly" rows={result.monthly_results} />
          </div>
        </details>
      ) : null}

    </div>
  );
}

function ChronologicalHoldout({
  split,
}: {
  split: NonNullable<BotBacktestResult["evaluation_split"]>;
}) {
  return (
    <section className="space-y-3 rounded-xl border border-app-accent/30 bg-app-accent/10 p-4">
      <div>
        <h5 className="text-sm font-semibold text-app-text">{split.label}</h5>
        <p className="mt-1 text-xs leading-5 text-app-muted">
          The last 20% of bars was replayed with fresh portfolio and risk state. It is a diagnostic only—not proof of future performance.
        </p>
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        {([
          ["First 80% (in sample)", split.in_sample],
          ["Final 20% (holdout)", split.holdout],
        ] as const).map(([label, window]) => (
          <div key={label} className="rounded-lg border border-app-border bg-app-bg/35 p-3">
            <p className="text-xs font-semibold uppercase tracking-wide text-app-muted">{label}</p>
            <p className="mt-1 text-xs text-app-muted">
              {integerFormatter.format(window.bar_count)} bars · {formatTimestamp(window.start)} – {formatTimestamp(window.end)}
            </p>
            <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
              <HoldoutMetric label="Net P&L" value={formatCurrency(window.metrics.net_pnl)} />
              <HoldoutMetric label="Trades" value={integerFormatter.format(window.metrics.trade_count)} />
              <HoldoutMetric label="Win rate" value={formatPercent(window.metrics.win_rate)} />
              <HoldoutMetric label="Profit factor" value={formatRatio(window.metrics.profit_factor)} />
              <HoldoutMetric label="Expectancy" value={formatCurrency(window.metrics.expectancy)} />
              <HoldoutMetric label="W / L" value={`${window.metrics.winning_trades} / ${window.metrics.losing_trades}`} />
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function HoldoutMetric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-[10px] uppercase tracking-wide text-app-muted">{label}</p>
      <p className="font-medium text-app-text">{value}</p>
    </div>
  );
}

function ResultMetric({ label, value, tone = "neutral" }: { label: string; value: string; tone?: "positive" | "negative" | "neutral" }) {
  return (
    <div className="rounded-xl border border-app-border bg-app-bg/30 px-3 py-3">
      <p className="text-[10px] font-medium uppercase tracking-wide text-app-muted">{label}</p>
      <p
        className={cn(
          "mt-1 truncate font-mono text-sm font-semibold text-app-text",
          tone === "positive" && "text-app-positive",
          tone === "negative" && "text-app-negative",
        )}
        title={value}
      >
        {value}
      </p>
    </div>
  );
}

function EquityDrawdownChart({ equity, drawdown }: { equity: BotBacktestEquityPoint[]; drawdown: BotBacktestDrawdownPoint[] }) {
  const paths = useMemo(() => buildBacktestChartPaths(equity, drawdown), [drawdown, equity]);
  const firstTimestamp = equity[0]?.timestamp ?? drawdown[0]?.timestamp ?? null;
  const lastTimestamp = equity[equity.length - 1]?.timestamp ?? drawdown[drawdown.length - 1]?.timestamp ?? null;
  const chartWasSampled =
    paths.equityRenderedPointCount < paths.equitySourcePointCount ||
    paths.drawdownRenderedPointCount < paths.drawdownSourcePointCount;

  return (
    <div className="min-w-0 rounded-xl border border-app-border bg-app-bg/30 p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h5 className="text-sm font-semibold text-app-text">Equity and drawdown</h5>
          <p className="mt-0.5 text-xs text-app-muted">Closed and open P&amp;L across the replay</p>
        </div>
        <div className="flex gap-3 text-[11px] text-app-muted">
          <span className="flex items-center gap-1.5"><span className="h-0.5 w-4 bg-app-accent" />Equity</span>
          <span className="flex items-center gap-1.5"><span className="h-0.5 w-4 bg-app-negative" />Drawdown</span>
        </div>
      </div>
      {paths.equity || paths.drawdown ? (
        <>
          <svg viewBox={`0 0 ${BACKTEST_CHART_WIDTH} 270`} className="h-auto w-full" role="img" aria-labelledby="backtest-chart-title backtest-chart-description">
            <title id="backtest-chart-title">Backtest equity and drawdown chart</title>
            <desc id="backtest-chart-description">Equity ranges from {formatCurrency(paths.equityMin)} to {formatCurrency(paths.equityMax)}. Maximum drawdown is {formatPercent(paths.drawdownMax)}.</desc>
            {[BACKTEST_EQUITY_TOP, BACKTEST_EQUITY_TOP + BACKTEST_EQUITY_HEIGHT / 2, BACKTEST_EQUITY_TOP + BACKTEST_EQUITY_HEIGHT, BACKTEST_DRAWDOWN_TOP, BACKTEST_DRAWDOWN_TOP + BACKTEST_DRAWDOWN_HEIGHT].map((y) => (
              <line key={y} x1="0" x2={BACKTEST_CHART_WIDTH} y1={y} y2={y} stroke="rgb(var(--theme-chart-grid) / 0.5)" strokeWidth="1" />
            ))}
            {paths.equityFill ? <path d={paths.equityFill} fill="rgb(var(--theme-accent) / 0.09)" /> : null}
            {paths.equity ? <path d={paths.equity} fill="none" stroke="rgb(var(--theme-accent))" strokeWidth="2.25" vectorEffect="non-scaling-stroke" /> : null}
            {paths.drawdown ? <path d={paths.drawdown} fill="none" stroke="rgb(var(--theme-negative))" strokeWidth="1.75" vectorEffect="non-scaling-stroke" /> : null}
            <text x="4" y="10" fill="rgb(var(--theme-chart-label))" fontSize="11">{formatCompactCurrency(paths.equityMax)}</text>
            <text x="4" y={BACKTEST_EQUITY_TOP + BACKTEST_EQUITY_HEIGHT - 4} fill="rgb(var(--theme-chart-label))" fontSize="11">{formatCompactCurrency(paths.equityMin)}</text>
            <text x="4" y={BACKTEST_DRAWDOWN_TOP - 7} fill="rgb(var(--theme-chart-label))" fontSize="11">Drawdown</text>
            <text x="4" y={BACKTEST_DRAWDOWN_TOP + BACKTEST_DRAWDOWN_HEIGHT - 4} fill="rgb(var(--theme-chart-label))" fontSize="11">{formatPercent(paths.drawdownMax)}</text>
            {firstTimestamp ? <text x="0" y="266" fill="rgb(var(--theme-chart-label))" fontSize="11">{formatTimestamp(firstTimestamp)}</text> : null}
            {lastTimestamp ? <text x={BACKTEST_CHART_WIDTH} y="266" textAnchor="end" fill="rgb(var(--theme-chart-label))" fontSize="11">{formatTimestamp(lastTimestamp)}</text> : null}
          </svg>
          {chartWasSampled ? (
            <p className="mt-2 text-[11px] text-app-muted">
              Chart rendering is deterministically sampled to {integerFormatter.format(paths.equityRenderedPointCount)} of {integerFormatter.format(paths.equitySourcePointCount)} equity points for display; all returned points remain included in backtest results and metrics.
            </p>
          ) : null}
        </>
      ) : (
        <div className="grid h-56 place-items-center text-sm text-app-muted">No equity points returned.</div>
      )}
    </div>
  );
}

function Assumptions({ result }: { result: BotBacktestResult }) {
  const assumptions = result.assumptions;
  return (
    <div className="rounded-xl border border-app-border bg-app-bg/30 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h5 className="text-sm font-semibold text-app-text">Execution assumptions</h5>
          <p className="mt-0.5 text-xs text-app-muted">Persisted with the configuration snapshot for reproducibility</p>
        </div>
        <Badge variant="positive">External routing disabled</Badge>
      </div>
      <dl className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Assumption label="Fill model" value={humanize(assumptions.fill_model)} />
        <Assumption label="Strategy replay" value={humanize(assumptions.strategy_replay)} />
        <Assumption label="Synchronized streams" value={integerFormatter.format(assumptions.synchronized_stream_count)} />
        <Assumption label="Same-bar exit" value={humanize(assumptions.same_bar_exit_rule)} />
        <Assumption label="Bracket placement" value={humanize(assumptions.bracket_rule)} />
        <Assumption label="Final position" value={humanize(assumptions.final_position_handling)} />
        <Assumption label="Session timezone" value={assumptions.timezone} />
        <Assumption label="Commission" value={`${formatCurrency(assumptions.commission_per_contract)} / contract`} />
        <Assumption label="Slippage" value={`${numberFormatter.format(assumptions.slippage_ticks)} tick${assumptions.slippage_ticks === 1 ? "" : "s"}`} />
        <Assumption label="Tick size" value={numberFormatter.format(assumptions.tick_size)} />
        <Assumption label="Tick value" value={formatCurrency(assumptions.tick_value)} />
      </dl>
      <details className="mt-4 border-t border-app-border pt-3">
        <summary className="cursor-pointer text-xs font-medium text-app-text-soft">Configuration snapshot</summary>
        <pre className="mt-3 max-h-72 overflow-auto rounded-lg bg-app-bg/55 p-3 text-[11px] leading-5 text-app-muted">{JSON.stringify(result.config_snapshot, null, 2)}</pre>
      </details>
    </div>
  );
}

function Assumption({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-[10px] font-medium uppercase tracking-wide text-app-muted">{label}</dt>
      <dd className="mt-1 text-xs text-app-text-soft">{value}</dd>
    </div>
  );
}

function PeriodResults({ title, rows }: { title: string; rows: BotBacktestPeriodResult[] }) {
  return (
    <div className="min-w-0">
      <h5 className="mb-2 text-xs font-semibold uppercase tracking-wide text-app-muted">{title}</h5>
      {rows.length > 0 ? (
        <div className="max-h-72 overflow-auto rounded-lg border border-app-border">
          <table className="w-full min-w-[420px] text-xs">
            <thead className="sticky top-0 bg-app-surface text-left uppercase tracking-wide text-app-muted">
              <tr><th className="px-3 py-2 font-medium">Period</th><th className="px-3 py-2 text-right font-medium">Net</th><th className="px-3 py-2 text-right font-medium">Gross</th><th className="px-3 py-2 text-right font-medium">Trades</th><th className="px-3 py-2 text-right font-medium">W / L</th></tr>
            </thead>
            <tbody className="divide-y divide-app-border">
              {rows.map((row) => (
                <tr key={row.period}>
                  <td className="px-3 py-2 text-app-text-soft">{row.period}</td>
                  <td className={cn("px-3 py-2 text-right font-mono", pnlClassName(row.net_pnl))}>{formatCurrency(row.net_pnl)}</td>
                  <td className="px-3 py-2 text-right font-mono text-app-text-soft">{formatCurrency(row.gross_pnl)}</td>
                  <td className="px-3 py-2 text-right font-mono text-app-text-soft">{row.trade_count}</td>
                  <td className="px-3 py-2 text-right font-mono text-app-text-soft">{row.wins} / {row.losses}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <p className="text-xs text-app-muted">No {title.toLowerCase()} rows.</p>}
    </div>
  );
}

function buildDefaultForm(): BotBacktestFormState {
  return { startingBalance: "50000", commissionPerContract: "1.20", slippageTicks: "1" };
}

function formatCurrency(value: number): string {
  return Number.isFinite(value) ? currencyFormatter.format(value) : "—";
}

function formatCompactCurrency(value: number): string {
  if (!Number.isFinite(value)) {
    return "—";
  }
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 1 }).format(value);
}

function formatPercent(value: number): string {
  return Number.isFinite(value) ? `${numberFormatter.format(Math.abs(value))}%` : "—";
}

function formatRatio(value: number | null): string {
  return value !== null && Number.isFinite(value) ? `${numberFormatter.format(value)}×` : "—";
}

function formatTimestamp(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : timestampFormatter.format(date);
}

function formatContract(symbol: string | null, contractId: string): string {
  return symbol ? `${symbol} (${contractId})` : contractId;
}

function formatTimeframe(unit: string, unitNumber: number): string {
  return `${integerFormatter.format(unitNumber)}-${unit}`;
}

function humanize(value: string): string {
  return value.replaceAll("_", " ");
}

function pnlTone(value: number): "positive" | "negative" | "neutral" {
  return value > 0 ? "positive" : value < 0 ? "negative" : "neutral";
}

function pnlClassName(value: number): string {
  return value > 0 ? "text-app-positive" : value < 0 ? "text-app-negative" : "text-app-text-soft";
}
