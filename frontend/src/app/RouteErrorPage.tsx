import { isRouteErrorResponse, useLocation, useNavigate, useRouteError } from "react-router-dom";

import { Button } from "../components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../components/ui/Card";
import { cn } from "../components/ui/cn";

function getErrorSummary(error: unknown) {
  if (isRouteErrorResponse(error)) {
    return {
      title: `${error.status} ${error.statusText || "Route error"}`,
      message:
        typeof error.data === "string" && error.data.trim().length > 0
          ? error.data
          : "The requested screen could not be loaded.",
      stack: null,
    };
  }

  if (error instanceof Error) {
    return {
      title: error.name || "Application error",
      message: error.message || "An unexpected error interrupted this screen.",
      stack: error.stack ?? null,
    };
  }

  if (typeof error === "string" && error.trim().length > 0) {
    return {
      title: "Application error",
      message: error,
      stack: null,
    };
  }

  return {
    title: "Application error",
    message: "An unexpected error interrupted this screen.",
    stack: null,
  };
}

interface RouteErrorPageProps {
  fullScreen?: boolean;
}

export function RouteErrorPage({ fullScreen = false }: RouteErrorPageProps) {
  const error = useRouteError();
  const location = useLocation();
  const navigate = useNavigate();
  const summary = getErrorSummary(error);
  const showDetails = import.meta.env.DEV && Boolean(summary.stack);

  const content = (
    <Card className="w-full max-w-3xl rounded-lg border-app-negative/55 bg-app-bg/85 p-0 shadow-[0_20px_80px_-48px_rgb(var(--theme-negative)/0.75)]">
      <CardHeader className="border-b border-app-border px-5 py-5 md:px-6">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="space-y-2">
            <p className="text-xs font-medium uppercase tracking-wide text-app-negative">Screen unavailable</p>
            <CardTitle className="text-xl tracking-tight text-app-text md:text-2xl">
              Something broke on this page
            </CardTitle>
            <CardDescription className="max-w-2xl text-sm leading-6 text-app-muted">
              TopSignal is still running, but this route hit an error before it could finish rendering.
            </CardDescription>
          </div>
          <div className="rounded-full border border-app-negative/25 bg-app-negative/10 px-3 py-1 text-xs font-medium text-app-negative">
            Needs attention
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-5 px-5 py-5 md:px-6">
        <div className="grid gap-3 md:grid-cols-[150px_1fr]">
          <div className="text-xs font-medium uppercase tracking-wide text-app-muted-strong">Route</div>
          <div className="min-w-0 break-words rounded-lg border border-app-border bg-app-surface/70 px-3 py-2 font-mono text-xs text-app-muted">
            {location.pathname}
            {location.search}
          </div>
          <div className="text-xs font-medium uppercase tracking-wide text-app-muted-strong">Error</div>
          <div className="min-w-0 space-y-1 rounded-lg border border-app-border bg-app-surface/70 px-3 py-2">
            <p className="text-sm font-medium text-app-text">{summary.title}</p>
            <p className="break-words text-sm leading-6 text-app-muted">{summary.message}</p>
          </div>
        </div>

        <div className="flex flex-col gap-3 sm:flex-row">
          <Button onClick={() => window.location.reload()}>Reload page</Button>
          <Button variant="secondary" onClick={() => navigate("/")}>
            Go to dashboard
          </Button>
          <Button variant="ghost" onClick={() => navigate(-1)}>
            Go back
          </Button>
        </div>

        {showDetails ? (
          <details className="rounded-lg border border-app-border bg-app-bg/80">
            <summary className="cursor-pointer px-3 py-2 text-xs font-medium uppercase tracking-wide text-app-muted">
              Developer details
            </summary>
            <pre className="max-h-72 overflow-auto border-t border-app-border px-3 py-3 text-xs leading-5 text-app-muted">
              {summary.stack}
            </pre>
          </details>
        ) : null}
      </CardContent>
    </Card>
  );

  return (
    <div
      className={cn(
        "flex w-full items-center justify-center px-0 py-8",
        fullScreen ? "min-h-screen bg-app-bg px-4 py-10" : "min-h-[520px]",
      )}
    >
      {content}
    </div>
  );
}
