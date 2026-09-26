import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Capabilities } from "../lib/capabilities";
import { SidebarOptions } from "./SidebarOptions";

const f = (id: string, available: boolean, reason = "") => ({ id, label: id, available, reason });
const caps: Capabilities = {
  machine: { chip: "Apple M5 Max", memory_gb: 64, macos: "26" }, tested: true, report_url: "", installed: [], web_access: true,
  features: [f("chat", true), f("voice_mode", true), f("voice_messages", false, "Nothing installed can hear audio."),
    f("video_sound", true), f("animate_photo", true)],
  limitations: [],
};
vi.mock("../api/client", () => ({ get: vi.fn(() => Promise.resolve(caps)) }));

describe("SidebarOptions", () => {
  afterEach(cleanup);
  it("ticks what this Mac can do and crosses the rest, with the reason on hover", async () => {
    render(<MemoryRouter><SidebarOptions /></MemoryRouter>);
    expect(await screen.findByText("Chat, photos, documents")).toBeInTheDocument();
    const voice = screen.getByText("Voice conversations, voice messages").closest("li")!;
    expect(voice).toHaveTextContent("Not available:");                   // voice messages can't be heard
    expect(voice).toHaveAttribute("title", "Nothing installed can hear audio.");
    expect(screen.getByText("Animating a photo").closest("li")).toHaveTextContent("Available:");
    expect(screen.getByRole("link", { name: /What this Mac can do/ })).toHaveAttribute("href", "/settings/setup");
  });
});
