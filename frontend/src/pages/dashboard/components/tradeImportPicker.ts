export function openTradeImportFilePicker(
  input: Pick<HTMLInputElement, "value" | "click"> | null,
): boolean {
  if (!input) {
    return false;
  }
  // Clearing the native value lets selecting the same export fire onChange.
  input.value = "";
  input.click();
  return true;
}
