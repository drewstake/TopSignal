// @vitest-environment jsdom
import { cleanup, render } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { DonutRing } from "./DonutRing";
import { SplitBar } from "./SplitBar";

afterEach(cleanup);

it.each([
  [100, 0, ["green"]], [0, 100, ["red"]], [99.9, 0.1, ["green", "red"]],
  [0, 0, []], [null, null, []],
] as const)("draws only positive shares for %s long and %s short", (long, short, colors) => {
  const { container } = render(<DonutRing centerLabel="Direction" segments={[
    { label: "Long", value: long, valueLabel: String(long), color: "green" },
    { label: "Short", value: short, valueLabel: String(short), color: "red" },
  ]} />);
  const arcs = Array.from(container.querySelectorAll("svg g circle"));
  expect(arcs.map(arc => arc.getAttribute("stroke"))).toEqual(colors);
  if (arcs.length === 1) expect(arcs[0].hasAttribute("stroke-dasharray")).toBe(false);
  for (const arc of arcs) expect(arc.getAttribute("stroke-linecap")).toBe("butt");
});

it("leaves the comparison track empty when both magnitudes are zero", () => {
  const { container } = render(<SplitBar leftLabel="Long" rightLabel="Short"
    leftValue="$0" rightValue="$0" leftMagnitude={0} rightMagnitude={0} />);
  expect(Array.from(container.querySelectorAll<HTMLElement>("[aria-hidden]")).map(bar => bar.style.width)).toEqual(["0%", "0%"]);
});
