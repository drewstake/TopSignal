import { topstepPost } from "./topstepClient";

export type TopstepContract = {
  id: string;
  name: string;
  description: string;
  tickSize: number;
  tickValue: number;
  activeContract: boolean;
  symbolId: string;
};

export type ContractByIdResponse = {
  contract: TopstepContract | null;
  success: boolean;
  errorCode: number;
  errorMessage: string | null;
};

export async function searchContractById(contractId: string): Promise<ContractByIdResponse> {
  return topstepPost("/api/Contract/searchById", { contractId });
}

type ContractSearchResult = {
  id?: string | null;
  contractId?: string | null;
  activeContract?: boolean | string | null | { id?: string | null; contractId?: string | null };
  symbolId?: string | null;
};

function normalizeContractResults(payload: unknown): ContractSearchResult[] {
  if (Array.isArray(payload)) return payload as ContractSearchResult[];
  if (payload && typeof payload === "object") {
    const obj = payload as Record<string, unknown>;
    if (Array.isArray(obj.contracts)) return obj.contracts as ContractSearchResult[];
    if (Array.isArray(obj.results)) return obj.results as ContractSearchResult[];
    if (Array.isArray(obj.data)) return obj.data as ContractSearchResult[];
  }

  return [] as ContractSearchResult[];
}

function toSymbolId(symbol: string) {
  const upperSymbol = symbol.toUpperCase();
  if (upperSymbol === "NQ") return "F.US.ENQ";
  if (upperSymbol === "ES") return "F.US.EP";
  if (upperSymbol === "MNQ") return "F.US.MNQ";
  if (upperSymbol === "MES") return "F.US.MES";
  return `F.US.${upperSymbol}`;
}

function getContractId(result: ContractSearchResult | null | undefined) {
  if (!result) return null;

  if (typeof result.activeContract === "string") return result.activeContract;
  if (result.activeContract && typeof result.activeContract === "object") {
    return result.activeContract.contractId ?? result.activeContract.id ?? null;
  }

  return result.contractId ?? result.id ?? null;
}

function hasActiveContract(result: ContractSearchResult) {
  if (result.activeContract === true) return true;
  if (typeof result.activeContract === "string") return true;
  if (result.activeContract && typeof result.activeContract === "object") {
    return Boolean(result.activeContract.contractId ?? result.activeContract.id);
  }

  return false;
}

function pickContract(results: ContractSearchResult[], symbol: string) {
  const upperSymbol = symbol.toUpperCase();
  const symbolId = toSymbolId(symbol).toUpperCase();
  const prefixes = [`CON.${symbolId}`, symbolId, upperSymbol];

  const matchId = (result: ContractSearchResult | undefined | null) => {
    if (!result) return null;
    return getContractId(result);
  };

  const bySymbol = results.find((result) => {
    const id = matchId(result)?.toUpperCase();
    const matchesSymbolId = result.symbolId?.toUpperCase() === symbolId;
    return matchesSymbolId || (!!id && prefixes.some((prefix) => id.startsWith(prefix)));
  });

  const activeSymbol = results.find(
    (result) => hasActiveContract(result) && matchId(result)?.toUpperCase().startsWith(`CON.${symbolId}`)
  );

  return matchId(activeSymbol ?? bySymbol ?? results.find((r) => matchId(r)) ?? null);
}

async function fetchAvailableContractId(symbolId: string, live: boolean) {
  const contracts = normalizeContractResults(await topstepPost("/api/Contract/available", { live }));
  const match = contracts.find(
    (c) =>
      (c.symbolId?.toUpperCase?.() === symbolId.toUpperCase() ||
        getContractId(c)?.toUpperCase().startsWith(symbolId.toUpperCase())) &&
      hasActiveContract(c)
  );

  return getContractId(match ?? ({} as ContractSearchResult));
}

async function searchContracts(symbol: string) {
  const attempts = [
    { live: false, label: "paper" },
    { live: true, label: "live" },
  ];

  for (const attempt of attempts) {
    const results = normalizeContractResults(
      await topstepPost("/api/Contract/search", { live: attempt.live, searchText: symbol })
    );

    if (results.length > 0) return results as ContractSearchResult[];
  }

  return [] as ContractSearchResult[];
}

export async function resolveContractId(symbol: string) {
  const symbolId = toSymbolId(symbol);
  const attempts = [
    { live: false, label: "paper" },
    { live: true, label: "live" },
  ];

  for (const attempt of attempts) {
    const available = await fetchAvailableContractId(symbolId, attempt.live);
    if (available) return available;
  }

  const results = await searchContracts(symbol);
  if (results.length === 0) {
    throw new Error(`No contracts returned for ${symbol}.`);
  }

  const match = pickContract(results, symbol);
  if (match) return match;

  const fallback = results.find((c) => c.id || c.contractId);
  if (fallback?.id || fallback?.contractId) {
    return fallback.id ?? fallback.contractId!;
  }

  throw new Error(`${symbol} contract not found in search results.`);
}
