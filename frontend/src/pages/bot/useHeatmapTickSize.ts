import { useEffect, useState } from "react";
import { botsApi } from "../../lib/api";

/** Exact-contract metadata only. No guessed tick size for a different symbol. */
export function useHeatmapTickSize(contractId: string | null, supplied?: number | null) {
  const [resolved, setResolved] = useState<{ contractId: string; tickSize: number | null; error: string | null } | null>(null);
  const [retry, setRetry] = useState(0);
  const validSupplied = supplied != null && Number.isFinite(supplied) && supplied > 0 ? supplied : null;
  useEffect(() => {
    if (!contractId || validSupplied !== null) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      controller.abort();
      setResolved({ contractId, tickSize: null, error: "Contract tick-size lookup timed out" });
    }, 15_000);
    // ProjectX contract IDs end in symbol.expiry. Match the exact ID in results.
    const searchText = contractId.split(".").at(-2) ?? contractId;
    void botsApi.searchContracts({ searchText, live: false }, { signal: controller.signal }).then(rows => {
      if (controller.signal.aborted) return;
      const tickSize = rows.find(row => row.id.toUpperCase() === contractId)?.tick_size;
      const valid = tickSize != null && Number.isFinite(tickSize) && tickSize > 0;
      setResolved({ contractId, tickSize: valid ? tickSize : null, error: valid ? null : "Tick size unavailable for selected contract" });
    }).catch(() => {
      if (!controller.signal.aborted) setResolved({ contractId, tickSize: null, error: "Could not load contract tick size" });
    }).finally(() => clearTimeout(timer));
    return () => { clearTimeout(timer); controller.abort(); };
  }, [contractId, validSupplied, retry]);
  return { tickSize: validSupplied ?? (resolved?.contractId === contractId ? resolved.tickSize : null),
    error: validSupplied === null && resolved?.contractId === contractId ? resolved.error : null,
    retry: () => setRetry(value => value + 1) };
}
