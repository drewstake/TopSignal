// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { APP_ZOOM_STORAGE_KEY, initializeAppScale, readStoredAppZoom, selectAppZoom } from "../../lib/appScale";
import { AppearancePage } from "./ThemePage";
import { DESIGN_STORAGE_KEY, initializeWorkspaceDesign } from "../../lib/workspaceDesign";
import { APP_THEME_STORAGE_KEY } from "../../lib/theme";

vi.mock("../../components/demo/DemoModeNotice", () => ({ DemoModeNotice: () => null }));

let dispose: () => void;
let disposeDesign: () => void;
beforeEach(() => {
  localStorage.clear();
  dispose = initializeAppScale();
  disposeDesign = initializeWorkspaceDesign();
});
afterEach(() => {
  cleanup();
  dispose();
  disposeDesign();
  vi.restoreAllMocks();
  document.documentElement.style.removeProperty("--app-scale");
});

describe("Workspace design", () => {
  it("switches in place, restores on startup, and lets the user return to the original layout", () => {
    localStorage.setItem(APP_THEME_STORAGE_KEY, "daylight");
    selectAppZoom(110);
    const view = render(<AppearancePage />);
    const root = document.documentElement;
    expect(root.dataset.workspaceDesign).toBe("signal-deck");
    fireEvent.click(screen.getByRole("radio", { name: "Terminal" }));
    expect(root.dataset.workspaceDesign).toBe("terminal");
    fireEvent.click(screen.getByRole("radio", { name: "Editorial" }));
    expect(root.dataset.workspaceDesign).toBe("editorial");
    expect(localStorage.getItem(DESIGN_STORAGE_KEY)).toBe("editorial");
    expect(localStorage.getItem(APP_THEME_STORAGE_KEY)).toBe("daylight");
    expect(readStoredAppZoom()).toBe(110);
    view.unmount();
    disposeDesign();
    delete root.dataset.workspaceDesign;
    disposeDesign = initializeWorkspaceDesign();
    render(<AppearancePage />);
    expect(screen.getByRole("radio", { name: "Editorial", checked: true })).toBeTruthy();
    fireEvent.click(screen.getByRole("radio", { name: "Signal Deck" }));
    expect(root.dataset.workspaceDesign).toBe("signal-deck");
  });

  it("syncs design changes from another tab and recovers from invalid or cleared storage", () => {
    render(<AppearancePage />);
    for (const [stored, name] of [["terminal", "Terminal"], ["old-layout", "Signal Deck"], ["editorial", "Editorial"], [null, "Signal Deck"]] as const) {
      if (stored === null) localStorage.clear();
      else localStorage.setItem(DESIGN_STORAGE_KEY, stored);
      act(() => window.dispatchEvent(new StorageEvent("storage", { key: stored === null ? null : DESIGN_STORAGE_KEY, storageArea: localStorage })));
      expect(screen.getByRole("radio", { name, checked: true })).toBeTruthy();
    }
  });
});

describe("Appearance zoom", () => {
  it("defaults to 80%, applies selections globally, restores on startup, and resets", () => {
    const view = render(<AppearancePage />);
    const select = screen.getByRole("combobox", { name: "Display scale" }) as HTMLSelectElement;
    expect(select.value).toBe("80");
    expect(document.documentElement.style.getPropertyValue("--app-scale")).toBe("0.8");
    fireEvent.change(select, { target: { value: "110" } });
    expect(document.documentElement.style.getPropertyValue("--app-scale")).toBe("1.1");
    expect(readStoredAppZoom()).toBe(110);
    view.unmount();
    dispose();
    document.documentElement.style.removeProperty("--app-scale");
    dispose = initializeAppScale();
    render(<AppearancePage />);
    expect((screen.getByRole("combobox", { name: "Display scale" }) as HTMLSelectElement).value).toBe("110");
    fireEvent.click(screen.getByRole("button", { name: "Reset to 80%" }));
    expect(readStoredAppZoom()).toBe(80);
    expect(document.documentElement.style.getPropertyValue("--app-scale")).toBe("0.8");
  });

  it("steps by 5% and disables controls at the bounds", () => {
    render(<AppearancePage />);
    fireEvent.click(screen.getByRole("button", { name: "Increase app zoom" }));
    expect(readStoredAppZoom()).toBe(85);
    fireEvent.click(screen.getByRole("button", { name: "Decrease app zoom" }));
    expect(readStoredAppZoom()).toBe(80);
    act(() => selectAppZoom(60));
    expect((screen.getByRole("button", { name: "Decrease app zoom" }) as HTMLButtonElement).disabled).toBe(true);
    act(() => selectAppZoom(150));
    expect((screen.getByRole("button", { name: "Increase app zoom" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it.each(["", "0", "-80", "1000", "NaN", "83"])("uses the default for invalid storage %s", (value) => {
    localStorage.setItem(APP_ZOOM_STORAGE_KEY, value);
    expect(readStoredAppZoom()).toBe(80);
  });

  it("updates the document and controls when another tab changes or clears the preference", () => {
    render(<AppearancePage />);
    localStorage.setItem(APP_ZOOM_STORAGE_KEY, "125");
    act(() => window.dispatchEvent(new StorageEvent("storage", { key: APP_ZOOM_STORAGE_KEY, storageArea: localStorage })));
    expect(document.documentElement.style.getPropertyValue("--app-scale")).toBe("1.25");
    expect((screen.getByRole("combobox", { name: "Display scale" }) as HTMLSelectElement).value).toBe("125");
    localStorage.clear();
    act(() => window.dispatchEvent(new StorageEvent("storage", { key: null, storageArea: localStorage })));
    expect((screen.getByRole("combobox", { name: "Display scale" }) as HTMLSelectElement).value).toBe("80");
  });

  it("still changes the scale when storage is blocked", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("Blocked"); });
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("Blocked"); });
    expect(readStoredAppZoom()).toBe(80);
    render(<AppearancePage />);
    fireEvent.change(screen.getByRole("combobox", { name: "Display scale" }), { target: { value: "100" } });
    expect(document.documentElement.style.getPropertyValue("--app-scale")).toBe("1");
    expect((screen.getByRole("combobox", { name: "Display scale" }) as HTMLSelectElement).value).toBe("100");
  });
});
