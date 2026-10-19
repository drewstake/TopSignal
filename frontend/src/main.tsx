import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./styles/globals.css";
import "./styles/workspace.css";
import "./styles/signal.css";
import App from "./App";
import { initializeAppTheme } from "./lib/theme";
import { initializeAppScale } from "./lib/appScale";

initializeAppTheme();
const disposeAppScale = initializeAppScale();
if (import.meta.hot) import.meta.hot.dispose(disposeAppScale);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
