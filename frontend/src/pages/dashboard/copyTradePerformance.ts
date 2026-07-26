export function computeCopyTradeWhenEnabled<T>(
  enabled: boolean,
  disabledValue: T,
  compute: () => T,
): T {
  return enabled ? compute() : disabledValue;
}
