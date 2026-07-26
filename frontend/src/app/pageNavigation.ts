export function replacePagePath(path: string): void {
  window.history.replaceState(window.history.state, "", path);
}

export function reloadPage(): void {
  window.location.reload();
}
