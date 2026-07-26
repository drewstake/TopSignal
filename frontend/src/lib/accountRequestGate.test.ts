import { describe, expect, it } from "vitest";

import { AccountRequestGate } from "./accountRequestGate";

describe("AccountRequestGate", () => {
  it("invalidates an operation when the active account changes", () => {
    const gate = new AccountRequestGate();
    gate.activate(101);
    const expressSync = gate.begin(101, "sync");

    gate.activate(202);

    expect(gate.isCurrent(expressSync)).toBe(false);
  });

  it("does not revive old work after switching away and back", () => {
    const gate = new AccountRequestGate();
    gate.activate(101);
    const oldRequest = gate.begin(101, "trade-data");

    gate.activate(202);
    gate.activate(101);

    expect(gate.isCurrent(oldRequest)).toBe(false);
    expect(gate.isCurrent(gate.begin(101, "trade-data"))).toBe(true);
  });

  it("keys generations by account and channel", () => {
    const gate = new AccountRequestGate();
    gate.activate(101);
    const firstTradeLoad = gate.begin(101, "trade-data");
    const imageUpload = gate.begin(101, "image-upload:7");
    const secondTradeLoad = gate.begin(101, "trade-data");

    expect(gate.isCurrent(firstTradeLoad)).toBe(false);
    expect(gate.isCurrent(secondTradeLoad)).toBe(true);
    expect(gate.isCurrent(imageUpload)).toBe(true);
  });

  it("keeps a captured account scope stale after a round trip", () => {
    const gate = new AccountRequestGate();
    gate.activate(101);
    const scope = gate.capture(101);

    gate.activate(202);
    gate.activate(101);

    expect(gate.isActive(scope)).toBe(false);
  });
});
