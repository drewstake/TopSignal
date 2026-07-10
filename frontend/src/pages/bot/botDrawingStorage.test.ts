import { describe, expect, it } from "vitest";
import type { Logical, UTCTimestamp } from "lightweight-charts";

import {
  BOT_DRAWING_STORAGE_VERSION,
  buildBotDrawingStorageKey,
  clearBotDrawings,
  parseBotDrawings,
  readBotDrawings,
  writeBotDrawings,
  type BotDrawingShape,
  type BotDrawingStorageAdapter,
  type BotDrawingStorageScope,
} from "./botDrawingStorage";

const scope: BotDrawingStorageScope = {
  botId: 42,
  contractId: "CON.F.US.MNQ.U26",
  timeframe: "5m",
};

describe("buildBotDrawingStorageKey", () => {
  it("normalizes casing but keeps bot, contract, and timeframe scopes isolated", () => {
    expect(buildBotDrawingStorageKey(scope)).toBe(
      buildBotDrawingStorageKey({ botId: 42, contractId: " con.f.us.mnq.u26 ", timeframe: " 5M " }),
    );
    expect(buildBotDrawingStorageKey({ ...scope, botId: 43 })).not.toBe(buildBotDrawingStorageKey(scope));
    expect(buildBotDrawingStorageKey({ ...scope, contractId: "CON.F.US.ES.U26" })).not.toBe(
      buildBotDrawingStorageKey(scope),
    );
    expect(buildBotDrawingStorageKey({ ...scope, timeframe: "15m" })).not.toBe(buildBotDrawingStorageKey(scope));
  });
});

describe("drawing persistence", () => {
  it("round-trips a versioned drawing payload and clears it", () => {
    const storage = new MemoryStorage();
    const drawings = [drawing("one", "line"), drawing("two", "rectangle")];

    expect(writeBotDrawings(scope, drawings, storage)).toBe(true);
    expect(JSON.parse(storage.getItem(buildBotDrawingStorageKey(scope)) ?? "{}").version).toBe(
      BOT_DRAWING_STORAGE_VERSION,
    );
    expect(readBotDrawings(scope, storage)).toEqual(drawings);

    expect(clearBotDrawings(scope, storage)).toBe(true);
    expect(readBotDrawings(scope, storage)).toEqual([]);
  });

  it("returns an empty collection for malformed JSON, unsupported versions, and invalid envelopes", () => {
    expect(parseBotDrawings("not json")).toEqual([]);
    expect(parseBotDrawings(JSON.stringify({ version: 999, drawings: [drawing("one", "line")] }))).toEqual([]);
    expect(parseBotDrawings(JSON.stringify({ version: BOT_DRAWING_STORAGE_VERSION, drawings: "invalid" }))).toEqual([]);
    expect(parseBotDrawings(JSON.stringify([drawing("one", "line")]))).toEqual([]);
  });

  it("salvages valid drawings while rejecting malformed points, kinds, duplicate ids, and non-finite values", () => {
    const valid = drawing("valid", "line");
    const parsed = parseBotDrawings(
      JSON.stringify({
        version: BOT_DRAWING_STORAGE_VERSION,
        drawings: [
          valid,
          { ...drawing("bad-kind", "line"), kind: "circle" },
          { ...drawing("bad-price", "line"), start: { logical: 1, time: null, price: "100" } },
          { ...drawing("valid", "rectangle") },
          { ...drawing("bad-time", "line"), end: { logical: 2, time: "today", price: 101 } },
        ],
      }),
    );

    expect(parsed).toEqual([valid]);
  });

  it("contains storage access failures without losing in-memory chart behavior", () => {
    const throwingStorage: BotDrawingStorageAdapter = {
      getItem() {
        throw new Error("blocked");
      },
      setItem() {
        throw new Error("full");
      },
      removeItem() {
        throw new Error("blocked");
      },
    };

    expect(readBotDrawings(scope, throwingStorage)).toEqual([]);
    expect(writeBotDrawings(scope, [drawing("one", "line")], throwingStorage)).toBe(false);
    expect(clearBotDrawings(scope, throwingStorage)).toBe(false);
  });
});

function drawing(id: string, kind: BotDrawingShape["kind"]): BotDrawingShape {
  return {
    id,
    kind,
    start: { logical: 10.5 as Logical, time: 1_783_603_200 as UTCTimestamp, price: 22_500.25 },
    end: { logical: 15.25 as Logical, time: null, price: 22_510.75 },
  };
}

class MemoryStorage implements BotDrawingStorageAdapter {
  private readonly values = new Map<string, string>();

  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }

  removeItem(key: string): void {
    this.values.delete(key);
  }
}
