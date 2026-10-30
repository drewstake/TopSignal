// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { RouteErrorPage } from "./RouteErrorPage";
import { checkRouteLoad, isModuleLoadFailure } from "./routeLoadFailure";

const error = vi.hoisted(() => ({ value: new Error("render failure") }));
vi.mock("react-router-dom", () => ({
  isRouteErrorResponse: () => false, useRouteError: () => error.value,
  useLocation: () => ({ pathname: "/bot", search: "" }), useNavigate: () => vi.fn(),
}));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const moduleError = () => new Error(`Failed to fetch dynamically imported module: ${location.origin}/assets/old.js`);
const html = () => new Response(null, { status: 200, headers: { "content-type": "text/html" } });

it("does not claim backend health or probe for an ordinary render failure", () => {
  error.value = new Error("render failure");
  const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
  render(<RouteErrorPage />);
  expect(screen.getByText(/Backend and trading status have not been verified/)).toBeTruthy();
  expect(fetcher).not.toHaveBeenCalled();
});

it("reports a connection failure and can recheck after recovery without reloading", async () => {
  error.value = moduleError();
  vi.stubGlobal("fetch", vi.fn().mockRejectedValueOnce(new TypeError("network")).mockResolvedValueOnce(html()).mockResolvedValueOnce(new Response(null)));
  render(<RouteErrorPage />);
  expect(await screen.findByText(/dashboard server did not respond successfully/)).toBeTruthy();
  await userEvent.setup().click(screen.getByRole("button", { name: "Check connection" }));
  expect(await screen.findByText(/dashboard is reachable now/)).toBeTruthy();
});

it("distinguishes a missing old chunk from an unavailable server", async () => {
  error.value = moduleError();
  vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(html()).mockResolvedValueOnce(new Response(null, { status: 404 })));
  render(<RouteErrorPage />);
  expect(await screen.findByText(/page file requested by this tab is missing/)).toBeTruthy();
});

it("never fetches external error URLs or API paths", async () => {
  const fetcher = vi.fn().mockImplementation(async () => html()); vi.stubGlobal("fetch", fetcher);
  for (const url of ["https://untrusted.invalid/assets/x.js", `${location.origin}/api/private.js`]) {
    expect(await checkRouteLoad(`Failed to fetch dynamically imported module: ${url}`, new AbortController().signal)).toBe("reachable");
  }
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(isModuleLoadFailure("Importing a module script failed.")).toBe(true);
  expect(isModuleLoadFailure("Cannot read properties of undefined")).toBe(false);
});

it("does not mistake an HTML fallback or a bad HTTP response for a stale chunk", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(new Response(null, { status: 503 })));
  expect(await checkRouteLoad(moduleError().message, new AbortController().signal)).toBe("unreachable");
  vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(html()).mockResolvedValueOnce(new Response(null, { status: 500 })));
  expect(await checkRouteLoad(moduleError().message, new AbortController().signal)).toBe("unknown");
});
