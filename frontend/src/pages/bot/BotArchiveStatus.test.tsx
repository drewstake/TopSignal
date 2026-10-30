import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { BotArchiveStatus } from "./BotArchiveStatus";
afterEach(cleanup);

it("keeps received, queued, persisted, retried and terminal loss counts distinct", () => {
  render(<BotArchiveStatus data={{ enabled: true, critical_events: {
    process: { optional_quotes_received: 100, optional_quotes_sampled_out: 80, optional_quotes_queued: 18,
      optional_quotes_dropped: 2, quote_memory_backlog: 1, optional_quotes_memory_retried: 3 },
    quotes: { persisted: 12, retried: 4, dropped: 2, backlog: 3, last_error: { category: "read_only" } },
  } }} />);
  expect(screen.getByText(/100 received.*80 sampled out.*18 queued/)).toBeTruthy();
  expect(screen.getByText(/12 persisted.*4 retried.*2 terminal drops.*3 pending/)).toBeTruthy();
  expect(screen.getByText(/Recording is not lossless/)).toBeTruthy();
});

it("keeps unavailable archive reconciliation separate from confirmed loss", () => {
  render(<BotArchiveStatus data={{ enabled: true, critical_events: {
    quotes: { persisted: 3, dropped: 1, uncertain: 2, backlog: 0 },
  } }} />);
  expect(screen.getByText(/3 persisted.*1 terminal drops.*2 uncertain outcomes.*0 pending/)).toBeTruthy();
  expect(screen.getByText(/neither confirmed persistence nor confirmed loss/)).toBeTruthy();
});

it("shows unresolved gaps, stale writes and recording errors without claiming completeness", () => {
  render(<BotArchiveStatus data={{ enabled: true, last_write: "2020-01-01T00:00:00Z", bytes: 1e6, disk_free_bytes: 1e12,
    errors: { errors: 2, last_error: { operation: "decision_write", type: "OSError", at: "2020-01-01" } },
    coverage: [{ day: "2026-10-28", contract: "MNQ.Z26", observed: 8, expected: 10, missing: 2, attempts: 3, gaps: [] }] }} />);
  expect(screen.getByText(/writes stale/)).toBeTruthy();
  expect(screen.getByText(/2 expected minutes missing/)).toBeTruthy();
  expect(screen.getByRole("alert").textContent).toContain("decision_write");
  expect(screen.getByText(/Latest quote receipt: Not observed/)).toBeTruthy();
});
// @vitest-environment jsdom

it("separates collector state, critical backlog and verified backup freshness", () => {
  render(<BotArchiveStatus data={{ enabled: true,
    collector: { enabled: true, authorized: true, running: true },
    critical_events: { backlog: 3, retry_records: 2, process: { memory_backlog: 1, critical_unrecoverable: 4 } },
    backup: { status: "failed", stale: true, last_failure: { type: "OSError" } },
  }} />);
  expect(screen.getByText(/Independent collector: running/)).toBeTruthy();
  expect(screen.getByText(/3 durably queued/)).toBeTruthy();
  expect(screen.getByText(/4 unrecoverable this process/)).toBeTruthy();
  expect(screen.getByText(/Off-device backup: failed/)).toBeTruthy();
});
