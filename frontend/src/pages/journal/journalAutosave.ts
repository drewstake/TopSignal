import { beginLiveMutationRequest } from "../../lib/liveMutationState";

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
  private finishLiveMutation: (() => void) | null = null;
  private disposed = false;
  private ignoreInFlightResult = false;
  private replacementBaseline: TPayload | null = null;

  constructor(options: DebouncedAutosaveQueueOptions<TPayload>) {
    this.delayMs = options.delayMs;
    this.save = options.save;
    this.equals = options.equals;
    this.onStateChange = options.onStateChange;
    this.onError = options.onError;
  }

  setBaseline(payload: TPayload): void {
    if (this.disposed) {
      return;
    }
    this.clearTimer();
    this.latestPayload = payload;
    this.lastSavedPayload = payload;
    this.queuedPayload = null;
    if (this.inFlight) {
      this.ignoreInFlightResult = true;
      this.replacementBaseline = payload;
    }
    this.setState("saved");
    this.syncLiveMutationLease();
    this.notifyIdle();
  }

  queue(payload: TPayload): void {
    if (this.disposed) {
      return;
    }
    this.latestPayload = payload;
    if (this.lastSavedPayload && this.equals(payload, this.lastSavedPayload) && !this.inFlight) {
      this.clearTimer();
      this.queuedPayload = null;
      this.setState("saved");
      this.syncLiveMutationLease();
      this.notifyIdle();
      return;
    }

    this.acquireLiveMutationLease();
    this.setState("unsaved");
    if (this.inFlight) {
      this.queuedPayload = payload;
      return;
    }

    this.clearTimer();
    this.timer = setTimeout(() => {
      this.timer = null;
      void this.persistLatest(false);
    }, this.delayMs);
  }

  async flush(): Promise<void> {
    if (this.disposed) {
      return;
    }
    this.clearTimer();
    await this.persistLatest(false);
    await this.waitForIdle();
  }

  async retryNow(): Promise<void> {
    if (this.disposed) {
      return;
    }
    this.clearTimer();
    await this.persistLatest(true);
    await this.waitForIdle();
  }

  cancel(): void {
    if (this.disposed) {
      return;
    }
    this.clearTimer();
    this.queuedPayload = null;
    if (this.inFlight) {
      this.ignoreInFlightResult = true;
      this.replacementBaseline = this.lastSavedPayload;
    }
    this.latestPayload = this.lastSavedPayload;
    this.setState("saved");
    this.syncLiveMutationLease();
    this.notifyIdle();
  }

  dispose(): void {
    if (this.disposed) {
      return;
    }
    this.disposed = true;
    this.clearTimer();
    this.queuedPayload = null;
    this.releaseLiveMutationLease();
    const callbacks = [...this.waiters];
    this.waiters = [];
    callbacks.forEach((callback) => callback());
  }

  private async persistLatest(force: boolean): Promise<void> {
    if (!this.latestPayload) {
      this.notifyIdle();
      return;
    }

    const payload = this.latestPayload;
    if (!force && this.lastSavedPayload && this.equals(payload, this.lastSavedPayload) && !this.inFlight) {
      this.setState("saved");
      this.syncLiveMutationLease();
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
    this.acquireLiveMutationLease();
    this.setState("saving");

    let saveFailed = false;
    let saveError: unknown;
    try {
      await this.save(payload);
    } catch (error) {
      saveFailed = true;
      saveError = error;
    }

    if (this.disposed) {
      this.inFlight = false;
      this.releaseLiveMutationLease();
      return;
    }

    if (this.ignoreInFlightResult) {
      this.ignoreInFlightResult = false;
      if (this.replacementBaseline !== null) {
        this.lastSavedPayload = this.replacementBaseline;
      }
      this.replacementBaseline = null;
      this.inFlight = false;
      if (this.queuedPayload) {
        const nextPayload = this.queuedPayload;
        this.queuedPayload = null;
        await this.persistPayload(nextPayload);
        return;
      }
      if (this.latestPayload && this.lastSavedPayload && this.equals(this.latestPayload, this.lastSavedPayload)) {
        this.setState("saved");
      }
      this.syncLiveMutationLease();
      this.notifyIdle();
      return;
    }

    if (saveFailed) {
      this.queuedPayload = null;
      this.inFlight = false;
      this.onError?.(saveError);
      this.setState("error");
      this.syncLiveMutationLease();
      this.notifyIdle();
      return;
    }

    this.lastSavedPayload = payload;
    this.inFlight = false;
    if (this.queuedPayload) {
      const nextPayload = this.queuedPayload;
      this.queuedPayload = null;
      if (this.lastSavedPayload && this.equals(nextPayload, this.lastSavedPayload)) {
        if (this.latestPayload && this.equals(this.latestPayload, this.lastSavedPayload)) {
          this.setState("saved");
          this.syncLiveMutationLease();
          this.notifyIdle();
          return;
        }
        this.setState("unsaved");
        this.syncLiveMutationLease();
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
    this.syncLiveMutationLease();
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

  private hasDirtyOrPendingWork(): boolean {
    if (this.disposed) {
      return false;
    }
    if (this.timer !== null || this.inFlight || this.queuedPayload !== null) {
      return true;
    }
    if (this.latestPayload === null) {
      return false;
    }
    return this.lastSavedPayload === null || !this.equals(this.latestPayload, this.lastSavedPayload);
  }

  private acquireLiveMutationLease(): void {
    if (this.finishLiveMutation !== null || this.disposed) {
      return;
    }
    this.finishLiveMutation = beginLiveMutationRequest();
  }

  private releaseLiveMutationLease(): void {
    const finish = this.finishLiveMutation;
    if (finish === null) {
      return;
    }
    this.finishLiveMutation = null;
    finish();
  }

  private syncLiveMutationLease(): void {
    if (this.hasDirtyOrPendingWork()) {
      this.acquireLiveMutationLease();
    } else {
      this.releaseLiveMutationLease();
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
