import { useDemoMode } from "../../lib/demoMode";

export function useDemoInteractionPolicy() {
  const demoMode = useDemoMode();

  return {
    demoModeEnabled: demoMode.enabled,
    demoDisabledTitle: demoMode.enabled
      ? "Unavailable in Demo Mode because it would read or change connected account data."
      : undefined,
  };
}
