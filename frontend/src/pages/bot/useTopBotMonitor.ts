import { useEffect, useState } from "react";
import { botsApi } from "../../lib/api";
import type { BotActivity, BotConfig } from "../../lib/types";
import { loadBotConfigsForProviderAccount } from "./botAccountIsolation";
import { boundedBotRequest } from "./botPageRequests";
import { botRunStatus } from "./botRunPresentation";
import { usePageVisibility } from "./usePageVisibility";

export type MonitorStatus = { color: "green" | "yellow" | "red"; label: string };
const checking: MonitorStatus = { color: "yellow", label: "Checking bot status" };
export type MonitorSnapshot = {
  bot: BotConfig | null; activity: BotActivity | null; scope: string | null;
  status: MonitorStatus; updatedAt: string | null; error: string | null;
};
const empty: MonitorSnapshot = { bot: null, activity: null, scope: null, status: checking, updatedAt: null, error: null };

/** Read-only monitoring. Never evaluates, starts, or places orders. */
export function useTopBotMonitor(accountId: number | null, pending: boolean, unavailable: boolean, demoMode: boolean, refreshToken = 0) {
  const [snapshot, setSnapshot] = useState<MonitorSnapshot>(empty);
  const visible = usePageVisibility();
  useEffect(() => {
    if (pending || unavailable || accountId === null || demoMode || !visible) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    let controller: AbortController;
    const refresh = async () => {
      const requestController = new AbortController();
      controller = requestController;
      try {
        const next = await boundedBotRequest((async (): Promise<MonitorSnapshot> => {
          const result = await loadBotConfigsForProviderAccount(accountId,
            id => botsApi.listConfigsWithCacheScope(id, { signal: requestController.signal }));
          requestController.signal.throwIfAborted();
          const configs = result?.configs.items.filter(bot => bot.strategy_type === "topbot_adaptive") ?? [];
          const bot = configs.find(bot => bot.enabled) ?? configs.sort((a, b) => b.id - a.id)[0];
          const base = { scope: result?.cacheScope ?? null, updatedAt: new Date().toISOString(), error: null };
          if (!bot) return { ...base, bot: null, activity: null, status: { color: "red", label: "Inactive · ready to configure practice run" } };
          const activity = await botsApi.getActivity(bot.id, 20, { signal: requestController.signal });
          requestController.signal.throwIfAborted();
          if (activity.config.id !== bot.id || activity.config.account_id !== accountId) throw new Error("Account mismatch");
          // Scope every row as well as the response config before displaying it.
          const scoped: BotActivity = { ...activity,
            runs: activity.runs.filter(row => row.account_id === accountId && row.bot_config_id === bot.id),
            decisions: (activity.decisions ?? []).filter(row => row.account_id === accountId && row.bot_config_id === bot.id),
            order_attempts: (activity.order_attempts ?? []).filter(row => row.account_id === accountId && row.bot_config_id === bot.id),
            risk_events: (activity.risk_events ?? []).filter(row => row.account_id === accountId && row.bot_config_id === bot.id),
          };
          const run = botRunStatus(activity.config, scoped);
          let status: MonitorStatus = { color: run.variant === "warning" ? "yellow" : "red", label: run.label };
          if (activity.config.strategy_params?.revision === "custom_unconfigured") {
            status = { color: "red", label: "Inactive · strategy not configured" };
          } else if (run.variant === "positive") {
            try {
              const runtime = await botsApi.getRuntimeStatus({ signal: requestController.signal });
              status = runtime.state === "running" && runtime.checks.worker_enabled
                && runtime.checks.worker_task_healthy && runtime.checks.lease_healthy
                ? { color: "green", label: run.label }
                : { color: "yellow", label: "Run recorded · worker needs attention" };
            } catch { status = { color: "yellow", label: "Worker status unavailable" }; }
          }
          return { ...base, bot: activity.config, activity: scoped, status };
        })(), requestController, "Bot status check timed out", 15_000);
        if (!disposed) setSnapshot(previous => ({ ...next,
          bot: JSON.stringify(previous.bot) === JSON.stringify(next.bot) ? previous.bot : next.bot,
          activity: JSON.stringify(previous.activity) === JSON.stringify(next.activity) ? previous.activity : next.activity,
        }));
      } catch {
        if (!disposed) setSnapshot(previous => ({ ...previous,
          status: { color: "yellow", label: "Bot status unavailable" }, error: "Connection interrupted. Showing the last recorded update.",
        }));
      } finally {
        if (!disposed) timer = setTimeout(refresh, 15_000);
      }
    };
    void refresh();
    return () => { disposed = true; clearTimeout(timer); controller?.abort(); };
  }, [accountId, demoMode, pending, unavailable, visible, refreshToken]);
  const status: MonitorStatus = demoMode ? { color: "yellow", label: "Demo mode · live bot status not checked" }
    : pending ? checking : unavailable ? { color: "yellow", label: "Account status unavailable" }
    : accountId === null ? { color: "red", label: "Inactive · no trading account selected" }
    : !visible ? checking : snapshot.status;
  return { ...snapshot, status };
}
