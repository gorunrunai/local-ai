// Runs inside the sandboxed artifact frame (opaque origin, no network). Bundled by
// vite.runtime.config.ts into public/artifact-runtime/runtime.js together with React,
// lucide-react and recharts; Babel and Tailwind load as separate local scripts.
import * as Lucide from "lucide-react";
import * as React from "react";
import * as ReactDOM from "react-dom";
import * as ReactDOMClient from "react-dom/client";
import * as Recharts from "recharts";
import { ArtifactError, rewriteModule } from "./transform";

declare global {
  interface Window {
    Babel: { transform: (code: string, opts: object) => { code: string } };
  }
}

const MODS: Record<string, unknown> = {
  react: React, "react-dom": ReactDOM, "react-dom/client": ReactDOMClient, "lucide-react": Lucide, recharts: Recharts,
};

/** Web Storage kept in memory for this frame. The sandbox has an opaque origin, so the real
 * localStorage/sessionStorage throw; generated apps use them a lot (to save habits, todos...). */
class MemoryStorage {
  private data = new Map<string, string>();
  get length() { return this.data.size; }
  key(i: number) { return [...this.data.keys()][i] ?? null; }
  getItem(k: string) { return this.data.has(String(k)) ? this.data.get(String(k))! : null; }
  setItem(k: string, v: unknown) { this.data.set(String(k), String(v)); }
  removeItem(k: string) { this.data.delete(String(k)); }
  clear() { this.data.clear(); }
}

function storage(name: "localStorage" | "sessionStorage"): Storage {
  try {
    const real = window[name];
    void real.length;
    return real;
  } catch {
    const mem = new MemoryStorage() as unknown as Storage;
    try {
      Object.defineProperty(window, name, { value: mem, configurable: true });
    } catch {
      /* bare `localStorage` references still get `mem` through the factory's scope */
    }
    return mem;
  }
}
const STORAGE = { local: storage("localStorage"), session: storage("sessionStorage") };

function showError(title: string, detail: string) {
  const root = document.getElementById("root")!;
  root.innerHTML = "";
  const box = document.createElement("div");
  box.style.cssText = "margin:16px;padding:12px 14px;border:1px solid #e5a3a0;background:#fdf1f0;color:#8a211b;border-radius:10px;font:13px/1.5 -apple-system,sans-serif;white-space:pre-wrap";
  box.textContent = `${title}\n\n${detail}`;
  root.appendChild(box);
}

class Boundary extends React.Component<{ children: React.ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  render() {
    if (this.state.error) {
      return React.createElement("pre", { style: { margin: 16, padding: 12, color: "#8a211b", background: "#fdf1f0", borderRadius: 10, whiteSpace: "pre-wrap", font: "13px/1.5 ui-monospace, monospace" } },
        `This component crashed while rendering:\n\n${this.state.error.message}`);
    }
    return this.props.children;
  }
}

function run() {
  const src = document.getElementById("artifact-source")?.textContent ?? "";
  try {
    const { code, defaultName } = rewriteModule(src);
    const compiled = window.Babel.transform(code, {
      filename: "artifact.tsx",
      presets: [["react", { runtime: "classic" }], ["typescript", { isTSX: true, allExtensions: true }]],
    }).code;
    // React is provided for JSX; the artifact's own `import React` may shadow it inside the block.
    const factory = new Function("__mods", "__React", "__storage",
      `var React = __React, localStorage = __storage.local, sessionStorage = __storage.session;\n{\n${compiled}\n;return ${defaultName};\n}`);
    const Component = factory(MODS, React, STORAGE) as React.ComponentType;
    if (typeof Component !== "function") throw new ArtifactError("The default export isn't a React component.");
    ReactDOMClient.createRoot(document.getElementById("root")!).render(
      React.createElement(Boundary, null, React.createElement(Component)));
  } catch (e) {
    const err = e as Error;
    showError(err instanceof ArtifactError ? "This artifact can't be shown." : "This component has an error.", err.message);
  }
}

window.addEventListener("error", (e) => showError("Runtime error", String(e.message)));
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", run);
else run();
