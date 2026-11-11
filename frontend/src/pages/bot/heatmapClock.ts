/**
 * Server-aligned clock for the heatmap.
 *
 * Provider timestamps, recorder receipts and the history API all use server
 * time. Comparing them with a skewed browser clock made fresh depth look stale
 * (a 10 s skew marked every live sample stale). Offsets come from API responses
 * using the midpoint of the request; low-latency samples are preferred.
 *
 * The first measurement applies immediately. Later corrections slew at most
 * 50 ms per second and the clock never runs backwards, so the 2 Hz sampler
 * neither skips nor repeats display buckets when the estimate improves.
 */
export class ServerClock {
  private target = 0;
  private offset = 0;
  private rtt = Number.POSITIVE_INFINITY;
  private updatedAt = 0;
  private known = false;
  private lastLocal: number | null = null;
  private lastValue = Number.NEGATIVE_INFINITY;
  private readonly local: () => number;

  constructor(local: () => number = Date.now) {
    this.local = local;
  }

  observe(serverMs: unknown, sentAt: number, receivedAt: number): void {
    if (typeof serverMs !== "number" || !Number.isFinite(serverMs) || receivedAt < sentAt) return;
    const rtt = receivedAt - sentAt;
    // Accept a better (lower round-trip) sample at any time, and refresh with an
    // ordinary sample every few minutes so slow clock drift is still tracked.
    if (!this.known || rtt <= this.rtt || receivedAt - this.updatedAt > 5 * 60_000) {
      const target = serverMs - (sentAt + receivedAt) / 2;
      // A much better sample that disagrees by over a second replaces a poor first estimate at once
      // (e.g. the first sync came from a slow read); ordinary refinements still slew.
      const hard = this.known && rtt * 4 < this.rtt && Math.abs(target - this.offset) > 1_000;
      this.target = target;
      if (!this.known || hard) {
        this.offset = this.target;
        this.lastValue = Number.NEGATIVE_INFINITY;
      }
      this.rtt = rtt;
      this.updatedAt = receivedAt;
      this.known = true;
    }
  }

  now(): number {
    const local = this.local();
    if (this.offset !== this.target) {
      const elapsed = this.lastLocal === null ? 0 : Math.max(0, local - this.lastLocal);
      const step = elapsed * 0.05;
      const difference = this.target - this.offset;
      this.offset += Math.abs(difference) <= step ? difference : Math.sign(difference) * step;
    }
    this.lastLocal = local;
    this.lastValue = Math.max(this.lastValue, local + this.offset);
    return this.lastValue;
  }

  get synced(): boolean {
    return this.known;
  }

  /** Browser clock minus server clock in milliseconds, once measured. */
  get skewMs(): number | null {
    return this.known ? -this.target : null;
  }

  get uncertaintyMs(): number | null {
    return this.known ? this.rtt / 2 : null;
  }
}
