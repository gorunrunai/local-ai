import { create } from "zustand";
import type { Artifact } from "../api/types";

function readWidth(): number {
  try {
    return Number(localStorage.getItem("la_artifact_w")) || 560;
  } catch {
    return 560;
  }
}

export type Theme = "light" | "dark" | "system";

function readTheme(): Theme {
  try {
    const t = localStorage.getItem("la_theme");
    return t === "light" || t === "dark" ? t : "system";
  } catch {
    return "system";
  }
}

export function applyTheme(t: Theme) {
  const root = document.documentElement;
  if (t === "system") delete root.dataset.theme;
  else root.dataset.theme = t;
}

const isMobile = () => typeof window !== "undefined" && window.matchMedia("(max-width: 767px)").matches;

interface UiState {
  theme: Theme;
  sidebar: boolean;
  shortcuts: boolean;
  modelsPanel: boolean;
  voice: boolean;
  setVoice: (open: boolean) => void;
  artifact: (Artifact & { nonce?: number }) | null;
  artifactWidth: number;
  openArtifact: (a: Artifact) => void;
  closeArtifact: () => void;
  setArtifactWidth: (w: number) => void;
  setTheme: (t: Theme) => void;
  setSidebar: (open: boolean) => void;
  toggleSidebar: () => void;
  closeOnMobile: () => void;
  setShortcuts: (open: boolean) => void;
  setModelsPanel: (open: boolean) => void;
}

export const useUi = create<UiState>((set, get) => ({
  theme: readTheme(),
  sidebar: !isMobile(),
  shortcuts: false,
  modelsPanel: false,
  voice: false,
  setVoice: (voice) => set({ voice }),
  artifact: null,
  artifactWidth: readWidth(),
  openArtifact: (a) => set({ artifact: { ...a, nonce: Date.now() } }),
  closeArtifact: () => set({ artifact: null }),
  setArtifactWidth: (w) => {
    try {
      localStorage.setItem("la_artifact_w", String(Math.round(w)));
    } catch {
      /* ignore */
    }
    set({ artifactWidth: w });
  },
  setTheme: (theme) => {
    try {
      localStorage.setItem("la_theme", theme);
    } catch {
      /* ignore */
    }
    applyTheme(theme);
    set({ theme });
  },
  setSidebar: (sidebar) => set({ sidebar }),
  toggleSidebar: () => set({ sidebar: !get().sidebar }),
  closeOnMobile: () => isMobile() && set({ sidebar: false }),
  setShortcuts: (shortcuts) => set({ shortcuts }),
  setModelsPanel: (modelsPanel) => set({ modelsPanel }),
}));
