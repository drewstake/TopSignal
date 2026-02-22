export type JournalSaveState = "saved" | "saving" | "unsaved" | "error";

interface DebouncedAutosaveQueueOptions<TPayload> {
  delayMs: number;
  save: (payload: TPayload) => Promise<void>;
  equals: (left: TPayload, right: TPayload) => boolean;
  onStateChange: (state: JournalSaveState) => void;
  onError?: (error: unknown) => void;
}

export class DebouncedAutosaveQueue<TPayload> {
  private readonly delayMs: number;
  private readonly save: (payload: TPayload) => Promise<void>;
  private readonly equals: (left: TPayload, right: TPayload) => boolean;
  private readonly onStateChange: (state: JournalSaveState) => void;
  private readonly onError?: (error: unknown) => void;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private latestPayload: TPayload | null = null;
  private lastSavedPayload: TPayload | null = null;
  private queuedPayload: TPayload | null = null;
  private inFlight = false;
  private state: JournalSaveState = "saved";
  private waiters: Array<() => void> = [];

  constructor(options: DebouncedAutosaveQueueOptions<TPayload>) {
    this.delayMs = options.delayMs;
    this.save = options.save;
    this.equals = options.equals;
    this.onStateChange = options.onStateChange;
    this.onError = options.onError;
  }

  setBaseline(payload: TPayload): void {
    this.clearTimer();
    this.latestPayload = payload;
    this.lastSavedPayload = payload;
    this.queuedPayload = null;
    this.setState("saved");
    this.notifyIdle();
  }

  queue(payload: TPayload): void {
    this.latestPayload = payload;
    if (this.lastSavedPayload && this.equals(payload, this.lastSavedPayload) && !this.inFlight) {
      this.clearTimer();
      this.queuedPayload = null;
      this.setState("saved");
      this.notifyIdle();
      return;
    }

    this.setState("unsaved");
    if (this.inFlight) {
      this.queuedPayload = payload;
      return;
    }

    this.clearTimer();
    this.timer = setTimeout(() => {
      void this.persistLatest(false);
    }, this.delayMs);
  }

  async flush(): Promise<void> {
    this.clearTimer();
    await this.persistLatest(false);
    await this.waitForIdle();
  }

  async retryNow(): Promise<void> {
    this.clearTimer();
    await this.persistLatest(true);
    await this.waitForIdle();
  }

  dispose(): void {
    this.clearTimer();
    this.waiters = [];
  }

  private async persistLatest(force: boolean): Promise<void> {
    if (!this.latestPayload) {
      this.notifyIdle();
      return;
    }

    const payload = this.latestPayload;
    if (!force && this.lastSavedPayload && this.equals(payload, this.lastSavedPayload) && !this.inFlight) {
      this.setState("saved");
      this.notifyIdle();
      return;
    }

    if (this.inFlight) {
      this.queuedPayload = payload;
      return;
    }

    await this.persistPayload(payload);
  }

  private async persistPayload(payload: TPayload): Promise<void> {
    this.inFlight = true;
    this.setState("saving");

    try {
      await this.save(payload);
      this.lastSavedPayload = payload;
    } catch (error) {
      this.queuedPayload = null;
      this.inFlight = false;
      this.onError?.(error);
      this.setState("error");
      this.notifyIdle();
      return;
    }

    this.inFlight = false;
    if (this.queuedPayload) {
      const nextPayload = this.queuedPayload;
      this.queuedPayload = null;
      if (this.lastSavedPayload && this.equals(nextPayload, this.lastSavedPayload)) {
        if (this.latestPayload && this.equals(this.latestPayload, this.lastSavedPayload)) {
          this.setState("saved");
          this.notifyIdle();
          return;
        }
        this.setState("unsaved");
        this.notifyIdle();
        return;
      }
      await this.persistPayload(nextPayload);
      return;
    }

    if (this.latestPayload && this.lastSavedPayload && this.equals(this.latestPayload, this.lastSavedPayload)) {
      this.setState("saved");
    } else {
      this.setState("unsaved");
    }
    this.notifyIdle();
  }

  private waitForIdle(): Promise<void> {
    if (!this.inFlight && this.queuedPayload === null && this.timer === null) {
      return Promise.resolve();
    }
    return new Promise((resolve) => {
      this.waiters.push(resolve);
    });
  }

  private notifyIdle(): void {
    if (this.inFlight || this.queuedPayload !== null || this.timer !== null) {
      return;
    }
    const callbacks = [...this.waiters];
    this.waiters = [];
    callbacks.forEach((callback) => callback());
  }

  private clearTimer(): void {
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
  }

  private setState(nextState: JournalSaveState): void {
    if (this.state === nextState) {
      return;
    }
    this.state = nextState;
    this.onStateChange(nextState);
  }
}
