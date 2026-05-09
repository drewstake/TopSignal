import { useEffect, useRef, useState, type KeyboardEvent } from "react";

import { Badge } from "../../components/ui/Badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/Card";
import { cn } from "../../components/ui/cn";
import {
  APP_THEME_CHANGED_EVENT,
  APP_THEMES,
  getAppTheme,
  isAppThemeId,
  readStoredAppThemeId,
  selectAppTheme,
  type AppThemeChangedDetail,
  type AppThemeId,
} from "../../lib/theme";

type PreviewActionVariant = "primary" | "secondary" | "ghost";

const previewActionStyles: Record<PreviewActionVariant, string> = {
  primary: "bg-app-accent/90 text-app-accent-contrast shadow-[0_8px_24px_-16px_rgb(var(--theme-accent)/0.95)]",
  secondary: "bg-app-raised text-app-text",
  ghost: "bg-transparent text-app-text-soft",
};

function PreviewAction({ children, variant = "primary" }: { children: string; variant?: PreviewActionVariant }) {
  return (
    <span
      className={cn(
        "inline-flex h-8 items-center justify-center gap-2 rounded-xl border border-transparent px-3 text-xs font-medium",
        previewActionStyles[variant],
      )}
    >
      {children}
    </span>
  );
}

export function ThemePage() {
  const [selectedThemeId, setSelectedThemeId] = useState<AppThemeId>(() => readStoredAppThemeId());
  const themeButtonRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const selectedTheme = getAppTheme(selectedThemeId);

  useEffect(() => {
    function handleThemeChanged(event: Event) {
      const detail = (event as CustomEvent<Partial<AppThemeChangedDetail> | undefined>).detail;
      if (isAppThemeId(detail?.themeId)) {
        setSelectedThemeId(detail.themeId);
      }
    }

    window.addEventListener(APP_THEME_CHANGED_EVENT, handleThemeChanged);
    return () => window.removeEventListener(APP_THEME_CHANGED_EVENT, handleThemeChanged);
  }, []);

  function handleThemeSelect(themeId: AppThemeId) {
    setSelectedThemeId(themeId);
    selectAppTheme(themeId);
  }

  function handleThemeKeyDown(event: KeyboardEvent<HTMLButtonElement>, themeIndex: number) {
    const lastThemeIndex = APP_THEMES.length - 1;
    let nextThemeIndex: number | null = null;

    if (event.key === "ArrowRight" || event.key === "ArrowDown") {
      nextThemeIndex = themeIndex === lastThemeIndex ? 0 : themeIndex + 1;
    } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
      nextThemeIndex = themeIndex === 0 ? lastThemeIndex : themeIndex - 1;
    } else if (event.key === "Home") {
      nextThemeIndex = 0;
    } else if (event.key === "End") {
      nextThemeIndex = lastThemeIndex;
    }

    if (nextThemeIndex === null) {
      return;
    }

    event.preventDefault();
    const nextTheme = APP_THEMES[nextThemeIndex];
    if (nextTheme) {
      handleThemeSelect(nextTheme.id);
      themeButtonRefs.current[nextThemeIndex]?.focus();
    }
  }

  return (
    <div className="space-y-6 pb-8">
      <section className="flex flex-col gap-4 border-b border-app-border/80 pb-6 lg:flex-row lg:items-end lg:justify-between">
        <div className="max-w-3xl space-y-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-app-accent">Appearance</p>
          <h1 className="text-3xl font-semibold tracking-tight text-app-text md:text-4xl">Themes</h1>
          <p className="max-w-2xl text-sm leading-6 text-app-muted">
            Select a workspace palette for dashboard review, trade management, and journal work.
          </p>
        </div>
        <div className="flex items-center gap-2 rounded-xl border border-app-border bg-app-surface/60 px-3 py-2 text-sm text-app-muted">
          <span className="text-app-muted-strong">Active</span>
          <span className="font-semibold text-app-text">{selectedTheme.name}</span>
        </div>
      </section>

      <section className="grid gap-4 lg:grid-cols-4" role="radiogroup" aria-label="Theme selection">
        {APP_THEMES.map((theme, themeIndex) => {
          const isSelected = theme.id === selectedThemeId;
          return (
            <Card
              key={theme.id}
              className={cn(
                "flex min-h-[250px] flex-col rounded-lg p-0 transition duration-200",
                isSelected ? "border-app-accent/70 shadow-[0_20px_45px_-32px_rgb(var(--theme-accent)/0.95)]" : "",
              )}
            >
              <button
                ref={(element) => {
                  themeButtonRefs.current[themeIndex] = element;
                }}
                type="button"
                className="flex h-full flex-1 flex-col rounded-lg p-4 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-app-accent/55"
                role="radio"
                aria-checked={isSelected}
                aria-label={`${theme.name}: ${theme.description}`}
                tabIndex={isSelected ? 0 : -1}
                onClick={() => handleThemeSelect(theme.id)}
                onKeyDown={(event) => handleThemeKeyDown(event, themeIndex)}
              >
                <div
                  className="grid h-24 overflow-hidden rounded-lg border border-app-border"
                  style={{ gridTemplateColumns: `repeat(${theme.swatches.length}, minmax(0, 1fr))` }}
                >
                  {theme.swatches.map((swatch) => (
                    <span key={swatch} style={{ backgroundColor: swatch }} />
                  ))}
                </div>
                <div className="mt-4 flex flex-1 flex-col gap-3">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <CardTitle className="text-base">{theme.name}</CardTitle>
                      <CardDescription className="mt-1 leading-5">{theme.description}</CardDescription>
                    </div>
                    <span
                      className={cn(
                        "mt-1 h-4 w-4 shrink-0 rounded-full border",
                        isSelected
                          ? "border-app-accent/70 bg-app-accent shadow-[0_0_0_4px_rgb(var(--theme-accent)/0.16)]"
                          : "border-app-border-strong",
                      )}
                      aria-hidden="true"
                    />
                  </div>
                  <div className="mt-auto flex flex-wrap gap-2">
                    {theme.tags.map((tag) => (
                      <Badge key={tag} variant={tag === "Default" ? "accent" : "neutral"}>
                        {tag}
                      </Badge>
                    ))}
                  </div>
                </div>
              </button>
            </Card>
          );
        })}
      </section>

      <section className="grid gap-5 lg:grid-cols-[1.1fr_0.9fr]">
        <Card className="rounded-lg">
          <CardHeader>
            <CardTitle>Live Preview</CardTitle>
            <CardDescription>Controls, surfaces, metrics, and status colors using the active palette.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-5">
            <div className="grid gap-3 sm:grid-cols-3">
              <div className="rounded-lg border border-app-border bg-app-surface/60 p-3">
                <p className="text-xs text-app-muted-strong">Net PnL</p>
                <p className="mt-1 text-2xl font-semibold text-app-text">$4,280</p>
                <p className="mt-1 text-xs text-app-positive">+12.4% this week</p>
              </div>
              <div className="rounded-lg border border-app-border bg-app-surface/60 p-3">
                <p className="text-xs text-app-muted-strong">Win Rate</p>
                <p className="mt-1 text-2xl font-semibold text-app-text">64%</p>
                <p className="mt-1 text-xs text-app-accent">34 completed trades</p>
              </div>
              <div className="rounded-lg border border-app-border bg-app-surface/60 p-3">
                <p className="text-xs text-app-muted-strong">Max Drawdown</p>
                <p className="mt-1 text-2xl font-semibold text-app-text">$710</p>
                <p className="mt-1 text-xs text-app-warning">Inside risk plan</p>
              </div>
            </div>

            <div className="rounded-lg border border-app-border bg-app-bg/60 p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <p className="text-sm font-semibold text-app-text">Session Controls</p>
                  <p className="text-xs text-app-muted">Primary, secondary, and quiet actions.</p>
                </div>
                <div className="flex flex-wrap gap-2">
                  <PreviewAction>Apply</PreviewAction>
                  <PreviewAction variant="secondary">Review</PreviewAction>
                  <PreviewAction variant="ghost">Cancel</PreviewAction>
                </div>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card className="rounded-lg">
          <CardHeader>
            <CardTitle>Current Palette</CardTitle>
            <CardDescription>{selectedTheme.description}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div
              className="grid overflow-hidden rounded-lg border border-app-border"
              style={{ gridTemplateColumns: `repeat(${selectedTheme.swatches.length}, minmax(0, 1fr))` }}
            >
              {selectedTheme.swatches.map((swatch) => (
                <div key={swatch} className="h-16" style={{ backgroundColor: swatch }} />
              ))}
            </div>
            <div className="grid gap-3 text-sm sm:grid-cols-2">
              <div className="rounded-lg border border-app-border bg-app-surface/60 p-3">
                <p className="text-xs uppercase tracking-wide text-app-muted-strong">Mode</p>
                <p className="mt-1 font-semibold capitalize text-app-text">{selectedTheme.colorScheme}</p>
              </div>
              <div className="rounded-lg border border-app-border bg-app-surface/60 p-3">
                <p className="text-xs uppercase tracking-wide text-app-muted-strong">Status</p>
                <p className="mt-1 font-semibold text-app-text">Applied</p>
              </div>
            </div>
          </CardContent>
        </Card>
      </section>
    </div>
  );
}
