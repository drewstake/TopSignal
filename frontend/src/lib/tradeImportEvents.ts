export const TRADE_IMPORT_FILE_PICKER_REQUESTED_EVENT = "topsignal:trade-import-file-picker-requested";

export function requestTradeImportFilePicker() {
  if (typeof window === "undefined") {
    return;
  }
  window.dispatchEvent(new Event(TRADE_IMPORT_FILE_PICKER_REQUESTED_EVENT));
}
