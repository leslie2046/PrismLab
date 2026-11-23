import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { AuthGate } from "./auth";
import { ConfirmProvider } from "./components/ConfirmDialog";
import "./index.css";
import "./auth.css";
import "./workspaces.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ConfirmProvider><AuthGate><App /></AuthGate></ConfirmProvider>
  </StrictMode>,
);

import "./design-system.css";
