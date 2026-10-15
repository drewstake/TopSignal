# Simulation chart interactions and performance

Tested on October 15, 2026. The requested destination is the new funded
`TradingPage`. Its production playback remains fixed at 18 simulated seconds per
wall second (72 quarter-second engine steps). “4×” below is a benchmark-only
elapsed-time multiplier, never a production control.

## Implementation

- Compact **A** and **L** buttons sit at the bottom of the price axis, with
  tooltips, pressed states and keyboard equivalents. Auto price fitting and
  following the latest candle are independent. Double-clicking the price axis
  restores Auto without moving the time window or changing Log.
- One forward/inverse price transform covers candles, wicks, dense candle paths,
  indicators, measurements, crosshairs, price markers and labels. Price dragging
  and panning operate in linear or logarithmic coordinates. Price-axis wheel
  scaling retains the pointer price; time zoom and moving pinch gestures retain
  their time anchor. Existing keyboard navigation remains available.
- Log rejects candles spanning nonpositive prices, omits invalid geometry and
  explains the omission. Actual OHLC values remain available. An entirely invalid
  visible window has no invented log labels or crosshair price. Tiny positive
  labels use scientific notation instead of rounding to zero.
- View and indicator preferences are scoped to instrument/timeframe. View writes
  wait until a gesture settles, then flush on unmount or page exit.
- The chart stays SVG: visible-candle slicing, memoized glyphs, three dense paths,
  and a separate cursor subscription already keep chart work inexpensive. The
  new memo boundary also avoids chart renders caused by unrelated ticket input.
  No price interpolation or skipped simulation steps were introduced.
- Expanded view retains the mounted chart and restores focus/scroll on Escape.
  Configuration remains in the chart tools popover. The funded population is
  fixed and read-only; this task does not introduce participant overrides.
- Funded seed persistence writes on seed changes, retrying after storage failure,
  while delivering every worker publication. Currency/price formatters are reused
  across publications. The original worker publication and execution cadence is
  retained.

## Measurement conditions

AMD Ryzen 7 5800X (8 cores / 16 logical processors), 31.9 GiB RAM, Windows
10.0.26300, installed Chrome 155.0.8059.40, headless, 1440×1000 CSS pixels,
DPR 1. The machine has an RTX 3070; hardware GPU acceleration was not independently
verified. Observed rAF cadence was approximately 144 Hz. Both sides use React's
production profiling build and Chrome CPU sampling, with identical scripted
pointer gestures and six-second measurement windows after settling. CSS is
processed through the application's Tailwind/PostCSS configuration. Benchmarks
run sequentially, without concurrent tests/builds, on a shared desktop.

Fixtures use seed 73521 and real executed simulation history, up to the retained
1,200 one-minute candles. Fresh browser contexts and an ephemeral localhost
server isolate all storage. These sessions never read existing saved runs or
call the backend. Engine steps continue in order; only the benchmark worker's
initial state and test-only elapsed-time multiplier differ from production.
Each funded workload resets to the same frozen executed state and zero debt.
The fixture SHA-256 is
`46904e5ea5530669cf431344cc811493fe0bf20eb8bc2cdfb2b1a2fd6d775d03`.
The before and after funded engine/model/participant source hashes match.
The fixture was captured from the engine snapshot at the start of profiling;
reuse that fixture for exact comparisons rather than regenerating from a later
engine revision.

Frame measurements are rAF intervals, not a claim about every compositor frame.
Input responsiveness is event timestamp to next rAF, not physical input latency
or INP. React durations measure commits, not the full browser rendering pipeline.
Long tasks are main-thread tasks over 50 ms; worker debt/throughput is reported
separately. Short headless samples are not a guarantee across devices or long
sessions. Touch pinch is regression-tested with pointer events, not measured on
physical touch hardware.

## Funded TradingPage results

The funded page already used a worker and met the frame-time target before
these changes. The shared chart adds Log/Auto interactions while retaining that
frame cadence; the measurable improvement is cheaper rendering and persistence,
not a large playback FPS increase. The order book and recent execution tape
remain open throughout. “Ticket adjustments” edits the player contract input
during playback; fixed bot participants are not adjustable on this page.

| Workload | Frame p95/p99 before → after (ms) | Input p95 before → after (ms) | Steps/s before → after |
|---|---|---|---|
| Paused crosshair | 7.1/7.2 → 7.1/7.1 | 8.7 → 8.7 | 0 → 0 |
| Paused pan | 7.1/7.1 → 7.1/13.7 | 7.3 → 8.7 | 0 → 0 |
| Paused zoom | 7.1/7.1 → 13.8/13.9 | 8.3 → 12.5 | 0 → 0 |
| Playback 1× | 7.1/7.2 → 7.1/13.9 | 8.9 → 11.5 | 72.52 → 72.01 |
| Playback 4× | 7.1/7.2 → 7.1/13.9 | 9.2 → 10.3 | 192.07 → 177.57 |
| Dense + indicators, 4× | 7.1/7.2 → 7.1/13.9 | 9.3 → 11.7 | 192.14 → 147.69 |
| Ticket adjustments, 4× | 7.1/7.2 → 7.1/13.8 | 9.1 → 10.2 | 194.06 → 148.59 |
| Fills tab open, 4× | 7.1/7.2 → 13.7/13.9 | 8.8 → 11.9 | 193.64 → 151.10 |
| Log + Auto, historical view, 4× | unavailable → 13.7/13.9 | unavailable → 12.4 | unavailable → 152.21 |

All funded samples recorded zero main-thread long tasks. Paused-crosshair React
p95 fell from 1.5 to 0.8 ms, pan from 1.5 to 0.7 ms, and zoom from 1.5 to 0.8 ms.
Paused-pan storage calls fell from 329 to zero during the gesture; the final
preference still saves after settling. Unchanged seed writes fell from about
55–58 per window to zero. Dense playback React p95 was 3.0 → 2.4 ms, ticket
adjustments 3.2 → 2.1 ms, and normal playback 1.6 → 1.0 ms. The final frame p99
stayed below the 16.7 ms budget, but **these captures do not establish a blanket
funded FPS or input-latency improvement**: final frame/input percentiles were
worse despite lower React work. Earlier captures had 7.1–7.2 ms frame p99 on
both builds; shared-desktop variation and short windows limit attribution.

Normal fixed-speed playback sustained approximately 72 steps/s with under one
step of residual debt. **4× is CPU-bound in the simulation worker**: neither
build reached the requested 288 steps/s. Final 4× throughput was lower and the
full cause of that delta is unresolved; no simulation-throughput improvement is
claimed. Its 4× windows ended with roughly 182–232 simulated seconds pending.
The clock retains every owed step instead of
dropping work or changing participant decisions. This is the principal remaining
bottleneck; further throughput work needs controlled, longer worker profiles and
deterministic equivalence checks. An experimental suppression of paused
publications was discarded after lower 4× throughput was observed; the final
implementation retains the original worker cadence. The publication handler
still accounts for the largest named non-idle main-thread profile samples,
including subscriber work. Full histories are still cloned on each publication.

Maximum recorded funded frame intervals were 83.3 ms before and 62.5 ms after;
the capture includes CPU-profiler start/stop overhead. Thus these measurements
support the percentile target, not a claim that every frame met 16.7 ms.
Instrument/timeframe switching (three instruments × five timeframes), 390 px
mobile resizing, expanded view and Escape were also checked. Mobile had no
document overflow. A separate final visual check confirms edge price labels
clear the A/L controls while price lines/crosshairs keep their actual coordinates.

## Earlier SimulationPage profile

These results concern the retained legacy workspace, measured before the user
selected the funded page. They must not be read as speedups of the funded page.
Profiling showed currency formatting in mounted hidden participant ledgers was
more expensive than the chart. The 4× CPU profile attributed 572 samples to
`dollars` versus 97 to `advanceSimulation`; paused chart React commits were
approximately 0.5 ms. Replacing the renderer would not address that bottleneck.

Hidden activity/individual/order-book panels now stop subscribing or calculating;
integer/currency formatters are reused. The legacy checkpoint writer skips an
unchanged already-saved session while retaining lease and writer checks. No
checkpoint schema or simulation decision code changed.

| Workload | Frame p95/p99 before → after (ms) | Input p95 before → after (ms) | Steps/s before → after |
|---|---|---|---|
| Paused crosshair | 7.1/7.2 → 7.1/7.1 | 8.5 → 8.8 | 0 → 0 |
| Paused pan | 7.1/7.1 → 7.1/7.1 | 7.2 → 7.1 | 0 → 0 |
| Paused zoom | 7.1/7.1 → 7.1/7.1 | 8.7 → 8.5 | 0 → 0 |
| Playback 1× | 13.9/14.0 → 7.1/7.2 | 19.0 → 8.6 | 71.81 → 72.29 |
| Playback 4× | 13.9/14.1 → 7.1/7.1 | 20.1 → 9.4 | 288.62 → 288.41 |
| Dense + indicators | 13.9/20.9 → 7.1/7.2 | 21.9 → 9.9 | 288.47 → 287.61 |
| Participant adjustments | 27.7/34.8 → 7.1/7.2 | 31.4 → 10.1 | 287.54 → 287.93 |
| Order book open | 20.9/27.8 → 7.2/14.0 | 28.6 → 14.2 | 287.17 → 287.49 |
| Activity open | 14.0/20.9 → 7.1/7.2 | 20.8 → 9.2 | 286.62 → 287.69 |

All final legacy samples had zero long tasks. Open-book React p95 fell from
17.3 to 3.1 ms. Default DOM nodes fell from 3,550 to 716. Paused-pan storage calls
fell from 292 to three lease renewals. Maximum recorded frame intervals were
41.9 → 76.3 ms, including profiler overhead. Active legacy checkpoint writes
still take approximately 4–5 ms periodically and remain a potential source of
input-latency spikes. That runtime is not used by the funded page.

## Reproduction and regression coverage

From `frontend`, run `node scripts/funded-benchmark.mjs funded-after` or the
legacy `node scripts/simulation-benchmark.mjs after`. `PLAYWRIGHT_MODULE` may
point to an installed Playwright module and `CHROMIUM_PATH` to Chrome.
`BENCH_SOURCE` selects a preserved `simulation/` source tree for the before
build. The local before snapshots and full CPU profiles live in
`tmp/simulation-benchmark`; they are intentionally ignored, along with the large
execution fixtures. Creating the maximum-history funded fixture requires a long
real engine run. Both comparison builds reuse exactly the same fixture.
The final numeric captures are checked in under `docs/performance/` so the
evidence remains available without the ignored fixtures/profiles. Use
`BENCH_SMOKE_ONLY=1` for the funded harness's geometry/layout checks alone.

Regression tests cover forward/inverse coordinates, log ratios and bounds,
nonpositive prices, linear/log layer alignment, pointer-anchored scaling,
axis dragging, moving pinch centers, Auto/Log while browsing history, preference
isolation, keyboard inspection, expanded-view focus and clock debt. Deep state
comparisons prove chart operations/publications and timer batch sizes preserve
engine state, RNG and execution results. Legacy 4× playback is compared with
the equivalent individual fixed steps.

Validation: all **1,163 tests in 136 frontend files** passed. After the final
formatter/cadence adjustment, all ten affected funded UI/runtime/clock tests
passed again. The production TypeScript/Vite build and full frontend ESLint
check passed. Source fixtures and native parity tests from the funded-page work
are included in the same full test run. All 40 development-script tests passed.
Test logs and screenshots are in
`tmp/simulation-benchmark`; final numeric captures are linked below.

- [Funded before](performance/2026-10-15-funded-before.json)
- [Funded after](performance/2026-10-15-funded-after.json)
- [Legacy before](performance/2026-10-15-legacy-before.json)
- [Legacy after](performance/2026-10-15-legacy-after.json)
