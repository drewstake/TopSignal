import { useEffect, useLayoutEffect, useState } from "react";

export interface AccountScopeToken {
  readonly accountId: number;
  readonly accountEpoch: number;
}

export interface AccountRequestToken extends AccountScopeToken {
  readonly channel: string;
  readonly generation: number;
}

/**
 * Guards asynchronous UI work by both the active account and a named request
 * channel. Switching away and back invalidates the old work, while unrelated
 * channels for the same account can continue independently.
 */
export class AccountRequestGate {
  private activeAccountId: number | null = null;
  private accountEpoch = 0;
  private readonly channelGenerations = new Map<string, number>();

  activate(accountId: number | null): void {
    if (this.activeAccountId === accountId) {
      return;
    }
    this.activeAccountId = accountId;
    this.accountEpoch += 1;
  }

  capture(accountId: number): AccountScopeToken {
    return {
      accountId,
      accountEpoch: this.accountEpoch,
    };
  }

  begin(accountId: number, channel = "default"): AccountRequestToken {
    const key = this.channelKey(accountId, channel);
    const generation = (this.channelGenerations.get(key) ?? 0) + 1;
    this.channelGenerations.set(key, generation);
    return {
      ...this.capture(accountId),
      channel,
      generation,
    };
  }

  isActive(token: AccountScopeToken): boolean {
    return this.activeAccountId === token.accountId && this.accountEpoch === token.accountEpoch;
  }

  isCurrent(token: AccountRequestToken): boolean {
    return (
      this.isActive(token) &&
      this.channelGenerations.get(this.channelKey(token.accountId, token.channel)) === token.generation
    );
  }

  invalidate(accountId: number, channel = "default"): void {
    const key = this.channelKey(accountId, channel);
    this.channelGenerations.set(key, (this.channelGenerations.get(key) ?? 0) + 1);
  }

  invalidateAll(): void {
    this.activeAccountId = null;
    this.accountEpoch += 1;
    this.channelGenerations.clear();
  }

  private channelKey(accountId: number, channel: string): string {
    return `${accountId}:${channel}`;
  }
}

export function useAccountRequestGate(activeAccountId: number | null): AccountRequestGate {
  const [gate] = useState(() => new AccountRequestGate());

  useLayoutEffect(() => {
    gate.activate(activeAccountId);
  }, [activeAccountId, gate]);

  useEffect(
    () => () => {
      gate.invalidateAll();
    },
    [gate],
  );

  return gate;
}
