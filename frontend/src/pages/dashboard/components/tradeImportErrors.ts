import { isApiError } from "../../../lib/api";

function getDetailMessage(detail: unknown): string | null {
  if (typeof detail === "string" && detail.trim()) {
    if (detail === "trade_import_requires_csv_import_account") {
      return "Select a separate Live CSV account before importing trades. Express accounts cannot be converted.";
    }
    return detail;
  }

  if (Array.isArray(detail)) {
    const messages = detail.flatMap((item) => {
      if (typeof item === "string" && item.trim()) {
        return [item];
      }
      if (item && typeof item === "object") {
        const record = item as Record<string, unknown>;
        if (typeof record.msg === "string" && record.msg.trim()) {
          return [record.msg];
        }
        if (typeof record.message === "string" && record.message.trim()) {
          return [record.message];
        }
      }
      return [];
    });
    return messages.length > 0 ? messages.join(" ") : null;
  }

  if (!detail || typeof detail !== "object") {
    return null;
  }

  const record = detail as Record<string, unknown>;
  if (record.code === "account_trade_data_source_conflict") {
    return "That account ID already belongs to an Express/ProjectX account. Enter the separate Topstep Live account ID.";
  }
  const missingColumns = record.missing_columns;
  if (Array.isArray(missingColumns)) {
    const columns = missingColumns.filter((column): column is string => typeof column === "string" && column.trim() !== "");
    if (columns.length > 0) {
      return `Missing required columns: ${columns.join(", ")}.`;
    }
  }

  for (const key of ["message", "error", "detail"]) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) {
      return value;
    }
  }

  if (typeof record.code === "string" && record.code.trim()) {
    return record.code.replaceAll("_", " ");
  }

  return null;
}

export function getTradeImportErrorMessage(error: unknown) {
  if (isApiError(error)) {
    return getDetailMessage(error.detail) ?? error.message;
  }
  return error instanceof Error && error.message ? error.message : "The trade file could not be processed.";
}
