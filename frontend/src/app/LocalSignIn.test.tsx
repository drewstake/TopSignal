// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
const auth = vi.hoisted(() => ({ status: vi.fn(), signIn: vi.fn(), device: vi.fn() }));
vi.mock("../lib/localAuth", () => ({ localAuthStatus: auth.status, signInLocally: auth.signIn, continueOnThisDevice: auth.device }));
import { LocalSignIn } from "./LocalSignIn";
beforeEach(() => { vi.clearAllMocks(); });
afterEach(cleanup);

it("offers one-click device access without password or setup fields", async () => {
  auth.status.mockResolvedValue({ enabled: true, needs_setup: false, mode: "trusted_device" });
  auth.device.mockResolvedValue({});
  render(<LocalSignIn />);
  fireEvent.click(await screen.findByRole("button", { name: "Continue on this device" }));
  await waitFor(() => expect(auth.device).toHaveBeenCalledTimes(1));
  expect(auth.signIn).not.toHaveBeenCalled();
  expect(screen.queryByLabelText("Password")).toBeNull();
  expect(screen.queryByLabelText("Setup code")).toBeNull();
});

it("shows a connection failure and allows retrying device access", async () => {
  auth.status.mockResolvedValue({ enabled: true, needs_setup: false, mode: "trusted_device" });
  auth.device.mockRejectedValue(new Error("Could not connect"));
  render(<LocalSignIn />);
  fireEvent.click(await screen.findByRole("button", { name: "Continue on this device" }));
  expect((await screen.findByRole("alert")).textContent).toBe("Could not connect");
  await waitFor(() => expect((screen.getByRole("button", { name: "Continue on this device" }) as HTMLButtonElement).disabled).toBe(false));
});

it("requires matching setup passwords and submits only when they match", async () => {
  auth.status.mockResolvedValue({ enabled: true, needs_setup: true });
  auth.signIn.mockResolvedValue({});
  render(<LocalSignIn />);
  fireEvent.change(await screen.findByLabelText("Setup code"), { target: { value: "one-time-code" } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: "a long test passphrase" } });
  fireEvent.change(screen.getByLabelText("Confirm password"), { target: { value: "different passphrase" } });
  fireEvent.click(screen.getByRole("button", { name: "Create password and sign in" }));
  expect(screen.getByRole("alert").textContent).toBe("Passwords do not match.");
  expect(auth.signIn).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("Confirm password"), { target: { value: "a long test passphrase" } });
  fireEvent.click(screen.getByRole("button", { name: "Create password and sign in" }));
  await waitFor(() => expect(auth.signIn).toHaveBeenCalledWith("a long test passphrase", "one-time-code"));
});

it("shows password-only login after setup", async () => {
  auth.status.mockResolvedValue({ enabled: true, needs_setup: false });
  auth.signIn.mockRejectedValue(new Error("Incorrect password."));
  render(<LocalSignIn />);
  fireEvent.change(await screen.findByLabelText("Password"), { target: { value: "wrong" } });
  expect(screen.queryByLabelText("Setup code")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Sign in locally" }));
  expect((await screen.findByRole("alert")).textContent).toBe("Incorrect password.");
});
