import { describe, expect, it } from "vitest";

import { LatestRequestGate } from "./latestRequest";

describe("LatestRequestGate", () => {
  it("makes an older response stale as soon as a newer request starts", () => {
    const gate = new LatestRequestGate();
    const first = gate.begin();
    const second = gate.begin();

    expect(first()).toBe(false);
    expect(second()).toBe(true);
  });

  it("invalidates the active response on unmount", () => {
    const gate = new LatestRequestGate();
    const active = gate.begin();
    gate.invalidate();

    expect(active()).toBe(false);
  });
});
