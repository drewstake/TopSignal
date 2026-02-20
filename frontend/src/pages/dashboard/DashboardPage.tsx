import { useState, type FormEvent } from "react";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/Card";
import { Input } from "../../components/ui/Input";
import {
  contractPerformance,
  dailyPnl,
  journalEntries,
  kpiMetrics,
  mockTrades,
  riskRules,
  sessionPerformance,
  type JournalEntry,
  type Trade,
} from "../../mock/data";

const currencyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const dateTimeFormatter = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
  timeZone: "UTC",
});

const dateFormatter = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  timeZone: "UTC",
});

const stickyHeadClass =
  "sticky top-0 z-10 border-b border-slate-800/80 bg-slate-900/95 px-3 py-3 text-xs font-medium uppercase tracking-wide text-slate-400";

function sideVariant(side: Trade["side"]) {
  return side === "Long" ? "accent" : "warning";
}

function signedNumber(value: number, digits = 2) {
  const prefix = value > 0 ? "+" : "";
  return `${prefix}${value.toFixed(digits)}`;
}

function pnlClass(value: number) {
  return value >= 0 ? "text-emerald-300" : "text-rose-300";
}

function formatPnl(value: number) {
  const prefix = value > 0 ? "+" : "";
  return `${prefix}${currencyFormatter.format(value)}`;
}

function formatPrice(value: number) {
  return value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 5 });
}

function formatDate(isoDate: string) {
  return dateFormatter.format(new Date(`${isoDate}T00:00:00Z`));
}

export function DashboardPage() {
  const [notes, setNotes] = useState<JournalEntry[]>(journalEntries);
  const [noteTitle, setNoteTitle] = useState("");
  const [noteBody, setNoteBody] = useState("");

  const recentTrades = [...mockTrades].sort((left, right) => right.closedAt.localeCompare(left.closedAt)).slice(0, 12);
  const maxDailyMagnitude = Math.max(...dailyPnl.map((point) => Math.abs(point.value)), 1);

  function handleAddNote(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    const cleanTitle = noteTitle.trim();
    const cleanBody = noteBody.trim();
    if (cleanTitle.length === 0 || cleanBody.length === 0) {
      return;
    }

    const now = new Date();
    const nextEntry: JournalEntry = {
      id: `JR-${now.getTime()}`,
      date: now.toISOString().slice(0, 10),
      title: cleanTitle,
      mood: "Focused",
      body: cleanBody,
      tags: ["manual", "dashboard"],
    };

    setNotes((current) => [nextEntry, ...current]);
    setNoteTitle("");
    setNoteBody("");
  }

  return (
    <div className="space-y-6 pb-10">
      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        {kpiMetrics.map((metric) => (
          <Card key={metric.id} className="p-4">
            <p className="text-xs uppercase tracking-[0.16em] text-slate-500">{metric.label}</p>
            <p className="mt-2 text-2xl font-semibold text-slate-100">{metric.value}</p>
            <p className={`mt-2 text-xs ${metric.changePct >= 0 ? "text-emerald-300" : "text-rose-300"}`}>
              {signedNumber(metric.changePct, 1)}%
              <span className="ml-2 text-slate-500">{metric.hint}</span>
            </p>
          </Card>
        ))}
      </section>

      <Card>
        <CardHeader>
          <CardTitle>Recent Trades</CardTitle>
          <CardDescription>Topstep futures fills with contract-level detail and PnL in USD.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-0">
          <div className="max-h-[420px] overflow-auto rounded-xl border border-slate-800/80">
            <table className="w-full min-w-[980px] border-collapse text-sm">
              <thead>
                <tr>
                  <th className={`${stickyHeadClass} text-left`}>Contract</th>
                  <th className={`${stickyHeadClass} text-left`}>Side</th>
                  <th className={`${stickyHeadClass} text-right`}>Qty</th>
                  <th className={`${stickyHeadClass} text-right`}>Entry</th>
                  <th className={`${stickyHeadClass} text-right`}>Exit</th>
                  <th className={`${stickyHeadClass} text-right`}>Pts</th>
                  <th className={`${stickyHeadClass} text-right`}>PnL ($)</th>
                  <th className={`${stickyHeadClass} text-right`}>Closed</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/70">
                {recentTrades.map((trade) => (
                  <tr key={trade.id} className="transition hover:bg-slate-900/65">
                    <td className="px-3 py-3 text-left">
                      <p className="font-medium text-slate-100">{trade.contract}</p>
                      <p className="text-xs text-slate-500">{trade.symbol}</p>
                    </td>
                    <td className="px-3 py-3 text-left">
                      <Badge variant={sideVariant(trade.side)}>{trade.side}</Badge>
                    </td>
                    <td className="px-3 py-3 text-right text-slate-200">{trade.qty}</td>
                    <td className="px-3 py-3 text-right font-mono text-slate-200">{formatPrice(trade.entryPrice)}</td>
                    <td className="px-3 py-3 text-right font-mono text-slate-200">{formatPrice(trade.exitPrice)}</td>
                    <td className={`px-3 py-3 text-right font-mono ${pnlClass(trade.points)}`}>{signedNumber(trade.points)}</td>
                    <td className={`px-3 py-3 text-right font-semibold ${pnlClass(trade.pnlUsd)}`}>{formatPnl(trade.pnlUsd)}</td>
                    <td className="px-3 py-3 text-right text-slate-400">{dateTimeFormatter.format(new Date(trade.closedAt))}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Symbol / Contract Performance</CardTitle>
          <CardDescription>Root-level performance snapshot across the tracked futures basket.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-0">
          <div className="max-h-[360px] overflow-auto rounded-xl border border-slate-800/80">
            <table className="w-full min-w-[900px] border-collapse text-sm">
              <thead>
                <tr>
                  <th className={`${stickyHeadClass} text-left`}>Symbol</th>
                  <th className={`${stickyHeadClass} text-right`}>Trades</th>
                  <th className={`${stickyHeadClass} text-right`}>Win Rate</th>
                  <th className={`${stickyHeadClass} text-right`}>Avg Hold</th>
                  <th className={`${stickyHeadClass} text-right`}>Avg Pts</th>
                  <th className={`${stickyHeadClass} text-right`}>Profit Factor</th>
                  <th className={`${stickyHeadClass} text-right`}>Net PnL ($)</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/70">
                {contractPerformance.map((row) => (
                  <tr key={row.symbol} className="transition hover:bg-slate-900/65">
                    <td className="px-3 py-3 text-left font-medium text-slate-100">{row.symbol}</td>
                    <td className="px-3 py-3 text-right text-slate-200">{row.trades}</td>
                    <td className="px-3 py-3 text-right">
                      <Badge variant={row.winRate >= 55 ? "positive" : "warning"}>{row.winRate.toFixed(1)}%</Badge>
                    </td>
                    <td className="px-3 py-3 text-right text-slate-200">{row.avgHold}</td>
                    <td className={`px-3 py-3 text-right font-mono ${pnlClass(row.avgPoints)}`}>{signedNumber(row.avgPoints)}</td>
                    <td className="px-3 py-3 text-right font-mono text-slate-200">{row.profitFactor.toFixed(2)}</td>
                    <td className={`px-3 py-3 text-right font-semibold ${pnlClass(row.netPnlUsd)}`}>{formatPnl(row.netPnlUsd)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      <section className="grid gap-4 xl:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle>Session Edge</CardTitle>
            <CardDescription>Analytics placeholder with session-level efficiency.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {sessionPerformance.map((session) => (
              <div key={session.session} className="rounded-xl border border-slate-800/80 bg-slate-900/45 p-3">
                <div className="flex items-center justify-between">
                  <p className="text-sm font-medium text-slate-100">{session.session}</p>
                  <Badge variant={session.winRate >= 55 ? "positive" : "warning"}>{session.winRate.toFixed(1)}%</Badge>
                </div>
                <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-800">
                  <div
                    className={`h-full rounded-full ${session.netPnlUsd >= 0 ? "bg-emerald-500/70" : "bg-rose-500/70"}`}
                    style={{ width: `${Math.min(100, Math.max(12, session.winRate))}%` }}
                  />
                </div>
                <p className={`mt-2 text-xs font-medium ${pnlClass(session.netPnlUsd)}`}>
                  {formatPnl(session.netPnlUsd)} net | Avg {signedNumber(session.avgPoints)} pts
                </p>
              </div>
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Daily PnL Rhythm</CardTitle>
            <CardDescription>Compact bar visualization of recent session outcomes.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            <div className="flex h-40 items-end gap-2">
              {dailyPnl.map((point) => {
                const barHeight = Math.max(8, Math.round((Math.abs(point.value) / maxDailyMagnitude) * 100));
                return (
                  <div key={point.day} className="flex flex-1 flex-col items-center gap-2">
                    <div className="relative h-28 w-full rounded-md bg-slate-900/70">
                      <div
                        className={`absolute bottom-0 left-0 right-0 rounded-md ${
                          point.value >= 0 ? "bg-emerald-500/70" : "bg-rose-500/70"
                        }`}
                        style={{ height: `${barHeight}%` }}
                      />
                    </div>
                    <span className="text-[10px] text-slate-500">{point.day}</span>
                  </div>
                );
              })}
            </div>
            <p className="text-xs text-slate-500">Bars are normalized by absolute daily magnitude to preserve shape.</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Risk Telemetry</CardTitle>
            <CardDescription>Rule tracking placeholders for Topstep discipline checks.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {riskRules.map((rule) => (
              <div key={rule.id} className="rounded-xl border border-slate-800/80 bg-slate-900/45 p-3">
                <div className="flex items-center justify-between">
                  <p className="text-sm font-medium text-slate-100">{rule.name}</p>
                  <Badge
                    variant={rule.status === "good" ? "positive" : rule.status === "warning" ? "warning" : "negative"}
                  >
                    {rule.progress}%
                  </Badge>
                </div>
                <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-800">
                  <div
                    className={`h-full rounded-full ${
                      rule.status === "good"
                        ? "bg-emerald-500/70"
                        : rule.status === "warning"
                          ? "bg-amber-500/70"
                          : "bg-rose-500/70"
                    }`}
                    style={{ width: `${rule.progress}%` }}
                  />
                </div>
                <p className="mt-2 text-xs text-slate-400">{rule.detail}</p>
              </div>
            ))}
          </CardContent>
        </Card>
      </section>

      <section className="grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader>
            <CardTitle>Journal</CardTitle>
            <CardDescription>Execution notes and session debriefs in one timeline.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="max-h-[320px] space-y-3 overflow-auto pr-1">
              {notes.map((entry) => (
                <article key={entry.id} className="rounded-xl border border-slate-800/80 bg-slate-900/45 p-3">
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-sm font-medium text-slate-100">{entry.title}</p>
                    <span className="text-xs text-slate-500">{formatDate(entry.date)}</span>
                  </div>
                  <p className="mt-2 text-sm text-slate-300">{entry.body}</p>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {entry.tags.map((tag) => (
                      <Badge key={`${entry.id}-${tag}`} variant="neutral">
                        {tag}
                      </Badge>
                    ))}
                  </div>
                </article>
              ))}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Add Note</CardTitle>
            <CardDescription>Quick capture for post-trade context and follow-up actions.</CardDescription>
          </CardHeader>
          <CardContent>
            <form className="space-y-3" onSubmit={handleAddNote}>
              <Input
                value={noteTitle}
                onChange={(event) => setNoteTitle(event.target.value)}
                placeholder="Session title"
                aria-label="Session title"
              />
              <textarea
                value={noteBody}
                onChange={(event) => setNoteBody(event.target.value)}
                placeholder="Write what happened, what worked, and what to improve."
                aria-label="Session note"
                className="min-h-36 w-full rounded-xl border border-slate-700 bg-slate-900/70 p-3 text-sm text-slate-100 outline-none transition focus:border-cyan-400/70 focus:ring-2 focus:ring-cyan-500/30"
              />
              <div className="flex items-center justify-between gap-3">
                <p className="text-xs text-slate-500">Notes are stored in this local dashboard session.</p>
                <Button type="submit" size="sm">
                  Add Note
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>
      </section>
    </div>
  );
}
