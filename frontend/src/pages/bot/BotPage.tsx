import { lazy, Suspense, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useOutletContext, useSearchParams } from "react-router-dom";

import type { AppShellOutletContext } from "../../app/AppShell";
import { DemoModeNotice } from "../../components/demo/DemoModeNotice";
import { useDemoInteractionPolicy } from "../../components/demo/useDemoInteractionPolicy";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/Card";
import { Skeleton } from "../../components/ui/Skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../components/ui/Table";
import { ACCOUNT_QUERY_PARAM, parseAccountId, writeStoredAccountId } from "../../lib/accountSelection";
import { useAccountRequestGate } from "../../lib/accountRequestGate";
import { accountsApi, botsApi } from "../../lib/api";
import type {
  AccountInfo,
  BotActivity,
  BotConfig,
  BotRuntimeStatus,
  BotEvaluation,
  ProjectXMarketCandle,
} from "../../lib/types";
import { BotSignalChart } from "./BotSignalChart";
import { OrderBookPanel } from "./OrderBookPanel";
import { BotExpressAccountRequired, BotProviderWorkspaceBoundary } from "./BotAccountGate";
import {
  getBotProviderAccountId,
  getProjectXBotAccounts,
  loadBotConfigsForProviderAccount,
  resolveActiveBotAccount,
} from "./botAccountIsolation";
import type { BotMarketSnapshot } from "./botMarketContext";

const BotAnalysisPanel = lazy(() =>
  import("./BotAnalysisPanel").then((module) => ({ default: module.BotAnalysisPanel })),
);
const BotBacktestPanel = lazy(() =>
  import("./BotBacktestPanel").then((module) => ({ default: module.BotBacktestPanel })),
);

const PROVIDER_CLASSIFICATION_MAX_AGE_MS = 5 * 60 * 1_000;
const PROVIDER_CLASSIFICATION_FUTURE_TOLERANCE_MS = 30 * 1_000;
const dateTimeFormatter = new Intl.DateTimeFormat("en-US", {
  month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit",
  hour12: true, timeZone: "America/New_York",
});

type AccountEmergencyOutcome = {
  accountId: number;
  auditReference: string | null;
  completedAt: string;
  message: string;
  state: "confirmed" | "unconfirmed" | "unknown";
  status: string;
};

type AccountClassificationOverride = {
  observedAt: string | null;
  simulated: boolean | null;
};

type AccountClassificationVerification = {
  accountId: number;
  completedAt: string;
  message: string;
  state: "verified" | "blocked" | "failed";
};

function providerClassificationIsFresh(account: AccountInfo | null | undefined, now = Date.now()): boolean {
  if (!account?.provider_classification_observed_at) {
    return false;
  }
  const observedAt = Date.parse(account.provider_classification_observed_at);
  if (!Number.isFinite(observedAt)) {
    return false;
  }
  const age = now - observedAt;
  return age >= -PROVIDER_CLASSIFICATION_FUTURE_TOLERANCE_MS && age <= PROVIDER_CLASSIFICATION_MAX_AGE_MS;
}

function emergencyAuditReference(auditId: number | null | undefined, audit: Record<string, unknown>): string | null {
  if (typeof auditId === "number" && Number.isFinite(auditId)) {
    return `Audit #${auditId}`;
  }
  for (const key of ["reference", "correlation_id", "provider_reference", "request_id"]) {
    const value = audit[key];
    if (typeof value === "string" && value.trim()) {
      return `${key.replaceAll("_", " ")}: ${value.trim()}`;
    }
    if (typeof value === "number" && Number.isFinite(value)) {
      return `${key.replaceAll("_", " ")}: ${value}`;
    }
  }
  return null;
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
  if (status === "blocked" || status === "risk_blocked" || status === "error" || status === "rejected") {
    return "negative" as const;
  }
  if (status === "duplicate_skipped") {
    return "warning" as const;
  }
  return "neutral" as const;
}

function evaluationStatusLabel(status: string) {
  return status.replaceAll("_", " ");
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
  const { demoModeEnabled, demoDisabledTitle } = useDemoInteractionPolicy();
  const [searchParams, setSearchParams] = useSearchParams();
  const accountFromQuery = parseAccountId(searchParams.get(ACCOUNT_QUERY_PARAM));
  const { accounts, accountsLoading } = useOutletContext<AppShellOutletContext>();
  const activeAccount = useMemo(
    () => resolveActiveBotAccount(accounts, accountFromQuery),
    [accountFromQuery, accounts],
  );
  const activeProjectXAccountId = getBotProviderAccountId(activeAccount);
  const projectXAccounts = useMemo(() => getProjectXBotAccounts(accounts), [accounts]);
  const [configs, setConfigs] = useState<BotConfig[]>([]);
  const [selectedBotId, setSelectedBotId] = useState<number | null>(null);
  const [activity, setActivity] = useState<BotActivity | null>(null);
  const [lastEvaluation, setLastEvaluation] = useState<BotEvaluation | null>(null);
  const [loading, setLoading] = useState(true);
  const [activityLoading, setActivityLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [configWarnings, setConfigWarnings] = useState<string[]>([]);
  const [chartRefreshToken, setChartRefreshToken] = useState(0);
  const [marketSnapshot, setMarketSnapshot] = useState<BotMarketSnapshot | null>(null);
  const [authenticatedCacheScope, setAuthenticatedCacheScope] = useState<string | null>(null);
  const [runtimeStatus, setRuntimeStatus] = useState<BotRuntimeStatus | null>(null);
  const [runtimeStatusError, setRuntimeStatusError] = useState<string | null>(null);
  const [emergencyFlattenAccountId, setEmergencyFlattenAccountId] = useState<number | null>(null);
  const [emergencyOutcome, setEmergencyOutcome] = useState<AccountEmergencyOutcome | null>(null);
  const [classificationOverrides, setClassificationOverrides] = useState<Record<number, AccountClassificationOverride>>({});
  const [classificationVerificationAccountId, setClassificationVerificationAccountId] = useState<number | null>(null);
  const [classificationVerification, setClassificationVerification] = useState<AccountClassificationVerification | null>(null);
  const accountRequestGate = useAccountRequestGate(activeProjectXAccountId);
  const configsRequestSequence = useRef(0);
  const activityRequestSequence = useRef(0);
  const activityRequestController = useRef<AbortController | null>(null);
  const selectedBotIdRef = useRef<number | null>(null);
  const previousProjectXAccountIdRef = useRef<number | null>(activeProjectXAccountId);
  const activeProjectXAccountIdRef = useRef<number | null>(activeProjectXAccountId);
  const runtimeRequestController = useRef<AbortController | null>(null);

  const selectedBot = useMemo(() => {
    if (activeProjectXAccountId === null) {
      return null;
    }
    const activeConfigs = configs.filter((config) => config.account_id === activeProjectXAccountId);
    return activeConfigs.find((config) => config.enabled)
      ?? activeConfigs.find((config) => config.id === selectedBotId)
      ?? activeConfigs.find((config) => config.strategy_type === "topbot_adaptive")
      ?? activeConfigs[0] ?? null;
  }, [activeProjectXAccountId, configs, selectedBotId]);
  const selectedBotEvaluation = useMemo(
    () =>
      selectedBot &&
      lastEvaluation?.config.id === selectedBot.id &&
      lastEvaluation.config.contract_id === selectedBot.contract_id &&
      lastEvaluation.config.timeframe_unit === selectedBot.timeframe_unit &&
      lastEvaluation.config.timeframe_unit_number === selectedBot.timeframe_unit_number
        ? lastEvaluation
        : null,
    [lastEvaluation, selectedBot],
  );
  selectedBotIdRef.current = selectedBot?.id ?? null;
  activeProjectXAccountIdRef.current = activeProjectXAccountId;
  const activeClassificationOverride = activeProjectXAccountId === null
    ? undefined
    : classificationOverrides[activeProjectXAccountId];
  const activeProviderSimulated = activeClassificationOverride
    ? activeClassificationOverride.simulated
    : activeAccount?.provider_simulated;
  const activeProviderClassificationObservedAt = activeClassificationOverride
    ? activeClassificationOverride.observedAt
    : activeAccount?.provider_classification_observed_at;
  const effectiveActiveAccount = activeAccount
    ? {
        ...activeAccount,
        provider_simulated: activeProviderSimulated,
        provider_classification_observed_at: activeProviderClassificationObservedAt,
      }
    : activeAccount;
  const selectedBotActivity = useMemo(
    () =>
      selectedBot &&
      activity?.config.id === selectedBot.id &&
      activity.config.contract_id === selectedBot.contract_id &&
      activity.config.timeframe_unit === selectedBot.timeframe_unit &&
      activity.config.timeframe_unit_number === selectedBot.timeframe_unit_number
        ? activity
        : null,
    [activity, selectedBot],
  );
  const activeAccountClassificationFresh = providerClassificationIsFresh(effectiveActiveAccount);
  const runtimeContinuousBlockReason = useMemo(() => {
    if (runtimeStatusError) {
      return `Runtime status could not be verified: ${runtimeStatusError}`;
    }
    if (!runtimeStatus) {
      return "Waiting for the continuous-worker status check.";
    }
    if (runtimeStatus.checks.worker_enabled !== true) {
      return "The continuous worker is disabled on the server.";
    }
    if (runtimeStatus.checks.worker_task_healthy !== true) {
      return `The continuous worker task is unhealthy (state: ${runtimeStatus.state}).`;
    }
    if (runtimeStatus.checks.lease_healthy !== true) {
      return "No healthy worker lease is currently confirmed.";
    }
    if (runtimeStatus.checks.account_emergency_clear !== true) {
      const unresolved = runtimeStatus.counts.unresolved_account_emergency_actions;
      return Number.isFinite(unresolved) && unresolved > 0
        ? `${unresolved} account emergency-flatten outcome(s) remain unresolved. Confirm the affected account is flat before arming automation.`
        : "An account emergency-flatten outcome remains unresolved. Confirm the affected account is flat before arming automation.";
    }
    if (["crashed", "error", "lease_lost", "stopped"].includes(runtimeStatus.state)) {
      return `The continuous worker state is ${runtimeStatus.state}.`;
    }
    if (
      runtimeStatus.checks.provider_healthy !== true ||
      ["error", "throttled"].includes(runtimeStatus.provider_status)
    ) {
      return `ProjectX provider health is ${runtimeStatus.provider_status}.`;
    }
    if (
      runtimeStatus.checks.submissions_reconciled !== true ||
      runtimeStatus.counts.unresolved_live_submissions !== 0
    ) {
      const unresolved = runtimeStatus.counts.unresolved_live_submissions;
      return Number.isFinite(unresolved)
        ? `${unresolved} live submission(s) still require reconciliation.`
        : "Live-submission reconciliation is not confirmed.";
    }
    return null;
  }, [runtimeStatus, runtimeStatusError]);
  const liveRunBlockReason = useMemo(() => {
    if (runtimeContinuousBlockReason) {
      return runtimeContinuousBlockReason;
    }
    if (runtimeStatus?.checks.live_gate !== true) {
      return "Both server-side live-routing gates must be enabled.";
    }
    if (runtimeStatus.checks.account_classification_fresh !== true) {
      return "A fresh simulated-account classification is not confirmed for every armed live account.";
    }
    if (runtimeStatus.checks.accounts_simulated !== true) {
      return "At least one armed account is not eligible for automated ProjectX routing.";
    }
    if (activeProviderSimulated === false) {
      return "Automated ProjectX routing is blocked for live or funded accounts.";
    }
    if (activeProviderSimulated !== true) {
      return "ProjectX has not verified this account as simulated Practice.";
    }
    if (!activeAccountClassificationFresh) {
      return "The simulated Practice-account classification is stale; wait for a fresh provider observation.";
    }
    return null;
  }, [activeAccountClassificationFresh, activeProviderSimulated, runtimeContinuousBlockReason, runtimeStatus]);
  const runtimeCanArmContinuous = runtimeContinuousBlockReason === null;

  const loadRuntimeStatus = useCallback(async () => {
    runtimeRequestController.current?.abort();
    const controller = new AbortController();
    runtimeRequestController.current = controller;
    try {
      const nextStatus = await botsApi.getRuntimeStatus({ signal: controller.signal });
      if (!controller.signal.aborted) {
        setRuntimeStatus(nextStatus);
        setRuntimeStatusError(null);
      }
    } catch (err) {
      if (!controller.signal.aborted && !(err instanceof Error && err.name === "AbortError")) {
        setRuntimeStatus(null);
        setRuntimeStatusError(err instanceof Error ? err.message : "Runtime status unavailable");
      }
    } finally {
      if (runtimeRequestController.current === controller) {
        runtimeRequestController.current = null;
      }
    }
  }, []);

  const loadConfigs = useCallback(async ({ showLoading = false }: { showLoading?: boolean } = {}) => {
    if (activeProjectXAccountId === null) {
      setAuthenticatedCacheScope(null);
      setConfigs([]);
      setConfigWarnings([]);
      setSelectedBotId(null);
      setLoading(false);
      return;
    }
    const accountScope = accountRequestGate.capture(activeProjectXAccountId);
    if (!accountRequestGate.isActive(accountScope)) {
      return;
    }
    const requestToken = accountRequestGate.begin(activeProjectXAccountId, "configs");
    const sequence = configsRequestSequence.current + 1;
    configsRequestSequence.current = sequence;
    if (showLoading) {
      setLoading(true);
    }
    setError(null);
    try {
      const result = await loadBotConfigsForProviderAccount(
        activeProjectXAccountId,
        botsApi.listConfigsWithCacheScope,
      );
      if (
        configsRequestSequence.current !== sequence ||
        !accountRequestGate.isCurrent(requestToken)
      ) {
        return;
      }
      if (!result) {
        return;
      }
      const { configs: botRows, cacheScope } = result;
      setAuthenticatedCacheScope(cacheScope);
      setConfigs(botRows.items);
      setConfigWarnings(botRows.warnings ?? []);
      setSelectedBotId((current) => {
        if (current && botRows.items.some((item) => item.id === current)) {
          return current;
        }
        return botRows.items.find((item) => item.strategy_type === "topbot_adaptive")?.id ?? botRows.items[0]?.id ?? null;
      });
    } catch (err) {
      if (
        configsRequestSequence.current === sequence &&
        accountRequestGate.isCurrent(requestToken)
      ) {
        setConfigWarnings([]);
        setError(err instanceof Error ? err.message : "Failed to load bot data");
      }
    } finally {
      if (
        showLoading &&
        configsRequestSequence.current === sequence &&
        accountRequestGate.isCurrent(requestToken)
      ) {
        setLoading(false);
      }
    }
  }, [accountRequestGate, activeProjectXAccountId]);

  const loadActivity = useCallback(async (botId: number | null) => {
    const sequence = activityRequestSequence.current + 1;
    activityRequestSequence.current = sequence;
    activityRequestController.current?.abort();
    activityRequestController.current = null;
    if (!botId) {
      setActivity(null);
      setActivityLoading(false);
      return;
    }
    if (selectedBotIdRef.current !== botId) {
      return;
    }
    const requestAccountId = activeProjectXAccountId;
    if (requestAccountId === null) {
      return;
    }
    const accountScope = accountRequestGate.capture(requestAccountId);
    if (!accountRequestGate.isActive(accountScope)) {
      return;
    }
    const requestToken = accountRequestGate.begin(requestAccountId, "activity");
    const controller = new AbortController();
    activityRequestController.current = controller;
    setActivity(null);
    setActivityLoading(true);
    try {
      const payload = await botsApi.getActivity(botId, 50, { signal: controller.signal });
      if (
        activityRequestSequence.current === sequence &&
        selectedBotIdRef.current === botId &&
        accountRequestGate.isCurrent(requestToken)
      ) {
        setActivity(payload);
      }
    } catch (err) {
      if (
        activityRequestSequence.current === sequence &&
        selectedBotIdRef.current === botId &&
        accountRequestGate.isCurrent(requestToken) &&
        !(err instanceof Error && err.name === "AbortError")
      ) {
        setError(err instanceof Error ? err.message : "Failed to load bot activity");
      }
    } finally {
      if (
        activityRequestSequence.current === sequence &&
        accountRequestGate.isCurrent(requestToken)
      ) {
        setActivityLoading(false);
        activityRequestController.current = null;
      }
    }
  }, [accountRequestGate, activeProjectXAccountId]);

  useEffect(() => {
    void loadConfigs({ showLoading: true });
  }, [loadConfigs]);

  useEffect(() => {
    if (demoModeEnabled || activeProjectXAccountId === null) {
      runtimeRequestController.current?.abort();
      runtimeRequestController.current = null;
      setRuntimeStatus(null);
      setRuntimeStatusError(null);
      return undefined;
    }
    void loadRuntimeStatus();
    const intervalId = window.setInterval(() => void loadRuntimeStatus(), 15_000);
    return () => {
      window.clearInterval(intervalId);
      runtimeRequestController.current?.abort();
      runtimeRequestController.current = null;
    };
  }, [activeProjectXAccountId, demoModeEnabled, loadRuntimeStatus]);

  useEffect(() => () => {
    configsRequestSequence.current += 1;
    activityRequestSequence.current += 1;
    activityRequestController.current?.abort();
    activityRequestController.current = null;
    runtimeRequestController.current?.abort();
    runtimeRequestController.current = null;
  }, []);

  useLayoutEffect(() => {
    if (previousProjectXAccountIdRef.current === activeProjectXAccountId) {
      return;
    }

    previousProjectXAccountIdRef.current = activeProjectXAccountId;
    setConfigs([]);
    setConfigWarnings([]);
    setSelectedBotId(null);
    setActivity(null);
    setLastEvaluation(null);
    setMarketSnapshot(null);
    setAuthenticatedCacheScope(null);
    setLoading(activeProjectXAccountId !== null);
    setActivityLoading(false);
    setActionLoading(null);
    setError(null);
  }, [activeProjectXAccountId]);

  useEffect(() => {
    activityRequestSequence.current += 1;
    activityRequestController.current?.abort();
    activityRequestController.current = null;
    setActivity(null);
    setActivityLoading(false);
    const botId = selectedBot?.id ?? null;
    if (!botId) {
      return undefined;
    }

    const run = () => void loadActivity(botId);
    if (typeof window.requestIdleCallback === "function") {
      const requestId = window.requestIdleCallback(run, { timeout: 1_500 });
      return () => window.cancelIdleCallback(requestId);
    }
    const timeoutId = window.setTimeout(run, 100);
    return () => window.clearTimeout(timeoutId);
  }, [
    loadActivity,
    selectedBot?.contract_id,
    selectedBot?.id,
    selectedBot?.timeframe_unit,
    selectedBot?.timeframe_unit_number,
    selectedBot?.updated_at,
  ]);

  async function runBotAction(kind: "dry_run" | "live" | "evaluate" | "stop") {
    if (demoModeEnabled || activeProjectXAccountId === null || actionLoading !== null) return;
    const starting = kind === "dry_run" || kind === "live";
    if (!starting && !selectedBot) return;
    const blockReason = kind === "live" ? liveRunBlockReason : runtimeContinuousBlockReason;
    if (starting && blockReason) {
      setError(blockReason);
      return;
    }
    if (kind === "live" && !window.confirm(
      `Start TopBot Live Run on account ${activeProjectXAccountId}? ` +
      "This allows MNQ orders while all server safety checks pass. " +
      "An application restart disarms routing; start a new Live Run to resume.",
    )) return;
    const requestAccountId = activeProjectXAccountId;
    let requestBotId = selectedBot?.id ?? null;
    const requestToken = accountRequestGate.begin(requestAccountId, "bot-action");
    setActionLoading(kind);
    setError(null);
    try {
      if (starting) {
        const result = await botsApi.startTopBot(requestAccountId, kind === "dry_run");
        if (!accountRequestGate.isCurrent(requestToken)) return;
        requestBotId = result.config.id;
        setConfigs((current) => [result.config, ...current.filter((config) => config.id !== result.config.id)]);
        setSelectedBotId(result.config.id);
        setLastEvaluation(result);
      } else if (kind === "evaluate" && requestBotId !== null) {
        const result = await botsApi.evaluate(requestBotId, { dryRun: true });
        if (!accountRequestGate.isCurrent(requestToken)) return;
        setLastEvaluation(result);
      } else if (requestBotId !== null) {
        await botsApi.stop(requestBotId);
        if (!accountRequestGate.isCurrent(requestToken)) return;
        setClassificationOverrides((current) => ({
          ...current,
          [requestAccountId]: { observedAt: null, simulated: null },
        }));
      }
      await Promise.all([loadConfigs(), loadActivity(requestBotId), loadRuntimeStatus()]);
      if (!accountRequestGate.isCurrent(requestToken)) return;
      setChartRefreshToken((current) => current + 1);
    } catch (err) {
      if (accountRequestGate.isCurrent(requestToken)) {
        // A start can persist a run before provider I/O fails. Refresh so Stop
        // remains reachable even when no successful evaluation was returned.
        await Promise.allSettled([loadConfigs(), loadRuntimeStatus()]);
        if (accountRequestGate.isCurrent(requestToken)) {
          setError(err instanceof Error ? err.message : "Bot action failed");
        }
      }
    } finally {
      if (accountRequestGate.isCurrent(requestToken)) setActionLoading(null);
    }
  }

  async function verifyAutomationClassification() {
    if (
      demoModeEnabled ||
      activeProjectXAccountId === null ||
      classificationVerificationAccountId !== null
    ) {
      return;
    }
    const requestAccountId = activeProjectXAccountId;
    setClassificationVerificationAccountId(requestAccountId);
    try {
      const result = await accountsApi.refreshAutomationClassification(requestAccountId);
      if (result.account_id !== requestAccountId) {
        throw new Error("ProjectX classification response did not match the requested account.");
      }
      setClassificationOverrides((current) => ({
        ...current,
        [requestAccountId]: {
          observedAt: result.provider_classification_observed_at,
          simulated: result.provider_simulated,
        },
      }));
      setClassificationVerification({
        accountId: requestAccountId,
        completedAt: new Date().toISOString(),
        message: result.provider_simulated
          ? "ProjectX verified this as a simulated Practice account. Live arming still depends on every other safety check."
          : "ProjectX classified this as live or funded, so automated order routing remains blocked.",
        state: result.provider_simulated ? "verified" : "blocked",
      });
      await loadRuntimeStatus();
    } catch (err) {
      setClassificationOverrides((current) => ({
        ...current,
        [requestAccountId]: { observedAt: null, simulated: null },
      }));
      setClassificationVerification({
        accountId: requestAccountId,
        completedAt: new Date().toISOString(),
        message: err instanceof Error ? err.message : "Practice-account verification failed.",
        state: "failed",
      });
    } finally {
      setClassificationVerificationAccountId((current) => (current === requestAccountId ? null : current));
    }
  }

  async function runEmergencyFlatten() {
    if (demoModeEnabled || activeProjectXAccountId === null || emergencyFlattenAccountId !== null) {
      return;
    }
    const requestAccountId = activeProjectXAccountId;
    const confirmationPhrase = `FLATTEN ${requestAccountId}`;
    const typedConfirmation = window.prompt(
      `EMERGENCY ACCOUNT-WIDE ACTION\n\nThis disables automation, cancels every working order, and closes every open position on ProjectX account ${requestAccountId} — including trades not opened by a bot.\n\nType ${confirmationPhrase} to continue.`,
    );
    if (typedConfirmation === null) {
      return;
    }
    if (typedConfirmation.trim() !== confirmationPhrase) {
      setError(`Emergency flatten cancelled: enter exactly ${confirmationPhrase}.`);
      return;
    }

    const requestBotId = selectedBot?.account_id === requestAccountId ? selectedBot.id : null;
    setEmergencyFlattenAccountId(requestAccountId);
    setError(null);
    try {
      const result = await botsApi.emergencyFlattenAccount(requestAccountId, true);
      if (result.account_id !== requestAccountId) {
        throw new Error("Emergency-flatten response did not match the requested account.");
      }
      const completedAt = new Date().toISOString();
      setEmergencyOutcome({
        accountId: requestAccountId,
        auditReference: emergencyAuditReference(result.audit_id, result.audit),
        completedAt,
        message: result.confirmed_flat
          ? "ProjectX confirmed that no working orders or open positions remain on the account."
          : result.risk_block?.message ??
            "The provider did not confirm that the entire account is flat. Check ProjectX immediately.",
        state: result.confirmed_flat ? "confirmed" : "unconfirmed",
        status: result.status,
      });
      setClassificationOverrides((current) => ({
        ...current,
        [requestAccountId]: { observedAt: null, simulated: null },
      }));
      if (activeProjectXAccountIdRef.current === requestAccountId) {
        const refreshes: Array<Promise<unknown>> = [loadConfigs(), loadRuntimeStatus()];
        if (requestBotId !== null) {
          refreshes.push(loadActivity(requestBotId));
        }
        await Promise.allSettled(refreshes);
        setChartRefreshToken((current) => current + 1);
      }
    } catch (err) {
      setClassificationOverrides((current) => ({
        ...current,
        [requestAccountId]: { observedAt: null, simulated: null },
      }));
      const detail = err instanceof Error ? err.message : "No verified response was received.";
      setEmergencyOutcome({
        accountId: requestAccountId,
        auditReference: null,
        completedAt: new Date().toISOString(),
        message:
          `The request ended without a verifiable provider outcome (${detail}). The account may or may not be flat. ` +
          "Check ProjectX working orders and positions directly; do not retry blindly.",
        state: "unknown",
        status: "transport_outcome_unknown",
      });
    } finally {
      setEmergencyFlattenAccountId((current) => (current === requestAccountId ? null : current));
    }
  }

  function handleSelectExpressAccount(accountId: number) {
    if (!projectXAccounts.some((account) => account.id === accountId)) {
      return;
    }
    writeStoredAccountId(accountId);
    const next = new URLSearchParams(searchParams);
    next.set(ACCOUNT_QUERY_PARAM, String(accountId));
    setSearchParams(next, { replace: true });
  }

  if (accountsLoading) {
    return (
      <div className="grid gap-5 lg:grid-cols-[1fr_1.4fr]" role="status" aria-live="polite" aria-busy="true">
        <h1 className="sr-only">Trading Bot</h1>
        <p className="sr-only">Loading bot workspace accounts.</p>
        <Skeleton className="h-[520px]" />
        <Skeleton className="h-[520px]" />
      </div>
    );
  }

  if (activeProjectXAccountId === null) {
    return (
      <BotProviderWorkspaceBoundary
        activeAccount={activeAccount}
        fallback={
          <BotExpressAccountRequired
            activeAccount={activeAccount}
            expressAccounts={projectXAccounts}
            onSelectAccount={handleSelectExpressAccount}
          />
        }
      >
        {null}
      </BotProviderWorkspaceBoundary>
    );
  }

  if (loading) {
    return (
      <div className="grid gap-5 lg:grid-cols-[1fr_1.4fr]" role="status" aria-live="polite" aria-busy="true">
        <h1 className="sr-only">Trading Bot</h1>
        <p className="sr-only">Loading TopBot and activity.</p>
        <Skeleton className="h-[520px]" />
        <Skeleton className="h-[520px]" />
      </div>
    );
  }

  return (
    <BotProviderWorkspaceBoundary
      activeAccount={activeAccount}
      fallback={
        <BotExpressAccountRequired
          activeAccount={activeAccount}
          expressAccounts={projectXAccounts}
          onSelectAccount={handleSelectExpressAccount}
        />
      }
    >
    <div className="space-y-5 pb-8">
      <h1 className="sr-only">Trading Bot</h1>
      <DemoModeNotice>
        <p>
          Signals, activity, and charts are a fixed read-only snapshot. Run controls and live market streams are disabled.
        </p>
      </DemoModeNotice>
      {error ? <div className="rounded-xl border border-rose-400/35 bg-rose-500/10 px-4 py-3 text-sm text-rose-200" role="alert">{error}</div> : null}
      {configWarnings.map((warning) => (
        <div key={warning} className="rounded-xl border border-amber-400/35 bg-amber-500/10 px-4 py-3 text-sm text-amber-100" role="status">
          {warning}
        </div>
      ))}

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <CardTitle>TopBot</CardTitle>
              <CardDescription>MNQ · TopBot Adaptive · {activeAccount?.name} ({activeProjectXAccountId})</CardDescription>
              <p className="mt-2 text-sm text-app-muted">5-minute EMA/VWAP pullback · Long bias: shorts need a falling 50 EMA · 1 contract · Hold for 50-point stop / 50-point target</p>
            </div>
            <Badge variant={selectedBot?.enabled ? "positive" : "neutral"}>
              {selectedBot?.enabled ? (selectedBot.execution_mode === "live" ? "Live Run active" : "Dry Run active") : "Stopped"}
            </Badge>
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap gap-3">
            <Button
              onClick={() => void runBotAction("dry_run")}
              disabled={demoModeEnabled || actionLoading !== null || emergencyFlattenAccountId !== null || Boolean(selectedBot?.enabled) || runtimeContinuousBlockReason !== null}
              title={demoModeEnabled ? demoDisabledTitle : runtimeContinuousBlockReason ?? undefined}
            >
              {actionLoading === "dry_run" ? "Starting Dry Run…" : "Dry Run"}
            </Button>
            <Button
              variant="secondary"
              onClick={() => void runBotAction("live")}
              disabled={demoModeEnabled || actionLoading !== null || emergencyFlattenAccountId !== null || Boolean(selectedBot?.enabled) || liveRunBlockReason !== null}
              title={demoModeEnabled ? demoDisabledTitle : liveRunBlockReason ?? undefined}
            >
              {actionLoading === "live" ? "Starting Live Run…" : "Live Run"}
            </Button>
            <Button
              variant="danger"
              onClick={() => void runBotAction("stop")}
              disabled={demoModeEnabled || !selectedBot || actionLoading !== null || emergencyFlattenAccountId !== null}
              title={demoModeEnabled ? demoDisabledTitle : undefined}
            >
              {actionLoading === "stop" ? "Stopping…" : "Stop Automation"}
            </Button>
          </div>
          <p className="text-sm text-slate-400">Dry Run follows the market without placing orders. Live Run enables MNQ order routing.</p>
          <p className="text-xs text-slate-400">Stop Automation does not cancel broker orders or close positions.</p>
          {selectedBot?.enabled ? <p className="text-xs text-slate-400">Stop automation before starting another run.</p> : null}
          {runtimeContinuousBlockReason ? (
            <p className="text-xs text-amber-200" role="status">Runs unavailable: {runtimeContinuousBlockReason}</p>
          ) : liveRunBlockReason ? (
            <p className="text-xs text-amber-200" role="status">Live Run unavailable: {liveRunBlockReason}</p>
          ) : null}
        </CardContent>
      </Card>
      <div className="flex flex-col gap-5">
        <div className="contents">
          <Card className="order-2 min-w-0">
            <CardHeader className="space-y-3">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <CardTitle>Run activity</CardTitle>
                  <CardDescription>ProjectX rule execution</CardDescription>
                </div>
                {selectedBot ? (
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant={selectedBot.enabled ? "positive" : "neutral"}>
                      {selectedBot.enabled ? "Enabled" : "Disabled"}
                    </Badge>

                    <Badge variant="accent">{selectedBot.execution_mode === "dry_run" ? "Dry run" : "Live"}</Badge>
                  </div>
                ) : null}
              </div>
              <div
                className="flex flex-wrap items-center gap-2 rounded-xl border border-slate-800 bg-slate-950/45 px-3 py-2 text-xs text-slate-300"
                role="status"
                aria-label="Automation runtime status"
              >
                <Badge variant={runtimeCanArmContinuous ? "positive" : runtimeStatusError ? "negative" : "warning"}>
                  {runtimeCanArmContinuous
                    ? "Worker admission checks passed"
                    : runtimeStatusError
                      ? "Runtime unavailable"
                      : "Continuous arming blocked"}
                </Badge>
                {runtimeStatus ? (
                  <span>Worker: {runtimeStatus.state}; provider: {runtimeStatus.provider_status}</span>
                ) : null}
                {runtimeContinuousBlockReason ? (
                  <span className="text-amber-200">{runtimeContinuousBlockReason}</span>
                ) : null}
                {runtimeStatus?.counts.unresolved_live_submissions ? (
                  <span className="text-rose-200">
                    {runtimeStatus.counts.unresolved_live_submissions} unresolved live submission(s)
                  </span>
                ) : null}
                <Badge
                  variant={
                    activeProviderSimulated === true && activeAccountClassificationFresh
                      ? "positive"
                      : activeProviderSimulated === false
                        ? "negative"
                        : "warning"
                  }
                >
                  {activeProviderSimulated === true && activeAccountClassificationFresh
                    ? "Practice account verified"
                    : activeProviderSimulated === false
                      ? "Live/funded routing blocked"
                      : activeProviderSimulated === true
                        ? "Practice classification stale"
                        : "Account classification pending"}
                </Badge>
                {activeProviderClassificationObservedAt ? (
                  <span>
                    Classification observed {formatDateTime(activeProviderClassificationObservedAt)}
                  </span>
                ) : null}
                {activeProviderSimulated !== false && !activeAccountClassificationFresh ? (
                  <Button
                    variant="secondary"
                    onClick={() => void verifyAutomationClassification()}
                    disabled={demoModeEnabled || classificationVerificationAccountId !== null}
                    title={demoModeEnabled ? demoDisabledTitle : "Open a bounded ProjectX user-hub probe for this account"}
                  >
                    {classificationVerificationAccountId !== null
                      ? `Verifying account ${classificationVerificationAccountId}`
                      : "Verify Practice account"}
                  </Button>
                ) : null}
                {runtimeStatusError ? <span>{runtimeStatusError}</span> : null}
              </div>
            </CardHeader>
            <CardContent>
              <div className="space-y-5">
                {classificationVerification ? (
                  <div
                    className={
                      classificationVerification.state === "verified"
                        ? "rounded-xl border border-emerald-400/35 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-100"
                        : "rounded-xl border border-amber-400/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-100"
                    }
                    role={classificationVerification.state === "failed" ? "alert" : "status"}
                  >
                    <p className="font-semibold">
                      Account {classificationVerification.accountId} classification {classificationVerification.state}
                    </p>
                    <p className="mt-1">{classificationVerification.message}</p>
                    <p className="mt-1 text-xs opacity-80">
                      Checked {formatDateTime(classificationVerification.completedAt)}
                    </p>
                  </div>
                ) : null}
                <div className="rounded-xl border border-rose-400/35 bg-rose-500/10 p-3" aria-label="ProjectX emergency controls">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-semibold text-rose-100">ProjectX account {activeProjectXAccountId} emergency control</p>
                      <p className="mt-1 text-xs text-rose-100/85">
                        Account-wide: disables its bots, cancels every working order, and closes every open position, including trades outside this bot workspace.
                      </p>
                    </div>
                    <Button
                      variant="danger"
                      onClick={() => void runEmergencyFlatten()}
                      disabled={demoModeEnabled || emergencyFlattenAccountId !== null}
                      title={demoModeEnabled ? demoDisabledTitle : "Affects every open order and position on the selected ProjectX account"}
                    >
                      {emergencyFlattenAccountId !== null
                        ? `Flatten pending for account ${emergencyFlattenAccountId}`
                        : `Emergency: Flatten Account ${activeProjectXAccountId}`}
                    </Button>
                  </div>
                </div>
                {emergencyOutcome ? (
                  <div
                    className={
                      emergencyOutcome.state === "confirmed"
                        ? "rounded-xl border border-emerald-400/35 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-100"
                        : "rounded-xl border border-rose-400/45 bg-rose-500/15 px-4 py-3 text-sm text-rose-100"
                    }
                    role={emergencyOutcome.state === "confirmed" ? "status" : "alert"}
                    aria-live={emergencyOutcome.state === "confirmed" ? "polite" : "assertive"}
                  >
                    <p className="font-semibold">
                      {emergencyOutcome.state === "confirmed"
                        ? `Account ${emergencyOutcome.accountId} confirmed flat`
                        : emergencyOutcome.state === "unconfirmed"
                          ? `Account ${emergencyOutcome.accountId} flatten unconfirmed`
                          : `Account ${emergencyOutcome.accountId} flatten outcome unknown`}
                    </p>
                    <p className="mt-1">{emergencyOutcome.message}</p>
                    <p className="mt-1 text-xs opacity-80">
                      Recorded {formatDateTime(emergencyOutcome.completedAt)} · Status: {emergencyOutcome.status}
                      {emergencyOutcome.auditReference ? ` · ${emergencyOutcome.auditReference}` : ""}
                    </p>
                  </div>
                ) : null}
                {selectedBot ? (
                  <div className="space-y-4">
                    {selectedBotEvaluation ? (
                      <div className="grid gap-3">
                        <div className="rounded-xl border border-slate-800 bg-slate-950/45 p-3">
                          <div className="mb-2 flex items-center justify-between gap-2">
                            <div className="flex flex-wrap items-center gap-2">
                              <Badge variant={actionBadgeVariant(selectedBotEvaluation.decision.action)}>
                                {selectedBotEvaluation.decision.action}
                              </Badge>
                              <Badge variant={statusBadgeVariant(selectedBotEvaluation.status)}>
                                {evaluationStatusLabel(selectedBotEvaluation.status)}
                              </Badge>
                            </div>
                            <span className="text-xs text-slate-500">{formatDateTime(selectedBotEvaluation.decision.candle_timestamp)}</span>
                          </div>
                          <p className="text-sm text-slate-200">{selectedBotEvaluation.decision.reason}</p>
                          {selectedBotEvaluation.correlation_id ? (
                            <p className="mt-2 text-xs text-slate-500" title={selectedBotEvaluation.correlation_id}>
                              Correlation: {selectedBotEvaluation.correlation_id.slice(0, 16)}
                              {selectedBotEvaluation.correlation_id.length > 16 ? "…" : ""}
                            </p>
                          ) : null}
                          {selectedBotEvaluation.status === "duplicate_skipped" && selectedBotEvaluation.duplicate_of_order_attempt_id ? (
                            <p className="mt-2 text-xs text-amber-200">
                              Duplicate skipped; original order attempt #{selectedBotEvaluation.duplicate_of_order_attempt_id}.
                            </p>
                          ) : null}
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
                  <p className="text-sm text-slate-400">Ready for your first run.</p>
                )}

                <div className="border-t border-slate-800 pt-5">
                  <div className="mb-4 space-y-1">
                    <h4 className="text-sm font-semibold text-slate-100 md:text-base">Activity</h4>
                    <p className="text-xs text-slate-400">Signals, risk events, and order attempts</p>
                  </div>
                  {activityLoading ? (
                    <div role="status" aria-live="polite" aria-label="Loading bot activity"><Skeleton className="h-64" aria-hidden="true" /></div>
                  ) : selectedBotActivity ? (
                    <div className="grid gap-4 xl:grid-cols-2">
                      <ActivityTable
                        title="Decisions"
                        rows={selectedBotActivity.decisions.slice(0, 8).map((decision) => ({
                          id: decision.id,
                          left: decision.action,
                          middle: decision.reason,
                          right: formatDateTime(decision.created_at),
                          badgeVariant: actionBadgeVariant(decision.action),
                        }))}
                      />
                      <ActivityTable
                        title="Orders"
                        rows={selectedBotActivity.order_attempts.slice(0, 8).map((attempt) => ({
                          id: attempt.id,
                          left: attempt.status,
                          middle: `${attempt.side} ${attempt.size} ${attempt.contract_id}`,
                          right: formatDateTime(attempt.created_at),
                          badgeVariant: statusBadgeVariant(attempt.status),
                        }))}
                      />
                      <ActivityTable
                        title="Risk"
                        rows={selectedBotActivity.risk_events.slice(0, 8).map((risk) => ({
                          id: risk.id,
                          left: risk.severity,
                          middle: `${risk.code}: ${risk.message}`,
                          right: formatDateTime(risk.created_at),
                          badgeVariant: risk.severity === "critical" ? "negative" : "warning",
                        }))}
                      />
                      <ActivityTable
                        title="Runs"
                        rows={selectedBotActivity.runs.slice(0, 8).map((run) => ({
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

          <div className="order-1 min-w-0 space-y-5">
            <BotSignalChart
              bot={selectedBot}
              authenticatedCacheScope={authenticatedCacheScope}
              activity={selectedBotActivity}
              lastEvaluation={selectedBotEvaluation}
              refreshToken={chartRefreshToken}
              demoMode={demoModeEnabled}
              onMarketData={setMarketSnapshot}
            />
            <OrderBookPanel
              key={selectedBot?.contract_id ?? "no-contract"}
              contractId={selectedBot?.contract_id}
              symbol={selectedBot?.symbol}
              demoMode={demoModeEnabled}
            />
            <Suspense fallback={<Skeleton className="h-[360px]" />}>
              <BotAnalysisPanel
                bot={selectedBot}
                evaluation={selectedBotEvaluation}
                marketSnapshot={marketSnapshot}
                loading={actionLoading === "dry_run" || actionLoading === "live" || actionLoading === "evaluate"}
                onEvaluate={selectedBot && !demoModeEnabled ? () => void runBotAction("evaluate") : undefined}
              />
            </Suspense>
          </div>
        </div>
      </div>
      <Suspense fallback={<Skeleton className="h-[420px]" />}>
        <BotBacktestPanel key={selectedBot?.id ?? "no-bot"} bot={selectedBot} demoMode={demoModeEnabled} />
      </Suspense>
    </div>
    </BotProviderWorkspaceBoundary>
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
