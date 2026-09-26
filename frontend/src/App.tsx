import clsx from "clsx";
import { X } from "lucide-react";
import { useEffect, useState } from "react";
import { BrowserRouter, Route, Routes, useNavigate } from "react-router";
import { get, setUnauthorizedHandler } from "./api/client";
import { ChatView } from "./components/ChatView";
import { ModelsPanel, ShortcutsOverlay, TokenGate } from "./components/Panels";
import { ProjectsPage } from "./components/ProjectsPage";
import { SettingsPage } from "./components/SettingsPage";
import { Sidebar } from "./components/Sidebar";
import { VoiceMode } from "./components/VoiceMode";
import { useChat } from "./store/chat";
import { useDraft } from "./store/draft";
import { applyTheme, useUi } from "./store/ui";

function usePushToTalk() {
  useEffect(() => {
    let key = "AltRight";
    get<{ voice?: { push_to_talk_key?: string } }>("/settings").then((s) => { key = s.voice?.push_to_talk_key || key; }).catch(() => undefined);
    const btn = () => document.querySelector<HTMLButtonElement>('button[aria-label^="Dictate"], button[aria-label="Stop dictation"]');
    let held = false;
    const down = (e: KeyboardEvent) => {
      const typing = (e.target as HTMLElement)?.closest?.("input, textarea, [contenteditable]");
      if (e.code !== key || e.repeat || held || (typing && !key.startsWith("Alt") && !key.startsWith("Meta"))) return;
      held = true;
      btn()?.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true }));
    };
    const up = (e: KeyboardEvent) => {
      if (e.code !== key || !held) return;
      held = false;
      btn()?.dispatchEvent(new PointerEvent("pointerup", { bubbles: true }));
    };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
    };
  }, []);
}

function useGlobalShortcuts() {
  const navigate = useNavigate();
  usePushToTalk();
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey;
      const typing = (e.target as HTMLElement)?.closest?.("input, textarea, [contenteditable]");
      if (mod && e.key.toLowerCase() === "k") {
        e.preventDefault();
        useChat.getState().newChat();
        navigate("/");
        setTimeout(() => useDraft.getState().focusComposer(), 0);
      } else if (mod && e.key === "/") {
        e.preventDefault();
        useDraft.getState().focusComposer();
      } else if (mod && e.shiftKey && e.key.toLowerCase() === "v") {
        e.preventDefault();
        const ui = useUi.getState();
        if (!useChat.getState().sending) ui.setVoice(!ui.voice);
      } else if (mod && e.shiftKey && e.key.toLowerCase() === "d") {
        e.preventDefault();
        document.querySelector<HTMLButtonElement>('button[aria-label^="Dictate"], button[aria-label="Stop dictation"]')
          ?.dispatchEvent(new PointerEvent("pointerup", { bubbles: true }));
      } else if (e.key === "Escape" && useChat.getState().sending) {
        e.preventDefault();
        useChat.getState().stop();
      } else if (e.key === "?" && !typing) {
        e.preventDefault();
        useUi.getState().setShortcuts(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navigate]);
}

function Toast() {
  const { toast, showToast } = useChat();
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => showToast(null), 6000);
    return () => clearTimeout(t);
  }, [toast, showToast]);
  if (!toast) return null;
  return (
    <div role="alert" className="fixed bottom-24 left-1/2 z-50 flex max-w-md -translate-x-1/2 items-start gap-2 rounded-xl border border-line bg-surface px-4 py-2.5 text-sm shadow-card">
      <span className="min-w-0 flex-1">{toast}</span>
      <button type="button" onClick={() => showToast(null)} aria-label="Dismiss" className="text-muted hover:text-fg"><X size={15} /></button>
    </div>
  );
}

function Shell() {
  const { sidebar, setSidebar, shortcuts, setShortcuts, modelsPanel, setModelsPanel, voice, setVoice } = useUi();
  const loadModels = useChat((s) => s.loadModels);
  useGlobalShortcuts();
  useEffect(() => {
    loadModels();
    const t = setInterval(loadModels, 15000);
    return () => clearInterval(t);
  }, [loadModels]);
  return (
    <div className="flex h-full overflow-hidden">
      <div className={clsx("no-print z-40 h-full shrink-0 transition-[margin] duration-200 max-md:fixed max-md:inset-y-0 max-md:left-0 max-md:shadow-card",
        sidebar ? "ml-0" : "-ml-72")}>
        <Sidebar />
      </div>
      {sidebar && <div className="fixed inset-0 z-30 bg-black/30 md:hidden" onClick={() => setSidebar(false)} aria-hidden="true" />}
      <Routes>
        <Route path="/" element={<ChatView />} />
        <Route path="/c/:id" element={<ChatView />} />
        <Route path="/projects" element={<ProjectsPage />} />
        <Route path="/projects/:id" element={<ProjectsPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/settings/:section" element={<SettingsPage />} />
        <Route path="*" element={<ChatView />} />
      </Routes>
      {shortcuts && <ShortcutsOverlay onClose={() => setShortcuts(false)} />}
      {modelsPanel && <ModelsPanel onClose={() => setModelsPanel(false)} />}
      {voice && <VoiceMode onClose={() => setVoice(false)} />}
      <Toast />
    </div>
  );
}

export default function App() {
  const theme = useUi((s) => s.theme);
  const [auth, setAuth] = useState<"checking" | "ok" | "needed">("checking");
  useEffect(() => applyTheme(theme), [theme]);
  useEffect(() => {
    setUnauthorizedHandler(() => setAuth("needed"));
    get("/health").then(() => get("/models")).then(() => setAuth("ok"), (e) => setAuth(e.status === 401 ? "needed" : "ok"));
  }, []);
  if (auth === "checking") return null;
  if (auth === "needed") return <TokenGate onDone={() => setAuth("ok")} />;
  return (
    <BrowserRouter>
      <Shell />
    </BrowserRouter>
  );
}
