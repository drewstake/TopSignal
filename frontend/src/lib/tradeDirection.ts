export type TradeDirection = "LONG" | "SHORT";

export function inferTradeDirection(side: string): TradeDirection | null {
  const normalized = side.trim().toUpperCase();
  if (normalized === "SELL" || normalized === "LONG") {
    return "LONG";
  }
  if (normalized === "BUY" || normalized === "SHORT") {
    return "SHORT";
  }
  return null;
}

export function formatTradeDirection(side: string): string {
  const inferred = inferTradeDirection(side);
  if (inferred !== null) {
    return inferred;
  }

  const normalized = side.trim().toUpperCase();
  return normalized === "" ? "UNKNOWN" : normalized;
}

export function tradeDirectionBadgeVariant(side: string): "accent" | "warning" | "neutral" {
  const direction = inferTradeDirection(side);
  if (direction === "LONG") {
    return "accent";
  }
  if (direction === "SHORT") {
    return "warning";
  }
  return "neutral";
}
