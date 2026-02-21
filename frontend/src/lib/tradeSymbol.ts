const MONTH_CODE_WITH_YEAR_PATTERN = /^[FGHJKMNQUVXZ]\d{1,4}$/i;
const CONTRACT_SUFFIX_PATTERN = /^([A-Z0-9]+?)[FGHJKMNQUVXZ]\d{1,4}$/i;

function extractRootFromDottedContract(value: string) {
  const segments = value
    .split(".")
    .map((segment) => segment.trim())
    .filter(Boolean);

  if (segments.length < 2) {
    return null;
  }

  const contractMonthSegment = segments[segments.length - 1];
  if (!MONTH_CODE_WITH_YEAR_PATTERN.test(contractMonthSegment)) {
    return null;
  }

  return segments[segments.length - 2].toUpperCase();
}

function extractRootFromContractSuffix(value: string) {
  const compact = value.replace(/[\s._-]+/g, "").toUpperCase();
  const match = compact.match(CONTRACT_SUFFIX_PATTERN);
  return match?.[1] ?? null;
}

export function getDisplayTradeSymbol(symbol?: string | null, contractId?: string | null) {
  const normalizedSymbol = symbol?.trim() ?? "";
  const normalizedContractId = contractId?.trim() ?? "";
  const preferred = normalizedSymbol || normalizedContractId;

  if (!preferred) {
    return "";
  }

  const parsedRoot =
    extractRootFromDottedContract(preferred) ??
    extractRootFromDottedContract(normalizedContractId) ??
    extractRootFromContractSuffix(preferred) ??
    extractRootFromContractSuffix(normalizedContractId);

  if (parsedRoot) {
    return parsedRoot;
  }

  return preferred.toUpperCase();
}

export function buildTradeSymbolSearchText(symbol?: string | null, contractId?: string | null) {
  return [symbol ?? "", contractId ?? "", getDisplayTradeSymbol(symbol, contractId)]
    .map((value) => value.trim().toLowerCase())
    .filter(Boolean)
    .join(" ");
}
