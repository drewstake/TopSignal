import type { BotBacktestResult } from "../../lib/types";

const integer = new Intl.NumberFormat("en-US");
const timestamp = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York", year: "numeric", month: "short", day: "numeric",
  hour: "numeric", minute: "2-digit",
});

export function BacktestDiagnostics({ result }: { result: BotBacktestResult }) {
  const gaps = result.data_quality?.gaps;
  const notes = result.notes ?? [];
  return <>
    {result.warnings.length > 0 && (
      <div role="status" className="rounded-xl border border-app-warning/35 bg-app-warning/10 p-4">
        <p className="text-xs font-semibold uppercase tracking-wide text-app-warning">Sample-quality warnings</p>
        <ul className="mt-2 list-disc space-y-1.5 pl-4 text-sm text-app-text-soft">
          {result.warnings.map((warning, index) => <li key={index}>{warning}</li>)}
        </ul>
      </div>
    )}
    {gaps && gaps.gap_count > 0 && (
      <details className="rounded-xl border border-app-border bg-app-bg/25 p-4">
        <summary className="cursor-pointer text-sm font-semibold text-app-text">Candle coverage · {integer.format(gaps.in_session_gap_count)} gaps during entry hours</summary>
        <div className="mt-3 space-y-4 text-sm text-app-text-soft">
          <p>{integer.format(gaps.missing_bar_count)} expected complete bars are absent across {integer.format(gaps.gap_count)} gaps; {integer.format(gaps.in_session_missing_bar_count)} of those bars fall within the bot’s entry hours. Times below are Eastern.</p>
          <p className="text-xs text-app-muted">Scheduled closures in the current calendar are excluded; historical schedule exceptions may still appear. A gap can reflect minutes with no trades, an incomplete aggregate, an exchange halt, or missing source data. OHLCV alone cannot establish the cause. No prices are filled in. Gaps outside entry hours can still affect indicators or positions held overnight.</p>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <caption className="pb-2 text-left font-semibold text-app-text">Gaps by year</caption>
              <thead className="text-app-muted"><tr><th className="py-2">Year</th><th>Gaps</th><th>Missing bars</th><th>During entry hours</th></tr></thead>
              <tbody>{gaps.by_year.map(row => <tr key={row.year} className="border-t border-app-border">
                <td className="py-2">{row.year}</td><td>{integer.format(row.gap_count)}</td><td>{integer.format(row.missing_bar_count)}</td><td>{integer.format(row.in_session_gap_count)}</td>
              </tr>)}</tbody>
            </table>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <caption className="pb-2 text-left font-semibold text-app-text">Largest gaps (up to 20) · first absent bar to next available bar</caption>
              <thead className="text-app-muted"><tr><th className="py-2">From (ET)</th><th>To (ET)</th><th>Missing bars</th><th>Bars during entry hours</th></tr></thead>
              <tbody>{gaps.largest_gaps.map(row => <tr key={row.start} className="border-t border-app-border">
                <td className="whitespace-nowrap py-2 pr-3">{timestamp.format(new Date(row.start))}</td>
                <td className="whitespace-nowrap pr-3">{timestamp.format(new Date(row.end))}</td>
                <td>{integer.format(row.missing_bar_count)}</td><td>{integer.format(row.in_session_missing_bar_count)}</td>
              </tr>)}</tbody>
            </table>
          </div>
        </div>
      </details>
    )}
    {notes.length > 0 && (
      <details className="rounded-xl border border-app-border bg-app-bg/25 p-4">
        <summary className="cursor-pointer text-sm font-semibold text-app-text">Replay details · warmup, risk controls and contract rolls</summary>
        <ul className="mt-3 list-disc space-y-1.5 pl-4 text-xs text-app-muted">
          {notes.map((note, index) => <li key={index}>{note}</li>)}
        </ul>
      </details>
    )}
  </>;
}
