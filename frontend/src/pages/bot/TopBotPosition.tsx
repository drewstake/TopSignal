import { useEffect, useState } from "react";
import { botsApi } from "../../lib/api";
import type { ManualOrderState } from "../../lib/manualOrderTypes";
import { boundedBotRequest } from "./botPageRequests";
import { priceLabel, timeLabel } from "./topBotPresentation";
import { usePageVisibility } from "./usePageVisibility";

export function TopBotPosition({ accountId, contractId, demoMode }: { accountId: number | null; contractId?: string; demoMode: boolean }) {
  const visible = usePageVisibility();
  const [data, setData] = useState<{ value: ManualOrderState; at: string } | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!accountId || !contractId || demoMode || !visible) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    let controller: AbortController;
    const refresh = async () => {
      controller = new AbortController();
      try {
        const value = await boundedBotRequest(botsApi.getManualOrderState(accountId, { signal: controller.signal }), controller, "Position read timed out", 15_000);
        if (value.account_id !== accountId) throw new Error("Account mismatch");
        if (!stopped) { setData({ value, at: new Date().toISOString() }); setFailed(false); }
      } catch { if (!stopped) setFailed(true); }
      finally { if (!stopped) timer = setTimeout(refresh, 30_000); }
    };
    void refresh();
    return () => { stopped = true; clearTimeout(timer); controller?.abort(); };
  }, [accountId, contractId, demoMode, visible]);
  const positions = data?.value.positions.filter(position => position.contract_id === contractId);
  return <div aria-label="Account position">
    <dt>Position{contractId && ` · ${contractId.split(".").slice(-2).join(" ")}`}</dt>
    <dd title={data ? `Account position checked ${timeLabel(data.at)}` : undefined}>
      {demoMode || !accountId || !contractId ? "Unavailable" : failed ? "Status unknown" : !positions ? "Checking…" : !positions.length ? "Flat" :
        positions.map(position => `${position.type === 1 ? "Long" : position.type === 2 ? "Short" : "Position"} ${position.size} @ ${priceLabel(position.average_price)}`).join(" · ")}
    </dd>
  </div>;
}
