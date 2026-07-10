import type { Logical, UTCTimestamp } from "lightweight-charts";

export const BOT_DRAWING_STORAGE_VERSION = 2;
export const BOT_DRAWING_STORAGE_KEY_PREFIX = `topsignal:bot-chart-drawings:v${BOT_DRAWING_STORAGE_VERSION}:`;
const LEGACY_BOT_DRAWING_STORAGE_KEY_PREFIX = "topsignal:bot-chart-drawings:v1:";
export const MAX_PERSISTED_BOT_DRAWINGS = 250;

export type BotDrawingKind = "line" | "rectangle";

export interface BotDrawingPoint {
  logical: Logical;
  time: UTCTimestamp | null;
  price: number;
}

export interface BotDrawingShape {
  id: string;
  kind: BotDrawingKind;
  start: BotDrawingPoint;
  end: BotDrawingPoint;
}

export interface BotDrawingStorageScope {
  /** Stable non-secret authenticated-user namespace. */
  userScope: string;
  botId: number;
  contractId: string;
  /** Stable chart timeframe identifier, for example "5m" or "1h". */
  timeframe: string;
}

export interface BotDrawingStorageAdapter {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

interface BotDrawingStoragePayload {
  version: typeof BOT_DRAWING_STORAGE_VERSION;
  drawings: BotDrawingShape[];
}

export function buildBotDrawingStorageKey(scope: BotDrawingStorageScope): string {
  const userScope = scope.userScope.trim();
  const botId = Number.isFinite(scope.botId) ? String(Math.trunc(scope.botId)) : "invalid";
  const contractId = scope.contractId.trim().toUpperCase();
  const timeframe = scope.timeframe.trim().toLowerCase();
  return `${BOT_DRAWING_STORAGE_KEY_PREFIX}${encodeURIComponent(`${userScope}|${botId}|${contractId}|${timeframe}`)}`;
}

export function readBotDrawings(
  scope: BotDrawingStorageScope,
  storage: BotDrawingStorageAdapter | null = getBrowserStorage(),
): BotDrawingShape[] {
  if (!storage) {
    return [];
  }
  try {
    removeLegacyBotDrawings(scope, storage);
    return parseBotDrawings(storage.getItem(buildBotDrawingStorageKey(scope)));
  } catch {
    return [];
  }
}

export function writeBotDrawings(
  scope: BotDrawingStorageScope,
  drawings: BotDrawingShape[],
  storage: BotDrawingStorageAdapter | null = getBrowserStorage(),
): boolean {
  if (!storage) {
    return false;
  }

  removeLegacyBotDrawings(scope, storage);

  const sanitizedDrawings = sanitizeDrawings(drawings);
  if (sanitizedDrawings.length === 0) {
    return clearBotDrawings(scope, storage);
  }

  const payload: BotDrawingStoragePayload = {
    version: BOT_DRAWING_STORAGE_VERSION,
    drawings: sanitizedDrawings,
  };
  try {
    storage.setItem(buildBotDrawingStorageKey(scope), JSON.stringify(payload));
    return true;
  } catch {
    return false;
  }
}

export function clearBotDrawings(
  scope: BotDrawingStorageScope,
  storage: BotDrawingStorageAdapter | null = getBrowserStorage(),
): boolean {
  if (!storage) {
    return false;
  }
  try {
    removeLegacyBotDrawings(scope, storage);
    storage.removeItem(buildBotDrawingStorageKey(scope));
    return true;
  } catch {
    return false;
  }
}

export function parseBotDrawings(raw: string | null): BotDrawingShape[] {
  if (!raw) {
    return [];
  }
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!isRecord(parsed) || parsed.version !== BOT_DRAWING_STORAGE_VERSION || !Array.isArray(parsed.drawings)) {
      return [];
    }
    return sanitizeDrawings(parsed.drawings);
  } catch {
    return [];
  }
}

function sanitizeDrawings(values: unknown[]): BotDrawingShape[] {
  const drawings: BotDrawingShape[] = [];
  const seenIds = new Set<string>();
  for (const value of values.slice(0, MAX_PERSISTED_BOT_DRAWINGS)) {
    const drawing = parseDrawing(value);
    if (!drawing || seenIds.has(drawing.id)) {
      continue;
    }
    seenIds.add(drawing.id);
    drawings.push(drawing);
  }
  return drawings;
}

function parseDrawing(value: unknown): BotDrawingShape | null {
  if (!isRecord(value)) {
    return null;
  }
  const id = typeof value.id === "string" ? value.id.trim() : "";
  if (id === "" || id.length > 256 || (value.kind !== "line" && value.kind !== "rectangle")) {
    return null;
  }
  const start = parseDrawingPoint(value.start);
  const end = parseDrawingPoint(value.end);
  if (!start || !end) {
    return null;
  }
  return { id, kind: value.kind, start, end };
}

function parseDrawingPoint(value: unknown): BotDrawingPoint | null {
  if (!isRecord(value) || !isFiniteNumber(value.logical) || !isFiniteNumber(value.price)) {
    return null;
  }
  const time = value.time;
  if (time !== null && (!isFiniteNumber(time) || !Number.isInteger(time))) {
    return null;
  }
  return { logical: value.logical as Logical, time: time as UTCTimestamp | null, price: value.price };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function getBrowserStorage(): BotDrawingStorageAdapter | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

function removeLegacyBotDrawings(scope: BotDrawingStorageScope, storage: BotDrawingStorageAdapter): void {
  try {
    storage.removeItem(buildLegacyBotDrawingStorageKey(scope));
  } catch {
    // Access failures are intentionally contained by the public operations.
  }
}

function buildLegacyBotDrawingStorageKey(scope: BotDrawingStorageScope): string {
  const botId = Number.isFinite(scope.botId) ? String(Math.trunc(scope.botId)) : "invalid";
  const contractId = scope.contractId.trim().toUpperCase();
  const timeframe = scope.timeframe.trim().toLowerCase();
  return `${LEGACY_BOT_DRAWING_STORAGE_KEY_PREFIX}${encodeURIComponent(`${botId}|${contractId}|${timeframe}`)}`;
}
