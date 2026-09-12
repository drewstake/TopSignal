import type { BotActivity, BotConfig, BotRun } from "../../lib/types";
import { TOPBOT_PRESET } from "./botChartIndicators";

export function botStrategyLabel(strategy: string): string {
  if (strategy === "topbot_adaptive") return "TopBot Adaptive";
  return strategy.split("_").map((part) =>
    ["ema", "vwap", "atr", "rsi", "orb", "fvg", "mss", "spy"].includes(part)
      ? part.toUpperCase()
      : part[0].toUpperCase() + part.slice(1),
  ).join(" ");
}

export function botConfigurationSummary(bot: BotConfig): string {
  if (bot.strategy_type === "topbot_adaptive") {
    return `${TOPBOT_PRESET.timeframeUnitNumber}-${TOPBOT_PRESET.timeframeUnit} EMA/VWAP pullback · ${TOPBOT_PRESET.positionSize} contract · ${TOPBOT_PRESET.sessionStart}–${TOPBOT_PRESET.sessionEnd} ET · ${TOPBOT_PRESET.stopPoints}-point stop / ${TOPBOT_PRESET.targetPoints}-point target`;
  }
  const params = bot.strategy_params as Record<string, unknown>;
  const parts = [
    `${bot.timeframe_unit_number}-${bot.timeframe_unit} candles`,
    `${bot.order_size} contract${bot.order_size === 1 ? "" : "s"}`,
    `${bot.trading_start_time.slice(0, 5)}–${bot.trading_end_time.slice(0, 5)} ET`,
  ];
  if (typeof params.stop_points === "number" && typeof params.target_points === "number") {
    parts.push(`${params.stop_points}-point stop / ${params.target_points}-point target`);
  } else if (typeof params.protective_stop_ticks === "number" && typeof params.take_profit_ticks === "number") {
    parts.push(`${params.protective_stop_ticks}-tick stop / ${params.take_profit_ticks}-tick target`);
  }
  return parts.join(" · ");
}

export function latestBotRun(activity: BotActivity | null): BotRun | null {
  return activity?.runs.reduce<BotRun | null>((latest, run) => {
    if (!latest) return run;
    const difference = Date.parse(run.started_at) - Date.parse(latest.started_at);
    return difference > 0 || (difference === 0 && run.id > latest.id) ? run : latest;
  }, null) ?? null;
}

export function botRunStatus(bot: BotConfig | null, activity: BotActivity | null) {
  const run = latestBotRun(activity);
  if (!bot) return { label: "Stopped", variant: "neutral" as const };
  if (!activity) return { label: "Checking run status", variant: "neutral" as const };
  if (run?.stop_reason === "worker_restart_requires_rearm") {
    return { label: "Re-arm required", variant: "warning" as const };
  }
  if (run?.status === "running" && bot.enabled) {
    return { label: run.dry_run ? "Dry Run active" : "Live Run active", variant: "positive" as const };
  }
  if (run?.status === "blocked" || run?.status === "error") {
    return { label: run.status === "blocked" ? "Run blocked" : "Run error", variant: "negative" as const };
  }
  if (run?.status === "stopped") return { label: "Stopped", variant: "neutral" as const };
  if (bot.enabled || run?.status === "running") {
    return { label: "Run state needs review", variant: "warning" as const };
  }
  return { label: "Stopped", variant: "neutral" as const };
}
