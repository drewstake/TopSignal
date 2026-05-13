import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";

import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/Card";
import { Input } from "../../components/ui/Input";
import { Select } from "../../components/ui/Select";
import { Skeleton } from "../../components/ui/Skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../components/ui/Table";
import { ACCOUNT_QUERY_PARAM, parseAccountId } from "../../lib/accountSelection";
import { accountsApi, botsApi } from "../../lib/api";
import type {
  AccountInfo,
  BotActivity,
  BotConfig,
  BotEvaluation,
  BotLiquiditySweepTargetMode,
  BotOrbStopMode,
  BotOrbTargetMode,
  BotTakeProfitMode,
  BotTrailingStopMode,
  BotStrategyType,
  BotTimeframeUnit,
  ProjectXContract,
  ProjectXMarketCandle,
} from "../../lib/types";
import { BotSignalChart } from "./BotSignalChart";

const timeframeUnits: BotTimeframeUnit[] = ["second", "minute", "hour", "day", "week", "month"];
const strategyOptions: Array<{ value: BotStrategyType; label: string }> = [
  { value: "sma_cross", label: "SMA Cross" },
  { value: "ema_scalping", label: "9/15 EMA Scalping" },
  { value: "support_resistance", label: "Support/Resistance" },
  { value: "donchian_breakout", label: "Donchian Breakout" },
  { value: "fvg_sweep_mss", label: "FVG Sweep + Market Structure Shift" },
  { value: "liquidity_sweep_retest", label: "Liquidity Sweep + Zone Retest" },
  { value: "supertrend_pivot", label: "Supertrend + Pivot Points" },
  { value: "opening_rvol_breakout", label: "Opening 5m RVOL Breakout" },
  { value: "atr_adjusted_relative_strength", label: "ATR-Adjusted Relative Strength" },
  { value: "relative_strength_spy", label: "Relative Strength vs SPY" },
  { value: "pullback_trap_reversal", label: "Pullback Trap Reversal" },
  { value: "bollinger_mean_reversion", label: "Bollinger Band Mean Reversion" },
  { value: "bollinger_rsi_reversal", label: "Bollinger RSI Reversal" },
  { value: "macd_support_resistance", label: "MACD + S/R + Trail" },
  { value: "ema_trend_pullback", label: "20/50 EMA Trend Pullback" },
  { value: "delayed_orb_confirmation", label: "Delayed ORB Confirmation" },
  { value: "orb_fibonacci_pullback", label: "ORB Fibonacci Pullback" },
  { value: "fisher_transform_mean_reversion", label: "Fisher Transform Mean Reversion" },
  { value: "vwap_atr_mean_reversion", label: "VWAP ATR Mean Reversion" },
  { value: "vwap_gap_retrace", label: "VWAP Gap Retrace" },
];
const EASTERN_TIME_ZONE = "America/New_York";
const SUPPORT_RESISTANCE_DEFAULT_TOLERANCE_PERCENT = "0.25";
const DONCHIAN_DEFAULTS = {
  entryPeriod: "20",
  exitPeriod: "10",
  atrPeriod: "14",
  atrStopMultiple: "2",
  trailingAtrMultiple: "2",
  takeProfitRMultiple: "2",
  atrSizeReferencePercent: "1.5",
  minSizeScale: "0.5",
  lookbackBars: "250",
  maxDataStalenessSeconds: "600",
};
const FVG_SWEEP_MSS_DEFAULTS = {
  swingWindow: "5",
  volumeLookbackBars: "20",
  strongVolumeMultiplier: "1.5",
  stopBufferPercent: "0.05",
  targetMode: "next_liquidity" as BotLiquiditySweepTargetMode,
  lookbackBars: "200",
  maxDataStalenessSeconds: "600",
};
const LIQUIDITY_SWEEP_DEFAULTS = {
  reclaimWithinBars: "2",
  retestWithinBars: "3",
  stopBeyondSweepPercent: "0.05",
  takeProfitMode: "2r" as BotLiquiditySweepTargetMode,
  fastPeriod: "9",
  slowPeriod: "21",
  lookbackBars: "100",
  maxDataStalenessSeconds: "7200",
};
const OPENING_RVOL_DEFAULTS = {
  relativeVolumeLookbackDays: "20",
  minRelativeVolume: "2",
  minOpeningVolume: "500",
  minBodyToRangeRatio: "0.5",
  atrPeriod: "14",
  atrStopMultiple: "1",
  takeProfitRMultiple: "2",
  lookbackBars: "500",
  maxTradesPerDay: "1",
};
const PULLBACK_TRAP_DEFAULTS = {
  fastPeriod: "20",
  slowPeriod: "50",
  pullbackLookbackBars: "4",
  microLevelWindow: "3",
  volumeBaselineBars: "20",
  volumeSpikeMultiple: "1.5",
  wickToBodyRatioMin: "1.5",
  stopBufferPercent: "0.1",
  takeProfitRMultiple: "2",
  trendConfirmationBars: "3",
  minCountertrendBars: "2",
  pullbackRangeMultiplier: "1.25",
  priorSwingWindow: "10",
  lookbackBars: "250",
  maxDataStalenessSeconds: "900",
};
const DELAYED_ORB_DEFAULTS = {
  openingRangeMinutes: "15",
  confirmationMinutes: "5",
  stopMode: "inside_range" as BotOrbStopMode,
  targetMode: "2r" as BotOrbTargetMode,
  lookbackBars: "390",
  maxDataStalenessSeconds: "180",
};
const ORB_FIBONACCI_DEFAULTS = {
  openingRangeMinutes: "15",
  swingLookbackBars: "5",
  targetMode: "2r" as BotOrbTargetMode,
  lookbackBars: "150",
  timeframeUnitNumber: "5",
  maxDataStalenessSeconds: "600",
};
const FISHER_DEFAULTS = {
  fisherLength: "10",
  fisherExtremeThreshold: "1.5",
  priceStretchPercent: "0.2",
  emaSlopeLookbackBars: "5",
  emaSlopeMaxPercent: "0.6",
  swingStopLookbackBars: "5",
  takeProfitRMultiple: "2",
  meanEmaPeriod: "20",
  trendEmaPeriod: "50",
  lookbackBars: "300",
  maxDataStalenessSeconds: "600",
};
const BOLLINGER_RSI_DEFAULTS = {
  rsiPeriod: "14",
  bollingerPeriod: "20",
  bollingerStddev: "2",
  adxPeriod: "14",
  rsiOversold: "30",
  rsiOverbought: "70",
  adxMax: "25",
  swingStopLookbackBars: "5",
  stopBufferPercent: "0.1",
  takeProfitMode: "middle_band" as BotTakeProfitMode,
  takeProfitRMultiple: "2",
  lookbackBars: "250",
  maxDataStalenessSeconds: "600",
};
const BOLLINGER_MEAN_REVERSION_DEFAULTS = {
  atrStopBuffer: "0.5",
  newsBlackoutWindows: "08:25-08:35, 09:55-10:05, 13:55-14:05",
};
const VWAP_ATR_DEFAULTS = {
  atrPeriod: "14",
  rsiPeriod: "14",
  adxPeriod: "14",
  stretchAtrMultiple: "1",
  rsiOversold: "30",
  rsiOverbought: "70",
  adxMax: "20",
  vwapSlopeBars: "5",
  flatVwapThresholdBps: "8",
  localExtremeLookback: "5",
  stopBufferAtr: "0.1",
  takeProfitMode: "vwap" as BotTakeProfitMode,
  takeProfitRMultiple: "1.5",
};
const VWAP_GAP_RETRACE_DEFAULTS = {
  minGapPercent: "2",
  waitStartMinutes: "5",
  waitEndMinutes: "15",
  minVolumeRatio: "1",
  stopBeyondVwapPercent: "0.1",
  touchTolerancePercent: "0.1",
  barsToFetch: "2000",
  lookbackBars: "390",
  maxDataStalenessSeconds: "180",
};
const ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS = {
  benchmarkSymbol: "SPY",
  moveLookbackBars: "3",
  atrPeriod: "14",
  relativeVolumePeriod: "20",
  relativeVolumeCap: "3",
  longScoreThreshold: "1.5",
  shortScoreThreshold: "-1.5",
  emaPeriod: "9",
  stopStructureWindow: "5",
  stopAtrMultiple: "0.25",
  takeProfitRMultiple: "2",
  lookbackBars: "200",
  maxDataStalenessSeconds: "600",
};
const RELATIVE_STRENGTH_SPY_DEFAULTS = {
  benchmarkSymbol: "SPY",
  comparisonBars: "12",
  pullbackLookbackBars: "3",
  relativeVolumePeriod: "20",
  minimumRelativeVolume: "2",
  minimumRelativeStrengthPercent: "0.25",
  minimumBenchmarkMovePercent: "0.1",
  emaPeriod: "9",
  swingWindow: "5",
  majorLevelLookbackBars: "40",
  entryLevelTolerancePercent: "0.4",
  stopBufferPercent: "0.1",
  takeProfitRMultiple: "2",
  lookbackBars: "200",
  maxDataStalenessSeconds: "900",
};
const MACD_SUPPORT_RESISTANCE_DEFAULTS = {
  signalPeriod: "9",
  atrPeriod: "14",
  initialStopAtrMultiplier: "1.5",
  trailingStopMode: "atr" as BotTrailingStopMode,
  trailingAtrMultiplier: "2",
  trailingMaPeriod: "21",
};
const EMA_TREND_PULLBACK_DEFAULTS = {
  fastPeriod: "20",
  slowPeriod: "50",
  rsiPeriod: "14",
  volumeAveragePeriod: "20",
  swingLookbackBars: "5",
  longRsiMin: "40",
  longRsiMax: "55",
  shortRsiMin: "45",
  shortRsiMax: "60",
  partialTakeProfitRMultiple: "1",
  finalTakeProfitRMultiple: "2",
  lookbackBars: "200",
  maxDataStalenessSeconds: "900",
};
const SUPERTREND_PIVOT_DEFAULTS = {
  supertrendPeriod: "10",
  supertrendMultiplier: "3",
  pivotTolerancePercent: "0.05",
  stopBeyondLevelPercent: "0.05",
  takeProfitRMultiple: "2",
  chopLookbackBars: "12",
  chopMaxFlips: "3",
  chopMaxRangePercent: "0.5",
  lookbackBars: "250",
  maxDataStalenessSeconds: "1800",
};
const STRATEGY_DEFAULT_NAMES: Partial<Record<BotStrategyType, string>> = {
  sma_cross: "MNQ SMA Cross",
  support_resistance: "MNQ Support/Resistance",
  donchian_breakout: "MNQ Donchian Breakout",
  fvg_sweep_mss: "MNQ FVG Sweep + MSS",
  liquidity_sweep_retest: "MNQ Liquidity Sweep + Zone Retest",
  supertrend_pivot: "MNQ Supertrend + Pivot Points",
  opening_rvol_breakout: "MNQ Opening 5m RVOL Breakout",
  atr_adjusted_relative_strength: "AAPL ATR-Adjusted Relative Strength",
  relative_strength_spy: "AAPL Relative Strength vs SPY",
  pullback_trap_reversal: "MNQ Pullback Trap Reversal",
  bollinger_mean_reversion: "MNQ Bollinger Band Mean Reversion",
  bollinger_rsi_reversal: "MNQ Bollinger RSI Reversal",
  macd_support_resistance: "MNQ MACD + S/R + Trail",
  ema_trend_pullback: "MNQ 20/50 EMA Trend Pullback",
  delayed_orb_confirmation: "MNQ Delayed ORB Confirmation",
  orb_fibonacci_pullback: "MNQ ORB Fibonacci Pullback",
  ema_scalping: "MNQ 9/15 EMA Scalping",
  fisher_transform_mean_reversion: "MNQ Fisher Transform Mean Reversion",
  vwap_atr_mean_reversion: "MNQ VWAP ATR Mean Reversion",
  vwap_gap_retrace: "MNQ VWAP Gap Retrace",
};
const delayedOrbStopOptions: Array<{ value: BotOrbStopMode; label: string }> = [
  { value: "inside_range", label: "Back Inside Range" },
  { value: "opposite_side", label: "Opposite Side" },
];
const delayedOrbTargetOptions: Array<{ value: BotOrbTargetMode; label: string }> = [
  { value: "2r", label: "2R" },
  { value: "3r", label: "3R" },
  { value: "measured_move", label: "Measured Move" },
];
const orbFibTargetOptions: Array<{ value: BotOrbTargetMode; label: string }> = [
  { value: "2r", label: "2R" },
  { value: "3r", label: "3R" },
  { value: "day_extreme", label: "Day High/Low" },
];
const liquiditySweepTargetOptions: Array<{ value: BotLiquiditySweepTargetMode; label: string }> = [
  { value: "2r", label: "2R" },
  { value: "3r", label: "3R" },
  { value: "next_liquidity", label: "Next Liquidity" },
];
const vwapAtrTakeProfitOptions: Array<{ value: BotTakeProfitMode; label: string }> = [
  { value: "vwap", label: "VWAP" },
  { value: "half_vwap_distance", label: "Half VWAP" },
  { value: "r_multiple", label: "1.5R" },
];
const bollingerRsiTakeProfitOptions: Array<{ value: BotTakeProfitMode; label: string }> = [
  { value: "middle_band", label: "Mid Band" },
  { value: "vwap", label: "VWAP" },
  { value: "two_r", label: "2R" },
];
const trailingStopOptions: Array<{ value: BotTrailingStopMode; label: string }> = [
  { value: "atr", label: "ATR" },
  { value: "swing", label: "Swing Structure" },
  { value: "moving_average", label: "Moving Average" },
];

const dateTimeFormatter = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: true,
  timeZone: EASTERN_TIME_ZONE,
});

interface BotFormState {
  name: string;
  strategyType: BotStrategyType;
  accountId: string;
  contractSearch: string;
  contractId: string;
  symbol: string;
  timeframeUnit: BotTimeframeUnit;
  timeframeUnitNumber: string;
  lookbackBars: string;
  fastPeriod: string;
  slowPeriod: string;
  levelTolerancePercent: string;
  donchianEntryPeriod: string;
  donchianExitPeriod: string;
  reclaimWithinBars: string;
  retestWithinBars: string;
  stopBeyondSweepPercent: string;
  relativeVolumeLookbackDays: string;
  minRelativeVolume: string;
  minOpeningVolume: string;
  minBodyToRangeRatio: string;
  benchmarkSymbol: string;
  moveLookbackBars: string;
  pullbackLookbackBars: string;
  microLevelWindow: string;
  volumeBaselineBars: string;
  volumeSpikeMultiple: string;
  wickToBodyRatioMin: string;
  stopBufferPercent: string;
  trendConfirmationBars: string;
  minCountertrendBars: string;
  pullbackRangeMultiplier: string;
  priorSwingWindow: string;
  signalPeriod: string;
  initialStopAtrMultiplier: string;
  trailingStopMode: BotTrailingStopMode;
  trailingAtrMultiplier: string;
  trailingMaPeriod: string;
  supertrendPeriod: string;
  supertrendMultiplier: string;
  chopLookbackBars: string;
  chopMaxFlips: string;
  chopMaxRangePercent: string;
  openingRangeMinutes: string;
  confirmationMinutes: string;
  orbStopMode: BotOrbStopMode;
  orbTargetMode: BotOrbTargetMode;
  fisherLength: string;
  fisherExtremeThreshold: string;
  priceStretchPercent: string;
  emaSlopeLookbackBars: string;
  emaSlopeMaxPercent: string;
  swingStopLookbackBars: string;
  atrPeriod: string;
  relativeVolumePeriod: string;
  relativeVolumeCap: string;
  longScoreThreshold: string;
  shortScoreThreshold: string;
  emaPeriod: string;
  stopStructureWindow: string;
  stopAtrMultiple: string;
  rsiPeriod: string;
  bollingerPeriod: string;
  bollingerStddev: string;
  adxPeriod: string;
  stretchAtrMultiple: string;
  rsiOversold: string;
  rsiOverbought: string;
  adxMax: string;
  vwapSlopeBars: string;
  flatVwapThresholdBps: string;
  localExtremeLookback: string;
  atrStopMultiple: string;
  atrStopBuffer: string;
  atrSizeReferencePercent: string;
  minSizeScale: string;
  stopBufferAtr: string;
  takeProfitMode: BotTakeProfitMode | BotLiquiditySweepTargetMode;
  takeProfitRMultiple: string;
  newsBlackoutWindows: string;
  orderSize: string;
  maxContracts: string;
  maxDailyLoss: string;
  maxTradesPerDay: string;
  maxOpenPosition: string;
  tradingStartTime: string;
  tradingEndTime: string;
  cooldownSeconds: string;
  maxDataStalenessSeconds: string;
}

function buildInitialForm(accountId: number | null): BotFormState {
  return {
    name: strategyDefaultName("sma_cross"),
    strategyType: "sma_cross",
    accountId: accountId ? String(accountId) : "",
    contractSearch: "MNQ",
    contractId: "",
    symbol: "MNQ",
    timeframeUnit: "minute",
    timeframeUnitNumber: "5",
    lookbackBars: "200",
    fastPeriod: "9",
    slowPeriod: "21",
    levelTolerancePercent: SUPPORT_RESISTANCE_DEFAULT_TOLERANCE_PERCENT,
    donchianEntryPeriod: DONCHIAN_DEFAULTS.entryPeriod,
    donchianExitPeriod: DONCHIAN_DEFAULTS.exitPeriod,
    reclaimWithinBars: LIQUIDITY_SWEEP_DEFAULTS.reclaimWithinBars,
    retestWithinBars: LIQUIDITY_SWEEP_DEFAULTS.retestWithinBars,
    stopBeyondSweepPercent: LIQUIDITY_SWEEP_DEFAULTS.stopBeyondSweepPercent,
    relativeVolumeLookbackDays: OPENING_RVOL_DEFAULTS.relativeVolumeLookbackDays,
    minRelativeVolume: OPENING_RVOL_DEFAULTS.minRelativeVolume,
    minOpeningVolume: OPENING_RVOL_DEFAULTS.minOpeningVolume,
    minBodyToRangeRatio: OPENING_RVOL_DEFAULTS.minBodyToRangeRatio,
    benchmarkSymbol: ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS.benchmarkSymbol,
    moveLookbackBars: ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS.moveLookbackBars,
    pullbackLookbackBars: PULLBACK_TRAP_DEFAULTS.pullbackLookbackBars,
    microLevelWindow: PULLBACK_TRAP_DEFAULTS.microLevelWindow,
    volumeBaselineBars: PULLBACK_TRAP_DEFAULTS.volumeBaselineBars,
    volumeSpikeMultiple: PULLBACK_TRAP_DEFAULTS.volumeSpikeMultiple,
    wickToBodyRatioMin: PULLBACK_TRAP_DEFAULTS.wickToBodyRatioMin,
    stopBufferPercent: PULLBACK_TRAP_DEFAULTS.stopBufferPercent,
    trendConfirmationBars: PULLBACK_TRAP_DEFAULTS.trendConfirmationBars,
    minCountertrendBars: PULLBACK_TRAP_DEFAULTS.minCountertrendBars,
    pullbackRangeMultiplier: PULLBACK_TRAP_DEFAULTS.pullbackRangeMultiplier,
    priorSwingWindow: PULLBACK_TRAP_DEFAULTS.priorSwingWindow,
    signalPeriod: MACD_SUPPORT_RESISTANCE_DEFAULTS.signalPeriod,
    initialStopAtrMultiplier: MACD_SUPPORT_RESISTANCE_DEFAULTS.initialStopAtrMultiplier,
    trailingStopMode: MACD_SUPPORT_RESISTANCE_DEFAULTS.trailingStopMode,
    trailingAtrMultiplier: MACD_SUPPORT_RESISTANCE_DEFAULTS.trailingAtrMultiplier,
    trailingMaPeriod: MACD_SUPPORT_RESISTANCE_DEFAULTS.trailingMaPeriod,
    supertrendPeriod: SUPERTREND_PIVOT_DEFAULTS.supertrendPeriod,
    supertrendMultiplier: SUPERTREND_PIVOT_DEFAULTS.supertrendMultiplier,
    chopLookbackBars: SUPERTREND_PIVOT_DEFAULTS.chopLookbackBars,
    chopMaxFlips: SUPERTREND_PIVOT_DEFAULTS.chopMaxFlips,
    chopMaxRangePercent: SUPERTREND_PIVOT_DEFAULTS.chopMaxRangePercent,
    openingRangeMinutes: DELAYED_ORB_DEFAULTS.openingRangeMinutes,
    confirmationMinutes: DELAYED_ORB_DEFAULTS.confirmationMinutes,
    orbStopMode: DELAYED_ORB_DEFAULTS.stopMode,
    orbTargetMode: ORB_FIBONACCI_DEFAULTS.targetMode,
    fisherLength: FISHER_DEFAULTS.fisherLength,
    fisherExtremeThreshold: FISHER_DEFAULTS.fisherExtremeThreshold,
    priceStretchPercent: FISHER_DEFAULTS.priceStretchPercent,
    emaSlopeLookbackBars: FISHER_DEFAULTS.emaSlopeLookbackBars,
    emaSlopeMaxPercent: FISHER_DEFAULTS.emaSlopeMaxPercent,
    swingStopLookbackBars: FISHER_DEFAULTS.swingStopLookbackBars,
    atrPeriod: VWAP_ATR_DEFAULTS.atrPeriod,
    relativeVolumePeriod: ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS.relativeVolumePeriod,
    relativeVolumeCap: ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS.relativeVolumeCap,
    longScoreThreshold: ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS.longScoreThreshold,
    shortScoreThreshold: ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS.shortScoreThreshold,
    emaPeriod: ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS.emaPeriod,
    stopStructureWindow: ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS.stopStructureWindow,
    stopAtrMultiple: ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS.stopAtrMultiple,
    rsiPeriod: VWAP_ATR_DEFAULTS.rsiPeriod,
    bollingerPeriod: BOLLINGER_RSI_DEFAULTS.bollingerPeriod,
    bollingerStddev: BOLLINGER_RSI_DEFAULTS.bollingerStddev,
    adxPeriod: VWAP_ATR_DEFAULTS.adxPeriod,
    stretchAtrMultiple: VWAP_ATR_DEFAULTS.stretchAtrMultiple,
    rsiOversold: VWAP_ATR_DEFAULTS.rsiOversold,
    rsiOverbought: VWAP_ATR_DEFAULTS.rsiOverbought,
    adxMax: VWAP_ATR_DEFAULTS.adxMax,
    vwapSlopeBars: VWAP_ATR_DEFAULTS.vwapSlopeBars,
    flatVwapThresholdBps: VWAP_ATR_DEFAULTS.flatVwapThresholdBps,
    localExtremeLookback: VWAP_ATR_DEFAULTS.localExtremeLookback,
    atrStopMultiple: OPENING_RVOL_DEFAULTS.atrStopMultiple,
    atrStopBuffer: BOLLINGER_MEAN_REVERSION_DEFAULTS.atrStopBuffer,
    atrSizeReferencePercent: DONCHIAN_DEFAULTS.atrSizeReferencePercent,
    minSizeScale: DONCHIAN_DEFAULTS.minSizeScale,
    stopBufferAtr: VWAP_ATR_DEFAULTS.stopBufferAtr,
    takeProfitMode: VWAP_ATR_DEFAULTS.takeProfitMode,
    takeProfitRMultiple: VWAP_ATR_DEFAULTS.takeProfitRMultiple,
    newsBlackoutWindows: BOLLINGER_MEAN_REVERSION_DEFAULTS.newsBlackoutWindows,
    orderSize: "1",
    maxContracts: "1",
    maxDailyLoss: "250",
    maxTradesPerDay: "3",
    maxOpenPosition: "1",
    tradingStartTime: "09:30",
    tradingEndTime: "15:45",
    cooldownSeconds: "300",
    maxDataStalenessSeconds: "600",
  };
}

function formFromBot(bot: BotConfig): BotFormState {
  return {
    name: bot.name,
    strategyType: bot.strategy_type,
    accountId: String(bot.account_id),
    contractSearch: bot.symbol ?? bot.contract_id,
    contractId: bot.contract_id,
    symbol: bot.symbol ?? "",
    timeframeUnit:
      bot.strategy_type === "support_resistance" ||
      bot.strategy_type === "liquidity_sweep_retest" ||
      bot.strategy_type === "macd_support_resistance"
        ? "hour"
        : bot.strategy_type === "opening_rvol_breakout"
          ? "minute"
        : bot.strategy_type === "relative_strength_spy"
          ? "minute"
        : bot.strategy_type === "delayed_orb_confirmation"
          ? "minute"
        : bot.strategy_type === "orb_fibonacci_pullback"
          ? "minute"
        : bot.strategy_type === "vwap_gap_retrace"
          ? "minute"
          : bot.timeframe_unit,
    timeframeUnitNumber:
      bot.strategy_type === "support_resistance" ||
      bot.strategy_type === "liquidity_sweep_retest" ||
      bot.strategy_type === "macd_support_resistance"
        ? "1"
        : bot.strategy_type === "opening_rvol_breakout"
          ? "5"
        : bot.strategy_type === "relative_strength_spy"
          ? "5"
        : bot.strategy_type === "delayed_orb_confirmation"
          ? "1"
        : bot.strategy_type === "orb_fibonacci_pullback"
          ? String(bot.timeframe_unit_number || ORB_FIBONACCI_DEFAULTS.timeframeUnitNumber)
        : bot.strategy_type === "vwap_gap_retrace"
          ? "1"
          : String(bot.timeframe_unit_number),
    lookbackBars:
      bot.strategy_type === "support_resistance" ||
      bot.strategy_type === "liquidity_sweep_retest" ||
      bot.strategy_type === "macd_support_resistance"
        ? "100"
      : bot.strategy_type === "opening_rvol_breakout"
          ? String(bot.lookback_bars || OPENING_RVOL_DEFAULTS.lookbackBars)
      : bot.strategy_type === "supertrend_pivot"
          ? String(bot.lookback_bars || SUPERTREND_PIVOT_DEFAULTS.lookbackBars)
      : bot.strategy_type === "relative_strength_spy"
          ? String(bot.lookback_bars || RELATIVE_STRENGTH_SPY_DEFAULTS.lookbackBars)
      : bot.strategy_type === "delayed_orb_confirmation"
          ? DELAYED_ORB_DEFAULTS.lookbackBars
        : bot.strategy_type === "orb_fibonacci_pullback"
          ? String(bot.lookback_bars || ORB_FIBONACCI_DEFAULTS.lookbackBars)
        : bot.strategy_type === "vwap_gap_retrace"
          ? String(bot.lookback_bars || VWAP_GAP_RETRACE_DEFAULTS.lookbackBars)
        : bot.strategy_type === "pullback_trap_reversal"
          ? String(bot.lookback_bars || PULLBACK_TRAP_DEFAULTS.lookbackBars)
        : bot.strategy_type === "fisher_transform_mean_reversion"
          ? String(bot.lookback_bars || FISHER_DEFAULTS.lookbackBars)
        : bot.strategy_type === "vwap_atr_mean_reversion"
          ? String(bot.lookback_bars || 300)
          : String(bot.lookback_bars),
    fastPeriod: bot.strategy_type === "ema_trend_pullback" ? EMA_TREND_PULLBACK_DEFAULTS.fastPeriod : String(bot.fast_period),
    slowPeriod: bot.strategy_type === "ema_trend_pullback" ? EMA_TREND_PULLBACK_DEFAULTS.slowPeriod : String(bot.slow_period),
    levelTolerancePercent: String(
      bot.strategy_type === "supertrend_pivot"
        ? bot.strategy_params?.pivot_tolerance_percent ?? SUPERTREND_PIVOT_DEFAULTS.pivotTolerancePercent
        : bot.strategy_params?.level_tolerance_percent ?? SUPPORT_RESISTANCE_DEFAULT_TOLERANCE_PERCENT,
    ),
    donchianEntryPeriod: String(bot.strategy_params?.entry_period ?? DONCHIAN_DEFAULTS.entryPeriod),
    donchianExitPeriod: String(bot.strategy_params?.exit_period ?? DONCHIAN_DEFAULTS.exitPeriod),
    reclaimWithinBars: String(bot.strategy_params?.reclaim_within_bars ?? LIQUIDITY_SWEEP_DEFAULTS.reclaimWithinBars),
    retestWithinBars: String(bot.strategy_params?.retest_within_bars ?? LIQUIDITY_SWEEP_DEFAULTS.retestWithinBars),
    stopBeyondSweepPercent: String(bot.strategy_params?.stop_beyond_sweep_percent ?? LIQUIDITY_SWEEP_DEFAULTS.stopBeyondSweepPercent),
    relativeVolumeLookbackDays: String(bot.strategy_params?.relative_volume_lookback_days ?? OPENING_RVOL_DEFAULTS.relativeVolumeLookbackDays),
    minRelativeVolume: String(bot.strategy_params?.min_relative_volume ?? OPENING_RVOL_DEFAULTS.minRelativeVolume),
    minOpeningVolume: String(bot.strategy_params?.min_opening_volume ?? OPENING_RVOL_DEFAULTS.minOpeningVolume),
    minBodyToRangeRatio: String(bot.strategy_params?.min_body_to_range_ratio ?? OPENING_RVOL_DEFAULTS.minBodyToRangeRatio),
    benchmarkSymbol: String(bot.strategy_params?.benchmark_symbol ?? ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS.benchmarkSymbol),
    moveLookbackBars: String(bot.strategy_params?.move_lookback_bars ?? ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS.moveLookbackBars),
    pullbackLookbackBars: String(bot.strategy_params?.pullback_lookback_bars ?? PULLBACK_TRAP_DEFAULTS.pullbackLookbackBars),
    microLevelWindow: String(
      bot.strategy_type === "fvg_sweep_mss"
        ? bot.strategy_params?.swing_window ?? FVG_SWEEP_MSS_DEFAULTS.swingWindow
        : bot.strategy_params?.micro_level_window ?? PULLBACK_TRAP_DEFAULTS.microLevelWindow,
    ),
    volumeBaselineBars: String(
      bot.strategy_type === "fvg_sweep_mss"
        ? bot.strategy_params?.volume_lookback_bars ?? FVG_SWEEP_MSS_DEFAULTS.volumeLookbackBars
        : bot.strategy_params?.volume_baseline_bars ?? PULLBACK_TRAP_DEFAULTS.volumeBaselineBars,
    ),
    volumeSpikeMultiple: String(
      bot.strategy_type === "fvg_sweep_mss"
        ? bot.strategy_params?.strong_volume_multiplier ?? FVG_SWEEP_MSS_DEFAULTS.strongVolumeMultiplier
        : bot.strategy_params?.volume_spike_multiple ?? PULLBACK_TRAP_DEFAULTS.volumeSpikeMultiple,
    ),
    wickToBodyRatioMin: String(bot.strategy_params?.wick_to_body_ratio_min ?? PULLBACK_TRAP_DEFAULTS.wickToBodyRatioMin),
    stopBufferPercent: String(
      bot.strategy_type === "supertrend_pivot"
        ? bot.strategy_params?.stop_beyond_level_percent ?? SUPERTREND_PIVOT_DEFAULTS.stopBeyondLevelPercent
        : bot.strategy_type === "fvg_sweep_mss"
          ? bot.strategy_params?.stop_buffer_percent ?? FVG_SWEEP_MSS_DEFAULTS.stopBufferPercent
          : bot.strategy_params?.stop_buffer_percent ?? PULLBACK_TRAP_DEFAULTS.stopBufferPercent,
    ),
    trendConfirmationBars: String(bot.strategy_params?.trend_confirmation_bars ?? PULLBACK_TRAP_DEFAULTS.trendConfirmationBars),
    minCountertrendBars: String(bot.strategy_params?.min_countertrend_bars ?? PULLBACK_TRAP_DEFAULTS.minCountertrendBars),
    pullbackRangeMultiplier: String(bot.strategy_params?.pullback_range_multiplier ?? PULLBACK_TRAP_DEFAULTS.pullbackRangeMultiplier),
    priorSwingWindow: String(bot.strategy_params?.prior_swing_window ?? PULLBACK_TRAP_DEFAULTS.priorSwingWindow),
    signalPeriod: String(bot.strategy_params?.signal_period ?? MACD_SUPPORT_RESISTANCE_DEFAULTS.signalPeriod),
    initialStopAtrMultiplier: String(
      bot.strategy_params?.initial_stop_atr_multiplier ?? MACD_SUPPORT_RESISTANCE_DEFAULTS.initialStopAtrMultiplier,
    ),
    trailingStopMode:
      (bot.strategy_params?.trailing_stop_mode as BotTrailingStopMode | undefined) ?? MACD_SUPPORT_RESISTANCE_DEFAULTS.trailingStopMode,
    trailingAtrMultiplier: String(
      bot.strategy_params?.trailing_atr_multiplier ?? MACD_SUPPORT_RESISTANCE_DEFAULTS.trailingAtrMultiplier,
    ),
    trailingMaPeriod: String(bot.strategy_params?.trailing_ma_period ?? MACD_SUPPORT_RESISTANCE_DEFAULTS.trailingMaPeriod),
    supertrendPeriod: String(bot.strategy_params?.supertrend_period ?? SUPERTREND_PIVOT_DEFAULTS.supertrendPeriod),
    supertrendMultiplier: String(bot.strategy_params?.supertrend_multiplier ?? SUPERTREND_PIVOT_DEFAULTS.supertrendMultiplier),
    chopLookbackBars: String(bot.strategy_params?.chop_lookback_bars ?? SUPERTREND_PIVOT_DEFAULTS.chopLookbackBars),
    chopMaxFlips: String(bot.strategy_params?.chop_max_flips ?? SUPERTREND_PIVOT_DEFAULTS.chopMaxFlips),
    chopMaxRangePercent: String(bot.strategy_params?.chop_max_range_percent ?? SUPERTREND_PIVOT_DEFAULTS.chopMaxRangePercent),
    openingRangeMinutes: String(
      bot.strategy_params?.opening_range_minutes ??
        (bot.strategy_type === "orb_fibonacci_pullback" ? ORB_FIBONACCI_DEFAULTS.openingRangeMinutes : DELAYED_ORB_DEFAULTS.openingRangeMinutes),
    ),
    confirmationMinutes: String(bot.strategy_params?.confirmation_minutes ?? DELAYED_ORB_DEFAULTS.confirmationMinutes),
    orbStopMode: (bot.strategy_params?.stop_mode as BotOrbStopMode | undefined) ?? DELAYED_ORB_DEFAULTS.stopMode,
    orbTargetMode:
      bot.strategy_type === "orb_fibonacci_pullback"
        ? (bot.strategy_params?.take_profit_mode as BotOrbTargetMode | undefined) ?? ORB_FIBONACCI_DEFAULTS.targetMode
        : (bot.strategy_params?.target_mode as BotOrbTargetMode | undefined) ?? DELAYED_ORB_DEFAULTS.targetMode,
    fisherLength: String(bot.strategy_params?.fisher_length ?? FISHER_DEFAULTS.fisherLength),
    fisherExtremeThreshold: String(bot.strategy_params?.fisher_extreme_threshold ?? FISHER_DEFAULTS.fisherExtremeThreshold),
    priceStretchPercent: String(bot.strategy_params?.price_stretch_percent ?? FISHER_DEFAULTS.priceStretchPercent),
    emaSlopeLookbackBars: String(bot.strategy_params?.ema_slope_lookback_bars ?? FISHER_DEFAULTS.emaSlopeLookbackBars),
    emaSlopeMaxPercent: String(bot.strategy_params?.ema_slope_max_percent ?? FISHER_DEFAULTS.emaSlopeMaxPercent),
    swingStopLookbackBars: String(
      bot.strategy_type === "orb_fibonacci_pullback"
        ? bot.strategy_params?.swing_lookback_bars ?? ORB_FIBONACCI_DEFAULTS.swingLookbackBars
        : bot.strategy_params?.swing_stop_lookback_bars ?? FISHER_DEFAULTS.swingStopLookbackBars,
    ),
    atrPeriod: String(bot.strategy_params?.atr_period ?? VWAP_ATR_DEFAULTS.atrPeriod),
    relativeVolumePeriod: String(bot.strategy_params?.relative_volume_period ?? ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS.relativeVolumePeriod),
    relativeVolumeCap: String(bot.strategy_params?.relative_volume_cap ?? ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS.relativeVolumeCap),
    longScoreThreshold: String(bot.strategy_params?.long_score_threshold ?? ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS.longScoreThreshold),
    shortScoreThreshold: String(bot.strategy_params?.short_score_threshold ?? ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS.shortScoreThreshold),
    emaPeriod: String(bot.strategy_params?.ema_period ?? ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS.emaPeriod),
    stopStructureWindow: String(bot.strategy_params?.stop_structure_window ?? ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS.stopStructureWindow),
    stopAtrMultiple: String(bot.strategy_params?.stop_atr_multiple ?? ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS.stopAtrMultiple),
    rsiPeriod: String(bot.strategy_params?.rsi_period ?? VWAP_ATR_DEFAULTS.rsiPeriod),
    bollingerPeriod: String(bot.strategy_params?.bollinger_period ?? BOLLINGER_RSI_DEFAULTS.bollingerPeriod),
    bollingerStddev: String(bot.strategy_params?.bollinger_stddev ?? BOLLINGER_RSI_DEFAULTS.bollingerStddev),
    adxPeriod: String(bot.strategy_params?.adx_period ?? VWAP_ATR_DEFAULTS.adxPeriod),
    stretchAtrMultiple: String(bot.strategy_params?.stretch_atr_multiple ?? VWAP_ATR_DEFAULTS.stretchAtrMultiple),
    rsiOversold: String(bot.strategy_params?.rsi_oversold ?? VWAP_ATR_DEFAULTS.rsiOversold),
    rsiOverbought: String(bot.strategy_params?.rsi_overbought ?? VWAP_ATR_DEFAULTS.rsiOverbought),
    adxMax: String(bot.strategy_params?.adx_max ?? VWAP_ATR_DEFAULTS.adxMax),
    vwapSlopeBars: String(bot.strategy_params?.vwap_slope_bars ?? VWAP_ATR_DEFAULTS.vwapSlopeBars),
    flatVwapThresholdBps: String(bot.strategy_params?.flat_vwap_threshold_bps ?? VWAP_ATR_DEFAULTS.flatVwapThresholdBps),
    localExtremeLookback: String(bot.strategy_params?.local_extreme_lookback ?? VWAP_ATR_DEFAULTS.localExtremeLookback),
    atrStopMultiple: String(
      bot.strategy_type === "donchian_breakout"
        ? bot.strategy_params?.atr_stop_multiple ?? DONCHIAN_DEFAULTS.atrStopMultiple
        : bot.strategy_params?.atr_stop_multiple ?? OPENING_RVOL_DEFAULTS.atrStopMultiple,
    ),
    atrSizeReferencePercent: String(bot.strategy_params?.atr_size_reference_percent ?? DONCHIAN_DEFAULTS.atrSizeReferencePercent),
    minSizeScale: String(bot.strategy_params?.min_size_scale ?? DONCHIAN_DEFAULTS.minSizeScale),
    atrStopBuffer: String(bot.strategy_params?.atr_stop_buffer ?? BOLLINGER_MEAN_REVERSION_DEFAULTS.atrStopBuffer),
    stopBufferAtr: String(bot.strategy_params?.stop_buffer_atr ?? VWAP_ATR_DEFAULTS.stopBufferAtr),
    takeProfitMode:
      bot.strategy_type === "fvg_sweep_mss"
        ? isFvgTargetMode(String(bot.strategy_params?.target_mode ?? ""))
          ? (bot.strategy_params?.target_mode as BotLiquiditySweepTargetMode)
          : FVG_SWEEP_MSS_DEFAULTS.targetMode
        : bot.strategy_type === "liquidity_sweep_retest"
          ? (bot.strategy_params?.take_profit_mode as BotLiquiditySweepTargetMode | undefined) ?? LIQUIDITY_SWEEP_DEFAULTS.takeProfitMode
          : bot.strategy_type === "bollinger_rsi_reversal"
            ? (bot.strategy_params?.take_profit_mode as BotTakeProfitMode | undefined) ?? BOLLINGER_RSI_DEFAULTS.takeProfitMode
            : bot.strategy_type === "vwap_atr_mean_reversion"
              ? (bot.strategy_params?.take_profit_mode as BotTakeProfitMode | undefined) ?? VWAP_ATR_DEFAULTS.takeProfitMode
              : VWAP_ATR_DEFAULTS.takeProfitMode,
    takeProfitRMultiple: String(
      bot.strategy_params?.take_profit_r_multiple ??
        (bot.strategy_type === "donchian_breakout"
          ? DONCHIAN_DEFAULTS.takeProfitRMultiple
          : bot.strategy_type === "supertrend_pivot"
          ? SUPERTREND_PIVOT_DEFAULTS.takeProfitRMultiple
          : bot.strategy_type === "opening_rvol_breakout"
          ? OPENING_RVOL_DEFAULTS.takeProfitRMultiple
          : bot.strategy_type === "bollinger_rsi_reversal"
            ? BOLLINGER_RSI_DEFAULTS.takeProfitRMultiple
          : VWAP_ATR_DEFAULTS.takeProfitRMultiple),
    ),
    newsBlackoutWindows: Array.isArray(bot.strategy_params?.news_blackout_windows)
      ? bot.strategy_params.news_blackout_windows.join(", ")
      : BOLLINGER_MEAN_REVERSION_DEFAULTS.newsBlackoutWindows,
    orderSize: String(bot.order_size),
    maxContracts: String(bot.max_contracts),
    maxDailyLoss: String(bot.max_daily_loss),
    maxTradesPerDay: String(bot.max_trades_per_day),
    maxOpenPosition: String(bot.max_open_position),
    tradingStartTime: bot.trading_start_time,
    tradingEndTime: bot.trading_end_time,
    cooldownSeconds: String(bot.cooldown_seconds),
    maxDataStalenessSeconds:
      (bot.strategy_type === "support_resistance" ||
        bot.strategy_type === "liquidity_sweep_retest" ||
        bot.strategy_type === "macd_support_resistance") &&
      bot.max_data_staleness_seconds < 3600
        ? "7200"
        : bot.strategy_type === "relative_strength_spy"
          ? String(bot.max_data_staleness_seconds || RELATIVE_STRENGTH_SPY_DEFAULTS.maxDataStalenessSeconds)
        : bot.strategy_type === "delayed_orb_confirmation"
          ? bot.max_data_staleness_seconds > Number(DELAYED_ORB_DEFAULTS.maxDataStalenessSeconds)
            ? DELAYED_ORB_DEFAULTS.maxDataStalenessSeconds
            : String(bot.max_data_staleness_seconds)
        : bot.strategy_type === "orb_fibonacci_pullback" && bot.max_data_staleness_seconds < Number(ORB_FIBONACCI_DEFAULTS.maxDataStalenessSeconds)
          ? ORB_FIBONACCI_DEFAULTS.maxDataStalenessSeconds
        : bot.strategy_type === "fisher_transform_mean_reversion" && bot.max_data_staleness_seconds < Number(FISHER_DEFAULTS.maxDataStalenessSeconds)
          ? FISHER_DEFAULTS.maxDataStalenessSeconds
        : bot.strategy_type === "vwap_gap_retrace" && bot.max_data_staleness_seconds < Number(VWAP_GAP_RETRACE_DEFAULTS.maxDataStalenessSeconds)
          ? VWAP_GAP_RETRACE_DEFAULTS.maxDataStalenessSeconds
        : bot.strategy_type === "pullback_trap_reversal" && bot.max_data_staleness_seconds < Number(PULLBACK_TRAP_DEFAULTS.maxDataStalenessSeconds)
          ? PULLBACK_TRAP_DEFAULTS.maxDataStalenessSeconds
        : bot.strategy_type === "atr_adjusted_relative_strength" && bot.max_data_staleness_seconds < Number(ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS.maxDataStalenessSeconds)
          ? ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS.maxDataStalenessSeconds
        : bot.strategy_type === "supertrend_pivot" && bot.max_data_staleness_seconds < Number(SUPERTREND_PIVOT_DEFAULTS.maxDataStalenessSeconds)
          ? SUPERTREND_PIVOT_DEFAULTS.maxDataStalenessSeconds
        : bot.strategy_type === "vwap_atr_mean_reversion" && bot.max_data_staleness_seconds < 600
          ? "600"
          : bot.strategy_type === "ema_trend_pullback" && bot.max_data_staleness_seconds < Number(EMA_TREND_PULLBACK_DEFAULTS.maxDataStalenessSeconds)
            ? EMA_TREND_PULLBACK_DEFAULTS.maxDataStalenessSeconds
          : String(bot.max_data_staleness_seconds),
  };
}

function parsePositiveNumber(value: string) {
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

function parseNonNegativeNumber(value: string) {
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
}

function parseFiniteNumber(value: string) {
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function parsePositiveInt(value: string) {
  const parsed = Number.parseInt(value, 10);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
}

function parseNonNegativeInt(value: string) {
  const parsed = Number.parseInt(value, 10);
  return Number.isInteger(parsed) && parsed >= 0 ? parsed : null;
}

function formatDateTime(value: string | null) {
  if (!value) {
    return "None";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "Unknown";
  }
  return `${dateTimeFormatter.format(date)} ET`;
}

function actionBadgeVariant(action: string) {
  if (action === "BUY") {
    return "positive" as const;
  }
  if (action === "SELL" || action === "STOP") {
    return "negative" as const;
  }
  return "neutral" as const;
}

function statusBadgeVariant(status: string) {
  if (status === "running" || status === "dry_run" || status === "submitted") {
    return "positive" as const;
  }
  if (status === "blocked" || status === "error" || status === "rejected") {
    return "negative" as const;
  }
  return "neutral" as const;
}

function isLevelStrategy(strategyType: BotStrategyType) {
  return (
    strategyType === "support_resistance" ||
    strategyType === "liquidity_sweep_retest" ||
    strategyType === "macd_support_resistance"
  );
}

function isFvgTargetMode(value: string): value is "2r" | "3r" | "next_liquidity" {
  return value === "2r" || value === "3r" || value === "next_liquidity";
}

function isBollingerRsiTakeProfitMode(value: string): value is "middle_band" | "vwap" | "two_r" {
  return value === "middle_band" || value === "vwap" || value === "two_r";
}

function isVwapAtrTakeProfitMode(value: string): value is "vwap" | "half_vwap_distance" | "r_multiple" {
  return value === "vwap" || value === "half_vwap_distance" || value === "r_multiple";
}

function deriveFvgStructureTimeframe(unit: BotTimeframeUnit, unitNumber: number) {
  const unitSeconds: Record<BotTimeframeUnit, number> = {
    second: 1,
    minute: 60,
    hour: 60 * 60,
    day: 24 * 60 * 60,
    week: 7 * 24 * 60 * 60,
    month: 31 * 24 * 60 * 60,
  };
  const suffixByUnit: Record<BotTimeframeUnit, string> = {
    second: "s",
    minute: "m",
    hour: "H",
    day: "D",
    week: "W",
    month: "M",
  };
  const totalSeconds = unitSeconds[unit] * Math.max(1, Math.trunc(unitNumber));
  for (const divisor of [4, 3, 5, 2]) {
    if (totalSeconds % divisor !== 0) {
      continue;
    }
    const candidateSeconds = totalSeconds / divisor;
    for (const nextUnit of ["month", "week", "day", "hour", "minute"] as BotTimeframeUnit[]) {
      if (candidateSeconds % unitSeconds[nextUnit] === 0) {
        return `${candidateSeconds / unitSeconds[nextUnit]}${suffixByUnit[nextUnit]}`;
      }
    }
    if (unit === "second") {
      return `${candidateSeconds}s`;
    }
  }
  return `${Math.max(1, Math.trunc(unitNumber))}${suffixByUnit[unit]}`;
}

function strategyLabel(strategyType: BotStrategyType) {
  if (strategyType === "support_resistance") {
    return "Support/Resistance";
  }
  if (strategyType === "donchian_breakout") {
    return "Donchian Breakout";
  }
  if (strategyType === "fvg_sweep_mss") {
    return "FVG Sweep + MSS";
  }
  if (strategyType === "liquidity_sweep_retest") {
    return "Liquidity Sweep + Zone Retest";
  }
  if (strategyType === "supertrend_pivot") {
    return "Supertrend + Pivot Points";
  }
  if (strategyType === "opening_rvol_breakout") {
    return "Opening 5m RVOL Breakout";
  }
  if (strategyType === "atr_adjusted_relative_strength") {
    return "ATR-Adjusted Relative Strength";
  }
  if (strategyType === "relative_strength_spy") {
    return "Relative Strength vs SPY";
  }
  if (strategyType === "pullback_trap_reversal") {
    return "Pullback Trap Reversal";
  }
  if (strategyType === "bollinger_rsi_reversal") {
    return "Bollinger RSI Reversal";
  }
  if (strategyType === "macd_support_resistance") {
    return "MACD + S/R + Trail";
  }
  if (strategyType === "ema_trend_pullback") {
    return "20/50 EMA Trend Pullback";
  }
  if (strategyType === "delayed_orb_confirmation") {
    return "15-Minute ORB Trend Filter";
  }
  if (strategyType === "orb_fibonacci_pullback") {
    return "ORB Fibonacci Pullback";
  }
  if (strategyType === "fisher_transform_mean_reversion") {
    return "Fisher Transform Mean Reversion";
  }
  if (strategyType === "vwap_atr_mean_reversion") {
    return "VWAP ATR Mean Reversion";
  }
  if (strategyType === "ema_scalping") {
    return "9/15 EMA Scalping";
  }
  if (strategyType === "vwap_gap_retrace") {
    return "VWAP Gap Retrace";
  }
  return "SMA Cross";
}

function strategyDefaultName(strategyType: BotStrategyType) {
  return STRATEGY_DEFAULT_NAMES[strategyType] ?? "Trading Bot";
}

function strategySummary(bot: BotConfig) {
  if (bot.strategy_type === "support_resistance") {
    return {
      label: "Level %",
      value: String(bot.strategy_params?.level_tolerance_percent ?? SUPPORT_RESISTANCE_DEFAULT_TOLERANCE_PERCENT),
    };
  }
  if (bot.strategy_type === "donchian_breakout") {
    const entryPeriod = bot.strategy_params?.entry_period ?? DONCHIAN_DEFAULTS.entryPeriod;
    const exitPeriod = bot.strategy_params?.exit_period ?? DONCHIAN_DEFAULTS.exitPeriod;
    const atrPeriod = bot.strategy_params?.atr_period ?? DONCHIAN_DEFAULTS.atrPeriod;
    return {
      label: "Donchian",
      value: `N ${entryPeriod}/${exitPeriod} · ATR ${atrPeriod}`,
    };
  }
  if (bot.strategy_type === "fvg_sweep_mss") {
    const targetMode = bot.strategy_params?.target_mode ?? FVG_SWEEP_MSS_DEFAULTS.targetMode;
    const targetLabel =
      targetMode === "next_liquidity" ? "Next liquidity" : targetMode === "3r" ? "3R" : "2R";
    const swingWindow = bot.strategy_params?.swing_window ?? FVG_SWEEP_MSS_DEFAULTS.swingWindow;
    return {
      label: "FVG",
      value: `${targetLabel} · Swing ${swingWindow}`,
    };
  }
  if (bot.strategy_type === "liquidity_sweep_retest") {
    const targetMode = bot.strategy_params?.take_profit_mode ?? LIQUIDITY_SWEEP_DEFAULTS.takeProfitMode;
    const targetLabel =
      targetMode === "next_liquidity" ? "Next Liquidity" : targetMode === "3r" ? "3R" : "2R";
    return {
      label: "Bias / TP",
      value: `${bot.fast_period}/${bot.slow_period} SMA · ${targetLabel}`,
    };
  }
  if (bot.strategy_type === "supertrend_pivot") {
    const period = bot.strategy_params?.supertrend_period ?? SUPERTREND_PIVOT_DEFAULTS.supertrendPeriod;
    const multiplier = bot.strategy_params?.supertrend_multiplier ?? SUPERTREND_PIVOT_DEFAULTS.supertrendMultiplier;
    const pivot = bot.strategy_params?.pivot_tolerance_percent ?? SUPERTREND_PIVOT_DEFAULTS.pivotTolerancePercent;
    return {
      label: "ST / Pivot",
      value: `${period} x ${multiplier} · Pivot ${pivot}%`,
    };
  }
  if (bot.strategy_type === "opening_rvol_breakout") {
    const rvol = bot.strategy_params?.min_relative_volume ?? OPENING_RVOL_DEFAULTS.minRelativeVolume;
    const atrStop = bot.strategy_params?.atr_stop_multiple ?? OPENING_RVOL_DEFAULTS.atrStopMultiple;
    return {
      label: "RVOL",
      value: `${rvol}x / ${atrStop} ATR`,
    };
  }
  if (bot.strategy_type === "pullback_trap_reversal") {
    const volume = bot.strategy_params?.volume_spike_multiple ?? PULLBACK_TRAP_DEFAULTS.volumeSpikeMultiple;
    const wick = bot.strategy_params?.wick_to_body_ratio_min ?? PULLBACK_TRAP_DEFAULTS.wickToBodyRatioMin;
    return {
      label: "Trap",
      value: `EMA ${bot.fast_period}/${bot.slow_period} · Vol ${volume}x · Wick ${wick}x`,
    };
  }
  if (bot.strategy_type === "relative_strength_spy") {
    const window = bot.strategy_params?.comparison_bars ?? RELATIVE_STRENGTH_SPY_DEFAULTS.comparisonBars;
    const rvol = bot.strategy_params?.minimum_relative_volume ?? RELATIVE_STRENGTH_SPY_DEFAULTS.minimumRelativeVolume;
    return {
      label: "RS/SPY",
      value: `${window} x 5m · RVOL ${rvol}x`,
    };
  }
  if (bot.strategy_type === "atr_adjusted_relative_strength") {
    const benchmark = bot.strategy_params?.benchmark_symbol ?? ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS.benchmarkSymbol;
    const longThreshold = bot.strategy_params?.long_score_threshold ?? ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS.longScoreThreshold;
    const shortThreshold = bot.strategy_params?.short_score_threshold ?? ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS.shortScoreThreshold;
    return {
      label: "ATR RS",
      value: `${benchmark} >${longThreshold} / <${shortThreshold}`,
    };
  }
  if (bot.strategy_type === "macd_support_resistance") {
    const signalPeriod = bot.strategy_params?.signal_period ?? MACD_SUPPORT_RESISTANCE_DEFAULTS.signalPeriod;
    const trailMode = bot.strategy_params?.trailing_stop_mode ?? MACD_SUPPORT_RESISTANCE_DEFAULTS.trailingStopMode;
    return {
      label: "MACD",
      value: `${bot.fast_period}/${bot.slow_period}/${signalPeriod} · ${trailMode}`,
    };
  }
  if (bot.strategy_type === "ema_scalping") {
    return {
      label: "EMA",
      value: `9/15 · ${bot.timeframe_unit_number}m`,
    };
  }
  if (bot.strategy_type === "ema_trend_pullback") {
    const longRange = `${bot.strategy_params?.long_rsi_min ?? EMA_TREND_PULLBACK_DEFAULTS.longRsiMin}-${bot.strategy_params?.long_rsi_max ?? EMA_TREND_PULLBACK_DEFAULTS.longRsiMax}`;
    const shortRange = `${bot.strategy_params?.short_rsi_min ?? EMA_TREND_PULLBACK_DEFAULTS.shortRsiMin}-${bot.strategy_params?.short_rsi_max ?? EMA_TREND_PULLBACK_DEFAULTS.shortRsiMax}`;
    return {
      label: "EMA",
      value: `20/50 · RSI ${longRange} / ${shortRange}`,
    };
  }
  if (bot.strategy_type === "delayed_orb_confirmation") {
    const openingRange = bot.strategy_params?.opening_range_minutes ?? DELAYED_ORB_DEFAULTS.openingRangeMinutes;
    const confirmation = bot.strategy_params?.confirmation_minutes ?? DELAYED_ORB_DEFAULTS.confirmationMinutes;
    const target = bot.strategy_params?.target_mode ?? DELAYED_ORB_DEFAULTS.targetMode;
    return {
      label: "ORB",
      value: `${openingRange}m / ${confirmation}m / ${target}`,
    };
  }
  if (bot.strategy_type === "orb_fibonacci_pullback") {
    const openingRange = bot.strategy_params?.opening_range_minutes ?? ORB_FIBONACCI_DEFAULTS.openingRangeMinutes;
    const swingBars = bot.strategy_params?.swing_lookback_bars ?? ORB_FIBONACCI_DEFAULTS.swingLookbackBars;
    const target = bot.strategy_params?.take_profit_mode ?? ORB_FIBONACCI_DEFAULTS.targetMode;
    return {
      label: "ORB Fib",
      value: `${openingRange}m / Swing ${swingBars} / ${target}`,
    };
  }
  if (bot.strategy_type === "vwap_gap_retrace") {
    const gap = bot.strategy_params?.min_gap_percent ?? VWAP_GAP_RETRACE_DEFAULTS.minGapPercent;
    const windowStart = bot.strategy_params?.wait_start_minutes ?? VWAP_GAP_RETRACE_DEFAULTS.waitStartMinutes;
    const windowEnd = bot.strategy_params?.wait_end_minutes ?? VWAP_GAP_RETRACE_DEFAULTS.waitEndMinutes;
    return {
      label: "VWAP",
      value: `${gap}% gap / ${windowStart}-${windowEnd}m`,
    };
  }
  if (bot.strategy_type === "bollinger_rsi_reversal") {
    const oversold = bot.strategy_params?.rsi_oversold ?? BOLLINGER_RSI_DEFAULTS.rsiOversold;
    const overbought = bot.strategy_params?.rsi_overbought ?? BOLLINGER_RSI_DEFAULTS.rsiOverbought;
    const takeProfitMode = bot.strategy_params?.take_profit_mode ?? BOLLINGER_RSI_DEFAULTS.takeProfitMode;
    return {
      label: "RSI / TP",
      value: `${oversold}-${overbought} / ${takeProfitMode}`,
    };
  }
  if (bot.strategy_type === "vwap_atr_mean_reversion") {
    const stretch = bot.strategy_params?.stretch_atr_multiple ?? VWAP_ATR_DEFAULTS.stretchAtrMultiple;
    const oversold = bot.strategy_params?.rsi_oversold ?? VWAP_ATR_DEFAULTS.rsiOversold;
    const overbought = bot.strategy_params?.rsi_overbought ?? VWAP_ATR_DEFAULTS.rsiOverbought;
    return {
      label: "Setup",
      value: `ATR x${stretch} / RSI ${oversold}-${overbought}`,
    };
  }
  if (bot.strategy_type === "fisher_transform_mean_reversion") {
    const threshold = bot.strategy_params?.fisher_extreme_threshold ?? FISHER_DEFAULTS.fisherExtremeThreshold;
    const stretch = bot.strategy_params?.price_stretch_percent ?? FISHER_DEFAULTS.priceStretchPercent;
    return {
      label: "Fisher",
      value: `±${threshold} / ${stretch}% stretch`,
    };
  }
  return {
    label: "SMA",
    value: `${bot.fast_period}/${bot.slow_period}`,
  };
}

function Sparkline({ candles }: { candles: ProjectXMarketCandle[] }) {
  const closes = candles.map((candle) => candle.close).filter((value) => Number.isFinite(value));
  const path = useMemo(() => {
    if (closes.length < 2) {
      return "";
    }
    const min = Math.min(...closes);
    const max = Math.max(...closes);
    const span = max - min || 1;
    return closes
      .map((value, index) => {
        const x = (index / (closes.length - 1)) * 100;
        const y = 36 - ((value - min) / span) * 32;
        return `${index === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`;
      })
      .join(" ");
  }, [closes]);

  return (
    <svg viewBox="0 0 100 40" className="h-16 w-full overflow-visible" aria-hidden="true">
      <path d="M 0 38 L 100 38" stroke="rgba(148,163,184,0.25)" strokeWidth="1" />
      {path ? <path d={path} fill="none" stroke="rgb(34,211,238)" strokeWidth="2" vectorEffect="non-scaling-stroke" /> : null}
    </svg>
  );
}

export function BotPage() {
  const [searchParams] = useSearchParams();
  const accountFromQuery = parseAccountId(searchParams.get(ACCOUNT_QUERY_PARAM));
  const [accounts, setAccounts] = useState<AccountInfo[]>([]);
  const [configs, setConfigs] = useState<BotConfig[]>([]);
  const [selectedBotId, setSelectedBotId] = useState<number | null>(null);
  const [activity, setActivity] = useState<BotActivity | null>(null);
  const [lastEvaluation, setLastEvaluation] = useState<BotEvaluation | null>(null);
  const [contracts, setContracts] = useState<ProjectXContract[]>([]);
  const [form, setForm] = useState<BotFormState>(() => buildInitialForm(accountFromQuery));
  const [loading, setLoading] = useState(true);
  const [activityLoading, setActivityLoading] = useState(false);
  const [contractLoading, setContractLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [chartRefreshToken, setChartRefreshToken] = useState(0);
  const [editingBotId, setEditingBotId] = useState<number | null>(null);

  const selectedBot = useMemo(
    () => configs.find((config) => config.id === selectedBotId) ?? configs[0] ?? null,
    [configs, selectedBotId],
  );
  const selectedBotEvaluation = useMemo(
    () => (selectedBot && lastEvaluation?.config.id === selectedBot.id ? lastEvaluation : null),
    [lastEvaluation, selectedBot],
  );
  const selectedBotStrategySummary = useMemo(() => (selectedBot ? strategySummary(selectedBot) : null), [selectedBot]);

  const loadConfigs = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [accountRows, botRows] = await Promise.all([
        accountsApi.getSelectableAccounts(),
        botsApi.listConfigs(),
      ]);
      setAccounts(accountRows);
      setConfigs(botRows.items);
      setSelectedBotId((current) => {
        if (current && botRows.items.some((item) => item.id === current)) {
          return current;
        }
        return botRows.items[0]?.id ?? null;
      });
      if (accountRows.length > 0) {
        setForm((current) =>
          current.accountId ? current : { ...current, accountId: String(accountFromQuery ?? accountRows[0].id) },
        );
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load bot data");
    } finally {
      setLoading(false);
    }
  }, [accountFromQuery]);

  const loadActivity = useCallback(async (botId: number | null) => {
    if (!botId) {
      setActivity(null);
      return;
    }
    setActivityLoading(true);
    try {
      const payload = await botsApi.getActivity(botId);
      setActivity(payload);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load bot activity");
    } finally {
      setActivityLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadConfigs();
  }, [loadConfigs]);

  useEffect(() => {
    void loadActivity(selectedBot?.id ?? null);
  }, [loadActivity, selectedBot?.id]);

  useEffect(() => {
    if (!selectedBot) {
      return;
    }

    setEditingBotId(selectedBot.id);
    setForm(formFromBot(selectedBot));
    setContracts([]);
    setFormError(null);
  }, [selectedBot]);

  async function handleSearchContracts() {
    if (!form.contractSearch.trim()) {
      setFormError("Contract search is required.");
      return;
    }
    setContractLoading(true);
    setFormError(null);
    try {
      const rows = await botsApi.searchContracts({ searchText: form.contractSearch, live: false });
      setContracts(rows);
      if (rows[0]) {
        applyContract(rows[0]);
      }
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Contract search failed");
    } finally {
      setContractLoading(false);
    }
  }

  function applyContract(contract: ProjectXContract) {
    setForm((current) => ({
      ...current,
      contractId: contract.id,
      symbol: contract.symbol_id ?? contract.name,
      contractSearch: contract.name,
    }));
  }

  function handleStrategyChange(strategyType: BotStrategyType) {
    setForm((current) => {
      const useDefaultName = Object.values(STRATEGY_DEFAULT_NAMES).includes(current.name);
      const nextName = useDefaultName ? strategyDefaultName(strategyType) : current.name;
      if (strategyType === "support_resistance") {
        return {
          ...current,
          strategyType,
          name: nextName,
          timeframeUnit: "hour",
          timeframeUnitNumber: "1",
          lookbackBars: "100",
          levelTolerancePercent: current.levelTolerancePercent || SUPPORT_RESISTANCE_DEFAULT_TOLERANCE_PERCENT,
          maxDataStalenessSeconds: "7200",
        };
      }

      if (strategyType === "donchian_breakout") {
        return {
          ...current,
          strategyType,
          name: nextName,
          timeframeUnit:
            current.timeframeUnit === "hour" && current.timeframeUnitNumber === "1" ? "hour" : current.timeframeUnit,
          timeframeUnitNumber:
            current.timeframeUnit === "hour" && current.timeframeUnitNumber === "1" ? "1" : current.timeframeUnitNumber,
          lookbackBars:
            current.lookbackBars === "100" ? DONCHIAN_DEFAULTS.lookbackBars : current.lookbackBars || DONCHIAN_DEFAULTS.lookbackBars,
          donchianEntryPeriod: current.donchianEntryPeriod || DONCHIAN_DEFAULTS.entryPeriod,
          donchianExitPeriod: current.donchianExitPeriod || DONCHIAN_DEFAULTS.exitPeriod,
          atrPeriod: current.atrPeriod || DONCHIAN_DEFAULTS.atrPeriod,
          atrStopMultiple: current.atrStopMultiple || DONCHIAN_DEFAULTS.atrStopMultiple,
          trailingAtrMultiplier: current.trailingAtrMultiplier || DONCHIAN_DEFAULTS.trailingAtrMultiple,
          takeProfitRMultiple: current.takeProfitRMultiple || DONCHIAN_DEFAULTS.takeProfitRMultiple,
          atrSizeReferencePercent: current.atrSizeReferencePercent || DONCHIAN_DEFAULTS.atrSizeReferencePercent,
          minSizeScale: current.minSizeScale || DONCHIAN_DEFAULTS.minSizeScale,
          maxDataStalenessSeconds: DONCHIAN_DEFAULTS.maxDataStalenessSeconds,
        };
      }

      if (strategyType === "fvg_sweep_mss") {
        return {
          ...current,
          strategyType,
          name: nextName,
          timeframeUnit: current.timeframeUnit === "hour" && current.timeframeUnitNumber === "1" ? "minute" : current.timeframeUnit,
          timeframeUnitNumber:
            current.timeframeUnit === "hour" && current.timeframeUnitNumber === "1" ? "15" : current.timeframeUnitNumber,
          lookbackBars: current.lookbackBars === "100" ? FVG_SWEEP_MSS_DEFAULTS.lookbackBars : current.lookbackBars || FVG_SWEEP_MSS_DEFAULTS.lookbackBars,
          microLevelWindow: current.microLevelWindow || FVG_SWEEP_MSS_DEFAULTS.swingWindow,
          volumeBaselineBars: current.volumeBaselineBars || FVG_SWEEP_MSS_DEFAULTS.volumeLookbackBars,
          volumeSpikeMultiple: current.volumeSpikeMultiple || FVG_SWEEP_MSS_DEFAULTS.strongVolumeMultiplier,
          stopBufferPercent: current.stopBufferPercent || FVG_SWEEP_MSS_DEFAULTS.stopBufferPercent,
          takeProfitMode: isFvgTargetMode(current.takeProfitMode) ? current.takeProfitMode : FVG_SWEEP_MSS_DEFAULTS.targetMode,
          maxDataStalenessSeconds:
            current.maxDataStalenessSeconds === "7200"
              ? FVG_SWEEP_MSS_DEFAULTS.maxDataStalenessSeconds
              : current.maxDataStalenessSeconds || FVG_SWEEP_MSS_DEFAULTS.maxDataStalenessSeconds,
        };
      }

      if (strategyType === "liquidity_sweep_retest") {
        return {
          ...current,
          strategyType,
          name: nextName,
          timeframeUnit: "hour",
          timeframeUnitNumber: "1",
          lookbackBars: LIQUIDITY_SWEEP_DEFAULTS.lookbackBars,
          fastPeriod: current.fastPeriod || LIQUIDITY_SWEEP_DEFAULTS.fastPeriod,
          slowPeriod: current.slowPeriod || LIQUIDITY_SWEEP_DEFAULTS.slowPeriod,
          levelTolerancePercent: current.levelTolerancePercent || SUPPORT_RESISTANCE_DEFAULT_TOLERANCE_PERCENT,
          reclaimWithinBars: current.reclaimWithinBars || LIQUIDITY_SWEEP_DEFAULTS.reclaimWithinBars,
          retestWithinBars: current.retestWithinBars || LIQUIDITY_SWEEP_DEFAULTS.retestWithinBars,
          stopBeyondSweepPercent: current.stopBeyondSweepPercent || LIQUIDITY_SWEEP_DEFAULTS.stopBeyondSweepPercent,
          takeProfitMode:
            current.takeProfitMode === "2r" || current.takeProfitMode === "3r" || current.takeProfitMode === "next_liquidity"
              ? current.takeProfitMode
              : LIQUIDITY_SWEEP_DEFAULTS.takeProfitMode,
          maxDataStalenessSeconds: LIQUIDITY_SWEEP_DEFAULTS.maxDataStalenessSeconds,
        };
      }

      if (strategyType === "supertrend_pivot") {
        return {
          ...current,
          strategyType,
          name: nextName,
          timeframeUnit: "minute",
          timeframeUnitNumber: "15",
          lookbackBars:
            current.lookbackBars === "100" ? SUPERTREND_PIVOT_DEFAULTS.lookbackBars : current.lookbackBars || SUPERTREND_PIVOT_DEFAULTS.lookbackBars,
          levelTolerancePercent: current.levelTolerancePercent || SUPERTREND_PIVOT_DEFAULTS.pivotTolerancePercent,
          stopBufferPercent: current.stopBufferPercent || SUPERTREND_PIVOT_DEFAULTS.stopBeyondLevelPercent,
          takeProfitRMultiple: current.takeProfitRMultiple || SUPERTREND_PIVOT_DEFAULTS.takeProfitRMultiple,
          supertrendPeriod: current.supertrendPeriod || SUPERTREND_PIVOT_DEFAULTS.supertrendPeriod,
          supertrendMultiplier: current.supertrendMultiplier || SUPERTREND_PIVOT_DEFAULTS.supertrendMultiplier,
          chopLookbackBars: current.chopLookbackBars || SUPERTREND_PIVOT_DEFAULTS.chopLookbackBars,
          chopMaxFlips: current.chopMaxFlips || SUPERTREND_PIVOT_DEFAULTS.chopMaxFlips,
          chopMaxRangePercent: current.chopMaxRangePercent || SUPERTREND_PIVOT_DEFAULTS.chopMaxRangePercent,
          maxDataStalenessSeconds: SUPERTREND_PIVOT_DEFAULTS.maxDataStalenessSeconds,
        };
      }

      if (strategyType === "opening_rvol_breakout") {
        return {
          ...current,
          strategyType,
          name: nextName,
          timeframeUnit: "minute",
          timeframeUnitNumber: "5",
          lookbackBars: current.lookbackBars === "100" ? OPENING_RVOL_DEFAULTS.lookbackBars : current.lookbackBars || OPENING_RVOL_DEFAULTS.lookbackBars,
          fastPeriod: "9",
          slowPeriod: "21",
          relativeVolumeLookbackDays: current.relativeVolumeLookbackDays || OPENING_RVOL_DEFAULTS.relativeVolumeLookbackDays,
          minRelativeVolume: current.minRelativeVolume || OPENING_RVOL_DEFAULTS.minRelativeVolume,
          minOpeningVolume: current.minOpeningVolume || OPENING_RVOL_DEFAULTS.minOpeningVolume,
          minBodyToRangeRatio: current.minBodyToRangeRatio || OPENING_RVOL_DEFAULTS.minBodyToRangeRatio,
          atrPeriod: current.atrPeriod || OPENING_RVOL_DEFAULTS.atrPeriod,
          atrStopMultiple: current.atrStopMultiple || OPENING_RVOL_DEFAULTS.atrStopMultiple,
          takeProfitRMultiple: current.takeProfitRMultiple || OPENING_RVOL_DEFAULTS.takeProfitRMultiple,
          maxTradesPerDay: OPENING_RVOL_DEFAULTS.maxTradesPerDay,
          maxDataStalenessSeconds: "600",
        };
      }

      if (strategyType === "macd_support_resistance") {
        return {
          ...current,
          strategyType,
          name: nextName,
          timeframeUnit: "hour",
          timeframeUnitNumber: "1",
          lookbackBars: "100",
          levelTolerancePercent: current.levelTolerancePercent || SUPPORT_RESISTANCE_DEFAULT_TOLERANCE_PERCENT,
          signalPeriod: current.signalPeriod || MACD_SUPPORT_RESISTANCE_DEFAULTS.signalPeriod,
          atrPeriod: current.atrPeriod || MACD_SUPPORT_RESISTANCE_DEFAULTS.atrPeriod,
          initialStopAtrMultiplier: current.initialStopAtrMultiplier || MACD_SUPPORT_RESISTANCE_DEFAULTS.initialStopAtrMultiplier,
          trailingStopMode: current.trailingStopMode || MACD_SUPPORT_RESISTANCE_DEFAULTS.trailingStopMode,
          trailingAtrMultiplier: current.trailingAtrMultiplier || MACD_SUPPORT_RESISTANCE_DEFAULTS.trailingAtrMultiplier,
          trailingMaPeriod: current.trailingMaPeriod || MACD_SUPPORT_RESISTANCE_DEFAULTS.trailingMaPeriod,
          maxDataStalenessSeconds: "7200",
        };
      }

      if (strategyType === "ema_trend_pullback") {
        return {
          ...current,
          strategyType,
          name: nextName,
          timeframeUnit: "minute",
          timeframeUnitNumber: "5",
          lookbackBars: EMA_TREND_PULLBACK_DEFAULTS.lookbackBars,
          fastPeriod: EMA_TREND_PULLBACK_DEFAULTS.fastPeriod,
          slowPeriod: EMA_TREND_PULLBACK_DEFAULTS.slowPeriod,
          maxDataStalenessSeconds: EMA_TREND_PULLBACK_DEFAULTS.maxDataStalenessSeconds,
        };
      }

      if (strategyType === "ema_scalping") {
        return {
          ...current,
          strategyType,
          name: nextName,
          timeframeUnit: "minute",
          timeframeUnitNumber: current.timeframeUnitNumber === "3" || current.timeframeUnitNumber === "5" ? current.timeframeUnitNumber : "5",
          lookbackBars: current.lookbackBars === "100" ? "200" : current.lookbackBars,
          fastPeriod: "9",
          slowPeriod: "15",
          maxDataStalenessSeconds: "600",
        };
      }

      if (strategyType === "relative_strength_spy") {
        return {
          ...current,
          strategyType,
          name: nextName,
          timeframeUnit: "minute",
          timeframeUnitNumber: "5",
          lookbackBars: RELATIVE_STRENGTH_SPY_DEFAULTS.lookbackBars,
          takeProfitRMultiple: RELATIVE_STRENGTH_SPY_DEFAULTS.takeProfitRMultiple,
          maxDataStalenessSeconds: RELATIVE_STRENGTH_SPY_DEFAULTS.maxDataStalenessSeconds,
        };
      }

      if (strategyType === "atr_adjusted_relative_strength") {
        return {
          ...current,
          strategyType,
          name: nextName,
          timeframeUnit: "minute",
          timeframeUnitNumber: "5",
          lookbackBars:
            current.lookbackBars === "100"
              ? ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS.lookbackBars
              : current.lookbackBars || ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS.lookbackBars,
          benchmarkSymbol: current.benchmarkSymbol || ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS.benchmarkSymbol,
          moveLookbackBars: current.moveLookbackBars || ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS.moveLookbackBars,
          atrPeriod: current.atrPeriod || ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS.atrPeriod,
          relativeVolumePeriod: current.relativeVolumePeriod || ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS.relativeVolumePeriod,
          relativeVolumeCap: current.relativeVolumeCap || ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS.relativeVolumeCap,
          longScoreThreshold: current.longScoreThreshold || ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS.longScoreThreshold,
          shortScoreThreshold: current.shortScoreThreshold || ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS.shortScoreThreshold,
          emaPeriod: current.emaPeriod || ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS.emaPeriod,
          stopStructureWindow: current.stopStructureWindow || ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS.stopStructureWindow,
          stopAtrMultiple: current.stopAtrMultiple || ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS.stopAtrMultiple,
          takeProfitRMultiple: current.takeProfitRMultiple || ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS.takeProfitRMultiple,
          maxDataStalenessSeconds: ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS.maxDataStalenessSeconds,
        };
      }

      if (strategyType === "pullback_trap_reversal") {
        return {
          ...current,
          strategyType,
          name: nextName,
          timeframeUnit: "minute",
          timeframeUnitNumber: "5",
          lookbackBars: current.lookbackBars === "100" ? PULLBACK_TRAP_DEFAULTS.lookbackBars : current.lookbackBars || PULLBACK_TRAP_DEFAULTS.lookbackBars,
          fastPeriod: current.fastPeriod || PULLBACK_TRAP_DEFAULTS.fastPeriod,
          slowPeriod: current.slowPeriod || PULLBACK_TRAP_DEFAULTS.slowPeriod,
          pullbackLookbackBars: current.pullbackLookbackBars || PULLBACK_TRAP_DEFAULTS.pullbackLookbackBars,
          microLevelWindow: current.microLevelWindow || PULLBACK_TRAP_DEFAULTS.microLevelWindow,
          volumeBaselineBars: current.volumeBaselineBars || PULLBACK_TRAP_DEFAULTS.volumeBaselineBars,
          volumeSpikeMultiple: current.volumeSpikeMultiple || PULLBACK_TRAP_DEFAULTS.volumeSpikeMultiple,
          wickToBodyRatioMin: current.wickToBodyRatioMin || PULLBACK_TRAP_DEFAULTS.wickToBodyRatioMin,
          stopBufferPercent: current.stopBufferPercent || PULLBACK_TRAP_DEFAULTS.stopBufferPercent,
          takeProfitRMultiple: current.takeProfitRMultiple || PULLBACK_TRAP_DEFAULTS.takeProfitRMultiple,
          trendConfirmationBars: current.trendConfirmationBars || PULLBACK_TRAP_DEFAULTS.trendConfirmationBars,
          minCountertrendBars: current.minCountertrendBars || PULLBACK_TRAP_DEFAULTS.minCountertrendBars,
          pullbackRangeMultiplier: current.pullbackRangeMultiplier || PULLBACK_TRAP_DEFAULTS.pullbackRangeMultiplier,
          priorSwingWindow: current.priorSwingWindow || PULLBACK_TRAP_DEFAULTS.priorSwingWindow,
          maxDataStalenessSeconds: PULLBACK_TRAP_DEFAULTS.maxDataStalenessSeconds,
        };
      }

      if (strategyType === "bollinger_rsi_reversal") {
        return {
          ...current,
          strategyType,
          name: nextName,
          timeframeUnit: "minute",
          timeframeUnitNumber: "5",
          lookbackBars: current.lookbackBars === "100" ? BOLLINGER_RSI_DEFAULTS.lookbackBars : current.lookbackBars || BOLLINGER_RSI_DEFAULTS.lookbackBars,
          rsiPeriod: current.rsiPeriod || BOLLINGER_RSI_DEFAULTS.rsiPeriod,
          bollingerPeriod: current.bollingerPeriod || BOLLINGER_RSI_DEFAULTS.bollingerPeriod,
          bollingerStddev: current.bollingerStddev || BOLLINGER_RSI_DEFAULTS.bollingerStddev,
          adxPeriod: current.adxPeriod || BOLLINGER_RSI_DEFAULTS.adxPeriod,
          rsiOversold: current.rsiOversold || BOLLINGER_RSI_DEFAULTS.rsiOversold,
          rsiOverbought: current.rsiOverbought || BOLLINGER_RSI_DEFAULTS.rsiOverbought,
          adxMax: current.adxMax || BOLLINGER_RSI_DEFAULTS.adxMax,
          swingStopLookbackBars: current.swingStopLookbackBars || BOLLINGER_RSI_DEFAULTS.swingStopLookbackBars,
          stopBufferPercent: current.stopBufferPercent || BOLLINGER_RSI_DEFAULTS.stopBufferPercent,
          takeProfitMode: current.takeProfitMode || BOLLINGER_RSI_DEFAULTS.takeProfitMode,
          takeProfitRMultiple: current.takeProfitRMultiple || BOLLINGER_RSI_DEFAULTS.takeProfitRMultiple,
          maxDataStalenessSeconds: BOLLINGER_RSI_DEFAULTS.maxDataStalenessSeconds,
        };
      }

      if (strategyType === "delayed_orb_confirmation") {
        return {
          ...current,
          strategyType,
          name: nextName,
          timeframeUnit: "minute",
          timeframeUnitNumber: "1",
          lookbackBars: DELAYED_ORB_DEFAULTS.lookbackBars,
          openingRangeMinutes: current.openingRangeMinutes || DELAYED_ORB_DEFAULTS.openingRangeMinutes,
          confirmationMinutes: current.confirmationMinutes || DELAYED_ORB_DEFAULTS.confirmationMinutes,
          orbStopMode: current.orbStopMode || DELAYED_ORB_DEFAULTS.stopMode,
          orbTargetMode: current.orbTargetMode || DELAYED_ORB_DEFAULTS.targetMode,
          maxDataStalenessSeconds: DELAYED_ORB_DEFAULTS.maxDataStalenessSeconds,
        };
      }

      if (strategyType === "orb_fibonacci_pullback") {
        return {
          ...current,
          strategyType,
          name: nextName,
          timeframeUnit: "minute",
          timeframeUnitNumber: current.timeframeUnitNumber || ORB_FIBONACCI_DEFAULTS.timeframeUnitNumber,
          lookbackBars: current.lookbackBars === "100" ? ORB_FIBONACCI_DEFAULTS.lookbackBars : current.lookbackBars || ORB_FIBONACCI_DEFAULTS.lookbackBars,
          openingRangeMinutes: current.openingRangeMinutes || ORB_FIBONACCI_DEFAULTS.openingRangeMinutes,
          swingStopLookbackBars: current.swingStopLookbackBars || ORB_FIBONACCI_DEFAULTS.swingLookbackBars,
          orbTargetMode: current.orbTargetMode || ORB_FIBONACCI_DEFAULTS.targetMode,
          maxDataStalenessSeconds: ORB_FIBONACCI_DEFAULTS.maxDataStalenessSeconds,
        };
      }

      if (strategyType === "fisher_transform_mean_reversion") {
        return {
          ...current,
          strategyType,
          name: nextName,
          timeframeUnit: "minute",
          timeframeUnitNumber: "5",
          lookbackBars: current.lookbackBars === "100" ? FISHER_DEFAULTS.lookbackBars : current.lookbackBars || FISHER_DEFAULTS.lookbackBars,
          fastPeriod: current.fastPeriod || FISHER_DEFAULTS.meanEmaPeriod,
          slowPeriod: current.slowPeriod || FISHER_DEFAULTS.trendEmaPeriod,
          fisherLength: current.fisherLength || FISHER_DEFAULTS.fisherLength,
          fisherExtremeThreshold: current.fisherExtremeThreshold || FISHER_DEFAULTS.fisherExtremeThreshold,
          priceStretchPercent: current.priceStretchPercent || FISHER_DEFAULTS.priceStretchPercent,
          emaSlopeLookbackBars: current.emaSlopeLookbackBars || FISHER_DEFAULTS.emaSlopeLookbackBars,
          emaSlopeMaxPercent: current.emaSlopeMaxPercent || FISHER_DEFAULTS.emaSlopeMaxPercent,
          swingStopLookbackBars: current.swingStopLookbackBars || FISHER_DEFAULTS.swingStopLookbackBars,
          takeProfitRMultiple: current.takeProfitRMultiple || FISHER_DEFAULTS.takeProfitRMultiple,
          maxDataStalenessSeconds: FISHER_DEFAULTS.maxDataStalenessSeconds,
        };
      }

      if (strategyType === "vwap_atr_mean_reversion") {
        return {
          ...current,
          strategyType,
          name: nextName,
          timeframeUnit: "minute",
          timeframeUnitNumber: "5",
          lookbackBars: current.lookbackBars === "100" ? "300" : current.lookbackBars || "300",
          atrPeriod: current.atrPeriod || VWAP_ATR_DEFAULTS.atrPeriod,
          rsiPeriod: current.rsiPeriod || VWAP_ATR_DEFAULTS.rsiPeriod,
          adxPeriod: current.adxPeriod || VWAP_ATR_DEFAULTS.adxPeriod,
          stretchAtrMultiple: current.stretchAtrMultiple || VWAP_ATR_DEFAULTS.stretchAtrMultiple,
          rsiOversold: current.rsiOversold || VWAP_ATR_DEFAULTS.rsiOversold,
          rsiOverbought: current.rsiOverbought || VWAP_ATR_DEFAULTS.rsiOverbought,
          adxMax: current.adxMax || VWAP_ATR_DEFAULTS.adxMax,
          vwapSlopeBars: current.vwapSlopeBars || VWAP_ATR_DEFAULTS.vwapSlopeBars,
          flatVwapThresholdBps: current.flatVwapThresholdBps || VWAP_ATR_DEFAULTS.flatVwapThresholdBps,
          localExtremeLookback: current.localExtremeLookback || VWAP_ATR_DEFAULTS.localExtremeLookback,
          stopBufferAtr: current.stopBufferAtr || VWAP_ATR_DEFAULTS.stopBufferAtr,
          takeProfitMode: current.takeProfitMode || VWAP_ATR_DEFAULTS.takeProfitMode,
          takeProfitRMultiple: current.takeProfitRMultiple || VWAP_ATR_DEFAULTS.takeProfitRMultiple,
          maxDataStalenessSeconds: "600",
        };
      }

      if (strategyType === "vwap_gap_retrace") {
        return {
          ...current,
          strategyType,
          name: nextName,
          timeframeUnit: "minute",
          timeframeUnitNumber: "1",
          lookbackBars: VWAP_GAP_RETRACE_DEFAULTS.lookbackBars,
          maxDataStalenessSeconds: VWAP_GAP_RETRACE_DEFAULTS.maxDataStalenessSeconds,
        };
      }

      return {
        ...current,
        strategyType,
        name: nextName,
        timeframeUnit:
          current.timeframeUnit === "hour" && current.timeframeUnitNumber === "1" ? "minute" : current.timeframeUnit,
        timeframeUnitNumber: current.timeframeUnit === "hour" && current.timeframeUnitNumber === "1" ? "5" : current.timeframeUnitNumber,
        lookbackBars: current.lookbackBars === "100" ? "200" : current.lookbackBars,
        maxDataStalenessSeconds: current.maxDataStalenessSeconds === "7200" ? "600" : current.maxDataStalenessSeconds,
      };
    });
  }

  function handleEditSelectedBot() {
    if (!selectedBot) {
      return;
    }

    setEditingBotId(selectedBot.id);
    setForm(formFromBot(selectedBot));
    setContracts([]);
    setFormError(null);
  }

  function handleCancelEdit() {
    setEditingBotId(null);
    setForm(buildInitialForm(accountFromQuery ?? accounts[0]?.id ?? null));
    setContracts([]);
    setFormError(null);
  }

  async function handleDeleteSelectedBot() {
    if (!selectedBot) {
      return;
    }

    const botName = selectedBot.name;
    if (!window.confirm(`Delete "${botName}"? This will remove the strategy and its bot activity.`)) {
      return;
    }

    const deletedBotId = selectedBot.id;
    setActionLoading("delete");
    setError(null);
    try {
      await botsApi.deleteConfig(deletedBotId);
      if (editingBotId === deletedBotId) {
        handleCancelEdit();
      }
      if (lastEvaluation?.config.id === deletedBotId) {
        setLastEvaluation(null);
      }
      setActivity(null);
      await loadConfigs();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete bot");
    } finally {
      setActionLoading(null);
    }
  }

  async function handleSaveBot(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const accountId = parsePositiveInt(form.accountId);
    const timeframeUnitNumber = parsePositiveInt(form.timeframeUnitNumber);
    const lookbackBars = parsePositiveInt(form.lookbackBars);
    const fastPeriod = parsePositiveInt(form.fastPeriod);
    const slowPeriod = parsePositiveInt(form.slowPeriod);
    const effectiveFastPeriod =
      form.strategyType === "ema_trend_pullback"
        ? Number(EMA_TREND_PULLBACK_DEFAULTS.fastPeriod)
        : form.strategyType === "ema_scalping"
          ? 9
          : fastPeriod;
    const effectiveSlowPeriod =
      form.strategyType === "ema_trend_pullback"
        ? Number(EMA_TREND_PULLBACK_DEFAULTS.slowPeriod)
        : form.strategyType === "ema_scalping"
          ? 15
          : slowPeriod;
    const levelTolerancePercent = parsePositiveNumber(form.levelTolerancePercent);
    const donchianEntryPeriod = parsePositiveInt(form.donchianEntryPeriod);
    const donchianExitPeriod = parsePositiveInt(form.donchianExitPeriod);
    const reclaimWithinBars = parsePositiveInt(form.reclaimWithinBars);
    const retestWithinBars = parsePositiveInt(form.retestWithinBars);
    const stopBeyondSweepPercent = parsePositiveNumber(form.stopBeyondSweepPercent);
    const relativeVolumeLookbackDays = parsePositiveInt(form.relativeVolumeLookbackDays);
    const minRelativeVolume = parsePositiveNumber(form.minRelativeVolume);
    const minOpeningVolume = parseNonNegativeNumber(form.minOpeningVolume);
    const minBodyToRangeRatio = parsePositiveNumber(form.minBodyToRangeRatio);
    const pullbackLookbackBars = parsePositiveInt(form.pullbackLookbackBars);
    const microLevelWindow = parsePositiveInt(form.microLevelWindow);
    const volumeBaselineBars = parsePositiveInt(form.volumeBaselineBars);
    const volumeSpikeMultiple = parsePositiveNumber(form.volumeSpikeMultiple);
    const wickToBodyRatioMin = parsePositiveNumber(form.wickToBodyRatioMin);
    const stopBufferPercent = parseNonNegativeNumber(form.stopBufferPercent);
    const trendConfirmationBars = parsePositiveInt(form.trendConfirmationBars);
    const minCountertrendBars = parsePositiveInt(form.minCountertrendBars);
    const pullbackRangeMultiplier = parsePositiveNumber(form.pullbackRangeMultiplier);
    const priorSwingWindow = parsePositiveInt(form.priorSwingWindow);
    const signalPeriod = parsePositiveInt(form.signalPeriod);
    const initialStopAtrMultiplier = parsePositiveNumber(form.initialStopAtrMultiplier);
    const trailingAtrMultiplier = parsePositiveNumber(form.trailingAtrMultiplier);
    const trailingMaPeriod = parsePositiveInt(form.trailingMaPeriod);
    const supertrendPeriod = parsePositiveInt(form.supertrendPeriod);
    const supertrendMultiplier = parsePositiveNumber(form.supertrendMultiplier);
    const chopLookbackBars = parsePositiveInt(form.chopLookbackBars);
    const chopMaxFlips = parsePositiveInt(form.chopMaxFlips);
    const chopMaxRangePercent = parsePositiveNumber(form.chopMaxRangePercent);
    const openingRangeMinutes = parsePositiveInt(form.openingRangeMinutes);
    const confirmationMinutes = parsePositiveInt(form.confirmationMinutes);
    const fisherLength = parsePositiveInt(form.fisherLength);
    const fisherExtremeThreshold = parsePositiveNumber(form.fisherExtremeThreshold);
    const priceStretchPercent = parsePositiveNumber(form.priceStretchPercent);
    const emaSlopeLookbackBars = parsePositiveInt(form.emaSlopeLookbackBars);
    const emaSlopeMaxPercent = parsePositiveNumber(form.emaSlopeMaxPercent);
    const swingStopLookbackBars = parsePositiveInt(form.swingStopLookbackBars);
    const atrPeriod = parsePositiveInt(form.atrPeriod);
    const moveLookbackBars = parsePositiveInt(form.moveLookbackBars);
    const relativeVolumePeriod = parsePositiveInt(form.relativeVolumePeriod);
    const relativeVolumeCap = parsePositiveNumber(form.relativeVolumeCap);
    const longScoreThreshold = parsePositiveNumber(form.longScoreThreshold);
    const shortScoreThreshold = parseFiniteNumber(form.shortScoreThreshold);
    const emaPeriod = parsePositiveInt(form.emaPeriod);
    const stopStructureWindow = parsePositiveInt(form.stopStructureWindow);
    const stopAtrMultiple = parseNonNegativeNumber(form.stopAtrMultiple);
    const rsiPeriod = parsePositiveInt(form.rsiPeriod);
    const bollingerPeriod = parsePositiveInt(form.bollingerPeriod);
    const bollingerStddev = parsePositiveNumber(form.bollingerStddev);
    const adxPeriod = parsePositiveInt(form.adxPeriod);
    const stretchAtrMultiple = parsePositiveNumber(form.stretchAtrMultiple);
    const rsiOversold = parsePositiveNumber(form.rsiOversold);
    const rsiOverbought = parsePositiveNumber(form.rsiOverbought);
    const adxMax = parsePositiveNumber(form.adxMax);
    const vwapSlopeBars = parsePositiveInt(form.vwapSlopeBars);
    const flatVwapThresholdBps = parsePositiveNumber(form.flatVwapThresholdBps);
    const localExtremeLookback = parsePositiveInt(form.localExtremeLookback);
    const atrStopMultiple = parsePositiveNumber(form.atrStopMultiple);
    const atrSizeReferencePercent = parsePositiveNumber(form.atrSizeReferencePercent);
    const minSizeScale = parsePositiveNumber(form.minSizeScale);
    const stopBufferAtr = parseNonNegativeNumber(form.stopBufferAtr);
    const takeProfitRMultiple = parsePositiveNumber(form.takeProfitRMultiple);
    const orderSize = parsePositiveNumber(form.orderSize);
    const maxContracts = parsePositiveNumber(form.maxContracts);
    const maxDailyLoss = parseNonNegativeNumber(form.maxDailyLoss);
    const maxTradesPerDay = parseNonNegativeInt(form.maxTradesPerDay);
    const maxOpenPosition = parsePositiveNumber(form.maxOpenPosition);
    const cooldownSeconds = parseNonNegativeInt(form.cooldownSeconds);
    const maxDataStalenessSeconds = parsePositiveInt(form.maxDataStalenessSeconds);
    const normalizedName = form.name.trim().toLowerCase();
    const duplicateName = configs.some(
      (config) => config.id !== editingBotId && config.name.trim().toLowerCase() === normalizedName,
    );

    if (!normalizedName) {
      setFormError("Bot name is required.");
      return;
    }
    if (duplicateName) {
      setFormError("A bot with this name already exists.");
      return;
    }
    if (
      accountId === null ||
      timeframeUnitNumber === null ||
      lookbackBars === null ||
      effectiveFastPeriod === null ||
      effectiveSlowPeriod === null ||
      orderSize === null ||
      maxContracts === null ||
      maxDailyLoss === null ||
      maxTradesPerDay === null ||
      maxOpenPosition === null ||
      cooldownSeconds === null ||
      maxDataStalenessSeconds === null
    ) {
      setFormError("Numeric settings must be valid positive values.");
      return;
    }
    if (!form.contractId.trim()) {
      setFormError("Select a contract before saving.");
      return;
    }
    if (effectiveSlowPeriod <= effectiveFastPeriod) {
      setFormError("Slow period must be greater than fast period.");
      return;
    }
    if (
      form.strategyType === "ema_scalping" &&
      (form.timeframeUnit !== "minute" || (form.timeframeUnitNumber !== "3" && form.timeframeUnitNumber !== "5"))
    ) {
      setFormError("9/15 EMA scalping requires a 3-minute or 5-minute chart.");
      return;
    }
    if (form.strategyType === "support_resistance" && levelTolerancePercent === null) {
      setFormError("Level tolerance must be a valid positive percent.");
      return;
    }
    if (
      form.strategyType === "donchian_breakout" &&
      (
        donchianEntryPeriod === null ||
        donchianExitPeriod === null ||
        atrPeriod === null ||
        atrStopMultiple === null ||
        trailingAtrMultiplier === null ||
        takeProfitRMultiple === null ||
        atrSizeReferencePercent === null ||
        minSizeScale === null
      )
    ) {
      setFormError("Donchian settings must be valid numeric values.");
      return;
    }
    if (
      form.strategyType === "donchian_breakout" &&
      donchianEntryPeriod !== null &&
      donchianExitPeriod !== null &&
      donchianExitPeriod > donchianEntryPeriod
    ) {
      setFormError("Donchian exit period cannot be greater than the entry period.");
      return;
    }
    if (form.strategyType === "donchian_breakout" && minSizeScale !== null && minSizeScale > 1) {
      setFormError("Minimum size scale must be 1.0 or less.");
      return;
    }
    if (
      form.strategyType === "fvg_sweep_mss" &&
      (microLevelWindow === null || volumeBaselineBars === null || volumeSpikeMultiple === null || stopBufferPercent === null)
    ) {
      setFormError("FVG sweep settings must be valid numeric values.");
      return;
    }
    if (
      form.strategyType === "liquidity_sweep_retest" &&
      (levelTolerancePercent === null || reclaimWithinBars === null || retestWithinBars === null || stopBeyondSweepPercent === null)
    ) {
      setFormError("Liquidity sweep settings must be valid positive values.");
      return;
    }
    if (
      form.strategyType === "supertrend_pivot" &&
      (
        levelTolerancePercent === null ||
        stopBufferPercent === null ||
        stopBufferPercent <= 0 ||
        takeProfitRMultiple === null ||
        supertrendPeriod === null ||
        supertrendMultiplier === null ||
        chopLookbackBars === null ||
        chopMaxFlips === null ||
        chopMaxRangePercent === null
      )
    ) {
      setFormError("Supertrend + pivot settings must be valid numeric values.");
      return;
    }
    if (
      form.strategyType === "opening_rvol_breakout" &&
      (
        relativeVolumeLookbackDays === null ||
        minRelativeVolume === null ||
        minOpeningVolume === null ||
        minBodyToRangeRatio === null ||
        atrPeriod === null ||
        atrStopMultiple === null ||
        takeProfitRMultiple === null
      )
    ) {
      setFormError("Opening RVOL breakout settings must be valid numeric values.");
      return;
    }
    if (
      form.strategyType === "atr_adjusted_relative_strength" &&
      (
        !form.benchmarkSymbol.trim() ||
        moveLookbackBars === null ||
        atrPeriod === null ||
        relativeVolumePeriod === null ||
        relativeVolumeCap === null ||
        longScoreThreshold === null ||
        shortScoreThreshold === null ||
        emaPeriod === null ||
        stopStructureWindow === null ||
        stopAtrMultiple === null ||
        takeProfitRMultiple === null
      )
    ) {
      setFormError("ATR relative strength settings must be valid numeric values and include a benchmark symbol.");
      return;
    }
    if (form.strategyType === "atr_adjusted_relative_strength" && shortScoreThreshold !== null && shortScoreThreshold >= 0) {
      setFormError("Short score threshold must be negative.");
      return;
    }
    if (
      form.strategyType === "pullback_trap_reversal" &&
      (
        pullbackLookbackBars === null ||
        microLevelWindow === null ||
        volumeBaselineBars === null ||
        volumeSpikeMultiple === null ||
        wickToBodyRatioMin === null ||
        stopBufferPercent === null ||
        takeProfitRMultiple === null ||
        trendConfirmationBars === null ||
        minCountertrendBars === null ||
        pullbackRangeMultiplier === null ||
        priorSwingWindow === null
      )
    ) {
      setFormError("Pullback trap settings must be valid numeric values.");
      return;
    }
    if (
      form.strategyType === "pullback_trap_reversal" &&
      pullbackLookbackBars !== null &&
      microLevelWindow !== null &&
      microLevelWindow > pullbackLookbackBars
    ) {
      setFormError("Micro level window cannot exceed pullback lookback bars.");
      return;
    }
    if (
      form.strategyType === "pullback_trap_reversal" &&
      pullbackLookbackBars !== null &&
      minCountertrendBars !== null &&
      minCountertrendBars > pullbackLookbackBars
    ) {
      setFormError("Countertrend bars cannot exceed pullback lookback bars.");
      return;
    }
    if (
      form.strategyType === "macd_support_resistance" &&
      (levelTolerancePercent === null || signalPeriod === null || initialStopAtrMultiplier === null || atrPeriod === null)
    ) {
      setFormError("MACD support/resistance settings must be valid numeric values.");
      return;
    }
    if (
      form.strategyType === "macd_support_resistance" &&
      ((form.trailingStopMode === "atr" && trailingAtrMultiplier === null) ||
        (form.trailingStopMode === "moving_average" && trailingMaPeriod === null))
    ) {
      setFormError("Trailing stop settings must be valid for the selected mode.");
      return;
    }
    if (form.strategyType === "delayed_orb_confirmation" && (openingRangeMinutes === null || confirmationMinutes === null)) {
      setFormError("Delayed ORB settings must be valid positive values.");
      return;
    }
    if (
      form.strategyType === "delayed_orb_confirmation" &&
      (openingRangeMinutes === null || openingRangeMinutes < 5 || confirmationMinutes === null || confirmationMinutes < 4 || confirmationMinutes > 6)
    ) {
      setFormError("Opening range must be at least 5 minutes, and confirmation must be between 4 and 6 minutes.");
      return;
    }
    if (
      form.strategyType === "orb_fibonacci_pullback" &&
      (openingRangeMinutes === null || swingStopLookbackBars === null)
    ) {
      setFormError("ORB Fibonacci settings must be valid positive values.");
      return;
    }
    if (
      form.strategyType === "orb_fibonacci_pullback" &&
      (openingRangeMinutes === null || openingRangeMinutes < 15 || openingRangeMinutes > 30)
    ) {
      setFormError("ORB Fibonacci opening range must be between 15 and 30 minutes.");
      return;
    }
    if (
      form.strategyType === "fisher_transform_mean_reversion" &&
      (
        fisherLength === null ||
        fisherExtremeThreshold === null ||
        priceStretchPercent === null ||
        emaSlopeLookbackBars === null ||
        emaSlopeMaxPercent === null ||
        swingStopLookbackBars === null ||
        takeProfitRMultiple === null
      )
    ) {
      setFormError("Fisher settings must be valid numeric values.");
      return;
    }
    if (
      form.strategyType === "bollinger_rsi_reversal" &&
      (
        rsiPeriod === null ||
        bollingerPeriod === null ||
        bollingerStddev === null ||
        adxPeriod === null ||
        rsiOversold === null ||
        rsiOverbought === null ||
        adxMax === null ||
        swingStopLookbackBars === null ||
        stopBufferPercent === null ||
        takeProfitRMultiple === null
      )
    ) {
      setFormError("Bollinger RSI settings must be valid numeric values.");
      return;
    }
    if (
      form.strategyType === "bollinger_rsi_reversal" &&
      rsiOversold !== null &&
      rsiOverbought !== null &&
      rsiOversold >= rsiOverbought
    ) {
      setFormError("RSI oversold must be lower than RSI overbought.");
      return;
    }
    if (
      form.strategyType === "vwap_atr_mean_reversion" &&
      (
        atrPeriod === null ||
        rsiPeriod === null ||
        adxPeriod === null ||
        stretchAtrMultiple === null ||
        rsiOversold === null ||
        rsiOverbought === null ||
        adxMax === null ||
        vwapSlopeBars === null ||
        flatVwapThresholdBps === null ||
        localExtremeLookback === null ||
        stopBufferAtr === null ||
        takeProfitRMultiple === null
      )
    ) {
      setFormError("VWAP ATR settings must be valid numeric values.");
      return;
    }
    if (form.strategyType === "vwap_atr_mean_reversion" && rsiOversold !== null && rsiOverbought !== null && rsiOversold >= rsiOverbought) {
      setFormError("RSI oversold must be lower than RSI overbought.");
      return;
    }

    setSaving(true);
    setFormError(null);
    try {
      const strategyParams: BotConfig["strategy_params"] =
        form.strategyType === "support_resistance"
          ? {
              bars_per_timeframe: 100,
              swing_window: 5,
              level_tolerance_percent: levelTolerancePercent ?? Number(SUPPORT_RESISTANCE_DEFAULT_TOLERANCE_PERCENT),
              stop_beyond_level_percent: 1,
              take_profit_r_multiple: 2,
            }
          : form.strategyType === "fvg_sweep_mss"
            ? {
                swing_window: microLevelWindow ?? Number(FVG_SWEEP_MSS_DEFAULTS.swingWindow),
                volume_lookback_bars: volumeBaselineBars ?? Number(FVG_SWEEP_MSS_DEFAULTS.volumeLookbackBars),
                strong_volume_multiplier: volumeSpikeMultiple ?? Number(FVG_SWEEP_MSS_DEFAULTS.strongVolumeMultiplier),
                stop_buffer_percent: stopBufferPercent ?? Number(FVG_SWEEP_MSS_DEFAULTS.stopBufferPercent),
                target_mode: isFvgTargetMode(form.takeProfitMode) ? form.takeProfitMode : FVG_SWEEP_MSS_DEFAULTS.targetMode,
              }
          : form.strategyType === "liquidity_sweep_retest"
            ? {
                bars_per_timeframe: 100,
                swing_window: 5,
                level_tolerance_percent: levelTolerancePercent ?? Number(SUPPORT_RESISTANCE_DEFAULT_TOLERANCE_PERCENT),
                reclaim_within_bars: reclaimWithinBars ?? Number(LIQUIDITY_SWEEP_DEFAULTS.reclaimWithinBars),
                retest_within_bars: retestWithinBars ?? Number(LIQUIDITY_SWEEP_DEFAULTS.retestWithinBars),
                stop_beyond_sweep_percent: stopBeyondSweepPercent ?? Number(LIQUIDITY_SWEEP_DEFAULTS.stopBeyondSweepPercent),
                take_profit_mode:
                  form.takeProfitMode === "2r" || form.takeProfitMode === "3r" || form.takeProfitMode === "next_liquidity"
                    ? form.takeProfitMode
                    : LIQUIDITY_SWEEP_DEFAULTS.takeProfitMode,
              }
          : form.strategyType === "supertrend_pivot"
            ? {
                daily_bars: 10,
                supertrend_period: supertrendPeriod ?? Number(SUPERTREND_PIVOT_DEFAULTS.supertrendPeriod),
                supertrend_multiplier: supertrendMultiplier ?? Number(SUPERTREND_PIVOT_DEFAULTS.supertrendMultiplier),
                pivot_tolerance_percent: levelTolerancePercent ?? Number(SUPERTREND_PIVOT_DEFAULTS.pivotTolerancePercent),
                stop_beyond_level_percent: stopBufferPercent ?? Number(SUPERTREND_PIVOT_DEFAULTS.stopBeyondLevelPercent),
                take_profit_r_multiple: takeProfitRMultiple ?? Number(SUPERTREND_PIVOT_DEFAULTS.takeProfitRMultiple),
                chop_lookback_bars: chopLookbackBars ?? Number(SUPERTREND_PIVOT_DEFAULTS.chopLookbackBars),
                chop_max_flips: chopMaxFlips ?? Number(SUPERTREND_PIVOT_DEFAULTS.chopMaxFlips),
                chop_max_range_percent: chopMaxRangePercent ?? Number(SUPERTREND_PIVOT_DEFAULTS.chopMaxRangePercent),
              }
          : form.strategyType === "opening_rvol_breakout"
            ? {
                relative_volume_lookback_days:
                  relativeVolumeLookbackDays ?? Number(OPENING_RVOL_DEFAULTS.relativeVolumeLookbackDays),
                min_relative_volume: minRelativeVolume ?? Number(OPENING_RVOL_DEFAULTS.minRelativeVolume),
                min_opening_volume: minOpeningVolume ?? Number(OPENING_RVOL_DEFAULTS.minOpeningVolume),
                min_body_to_range_ratio: minBodyToRangeRatio ?? Number(OPENING_RVOL_DEFAULTS.minBodyToRangeRatio),
                atr_period: atrPeriod ?? Number(OPENING_RVOL_DEFAULTS.atrPeriod),
                atr_stop_multiple: atrStopMultiple ?? Number(OPENING_RVOL_DEFAULTS.atrStopMultiple),
                take_profit_r_multiple: takeProfitRMultiple ?? Number(OPENING_RVOL_DEFAULTS.takeProfitRMultiple),
              }
          : form.strategyType === "relative_strength_spy"
            ? {
                benchmark_symbol: RELATIVE_STRENGTH_SPY_DEFAULTS.benchmarkSymbol,
                comparison_bars: Number(RELATIVE_STRENGTH_SPY_DEFAULTS.comparisonBars),
                pullback_lookback_bars: Number(RELATIVE_STRENGTH_SPY_DEFAULTS.pullbackLookbackBars),
                relative_volume_period: Number(RELATIVE_STRENGTH_SPY_DEFAULTS.relativeVolumePeriod),
                minimum_relative_volume: Number(RELATIVE_STRENGTH_SPY_DEFAULTS.minimumRelativeVolume),
                minimum_relative_strength_percent: Number(RELATIVE_STRENGTH_SPY_DEFAULTS.minimumRelativeStrengthPercent),
                minimum_benchmark_move_percent: Number(RELATIVE_STRENGTH_SPY_DEFAULTS.minimumBenchmarkMovePercent),
                ema_period: Number(RELATIVE_STRENGTH_SPY_DEFAULTS.emaPeriod),
                swing_window: Number(RELATIVE_STRENGTH_SPY_DEFAULTS.swingWindow),
                major_level_lookback_bars: Number(RELATIVE_STRENGTH_SPY_DEFAULTS.majorLevelLookbackBars),
                entry_level_tolerance_percent: Number(RELATIVE_STRENGTH_SPY_DEFAULTS.entryLevelTolerancePercent),
                stop_buffer_percent: Number(RELATIVE_STRENGTH_SPY_DEFAULTS.stopBufferPercent),
                take_profit_r_multiple: Number(RELATIVE_STRENGTH_SPY_DEFAULTS.takeProfitRMultiple),
              }
          : form.strategyType === "atr_adjusted_relative_strength"
            ? {
                benchmark_symbol: form.benchmarkSymbol.trim() || ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS.benchmarkSymbol,
                move_lookback_bars: moveLookbackBars ?? Number(ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS.moveLookbackBars),
                atr_period: atrPeriod ?? Number(ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS.atrPeriod),
                relative_volume_period:
                  relativeVolumePeriod ?? Number(ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS.relativeVolumePeriod),
                relative_volume_cap: relativeVolumeCap ?? Number(ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS.relativeVolumeCap),
                long_score_threshold:
                  longScoreThreshold ?? Number(ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS.longScoreThreshold),
                short_score_threshold:
                  shortScoreThreshold ?? Number(ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS.shortScoreThreshold),
                ema_period: emaPeriod ?? Number(ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS.emaPeriod),
                stop_structure_window:
                  stopStructureWindow ?? Number(ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS.stopStructureWindow),
                stop_atr_multiple: stopAtrMultiple ?? Number(ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS.stopAtrMultiple),
                take_profit_r_multiple:
                  takeProfitRMultiple ?? Number(ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS.takeProfitRMultiple),
              }
          : form.strategyType === "pullback_trap_reversal"
            ? {
                pullback_lookback_bars: pullbackLookbackBars ?? Number(PULLBACK_TRAP_DEFAULTS.pullbackLookbackBars),
                micro_level_window: microLevelWindow ?? Number(PULLBACK_TRAP_DEFAULTS.microLevelWindow),
                volume_baseline_bars: volumeBaselineBars ?? Number(PULLBACK_TRAP_DEFAULTS.volumeBaselineBars),
                volume_spike_multiple: volumeSpikeMultiple ?? Number(PULLBACK_TRAP_DEFAULTS.volumeSpikeMultiple),
                wick_to_body_ratio_min: wickToBodyRatioMin ?? Number(PULLBACK_TRAP_DEFAULTS.wickToBodyRatioMin),
                stop_buffer_percent: stopBufferPercent ?? Number(PULLBACK_TRAP_DEFAULTS.stopBufferPercent),
                take_profit_r_multiple: takeProfitRMultiple ?? Number(PULLBACK_TRAP_DEFAULTS.takeProfitRMultiple),
                trend_confirmation_bars: trendConfirmationBars ?? Number(PULLBACK_TRAP_DEFAULTS.trendConfirmationBars),
                min_countertrend_bars: minCountertrendBars ?? Number(PULLBACK_TRAP_DEFAULTS.minCountertrendBars),
                pullback_range_multiplier: pullbackRangeMultiplier ?? Number(PULLBACK_TRAP_DEFAULTS.pullbackRangeMultiplier),
                prior_swing_window: priorSwingWindow ?? Number(PULLBACK_TRAP_DEFAULTS.priorSwingWindow),
              }
          : form.strategyType === "macd_support_resistance"
            ? {
                bars_per_timeframe: 100,
                swing_window: 5,
                level_tolerance_percent: levelTolerancePercent ?? Number(SUPPORT_RESISTANCE_DEFAULT_TOLERANCE_PERCENT),
                signal_period: signalPeriod ?? Number(MACD_SUPPORT_RESISTANCE_DEFAULTS.signalPeriod),
                atr_period: atrPeriod ?? Number(MACD_SUPPORT_RESISTANCE_DEFAULTS.atrPeriod),
                initial_stop_atr_multiplier:
                  initialStopAtrMultiplier ?? Number(MACD_SUPPORT_RESISTANCE_DEFAULTS.initialStopAtrMultiplier),
                trailing_stop_mode: form.trailingStopMode,
                trailing_atr_multiplier:
                  trailingAtrMultiplier ?? Number(MACD_SUPPORT_RESISTANCE_DEFAULTS.trailingAtrMultiplier),
                trailing_ma_period: trailingMaPeriod ?? Number(MACD_SUPPORT_RESISTANCE_DEFAULTS.trailingMaPeriod),
              }
          : form.strategyType === "ema_trend_pullback"
            ? {
                rsi_period: Number(EMA_TREND_PULLBACK_DEFAULTS.rsiPeriod),
                volume_average_period: Number(EMA_TREND_PULLBACK_DEFAULTS.volumeAveragePeriod),
                swing_lookback_bars: Number(EMA_TREND_PULLBACK_DEFAULTS.swingLookbackBars),
                long_rsi_min: Number(EMA_TREND_PULLBACK_DEFAULTS.longRsiMin),
                long_rsi_max: Number(EMA_TREND_PULLBACK_DEFAULTS.longRsiMax),
                short_rsi_min: Number(EMA_TREND_PULLBACK_DEFAULTS.shortRsiMin),
                short_rsi_max: Number(EMA_TREND_PULLBACK_DEFAULTS.shortRsiMax),
                partial_take_profit_r_multiple: Number(EMA_TREND_PULLBACK_DEFAULTS.partialTakeProfitRMultiple),
                final_take_profit_r_multiple: Number(EMA_TREND_PULLBACK_DEFAULTS.finalTakeProfitRMultiple),
              }
          : form.strategyType === "delayed_orb_confirmation"
            ? {
                opening_range_minutes: openingRangeMinutes ?? Number(DELAYED_ORB_DEFAULTS.openingRangeMinutes),
                confirmation_minutes: confirmationMinutes ?? Number(DELAYED_ORB_DEFAULTS.confirmationMinutes),
                stop_mode: form.orbStopMode,
                target_mode: form.orbTargetMode,
              }
          : form.strategyType === "orb_fibonacci_pullback"
            ? {
                opening_range_minutes: openingRangeMinutes ?? Number(ORB_FIBONACCI_DEFAULTS.openingRangeMinutes),
                swing_lookback_bars: swingStopLookbackBars ?? Number(ORB_FIBONACCI_DEFAULTS.swingLookbackBars),
                take_profit_mode: form.orbTargetMode,
              }
          : form.strategyType === "fisher_transform_mean_reversion"
            ? {
                fisher_length: fisherLength ?? Number(FISHER_DEFAULTS.fisherLength),
                fisher_extreme_threshold: fisherExtremeThreshold ?? Number(FISHER_DEFAULTS.fisherExtremeThreshold),
                price_stretch_percent: priceStretchPercent ?? Number(FISHER_DEFAULTS.priceStretchPercent),
                ema_slope_lookback_bars: emaSlopeLookbackBars ?? Number(FISHER_DEFAULTS.emaSlopeLookbackBars),
                ema_slope_max_percent: emaSlopeMaxPercent ?? Number(FISHER_DEFAULTS.emaSlopeMaxPercent),
                swing_stop_lookback_bars: swingStopLookbackBars ?? Number(FISHER_DEFAULTS.swingStopLookbackBars),
                take_profit_r_multiple: takeProfitRMultiple ?? Number(FISHER_DEFAULTS.takeProfitRMultiple),
              }
          : form.strategyType === "bollinger_rsi_reversal"
            ? {
                rsi_period: rsiPeriod ?? Number(BOLLINGER_RSI_DEFAULTS.rsiPeriod),
                rsi_oversold: rsiOversold ?? Number(BOLLINGER_RSI_DEFAULTS.rsiOversold),
                rsi_overbought: rsiOverbought ?? Number(BOLLINGER_RSI_DEFAULTS.rsiOverbought),
                bollinger_period: bollingerPeriod ?? Number(BOLLINGER_RSI_DEFAULTS.bollingerPeriod),
                bollinger_stddev: bollingerStddev ?? Number(BOLLINGER_RSI_DEFAULTS.bollingerStddev),
                adx_period: adxPeriod ?? Number(BOLLINGER_RSI_DEFAULTS.adxPeriod),
                adx_max: adxMax ?? Number(BOLLINGER_RSI_DEFAULTS.adxMax),
                swing_stop_lookback_bars: swingStopLookbackBars ?? Number(BOLLINGER_RSI_DEFAULTS.swingStopLookbackBars),
                stop_buffer_percent: stopBufferPercent ?? Number(BOLLINGER_RSI_DEFAULTS.stopBufferPercent),
                take_profit_mode: isBollingerRsiTakeProfitMode(form.takeProfitMode)
                  ? form.takeProfitMode
                  : BOLLINGER_RSI_DEFAULTS.takeProfitMode,
                take_profit_r_multiple: takeProfitRMultiple ?? Number(BOLLINGER_RSI_DEFAULTS.takeProfitRMultiple),
              }
          : form.strategyType === "vwap_gap_retrace"
            ? {
                min_gap_percent: Number(VWAP_GAP_RETRACE_DEFAULTS.minGapPercent),
                wait_start_minutes: Number(VWAP_GAP_RETRACE_DEFAULTS.waitStartMinutes),
                wait_end_minutes: Number(VWAP_GAP_RETRACE_DEFAULTS.waitEndMinutes),
                min_volume_ratio: Number(VWAP_GAP_RETRACE_DEFAULTS.minVolumeRatio),
                stop_beyond_vwap_percent: Number(VWAP_GAP_RETRACE_DEFAULTS.stopBeyondVwapPercent),
                touch_tolerance_percent: Number(VWAP_GAP_RETRACE_DEFAULTS.touchTolerancePercent),
                bars_to_fetch: Number(VWAP_GAP_RETRACE_DEFAULTS.barsToFetch),
              }
          : form.strategyType === "vwap_atr_mean_reversion"
            ? {
                atr_period: atrPeriod ?? Number(VWAP_ATR_DEFAULTS.atrPeriod),
                rsi_period: rsiPeriod ?? Number(VWAP_ATR_DEFAULTS.rsiPeriod),
                adx_period: adxPeriod ?? Number(VWAP_ATR_DEFAULTS.adxPeriod),
                stretch_atr_multiple: stretchAtrMultiple ?? Number(VWAP_ATR_DEFAULTS.stretchAtrMultiple),
                rsi_oversold: rsiOversold ?? Number(VWAP_ATR_DEFAULTS.rsiOversold),
                rsi_overbought: rsiOverbought ?? Number(VWAP_ATR_DEFAULTS.rsiOverbought),
                adx_max: adxMax ?? Number(VWAP_ATR_DEFAULTS.adxMax),
                vwap_slope_bars: vwapSlopeBars ?? Number(VWAP_ATR_DEFAULTS.vwapSlopeBars),
                flat_vwap_threshold_bps: flatVwapThresholdBps ?? Number(VWAP_ATR_DEFAULTS.flatVwapThresholdBps),
                local_extreme_lookback: localExtremeLookback ?? Number(VWAP_ATR_DEFAULTS.localExtremeLookback),
                stop_buffer_atr: stopBufferAtr ?? Number(VWAP_ATR_DEFAULTS.stopBufferAtr),
                take_profit_mode: isVwapAtrTakeProfitMode(form.takeProfitMode)
                  ? form.takeProfitMode
                  : VWAP_ATR_DEFAULTS.takeProfitMode,
                take_profit_r_multiple: takeProfitRMultiple ?? Number(VWAP_ATR_DEFAULTS.takeProfitRMultiple),
              }
            : {};
      const payload = {
        name: form.name.trim(),
        account_id: accountId,
        contract_id: form.contractId,
        symbol: form.symbol || null,
        strategy_type: form.strategyType,
        strategy_params: strategyParams,
        timeframe_unit: form.timeframeUnit,
        timeframe_unit_number: timeframeUnitNumber,
        lookback_bars: lookbackBars,
        fast_period: effectiveFastPeriod,
        slow_period: effectiveSlowPeriod,
        order_size: orderSize,
        max_contracts: maxContracts,
        max_daily_loss: maxDailyLoss,
        max_trades_per_day: maxTradesPerDay,
        max_open_position: maxOpenPosition,
        allowed_contracts: [form.contractId],
        trading_start_time: form.tradingStartTime,
        trading_end_time: form.tradingEndTime,
        cooldown_seconds: cooldownSeconds,
        max_data_staleness_seconds: maxDataStalenessSeconds,
        allow_market_depth: false,
      };

      const saved = editingBotId
        ? await botsApi.updateConfig(editingBotId, payload)
        : await botsApi.createConfig({
            ...payload,
            enabled: false,
            execution_mode: "dry_run",
          });
      setEditingBotId(null);
      await loadConfigs();
      setSelectedBotId(saved.id);
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Failed to save bot");
    } finally {
      setSaving(false);
    }
  }

  async function runBotAction(kind: "start" | "evaluate" | "stop") {
    if (!selectedBot) {
      return;
    }
    setActionLoading(kind);
    setError(null);
    try {
      if (kind === "start") {
        const result = await botsApi.start(selectedBot.id, { dryRun: true });
        setLastEvaluation(result);
      } else if (kind === "evaluate") {
        const result = await botsApi.evaluate(selectedBot.id, { dryRun: true });
        setLastEvaluation(result);
      } else {
        await botsApi.stop(selectedBot.id);
      }
      await Promise.all([loadConfigs(), loadActivity(selectedBot.id)]);
      setChartRefreshToken((current) => current + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Bot action failed");
    } finally {
      setActionLoading(null);
    }
  }

  if (loading) {
    return (
      <div className="grid gap-5 lg:grid-cols-[1fr_1.4fr]">
        <Skeleton className="h-[520px]" />
        <Skeleton className="h-[520px]" />
      </div>
    );
  }

  return (
    <div className="space-y-5 pb-8">
      {error ? <div className="rounded-xl border border-rose-400/35 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">{error}</div> : null}

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_340px] xl:items-stretch">
        <Card className="order-3 min-w-0 xl:col-start-2 xl:row-start-1">
          <CardHeader>
            <CardTitle>Configuration</CardTitle>
            <CardDescription>ProjectX candles, server-side audit trail</CardDescription>
          </CardHeader>
          <CardContent>
            <form className="space-y-3.5" onSubmit={handleSaveBot}>
              <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                <span>Name</span>
                <Input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} />
              </label>
              <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                <span>Strategy</span>
                <Select value={form.strategyType} onChange={(event) => handleStrategyChange(event.target.value as BotStrategyType)}>
                  {strategyOptions.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </Select>
              </label>
              <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                <span>Account</span>
                <Select value={form.accountId} onChange={(event) => setForm({ ...form, accountId: event.target.value })}>
                  <option value="">Select account</option>
                  {accounts.map((account) => (
                    <option key={account.id} value={account.id}>
                      {account.name} ({account.id})
                    </option>
                  ))}
                </Select>
              </label>

              <div className="space-y-2">
                <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                  <span>Contract</span>
                  <div className="flex gap-2">
                    <Input
                      value={form.contractSearch}
                      onChange={(event) => setForm({ ...form, contractSearch: event.target.value })}
                    />
                    <Button type="button" variant="secondary" onClick={handleSearchContracts} disabled={contractLoading}>
                      {contractLoading ? "Searching" : "Search"}
                    </Button>
                  </div>
                </label>
                {contracts.length > 0 ? (
                  <div className="grid gap-2">
                    {contracts.slice(0, 4).map((contract) => (
                      <button
                        key={contract.id}
                        type="button"
                        onClick={() => applyContract(contract)}
                        className="rounded-xl border border-slate-800 bg-slate-950/50 px-3 py-2 text-left text-xs text-slate-300 transition hover:border-cyan-400/45"
                      >
                        <span className="font-semibold text-slate-100">{contract.name}</span>
                        <span className="ml-2 text-slate-500">{contract.id}</span>
                      </button>
                    ))}
                  </div>
                ) : null}
                {form.contractId ? <p className="text-xs text-slate-500">{form.contractId}</p> : null}
              </div>

              {isLevelStrategy(form.strategyType) ? (
                <>
                  <div className="grid grid-cols-2 gap-2.5">
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Signal TF</span>
                      <Input value="1H" readOnly disabled />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Context TF</span>
                      <Input value="4H" readOnly disabled />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Bars</span>
                      <Input value="100" readOnly disabled />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Window</span>
                      <Input value="5" readOnly disabled />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Level %</span>
                      <Input
                        value={form.levelTolerancePercent}
                        onChange={(event) => setForm({ ...form, levelTolerancePercent: event.target.value })}
                      />
                    </label>
                    {form.strategyType === "liquidity_sweep_retest" ? (
                      <>
                        <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                          <span>Reclaim Bars</span>
                          <Input
                            value={form.reclaimWithinBars}
                            onChange={(event) => setForm({ ...form, reclaimWithinBars: event.target.value })}
                          />
                        </label>
                        <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                          <span>Retest Bars</span>
                          <Input
                            value={form.retestWithinBars}
                            onChange={(event) => setForm({ ...form, retestWithinBars: event.target.value })}
                          />
                        </label>
                      </>
                    ) : null}
                    {form.strategyType === "macd_support_resistance" ? (
                      <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                        <span>MACD Signal</span>
                        <Input value={form.signalPeriod} onChange={(event) => setForm({ ...form, signalPeriod: event.target.value })} />
                      </label>
                    ) : null}
                  </div>
                  {form.strategyType === "liquidity_sweep_retest" ? (
                    <>
                      <div className="grid grid-cols-2 gap-2.5">
                        <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                          <span>Bias Fast SMA</span>
                          <Input value={form.fastPeriod} onChange={(event) => setForm({ ...form, fastPeriod: event.target.value })} />
                        </label>
                        <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                          <span>Bias Slow SMA</span>
                          <Input value={form.slowPeriod} onChange={(event) => setForm({ ...form, slowPeriod: event.target.value })} />
                        </label>
                        <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                          <span>Stop %</span>
                          <Input
                            value={form.stopBeyondSweepPercent}
                            onChange={(event) => setForm({ ...form, stopBeyondSweepPercent: event.target.value })}
                          />
                        </label>
                        <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                          <span>Target</span>
                          <Select
                            value={form.takeProfitMode}
                            onChange={(event) => setForm({ ...form, takeProfitMode: event.target.value as BotLiquiditySweepTargetMode })}
                          >
                            {liquiditySweepTargetOptions.map((option) => (
                              <option key={option.value} value={option.value}>
                                {option.label}
                              </option>
                            ))}
                          </Select>
                        </label>
                      </div>
                    </>
                  ) : null}
                  {form.strategyType === "macd_support_resistance" ? (
                    <>
                      <div className="grid grid-cols-2 gap-2.5">
                        <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                          <span>Fast EMA</span>
                          <Input value={form.fastPeriod} onChange={(event) => setForm({ ...form, fastPeriod: event.target.value })} />
                        </label>
                        <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                          <span>Slow EMA</span>
                          <Input value={form.slowPeriod} onChange={(event) => setForm({ ...form, slowPeriod: event.target.value })} />
                        </label>
                      </div>
                      <div className="grid grid-cols-2 gap-2.5">
                        <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                          <span>ATR Period</span>
                          <Input value={form.atrPeriod} onChange={(event) => setForm({ ...form, atrPeriod: event.target.value })} />
                        </label>
                        <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                          <span>Init Stop ATR</span>
                          <Input
                            value={form.initialStopAtrMultiplier}
                            onChange={(event) => setForm({ ...form, initialStopAtrMultiplier: event.target.value })}
                          />
                        </label>
                        <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                          <span>Trail Mode</span>
                          <Select
                            value={form.trailingStopMode}
                            onChange={(event) => setForm({ ...form, trailingStopMode: event.target.value as BotTrailingStopMode })}
                          >
                            {trailingStopOptions.map((option) => (
                              <option key={option.value} value={option.value}>
                                {option.label}
                              </option>
                            ))}
                          </Select>
                        </label>
                        {form.trailingStopMode === "atr" ? (
                          <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                            <span>Trail ATR</span>
                            <Input
                              value={form.trailingAtrMultiplier}
                              onChange={(event) => setForm({ ...form, trailingAtrMultiplier: event.target.value })}
                            />
                          </label>
                        ) : form.trailingStopMode === "moving_average" ? (
                          <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                            <span>Trail MA Period</span>
                            <Input
                              value={form.trailingMaPeriod}
                              onChange={(event) => setForm({ ...form, trailingMaPeriod: event.target.value })}
                            />
                          </label>
                        ) : (
                          <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                            <span>Trail Basis</span>
                            <Input value="Latest swing structure" readOnly disabled />
                          </label>
                        )}
                      </div>
                    </>
                  ) : null}
                </>
              ) : form.strategyType === "opening_rvol_breakout" ? (
                <>
                  <div className="grid grid-cols-3 gap-2.5">
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Signal TF</span>
                      <Input value="5m" readOnly disabled />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Entry</span>
                      <Input value="First close" readOnly disabled />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Bars</span>
                      <Input value={form.lookbackBars} onChange={(event) => setForm({ ...form, lookbackBars: event.target.value })} />
                    </label>
                  </div>
                  <div className="grid grid-cols-3 gap-2.5">
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>RVOL Lookback</span>
                      <Input
                        value={form.relativeVolumeLookbackDays}
                        onChange={(event) => setForm({ ...form, relativeVolumeLookbackDays: event.target.value })}
                      />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Min RVOL</span>
                      <Input value={form.minRelativeVolume} onChange={(event) => setForm({ ...form, minRelativeVolume: event.target.value })} />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Min Open Vol</span>
                      <Input value={form.minOpeningVolume} onChange={(event) => setForm({ ...form, minOpeningVolume: event.target.value })} />
                    </label>
                  </div>
                  <div className="grid grid-cols-2 gap-2.5">
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Body / Range</span>
                      <Input
                        value={form.minBodyToRangeRatio}
                        onChange={(event) => setForm({ ...form, minBodyToRangeRatio: event.target.value })}
                      />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>ATR Period</span>
                      <Input value={form.atrPeriod} onChange={(event) => setForm({ ...form, atrPeriod: event.target.value })} />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>ATR Stop</span>
                      <Input value={form.atrStopMultiple} onChange={(event) => setForm({ ...form, atrStopMultiple: event.target.value })} />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Target R</span>
                      <Input
                        value={form.takeProfitRMultiple}
                        onChange={(event) => setForm({ ...form, takeProfitRMultiple: event.target.value })}
                      />
                    </label>
                  </div>
                </>
              ) : form.strategyType === "supertrend_pivot" ? (
                <>
                  <div className="grid grid-cols-3 gap-2.5">
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Unit</span>
                      <Select
                        value={form.timeframeUnit}
                        onChange={(event) => setForm({ ...form, timeframeUnit: event.target.value as BotTimeframeUnit })}
                      >
                        {timeframeUnits.map((unit) => (
                          <option key={unit} value={unit}>
                            {unit}
                          </option>
                        ))}
                      </Select>
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Size</span>
                      <Input value={form.timeframeUnitNumber} onChange={(event) => setForm({ ...form, timeframeUnitNumber: event.target.value })} />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Bars</span>
                      <Input value={form.lookbackBars} onChange={(event) => setForm({ ...form, lookbackBars: event.target.value })} />
                    </label>
                  </div>
                  <div className="grid grid-cols-3 gap-2.5">
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>ST Period</span>
                      <Input value={form.supertrendPeriod} onChange={(event) => setForm({ ...form, supertrendPeriod: event.target.value })} />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>ST Mult</span>
                      <Input value={form.supertrendMultiplier} onChange={(event) => setForm({ ...form, supertrendMultiplier: event.target.value })} />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Pivot %</span>
                      <Input value={form.levelTolerancePercent} onChange={(event) => setForm({ ...form, levelTolerancePercent: event.target.value })} />
                    </label>
                  </div>
                  <div className="grid grid-cols-3 gap-2.5">
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Stop %</span>
                      <Input value={form.stopBufferPercent} onChange={(event) => setForm({ ...form, stopBufferPercent: event.target.value })} />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>TP R</span>
                      <Input value={form.takeProfitRMultiple} onChange={(event) => setForm({ ...form, takeProfitRMultiple: event.target.value })} />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Chop Bars</span>
                      <Input value={form.chopLookbackBars} onChange={(event) => setForm({ ...form, chopLookbackBars: event.target.value })} />
                    </label>
                  </div>
                  <div className="grid grid-cols-2 gap-2.5">
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Max Flips</span>
                      <Input value={form.chopMaxFlips} onChange={(event) => setForm({ ...form, chopMaxFlips: event.target.value })} />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Range %</span>
                      <Input value={form.chopMaxRangePercent} onChange={(event) => setForm({ ...form, chopMaxRangePercent: event.target.value })} />
                    </label>
                  </div>
                </>
              ) : form.strategyType === "fvg_sweep_mss" ? (
                <>
                  <div className="grid grid-cols-3 gap-2.5">
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Unit</span>
                      <Select
                        value={form.timeframeUnit}
                        onChange={(event) => setForm({ ...form, timeframeUnit: event.target.value as BotTimeframeUnit })}
                      >
                        {timeframeUnits.map((unit) => (
                          <option key={unit} value={unit}>
                            {unit}
                          </option>
                        ))}
                      </Select>
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Size</span>
                      <Input value={form.timeframeUnitNumber} onChange={(event) => setForm({ ...form, timeframeUnitNumber: event.target.value })} />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Bars</span>
                      <Input value={form.lookbackBars} onChange={(event) => setForm({ ...form, lookbackBars: event.target.value })} />
                    </label>
                  </div>
                  <div className="grid grid-cols-2 gap-2.5">
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>FVG TF</span>
                      <Input
                        value={`${form.timeframeUnitNumber}${form.timeframeUnit === "minute" ? "m" : form.timeframeUnit === "hour" ? "H" : form.timeframeUnit === "day" ? "D" : form.timeframeUnit === "week" ? "W" : form.timeframeUnit === "month" ? "M" : "s"}`}
                        readOnly
                        disabled
                      />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Structure TF</span>
                      <Input
                        value={deriveFvgStructureTimeframe(form.timeframeUnit, Number.parseInt(form.timeframeUnitNumber, 10) || 1)}
                        readOnly
                        disabled
                      />
                    </label>
                  </div>
                  <div className="grid grid-cols-2 gap-2.5">
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Swing Window</span>
                      <Input value={form.microLevelWindow} onChange={(event) => setForm({ ...form, microLevelWindow: event.target.value })} />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Target</span>
                      <Select
                        value={isFvgTargetMode(form.takeProfitMode) ? form.takeProfitMode : FVG_SWEEP_MSS_DEFAULTS.targetMode}
                        onChange={(event) => setForm({ ...form, takeProfitMode: event.target.value as BotTakeProfitMode })}
                      >
                        <option value="2r">2R</option>
                        <option value="3r">3R</option>
                        <option value="next_liquidity">Next Liquidity</option>
                      </Select>
                    </label>
                  </div>
                  <div className="grid grid-cols-3 gap-2.5">
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Volume Bars</span>
                      <Input value={form.volumeBaselineBars} onChange={(event) => setForm({ ...form, volumeBaselineBars: event.target.value })} />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Strong Vol x</span>
                      <Input value={form.volumeSpikeMultiple} onChange={(event) => setForm({ ...form, volumeSpikeMultiple: event.target.value })} />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Stop %</span>
                      <Input value={form.stopBufferPercent} onChange={(event) => setForm({ ...form, stopBufferPercent: event.target.value })} />
                    </label>
                  </div>
                </>
              ) : form.strategyType === "relative_strength_spy" ? (
                <div className="grid grid-cols-2 gap-2.5">
                  <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                    <span>Signal TF</span>
                    <Input value="5m" readOnly disabled />
                  </label>
                  <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                    <span>Benchmark</span>
                    <Input value={RELATIVE_STRENGTH_SPY_DEFAULTS.benchmarkSymbol} readOnly disabled />
                  </label>
                  <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                    <span>Window</span>
                    <Input value={`${RELATIVE_STRENGTH_SPY_DEFAULTS.comparisonBars} bars`} readOnly disabled />
                  </label>
                  <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                    <span>Min RVOL</span>
                    <Input value={`${RELATIVE_STRENGTH_SPY_DEFAULTS.minimumRelativeVolume}x`} readOnly disabled />
                  </label>
                  <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                    <span>Entry</span>
                    <Input value="VWAP / EMA / S-R" readOnly disabled />
                  </label>
                  <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                    <span>Target</span>
                    <Input value={`${RELATIVE_STRENGTH_SPY_DEFAULTS.takeProfitRMultiple}R or level`} readOnly disabled />
                  </label>
                </div>
              ) : form.strategyType === "atr_adjusted_relative_strength" ? (
                <>
                  <div className="grid grid-cols-3 gap-2.5">
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Unit</span>
                      <Select
                        value={form.timeframeUnit}
                        onChange={(event) => setForm({ ...form, timeframeUnit: event.target.value as BotTimeframeUnit })}
                      >
                        {timeframeUnits.map((unit) => (
                          <option key={unit} value={unit}>
                            {unit}
                          </option>
                        ))}
                      </Select>
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Size</span>
                      <Input value={form.timeframeUnitNumber} onChange={(event) => setForm({ ...form, timeframeUnitNumber: event.target.value })} />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Bars</span>
                      <Input value={form.lookbackBars} onChange={(event) => setForm({ ...form, lookbackBars: event.target.value })} />
                    </label>
                  </div>

                  <div className="grid grid-cols-2 gap-2.5">
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Benchmark</span>
                      <Input
                        value={form.benchmarkSymbol}
                        onChange={(event) => setForm({ ...form, benchmarkSymbol: event.target.value.toUpperCase() })}
                      />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Move Bars</span>
                      <Input value={form.moveLookbackBars} onChange={(event) => setForm({ ...form, moveLookbackBars: event.target.value })} />
                    </label>
                  </div>

                  <div className="grid grid-cols-3 gap-2.5">
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>ATR Period</span>
                      <Input value={form.atrPeriod} onChange={(event) => setForm({ ...form, atrPeriod: event.target.value })} />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>RVOL Period</span>
                      <Input
                        value={form.relativeVolumePeriod}
                        onChange={(event) => setForm({ ...form, relativeVolumePeriod: event.target.value })}
                      />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>RVOL Cap</span>
                      <Input value={form.relativeVolumeCap} onChange={(event) => setForm({ ...form, relativeVolumeCap: event.target.value })} />
                    </label>
                  </div>

                  <div className="grid grid-cols-2 gap-2.5">
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Long Score</span>
                      <Input value={form.longScoreThreshold} onChange={(event) => setForm({ ...form, longScoreThreshold: event.target.value })} />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Short Score</span>
                      <Input value={form.shortScoreThreshold} onChange={(event) => setForm({ ...form, shortScoreThreshold: event.target.value })} />
                    </label>
                  </div>

                  <div className="grid grid-cols-3 gap-2.5">
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Entry EMA</span>
                      <Input value={form.emaPeriod} onChange={(event) => setForm({ ...form, emaPeriod: event.target.value })} />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Stop Swing Bars</span>
                      <Input
                        value={form.stopStructureWindow}
                        onChange={(event) => setForm({ ...form, stopStructureWindow: event.target.value })}
                      />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Stop ATR</span>
                      <Input value={form.stopAtrMultiple} onChange={(event) => setForm({ ...form, stopAtrMultiple: event.target.value })} />
                    </label>
                  </div>

                  <div className="grid grid-cols-2 gap-2.5">
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Target R</span>
                      <Input
                        value={form.takeProfitRMultiple}
                        onChange={(event) => setForm({ ...form, takeProfitRMultiple: event.target.value })}
                      />
                    </label>
                  </div>
                </>
              ) : form.strategyType === "pullback_trap_reversal" ? (
                <>
                  <div className="grid grid-cols-3 gap-2.5">
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Unit</span>
                      <Select
                        value={form.timeframeUnit}
                        onChange={(event) => setForm({ ...form, timeframeUnit: event.target.value as BotTimeframeUnit })}
                      >
                        {timeframeUnits.map((unit) => (
                          <option key={unit} value={unit}>
                            {unit}
                          </option>
                        ))}
                      </Select>
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Size</span>
                      <Input value={form.timeframeUnitNumber} onChange={(event) => setForm({ ...form, timeframeUnitNumber: event.target.value })} />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Bars</span>
                      <Input value={form.lookbackBars} onChange={(event) => setForm({ ...form, lookbackBars: event.target.value })} />
                    </label>
                  </div>

                  <div className="grid grid-cols-2 gap-2.5">
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Fast EMA</span>
                      <Input value={form.fastPeriod} onChange={(event) => setForm({ ...form, fastPeriod: event.target.value })} />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Slow EMA</span>
                      <Input value={form.slowPeriod} onChange={(event) => setForm({ ...form, slowPeriod: event.target.value })} />
                    </label>
                  </div>

                  <div className="grid grid-cols-3 gap-2.5">
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Pullback Bars</span>
                      <Input
                        value={form.pullbackLookbackBars}
                        onChange={(event) => setForm({ ...form, pullbackLookbackBars: event.target.value })}
                      />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Micro Window</span>
                      <Input
                        value={form.microLevelWindow}
                        onChange={(event) => setForm({ ...form, microLevelWindow: event.target.value })}
                      />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Trend Bars</span>
                      <Input
                        value={form.trendConfirmationBars}
                        onChange={(event) => setForm({ ...form, trendConfirmationBars: event.target.value })}
                      />
                    </label>
                  </div>

                  <div className="grid grid-cols-3 gap-2.5">
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Volume Bars</span>
                      <Input
                        value={form.volumeBaselineBars}
                        onChange={(event) => setForm({ ...form, volumeBaselineBars: event.target.value })}
                      />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Volume x</span>
                      <Input
                        value={form.volumeSpikeMultiple}
                        onChange={(event) => setForm({ ...form, volumeSpikeMultiple: event.target.value })}
                      />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Wick x</span>
                      <Input
                        value={form.wickToBodyRatioMin}
                        onChange={(event) => setForm({ ...form, wickToBodyRatioMin: event.target.value })}
                      />
                    </label>
                  </div>

                  <div className="grid grid-cols-3 gap-2.5">
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Min Counter</span>
                      <Input
                        value={form.minCountertrendBars}
                        onChange={(event) => setForm({ ...form, minCountertrendBars: event.target.value })}
                      />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Range x</span>
                      <Input
                        value={form.pullbackRangeMultiplier}
                        onChange={(event) => setForm({ ...form, pullbackRangeMultiplier: event.target.value })}
                      />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Prior Swing</span>
                      <Input
                        value={form.priorSwingWindow}
                        onChange={(event) => setForm({ ...form, priorSwingWindow: event.target.value })}
                      />
                    </label>
                  </div>

                  <div className="grid grid-cols-2 gap-2.5">
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Stop Buffer %</span>
                      <Input
                        value={form.stopBufferPercent}
                        onChange={(event) => setForm({ ...form, stopBufferPercent: event.target.value })}
                      />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Target R</span>
                      <Input
                        value={form.takeProfitRMultiple}
                        onChange={(event) => setForm({ ...form, takeProfitRMultiple: event.target.value })}
                      />
                    </label>
                  </div>
                </>
	              ) : form.strategyType === "delayed_orb_confirmation" ? (
	                <>
	                  <div className="grid grid-cols-3 gap-2.5">
	                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
	                      <span>Signal TF</span>
	                      <Input value="1m" readOnly disabled />
	                    </label>
	                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
	                      <span>Bars</span>
	                      <Input value={DELAYED_ORB_DEFAULTS.lookbackBars} readOnly disabled />
	                    </label>
	                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
	                      <span>Entry Window</span>
	                      <Input value="4-6m" readOnly disabled />
	                    </label>
	                  </div>

	                  <div className="grid grid-cols-2 gap-2.5">
	                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
	                      <span>Opening Range</span>
	                      <Input
	                        value={form.openingRangeMinutes}
	                        onChange={(event) => setForm({ ...form, openingRangeMinutes: event.target.value })}
	                      />
	                    </label>
	                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
	                      <span>Confirm Min</span>
	                      <Input
	                        value={form.confirmationMinutes}
	                        onChange={(event) => setForm({ ...form, confirmationMinutes: event.target.value })}
	                      />
	                    </label>
	                  </div>

	                  <div className="grid grid-cols-2 gap-2.5">
	                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
	                      <span>Stop</span>
	                      <Select
	                        value={form.orbStopMode}
	                        onChange={(event) => setForm({ ...form, orbStopMode: event.target.value as BotOrbStopMode })}
	                      >
	                        {delayedOrbStopOptions.map((option) => (
	                          <option key={option.value} value={option.value}>
	                            {option.label}
	                          </option>
	                        ))}
	                      </Select>
	                    </label>
	                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
	                      <span>Target</span>
	                      <Select
	                        value={form.orbTargetMode}
	                        onChange={(event) => setForm({ ...form, orbTargetMode: event.target.value as BotOrbTargetMode })}
	                      >
	                        {delayedOrbTargetOptions.map((option) => (
	                          <option key={option.value} value={option.value}>
	                            {option.label}
	                          </option>
	                        ))}
	                      </Select>
	                    </label>
	                  </div>
	                </>
              ) : form.strategyType === "orb_fibonacci_pullback" ? (
                <>
                  <div className="grid grid-cols-3 gap-2.5">
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Unit</span>
                      <Input value="minute" readOnly disabled />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Size</span>
                      <Input value={form.timeframeUnitNumber} onChange={(event) => setForm({ ...form, timeframeUnitNumber: event.target.value })} />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Bars</span>
                      <Input value={form.lookbackBars} onChange={(event) => setForm({ ...form, lookbackBars: event.target.value })} />
                    </label>
                  </div>

                  <div className="grid grid-cols-2 gap-2.5">
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Opening Range</span>
                      <Input
                        value={form.openingRangeMinutes}
                        onChange={(event) => setForm({ ...form, openingRangeMinutes: event.target.value })}
                      />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Swing Bars</span>
                      <Input
                        value={form.swingStopLookbackBars}
                        onChange={(event) => setForm({ ...form, swingStopLookbackBars: event.target.value })}
                      />
                    </label>
                  </div>

                  <div className="grid grid-cols-2 gap-2.5">
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Stop Model</span>
                      <Input value="78.6 or swing" readOnly disabled />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Target</span>
                      <Select
                        value={form.orbTargetMode}
                        onChange={(event) => setForm({ ...form, orbTargetMode: event.target.value as BotOrbTargetMode })}
                      >
                        {orbFibTargetOptions.map((option) => (
                          <option key={option.value} value={option.value}>
                            {option.label}
                          </option>
                        ))}
                      </Select>
                    </label>
                  </div>
                </>
              ) : form.strategyType === "vwap_gap_retrace" ? (
                <>
                  <div className="grid grid-cols-3 gap-2.5">
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Signal TF</span>
                      <Input value="1m" readOnly disabled />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Bars</span>
                      <Input value={VWAP_GAP_RETRACE_DEFAULTS.lookbackBars} readOnly disabled />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Gap %</span>
                      <Input value={VWAP_GAP_RETRACE_DEFAULTS.minGapPercent} readOnly disabled />
                    </label>
                  </div>

                  <div className="grid grid-cols-3 gap-2.5">
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Wait Start</span>
                      <Input value={`${VWAP_GAP_RETRACE_DEFAULTS.waitStartMinutes}m`} readOnly disabled />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Wait End</span>
                      <Input value={`${VWAP_GAP_RETRACE_DEFAULTS.waitEndMinutes}m`} readOnly disabled />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Vol x Prev</span>
                      <Input value={`>${VWAP_GAP_RETRACE_DEFAULTS.minVolumeRatio}x`} readOnly disabled />
                    </label>
                  </div>

                  <div className="grid grid-cols-2 gap-2.5">
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>VWAP Stop %</span>
                      <Input value={VWAP_GAP_RETRACE_DEFAULTS.stopBeyondVwapPercent} readOnly disabled />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Touch Tol %</span>
                      <Input value={VWAP_GAP_RETRACE_DEFAULTS.touchTolerancePercent} readOnly disabled />
                    </label>
                  </div>
                </>
              ) : form.strategyType === "fisher_transform_mean_reversion" ? (
                <>
                  <div className="grid grid-cols-3 gap-2.5">
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Unit</span>
                      <Select
                        value={form.timeframeUnit}
                        onChange={(event) => setForm({ ...form, timeframeUnit: event.target.value as BotTimeframeUnit })}
                      >
                        {timeframeUnits.map((unit) => (
                          <option key={unit} value={unit}>
                            {unit}
                          </option>
                        ))}
                      </Select>
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Size</span>
                      <Input value={form.timeframeUnitNumber} onChange={(event) => setForm({ ...form, timeframeUnitNumber: event.target.value })} />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Bars</span>
                      <Input value={form.lookbackBars} onChange={(event) => setForm({ ...form, lookbackBars: event.target.value })} />
                    </label>
                  </div>

                  <div className="grid grid-cols-3 gap-2.5">
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Mean EMA</span>
                      <Input value={form.fastPeriod} onChange={(event) => setForm({ ...form, fastPeriod: event.target.value })} />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Trend EMA</span>
                      <Input value={form.slowPeriod} onChange={(event) => setForm({ ...form, slowPeriod: event.target.value })} />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Fisher Len</span>
                      <Input value={form.fisherLength} onChange={(event) => setForm({ ...form, fisherLength: event.target.value })} />
                    </label>
                  </div>

                  <div className="grid grid-cols-3 gap-2.5">
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Extreme</span>
                      <Input
                        value={form.fisherExtremeThreshold}
                        onChange={(event) => setForm({ ...form, fisherExtremeThreshold: event.target.value })}
                      />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Stretch %</span>
                      <Input
                        value={form.priceStretchPercent}
                        onChange={(event) => setForm({ ...form, priceStretchPercent: event.target.value })}
                      />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Slope Bars</span>
                      <Input
                        value={form.emaSlopeLookbackBars}
                        onChange={(event) => setForm({ ...form, emaSlopeLookbackBars: event.target.value })}
                      />
                    </label>
                  </div>

                  <div className="grid grid-cols-3 gap-2.5">
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Slope % Max</span>
                      <Input
                        value={form.emaSlopeMaxPercent}
                        onChange={(event) => setForm({ ...form, emaSlopeMaxPercent: event.target.value })}
                      />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Swing Bars</span>
                      <Input
                        value={form.swingStopLookbackBars}
                        onChange={(event) => setForm({ ...form, swingStopLookbackBars: event.target.value })}
                      />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Target R</span>
                      <Input
                        value={form.takeProfitRMultiple}
                        onChange={(event) => setForm({ ...form, takeProfitRMultiple: event.target.value })}
                      />
                    </label>
                  </div>
                </>
              ) : form.strategyType === "bollinger_rsi_reversal" ? (
                <>
                  <div className="grid grid-cols-3 gap-2.5">
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Unit</span>
                      <Select
                        value={form.timeframeUnit}
                        onChange={(event) => setForm({ ...form, timeframeUnit: event.target.value as BotTimeframeUnit })}
                      >
                        {timeframeUnits.map((unit) => (
                          <option key={unit} value={unit}>
                            {unit}
                          </option>
                        ))}
                      </Select>
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Size</span>
                      <Input value={form.timeframeUnitNumber} onChange={(event) => setForm({ ...form, timeframeUnitNumber: event.target.value })} />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Bars</span>
                      <Input value={form.lookbackBars} onChange={(event) => setForm({ ...form, lookbackBars: event.target.value })} />
                    </label>
                  </div>

                  <div className="grid grid-cols-3 gap-2.5">
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>RSI Period</span>
                      <Input value={form.rsiPeriod} onChange={(event) => setForm({ ...form, rsiPeriod: event.target.value })} />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>BB Period</span>
                      <Input value={form.bollingerPeriod} onChange={(event) => setForm({ ...form, bollingerPeriod: event.target.value })} />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>BB StdDev</span>
                      <Input value={form.bollingerStddev} onChange={(event) => setForm({ ...form, bollingerStddev: event.target.value })} />
                    </label>
                  </div>

                  <div className="grid grid-cols-3 gap-2.5">
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>ADX Period</span>
                      <Input value={form.adxPeriod} onChange={(event) => setForm({ ...form, adxPeriod: event.target.value })} />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>RSI OS</span>
                      <Input value={form.rsiOversold} onChange={(event) => setForm({ ...form, rsiOversold: event.target.value })} />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>RSI OB</span>
                      <Input value={form.rsiOverbought} onChange={(event) => setForm({ ...form, rsiOverbought: event.target.value })} />
                    </label>
                  </div>

                  <div className="grid grid-cols-3 gap-2.5">
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>ADX Max</span>
                      <Input value={form.adxMax} onChange={(event) => setForm({ ...form, adxMax: event.target.value })} />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Swing Bars</span>
                      <Input
                        value={form.swingStopLookbackBars}
                        onChange={(event) => setForm({ ...form, swingStopLookbackBars: event.target.value })}
                      />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Stop Buffer %</span>
                      <Input value={form.stopBufferPercent} onChange={(event) => setForm({ ...form, stopBufferPercent: event.target.value })} />
                    </label>
                  </div>

                  <div className="grid grid-cols-2 gap-2.5">
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Target</span>
                      <Select
                        value={form.takeProfitMode}
                        onChange={(event) => setForm({ ...form, takeProfitMode: event.target.value as BotTakeProfitMode })}
                      >
                        {bollingerRsiTakeProfitOptions.map((option) => (
                          <option key={option.value} value={option.value}>
                            {option.label}
                          </option>
                        ))}
                      </Select>
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Target R</span>
                      <Input
                        value={form.takeProfitRMultiple}
                        onChange={(event) => setForm({ ...form, takeProfitRMultiple: event.target.value })}
                      />
                    </label>
                  </div>
                </>
              ) : form.strategyType === "vwap_atr_mean_reversion" ? (
                <>
                  <div className="grid grid-cols-3 gap-2.5">
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Unit</span>
                      <Select
                        value={form.timeframeUnit}
                        onChange={(event) => setForm({ ...form, timeframeUnit: event.target.value as BotTimeframeUnit })}
                      >
                        {timeframeUnits.map((unit) => (
                          <option key={unit} value={unit}>
                            {unit}
                          </option>
                        ))}
                      </Select>
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Size</span>
                      <Input value={form.timeframeUnitNumber} onChange={(event) => setForm({ ...form, timeframeUnitNumber: event.target.value })} />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Bars</span>
                      <Input value={form.lookbackBars} onChange={(event) => setForm({ ...form, lookbackBars: event.target.value })} />
                    </label>
                  </div>

                  <div className="grid grid-cols-3 gap-2.5">
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>ATR Period</span>
                      <Input value={form.atrPeriod} onChange={(event) => setForm({ ...form, atrPeriod: event.target.value })} />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>RSI Period</span>
                      <Input value={form.rsiPeriod} onChange={(event) => setForm({ ...form, rsiPeriod: event.target.value })} />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>ADX Period</span>
                      <Input value={form.adxPeriod} onChange={(event) => setForm({ ...form, adxPeriod: event.target.value })} />
                    </label>
                  </div>

                  <div className="grid grid-cols-3 gap-2.5">
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Stretch ATR</span>
                      <Input value={form.stretchAtrMultiple} onChange={(event) => setForm({ ...form, stretchAtrMultiple: event.target.value })} />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>RSI OS</span>
                      <Input value={form.rsiOversold} onChange={(event) => setForm({ ...form, rsiOversold: event.target.value })} />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>RSI OB</span>
                      <Input value={form.rsiOverbought} onChange={(event) => setForm({ ...form, rsiOverbought: event.target.value })} />
                    </label>
                  </div>

                  <div className="grid grid-cols-3 gap-2.5">
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>ADX Max</span>
                      <Input value={form.adxMax} onChange={(event) => setForm({ ...form, adxMax: event.target.value })} />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>VWAP Slope Bars</span>
                      <Input value={form.vwapSlopeBars} onChange={(event) => setForm({ ...form, vwapSlopeBars: event.target.value })} />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Flat VWAP Bps</span>
                      <Input
                        value={form.flatVwapThresholdBps}
                        onChange={(event) => setForm({ ...form, flatVwapThresholdBps: event.target.value })}
                      />
                    </label>
                  </div>

                  <div className="grid grid-cols-2 gap-2.5">
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Local Extreme Bars</span>
                      <Input
                        value={form.localExtremeLookback}
                        onChange={(event) => setForm({ ...form, localExtremeLookback: event.target.value })}
                      />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Stop Buffer ATR</span>
                      <Input value={form.stopBufferAtr} onChange={(event) => setForm({ ...form, stopBufferAtr: event.target.value })} />
                    </label>
                  </div>

                  <div className="grid grid-cols-2 gap-2.5">
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Target</span>
                      <Select
                        value={form.takeProfitMode}
                        onChange={(event) => setForm({ ...form, takeProfitMode: event.target.value as BotTakeProfitMode })}
                      >
                        {vwapAtrTakeProfitOptions.map((option) => (
                          <option key={option.value} value={option.value}>
                            {option.label}
                          </option>
                        ))}
                      </Select>
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Target R</span>
                      <Input
                        value={form.takeProfitRMultiple}
                        onChange={(event) => setForm({ ...form, takeProfitRMultiple: event.target.value })}
                      />
                    </label>
                  </div>
                </>
              ) : (
                <>
                  <div className="grid grid-cols-3 gap-2.5">
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Unit</span>
                      {form.strategyType === "ema_scalping" ? (
                        <Input value="minute" readOnly disabled />
                      ) : (
                        <Select
                          value={form.timeframeUnit}
                          onChange={(event) => setForm({ ...form, timeframeUnit: event.target.value as BotTimeframeUnit })}
                        >
                          {timeframeUnits.map((unit) => (
                            <option key={unit} value={unit}>
                              {unit}
                            </option>
                          ))}
                        </Select>
                      )}
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Size</span>
                      {form.strategyType === "ema_scalping" ? (
                        <Select value={form.timeframeUnitNumber} onChange={(event) => setForm({ ...form, timeframeUnitNumber: event.target.value })}>
                          <option value="3">3</option>
                          <option value="5">5</option>
                        </Select>
                      ) : (
                        <Input value={form.timeframeUnitNumber} onChange={(event) => setForm({ ...form, timeframeUnitNumber: event.target.value })} />
                      )}
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>Bars</span>
                      <Input value={form.lookbackBars} onChange={(event) => setForm({ ...form, lookbackBars: event.target.value })} />
                    </label>
                  </div>

                  <div className="grid grid-cols-2 gap-2.5">
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>{form.strategyType === "ema_trend_pullback" || form.strategyType === "ema_scalping" ? "Fast EMA" : "Fast SMA"}</span>
                      <Input
                        value={form.fastPeriod}
                        readOnly={form.strategyType === "ema_trend_pullback" || form.strategyType === "ema_scalping"}
                        onChange={(event) => setForm({ ...form, fastPeriod: event.target.value })}
                      />
                    </label>
                    <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                      <span>{form.strategyType === "ema_trend_pullback" || form.strategyType === "ema_scalping" ? "Slow EMA" : "Slow SMA"}</span>
                      <Input
                        value={form.slowPeriod}
                        readOnly={form.strategyType === "ema_trend_pullback" || form.strategyType === "ema_scalping"}
                        onChange={(event) => setForm({ ...form, slowPeriod: event.target.value })}
                      />
                    </label>
                  </div>
                </>
              )}

              <div className="grid grid-cols-2 gap-2.5">
                <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                  <span>Order Size</span>
                  <Input value={form.orderSize} onChange={(event) => setForm({ ...form, orderSize: event.target.value })} />
                </label>
                <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                  <span>Max Contracts</span>
                  <Input value={form.maxContracts} onChange={(event) => setForm({ ...form, maxContracts: event.target.value })} />
                </label>
                <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                  <span>Daily Loss</span>
                  <Input value={form.maxDailyLoss} onChange={(event) => setForm({ ...form, maxDailyLoss: event.target.value })} />
                </label>
                <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                  <span>Max Open</span>
                  <Input
                    value={form.maxOpenPosition}
                    onChange={(event) => setForm({ ...form, maxOpenPosition: event.target.value })}
                  />
                </label>
                <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                  <span>Trades/Day</span>
                  <Input value={form.maxTradesPerDay} onChange={(event) => setForm({ ...form, maxTradesPerDay: event.target.value })} />
                </label>
              </div>

              <div className="grid grid-cols-2 gap-2.5">
                <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                  <span>Start ET</span>
                  <Input value={form.tradingStartTime} onChange={(event) => setForm({ ...form, tradingStartTime: event.target.value })} />
                </label>
                <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                  <span>End ET</span>
                  <Input value={form.tradingEndTime} onChange={(event) => setForm({ ...form, tradingEndTime: event.target.value })} />
                </label>
                <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                  <span>Cooldown</span>
                  <Input value={form.cooldownSeconds} onChange={(event) => setForm({ ...form, cooldownSeconds: event.target.value })} />
                </label>
                <label className="block space-y-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                  <span>Stale Sec</span>
                  <Input
                    value={form.maxDataStalenessSeconds}
                    onChange={(event) => setForm({ ...form, maxDataStalenessSeconds: event.target.value })}
                  />
                </label>
              </div>

              {formError ? <p className="text-sm text-rose-300">{formError}</p> : null}
              <div className="flex gap-2">
                <Button className="flex-1" type="submit" disabled={saving}>
                  {saving ? (editingBotId ? "Updating" : "Saving") : editingBotId ? "Update Bot" : "Save Bot"}
                </Button>
                {editingBotId ? (
                  <Button type="button" variant="secondary" onClick={handleCancelEdit} disabled={saving}>
                    Cancel
                  </Button>
                ) : null}
              </div>
            </form>
          </CardContent>
        </Card>

        <div className="contents">
          <Card className="order-2 min-w-0 xl:col-span-2 xl:row-start-2">
            <CardHeader className="space-y-3">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <CardTitle>Bot</CardTitle>
                  <CardDescription>ProjectX rule execution</CardDescription>
                </div>
                {selectedBot ? (
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant={selectedBot.enabled ? "positive" : "neutral"}>
                      {selectedBot.enabled ? "Enabled" : "Disabled"}
                    </Badge>
                    <Badge variant="neutral">{strategyLabel(selectedBot.strategy_type)}</Badge>
                    <Badge variant="accent">{selectedBot.execution_mode === "dry_run" ? "Dry run" : "Live"}</Badge>
                  </div>
                ) : null}
              </div>
              <div className="flex items-center gap-2">
                <Select
                  className="min-w-0 flex-1"
                  value={selectedBot?.id ? String(selectedBot.id) : ""}
                  onChange={(event) => {
                    const nextId = Number.parseInt(event.target.value, 10);
                    setSelectedBotId(Number.isFinite(nextId) ? nextId : null);
                  }}
                >
                  {configs.length === 0 ? <option value="">No bots</option> : null}
                  {configs.map((config) => (
                    <option key={config.id} value={config.id}>
                      {config.name}
                    </option>
                  ))}
                </Select>
                <button
                  type="button"
                  aria-label={selectedBot ? `Edit ${selectedBot.name}` : "Edit selected strategy"}
                  title="Edit strategy"
                  disabled={!selectedBot || saving || actionLoading !== null}
                  onClick={handleEditSelectedBot}
                  className="grid h-10 w-10 shrink-0 place-items-center rounded-xl border border-slate-700 bg-slate-950/70 text-slate-300 transition hover:border-cyan-400/50 hover:bg-slate-900 hover:text-cyan-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400/55 disabled:cursor-not-allowed disabled:opacity-45"
                >
                  <EditIcon />
                </button>
                <button
                  type="button"
                  aria-label={selectedBot ? `Delete ${selectedBot.name}` : "Delete selected strategy"}
                  title="Delete strategy"
                  disabled={!selectedBot || saving || actionLoading !== null}
                  onClick={() => void handleDeleteSelectedBot()}
                  className="grid h-10 w-10 shrink-0 place-items-center rounded-xl border border-rose-400/30 bg-rose-500/10 text-rose-200 transition hover:border-rose-300/60 hover:bg-rose-500/15 hover:text-rose-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-300/55 disabled:cursor-not-allowed disabled:opacity-45"
                >
                  {actionLoading === "delete" ? (
                    <span className="h-4 w-4 animate-spin rounded-full border-2 border-rose-200/30 border-t-rose-100" />
                  ) : (
                    <TrashIcon />
                  )}
                </button>
              </div>
            </CardHeader>
            <CardContent>
              <div className="space-y-5">
                {selectedBot ? (
                  <div className="space-y-4">
                    <div className="grid grid-cols-2 gap-3">
                      <Metric label="Account" value={String(selectedBot.account_id)} />
                      <Metric label="Contract" value={selectedBot.symbol ?? selectedBot.contract_id} />
                      <Metric
                        label={selectedBotStrategySummary?.label ?? "Strategy"}
                        value={selectedBotStrategySummary?.value ?? "-"}
                      />
                      <Metric label="Risk" value={`$${selectedBot.max_daily_loss.toFixed(0)}`} />
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <Button onClick={() => void runBotAction("start")} disabled={actionLoading !== null}>
                        {actionLoading === "start" ? "Starting" : "Start Dry Run"}
                      </Button>
                      <Button variant="secondary" onClick={() => void runBotAction("evaluate")} disabled={actionLoading !== null}>
                        {actionLoading === "evaluate" ? "Evaluating" : "Evaluate"}
                      </Button>
                      <Button variant="danger" onClick={() => void runBotAction("stop")} disabled={actionLoading !== null}>
                        {actionLoading === "stop" ? "Stopping" : "Stop"}
                      </Button>
                    </div>
                    {selectedBotEvaluation ? (
                      <div className="grid gap-3">
                        <div className="rounded-xl border border-slate-800 bg-slate-950/45 p-3">
                          <div className="mb-2 flex items-center justify-between gap-2">
                            <Badge variant={actionBadgeVariant(selectedBotEvaluation.decision.action)}>
                              {selectedBotEvaluation.decision.action}
                            </Badge>
                            <span className="text-xs text-slate-500">{formatDateTime(selectedBotEvaluation.decision.candle_timestamp)}</span>
                          </div>
                          <p className="text-sm text-slate-200">{selectedBotEvaluation.decision.reason}</p>
                          {selectedBotEvaluation.order_attempt ? (
                            <p className="mt-2 text-xs text-slate-400">
                              Order attempt #{selectedBotEvaluation.order_attempt.id}: {selectedBotEvaluation.order_attempt.status}
                            </p>
                          ) : null}
                          {selectedBotEvaluation.risk_events.length > 0 ? (
                            <div className="mt-3 space-y-1">
                              {selectedBotEvaluation.risk_events.map((risk) => (
                                <p key={risk.id} className="text-xs text-amber-200">
                                  {risk.code}: {risk.message}
                                </p>
                              ))}
                            </div>
                          ) : null}
                        </div>
                        <div className="rounded-xl border border-slate-800 bg-slate-950/45 p-3">
                          <Sparkline candles={selectedBotEvaluation.candles} />
                        </div>
                      </div>
                    ) : null}
                  </div>
                ) : (
                  <p className="text-sm text-slate-400">No bot configuration saved.</p>
                )}

                <div className="border-t border-slate-800 pt-5">
                  <div className="mb-4 space-y-1">
                    <h4 className="text-sm font-semibold text-slate-100 md:text-base">Activity</h4>
                    <p className="text-xs text-slate-400">Signals, risk events, and order attempts</p>
                  </div>
                  {activityLoading ? (
                    <Skeleton className="h-64" />
                  ) : activity ? (
                    <div className="grid gap-4 xl:grid-cols-2">
                      <ActivityTable
                        title="Decisions"
                        rows={activity.decisions.slice(0, 8).map((decision) => ({
                          id: decision.id,
                          left: decision.action,
                          middle: decision.reason,
                          right: formatDateTime(decision.created_at),
                          badgeVariant: actionBadgeVariant(decision.action),
                        }))}
                      />
                      <ActivityTable
                        title="Orders"
                        rows={activity.order_attempts.slice(0, 8).map((attempt) => ({
                          id: attempt.id,
                          left: attempt.status,
                          middle: `${attempt.side} ${attempt.size} ${attempt.contract_id}`,
                          right: formatDateTime(attempt.created_at),
                          badgeVariant: statusBadgeVariant(attempt.status),
                        }))}
                      />
                      <ActivityTable
                        title="Risk"
                        rows={activity.risk_events.slice(0, 8).map((risk) => ({
                          id: risk.id,
                          left: risk.severity,
                          middle: `${risk.code}: ${risk.message}`,
                          right: formatDateTime(risk.created_at),
                          badgeVariant: risk.severity === "critical" ? "negative" : "warning",
                        }))}
                      />
                      <ActivityTable
                        title="Runs"
                        rows={activity.runs.slice(0, 8).map((run) => ({
                          id: run.id,
                          left: run.status,
                          middle: run.stop_reason ?? (run.dry_run ? "dry_run" : "live"),
                          right: formatDateTime(run.started_at),
                          badgeVariant: statusBadgeVariant(run.status),
                        }))}
                      />
                    </div>
                  ) : (
                    <p className="text-sm text-slate-400">No activity.</p>
                  )}
                </div>
              </div>
            </CardContent>
          </Card>

          <div className="order-1 min-w-0 xl:col-start-1 xl:row-start-1">
            <BotSignalChart
              bot={selectedBot}
              activity={activity}
              lastEvaluation={selectedBotEvaluation}
              refreshToken={chartRefreshToken}
            />
          </div>
        </div>
      </div>
    </div>
  );
}

function EditIcon() {
  return (
    <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <path d="M12 20h9" strokeLinecap="round" />
      <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function TrashIcon() {
  return (
    <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <path d="M6 7h12" strokeLinecap="round" />
      <path d="M9 7V5h6v2" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M9 10v8" strokeLinecap="round" />
      <path d="M15 10v8" strokeLinecap="round" />
      <path d="M8 7l1 13h6l1-13" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-950/45 p-3">
      <p className="text-[11px] uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-1 truncate text-sm font-semibold text-slate-100">{value}</p>
    </div>
  );
}

interface ActivityRow {
  id: number;
  left: string;
  middle: string;
  right: string;
  badgeVariant: "positive" | "negative" | "neutral" | "accent" | "warning";
}

function ActivityTable({ title, rows }: { title: string; rows: ActivityRow[] }) {
  return (
    <div className="overflow-hidden rounded-xl border border-slate-800">
      <div className="border-b border-slate-800 bg-slate-900/50 px-3 py-2 text-sm font-semibold text-slate-100">{title}</div>
      {rows.length === 0 ? (
        <p className="px-3 py-4 text-sm text-slate-500">No rows</p>
      ) : (
        <div className="max-h-64 overflow-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-24">State</TableHead>
                <TableHead>Detail</TableHead>
                <TableHead className="w-32 text-right">Time</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <TableRow key={row.id}>
                  <TableCell>
                    <Badge variant={row.badgeVariant}>{row.left}</Badge>
                  </TableCell>
                  <TableCell className="max-w-[320px] truncate text-xs text-slate-300">{row.middle}</TableCell>
                  <TableCell className="text-right text-xs text-slate-500">{row.right}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}
