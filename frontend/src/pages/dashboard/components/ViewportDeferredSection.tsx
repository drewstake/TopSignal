import { useEffect, useRef, useState, type ReactNode } from "react";

interface ViewportDeferredSectionProps {
  children: ReactNode;
  fallback: ReactNode;
  rootMargin?: string;
}

function canObserveViewport() {
  return typeof window !== "undefined" && typeof window.IntersectionObserver === "function";
}

export function ViewportDeferredSection({
  children,
  fallback,
  rootMargin = "600px 0px",
}: ViewportDeferredSectionProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [shouldMount, setShouldMount] = useState(() => !canObserveViewport());

  useEffect(() => {
    if (shouldMount) {
      return;
    }

    const target = containerRef.current;
    if (!target || !canObserveViewport()) {
      const fallbackTimer = window.setTimeout(() => setShouldMount(true), 0);
      return () => window.clearTimeout(fallbackTimer);
    }

    const observer = new window.IntersectionObserver(
      (entries) => {
        if (!entries.some((entry) => entry.isIntersecting || entry.intersectionRatio > 0)) {
          return;
        }
        setShouldMount(true);
        observer.disconnect();
      },
      { rootMargin },
    );
    observer.observe(target);

    return () => observer.disconnect();
  }, [rootMargin, shouldMount]);

  return (
    <div ref={containerRef} aria-busy={!shouldMount}>
      {shouldMount ? children : fallback}
    </div>
  );
}
