// @vitest-environment jsdom

import { Suspense, lazy } from "react";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ViewportDeferredSection } from "./ViewportDeferredSection";

type ObserverCallback = ConstructorParameters<typeof IntersectionObserver>[0];

class FakeIntersectionObserver implements IntersectionObserver {
  static instances: FakeIntersectionObserver[] = [];

  private readonly callback: ObserverCallback;
  readonly root = null;
  readonly rootMargin: string;
  readonly thresholds = [0];
  readonly disconnect = vi.fn();
  readonly observe = vi.fn();
  readonly takeRecords = vi.fn(() => []);
  readonly unobserve = vi.fn();

  constructor(callback: ObserverCallback, options?: IntersectionObserverInit) {
    this.callback = callback;
    this.rootMargin = options?.rootMargin ?? "0px";
    FakeIntersectionObserver.instances.push(this);
  }

  trigger(isIntersecting: boolean) {
    this.callback(
      [
        {
          isIntersecting,
          intersectionRatio: isIntersecting ? 1 : 0,
        } as IntersectionObserverEntry,
      ],
      this,
    );
  }
}

const originalIntersectionObserver = window.IntersectionObserver;

afterEach(() => {
  cleanup();
  FakeIntersectionObserver.instances = [];
  if (originalIntersectionObserver) {
    window.IntersectionObserver = originalIntersectionObserver;
  } else {
    Reflect.deleteProperty(window, "IntersectionObserver");
  }
});

describe("ViewportDeferredSection", () => {
  it("keeps children unmounted until the section approaches the viewport", () => {
    window.IntersectionObserver = FakeIntersectionObserver;

    render(
      <ViewportDeferredSection fallback={<p>Deferred placeholder</p>}>
        <p>Expensive dashboard section</p>
      </ViewportDeferredSection>,
    );

    expect(screen.getByText("Deferred placeholder")).toBeTruthy();
    expect(screen.queryByText("Expensive dashboard section")).toBeNull();
    expect(FakeIntersectionObserver.instances).toHaveLength(1);
    expect(FakeIntersectionObserver.instances[0].rootMargin).toBe("600px 0px");

    act(() => FakeIntersectionObserver.instances[0].trigger(true));

    expect(screen.getByText("Expensive dashboard section")).toBeTruthy();
    expect(FakeIntersectionObserver.instances[0].disconnect).toHaveBeenCalled();
  });

  it("does not start a lazy import before the deferred section intersects", async () => {
    window.IntersectionObserver = FakeIntersectionObserver;
    const importer = vi.fn(async () => ({ default: () => <p>Lazy dashboard card</p> }));
    const LazyDashboardCard = lazy(importer);

    render(
      <ViewportDeferredSection fallback={<p role="status">Height-preserving placeholder</p>}>
        <Suspense fallback={<p role="status">Loading lazy card</p>}>
          <LazyDashboardCard />
        </Suspense>
      </ViewportDeferredSection>,
    );

    expect(importer).not.toHaveBeenCalled();
    expect(screen.getByRole("status")).toBeTruthy();
    expect(screen.getByText("Height-preserving placeholder")).toBeTruthy();

    await act(async () => FakeIntersectionObserver.instances[0].trigger(true));

    await waitFor(() => expect(importer).toHaveBeenCalledTimes(1));
    expect(await screen.findByText("Lazy dashboard card")).toBeTruthy();
  });

  it("mounts immediately when IntersectionObserver is unavailable", () => {
    Reflect.deleteProperty(window, "IntersectionObserver");

    render(
      <ViewportDeferredSection fallback={<p>Deferred placeholder</p>}>
        <p>Expensive dashboard section</p>
      </ViewportDeferredSection>,
    );

    expect(screen.getByText("Expensive dashboard section")).toBeTruthy();
    expect(screen.queryByText("Deferred placeholder")).toBeNull();
  });
});
