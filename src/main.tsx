import React from "react";
import ReactDOM from "react-dom/client";
import { RouterProvider } from "react-router-dom";
import "./index.css";
import { router } from "./app/router";
import { ThemeProvider } from "./lib/theme";
if (import.meta.env.VITE_ENABLE_MARKET_DATA_DEMO === "true") {
  import("./market/demoMarketData").then(({ startMarketDataDemo }) => {
    void startMarketDataDemo();
  });
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ThemeProvider>
      <RouterProvider router={router} />
    </ThemeProvider>
  </React.StrictMode>
);
