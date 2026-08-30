const DECIMAL_NUMBER_PATTERN = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/;

export function parseStrictFiniteNumber(value: string): number | null {
  const normalized = value.trim();
  if (!normalized || !DECIMAL_NUMBER_PATTERN.test(normalized)) {
    return null;
  }

  const parsed = Number(normalized);
  return Number.isFinite(parsed) ? parsed : null;
}

export function parseStrictInteger(value: string): number | null {
  const parsed = parseStrictFiniteNumber(value);
  return parsed !== null && Number.isSafeInteger(parsed) ? parsed : null;
}
