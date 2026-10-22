import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useDemoInteractionPolicy } from "../../components/demo/useDemoInteractionPolicy";
import { botsApi } from "../../lib/api";
import type { BotConfig } from "../../lib/types";
import { BotForwardResultsPanel } from "./BotForwardResultsPanel";

// Research stays available when a broker account leaves the active account picker.
// AccountAccess resets this page on identity changes; both API reads enforce ownership.
export function BotForwardResultsPage() {
  const { botId: rawBotId } = useParams();
  const parsed = rawBotId && /^[1-9]\d*$/.test(rawBotId) ? Number(rawBotId) : NaN;
  const botId = Number.isSafeInteger(parsed) ? parsed : null;
  const { demoModeEnabled } = useDemoInteractionPolicy();
  const [loaded, setLoaded] = useState<{ id: number; config: BotConfig; scope: string } | null>(null);
  const [error, setError] = useState(false);
  const config = loaded?.id === botId ? loaded.config : null;

  useEffect(() => {
    setLoaded(null); setError(false);
    if (!botId || demoModeEnabled) return;
    const controller = new AbortController();
    void botsApi.listConfigsWithCacheScope(undefined, { signal: controller.signal }).then(result => {
      if (controller.signal.aborted) return;
      const match = result.configs.items.find(item => item.id === botId);
      if (match) setLoaded({ id: botId, config: match, scope: result.cacheScope });
      else setError(true);
    }).catch(() => { if (!controller.signal.aborted) setError(true); });
    return () => controller.abort();
  }, [botId, demoModeEnabled]);

  return <main className="min-h-screen bg-app-bg p-5 text-app-text md:p-8">
    <div className="mx-auto max-w-6xl space-y-6">
      <header className="space-y-2">
        <Link to="/bot" className="text-sm underline">TopSignal workspace</Link>
        <h1 className="text-3xl font-semibold">{config ? `${config.name} results` : "Bot results"}</h1>
        <p className="text-sm text-app-muted">Forward research and simulated performance · refreshes every 30 seconds</p>
        {config && <p className="text-sm text-app-muted">{config.symbol ?? config.contract_id} · Account {config.account_id} · Bot {config.id}</p>}
      </header>
      {demoModeEnabled ? <p>Exit Demo Mode to view your saved dry-run results.</p> : !botId || error ?
        <p role="alert">This bot is unavailable or you do not have access to its results.</p> : !config ?
        <p>Loading your bot…</p> :
        <BotForwardResultsPanel key={`${loaded?.scope}:${config.id}`} botId={config.id} />}
    </div>
  </main>;
}
