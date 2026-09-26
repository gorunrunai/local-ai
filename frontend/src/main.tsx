import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { registerSW } from "virtual:pwa-register";
import { consumeLinkToken } from "./api/client";
import App from "./App";
import { useUi } from "./store/ui";
import "./styles.css";

// Service worker caches the app shell only (see vite.config.ts); API calls always hit the Mac.
consumeLinkToken();

if (import.meta.env.PROD) registerSW({ immediate: true });

(window as unknown as { __openArtifact: unknown }).__openArtifact = (a: Parameters<ReturnType<typeof useUi.getState>["openArtifact"]>[0]) =>
  useUi.getState().openArtifact(a);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
